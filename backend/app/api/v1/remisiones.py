"""Remisiones — CRUD + confirmar/cancelar con efecto en inventario (Phase 4e).

Reads gated by `menu:remisiones`; writes by `remision:gestionar`.

Lifecycle: BORRADOR (editable, no stock effect) → CONFIRMADA (salida directa:
disponible baja, one SALIDA_REMISION movement per line; la línea guarda
lote_id/cantidad_surtida para poder restituir) → CANCELADA (restituye:
disponible sube, CANCELACION_REMISION). A draft cancels with no inventory
effect. La salida usa el lote default de (producto, almacén) — lot-selection/
FIFO is a later refinement. `cantidad_reservada` ya no se usa (decisión
2026-07-29: confirmar = el camión salió; la columna queda en 0).
"""
from __future__ import annotations

import html as html_mod

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Almacen,
    Cliente,
    Devolucion,
    EsquemaImpuesto,
    Factura,
    LineaDevolucion,
    LineaRemision,
    ListaPrecios,
    LoteInventario,
    Producto,
    Remision,
    Sucursal,
    Tenant,
)
from ...services import email as email_service
from ...services.fiscal import calcular_linea_producto
from ...services.importar_remisiones import ImportError_, agrupar_por_folio, normalizar_folio, parsear_excel
from ...services.producto_match import buscar, productos_activos
from ...services.precios import resolver_precio
from ...services.series import consumir_folio, resolver_serie, siguiente_folio
from ...schemas.common import Page
from ...schemas.remision import (
    ConfirmarRemisionIn,
    RemisionCreate,
    RemisionDetailOut,
    RemisionOut,
    RemisionUpdate,
)
from ...services.inventario import build_movimiento, presentacion_factor, resolve_lote
from ...services.remision_pdf import build_remision_pdf, build_remisiones_pdf
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/remisiones", tags=["remisiones"])

_READ = "menu:remisiones"
_WRITE = "remision:gestionar"
_ZERO = Decimal("0")
_DUP = "Folio de remisión duplicado"


def _fiscal_por_producto(db: Session, producto_ids) -> dict:
    """{producto_id: (Producto, EsquemaImpuesto|None)} para el cálculo fiscal
    de líneas — una sola carga por request."""
    ids = set(producto_ids)
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(ids)).all()}
    esq_ids = {p.esquema_impuesto_id for p in productos.values() if p.esquema_impuesto_id}
    esquemas = (
        {e.id: e for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.id.in_(esq_ids)).all()}
        if esq_ids else {}
    )
    return {
        pid: (p, esquemas.get(p.esquema_impuesto_id) if p.esquema_impuesto_id else None)
        for pid, p in productos.items()
    }


def _next_folio(db: Session, tenant_id, *, sucursal_id=None, cliente_id=None, serie_id=None) -> str:
    """Folio `{codigo}{N}` (serie y número juntos, sin guion) de la serie de remisión
    resuelta (override → sucursal → cliente → default), contador sin huecos. Si no hay
    serie aplicable, cae a la serie 'R' por código y, en último caso, a max+1 (back-compat)."""
    serie = resolver_serie(
        db, tenant_id, "REMISION", serie_id=serie_id, sucursal_id=sucursal_id, cliente_id=cliente_id
    )
    if serie is not None:
        folio = consumir_folio(db, serie.id)
        if folio is not None:
            return f"{serie.codigo}{folio}"
    folio = siguiente_folio(db, tenant_id, codigo="R", tipo_documento="REMISION")
    if folio is not None:
        return f"R{folio}"
    mx = 0
    for (f,) in db.query(Remision.folio_interno).filter(Remision.folio_interno.isnot(None)).all():
        if not f or not f.startswith("R"):
            continue
        num = f[1:].lstrip("-")  # tolera "R5" y "R-5" (legado)
        if num.isdigit():
            mx = max(mx, int(num))
    return f"R{mx + 1}"


@router.get("", response_model=Page[RemisionOut])
def list_remisiones(
    estado: Optional[str] = Query(default=None, max_length=20),
    cliente_id: Optional[UUID] = Query(default=None),
    fecha_desde: Optional[date] = Query(default=None),
    fecha_hasta: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = (
        db.query(Remision)
        .options(joinedload(Remision.factura))
        .filter(Remision.deleted_at.is_(None))
    )
    if estado:
        query = query.filter(Remision.estado == estado)
    if cliente_id is not None:
        query = query.filter(Remision.cliente_facturacion_id == cliente_id)
    if fecha_desde:
        query = query.filter(Remision.fecha_remision >= fecha_desde)
    if fecha_hasta:
        query = query.filter(Remision.fecha_remision <= fecha_hasta)
    query = query.order_by(Remision.fecha_remision.desc(), Remision.folio_interno.desc())
    return paginate(query, RemisionOut, limit, offset)


@router.post("", response_model=RemisionDetailOut, status_code=status.HTTP_201_CREATED)
def create_remision(
    payload: RemisionCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_facturacion_id, "cliente_facturacion_id")
    ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")
    ensure_fk(db, ListaPrecios, payload.lista_precios_id, "lista_precios_id")
    if payload.sucursal_id is not None:
        suc = get_or_404(db, Sucursal, payload.sucursal_id)
        if suc.cliente_id != payload.cliente_facturacion_id:
            raise HTTPException(status_code=422, detail="La sucursal no pertenece al cliente de la remisión")
    for ln in payload.lineas:
        ensure_fk(db, Producto, ln.producto_id, "producto_id")

    rem = Remision(
        tenant_id=ctx.tenant_id,
        folio_interno=_next_folio(
            db, ctx.tenant_id,
            sucursal_id=payload.sucursal_id,
            cliente_id=payload.cliente_facturacion_id,
            serie_id=payload.serie_id,
        ),
        cliente_facturacion_id=payload.cliente_facturacion_id,
        almacen_id=payload.almacen_id,
        sucursal_id=payload.sucursal_id,
        lista_precios_id=payload.lista_precios_id,
        fecha_remision=payload.fecha_remision or date.today(),
        fecha_entrega=payload.fecha_entrega,
        canal=payload.canal,
        descuento=payload.descuento,
        notas=payload.notas,
        nota_entrega=payload.nota_entrega,
        estado="BORRADOR",
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    db.add(rem)
    db.flush()

    fiscal = _fiscal_por_producto(db, [ln.producto_id for ln in payload.lineas])
    subtotal = _ZERO
    iva_total = _ZERO
    ieps_total = _ZERO
    for i, ln in enumerate(payload.lineas, start=1):
        # Precio: manual si se envía; si no, se resuelve por cliente/sucursal/volumen.
        precio = ln.precio_unitario
        if precio is None:
            res = resolver_precio(
                db, producto_id=ln.producto_id, presentacion=ln.presentacion,
                cantidad=ln.cantidad_solicitada,
                cliente_id=payload.cliente_facturacion_id, sucursal_id=payload.sucursal_id,
            )
            if not res or res.get("precio") is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"No se encontró precio para el producto de la línea {i}; indícalo manualmente",
                )
            precio = res["precio"]
        importe = ln.cantidad_solicitada * precio
        subtotal += importe
        prod, esq = fiscal.get(ln.producto_id, (None, None))
        calc = calcular_linea_producto(prod, esq, importe, ln.cantidad_solicitada)
        iva_total += calc["iva_importe"]
        ieps_total += calc["ieps_importe"]
        db.add(LineaRemision(
            tenant_id=ctx.tenant_id,
            remision_id=rem.id,
            numero_linea=i,
            producto_id=ln.producto_id,
            presentacion=ln.presentacion,
            cantidad_solicitada=ln.cantidad_solicitada,
            precio_unitario=precio,
            importe=importe,
            iva_importe=calc["iva_importe"],
            ieps_importe=calc["ieps_importe"],
            notas=ln.notas,
        ))
    rem.subtotal = subtotal
    rem.iva = iva_total
    rem.ieps = ieps_total
    rem.total = subtotal - payload.descuento + iva_total + ieps_total
    flush_or_conflict(db, detail=_DUP)
    db.refresh(rem)
    return rem


@router.get("/pdf")
def remisiones_pdf_lote(
    ids: str = Query(..., description="IDs de remisión separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """PDF de varias remisiones (una por página) con el diseño de la factura.
    Definido ANTES de /{rem_id} para que la ruta estática gane."""
    id_list: list[UUID] = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            id_list.append(UUID(raw))
        except ValueError:
            continue
    if not id_list:
        raise HTTPException(status_code=422, detail="Sin remisiones para imprimir")
    if len(id_list) > 200:
        raise HTTPException(status_code=422, detail="Máximo 200 remisiones por PDF")
    rems = db.query(Remision).filter(
        Remision.id.in_(id_list), Remision.deleted_at.is_(None)
    ).order_by(Remision.folio_interno).all()
    if not rems:
        raise HTTPException(status_code=404, detail="No se encontraron remisiones")
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    prod_ids = {ln.producto_id for r in rems for ln in r.lineas}
    nombres = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    cli_ids = {r.cliente_facturacion_id for r in rems}
    clientes = {c.id: c for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()}
    items = [(r, clientes.get(r.cliente_facturacion_id), nombres) for r in rems]
    pdf = build_remisiones_pdf(items, tenant)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="remisiones.pdf"'},
    )


@router.get("/{rem_id}", response_model=RemisionDetailOut)
def get_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    rem = get_or_404(db, Remision, rem_id)
    prod_ids = {ln.producto_id for ln in rem.lineas}
    names = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    for ln in rem.lineas:
        ln.producto_nombre = names.get(ln.producto_id)
    return rem


def _liberar_reservas(db: Session, ctx: AuthContext, rem: Remision, *, motivo: str) -> None:
    """Restituye al inventario la salida de una remisión CONFIRMADA (disponible
    sube exactamente lo que salió, según el stamp de cada línea) y registra un
    movimiento por línea. Lo usan cancelar y la reedición de una confirmada
    (que luego vuelve a descontar)."""
    prod_ids = {ln.producto_id for ln in rem.lineas if ln.lote_id is not None}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}
    for ln in rem.lineas:
        if ln.lote_id is None:
            continue
        lote = (
            db.query(LoteInventario)
            .filter(LoteInventario.id == ln.lote_id)
            .with_for_update()
            .one_or_none()
        )
        if lote is None:
            continue
        # Libera exactamente lo reservado al confirmar (unidad base guardada);
        # para filas antiguas sin cantidad_surtida, usa la estimación.
        if ln.cantidad_surtida is not None:
            cantidad = ln.cantidad_surtida
        else:
            factor = presentacion_factor(productos.get(ln.producto_id), ln.presentacion)
            cantidad = ln.cantidad_solicitada * factor
        lote.cantidad_disponible = lote.cantidad_disponible + cantidad
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "CANCELACION_REMISION", cantidad,
            ref_tipo="REMISION", ref_id=rem.id, motivo=motivo,
        ))
        # Limpia el vínculo de reserva: la línea ya no reserva nada. Evita
        # reservas huérfanas y cualquier doble-liberación futura.
        ln.lote_id = None
        ln.cantidad_surtida = None


@router.patch("/{rem_id}", response_model=RemisionDetailOut)
def update_remision(
    rem_id: UUID,
    payload: RemisionUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado not in ("BORRADOR", "CONFIRMADA"):
        raise HTTPException(status_code=409, detail="Solo se puede editar una remisión en borrador o confirmada")
    # No editar por detrás de una factura viva: si la remisión está ligada a una
    # factura BORRADOR o TIMBRADA, editarla desincronizaría el comprobante ya
    # emitido. Solo se edita si no tiene factura o si la última fue CANCELADA.
    if rem.factura_id is not None:
        fac = db.query(Factura).filter(Factura.id == rem.factura_id).one_or_none()
        if fac is not None and fac.estado != "CANCELADA":
            raise HTTPException(
                status_code=409,
                detail="La remisión está ligada a una factura; cancélala o descártala antes de editar",
            )
    era_confirmada = rem.estado == "CONFIRMADA"
    almacen_anterior = rem.almacen_id           # para detectar cambio de almacén
    data = payload.model_dump(exclude_unset=True)
    lineas_in = data.pop("lineas", None)
    permitir_negativos = bool(data.pop("permitir_negativos", False))
    almacen_cambio = "almacen_id" in data and data["almacen_id"] != almacen_anterior

    if data.get("almacen_id") is not None:
        ensure_fk(db, Almacen, data["almacen_id"], "almacen_id")
    if data.get("lista_precios_id") is not None:
        ensure_fk(db, ListaPrecios, data["lista_precios_id"], "lista_precios_id")
    if data.get("cliente_facturacion_id") is not None:
        ensure_fk(db, Cliente, data["cliente_facturacion_id"], "cliente_facturacion_id")

    # Cliente/sucursal coherentes (la sucursal debe ser del cliente). Se valida
    # también la sucursal HEREDADA: cambiar solo el cliente no debe dejar una
    # sucursal de otro cliente (precio/serie se resolverían con datos mezclados).
    nuevo_cliente = data.get("cliente_facturacion_id", rem.cliente_facturacion_id)
    sucursal_efectiva = data["sucursal_id"] if "sucursal_id" in data else rem.sucursal_id
    if sucursal_efectiva is not None and ("sucursal_id" in data or "cliente_facturacion_id" in data):
        suc = get_or_404(db, Sucursal, sucursal_efectiva)
        if suc.cliente_id != nuevo_cliente:
            if "sucursal_id" in data:
                raise HTTPException(status_code=422, detail="La sucursal no pertenece al cliente de la remisión")
            raise HTTPException(
                status_code=422,
                detail="La sucursal actual no pertenece al nuevo cliente; cámbiala o quítala en la misma edición",
            )

    for key, value in data.items():
        setattr(rem, key, value)

    # Cambio de almacén SIN reenviar líneas en una CONFIRMADA: la reserva vive en
    # lotes del almacén anterior → se libera y se re-reserva en el nuevo (el mismo
    # tratamiento que el bloque de líneas hace vía `almacen_cambio`).
    if era_confirmada and almacen_cambio and lineas_in is None:
        _liberar_reservas(
            db, ctx, rem,
            motivo=f"Cambio de almacén remisión {rem.folio_interno} (libera reserva previa)",
        )
        db.flush()
        reservar_stock_remision(db, ctx, rem, permitir_negativos=permitir_negativos)

    # Reemplaza las líneas y recalcula el subtotal (mismo criterio que el alta).
    if lineas_in is not None:
        if not lineas_in:
            raise HTTPException(status_code=422, detail="La remisión debe tener al menos una línea")
        for ln in lineas_in:
            ensure_fk(db, Producto, ln["producto_id"], "producto_id")

        # ¿Cambió el detalle que AFECTA inventario (producto, presentación,
        # cantidad)? Si no, no se toca el inventario: se preserva la reserva
        # existente y solo se actualizan precios/notas. Esto evita retiros/
        # reservas no deseados al editar una CONFIRMADA sin mover cantidades.
        def _firma(pid, pres, cant) -> tuple:
            return (str(pid), str(pres), Decimal(str(cant)))
        firma_actual = sorted(_firma(l.producto_id, l.presentacion, l.cantidad_solicitada) for l in rem.lineas)
        firma_nueva = sorted(_firma(ln["producto_id"], ln["presentacion"], ln["cantidad_solicitada"]) for ln in lineas_in)
        # Cambió el inventario si cambian productos/cantidades O el almacén (la
        # reserva vive en un lote de un almacén concreto; mover almacén = re-reservar).
        inv_cambio = firma_actual != firma_nueva or almacen_cambio

        # Confirmada + cambio real de inventario → libera la reserva previa
        # (más abajo se re-reserva con las líneas nuevas). Si no cambia, indexa
        # la reserva por firma para heredarla en las líneas reconstruidas.
        reserva_por_firma: dict[tuple, list] = {}
        if era_confirmada and inv_cambio:
            _liberar_reservas(db, ctx, rem, motivo=f"Reedición remisión {rem.folio_interno} (libera reserva previa)")
        elif era_confirmada:
            for l in rem.lineas:
                reserva_por_firma.setdefault(
                    _firma(l.producto_id, l.presentacion, l.cantidad_solicitada), []
                ).append((l.lote_id, l.cantidad_surtida))

        for old in list(rem.lineas):
            db.delete(old)
        db.flush()
        fiscal = _fiscal_por_producto(db, [ln["producto_id"] for ln in lineas_in])
        subtotal = _ZERO
        iva_total = _ZERO
        ieps_total = _ZERO
        for i, ln in enumerate(lineas_in, start=1):
            precio = ln.get("precio_unitario")
            if precio is None:
                res = resolver_precio(
                    db, producto_id=ln["producto_id"], presentacion=ln["presentacion"],
                    cantidad=ln["cantidad_solicitada"],
                    cliente_id=rem.cliente_facturacion_id, sucursal_id=rem.sucursal_id,
                )
                if not res or res.get("precio") is None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"No se encontró precio para el producto de la línea {i}; indícalo manualmente",
                    )
                precio = res["precio"]
            importe = ln["cantidad_solicitada"] * precio
            subtotal += importe
            prod, esq = fiscal.get(ln["producto_id"], (None, None))
            calc = calcular_linea_producto(prod, esq, importe, ln["cantidad_solicitada"])
            iva_total += calc["iva_importe"]
            ieps_total += calc["ieps_importe"]
            nueva = LineaRemision(
                tenant_id=ctx.tenant_id, remision_id=rem.id, numero_linea=i,
                producto_id=ln["producto_id"], presentacion=ln["presentacion"],
                cantidad_solicitada=ln["cantidad_solicitada"], precio_unitario=precio,
                importe=importe, iva_importe=calc["iva_importe"],
                ieps_importe=calc["ieps_importe"], notas=ln.get("notas"),
            )
            # Inventario sin cambios: hereda la reserva de la línea equivalente.
            if era_confirmada and not inv_cambio:
                heredadas = reserva_por_firma.get(_firma(ln["producto_id"], ln["presentacion"], ln["cantidad_solicitada"]))
                if heredadas:
                    nueva.lote_id, nueva.cantidad_surtida = heredadas.pop()
            db.add(nueva)
        rem.subtotal = subtotal
        rem.iva = iva_total
        rem.ieps = ieps_total
        # Reedición de una CONFIRMADA con cambio de inventario: re-reserva con
        # las líneas nuevas (queda CONFIRMADA). Sin existencia → 422 y revierte.
        if era_confirmada and inv_cambio:
            db.flush()
            db.refresh(rem)                          # recarga rem.lineas con las nuevas
            reservar_stock_remision(db, ctx, rem, permitir_negativos=permitir_negativos)

    rem.total = (rem.subtotal or _ZERO) - (rem.descuento or _ZERO) + (rem.iva or _ZERO) + (rem.ieps or _ZERO)
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


def reservar_stock_remision(
    db: Session,
    ctx: AuthContext,
    rem: Remision,
    *,
    permitir_negativos: bool = False,
    pesos: dict | None = None,
) -> None:
    """Reserva inventario para una remisión BORRADOR y la deja CONFIRMADA.

    Cada línea trae presentación + cantidad; se reserva el equivalente en unidad
    base (`disponible → reservada`), se estampa la cantidad reservada en la línea
    (`cantidad_surtida`/`lote_id`) para que la cancelación libere exactamente lo
    mismo, y se registra un movimiento SALIDA_REMISION por línea. Lanza 422 si
    falta existencia y no se autorizó sobregiro. Compartida por el endpoint de
    confirmar y por facturar-desde-remisiones (auto-confirma el borrador).
    """
    if rem.almacen_id is None:
        raise HTTPException(status_code=422, detail="La remisión requiere un almacén para reservar inventario")
    pesos = pesos or {}
    prod_ids = {ln.producto_id for ln in rem.lineas}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}

    for ln in rem.lineas:
        factor = presentacion_factor(productos.get(ln.producto_id), ln.presentacion)
        real = pesos.get(ln.id)
        base_qty = real if real is not None else (ln.cantidad_solicitada * factor)
        # Con sobregiro autorizado creamos el lote por defecto si no existe, para
        # poder reservar contra él (la disponible quedará en negativo).
        lote = resolve_lote(
            db, ctx.tenant_id, ln.producto_id, rem.almacen_id,
            numero_lote=None, create=permitir_negativos,
        )
        if lote is None or (not permitir_negativos and lote.cantidad_disponible < base_qty):
            raise HTTPException(
                status_code=422,
                detail=f"Existencia insuficiente para la línea {ln.numero_linea}",
            )
        # Decisión de negocio 2026-07-29: confirmar = el camión salió → salida
        # directa de disponible, SIN cubeta "reservada" (siempre queda en 0).
        # El stamp lote_id/cantidad_surtida en la línea permite restituir
        # exactamente lo mismo si la remisión se cancela.
        lote.cantidad_disponible = lote.cantidad_disponible - base_qty
        ln.lote_id = lote.id
        ln.cantidad_surtida = base_qty
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "SALIDA_REMISION", -base_qty,
            ref_tipo="REMISION", ref_id=rem.id, motivo=f"Reserva remisión {rem.folio_interno}",
        ))

    rem.estado = "CONFIRMADA"
    rem.updated_by = ctx.user_id


@router.post("/{rem_id}/confirmar", response_model=RemisionDetailOut)
def confirmar_remision(
    rem_id: UUID,
    payload: ConfirmarRemisionIn | None = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado != "BORRADOR":
        raise HTTPException(status_code=409, detail=f"Solo se confirma desde BORRADOR (actual: {rem.estado})")

    # Optional per-line real weights (catch-weight); override the estimate.
    pesos = {p.linea_id: p.cantidad_base for p in (payload.pesos or [])} if payload else {}
    # Sobregiro autorizado: confirma sin existencia suficiente (inventario negativo).
    permitir_negativos = bool(payload.permitir_negativos) if payload else False

    reservar_stock_remision(db, ctx, rem, permitir_negativos=permitir_negativos, pesos=pesos)
    db.flush()
    db.refresh(rem)
    return rem


@router.post("/{rem_id}/cancelar", response_model=RemisionDetailOut)
def cancelar_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "CANCELADA":
        raise HTTPException(status_code=409, detail="La remisión ya está cancelada")
    if rem.estado == "FACTURADA":
        raise HTTPException(
            status_code=409,
            detail="La remisión está facturada; cancela su factura primero (eso libera el inventario y permite refacturar)",
        )

    if rem.estado == "CONFIRMADA":
        _liberar_reservas(db, ctx, rem, motivo=f"Cancelación remisión {rem.folio_interno}")

    rem.estado = "CANCELADA"
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


@router.post("/importar-preview")
def importar_preview(
    archivo: UploadFile = File(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Importación masiva estilo SAE: parsea el Excel, agrupa por FOLIO (una
    remisión por folio del archivo) y cruza cliente (código) y productos (CLAVE/
    SKU exacto; si no, candidatos del cruce). NO crea nada: la UI muestra el
    preview, el usuario resuelve lo no cruzado y crea con POST /remisiones."""
    _MAX = 5 * 1024 * 1024
    data = archivo.file.read(_MAX + 1)
    if len(data) > _MAX:
        raise HTTPException(status_code=422, detail="El archivo no debe exceder 5 MB")
    try:
        grupos = agrupar_por_folio(parsear_excel(data, archivo.filename or "archivo"))
    except ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if len(grupos) > 200:
        raise HTTPException(status_code=422, detail="Máximo 200 remisiones por archivo")

    # Cruce de clientes por código (exacto y sin ceros a la izquierda).
    clientes = db.query(Cliente).filter(Cliente.deleted_at.is_(None)).all()
    por_codigo: dict[str, Cliente] = {}
    for c in clientes:
        cod = (c.codigo or "").strip().upper()
        if cod:
            por_codigo.setdefault(cod, c)
            por_codigo.setdefault(normalizar_folio(cod).upper(), c)

    # Cruce de productos por CLAVE = SKU (exacto); si no, candidatos del cruce.
    catalogo = productos_activos(db)
    por_sku = {p.sku.strip().upper(): p for p in catalogo if p.sku}

    sin_cliente = 0
    sin_producto = 0
    out = []
    for g in grupos:
        cod = str(g["cliente"]).strip().upper()
        cli = por_codigo.get(cod) or por_codigo.get(normalizar_folio(cod).upper())
        if cli is None:
            sin_cliente += 1
        lineas = []
        for ln in g["lineas"]:
            prod = por_sku.get(ln["clave"].strip().upper())
            candidatos = []
            if prod is None:
                sin_producto += 1
                candidatos = [
                    {"producto_id": c.producto_id, "sku": c.sku, "nombre": c.nombre, "score": c.score}
                    for c in buscar(db, ctx.tenant_id, ln["clave"], limit=3, prods=catalogo)
                ]
            lineas.append({
                "clave": ln["clave"],
                "cantidad": ln["cantidad"],
                "precio": ln["precio"],
                "producto_id": prod.id if prod else None,
                "producto_nombre": prod.nombre if prod else None,
                "presentacion": (prod.presentacion_default if prod else None),
                "candidatos": candidatos,
            })
        out.append({
            "folio_ref": g["folio_ref"],
            "fecha": g["fecha"],
            "su_pedido": g["su_pedido"],
            "observaciones": g["observaciones"],
            "cliente_codigo": g["cliente"],
            "cliente_id": cli.id if cli else None,
            "cliente_nombre": cli.legal_name if cli else None,
            "lineas": lineas,
        })
    return {
        "grupos": out,
        "clientes_sin_cruce": sin_cliente,
        "productos_sin_cruce": sin_producto,
    }


class DevolucionLineaIn(BaseModel):
    linea_id: UUID
    # En unidades de la PRESENTACIÓN de la línea (igual que se capturó).
    cantidad: Decimal = PydField(gt=0)


class DevolucionIn(BaseModel):
    lineas: list[DevolucionLineaIn] = PydField(min_length=1, max_length=200)
    motivo: Optional[str] = PydField(default=None, max_length=500)


@router.post("/{rem_id}/devolucion", response_model=RemisionDetailOut)
def devolucion_remision(
    rem_id: UUID,
    payload: DevolucionIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Devolución parcial o total de una remisión CONFIRMADA (el camión ya salió).

    Decisión 2026-07-29: la devolución AJUSTA la remisión a lo neto entregado —
    reduce cantidades/importes/impuestos de las líneas (la factura posterior sale
    por lo neto), regresa el inventario a disponible (ENTRADA_DEVOLUCION) y deja
    el rastro en `devoluciones`.
    """
    rem = get_or_404(db, Remision, rem_id, for_update=True)
    if rem.estado != "CONFIRMADA":
        detalle = {
            "BORRADOR": "La remisión está en borrador: aún no sale mercancía que devolver",
            "FACTURADA": "La remisión ya está facturada; cancela la factura primero",
            "CANCELADA": "La remisión está cancelada",
        }.get(rem.estado, f"No se puede devolver en estado {rem.estado}")
        raise HTTPException(status_code=409, detail=detalle)

    por_id = {l.id: l for l in rem.lineas}
    fiscal = _fiscal_por_producto(db, [l.producto_id for l in rem.lineas])

    dev = Devolucion(
        tenant_id=ctx.tenant_id, remision_id=rem.id,
        motivo=(payload.motivo or "").strip() or None, created_by=ctx.user_id,
    )
    db.add(dev)
    db.flush()

    for item in payload.lineas:
        ln = por_id.get(item.linea_id)
        if ln is None:
            raise HTTPException(status_code=422, detail="Una línea no pertenece a la remisión")
        if item.cantidad > ln.cantidad_solicitada:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"La línea {ln.numero_linea} tiene {ln.cantidad_solicitada} y se "
                    f"intentan devolver {item.cantidad}"
                ),
            )
        prod, esq = fiscal.get(ln.producto_id, (None, None))
        factor = presentacion_factor(prod, ln.presentacion)
        base = item.cantidad * factor
        if ln.cantidad_surtida is not None:
            # Catch-weight: nunca regresar al inventario más de lo que salió.
            base = min(base, Decimal(ln.cantidad_surtida))

        # Inventario: regresa a disponible en el lote del que salió.
        lote = None
        if ln.lote_id is not None:
            lote = (
                db.query(LoteInventario)
                .filter(LoteInventario.id == ln.lote_id)
                .with_for_update()
                .one_or_none()
            )
        if lote is None:
            lote = resolve_lote(
                db, ctx.tenant_id, ln.producto_id, rem.almacen_id, numero_lote=None, create=True
            )
        lote.cantidad_disponible = lote.cantidad_disponible + base
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "ENTRADA_DEVOLUCION", base,
            ref_tipo="REMISION", ref_id=rem.id,
            motivo=f"Devolución remisión {rem.folio_interno}",
        ))

        # La línea queda por lo NETO entregado (misma regla fiscal del alta).
        ln.cantidad_solicitada = ln.cantidad_solicitada - item.cantidad
        if ln.cantidad_surtida is not None:
            ln.cantidad_surtida = max(_ZERO, Decimal(ln.cantidad_surtida) - base)
        ln.importe = ln.cantidad_solicitada * ln.precio_unitario
        calc = calcular_linea_producto(prod, esq, ln.importe, ln.cantidad_solicitada)
        ln.iva_importe = calc["iva_importe"]
        ln.ieps_importe = calc["ieps_importe"]

        db.add(LineaDevolucion(
            tenant_id=ctx.tenant_id, devolucion_id=dev.id, linea_remision_id=ln.id,
            producto_id=ln.producto_id, presentacion=ln.presentacion,
            cantidad=item.cantidad, cantidad_base=base,
        ))

    rem.subtotal = sum((l.importe or _ZERO for l in rem.lineas), _ZERO)
    rem.iva = sum((l.iva_importe or _ZERO for l in rem.lineas), _ZERO)
    rem.ieps = sum((l.ieps_importe or _ZERO for l in rem.lineas), _ZERO)
    rem.total = rem.subtotal - (rem.descuento or _ZERO) + rem.iva + rem.ieps
    rem.updated_by = ctx.user_id
    db.flush()
    db.refresh(rem)
    return rem


class PreviewLineaIn(BaseModel):
    producto_id: UUID
    cantidad: Decimal = PydField(gt=0)
    precio_unitario: Decimal = PydField(ge=0)
    presentacion: Optional[str] = PydField(default=None, max_length=20)


class PreviewTotalesIn(BaseModel):
    lineas: list[PreviewLineaIn] = PydField(min_length=1, max_length=500)
    descuento: Decimal = PydField(default=Decimal("0"), ge=0)


@router.post("/preview-totales")
def preview_totales(
    payload: PreviewTotalesIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Totales calculados por el SERVIDOR para el preview del alta (remisión o
    factura directa): el frontend nunca deriva impuestos por su cuenta — regla
    "el backend calcula todo" (2026-07-29). Mismo cerebro que el documento real."""
    fiscal = _fiscal_por_producto(db, [ln.producto_id for ln in payload.lineas])
    subtotal = _ZERO
    iva = _ZERO
    ieps = _ZERO
    por_linea = []
    for ln in payload.lineas:
        importe = ln.cantidad * ln.precio_unitario
        subtotal += importe
        prod, esq = fiscal.get(ln.producto_id, (None, None))
        calc = calcular_linea_producto(prod, esq, importe, ln.cantidad)
        iva += calc["iva_importe"]
        ieps += calc["ieps_importe"]
        por_linea.append({
            "importe": importe,
            "iva_importe": calc["iva_importe"],
            "ieps_importe": calc["ieps_importe"],
        })
    return {
        "subtotal": subtotal,
        "descuento": payload.descuento,
        "iva": iva,
        "ieps": ieps,
        "total": subtotal - payload.descuento + iva + ieps,
        "lineas": por_linea,      # mismo orden que el payload
    }


class EnviarRemisionIn(BaseModel):
    to: Optional[str] = PydField(default=None, max_length=1000)
    mensaje: Optional[str] = PydField(default=None, max_length=5000)


class EnviarRemisionesLoteIn(BaseModel):
    """Un solo correo con varias remisiones (todas del mismo cliente)."""
    ids: list[UUID] = PydField(min_length=1, max_length=100)
    to: Optional[str] = PydField(default=None, max_length=1000)
    mensaje: Optional[str] = PydField(default=None, max_length=5000)


def _fmt_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _validar_destinatarios(destinatarios: list[str]) -> list[str]:
    """Cada token debe ser un correo válido: un valor arbitrario llegaría hasta
    el header `To` del SMTP (502 críptico en el mejor caso, inyección de headers
    en el peor)."""
    limpios: list[str] = []
    for d in destinatarios:
        try:
            limpios.append(validate_email(d, check_deliverability=False).normalized)
        except EmailNotValidError:
            raise HTTPException(status_code=422, detail=f"Correo inválido: {d}")
    return limpios


def _build_remision_html(rem: Remision, cliente_nombre: str, lineas: list) -> str:
    filas = []
    total = _ZERO
    for ln in lineas:
        importe = ln.importe or _ZERO
        total += importe
        filas.append(
            "<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(ln.producto_nombre or '')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(str(ln.presentacion or ''))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{ln.cantidad_solicitada:,.2f}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_money(ln.precio_unitario or _ZERO)}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_money(importe)}</td>"
            "</tr>"
        )
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#222'>"
        f"<h2 style='margin:0 0 4px'>Remisión {html_mod.escape(rem.folio_interno or '')}</h2>"
        f"<p style='margin:0 0 2px'><strong>Cliente:</strong> {html_mod.escape(cliente_nombre)}</p>"
        f"<p style='margin:0 0 16px'><strong>Fecha:</strong> {rem.fecha_remision}</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:6px 10px;text-align:left'>Producto</th>"
        "<th style='padding:6px 10px;text-align:left'>Presentación</th>"
        "<th style='padding:6px 10px;text-align:right'>Cantidad</th>"
        "<th style='padding:6px 10px;text-align:right'>Precio</th>"
        "<th style='padding:6px 10px;text-align:right'>Importe</th>"
        "</tr></thead><tbody>"
        + "".join(filas)
        + "</tbody></table>"
        + (
            f"<p style='margin:12px 0 0;text-align:right;font-size:13px;color:#555'>"
            f"Subtotal: {_fmt_money(rem.subtotal or total)}"
            + (f" · IVA: {_fmt_money(rem.iva)}" if rem.iva and Decimal(rem.iva) > 0 else "")
            + (f" · IEPS: {_fmt_money(rem.ieps)}" if rem.ieps and Decimal(rem.ieps) > 0 else "")
            + "</p>"
        )
        + f"<p style='margin:4px 0 0;text-align:right;font-size:16px'>"
        f"<strong>Total: {_fmt_money(rem.total if rem.total is not None else total)}</strong></p>"
        "</div>"
    )


def _build_remisiones_lote_html(
    rems: list, cliente_nombre: str, mensaje: Optional[str], emisor_nombre: str
) -> str:
    """Cuerpo del correo cuando se envían VARIAS remisiones de un cliente en un
    solo correo: saludo, mensaje opcional, un resumen (folio/fecha/total) y el
    total general. El detalle de cada remisión va en su PDF adjunto."""
    filas = []
    total_general = _ZERO
    for rem in rems:
        # Total oficial del documento (con impuestos) — el mismo de lista/PDF.
        total_rem = rem.total if rem.total is not None else sum(
            (ln.importe or _ZERO for ln in rem.lineas), _ZERO
        )
        total_general += total_rem
        filas.append(
            "<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(rem.folio_interno or '')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{rem.fecha_remision}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_money(total_rem)}</td>"
            "</tr>"
        )
    n = len(rems)
    etiqueta = "remisión" if n == 1 else "remisiones"
    mensaje_html = (
        "<div style='background:#f5f7ff;border-left:3px solid #4f6bed;"
        "padding:10px 14px;margin:0 0 16px;border-radius:4px;white-space:pre-line'>"
        f"{html_mod.escape(mensaje)}</div>"
        if mensaje else ""
    )
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#222;max-width:640px'>"
        f"<h2 style='margin:0 0 4px'>{html_mod.escape(emisor_nombre)}</h2>"
        f"<p style='margin:0 0 16px;color:#555'>Estimado(a) <strong>{html_mod.escape(cliente_nombre)}</strong>:</p>"
        + mensaje_html
        + f"<p style='margin:0 0 16px'>Le compartimos {n} {etiqueta}. "
        "El detalle completo de cada una se encuentra en el PDF adjunto correspondiente.</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:6px 10px;text-align:left'>Remisión</th>"
        "<th style='padding:6px 10px;text-align:left'>Fecha</th>"
        "<th style='padding:6px 10px;text-align:right'>Total</th>"
        "</tr></thead><tbody>"
        + "".join(filas)
        + "</tbody></table>"
        f"<p style='margin:16px 0 0;text-align:right;font-size:16px'>"
        f"<strong>Total general: {_fmt_money(total_general)}</strong></p>"
        "<p style='margin:24px 0 0;color:#999;font-size:12px'>"
        "Correo enviado automáticamente por Facturador.</p>"
        "</div>"
    )


@router.post("/{rem_id}/enviar")
def enviar_remision(
    rem_id: UUID,
    payload: EnviarRemisionIn | None = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "CANCELADA":
        raise HTTPException(status_code=409, detail="La remisión está cancelada; no se envía por correo")
    cliente = db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one_or_none()

    # Destinatarios: los que vengan en el payload (uno o varios, coma/espacio) o,
    # en su defecto, los correos del cliente (`correos` array, o el `email` legado).
    destinatarios: list[str] = []
    if payload and payload.to:
        destinatarios = [c for c in payload.to.replace(",", " ").split() if c]
    if not destinatarios and cliente is not None:
        dom = cliente.domicilio_fiscal or {}
        correos = dom.get("correos")
        if isinstance(correos, list):
            destinatarios = [str(c).strip() for c in correos if str(c).strip()]
        elif dom.get("email"):
            destinatarios = [str(dom["email"]).strip()]
    if not destinatarios:
        raise HTTPException(status_code=422, detail="El cliente no tiene correo")
    destinatarios = _validar_destinatarios(destinatarios)

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one_or_none()
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="El correo no está configurado")

    # Nombres de producto (como get_remision).
    prod_ids = {ln.producto_id for ln in rem.lineas}
    names = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    for ln in rem.lineas:
        ln.producto_nombre = names.get(ln.producto_id)

    cliente_nombre = cliente.legal_name if cliente else ""
    mensaje_html = f"<p>{html_mod.escape(payload.mensaje)}</p>" if (payload and payload.mensaje) else ""
    html = mensaje_html + _build_remision_html(rem, cliente_nombre, rem.lineas)

    # Se adjunta el PDF de la remisión (mismo diseño que la factura).
    folio = rem.folio_interno or ""
    pdf = build_remision_pdf(rem, tenant, cliente, names)
    attachments: list[tuple[str, bytes, str]] = [(f"{folio}.pdf", pdf, "application/pdf")]

    try:
        email_service.send_email(
            email_service.smtp_config(tenant),
            destinatarios,
            f"Remisión {folio}",
            html,
            attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ", ".join(destinatarios)}


@router.post("/enviar-lote")
def enviar_remisiones_lote(
    payload: EnviarRemisionesLoteIn = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Un SOLO correo con todas las remisiones indicadas (un PDF adjunto por
    remisión). Pensado para el envío masivo agrupado por cliente: cada cliente
    recibe un único correo con todas sus remisiones."""
    if not payload.ids:
        raise HTTPException(status_code=422, detail="Sin remisiones para enviar")
    rems = (
        db.query(Remision)
        .filter(Remision.id.in_(payload.ids), Remision.deleted_at.is_(None))
        .order_by(Remision.folio_interno)
        .all()
    )
    if not rems:
        raise HTTPException(status_code=404, detail="No se encontraron remisiones")
    canceladas = [r.folio_interno for r in rems if r.estado == "CANCELADA"]
    if canceladas:
        raise HTTPException(
            status_code=409,
            detail="Remisiones canceladas en la selección: " + ", ".join(canceladas),
        )

    # El envío masivo agrupa por cliente, así que todas deben ser del mismo.
    cli_ids = {r.cliente_facturacion_id for r in rems}
    if len(cli_ids) > 1:
        raise HTTPException(status_code=422, detail="Las remisiones deben ser del mismo cliente")
    cliente = db.query(Cliente).filter(Cliente.id == next(iter(cli_ids))).one_or_none()

    # Destinatarios: los del payload (uno o varios, coma/espacio) o, en su
    # defecto, los correos del cliente (`correos` array, o el `email` legado).
    destinatarios: list[str] = []
    if payload.to:
        destinatarios = [c for c in payload.to.replace(",", " ").split() if c]
    if not destinatarios and cliente is not None:
        dom = cliente.domicilio_fiscal or {}
        correos = dom.get("correos")
        if isinstance(correos, list):
            destinatarios = [str(c).strip() for c in correos if str(c).strip()]
        elif dom.get("email"):
            destinatarios = [str(dom["email"]).strip()]
    if not destinatarios:
        raise HTTPException(status_code=422, detail="El cliente no tiene correo")
    destinatarios = _validar_destinatarios(destinatarios)

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one_or_none()
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="El correo no está configurado")

    # Nombres de producto de todas las remisiones (para el cuerpo y los PDFs).
    prod_ids = {ln.producto_id for r in rems for ln in r.lineas}
    names = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    for r in rems:
        for ln in r.lineas:
            ln.producto_nombre = names.get(ln.producto_id)

    cliente_nombre = cliente.legal_name if cliente else ""
    emisor_nombre = (tenant.trade_name or tenant.legal_name) if tenant else "Facturador"
    mensaje = (payload.mensaje or "").strip() or None
    html = _build_remisiones_lote_html(rems, cliente_nombre, mensaje, emisor_nombre)

    # Un PDF adjunto por remisión.
    attachments: list[tuple[str, bytes, str]] = []
    for r in rems:
        pdf = build_remision_pdf(r, tenant, cliente, names)
        folio = r.folio_interno or str(r.id)
        attachments.append((f"{folio}.pdf", pdf, "application/pdf"))

    n = len(rems)
    asunto = (
        f"Remisión {rems[0].folio_interno}" if n == 1
        else f"{n} remisiones — {emisor_nombre}"
    )
    try:
        email_service.send_email(
            email_service.smtp_config(tenant),
            destinatarios,
            asunto,
            html,
            attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ", ".join(destinatarios), "remisiones": n}


@router.get("/{rem_id}/pdf")
def remision_pdf(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """PDF de la remisión (mismo diseño que la factura, marcado NO FISCAL)."""
    rem = get_or_404(db, Remision, rem_id)
    cliente = db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one_or_none()
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    prod_ids = {ln.producto_id for ln in rem.lineas}
    nombres = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    pdf = build_remision_pdf(rem, tenant, cliente, nombres)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{rem.folio_interno}.pdf"'},
    )


@router.delete("/{rem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "FACTURADA":
        raise HTTPException(status_code=409, detail="La remisión está facturada; cancela su factura antes de eliminarla")
    if rem.estado == "CONFIRMADA":
        raise HTTPException(status_code=409, detail="Cancela la remisión antes de eliminarla (libera inventario)")
    rem.deleted_at = func.now()
    db.flush()
    return None

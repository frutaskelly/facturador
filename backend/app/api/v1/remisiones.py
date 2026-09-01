"""Remisiones — CRUD + confirmar/cancelar con efecto en inventario (Phase 4e).

Reads gated by `menu:remisiones`; writes by `remision:gestionar`.

Lifecycle: BORRADOR (editable, no stock effect) → RESERVADO (trae folio de
factura de SAE: mercancía comprometida con un comprobante de fuera; tampoco
mueve inventario y se edita igual) → CONFIRMADA (salida directa:
disponible baja, one SALIDA_REMISION movement per line; la línea guarda
lote_id/cantidad_surtida para poder restituir) → CANCELADA (restituye:
disponible sube, CANCELACION_REMISION). A draft cancels with no inventory
effect. La salida usa el lote default de (producto, almacén) — lot-selection/
FIFO is a later refinement. `cantidad_reservada` ya no se usa (decisión
2026-07-29: confirmar = el camión salió; la columna queda en 0).
"""
from __future__ import annotations

import html as html_mod
import re
import unicodedata

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from rapidfuzz import fuzz
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
    OCRecibida,
    Producto,
    ProductoCliente,
    Proyecto,
    Remision,
    Sucursal,
    Tenant,
)
from ...services import email as email_service
from ...services.fiscal import calcular_linea_producto
from ...services.importar_remisiones import (
    ImportError_,
    agrupar_por_folio,
    normalizar_folio,
    normalizar_nombre,
    parsear_excel,
)
from ...services.producto_match import (
    alias_del_tenant,
    buscar,
    normalizar,
    normalizar_catalogo,
    productos_activos,
)
from ...services.precios import resolver_precio
from ...services.series import consumir_folio, resolver_serie, siguiente_folio
from ...services.sucursales import es_sucursal_de
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


def _norm_codigo(v) -> str:
    """Clave del cliente comparable: sin espacios, sin acentos y en mayúsculas.

    El master escribe "AJO -FRUT-017" donde el catálogo guarda "AJO-FRUT-017", y
    la ñ va y viene entre los dos ("PIÑA-FRUT-350" = "PINA-FRUT-350"). El cruce
    sigue siendo exacto, solo que no castiga el espaciado ni la acentuación.
    """
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", "", s).upper()


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


def _adjuntar_oc(db: Session, rems: list) -> None:
    """Cuelga de cada remisión su OC original, en DOS consultas para toda la
    página: la que la generó (`remision_id`) y, si no la hay, la que traiga el
    mismo folio del cliente (las importadas del master no pasaron por la
    bandeja, pero comparten "su pedido")."""
    if not rems:
        return
    ids = [r.id for r in rems]
    folios = {r.su_pedido for r in rems if r.su_pedido}
    por_remision: dict = {}
    for oc in db.query(OCRecibida).filter(OCRecibida.remision_id.in_(ids)).all():
        por_remision.setdefault(oc.remision_id, oc)
    por_folio: dict = {}
    if folios:
        for oc in db.query(OCRecibida).filter(OCRecibida.folio_externo.in_(folios)).all():
            por_folio.setdefault(normalizar_folio(oc.folio_externo), oc)
    for r in rems:
        oc = por_remision.get(r.id) or (
            por_folio.get(normalizar_folio(r.su_pedido)) if r.su_pedido else None
        )
        if oc is None:
            continue
        r.oc_id = oc.id
        r.oc_archivo_url = oc.archivo_url
        r.oc_archivo_nombre = oc.archivo_nombre


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
    if ctx.cliente_scope:
        # Candado del portal: solo documentos de SUS clientes.
        query = query.filter(Remision.cliente_facturacion_id.in_(ctx.cliente_scope))
    if estado:
        query = query.filter(Remision.estado == estado)
    if cliente_id is not None:
        query = query.filter(Remision.cliente_facturacion_id == cliente_id)
    if fecha_desde:
        query = query.filter(Remision.fecha_remision >= fecha_desde)
    if fecha_hasta:
        query = query.filter(Remision.fecha_remision <= fecha_hasta)
    query = query.order_by(Remision.fecha_remision.desc(), Remision.folio_interno.desc())
    return paginate(query, RemisionOut, limit, offset,
                    preparar=lambda rows: _adjuntar_oc(db, rows))


@router.post("", response_model=RemisionDetailOut, status_code=status.HTTP_201_CREATED)
def create_remision(
    payload: RemisionCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_facturacion_id, "cliente_facturacion_id")
    ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")
    ensure_fk(db, ListaPrecios, payload.lista_precios_id, "lista_precios_id")
    ensure_fk(db, Proyecto, payload.proyecto_id, "proyecto_id")
    if payload.sucursal_id is not None:
        get_or_404(db, Sucursal, payload.sucursal_id)
        if not es_sucursal_de(db, payload.sucursal_id, payload.cliente_facturacion_id):
            raise HTTPException(status_code=422, detail="El cliente de la remisión no se surte de esa sucursal")
    for ln in payload.lineas:
        ensure_fk(db, Producto, ln.producto_id, "producto_id")

    # La serie se resuelve ANTES de precificar, no al foliar: además del folio,
    # decide qué lista de precios aplica (el negocio pacta por serie). Se
    # resuelve una vez y se guarda; `_next_folio` recibe ya la decidida, así
    # nadie puede foliar con una serie distinta de la que fijó los precios.
    serie = resolver_serie(
        db, ctx.tenant_id, "REMISION",
        serie_id=payload.serie_id,
        sucursal_id=payload.sucursal_id,
        cliente_id=payload.cliente_facturacion_id,
    )

    rem = Remision(
        tenant_id=ctx.tenant_id,
        folio_interno=_next_folio(
            db, ctx.tenant_id,
            sucursal_id=payload.sucursal_id,
            cliente_id=payload.cliente_facturacion_id,
            serie_id=serie.id if serie is not None else payload.serie_id,
        ),
        cliente_facturacion_id=payload.cliente_facturacion_id,
        almacen_id=payload.almacen_id,
        sucursal_id=payload.sucursal_id,
        lista_precios_id=payload.lista_precios_id,
        proyecto_id=payload.proyecto_id,
        serie_id=serie.id if serie is not None else None,
        fecha_remision=payload.fecha_remision or date.today(),
        fecha_entrega=payload.fecha_entrega,
        canal=payload.canal,
        descuento=payload.descuento,
        notas=payload.notas,
        nota_entrega=payload.nota_entrega,
        factura_sae=(payload.factura_sae or "").strip() or None,
        su_pedido=(payload.su_pedido or "").strip() or None,
        # Traer folio de factura de SAE = mercancía ya comprometida con un
        # comprobante de fuera: nace RESERVADA, no como borrador cualquiera.
        estado="RESERVADO" if (payload.factura_sae or "").strip() else "BORRADOR",
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
                serie_id=rem.serie_id, proyecto_id=rem.proyecto_id,
                lista_id=rem.lista_precios_id,
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
    q = db.query(Remision).filter(
        Remision.id.in_(id_list), Remision.deleted_at.is_(None)
    )
    if ctx.cliente_scope:
        q = q.filter(Remision.cliente_facturacion_id.in_(ctx.cliente_scope))
    rems = q.order_by(Remision.folio_interno).all()
    if not rems:
        raise HTTPException(status_code=404, detail="No se encontraron remisiones")
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    por_rem = _nombres_para_pdf(db, rems)
    cli_ids = {r.cliente_facturacion_id for r in rems}
    clientes = {c.id: c for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()}
    items = [(r, clientes.get(r.cliente_facturacion_id), por_rem[r.id]) for r in rems]
    pdf = build_remisiones_pdf(items, tenant)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="remisiones.pdf"'},
    )


def _nombres_para_pdf(db: Session, rems: list[Remision]) -> dict:
    """{remision_id: {producto_id: texto impreso}} para PDFs y correos al cliente.

    El documento que el cliente firma trae SU nombre y SU clave del producto
    (producto_clientes — la misma capa que ya usa el CFDI en services/cfdi.py),
    con el nombre interno de respaldo. Formato: "CLAVE — NOMBRE DEL CLIENTE".
    Todo precargado en dos consultas: un lote de 200 remisiones no puede hacer
    un SELECT por línea.
    """
    prod_ids = {ln.producto_id for r in rems for ln in r.lineas}
    interno = dict(
        db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all()
    ) if prod_ids else {}
    cli_ids = {r.cliente_facturacion_id for r in rems if r.cliente_facturacion_id}
    pcs = {}
    if cli_ids and prod_ids:
        for pc in (
            db.query(ProductoCliente)
            .filter(
                ProductoCliente.cliente_id.in_(cli_ids),
                ProductoCliente.producto_id.in_(prod_ids),
            )
            .all()
        ):
            pcs[(pc.cliente_id, pc.producto_id)] = pc
    out: dict = {}
    for r in rems:
        nombres: dict = {}
        for ln in r.lineas:
            pc = pcs.get((r.cliente_facturacion_id, ln.producto_id))
            base = (pc.nombre_cliente or "").strip() if pc else ""
            base = base or interno.get(ln.producto_id) or str(ln.producto_id)
            codigo = (pc.codigo_cliente or "").strip() if pc else ""
            nombres[ln.producto_id] = f"{codigo} — {base}" if codigo else base
        out[r.id] = nombres
    return out


@router.get("/{rem_id}", response_model=RemisionDetailOut)
def get_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    rem = get_or_404(db, Remision, rem_id)
    if not ctx.cliente_permitido(rem.cliente_facturacion_id):
        raise HTTPException(status_code=404, detail="Remisión no encontrada")
    prod_ids = {ln.producto_id for ln in rem.lineas}
    names = dict(db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(prod_ids)).all())
    for ln in rem.lineas:
        ln.producto_nombre = names.get(ln.producto_id)
    _adjuntar_oc(db, [rem])
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
    if rem.estado not in ("BORRADOR", "RESERVADO", "CONFIRMADA"):
        raise HTTPException(
            status_code=409,
            detail="Solo se puede editar una remisión en borrador, reservada o confirmada",
        )
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
    if data.get("proyecto_id") is not None:
        ensure_fk(db, Proyecto, data["proyecto_id"], "proyecto_id")
    if data.get("cliente_facturacion_id") is not None:
        ensure_fk(db, Cliente, data["cliente_facturacion_id"], "cliente_facturacion_id")

    # Cliente/sucursal coherentes (el cliente debe surtirse de la plaza). Se
    # valida también la sucursal HEREDADA: cambiar solo el cliente no debe dejar
    # una plaza que no lo surte (precio/serie se resolverían con datos mezclados).
    nuevo_cliente = data.get("cliente_facturacion_id", rem.cliente_facturacion_id)
    sucursal_efectiva = data["sucursal_id"] if "sucursal_id" in data else rem.sucursal_id
    if sucursal_efectiva is not None and ("sucursal_id" in data or "cliente_facturacion_id" in data):
        get_or_404(db, Sucursal, sucursal_efectiva)
        if not es_sucursal_de(db, sucursal_efectiva, nuevo_cliente):
            if "sucursal_id" in data:
                raise HTTPException(status_code=422, detail="El cliente de la remisión no se surte de esa sucursal")
            raise HTTPException(
                status_code=422,
                detail="El nuevo cliente no se surte de la sucursal actual; cámbiala o quítala en la misma edición",
            )

    for key, value in data.items():
        setattr(rem, key, value)

    # El folio de SAE manda sobre el estado mientras la remisión no haya salido:
    # ponerlo la reserva, quitarlo la regresa a borrador. Una CONFIRMADA o
    # FACTURADA no retrocede — ahí el folio es solo un dato más.
    if "su_pedido" in data:
        rem.su_pedido = (data["su_pedido"] or "").strip() or None
    if "factura_sae" in data:
        rem.factura_sae = (data["factura_sae"] or "").strip() or None
        if rem.factura_sae and rem.estado == "BORRADOR":
            rem.estado = "RESERVADO"
        elif not rem.factura_sae and rem.estado == "RESERVADO":
            rem.estado = "BORRADOR"

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
                    serie_id=rem.serie_id, proyecto_id=rem.proyecto_id,
                    lista_id=rem.lista_precios_id,
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
    if rem.estado not in ("BORRADOR", "RESERVADO"):
        raise HTTPException(
            status_code=409,
            detail=f"Solo se confirma desde BORRADOR o RESERVADO (actual: {rem.estado})",
        )

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
    """Importación masiva (SAE o Master Ordenes): parsea el Excel, agrupa por
    FOLIO (una remisión por folio del archivo) y cruza cliente (código, RFC o
    nombre) y productos (CLAVE/SKU exacto; si no, candidatos del cruce). NO crea
    nada: la UI muestra el preview, el usuario resuelve lo no cruzado y crea con
    POST /remisiones."""
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

    # Cruce de clientes: código (SAE, exacto y sin ceros a la izquierda), RFC o
    # nombre normalizado (Master Ordenes trae "RFC Cliente"/"Nombre Cliente").
    clientes = db.query(Cliente).filter(Cliente.deleted_at.is_(None)).all()
    por_codigo: dict[str, Cliente] = {}
    por_rfc: dict[str, Cliente] = {}
    por_nombre: dict[str, Cliente] = {}
    for c in clientes:
        cod = (c.codigo or "").strip().upper()
        if cod:
            por_codigo.setdefault(cod, c)
            por_codigo.setdefault(normalizar_folio(cod).upper(), c)
        rfc = (c.rfc or "").strip().upper()
        if rfc:
            por_rfc.setdefault(rfc, c)
        for nombre in (c.legal_name, getattr(c, "nombre_comercial", None)):
            nom = normalizar_nombre(nombre)
            if nom:
                por_nombre.setdefault(nom, c)

    # Cruce de productos por CLAVE = SKU (exacto); si no, candidatos del cruce.
    # Alias y catálogo normalizado se cargan UNA vez: sin esto, cada línea sin
    # cruce hace un SELECT de alias y renormaliza el catálogo entero (1,125
    # líneas del master = minutos contra la base en la nube).
    catalogo = productos_activos(db)
    por_sku = {p.sku.strip().upper(): p for p in catalogo if p.sku}
    aliases = alias_del_tenant(db)
    norms = normalizar_catalogo(catalogo)
    cache_candidatos: dict[tuple, list[dict]] = {}
    por_id = {p.id: p for p in catalogo}
    # Código del cliente → producto: el master trae la CLAVE del cliente, no el
    # SKU propio ("AJO -FRUT-017" vs "00000282"). Es el cruce EXACTO y va antes
    # que cualquier parecido de texto.
    #   1) (cliente, código) — lo que ese cliente pidió con esa clave.
    #   2) (código) a secas — la misma clave usada con otro cliente; el esquema
    #      de claves es uno solo, así que sirve mientras no apunte a productos
    #      distintos (ahí se descarta y decide el usuario).
    por_codigo_cliente: dict[tuple, Producto] = {}
    por_codigo: dict[str, Optional[Producto]] = {}
    for pc in db.query(ProductoCliente).all():
        cod = _norm_codigo(pc.codigo_cliente)
        prod_pc = por_id.get(pc.producto_id)
        if not cod or prod_pc is None:
            continue
        por_codigo_cliente.setdefault((pc.cliente_id, cod), prod_pc)
        if cod in por_codigo:
            if por_codigo[cod] is not None and por_codigo[cod].id != prod_pc.id:
                por_codigo[cod] = None      # la misma clave en dos productos: ambigua
        else:
            por_codigo[cod] = prod_pc

    sin_cliente = 0
    sin_producto = 0
    out = []
    for g in grupos:
        cod = str(g["cliente"]).strip().upper()
        rfc = str(g.get("cliente_rfc") or "").strip().upper()
        cli = (
            por_rfc.get(rfc)
            or por_codigo.get(cod)
            or por_codigo.get(normalizar_folio(cod).upper())
            or por_nombre.get(normalizar_nombre(g["cliente"]))
        )
        if cli is None:
            sin_cliente += 1
        lineas = []
        for ln in g["lineas"]:
            clave = ln["clave"].strip().upper()
            cod = _norm_codigo(ln["clave"])
            prod = por_sku.get(clave)
            cruce = "sku" if prod else None
            if prod is None and cli is not None:
                prod = por_codigo_cliente.get((cli.id, cod))
                cruce = "cliente" if prod else None
            if prod is None:
                prod = por_codigo.get(cod)
                cruce = "codigo" if prod else None
            candidatos = []
            if prod is None:
                sin_producto += 1
                # Candidatos por CLAVE y, si el archivo la trae (Master Ordenes),
                # también por DESCRIPCION: el nombre cruza mejor que la clave ajena.
                # El master repite las mismas claves en las 64 órdenes → se cachea
                # el resultado por (clave, descripción).
                # La descripción es el texto que describe la mercancía; la
                # clave es el código del cliente y solo sirve si no hay otra cosa.
                texto_ref = ln.get("descripcion") or ln["clave"]
                key = (ln["clave"], ln.get("descripcion"))
                candidatos = cache_candidatos.get(key)
                if candidatos is None:
                    vistos: set = set()
                    candidatos = []
                    for texto in key:
                        if not texto:
                            continue
                        for c in buscar(db, ctx.tenant_id, texto, limit=3, prods=catalogo,
                                        aliases=aliases, norms=norms):
                            if c.producto_id in vistos:
                                continue
                            vistos.add(c.producto_id)
                            candidatos.append({
                                "producto_id": c.producto_id, "sku": c.sku,
                                "nombre": c.nombre, "score": c.score,
                                "origen": c.origen,
                                # El score del catálogo premia al SUBCONJUNTO
                                # ("TOMATE" saca 100 contra "TOMATE SERRANO"),
                                # necesario porque los nombres vienen cortados a
                                # 30 caracteres. `parecido` compara los textos
                                # COMPLETOS y desempata entre esos empates.
                                "parecido": int(fuzz.ratio(normalizar(texto_ref), normalizar(c.nombre))),
                            })
                    candidatos.sort(key=lambda c: (c["score"], c["parecido"]), reverse=True)
                    candidatos = cache_candidatos[key] = candidatos[:5]
                # Se cruza solo cuando el mejor candidato es inequívoco: score
                # máximo del catálogo, parecido real con el texto completo, y
                # ventaja clara sobre los que empatan en score. Lo dudoso queda
                # en ámbar para que lo resuelva el usuario.
                mejor = candidatos[0] if candidatos else None
                empatados = [c for c in candidatos[1:] if mejor and c["score"] == mejor["score"]]
                if (
                    mejor is not None
                    and mejor["score"] == 100
                    and mejor["parecido"] >= 60
                    and all(mejor["parecido"] - c["parecido"] >= 10 for c in empatados)
                ):
                    prod = por_id.get(mejor["producto_id"])
                    if prod is not None:
                        cruce = "descripcion"
                        sin_producto -= 1
            lineas.append({
                "clave": ln["clave"],
                "cruce": cruce,
                "descripcion": ln.get("descripcion"),
                "unidad": ln.get("unidad"),
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
            "cliente_rfc": g.get("cliente_rfc"),
            "requisicion": g.get("requisicion"),
            "entregar_bodega": g.get("entregar_bodega"),
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
            "RESERVADO": "La remisión está reservada: aún no sale mercancía que devolver",
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
    if ctx.cliente_scope:
        raise HTTPException(status_code=403, detail="Tu usuario no captura documentos")
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

    # Nombres de producto COMO LOS CONOCE EL CLIENTE (catálogo del cliente, con
    # el interno de respaldo): el correo y su PDF los lee él, no el operador.
    names = _nombres_para_pdf(db, [rem])[rem.id]
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

    # Nombres de producto de todas las remisiones (para el cuerpo y los PDFs),
    # como los conoce el cliente — es él quien recibe este correo.
    por_rem = _nombres_para_pdf(db, rems)
    for r in rems:
        for ln in r.lineas:
            ln.producto_nombre = por_rem[r.id].get(ln.producto_id)

    cliente_nombre = cliente.legal_name if cliente else ""
    emisor_nombre = (tenant.trade_name or tenant.legal_name) if tenant else "Facturador"
    mensaje = (payload.mensaje or "").strip() or None
    html = _build_remisiones_lote_html(rems, cliente_nombre, mensaje, emisor_nombre)

    # Un PDF adjunto por remisión.
    attachments: list[tuple[str, bytes, str]] = []
    for r in rems:
        pdf = build_remision_pdf(r, tenant, cliente, por_rem[r.id])
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
    if not ctx.cliente_permitido(rem.cliente_facturacion_id):
        raise HTTPException(status_code=404, detail="Remisión no encontrada")
    cliente = db.query(Cliente).filter(Cliente.id == rem.cliente_facturacion_id).one_or_none()
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    # El papel que firma el cliente trae SU clave y SU nombre del producto
    # (catálogo del cliente), con el interno de respaldo — igual que el CFDI.
    pdf = build_remision_pdf(rem, tenant, cliente, _nombres_para_pdf(db, [rem])[rem.id])
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{rem.folio_interno}.pdf"'},
    )


@router.delete("/{rem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_remision(
    rem_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("remision:eliminar")),
):
    rem = get_or_404(db, Remision, rem_id)
    if rem.estado == "FACTURADA":
        raise HTTPException(status_code=409, detail="La remisión está facturada; cancela su factura antes de eliminarla")
    if rem.estado == "CONFIRMADA":
        raise HTTPException(status_code=409, detail="Cancela la remisión antes de eliminarla (libera inventario)")
    rem.deleted_at = func.now()
    db.flush()
    return None


# ─── Export masivo para SAE (fase espejo de la migración) ────────────────────
# El Facturador genera el archivo que Aspel importa; el layout y las trampas
# (fechas MM/DD, relleno del folio, claves del cliente) viven en
# services/export_sae.py. El folio inicial de cada serie lo CONFIRMA el
# operador (regla D1 del plan): aquí solo se sugiere.

class ExportSaeIn(BaseModel):
    ids: list[UUID] = PydField(min_length=1)
    tipo: str = "FACTURA"                        # FACTURA | PEDIDO
    # {serie: folio_inicial} confirmados por el operador. Solo FACTURA.
    folios: Optional[dict[str, int]] = None
    fecha: Optional[date] = None                 # default: hoy (MM/DD/YYYY en el archivo)


@router.post("/export-sae/preview")
def export_sae_preview(
    body: ExportSaeIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Valida el lote y sugiere folios SIN generar nada: la pantalla muestra
    esto antes de que el operador confirme. Los errores llegan completos (no
    solo el primero) porque el operador corrige todo de una pasada."""
    from ...services import export_sae as svc

    res, _docs = svc.preparar(db, ctx.tenant_id, body.ids, body.tipo)
    return {
        "ok": res.ok, "errores": res.errores, "avisos": res.avisos,
        "empresa": res.empresa, "series": res.series, "remisiones": res.remisiones,
        "fecha_ejemplo": res.fecha_ejemplo,
    }


@router.post("/export-sae")
def export_sae(
    body: ExportSaeIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Genera el .xls masivo (Excel 97-2004). NO estampa folios: los del
    archivo son la propuesta confirmada por el operador, y `factura_sae` lo
    pone el ESPEJO cuando la factura de verdad existe en SAE — un archivo que
    nunca se sube ya no deja folios fantasma en las remisiones."""
    from ...services import export_sae as svc

    res, contenido, nombre = svc.generar(
        db, ctx.tenant_id, body.ids, body.tipo,
        folios=body.folios, fecha=body.fecha,
    )
    if not res.ok or contenido is None:
        raise HTTPException(status_code=422, detail=" · ".join(res.errores) or "lote inválido")
    db.flush()
    return Response(
        content=contenido,
        media_type="application/vnd.ms-excel",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )

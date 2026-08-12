"""Facturas (CFDI 4.0) — generar desde remisión(es) + consulta (Fase 6, P6.1).

Reads gated por `menu:facturas`; writes por `factura:gestionar`. Aquí se genera
el DOCUMENTO con el desglose fiscal (IVA/IEPS/retenciones por concepto). El
timbrado real ante el SAT vía Facturama llega en P6.2.

Cruce: una factura agrupa una o varias remisiones CONFIRMADAS del MISMO cliente
que aún no estén facturadas. Cada concepto respeta el modelo de unidades: para
productos de peso variable factura la cantidad realmente surtida (cantidad_surtida)
en la unidad base; el resto factura la presentación con su unidad SAT.
"""
from __future__ import annotations

import html as html_mod
import logging

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.db import set_role_tenant
from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Almacen,
    Cliente,
    EsquemaImpuesto,
    Factura,
    LineaFactura,
    LoteInventario,
    Merma,
    Producto,
    Remision,
    Tenant,
    TimbradoIntento,
)
from ...schemas.common import Page
from ...schemas.factura import (
    CancelarFacturaIn,
    EnviarFacturaIn,
    FacturaDesdeRemisionesIn,
    FacturaDetailOut,
    FacturaDirectaIn,
    FacturaOut,
    TimbrarIn,
)
from ...services import email as email_service
from ...services.cfdi import build_payload
from ...services.factura_pdf import build_factura_pdf, build_facturas_pdf
from ...services.facturama import FacturamaClient, FacturamaError
from ...services.fiscal import calcular_linea_producto, totales
from ...services.onboarding import compute_status
from ...services.inventario import build_movimiento, presentacion_factor, presentacion_sat, resolve_lote
from ...services.series import consumir_folio, resolver_serie, siguiente_folio
from ._helpers import ensure_fk, get_or_404, paginate
from .remisiones import _validar_destinatarios, reservar_stock_remision

log = logging.getLogger(__name__)

router = APIRouter(prefix="/facturas", tags=["facturas"])

_READ = "menu:facturas"
_WRITE = "factura:gestionar"
ZERO = Decimal("0")


def _next_folio(db: Session, tenant_id, serie: str) -> int:
    """Folio de la serie (contador sin huecos); si la serie no existe, cae a
    max+1 sobre las facturas de esa serie (back-compat)."""
    folio = siguiente_folio(db, tenant_id, codigo=serie, tipo_documento="FACTURA")
    if folio is not None:
        return folio
    mx = 0
    for (f,) in db.query(Factura.folio).filter(Factura.serie == serie).all():
        if f and f > mx:
            mx = f
    return mx + 1


# El desglose fiscal por producto vive en services/fiscal.py (único cerebro,
# compartido con remisiones y el preview de totales).
_fiscal_calc = calcular_linea_producto


def _iniciar_saldo_insoluto(factura) -> None:
    """Al timbrar: una PPD queda por cobrar (saldo = total); PUE / no-PPD en 0.
    Base del estado de cuenta y de los complementos de pago (REP)."""
    factura.saldo_insoluto = Decimal(factura.total) if factura.metodo_pago == "PPD" else ZERO


def _release_remision_stock(db: Session, rems, ctx, factura) -> None:
    """Devuelve a 'disponible' el stock reservado por remisiones CONFIRMADAS/
    FACTURADAS al cancelar su factura (quedan liberadas como BORRADOR)."""
    prod_ids = {ln.producto_id for r in rems for ln in r.lineas if ln.lote_id}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}
    for r in rems:
        if r.estado not in ("CONFIRMADA", "FACTURADA"):
            continue
        for ln in r.lineas:
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
            if ln.cantidad_surtida is not None:
                cantidad = Decimal(ln.cantidad_surtida)
            else:
                cantidad = Decimal(ln.cantidad_solicitada) * presentacion_factor(
                    productos.get(ln.producto_id), ln.presentacion)
            lote.cantidad_disponible = lote.cantidad_disponible + cantidad
            db.add(build_movimiento(
                ctx.tenant_id, ctx.user_id, lote, "CANCELACION_REMISION", cantidad,
                ref_tipo="FACTURA", ref_id=factura.id,
                motivo=f"Cancelación factura {factura.serie}{factura.folio}",
            ))
            # La reserva quedó liberada: limpia el vínculo para evitar reservas
            # huérfanas y dobles liberaciones (al refacturar se vuelve a estampar).
            ln.lote_id = None
            ln.cantidad_surtida = None


def _writeoff_remision_stock(db: Session, rems, ctx, factura) -> None:
    """Da de baja como MERMA el stock reservado por las remisiones al cancelar su
    factura con 'Pérdida por cancelación': la mercancía NO regresa al almacén
    (se libera la reserva sin sumar a disponible) y se registra una merma."""
    prod_ids = {ln.producto_id for r in rems for ln in r.lineas if ln.lote_id}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}
    for r in rems:
        if r.estado not in ("CONFIRMADA", "FACTURADA"):
            continue
        for ln in r.lineas:
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
            if ln.cantidad_surtida is not None:
                cantidad = Decimal(ln.cantidad_surtida)
            else:
                cantidad = Decimal(ln.cantidad_solicitada) * presentacion_factor(
                    productos.get(ln.producto_id), ln.presentacion)
            # La mercancía NO regresa a disponible → se pierde (ya salió al confirmar).
            desc = f"Pérdida por cancelación factura {factura.serie}{factura.folio}"
            db.add(Merma(
                tenant_id=ctx.tenant_id, lote_id=lote.id, cantidad=cantidad,
                motivo="OTRO", descripcion=desc, created_by=ctx.user_id,
            ))
            # Par balanceado: al confirmar ya se emitió SALIDA_REMISION -q (y
            # disponible bajó). Aquí disponible NO cambia, así que se emite
            # +q/-q para que la suma del kardex siga cuadrando con disponible
            # y a la vez quede rastro de que la salida terminó en merma.
            db.add(build_movimiento(
                ctx.tenant_id, ctx.user_id, lote, "CANCELACION_REMISION", cantidad,
                ref_tipo="FACTURA", ref_id=factura.id, motivo=desc,
            ))
            db.add(build_movimiento(
                ctx.tenant_id, ctx.user_id, lote, "MERMA", -cantidad,
                ref_tipo="FACTURA", ref_id=factura.id, motivo=desc,
            ))
            # La reserva quedó consumida como merma: limpia el vínculo.
            ln.lote_id = None
            ln.cantidad_surtida = None


def _descontar_factura_directa(db: Session, ctx, factura) -> None:
    """Al timbrar una factura DIRECTA: descuenta de `disponible` la cantidad base
    de cada línea, del almacén de la factura (permite negativo: sobregiro), y
    estampa el lote en la línea para poder revertir al cancelar."""
    if factura.almacen_id is None:      # facturas desde remisiones no aplican
        return
    lineas = db.query(LineaFactura).filter(LineaFactura.factura_id == factura.id).all()
    for ln in lineas:
        base = Decimal(ln.cantidad_base) if ln.cantidad_base is not None else Decimal(ln.cantidad)
        lote = resolve_lote(
            db, ctx.tenant_id, ln.producto_id, factura.almacen_id,
            numero_lote=None, create=True,   # sobregiro: crea el lote si no existe
        )
        if lote is None:
            continue
        lote.cantidad_disponible = lote.cantidad_disponible - base
        ln.lote_id = lote.id
        db.add(build_movimiento(
            ctx.tenant_id, ctx.user_id, lote, "CONFIRMACION_FACTURA", -base,
            ref_tipo="FACTURA", ref_id=factura.id,
            motivo=f"Salida factura {factura.serie}{factura.folio}",
        ))


def _revertir_factura_directa(db: Session, ctx, factura, *, perdida: bool) -> None:
    """Al cancelar una factura DIRECTA timbrada: devolución → regresa a
    `disponible` (ENTRADA_DEVOLUCION); pérdida → la mercancía no regresa (ya salió
    al timbrar), solo se suelta el vínculo. Limpia el lote de cada línea."""
    if factura.almacen_id is None:
        return
    lineas = db.query(LineaFactura).filter(LineaFactura.factura_id == factura.id).all()
    for ln in lineas:
        if ln.lote_id is None:          # no se timbró / ya revertida
            continue
        base = Decimal(ln.cantidad_base) if ln.cantidad_base is not None else Decimal(ln.cantidad)
        if not perdida:
            lote = (
                db.query(LoteInventario)
                .filter(LoteInventario.id == ln.lote_id)
                .with_for_update()
                .one_or_none()
            )
            if lote is not None:
                lote.cantidad_disponible = lote.cantidad_disponible + base
                db.add(build_movimiento(
                    ctx.tenant_id, ctx.user_id, lote, "ENTRADA_DEVOLUCION", base,
                    ref_tipo="FACTURA", ref_id=factura.id,
                    motivo=f"Cancelación factura {factura.serie}{factura.folio}",
                ))
        else:
            # Pérdida: los bienes ya salieron al timbrar (disponible no cambia,
            # sin movimiento), pero la mercancía perdida SÍ debe constar en el
            # registro de mermas — igual que el flujo por remisiones.
            db.add(Merma(
                tenant_id=ctx.tenant_id, lote_id=ln.lote_id, cantidad=base,
                motivo="OTRO", created_by=ctx.user_id,
                descripcion=f"Pérdida por cancelación factura {factura.serie}{factura.folio}",
            ))
        ln.lote_id = None


@router.get("", response_model=Page[FacturaOut])
def list_facturas(
    estado: Optional[str] = Query(default=None, max_length=20),
    cliente_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Factura).filter(Factura.deleted_at.is_(None))
    if estado:
        query = query.filter(Factura.estado == estado)
    if cliente_id is not None:
        query = query.filter(Factura.cliente_id == cliente_id)
    query = query.order_by(Factura.fecha.desc(), Factura.folio.desc())
    return paginate(query, FacturaOut, limit, offset)


@router.get("/pdf")
def facturas_pdf_lote(
    ids: str = Query(..., description="IDs de factura separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """PDF de varias facturas (una por página) para imprimir/guardar en lote.
    Definido ANTES de /{factura_id} para que la ruta estática gane."""
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
        raise HTTPException(status_code=422, detail="Sin facturas para imprimir")
    if len(id_list) > 200:
        raise HTTPException(status_code=422, detail="Máximo 200 facturas por PDF")
    facturas = (
        db.query(Factura)
        .filter(Factura.id.in_(id_list), Factura.deleted_at.is_(None))
        .order_by(Factura.serie, Factura.folio)
        .all()
    )
    if not facturas:
        raise HTTPException(status_code=404, detail="No se encontraron facturas")
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    cli_ids = {f.cliente_id for f in facturas}
    clientes = {c.id: c for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()}
    pdf = build_facturas_pdf([(f, clientes.get(f.cliente_id)) for f in facturas], tenant)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="facturas.pdf"'})


@router.get("/{factura_id}", response_model=FacturaDetailOut)
def get_factura(
    factura_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Factura, factura_id)


@router.post("/desde-remisiones", response_model=FacturaDetailOut, status_code=status.HTTP_201_CREATED)
def factura_desde_remisiones(
    payload: FacturaDesdeRemisionesIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ids = list(dict.fromkeys(payload.remision_ids))
    # FOR UPDATE: sin lock, dos "Facturar" simultáneos pasan ambos el check de
    # "ya facturada" → dos facturas vivas por la misma mercancía (y doble reserva
    # si venían en BORRADOR). Con el lock, el segundo ve el estado ya actualizado.
    rems = (
        db.query(Remision)
        .filter(Remision.id.in_(ids), Remision.deleted_at.is_(None))
        .with_for_update()
        .all()
    )
    if len(rems) != len(ids):
        raise HTTPException(status_code=404, detail="Una o más remisiones no existen")

    clientes = {r.cliente_facturacion_id for r in rems}
    if len(clientes) != 1:
        raise HTTPException(status_code=422, detail="Todas las remisiones deben ser del mismo cliente")
    # Una remisión queda "libre" para facturar si no tiene factura o si su última
    # factura fue CANCELADA (refacturación). factura_id ya no se anula al cancelar.
    linked_ids = {r.factura_id for r in rems if r.factura_id}
    linked = (
        {f.id: f for f in db.query(Factura).filter(Factura.id.in_(linked_ids)).all()}
        if linked_ids else {}
    )
    for r in rems:
        # El chequeo de "ya facturada" va primero, pero solo si su factura NO está
        # cancelada (una cancelada se puede refacturar).
        lf = linked.get(r.factura_id) if r.factura_id else None
        if lf is not None and lf.estado != "CANCELADA":
            raise HTTPException(status_code=409, detail=f"La remisión {r.folio_interno} ya está facturada")
        if r.estado not in ("BORRADOR", "CONFIRMADA"):
            raise HTTPException(status_code=422, detail=f"La remisión {r.folio_interno} no se puede facturar (estado {r.estado})")

    # Facturar auto-confirma las remisiones en BORRADOR (reserva inventario y
    # registra la salida) para que la factura salga contra existencias reales,
    # igual que confirmar manualmente. Las CONFIRMADAS ya reservaron su stock.
    for r in rems:
        if r.estado == "BORRADOR":
            reservar_stock_remision(db, ctx, r, permitir_negativos=payload.permitir_negativos)

    cliente = get_or_404(db, Cliente, clientes.pop())
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()

    prod_ids = {ln.producto_id for r in rems for ln in r.lineas}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}
    esq_ids = {p.esquema_impuesto_id for p in productos.values() if p.esquema_impuesto_id}
    esquemas = {e.id: e for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.id.in_(esq_ids)).all()}

    # Serie: override manual → sucursal (si todas las remisiones comparten una) →
    # cliente → default del inquilino. Folio del contador atómico de la serie.
    sucursales = {r.sucursal_id for r in rems if r.sucursal_id}
    sucursal_id = sucursales.pop() if len(sucursales) == 1 else None
    serie_obj = resolver_serie(
        db, ctx.tenant_id, "FACTURA",
        serie_id=payload.serie_id, sucursal_id=sucursal_id, cliente_id=cliente.id,
    )
    if serie_obj is not None:
        folio = consumir_folio(db, serie_obj.id)
        serie_codigo = serie_obj.codigo
        if folio is None:                       # carrera/desactivada: cae a back-compat
            serie_codigo = payload.serie or serie_obj.codigo
            folio = _next_folio(db, ctx.tenant_id, serie_codigo)
    else:
        serie_codigo = payload.serie or "A"
        folio = _next_folio(db, ctx.tenant_id, serie_codigo)

    # La(s) nota(s) de la(s) remisión(es) se transfieren a la factura. Si el
    # payload trae una nota explícita, esa manda; si no, se unen las notas
    # distintas y no vacías de las remisiones facturadas.
    notas_factura = payload.notas or "; ".join(
        dict.fromkeys(r.notas.strip() for r in rems if r.notas and r.notas.strip())
    ) or None

    factura = Factura(
        tenant_id=ctx.tenant_id, serie=serie_codigo, folio=folio,
        cliente_id=cliente.id,
        uso_cfdi=payload.uso_cfdi or cliente.uso_cfdi_default or "G01",
        forma_pago=payload.forma_pago or cliente.forma_pago_default or "99",
        metodo_pago=payload.metodo_pago or cliente.metodo_pago_default or "PPD",
        lugar_expedicion=tenant.domicilio_fiscal_cp,
        notas=notas_factura, created_by=ctx.user_id, estado="BORRADOR",
    )
    db.add(factura); db.flush()

    # Reúne las líneas de todas las remisiones; respeta peso variable/catch-weight.
    specs: list[dict] = []
    for r in rems:
        for ln in r.lineas:
            prod = productos.get(ln.producto_id)
            esq = esquemas.get(prod.esquema_impuesto_id) if prod and prod.esquema_impuesto_id else None
            importe = Decimal(ln.importe)
            if prod and prod.peso_variable and ln.cantidad_surtida and Decimal(ln.cantidad_surtida) > 0:
                cantidad = Decimal(ln.cantidad_surtida)            # unidades base reales
                clave_unidad = prod.unidad_sat
            else:
                cantidad = Decimal(ln.cantidad_solicitada)
                clave_unidad = presentacion_sat(prod, ln.presentacion) or (prod.unidad_sat if prod else "H87")
            if cantidad <= 0:
                # Línea devuelta por completo: no se factura (el PAC rechaza
                # conceptos con cantidad 0).
                continue
            specs.append({"producto_id": ln.producto_id, "prod": prod, "esq": esq,
                          "cantidad": cantidad, "clave_unidad": clave_unidad, "importe": importe})
        r.factura_id = factura.id
        r.estado = "FACTURADA"

    # agrupar_productos: suma cantidad/importe de líneas con mismo producto+unidad.
    if not specs:
        raise HTTPException(
            status_code=422,
            detail="Las remisiones no tienen líneas facturables (todo fue devuelto)",
        )
    if payload.agrupar_productos:
        merged: dict = {}
        order: list = []
        for s in specs:
            k = (s["producto_id"], s["clave_unidad"])
            if k not in merged:
                merged[k] = dict(s); order.append(k)
            else:
                merged[k]["cantidad"] += s["cantidad"]
                merged[k]["importe"] += s["importe"]
        specs = [merged[k] for k in order]

    calc_lineas = []
    for numero, s in enumerate(specs, start=1):
        prod, esq = s["prod"], s["esq"]
        importe, cantidad, clave_unidad = s["importe"], s["cantidad"], s["clave_unidad"]
        valor_unitario = (importe / cantidad).quantize(Decimal("0.000001")) if cantidad else ZERO
        calc = _fiscal_calc(prod, esq, importe, cantidad)
        calc_lineas.append(calc)
        db.add(LineaFactura(
            tenant_id=ctx.tenant_id, factura_id=factura.id, numero_linea=numero,
            producto_id=s["producto_id"],
            clave_prod_serv=(prod.clave_sat if prod else "01010101"),
            clave_unidad=clave_unidad, descripcion=(prod.nombre if prod else "Producto"),
            cantidad=cantidad, valor_unitario=valor_unitario, importe=importe, objeto_imp="02",
            iva_tasa=calc["iva_tasa"], iva_importe=calc["iva_importe"],
            ieps_tipo=calc["ieps_tipo"], ieps_valor=calc["ieps_valor"], ieps_importe=calc["ieps_importe"],
            ret_iva_importe=calc["ret_iva_importe"], ret_isr_importe=calc["ret_isr_importe"],
        ))

    tot = totales(calc_lineas)
    factura.subtotal = tot["subtotal"]
    factura.descuento = tot["descuento"]
    factura.iva_trasladado = tot["iva_trasladado"]
    factura.ieps_trasladado = tot["ieps_trasladado"]
    factura.ret_iva = tot["ret_iva"]
    factura.ret_isr = tot["ret_isr"]
    factura.total = tot["total"]
    db.flush()
    db.refresh(factura)
    return factura


@router.post("/directa", response_model=FacturaDetailOut, status_code=status.HTTP_201_CREATED)
def factura_directa(
    payload: FacturaDirectaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Crea una factura capturada a mano (sin remisión, sin afectar inventario).
    Las líneas referencian productos del catálogo para tomar su clave SAT y su
    desglose fiscal."""
    cliente = get_or_404(db, Cliente, payload.cliente_id)
    ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")   # de dónde sale el stock
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()

    prod_ids = {ln.producto_id for ln in payload.lineas}
    productos = {
        p.id: p
        for p in db.query(Producto)
        .filter(Producto.id.in_(prod_ids), Producto.deleted_at.is_(None))
        .all()
    }
    if len(productos) != len(prod_ids):
        raise HTTPException(status_code=404, detail="Uno o más productos no existen")
    # Presentación desconocida caería a factor 1 y descontaría inventario con la
    # magnitud equivocada (p. ej. "ARPILLA" mal tecleada = 1 kg en vez de 20).
    for ln in payload.lineas:
        prod = productos.get(ln.producto_id)
        if ln.presentacion and prod is not None and ln.presentacion not in (prod.presentaciones or {}):
            raise HTTPException(
                status_code=422,
                detail=f"El producto {prod.nombre} no tiene la presentación '{ln.presentacion}'",
            )
    esq_ids = {p.esquema_impuesto_id for p in productos.values() if p.esquema_impuesto_id}
    esquemas = {e.id: e for e in db.query(EsquemaImpuesto).filter(EsquemaImpuesto.id.in_(esq_ids)).all()}

    serie_obj = resolver_serie(
        db, ctx.tenant_id, "FACTURA", serie_id=payload.serie_id, cliente_id=cliente.id,
    )
    if serie_obj is not None:
        folio = consumir_folio(db, serie_obj.id)
        serie_codigo = serie_obj.codigo
        if folio is None:
            serie_codigo = payload.serie or serie_obj.codigo
            folio = _next_folio(db, ctx.tenant_id, serie_codigo)
    else:
        serie_codigo = payload.serie or "A"
        folio = _next_folio(db, ctx.tenant_id, serie_codigo)

    factura = Factura(
        tenant_id=ctx.tenant_id, serie=serie_codigo, folio=folio, cliente_id=cliente.id,
        uso_cfdi=payload.uso_cfdi or cliente.uso_cfdi_default or "G01",
        forma_pago=payload.forma_pago or cliente.forma_pago_default or "99",
        metodo_pago=payload.metodo_pago or cliente.metodo_pago_default or "PPD",
        lugar_expedicion=tenant.domicilio_fiscal_cp,
        almacen_id=payload.almacen_id,
        notas=payload.notas, created_by=ctx.user_id, estado="BORRADOR",
    )
    db.add(factura); db.flush()

    calc_lineas = []
    for numero, ln in enumerate(payload.lineas, start=1):
        prod = productos.get(ln.producto_id)
        esq = esquemas.get(prod.esquema_impuesto_id) if prod and prod.esquema_impuesto_id else None
        cantidad = Decimal(ln.cantidad)
        valor_unitario = Decimal(ln.precio_unitario)
        # A 2 decimales (centavos), como el resto del cálculo fiscal (fiscal._q):
        # si la línea se guarda a 4 decimales, la suma de importes de líneas no
        # cuadra con el subtotal del comprobante y el PAC rechaza el timbrado.
        importe = (cantidad * valor_unitario).quantize(Decimal("0.01"))
        clave_unidad = presentacion_sat(prod, ln.presentacion) or (prod.unidad_sat if prod else "H87")
        # Cantidad en unidad base (para descontar inventario al timbrar).
        cantidad_base = cantidad * presentacion_factor(prod, ln.presentacion)
        calc = _fiscal_calc(prod, esq, importe, cantidad)
        calc_lineas.append(calc)
        db.add(LineaFactura(
            tenant_id=ctx.tenant_id, factura_id=factura.id, numero_linea=numero,
            producto_id=ln.producto_id,
            clave_prod_serv=(prod.clave_sat if prod else "01010101"),
            clave_unidad=clave_unidad, descripcion=(prod.nombre if prod else "Producto"),
            cantidad=cantidad, cantidad_base=cantidad_base,
            valor_unitario=valor_unitario, importe=importe, objeto_imp="02",
            iva_tasa=calc["iva_tasa"], iva_importe=calc["iva_importe"],
            ieps_tipo=calc["ieps_tipo"], ieps_valor=calc["ieps_valor"], ieps_importe=calc["ieps_importe"],
            ret_iva_importe=calc["ret_iva_importe"], ret_isr_importe=calc["ret_isr_importe"],
        ))

    tot = totales(calc_lineas)
    factura.subtotal = tot["subtotal"]
    factura.descuento = tot["descuento"]
    factura.iva_trasladado = tot["iva_trasladado"]
    factura.ieps_trasladado = tot["ieps_trasladado"]
    factura.ret_iva = tot["ret_iva"]
    factura.ret_isr = tot["ret_isr"]
    factura.total = tot["total"]
    db.flush()
    db.refresh(factura)
    return factura


# ─── Timbrado ─────────────────────────────────────────────────────────────────
def _commit_seguro(db: Session, ctx) -> None:
    """Commit intermedio + re-arma ROLE app_user y el GUC del tenant.

    `set_config(..., is_local=true)` muere con cada commit; sin re-armarlo, todo
    lo que siga correría sin scope de RLS. Se usa para persistir el resultado
    del PAC en el instante (decisión 2026-07-29 #1): un timbre del SAT jamás
    debe perderse por un rollback posterior."""
    db.commit()
    set_role_tenant(db, ctx.tenant_id)


def _validar_stock_directa(db: Session, ctx, factura, *, permitir_negativos: bool) -> None:
    """Pre-check de existencias de una factura DIRECTA, ANTES de llamar al PAC
    (después ya no hay marcha atrás fiscal). Suma la necesidad por producto y
    frena con 422 salvo sobregiro autorizado (decisión 2026-07-29 #4)."""
    if factura.almacen_id is None or permitir_negativos:
        return
    lineas = db.query(LineaFactura).filter(LineaFactura.factura_id == factura.id).all()
    necesidad: dict = {}
    for ln in lineas:
        base = Decimal(ln.cantidad_base) if ln.cantidad_base is not None else Decimal(ln.cantidad)
        necesidad[ln.producto_id] = necesidad.get(ln.producto_id, ZERO) + base
    nombres = dict(
        db.query(Producto.id, Producto.nombre).filter(Producto.id.in_(necesidad.keys())).all()
    )
    for pid, total in necesidad.items():
        lote = resolve_lote(db, ctx.tenant_id, pid, factura.almacen_id, numero_lote=None, create=False)
        disponible = lote.cantidad_disponible if lote is not None else ZERO
        if disponible < total:
            raise HTTPException(
                status_code=422,
                detail=f"Existencia insuficiente para {nombres.get(pid, 'el producto')}",
            )


def _post_timbrado(db: Session, ctx, factura, client) -> None:
    """Inventario + XML DESPUÉS de persistir el timbre. Si algo falla aquí, el
    timbre ya está a salvo; se registra en el log para reconciliar el stock a
    mano (preferible a perder el registro de un CFDI real)."""
    try:
        _descontar_factura_directa(db, ctx, factura)
        if factura.facturama_id:
            try:
                factura.xml = client.download_xml(factura.facturama_id).decode("utf-8", "ignore")
            except FacturamaError:
                pass  # el timbre ya quedó; el XML se puede descargar luego
    except Exception:  # noqa: BLE001 — el timbre manda; el stock se reconcilia
        log.exception(
            "Post-timbrado falló (factura %s%s): el timbre quedó guardado; revisar inventario",
            factura.serie, factura.folio,
        )


@router.post("/{factura_id}/timbrar", response_model=FacturaDetailOut)
def timbrar_factura(
    factura_id: UUID,
    payload_in: TimbrarIn | None = Body(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Timbra la factura contra Facturama (BORRADOR → TIMBRADA).

    El ambiente (sandbox/producción) lo decide FACTURAMA_BASE_URL; en producción
    el timbre es REAL ante el SAT.
    """
    # Cada timbre consume un folio del PAC — tope generoso contra loops/abuso.
    enforce(f"timbrar:{ctx.tenant_id}", 300, 3600)
    # FOR UPDATE: dos timbrados simultáneos (doble clic / dos usuarios) generarían
    # DOS CFDI reales en el SAT y doble descuento de stock en directa. Con el lock,
    # el segundo request espera y ve estado=TIMBRADA (o el intento fresco) → 409.
    factura = get_or_404(db, Factura, factura_id, for_update=True)
    if factura.estado == "TIMBRADA":
        raise HTTPException(status_code=409, detail="La factura ya está timbrada")
    if factura.estado == "CANCELADA":
        raise HTTPException(status_code=409, detail="La factura está cancelada")

    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")

    # Multi-emisor: el tenant debe estar listo (datos fiscales + su CSD cargado)
    # antes de timbrar a su nombre. Mensaje accionable en vez del error críptico del PAC.
    if bool(getattr(settings, "FACTURAMA_MULTIEMISOR", False)):
        tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
        estado = compute_status(client, tenant, multiemisor=True)
        if not estado["listo_para_facturar"]:
            faltan = [p["titulo"] for p in estado["pasos"] if not p["completo"]]
            raise HTTPException(
                status_code=422,
                detail=(
                    "La empresa aún no está lista para facturar. Completa en "
                    "Ajustes › Empresa: " + ", ".join(faltan) + "."
                ),
            )

    permitir_negativos = bool(payload_in.permitir_negativos) if payload_in else False
    _validar_stock_directa(db, ctx, factura, permitir_negativos=permitir_negativos)

    # ─── Bitácora de intentos (decisión 2026-07-29 #1) ───────────────────────
    # Un PENDIENTE fresco = otro timbrado en vuelo AHORA (el mutex real: se
    # inserta bajo el lock de la factura y se comitea antes de llamar al PAC).
    # Un PENDIENTE viejo = un request que murió a media llamada: se reconcilia
    # contra Facturama antes de permitir re-timbrar.
    pendientes = (
        db.query(TimbradoIntento)
        .filter(TimbradoIntento.factura_id == factura.id, TimbradoIntento.estado == "PENDIENTE")
        .all()
    )
    if pendientes:
        ahora = db.query(func.now()).scalar()
        if any((ahora - i.created_at).total_seconds() < 300 for i in pendientes):
            raise HTTPException(
                status_code=409,
                detail="Hay un timbrado en curso para esta factura; espera un momento y recarga",
            )
        ok, existente = client.buscar_cfdi(factura.serie, factura.folio)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Hay un intento de timbrado previo sin confirmar y no se pudo "
                    "verificar con el PAC; reintenta en unos minutos"
                ),
            )
        if existente is not None:
            # El CFDI ya existe ante el SAT: se adopta, NUNCA se re-timbra.
            factura.facturama_id = existente.get("Id")
            factura.uuid = (
                ((existente.get("Complement") or {}).get("TaxStamp") or {}).get("Uuid")
                or existente.get("Uuid")
            )
            factura.estado = "TIMBRADA"
            factura.fecha_timbrado = func.now()
            _iniciar_saldo_insoluto(factura)
            for i in pendientes:
                i.estado = "TIMBRADA"
                i.detalle = "Reconciliado: el CFDI ya existía en Facturama"
            _commit_seguro(db, ctx)
            _post_timbrado(db, ctx, factura, client)
            db.flush()
            db.refresh(factura)
            return factura
        for i in pendientes:
            i.estado = "ERROR"
            i.detalle = "Verificado con el PAC: el intento no llegó a timbrar"

    payload = build_payload(db, factura)
    intento = TimbradoIntento(tenant_id=ctx.tenant_id, factura_id=factura.id, estado="PENDIENTE")
    db.add(intento)
    _commit_seguro(db, ctx)     # visible aunque este request muera a media llamada

    try:
        resp = client.create_cfdi(payload)
    except FacturamaError as exc:
        intento.estado = "ERROR"
        intento.detalle = str(exc)[:2000]
        _commit_seguro(db, ctx)
        raise HTTPException(status_code=502, detail=f"Timbrado rechazado por el PAC: {exc}")

    uuid_sat = ((resp.get("Complement") or {}).get("TaxStamp") or {}).get("Uuid") or resp.get("Uuid")
    factura.facturama_id = resp.get("Id")
    factura.uuid = uuid_sat
    factura.estado = "TIMBRADA"
    factura.fecha_timbrado = func.now()
    _iniciar_saldo_insoluto(factura)
    intento.estado = "TIMBRADA"
    # El timbre del SAT se persiste EN ESTE INSTANTE: cualquier falla posterior
    # (inventario, XML, commit final) ya no puede dejar un CFDI real sin registro.
    _commit_seguro(db, ctx)
    # Factura directa: al timbrar sale el inventario (las de remisión ya salieron
    # al confirmar; almacen_id es None en esas y el helper no hace nada).
    _post_timbrado(db, ctx, factura, client)
    db.flush()
    db.refresh(factura)
    return factura


@router.post("/{factura_id}/cancelar", response_model=FacturaDetailOut)
def cancelar_factura(
    factura_id: UUID,
    payload: CancelarFacturaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Cancela el CFDI ante el PAC y libera sus remisiones.

    Con FACTURAMA_FAKE_CANCEL=true la cancelación es interna (no llama al PAC);
    en producción debe ser false para que la cancelación llegue al SAT.
    """
    factura = get_or_404(db, Factura, factura_id, for_update=True)
    if factura.estado != "TIMBRADA":
        raise HTTPException(status_code=409, detail="Solo se cancela una factura timbrada")
    if payload.motivo == "01" and payload.uuid_sustitucion is None:
        raise HTTPException(status_code=422, detail="El motivo 01 requiere uuid_sustitucion")

    if settings.FACTURAMA_FAKE_CANCEL:
        # Cancelación simulada (el sandbox de Facturama no cancela). NO se llama al
        # PAC; solo se aplica la lógica interna. En producción: FACTURAMA_FAKE_CANCEL=false.
        pass
    else:
        client = FacturamaClient.from_settings(settings)
        if not client.configured:
            raise HTTPException(status_code=503, detail="Facturama no está configurado")
        try:
            client.cancel_cfdi(
                factura.facturama_id, motive=payload.motivo,
                uuid_replacement=str(payload.uuid_sustitucion) if payload.uuid_sustitucion else None,
            )
        except FacturamaError as exc:
            raise HTTPException(status_code=502, detail=f"Cancelación rechazada por el PAC: {exc}")

    factura.estado = "CANCELADA"
    factura.fecha_cancelacion = func.now()
    factura.motivo_cancelacion = payload.motivo
    factura.uuid_sustitucion = str(payload.uuid_sustitucion) if payload.uuid_sustitucion else None

    # Factura directa (sin remisión): revierte el inventario que salió al timbrar.
    _revertir_factura_directa(db, ctx, factura, perdida=(payload.inventario == "perdida"))

    rems = db.query(Remision).filter(Remision.factura_id == factura.id).all()
    # Qué hacer con el inventario reservado por las remisiones (elegido al cancelar):
    if payload.inventario == "perdida":
        # Pérdida por cancelación: la mercancía NO regresa (se da de baja como
        # merma) y las remisiones quedan CANCELADAS (no refacturables).
        _writeoff_remision_stock(db, rems, ctx, factura)
        nuevo_estado = "CANCELADA"
    else:
        # Devolución a almacén (default): el inventario regresa a disponible y las
        # remisiones se liberan a BORRADOR para poder volver a facturarlas.
        _release_remision_stock(db, rems, ctx, factura)
        nuevo_estado = "BORRADOR"
    # factura_id se conserva apuntando a la factura CANCELADA (al refacturar se
    # sobreescribe con la nueva).
    for r in rems:
        if r.estado in ("CONFIRMADA", "FACTURADA"):
            r.estado = nuevo_estado
    db.flush()
    db.refresh(factura)
    return factura


@router.delete("/{factura_id}", status_code=status.HTTP_204_NO_CONTENT)
def descartar_factura(
    factura_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Descarta una factura en BORRADOR (nunca timbrada) y regresa sus remisiones
    a CONFIRMADA para poder refacturarlas.

    Al facturar desde remisiones, estas quedan FACTURADA apuntando a una factura
    BORRADOR. Si el timbrado falla o nunca se timbra, sin esto las remisiones
    quedarían bloqueadas para siempre (cancelar_factura exige TIMBRADA). Una
    TIMBRADA se cancela ante el PAC vía /cancelar; una CANCELADA ya está cerrada.
    """
    factura = get_or_404(db, Factura, factura_id)
    if factura.estado != "BORRADOR":
        raise HTTPException(
            status_code=409,
            detail="Solo se descarta una factura en BORRADOR; una timbrada se cancela ante el PAC",
        )
    rems = db.query(Remision).filter(Remision.factura_id == factura.id).all()
    for r in rems:
        if r.estado == "FACTURADA":
            r.estado = "CONFIRMADA"  # el inventario reservado se conserva
        r.factura_id = None
    db.query(LineaFactura).filter(LineaFactura.factura_id == factura.id).delete(synchronize_session=False)
    db.delete(factura)
    db.flush()
    return None


def _xml_de(factura: Factura) -> Optional[str]:
    """XML cacheado en `factura.xml` o, si falta, obtenido en vivo del PAC."""
    xml = factura.xml
    if not xml and factura.facturama_id:
        try:
            xml = FacturamaClient.from_settings(settings).download_xml(factura.facturama_id).decode("utf-8", "ignore")
        except FacturamaError:
            xml = None
    return xml


@router.get("/{factura_id}/xml")
def descargar_xml(
    factura_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    factura = get_or_404(db, Factura, factura_id)
    xml = _xml_de(factura)
    if not xml:
        raise HTTPException(status_code=404, detail="La factura no tiene XML (¿no está timbrada?)")
    return Response(content=xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{factura.serie}{factura.folio}.xml"'})


@router.get("/{factura_id}/pdf")
def descargar_pdf(
    factura_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    factura = get_or_404(db, Factura, factura_id)
    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    cliente = db.query(Cliente).filter(Cliente.id == factura.cliente_id).one_or_none()
    pdf = build_factura_pdf(factura, tenant, cliente)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{factura.serie}{factura.folio}.pdf"'})


@router.post("/{factura_id}/enviar")
def enviar_factura(
    factura_id: UUID,
    payload: EnviarFacturaIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Envía el XML + PDF de una factura TIMBRADA por correo (SMTP del tenant,
    configurado en Ajustes › Correo)."""
    factura = get_or_404(db, Factura, factura_id)
    if factura.estado != "TIMBRADA":
        raise HTTPException(status_code=409, detail="Solo se puede enviar una factura timbrada")

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    cfg = email_service.smtp_config(tenant)
    if not email_service.configured(tenant):
        raise HTTPException(
            status_code=503,
            detail="Configura una cuenta de correo en Ajustes › Correo antes de enviar facturas.",
        )

    xml = _xml_de(factura)
    cliente = db.query(Cliente).filter(Cliente.id == factura.cliente_id).one_or_none()
    pdf = build_factura_pdf(factura, tenant, cliente)
    nombre_archivo = f"{factura.serie}{factura.folio}"
    subject = f"Factura {nombre_archivo}" + (f" — {tenant.legal_name}" if tenant.legal_name else "")
    # html.escape en todo dato dinámico: sin esto, un mensaje o nombre de cliente
    # con HTML se inyecta tal cual en el correo saliente (phishing con el SMTP
    # del tenant).
    mensaje_html = f"<p>{html_mod.escape(payload.mensaje)}</p>" if payload.mensaje else ""
    html = (
        f"{mensaje_html}"
        f"<p>Adjunto la factura <strong>{html_mod.escape(nombre_archivo)}</strong>"
        + (f" a nombre de {html_mod.escape(cliente.legal_name)}" if cliente else "")
        + f" por un total de <strong>${factura.total:,.2f} {factura.moneda}</strong>.</p>"
        f"<p>UUID: <span style=\"font-family:monospace\">{html_mod.escape(factura.uuid or '')}</span></p>"
    )
    attachments: list[tuple[str, bytes, str]] = [(f"{nombre_archivo}.pdf", pdf, "application/pdf")]
    if xml:
        attachments.append((f"{nombre_archivo}.xml", xml.encode("utf-8"), "application/xml"))

    try:
        email_service.send_email(cfg, [str(e) for e in payload.to], subject, html, attachments=attachments)
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True}


class EnviarFacturasLoteIn(BaseModel):
    """Un solo correo con varias facturas TIMBRADAS (todas del mismo cliente):
    un PDF + XML adjunto por factura, como el enviar-lote de remisiones."""
    ids: list[UUID] = PydField(min_length=1, max_length=100)
    to: Optional[str] = PydField(default=None, max_length=1000)
    mensaje: Optional[str] = PydField(default=None, max_length=5000)


@router.post("/enviar-lote")
def enviar_facturas_lote(
    payload: EnviarFacturasLoteIn = Body(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    facturas = (
        db.query(Factura)
        .filter(Factura.id.in_(payload.ids), Factura.deleted_at.is_(None))
        .order_by(Factura.serie, Factura.folio)
        .all()
    )
    if not facturas:
        raise HTTPException(status_code=404, detail="No se encontraron facturas")
    no_timbradas = [f"{f.serie}{f.folio}" for f in facturas if f.estado != "TIMBRADA"]
    if no_timbradas:
        raise HTTPException(
            status_code=409,
            detail="Solo se envían facturas timbradas; sin timbrar: " + ", ".join(no_timbradas),
        )
    cli_ids = {f.cliente_id for f in facturas}
    if len(cli_ids) > 1:
        raise HTTPException(status_code=422, detail="Las facturas deben ser del mismo cliente")
    cliente = db.query(Cliente).filter(Cliente.id == next(iter(cli_ids))).one_or_none()

    # Destinatarios: payload (coma/espacio) o los correos del cliente.
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

    tenant = db.query(Tenant).filter(Tenant.id == ctx.tenant_id).one()
    if not email_service.configured(tenant):
        raise HTTPException(
            status_code=503,
            detail="Configura una cuenta de correo en Ajustes › Correo antes de enviar facturas.",
        )

    emisor_nombre = tenant.trade_name or tenant.legal_name or "SmartSupply"
    cliente_nombre = cliente.legal_name if cliente else ""
    n = len(facturas)
    filas = []
    total_general = Decimal("0")
    attachments: list[tuple[str, bytes, str]] = []
    for f in facturas:
        total_general += Decimal(f.total or 0)
        filas.append(
            "<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html_mod.escape(f'{f.serie}{f.folio}')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;font-family:monospace;font-size:11px'>{html_mod.escape(f.uuid or '')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>${Decimal(f.total or 0):,.2f}</td>"
            "</tr>"
        )
        nombre_archivo = f"{f.serie}{f.folio}"
        attachments.append((f"{nombre_archivo}.pdf", build_factura_pdf(f, tenant, cliente), "application/pdf"))
        xml = _xml_de(f)
        if xml:
            attachments.append((f"{nombre_archivo}.xml", xml.encode("utf-8"), "application/xml"))

    mensaje_html = (
        "<div style='background:#f5f7ff;border-left:3px solid #4f6bed;"
        "padding:10px 14px;margin:0 0 16px;border-radius:4px;white-space:pre-line'>"
        f"{html_mod.escape(payload.mensaje)}</div>"
        if payload.mensaje else ""
    )
    etiqueta = "factura" if n == 1 else "facturas"
    html = (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#222;max-width:640px'>"
        f"<h2 style='margin:0 0 4px'>{html_mod.escape(emisor_nombre)}</h2>"
        f"<p style='margin:0 0 16px;color:#555'>Estimado(a) <strong>{html_mod.escape(cliente_nombre)}</strong>:</p>"
        + mensaje_html
        + f"<p style='margin:0 0 16px'>Le compartimos {n} {etiqueta}. "
        "El PDF y XML de cada una van adjuntos.</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<thead><tr style='background:#f5f5f5'>"
        "<th style='padding:6px 10px;text-align:left'>Factura</th>"
        "<th style='padding:6px 10px;text-align:left'>UUID</th>"
        "<th style='padding:6px 10px;text-align:right'>Total</th>"
        "</tr></thead><tbody>"
        + "".join(filas)
        + "</tbody></table>"
        f"<p style='margin:16px 0 0;text-align:right;font-size:16px'>"
        f"<strong>Total general: ${total_general:,.2f}</strong></p>"
        "<p style='margin:24px 0 0;color:#999;font-size:12px'>"
        "Correo enviado automáticamente por SmartSupply.</p>"
        "</div>"
    )
    asunto = (
        f"Factura {facturas[0].serie}{facturas[0].folio}" if n == 1
        else f"{n} facturas — {emisor_nombre}"
    )
    try:
        email_service.send_email(
            email_service.smtp_config(tenant), destinatarios, asunto, html, attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "to": ", ".join(destinatarios), "facturas": n}

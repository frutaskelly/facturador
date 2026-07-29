"""Inventario — existencias, kardex y movimientos manuales (Phase 4b).

Reads gated by `menu:inventario`; writes by `inventario:gestionar`.

Every quantity change is atomic: the affected lot is locked with
`SELECT … FOR UPDATE`, its cached `cantidad_disponible` (and weighted-average
`costo_unitario` on purchases) is updated, and an immutable `MovimientoInventario`
row is appended — both in the same RLS-scoped transaction. The lot is a cache;
the kardex is the source of truth. The lot/weighted-cost primitives live in
`app/services/inventario.py` so the purchase-receipt path can't diverge.

The manual endpoint handles operator-initiated movements: ENTRADA_COMPRA,
AJUSTE, MERMA, TRANSFERENCIA. The remisión / POS flows emit their own
SALIDA_REMISION / CONFIRMACION_FACTURA / CANCELACION_REMISION movements.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Almacen,
    LineaOrdenCompra,
    LoteInventario,
    Merma,
    MovimientoInventario,
    OrdenCompra,
    Producto,
)
from ...schemas.common import Page
from ...schemas.inventario import (
    ExistenciaRow,
    LoteOut,
    MovimientoCreate,
    MovimientoOut,
    ResumenProductoOut,
)
from ...services.inventario import (
    apply_entrada_compra,
    build_movimiento,
    resolve_lote,
    weighted_cost,
)
from ._helpers import ensure_fk, paginate

router = APIRouter(prefix="/inventario", tags=["inventario"])

_READ = "menu:inventario"
_WRITE = "inventario:gestionar"
_ZERO = Decimal("0")
_Q = Decimal("0.0001")

# OCs that still expect goods to arrive (NOT BORRADOR / RECIBIDA / CANCELADA).
_OC_ABIERTAS = ("ENVIADA", "ACEPTADA", "EN_TRANSITO", "RECIBIDA_PARCIAL")


def _ultimo_costo_compra(db: Session, producto_id) -> Optional[Decimal]:
    """Último costo de ENTRADA_COMPRA del producto (cualquier almacén), para
    que un ajuste positivo no cree existencia a costo $0."""
    row = (
        db.query(MovimientoInventario.costo_unitario)
        .join(LoteInventario, MovimientoInventario.lote_id == LoteInventario.id)
        .filter(
            LoteInventario.producto_id == producto_id,
            MovimientoInventario.tipo == "ENTRADA_COMPRA",
            MovimientoInventario.costo_unitario.isnot(None),
        )
        .order_by(MovimientoInventario.created_at.desc())
        .first()
    )
    return row[0] if row else None


# ─── existencias (aggregated view) ───────────────────────────────────────────
@router.get("/existencias", response_model=list[ExistenciaRow])
def existencias(
    producto_id: Optional[UUID] = Query(default=None),
    almacen_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = (
        db.query(
            LoteInventario.producto_id,
            Producto.sku.label("producto_sku"),
            Producto.nombre.label("producto_nombre"),
            LoteInventario.almacen_id,
            Almacen.nombre.label("almacen_nombre"),
            func.coalesce(func.sum(LoteInventario.cantidad_disponible), 0).label("disponible"),
            func.coalesce(func.sum(LoteInventario.cantidad_reservada), 0).label("reservada"),
            func.coalesce(func.sum(LoteInventario.cantidad_disponible * LoteInventario.costo_unitario), 0).label("valor"),
        )
        .join(Producto, Producto.id == LoteInventario.producto_id)
        .join(Almacen, Almacen.id == LoteInventario.almacen_id)
        # Excluye catálogo borrado (soft-delete): un producto/almacén eliminado
        # no debe seguir apareciendo en existencias ni en la valuación.
        .filter(Producto.deleted_at.is_(None), Almacen.deleted_at.is_(None))
    )
    if producto_id is not None:
        query = query.filter(LoteInventario.producto_id == producto_id)
    if almacen_id is not None:
        query = query.filter(LoteInventario.almacen_id == almacen_id)
    query = query.group_by(
        LoteInventario.producto_id,
        Producto.sku,
        Producto.nombre,
        LoteInventario.almacen_id,
        Almacen.nombre,
    )

    out = []
    for pid, sku, nombre, aid, alm_nombre, disponible, reservada, valor in query.all():
        disponible = Decimal(disponible)
        valor = Decimal(valor)
        costo = (valor / disponible).quantize(_Q) if disponible > 0 else _ZERO
        out.append(
            ExistenciaRow(
                producto_id=pid,
                producto_sku=sku,
                producto_nombre=nombre,
                almacen_id=aid,
                almacen_nombre=alm_nombre,
                disponible=disponible,
                reservada=Decimal(reservada),
                costo_promedio=costo,
                valor=valor.quantize(_Q),
            )
        )
    return out


@router.get("/resumen", response_model=ResumenProductoOut | list[ResumenProductoOut])
def resumen(
    producto_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Per-product summary: `disponible` (current stock) and `en_transito`
    (pending qty across open purchase orders). With `producto_id`, returns one
    object; otherwise a list for every product that has stock or in-transit qty."""
    # disponible per producto (same source as /existencias).
    disp_q = db.query(
        LoteInventario.producto_id.label("producto_id"),
        func.coalesce(func.sum(LoteInventario.cantidad_disponible), 0).label("disponible"),
    )
    if producto_id is not None:
        disp_q = disp_q.filter(LoteInventario.producto_id == producto_id)
    disp_q = disp_q.group_by(LoteInventario.producto_id)

    # en_transito = Σ(solicitada − recibida) over open OCs' lines.
    pendiente = func.coalesce(
        func.sum(LineaOrdenCompra.cantidad_solicitada - LineaOrdenCompra.cantidad_recibida), 0
    )
    trans_q = (
        db.query(LineaOrdenCompra.producto_id.label("producto_id"), pendiente.label("en_transito"))
        .join(OrdenCompra, OrdenCompra.id == LineaOrdenCompra.orden_compra_id)
        .filter(OrdenCompra.estado.in_(_OC_ABIERTAS))
    )
    if producto_id is not None:
        trans_q = trans_q.filter(LineaOrdenCompra.producto_id == producto_id)
    trans_q = trans_q.group_by(LineaOrdenCompra.producto_id)

    disp = {row.producto_id: Decimal(row.disponible) for row in disp_q.all()}
    trans = {row.producto_id: Decimal(row.en_transito) for row in trans_q.all()}

    if producto_id is not None:
        return ResumenProductoOut(
            producto_id=producto_id,
            disponible=disp.get(producto_id, _ZERO),
            en_transito=trans.get(producto_id, _ZERO),
        )

    return [
        ResumenProductoOut(
            producto_id=pid,
            disponible=disp.get(pid, _ZERO),
            en_transito=trans.get(pid, _ZERO),
        )
        for pid in (disp.keys() | trans.keys())
    ]


@router.get("/lotes", response_model=Page[LoteOut])
def list_lotes(
    producto_id: Optional[UUID] = Query(default=None),
    almacen_id: Optional[UUID] = Query(default=None),
    con_stock: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(LoteInventario)
    if producto_id is not None:
        query = query.filter(LoteInventario.producto_id == producto_id)
    if almacen_id is not None:
        query = query.filter(LoteInventario.almacen_id == almacen_id)
    if con_stock:
        query = query.filter(LoteInventario.cantidad_disponible > 0)
    query = query.order_by(LoteInventario.fecha_ingreso.desc(), LoteInventario.id.asc())
    return paginate(query, LoteOut, limit, offset)


@router.get("/movimientos", response_model=Page[MovimientoOut])
def list_movimientos(
    lote_id: Optional[UUID] = Query(default=None),
    producto_id: Optional[UUID] = Query(default=None),
    almacen_id: Optional[UUID] = Query(default=None),
    tipo: Optional[str] = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(MovimientoInventario)
    if producto_id is not None or almacen_id is not None:
        query = query.join(LoteInventario, MovimientoInventario.lote_id == LoteInventario.id)
        if producto_id is not None:
            query = query.filter(LoteInventario.producto_id == producto_id)
        if almacen_id is not None:
            query = query.filter(LoteInventario.almacen_id == almacen_id)
    if lote_id is not None:
        query = query.filter(MovimientoInventario.lote_id == lote_id)
    if tipo is not None:
        query = query.filter(MovimientoInventario.tipo == tipo)
    query = query.order_by(MovimientoInventario.fecha.desc(), MovimientoInventario.id.asc())
    return paginate(query, MovimientoOut, limit, offset)


# ─── manual movement ─────────────────────────────────────────────────────────
@router.post("/movimientos", response_model=list[MovimientoOut], status_code=status.HTTP_201_CREATED)
def create_movimiento(
    payload: MovimientoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")

    cantidad = payload.cantidad
    movimientos: list[MovimientoInventario] = []

    if payload.tipo == "ENTRADA_COMPRA":
        _, mov = apply_entrada_compra(
            db, ctx.tenant_id, ctx.user_id,
            producto_id=payload.producto_id, almacen_id=payload.almacen_id,
            cantidad=cantidad, costo=payload.costo_unitario,
            numero_lote=payload.numero_lote, fecha_caducidad=payload.fecha_caducidad,
            motivo=payload.motivo, notas=payload.notas,
        )
        movimientos.append(mov)

    elif payload.tipo == "AJUSTE":
        lote = resolve_lote(
            db, ctx.tenant_id, payload.producto_id, payload.almacen_id,
            numero_lote=payload.numero_lote, lote_id=payload.lote_id, create=True,
            fecha_caducidad=payload.fecha_caducidad,
        )
        if lote is None:
            raise HTTPException(status_code=404, detail="Lote no encontrado")
        nueva = lote.cantidad_disponible + cantidad
        if nueva < 0:
            raise HTTPException(status_code=422, detail="El ajuste dejaría la existencia en negativo")
        lote.cantidad_disponible = nueva
        if cantidad > 0:
            lote.cantidad_inicial = lote.cantidad_inicial + cantidad
            # Decisión 2026-07-29 (#9): mercancía "encontrada" no entra a costo
            # $0 (desploma el promedio y la valuación). Usa el costo capturado
            # o, en su defecto, el último costo de compra del producto.
            if not lote.costo_unitario:
                costo = payload.costo_unitario or _ultimo_costo_compra(db, payload.producto_id)
                if costo:
                    lote.costo_unitario = costo
        movimientos.append(build_movimiento(ctx.tenant_id, ctx.user_id, lote, "AJUSTE", cantidad,
                                             motivo=payload.motivo, notas=payload.notas))

    elif payload.tipo == "MERMA":
        lote = resolve_lote(
            db, ctx.tenant_id, payload.producto_id, payload.almacen_id,
            numero_lote=payload.numero_lote, lote_id=payload.lote_id, create=False,
        )
        if lote is None:
            raise HTTPException(status_code=404, detail="No hay existencia para mermar")
        if lote.cantidad_disponible < cantidad:
            raise HTTPException(status_code=422, detail="Existencia insuficiente para la merma")
        lote.cantidad_disponible = lote.cantidad_disponible - cantidad
        db.add(Merma(
            tenant_id=ctx.tenant_id, lote_id=lote.id, cantidad=cantidad,
            motivo=payload.merma_motivo, descripcion=payload.notas, created_by=ctx.user_id,
        ))
        movimientos.append(build_movimiento(ctx.tenant_id, ctx.user_id, lote, "MERMA", -cantidad,
                                            motivo=payload.motivo or payload.merma_motivo, notas=payload.notas))

    elif payload.tipo == "TRANSFERENCIA":
        ensure_fk(db, Almacen, payload.almacen_destino_id, "almacen_destino_id")
        if payload.almacen_destino_id == payload.almacen_id:
            raise HTTPException(status_code=422, detail="El almacén destino debe ser distinto del origen")
        origen = resolve_lote(
            db, ctx.tenant_id, payload.producto_id, payload.almacen_id,
            numero_lote=payload.numero_lote, lote_id=payload.lote_id, create=False,
        )
        if origen is None:
            raise HTTPException(status_code=404, detail="No hay existencia en el almacén origen")
        if origen.cantidad_disponible < cantidad:
            raise HTTPException(status_code=422, detail="Existencia insuficiente para la transferencia")
        costo_origen = origen.costo_unitario
        origen.cantidad_disponible = origen.cantidad_disponible - cantidad
        destino = resolve_lote(
            db, ctx.tenant_id, payload.producto_id, payload.almacen_destino_id,
            numero_lote=payload.numero_lote, create=True,
            fecha_caducidad=origen.fecha_caducidad, costo_inicial=costo_origen,
        )
        destino.costo_unitario = weighted_cost(destino.cantidad_disponible, destino.costo_unitario, cantidad, costo_origen)
        destino.cantidad_disponible = destino.cantidad_disponible + cantidad
        destino.cantidad_inicial = destino.cantidad_inicial + cantidad
        movimientos.append(build_movimiento(ctx.tenant_id, ctx.user_id, origen, "TRANSFERENCIA", -cantidad,
                                            costo=costo_origen, motivo=payload.motivo, notas=payload.notas))
        movimientos.append(build_movimiento(ctx.tenant_id, ctx.user_id, destino, "TRANSFERENCIA", cantidad,
                                            costo=costo_origen, motivo=payload.motivo, notas=payload.notas))

    for m in movimientos:
        db.add(m)
    db.flush()
    for m in movimientos:
        db.refresh(m)
    return movimientos

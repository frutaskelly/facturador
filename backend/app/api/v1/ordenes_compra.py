"""Órdenes de compra — CRUD, state machine, y recepción (Phase 4c).

Reads gated by `menu:compras`; writes by `compra:gestionar`. Receiving a PO
line creates an ENTRADA_COMPRA inventory movement (via the shared inventory
service) using the line's `precio_unitario` as the unit cost, stamping the
resulting lot with this order + supplier.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Almacen, LineaOrdenCompra, OrdenCompra, Producto, Proveedor
from ...schemas.common import Page
from ...schemas.orden_compra import (
    OrdenCompraCreate,
    OrdenCompraDetailOut,
    OrdenCompraOut,
    OrdenCompraUpdate,
    RecibirIn,
    TransitionIn,
)
from ...services.inventario import apply_entrada_compra, presentacion_factor
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/ordenes-compra", tags=["órdenes de compra"])

_READ = "menu:compras"
_WRITE = "compra:gestionar"
_ZERO = Decimal("0")
_DUP = "Folio de orden de compra duplicado"

_VALID_TRANSITIONS = {
    "BORRADOR": {"ENVIADA", "CANCELADA"},
    "ENVIADA": {"ACEPTADA", "CANCELADA"},
    "ACEPTADA": {"EN_TRANSITO", "CANCELADA"},
    "EN_TRANSITO": {"RECIBIDA_PARCIAL", "RECIBIDA", "CANCELADA"},
    "RECIBIDA_PARCIAL": {"RECIBIDA", "CANCELADA"},
    "RECIBIDA": set(),
    "CANCELADA": set(),
}
_RECEIVABLE = {"ENVIADA", "ACEPTADA", "EN_TRANSITO", "RECIBIDA_PARCIAL"}
_TERMINAL = {"RECIBIDA", "CANCELADA"}


def _next_folio(db: Session) -> str:
    """Next per-tenant OC folio. Computed in the app (not a DB sequence) so it
    can be made node-aware later without a schema change."""
    mx = 0
    for (folio,) in db.query(OrdenCompra.folio).filter(OrdenCompra.folio.isnot(None)).all():
        if folio and folio.startswith("OC-"):
            try:
                mx = max(mx, int(folio[3:]))
            except ValueError:
                pass
    return f"OC-{mx + 1:06d}"


@router.get("", response_model=Page[OrdenCompraOut])
def list_ordenes(
    estado: Optional[str] = Query(default=None, max_length=20),
    proveedor_id: Optional[UUID] = Query(default=None),
    fecha_desde: Optional[date] = Query(default=None),
    fecha_hasta: Optional[date] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(OrdenCompra)
    if estado:
        query = query.filter(OrdenCompra.estado == estado)
    if proveedor_id is not None:
        query = query.filter(OrdenCompra.proveedor_id == proveedor_id)
    if fecha_desde:
        query = query.filter(OrdenCompra.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.filter(OrdenCompra.fecha <= fecha_hasta)
    query = query.order_by(OrdenCompra.fecha.desc(), OrdenCompra.folio.desc())
    return paginate(query, OrdenCompraOut, limit, offset)


@router.post("", response_model=OrdenCompraDetailOut, status_code=status.HTTP_201_CREATED)
def create_orden(
    payload: OrdenCompraCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Proveedor, payload.proveedor_id, "proveedor_id")
    ensure_fk(db, Almacen, payload.almacen_destino_id, "almacen_destino_id")
    for ln in payload.lineas:
        ensure_fk(db, Producto, ln.producto_id, "producto_id")

    oc = OrdenCompra(
        tenant_id=ctx.tenant_id,
        folio=_next_folio(db),
        proveedor_id=payload.proveedor_id,
        almacen_destino_id=payload.almacen_destino_id,
        fecha=payload.fecha or date.today(),
        fecha_entrega_esperada=payload.fecha_entrega_esperada,
        notas=payload.notas,
        estado="BORRADOR",
    )
    db.add(oc)
    db.flush()

    subtotal = _ZERO
    for ln in payload.lineas:
        importe = ln.cantidad_solicitada * ln.precio_unitario
        subtotal += importe
        db.add(LineaOrdenCompra(
            tenant_id=ctx.tenant_id,
            orden_compra_id=oc.id,
            producto_id=ln.producto_id,
            cantidad_solicitada=ln.cantidad_solicitada,
            precio_unitario=ln.precio_unitario,
            presentacion=ln.presentacion,
            importe=importe,
            notas=ln.notas,
        ))
    oc.subtotal = subtotal
    oc.iva_total = _ZERO
    oc.total_estimado = subtotal
    flush_or_conflict(db, detail=_DUP)
    db.refresh(oc)
    return oc


@router.get("/{oc_id}", response_model=OrdenCompraDetailOut)
def get_orden(
    oc_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, OrdenCompra, oc_id)


@router.patch("/{oc_id}", response_model=OrdenCompraDetailOut)
def update_orden(
    oc_id: UUID,
    payload: OrdenCompraUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    oc = get_or_404(db, OrdenCompra, oc_id)
    if oc.estado in _TERMINAL:
        raise HTTPException(status_code=409, detail=f"No se puede editar una orden en estado {oc.estado}")
    data = payload.model_dump(exclude_unset=True)
    if "almacen_destino_id" in data:
        ensure_fk(db, Almacen, data["almacen_destino_id"], "almacen_destino_id")
    for key, value in data.items():
        setattr(oc, key, value)
    db.flush()
    db.refresh(oc)
    return oc


@router.post("/{oc_id}/transition", response_model=OrdenCompraDetailOut)
def transition_orden(
    oc_id: UUID,
    payload: TransitionIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    oc = get_or_404(db, OrdenCompra, oc_id)
    allowed = _VALID_TRANSITIONS.get(oc.estado, set())
    if payload.nuevo_estado not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Transición inválida: {oc.estado} → {payload.nuevo_estado}",
        )
    oc.estado = payload.nuevo_estado
    db.flush()
    db.refresh(oc)
    return oc


@router.post("/{oc_id}/recibir", response_model=OrdenCompraDetailOut)
def recibir_orden(
    oc_id: UUID,
    payload: RecibirIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    # FOR UPDATE: dos recepciones simultáneas leerían la misma cantidad_recibida
    # y duplicarían la entrada de stock.
    oc = get_or_404(db, OrdenCompra, oc_id, for_update=True)
    if oc.estado not in _RECEIVABLE:
        raise HTTPException(status_code=409, detail=f"No se puede recibir una orden en estado {oc.estado}")

    almacen_id = payload.almacen_id or oc.almacen_destino_id
    if almacen_id is None:
        raise HTTPException(status_code=422, detail="Se requiere un almacén destino para recibir")
    if payload.almacen_id is not None:
        ensure_fk(db, Almacen, payload.almacen_id, "almacen_id")

    by_id = {ln.id: ln for ln in oc.lineas}
    if payload.recepciones:
        recepciones = [(r.linea_id, r.cantidad, r.cantidad_base) for r in payload.recepciones]
    else:
        recepciones = [
            (ln.id, ln.cantidad_solicitada - ln.cantidad_recibida, None)
            for ln in oc.lineas
            if ln.cantidad_solicitada - ln.cantidad_recibida > 0
        ]
    if not recepciones:
        raise HTTPException(status_code=422, detail="No hay cantidades por recibir")

    # Products carry the presentation→base-unit factors; load them once so the
    # receipt can convert document quantities (in presentation units) to the
    # base units inventory is stored in.
    prod_ids = {by_id[lid].producto_id for lid, _, _ in recepciones if by_id.get(lid)}
    productos = {p.id: p for p in db.query(Producto).filter(Producto.id.in_(prod_ids)).all()}

    for linea_id, cantidad, cantidad_base in recepciones:
        ln = by_id.get(linea_id)
        if ln is None:
            raise HTTPException(status_code=404, detail="La línea no pertenece a esta orden")
        pendiente = ln.cantidad_solicitada - ln.cantidad_recibida
        if cantidad > pendiente:
            raise HTTPException(status_code=422, detail="La cantidad recibida excede lo pendiente de la línea")
        # Base units into inventory: the real weighed amount (catch-weight) when
        # given, else cantidad×factor. Per-base cost = total de la línea / base.
        factor = presentacion_factor(productos.get(ln.producto_id), ln.presentacion)
        base_qty = cantidad_base if cantidad_base is not None else (cantidad * factor)
        costo_base = (ln.precio_unitario * cantidad / base_qty) if base_qty else ln.precio_unitario
        apply_entrada_compra(
            db, ctx.tenant_id, ctx.user_id,
            producto_id=ln.producto_id, almacen_id=almacen_id,
            cantidad=base_qty, costo=costo_base,
            proveedor_id=oc.proveedor_id, orden_compra_id=oc.id,
            ref_tipo="ORDEN_COMPRA", ref_id=oc.id,
        )
        ln.cantidad_recibida = ln.cantidad_recibida + cantidad

    total_recibido = _ZERO
    completa = True
    for ln in oc.lineas:
        total_recibido += ln.cantidad_recibida * ln.precio_unitario
        if ln.cantidad_recibida < ln.cantidad_solicitada:
            completa = False
    oc.total_recibido = total_recibido
    oc.estado = "RECIBIDA" if completa else "RECIBIDA_PARCIAL"
    oc.fecha_recibida = date.today()
    db.flush()
    db.refresh(oc)
    return oc

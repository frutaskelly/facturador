"""Inventory write primitives, shared by the manual movements endpoint and the
órdenes-de-compra / remisión flows.

Keeping the lot resolution + weighted-average cost in one place means the
purchase-receipt path and the manual ENTRADA_COMPRA path can never diverge.
Callers must already be on an RLS-scoped (`get_tenant_db`) session and pass the
validated `tenant_id` from the AuthContext.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import LoteInventario, MovimientoInventario, Producto

ZERO = Decimal("0")
ONE = Decimal("1")
_Q = Decimal("0.0001")


def _presentacion_entry(producto: Optional[Producto], presentacion: Optional[str]):
    """Raw `presentaciones` entry for a presentation. Supports both the legacy
    shape `{nombre: factor}` and the rich shape `{nombre: {factor, sat, estimado}}`.
    Returns None when blank/unknown."""
    if not presentacion:
        return None
    pres = (getattr(producto, "presentaciones", None) or {})
    return pres.get(presentacion)


def presentacion_factor(producto: Optional[Producto], presentacion: Optional[str]) -> Decimal:
    """Base units contained in one unit of `presentacion` for this product.

    The base presentation — and any blank or unknown presentation — is 1:1, so
    documents that don't specify a presentation behave exactly as before. A
    non-positive or non-numeric factor is treated as 1 (defensive). Accepts both
    the legacy `{nombre: factor}` and rich `{nombre: {factor, ...}}` shapes.
    """
    raw = _presentacion_entry(producto, presentacion)
    if raw is None:
        return ONE
    val = raw.get("factor") if isinstance(raw, dict) else raw
    if val is None:
        return ONE
    try:
        factor = Decimal(str(val))
    except (ArithmeticError, ValueError):
        return ONE
    return factor if factor > 0 else ONE


def presentacion_declarada(producto: Optional[Producto], presentacion: Optional[str]) -> bool:
    """¿El producto sabe vender en esta presentación?

    Un precio en una presentación que el producto no declara es una fila MUERTA:
    los desplegables se arman de `presentaciones`, así que nadie la pedirá nunca
    y el renglón se queda en la lista aparentando estar configurado. Vacío se
    acepta — el documento sin presentación cae en la unidad base.
    """
    if not presentacion:
        return True
    conocidas = {(getattr(producto, "unidad_base", None) or "").upper()}
    conocidas |= {str(k).upper() for k in (getattr(producto, "presentaciones", None) or {})}
    conocidas.discard("")
    return presentacion.strip().upper() in conocidas


def presentacion_sat(producto: Optional[Producto], presentacion: Optional[str]) -> Optional[str]:
    """The CFDI clave_unidad (unidad SAT) for a presentation. When the rich shape
    carries a `sat`, it wins (so the same product can bill KGM por kilo y H87 por
    pieza); otherwise it falls back to the product's `unidad_sat`."""
    raw = _presentacion_entry(producto, presentacion)
    if isinstance(raw, dict) and raw.get("sat"):
        return str(raw["sat"])
    return getattr(producto, "unidad_sat", None)


def weighted_cost(qty_old: Decimal, cost_old: Decimal, qty_in: Decimal, cost_in: Decimal) -> Decimal:
    """New weighted-average unit cost after adding `qty_in` at `cost_in`."""
    total = qty_old + qty_in
    if total <= 0:
        return cost_in
    return (((qty_old * cost_old) + (qty_in * cost_in)) / total).quantize(_Q)


def resolve_lote(
    db: Session,
    tenant_id: UUID,
    producto_id: UUID,
    almacen_id: UUID,
    *,
    numero_lote: Optional[str],
    lote_id: Optional[UUID] = None,
    create: bool = False,
    fecha_caducidad=None,
    costo_inicial: Decimal = ZERO,
    proveedor_id: Optional[UUID] = None,
    orden_compra_id: Optional[UUID] = None,
) -> Optional[LoteInventario]:
    """Locate (and lock with FOR UPDATE) the target lot, optionally creating the
    default one. With `lote_id`, returns that lot or None; otherwise matches on
    (producto, almacén, numero_lote)."""
    if lote_id is not None:
        return (
            db.query(LoteInventario)
            .filter(
                LoteInventario.id == lote_id,
                LoteInventario.producto_id == producto_id,
                LoteInventario.almacen_id == almacen_id,
            )
            .with_for_update()
            .one_or_none()
        )

    query = db.query(LoteInventario).filter(
        LoteInventario.producto_id == producto_id,
        LoteInventario.almacen_id == almacen_id,
    )
    if numero_lote is not None:
        query = query.filter(LoteInventario.numero_lote == numero_lote)
    else:
        query = query.filter(LoteInventario.numero_lote.is_(None))
    lote = query.with_for_update().first()

    if lote is None and create:
        lote = LoteInventario(
            tenant_id=tenant_id,
            producto_id=producto_id,
            almacen_id=almacen_id,
            numero_lote=numero_lote,
            fecha_caducidad=fecha_caducidad,
            cantidad_inicial=ZERO,
            cantidad_disponible=ZERO,
            cantidad_reservada=ZERO,
            costo_unitario=costo_inicial,
            proveedor_id=proveedor_id,
            orden_compra_id=orden_compra_id,
        )
        db.add(lote)
        db.flush()
    return lote


def build_movimiento(
    tenant_id: UUID,
    user_id: Optional[UUID],
    lote: LoteInventario,
    tipo: str,
    cantidad: Decimal,
    *,
    costo: Optional[Decimal] = None,
    ref_tipo: Optional[str] = None,
    ref_id: Optional[UUID] = None,
    motivo: Optional[str] = None,
    notas: Optional[str] = None,
) -> MovimientoInventario:
    return MovimientoInventario(
        tenant_id=tenant_id,
        tipo=tipo,
        lote_id=lote.id,
        cantidad=cantidad,
        costo_unitario=costo,
        ref_tipo=ref_tipo,
        ref_id=ref_id,
        motivo=motivo,
        notas=notas,
        created_by=user_id,
    )


def apply_entrada_compra(
    db: Session,
    tenant_id: UUID,
    user_id: Optional[UUID],
    *,
    producto_id: UUID,
    almacen_id: UUID,
    cantidad: Decimal,
    costo: Decimal,
    numero_lote: Optional[str] = None,
    fecha_caducidad=None,
    proveedor_id: Optional[UUID] = None,
    orden_compra_id: Optional[UUID] = None,
    ref_tipo: Optional[str] = None,
    ref_id: Optional[UUID] = None,
    motivo: Optional[str] = None,
    notas: Optional[str] = None,
) -> tuple[LoteInventario, MovimientoInventario]:
    """Add stock at `costo` into the (default or numbered) lot, recomputing the
    weighted-average cost, and append the immutable ENTRADA_COMPRA movement."""
    lote = resolve_lote(
        db, tenant_id, producto_id, almacen_id,
        numero_lote=numero_lote, create=True, fecha_caducidad=fecha_caducidad,
        costo_inicial=costo, proveedor_id=proveedor_id, orden_compra_id=orden_compra_id,
    )
    lote.costo_unitario = weighted_cost(lote.cantidad_disponible, lote.costo_unitario, cantidad, costo)
    lote.cantidad_disponible = lote.cantidad_disponible + cantidad
    lote.cantidad_inicial = lote.cantidad_inicial + cantidad
    if fecha_caducidad and not lote.fecha_caducidad:
        lote.fecha_caducidad = fecha_caducidad
    if proveedor_id and not lote.proveedor_id:
        lote.proveedor_id = proveedor_id
    if orden_compra_id and not lote.orden_compra_id:
        lote.orden_compra_id = orden_compra_id
    mov = build_movimiento(
        tenant_id, user_id, lote, "ENTRADA_COMPRA", cantidad,
        costo=costo, ref_tipo=ref_tipo, ref_id=ref_id, motivo=motivo, notas=notas,
    )
    db.add(mov)
    db.flush()
    return lote, mov

"""Conversiones de producto — CRUD + substitutes lookup (Phase 4d).

Reads gated by `menu:conversiones`; writes by `conversion:gestionar`. Both
product references are re-validated under the tenant scope. Conversions are
hard-deleted (cheap mappings, recreated freely).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import ConversionProducto, Producto
from ...schemas.common import Page
from ...schemas.conversion import ConversionCreate, ConversionOut, ConversionUpdate
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/conversiones", tags=["conversiones"])

_READ = "menu:conversiones"
_WRITE = "conversion:gestionar"
_DUP = "Ya existe una conversión para ese par de productos"


@router.get("/producto/{producto_id}/disponibles", response_model=list[ConversionOut])
def conversiones_disponibles(
    producto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Active substitutes for a catalogued product, ordered by priority."""
    rows = (
        db.query(ConversionProducto)
        .filter(
            ConversionProducto.producto_catalogado_id == producto_id,
            ConversionProducto.activo.is_(True),
        )
        .order_by(ConversionProducto.prioridad.asc(), ConversionProducto.id.asc())
        .all()
    )
    return rows


@router.get("", response_model=Page[ConversionOut])
def list_conversiones(
    producto_catalogado_id: Optional[UUID] = Query(default=None),
    producto_no_catalogado_id: Optional[UUID] = Query(default=None),
    activo: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(ConversionProducto)
    if producto_catalogado_id is not None:
        query = query.filter(ConversionProducto.producto_catalogado_id == producto_catalogado_id)
    if producto_no_catalogado_id is not None:
        query = query.filter(ConversionProducto.producto_no_catalogado_id == producto_no_catalogado_id)
    if activo is not None:
        query = query.filter(ConversionProducto.activo.is_(activo))
    query = query.order_by(ConversionProducto.prioridad.asc(), ConversionProducto.id.asc())
    return paginate(query, ConversionOut, limit, offset)


@router.post("", response_model=ConversionOut, status_code=status.HTTP_201_CREATED)
def create_conversion(
    payload: ConversionCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Producto, payload.producto_catalogado_id, "producto_catalogado_id")
    ensure_fk(db, Producto, payload.producto_no_catalogado_id, "producto_no_catalogado_id")
    obj = ConversionProducto(**payload.model_dump(), tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.get("/{conversion_id}", response_model=ConversionOut)
def get_conversion(
    conversion_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, ConversionProducto, conversion_id)


@router.patch("/{conversion_id}", response_model=ConversionOut)
def update_conversion(
    conversion_id: UUID,
    payload: ConversionUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, ConversionProducto, conversion_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{conversion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversion(
    conversion_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("conversion:eliminar")),
):
    obj = get_or_404(db, ConversionProducto, conversion_id)
    db.delete(obj)
    db.flush()
    return None

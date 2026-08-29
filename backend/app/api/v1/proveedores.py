"""Proveedores / suppliers — CRUD (Phase 4 — operaciones).

Reads gated by `menu:compras` (suppliers belong to the purchasing module);
writes by `proveedor:gestionar`. Soft-deleted (referenced by órdenes de compra
and inventory lots).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Proveedor
from ...schemas.common import Page
from ...schemas.proveedor import ProveedorCreate, ProveedorOut, ProveedorUpdate
from ._helpers import flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/proveedores", tags=["proveedores"])

_READ = "menu:compras"
_WRITE = "proveedor:gestionar"
_DUP = "Ya existe un proveedor con ese código"


def _generate_codigo(db: Session, tenant_id) -> str:
    """Código secuencial por tenant: PROV-01, PROV-02, … (único)."""
    existing = {
        c
        for (c,) in db.query(Proveedor.codigo).filter(Proveedor.tenant_id == tenant_id).all()
        if c
    }
    n = len(existing) + 1
    while f"PROV-{n:02d}" in existing:
        n += 1
    return f"PROV-{n:02d}"


@router.get("", response_model=Page[ProveedorOut])
def list_proveedores(
    q: Optional[str] = Query(default=None, max_length=254),
    activo: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Proveedor).filter(Proveedor.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            Proveedor.nombre.ilike(like)
            | Proveedor.codigo.ilike(like)
            | Proveedor.rfc.ilike(like)
        )
    if activo is not None:
        query = query.filter(Proveedor.activo.is_(activo))
    query = query.order_by(Proveedor.nombre.asc())
    return paginate(query, ProveedorOut, limit, offset)


@router.post("", response_model=ProveedorOut, status_code=status.HTTP_201_CREATED)
def create_proveedor(
    payload: ProveedorCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    data = payload.model_dump()
    if not data.get("codigo"):
        data["codigo"] = _generate_codigo(db, ctx.tenant_id)
    obj = Proveedor(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.get("/{proveedor_id}", response_model=ProveedorOut)
def get_proveedor(
    proveedor_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Proveedor, proveedor_id)


@router.patch("/{proveedor_id}", response_model=ProveedorOut)
def update_proveedor(
    proveedor_id: UUID,
    payload: ProveedorUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Proveedor, proveedor_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{proveedor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_proveedor(
    proveedor_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("proveedor:eliminar")),
):
    obj = get_or_404(db, Proveedor, proveedor_id)
    obj.deleted_at = func.now()
    db.flush()
    return None

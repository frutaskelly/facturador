"""Almacenes / warehouses — CRUD (Phase 4 — operaciones).

Reads gated by `menu:inventario`; writes by `almacen:gestionar`. At most one
warehouse per tenant may be `es_default`; setting it clears the flag on any
other (enforced here — the DB only guarantees unique codes). Soft-deleted
because inventory lots reference it.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Almacen, LoteInventario
from ...schemas.almacen import AlmacenCreate, AlmacenOut, AlmacenUpdate
from ...schemas.common import Page
from ._helpers import flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/almacenes", tags=["almacenes"])

_READ = "menu:inventario"
_WRITE = "almacen:gestionar"
_DUP = "Ya existe un almacén con ese código"


def _generate_codigo(db: Session, tenant_id) -> str:
    """Código secuencial por tenant: ALM-01, ALM-02, … (único)."""
    existing = {
        c
        for (c,) in db.query(Almacen.codigo).filter(Almacen.tenant_id == tenant_id).all()
        if c
    }
    n = len(existing) + 1
    while f"ALM-{n:02d}" in existing:
        n += 1
    return f"ALM-{n:02d}"


def _clear_other_defaults(db: Session, keep_id: Optional[UUID] = None) -> None:
    """Unset es_default on every (other) warehouse in the current tenant scope."""
    query = db.query(Almacen).filter(
        Almacen.es_default.is_(True), Almacen.deleted_at.is_(None)
    )
    if keep_id is not None:
        query = query.filter(Almacen.id != keep_id)
    query.update({Almacen.es_default: False}, synchronize_session=False)


@router.get("", response_model=Page[AlmacenOut])
def list_almacenes(
    q: Optional[str] = Query(default=None, max_length=254),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Almacen).filter(Almacen.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(Almacen.nombre.ilike(like) | Almacen.codigo.ilike(like))
    query = query.order_by(Almacen.codigo.asc())
    return paginate(query, AlmacenOut, limit, offset)


@router.post("", response_model=AlmacenOut, status_code=status.HTTP_201_CREATED)
def create_almacen(
    payload: AlmacenCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    data = payload.model_dump()
    data["codigo"] = _generate_codigo(db, ctx.tenant_id)
    obj = Almacen(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    if obj.es_default:
        _clear_other_defaults(db, keep_id=obj.id)
    db.refresh(obj)
    return obj


@router.get("/{almacen_id}", response_model=AlmacenOut)
def get_almacen(
    almacen_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Almacen, almacen_id)


@router.patch("/{almacen_id}", response_model=AlmacenOut)
def update_almacen(
    almacen_id: UUID,
    payload: AlmacenUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Almacen, almacen_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    if data.get("es_default") is True:
        _clear_other_defaults(db, keep_id=obj.id)
    db.refresh(obj)
    return obj


@router.delete("/{almacen_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_almacen(
    almacen_id: UUID,
    force: bool = Query(default=False, description="Eliminar aunque tenga inventario (riesgoso)"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("almacen:eliminar")),
):
    obj = get_or_404(db, Almacen, almacen_id)
    # Guard: no permitir eliminar un almacén con existencias — desconectaría el
    # inventario (los lotes apuntan a este almacén). El usuario debe transferir o
    # ajustar a 0 primero. `force=true` es el escape para casos excepcionales.
    if not force:
        n_prod, disp = (
            db.query(
                func.count(func.distinct(LoteInventario.producto_id)),
                func.coalesce(func.sum(LoteInventario.cantidad_disponible), 0),
            )
            .filter(LoteInventario.almacen_id == almacen_id)
            .one()
        )
        if disp and disp > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"\"{obj.nombre}\" tiene inventario activo: {int(n_prod)} producto(s) "
                    f"con {float(disp):g} unidades en existencia. Si lo eliminas, ese "
                    f"inventario quedará desconectado. Transfiere o ajusta a 0 esas "
                    f"existencias antes de eliminar el almacén."
                ),
            )
    obj.deleted_at = func.now()
    db.flush()
    return None

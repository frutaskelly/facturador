"""Esquemas de impuesto — CRUD.

Reusable IVA/IEPS/retención bundles a product can point at. Reads gated by
`menu:esquemas_impuesto`; writes by `esquema_impuesto:gestionar`.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import EsquemaImpuesto, Producto
from ...schemas.esquema_impuesto import (
    EsquemaImpuestoCreate,
    EsquemaImpuestoOut,
    EsquemaImpuestoUpdate,
)
from ...schemas.common import Page
from ._helpers import flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/esquemas-impuesto", tags=["esquemas de impuesto"])

_READ = "menu:esquemas_impuesto"
_WRITE = "esquema_impuesto:gestionar"
_DUP = "Ya existe un esquema de impuesto con ese código"


def _generate_codigo(db: Session, tenant_id) -> str:
    """Código secuencial por tenant: ESQ-01, ESQ-02, … (único)."""
    existing = {
        c
        for (c,) in db.query(EsquemaImpuesto.codigo)
        .filter(EsquemaImpuesto.tenant_id == tenant_id)
        .all()
        if c
    }
    n = len(existing) + 1
    while f"ESQ-{n:02d}" in existing:
        n += 1
    return f"ESQ-{n:02d}"


@router.get("", response_model=Page[EsquemaImpuestoOut])
def list_esquemas(
    q: Optional[str] = Query(default=None, max_length=120),
    activo: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(EsquemaImpuesto).filter(EsquemaImpuesto.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            EsquemaImpuesto.nombre.ilike(like) | EsquemaImpuesto.codigo.ilike(like)
        )
    if activo is not None:
        query = query.filter(EsquemaImpuesto.activo.is_(activo))
    query = query.order_by(EsquemaImpuesto.codigo.asc())

    def _con_conteo(rows):
        ids = [r.id for r in rows]
        conteo = dict(
            db.query(Producto.esquema_impuesto_id, func.count(Producto.id))
            .filter(Producto.esquema_impuesto_id.in_(ids or [None]), Producto.deleted_at.is_(None))
            .group_by(Producto.esquema_impuesto_id)
            .all()
        ) if ids else {}
        for r in rows:
            r.productos = conteo.get(r.id, 0)

    return paginate(query, EsquemaImpuestoOut, limit, offset, preparar=_con_conteo)


@router.post("", response_model=EsquemaImpuestoOut, status_code=status.HTTP_201_CREATED)
def create_esquema(
    payload: EsquemaImpuestoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    data = payload.model_dump()
    data["codigo"] = _generate_codigo(db, ctx.tenant_id)
    obj = EsquemaImpuesto(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.get("/{esquema_id}", response_model=EsquemaImpuestoOut)
def get_esquema(
    esquema_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, EsquemaImpuesto, esquema_id)


@router.patch("/{esquema_id}", response_model=EsquemaImpuestoOut)
def update_esquema(
    esquema_id: UUID,
    payload: EsquemaImpuestoUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, EsquemaImpuesto, esquema_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{esquema_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_esquema(
    esquema_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, EsquemaImpuesto, esquema_id)
    obj.deleted_at = func.now()
    db.flush()
    return None

"""Categorías de producto — CRUD.

Reads are gated by the menu permission (`menu:productos.categorias`); writes by
the stronger action permission (`categoria:gestionar`). OWNER bypasses both in
code. Every query runs on the RLS-scoped session from `get_tenant_db`.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import CategoriaProducto, Producto
from ...schemas.categoria import CategoriaCreate, CategoriaOut, CategoriaUpdate
from ...schemas.common import Page
from ...services.categoria_codigo import generate_unique_codigo
from ._helpers import flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/categorias", tags=["categorías"])

_READ = "menu:productos.categorias"
_WRITE = "categoria:gestionar"
_DUP = "Ya existe una categoría con ese código"


@router.get("", response_model=Page[CategoriaOut])
def list_categorias(
    q: Optional[str] = Query(default=None, max_length=100),
    activo: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(CategoriaProducto).filter(CategoriaProducto.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            CategoriaProducto.nombre.ilike(like) | CategoriaProducto.codigo.ilike(like)
        )
    if activo is not None:
        query = query.filter(CategoriaProducto.activo.is_(activo))
    query = query.order_by(CategoriaProducto.nombre.asc())

    def _con_conteo(rows):
        # Cuántos productos VIVOS cuelgan de cada categoría de la página — en
        # una consulta, no una por renglón.
        ids = [r.id for r in rows]
        conteo = dict(
            db.query(Producto.categoria_id, func.count(Producto.id))
            .filter(Producto.categoria_id.in_(ids or [None]), Producto.deleted_at.is_(None))
            .group_by(Producto.categoria_id)
            .all()
        ) if ids else {}
        for r in rows:
            r.productos = conteo.get(r.id, 0)

    return paginate(query, CategoriaOut, limit, offset, preparar=_con_conteo)


@router.post("", response_model=CategoriaOut, status_code=status.HTTP_201_CREATED)
def create_categoria(
    payload: CategoriaCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    data = payload.model_dump()
    # Sin código explícito, se deriva del nombre con sufijo anticolisión.
    if not data.get("codigo"):
        data["codigo"] = generate_unique_codigo(db, ctx.tenant_id, data["nombre"])
    obj = CategoriaProducto(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.get("/{categoria_id}", response_model=CategoriaOut)
def get_categoria(
    categoria_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, CategoriaProducto, categoria_id)


@router.patch("/{categoria_id}", response_model=CategoriaOut)
def update_categoria(
    categoria_id: UUID,
    payload: CategoriaUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, CategoriaProducto, categoria_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP)
    db.refresh(obj)
    return obj


@router.delete("/{categoria_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_categoria(
    categoria_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission("categoria:eliminar")),
):
    obj = get_or_404(db, CategoriaProducto, categoria_id)
    obj.deleted_at = func.now()
    db.flush()
    return None

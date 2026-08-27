"""Listas de precios y sus precios — CRUD.

A price list is a named, tenant-scoped collection; each `Precio` is one price
for a (producto, presentación, cantidad_minima) tier inside a list. Both reads
are gated by `menu:listas_precios`; both writes by `lista_precios:gestionar`.

Price lists are soft-deleted (they may be referenced by clients); individual
prices are hard-deleted (they're cheap line items, recreated freely).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ListaPrecios, Precio, Producto
from ...schemas.lista_precios import (
    ListaAsignarIn,
    ListaAsignarOut,
    ListaPreciosCreate,
    ListaPreciosOut,
    ListaPreciosUpdate,
    PrecioBulkRequest,
    PrecioBulkResult,
    PrecioCopiarRequest,
    PrecioCreate,
    PrecioOut,
    PrecioUpdate,
)
from ...schemas.common import Page
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/listas-precios", tags=["listas de precios"])

_READ = "menu:listas_precios"
_WRITE = "lista_precios:gestionar"
_DUP_LISTA = "Ya existe una lista de precios con ese código"
_DUP_PRECIO = "Ya existe un precio para ese producto/presentación/cantidad en la lista"


# ─── price lists ─────────────────────────────────────────────────────────────
@router.get("", response_model=Page[ListaPreciosOut])
def list_listas(
    q: Optional[str] = Query(default=None, max_length=254),
    status_: Optional[str] = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(ListaPrecios).filter(ListaPrecios.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(
            ListaPrecios.nombre.ilike(like) | ListaPrecios.codigo.ilike(like)
        )
    if status_:
        query = query.filter(ListaPrecios.status == status_)
    query = query.order_by(ListaPrecios.codigo.asc())
    return paginate(query, ListaPreciosOut, limit, offset)


@router.post("", response_model=ListaPreciosOut, status_code=status.HTTP_201_CREATED)
def create_lista(
    payload: ListaPreciosCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = ListaPrecios(**payload.model_dump(), tenant_id=ctx.tenant_id)
    db.add(obj)
    flush_or_conflict(db, detail=_DUP_LISTA)
    db.refresh(obj)
    return obj


@router.get("/{lista_id}", response_model=ListaPreciosOut)
def get_lista(
    lista_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, ListaPrecios, lista_id)


@router.patch("/{lista_id}", response_model=ListaPreciosOut)
def update_lista(
    lista_id: UUID,
    payload: ListaPreciosUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, ListaPrecios, lista_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP_LISTA)
    db.refresh(obj)
    return obj


@router.delete("/{lista_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lista(
    lista_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, ListaPrecios, lista_id)
    obj.deleted_at = func.now()
    db.flush()
    return None


# ─── copy prices from another list ───────────────────────────────────────────
@router.post("/{lista_id}/copiar", response_model=PrecioBulkResult)
def copiar_precios(
    lista_id: UUID,
    payload: PrecioCopiarRequest,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Copy ALL precios from `origen_id` into `lista_id`, skipping duplicates.

    A row is a duplicate when the destination already has a precio for the same
    (producto, presentación, cantidad_minima) tier.
    """
    get_or_404(db, ListaPrecios, lista_id)
    get_or_404(db, ListaPrecios, payload.origen_id)  # 404 if origen isn't ours
    if payload.origen_id == lista_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La lista de origen no puede ser la misma que el destino",
        )

    origen_precios = db.query(Precio).filter(Precio.lista_id == payload.origen_id).all()
    existing = {
        (p.producto_id, p.presentacion, p.cantidad_minima)
        for p in db.query(Precio).filter(Precio.lista_id == lista_id).all()
    }

    created = 0
    skipped = 0
    for src in origen_precios:
        key = (src.producto_id, src.presentacion, src.cantidad_minima)
        if key in existing:
            skipped += 1
            continue
        db.add(
            Precio(
                tenant_id=ctx.tenant_id,
                lista_id=lista_id,
                producto_id=src.producto_id,
                presentacion=src.presentacion,
                precio_unitario=src.precio_unitario,
                cantidad_minima=src.cantidad_minima,
                vigencia_desde=src.vigencia_desde,
                vigencia_hasta=src.vigencia_hasta,
            )
        )
        existing.add(key)
        created += 1

    db.flush()
    return PrecioBulkResult(created=created, updated=0, skipped=skipped)


# ─── prices (nested under a list) ────────────────────────────────────────────
@router.get("/{lista_id}/precios", response_model=Page[PrecioOut])
def list_precios(
    lista_id: UUID,
    producto_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    get_or_404(db, ListaPrecios, lista_id)  # 404 if the list isn't ours
    query = db.query(Precio).filter(Precio.lista_id == lista_id)
    if producto_id is not None:
        query = query.filter(Precio.producto_id == producto_id)
    query = query.order_by(Precio.producto_id.asc(), Precio.cantidad_minima.asc())
    return paginate(query, PrecioOut, limit, offset)


@router.post(
    "/{lista_id}/precios",
    response_model=PrecioOut,
    status_code=status.HTTP_201_CREATED,
)
def create_precio(
    lista_id: UUID,
    payload: PrecioCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    get_or_404(db, ListaPrecios, lista_id)
    ensure_fk(db, Producto, payload.producto_id, "producto_id")
    obj = Precio(
        **payload.model_dump(),
        tenant_id=ctx.tenant_id,
        lista_id=lista_id,
    )
    db.add(obj)
    flush_or_conflict(db, detail=_DUP_PRECIO)
    db.refresh(obj)
    return obj


@router.post("/{lista_id}/precios/bulk", response_model=PrecioBulkResult)
def bulk_upsert_precios(
    lista_id: UUID,
    payload: PrecioBulkRequest,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Upsert many precios at once.

    For each item, if a precio already exists for the same
    (producto, presentación, cantidad_minima) tier its `precio_unitario` is
    overwritten; otherwise a new row is created. Productos outside the tenant
    scope are rejected.
    """
    get_or_404(db, ListaPrecios, lista_id)

    existing = {
        (p.producto_id, p.presentacion, p.cantidad_minima): p
        for p in db.query(Precio).filter(Precio.lista_id == lista_id).all()
    }
    validated_productos: set = set()

    created = 0
    updated = 0
    for item in payload.items:
        if item.producto_id not in validated_productos:
            ensure_fk(db, Producto, item.producto_id, "producto_id")
            validated_productos.add(item.producto_id)
        key = (item.producto_id, item.presentacion, item.cantidad_minima)
        current = existing.get(key)
        if current is not None:
            current.precio_unitario = item.precio_unitario
            updated += 1
        else:
            obj = Precio(
                tenant_id=ctx.tenant_id,
                lista_id=lista_id,
                producto_id=item.producto_id,
                presentacion=item.presentacion,
                precio_unitario=item.precio_unitario,
                cantidad_minima=item.cantidad_minima,
            )
            db.add(obj)
            existing[key] = obj
            created += 1

    flush_or_conflict(db, detail=_DUP_PRECIO)
    return PrecioBulkResult(created=created, updated=updated, skipped=0)


@router.patch("/{lista_id}/precios/{precio_id}", response_model=PrecioOut)
def update_precio(
    lista_id: UUID,
    precio_id: UUID,
    payload: PrecioUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = (
        db.query(Precio)
        .filter(Precio.id == precio_id, Precio.lista_id == lista_id)
        .one_or_none()
    )
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Precio no encontrado")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    flush_or_conflict(db, detail=_DUP_PRECIO)
    db.refresh(obj)
    return obj


@router.delete(
    "/{lista_id}/precios/{precio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_precio(
    lista_id: UUID,
    precio_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = (
        db.query(Precio)
        .filter(Precio.id == precio_id, Precio.lista_id == lista_id)
        .one_or_none()
    )
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Precio no encontrado")
    db.delete(obj)
    db.flush()
    return None


@router.post("/{lista_id}/asignar", response_model=ListaAsignarOut)
def asignar_lista(
    lista_id: UUID,
    payload: ListaAsignarIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Asignar la lista como DEFAULT del negocio (la usan los clientes sin lista
    propia) y/o a clientes específicos — el último paso del wizard de
    importación, también disponible desde la administración de listas."""
    lista = get_or_404(db, ListaPrecios, lista_id)
    if payload.default:
        # Solo puede haber una default: se limpia la anterior.
        db.query(ListaPrecios).filter(
            ListaPrecios.es_default.is_(True), ListaPrecios.id != lista.id
        ).update({"es_default": False})
        lista.es_default = True
    asignados = 0
    for cid in payload.cliente_ids:
        cli = (
            db.query(Cliente)
            .filter(Cliente.id == cid, Cliente.deleted_at.is_(None))
            .one_or_none()
        )
        if cli is not None:
            cli.lista_precios_id = lista.id
            asignados += 1
    db.flush()
    return ListaAsignarOut(default=lista.es_default, clientes_asignados=asignados)

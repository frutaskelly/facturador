"""Small shared helpers for the Phase 3 catalog CRUD routers.

These exist to keep the routers thin and to enforce two cross-cutting rules in
one place:

  * `get_or_404` / `ensure_fk` re-check row visibility *under the RLS-scoped
    session*. Postgres does NOT subject foreign-key integrity checks to RLS, so
    a cross-tenant id could otherwise be accepted by the raw FK constraint. We
    re-validate at the app layer: if a referenced row isn't visible in the
    current tenant scope, it doesn't exist as far as this request is concerned.
  * `flush_or_conflict` turns a unique-constraint violation into a clean 409
    instead of a 500.
"""
from __future__ import annotations

from typing import Optional, Type, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from ...schemas.common import Page

M = TypeVar("M")
T = TypeVar("T")


def get_or_404(
    db: Session, model: Type[M], obj_id: UUID, *, soft: bool = True, for_update: bool = False
) -> M:
    """Fetch a row by id within the current tenant scope, or raise 404.

    Soft-deleted rows (deleted_at set) are treated as absent. `for_update=True`
    takes a row lock (SELECT … FOR UPDATE): úsalo en transiciones de estado no
    idempotentes (timbrar/cancelar) para serializar requests concurrentes.
    """
    query = db.query(model).filter(model.id == obj_id)
    if soft and hasattr(model, "deleted_at"):
        query = query.filter(model.deleted_at.is_(None))
    if for_update:
        query = query.with_for_update()
    obj = query.one_or_none()
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{model.__name__} no encontrado",
        )
    return obj


def ensure_fk(
    db: Session,
    model: Type[M],
    obj_id: Optional[UUID],
    field: str,
    *,
    soft: bool = True,
) -> None:
    """Validate a referenced id is visible in the current tenant scope.

    A no-op when `obj_id` is None (optional FK). Raises 422 otherwise.
    """
    if obj_id is None:
        return
    query = db.query(model.id).filter(model.id == obj_id)
    if soft and hasattr(model, "deleted_at"):
        query = query.filter(model.deleted_at.is_(None))
    if query.first() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} inválido o fuera de alcance",
        )


def flush_or_conflict(db: Session, *, detail: str = "Registro duplicado") -> None:
    """Flush pending changes, mapping a unique violation to 409 Conflict."""
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def paginate(
    query: Query, out_model: Type[T], limit: int, offset: int, *, preparar=None
) -> Page:
    """Count the (ordered) query, then return one page mapped to `out_model`.

    `preparar` recibe los renglones de la página ANTES de serializarlos: es el
    lugar para resolver en UNA consulta lo que si no sería una por renglón.
    """
    total = query.order_by(None).count()
    rows = query.offset(offset).limit(limit).all()
    if preparar is not None:
        preparar(rows)
    return Page[out_model](
        items=[out_model.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )

"""Proyectos — la negociación con nombre propio ("HOSPITALES E IMSS BIENESTAR").

Reads gated por `menu:clientes` (quien vende necesita verlos para etiquetar un
documento); writes por `cliente:gestionar`, porque un proyecto es parte del alta
comercial del cliente y mover uno cambia qué precios se cobran.

El `codigo` se autogenera del nombre y NO se acepta del cliente HTTP — misma
convención que categorías, sucursales y proveedores.
"""
from __future__ import annotations

import unicodedata
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ListaAsignacion, Proyecto
from ...schemas.common import Page
from ...schemas.proyecto import ProyectoCreate, ProyectoOut, ProyectoUpdate
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/proyectos", tags=["proyectos"])

_READ = "menu:clientes"
_WRITE = "cliente:gestionar"
_MAX_LEN = 20  # debe coincidir con String(20) en el modelo


def _slug(nombre: str) -> str:
    """"Hospitales e IMSS Bienestar" → "HOSPITALES" (sin acentos, A-Z0-9)."""
    sin_acentos = (
        unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    )
    return "".join(ch for ch in sin_acentos.upper() if ch.isalnum())[:10] or "PROY"


def _generar_codigo(db: Session, tenant_id, nombre: str, *, exclude_id=None) -> str:
    """Código derivado del nombre, único dentro del inquilino.

    Se compara contra TODAS las filas (incluidas las borradas lógicamente):
    la restricción única no distingue, y chocar ahí sería un 409 sin remedio.
    """
    base = _slug(nombre)
    q = db.query(Proyecto.codigo).filter(Proyecto.tenant_id == tenant_id)
    if exclude_id is not None:
        q = q.filter(Proyecto.id != exclude_id)
    tomados = {c for (c,) in q.all()}
    if base not in tomados:
        return base
    for n in range(2, 1000):
        sufijo = str(n)
        candidato = base[: _MAX_LEN - len(sufijo)] + sufijo
        if candidato not in tomados:
            return candidato
    return base


@router.get("", response_model=Page[ProyectoOut])
def list_proyectos(
    cliente_id: Optional[UUID] = Query(default=None),
    activo: Optional[bool] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Proyecto).filter(Proyecto.deleted_at.is_(None))
    if cliente_id is not None:
        # Los proyectos del grupo (sin dueño) aplican a cualquier cliente, así
        # que también salen al filtrar por uno.
        query = query.filter(
            or_(Proyecto.cliente_id == cliente_id, Proyecto.cliente_id.is_(None))
        )
    if activo is not None:
        query = query.filter(Proyecto.activo.is_(activo))
    if q:
        query = query.filter(Proyecto.nombre.ilike(f"%{q}%"))
    return paginate(query.order_by(Proyecto.nombre.asc()), ProyectoOut, limit, offset)


@router.get("/{proyecto_id}", response_model=ProyectoOut)
def get_proyecto(
    proyecto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    return get_or_404(db, Proyecto, proyecto_id)


@router.post("", response_model=ProyectoOut, status_code=status.HTTP_201_CREATED)
def create_proyecto(
    payload: ProyectoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    obj = Proyecto(
        **payload.model_dump(),
        tenant_id=ctx.tenant_id,
        codigo=_generar_codigo(db, ctx.tenant_id, payload.nombre),
    )
    db.add(obj)
    flush_or_conflict(db, detail="Ya existe un proyecto con ese código")
    db.refresh(obj)
    return obj


@router.patch("/{proyecto_id}", response_model=ProyectoOut)
def update_proyecto(
    proyecto_id: UUID,
    payload: ProyectoUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Proyecto, proyecto_id)
    data = payload.model_dump(exclude_unset=True)
    if "cliente_id" in data:
        ensure_fk(db, Cliente, data["cliente_id"], "cliente_id")
    for key, value in data.items():
        setattr(obj, key, value)
    # El código sigue al nombre: si no, un proyecto renombrado se queda con las
    # siglas del anterior y nadie lo reconoce en los reportes.
    if data.get("nombre"):
        obj.codigo = _generar_codigo(db, ctx.tenant_id, data["nombre"], exclude_id=obj.id)
    flush_or_conflict(db, detail="Ya existe un proyecto con ese código")
    db.refresh(obj)
    return obj


@router.delete("/{proyecto_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_proyecto(
    proyecto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Proyecto, proyecto_id)
    # Las asignaciones de precio que colgaban del proyecto se van con él: un
    # proyecto archivado que siguiera fijando precios sería invisible y activo.
    db.query(ListaAsignacion).filter(ListaAsignacion.proyecto_id == obj.id).delete()
    obj.deleted_at = func.now()
    db.flush()
    return None

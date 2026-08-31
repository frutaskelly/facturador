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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ListaAsignacion, Proyecto, ProyectoSucursal, Sucursal
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


def _sync_sucursales(db: Session, ctx, obj: Proyecto, sucursal_ids) -> None:
    """Persiste el ALCANCE del proyecto (migración 0058): en qué sucursales
    entrega. Si el proyecto tiene dueño, cada sucursal debe ser de ese cliente
    — mezclar plazas de otro cliente cobraría con la negociación equivocada.
    Un proyecto del grupo (sin dueño) sí puede abarcar varios clientes.
    """
    deseadas = []
    for sid in sucursal_ids or []:
        s = (
            db.query(Sucursal)
            .filter(Sucursal.id == sid, Sucursal.deleted_at.is_(None))
            .one_or_none()
        )
        if s is None:
            raise HTTPException(status_code=422, detail=f"La sucursal {sid} no existe")
        if obj.cliente_id is not None and s.cliente_id != obj.cliente_id:
            raise HTTPException(
                status_code=422,
                detail=f"La sucursal «{s.nombre}» no es del cliente del proyecto",
            )
        deseadas.append(s)
    db.query(ProyectoSucursal).filter(ProyectoSucursal.proyecto_id == obj.id).delete()
    for s in deseadas:
        db.add(ProyectoSucursal(tenant_id=ctx.tenant_id, proyecto_id=obj.id, sucursal_id=s.id))
    # La sesión corre con autoflush=False: sin esto, la hidratación de la
    # respuesta no vería las ligas recién agregadas.
    db.flush()


def _validar_alcance_vigente(db: Session, obj: Proyecto) -> None:
    """Al cambiar el dueño SIN mandar sucursal_ids: si alguna sucursal asignada
    no es del nuevo cliente, se rechaza (mejor que soltarla en silencio)."""
    if obj.cliente_id is None:
        return
    ajena = (
        db.query(Sucursal.nombre)
        .join(ProyectoSucursal, ProyectoSucursal.sucursal_id == Sucursal.id)
        .filter(
            ProyectoSucursal.proyecto_id == obj.id,
            Sucursal.cliente_id != obj.cliente_id,
        )
        .first()
    )
    if ajena is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"La sucursal «{ajena[0]}» asignada al proyecto no es del nuevo "
                "cliente; actualiza también las sucursales"
            ),
        )


def _con_sucursales(db: Session, rows) -> None:
    """Cuelga sucursal_ids / sucursales_nombres a cada proyecto de la página en
    dos consultas (no una por renglón)."""
    filas = list(rows)
    ids = [r.id for r in filas]
    if not ids:
        return
    nombres = dict(db.query(Sucursal.id, Sucursal.nombre).all())
    por_proyecto = {}
    for ps in db.query(ProyectoSucursal).filter(ProyectoSucursal.proyecto_id.in_(ids)):
        por_proyecto.setdefault(ps.proyecto_id, []).append(ps.sucursal_id)
    for r in filas:
        r.sucursal_ids = por_proyecto.get(r.id, [])
        r.sucursales_nombres = [nombres[s] for s in r.sucursal_ids if s in nombres]


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
    if ctx.cliente_scope:
        # Los del candado + los del GRUPO (sin dueño), que aplican a cualquier
        # cliente — el cotizador los espera igual que el filtro por cliente.
        from sqlalchemy import or_ as _or
        query = query.filter(_or(
            Proyecto.cliente_id.in_(ctx.cliente_scope),
            Proyecto.cliente_id.is_(None),
        ))
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
    return paginate(
        query.order_by(Proyecto.nombre.asc()), ProyectoOut, limit, offset,
        preparar=lambda rows: _con_sucursales(db, rows),
    )


@router.get("/{proyecto_id}", response_model=ProyectoOut)
def get_proyecto(
    proyecto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    obj = get_or_404(db, Proyecto, proyecto_id)
    if obj.cliente_id is not None and not ctx.cliente_permitido(obj.cliente_id):
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    _con_sucursales(db, [obj])
    return obj


@router.post("", response_model=ProyectoOut, status_code=status.HTTP_201_CREATED)
def create_proyecto(
    payload: ProyectoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    data = payload.model_dump()
    sucursal_ids = data.pop("sucursal_ids", [])
    obj = Proyecto(
        **data,
        tenant_id=ctx.tenant_id,
        codigo=_generar_codigo(db, ctx.tenant_id, payload.nombre),
    )
    db.add(obj)
    flush_or_conflict(db, detail="Ya existe un proyecto con ese código")
    _sync_sucursales(db, ctx, obj, sucursal_ids)
    db.refresh(obj)
    _con_sucursales(db, [obj])
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
    sucursal_ids = data.pop("sucursal_ids", None)
    for key, value in data.items():
        setattr(obj, key, value)
    # El código sigue al nombre: si no, un proyecto renombrado se queda con las
    # siglas del anterior y nadie lo reconoce en los reportes.
    if data.get("nombre"):
        obj.codigo = _generar_codigo(db, ctx.tenant_id, data["nombre"], exclude_id=obj.id)
    if sucursal_ids is not None:
        _sync_sucursales(db, ctx, obj, sucursal_ids)
    elif "cliente_id" in data:
        # Cambió el dueño sin retocar el alcance: las sucursales ya asignadas
        # deben seguir siendo suyas, o el proyecto cobraría en plazas ajenas.
        _validar_alcance_vigente(db, obj)
    flush_or_conflict(db, detail="Ya existe un proyecto con ese código")
    db.refresh(obj)
    _con_sucursales(db, [obj])
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

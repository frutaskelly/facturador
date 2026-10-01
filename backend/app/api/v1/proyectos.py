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
from ...models import Cliente, ListaAsignacion, ListaPrecios, Proyecto, Sucursal
from ...services.sucursales import es_sucursal_de
from ...schemas.common import Page
from ...schemas.proyecto import ProyectoCreate, ProyectoOut, ProyectoUpdate
from ._helpers import ensure_fk, flush_or_conflict, get_or_404, paginate

router = APIRouter(prefix="/proyectos", tags=["proyectos"])

_READ = "menu:clientes"
_WRITE = "cliente:gestionar"
_MAX_LEN = 20  # debe coincidir con String(20) en el modelo


def _slug(nombre: str) -> str:
    """"Hospitales Tuxtla" → "HOSPITALESTUXTLA" (sin acentos, A-Z0-9).

    Usa el largo completo de la columna: con 10 letras, «HOSPITALES HIDALGO» y
    «HOSPITALES TUXTLA» quedaban en HOSPITALES y HOSPITALES2."""
    sin_acentos = (
        unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    )
    return "".join(ch for ch in sin_acentos.upper() if ch.isalnum())[:_MAX_LEN] or "PROY"


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


def _validar_plaza(db: Session, obj: Proyecto, sucursal_id) -> None:
    """La plaza del proyecto debe existir y, si el proyecto tiene dueño, estar
    VINCULADA a ese cliente — un proyecto en una plaza que no lo surte cobraría
    con la negociación equivocada. Uno del grupo (sin dueño) acepta cualquiera.
    """
    if sucursal_id is None:
        return
    s = (
        db.query(Sucursal)
        .filter(Sucursal.id == sucursal_id, Sucursal.deleted_at.is_(None))
        .one_or_none()
    )
    if s is None:
        raise HTTPException(status_code=422, detail="Esa sucursal no existe")
    if obj.cliente_id is not None and not es_sucursal_de(db, s.id, obj.cliente_id):
        raise HTTPException(
            status_code=422,
            detail=f"El cliente del proyecto no se surte de «{s.nombre}»",
        )


def _validar_reporta_en(db: Session, obj: Proyecto) -> None:
    """Un solo nivel: «se reporta en» apunta a un proyecto que se reporta a sí
    mismo. Dos saltos (o un ciclo) harían que nadie supiera en qué fila cae."""
    if obj.reporta_en_id is None:
        return
    if obj.reporta_en_id == obj.id:
        raise HTTPException(status_code=422, detail="Un proyecto no se reporta en sí mismo")
    padre = (
        db.query(Proyecto)
        .filter(Proyecto.id == obj.reporta_en_id, Proyecto.deleted_at.is_(None))
        .one_or_none()
    )
    if padre is None:
        raise HTTPException(status_code=422, detail="Ese proyecto no existe")
    if padre.reporta_en_id is not None:
        raise HTTPException(
            status_code=422,
            detail=f"«{padre.nombre}» ya se reporta en otro proyecto; elige ese directamente",
        )
    if obj.id is not None:
        hijos = (
            db.query(Proyecto.nombre)
            .filter(Proyecto.reporta_en_id == obj.id, Proyecto.deleted_at.is_(None))
            .first()
        )
        if hijos:
            raise HTTPException(
                status_code=422,
                detail=f"«{hijos[0]}» se reporta en este proyecto; no puede reportarse en otro",
            )


def _validar_series(db: Session, obj: Proyecto) -> None:
    """Cada factura tiene que caer en UN proyecto. Si dos comparten una serie,
    sólo uno puede quedarse sin palabras (el que se lleva el resto); los demás
    se distinguen por lo que dice la observación."""
    if not obj.series:
        return
    # Pocos proyectos por inquilino: se comparan en Python y no con operadores
    # JSONB que dependen del driver.
    otros = db.query(Proyecto).filter(Proyecto.deleted_at.is_(None))
    if obj.id is not None:
        otros = otros.filter(Proyecto.id != obj.id)
    for otro in otros:
        comunes = sorted(set(obj.series) & set(otro.series or []))
        if not comunes:
            continue
        if not obj.palabras_obs and not otro.palabras_obs:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"La serie {', '.join(comunes)} ya es de «{otro.nombre}». Si la comparten, "
                    "ponle a uno las palabras de la observación que lo distinguen."
                ),
            )


def _guardar_lista(db: Session, obj: Proyecto, lista_id) -> None:
    """La lista del proyecto es su renglón de asignación (especificidad 8): se
    cambia ahí mismo, para que no haga falta ir a otra pantalla a «asignar»."""
    actuales = (
        db.query(ListaAsignacion)
        .filter(ListaAsignacion.proyecto_id == obj.id, ListaAsignacion.serie_id.is_(None))
        .all()
    )
    if lista_id is None:
        for a in actuales:
            db.delete(a)
        return
    ensure_fk(db, ListaPrecios, lista_id, "lista_id")
    if actuales:
        for a in actuales:
            a.lista_id = lista_id
        return
    db.add(ListaAsignacion(
        tenant_id=obj.tenant_id, lista_id=lista_id,
        cliente_id=obj.cliente_id, proyecto_id=obj.id,
    ))


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
    return paginate(query.order_by(Proyecto.nombre.asc()), ProyectoOut, limit, offset)


@router.get("/{proyecto_id}", response_model=ProyectoOut)
def get_proyecto(
    proyecto_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    obj = get_or_404(db, Proyecto, proyecto_id)
    if obj.cliente_id is not None and not ctx.cliente_permitido(obj.cliente_id):
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return obj


@router.post("", response_model=ProyectoOut, status_code=status.HTTP_201_CREATED)
def create_proyecto(
    payload: ProyectoCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    obj = Proyecto(
        **payload.model_dump(exclude={"lista_id"}),
        tenant_id=ctx.tenant_id,
        codigo=_generar_codigo(db, ctx.tenant_id, payload.nombre),
    )
    _validar_plaza(db, obj, obj.sucursal_id)
    _validar_reporta_en(db, obj)
    _validar_series(db, obj)
    db.add(obj)
    flush_or_conflict(db, detail="Ya existe un proyecto con ese código")
    if payload.lista_id is not None:
        _guardar_lista(db, obj, payload.lista_id)
        db.flush()
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
    cambia_lista = "lista_id" in data
    lista_id = data.pop("lista_id", None)
    if "cliente_id" in data:
        ensure_fk(db, Cliente, data["cliente_id"], "cliente_id")
    for key, value in data.items():
        setattr(obj, key, value)
    # El código sigue al nombre: si no, un proyecto renombrado se queda con las
    # siglas del anterior y nadie lo reconoce en los reportes.
    if data.get("nombre"):
        obj.codigo = _generar_codigo(db, ctx.tenant_id, data["nombre"], exclude_id=obj.id)
    if "sucursal_id" in data or "cliente_id" in data:
        # Se valida la combinación EFECTIVA: cambiar solo el dueño no debe
        # dejar el proyecto en una plaza que no surte al nuevo cliente.
        _validar_plaza(db, obj, obj.sucursal_id)
    if "reporta_en_id" in data:
        _validar_reporta_en(db, obj)
    if "series" in data or "palabras_obs" in data:
        _validar_series(db, obj)
    if cambia_lista:
        _guardar_lista(db, obj, lista_id)
    flush_or_conflict(db, detail="Ya existe un proyecto con ese código")
    db.expire(obj, ["asignaciones"])
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

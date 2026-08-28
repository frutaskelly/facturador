"""Sucursales (ship-to) de un cliente — precios v2.

Reads gated por `menu:clientes`; writes por `cliente:gestionar` (las sucursales
son parte del alta comercial del cliente).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, Sucursal, Serie, SucursalSerie
from ...schemas.common import Page
from ...schemas.sucursal import SucursalCreate, SucursalOut, SucursalUpdate
from ._helpers import ensure_fk, get_or_404, paginate

router = APIRouter(prefix="/sucursales", tags=["sucursales"])

_READ = "menu:clientes"
_WRITE = "cliente:gestionar"


def _generate_codigo(db: Session, tenant_id, cliente_id) -> str:
    """Código secuencial por cliente: SUC-01, SUC-02, … (único dentro del cliente)."""
    existing = {
        c
        for (c,) in db.query(Sucursal.codigo)
        .filter(Sucursal.tenant_id == tenant_id, Sucursal.cliente_id == cliente_id)
        .all()
        if c
    }
    n = len(existing) + 1
    while f"SUC-{n:02d}" in existing:
        n += 1
    return f"SUC-{n:02d}"




def _sync_series_sucursal(db, ctx, obj, series_factura_ids, series_remision_ids):
    """Persiste el ABANICO de series disponibles (0056) y acomoda las defaults.

    La primera de cada tipo se vuelve la default de la sucursal si no hay una;
    si la default vigente quedó FUERA del abanico, se reemplaza por la primera
    (una default que la sucursal ya no ofrece cobraría con la serie equivocada).
    """
    deseadas = []
    for sid in list(series_factura_ids or []) + list(series_remision_ids or []):
        s = db.query(Serie).filter(Serie.id == sid, Serie.tenant_id == ctx.tenant_id).one_or_none()
        if s is None:
            raise HTTPException(status_code=422, detail=f"La serie {sid} no existe")
        deseadas.append(s)
    db.query(SucursalSerie).filter(SucursalSerie.sucursal_id == obj.id).delete()
    for s in deseadas:
        db.add(SucursalSerie(tenant_id=ctx.tenant_id, sucursal_id=obj.id, serie_id=s.id))
    fact = [s.id for s in deseadas if s.tipo_documento == "FACTURA"]
    rem = [s.id for s in deseadas if s.tipo_documento == "REMISION"]
    if fact and (obj.serie_factura_id is None or obj.serie_factura_id not in fact):
        obj.serie_factura_id = fact[0]
    if rem and (obj.serie_remision_id is None or obj.serie_remision_id not in rem):
        obj.serie_remision_id = rem[0]
    if not fact:
        # sin abanico de facturas: la default explícita (si vino) también cuenta
        if obj.serie_factura_id:
            db.add(SucursalSerie(tenant_id=ctx.tenant_id, sucursal_id=obj.id, serie_id=obj.serie_factura_id))
    if not rem and obj.serie_remision_id:
        db.add(SucursalSerie(tenant_id=ctx.tenant_id, sucursal_id=obj.id, serie_id=obj.serie_remision_id))


def _con_series(db, rows):
    """Cuelga series_factura_ids / series_remision_ids a cada sucursal de la
    página en dos consultas (no una por renglón)."""
    filas = list(rows)
    ids = [r.id for r in filas]
    if not ids:
        return
    tipos = dict(db.query(Serie.id, Serie.tipo_documento).all())
    por_suc = {}
    for ss in db.query(SucursalSerie).filter(SucursalSerie.sucursal_id.in_(ids)):
        por_suc.setdefault(ss.sucursal_id, []).append(ss.serie_id)
    for r in filas:
        serie_ids = por_suc.get(r.id, [])
        r.series_factura_ids = [s for s in serie_ids if tipos.get(s) == "FACTURA"]
        r.series_remision_ids = [s for s in serie_ids if tipos.get(s) == "REMISION"]


@router.get("", response_model=Page[SucursalOut])
def list_sucursales(
    cliente_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Sucursal).filter(Sucursal.deleted_at.is_(None))
    if cliente_id is not None:
        query = query.filter(Sucursal.cliente_id == cliente_id)
    query = query.order_by(Sucursal.nombre.asc())
    return paginate(query, SucursalOut, limit, offset, preparar=lambda rows: _con_series(db, rows))


@router.get("/{sucursal_id}", response_model=SucursalOut)
def get_sucursal(
    sucursal_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    obj = get_or_404(db, Sucursal, sucursal_id)
    _con_series(db, [obj])
    return obj


@router.post("", response_model=SucursalOut, status_code=status.HTTP_201_CREATED)
def create_sucursal(
    payload: SucursalCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
    data = payload.model_dump(exclude={"series_factura_ids", "series_remision_ids"})
    # El código se autogenera por cliente si no viene dado.
    if not data.get("codigo"):
        data["codigo"] = _generate_codigo(db, ctx.tenant_id, payload.cliente_id)
    obj = Sucursal(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    db.flush()
    _sync_series_sucursal(db, ctx, obj, payload.series_factura_ids, payload.series_remision_ids)
    db.flush()
    db.refresh(obj)
    _con_series(db, [obj])
    return obj


@router.patch("/{sucursal_id}", response_model=SucursalOut)
def update_sucursal(
    sucursal_id: UUID,
    payload: SucursalUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Sucursal, sucursal_id)
    data = payload.model_dump(exclude_unset=True)
    series_f = data.pop("series_factura_ids", None)
    series_r = data.pop("series_remision_ids", None)
    for key, value in data.items():
        setattr(obj, key, value)
    if series_f is not None or series_r is not None:
        actuales_f = [s.serie_id for s in db.query(SucursalSerie).join(Serie, Serie.id == SucursalSerie.serie_id)
                      .filter(SucursalSerie.sucursal_id == obj.id, Serie.tipo_documento == "FACTURA")]
        actuales_r = [s.serie_id for s in db.query(SucursalSerie).join(Serie, Serie.id == SucursalSerie.serie_id)
                      .filter(SucursalSerie.sucursal_id == obj.id, Serie.tipo_documento == "REMISION")]
        _sync_series_sucursal(db, ctx, obj,
                              series_f if series_f is not None else actuales_f,
                              series_r if series_r is not None else actuales_r)
    db.flush()
    db.refresh(obj)
    _con_series(db, [obj])
    return obj


@router.delete("/{sucursal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sucursal(
    sucursal_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Sucursal, sucursal_id)
    obj.deleted_at = func.now()
    db.flush()
    return None

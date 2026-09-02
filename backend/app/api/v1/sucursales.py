"""Sucursales (plazas) — unidades de negocio del tenant.

Desde el rediseño 01-sep-2026 la sucursal NO es de un cliente: es la plaza
(Pachuca, Tabasco) y los clientes se VINCULAN a ella (`cliente_sucursales`).
El vínculo carga la serie de folios de esa relación y su abanico de series
disponibles; la plaza carga el almacén, que aplica a todos sus clientes.

Compatibilidad con los selectores de emisión: `GET /sucursales?cliente_id=X`
sigue existiendo y devuelve las plazas VINCULADAS a X — y cada fila trae
`serie_factura_id` / `serie_remision_id` / abanicos DEL VÍNCULO con ese
cliente, que es lo que el cotizador, la bandeja y las remisiones esperaban
cuando esas columnas vivían en la sucursal.

Reads gated por `menu:clientes`; writes por `cliente:gestionar` (vincular una
plaza sigue siendo parte del alta comercial del cliente).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ClienteSucursal, ClienteSucursalSerie, Serie, Sucursal
from ...schemas.common import Page
from ...schemas.sucursal import (
    ClienteSucursalOut,
    ClienteSucursalUpsert,
    SucursalCreate,
    SucursalOut,
    SucursalUpdate,
)
from ._helpers import ensure_fk, get_or_404, paginate

router = APIRouter(prefix="/sucursales", tags=["sucursales"])

_READ = "menu:clientes"
_WRITE = "cliente:gestionar"


def _generate_codigo(db: Session, tenant_id) -> str:
    """Código secuencial por tenant: SUC-01, SUC-02, … (la plaza es una sola
    para todos los clientes, así que el consecutivo también)."""
    existing = {
        c
        for (c,) in db.query(Sucursal.codigo)
        .filter(Sucursal.tenant_id == tenant_id, Sucursal.deleted_at.is_(None))
        .all()
        if c
    }
    n = len(existing) + 1
    while f"SUC-{n:02d}" in existing:
        n += 1
    return f"SUC-{n:02d}"


def _sync_series_vinculo(db, ctx, vinc: ClienteSucursal, series_factura_ids, series_remision_ids):
    """Persiste el ABANICO de series del vínculo y acomoda las defaults.

    La primera de cada tipo se vuelve la default del vínculo si no hay una; si
    la default vigente quedó FUERA del abanico, se reemplaza por la primera
    (una default que la relación ya no ofrece cobraría con la serie equivocada).
    """
    deseadas = []
    for sid in list(series_factura_ids or []) + list(series_remision_ids or []):
        s = db.query(Serie).filter(Serie.id == sid, Serie.tenant_id == ctx.tenant_id).one_or_none()
        if s is None:
            raise HTTPException(status_code=422, detail=f"La serie {sid} no existe")
        deseadas.append(s)
    db.query(ClienteSucursalSerie).filter(
        ClienteSucursalSerie.cliente_sucursal_id == vinc.id
    ).delete()
    for s in deseadas:
        db.add(ClienteSucursalSerie(tenant_id=ctx.tenant_id, cliente_sucursal_id=vinc.id, serie_id=s.id))
    fact = [s.id for s in deseadas if s.tipo_documento == "FACTURA"]
    rem = [s.id for s in deseadas if s.tipo_documento == "REMISION"]
    if fact and (vinc.serie_factura_id is None or vinc.serie_factura_id not in fact):
        vinc.serie_factura_id = fact[0]
    if rem and (vinc.serie_remision_id is None or vinc.serie_remision_id not in rem):
        vinc.serie_remision_id = rem[0]
    if not fact and vinc.serie_factura_id:
        # sin abanico de facturas: la default explícita (si vino) también cuenta
        db.add(ClienteSucursalSerie(tenant_id=ctx.tenant_id, cliente_sucursal_id=vinc.id, serie_id=vinc.serie_factura_id))
    if not rem and vinc.serie_remision_id:
        db.add(ClienteSucursalSerie(tenant_id=ctx.tenant_id, cliente_sucursal_id=vinc.id, serie_id=vinc.serie_remision_id))


def _abanicos(db, vinculo_ids) -> dict:
    """{vinculo_id: ([facturas], [remisiones])} en dos consultas."""
    out: dict = {}
    if not vinculo_ids:
        return out
    tipos = dict(db.query(Serie.id, Serie.tipo_documento).all())
    for css in db.query(ClienteSucursalSerie).filter(
        ClienteSucursalSerie.cliente_sucursal_id.in_(vinculo_ids)
    ):
        fact, rem = out.setdefault(css.cliente_sucursal_id, ([], []))
        (fact if tipos.get(css.serie_id) == "FACTURA" else rem).append(css.serie_id)
    return out


def _hidratar(db, rows, *, cliente_id: Optional[UUID] = None, ctx=None):
    """Cuelga a cada plaza sus clientes vinculados y, si la lista se pidió PARA
    un cliente, las series del vínculo con ese cliente (compatibilidad con los
    selectores de emisión). Un puñado de consultas, no una por renglón.

    Con candado por cliente solo se enumeran los clientes DENTRO del candado: la
    plaza es compartida, y decir quién más se surte de ella le revelaría a un
    usuario de portal la cartera del negocio."""
    filas = list(rows)
    ids = [r.id for r in filas]
    if not ids:
        return
    q = db.query(ClienteSucursal).filter(ClienteSucursal.sucursal_id.in_(ids))
    if ctx is not None and ctx.cliente_scope:
        q = q.filter(ClienteSucursal.cliente_id.in_(ctx.cliente_scope))
    vincs = q.all()
    nombres = dict(
        db.query(Cliente.id, Cliente.legal_name)
        .filter(Cliente.id.in_({v.cliente_id for v in vincs} or {None}))
        .all()
    )
    por_suc: dict = {}
    for v in vincs:
        por_suc.setdefault(v.sucursal_id, []).append(v)
    del_cliente = {v.sucursal_id: v for v in vincs if cliente_id and v.cliente_id == cliente_id}
    abanicos = _abanicos(db, [v.id for v in del_cliente.values()])
    for r in filas:
        suyos = por_suc.get(r.id, [])
        r.clientes_ids = [v.cliente_id for v in suyos]
        r.clientes_nombres = sorted(
            n for n in (nombres.get(v.cliente_id) for v in suyos) if n
        )
        v = del_cliente.get(r.id)
        r.serie_factura_id = v.serie_factura_id if v else None
        r.serie_remision_id = v.serie_remision_id if v else None
        r.es_default = bool(v.es_default) if v else False
        fact, rem = abanicos.get(v.id, ([], [])) if v else ([], [])
        r.series_factura_ids = fact
        r.series_remision_ids = rem


def _vinculo_out(db, vincs) -> list[ClienteSucursalOut]:
    nombres = dict(
        db.query(Cliente.id, Cliente.legal_name)
        .filter(Cliente.id.in_({v.cliente_id for v in vincs} or {None}))
        .all()
    )
    abanicos = _abanicos(db, [v.id for v in vincs])
    out = []
    for v in vincs:
        fact, rem = abanicos.get(v.id, ([], []))
        out.append(
            ClienteSucursalOut(
                id=v.id,
                cliente_id=v.cliente_id,
                sucursal_id=v.sucursal_id,
                cliente_nombre=nombres.get(v.cliente_id),
                serie_factura_id=v.serie_factura_id,
                serie_remision_id=v.serie_remision_id,
                series_factura_ids=fact,
                series_remision_ids=rem,
                es_default=bool(v.es_default),
            )
        )
    return out


def _solo_sin_candado(ctx, verbo: str) -> None:
    """La PLAZA es infraestructura del negocio: la comparten varios clientes, así
    que editarla o borrarla afecta a todos. Un usuario acotado a sus clientes
    (portal) administra sus VÍNCULOS, no la plaza."""
    if ctx.cliente_scope:
        raise HTTPException(
            status_code=403,
            detail=f"Tu usuario no puede {verbo} una sucursal: la comparten varios clientes",
        )


def _visible(db, ctx, sucursal_id: UUID) -> bool:
    """Con candado por cliente, una plaza se ve solo si surte a alguno suyo."""
    if not ctx.cliente_scope:
        return True
    return (
        db.query(ClienteSucursal.id)
        .filter(
            ClienteSucursal.sucursal_id == sucursal_id,
            ClienteSucursal.cliente_id.in_(ctx.cliente_scope),
        )
        .first()
        is not None
    )


@router.get("", response_model=Page[SucursalOut])
def list_sucursales(
    cliente_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    query = db.query(Sucursal).filter(Sucursal.deleted_at.is_(None))
    if ctx.cliente_scope:
        query = (
            query.join(ClienteSucursal, ClienteSucursal.sucursal_id == Sucursal.id)
            .filter(ClienteSucursal.cliente_id.in_(ctx.cliente_scope))
            .distinct()
        )
    if cliente_id is not None:
        if not ctx.cliente_permitido(cliente_id):
            raise HTTPException(status_code=403, detail="Tu usuario no tiene acceso a ese cliente")
        # Las plazas VINCULADAS a ese cliente (join propio: el del candado, si
        # lo hubo, filtra por otros clientes).
        query = query.filter(
            Sucursal.id.in_(
                db.query(ClienteSucursal.sucursal_id).filter(
                    ClienteSucursal.cliente_id == cliente_id
                )
            )
        )
    query = query.order_by(Sucursal.nombre.asc())
    return paginate(
        query, SucursalOut, limit, offset,
        preparar=lambda rows: _hidratar(db, rows, cliente_id=cliente_id, ctx=ctx),
    )


@router.get("/{sucursal_id}", response_model=SucursalOut)
def get_sucursal(
    sucursal_id: UUID,
    cliente_id: Optional[UUID] = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    obj = get_or_404(db, Sucursal, sucursal_id)
    if not _visible(db, ctx, obj.id):
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=403, detail="Tu usuario no tiene acceso a ese cliente")
    _hidratar(db, [obj], cliente_id=cliente_id, ctx=ctx)
    return obj


@router.post("", response_model=SucursalOut, status_code=status.HTTP_201_CREATED)
def create_sucursal(
    payload: SucursalCreate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    data = payload.model_dump(
        exclude={"cliente_id", "serie_factura_id", "serie_remision_id",
                 "series_factura_ids", "series_remision_ids"}
    )
    # El código se autogenera por tenant si no viene dado.
    if not data.get("codigo"):
        data["codigo"] = _generate_codigo(db, ctx.tenant_id)
    obj = Sucursal(**data, tenant_id=ctx.tenant_id)
    db.add(obj)
    db.flush()
    # Conveniencia de alta: crear la plaza ya vinculada a un cliente (es el
    # flujo de la pantalla de clientes) — las series van al vínculo.
    if payload.cliente_id is not None:
        if not ctx.cliente_permitido(payload.cliente_id):
            raise HTTPException(status_code=403, detail="Tu usuario no tiene acceso a ese cliente")
        ensure_fk(db, Cliente, payload.cliente_id, "cliente_id")
        vinc = ClienteSucursal(
            tenant_id=ctx.tenant_id,
            cliente_id=payload.cliente_id,
            sucursal_id=obj.id,
            serie_factura_id=payload.serie_factura_id,
            serie_remision_id=payload.serie_remision_id,
        )
        db.add(vinc)
        db.flush()
        _sync_series_vinculo(db, ctx, vinc, payload.series_factura_ids, payload.series_remision_ids)
        db.flush()
    db.refresh(obj)
    _hidratar(db, [obj], cliente_id=payload.cliente_id, ctx=ctx)
    return obj


@router.patch("/{sucursal_id}", response_model=SucursalOut)
def update_sucursal(
    sucursal_id: UUID,
    payload: SucursalUpdate,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Sucursal, sucursal_id)
    _solo_sin_candado(ctx, "editar")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(obj, key, value)
    db.flush()
    db.refresh(obj)
    _hidratar(db, [obj], ctx=ctx)
    return obj


@router.delete("/{sucursal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sucursal(
    sucursal_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    obj = get_or_404(db, Sucursal, sucursal_id)
    _solo_sin_candado(ctx, "eliminar")
    obj.deleted_at = func.now()
    db.flush()
    return None


# ── Vínculos: qué clientes se surten de la plaza ──


@router.get("/{sucursal_id}/clientes", response_model=list[ClienteSucursalOut])
def list_vinculos(
    sucursal_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    get_or_404(db, Sucursal, sucursal_id)
    if not _visible(db, ctx, sucursal_id):
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")
    vincs = (
        db.query(ClienteSucursal)
        .filter(ClienteSucursal.sucursal_id == sucursal_id)
        .all()
    )
    if ctx.cliente_scope:
        vincs = [v for v in vincs if v.cliente_id in ctx.cliente_scope]
    return _vinculo_out(db, vincs)


@router.put("/{sucursal_id}/clientes/{cliente_id}", response_model=ClienteSucursalOut)
def upsert_vinculo(
    sucursal_id: UUID,
    cliente_id: UUID,
    payload: ClienteSucursalUpsert,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Vincula al cliente con la plaza (o actualiza sus series). Idempotente."""
    get_or_404(db, Sucursal, sucursal_id)
    if not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=403, detail="Tu usuario no tiene acceso a ese cliente")
    ensure_fk(db, Cliente, cliente_id, "cliente_id")
    vinc = (
        db.query(ClienteSucursal)
        .filter(
            ClienteSucursal.cliente_id == cliente_id,
            ClienteSucursal.sucursal_id == sucursal_id,
        )
        .one_or_none()
    )
    if vinc is None:
        vinc = ClienteSucursal(
            tenant_id=ctx.tenant_id, cliente_id=cliente_id, sucursal_id=sucursal_id
        )
        db.add(vinc)
        db.flush()
    data = payload.model_dump(exclude_unset=True)
    series_f = data.pop("series_factura_ids", None)
    series_r = data.pop("series_remision_ids", None)
    # El default es a lo más UNO por cliente (índice parcial): marcar este
    # vínculo desmarca el que lo fuera — antes del flush, o el índice truena.
    if data.get("es_default"):
        db.query(ClienteSucursal).filter(
            ClienteSucursal.cliente_id == cliente_id,
            ClienteSucursal.id != vinc.id,
            ClienteSucursal.es_default.is_(True),
        ).update({"es_default": False})
    for key, value in data.items():
        setattr(vinc, key, value)
    if series_f is not None or series_r is not None:
        actuales_f = [s.serie_id for s in db.query(ClienteSucursalSerie).join(Serie, Serie.id == ClienteSucursalSerie.serie_id)
                      .filter(ClienteSucursalSerie.cliente_sucursal_id == vinc.id, Serie.tipo_documento == "FACTURA")]
        actuales_r = [s.serie_id for s in db.query(ClienteSucursalSerie).join(Serie, Serie.id == ClienteSucursalSerie.serie_id)
                      .filter(ClienteSucursalSerie.cliente_sucursal_id == vinc.id, Serie.tipo_documento == "REMISION")]
        _sync_series_vinculo(db, ctx, vinc,
                             series_f if series_f is not None else actuales_f,
                             series_r if series_r is not None else actuales_r)
    db.flush()
    db.refresh(vinc)
    return _vinculo_out(db, [vinc])[0]


@router.delete("/{sucursal_id}/clientes/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vinculo(
    sucursal_id: UUID,
    cliente_id: UUID,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Desvincula al cliente de la plaza. El histórico (remisiones, órdenes)
    conserva su sucursal_id: esto solo cierra la relación hacia adelante."""
    if not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=403, detail="Tu usuario no tiene acceso a ese cliente")
    vinc = (
        db.query(ClienteSucursal)
        .filter(
            ClienteSucursal.cliente_id == cliente_id,
            ClienteSucursal.sucursal_id == sucursal_id,
        )
        .one_or_none()
    )
    if vinc is None:
        raise HTTPException(status_code=404, detail="Ese cliente no está vinculado a la sucursal")
    db.delete(vinc)
    db.flush()
    return None

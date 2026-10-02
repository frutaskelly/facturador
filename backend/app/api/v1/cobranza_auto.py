"""Cobranza automática — Ajustes generales y la bitácora de envíos.

Cada envío se configura por separado (`cobranza_grupos.py`); aquí queda lo
general: el interruptor maestro de los automáticos, el candado del espejo y
la copia fija a todos, más el historial de lo que salió. Leer pide lo mismo
que ver cobranza; cambiar algo pide `factura:gestionar`, como mandar correos.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, CobranzaEnvio
from ...services import cobranza_auto as svc
from .cobranza import _READ, _WRITE
from .remisiones import _validar_destinatarios

router = APIRouter(prefix="/cobranza/automatica", tags=["cobranza"])


# ─── Ajustes generales ───────────────────────────────────────────────────────

class CobranzaConfigIn(BaseModel):
    activo: bool
    espejo_max_horas: int = Field(ge=0, le=168)
    cc_siempre: list[str] = Field(default_factory=list, max_length=20)


def _config_out(db: Session, ctx: AuthContext, cfg) -> dict:
    ok, ultima = svc.espejo_al_dia(db, ctx.tenant_id, cfg)
    return {
        "activo": cfg.activo, "espejo_max_horas": cfg.espejo_max_horas,
        "cc_siempre": cfg.cc_siempre or [], "zona": cfg.zona,
        "espejo_ok": ok, "espejo_ultima": ultima,
    }


@router.get("/config")
def leer_config(db: Session = Depends(get_tenant_db),
                ctx: AuthContext = Depends(require_permission(_READ))):
    cfg = svc.config_de(db, ctx.tenant_id)
    db.commit()
    return _config_out(db, ctx, cfg)


@router.put("/config")
def guardar_config(payload: CobranzaConfigIn, db: Session = Depends(get_tenant_db),
                   ctx: AuthContext = Depends(require_permission(_WRITE))):
    cfg = svc.config_de(db, ctx.tenant_id)
    cfg.activo = payload.activo
    cfg.espejo_max_horas = payload.espejo_max_horas
    cfg.cc_siempre = _validar_destinatarios([x.strip() for x in payload.cc_siempre if x.strip()])
    db.commit()
    return _config_out(db, ctx, cfg)


# ─── Historial ───────────────────────────────────────────────────────────────

def envio_out(e: CobranzaEnvio, nombres: Optional[dict] = None) -> dict:
    """Una fila de la bitácora. Las viejas (0088) eran por cliente y serie."""
    nombre = e.grupo_nombre
    if not nombre and e.cliente_id:
        nombre = (nombres or {}).get(e.cliente_id, "—") + (f" · {e.serie}" if e.serie else "")
    return {
        "id": str(e.id), "grupo_id": str(e.grupo_id) if e.grupo_id else None, "envio": nombre or "—",
        "corte": e.corte, "estado": e.estado, "origen": e.origen,
        "para": e.para or [], "cc": e.cc or [], "saldo": e.saldo, "vencido": e.vencido,
        "facturas": e.facturas, "dias_max_vencida": e.dias_max_vencida, "escalado": e.escalado,
        "error": e.error, "enviado_at": e.enviado_at, "created_at": e.created_at,
    }


@router.get("/envios")
def historial(
    grupo_id: Optional[UUID] = Query(default=None, description="Solo los de un envío"),
    dias: int = Query(default=120, ge=1, le=730, description="Solo de los últimos N días"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cada correo que salió (o no pudo salir), el más nuevo primero."""
    q = db.query(CobranzaEnvio).filter(
        CobranzaEnvio.tenant_id == ctx.tenant_id,
        CobranzaEnvio.corte >= dt.date.today() - dt.timedelta(days=dias))
    if grupo_id:
        q = q.filter(CobranzaEnvio.grupo_id == grupo_id)
    envios = q.order_by(CobranzaEnvio.created_at.desc()).limit(1000).all()
    if ctx.cliente_scope:
        # Con candado por cliente solo se ven los viejos de sus clientes: los
        # de un envío pueden juntar razones que no ve.
        envios = [e for e in envios if e.cliente_id and ctx.cliente_permitido(e.cliente_id)]
    ids = {e.cliente_id for e in envios if e.cliente_id}
    nombres = dict(db.query(Cliente.id, Cliente.legal_name).filter(Cliente.id.in_(ids or [None])).all())
    return [envio_out(e, nombres) for e in envios]

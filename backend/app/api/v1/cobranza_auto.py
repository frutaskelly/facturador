"""Cobranza automática — configuración, contactos y la cola de envíos.

Ver `services/cobranza_auto.py` para las reglas (candado del espejo, una fila
por contacto y corte, recálculo al enviar). Leer pide lo mismo que ver
cobranza; cambiar algo o mandar correos pide `factura:gestionar`, como el
envío manual del estado de cuenta.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, CobranzaContacto, CobranzaEnvio, Factura
from ...services import cobranza_auto as svc
from .cobranza import _READ, _WRITE
from .remisiones import _validar_destinatarios

router = APIRouter(prefix="/cobranza/automatica", tags=["cobranza"])


# ─── Configuración ───────────────────────────────────────────────────────────

class CobranzaConfigIn(BaseModel):
    activo: bool
    modo: Literal["REVISION", "AUTOMATICO"]
    dia_semana: int = Field(ge=0, le=6)
    hora: int = Field(ge=0, le=23)
    incluir_por_vencer: bool
    saldo_minimo: Decimal = Field(ge=0, le=10_000_000)
    escalar_dias: int = Field(ge=0, le=365)
    escalar_cc: list[str] = Field(default_factory=list, max_length=20)
    cc_siempre: list[str] = Field(default_factory=list, max_length=20)
    adjuntar_pdf: bool
    adjuntar_excel: bool
    espejo_max_horas: int = Field(ge=0, le=168)
    asunto: Optional[str] = Field(default=None, max_length=200)
    mensaje: Optional[str] = Field(default=None, max_length=4000)


def _config_out(db: Session, ctx: AuthContext, cfg) -> dict:
    ok, ultima = svc.espejo_al_dia(db, ctx.tenant_id, cfg)
    return {
        "activo": cfg.activo, "modo": cfg.modo, "dia_semana": cfg.dia_semana, "hora": cfg.hora,
        "zona": cfg.zona, "incluir_por_vencer": cfg.incluir_por_vencer,
        "saldo_minimo": cfg.saldo_minimo, "escalar_dias": cfg.escalar_dias,
        "escalar_cc": cfg.escalar_cc or [], "cc_siempre": cfg.cc_siempre or [],
        "adjuntar_pdf": cfg.adjuntar_pdf, "adjuntar_excel": cfg.adjuntar_excel,
        "espejo_max_horas": cfg.espejo_max_horas, "asunto": cfg.asunto, "mensaje": cfg.mensaje,
        "ultima_generacion": cfg.ultima_generacion,
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
    datos = payload.model_dump()
    datos["escalar_cc"] = _validar_destinatarios(datos["escalar_cc"])
    datos["cc_siempre"] = _validar_destinatarios(datos["cc_siempre"])
    for campo in ("asunto", "mensaje"):
        datos[campo] = (datos[campo] or "").strip() or None
    for k, v in datos.items():
        setattr(cfg, k, v)
    db.commit()
    return _config_out(db, ctx, cfg)


# ─── Contactos ───────────────────────────────────────────────────────────────

class ContactoIn(BaseModel):
    cliente_id: UUID
    serie: Optional[str] = Field(default=None, max_length=10)
    correos: list[str] = Field(default_factory=list, max_length=20)
    cc: list[str] = Field(default_factory=list, max_length=20)
    pausado: bool = False
    motivo_pausa: Optional[str] = Field(default=None, max_length=254)


def _contacto_out(c: CobranzaContacto) -> dict:
    return {"id": str(c.id), "cliente_id": str(c.cliente_id), "serie": c.serie,
            "correos": c.correos or [], "cc": c.cc or [],
            "pausado": c.pausado, "motivo_pausa": c.motivo_pausa}


@router.get("/contactos")
def listar_contactos(db: Session = Depends(get_tenant_db),
                     ctx: AuthContext = Depends(require_permission(_READ))):
    """Cada cliente con saldo PPD (o con contacto capturado), sus series con
    saldo y sus contactos: la pantalla arma de aquí la tabla por cliente."""
    import sqlalchemy as sa

    saldos = (
        db.query(Factura.cliente_id, Factura.serie, sa.func.count(), sa.func.sum(Factura.saldo_insoluto))
        .filter(Factura.tenant_id == ctx.tenant_id, Factura.deleted_at.is_(None),
                Factura.estado == "TIMBRADA", Factura.metodo_pago == "PPD",
                Factura.saldo_insoluto > 0)
        .group_by(Factura.cliente_id, Factura.serie)
        .all()
    )
    contactos = db.query(CobranzaContacto).filter(CobranzaContacto.tenant_id == ctx.tenant_id).all()
    ids = {c for c, *_ in saldos} | {c.cliente_id for c in contactos}
    ids = {i for i in ids if ctx.cliente_permitido(i)}
    nombres = dict(db.query(Cliente.id, Cliente.legal_name).filter(Cliente.id.in_(ids or [None])).all())

    filas: dict[UUID, dict] = {i: {"cliente_id": str(i), "cliente": nombres.get(i, "—"),
                                   "saldo": Decimal("0"), "series": [], "contactos": []} for i in ids}
    for cliente_id, serie, n, saldo in saldos:
        if cliente_id in filas:
            filas[cliente_id]["saldo"] += saldo or 0
            filas[cliente_id]["series"].append({"serie": serie or "", "facturas": n, "saldo": saldo})
    for c in contactos:
        if c.cliente_id in filas:
            filas[c.cliente_id]["contactos"].append(_contacto_out(c))
    return sorted(filas.values(), key=lambda f: f["saldo"], reverse=True)


@router.put("/contactos")
def guardar_contacto(payload: ContactoIn, db: Session = Depends(get_tenant_db),
                     ctx: AuthContext = Depends(require_permission(_WRITE))):
    """Alta o cambio del contacto de (cliente, serie). serie vacía = todas."""
    if not ctx.cliente_permitido(payload.cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    serie = (payload.serie or "").strip().upper() or None
    q = db.query(CobranzaContacto).filter(CobranzaContacto.tenant_id == ctx.tenant_id,
                                          CobranzaContacto.cliente_id == payload.cliente_id)
    q = q.filter(CobranzaContacto.serie == serie) if serie else q.filter(CobranzaContacto.serie.is_(None))
    c = q.one_or_none()
    if c is None:
        c = CobranzaContacto(tenant_id=ctx.tenant_id, cliente_id=payload.cliente_id, serie=serie)
        db.add(c)
    c.correos = _validar_destinatarios([x.strip() for x in payload.correos if x.strip()])
    c.cc = _validar_destinatarios([x.strip() for x in payload.cc if x.strip()])
    c.pausado = payload.pausado
    c.motivo_pausa = (payload.motivo_pausa or "").strip() or None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ese cliente ya tiene contacto para esa serie")
    return _contacto_out(c)


@router.delete("/contactos/{contacto_id}")
def borrar_contacto(contacto_id: UUID, db: Session = Depends(get_tenant_db),
                    ctx: AuthContext = Depends(require_permission(_WRITE))):
    c = db.query(CobranzaContacto).filter(CobranzaContacto.id == contacto_id,
                                          CobranzaContacto.tenant_id == ctx.tenant_id).one_or_none()
    if c is None or not ctx.cliente_permitido(c.cliente_id):
        raise HTTPException(status_code=404, detail="Contacto no encontrado")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ─── Cola y bitácora ─────────────────────────────────────────────────────────

def _envio_out(e: CobranzaEnvio, nombres: dict) -> dict:
    return {
        "id": str(e.id), "cliente_id": str(e.cliente_id), "cliente": nombres.get(e.cliente_id, "—"),
        "serie": e.serie, "corte": e.corte, "estado": e.estado, "origen": e.origen,
        "para": e.para or [], "cc": e.cc or [], "saldo": e.saldo, "vencido": e.vencido,
        "facturas": e.facturas, "dias_max_vencida": e.dias_max_vencida, "escalado": e.escalado,
        "error": e.error, "enviado_at": e.enviado_at, "created_at": e.created_at,
    }


def _nombres(db: Session, envios) -> dict:
    ids = {e.cliente_id for e in envios}
    return dict(db.query(Cliente.id, Cliente.legal_name).filter(Cliente.id.in_(ids or [None])).all())


@router.get("/envios")
def listar_envios(
    estado: Optional[str] = Query(default=None, description="PENDIENTE, ENVIADO, ERROR…; vacío = todos"),
    cliente_id: Optional[UUID] = Query(default=None),
    dias: int = Query(default=60, ge=1, le=730, description="Solo cortes de los últimos N días"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    q = db.query(CobranzaEnvio).filter(
        CobranzaEnvio.tenant_id == ctx.tenant_id,
        CobranzaEnvio.corte >= dt.date.today() - dt.timedelta(days=dias))
    if estado:
        q = q.filter(CobranzaEnvio.estado.in_([s.strip().upper() for s in estado.split(",")]))
    if cliente_id:
        q = q.filter(CobranzaEnvio.cliente_id == cliente_id)
    if ctx.cliente_scope:
        q = q.filter(CobranzaEnvio.cliente_id.in_(ctx.cliente_scope))
    envios = q.order_by(CobranzaEnvio.corte.desc(), CobranzaEnvio.saldo.desc()).limit(1000).all()
    nombres = _nombres(db, envios)
    return [_envio_out(e, nombres) for e in envios]


@router.post("/generar")
def generar_ahora(db: Session = Depends(get_tenant_db),
                  ctx: AuthContext = Depends(require_permission(_WRITE))):
    """Arma la cola de HOY sin esperar al día programado (no la envía)."""
    r = svc.generar_cola(db, ctx.tenant_id, origen="MANUAL")
    db.commit()
    return r


class IdsIn(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500)


def _propios(db: Session, ctx: AuthContext, ids: list[UUID]) -> list[CobranzaEnvio]:
    envios = db.query(CobranzaEnvio).filter(CobranzaEnvio.tenant_id == ctx.tenant_id,
                                            CobranzaEnvio.id.in_(ids)).all()
    return [e for e in envios if ctx.cliente_permitido(e.cliente_id)]


@router.post("/envios/enviar")
def enviar_envios(payload: IdsIn, db: Session = Depends(get_tenant_db),
                  ctx: AuthContext = Depends(require_permission(_WRITE))):
    """Aprueba y manda. Cada envío se confirma por separado: si el 10 falla, los
    9 que ya salieron quedan registrados como enviados."""
    envios = _propios(db, ctx, payload.ids)
    ids = [e.id for e in envios]
    resultado = []
    for i in ids:
        e = svc.enviar(db, ctx.tenant_id, i, aprobado_por=ctx.user_id)
        db.commit()
        resultado.append(e)
    nombres = _nombres(db, resultado)
    return [_envio_out(e, nombres) for e in resultado]


@router.post("/envios/descartar")
def descartar_envios(payload: IdsIn, db: Session = Depends(get_tenant_db),
                     ctx: AuthContext = Depends(require_permission(_WRITE))):
    envios = [e for e in _propios(db, ctx, payload.ids) if e.estado in ("PENDIENTE", "ERROR")]
    for e in envios:
        e.estado = "DESCARTADO"
        e.error = "Descartado a mano."
        e.aprobado_por = ctx.user_id
    db.commit()
    nombres = _nombres(db, envios)
    return [_envio_out(e, nombres) for e in envios]

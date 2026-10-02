"""Cobranza: mandar el estado de cuenta de cada envío y llevar la bitácora.

Cada envío (`cobranza_grupos`) se configura por separado: MANUAL sale solo
con el botón Enviar; AUTOMATICO, además, sale solo el día y la hora que diga
(si el interruptor maestro de Ajustes generales está encendido). Los dos
caminos pasan por `enviar_grupo`. Cómo se arma el correo: `cobranza_grupos`.

Reglas que no se rompen:

1. **No se cobra con el espejo viejo.** Los pagos (REP) llegan de SAE por el
   espejo; si su última pasada buena es más vieja que `espejo_max_horas`, no
   sale nada. El botón lo dice; el automático deja el día PENDIENTE y lo
   reintenta en la siguiente vuelta.
2. **Un programado por envío y día.** El índice parcial de `cobranza_envios`
   lo garantiza en la base, no en el código: dos vueltas del reloj no
   mandan dos correos.
3. **Se arma al momento de mandar.** El correo lleva el saldo de ese instante.
   El automático no manda bajo el saldo mínimo del envío; el botón sí (quien
   lo presiona ya lo vio en la vista previa).
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models import CobranzaConfig, CobranzaEnvio, CobranzaGrupo, EspejoSync, Tenant
from . import cobranza_grupos as grupos

log = logging.getLogger(__name__)
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def contexto_cobranza(tenant_id) -> AuthContext:
    """El reloj no es una persona: nada que firme, candado por cliente vacío."""
    return AuthContext(
        user_id=None, auth_user_id="sistema:cobranza", email=None,
        tenant_id=tenant_id, role_id=None, role_name="sistema",
        is_owner=False, permissions=set(),
    )


def config_de(db: Session, tenant_id) -> CobranzaConfig:
    """La config del inquilino; si no existe se crea APAGADA con los defaults."""
    cfg = db.query(CobranzaConfig).filter(CobranzaConfig.tenant_id == tenant_id).one_or_none()
    if cfg is None:
        cfg = CobranzaConfig(tenant_id=tenant_id)
        db.add(cfg)
        db.flush()
        db.refresh(cfg)
    return cfg


def zona(cfg: CobranzaConfig) -> ZoneInfo:
    return ZoneInfo(cfg.zona or "America/Mexico_City")


def hoy_local(cfg: CobranzaConfig) -> dt.date:
    return dt.datetime.now(zona(cfg)).date()


# ─── Candado del espejo ──────────────────────────────────────────────────────

def espejo_al_dia(db: Session, tenant_id, cfg: CobranzaConfig) -> tuple[bool, Optional[dt.datetime]]:
    """(¿se puede cobrar?, última pasada buena). Sin candado si max_horas = 0."""
    ultima = (
        db.query(sa.func.max(EspejoSync.terminada_at))
        .filter(EspejoSync.tenant_id == tenant_id, EspejoSync.estado == "OK")
        .scalar()
    )
    if not cfg.espejo_max_horas:
        return True, ultima
    if ultima is None:
        return False, None
    edad = dt.datetime.now(dt.timezone.utc) - ultima
    return edad <= dt.timedelta(hours=cfg.espejo_max_horas), ultima


# ─── Mandar un envío ─────────────────────────────────────────────────────────

class NoSeEnvia(Exception):
    """El botón Enviar no puede mandar (y no deja rastro en la bitácora)."""


def _impedimento(db: Session, tenant, cfg: CobranzaConfig, d: grupos.Definicion, datos: dict,
                 para: list[str], origen: str) -> tuple[Optional[str], Optional[str]]:
    """(motivo, estado con que queda en la bitácora) o (None, None) si sale."""
    from . import email as email_service

    ok, ultima = espejo_al_dia(db, tenant.id, cfg)
    if not ok:
        cuando = f"{ultima:%d/%m %H:%M} UTC" if ultima else "nunca"
        return (f"El espejo de SAE no está al día (última pasada buena: {cuando}); "
                "no se cobra hasta que sincronice.", "PENDIENTE")
    if not email_service.configured(tenant):
        return "No hay correo configurado (Ajustes › Correo).", "ERROR"
    if not para:
        return "Falta el correo: captura a quién se le manda (Para).", "ERROR"
    if datos["facturas"] == 0:
        return "No hay facturas por cobrar.", "DESCARTADO"
    if origen == "PROGRAMADO" and datos["saldo_total"] < Decimal(d.saldo_minimo or 0):
        return "El saldo quedó bajo el mínimo del envío.", "DESCARTADO"
    return None, None


def _reclamar_programado(db: Session, tenant_id, grupo: CobranzaGrupo, corte: dt.date) -> Optional[CobranzaEnvio]:
    """La fila del día para el automático, reclamada (ENVIANDO). None = ese día
    ya salió, se descartó o falló (un ERROR no se reintenta solo: se ve en la
    pantalla y se manda con el botón)."""
    nueva = db.execute(pg_insert(CobranzaEnvio).values(
        tenant_id=tenant_id, grupo_id=grupo.id, grupo_nombre=grupo.nombre, corte=corte,
        estado="ENVIANDO", origen="PROGRAMADO",
    ).on_conflict_do_nothing()).rowcount
    if not nueva:
        reintento = db.execute(
            sa.update(CobranzaEnvio)
            .where(CobranzaEnvio.tenant_id == tenant_id, CobranzaEnvio.grupo_id == grupo.id,
                   CobranzaEnvio.corte == corte, CobranzaEnvio.origen == "PROGRAMADO",
                   CobranzaEnvio.estado == "PENDIENTE")
            .values(estado="ENVIANDO")
        ).rowcount
        if not reintento:
            return None
    db.flush()
    return db.query(CobranzaEnvio).filter(
        CobranzaEnvio.tenant_id == tenant_id, CobranzaEnvio.grupo_id == grupo.id,
        CobranzaEnvio.corte == corte, CobranzaEnvio.origen == "PROGRAMADO").one()


def enviar_grupo(db: Session, tenant_id, grupo: CobranzaGrupo, *, origen: str = "MANUAL",
                 usuario: Optional[UUID] = None, corte: Optional[dt.date] = None) -> Optional[CobranzaEnvio]:
    """Manda el estado de cuenta de un envío y lo apunta en la bitácora.

    MANUAL (el botón): si algo lo impide lanza `NoSeEnvia` con el motivo y no
    deja fila; si el SMTP falla queda la fila en ERROR. PROGRAMADO (el reloj):
    reclama la fila del día y siempre la deja con su estado y motivo; devuelve
    None si ese día ya estaba resuelto."""
    from ..api.v1.remisiones import _validar_destinatarios
    from . import email as email_service

    cfg = config_de(db, tenant_id)
    corte = corte or hoy_local(cfg)
    envio = None
    if origen == "PROGRAMADO":
        envio = _reclamar_programado(db, tenant_id, grupo, corte)
        if envio is None:
            return None

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one()
    d = grupos.definicion_de(db, grupo)
    datos = grupos.datos_de_envio(grupos.Contexto(db, contexto_cobranza(tenant_id), corte), d)
    para, cc, escalado = grupos.destinatarios(cfg, d, datos["dias_max_vencida"])
    motivo, estado = _impedimento(db, tenant, cfg, d, datos, para, origen)
    if motivo and envio is None:
        raise NoSeEnvia(motivo)
    if envio is None:
        envio = CobranzaEnvio(tenant_id=tenant_id, grupo_id=grupo.id, corte=corte,
                              estado="ENVIANDO", origen=origen)
        db.add(envio)
    envio.grupo_nombre = grupo.nombre
    envio.aprobado_por = usuario
    envio.para, envio.cc, envio.escalado = para, cc, escalado
    envio.saldo = datos["saldo_total"]
    envio.vencido = datos["vencido_total"]
    envio.facturas = datos["facturas"]
    envio.dias_max_vencida = max(datos["dias_max_vencida"], 0)

    def _queda(estado_final: str, error: Optional[str] = None) -> CobranzaEnvio:
        envio.estado = estado_final
        envio.error = error
        if estado_final == "ENVIADO":
            envio.enviado_at = dt.datetime.now(dt.timezone.utc)
        db.flush()
        return envio

    if motivo:
        return _queda(estado, motivo)
    try:
        para_ok = _validar_destinatarios(para)
        cc_ok = _validar_destinatarios(cc)
    except Exception as e:  # noqa: BLE001 — HTTPException con el correo inválido
        return _queda("ERROR", getattr(e, "detail", str(e)))
    try:
        email_service.send_email(
            email_service.smtp_config(tenant), para_ok, grupos.asunto(d, datos), grupos.html_correo(d, datos),
            attachments=grupos.adjuntos(tenant, d, datos), cc=cc_ok or None)
    except Exception as e:  # noqa: BLE001 — el motivo del SMTP se guarda tal cual
        return _queda("ERROR", str(e))
    return _queda("ENVIADO")


# ─── El reloj ────────────────────────────────────────────────────────────────

def toca(grupo: CobranzaGrupo, cfg: CobranzaConfig, ahora: dt.datetime) -> bool:
    """¿Ya es el día y la hora del envío? (la fila del día evita repetir)."""
    local = ahora.astimezone(zona(cfg))
    return local.weekday() == grupo.dia_semana and local.hour >= grupo.hora


def proximo(grupo: CobranzaGrupo, cfg: CobranzaConfig, ahora: dt.datetime, salio_hoy: bool) -> dt.datetime:
    """Cuándo vuelve a salir el automático (hora local)."""
    local = ahora.astimezone(zona(cfg))
    dias = (grupo.dia_semana - local.weekday()) % 7
    if dias == 0 and salio_hoy:
        dias = 7          # hoy ya se resolvió; si no, sigue pendiente para hoy
    fecha = local.date() + dt.timedelta(days=dias)
    return dt.datetime.combine(fecha, dt.time(grupo.hora), tzinfo=zona(cfg))


def cuando_texto(grupo: CobranzaGrupo) -> str:
    return f"{DIAS[grupo.dia_semana].capitalize()} a las {grupo.hora:02d}:00"


def pasada() -> dict[str, Any]:
    """Una vuelta: los envíos AUTOMATICO a los que ya les toca, de los
    inquilinos con el interruptor maestro encendido."""
    from ..core.db import SessionLocal
    from ..core.rbac import tenant_session

    # Sesión privilegiada solo para saber QUIÉN tiene los automáticos
    # encendidos; todo lo demás corre con el RLS del inquilino.
    s = SessionLocal()
    try:
        tenants = [t for (t,) in s.query(CobranzaConfig.tenant_id)
                   .filter(CobranzaConfig.activo.is_(True)).all()]
    finally:
        s.close()

    ahora = dt.datetime.now(dt.timezone.utc)
    resumen: dict[str, Any] = {}
    for tid in tenants:
        try:
            with tenant_session(tid) as db:
                cfg = config_de(db, tid)
                estados = []
                for g in db.query(CobranzaGrupo).filter(CobranzaGrupo.tenant_id == tid,
                                                        CobranzaGrupo.modo == "AUTOMATICO").all():
                    if not toca(g, cfg, ahora):
                        continue
                    try:
                        e = enviar_grupo(db, tid, g, origen="PROGRAMADO")
                        db.commit()
                        if e is not None:
                            estados.append(e.estado)
                    except Exception as exc:  # noqa: BLE001 — un envío no tumba a los demás
                        db.rollback()
                        log.exception("cobranza: falló el envío %s (%s)", g.id, type(exc).__name__)
                if estados:
                    resumen[str(tid)] = {k: estados.count(k) for k in set(estados)}
        except Exception as e:  # noqa: BLE001 — un inquilino no tumba a los demás
            log.exception("cobranza automática: falló el tenant %s (%s)", tid, type(e).__name__)
    return resumen


async def reloj(intervalo: int = 300) -> None:
    """Cada `intervalo` segundos, para siempre (mismo patrón que el espejo)."""
    import asyncio

    while True:
        await asyncio.sleep(max(30, int(intervalo)))
        try:
            r = await asyncio.to_thread(pasada)
            if r:
                log.info("cobranza automática: %s", r)
        except Exception as e:  # noqa: BLE001
            log.exception("cobranza automática: la pasada falló (%s)", type(e).__name__)

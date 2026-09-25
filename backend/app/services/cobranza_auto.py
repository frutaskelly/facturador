"""Cobranza automática: arma la cola semanal, manda cada estado de cuenta y
lleva la bitácora. La configuración vive en `cobranza_config` (ver el modelo).

Reglas que no se rompen:

1. **No se cobra con el espejo viejo.** Los pagos (REP) llegan de SAE por el
   espejo; si su última pasada buena es más vieja que `espejo_max_horas`, no
   sale NADA y el envío se queda en la cola con el motivo.
2. **Una fila por contacto y corte.** El índice único de `cobranza_envios` lo
   garantiza en la base, no en el código: el reloj y el botón pueden coincidir.
3. **Se recalcula al enviar.** La foto de la cola es para revisar; el correo
   lleva el saldo del momento en que sale. Si ya no llega al mínimo, se descarta.
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models import (
    Cliente, CobranzaConfig, CobranzaContacto, CobranzaEnvio, EspejoSync, Factura, Tenant,
)

log = logging.getLogger(__name__)
ZERO = Decimal("0")


def contexto_cobranza(tenant_id) -> AuthContext:
    """El reloj no es una persona: nada que firme, candado por cliente vacío."""
    return AuthContext(
        user_id=None, auth_user_id="sistema:cobranza", email=None,
        tenant_id=tenant_id, role_id=None, role_name="sistema",
        is_owner=False, permissions=set(),
    )


def config_de(db: Session, tenant_id) -> CobranzaConfig:
    """La config del tenant; si no existe se crea APAGADA con los defaults."""
    cfg = db.query(CobranzaConfig).filter(CobranzaConfig.tenant_id == tenant_id).one_or_none()
    if cfg is None:
        cfg = CobranzaConfig(tenant_id=tenant_id)
        db.add(cfg)
        db.flush()
        db.refresh(cfg)
    return cfg


def hoy_local(cfg: CobranzaConfig) -> dt.date:
    return dt.datetime.now(ZoneInfo(cfg.zona or "America/Mexico_City")).date()


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


# ─── Quién recibe qué ────────────────────────────────────────────────────────

def _objetivos(db: Session, tenant_id) -> list[dict]:
    """Un objetivo por contacto con saldo: (cliente, serie o todas, correos).

    Si el cliente no tiene contacto, sale igual con `correos` vacío: la cola lo
    muestra como «sin correo» en vez de saltarlo callado (así se sabe a quién
    falta capturar). `serie` NULL cubre las series que no tienen contacto propio.
    """
    con_saldo = (
        db.query(Factura.cliente_id, Factura.serie)
        .filter(
            Factura.tenant_id == tenant_id,
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            Factura.metodo_pago == "PPD",
            Factura.saldo_insoluto > 0,
        )
        .distinct()
        .all()
    )
    series_por_cliente: dict[UUID, set[str]] = {}
    for cliente_id, serie in con_saldo:
        series_por_cliente.setdefault(cliente_id, set()).add(serie or "")

    contactos: dict[UUID, list[CobranzaContacto]] = {}
    for c in db.query(CobranzaContacto).filter(CobranzaContacto.tenant_id == tenant_id).all():
        contactos.setdefault(c.cliente_id, []).append(c)

    objetivos = []
    for cliente_id, series in series_por_cliente.items():
        propios = contactos.get(cliente_id, [])
        general = next((c for c in propios if not c.serie), None)
        por_serie = [c for c in propios if c.serie]
        if general and general.pausado:
            continue                                  # todo el cliente en pausa
        cubiertas = {c.serie for c in por_serie}
        for c in por_serie:
            if not c.pausado and c.serie in series:
                objetivos.append({"cliente_id": cliente_id, "serie": c.serie,
                                  "excluir": None, "contacto": c})
        restantes = series - cubiertas
        if restantes:
            objetivos.append({"cliente_id": cliente_id, "serie": None,
                              "excluir": cubiertas or None, "contacto": general})
    return objetivos


def _datos(db: Session, tenant_id, cfg: CobranzaConfig, cliente_id, serie, excluir, corte) -> dict:
    """El estado de cuenta tal como sale en el correo (solo vencidas si así se configuró)."""
    from ..api.v1.cobranza import _armar_estado_cuenta

    ctx = contexto_cobranza(tenant_id)
    datos = _armar_estado_cuenta(db, ctx, cliente_id, corte, serie=serie, excluir_series=excluir)
    if not cfg.incluir_por_vencer:
        vencidas = {UUID(d["factura_id"]) for d in datos["facturas"] if d["dias_vencida"] > 0}
        datos = _armar_estado_cuenta(db, ctx, cliente_id, corte, serie=serie,
                                     excluir_series=excluir, solo_facturas=vencidas)
    return datos


def _resumen(datos: dict) -> dict:
    vencido = sum((Decimal(d["saldo_insoluto"]) for d in datos["facturas"] if d["dias_vencida"] > 0), ZERO)
    dias = max((d["dias_vencida"] for d in datos["facturas"]), default=0)
    return {"saldo": Decimal(datos["saldo_total"]), "vencido": vencido,
            "facturas": len(datos["facturas"]), "dias_max_vencida": max(dias, 0)}


def _lista(v) -> list[str]:
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _destinatarios(cfg: CobranzaConfig, contacto: Optional[CobranzaContacto], dias_max: int):
    para = _lista(contacto.correos if contacto else [])
    cc = _lista(cfg.cc_siempre) + _lista(contacto.cc if contacto else [])
    escalado = bool(cfg.escalar_dias) and dias_max >= cfg.escalar_dias and bool(_lista(cfg.escalar_cc))
    if escalado:
        cc += _lista(cfg.escalar_cc)
    # sin repetidos y sin copiar a quien ya va en «para»
    vistos = {p.lower() for p in para}
    cc_limpio = []
    for c in cc:
        if c.lower() not in vistos:
            vistos.add(c.lower())
            cc_limpio.append(c)
    return para, cc_limpio, escalado


# ─── Generar la cola ─────────────────────────────────────────────────────────

def generar_cola(db: Session, tenant_id, *, origen: str = "PROGRAMADO",
                 corte: Optional[dt.date] = None) -> dict:
    """Arma la cola del corte. Idempotente: lo que ya estaba no se duplica."""
    cfg = config_de(db, tenant_id)
    corte = corte or hoy_local(cfg)
    creados = omitidos = 0
    for obj in _objetivos(db, tenant_id):
        datos = _datos(db, tenant_id, cfg, obj["cliente_id"], obj["serie"], obj["excluir"], corte)
        r = _resumen(datos)
        if r["facturas"] == 0 or r["saldo"] < Decimal(cfg.saldo_minimo or 0):
            omitidos += 1
            continue
        para, cc, escalado = _destinatarios(cfg, obj["contacto"], r["dias_max_vencida"])
        valores = dict(para=para, cc=cc, escalado=escalado,
                       error=None if para else "Sin correo de cobranza: captúralo en Contactos.", **r)
        fila = pg_insert(CobranzaEnvio).values(
            tenant_id=tenant_id, cliente_id=obj["cliente_id"], serie=obj["serie"], corte=corte,
            estado="PENDIENTE", origen=origen, **valores,
        ).on_conflict_do_nothing()
        if db.execute(fila).rowcount:
            creados += 1
        else:
            # Ya estaba en la cola: si sigue sin salir, se refresca con los
            # contactos y el saldo de ahora (p. ej. se capturó el correo).
            q = db.query(CobranzaEnvio).filter(
                CobranzaEnvio.tenant_id == tenant_id, CobranzaEnvio.cliente_id == obj["cliente_id"],
                CobranzaEnvio.corte == corte, CobranzaEnvio.estado.in_(("PENDIENTE", "ERROR")))
            q = q.filter(CobranzaEnvio.serie == obj["serie"]) if obj["serie"] else q.filter(CobranzaEnvio.serie.is_(None))
            q.update(valores, synchronize_session=False)
    cfg.ultima_generacion = corte
    db.flush()
    return {"corte": corte, "creados": creados, "omitidos": omitidos}


# ─── Enviar ──────────────────────────────────────────────────────────────────

def _html(cfg: CobranzaConfig, datos: dict) -> str:
    mensaje = (cfg.mensaje or "").strip()
    mensaje_html = "".join(f"<p>{html_mod.escape(p)}</p>" for p in mensaje.split("\n\n") if p.strip())
    n = len(datos["facturas"])
    vencido = sum((Decimal(d["saldo_insoluto"]) for d in datos["facturas"] if d["dias_vencida"] > 0), ZERO)
    serie = f" · serie {html_mod.escape(datos['serie'])}" if datos.get("serie") else ""
    return (
        f"{mensaje_html}"
        f"<p>Adjunto el estado de cuenta de <strong>{html_mod.escape(datos['cliente_nombre'] or '')}</strong>"
        f"{serie} al {datos['corte']:%d/%m/%Y}.</p>"
        f"<p>Saldo total: <strong>${Decimal(datos['saldo_total']):,.2f}</strong>"
        f" en {n} {'factura' if n == 1 else 'facturas'} por cobrar"
        + (f", de los cuales <strong>${vencido:,.2f}</strong> están vencidos" if vencido > 0 else "")
        + ".</p>"
    )


def enviar(db: Session, tenant_id, envio_id, *, aprobado_por=None) -> CobranzaEnvio:
    """Manda UN envío de la cola. Reclama la fila antes (PENDIENTE/ERROR →
    ENVIANDO) para que dos clics o el reloj y un clic no manden dos correos."""
    from ..api.v1.cobranza import _estado_cuenta_pdf, _nombre_estado_cuenta
    from ..api.v1.remisiones import _validar_destinatarios
    from . import email as email_service
    from .estado_cuenta_xlsx import generar as generar_xlsx

    reclamado = db.execute(
        sa.update(CobranzaEnvio)
        .where(CobranzaEnvio.id == envio_id, CobranzaEnvio.tenant_id == tenant_id,
               CobranzaEnvio.estado.in_(("PENDIENTE", "ERROR")))
        .values(estado="ENVIANDO", aprobado_por=aprobado_por)
    ).rowcount
    envio = db.query(CobranzaEnvio).filter(CobranzaEnvio.id == envio_id,
                                           CobranzaEnvio.tenant_id == tenant_id).one()
    if not reclamado:
        return envio          # ya salió, lo está mandando otro, o lo descartaron
    db.flush()

    def _fallo(motivo: str, estado: str = "ERROR") -> CobranzaEnvio:
        envio.estado = estado
        envio.error = motivo
        db.flush()
        return envio

    cfg = config_de(db, tenant_id)
    ok, ultima = espejo_al_dia(db, tenant_id, cfg)
    if not ok:
        cuando = f"{ultima:%d/%m %H:%M} UTC" if ultima else "nunca"
        return _fallo(f"El espejo de SAE no está al día (última pasada buena: {cuando}); "
                      "no se cobra hasta que sincronice.", "PENDIENTE")
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one()
    if not email_service.configured(tenant):
        return _fallo("No hay correo configurado (Ajustes › Correo).")
    # Los contactos de AHORA, no los de cuando se armó la cola: el correo pudo
    # capturarse después. Serie NULL = «el resto» (sin las que tienen contacto propio).
    contactos = db.query(CobranzaContacto).filter(
        CobranzaContacto.tenant_id == tenant_id,
        CobranzaContacto.cliente_id == envio.cliente_id).all()
    contacto = next((c for c in contactos if (c.serie or None) == envio.serie), None)
    if contacto and contacto.pausado:
        return _fallo("El contacto está en pausa.", "DESCARTADO")
    excluir = None
    if envio.serie is None:
        excluir = {c.serie for c in contactos if c.serie} or None
    datos = _datos(db, tenant_id, cfg, envio.cliente_id, envio.serie, excluir, hoy_local(cfg))
    r = _resumen(datos)
    if r["facturas"] == 0 or r["saldo"] < Decimal(cfg.saldo_minimo or 0):
        return _fallo("Ya no tiene saldo por cobrar (o quedó bajo el mínimo).", "DESCARTADO")
    envio.para, envio.cc, envio.escalado = _destinatarios(cfg, contacto, r["dias_max_vencida"])
    if not envio.para:
        return _fallo("Sin correo de cobranza: captúralo en Contactos.")

    try:
        para = _validar_destinatarios(_lista(envio.para))
        cc = _validar_destinatarios(_lista(envio.cc))
    except Exception as e:  # noqa: BLE001 — HTTPException con el correo inválido
        return _fallo(getattr(e, "detail", str(e)))

    nombre = _nombre_estado_cuenta(datos)
    adjuntos = []
    if cfg.adjuntar_pdf:
        adjuntos.append((f"{nombre}.pdf", _estado_cuenta_pdf(tenant, datos), "application/pdf"))
    if cfg.adjuntar_excel:
        cliente = db.query(Cliente).filter(Cliente.id == envio.cliente_id).one()
        adjuntos.append((f"{nombre}.xlsx", generar_xlsx(tenant, cliente, datos),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    asunto = (cfg.asunto or "").strip() or f"Estado de cuenta al {datos['corte']:%d/%m/%Y}"
    try:
        email_service.send_email(email_service.smtp_config(tenant), para, asunto, _html(cfg, datos),
                                 attachments=adjuntos, cc=cc or None)
    except Exception as e:  # noqa: BLE001 — el motivo del SMTP se guarda tal cual
        return _fallo(str(e))

    for k, v in r.items():
        setattr(envio, k, v)
    envio.estado = "ENVIADO"
    envio.error = None
    envio.enviado_at = dt.datetime.now(dt.timezone.utc)
    db.flush()
    return envio


# ─── El reloj ────────────────────────────────────────────────────────────────

def toca_generar(cfg: CobranzaConfig, ahora: dt.datetime) -> bool:
    """¿Ya es el día y la hora, y todavía no se armó la cola de hoy?"""
    local = ahora.astimezone(ZoneInfo(cfg.zona or "America/Mexico_City"))
    return (local.weekday() == cfg.dia_semana and local.hour >= cfg.hora
            and cfg.ultima_generacion != local.date())


def pasada() -> dict[str, Any]:
    """Una vuelta para todos los tenants con la cobranza encendida."""
    from ..core.db import SessionLocal
    from ..core.rbac import tenant_session

    # Sesión privilegiada solo para saber QUIÉN tiene la cobranza encendida;
    # todo lo demás corre con el RLS del tenant.
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
                r: dict[str, Any] = {}
                if toca_generar(cfg, ahora):
                    r["cola"] = generar_cola(db, tid)
                if cfg.modo == "AUTOMATICO":
                    ids = [i for (i,) in db.query(CobranzaEnvio.id).filter(
                        CobranzaEnvio.tenant_id == tid, CobranzaEnvio.estado == "PENDIENTE",
                        CobranzaEnvio.corte >= hoy_local(cfg) - dt.timedelta(days=6)).all()]
                    if ids and espejo_al_dia(db, tid, cfg)[0]:
                        estados = [enviar(db, tid, i).estado for i in ids]
                        r["enviados"] = estados.count("ENVIADO")
                        r["errores"] = estados.count("ERROR")
                if r:
                    resumen[str(tid)] = r
        except Exception as e:  # noqa: BLE001 — un tenant no tumba a los demás
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

"""Formulario de contacto PÚBLICO de las landings (facturador.mx, smartsupply.mx…).

Endpoint sin autenticación: cualquiera puede enviar un mensaje que llega por
correo. Un solo endpoint sirve a VARIOS sitios: el host se deriva del Origin y
decide el alias de destino (`settings.contact_recipient_for`) y el asunto, así
cada landing entrega en su propia dirección de marca. Las barreras
anti-abuso son las mismas que /registro: kill-switch, honeypot, rate limit por
IP (Redis, fail-open) y captcha opcional (Turnstile).

El envío usa el SMTP de PLATAFORMA (settings.CONTACT_SMTP_*), no el SMTP
por-tenant: este formulario no pertenece a ninguna empresa. Si ese SMTP no está
configurado, responde 503 (el front muestra el correo como alternativa).
"""
from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from ...core.config import settings
from ...core.ratelimit import client_ip, hit
from ...schemas.contacto import ContactoIn, ContactoOut
from ...services import email as email_service
from ...services import turnstile

log = logging.getLogger(__name__)

router = APIRouter(prefix="/contacto", tags=["contacto"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _site_host(request: Request) -> str:
    """Dominio de la landing que envía (facturador.mx, smartsupply.mx, ...).

    Se toma del Origin y, si falta, del Referer. Es solo para ROTULAR y elegir el
    alias de destino: los destinos posibles son una lista blanca en settings, así
    que un Origin falsificado no puede redirigir el correo a un tercero.
    """
    raw = request.headers.get("origin") or request.headers.get("referer") or ""
    host = urlparse(raw).hostname or ""
    return host.lower().removeprefix("www.")


def _render_html(payload: ContactoIn, ip: str, sitio: str) -> str:
    """Correo HTML para gerencia. Todo el input del usuario va escapado."""
    esc = html.escape
    nombre = esc(payload.nombre.strip())
    correo = esc((payload.correo or "").strip())
    empresa = esc((payload.empresa or "—").strip() or "—")
    telefono = esc((payload.telefono or "—").strip() or "—")
    mensaje = esc(payload.mensaje.strip()).replace("\n", "<br />")
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;color:#2C3E50;max-width:600px">
  <h2 style="margin:0 0 4px">Nuevo mensaje de contacto</h2>
  <p style="color:#64768a;margin:0 0 20px">Desde el formulario de {esc(sitio)}</p>
  <table style="border-collapse:collapse;width:100%;font-size:14px">
    <tr><td style="padding:6px 0;color:#64768a;width:110px">Nombre</td><td style="padding:6px 0"><b>{nombre}</b></td></tr>
    <tr><td style="padding:6px 0;color:#64768a">Correo</td><td style="padding:6px 0">{f'<a href="mailto:{correo}" style="color:#2C3E50">{correo}</a>' if correo else "—"}</td></tr>
    <tr><td style="padding:6px 0;color:#64768a">Empresa</td><td style="padding:6px 0">{empresa}</td></tr>
    <tr><td style="padding:6px 0;color:#64768a">Teléfono</td><td style="padding:6px 0">{telefono}</td></tr>
  </table>
  <div style="margin-top:16px;padding:16px;background:#f5f8fa;border-radius:12px;border:1px solid #e7ecf1">
    <div style="color:#64768a;font-size:12px;margin-bottom:6px">MENSAJE</div>
    <div style="font-size:14px;line-height:1.6">{mensaje}</div>
  </div>
  <p style="color:#9aa7bf;font-size:12px;margin-top:20px">IP: {esc(ip)} · Responde este correo para contactar directo al prospecto.</p>
</div>"""


@router.post("", response_model=ContactoOut)
def contacto(payload: ContactoIn, request: Request) -> ContactoOut:
    # ─── Anti-abuso (mismo orden que /registro) ────────────────────────────────
    if not settings.CONTACT_ENABLED:
        raise HTTPException(403, "El formulario de contacto está deshabilitado.")
    # Honeypot: un humano nunca llena este campo (oculto en el form).
    if payload.website:
        raise HTTPException(400, "Solicitud inválida.")
    ip = client_ip(request)
    ok, retry = hit(f"contacto:{ip}", settings.CONTACT_RATE_PER_HOUR, 3600)
    if not ok:
        raise HTTPException(
            429,
            "Enviaste varios mensajes seguidos. Intenta más tarde.",
            headers={"Retry-After": str(retry)},
        )
    if not turnstile.verify(payload.turnstile_token, ip):
        raise HTTPException(400, "Verificación anti-bot fallida. Recarga e intenta de nuevo.")

    correo = (payload.correo or "").strip().lower()
    telefono = (payload.telefono or "").strip()
    if correo and not _EMAIL_RE.match(correo):
        raise HTTPException(422, "Correo con formato inválido")
    if not correo and not telefono:
        raise HTTPException(422, "Deja un correo o un teléfono para poder responderte")

    # ─── Envío ─────────────────────────────────────────────────────────────────
    if not settings.contact_smtp_configured():
        # El front lo trata como "no disponible" y muestra el correo directo.
        raise HTTPException(503, "El envío de contacto no está disponible por ahora.")

    sitio = _site_host(request) or "facturador.mx"
    destinatario = settings.contact_recipient_for(sitio)
    subject = f"Contacto {sitio} — {payload.nombre.strip()}"
    if payload.empresa and payload.empresa.strip():
        subject += f" ({payload.empresa.strip()})"

    try:
        email_service.send_email(
            settings.contact_smtp_cfg(),
            to=[destinatario],
            subject=subject,
            html=_render_html(payload, ip, sitio),
            # Solo si dejó correo: si no, no hay a quién responderle desde el correo.
            reply_to=correo or None,
        )
    except Exception as exc:  # noqa: BLE001 — no filtrar el detalle SMTP al público
        log.error("Fallo al enviar correo de contacto: %s", exc)
        raise HTTPException(502, "No se pudo enviar tu mensaje. Intenta de nuevo en unos minutos.")

    return ContactoOut(ok=True)

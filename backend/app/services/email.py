"""Envío de correo por SMTP genérico (Gmail App Password, Outlook, cualquier SMTP).

La configuración del remitente vive en `tenant.config["email"]`, con la forma:

    {
        "host": "smtp.gmail.com",
        "port": 465,
        "username": "ventas@empresa.com",
        "password": "app-password",
        "from_email": "ventas@empresa.com",
        "from_name": "Empresa SA",
        "use_ssl": true
    }

El envío es síncrono (stdlib `smtplib`) — aceptable dentro de un request.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

_TIMEOUT = 20  # segundos


def smtp_config(tenant) -> Optional[dict]:
    """Devuelve la config de correo del tenant o None si no está definida."""
    cfg = (tenant.config or {}).get("email") if tenant else None
    return cfg or None


def configured(tenant) -> bool:
    """True si el tenant tiene SMTP utilizable (host + username + password)."""
    cfg = smtp_config(tenant)
    if not cfg:
        return False
    return bool(cfg.get("host") and cfg.get("username") and cfg.get("password"))


def send_email(
    cfg: dict,
    to: list[str],
    subject: str,
    html: str,
    attachments: Optional[list[tuple[str, bytes, str]]] = None,
    reply_to: Optional[str] = None,
) -> None:
    """Envía un correo HTML a `to` usando la config SMTP `cfg`.

    `attachments` es una lista de (filename, content, mime_type), p. ej.
    [("A1.pdf", pdf_bytes, "application/pdf")] — usado para adjuntar el
    XML/PDF de una factura.

    `reply_to` fija la cabecera Reply-To (p. ej. el correo del prospecto en el
    formulario de contacto, para poder responderle directo).

    Lanza una Exception con un mensaje claro si falla la conexión/autenticación.
    """
    host = (cfg.get("host") or "").strip()
    if not host:
        raise ValueError("Falta el servidor SMTP (host)")
    port = int(cfg.get("port") or 587)
    username = cfg.get("username") or ""
    password = cfg.get("password") or ""
    from_email = cfg.get("from_email") or username
    from_name = cfg.get("from_name")
    use_ssl = bool(cfg.get("use_ssl")) or port == 465

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = ", ".join(to)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(
        "Este mensaje contiene contenido en HTML. Usa un cliente que lo soporte."
    )
    msg.add_alternative(html, subtype="html")
    for filename, content, mime_type in attachments or []:
        maintype, _, subtype = mime_type.partition("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT) as server:
                if username:
                    server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if username:
                    server.login(username, password)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise Exception(
            "Autenticación rechazada por el servidor SMTP. Verifica usuario y "
            f"contraseña (en Gmail usa una Contraseña de aplicación). [{exc}]"
        )
    except smtplib.SMTPException as exc:
        raise Exception(f"Error SMTP al enviar el correo: {exc}")
    except OSError as exc:
        raise Exception(f"No se pudo conectar al servidor SMTP {host}:{port}: {exc}")

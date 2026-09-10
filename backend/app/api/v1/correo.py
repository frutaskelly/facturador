"""Configuración de correo SMTP del tenant (Ajustes › Correo).

El tenant conecta UNA cuenta SMTP remitente (Gmail con Contraseña de aplicación,
Outlook, o cualquier SMTP). La config se guarda en `tenant.config["email"]` — sin
migración de base de datos.

Gated con `membership:gestionar` (perm admin de Ajustes; misma que usuarios/roles),
ya que no existe una permission específica de "tenant/ajustes" en el catálogo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_mod
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ...core.config import settings
from ...core.db import get_db
from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Tenant
from ...schemas.correo import CorreoConfigIn, CorreoConfigOut, CorreoProbarIn
from ...services import email as email_service

router = APIRouter(prefix="/correo", tags=["correo"])

_WRITE = "membership:gestionar"

# ── liga de verificación (ticket 86bbxkzz5) ──────────────────────────────────
# El correo de prueba trae un botón «Verificar correo»: pulsarlo prueba las
# CUATRO cosas de una vez (se envió, llegó, el usuario tiene acceso al buzón,
# y la config sirve) — más confiable que un «prueba OK» en pantalla. El token
# es HMAC del tenant + fecha con el secreto del backend; caduca en 72 h.

_VERIFICACION_MAX_EDAD = 60 * 60 * 72


def _token_verificacion(tenant_id: str) -> str:
    cuerpo = f"{tenant_id}:{int(time.time())}"
    firma = hmac.new(
        settings.SUPABASE_SECRET_KEY.encode(), cuerpo.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{cuerpo}:{firma}".encode()).decode()


def _abrir_token(token: str) -> str | None:
    try:
        cuerpo = base64.urlsafe_b64decode(token.encode()).decode()
        tenant_id, ts, firma = cuerpo.rsplit(":", 2)
        esperada = hmac.new(
            settings.SUPABASE_SECRET_KEY.encode(),
            f"{tenant_id}:{ts}".encode(), hashlib.sha256,
        ).hexdigest()[:32]
        if not hmac.compare_digest(firma, esperada):
            return None
        if time.time() - int(ts) > _VERIFICACION_MAX_EDAD:
            return None
        return tenant_id
    except Exception:  # noqa: BLE001 — un token roto es simplemente inválido
        return None


def _load_tenant(db: Session, tenant_id) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    return tenant


def _masked(cfg: dict | None) -> CorreoConfigOut:
    cfg = cfg or {}
    has_password = bool(cfg.get("password"))
    return CorreoConfigOut(
        host=cfg.get("host", ""),
        port=int(cfg.get("port") or 587),
        username=cfg.get("username", ""),
        from_email=cfg.get("from_email", ""),
        from_name=cfg.get("from_name"),
        use_ssl=bool(cfg.get("use_ssl")),
        configured=bool(cfg.get("host") and cfg.get("username") and cfg.get("password")),
        has_password=has_password,
        verificado_at=cfg.get("verificado_at"),
    )


@router.get("", response_model=CorreoConfigOut)
def get_correo(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    tenant = _load_tenant(db, ctx.tenant_id)
    return _masked(email_service.smtp_config(tenant))


@router.put("", response_model=CorreoConfigOut)
def put_correo(
    payload: CorreoConfigIn,
    request: Request,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    tenant = _load_tenant(db, ctx.tenant_id)
    existing = email_service.smtp_config(tenant) or {}

    # Si no se manda contraseña (o viene vacía) y ya hay una guardada, se conserva.
    password = payload.password
    if not password:
        password = existing.get("password", "")

    new_cfg = {
        "host": payload.host.strip(),
        "port": payload.port,
        "username": payload.username.strip(),
        "password": password,
        "from_email": payload.from_email.strip(),
        "from_name": (payload.from_name or "").strip() or None,
        "use_ssl": payload.use_ssl,
    }
    # Guardar VERIFICA: se prueba el login SMTP antes de persistir, para que
    # nadie se quede con una configuración que no funciona (sin enviar correo).
    if new_cfg["host"] and new_cfg["username"] and new_cfg["password"]:
        try:
            email_service.verificar_conexion(new_cfg)
        except Exception as exc:  # noqa: BLE001 — mensaje accionable al usuario
            raise HTTPException(status_code=422, detail=str(exc))

    # Guardar INVALIDA la verificación anterior: la config cambió.
    new_cfg["verificado_at"] = None
    tenant.config = {**(tenant.config or {}), "email": new_cfg}
    flag_modified(tenant, "config")
    db.flush()

    out = _masked(new_cfg)
    # Prueba AUTOMÁTICA al guardar (ticket 86bbxkzz5): un correo real al propio
    # buzón configurado, con el botón de verificación. Si el envío falla se
    # avisa sin tirar el guardado — el login SMTP ya se validó arriba.
    if out.configured:
        enforce(f"correo-probar:{ctx.tenant_id}", 10, 3600)
        base = (getattr(settings, "PUBLIC_API_URL", "") or "").rstrip("/") or str(request.base_url).rstrip("/")
        liga = f"{base}/api/v1/correo/verificar?t={_token_verificacion(str(ctx.tenant_id))}"
        cuerpo = (
            "<h2 style=\"margin:0 0 8px\">Verifica tu configuración de correo</h2>"
            "<p>Hemos enviado este correo para comprobar que la configuración de tu "
            "cuenta en <strong>Facturador</strong> es correcta.</p>"
            f"<p style=\"margin:20px 0\"><a href=\"{html_mod.escape(liga)}\" "
            "style=\"background:#16a34a;color:#fff;padding:10px 22px;border-radius:8px;"
            "text-decoration:none;font-weight:600\">Verificar correo</a></p>"
            "<p style=\"color:#666;font-size:13px\">Si tú no configuraste esta cuenta, "
            "ignora este mensaje. La liga caduca en 72 horas.</p>"
        )
        try:
            email_service.send_email(
                new_cfg, [new_cfg["from_email"] or new_cfg["username"]],
                "Verifica tu configuración de correo — Facturador", cuerpo,
            )
            out.prueba_enviada = True
        except Exception as exc:  # noqa: BLE001 — aviso, no error: la config ya quedó
            extra = f"La prueba automática no se pudo enviar: {exc}"
            out.aviso = f"{out.aviso} · {extra}" if out.aviso else extra
    # Aviso (no bloquea): en Gmail, enviar desde un ALIAS distinto del usuario
    # solo funciona si el alias está verificado en «Enviar como»; si no, Gmail
    # reescribe el remitente al buzón autenticado sin avisar.
    remitente = new_cfg["from_email"].strip().lower()
    usuario = new_cfg["username"].strip().lower()
    if remitente and usuario and remitente != usuario and "gmail" in new_cfg["host"].lower():
        out.aviso = (
            f"El remitente «{new_cfg['from_email']}» es distinto del usuario "
            f"«{new_cfg['username']}». Para que Gmail respete ese remitente, el "
            "alias debe estar dado de alta y verificado en Gmail → Configuración → "
            "Cuentas → «Enviar como». Si no lo está, tus correos saldrán como "
            f"{new_cfg['username']}."
        )
    return out


@router.post("/probar")
def probar_correo(
    payload: CorreoProbarIn,
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    # Envía un correo REAL por el SMTP del tenant — con tope por hora.
    enforce(f"correo-probar:{ctx.tenant_id}", 10, 3600)
    tenant = _load_tenant(db, ctx.tenant_id)
    cfg = email_service.smtp_config(tenant)
    if not email_service.configured(tenant):
        raise HTTPException(status_code=503, detail="El correo no está configurado")
    to = payload.to.strip()
    if not to:
        raise HTTPException(status_code=422, detail="Indica un destinatario de prueba")
    html = (
        "<p>Esta es una <strong>prueba de conexión</strong> de Facturador.</p>"
        "<p>Si recibes este mensaje, tu cuenta de correo está configurada "
        "correctamente.</p>"
    )
    try:
        # Primero el login (mensaje claro si son las credenciales), luego el envío.
        email_service.verificar_conexion(cfg)
        email_service.send_email(cfg, [to], "Prueba de conexión — Facturador", html)
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True}


_PAGINA = """<!doctype html><meta charset="utf-8"><title>{titulo}</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;font-family:system-ui,sans-serif;background:#fafafa;color:#333">
<div style="text-align:center;max-width:26rem;padding:0 16px">
<div style="font-size:44px">{icono}</div>
<h1 style="font-size:20px;margin:8px 0">{titulo}</h1>
<p style="color:#666">{detalle}</p>
</div>"""


@router.get("/verificar", response_class=HTMLResponse)
def verificar_correo(t: str, db: Session = Depends(get_db)):
    """La liga del correo de prueba. PÚBLICA a propósito: quien la abre está
    leyendo el buzón configurado, y eso ES la prueba. El token firmado (72 h)
    es lo único que autoriza; no hay sesión de la app en el cliente de correo."""
    enforce("correo-verificar", 60, 3600)
    invalida = HTMLResponse(_PAGINA.format(
        icono="⚠️", titulo="Liga inválida o vencida",
        detalle="Vuelve a guardar la configuración en Ajustes → Correo para "
                "recibir una liga nueva.",
    ), status_code=400)
    tenant_id = _abrir_token(t)
    if tenant_id is None:
        return invalida
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one_or_none()
    if tenant is None:
        return invalida
    cfg = dict(((tenant.config or {}).get("email")) or {})
    if not cfg.get("host"):
        return invalida
    cfg["verificado_at"] = datetime.now(timezone.utc).isoformat()
    tenant.config = {**(tenant.config or {}), "email": cfg}
    flag_modified(tenant, "config")
    db.commit()  # get_db no confirma solo; sin esto la marca se pierde
    return HTMLResponse(_PAGINA.format(
        icono="✅", titulo="Correo verificado",
        detalle="Tu cuenta de correo quedó validada: el envío de facturas y "
                "remisiones ya puede usarla. Puedes cerrar esta pestaña.",
    ))

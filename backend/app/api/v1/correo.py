"""Configuración de correo SMTP del tenant (Ajustes › Correo).

El tenant conecta UNA cuenta SMTP remitente (Gmail con Contraseña de aplicación,
Outlook, o cualquier SMTP). La config se guarda en `tenant.config["email"]` — sin
migración de base de datos.

Gated con `membership:gestionar` (perm admin de Ajustes; misma que usuarios/roles),
ya que no existe una permission específica de "tenant/ajustes" en el catálogo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ...core.ratelimit import enforce
from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Tenant
from ...schemas.correo import CorreoConfigIn, CorreoConfigOut, CorreoProbarIn
from ...services import email as email_service

router = APIRouter(prefix="/correo", tags=["correo"])

_WRITE = "membership:gestionar"


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
    tenant.config = {**(tenant.config or {}), "email": new_cfg}
    flag_modified(tenant, "config")
    db.flush()
    return _masked(new_cfg)


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
        "<p>Esta es una <strong>prueba de conexión</strong> de SmartSupply.</p>"
        "<p>Si recibes este mensaje, tu cuenta de correo está configurada "
        "correctamente.</p>"
    )
    try:
        email_service.send_email(cfg, [to], "Prueba de conexión — SmartSupply", html)
    except Exception as exc:  # noqa: BLE001 — superficie del error al cliente
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True}

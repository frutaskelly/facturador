"""Empresa / emisor — datos fiscales del tenant + sellos digitales (CSD).

Edita la información fiscal del tenant que factura (razón social, RFC, régimen,
CP, domicilio) y sube su CSD (.cer + .key) a Facturama para poder timbrar CFDIs
con su propio RFC.

Usa la sesión plana `get_db` (operación de administración sobre la fila del
propio tenant; evita líos con RLS al actualizar `tenants`), scopeada por
`ctx.tenant_id`. Gated con `membership:gestionar` — la misma perm admin de
Ajustes que usa correo.py (no existe una permission específica de tenant).
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ...core.config import settings
from ...core.db import get_db
from ...core.rbac import AuthContext, require_permission
from ...models import Tenant
from ...schemas.empresa import EmpresaOnboardingOut, EmpresaOut, EmpresaUpdate
from ...services.facturama import FacturamaClient, FacturamaError, csd_public_fields
from ...services.onboarding import compute_status, rfc_valido

router = APIRouter(prefix="/empresa", tags=["empresa"])

_WRITE = "membership:gestionar"


def _load_tenant(db: Session, tenant_id) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    return tenant


def _empresa_out(tenant: Tenant) -> EmpresaOut:
    return EmpresaOut(
        legal_name=tenant.legal_name or "",
        rfc=tenant.rfc or "",
        regimen_fiscal_sat=tenant.regimen_fiscal_sat or "",
        domicilio_fiscal_cp=tenant.domicilio_fiscal_cp or "",
        domicilio_fiscal=tenant.domicilio_fiscal or {},
        has_logo=tenant.logo is not None,
    )


# Tipos de imagen aceptados para el logo (los que reportlab/PIL renderiza bien).
_LOGO_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


@router.get("", response_model=EmpresaOut)
def get_empresa(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    tenant = _load_tenant(db, ctx.tenant_id)
    return _empresa_out(tenant)


@router.put("", response_model=EmpresaOut)
def put_empresa(
    payload: EmpresaUpdate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    tenant = _load_tenant(db, ctx.tenant_id)

    rfc = payload.rfc.strip().upper()
    cp = payload.domicilio_fiscal_cp.strip()
    if not rfc:
        raise HTTPException(status_code=422, detail="El RFC es obligatorio")
    # El RFC va en cada CFDI emitido; formato inválido = timbrado rechazado después.
    if not rfc_valido(rfc):
        raise HTTPException(status_code=422, detail="El RFC no tiene un formato válido del SAT")
    if not cp:
        raise HTTPException(status_code=422, detail="El código postal es obligatorio")

    tenant.legal_name = payload.legal_name.strip()
    tenant.rfc = rfc
    tenant.regimen_fiscal_sat = payload.regimen_fiscal_sat.strip()
    tenant.domicilio_fiscal_cp = cp
    # Reasignar el dict para que SQLAlchemy detecte el cambio del JSONB.
    tenant.domicilio_fiscal = dict(payload.domicilio_fiscal or {})
    flag_modified(tenant, "domicilio_fiscal")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una empresa registrada con ese RFC")
    db.refresh(tenant)

    return _empresa_out(tenant)


@router.post("/logo", response_model=EmpresaOut)
def subir_logo(
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Guarda el logo del emisor (PNG/JPG/WebP) para la representación impresa."""
    mime = (logo.content_type or "").lower()
    if mime not in _LOGO_MIMES:
        raise HTTPException(status_code=422, detail="El logo debe ser PNG, JPG o WebP")
    # Leer con tope: sin él, un archivo gigante se carga completo a memoria
    # ANTES de poder rechazarlo.
    data = logo.file.read(_LOGO_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="El archivo está vacío")
    if len(data) > _LOGO_MAX_BYTES:
        raise HTTPException(status_code=422, detail="El logo no debe exceder 2 MB")

    tenant = _load_tenant(db, ctx.tenant_id)
    tenant.logo = data
    tenant.logo_mime = "image/jpeg" if mime == "image/jpg" else mime
    db.commit()
    db.refresh(tenant)
    return _empresa_out(tenant)


@router.get("/logo")
def obtener_logo(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Devuelve el logo del emisor (para previsualizarlo en Ajustes › Empresa)."""
    tenant = _load_tenant(db, ctx.tenant_id)
    if not tenant.logo:
        raise HTTPException(status_code=404, detail="La empresa no tiene logo")
    return Response(content=tenant.logo, media_type=tenant.logo_mime or "image/png",
                    headers={"Cache-Control": "no-store"})


@router.delete("/logo", response_model=EmpresaOut)
def borrar_logo(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    tenant = _load_tenant(db, ctx.tenant_id)
    tenant.logo = None
    tenant.logo_mime = None
    db.commit()
    db.refresh(tenant)
    return _empresa_out(tenant)


@router.post("/csd")
def subir_csd(
    cer: UploadFile = File(...),
    key: UploadFile = File(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    tenant = _load_tenant(db, ctx.tenant_id)
    if not tenant.rfc:
        raise HTTPException(status_code=422, detail="Configura primero el RFC del emisor")

    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")

    # Un .cer/.key real mide unos KB; 1 MB de tope corta archivos equivocados
    # (o abusivos) sin cargarlos completos a memoria.
    _CSD_MAX = 1024 * 1024
    cer_data = cer.file.read(_CSD_MAX + 1)
    key_data = key.file.read(_CSD_MAX + 1)
    if len(cer_data) > _CSD_MAX or len(key_data) > _CSD_MAX:
        raise HTTPException(status_code=422, detail="El .cer/.key no debe exceder 1 MB")
    cer_b64 = base64.b64encode(cer_data).decode()
    key_b64 = base64.b64encode(key_data).decode()
    try:
        return client.subir_csd(tenant.rfc, cer_b64, key_b64, password)
    except FacturamaError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo subir el CSD: {exc}")


@router.get("/csd")
def listar_csd(
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")
    return csd_public_fields(client.listar_csds())


@router.get("/onboarding", response_model=EmpresaOnboardingOut)
def onboarding_status(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Estado de la configuración fiscal del emisor para el wizard de onboarding:
    datos fiscales, RFC válido y CSD cargado → `listo_para_facturar`."""
    tenant = _load_tenant(db, ctx.tenant_id)
    client = FacturamaClient.from_settings(settings)
    status = compute_status(
        client, tenant, multiemisor=bool(getattr(settings, "FACTURAMA_MULTIEMISOR", False))
    )
    status["ambiente"] = client.env_label
    return EmpresaOnboardingOut(**status)

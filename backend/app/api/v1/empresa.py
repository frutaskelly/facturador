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
from ...core.rbac import AuthContext, invalidate_auth_cache, require_permission
from ...models import Membership, Role, Tenant
from ...schemas.empresa import (
    EmpresaHijaIn,
    EmpresaHijaOut,
    EmpresaOnboardingOut,
    EmpresaOut,
    EmpresaUpdate,
)
from ...services.facturama import FacturamaClient, FacturamaError, csd_public_fields
from ...services.onboarding import compute_status, rfc_valido
# Helpers del registro autoservicio (slug único + CP de 5 dígitos): misma
# convención de alta de tenants para las empresas hijas del grupo.
from .registro import _CP_RE, _unique_slug

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


# Tope de empresas por grupo (raíz + hijas): frena el abuso de RFCs basura sin
# estorbar a un grupo real. Subirlo es cambiar una constante.
_MAX_EMPRESAS_GRUPO = 10


@router.post("/hijas", response_model=EmpresaHijaOut, status_code=201)
def crear_empresa_hija(
    payload: EmpresaHijaIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Crea una empresa HIJA del grupo: otro RFC/razón social del mismo dueño,
    como tenant `SUB` colgado de la raíz del grupo. El creador queda como OWNER
    de la nueva (el switcher del Topbar la muestra al instante).

    Usa la sesión plana `get_db` a propósito: la política RLS de `tenants`
    (id = current_tenant_id) impide insertar OTRO tenant desde la sesión
    scopeada. La única barrera aquí es el código: `parent` sale SIEMPRE de
    ctx.tenant_id (jamás del payload) y se exige ser OWNER.
    """
    if not ctx.is_owner:
        raise HTTPException(status_code=403, detail="Solo el dueño (OWNER) puede agregar empresas")

    actual = _load_tenant(db, ctx.tenant_id)
    # Grupo PLANO: la nueva cuelga de la RAÍZ. Si la actual ya es una hija, la
    # nueva es su hermana (no nietas SUB_SUB — jerarquía simple y predecible).
    root_id = actual.parent_tenant_id or actual.id
    # FOR UPDATE sobre la raíz: serializa las altas del grupo. Sin el lock, N
    # POSTs simultáneos leen el mismo conteo y todos pasan el tope (TOCTOU) —
    # el límite solo vive en este check, no hay constraint de BD que lo respalde.
    raiz = (
        db.query(Tenant).filter(Tenant.id == root_id).with_for_update().one_or_none()
    )
    if raiz is None:
        raise HTTPException(status_code=404, detail="Tenant raíz no encontrado")

    en_grupo = (
        db.query(Tenant.id)
        .filter((Tenant.id == root_id) | (Tenant.parent_tenant_id == root_id))
        .count()
    )
    if en_grupo >= _MAX_EMPRESAS_GRUPO:
        raise HTTPException(
            status_code=409,
            detail=f"El grupo ya tiene {en_grupo} empresas (máximo {_MAX_EMPRESAS_GRUPO})",
        )

    legal_name = payload.legal_name.strip()
    rfc = payload.rfc.strip().upper()
    cp = payload.domicilio_fiscal_cp.strip()
    # Re-validar TRAS el strip (min_length de pydantic corre antes): "  " pasaría
    # y dejaría una empresa sin razón social en el switcher y los PDFs.
    if len(legal_name) < 2:
        raise HTTPException(status_code=422, detail="La razón social es obligatoria")
    if not rfc_valido(rfc):
        raise HTTPException(status_code=422, detail="El RFC no tiene un formato válido del SAT")
    if not _CP_RE.match(cp):
        raise HTTPException(status_code=422, detail="El código postal debe tener 5 dígitos")
    if db.query(Tenant.id).filter(Tenant.rfc == rfc).first() is not None:
        raise HTTPException(status_code=409, detail="Ya existe una empresa registrada con ese RFC")

    owner_role = (
        db.query(Role)
        .filter(Role.nombre == "OWNER", Role.es_preset.is_(True), Role.tenant_id.is_(None))
        .one_or_none()
    )
    if owner_role is None:
        raise HTTPException(status_code=503, detail="Falta el rol OWNER preset del sistema")

    hija = Tenant(
        slug=_unique_slug(db, legal_name),
        legal_name=legal_name,
        trade_name=legal_name,
        rfc=rfc,
        regimen_fiscal_sat=payload.regimen_fiscal_sat.strip(),
        domicilio_fiscal_cp=cp,
        tier="SUB",
        parent_tenant_id=root_id,
        status="ACTIVE",
        # El grupo comparte plan/asientos: la hija hereda de la raíz, no abre
        # un trial paralelo.
        plan=raiz.plan,
        trial_ends_at=raiz.trial_ends_at,
        seats_limit=raiz.seats_limit,
    )
    db.add(hija)
    db.flush()
    db.add(
        Membership(
            tenant_id=hija.id,
            user_id=ctx.user_id,
            role_id=owner_role.id,
            acceso_todas_sucursales=True,
            active=True,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Carrera contra los UNIQUE de tenants: puede ser el RFC o el slug (dos
        # altas simultáneas con la misma razón social) — no afirmar cuál.
        raise HTTPException(
            status_code=409,
            detail="No se pudo crear la empresa: el RFC o el nombre ya están registrados; intenta de nuevo",
        )
    # Sin esto, el caché de auth (TTL 30 s) escondería la empresa nueva del
    # switcher — y rechazaría el X-Tenant-Id de la hija — hasta expirar.
    invalidate_auth_cache()

    return EmpresaHijaOut(
        tenant_id=str(hija.id), slug=hija.slug, legal_name=hija.legal_name, rfc=hija.rfc
    )


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
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")
    # La cuenta Facturama es COMPARTIDA entre tenants (un CSD por RFC): sin
    # filtrar, cada empresa vería los sellos de TODAS las demás (leak
    # multi-tenant). Solo los del RFC propio — misma convención que _csd_match.
    tenant = _load_tenant(db, ctx.tenant_id)
    rfc_u = (tenant.rfc or "").strip().upper()
    propios = [
        c for c in (client.listar_csds() or [])
        if isinstance(c, dict)
        and rfc_u
        and str(c.get("Rfc") or c.get("rfc") or "").strip().upper() == rfc_u
    ]
    return csd_public_fields(propios)


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

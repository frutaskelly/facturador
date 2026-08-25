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
from sqlalchemy import text as sa_text
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
from ...services.csd_validador import validar_csd
from ...services.rfc import validar_rfc_local
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
    # Dígito verificador: atrapa typos (dígitos transpuestos, letra final mal).
    if rfc in ("XAXX010101000", "XEXX010101000"):
        raise HTTPException(status_code=422, detail="Los RFC genéricos del SAT no pueden ser el emisor")
    v = validar_rfc_local(rfc)
    if not v["digito_ok"]:
        raise HTTPException(
            status_code=422,
            detail="El RFC no pasa el dígito verificador del SAT — revisa si hay dígitos transpuestos o la última letra",
        )
    if not cp:
        raise HTTPException(status_code=422, detail="El código postal es obligatorio")

    tenant.legal_name = payload.legal_name.strip()
    tenant.rfc = rfc
    tenant.regimen_fiscal_sat = payload.regimen_fiscal_sat.strip()
    tenant.domicilio_fiscal_cp = cp
    # Reasignar el dict para que SQLAlchemy detecte el cambio del JSONB.
    dom = dict(payload.domicilio_fiscal or {})
    # País fijo: el Facturador emite CFDI mexicanos (emisor siempre en México).
    # Se fuerza server-side para que ningún cliente pueda mandar otro valor.
    dom["pais"] = "México"
    tenant.domicilio_fiscal = dom
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


# Un .cer/.key real mide unos KB; 1 MB de tope corta archivos equivocados.
_CSD_MAX = 1024 * 1024


def _leer_csd_files(cer: UploadFile, key: UploadFile) -> tuple[bytes, bytes]:
    cer_data = cer.file.read(_CSD_MAX + 1)
    key_data = key.file.read(_CSD_MAX + 1)
    if len(cer_data) > _CSD_MAX or len(key_data) > _CSD_MAX:
        raise HTTPException(status_code=422, detail="El .cer/.key no debe exceder 1 MB")
    return cer_data, key_data


@router.post("/csd/validar")
def validar_csd_endpoint(
    cer: UploadFile = File(...),
    key: UploadFile = File(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Prueba LOCAL del CSD (sin tocar Facturama): certifica que el .cer es un
    certificado real y vigente del RFC del emisor, que la contraseña abre el
    .key y que la llave corresponde al certificado. La UI pinta ✓/✗ por campo."""
    tenant = _load_tenant(db, ctx.tenant_id)
    cer_data, key_data = _leer_csd_files(cer, key)
    return validar_csd(cer_data, key_data, password, tenant.rfc or "")


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

    cer_data, key_data = _leer_csd_files(cer, key)

    # Compuerta local ANTES de llamar al PAC: solo fallas DEFINITIVAS (contraseña
    # que no abre la llave, llave de otro certificado, RFC ajeno, cert vencido).
    # Si nuestra validación no es concluyente, se deja pasar a Facturama.
    v = validar_csd(cer_data, key_data, password, tenant.rfc or "")
    if v["key_ok"] and not v["password_ok"]:
        raise HTTPException(status_code=422, detail=v["password_detalle"] or "La contraseña no abre la llave privada")
    if v["cer_ok"] and v["key_ok"] and v["password_ok"] and not v["par_ok"] and v["par_detalle"].startswith("La llave"):
        raise HTTPException(status_code=422, detail=v["par_detalle"])
    if v["cer_ok"] and v["rfc_coincide"] is False:
        raise HTTPException(status_code=422, detail=v["cer_detalle"])
    if v["cer_ok"] and v["vigente"] is False:
        raise HTTPException(status_code=422, detail=v["cer_detalle"])

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


def armar_checklist(
    *, fiscal: bool, logo: bool, correo: bool, productos: bool,
    clientes: bool, listas: bool, series: bool, primera_factura: bool,
) -> dict:
    """Checklist de primeros pasos (puro, testeable): el orden ES la guía."""
    pasos = [
        {"id": "fiscal", "titulo": "Configura tu empresa ante el SAT",
         "detalle": "Datos fiscales, RFC verificado y sello digital (CSD).",
         "completo": fiscal, "href": "/ajustes/empresa", "cta": "Configurar"},
        {"id": "logo", "titulo": "Sube el logo de tu empresa",
         "detalle": "Aparece en el PDF de tus facturas y remisiones.",
         "completo": logo, "href": "/ajustes/empresa", "cta": "Subir logo"},
        {"id": "correo", "titulo": "Conecta tu correo de envío",
         "detalle": "Para mandar facturas y remisiones a tus clientes por email.",
         "completo": correo, "href": "/ajustes/correo", "cta": "Conectar"},
        {"id": "productos", "titulo": "Da de alta tus productos",
         "detalle": "Tu catálogo con claves SAT y unidades.",
         "completo": productos, "href": "/productos", "cta": "Agregar"},
        {"id": "clientes", "titulo": "Registra tus clientes",
         "detalle": "Con su RFC y uso de CFDI para poder facturarles.",
         "completo": clientes, "href": "/clientes", "cta": "Registrar"},
        {"id": "listas", "titulo": "Crea tu lista de precios",
         "detalle": "Precios por cliente o generales para cotizar y vender.",
         "completo": listas, "href": "/listas-precios", "cta": "Crear"},
        {"id": "series", "titulo": "Revisa tus series de folios",
         "detalle": "Las series con las que se numeran facturas y remisiones.",
         "completo": series, "href": "/ajustes/series", "cta": "Revisar"},
        {"id": "primera_factura", "titulo": "Timbra tu primera factura",
         "detalle": "El último paso: tu primer CFDI real desde el sistema.",
         "completo": primera_factura, "href": "/facturas", "cta": "Facturar"},
    ]
    completos = sum(1 for p_ in pasos if p_["completo"])
    siguiente = next((p_["id"] for p_ in pasos if not p_["completo"]), None)
    return {"pasos": pasos, "completos": completos, "total": len(pasos),
            "todo_listo": completos == len(pasos), "siguiente": siguiente}


@router.get("/checklist")
def checklist_inicio(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Primeros pasos para el Dashboard: guía tipo checklist de qué falta para
    empezar a trabajar (fiscal + operativo), calculada de datos vivos."""
    tenant = _load_tenant(db, ctx.tenant_id)
    client = FacturamaClient.from_settings(settings)
    st = compute_status(client, tenant, multiemisor=settings.FACTURAMA_MULTIEMISOR)

    def _n(tabla: str, extra: str = "", soft_delete: bool = True) -> int:
        # Whitelist dura: la tabla se interpola en el SQL — solo literales internos.
        assert tabla in {"productos", "clientes", "listas_precios", "series", "facturas"}
        borrado = "and deleted_at is null" if soft_delete else ""
        return db.execute(
            sa_text(f"select count(*) from {tabla} where tenant_id = :t {borrado} {extra}"),
            {"t": tenant.id},
        ).scalar() or 0

    return armar_checklist(
        fiscal=bool(st.get("listo_para_facturar")),
        # exists en SQL: no cargar el blob del logo (hasta 2 MB) solo para saber si hay.
        logo=bool(db.execute(
            sa_text("select logo is not null from tenants where id = :t"), {"t": tenant.id}
        ).scalar()),
        correo=bool(((tenant.config or {}).get("email") or {}).get("host")),
        productos=_n("productos") > 0,
        clientes=_n("clientes") > 0,
        listas=_n("listas_precios") > 0,
        series=_n("series", soft_delete=False) > 0,
        primera_factura=_n("facturas", "and estado = 'TIMBRADA'") > 0,
    )


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

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
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ...core.config import settings
from ...core.db import get_db
from ...core.rbac import (
    AuthContext,
    get_auth_context,
    invalidate_auth_cache,
    require_permission,
)
from ...models import Membership, Role, RolePermission, Serie, Tenant
from ...schemas.empresa import (
    EmpresaColorIn,
    EmpresaColorOut,
    EmpresaGrupoItem,
    EmpresaGrupoOut,
    EmpresaHijaIn,
    EmpresaHijaOut,
    EmpresaOnboardingOut,
    EmpresaOut,
    EmpresaUpdate,
)
from ...services.facturama import FacturamaClient, FacturamaError, csd_public_fields
from ...services.catalogos_default import (
    categoria_sin_categorizar,
    sembrar_esquemas_impuesto,
)
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
    return _guardar_datos_fiscales(db, _load_tenant(db, ctx.tenant_id), payload)


def _guardar_datos_fiscales(db: Session, tenant: Tenant, payload: EmpresaUpdate) -> EmpresaOut:
    """Valida y escribe los datos fiscales del emisor.

    Compartido por la edición de la empresa activa (PUT /empresa) y por la del
    resto del grupo desde la lista (PUT /empresa/{tenant_id}): las reglas del SAT
    son las mismas, sin importar desde dónde se edite.
    """
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


def _asignar_membresias_de_la_hija(db: Session, ctx: AuthContext, root_id, owner_role: Role) -> dict:
    """Quién entra a la empresa recién creada, y con qué rol.

    Si la crea el DUEÑO, él la encabeza. Si la crea un administrador NO se le
    regala OWNER: sería una escalada (OWNER bypassa todos los permisos, y desde
    ahí podría resetear la contraseña —global— de un dueño al que invitara). El
    administrador entra como ADMIN y los dueños del grupo heredan la empresa.
    """
    if ctx.is_owner:
        return {ctx.user_id: owner_role.id}

    duenos = [
        uid
        for (uid,) in db.query(Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .filter(
            Membership.tenant_id == root_id,
            Membership.active.is_(True),
            Role.es_preset.is_(True),
            Role.nombre == "OWNER",
        )
        .all()
    ]
    admin_role = (
        db.query(Role)
        .filter(Role.nombre == "ADMIN", Role.es_preset.is_(True), Role.tenant_id.is_(None))
        .one_or_none()
    )
    # Grupo sin dueño vivo (o sin el preset ADMIN): la empresa nueva quedaría sin
    # quien la administre, así que entonces sí la encabeza el creador.
    if not duenos or admin_role is None:
        return {ctx.user_id: owner_role.id}

    asignaciones = {ctx.user_id: admin_role.id}
    # Los dueños al final: si el creador también es dueño de la raíz, gana OWNER.
    for uid in duenos:
        asignaciones[uid] = owner_role.id
    return asignaciones


@router.post("/hijas", response_model=EmpresaHijaOut, status_code=201)
def crear_empresa_hija(
    payload: EmpresaHijaIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Crea una empresa HIJA del grupo: otro RFC/razón social del mismo dueño,
    como tenant `SUB` colgado de la raíz del grupo (el switcher del Topbar la
    muestra al instante).

    Pueden crearla el dueño y los administradores (`membership:gestionar`), pero
    quién MANDA en la nueva lo decide `_asignar_membresias_de_la_hija`.

    Usa la sesión plana `get_db` a propósito: la política RLS de `tenants`
    (id = current_tenant_id) impide insertar OTRO tenant desde la sesión
    scopeada. La barrera aquí es el código: `parent` sale SIEMPRE de
    ctx.tenant_id, jamás del payload.
    """
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
    for user_id, role_id in _asignar_membresias_de_la_hija(db, ctx, root_id, owner_role).items():
        db.add(
            Membership(
                tenant_id=hija.id,
                user_id=user_id,
                role_id=role_id,
                acceso_todas_sucursales=True,
                active=True,
            )
        )
    # Catálogos base, igual que en el registro autoservicio: la empresa nueva
    # arranca con sus esquemas de impuesto listos (editables).
    sembrar_esquemas_impuesto(db, hija.id)
    # La categoría por defecto: los productos que se den de alta sin elegir una
    # caen aquí, y así se pueden listar y repartir en vez de ser un hueco.
    categoria_sin_categorizar(db, hija.id)
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
    return _csd_validar(_load_tenant(db, ctx.tenant_id), cer, key, password)


def _csd_validar(tenant: Tenant, cer: UploadFile, key: UploadFile, password: str):
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
    return _csd_subir(_load_tenant(db, ctx.tenant_id), cer, key, password)


def _csd_subir(tenant: Tenant, cer: UploadFile, key: UploadFile, password: str):
    """Sube el .cer/.key del RFC de `tenant` a Facturama.

    El sello es por RFC, así que todo cuelga de `tenant.rfc`: subirlo desde la
    lista de empresas o desde dentro de la empresa da exactamente el mismo
    resultado.
    """
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
    return _csd_listar(_load_tenant(db, ctx.tenant_id))


def _csd_listar(tenant: Tenant):
    client = FacturamaClient.from_settings(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="Facturama no está configurado")
    # La cuenta Facturama es COMPARTIDA entre tenants (un CSD por RFC): sin
    # filtrar, cada empresa vería los sellos de TODAS las demás (leak
    # multi-tenant). Solo los del RFC propio — misma convención que _tiene_csd.
    rfc_u = (tenant.rfc or "").strip().upper()
    propios = [
        c for c in (client.listar_csds() or [])
        if isinstance(c, dict)
        and rfc_u
        and str(c.get("Rfc") or c.get("rfc") or "").strip().upper() == rfc_u
    ]
    return csd_public_fields(propios)


# Pasos que el usuario puede dar por buenos a mano: son de REVISIÓN (el sistema
# ya los siembra), así que no hay un dato que se pueda contar como "hecho".
PASOS_MARCABLES = {"esquemas", "categorias"}


def armar_checklist(
    *, fiscal: bool, logo: bool, correo: bool, clientes: bool, series: bool,
    esquemas: bool, categorias: bool, productos: bool, listas: bool,
    remision: bool, primera_factura: bool, marcados: set[str] | None = None,
) -> dict:
    """Checklist de primeros pasos (puro, testeable): el orden ES la guía."""
    marcados = marcados or set()
    pasos = [
        {"id": "fiscal", "titulo": "Configura tu empresa ante el SAT",
         "detalle": "Datos fiscales, RFC verificado y sello digital (CSD).",
         "completo": fiscal, "href": "/ajustes/empresa/configuracion", "cta": "Configurar"},
        {"id": "logo", "titulo": "Sube el logo de tu empresa",
         "detalle": "Aparece en el PDF de tus facturas y remisiones.",
         "completo": logo, "href": "/ajustes/empresa/configuracion", "cta": "Subir logo"},
        {"id": "correo", "titulo": "Conecta tu correo de envío",
         "detalle": "Para mandar facturas y remisiones a tus clientes por email.",
         "completo": correo, "href": "/ajustes/correo", "cta": "Conectar"},
        {"id": "clientes", "titulo": "Registra tus clientes",
         "detalle": "Con su RFC y uso de CFDI para poder facturarles.",
         "completo": clientes, "href": "/clientes", "cta": "Registrar"},
        {"id": "series", "titulo": "Registra tus series de folio",
         "detalle": "Las series con las que se numeran facturas y remisiones.",
         "completo": series, "href": "/ajustes/series", "cta": "Registrar"},
        {"id": "esquemas", "titulo": "Revisa el esquema de impuesto",
         "detalle": "Los 8 esquemas vienen listos: revisa que las tasas sean las tuyas.",
         "completo": esquemas or "esquemas" in marcados,
         "href": "/esquemas-impuesto", "cta": "Revisar"},
        {"id": "categorias", "titulo": "Registra las categorías para tus productos",
         "detalle": "Agrupan tu catálogo; hay una lista sugerida lista para usar.",
         "completo": categorias or "categorias" in marcados,
         "href": "/categorias", "cta": "Registrar"},
        {"id": "productos", "titulo": "Da de alta tus productos",
         "detalle": "Tu catálogo con claves SAT y unidades.",
         "completo": productos, "href": "/productos", "cta": "Agregar"},
        {"id": "listas", "titulo": "Crea tu lista de precios",
         "detalle": "Precios por cliente o generales para cotizar y vender.",
         "completo": listas, "href": "/listas-precios", "cta": "Crear"},
        {"id": "remision", "titulo": "Crea tu primera remisión",
         "detalle": "La entrega que luego se convierte en factura.",
         "completo": remision, "href": "/remisiones", "cta": "Crear"},
        {"id": "primera_factura", "titulo": "Timbra tu primera factura",
         "detalle": "El último paso: tu primer CFDI real desde el sistema.",
         "completo": primera_factura, "href": "/facturas", "cta": "Facturar"},
    ]
    for p_ in pasos:
        # La UI muestra el "marcar como listo" solo en estos, y solo mientras no
        # se hayan cumplido por datos reales.
        p_["marcable"] = p_["id"] in PASOS_MARCABLES
        p_["marcado_manual"] = p_["id"] in marcados
    completos = sum(1 for p_ in pasos if p_["completo"])
    siguiente = next((p_["id"] for p_ in pasos if not p_["completo"]), None)
    return {"pasos": pasos, "completos": completos, "total": len(pasos),
            "todo_listo": completos == len(pasos), "siguiente": siguiente}


class ChecklistMarcaIn(BaseModel):
    paso: str
    completo: bool = True


@router.post("/checklist/marcar")
def marcar_paso_checklist(
    payload: ChecklistMarcaIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission(_WRITE)),
):
    """Marca (o desmarca) a mano un paso de revisión de la guía."""
    if payload.paso not in PASOS_MARCABLES:
        raise HTTPException(status_code=422, detail="Ese paso no se puede marcar a mano")
    tenant = _load_tenant(db, ctx.tenant_id)
    cfg = dict(tenant.config or {})
    marcados = set(cfg.get("checklist_marcados") or [])
    if payload.completo:
        marcados.add(payload.paso)
    else:
        marcados.discard(payload.paso)
    cfg["checklist_marcados"] = sorted(marcados)
    tenant.config = cfg
    flag_modified(tenant, "config")
    db.flush()
    return {"paso": payload.paso, "completo": payload.completo}


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
        assert tabla in {"productos", "clientes", "listas_precios", "series",
                         "facturas", "esquemas_impuesto", "categorias_producto", "remisiones"}
        borrado = "and deleted_at is null" if soft_delete else ""
        return db.execute(
            sa_text(f"select count(*) from {tabla} where tenant_id = :t {borrado} {extra}"),
            {"t": tenant.id},
        ).scalar() or 0

    marcados = set((tenant.config or {}).get("checklist_marcados") or [])
    return armar_checklist(
        marcados=marcados,
        esquemas=_n("esquemas_impuesto") > 0,
        categorias=_n("categorias_producto") > 0,
        remision=_n("remisiones") > 0,
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


# ─────────────────────────────────────────────────────────────────────────────
# Ajustes › Empresas — la lista del grupo y la edición sin cambiarte de empresa
# ─────────────────────────────────────────────────────────────────────────────


# Colores con los que se reconoce cada empresa en la lista y en el switcher.
# Cerrado a propósito: la inicial va en texto BLANCO encima, y los ocho tienen
# contraste suficiente. Un color libre podría dejarla ilegible.
COLORES_EMPRESA = (
    "#2c3e50",  # azul marino (el acento de la marca)
    "#0f7b6c",  # verde azulado
    "#a3431a",  # terracota
    "#6b3fa0",  # morado
    "#1f6feb",  # azul
    "#9b1c4b",  # vino
    "#3f6212",  # olivo
    "#414d58",  # grafito
)


def _root_id(tenant: Tenant):
    """Raíz del grupo al que pertenece `tenant` (el grupo es plano: raíz + hijas)."""
    return tenant.parent_tenant_id or tenant.id


def _tiene_csd(csds: list, rfc: str) -> bool:
    """¿Hay un CSD cargado en Facturama bajo ese RFC? (mismo criterio que onboarding)."""
    rfc_u = (rfc or "").strip().upper()
    if not rfc_u:
        return False
    return any(
        isinstance(c, dict)
        and str(c.get("Rfc") or c.get("rfc") or "").strip().upper() == rfc_u
        for c in csds or []
    )


def _membresias_admin(db: Session, user_id) -> dict:
    """Las empresas del usuario y si puede administrarlas.

    `ctx` solo trae los permisos de la empresa ACTIVA, así que para decidir sobre
    OTRA empresa hay que resolverlo aquí. El criterio es exactamente lo que podría
    hacer si se cambiara a ella con el switcher: ser OWNER, o tener un rol con
    `membership:gestionar`.
    """
    filas = (
        db.query(Membership.tenant_id, Role.id, Role.es_preset, Role.nombre)
        .join(Role, Role.id == Membership.role_id)
        .filter(Membership.user_id == user_id, Membership.active.is_(True))
        .all()
    )
    role_ids = {f[1] for f in filas}
    con_perm: set = set()
    if role_ids:
        con_perm = {
            rid
            for (rid,) in db.query(RolePermission.role_id)
            .filter(
                RolePermission.role_id.in_(role_ids),
                RolePermission.permission_id == _WRITE,
            )
            .all()
        }
    out: dict = {}
    for tenant_id, role_id, es_preset, nombre in filas:
        es_owner = bool(es_preset) and nombre == "OWNER"
        out[tenant_id] = {
            "rol": nombre,
            "owner": es_owner,
            "puede": es_owner or role_id in con_perm,
        }
    return out


@router.get("/grupo", response_model=EmpresaGrupoOut)
def empresas_del_grupo(
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Las empresas del usuario, cada una con lo que le falta para facturar.

    No exige permiso: devuelve exactamente el mismo conjunto que ya ve en el
    switcher del Topbar (sus membresías activas), solo que enriquecido con el
    estado de configuración. La pantalla que lo consume sí está gateada en el menú.
    """
    actual = _load_tenant(db, ctx.tenant_id)
    root_id = _root_id(actual)
    mems = _membresias_admin(db, ctx.user_id)
    ids = list(mems.keys())
    tenants = (
        db.query(Tenant).filter(Tenant.id.in_(ids), Tenant.deleted_at.is_(None)).all()
        if ids
        else []
    )
    tids = [t.id for t in tenants]

    # Tamaño del grupo ACTUAL (raíz + hijas), aunque el usuario no sea miembro de
    # todas: es lo que cuenta contra el tope al agregar otra empresa.
    # MISMO conteo que aplica el tope en crear_empresa_hija (sin filtrar
    # deleted_at): si difirieran, la pantalla diría "9 de 10" y el alta 409.
    grupo_total = (
        db.query(Tenant.id)
        .filter((Tenant.id == root_id) | (Tenant.parent_tenant_id == root_id))
        .count()
    )

    # UN solo listado de CSDs para TODAS las tarjetas: Facturama devuelve los de la
    # cuenta maestra completa, así que basta emparejar por RFC. Pedirlo por empresa
    # sería una llamada de red por tarjeta.
    csds: list = []
    client = FacturamaClient.from_settings(settings)
    if getattr(client, "configured", False):
        try:
            csds = client.listar_csds()
        except Exception:  # noqa: BLE001 — el estado del sello no debe tumbar la lista
            csds = []

    # `logo` está deferred (BYTEA de hasta 2 MB): se consulta como expresión
    # booleana para no traer el blob de cada empresa solo para pintar un chip.
    con_logo = (
        {tid for (tid, tiene) in db.query(Tenant.id, Tenant.logo.isnot(None)).filter(Tenant.id.in_(tids)).all() if tiene}
        if tids
        else set()
    )
    con_series = (
        {tid for (tid,) in db.query(Serie.tenant_id).filter(Serie.tenant_id.in_(tids)).distinct().all()}
        if tids
        else set()
    )

    multiemisor = bool(getattr(settings, "FACTURAMA_MULTIEMISOR", False))
    items = []
    # Principal primero, luego alfabético: el orden no cambia al entrar y salir.
    for t in sorted(tenants, key=lambda x: (x.parent_tenant_id is not None, (x.legal_name or "").lower())):
        rfc = (t.rfc or "").strip().upper()
        datos_ok = (
            bool((t.legal_name or "").strip())
            and rfc_valido(rfc)
            and bool((t.regimen_fiscal_sat or "").strip())
            and len((t.domicilio_fiscal_cp or "").strip()) == 5
        )
        csd_ok = _tiene_csd(csds, rfc)
        m = mems.get(t.id, {})
        items.append(
            EmpresaGrupoItem(
                tenant_id=str(t.id),
                slug=t.slug,
                legal_name=t.legal_name or "",
                trade_name=t.trade_name or "",
                rfc=rfc,
                regimen_fiscal_sat=t.regimen_fiscal_sat or "",
                domicilio_fiscal_cp=t.domicilio_fiscal_cp or "",
                domicilio_fiscal=t.domicilio_fiscal or {},
                color=((t.config or {}).get("color") or None),
                es_principal=t.parent_tenant_id is None,
                es_actual=t.id == ctx.tenant_id,
                en_grupo=(t.id == root_id or t.parent_tenant_id == root_id),
                rol=m.get("rol") or "",
                puede_editar=bool(m.get("puede")),
                datos_fiscales=datos_ok,
                csd=csd_ok,
                logo=t.id in con_logo,
                series=t.id in con_series,
                correo=bool(((t.config or {}).get("email") or {}).get("host")),
                # En single-emisor el CSD lo aporta la cuenta, no cada empresa.
                listo_para_facturar=datos_ok and (csd_ok or not multiemisor),
            )
        )

    return EmpresaGrupoOut(
        empresas=items,
        grupo_total=grupo_total,
        grupo_max=_MAX_EMPRESAS_GRUPO,
        puede_agregar=ctx.has(_WRITE) and grupo_total < _MAX_EMPRESAS_GRUPO,
    )


def _empresa_administrable(db: Session, ctx: AuthContext, tenant_id) -> Tenant:
    """La empresa destino, exigiendo que el usuario pueda administrarla.

    Los endpoints `/{tenant_id}/...` NO usan `require_permission`: ese resuelve
    los permisos de la empresa ACTIVA y aquí el destino es otra. La barrera es la
    membresía en la empresa destino — ni más ni menos de lo que podría hacer si
    se cambiara a ella con el switcher.
    """
    if not _membresias_admin(db, ctx.user_id).get(tenant_id, {}).get("puede"):
        raise HTTPException(status_code=403, detail="No puedes administrar esa empresa")
    tenant = _load_tenant(db, tenant_id)
    if tenant.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")
    return tenant


@router.put("/{tenant_id}", response_model=EmpresaOut)
def put_empresa_por_id(
    tenant_id: UUID,
    payload: EmpresaUpdate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Edita los datos fiscales de CUALQUIER empresa del usuario sin cambiarse a
    ella (Ajustes › Empresas › Editar)."""
    tenant = _empresa_administrable(db, ctx, tenant_id)
    out = _guardar_datos_fiscales(db, tenant, payload)
    # El nombre de la empresa viaja en el contexto cacheado (switcher, Topbar):
    # sin esto, renombrarla seguiría mostrando el nombre viejo hasta 30 s.
    invalidate_auth_cache()
    return out


@router.get("/{tenant_id}/csd")
def listar_csd_por_id(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Sellos cargados de esa empresa (solo los de SU RFC)."""
    return _csd_listar(_empresa_administrable(db, ctx, tenant_id))


@router.post("/{tenant_id}/csd/validar")
def validar_csd_por_id(
    tenant_id: UUID,
    cer: UploadFile = File(...),
    key: UploadFile = File(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Prueba LOCAL del CSD contra el RFC de esa empresa (no toca Facturama)."""
    return _csd_validar(_empresa_administrable(db, ctx, tenant_id), cer, key, password)


@router.post("/{tenant_id}/csd")
def subir_csd_por_id(
    tenant_id: UUID,
    cer: UploadFile = File(...),
    key: UploadFile = File(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Sube el sello de CUALQUIER empresa del usuario desde la lista, sin
    cambiarse a ella (Ajustes › Empresas › Editar › Sello digital)."""
    return _csd_subir(_empresa_administrable(db, ctx, tenant_id), cer, key, password)


@router.put("/{tenant_id}/color", response_model=EmpresaColorOut)
def put_color_empresa(
    tenant_id: UUID,
    payload: EmpresaColorIn,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    """Elige el color de la empresa. `null` la devuelve al automático.

    Vive en `tenants.config` (JSONB) en vez de una columna propia: es una
    preferencia de presentación, no un dato fiscal.
    """
    tenant = _empresa_administrable(db, ctx, tenant_id)
    color = (payload.color or "").strip().lower() or None
    if color is not None and color not in COLORES_EMPRESA:
        raise HTTPException(status_code=422, detail="Ese color no está en el catálogo")

    config = dict(tenant.config or {})
    if color is None:
        config.pop("color", None)
    else:
        config["color"] = color
    tenant.config = config
    flag_modified(tenant, "config")
    db.commit()
    # El switcher del Topbar pinta el color desde /auth/me, que va cacheado.
    invalidate_auth_cache()
    return EmpresaColorOut(color=color)

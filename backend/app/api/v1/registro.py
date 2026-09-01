"""Registro autoservicio (signup PÚBLICO) — alta de una empresa nueva + su dueño.

Endpoint sin autenticación: cualquiera puede crear una empresa. La barrera real
contra el abuso es fiscal: nadie puede TIMBRAR sin subir un CSD válido del SAT
(ver el gate de onboarding en facturas/timbrar), así que una empresa basura no
puede emitir CFDIs.

Crea, en una transacción:
  1. Usuario de Supabase Auth (service-role) → auth_user_id.
  2. Tenant (empresa) con sus datos fiscales.
  3. User local (email + auth_user_id ya ligado → login inmediato).
  4. Membership OWNER (rol preset).

Si el commit falla tras crear el usuario de Auth, se borra ese usuario
(best-effort) para no dejar cuentas huérfanas.

Usa `get_db` (sesión plana, igual que memberships.crear_usuario): el rol de la
conexión escribe en tenants/users/memberships sin scope de RLS.
"""
from __future__ import annotations

import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.db import get_db
from ...core.ratelimit import client_ip, hit
from ...models import Membership, Role, Tenant, User
from ...schemas.registro import RegistroIn, RegistroOut
from ...services import supabase_admin, turnstile
from ...services.catalogos_default import (
    categoria_sin_categorizar,
    sembrar_esquemas_impuesto,
)
from ...services.onboarding import rfc_valido

router = APIRouter(prefix="/registro", tags=["registro"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CP_RE = re.compile(r"^\d{5}$")


def _slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s or "empresa")[:40]


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug = base
    i = 2
    while db.query(Tenant.id).filter(Tenant.slug == slug).first() is not None:
        slug = f"{base[:36]}-{i}"
        i += 1
    return slug


@router.post("", response_model=RegistroOut, status_code=201)
def registro(payload: RegistroIn, request: Request, db: Session = Depends(get_db)):
    # ─── Anti-abuso ───────────────────────────────────────────────────────────
    # Kill-switch del operador.
    if not settings.SIGNUP_ENABLED:
        raise HTTPException(403, "El registro está temporalmente deshabilitado.")
    # Honeypot: un humano nunca llena este campo (está oculto en el form).
    if payload.website:
        raise HTTPException(400, "Solicitud inválida.")
    # Rate limit por IP (Redis; fail-open si no hay Redis).
    ip = client_ip(request)
    ok, retry = hit(f"signup:{ip}", settings.SIGNUP_RATE_PER_HOUR, 3600)
    if not ok:
        raise HTTPException(
            429,
            "Demasiados registros desde tu red. Intenta más tarde.",
            headers={"Retry-After": str(retry)},
        )
    # Captcha (solo si el operador configuró TURNSTILE_SECRET).
    if not turnstile.verify(payload.turnstile_token, ip):
        raise HTTPException(400, "Verificación anti-bot fallida. Recarga e intenta de nuevo.")

    email = payload.owner_email.strip().lower()
    rfc = payload.rfc.strip().upper()
    cp = payload.domicilio_fiscal_cp.strip()
    legal_name = payload.legal_name.strip()

    # ─── Validación de formato ────────────────────────────────────────────────
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "Correo con formato inválido")
    if not rfc_valido(rfc):
        raise HTTPException(422, "RFC con formato inválido")
    if not _CP_RE.match(cp):
        raise HTTPException(422, "El código postal debe tener 5 dígitos")

    # ─── Unicidad (mensajes claros antes de tocar Auth) ───────────────────────
    if db.query(User.id).filter(User.email == email).first() is not None:
        raise HTTPException(409, "Ese correo ya está registrado. Inicia sesión.")
    if db.query(Tenant.id).filter(Tenant.rfc == rfc).first() is not None:
        raise HTTPException(409, "Ese RFC ya está registrado en el sistema.")

    role = (
        db.query(Role)
        .filter(Role.nombre == "OWNER", Role.es_preset.is_(True), Role.tenant_id.is_(None))
        .one_or_none()
    )
    if role is None:
        raise HTTPException(503, "El sistema no está listo para registros (falta el rol OWNER).")

    if not supabase_admin.configured():
        raise HTTPException(503, "El registro no está disponible (Auth no configurado).")

    slug = _unique_slug(db, legal_name)

    # ─── 1) Cuenta de Auth ────────────────────────────────────────────────────
    require_confirm = bool(settings.SIGNUP_REQUIRE_EMAIL_CONFIRM)
    try:
        auth_id = supabase_admin.create_auth_user(
            email, payload.password, payload.owner_name, email_confirm=not require_confirm
        )
    except supabase_admin.SupabaseAdminError as exc:
        # Lo más común: el correo ya existe en Auth (aunque no en nuestra BD).
        detail = str(exc)
        if "registered" in detail.lower() or "already" in detail.lower() or "422" in detail:
            raise HTTPException(409, "Ese correo ya tiene una cuenta. Inicia sesión.")
        raise HTTPException(502, f"No se pudo crear la cuenta: {detail}")

    # ─── 2-4) Empresa + usuario + membresía OWNER (rollback + limpieza) ────────
    try:
        tenant = Tenant(
            slug=slug,
            legal_name=legal_name,
            trade_name=legal_name,
            rfc=rfc,
            regimen_fiscal_sat=payload.regimen_fiscal_sat.strip(),
            domicilio_fiscal_cp=cp,
            tier="PRINCIPAL",
            status="ACTIVE",
            plan="trial",
        )
        db.add(tenant)
        db.flush()

        user = User(email=email, full_name=payload.owner_name, auth_user_id=auth_id)
        db.add(user)
        db.flush()

        db.add(
            Membership(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=role.id,
                acceso_todas_sucursales=True,
                active=True,
            )
        )
        # Catálogos base: sin esquemas de impuesto no se puede dar de alta un
        # producto con sus tasas. Se siembran aquí para no arrancar en vacío.
        sembrar_esquemas_impuesto(db, tenant.id)
        # La categoría por defecto: los productos que se den de alta sin elegir una
        # caen aquí, y así se pueden listar y repartir en vez de ser un hueco.
        categoria_sin_categorizar(db, tenant.id)
        db.commit()
    except Exception:
        db.rollback()
        supabase_admin.delete_auth_user(auth_id)  # best-effort: no dejar huérfanos
        raise HTTPException(409, "No se pudo completar el registro (¿correo o RFC ya en uso?).")

    return RegistroOut(
        tenant_id=tenant.id, slug=slug, email=email,
        email_confirmation_required=require_confirm,
    )

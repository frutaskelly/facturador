"""RBAC — resolve authorization from a verified identity.

This is where v2's security model lives. The flow:

  1. `get_principal` (app/core/auth.py) proves "I am Supabase auth user <sub>"
     by verifying the JWT signature against the project JWKS. Nothing else
     about the request is trusted.
  2. `get_auth_context` takes that identity and resolves, *server-side*:
       - the local `users` row (linked to <sub>, or linked by email on first
         login for operator-provisioned accounts),
       - the user's active memberships,
       - which tenant the request operates in (validated against memberships —
         a client-supplied selector can only *choose among* the tenants the
         user already belongs to; it can never grant access),
       - the effective permission set for that (user, tenant).
  3. Tenant business queries then run on a session scoped with
     `get_tenant_db`: `SET LOCAL ROLE app_user` + the tenant GUC, so Postgres
     RLS enforces isolation even if application code has a bug.

OWNER is a full bypass (all permissions) resolved here in code — it carries no
permission rows in the catalog. There is no SUPER_ADMIN / cross-tenant role.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .auth import Principal, get_principal
from .db import SessionLocal, set_role_tenant
from ..models import Membership, Permission, Role, RolePermission, Tenant, User

logger = logging.getLogger(__name__)

# Preset role that bypasses the permission catalog within its own tenant.
_OWNER_ROLE = "OWNER"


@dataclass
class TenantMembershipView:
    tenant_id: UUID
    slug: str
    name: str
    role_id: UUID
    role_name: str
    # Color elegido en Ajustes › Empresas (None = automático).
    color: Optional[str] = None


@dataclass
class AuthContext:
    """Everything an endpoint needs, all derived from the verified identity."""

    user_id: UUID
    auth_user_id: str
    email: Optional[str]
    tenant_id: UUID
    role_id: UUID
    role_name: str
    is_owner: bool
    permissions: set[str]
    memberships: list[TenantMembershipView] = field(default_factory=list)

    def has(self, permission: str) -> bool:
        return self.is_owner or permission in self.permissions


# ─── User resolution (auth user <sub> → local users row) ─────────────────────
def _resolve_user(db: Session, principal: Principal) -> User:
    user = (
        db.query(User)
        .filter(User.auth_user_id == principal.auth_user_id)
        .one_or_none()
    )
    if user is not None:
        return user

    # First login of an operator-provisioned account: link by email.
    if principal.email:
        by_email = (
            db.query(User)
            .filter(User.email == principal.email.lower())
            .one_or_none()
        )
        if by_email is not None:
            if by_email.auth_user_id and by_email.auth_user_id != principal.auth_user_id:
                # Email already bound to a different auth user — refuse.
                logger.warning(
                    "Email %s ya vinculado a otro auth_user_id", principal.email
                )
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cuenta en conflicto")
            by_email.auth_user_id = principal.auth_user_id
            db.add(by_email)
            db.commit()
            db.refresh(by_email)
            return by_email

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Usuario no provisionado. Contacta al operador de la plataforma.",
    )


# ─── Tenant resolution (validated against memberships) ───────────────────────
def _active_memberships(db: Session, user: User) -> list[Membership]:
    return (
        db.query(Membership)
        .filter(Membership.user_id == user.id, Membership.active.is_(True))
        .order_by(Membership.created_at.asc())
        .all()
    )


def _select_membership(
    memberships: list[Membership], selector: Optional[str]
) -> Membership:
    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario sin tenant asignado",
        )
    if selector:
        # OJO: los textos "Selector de tenant inválido" y "Sin acceso a este
        # tenant" son CONTRATO con el frontend (lib/api.ts los detecta para
        # auto-limpiar una selección de empresa obsoleta). No reformular.
        try:
            wanted = UUID(selector)
        except ValueError:
            raise HTTPException(status_code=400, detail="Selector de tenant inválido")
        for m in memberships:
            if m.tenant_id == wanted:
                return m
        # The selector is NOT trusted: if the user has no membership there,
        # access is denied — we never fall back to the raw value.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sin acceso a este tenant")
    # No selector → deterministic default (oldest active membership).
    return memberships[0]


def _effective_permissions(db: Session, role_id: UUID, is_owner: bool) -> set[str]:
    if is_owner:
        return {pid for (pid,) in db.query(Permission.id).all()}
    rows = (
        db.query(RolePermission.permission_id)
        .filter(RolePermission.role_id == role_id)
        .all()
    )
    return {pid for (pid,) in rows}


def _membership_views(db: Session, memberships: list[Membership]) -> list[TenantMembershipView]:
    if not memberships:
        return []
    tenant_ids = [m.tenant_id for m in memberships]
    role_ids = [m.role_id for m in memberships]
    tenants = {t.id: t for t in db.query(Tenant).filter(Tenant.id.in_(tenant_ids)).all()}
    roles = {r.id: r for r in db.query(Role).filter(Role.id.in_(role_ids)).all()}
    views = []
    for m in memberships:
        t = tenants.get(m.tenant_id)
        r = roles.get(m.role_id)
        if t is None or r is None:
            continue
        views.append(
            TenantMembershipView(
                tenant_id=t.id,
                slug=t.slug,
                name=t.trade_name or t.legal_name,
                role_id=r.id,
                role_name=r.nombre,
                color=((t.config or {}).get("color") or None),
            )
        )
    return views


# Caché de contexto de auth por (usuario, tenant seleccionado). Evita ~5 consultas
# por request a la base (costoso cuando la DB está en la nube). TTL corto: un cambio
# de rol/permiso se refleja en ≤ _AUTH_TTL segundos.
_AUTH_TTL = float(os.environ.get("AUTH_CACHE_TTL", "30"))  # 0 desactiva (tests)
_AUTH_CACHE: dict[tuple[str, str], tuple[float, "AuthContext"]] = {}


def invalidate_auth_cache() -> None:
    """Limpia el caché (úsalo tras cambios de roles/membresías si se requiere efecto inmediato)."""
    _AUTH_CACHE.clear()


def get_auth_context(
    principal: Principal = Depends(get_principal),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
) -> AuthContext:
    """Resolve the full authorization context for the current request.

    Runs on a privileged (RLS-bypassing) session because it must read the
    user's memberships across tenants *before* a tenant is chosen. This is the
    trusted server-side resolution step — it never keys off a client header
    except to *select among* memberships that already exist. Cacheado por token
    durante `_AUTH_TTL` segundos para no repetir las consultas en cada request.
    """
    key = (principal.auth_user_id, x_tenant_id or "")
    now = time.monotonic()
    if _AUTH_TTL > 0:
        cached = _AUTH_CACHE.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

    db = SessionLocal()
    try:
        user = _resolve_user(db, principal)
        memberships = _active_memberships(db, user)
        chosen = _select_membership(memberships, x_tenant_id)

        role = db.query(Role).filter(Role.id == chosen.role_id).one_or_none()
        if role is None:
            raise HTTPException(status_code=500, detail="Membership sin rol válido")
        is_owner = role.es_preset and role.nombre == _OWNER_ROLE

        perms = _effective_permissions(db, chosen.role_id, is_owner)
        views = _membership_views(db, memberships)

        ctx = AuthContext(
            user_id=user.id,
            auth_user_id=principal.auth_user_id,
            email=user.email,
            tenant_id=chosen.tenant_id,
            role_id=role.id,
            role_name=role.nombre,
            is_owner=is_owner,
            permissions=perms,
            memberships=views,
        )
    finally:
        db.close()

    if _AUTH_TTL > 0:
        _AUTH_CACHE[key] = (now + _AUTH_TTL, ctx)
        if len(_AUTH_CACHE) > 512:  # poda simple de expirados
            for k in [k for k, (exp, _) in _AUTH_CACHE.items() if exp <= now]:
                _AUTH_CACHE.pop(k, None)
    return ctx


def require_permission(*needed: str):
    """FastAPI dependency factory: require ALL of `needed`, else 403.

    Usage:
        @router.post("/pedidos/{id}/cobrar")
        def cobrar(ctx: AuthContext = Depends(require_permission("pedido:cobrar"))):
            ...
    """

    def _dep(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if ctx.is_owner:
            return ctx
        missing = [p for p in needed if p not in ctx.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Falta permiso: {', '.join(missing)}",
            )
        return ctx

    return _dep


# ─── Tenant-scoped DB session for business endpoints (Phase 3+) ──────────────
def get_tenant_db(ctx: AuthContext = Depends(get_auth_context)) -> Iterator[Session]:
    """A session where Postgres RLS is in force.

    `SET LOCAL ROLE app_user` drops the connection to the non-superuser role,
    and the tenant GUC is set from the *validated* tenant_id. Every query is
    then constrained to the current tenant by the RLS policies — defense in
    depth behind the application's own checks.
    """
    db = SessionLocal()
    try:
        set_role_tenant(db, ctx.tenant_id)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def tenant_session(tenant_id: UUID | str) -> Iterator[Session]:
    """Same RLS scoping as `get_tenant_db`, for scripts / background tasks."""
    db = SessionLocal()
    try:
        set_role_tenant(db, tenant_id)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

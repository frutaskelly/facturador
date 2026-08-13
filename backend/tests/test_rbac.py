"""get_auth_context resolves tenant/role/permissions from the DB, not headers.

These exercise the trusted server-side resolution: identity in, validated
tenant + effective permissions out. OWNER bypasses; a scoped role gets exactly
its catalog; an unprovisioned identity and an unauthorized tenant selector are
both rejected.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.core.auth import Principal
from app.core.db import SessionLocal
from app.core.rbac import get_auth_context
from app.models import Membership, Role, Tenant, User


def _principal(sub: str, email: str) -> Principal:
    return Principal(auth_user_id=sub, email=email, role="authenticated", claims={"sub": sub})


@pytest.fixture
def seeded(db_engine):
    """Create a tenant + an OWNER user + a CAJERO user. Cleaned up after."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(
            slug=f"rbac-{suffix}",
            legal_name="RBAC Test SA",
            rfc=f"RB{suffix.upper()}X",
            regimen_fiscal_sat="601",
            domicilio_fiscal_cp="44100",
            tier="PRINCIPAL",
            status="ACTIVE",
        )
        db.add(tenant)
        db.flush()
        created["tenants"].append(tenant.id)

        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        cajero_role = db.query(Role).filter(Role.nombre == "CAJERO", Role.es_preset.is_(True)).one()

        owner_sub = f"sub-owner-{suffix}"
        cajero_sub = f"sub-cajero-{suffix}"
        owner = User(email=f"owner-{suffix}@t.test", auth_user_id=owner_sub, full_name="Owner")
        cajero = User(email=f"cajero-{suffix}@t.test", auth_user_id=cajero_sub, full_name="Cajero")
        db.add_all([owner, cajero])
        db.flush()
        created["users"] += [owner.id, cajero.id]

        m1 = Membership(tenant_id=tenant.id, user_id=owner.id, role_id=owner_role.id)
        m2 = Membership(tenant_id=tenant.id, user_id=cajero.id, role_id=cajero_role.id)
        db.add_all([m1, m2])
        db.flush()
        created["memberships"] += [m1.id, m2.id]
        db.commit()

        yield {
            "tenant_id": tenant.id,
            "owner_sub": owner_sub,
            "owner_email": owner.email,
            "cajero_sub": cajero_sub,
            "cajero_email": cajero.email,
        }
    finally:
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit()
        db.close()


def test_owner_gets_all_permissions(seeded):
    ctx = get_auth_context(
        principal=_principal(seeded["owner_sub"], seeded["owner_email"]),
        x_tenant_id=None,
    )
    assert ctx.tenant_id == seeded["tenant_id"]
    assert ctx.is_owner is True
    assert ctx.role_name == "OWNER"
    # Owner bypass → holds catalog permissions it has no explicit rows for.
    assert ctx.has("menu:dashboard")
    assert ctx.has("pedido:surtir")
    assert "menu:dashboard" in ctx.permissions


def test_scoped_role_gets_only_its_catalog(seeded):
    ctx = get_auth_context(
        principal=_principal(seeded["cajero_sub"], seeded["cajero_email"]),
        x_tenant_id=None,
    )
    assert ctx.is_owner is False
    assert ctx.role_name == "CAJERO"
    assert ctx.has("pedido:cobrar") is True
    assert ctx.has("pedido:surtir") is False  # belongs to BODEGUERO
    assert "menu:ajustes.usuarios" not in ctx.permissions


def test_unprovisioned_identity_is_rejected(db_engine):
    with pytest.raises(HTTPException) as exc:
        get_auth_context(
            principal=_principal(f"sub-ghost-{uuid.uuid4().hex}", "ghost@nowhere.test"),
            x_tenant_id=None,
        )
    assert exc.value.status_code == 403


def test_tenant_selector_must_match_a_membership(seeded):
    # Cajero asks to operate in a tenant they have no membership in → 403.
    other_tenant = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        get_auth_context(
            principal=_principal(seeded["cajero_sub"], seeded["cajero_email"]),
            x_tenant_id=other_tenant,
        )
    assert exc.value.status_code == 403


def test_tenant_selector_switches_among_own_memberships(seeded):
    # Usuario multi-empresa (grupo): el selector X-Tenant-Id elige entre SUS
    # membresías — la base del switcher de empresa del Topbar.
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        empresa2 = Tenant(
            slug=f"rbac2-{suffix}", legal_name="RBAC Dos SA", rfc=f"R2{suffix.upper()}X",
            regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
            tier="SUB", parent_tenant_id=seeded["tenant_id"], status="ACTIVE",
        )
        db.add(empresa2); db.flush()
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        cajero_user = db.query(User).filter(User.auth_user_id == seeded["cajero_sub"]).one()
        m = Membership(tenant_id=empresa2.id, user_id=cajero_user.id, role_id=admin_role.id)
        db.add(m); db.commit()
        empresa2_id, m_id = empresa2.id, m.id

        # Selecciona la empresa 2 → contexto de la empresa 2 con SU rol de allá.
        ctx2 = get_auth_context(
            principal=_principal(seeded["cajero_sub"], seeded["cajero_email"]),
            x_tenant_id=str(empresa2_id),
        )
        assert ctx2.tenant_id == empresa2_id
        assert ctx2.role_name == "ADMIN"

        # Selecciona la empresa 1 → contexto de la 1 (rol CAJERO).
        ctx1 = get_auth_context(
            principal=_principal(seeded["cajero_sub"], seeded["cajero_email"]),
            x_tenant_id=str(seeded["tenant_id"]),
        )
        assert ctx1.tenant_id == seeded["tenant_id"]
        assert ctx1.role_name == "CAJERO"
    finally:
        db.query(Membership).filter(Membership.id == m_id).delete()
        db.query(Tenant).filter(Tenant.id == empresa2_id).delete()
        db.commit(); db.close()


# ─── first-login linking by email (operator-provisioned accounts) ────────────
def _provision_unlinked_user(db, email: str):
    """A tenant + an ADMIN user provisioned but never logged in (auth_user_id NULL)."""
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(
        slug=f"link-{suffix}",
        legal_name="Link Test SA",
        rfc=f"LK{suffix.upper()}X",
        regimen_fiscal_sat="601",
        domicilio_fiscal_cp="44100",
        tier="PRINCIPAL",
        status="ACTIVE",
    )
    db.add(tenant)
    db.flush()
    admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
    user = User(email=email, full_name="Sin login", auth_user_id=None)
    db.add(user)
    db.flush()
    m = Membership(tenant_id=tenant.id, user_id=user.id, role_id=admin_role.id)
    db.add(m)
    db.commit()
    return tenant.id, user.id, m.id


def _cleanup(db, tenant_id, user_id, membership_id):
    db.query(Membership).filter(Membership.id == membership_id).delete()
    db.query(User).filter(User.id == user_id).delete()
    db.query(Tenant).filter(Tenant.id == tenant_id).delete()
    db.commit()
    db.close()


def test_first_login_links_account_by_email(db_engine):
    """An operator-provisioned user (no auth_user_id yet) is linked on first
    login by matching the JWT email — case-insensitively on the JWT side."""
    email = f"first-login-{uuid.uuid4().hex[:8]}@t.test"
    db = SessionLocal()
    tenant_id, user_id, membership_id = _provision_unlinked_user(db, email)
    try:
        new_sub = f"sub-firstlogin-{uuid.uuid4().hex}"
        # JWT carries the email upper-cased; resolution must still match.
        ctx = get_auth_context(principal=_principal(new_sub, email.upper()), x_tenant_id=None)
        assert ctx.user_id == user_id
        assert ctx.auth_user_id == new_sub
        assert ctx.tenant_id == tenant_id

        # The link is persisted, so a second login resolves directly by sub.
        db.expire_all()
        assert db.query(User).filter(User.id == user_id).one().auth_user_id == new_sub
    finally:
        _cleanup(db, tenant_id, user_id, membership_id)


def test_email_bound_to_other_auth_user_is_rejected(db_engine):
    """If the email is already linked to a different auth user, a new sub with
    the same email must be refused (account-takeover guard) — not silently
    relinked."""
    email = f"conflict-{uuid.uuid4().hex[:8]}@t.test"
    db = SessionLocal()
    tenant_id, user_id, membership_id = _provision_unlinked_user(db, email)
    try:
        # Bind the account to one auth user.
        db.query(User).filter(User.id == user_id).update({"auth_user_id": "sub-original"})
        db.commit()

        # A different sub presenting the same email → 403 conflict.
        with pytest.raises(HTTPException) as exc:
            get_auth_context(principal=_principal("sub-impostor", email), x_tenant_id=None)
        assert exc.value.status_code == 403

        # The original link is untouched.
        db.expire_all()
        assert db.query(User).filter(User.id == user_id).one().auth_user_id == "sub-original"
    finally:
        _cleanup(db, tenant_id, user_id, membership_id)

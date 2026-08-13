"""Los 3 fixes de seguridad multi-tenant (QA diferido, reactivados al abrir
multi-empresa):

1. GET /empresa/csd no filtra por RFC → un tenant vería los sellos de TODOS.
2. POST /memberships/{id}/password resetea la cuenta GLOBAL de un usuario que
   también pertenece a empresas fuera del grupo.
3. membership:gestionar podía otorgar el rol OWNER (escalada) o tocar/borrar la
   membresía de un OWNER (takeover).
"""
import uuid

import pytest

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Membership, Role, Tenant, User


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"m": [], "u": [], "t": []}
    try:
        def _tenant(s, *, tier="PRINCIPAL", parent=None):
            t = Tenant(slug=f"seg-{s}-{suffix}", legal_name=f"SEG {s} SA",
                       rfc=f"S{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier=tier, parent_tenant_id=parent,
                       status="ACTIVE")
            db.add(t); db.flush(); created["t"].append(t.id); return t

        tenant_a = _tenant("a")
        tenant_sub = _tenant("s", tier="SUB", parent=tenant_a.id)   # mismo grupo
        tenant_b = _tenant("b")                                     # OTRO grupo

        roles = {
            n: db.query(Role).filter(Role.nombre == n, Role.es_preset.is_(True)).one()
            for n in ("OWNER", "ADMIN", "TOMADOR")
        }

        def _user(label, *mems):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["u"].append(u.id)
            out = {"sub": sub, "email": u.email, "memberships": {}}
            for tenant, rol in mems:
                m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=roles[rol].id)
                db.add(m); db.flush(); created["m"].append(m.id)
                out["memberships"][str(tenant.id)] = str(m.id)
            return out

        owner_a = _user("owner", (tenant_a, "OWNER"))
        admin_a = _user("admin", (tenant_a, "ADMIN"))
        tomador_a = _user("tomador", (tenant_a, "TOMADOR"))
        multi_grupo = _user("mgrupo", (tenant_a, "TOMADOR"), (tenant_sub, "TOMADOR"))
        multi_fuera = _user("mfuera", (tenant_a, "TOMADOR"), (tenant_b, "TOMADOR"))
        # Dueño de la HIJA S, pero con rol bajo (TOMADOR) en la raíz A: el objetivo
        # del takeover — un admin de A no debe poder resetear su contraseña global.
        owner_hija = _user("ownerhija", (tenant_a, "TOMADOR"), (tenant_sub, "OWNER"))
        db.commit()

        ta = str(tenant_a.id)
        yield {
            "tenant_a": ta, "rfc_a": tenant_a.rfc,
            "owner_a": {**owner_a, "tenant_id": ta},
            "admin_a": {**admin_a, "tenant_id": ta},
            "m_tomador": tomador_a["memberships"][ta],
            "m_owner": owner_a["memberships"][ta],
            "m_multi_grupo": multi_grupo["memberships"][ta],
            "m_multi_fuera": multi_fuera["memberships"][ta],
            "m_owner_hija": owner_hija["memberships"][ta],   # su membresía TOMADOR en A
            "owner_role_id": str(roles["OWNER"].id),
            "admin_role_id": str(roles["ADMIN"].id),
        }
    finally:
        for mid in created["m"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["u"]:
            db.query(User).filter(User.id == uid).delete()
        # En reversa: la SUB referencia a su raíz por FK (la hija cae primero).
        for tid in reversed(created["t"]):
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit(); db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated",
            claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _h(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


# ── Fix 3: escalada / takeover de OWNER ──────────────────────────────────────
def test_admin_no_otorga_owner(client, env, auth_as):
    auth_as(env["admin_a"]); h = _h(env["admin_a"])
    # Por PATCH a una membresía existente:
    r = client.patch(f"/api/v1/memberships/{env['m_tomador']}", headers=h,
                     json={"role_id": env["owner_role_id"]})
    assert r.status_code == 403 and "OWNER" in r.json()["detail"]
    # Por alta de usuario nuevo (el 403 dispara antes de tocar Auth):
    r2 = client.post("/api/v1/memberships/usuarios", headers=h, json={
        "email": f"nuevo-{uuid.uuid4().hex[:6]}@t.test", "full_name": "X",
        "password": "Secreta123!", "role_id": env["owner_role_id"]})
    assert r2.status_code == 403


def test_admin_no_toca_membresia_de_owner(client, env, auth_as):
    auth_as(env["admin_a"]); h = _h(env["admin_a"])
    assert client.patch(f"/api/v1/memberships/{env['m_owner']}", headers=h,
                        json={"role_id": env["admin_role_id"]}).status_code == 403
    assert client.delete(f"/api/v1/memberships/{env['m_owner']}", headers=h).status_code == 403
    # Ni resetear su contraseña (takeover de la cuenta del dueño):
    assert client.post(f"/api/v1/memberships/{env['m_owner']}/password", headers=h,
                       json={"password": "Nueva123!"}).status_code == 403


def test_owner_si_otorga_owner(client, env, auth_as):
    auth_as(env["owner_a"]); h = _h(env["owner_a"])
    r = client.patch(f"/api/v1/memberships/{env['m_tomador']}", headers=h,
                     json={"role_id": env["owner_role_id"]})
    assert r.status_code == 200, r.text


# ── Fix 2: contraseña = cuenta global ────────────────────────────────────────
@pytest.fixture
def fake_auth_admin(monkeypatch):
    import app.api.v1.memberships as mod
    monkeypatch.setattr(mod.supabase_admin, "configured", lambda: True)
    monkeypatch.setattr(mod.supabase_admin, "set_password", lambda auth_id, pwd: None)


def test_password_fuera_del_grupo_bloqueada(client, env, auth_as, fake_auth_admin):
    auth_as(env["owner_a"]); h = _h(env["owner_a"])
    r = client.post(f"/api/v1/memberships/{env['m_multi_fuera']}/password", headers=h,
                    json={"password": "Nueva123!"})
    assert r.status_code == 409 and "fuera de tu grupo" in r.json()["detail"]


def test_password_mismo_grupo_ok(client, env, auth_as, fake_auth_admin):
    auth_as(env["owner_a"]); h = _h(env["owner_a"])
    # Usuario en la raíz + una empresa SUB del MISMO grupo → permitido.
    r = client.post(f"/api/v1/memberships/{env['m_multi_grupo']}/password", headers=h,
                    json={"password": "Nueva123!"})
    assert r.status_code == 200, r.text


def test_password_solo_esta_empresa_ok(client, env, auth_as, fake_auth_admin):
    auth_as(env["owner_a"]); h = _h(env["owner_a"])
    r = client.post(f"/api/v1/memberships/{env['m_tomador']}/password", headers=h,
                    json={"password": "Nueva123!"})
    assert r.status_code == 200, r.text


def test_takeover_owner_de_hija_bloqueado(client, env, auth_as, fake_auth_admin):
    # El objetivo es OWNER de la hija S pero TOMADOR en A. Un ADMIN de A NO debe
    # poder resetear su contraseña global (sería takeover de la hija).
    auth_as(env["admin_a"]); h = _h(env["admin_a"])
    r = client.post(f"/api/v1/memberships/{env['m_owner_hija']}/password", headers=h,
                    json={"password": "Conocida123!"})
    assert r.status_code == 403 and "OWNER" in r.json()["detail"]
    # El OWNER del grupo sí puede (mismo grupo, autoridad real):
    auth_as(env["owner_a"]); ho = _h(env["owner_a"])
    assert client.post(f"/api/v1/memberships/{env['m_owner_hija']}/password", headers=ho,
                       json={"password": "Conocida123!"}).status_code == 200


# ── Fix 1: leak de CSDs de otros tenants ─────────────────────────────────────
def test_csd_list_solo_del_rfc_propio(client, env, auth_as, monkeypatch):
    import app.api.v1.empresa as empresa_mod

    class _FakePAC:
        configured = True

        @classmethod
        def from_settings(cls, settings):
            return cls()

        def listar_csds(self):
            return [
                {"Rfc": env["rfc_a"], "Certificate": "", "CsdExpirationDate": "2027-01-01"},
                {"Rfc": "AAA010101AAA", "Certificate": "", "CsdExpirationDate": "2027-01-01",
                 "PrivateKey": "SECRETO-DE-OTRO-TENANT"},
            ]

    monkeypatch.setattr(empresa_mod, "FacturamaClient", _FakePAC)
    auth_as(env["owner_a"]); h = _h(env["owner_a"])
    r = client.get("/api/v1/empresa/csd", headers=h)
    assert r.status_code == 200, r.text
    csds = r.json()
    assert len(csds) == 1                     # solo el del RFC propio
    assert csds[0]["Rfc"] == env["rfc_a"]
    assert "PrivateKey" not in str(csds)      # y sin campos sensibles

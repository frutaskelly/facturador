"""Empresas hijas del grupo (multi-empresa por usuario).

POST /empresa/hijas: solo OWNER, crea el tenant SUB colgado de la raíz del
grupo + membresía OWNER del creador. El switcher (X-Tenant-Id) la ve al
instante (invalidate_auth_cache).
"""
import random
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Membership, Role, Tenant, User


def _rfc_random() -> str:
    """RFC con formato SAT válido y aleatorio: tenants.rfc es UNIQUE global y la
    BD de test persiste entre corridas — un RFC fijo dejaría residuos que rompen
    corridas futuras (y corridas paralelas chocarían entre sí)."""
    return f"ZZZ{random.randint(0, 999999):06d}{uuid.uuid4().hex[:3].upper()}"


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"m": [], "u": [], "t": []}
    try:
        # RFC de la raíz con formato SAT válido: permite probar el 409 por
        # "RFC de la raíz duplicado" (un RFC no-SAT daría 422 antes del 409).
        tenant = Tenant(slug=f"grp-{suffix}", legal_name="Grupo Raiz SA", rfc=_rfc_random(),
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
                        tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["t"].append(tenant.id)
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()

        def _user(role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["u"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["m"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        owner = _user(owner_role, "owner")
        admin = _user(admin_role, "admin")
        db.commit()
        yield {"owner": owner, "admin": admin, "tenant_id": tenant.id}
    finally:
        # Las hijas creadas por los tests cuelgan del tenant raíz.
        for tid in created["t"]:
            hijas = [h.id for h in db.query(Tenant).filter(Tenant.parent_tenant_id == tid).all()]
            for hid in hijas:
                db.execute(text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": hid})
            for hid in hijas:
                db.query(Tenant).filter(Tenant.id == hid).delete()
        for mid in created["m"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["u"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["t"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit(); db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated", claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _h(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _payload(rfc=None, nombre="Empresa Hermana SA"):
    return {"legal_name": nombre, "rfc": rfc or _rfc_random(),
            "regimen_fiscal_sat": "601", "domicilio_fiscal_cp": "45010"}


def test_owner_crea_hija_y_el_switcher_la_ve(client, env, auth_as):
    auth_as(env["owner"]); h = _h(env["owner"])
    rfc = _rfc_random()
    r = client.post("/api/v1/empresa/hijas", headers=h, json=_payload(rfc=rfc.lower()))
    assert r.status_code == 201, r.text
    hija = r.json()
    assert hija["rfc"] == rfc                               # normalizado a mayúsculas

    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.id == uuid.UUID(hija["tenant_id"])).one()
        assert t.tier == "SUB"
        assert t.parent_tenant_id == env["tenant_id"]      # cuelga de la raíz
        assert t.status == "ACTIVE"
    finally:
        db.close()

    # El /me ya incluye la hija (invalidate_auth_cache) y el selector la acepta.
    me = client.get("/api/v1/auth/me", headers=h).json()
    ids = {t["tenant_id"] for t in me["tenants"]}
    assert hija["tenant_id"] in ids
    me2 = client.get("/api/v1/auth/me", headers={"X-Tenant-Id": hija["tenant_id"]}).json()
    assert me2["active_tenant"]["tenant_id"] == hija["tenant_id"]
    assert me2["active_tenant"]["is_owner"] is True         # OWNER de la nueva


def test_no_owner_no_puede(client, env, auth_as):
    auth_as(env["admin"]); h = _h(env["admin"])
    r = client.post("/api/v1/empresa/hijas", headers=h, json=_payload())
    assert r.status_code == 403 and "OWNER" in r.json()["detail"]


def test_rfc_duplicado_409(client, env, auth_as):
    auth_as(env["owner"]); h = _h(env["owner"])
    # El RFC de la RAÍZ ya existe en tenants → 409 directo.
    db = SessionLocal()
    try:
        rfc_raiz = db.query(Tenant).filter(Tenant.id == env["tenant_id"]).one().rfc
    finally:
        db.close()
    assert client.post("/api/v1/empresa/hijas", headers=h,
                       json=_payload(rfc=rfc_raiz)).status_code == 409
    # Y el RFC de una hermana recién creada también → 409.
    rfc = _rfc_random()
    assert client.post("/api/v1/empresa/hijas", headers=h,
                       json=_payload(rfc=rfc)).status_code == 201
    assert client.post("/api/v1/empresa/hijas", headers=h,
                       json=_payload(rfc=rfc, nombre="Otra SA")).status_code == 409


def test_rfc_invalido_422(client, env, auth_as):
    auth_as(env["owner"]); h = _h(env["owner"])
    r = client.post("/api/v1/empresa/hijas", headers=h, json=_payload(rfc="NO-ES-UN-RFC"))
    assert r.status_code == 422


def test_hija_de_hija_es_hermana(client, env, auth_as):
    auth_as(env["owner"]); h = _h(env["owner"])
    h1 = client.post("/api/v1/empresa/hijas", headers=h, json=_payload()).json()
    # Operando DESDE la hija, la nueva cuelga de la raíz (hermanas, no nietas).
    h2 = client.post("/api/v1/empresa/hijas", headers={"X-Tenant-Id": h1["tenant_id"]},
                     json=_payload(nombre="Nieta Que No Es SA"))
    assert h2.status_code == 201, h2.text
    db = SessionLocal()
    try:
        t2 = db.query(Tenant).filter(Tenant.id == uuid.UUID(h2.json()["tenant_id"])).one()
        assert t2.parent_tenant_id == env["tenant_id"]      # raíz, no h1
        assert t2.tier == "SUB"
    finally:
        db.close()


def test_limite_de_grupo(client, env, auth_as, monkeypatch):
    import app.api.v1.empresa as empresa_mod
    monkeypatch.setattr(empresa_mod, "_MAX_EMPRESAS_GRUPO", 2)
    auth_as(env["owner"]); h = _h(env["owner"])
    assert client.post("/api/v1/empresa/hijas", headers=h, json=_payload()).status_code == 201
    # raíz + 1 hija = 2 → tope alcanzado
    r = client.post("/api/v1/empresa/hijas", headers=h, json=_payload(nombre="Tercera SA"))
    assert r.status_code == 409 and "máximo" in r.json()["detail"]

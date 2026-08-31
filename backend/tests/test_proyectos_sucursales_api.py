"""Alcance del proyecto (migración 0058): a qué sucursales entrega.

La regla de negocio: si el proyecto tiene dueño (`cliente_id`), sus sucursales
deben ser de ese cliente — mezclar plazas ajenas cobraría con la negociación
equivocada. Un proyecto del grupo (sin dueño) sí puede abarcar sucursales de
varios clientes. Todo por la API real (RLS + RBAC), como test_catalog_api.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Cliente, Membership, Role, Sucursal, Tenant, User

_PURGE = ("proyecto_sucursales", "proyectos", "sucursales", "clientes")


@pytest.fixture
def env(db_engine):
    """Un tenant con ADMIN, dos clientes y sus sucursales."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(
            slug=f"proy-{suffix}", legal_name="Proy SA", rfc=f"P{suffix.upper()}X"[:13],
            regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
            tier="PRINCIPAL", status="ACTIVE",
        )
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        tid = tenant.id

        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-proy-{suffix}"
        u = User(email=f"proy-{suffix}@t.test", auth_user_id=sub, full_name="Admin Proy")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=tid, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)

        cli1 = Cliente(tenant_id=tid, codigo="C1", legal_name="Cliente 1 SA", rfc="XAXX010101000")
        cli2 = Cliente(tenant_id=tid, codigo="C2", legal_name="Cliente 2 SA", rfc="XEXX010101000")
        db.add_all([cli1, cli2]); db.flush()

        s1a = Sucursal(tenant_id=tid, cliente_id=cli1.id, nombre="Pachuca")
        s1b = Sucursal(tenant_id=tid, cliente_id=cli1.id, nombre="Tulancingo")
        s2a = Sucursal(tenant_id=tid, cliente_id=cli2.id, nombre="Actopan")
        db.add_all([s1a, s1b, s2a]); db.flush()
        db.commit()

        yield {
            "tenant": tid,
            "admin": {"sub": sub, "email": u.email, "tenant_id": tid},
            "cli1": str(cli1.id), "cli2": str(cli2.id),
            "s1a": str(s1a.id), "s1b": str(s1b.id), "s2a": str(s2a.id),
        }
    finally:
        for table in _PURGE:
            for tid in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit()
        db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"],
            email=user["email"],
            role="authenticated",
            claims={"sub": user["sub"]},
        )
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(user):
    return {"X-Tenant-Id": str(user["tenant_id"])}


def test_create_con_sucursales_del_cliente(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Hospitales", "cliente_id": env["cli1"],
        "sucursal_ids": [env["s1a"], env["s1b"]],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert sorted(body["sucursal_ids"]) == sorted([env["s1a"], env["s1b"]])
    assert sorted(body["sucursales_nombres"]) == ["Pachuca", "Tulancingo"]

    # La lista también las trae (hidratación en paginado).
    r = client.get("/api/v1/proyectos", headers=_hdr(env["admin"]))
    fila = next(p for p in r.json()["items"] if p["id"] == body["id"])
    assert sorted(fila["sucursales_nombres"]) == ["Pachuca", "Tulancingo"]


def test_create_rechaza_sucursal_de_otro_cliente(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Cruzado", "cliente_id": env["cli1"],
        "sucursal_ids": [env["s2a"]],
    })
    assert r.status_code == 422
    assert "no es del cliente" in r.json()["detail"]


def test_create_rechaza_sucursal_inexistente(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Fantasma", "cliente_id": env["cli1"],
        "sucursal_ids": [str(uuid.uuid4())],
    })
    assert r.status_code == 422
    assert "no existe" in r.json()["detail"]


def test_proyecto_del_grupo_acepta_varios_clientes(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Programa Estatal", "cliente_id": None,
        "sucursal_ids": [env["s1a"], env["s2a"]],
    })
    assert r.status_code == 201, r.text
    assert sorted(r.json()["sucursales_nombres"]) == ["Actopan", "Pachuca"]


def test_update_reemplaza_y_omitir_no_toca(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Rotativo", "cliente_id": env["cli1"],
        "sucursal_ids": [env["s1a"]],
    })
    pid = r.json()["id"]

    # PATCH sin sucursal_ids: el alcance queda como estaba.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"notas": "sin tocar alcance"})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_ids"] == [env["s1a"]]

    # PATCH con la lista: reemplaza el set completo.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"sucursal_ids": [env["s1b"]]})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_ids"] == [env["s1b"]]

    # Lista vacía = quitar todas (explícito, no un olvido).
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"sucursal_ids": []})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_ids"] == []


def test_cambiar_dueno_valida_el_alcance(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Mudanza", "cliente_id": env["cli1"],
        "sucursal_ids": [env["s1a"]],
    })
    pid = r.json()["id"]

    # Cambiar el dueño sin retocar sucursales → 422 (no se sueltan en silencio).
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"cliente_id": env["cli2"]})
    assert r.status_code == 422
    assert "nuevo" in r.json()["detail"]

    # Con el alcance nuevo en el mismo PATCH sí pasa.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"cliente_id": env["cli2"], "sucursal_ids": [env["s2a"]]})
    assert r.status_code == 200, r.text
    assert r.json()["sucursales_nombres"] == ["Actopan"]

"""QA de los módulos nuevos: cruce de productos (match/alias), resolución de
series (cliente/sucursal/default), folio sin guion y guard de timbrado sandbox."""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from .conftest import crear_sucursal
from app.models import (
    Almacen, Cliente, Membership, Producto, Role, Serie, Sucursal, Tenant,
)

_PURGE = (
    "producto_alias", "lineas_factura", "facturas", "lineas_remision", "remisiones",
    "movimientos_inventario", "lotes_inventario", "series", "cliente_sucursales", "sucursales",
    "productos", "almacenes", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"cs-{suffix}", legal_name="CS SA", rfc=f"C{suffix.upper()}X"[:13],
                   regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        from app.models import User
        sub = f"sub-cs-{suffix}"
        u = User(email=f"cs-{suffix}@t.test", auth_user_id=sub, full_name="admin")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)

        zan = Producto(tenant_id=t.id, sku="P-ZAN", nombre="Zanahoria", clave_sat="50440000", unidad_sat="KGM",
                       unidad_base="KILO", presentaciones={"KILO": 1})
        chi = Producto(tenant_id=t.id, sku="P-CHI", nombre="Chile Jalapeño", clave_sat="50470000", unidad_sat="KGM",
                       unidad_base="KILO", presentaciones={"KILO": 1}, sinonimos=["jalapeno"])
        cli = Cliente(tenant_id=t.id, codigo="CL1", legal_name="Cliente 1", rfc="XAXX010101000")
        alm = Almacen(tenant_id=t.id, codigo="BG", nombre="Bodega")
        db.add_all([zan, chi, cli, alm]); db.flush()
        suc = crear_sucursal(db, tenant_id=t.id, cliente_id=cli.id, codigo="S1", nombre="Sucursal Norte")
        # series: default R (remisión) y A (factura)
        rdef = Serie(tenant_id=t.id, codigo="R", tipo="NO_FISCAL", tipo_documento="REMISION", es_default=True)
        adef = Serie(tenant_id=t.id, codigo="A", tipo="FISCAL", tipo_documento="FACTURA", es_default=True)
        rc = Serie(tenant_id=t.id, codigo="RC", tipo="NO_FISCAL", tipo_documento="REMISION")
        rs = Serie(tenant_id=t.id, codigo="RS", tipo="NO_FISCAL", tipo_documento="REMISION")
        db.add_all([rdef, adef, rc, rs]); db.flush()
        db.commit()
        out = {
            "user": {"sub": sub, "email": u.email, "tenant_id": t.id},
            "tenant_id": str(t.id), "zan": str(zan.id), "chi": str(chi.id),
            "cli": str(cli.id), "alm": str(alm.id), "suc": str(suc.id),
            "serie_rc": str(rc.id), "serie_rs": str(rs.id),
        }
        yield out
    finally:
        for table in _PURGE:
            for tid in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tid})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        from app.models import User
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for tid in created["tenants"]:
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


# ─── cruce de productos ──────────────────────────────────────────────────────
def test_match_exacto_y_difuso(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    # exacto
    r = client.post("/api/v1/productos/match", headers=h, json={"textos": ["Zanahoria"]})
    assert r.status_code == 200, r.text
    cands = r.json()[0]["candidatos"]
    assert cands and cands[0]["origen"] == "exacto" and cands[0]["producto_id"] == env["zan"]
    # difuso (typo "zanahorias")
    r = client.post("/api/v1/productos/match", headers=h, json={"textos": ["zanahorias"]})
    cands = r.json()[0]["candidatos"]
    assert any(c["producto_id"] == env["zan"] for c in cands)


def test_alias_aprendido_se_reutiliza(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    # "chile cuaresmeño" no matchea exacto al jalapeño
    r = client.post("/api/v1/productos/match", headers=h, json={"textos": ["chile cuaresmeño"]})
    assert all(c["origen"] != "alias" for c in r.json()[0]["candidatos"])
    # el usuario confirma el cruce → se aprende
    a = client.post("/api/v1/productos/alias", headers=h, json={"texto": "chile cuaresmeño", "producto_id": env["chi"]})
    assert a.status_code == 201, a.text
    # ahora resuelve por alias
    r = client.post("/api/v1/productos/match", headers=h, json={"textos": ["Chile Cuaresmeño"]})
    cands = r.json()[0]["candidatos"]
    assert cands[0]["origen"] == "alias" and cands[0]["producto_id"] == env["chi"]


# ─── resolución de series + folio sin guion ──────────────────────────────────
def _rem(client, h, env, **over):
    body = {"cliente_facturacion_id": env["cli"], "almacen_id": env["alm"],
            "lineas": [{"producto_id": env["zan"], "cantidad_solicitada": "5", "precio_unitario": "10"}]}
    body.update(over)
    return client.post("/api/v1/remisiones", headers=h, json=body)


def test_folio_sin_guion_y_default(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    r = _rem(client, h, env)
    assert r.status_code == 201, r.text
    assert r.json()["folio_interno"] == "R1"  # serie default, sin guion


def test_serie_override_gana(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    r = _rem(client, h, env, serie_id=env["serie_rc"])
    assert r.json()["folio_interno"] == "RC1"


def test_resolver_endpoint(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    r = client.get("/api/v1/series/resolver", headers=h,
                   params={"tipo_documento": "REMISION", "cliente_id": env["cli"]})
    assert r.status_code == 200
    assert r.json()["codigo"] == "R"  # default del inquilino


def test_serie_pareja(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    r = client.post("/api/v1/series/par", headers=h, json={
        "codigo_factura": "SLP", "codigo_remision": "RSLP", "nombre": "San Luis"})
    assert r.status_code == 201, r.text
    docs = {s["tipo_documento"]: s["codigo"] for s in r.json()}
    assert docs == {"FACTURA": "SLP", "REMISION": "RSLP"}


# ─── guard de timbrado sandbox (sin credenciales → 503) ──────────────────────
def test_timbrar_sin_credenciales_503(client, env, auth_as):
    auth_as(env["user"]); h = _h(env["user"])
    # carga stock, crea y confirma remisión, genera factura, intenta timbrar
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["zan"], "almacen_id": env["alm"],
        "cantidad": "100", "costo_unitario": "5"})
    rem = _rem(client, h, env).json()
    client.post(f"/api/v1/remisiones/{rem['id']}/confirmar", headers=h, json={})
    f = client.post("/api/v1/facturas/desde-remisiones", headers=h, json={"remision_ids": [rem["id"]]})
    assert f.status_code == 201, f.text
    t = client.post(f"/api/v1/facturas/{f.json()['id']}/timbrar", headers=h)
    # 503 si no hay credenciales (CI); 502 si el PAC sandbox rechaza datos de prueba.
    # Nunca debe timbrar real ni 200 con datos dummy.
    assert t.status_code in (502, 503), t.text

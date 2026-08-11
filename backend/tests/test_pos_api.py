"""POS Fase 0 — config del flujo por tenant + motor de transiciones.

Lo central: el MISMO motor sirve a un mostrador de 2 etapas y a una bodega de
4, solo cambiando la config; y el inventario sale al completar la etapa que la
config diga (con fallback a la última etapa activa).
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Almacen, Cliente, LoteInventario, Membership, Producto, Role, Tenant, User

_PURGE = (
    "pagos", "movimientos_inventario", "mermas", "lineas_remision",
    "remisiones", "lotes_inventario", "productos", "clientes", "almacenes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"pos-{suffix}", legal_name="POS SA", rfc=f"P{suffix.upper()}S"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
                        tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-pos-{suffix}"
        u = User(email=f"pos-{suffix}@t.test", auth_user_id=sub, full_name="admin")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)

        cli = Cliente(tenant_id=tenant.id, codigo="POSC", legal_name="Cliente POS SA",
                      rfc="XAXX010101000", regimen_fiscal="601", uso_cfdi_default="G03")
        prod = Producto(tenant_id=tenant.id, sku="POS-P", nombre="Prod POS",
                        clave_sat="50406500", unidad_sat="KGM")
        alm = Almacen(tenant_id=tenant.id, codigo="POS-BG", nombre="Bodega POS")
        db.add_all([cli, prod, alm]); db.flush()
        db.commit()
        yield {"sub": sub, "email": u.email, "tenant_id": tenant.id,
               "cli": str(cli.id), "prod": str(prod.id), "alm": str(alm.id)}
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
        db.commit(); db.close()


@pytest.fixture
def auth(env):
    app.dependency_overrides[get_principal] = lambda: Principal(
        auth_user_id=env["sub"], email=env["email"], role="authenticated", claims={"sub": env["sub"]})
    yield
    app.dependency_overrides.pop(get_principal, None)


def _h(env):
    return {"X-Tenant-Id": str(env["tenant_id"])}


def _disponible(env):
    db = SessionLocal()
    try:
        row = db.query(LoteInventario).filter(
            LoteInventario.producto_id == uuid.UUID(env["prod"])).first()
        return Decimal(row.cantidad_disponible) if row else None
    finally:
        db.close()


def _config(client, env, **over):
    base = {"activo": True, "etapas": ["pedido", "caja", "almacen", "salida"],
            "inventario_sale_en": "surtido"}
    r = client.put("/api/v1/pos/config", headers=_h(env), json={**base, **over})
    assert r.status_code == 200, r.text
    return r.json()


def _remision(client, env, cantidad="30"):
    h = _h(env)
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod"], "almacen_id": env["alm"],
        "cantidad": "100", "costo_unitario": "5"})
    r = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["cli"], "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": cantidad,
                    "precio_unitario": "20"}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_config_defaults_y_roundtrip(client, env, auth):
    r = client.get("/api/v1/pos/config", headers=_h(env))
    assert r.status_code == 200, r.text
    assert r.json()["activo"] is False                      # apagado por default
    assert r.json()["puede_configurar"] is True             # ADMIN

    cfg = _config(client, env, etapas=["pedido", "caja"], inventario_sale_en="cobro")
    assert cfg["etapas"] == ["pedido", "caja"]
    r2 = client.get("/api/v1/pos/config", headers=_h(env))
    assert r2.json()["activo"] is True
    assert r2.json()["etapas_visibles"] == ["pedido", "caja"]  # ADMIN ve las activas

    bad = client.put("/api/v1/pos/config", headers=_h(env),
                     json={"activo": True, "etapas": ["pedido", "drive-thru"]})
    assert bad.status_code == 422


def test_flujo_completo_4_etapas(client, env, auth):
    """caja → almacén (aquí sale inventario: sale_en=surtido) → salida → completado."""
    _config(client, env)
    rem = _remision(client, env, "30")
    h = _h(env)

    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/iniciar", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["pos_etapa"] == "caja"                  # primera cola
    assert _disponible(env) == Decimal("100")               # aún no sale nada

    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/avanzar", headers=h, json={"etapa": "caja"})
    assert r.status_code == 200 and r.json()["pos_etapa"] == "almacen"
    assert _disponible(env) == Decimal("100")

    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/avanzar", headers=h, json={"etapa": "almacen"})
    assert r.status_code == 200 and r.json()["pos_etapa"] == "salida"
    assert r.json()["estado"] == "CONFIRMADA"               # salida directa aplicada
    assert _disponible(env) == Decimal("70")

    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/avanzar", headers=h, json={"etapa": "salida"})
    assert r.status_code == 200 and r.json()["pos_etapa"] == "completado"

    # Doble clic / repetir una etapa ya completada → 409, nunca doble salida.
    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/avanzar", headers=h, json={"etapa": "salida"})
    assert r.status_code == 409
    assert _disponible(env) == Decimal("70")


def test_flujo_mostrador_2_etapas(client, env, auth):
    """Solo pedido+caja: sale_en=surtido cae a la última etapa activa (caja)."""
    _config(client, env, etapas=["pedido", "caja"])
    rem = _remision(client, env, "10")
    h = _h(env)

    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/iniciar", headers=h)
    assert r.json()["pos_etapa"] == "caja"
    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/avanzar", headers=h, json={"etapa": "caja"})
    assert r.status_code == 200, r.text
    assert r.json()["pos_etapa"] == "completado"
    assert r.json()["estado"] == "CONFIRMADA"
    assert _disponible(env) == Decimal("90")


def test_iniciar_validaciones(client, env, auth):
    h = _h(env)
    rem = _remision(client, env, "5")
    # POS apagado → 409
    r = client.post(f"/api/v1/pos/remisiones/{rem['id']}/iniciar", headers=h)
    assert r.status_code == 409
    _config(client, env)
    # ok → doble iniciar → 409
    assert client.post(f"/api/v1/pos/remisiones/{rem['id']}/iniciar", headers=h).status_code == 200
    assert client.post(f"/api/v1/pos/remisiones/{rem['id']}/iniciar", headers=h).status_code == 409


def test_cola_lista_y_valida(client, env, auth):
    _config(client, env)
    rem = _remision(client, env, "5")
    h = _h(env)
    client.post(f"/api/v1/pos/remisiones/{rem['id']}/iniciar", headers=h)

    r = client.get("/api/v1/pos/cola/caja", headers=h)
    assert r.status_code == 200, r.text
    assert any(x["id"] == rem["id"] for x in r.json()["items"])
    assert client.get("/api/v1/pos/cola/drive", headers=h).status_code == 422
    # etapa válida pero apagada en el flujo → 422
    _config(client, env, etapas=["pedido", "caja"])
    assert client.get("/api/v1/pos/cola/almacen", headers=h).status_code == 422

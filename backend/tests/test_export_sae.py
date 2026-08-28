"""Export masivo para SAE (fase espejo): layout exacto, folios con relleno,
estampado del espejo (factura_sae → RESERVADO), y los candados que evitan
duplicar documentos en SAE (re-export, cliente sin clave, partida sin código)."""
import io
import uuid

import pytest
import xlrd
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Cliente,
    ClienteExterno,
    Membership,
    Producto,
    ProductoCliente,
    Role,
    Serie,
    Tenant,
    User,
)
from app.services.export_sae import FACTURA_HDR, PEDIDO_HDR, folio_sae

_PURGE = (
    "lineas_factura", "facturas", "lineas_remision", "remisiones", "cliente_externos",
    "producto_clientes", "productos", "series", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"sae-{suffix}", legal_name="Export SAE SA",
                   rfc=f"SAE{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-sae-{suffix}"
        u = User(email=f"sae-{suffix}@t.test", auth_user_id=sub, full_name="sae")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        admin = {"sub": sub, "email": u.email, "tenant_id": t.id}

        serie_f = Serie(tenant_id=t.id, codigo="ZHGO", tipo="FISCAL",
                        tipo_documento="FACTURA", nombre="Balles y Jubran")
        serie_r = Serie(tenant_id=t.id, codigo="RZHGO", tipo="NO_FISCAL",
                        tipo_documento="REMISION", nombre="Balles y Jubran")
        db.add_all([serie_f, serie_r]); db.flush()

        cli = Cliente(tenant_id=t.id, codigo="CL1", legal_name="OPERADORA BALLES",
                      rfc="OBV191007BS1", serie_factura_id=serie_f.id,
                      serie_remision_id=serie_r.id, metodo_pago_default="PPD",
                      forma_pago_default="99", uso_cfdi_default="G01")
        prod = Producto(tenant_id=t.id, sku="00000001", nombre="ACEITE 20 LT",
                        clave_sat="50151500", unidad_sat="H87")
        db.add_all([cli, prod]); db.flush()
        db.add(ClienteExterno(tenant_id=t.id, sistema="SAE", clave="02:7",
                              clave_normalizada="02 7", cliente_id=cli.id,
                              origen="MANUAL", confianza="CONFIRMADA"))
        db.add(ProductoCliente(tenant_id=t.id, cliente_id=cli.id, producto_id=prod.id,
                               codigo_cliente="ACEI-ACEI-639"))
        db.commit()
        yield {"admin": admin, "cli": str(cli.id), "prod": str(prod.id), "tenant": t.id}
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
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated",
            claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _rem(client, h, env, **over):
    body = {"cliente_facturacion_id": env["cli"], "su_pedido": "24736",
            "lineas": [{"producto_id": env["prod"], "cantidad_solicitada": 5,
                        "precio_unitario": 836}]}
    body.update(over)
    r = client.post("/api/v1/remisiones", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_folio_sae_es_serie_mas_10():
    # El relleno de CVE_DOC no es por serie: SIEMPRE serie + número en 10.
    assert folio_sae("ZHGO", 233) == "ZHGO       233"          # 14 chars
    assert folio_sae("ZMAFAN", 7) == "ZMAFAN         7"        # 16 chars
    assert folio_sae("ZEHMOHOS", 1560) == "ZEHMOHOS      1560" # 18 chars


def test_export_factura_layout_y_espejo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)

    # preview: sugiere serie del cliente y pide folio
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv["ok"] is True, pv
    assert pv["empresa"] == "02"
    assert pv["series"] == [{"serie": "ZHGO", "remisiones": 1, "folio_sugerido": None}]

    # export: genera el .xls y estampa el espejo
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "FACTURA",
                          "folios": {"ZHGO": 233}, "fecha": "2026-08-28"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.ms-excel")

    libro = xlrd.open_workbook(file_contents=r.content)
    hoja = libro.sheet_by_name("Facturas")
    assert [c.value for c in hoja.row(0)] == FACTURA_HDR
    fila = [c.value for c in hoja.row(1)]
    assert fila[0] == "ZHGO       233"          # relleno exacto
    assert fila[1] == "7"                       # número de cliente SAE, no CLI-001
    assert fila[2] == "08/28/2026"              # MM/DD/YYYY — la trampa de fechas
    assert fila[4] == "ACEI-ACEI-639"           # CVE_ART del cliente, no el SKU
    assert fila[5] == 5.0 and fila[6] == 836.0
    assert fila[21:24] == ["PPD", "99", "G01"]
    assert fila[26].startswith("OC 24736")      # la llave de conciliación

    # el espejo quedó puesto: factura_sae + RESERVADO
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] == "ZHGO 233"
    assert det["estado"] == "RESERVADO"

    # candado: re-exportarla duplicaría el documento en SAE
    r2 = client.post("/api/v1/remisiones/export-sae", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA", "folios": {"ZHGO": 234}})
    assert r2.status_code == 422
    assert "ZHGO 233" in r2.json()["detail"]

    # y el folio sugerido del siguiente lote sale del espejo
    rem2 = _rem(client, h, env, su_pedido="24737")
    pv2 = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                      json={"ids": [rem2["id"]], "tipo": "FACTURA"}).json()
    assert pv2["series"][0]["folio_sugerido"] == 234


def test_export_factura_sin_folio_es_422(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "FACTURA"})
    assert r.status_code == 422
    assert "folio inicial" in r.json()["detail"]
    # y NO estampó nada (estampa y archivo viajan juntos o nada)
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None


def test_export_pedido_lleva_la_oc_del_cliente(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    rem = _rem(client, h, env)
    r = client.post("/api/v1/remisiones/export-sae", headers=h,
                    json={"ids": [rem["id"]], "tipo": "PEDIDO"})
    assert r.status_code == 200, r.text
    hoja = xlrd.open_workbook(file_contents=r.content).sheet_by_name("Pedidos")
    assert [c.value for c in hoja.row(0)] == PEDIDO_HDR
    fila = [c.value for c in hoja.row(1)]
    assert fila[0] == "24736"          # la OC del cliente; SAE folia al importar
    assert fila[4] == "ACEI-ACEI-639"
    # PEDIDO no estampa espejo: la factura aún no existe
    det = client.get(f"/api/v1/remisiones/{rem['id']}", headers=h).json()
    assert det["factura_sae"] is None


def test_partida_sin_codigo_del_cliente_detiene_el_lote(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:6]
        nuevo = Producto(tenant_id=env["tenant"], sku=f"9{suffix}", nombre="SIN CODIGO",
                         clave_sat="50300000", unidad_sat="KGM")
        db.add(nuevo); db.commit()
        nuevo_id = str(nuevo.id)
    finally:
        db.close()
    rem = _rem(client, h, env, lineas=[
        {"producto_id": env["prod"], "cantidad_solicitada": 5, "precio_unitario": 836},
        {"producto_id": nuevo_id, "cantidad_solicitada": 2, "precio_unitario": 10},
    ])
    pv = client.post("/api/v1/remisiones/export-sae/preview", headers=h,
                     json={"ids": [rem["id"]], "tipo": "FACTURA"}).json()
    assert pv["ok"] is False
    assert any("sin código del cliente" in e for e in pv["errores"])

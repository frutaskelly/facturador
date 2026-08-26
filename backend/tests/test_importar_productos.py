"""Importación masiva de productos + catálogo por cliente.

Cubre: la plantilla xlsx (descarga y round-trip determinista), el preview con
cruce contra el catálogo (vincular en vez de duplicar), el alta masiva (SKU
automático, alias del cliente, precios a su lista) y que el CFDI use el
código/nombre del cliente (NoIdentificacion / Descripcion).
"""
import io
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Almacen, Cliente, EsquemaImpuesto, ListaPrecios, Membership, Precio,
    Producto, ProductoAlias, ProductoCliente, Role, Tenant, User,
)

_PURGE = (
    "movimientos_inventario", "lineas_factura", "facturas", "lotes_inventario",
    "producto_clientes", "producto_alias", "precios", "listas_precios",
    "productos", "esquemas_impuesto", "clientes", "almacenes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"imp-{suffix}", legal_name="Imp SA", rfc=f"I{suffix.upper()}X"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        tomador_role = db.query(Role).filter(Role.nombre == "TOMADOR", Role.es_preset.is_(True)).one()

        def _user(role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        admin = _user(admin_role, "admin")
        tomador = _user(tomador_role, "tomador")

        lista = ListaPrecios(tenant_id=tenant.id, codigo="LIMP", nombre="Lista Imp")
        db.add(lista); db.flush()
        cli = Cliente(tenant_id=tenant.id, codigo="CIMP", legal_name="Cliente Imp SA",
                      rfc="OBV191007BS1", regimen_fiscal="601", uso_cfdi_default="G01",
                      lista_precios_id=lista.id, domicilio_fiscal={"cp": "42110"})
        prod = Producto(tenant_id=tenant.id, sku="00000010", nombre="JITOMATE SALADETT",
                        clave_sat="50421800", unidad_sat="KGM")
        alm = Almacen(tenant_id=tenant.id, codigo="IMP-BG", nombre="Bodega Imp")
        db.add_all([cli, prod, alm]); db.flush()
        db.commit()
        yield {"admin": admin, "tomador": tomador, "tenant_id": tenant.id,
               "cli_id": str(cli.id), "prod_id": str(prod.id),
               "lista_id": str(lista.id), "alm_id": str(alm.id)}
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
            auth_user_id=user["sub"], email=user["email"], role="authenticated", claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _xlsx(rows: list[list]) -> bytes:
    """Arma un xlsx en memoria (fila 0 = encabezados)."""
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    out = io.BytesIO(); wb.save(out)
    return out.getvalue()


# ─── plantilla ───────────────────────────────────────────────────────────────
def test_plantilla_descarga(client, env, auth_as):
    auth_as(env["admin"])
    r = client.get("/api/v1/productos/plantilla-importacion", headers=_hdr(env["admin"]))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"   # zip = xlsx

    auth_as(env["tomador"])
    r = client.get("/api/v1/productos/plantilla-importacion", headers=_hdr(env["tomador"]))
    assert r.status_code == 403     # solo quien gestiona productos


# ─── preview ─────────────────────────────────────────────────────────────────
def test_preview_plantilla_cruza_sin_duplicar(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([
        ["NOMBRE", "CODIGO", "UNIDAD", "PRECIO"],
        ["JITOMATE SALADETT", "JIT-SAD-001", "KILO", "28.50"],
        ["PRODUCTO INVENTADO XYZ", "", "PIEZA", "10"],
    ])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("productos.xlsx", data,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"usar_ia": "false", "cliente_id": env["cli_id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["formato"] == "plantilla"
    f1, f2 = body["filas"]
    # Nombre exacto en catálogo → sugiere VINCULAR al existente (no duplicar).
    assert f1["producto_id"] == env["prod_id"]
    assert f1["candidatos"][0]["sku"] == "00000010"
    assert f1["ya_vinculado"] is False
    # Sin cruce → alta nueva (producto_id vacío).
    assert f2["producto_id"] is None


def test_preview_codigo_ya_vinculado(client, env, auth_as):
    db = SessionLocal()
    try:
        db.add(ProductoCliente(
            tenant_id=env["tenant_id"], cliente_id=uuid.UUID(env["cli_id"]),
            producto_id=uuid.UUID(env["prod_id"]),
            codigo_cliente="JIT-SAD-001", nombre_cliente="JITOMATE ROMA"))
        db.commit()
    finally:
        db.close()
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([["NOMBRE", "CODIGO"], ["JITOMATE ROMA", "JIT-SAD-001"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("lista.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false", "cliente_id": env["cli_id"]},
    )
    assert r.status_code == 200, r.text
    f1 = r.json()["filas"][0]
    # Su código ya está vinculado → mismo producto, marcado como vinculado.
    assert f1["producto_id"] == env["prod_id"]
    assert f1["ya_vinculado"] is True


def test_preview_archivo_sin_nombre_sin_ia(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    data = _xlsx([["COSA", "OTRA"], ["x", "y"]])
    r = client.post(
        "/api/v1/productos/importar-preview", headers=h,
        files={"archivo": ("raro.xlsx", data, "application/octet-stream")},
        data={"usar_ia": "false"},
    )
    assert r.status_code == 422
    assert "plantilla" in r.json()["detail"].lower()


# ─── importar ────────────────────────────────────────────────────────────────
def test_importar_crea_vincula_alias_y_precios(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "cliente_id": env["cli_id"],
        "guardar_precios": True,
        "filas": [
            {  # el cliente llama "JITOMATE ROMA" a nuestro saladett
                "accion": "vincular", "producto_id": env["prod_id"],
                "nombre": "JITOMATE ROMA", "codigo_cliente": "JIT-SAD-001",
                "nombre_cliente": "JITOMATE ROMA", "precio": "28.50",
            },
            {  # producto nuevo: SKU automático + alias con su código
                "accion": "crear", "nombre": "Tortilla Harina Tia Rosa 50",
                "unidad_base": "PIEZA", "clave_sat": "50221300", "unidad_sat": "H87",
                "codigo_cliente": "TORT-CERE-631", "nombre_cliente": "TORTILLAS DE HARINA TIA ROSA 50 PZA",
                "precio": "93.50",
            },
            {"accion": "omitir", "nombre": "FILA IGNORADA"},
        ],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["creados"], res["vinculados"], res["omitidos"]) == (1, 1, 1)
    assert res["alias_guardados"] == 2
    assert res["precios_guardados"] == 2
    assert res["errores"] == []

    db = SessionLocal()
    try:
        nuevo = db.query(Producto).filter(
            Producto.tenant_id == env["tenant_id"],
            Producto.nombre == "TORTILLA HARINA TIA ROSA 50").one()
        assert nuevo.sku.isdigit() and len(nuevo.sku) == 8   # SKU automático
        assert nuevo.unidad_base == "PIEZA"
        assert nuevo.presentaciones == {"PIEZA": 1}

        pcs = {pc.codigo_cliente: pc for pc in db.query(ProductoCliente).filter(
            ProductoCliente.cliente_id == uuid.UUID(env["cli_id"])).all()}
        assert str(pcs["JIT-SAD-001"].producto_id) == env["prod_id"]
        assert pcs["TORT-CERE-631"].producto_id == nuevo.id

        # El cruce aprendió el nombre del cliente.
        alias = db.query(ProductoAlias).filter(
            ProductoAlias.tenant_id == env["tenant_id"],
            ProductoAlias.alias_normalizado == "jitomate roma").one()
        assert str(alias.producto_id) == env["prod_id"]
        assert alias.origen == "IMPORT"

        # Precios a la lista del cliente.
        precios = db.query(Precio).filter(
            Precio.lista_id == uuid.UUID(env["lista_id"])).all()
        assert {str(p.precio_unitario) for p in precios} == {"28.5000", "93.5000"}
    finally:
        db.close()


def test_importar_precios_sin_lista_falla(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "guardar_precios": True,
        "filas": [{"accion": "crear", "nombre": "ALGO", "precio": "1"}],
    })
    assert r.status_code == 422
    assert "lista" in r.json()["detail"].lower()


def test_importar_fila_mala_no_tira_el_lote(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/importar", headers=h, json={
        "filas": [
            {"accion": "crear", "nombre": "PRODUCTO BUENO A"},
            {"accion": "crear", "nombre": "PRODUCTO SKU DUP", "sku": "00000010"},  # choca
            {"accion": "crear", "nombre": "PRODUCTO BUENO B"},
        ],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["creados"] == 2
    assert len(res["errores"]) == 1 and res["errores"][0]["fila"] == 2


# ─── CFDI: el nombre/código del cliente viajan en el XML ─────────────────────
def test_cfdi_usa_nombre_y_codigo_del_cliente(client, env, auth_as):
    from app.models import Factura
    from app.services.cfdi import build_payload
    auth_as(env["admin"]); h = _hdr(env["admin"])

    # Stock para la factura directa.
    client.post("/api/v1/inventario/movimientos", headers=h, json={
        "tipo": "ENTRADA_COMPRA", "producto_id": env["prod_id"], "almacen_id": env["alm_id"],
        "cantidad": "100", "costo_unitario": "5"})
    fac = client.post("/api/v1/facturas/directa", headers=h, json={
        "cliente_id": env["cli_id"], "almacen_id": env["alm_id"],
        "lineas": [{"producto_id": env["prod_id"], "cantidad": "2", "precio_unitario": "28.50"}],
    }).json()

    db = SessionLocal()
    try:
        f = db.query(Factura).filter(Factura.id == uuid.UUID(fac["id"])).one()
        # Sin alias: nombre interno + SKU interno (estándar: siempre viaja).
        payload = build_payload(db, f)
        item = payload["Items"][0]
        assert item["Description"] == "JITOMATE SALADETT"
        assert item["IdentificationNumber"] == "00000010"

        # Con alias del cliente: SU nombre y SU código.
        db.add(ProductoCliente(
            tenant_id=env["tenant_id"], cliente_id=uuid.UUID(env["cli_id"]),
            producto_id=uuid.UUID(env["prod_id"]),
            codigo_cliente="JIT-SAD-001", nombre_cliente="JITOMATE ROMA"))
        db.flush()
        payload = build_payload(db, f)
        item = payload["Items"][0]
        assert item["Description"] == "JITOMATE ROMA"
        assert item["IdentificationNumber"] == "JIT-SAD-001"
        db.rollback()
    finally:
        db.close()


# ─── catálogo del cliente (CRUD) ─────────────────────────────────────────────
def test_catalogo_cliente_crud(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    base = f"/api/v1/clientes/{env['cli_id']}/catalogo"

    r = client.put(f"{base}/{env['prod_id']}", headers=h,
                   json={"codigo_cliente": "JIT-SAD-001", "nombre_cliente": "Jitomate Roma"})
    assert r.status_code == 200, r.text
    assert r.json()["codigo_cliente"] == "JIT-SAD-001"

    r = client.get(base, headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["producto_sku"] == "00000010"
    assert rows[0]["nombre_cliente"] == "Jitomate Roma"

    # Upsert: re-captura actualiza, no duplica.
    r = client.put(f"{base}/{env['prod_id']}", headers=h,
                   json={"codigo_cliente": "JIT-2", "nombre_cliente": None})
    assert r.status_code == 200 and r.json()["codigo_cliente"] == "JIT-2"
    assert len(client.get(base, headers=h).json()) == 1

    # Vacío total → 422.
    r = client.put(f"{base}/{env['prod_id']}", headers=h, json={})
    assert r.status_code == 422

    r = client.delete(f"{base}/{env['prod_id']}", headers=h)
    assert r.status_code == 204
    assert client.get(base, headers=h).json() == []

    # Escritura exige gestionar clientes.
    auth_as(env["tomador"])
    r = client.put(f"{base}/{env['prod_id']}", headers=_hdr(env["tomador"]),
                   json={"codigo_cliente": "X"})
    assert r.status_code == 403

"""Cotizador de documentos: cruce (clave del cliente → exacto → difuso),
líneas sin cruce fuerte que NO se cotizan a ciegas, precio del resolutor,
PDF y listas descargables del cliente. La extracción IA se mockea: aquí se
prueba el cruce y el precio, no a Claude.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Cliente, ListaAsignacion, ListaPrecios, Membership, Precio,
    Producto, ProductoCliente, Role, Tenant, User,
)

_PURGE = (
    "lista_asignaciones", "precios", "listas_precios", "producto_clientes",
    "productos", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"cotdoc-{suffix}", legal_name="CotDoc SA", rfc=f"C{suffix.upper()}X"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        tid = tenant.id
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-adm-{suffix}"
        u = User(email=f"adm-{suffix}@t.test", auth_user_id=sub, full_name="adm")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=tid, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        admin = {"sub": sub, "email": u.email, "tenant_id": tid}

        aguacate = Producto(tenant_id=tid, sku="AGUACATE", nombre="Aguacate", clave_sat="50300000",
                            unidad_sat="KGM", unidad_base="KILO", presentaciones={"KILO": {}})
        limon = Producto(tenant_id=tid, sku="LIMON", nombre="Limón sin semilla", clave_sat="50300000",
                         unidad_sat="KGM", unidad_base="KILO", presentaciones={"KILO": {}})
        base = ListaPrecios(tenant_id=tid, codigo="UNICO", nombre="Precio único")
        negociada = ListaPrecios(tenant_id=tid, codigo="NEG", nombre="Negociada")
        db.add_all([aguacate, limon, base, negociada]); db.flush()
        for prod, precio in ((aguacate, "25"), (limon, "18")):
            db.add(Precio(tenant_id=tid, lista_id=base.id, producto_id=prod.id, presentacion="KILO",
                          precio_unitario=Decimal(precio), cantidad_minima=1))
        db.add(Precio(tenant_id=tid, lista_id=negociada.id, producto_id=aguacate.id, presentacion="KILO",
                      precio_unitario=Decimal("22"), cantidad_minima=1))
        db.add(Precio(tenant_id=tid, lista_id=negociada.id, producto_id=limon.id, presentacion="KILO",
                      precio_unitario=Decimal("20"), cantidad_minima=1))

        cli = Cliente(tenant_id=tid, codigo="C1", legal_name="Cliente Uno SA", rfc="XAXX010101000")
        db.add(cli); db.flush()
        # La clave con la que ESTE cliente pide el limón en sus OC.
        db.add(ProductoCliente(tenant_id=tid, cliente_id=cli.id, producto_id=limon.id,
                               codigo_cliente="P-77"))
        db.add(ListaAsignacion(tenant_id=tid, lista_id=negociada.id, cliente_id=cli.id))
        db.commit()

        yield {"admin": admin, "cli": str(cli.id), "aguacate": str(aguacate.id),
               "limon": str(limon.id), "negociada": str(negociada.id)}
    finally:
        for table in _PURGE:
            for t in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": t})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for t in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == t).delete()
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


def _cotizar(client, env, monkeypatch, partidas):
    from app.services import cotizador
    monkeypatch.setattr(cotizador, "extraer_partidas", lambda data, filename: partidas)
    return client.post(
        "/api/v1/precios/cotizar-documento",
        headers=_hdr(env["admin"]),
        files={"archivo": ("oc.pdf", b"%PDF-fake", "application/pdf")},
        data={"cliente_id": env["cli"]},
    )


def test_cruce_precio_y_totales(client, env, auth_as, monkeypatch):
    auth_as(env["admin"])
    r = _cotizar(client, env, monkeypatch, [
        # cruza exacto por nombre → lista negociada del cliente ($22)
        {"descripcion": "AGUACATE", "cantidad": Decimal("10"), "unidad": "KG", "clave": None},
        # la descripción no dice nada, pero la CLAVE del cliente decide → limón negociado $20
        {"descripcion": "PRODUCTO 77 DEL CONTRATO", "cantidad": Decimal("5"), "unidad": None, "clave": "P-77"},
        # nada parecido en el catálogo → sin_cruce, NO se cotiza a ciegas
        {"descripcion": "TORNILLOS GALVANIZADOS 3/4", "cantidad": Decimal("2"), "unidad": "CAJA", "clave": None},
    ])
    assert r.status_code == 200, r.text
    cot = r.json()
    assert len(cot["lineas"]) == 2
    assert len(cot["sin_cruce"]) == 1
    assert cot["sin_cruce"][0]["descripcion"].startswith("TORNILLOS")

    por_prod = {l["producto_id"]: l for l in cot["lineas"]}
    agu = por_prod[env["aguacate"]]
    assert float(agu["precio_unitario"]) == 22.0  # la negociada gana a la base
    assert agu["origen_precio"] == "lista_cliente"
    lim = por_prod[env["limon"]]
    assert lim["cruce"] == "su clave"
    assert float(lim["precio_unitario"]) == 20.0
    # subtotal = 10×22 + 5×20
    assert float(cot["subtotal"]) == 320.0
    assert cot["sin_precio"] == 0


def test_fuera_de_lista_no_se_cotiza(client, env, auth_as, monkeypatch):
    """Regla del dueño (29-ago-2026): con cliente que TIENE lista negociada,
    un producto que cruza pero NO está en ella no se cotiza a precio base —
    sale reportado con su motivo."""
    db = SessionLocal()
    try:
        db.query(Precio).filter(
            Precio.producto_id == uuid.UUID(env["limon"]),
            Precio.lista_id == uuid.UUID(env["negociada"]),
        ).delete()
        db.commit()
    finally:
        db.close()
    auth_as(env["admin"])
    cot = _cotizar(client, env, monkeypatch, [
        {"descripcion": "LIMON SIN SEMILLA", "cantidad": Decimal("5"), "unidad": "KG", "clave": "P-77"},
        {"descripcion": "AGUACATE", "cantidad": Decimal("1"), "unidad": "KG", "clave": None},
    ]).json()
    assert len(cot["lineas"]) == 1                       # solo el aguacate
    assert len(cot["sin_cruce"]) == 1
    assert "no está en la lista" in cot["sin_cruce"][0]["motivo"]
    assert float(cot["subtotal"]) == 22.0


def test_sin_precio_no_suma(client, env, auth_as, monkeypatch):
    auth_as(env["admin"])
    # presentación CAJA no existe en las listas → cruza pero sin precio
    db = SessionLocal()
    try:
        db.query(Producto).filter(Producto.id == uuid.UUID(env["limon"])).update(
            {"presentaciones": {"CAJA": {}}, "unidad_base": "CAJA", "presentacion_default": "CAJA"})
        db.commit()
    finally:
        db.close()
    r = _cotizar(client, env, monkeypatch, [
        {"descripcion": "LIMON SIN SEMILLA", "cantidad": Decimal("3"), "unidad": "CAJA", "clave": "P-77"},
        {"descripcion": "AGUACATE", "cantidad": Decimal("1"), "unidad": "KG", "clave": None},
    ])
    assert r.status_code == 200, r.text
    cot = r.json()
    assert cot["sin_precio"] == 1
    assert float(cot["subtotal"]) == 22.0  # solo el aguacate entra al total


def test_pdf_y_listas_del_cliente(client, env, auth_as, monkeypatch):
    auth_as(env["admin"])
    cot = _cotizar(client, env, monkeypatch, [
        {"descripcion": "AGUACATE", "cantidad": Decimal("4"), "unidad": "KG", "clave": None},
    ]).json()

    pdf = client.post("/api/v1/precios/cotizacion-pdf", headers=_hdr(env["admin"]), json=cot)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    listas = client.get("/api/v1/precios/listas-del-cliente",
                        headers=_hdr(env["admin"]), params={"cliente_id": env["cli"]}).json()
    ids = {l["lista_id"] for l in listas["listas"]}
    assert env["negociada"] in ids

"""Mini Conta — la clave que solo LEE ventas, y lo que lee.

Tres promesas: la conexión MINI_CONTA no puede hacer nada de lo que hace Smart
Supply (y Smart Supply no pierde nada); `/mini-conta/ventas` devuelve las
líneas facturadas de la plaza con la fecha de ENTREGA bien resuelta; y cada
cuenta de Mini Conta tiene SU clave, que solo lee lo que el dueño le compartió.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Cliente, ClienteSucursal, ClienteSucursalSerie, Factura, LineaFactura, LineaRemision,
    Membership, NotaCredito, NotaCreditoFactura, Producto, ReciboPago, ReciboPagoFactura,
    Remision, Role, Serie, Sucursal, Tenant, User,
)
from app.models.sucursal import PrecioOverride

_PURGE = (
    "lineas_remision", "nota_credito_facturas", "notas_credito", "recibo_pago_facturas",
    "recibos_pago", "precio_overrides",
    "lineas_factura", "remisiones", "facturas", "cliente_sucursal_series",
    "cliente_sucursales", "sucursales", "series", "conexiones", "oc_recibidas",
    "productos", "clientes",
)


def _utc(y, m, d, h=18):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(s):
            t = Tenant(slug=f"mc-{s}-{suffix}", legal_name=f"MC {s} SA",
                       rfc=f"M{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id); return t

        ta, tb = _tenant("a"), _tenant("b")
        db.add(Serie(tenant_id=tb.id, codigo="ZB1", tipo_documento="FACTURA"))
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()

        def _user(tenant, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=owner_role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        dueno_a = _user(ta, "mc-owner-a")
        dueno_b = _user(tb, "mc-owner-b")

        def _serie(t, codigo, tipo="FACTURA"):
            s = Serie(tenant_id=t.id, codigo=codigo, tipo_documento=tipo)
            db.add(s); db.flush(); return s

        cli = Cliente(tenant_id=ta.id, codigo="MC", legal_name="EHMO MC", rfc="GOA180712SF5",
                      dias_credito=15)
        cli2 = Cliente(tenant_id=ta.id, codigo="MC2", legal_name="DIF MC", rfc="DIF180712SF5")
        prod = Producto(tenant_id=ta.id, sku="00000283", nombre="AGUACATE",
                        clave_sat="01010101", unidad_sat="KGM")
        db.add_all([cli, cli2, prod]); db.flush()

        zsur, zdif, zch = _serie(ta, "ZSUR"), _serie(ta, "ZDIF"), _serie(ta, "ZCH5C")
        rem_serie = _serie(ta, "RCHIS", tipo="REMISION")
        zpac = _serie(ta, "ZPAC")
        zold = _serie(ta, "ZOLD")
        chis1 = Sucursal(tenant_id=ta.id, nombre="Chiapas")
        # Una fila vieja (borrada) con el mismo nombre: sus series no cuentan.
        chis2 = Sucursal(tenant_id=ta.id, nombre="CHIAPAS ", deleted_at=_utc(2026, 9, 1))
        pach = Sucursal(tenant_id=ta.id, nombre="Pachuca")
        muerta = Sucursal(tenant_id=ta.id, nombre="Tabasco", activo=False)
        db.add_all([chis1, chis2, pach, muerta]); db.flush()
        cs1 = ClienteSucursal(tenant_id=ta.id, cliente_id=cli.id, sucursal_id=chis1.id,
                              serie_factura_id=zsur.id)
        cs2 = ClienteSucursal(tenant_id=ta.id, cliente_id=cli2.id, sucursal_id=chis1.id,
                              serie_factura_id=zch.id)
        cs5 = ClienteSucursal(tenant_id=ta.id, cliente_id=cli.id, sucursal_id=chis2.id,
                              serie_factura_id=zold.id)
        cs3 = ClienteSucursal(tenant_id=ta.id, cliente_id=cli.id, sucursal_id=pach.id,
                              serie_factura_id=zpac.id)
        cs4 = ClienteSucursal(tenant_id=ta.id, cliente_id=cli2.id, sucursal_id=muerta.id,
                              serie_factura_id=zpac.id)
        db.add_all([cs1, cs2, cs3, cs4, cs5]); db.flush()
        db.add_all([
            ClienteSucursalSerie(tenant_id=ta.id, cliente_sucursal_id=cs1.id, serie_id=zdif.id),
            # Una serie de REMISIÓN en el abanico no es serie de venta.
            ClienteSucursalSerie(tenant_id=ta.id, cliente_sucursal_id=cs1.id, serie_id=rem_serie.id),
        ])

        def _factura(serie, folio, fecha, *, notas=None, estado="TIMBRADA", tipo="I",
                     borrada=False, cliente=cli, ppd_saldo=None):
            f = Factura(tenant_id=ta.id, serie=serie, folio=folio, cliente_id=cliente.id,
                        estado=estado, tipo_comprobante=tipo, uuid=str(uuid.uuid4()),
                        fecha=fecha, notas=notas, total=Decimal("11693"),
                        metodo_pago="PPD" if ppd_saldo is not None else "PUE",
                        saldo_insoluto=ppd_saldo or Decimal(0),
                        deleted_at=_utc(2026, 9, 20) if borrada else None)
            db.add(f); db.flush()
            db.add(LineaFactura(
                tenant_id=ta.id, factura_id=f.id, numero_linea=1, producto_id=prod.id,
                clave_prod_serv="01010101", clave_unidad="KGM", descripcion="AGUACATE HASS",
                cantidad=Decimal("133.3"), valor_unitario=Decimal("90"),
                importe=Decimal("11997.0000"), descuento=Decimal("404.0000"),
            ))
            db.add(LineaFactura(
                tenant_id=ta.id, factura_id=f.id, numero_linea=2, producto_id=None,
                clave_prod_serv="01010101", clave_unidad="H87", descripcion="FLETE",
                cantidad=Decimal("1"), valor_unitario=Decimal("100"),
                importe=Decimal("100"), presentacion="PIEZA",
            ))
            db.flush()
            return f

        # 1. con remisión que dice la entrega (y notas que dirían otra cosa)
        f_rem = _factura("ZSUR", 1, _utc(2026, 9, 10), notas="ENTREGA 07/09/2026",
                         ppd_saldo=Decimal("5000"))
        db.add(Remision(tenant_id=ta.id, folio_interno=f"R{suffix}", cliente_facturacion_id=cli.id,
                        fecha_entrega=date(2026, 9, 4), factura_id=f_rem.id))
        # 2. sin remisión: la fecha sale de las notas
        _factura("ZDIF", 2, _utc(2026, 9, 24),
                 notas="SEMANA 38: DIF ENTREGA LUNES 21 DE SEPTIEMBRE DE 2026 EN:MUNICIPIO")
        # 3. sin nada: la fecha de la factura. 03:00 UTC = 21:00 del día anterior en México.
        _factura("ZCH5C", 3, datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc),
                 notas="OC 0000024547 ENTREGAR PARA BALLES", cliente=cli2)
        # Lo que NO entra:
        _factura("ZSUR", 4, _utc(2026, 9, 11), estado="CANCELADA")
        _factura("ZSUR", 5, _utc(2026, 9, 11), estado="BORRADOR")
        _factura("ZSUR", 6, _utc(2026, 9, 11), tipo="E")
        _factura("ZSUR", 7, _utc(2026, 9, 11), borrada=True)
        _factura("ZPAC", 8, _utc(2026, 9, 11))                 # otra plaza
        _factura("ZSUR", 9, _utc(2026, 10, 2))                 # fuera del rango
        _factura("ZOLD", 10, _utc(2026, 9, 11))                # serie de la plaza borrada

        # Fase 2: una remisión entregada sin factura en Chiapas (serie prevista ZSUR por
        # el vínculo cli×Chiapas) y una cancelada, que no cuenta.
        for folio, estado in ((f"RB{suffix}", "BORRADOR"), (f"RC{suffix}", "CANCELADA")):
            r = Remision(tenant_id=ta.id, folio_interno=folio, cliente_facturacion_id=cli.id,
                         sucursal_id=chis1.id, fecha_entrega=date(2026, 9, 25), estado=estado,
                         subtotal=Decimal("900"), total=Decimal("900"))
            db.add(r); db.flush()
            db.add(LineaRemision(tenant_id=ta.id, remision_id=r.id, numero_linea=1,
                                 producto_id=prod.id, cantidad_solicitada=Decimal("10"),
                                 precio_unitario=Decimal("90"), importe=Decimal("900")))
        # Una nota de crédito de $116 aplicada a la factura 1.
        nc = NotaCredito(tenant_id=ta.id, cliente_id=cli.id, serie="NC", folio=7,
                         fecha=_utc(2026, 9, 18), total=Decimal("116"), estado="VIGENTE")
        db.add(nc); db.flush()
        db.add(NotaCreditoFactura(tenant_id=ta.id, nota_id=nc.id, factura_id=f_rem.id,
                                  importe=Decimal("116")))
        # Fase 3: un pago de $500 a la factura 1, y el precio del aguacate para cli en Chiapas.
        rp = ReciboPago(tenant_id=ta.id, cliente_id=cli.id, serie="CP", folio=3,
                        fecha_pago=_utc(2026, 9, 20), monto=Decimal("500"), estado="TIMBRADO")
        db.add(rp); db.flush()
        db.add(ReciboPagoFactura(tenant_id=ta.id, recibo_id=rp.id, factura_id=f_rem.id,
                                 importe_pagado=Decimal("500"), saldo_insoluto=Decimal("5000")))
        db.add(PrecioOverride(tenant_id=ta.id, cliente_id=cli.id, sucursal_id=chis1.id,
                              producto_id=prod.id, presentacion="KILO",
                              precio_unitario=Decimal("95")))
        db.commit()
        yield {"dueno_a": dueno_a, "dueno_b": dueno_b, "rem_factura": str(f_rem.id),
               "cli": str(cli.id), "cli2": str(cli2.id), "ta": ta.id}
    finally:
        db.rollback()
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


def _sin_sesion():
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _bearer(clave):
    return {"Authorization": f"Bearer {clave}"}


_TODAS = ["ZSUR", "ZDIF", "ZCH5C", "ZPAC", "ZOLD"]


def _clave(client, u, tipo, *, nombre="Kelly Chiapas", series=None, clientes=None,
           catalogo=False, **datos):
    body = None
    if tipo == "MINI_CONTA":
        body = {"nombre": nombre, "alcance": {
            "series": _TODAS if series is None else series,
            "clientes": clientes, "catalogo": catalogo, **datos}}
    r = client.post(f"/api/v1/conexiones/{tipo}/clave", headers=_hdr(u), json=body)
    assert r.status_code == 201, r.text
    return r.json()


_RANGO = {"sucursal": "chiapas", "desde": "2026-09-01", "hasta": "2026-09-30"}


# ─── la clave ────────────────────────────────────────────────────────────────

def test_generar_clave_mini_conta(client, env, auth_as):
    auth_as(env["dueno_a"])
    body = _clave(client, env["dueno_a"], "MINI_CONTA")
    assert body["clave"].startswith("fi_ss_")
    assert body["conexion"]["tipo"] == "MINI_CONTA"
    assert body["instruccion_whatsapp"] is None       # no se pega en WhatsApp

    listado = client.get("/api/v1/conexiones", headers=_hdr(env["dueno_a"])).json()
    tipos = {c["tipo"]: c for c in listado}
    assert set(tipos) >= {"SMART_SUPPLY", "MINI_CONTA"}
    assert tipos["MINI_CONTA"]["conexion"]["estado"] == "PENDIENTE"
    assert tipos["SMART_SUPPLY"]["conexion"] is None   # son independientes


def test_mini_conta_solo_lee_ventas(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    h = _bearer(clave)

    # Lo suyo.
    r = client.get("/api/v1/conexiones/probar", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["permisos"] == ["venta:leer_lineas"]
    assert client.get("/api/v1/mini-conta/sucursales", headers=h).status_code == 200

    # Nada de lo de Smart Supply.
    oc = {"canal": "WHATSAPP", "origen_externo": "WA:x", "folio_externo": "1",
          "lineas": [{"descripcion": "JITOMATE", "cantidad": "1"}]}
    assert client.post("/api/v1/oc-recibidas", headers=h, json=oc).status_code == 403
    assert client.post("/api/v1/remisiones", headers=h, json={}).status_code == 403
    assert client.get("/api/v1/remisiones", headers=h).status_code == 403
    assert client.get("/api/v1/clientes", headers=h).status_code == 403
    assert client.get("/api/v1/productos", headers=h).status_code == 403
    assert client.post("/api/v1/conexiones/grupos", headers=h, json={"grupos": []}).status_code == 403
    assert client.get("/api/v1/facturas", headers=h).status_code == 403
    assert client.get("/api/v1/conexiones", headers=h).status_code == 403


def test_smart_supply_conserva_su_alcance_y_no_lee_ventas(client, env, auth_as):
    from app.core.rbac import PERMISOS_CONEXION

    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "SMART_SUPPLY")["clave"]
    _sin_sesion()
    h = _bearer(clave)
    r = client.get("/api/v1/conexiones/probar", headers=h)
    assert r.status_code == 200
    assert r.json()["permisos"] == sorted(PERMISOS_CONEXION)
    assert client.get("/api/v1/clientes", headers=h).status_code == 200
    assert client.get("/api/v1/mini-conta/sucursales", headers=h).status_code == 403
    assert client.get("/api/v1/mini-conta/ventas", headers=h, params=_RANGO).status_code == 403


def test_una_persona_sin_menu_remisiones_no_prueba_conexiones():
    """`/probar` dejó de pedir menu:remisiones a las claves, no a las personas."""
    from app.api.v1.conexiones import probar
    from app.core.rbac import AuthContext
    from fastapi import HTTPException

    ctx = AuthContext(user_id=uuid.uuid4(), auth_user_id="x", email=None,
                      tenant_id=uuid.uuid4(), role_id=None, role_name="t",
                      is_owner=False, permissions={"menu:facturas"})
    with pytest.raises(HTTPException) as e:
        probar(db=None, ctx=ctx)
    assert e.value.status_code == 403


# ─── los endpoints ───────────────────────────────────────────────────────────

def test_sucursales_con_sus_series(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    r = client.get("/api/v1/mini-conta/sucursales", headers=_bearer(clave))
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"nombre": "Chiapas", "series": ["ZCH5C", "ZDIF", "ZSUR"]},
        {"nombre": "Pachuca", "series": ["ZPAC"]},
    ]   # Tabasco (inactiva) no sale; la serie de remisión tampoco


def test_ventas_de_la_plaza(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    r = client.get("/api/v1/mini-conta/ventas", headers=_bearer(clave), params=_RANGO)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sucursal"] == "Chiapas"
    assert d["series"] == ["ZCH5C", "ZDIF", "ZSUR"]
    assert (d["desde"], d["hasta"]) == ("2026-09-01", "2026-09-30")

    folios = sorted({(l["serie"], l["folio"]) for l in d["lineas"]})
    assert folios == [("ZCH5C", 3), ("ZDIF", 2), ("ZSUR", 1)]
    assert len(d["lineas"]) == 6

    por_folio = {l["folio"]: l for l in d["lineas"] if l["descripcion"] == "AGUACATE HASS"}
    uno = por_folio[1]
    assert uno["factura_id"] == env["rem_factura"]
    assert uno["fecha_factura"] == "2026-09-10"
    assert (uno["fecha_entrega"], uno["fecha_entrega_origen"]) == ("2026-09-04", "remision")
    assert uno["sku"] == "00000283" and uno["producto"] == "AGUACATE"
    assert uno["cantidad"] == "133.300000"
    assert uno["importe"] == "11593.00"             # importe − descuento, sin IVA
    assert uno["clave_unidad"] == "KGM"
    assert uno["uuid_cfdi"]

    assert (por_folio[2]["fecha_entrega"], por_folio[2]["fecha_entrega_origen"]) == (
        "2026-09-21", "notas")
    # 03:00 UTC del 16 = 15 de septiembre en México.
    assert por_folio[3]["fecha_factura"] == "2026-09-15"
    assert (por_folio[3]["fecha_entrega"], por_folio[3]["fecha_entrega_origen"]) == (
        "2026-09-15", "factura")

    flete = next(l for l in d["lineas"] if l["descripcion"] == "FLETE")
    assert flete["sku"] is None and flete["producto"] is None
    assert flete["presentacion"] == "PIEZA"
    assert flete["importe"] == "100.00"


def test_ventas_rango_y_plaza_invalidos(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    h = _bearer(clave)
    url = "/api/v1/mini-conta/ventas"
    assert client.get(url, headers=h, params={**_RANGO, "sucursal": "Marte"}).status_code == 404
    assert client.get(url, headers=h, params={**_RANGO, "hasta": "2026-08-01"}).status_code == 422
    assert client.get(url, headers=h, params={**_RANGO, "desde": "2025-01-01"}).status_code == 422
    # Un día exacto (inclusivo en ambos extremos).
    d = client.get(url, headers=h, params={**_RANGO, "desde": "2026-09-10",
                                             "hasta": "2026-09-10"}).json()
    assert {l["folio"] for l in d["lineas"]} == {1}


def test_ventas_no_cruzan_de_inquilino(client, env, auth_as):
    """La clave de B no ve las ventas de A aunque se llame igual la plaza."""
    auth_as(env["dueno_b"])
    clave = _clave(client, env["dueno_b"], "MINI_CONTA", series=["ZB1"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    h["X-Tenant-Id"] = str(env["dueno_a"]["tenant_id"])
    assert client.get("/api/v1/mini-conta/sucursales", headers=h).json() == []
    assert client.get("/api/v1/mini-conta/ventas", headers=h, params=_RANGO).status_code == 404


def test_el_dueno_tambien_puede_leer(client, env, auth_as):
    auth_as(env["dueno_a"])
    r = client.get("/api/v1/mini-conta/ventas", headers=_hdr(env["dueno_a"]), params=_RANGO)
    assert r.status_code == 200
    assert len(r.json()["lineas"]) == 6


def test_ventas_dicen_de_que_cliente_son(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    d = client.get("/api/v1/mini-conta/ventas", headers=_bearer(clave), params=_RANGO).json()
    por_folio = {l["folio"]: l for l in d["lineas"]}
    assert (por_folio[1]["cliente_id"], por_folio[1]["cliente"]) == (env["cli"], "EHMO MC")
    assert (por_folio[3]["cliente_id"], por_folio[3]["cliente"]) == (env["cli2"], "DIF MC")


# ─── una clave por cuenta ────────────────────────────────────────────────────

def test_cada_cuenta_tiene_su_clave_y_no_revoca_a_las_demas(client, env, auth_as):
    """El bug de Tabasco: generar la clave de una cuenta mataba la de otra."""
    auth_as(env["dueno_a"])
    chis = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Kelly Chiapas",
                  series=["ZSUR", "ZDIF", "ZCH5C"])
    pach = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Kelly Hidalgo",
                  series=["ZPAC"])

    listado = client.get("/api/v1/conexiones", headers=_hdr(env["dueno_a"])).json()
    mc = next(c for c in listado if c["tipo"] == "MINI_CONTA")
    assert [c["nombre"] for c in mc["conexiones"]] == ["Kelly Chiapas", "Kelly Hidalgo"]
    assert {c["estado"] for c in mc["conexiones"]} == {"PENDIENTE"}
    assert mc["conexiones"][1]["alcance"] == {
        "series": ["ZPAC"], "clientes": None, "catalogo": False, "remisiones": False,
        "notas_credito": False, "cobranza": False, "precios": False}

    # Nombre repetido (sin importar mayúsculas): 409, no una segunda clave.
    r = client.post("/api/v1/conexiones/MINI_CONTA/clave", headers=_hdr(env["dueno_a"]),
                    json={"nombre": "kelly chiapas ", "alcance": {"series": ["ZSUR"]}})
    assert r.status_code == 409

    # Regenerar la de Chiapas no toca la de Hidalgo, y hereda nombre y alcance.
    r = client.post(f"/api/v1/conexiones/{chis['conexion']['id']}/regenerar",
                    headers=_hdr(env["dueno_a"]))
    assert r.status_code == 201, r.text
    chis2 = r.json()
    assert chis2["conexion"]["nombre"] == "Kelly Chiapas"
    assert chis2["conexion"]["alcance"]["series"] == ["ZCH5C", "ZDIF", "ZSUR"]

    _sin_sesion()
    assert client.get("/api/v1/mini-conta/sucursales",
                      headers=_bearer(chis["clave"])).status_code == 401
    assert client.get("/api/v1/mini-conta/sucursales",
                      headers=_bearer(chis2["clave"])).status_code == 200
    assert client.get("/api/v1/mini-conta/sucursales",
                      headers=_bearer(pach["clave"])).json() == [
        {"nombre": "Pachuca", "series": ["ZPAC"]}]


def test_mini_conta_pide_nombre_y_series(client, env, auth_as):
    auth_as(env["dueno_a"])
    url = "/api/v1/conexiones/MINI_CONTA/clave"
    h = _hdr(env["dueno_a"])
    assert client.post(url, headers=h).status_code == 422
    assert client.post(url, headers=h, json={"nombre": "X", "alcance": {"series": []}}
                       ).status_code == 422
    r = client.post(url, headers=h, json={"nombre": "X", "alcance": {"series": ["ZB1"]}})
    assert r.status_code == 422 and "ZB1" in r.json()["detail"]   # serie de otra empresa
    r = client.post(url, headers=h, json={"nombre": "X", "alcance": {"series": ["RCHIS"]}})
    assert r.status_code == 422                                 # de remisión, no de factura


def test_la_clave_solo_lee_sus_series(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Kelly Hidalgo",
                   series=["ZPAC"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    url = "/api/v1/mini-conta/ventas"
    rango = {"desde": "2026-09-01", "hasta": "2026-09-30"}

    a = client.get("/api/v1/mini-conta/alcance", headers=h).json()
    assert a["conexion"]["nombre"] == "Kelly Hidalgo"
    assert a["empresa"]["id"] == str(env["ta"])
    assert a["sucursales"] == [{"nombre": "Pachuca", "series": ["ZPAC"]}]
    assert a["series"] == ["ZPAC"]
    # cli2 tiene ZPAC solo en una plaza apagada: no cuenta.
    assert [c["nombre"] for c in a["clientes"]] == ["EHMO MC"]
    assert "rfc" not in a["clientes"][0]
    assert a["catalogo"] is False and a["sin_limite"] is False

    d = client.get(url, headers=h, params=rango).json()
    assert d["series"] == ["ZPAC"] and {l["folio"] for l in d["lineas"]} == {8}
    assert client.get(url, headers=h, params={**rango, "series": "ZPAC"}).status_code == 200
    r = client.get(url, headers=h, params={**rango, "series": "ZPAC,ZSUR"})
    assert r.status_code == 403 and "ZSUR" in r.json()["detail"]
    assert client.get(url, headers=h, params={**rango, "sucursal": "Chiapas"}).status_code == 403
    assert client.get(url, headers=h, params={**rango, "sucursal": "Pachuca"}).status_code == 200


def test_la_clave_solo_lee_sus_clientes(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", clientes=[env["cli2"]])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    url = "/api/v1/mini-conta/ventas"
    a = client.get("/api/v1/mini-conta/alcance", headers=h).json()
    assert [c["nombre"] for c in a["clientes"]] == ["DIF MC"]

    d = client.get(url, headers=h, params=_RANGO).json()
    assert {l["folio"] for l in d["lineas"]} == {3}
    assert d["clientes"] == [env["cli2"]]
    assert client.get(url, headers=h, params={**_RANGO, "clientes": env["cli"]}
                      ).status_code == 403
    assert client.get(url, headers=h, params={**_RANGO, "clientes": "no-es-id"}
                      ).status_code == 422


def test_mini_conta_escoge_dentro_del_alcance(client, env, auth_as):
    """Lo que la cuenta escoge traer (series, clientes) recorta aún más."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    h = _bearer(clave)
    d = client.get("/api/v1/mini-conta/ventas", headers=h, params={
        "desde": "2026-09-01", "hasta": "2026-09-30", "series": "ZSUR,ZCH5C",
        "clientes": env["cli"]}).json()
    assert {l["folio"] for l in d["lineas"]} == {1}
    assert d["series"] == ["ZCH5C", "ZSUR"]


def test_editar_el_alcance_sin_cambiar_la_clave(client, env, auth_as):
    auth_as(env["dueno_a"])
    nueva = _clave(client, env["dueno_a"], "MINI_CONTA", series=["ZSUR"])
    cid = nueva["conexion"]["id"]
    r = client.patch(f"/api/v1/conexiones/{cid}", headers=_hdr(env["dueno_a"]),
                     json={"nombre": "Kelly Chiapas 2",
                           "alcance": {"series": ["ZPAC"], "catalogo": True}})
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "Kelly Chiapas 2"
    assert r.json()["alcance"] == {
        "series": ["ZPAC"], "clientes": None, "catalogo": True, "remisiones": False,
        "notas_credito": False, "cobranza": False, "precios": False}
    _sin_sesion()
    a = client.get("/api/v1/mini-conta/alcance", headers=_bearer(nueva["clave"])).json()
    assert a["series"] == ["ZPAC"] and a["catalogo"] is True


def test_smart_supply_no_tiene_alcance_que_editar(client, env, auth_as):
    auth_as(env["dueno_a"])
    ss = _clave(client, env["dueno_a"], "SMART_SUPPLY")
    r = client.patch(f"/api/v1/conexiones/{ss['conexion']['id']}", headers=_hdr(env["dueno_a"]),
                     json={"alcance": {"series": ["ZSUR"]}})
    assert r.status_code == 422
    assert client.get("/api/v1/conexiones/SMART_SUPPLY/opciones",
                      headers=_hdr(env["dueno_a"])).status_code == 404


def test_opciones_para_el_dueno(client, env, auth_as):
    auth_as(env["dueno_a"])
    r = client.get("/api/v1/conexiones/MINI_CONTA/opciones", headers=_hdr(env["dueno_a"]))
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["series"] == ["ZCH5C", "ZDIF", "ZOLD", "ZPAC", "ZSUR"]
    assert o["sucursales"] == [
        {"nombre": "Chiapas", "series": ["ZCH5C", "ZDIF", "ZSUR"]},
        {"nombre": "Pachuca", "series": ["ZPAC"]},
    ]
    por_nombre = {c["nombre"]: c["series"] for c in o["clientes"]}
    assert por_nombre == {"DIF MC": ["ZCH5C"],
                          "EHMO MC": ["ZDIF", "ZOLD", "ZPAC", "ZSUR"]}


def test_la_clave_de_antes_lee_todo_sin_catalogo(client, env, auth_as):
    """Las claves de antes de 0092 no tienen alcance: siguen leyendo todo hasta
    que el dueño les ponga uno (así Mini Conta no se cae al desplegar)."""
    from app.models import Conexion
    from app.models.conexion import generar_clave, hash_clave, pista_de

    clave = generar_clave()
    db = SessionLocal()
    try:
        db.add(Conexion(tenant_id=env["ta"], tipo="MINI_CONTA", nombre="Mini Conta",
                        clave_hash=hash_clave(clave), clave_pista=pista_de(clave),
                        estado="ACTIVA"))
        db.commit()
    finally:
        db.close()
    _sin_sesion()
    h = _bearer(clave)
    a = client.get("/api/v1/mini-conta/alcance", headers=h).json()
    assert a["sin_limite"] is True and a["catalogo"] is False
    assert a["series"] == ["ZCH5C", "ZDIF", "ZOLD", "ZPAC", "ZSUR"]
    assert client.get("/api/v1/mini-conta/ventas", headers=h, params=_RANGO).status_code == 200
    assert client.get("/api/v1/mini-conta/productos", headers=h,
                      params={"desde": "2026-09-01", "hasta": "2026-09-30"}).status_code == 403


def test_productos_que_se_facturan_en_sus_series(client, env, auth_as):
    auth_as(env["dueno_a"])
    sin = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Sin catálogo")["clave"]
    con = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Con catálogo",
                 series=["ZSUR"], catalogo=True)["clave"]
    _sin_sesion()
    url = "/api/v1/mini-conta/productos"
    rango = {"desde": "2026-09-01", "hasta": "2026-09-30"}
    assert client.get(url, headers=_bearer(sin), params=rango).status_code == 403

    r = client.get(url, headers=_bearer(con), params=rango)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["series"] == ["ZSUR"]
    # Solo la factura 1 (timbrada, en rango); el flete no tiene producto.
    assert d["productos"] == [{
        "sku": "00000283", "nombre": "AGUACATE", "unidad_sat": "KGM", "unidad_base": "KILO",
        "presentacion_default": "KILO", "lineas": 1, "ultima_venta": "2026-09-10"}]
    assert client.get(url, headers=_bearer(con), params={**rango, "series": "ZPAC"}
                      ).status_code == 403



# ─── fases 2 y 3 ─────────────────────────────────────────────────────────────

_SEPT = {"desde": "2026-09-01", "hasta": "2026-09-30"}


def test_cada_dato_nuevo_pide_su_bandera(client, env, auth_as):
    auth_as(env["dueno_a"])
    solo_ventas = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Solo ventas")["clave"]
    todo = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Todo", remisiones=True,
                  notas_credito=True, cobranza=True, precios=True)["clave"]
    _sin_sesion()
    a = client.get("/api/v1/mini-conta/alcance", headers=_bearer(todo)).json()
    assert {k: a[k] for k in ("remisiones", "notas_credito", "cobranza", "precios")} == {
        "remisiones": True, "notas_credito": True, "cobranza": True, "precios": True}
    for ruta, params in (("remisiones", _SEPT), ("notas-credito", _SEPT), ("cobranza", _SEPT),
                         ("cartera", {}), ("precios", {})):
        url = f"/api/v1/mini-conta/{ruta}"
        assert client.get(url, headers=_bearer(solo_ventas), params=params).status_code == 403, ruta
        assert client.get(url, headers=_bearer(todo), params=params).status_code == 200, ruta


def test_remisiones_entregadas_con_su_serie_prevista(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", remisiones=True)["clave"]
    solo_pac = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Pachuca", series=["ZPAC"],
                      remisiones=True)["clave"]
    _sin_sesion()
    d = client.get("/api/v1/mini-conta/remisiones", headers=_bearer(clave), params=_SEPT).json()
    assert len(d["lineas"]) == 1                       # la cancelada no cuenta
    ln = d["lineas"][0]
    assert (ln["serie"], ln["plaza"], ln["cliente"]) == ("ZSUR", "Chiapas", "EHMO MC")
    assert (ln["fecha_entrega"], ln["estado"], ln["facturada"]) == ("2026-09-25", "BORRADOR", False)
    assert (ln["sku"], ln["cantidad"], ln["importe"]) == ("00000283", "10.0000", "900.00")
    # La serie prevista (ZSUR) no es de esa clave: no la ve.
    assert client.get("/api/v1/mini-conta/remisiones", headers=_bearer(solo_pac),
                      params=_SEPT).json()["lineas"] == []


def test_notas_de_credito_se_reparten_entre_las_lineas(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", notas_credito=True)["clave"]
    _sin_sesion()
    url = "/api/v1/mini-conta/notas-credito"
    d = client.get(url, headers=_bearer(clave), params=_SEPT).json()
    assert [(l["descripcion"], l["importe"]) for l in d["lineas"]] == [
        ("AGUACATE HASS", "-115.01"), ("FLETE", "-0.99")]   # 116 × 11593/11693 y el resto
    uno = d["lineas"][0]
    assert (uno["nota"], uno["serie"], uno["folio"], uno["cantidad"]) == ("NC 7", "ZSUR", 1, "0")
    # La nota corrige la venta de esa factura: su fecha de entrega es la de la factura.
    assert (uno["fecha_entrega"], uno["fecha_entrega_origen"]) == ("2026-09-04", "remision")
    assert uno["fecha_nota"] == "2026-09-18"
    # El id es estable: volver a leer no cambia las líneas.
    otra = client.get(url, headers=_bearer(clave), params=_SEPT).json()
    assert [l["linea_id"] for l in otra["lineas"]] == [l["linea_id"] for l in d["lineas"]]
    fuera = client.get(url, headers=_bearer(clave),
                       params={"desde": "2026-09-19", "hasta": "2026-09-30"}).json()
    assert fuera["lineas"] == []


def test_cobranza_y_saldos(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", cobranza=True)["clave"]
    otra = _clave(client, env["dueno_a"], "MINI_CONTA", nombre="Solo DIF", clientes=[env["cli2"]],
                  cobranza=True)["clave"]
    _sin_sesion()
    c = client.get("/api/v1/mini-conta/cobranza", headers=_bearer(clave), params=_SEPT).json()
    assert [(x["recibo"], x["fecha_pago"], x["serie"], x["folio"], x["importe"])
            for x in c["cobros"]] == [("CP 3", "2026-09-20", "ZSUR", 1, "500.00")]

    s = client.get("/api/v1/mini-conta/cartera", headers=_bearer(clave)).json()
    assert [(x["serie"], x["folio"], x["saldo"], x["vencimiento"]) for x in s["facturas"]] == [
        ("ZSUR", 1, "5000.00", "2026-09-25")]            # 10-sep + 15 días de crédito
    # Otro cliente: ni sus pagos ni sus saldos.
    assert client.get("/api/v1/mini-conta/cobranza", headers=_bearer(otra),
                      params=_SEPT).json()["cobros"] == []
    assert client.get("/api/v1/mini-conta/cartera", headers=_bearer(otra)).json()["facturas"] == []


def test_precios_con_la_cascada_del_facturador(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", series=["ZSUR"], precios=True)["clave"]
    _sin_sesion()
    d = client.get("/api/v1/mini-conta/precios", headers=_bearer(clave)).json()
    assert [(p["sku"], p["cliente"], p["plaza"], p["presentacion"], p["precio"], p["origen"])
            for p in d["precios"]] == [
        ("00000283", "EHMO MC", "Chiapas", "KILO", "95.0000", "override_sucursal")]


# ─── fechas que se perdían (auditoría Smart Supply, 3-oct-2026) ─────────────

def _agregar(env, **filas):
    """Agrega al inquilino A, dentro de un test, lo que la fixture no trae."""
    db = SessionLocal()
    try:
        ta = env["ta"]
        cli = db.get(Cliente, uuid.UUID(env["cli"]))
        chis = db.query(Sucursal).filter(Sucursal.tenant_id == ta, Sucursal.nombre == "Chiapas").one()
        prod = db.query(Producto).filter(Producto.tenant_id == ta).first()
        out = {}
        for nombre, (fecha_rem, factura) in filas.get("remisiones", {}).items():
            f = None
            if factura is not None:
                serie, folio, fecha, notas = factura
                f = Factura(tenant_id=ta, serie=serie, folio=folio, cliente_id=cli.id,
                            estado="TIMBRADA", tipo_comprobante="I", uuid=str(uuid.uuid4()),
                            fecha=fecha, notas=notas, origen="ESPEJO_SAE", espejo_empresa="03")
                db.add(f); db.flush()
                db.add(LineaFactura(
                    tenant_id=ta, factura_id=f.id, numero_linea=1, producto_id=prod.id,
                    clave_prod_serv="01010101", clave_unidad="KGM", descripcion="AGUACATE",
                    cantidad=Decimal("5"), valor_unitario=Decimal("10"), importe=Decimal("50")))
            # Captura a mano: sin fecha de entrega (el formulario no la pide).
            r = Remision(tenant_id=ta, folio_interno=f"{nombre}{uuid.uuid4().hex[:6]}",
                         cliente_facturacion_id=cli.id, sucursal_id=chis.id,
                         fecha_remision=fecha_rem, fecha_entrega=None,
                         estado="FACTURADA" if f else "BORRADOR",
                         factura_id=f.id if f else None,
                         subtotal=Decimal("50"), total=Decimal("50"))
            db.add(r); db.flush()
            db.add(LineaRemision(tenant_id=ta, remision_id=r.id, numero_linea=1,
                                 producto_id=prod.id, cantidad_solicitada=Decimal("5"),
                                 precio_unitario=Decimal("10"), importe=Decimal("50")))
            out[nombre] = (str(r.id), str(f.id) if f else None)
        db.commit()
        return out
    finally:
        db.close()


def test_remision_sin_fecha_de_entrega_tambien_es_lo_entregado(client, env, auth_as):
    """122 de 123 remisiones capturadas a mano (25-ago a 3-oct-2026) no traen
    fecha de entrega y `/remisiones` filtraba SOLO por ella: no llegaban nunca.
    Ahora salen con la de las notas de su factura o, si no, la de remisión, y
    dicen de dónde salió. La factura de una así cae el mismo día."""
    ids = _agregar(env, remisiones={
        "RSF": (date(2026, 9, 26), None),
        "RNF": (date(2026, 9, 28), ("ZSUR", 30, _utc(2026, 9, 29),
                                    "OC HO-39BIE SEMANA 39 ENTREGA 27/09/2026")),
        "RSN": (date(2026, 9, 22), ("ZSUR", 31, _utc(2026, 9, 29), "OC 624")),
    })
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA", remisiones=True)["clave"]
    _sin_sesion()
    d = client.get("/api/v1/mini-conta/remisiones", headers=_bearer(clave), params=_SEPT).json()
    por = {l["remision_id"]: l for l in d["lineas"]}
    assert (por[ids["RSF"][0]]["fecha_entrega"], por[ids["RSF"][0]]["fecha_entrega_origen"]) == (
        "2026-09-26", "fecha_remision")
    assert (por[ids["RNF"][0]]["fecha_entrega"], por[ids["RNF"][0]]["fecha_entrega_origen"]) == (
        "2026-09-27", "notas")
    assert (por[ids["RSN"][0]]["fecha_entrega"], por[ids["RSN"][0]]["fecha_entrega_origen"]) == (
        "2026-09-22", "fecha_remision")
    # La de siempre (con fecha capturada) sigue igual.
    assert sum(1 for l in d["lineas"] if l["fecha_entrega_origen"] == "entrega") == 1

    v = client.get("/api/v1/mini-conta/ventas", headers=_bearer(clave), params=_RANGO).json()
    fac = {l["factura_id"]: l for l in v["lineas"]}
    assert (fac[ids["RNF"][1]]["fecha_entrega"], fac[ids["RNF"][1]]["fecha_entrega_origen"]) == (
        "2026-09-27", "notas")
    # Sin notas con fecha: la de su remisión (antes, la de la factura: 29-sep).
    assert (fac[ids["RSN"][1]]["fecha_entrega"], fac[ids["RSN"][1]]["fecha_entrega_origen"]) == (
        "2026-09-22", "remision")


def test_la_factura_del_espejo_conserva_el_dia_del_sae(client, env, auth_as):
    """El SAE da la fecha sin hora y el espejo la guarda como medianoche UTC;
    pasarla a México la corría al día anterior (ZEHMOVH1542: SAE 2-oct, aquí
    1-oct). El día del espejo es el de UTC; el de lo nativo, el de México."""
    ids = _agregar(env, remisiones={
        "RES": (date(2026, 9, 12), ("ZSUR", 40, datetime(2026, 9, 12, tzinfo=timezone.utc), None)),
    })
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], "MINI_CONTA")["clave"]
    _sin_sesion()
    solo_el_12 = {**_RANGO, "desde": "2026-09-12", "hasta": "2026-09-12"}
    v = client.get("/api/v1/mini-conta/ventas", headers=_bearer(clave), params=solo_el_12).json()
    lineas = [l for l in v["lineas"] if l["factura_id"] == ids["RES"][1]]
    assert lineas and lineas[0]["fecha_factura"] == "2026-09-12"
    el_11 = {**_RANGO, "desde": "2026-09-11", "hasta": "2026-09-11"}
    v = client.get("/api/v1/mini-conta/ventas", headers=_bearer(clave), params=el_11).json()
    assert not [l for l in v["lineas"] if l["factura_id"] == ids["RES"][1]]

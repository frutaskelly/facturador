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
    Cliente, ClienteSucursal, ClienteSucursalSerie, Factura, LineaFactura, Membership,
    Producto, Remision, Role, Serie, Sucursal, Tenant, User,
)

_PURGE = (
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

        cli = Cliente(tenant_id=ta.id, codigo="MC", legal_name="EHMO MC", rfc="GOA180712SF5")
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
                     borrada=False, cliente=cli):
            f = Factura(tenant_id=ta.id, serie=serie, folio=folio, cliente_id=cliente.id,
                        estado=estado, tipo_comprobante=tipo, uuid=str(uuid.uuid4()),
                        fecha=fecha, notas=notas,
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
        f_rem = _factura("ZSUR", 1, _utc(2026, 9, 10), notas="ENTREGA 07/09/2026")
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
           catalogo=False):
    body = None
    if tipo == "MINI_CONTA":
        body = {"nombre": nombre, "alcance": {
            "series": _TODAS if series is None else series,
            "clientes": clientes, "catalogo": catalogo}}
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
    assert mc["conexiones"][1]["alcance"] == {"series": ["ZPAC"], "clientes": None,
                                               "catalogo": False}

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
    assert r.json()["alcance"] == {"series": ["ZPAC"], "clientes": None, "catalogo": True}
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

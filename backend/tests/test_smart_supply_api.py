"""Panel de Smart Supply — la clave por cuenta que solo LEE, y lo que lee.

Las promesas: cada cuenta (plaza) tiene SU clave y generar una no toca a las
demás; la clave solo lee (`abasto:leer`) y solo lo de su plaza —ni otra plaza
ni otro inquilino—; revocarla corta en el siguiente request; y las listas se
paginan por llave sin saltarse ni repetir líneas aunque el espejo recree una
factura entre página y página.
"""
import base64
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.api.v1.oc_recibidas import _detectar_cambio
from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.core.rbac import AuthContext
from app.main import app
from app.models import (
    Cliente, ClienteSucursal, Conexion, Factura, LineaFactura, LineaRemision, Membership,
    OCRecibida, Producto, Remision, Role, Serie, Sucursal, Tenant, User,
)

_PURGE = (
    "lineas_remision", "lineas_factura", "oc_recibidas", "remisiones", "facturas",
    "cliente_sucursales", "sucursales", "series", "conexiones", "productos", "clientes",
)

_SANDIA_PRES = {
    "KILO": {"factor": 1, "clave_sae": "SANDIAKG", "sat": "KGM"},
    "PIEZA": {"factor": 8, "clave_sae": "SANDIAPZ", "sat": "H87", "estimado": True},
}


def _utc(y, m, d, h=18):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(s):
            t = Tenant(slug=f"ss-{s}-{suffix}", legal_name=f"SS {s} SA",
                       rfc=f"S{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id); return t

        ta, tb = _tenant("a"), _tenant("b")
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()

        def _user(tenant, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=owner_role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        dueno_a = _user(ta, "ss-owner-a")
        dueno_b = _user(tb, "ss-owner-b")

        def _serie(t, codigo, tipo="FACTURA"):
            s = Serie(tenant_id=t.id, codigo=codigo, tipo_documento=tipo)
            db.add(s); db.flush(); return s

        zvh, zhos = _serie(ta, "ZEHMOVH"), _serie(ta, "ZEHMOHOS")
        rvh, rhos = _serie(ta, "RZEHMOVH", "REMISION"), _serie(ta, "RZEHMOHOS", "REMISION")
        _serie(tb, "ZEHMOVH")

        cli = Cliente(tenant_id=ta.id, codigo="EHMO", legal_name="EHMO SS", rfc="GOA180712SF5")
        cli_b = Cliente(tenant_id=tb.id, codigo="EHMO", legal_name="EHMO B", rfc="GOA180712SF5")
        sandia = Producto(tenant_id=ta.id, sku="00000100", nombre="SANDIA", clave_sat="01010101",
                          unidad_sat="KGM", unidad_base="KILO", clave_sae="SANDIAKG",
                          presentaciones=_SANDIA_PRES)
        jitomate = Producto(tenant_id=ta.id, sku="00000200", nombre="JITOMATE",
                            clave_sat="01010101", unidad_sat="KGM", unidad_base="KILO",
                            clave_sae="JITOMATEKG", presentaciones={"KILO": 1})
        db.add_all([cli, cli_b, sandia, jitomate]); db.flush()

        tab = Sucursal(tenant_id=ta.id, nombre="Tabasco")
        hgo = Sucursal(tenant_id=ta.id, nombre="Hidalgo")
        db.add_all([tab, hgo]); db.flush()
        db.add_all([
            ClienteSucursal(tenant_id=ta.id, cliente_id=cli.id, sucursal_id=tab.id,
                            serie_factura_id=zvh.id, serie_remision_id=rvh.id),
            ClienteSucursal(tenant_id=ta.id, cliente_id=cli.id, sucursal_id=hgo.id,
                            serie_factura_id=zhos.id, serie_remision_id=rhos.id),
        ])

        def _factura(serie, folio, fecha, *, estado="TIMBRADA", notas=None, tenant=ta,
                     cliente=cli, origen="ESPEJO_SAE"):
            f = Factura(tenant_id=tenant.id, serie=serie, folio=folio, cliente_id=cliente.id,
                        estado=estado, tipo_comprobante="I", uuid=str(uuid.uuid4()),
                        fecha=fecha, notas=notas, origen=origen,
                        espejo_empresa="03" if origen == "ESPEJO_SAE" else None)
            db.add(f); db.flush(); return f

        def _linea_f(f, n, *, producto=None, clave=None, pres=None, unidad="H87",
                     cantidad="1", importe="100", tenant=ta):
            db.add(LineaFactura(
                tenant_id=tenant.id, factura_id=f.id, numero_linea=n,
                producto_id=producto.id if producto else None, clave_sae=clave,
                presentacion=pres, clave_prod_serv="01010101", clave_unidad=unidad,
                descripcion=producto.nombre if producto else f"PARTIDA {clave}",
                cantidad=Decimal(cantidad), valor_unitario=Decimal("1"),
                importe=Decimal(importe), descuento=Decimal("0"),
            ))

        # Tabasco: facturada el 25 de lo entregado el 21 (la remisión lo dice).
        fac_vh1 = _factura("ZEHMOVH", 100, _utc(2026, 9, 25))
        _linea_f(fac_vh1, 1, producto=sandia, clave="SANDIAPZ", pres="PIEZA", cantidad="10",
                 importe="800")
        _linea_f(fac_vh1, 2, clave="XYZKG", cantidad="3", importe="30")    # no cruzó
        _linea_f(fac_vh1, 3, producto=jitomate, clave="JITOMATEKG", unidad="KGM",
                 cantidad="25.5", importe="510")
        # Facturada un mes después; la entrega sale de las notas.
        fac_vh2 = _factura("ZEHMOVH", 101, _utc(2026, 10, 20),
                           notas="SEMANA 39 ENTREGA 21/09/2026 EN BODEGA")
        _linea_f(fac_vh2, 1, producto=jitomate, clave="JITOMATEKG", unidad="KGM",
                 cantidad="4", importe="80")
        # Lo que NO entra para Tabasco.
        fac_cancel = _factura("ZEHMOVH", 102, _utc(2026, 9, 22), estado="CANCELADA")
        _linea_f(fac_cancel, 1, producto=jitomate, unidad="KGM", cantidad="9")
        fac_hgo = _factura("ZEHMOHOS", 200, _utc(2026, 9, 22))
        _linea_f(fac_hgo, 1, producto=jitomate, unidad="KGM", cantidad="50")
        fac_b = _factura("ZEHMOVH", 100, _utc(2026, 9, 22), tenant=tb, cliente=cli_b)
        _linea_f(fac_b, 1, unidad="KGM", cantidad="70", tenant=tb)

        def _remision(folio, serie, plaza, entrega, *, estado="BORRADOR", factura=None):
            r = Remision(tenant_id=ta.id, folio_interno=f"{folio}{suffix}",
                         cliente_facturacion_id=cli.id, sucursal_id=plaza.id, serie_id=serie.id,
                         fecha_entrega=entrega, estado=estado, subtotal=Decimal("1000"),
                         descuento=Decimal("100"), total=Decimal("900"),
                         factura_id=factura.id if factura else None, nota_entrega="HOSPITAL 1")
            db.add(r); db.flush(); return r

        def _linea_r(r, n, producto, pres, cantidad, importe="500"):
            db.add(LineaRemision(tenant_id=ta.id, remision_id=r.id, numero_linea=n,
                                 producto_id=producto.id, presentacion=pres,
                                 cantidad_solicitada=Decimal(cantidad),
                                 precio_unitario=Decimal("10"), importe=Decimal(importe)))

        rem_vh1 = _remision("RZVH1-", rvh, tab, date(2026, 9, 21), estado="FACTURADA",
                            factura=fac_vh1)
        _linea_r(rem_vh1, 1, sandia, "PIEZA", "10")
        _linea_r(rem_vh1, 2, jitomate, "KILO", "25.5")
        rem_vh2 = _remision("RZVH2-", rvh, tab, date(2026, 9, 23))
        _linea_r(rem_vh2, 1, jitomate, "KILO", "12")
        rem_cancel = _remision("RZVH3-", rvh, tab, date(2026, 9, 21), estado="CANCELADA")
        _linea_r(rem_cancel, 1, jitomate, "KILO", "99")
        rem_hgo = _remision("RZHOS1-", rhos, hgo, date(2026, 9, 21))
        _linea_r(rem_hgo, 1, jitomate, "KILO", "77")

        def _oc(origen, *, estado="ASIGNADA", remision=None, entrega=None, lineas=None,
                nuevas=None, sucursal=None, canal="WHATSAPP"):
            o = OCRecibida(
                tenant_id=ta.id, canal=canal, origen_externo=f"{origen}{suffix}",
                folio_externo=origen.split(":")[-1], estado=estado,
                remision_id=remision.id if remision else None, fecha_entrega=entrega,
                cliente_id=cli.id, sucursal_id=sucursal.id if sucursal else None,
                payload={"lineas": lineas or []},
                payload_nuevo={"lineas": nuevas} if nuevas is not None else None,
                cambio_detectado_at=_utc(2026, 9, 21) if nuevas is not None else None,
                cambio_detalle={"resumen": "2 partidas → 1"} if nuevas is not None else None,
            )
            db.add(o); db.flush(); return o

        oc_vh1 = _oc("EHMO:villahermosa:HO-1-", remision=rem_vh1, entrega=date(2026, 9, 21),
                     lineas=[{"clave": "sandiapz", "descripcion": "SANDIA", "unidad": "PZA",
                              "cantidad": "10"},
                             {"clave": "JITOMATEKG", "descripcion": "JITOMATE",
                              "unidad": "KG", "cantidad": 25.5}])
        oc_desc = _oc("EHMO:villahermosa:HO-2-", estado="DESCARTADA", entrega=date(2026, 9, 21),
                      lineas=[{"clave": "X", "cantidad": "1"}])
        oc_cambio = _oc("EHMO:villahermosa:HO-3-", estado="PENDIENTE", entrega=date(2026, 9, 22),
                        lineas=[{"clave": "A", "cantidad": "1"}, {"clave": "B", "cantidad": "2"}],
                        nuevas=[{"clave": "C", "cantidad": "7", "unidad": "KG"}])
        # Entró por un grupo de WhatsApp, pero se volvió remisión de Tabasco.
        oc_wa = _oc("WA:120363@g.us:5-", remision=rem_vh2, entrega=date(2026, 9, 23),
                    lineas=[{"clave": "JITOMATEKG", "cantidad": "12"}])
        # Lo que NO entra: otra plaza, y un perfil que solo EMPIEZA igual.
        oc_hgo = _oc("EHMO:ehmo:HO-9-", remision=rem_hgo, entrega=date(2026, 9, 21),
                     lineas=[{"clave": "JITOMATEKG", "cantidad": "77"}])
        oc_parecida = _oc("EHMO:villahermosa2:HO-4-", entrega=date(2026, 9, 21), estado="PENDIENTE",
                          lineas=[{"clave": "Z", "cantidad": "1"}])
        db.commit()
        yield {
            "dueno_a": dueno_a, "dueno_b": dueno_b, "ta": ta.id, "tb": tb.id,
            "fac_vh1": str(fac_vh1.id), "fac_vh2": str(fac_vh2.id), "fac_hgo": str(fac_hgo.id),
            "rem_vh1": str(rem_vh1.id), "rem_vh2": str(rem_vh2.id), "rem_hgo": str(rem_hgo.id),
            "oc_vh1": str(oc_vh1.id), "oc_desc": str(oc_desc.id), "oc_cambio": str(oc_cambio.id),
            "oc_wa": str(oc_wa.id), "oc_hgo": str(oc_hgo.id), "oc_parecida": str(oc_parecida.id),
            "sandia": str(sandia.id), "jitomate": str(jitomate.id),
        }
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


_TABASCO = {"plaza": "Tabasco", "series": ["ZEHMOVH"], "series_remision": ["RZEHMOVH"],
            "perfiles": ["EHMO:villahermosa"], "remisiones": True, "oc": True,
            "catalogo": True}


def _clave(client, u, *, nombre="Kelly Tabasco", **alcance):
    body = {"nombre": nombre, "alcance_panel": {**_TABASCO, **alcance}}
    r = client.post("/api/v1/conexiones/SMART_SUPPLY_PANEL/clave", headers=_hdr(u), json=body)
    assert r.status_code == 201, r.text
    return r.json()


_SEP = {"desde": "2026-09-01", "hasta": "2026-09-30"}


def _todas(client, h, ruta, params, limit=1000):
    """Recorre todas las páginas; regresa los items y cuántas páginas fueron."""
    items, paginas, despues = [], 0, None
    while True:
        p = {**params, "limit": limit}
        if despues:
            p["despues"] = despues
        r = client.get(ruta, headers=h, params=p)
        assert r.status_code == 200, r.text
        body = r.json()
        items += body["items"]
        paginas += 1
        despues = body["siguiente"]
        if not despues:
            return items, paginas


# ─── la clave ────────────────────────────────────────────────────────────────

def test_una_clave_por_cuenta_y_no_revoca_a_las_demas(client, env, auth_as):
    auth_as(env["dueno_a"])
    tab = _clave(client, env["dueno_a"])
    assert tab["clave"].startswith("fi_ss_")
    assert tab["conexion"]["tipo"] == "SMART_SUPPLY_PANEL"
    assert tab["instruccion_whatsapp"] is None
    assert tab["conexion"]["alcance"] is None                 # la forma de Mini Conta, no
    assert tab["conexion"]["alcance_panel"]["series_remision"] == ["RZEHMOVH"]
    hgo = _clave(client, env["dueno_a"], nombre="Kelly Hidalgo", plaza="Hidalgo",
                 series=["ZEHMOHOS"], series_remision=["RZEHMOHOS"], perfiles=["EHMO:ehmo"])
    # Mini Conta puede llamarse igual: es otro tipo.
    r = client.post("/api/v1/conexiones/MINI_CONTA/clave", headers=_hdr(env["dueno_a"]),
                    json={"nombre": "Kelly Tabasco", "alcance": {"series": ["ZEHMOVH"]}})
    assert r.status_code == 201, r.text

    listado = {c["tipo"]: c for c in client.get("/api/v1/conexiones",
                                                headers=_hdr(env["dueno_a"])).json()}
    panel = listado["SMART_SUPPLY_PANEL"]
    assert {c["nombre"] for c in panel["conexiones"]} == {"Kelly Tabasco", "Kelly Hidalgo"}
    assert all(c["estado"] == "PENDIENTE" for c in panel["conexiones"])
    assert listado["MINI_CONTA"]["conexiones"][0]["alcance_panel"] is None

    # Las dos sirven a la vez.
    _sin_sesion()
    for c in (tab, hgo):
        assert client.get("/api/v1/smart-supply/alcance",
                          headers=_bearer(c["clave"])).status_code == 200


def test_mismo_nombre_del_mismo_tipo_es_409(client, env, auth_as):
    auth_as(env["dueno_a"])
    _clave(client, env["dueno_a"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY_PANEL/clave", headers=_hdr(env["dueno_a"]),
                    json={"nombre": " kelly tabasco ", "alcance_panel": _TABASCO})
    assert r.status_code == 409


@pytest.mark.parametrize("cambio, detalle", [
    ({"series": []}, "serie de factura"),
    ({"series": ["ZPATITO"]}, "no son de factura"),
    ({"series_remision": ["ZEHMOVH"]}, "no son de remisión"),
    ({"perfiles": ["EHMO:villa%"]}, "Perfil inválido"),
    # Un tramo suelto es el canal de toda la empresa: abriría todas las plazas.
    ({"perfiles": ["MANUAL"]}, "Perfil inválido"),
    ({"perfiles": ["EHMO"]}, "Perfil inválido"),
    ({"plaza": None}, "escoge la plaza"),
    ({"series_remision": []}, "serie de remisión"),
    ({"series_remision": [], "perfiles": [], "remisiones": False}, "perfil o una serie"),
    ({"plaza": "Narnia"}, "No hay plaza"),
])
def test_alcance_invalido_es_422(client, env, auth_as, cambio, detalle):
    auth_as(env["dueno_a"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY_PANEL/clave", headers=_hdr(env["dueno_a"]),
                    json={"nombre": "X", "alcance_panel": {**_TABASCO, **cambio}})
    assert r.status_code == 422, r.text
    assert detalle in r.json()["detail"]


def test_sin_alcance_panel_es_422(client, env, auth_as):
    """Mandar la forma de Mini Conta no crea una clave sin límite."""
    auth_as(env["dueno_a"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY_PANEL/clave", headers=_hdr(env["dueno_a"]),
                    json={"nombre": "X", "alcance": {"series": ["ZEHMOVH"]}})
    assert r.status_code == 422


def test_solo_lee(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    r = client.get("/api/v1/conexiones/probar", headers=h)
    assert r.status_code == 200 and r.json()["permisos"] == ["abasto:leer"]
    # Nada de escribir ni de leer pantallas.
    oc = {"canal": "WHATSAPP", "origen_externo": "EHMO:villahermosa:Z", "folio_externo": "Z",
          "lineas": [{"descripcion": "JITOMATE", "cantidad": "1"}]}
    assert client.post("/api/v1/oc-recibidas", headers=h, json=oc).status_code == 403
    assert client.get("/api/v1/remisiones", headers=h).status_code == 403
    assert client.get("/api/v1/facturas", headers=h).status_code == 403
    assert client.get("/api/v1/productos", headers=h).status_code == 403
    assert client.get("/api/v1/mini-conta/alcance", headers=h).status_code == 403
    assert client.get("/api/v1/conexiones", headers=h).status_code == 403
    assert client.post(f"/api/v1/remisiones/{env['rem_vh2']}/cancelar",
                       headers=h).status_code == 403
    # Y nada de lo suyo es escritura: el router solo publica GET.
    rutas = [r for r in app.routes if getattr(r, "path", "").startswith("/api/v1/smart-supply")]
    assert rutas and all(r.methods == {"GET"} for r in rutas)


def test_todas_las_rutas_piden_abasto_leer(client, env, auth_as):
    """Recorre TODAS las rutas del router: una ruta nueva que olvide el permiso
    truena aquí, no en producción."""
    auth_as(env["dueno_a"])
    mc = client.post("/api/v1/conexiones/MINI_CONTA/clave", headers=_hdr(env["dueno_a"]),
                     json={"nombre": "Kelly", "alcance": {"series": ["ZEHMOVH"]}}).json()
    _sin_sesion()
    rutas = sorted(r.path for r in app.routes
                   if getattr(r, "path", "").startswith("/api/v1/smart-supply/"))
    assert len(rutas) == 6
    for ruta in rutas:
        r = client.get(ruta, headers=_bearer(mc["clave"]), params=_SEP)
        assert r.status_code == 403, ruta
        assert client.get(ruta, params=_SEP).status_code == 401, ruta


def test_una_persona_con_candado_por_cliente_no_lee(client, env, auth_as):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.auth_user_id == env["dueno_a"]["sub"]).one()
        m = db.query(Membership).filter(Membership.user_id == u.id).one()
        cli = db.query(Cliente).filter(Cliente.tenant_id == env["ta"]).first()
        m.cliente_scope = [cli.id]
        db.commit()
    finally:
        db.close()
    auth_as(env["dueno_a"])
    r = client.get("/api/v1/smart-supply/alcance", headers=_hdr(env["dueno_a"]))
    assert r.status_code == 403


def test_las_otras_claves_no_leen_el_panel(client, env, auth_as):
    auth_as(env["dueno_a"])
    bot = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(env["dueno_a"]))
    mc = client.post("/api/v1/conexiones/MINI_CONTA/clave", headers=_hdr(env["dueno_a"]),
                     json={"nombre": "Kelly", "alcance": {"series": ["ZEHMOVH"], "remisiones": True}})
    _sin_sesion()
    for r in (bot, mc):
        h = _bearer(r.json()["clave"])
        assert client.get("/api/v1/smart-supply/alcance", headers=h).status_code == 403
        assert client.get("/api/v1/smart-supply/facturado", headers=h,
                          params=_SEP).status_code == 403


def test_revocar_corta_en_el_acto(client, env, auth_as):
    auth_as(env["dueno_a"])
    nueva = _clave(client, env["dueno_a"])
    _sin_sesion()
    h = _bearer(nueva["clave"])
    assert client.get("/api/v1/smart-supply/facturado", headers=h, params=_SEP).status_code == 200
    auth_as(env["dueno_a"])
    r = client.post(f"/api/v1/conexiones/{nueva['conexion']['id']}/revocar",
                    headers=_hdr(env["dueno_a"]))
    assert r.status_code == 200
    _sin_sesion()
    assert client.get("/api/v1/smart-supply/facturado", headers=h, params=_SEP).status_code == 401
    assert client.get("/api/v1/smart-supply/alcance", headers=h).status_code == 401


def test_regenerar_hereda_el_alcance(client, env, auth_as):
    auth_as(env["dueno_a"])
    vieja = _clave(client, env["dueno_a"])
    r = client.post(f"/api/v1/conexiones/{vieja['conexion']['id']}/regenerar",
                    headers=_hdr(env["dueno_a"]))
    assert r.status_code == 201
    nueva = r.json()
    assert nueva["conexion"]["alcance_panel"]["series"] == ["ZEHMOVH"]
    _sin_sesion()
    assert client.get("/api/v1/smart-supply/alcance",
                      headers=_bearer(vieja["clave"])).status_code == 401
    assert client.get("/api/v1/smart-supply/alcance",
                      headers=_bearer(nueva["clave"])).status_code == 200


def test_editar_lo_que_comparte_surte_en_la_siguiente_lectura(client, env, auth_as):
    auth_as(env["dueno_a"])
    nueva = _clave(client, env["dueno_a"])
    r = client.patch(f"/api/v1/conexiones/{nueva['conexion']['id']}",
                     headers=_hdr(env["dueno_a"]),
                     json={"alcance_panel": {**_TABASCO, "remisiones": False}})
    assert r.status_code == 200, r.text
    assert r.json()["alcance_panel"]["remisiones"] is False
    _sin_sesion()
    h = _bearer(nueva["clave"])
    assert client.get("/api/v1/smart-supply/remisionado", headers=h,
                      params=_SEP).status_code == 403


def test_opciones_por_plaza(client, env, auth_as):
    auth_as(env["dueno_a"])
    r = client.get("/api/v1/conexiones/SMART_SUPPLY_PANEL/opciones", headers=_hdr(env["dueno_a"]))
    assert r.status_code == 200, r.text
    o = r.json()
    plazas = {p["nombre"]: p for p in o["plazas"]}
    assert plazas["Tabasco"]["series"] == ["ZEHMOVH"]
    assert plazas["Tabasco"]["series_remision"] == ["RZEHMOVH"]
    assert o["series"] == ["ZEHMOHOS", "ZEHMOVH"]
    assert o["series_remision"] == ["RZEHMOHOS", "RZEHMOVH"]
    # Los perfiles salen de las OC (el de la fixture lleva un sufijo en el folio).
    assert "EHMO:villahermosa" in o["perfiles"] and "EHMO:ehmo" in o["perfiles"]


def test_la_plaza_se_lleva_la_pareja_de_remision_de_sus_series(client, env, auth_as):
    """Chiapas en prod: sus vínculos solo traen serie de FACTURA y no hay
    remisiones recientes, así que al escoger la plaza no se marcaba ninguna
    serie de remisión y había que marcar las 8 a mano. La pareja R{factura}
    (la que crea `POST /series/par`) va con la plaza; una que no existe no se
    inventa, y una de remisión ajena a sus series no se cuela."""
    db = SessionLocal()
    try:
        ta = env["ta"]
        chis = Sucursal(tenant_id=ta, nombre="Chiapas")
        tg = Serie(tenant_id=ta, codigo="ZEHMOTG", tipo_documento="FACTURA")
        sur = Serie(tenant_id=ta, codigo="ZSUR", tipo_documento="FACTURA")  # sin pareja
        db.add_all([chis, tg, sur,
                    Serie(tenant_id=ta, codigo="RZEHMOTG", tipo_documento="REMISION"),
                    Serie(tenant_id=ta, codigo="RZOTRA", tipo_documento="REMISION")])
        db.flush()
        cli = db.query(Cliente).filter(Cliente.tenant_id == ta).first()
        otro = Cliente(tenant_id=ta, codigo="SUR", legal_name="SUREÑA SS", rfc="XAXX010101000")
        db.add(otro); db.flush()
        db.add_all([
            ClienteSucursal(tenant_id=ta, cliente_id=cli.id, sucursal_id=chis.id,
                            serie_factura_id=tg.id),
            ClienteSucursal(tenant_id=ta, cliente_id=otro.id, sucursal_id=chis.id,
                            serie_factura_id=sur.id),
        ])
        db.commit()
    finally:
        db.close()

    auth_as(env["dueno_a"])
    o = client.get("/api/v1/conexiones/SMART_SUPPLY_PANEL/opciones",
                   headers=_hdr(env["dueno_a"])).json()
    plazas = {p["nombre"]: p for p in o["plazas"]}
    assert plazas["Chiapas"]["series"] == ["ZEHMOTG", "ZSUR"]
    assert plazas["Chiapas"]["series_remision"] == ["RZEHMOTG"]
    # Las de antes siguen igual: el vínculo ya las traía.
    assert plazas["Tabasco"]["series_remision"] == ["RZEHMOVH"]
    assert plazas["Hidalgo"]["series_remision"] == ["RZEHMOHOS"]


# ─── lo que lee ──────────────────────────────────────────────────────────────

def test_alcance(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    a = client.get("/api/v1/smart-supply/alcance", headers=_bearer(clave)).json()
    assert a["sin_limite"] is False
    assert a["plaza"] == "Tabasco"
    assert a["series"] == ["ZEHMOVH"] and a["series_remision"] == ["RZEHMOVH"]
    assert a["perfiles"] == ["EHMO:villahermosa"]
    assert [p["nombre"] for p in a["plazas"]] == ["Tabasco"]
    assert a["conexion"]["nombre"] == "Kelly Tabasco" and len(a["conexion"]["pista"]) == 4
    assert a["max_dias"] == 93


def test_facturado_por_fecha_de_entrega(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    items, _ = _todas(client, h, "/api/v1/smart-supply/facturado",
                      {"desde": "2026-09-21", "hasta": "2026-09-21"})
    llaves = [(i["factura_id"], i["numero_linea"]) for i in items]
    assert llaves == sorted(llaves)                       # orden = la llave
    por_factura = {}
    for i in items:
        por_factura.setdefault(i["factura_id"], []).append(i)
    # Solo Tabasco, timbradas, de este inquilino; la de octubre entra por sus notas.
    assert set(por_factura) == {env["fac_vh1"], env["fac_vh2"]}
    vh2 = por_factura[env["fac_vh2"]][0]
    assert vh2["fecha_entrega"] == "2026-09-21" and vh2["fecha_entrega_origen"] == "notas"
    assert vh2["fecha_factura"] == "2026-10-20"

    l1, l2, l3 = por_factura[env["fac_vh1"]]
    assert l1["fecha_entrega_origen"] == "remision" and l1["fecha_factura"] == "2026-09-25"
    assert l1["remision_ids"] == [env["rem_vh1"]]
    assert l1["origen"] == "ESPEJO_SAE" and l1["espejo_empresa"] == "03"
    # El producto como lo guardó la línea: sin recalcular.
    assert l1["producto_id"] == env["sandia"] and l1["presentacion"] == "PIEZA"
    assert l1["unidad"] == "PIEZA" and l1["factor_kg"] == "8" and l1["kg"] == "80"
    assert l1["kg_estimado"] is True
    assert l2["producto_id"] is None and l2["clave_sae"] == "XYZKG" and l2["kg"] is None
    assert l3["unidad"] == "KILO" and l3["kg"] == "25.5" and l3["importe"] == "510.00"


def test_facturado_por_fecha_de_factura(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    items, _ = _todas(client, _bearer(clave), "/api/v1/smart-supply/facturado",
                      {**_SEP, "fecha": "factura"})
    assert {i["factura_id"] for i in items} == {env["fac_vh1"]}


def test_una_serie_fuera_del_alcance_es_403(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    r = client.get("/api/v1/smart-supply/facturado", headers=h,
                   params={**_SEP, "series": "ZEHMOHOS"})
    assert r.status_code == 403 and "ZEHMOHOS" in r.json()["detail"]
    r = client.get("/api/v1/smart-supply/remisionado", headers=h,
                   params={**_SEP, "series": "RZEHMOHOS"})
    assert r.status_code == 403
    r = client.get("/api/v1/smart-supply/facturado", headers=h,
                   params={**_SEP, "series": "ZEHMOVH"})
    assert r.status_code == 200


def test_no_ve_otra_plaza_ni_otro_inquilino(client, env, auth_as):
    """La clave de Hidalgo no ve Tabasco, y la de Tabasco de otra empresa no ve
    esta aunque sus series se llamen igual."""
    auth_as(env["dueno_a"])
    hgo = _clave(client, env["dueno_a"], nombre="Kelly Hidalgo", plaza="Hidalgo",
                 series=["ZEHMOHOS"], series_remision=["RZEHMOHOS"], perfiles=["EHMO:ehmo"])
    auth_as(env["dueno_b"])
    otra = client.post("/api/v1/conexiones/SMART_SUPPLY_PANEL/clave",
                       headers=_hdr(env["dueno_b"]),
                       json={"nombre": "Tabasco B", "alcance_panel": {
                           "series": ["ZEHMOVH"], "remisiones": False, "oc": False}})
    assert otra.status_code == 201, otra.text
    _sin_sesion()

    h = _bearer(hgo["clave"])
    fac, _ = _todas(client, h, "/api/v1/smart-supply/facturado", _SEP)
    assert {i["factura_id"] for i in fac} == {env["fac_hgo"]}
    rem, _ = _todas(client, h, "/api/v1/smart-supply/remisionado", _SEP)
    assert {i["remision_id"] for i in rem} == {env["rem_hgo"]}
    ocs, _ = _todas(client, h, "/api/v1/smart-supply/oc", {**_SEP, "campo": "entrega"})
    assert {i["id"] for i in ocs} == {env["oc_hgo"]}

    fac_b, _ = _todas(client, _bearer(otra.json()["clave"]), "/api/v1/smart-supply/facturado",
                      _SEP)
    assert len(fac_b) == 1 and fac_b[0]["cantidad"] == "70"
    assert fac_b[0]["factura_id"] not in {env["fac_vh1"], env["fac_vh2"]}


def _oc_suelta(db, tenant_id, origen, *, plaza=None):
    """Una OC PENDIENTE sin remisión, con entrega el 21-sep, en esa plaza (o sin plaza)."""
    cli = db.query(Cliente).filter(Cliente.tenant_id == tenant_id).first()
    suc = (db.query(Sucursal).filter(Sucursal.tenant_id == tenant_id,
                                     Sucursal.nombre == plaza).one() if plaza else None)
    o = OCRecibida(tenant_id=tenant_id, canal="MANUAL" if origen.startswith("MANUAL") else "WHATSAPP",
                   origen_externo=origen, folio_externo=origen.split(":")[-1][:40],
                   estado="PENDIENTE", cliente_id=cli.id, sucursal_id=suc.id if suc else None,
                   fecha_entrega=date(2026, 9, 21),
                   payload={"lineas": [{"clave": "JITOMATEKG", "cantidad": "5"}]})
    db.add(o)
    db.flush()
    return str(o.id)


def test_un_perfil_solo_abre_las_oc_de_su_plaza(client, env, auth_as):
    """«MANUAL» es el canal de captura a mano de TODA la empresa, y un grupo de
    WhatsApp puede pedir para dos plazas: ni uno ni otro le abre a la clave de
    Hidalgo las órdenes de Tabasco. Lo que aún no tiene plaza sí lo ve."""
    db = SessionLocal()
    try:
        ta = env["ta"]
        _oc_suelta(db, ta, f"MANUAL:{uuid.uuid4()}", plaza="Hidalgo")
        _oc_suelta(db, ta, f"MANUAL:{uuid.uuid4()}", plaza="Hidalgo")
        manual_tab = _oc_suelta(db, ta, f"MANUAL:{uuid.uuid4()}", plaza="Tabasco")
        # El perfil de Hidalgo (2 de 3 órdenes con plaza son suyas: la pantalla
        # se lo ofrece a Hidalgo), con una de Tabasco y otra sin plaza.
        ehmo_hgo = _oc_suelta(db, ta, f"EHMO:ehmo:HO-H-{uuid.uuid4().hex[:6]}", plaza="Hidalgo")
        ehmo_tab = _oc_suelta(db, ta, f"EHMO:ehmo:HO-T-{uuid.uuid4().hex[:6]}", plaza="Tabasco")
        ehmo_sin = _oc_suelta(db, ta, f"EHMO:ehmo:HO-N-{uuid.uuid4().hex[:6]}")
        db.commit()
    finally:
        db.close()

    auth_as(env["dueno_a"])
    op = client.get("/api/v1/conexiones/SMART_SUPPLY_PANEL/opciones",
                    headers=_hdr(env["dueno_a"])).json()
    hgo = next(p for p in op["plazas"] if p["nombre"] == "Hidalgo")
    # La pantalla ya no prellena «MANUAL»: ni en la plaza ni suelto.
    assert hgo["perfiles"] == ["EHMO:ehmo"]
    assert "MANUAL" not in op["perfiles"]
    k = _clave(client, env["dueno_a"], nombre="Kelly Hidalgo", plaza="Hidalgo",
               series=hgo["series"], series_remision=hgo["series_remision"],
               perfiles=hgo["perfiles"])
    _sin_sesion()
    h = _bearer(k["clave"])
    ocs, _ = _todas(client, h, "/api/v1/smart-supply/oc", {**_SEP, "campo": "entrega"})
    assert {i["id"] for i in ocs} == {env["oc_hgo"], ehmo_hgo, ehmo_sin}
    assert {i["plaza"] for i in ocs} == {"Hidalgo", None}
    lineas, _ = _todas(client, h, "/api/v1/smart-supply/oc-lineas", _SEP)
    assert {i["oc_id"] for i in lineas} == {env["oc_hgo"], ehmo_hgo, ehmo_sin}

    # Aunque «MANUAL» llegara guardado en la clave (por fuera de la validación),
    # no abre nada: el filtro solo usa perfiles de dos tramos.
    db = SessionLocal()
    try:
        con = db.get(Conexion, uuid.UUID(k["conexion"]["id"]))
        con.alcance = {**con.alcance, "perfiles": ["EHMO:ehmo", "MANUAL"]}
        db.commit()
    finally:
        db.close()
    ocs, _ = _todas(client, h, "/api/v1/smart-supply/oc", {**_SEP, "campo": "entrega"})
    assert {i["id"] for i in ocs} == {env["oc_hgo"], ehmo_hgo, ehmo_sin}
    assert manual_tab not in {i["id"] for i in ocs} and ehmo_tab not in {i["id"] for i in ocs}


def test_remisionado(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    items, _ = _todas(client, _bearer(clave), "/api/v1/smart-supply/remisionado", _SEP)
    assert [(i["remision_id"], i["numero_linea"]) for i in items] == sorted(
        [(env["rem_vh1"], 1), (env["rem_vh1"], 2), (env["rem_vh2"], 1)])
    por = {(i["remision_id"], i["numero_linea"]): i for i in items}
    s = por[(env["rem_vh1"], 1)]
    assert s["clave"] == "SANDIAPZ" and s["clave_producto"] == "SANDIAKG"
    assert s["unidad"] == "PIEZA" and s["kg"] == "80" and s["kg_estimado"] is True
    assert s["cantidad"] == "10" and s["cantidad_solicitada"] == "10"
    assert s["facturada"] is True and s["serie"] == "RZEHMOVH" and s["plaza"] == "Tabasco"
    assert s["punto_entrega"] == "HOSPITAL 1"
    # Descuento del encabezado (100 de 1000) prorrateado: 500 → 450.
    assert s["importe"] == "450.00"
    j = por[(env["rem_vh2"], 1)]
    assert j["clave"] == "JITOMATEKG" and j["kg"] == "12" and j["facturada"] is False


def test_oc_con_su_cadena(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    items, _ = _todas(client, _bearer(clave), "/api/v1/smart-supply/oc",
                      {**_SEP, "campo": "entrega"})
    por = {i["id"]: i for i in items}
    # Por perfil (incluida la descartada) o por serie de remisión (la de WhatsApp).
    assert set(por) == {env["oc_vh1"], env["oc_desc"], env["oc_cambio"], env["oc_wa"]}
    o = por[env["oc_vh1"]]
    assert o["perfil"] == "EHMO:villahermosa" and o["canal"] == "WHATSAPP"
    assert o["remision"]["id"] == env["rem_vh1"] and o["remision"]["serie"] == "RZEHMOVH"
    assert o["factura"]["id"] == env["fac_vh1"] and o["factura"]["origen"] == "ESPEJO_SAE"
    assert o["partidas"] == 2 and o["documento"] == "payload"
    c = por[env["oc_cambio"]]
    assert c["cambio_abierto"] is True and c["documento"] == "payload_nuevo"
    assert c["partidas"] == 1 and c["cambio_resumen"] == "2 partidas → 1"
    assert por[env["oc_desc"]]["estado"] == "DESCARTADA"
    llaves = [(i["actualizado_at"], i["id"]) for i in items]
    assert llaves == sorted(llaves)


def test_oc_actualizado_desde_trae_la_que_se_facturo_despues(client, env, auth_as):
    """La remisión de la OC de WhatsApp se factura DESPUÉS del último sondeo: la
    OC no cambió, pero su cadena sí, y el sondeo la tiene que traer."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    db = SessionLocal()
    try:
        corte = db.execute(text("SELECT now()")).scalar()
        db.commit()
        rem = db.get(Remision, uuid.UUID(env["rem_vh2"]))
        rem.factura_id = uuid.UUID(env["fac_vh2"])
        rem.estado = "FACTURADA"
        db.commit()
    finally:
        db.close()
    r = client.get("/api/v1/smart-supply/oc", headers=h,
                   params={"actualizado_desde": corte.isoformat()})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [env["oc_wa"]]
    assert items[0]["factura"]["id"] == env["fac_vh2"]


def test_oc_lineas_del_documento_vigente(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    items, _ = _todas(client, _bearer(clave), "/api/v1/smart-supply/oc-lineas", _SEP)
    por_oc = {}
    for i in items:
        por_oc.setdefault(i["oc_id"], []).append(i)
    # Sin la descartada; la del cambio abierto con la versión nueva.
    assert set(por_oc) == {env["oc_vh1"], env["oc_cambio"], env["oc_wa"]}
    a, b = por_oc[env["oc_vh1"]]
    assert (a["numero"], a["clave_doc"], a["clave"], a["cantidad"]) == (1, "sandiapz", "SANDIAPZ", "10")
    assert b["cantidad"] == "25.5" and b["unidad_doc"] == "KG"
    assert a["remision_id"] == env["rem_vh1"]
    [c] = por_oc[env["oc_cambio"]]
    assert c["documento"] == "payload_nuevo" and c["cambio_abierto"] is True
    assert c["clave"] == "C" and c["cantidad"] == "7"


def test_cambio_deshecho_vuelve_al_documento_original(client, env, auth_as):
    """El cliente corrige el pedido y luego lo deshace: el aviso se cierra solo,
    pero `payload_nuevo` se queda con la versión corregida. El pedido vigente
    es el original, no la corrección que ya no existe."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    ctx = AuthContext(user_id=None, auth_user_id="bot", email=None, tenant_id=env["ta"],
                      role_id=None, role_name="conexion", is_owner=False, permissions=set())

    def _lineas_vh1():
        items, _ = _todas(client, h, "/api/v1/smart-supply/oc-lineas", _SEP)
        return [(i["clave"], i["cantidad"], i["documento"]) for i in items
                if i["oc_id"] == env["oc_vh1"]]

    def _oc_vh1():
        items, _ = _todas(client, h, "/api/v1/smart-supply/oc", {**_SEP, "campo": "entrega"})
        return next(i for i in items if i["id"] == env["oc_vh1"])

    db = SessionLocal()
    try:
        oc = db.get(OCRecibida, uuid.UUID(env["oc_vh1"]))
        original = dict(oc.payload)
        _detectar_cambio(db, oc, {"lineas": [{"clave": "SANDIAPZ", "cantidad": "999"}]}, ctx)
        db.commit()
        # Con el cambio abierto manda la versión nueva.
        assert _lineas_vh1() == [("SANDIAPZ", "999", "payload_nuevo")]
        _detectar_cambio(db, oc, original, ctx)
        db.commit()
        assert oc.cambio_resuelto_at is not None and oc.payload_nuevo is not None
    finally:
        db.close()
    assert _lineas_vh1() == [("SANDIAPZ", "10", "payload"), ("JITOMATEKG", "25.5", "payload")]
    o = _oc_vh1()
    assert o["documento"] == "payload" and o["partidas"] == 2 and o["cambio_abierto"] is False


def test_catalogo(client, env, auth_as):
    auth_as(env["dueno_a"])
    con = _clave(client, env["dueno_a"])["clave"]
    sin = _clave(client, env["dueno_a"], nombre="Sin catálogo", catalogo=False)["clave"]
    _sin_sesion()
    assert client.get("/api/v1/smart-supply/catalogo", headers=_bearer(sin)).status_code == 403
    items, _ = _todas(client, _bearer(con), "/api/v1/smart-supply/catalogo", {})
    por_sku = {i["sku"]: i for i in items}
    assert set(por_sku) == {"00000100", "00000200"}
    s = por_sku["00000100"]
    assert s["clave_sae"] == "SANDIAKG"
    assert s["presentaciones"]["PIEZA"] == {"factor": "8", "clave_sae": "SANDIAPZ",
                                            "sat": "H87", "estimado": True}
    assert por_sku["00000200"]["presentaciones"]["KILO"]["factor"] == "1"


def test_sin_bandera_no_lee(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"], nombre="Solo facturado", series_remision=[],
                   perfiles=[], remisiones=False, oc=False, catalogo=False)["clave"]
    _sin_sesion()
    h = _bearer(clave)
    assert client.get("/api/v1/smart-supply/facturado", headers=h, params=_SEP).status_code == 200
    for ruta in ("remisionado", "oc-lineas"):
        assert client.get(f"/api/v1/smart-supply/{ruta}", headers=h,
                          params=_SEP).status_code == 403
    assert client.get("/api/v1/smart-supply/oc", headers=h, params=_SEP).status_code == 403


def test_el_dueno_lee_todo(client, env, auth_as):
    auth_as(env["dueno_a"])
    h = _hdr(env["dueno_a"])
    a = client.get("/api/v1/smart-supply/alcance", headers=h).json()
    assert a["sin_limite"] is True and a["conexion"] is None
    assert a["series"] == ["ZEHMOHOS", "ZEHMOVH"]
    items, _ = _todas(client, h, "/api/v1/smart-supply/facturado", _SEP)
    assert {i["factura_id"] for i in items} == {env["fac_vh1"], env["fac_vh2"], env["fac_hgo"]}


# ─── paginación y rangos ─────────────────────────────────────────────────────

def test_paginacion_por_llave_aguanta_que_el_espejo_recree_lineas(client, env, auth_as):
    """Entre la página 1 y la 2 el espejo borra y recrea las líneas de una
    factura (ids nuevos, mismos números). Por llave, nada se salta ni se
    repite; con offset, sí."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    completas, paginas = _todas(client, h, "/api/v1/smart-supply/facturado", _SEP, limit=1)
    assert len(completas) == 4 and paginas == 4

    r = client.get("/api/v1/smart-supply/facturado", headers=h, params={**_SEP, "limit": 2})
    primera = r.json()
    db = SessionLocal()
    try:
        fid = uuid.UUID(env["fac_vh1"])
        viejas = db.query(LineaFactura).filter(LineaFactura.factura_id == fid).all()
        copia = [dict(numero_linea=l.numero_linea, producto_id=l.producto_id,
                      clave_sae=l.clave_sae, presentacion=l.presentacion,
                      clave_unidad=l.clave_unidad, descripcion=l.descripcion,
                      cantidad=l.cantidad, importe=l.importe) for l in viejas]
        db.query(LineaFactura).filter(LineaFactura.factura_id == fid).delete()
        db.flush()
        for c in copia:
            db.add(LineaFactura(tenant_id=env["ta"], factura_id=fid, clave_prod_serv="01010101",
                                valor_unitario=Decimal("1"), **c))
        db.commit()
    finally:
        db.close()
    items = list(primera["items"])
    despues = primera["siguiente"]
    while despues:
        body = client.get("/api/v1/smart-supply/facturado", headers=h,
                          params={**_SEP, "limit": 2, "despues": despues}).json()
        items += body["items"]
        despues = body["siguiente"]
    llaves = [(i["factura_id"], i["numero_linea"]) for i in items]
    assert llaves == [(i["factura_id"], i["numero_linea"]) for i in completas]


def test_cursor_y_rango_invalidos(client, env, auth_as):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"])["clave"]
    _sin_sesion()
    h = _bearer(clave)
    r = client.get("/api/v1/smart-supply/facturado", headers=h,
                   params={**_SEP, "despues": "no-es-cursor"})
    assert r.status_code == 422

    def _b64(partes):
        return base64.urlsafe_b64encode(json.dumps(partes).encode()).decode().rstrip("=")

    # Bien codificados pero con lo que `_cursor` nunca escribe: 422, no 500.
    for ruta, partes in (("remisionado", [123, 1]), ("facturado", [123, 1]),
                         ("oc-lineas", [123, 1]), ("catalogo", [123]),
                         ("facturado", [[str(uuid.uuid4())], "1"]),
                         ("oc", [None, str(uuid.uuid4())])):
        params = {**_SEP, "despues": _b64(partes)}
        if ruta == "oc":
            params["campo"] = "entrega"
        r = client.get(f"/api/v1/smart-supply/{ruta}", headers=h, params=params)
        assert r.status_code == 422, (ruta, partes, r.status_code)
    r = client.get("/api/v1/smart-supply/facturado", headers=h,
                   params={"desde": "2026-06-01", "hasta": "2026-09-30"})
    assert r.status_code == 422 and "93" in r.json()["detail"]
    r = client.get("/api/v1/smart-supply/oc", headers=h)
    assert r.status_code == 422
    r = client.get("/api/v1/smart-supply/oc", headers=h,
                   params={"actualizado_desde": "2026-01-01T00:00:00Z"})
    assert r.status_code == 422
    r = client.get("/api/v1/smart-supply/remisionado", headers=h,
                   params={**_SEP, "limit": 5001})
    assert r.status_code == 422

"""Bandeja de OC + equivalencias de cliente.

Cubre lo que hace que la ingesta desatendida sea segura: idempotencia (un
reintento del bot no duplica), la regla de ambigüedad (dos pistas que se
contradicen NO eligen cliente), que una SUGERIDA no decida sola, el aprendizaje
al corregir desde la bandeja, y el aislamiento entre tenants.

El modelo del negocio, que estas pruebas dan por bueno: un CLIENTE es la razón
social a la que se factura (EHMO, MAFAN, Balles, Jubran); una SUCURSAL es su
operación regional (Pachuca, Tabasco) y es de donde salen serie y lista de
precios; y un PUNTO DE ENTREGA (un hospital, un plantel) es a dónde se descarga
dentro de esa sucursal. Balles y Jubran son dos razones sociales que comparten
puntos de entrega, así que un punto de entrega NUNCA identifica al cliente.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Almacen, Cliente, Membership, Producto, Role, Sucursal, Tenant, User

_PURGE = (
    "oc_recibidas", "cliente_externos", "lineas_remision", "remisiones",
    "movimientos_inventario", "lotes_inventario", "producto_alias",
    "precios", "listas_precios", "productos", "almacenes", "sucursales", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(s):
            t = Tenant(slug=f"oc-{s}-{suffix}", legal_name=f"OC {s} SA",
                       rfc=f"O{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id); return t

        tenant_a, tenant_b = _tenant("a"), _tenant("b")
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()

        def _user(tenant, role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        admin_a = _user(tenant_a, admin_role, "oc-admin-a")
        admin_b = _user(tenant_b, admin_role, "oc-admin-b")

        ehmo = Cliente(tenant_id=tenant_a.id, codigo="EH", legal_name="GRUPO EHMO",
                       rfc="GOA180712SF5")
        mafan = Cliente(tenant_id=tenant_a.id, codigo="MA", legal_name="MAFAN",
                        rfc="MCM170118UJ6")
        # Dos razones sociales que comparten puntos de entrega, serie y precios.
        balles = Cliente(tenant_id=tenant_a.id, codigo="BA", legal_name="OPERADORA BALLES",
                         rfc="OBV191007BS1")
        jubran = Cliente(tenant_id=tenant_a.id, codigo="JU", legal_name="DISTRIBUIDORA JUBRAN",
                         rfc="DAP250922PY2")
        db.add_all([ehmo, mafan, balles, jubran]); db.flush()
        # La SUCURSAL es la operación regional; el hospital es un punto DENTRO.
        suc = Sucursal(tenant_id=tenant_a.id, cliente_id=ehmo.id, codigo="TAB",
                       nombre="Tabasco")
        suc_balles = Sucursal(tenant_id=tenant_a.id, cliente_id=balles.id, codigo="HGO",
                              nombre="Hidalgo")
        suc_jubran = Sucursal(tenant_id=tenant_a.id, cliente_id=jubran.id, codigo="HGO",
                              nombre="Hidalgo")
        prod = Producto(tenant_id=tenant_a.id, sku="OC-P", nombre="Jitomate Saladet",
                        clave_sat="01010101", unidad_sat="KGM")
        alm = Almacen(tenant_id=tenant_a.id, codigo="OC-BG", nombre="Bodega OC")
        db.add_all([suc, suc_balles, suc_jubran, prod, alm]); db.flush()
        db.commit()
        yield {"admin_a": admin_a, "admin_b": admin_b,
               "ehmo": str(ehmo.id), "mafan": str(mafan.id), "suc": str(suc.id),
               "balles": str(balles.id), "jubran": str(jubran.id),
               "suc_balles": str(suc_balles.id), "suc_jubran": str(suc_jubran.id),
               "prod": str(prod.id), "alm": str(alm.id)}
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


def _oc(**over):
    body = {
        "canal": "WHATSAPP",
        "origen_externo": f"WA:grupo@g.us:{uuid.uuid4().hex[:6]}",
        "folio_externo": "1188",
        "remitente": "PEDIDOS FyV HOSPITALES",
        "rfc": "GOA180712SF5",
        "perfil": "villahermosa",
        "ubicacion": "JUAN GRAHAM",
        "lineas": [{"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG"}],
    }
    body.update(over)
    return body


def _externo(client, h, sistema, clave, cliente_id, **kw):
    body = {"sistema": sistema, "clave": clave, "cliente_id": cliente_id}
    body.update(kw)
    return client.post("/api/v1/clientes/externos", headers=h, json=body)


# ─── equivalencias ───────────────────────────────────────────────────────────

def test_resolver_por_rfc_y_sucursal_por_nombre(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"]).status_code == 201

    r = client.post("/api/v1/clientes/resolver", headers=h, json={
        "pistas": [{"sistema": "RFC", "clave": "goa 180712 sf5"}],   # sucio a propósito
        "ubicacion_texto": "Tabasco",       # el documento nombra la SUCURSAL
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cliente_id"] == env["ehmo"]
    assert body["via"] == "RFC"
    assert body["ambiguo"] is False
    assert body["sucursal_id"] == env["suc"]


def test_sugerida_no_resuelve_sola(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "NOMBRE", "BALLES", env["ehmo"],
                    origen="BOT", confianza="SUGERIDA").status_code == 201
    r = client.post("/api/v1/clientes/resolver", headers=h,
                    json={"pistas": [{"sistema": "NOMBRE", "clave": "BALLES"}]})
    assert r.json()["cliente_id"] is None


def test_reapuntar_equivalencia_es_idempotente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    a = _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["ehmo"])
    b = _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["mafan"])
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]        # misma fila, reapuntada
    listado = client.get("/api/v1/clientes/externos", headers=h,
                         params={"sistema": "PROYECTO"}).json()
    assert len(listado) == 1 and listado[0]["cliente_id"] == env["mafan"]


def test_sucursal_de_otro_cliente_rechazada(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    r = _externo(client, h, "UBICACION", "x:GRAHAM", env["mafan"], sucursal_id=env["suc"])
    assert r.status_code == 422


# ─── ingesta ────────────────────────────────────────────────────────────────

def test_ingesta_resuelve_cliente_y_guarda_el_punto_de_entrega(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc())
    assert r.status_code == 201, r.text
    oc = r.json()
    assert oc["cliente_id"] == env["ehmo"]
    assert oc["punto_entrega"] == "JUAN GRAHAM"
    # Un hospital NO es una sucursal: hasta que alguien diga a cuál pertenece,
    # el destino queda abierto y la bandeja lo pide.
    assert oc["sucursal_id"] is None
    assert "JUAN GRAHAM" in oc["motivo"]
    assert oc["estado"] == "PENDIENTE"          # aún no existe su remisión
    assert oc["ambiguo"] is False
    # El cruce de productos se sugiere al vuelo, sin persistirse.
    assert oc["lineas"][0]["candidatos"][0]["producto_id"] == env["prod"]


def test_ingesta_es_idempotente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = _oc()
    a = client.post("/api/v1/oc-recibidas", headers=h, json=body)
    b = client.post("/api/v1/oc-recibidas", headers=h, json=body)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    total = client.get("/api/v1/oc-recibidas", headers=h).json()["total"]
    assert total == 1


def test_listado_filtra_por_fecha_de_recepcion(client, env, auth_as):
    """El flujo diario es "lo que llegó hoy": el rango va sobre recibida_at y
    `fecha_hasta` es INCLUSIVO — "hasta el 28" no puede dejar fuera la tarde."""
    from datetime import date, timedelta

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    creada = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    # "hoy" según el RELOJ DEL SERVIDOR (recibida_at), no date.today(): cerca de
    # medianoche difieren y el test fallaba solo en esa ventana.
    hoy = date.fromisoformat(creada["recibida_at"][:10])
    ayer, manana = hoy - timedelta(days=1), hoy + timedelta(days=1)

    def total(**qs):
        return client.get("/api/v1/oc-recibidas", headers=h, params=qs).json()["total"]

    assert total(fecha_desde=str(hoy), fecha_hasta=str(hoy)) == 1   # inclusivo
    assert total(fecha_hasta=str(ayer)) == 0                        # antes de que llegara
    assert total(fecha_desde=str(manana)) == 0                      # todavía no existe


def test_pistas_contradictorias_no_eligen_cliente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    _externo(client, h, "PROYECTO", "ehmo:DIF", env["mafan"])

    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        perfil="ehmo", proyecto="DIF", ubicacion=None))
    oc = r.json()
    assert oc["ambiguo"] is True
    assert oc["cliente_id"] is None
    assert oc["estado"] == "PENDIENTE"
    assert "clientes distintos" in oc["motivo"]


def test_sin_pistas_conocidas_queda_pendiente(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["cliente_id"] is None and oc["estado"] == "PENDIENTE"
    assert "Ninguna pista" in oc["motivo"]


# ─── bandeja: corregir, aprender, crear la remisión ──────────────────────────

def test_asignar_manual_aprende_las_pistas(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["cliente_id"] is None

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": env["suc"], "aprender": True})
    assert r.status_code == 200, r.text
    assert r.json()["cliente_id"] == env["ehmo"]
    assert r.json()["resuelto_via"] == "MANUAL"

    # La siguiente OC igual ya no pregunta: aprendió el RFC (cliente) y que ese
    # hospital se descarga en la sucursal de Tabasco (destino).
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert otra["cliente_id"] == env["ehmo"]
    assert otra["sucursal_id"] == env["suc"]
    assert otra["punto_entrega"] == "JUAN GRAHAM"

    externos = client.get("/api/v1/clientes/externos", headers=h,
                          params={"cliente_id": env["ehmo"]}).json()
    sistemas = {e["sistema"] for e in externos}
    assert {"RFC", "UBICACION"} <= sistemas
    ubic = next(e for e in externos if e["sistema"] == "UBICACION")
    assert ubic["sucursal_id"] == env["suc"]
    assert ubic["clave"] == "villahermosa:JUAN GRAHAM"   # namespaceado por perfil


def test_crear_remision_liga_y_no_se_puede_dos_veces(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    # El hospital pertenece a la sucursal de Tabasco; eso lo dice una persona.
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"]})

    body = {"almacen_id": env["alm"], "lineas": [{
        "producto_id": env["prod"], "cantidad": "25", "precio_unitario": "18.50",
        "texto_original": "JITOMATE SALADET"}]}
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json=body)
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert hecho["estado"] == "ASIGNADA"
    assert hecho["remision_id"] and hecho["remision_folio"]

    rem = client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()
    assert rem["estado"] == "BORRADOR"
    assert rem["canal"] == "API"
    assert rem["sucursal_id"] == env["suc"]
    # El punto de entrega ENCABEZA las observaciones: es lo que el equipo lee
    # para saber a dónde llevarla, y de aquí pasa a las de la factura.
    assert (rem["notas"] or "").startswith("JUAN GRAHAM")
    assert "OC 1188" in (rem["notas"] or "")
    assert rem["nota_entrega"] == "JUAN GRAHAM"
    assert float(rem["subtotal"]) == 462.5

    # Segundo intento: la orden ya tiene remisión, no se generan dos folios.
    assert client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json=body
    ).status_code == 409


def test_crear_remision_sin_cliente_es_422(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "1"}]})
    assert r.status_code == 422
    assert "cliente" in r.json()["detail"].lower()


def test_descartar_y_reabrir(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()

    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/descartar", headers=h,
                    params={"motivo": "era cotización"})
    assert r.status_code == 200 and r.json()["estado"] == "DESCARTADA"

    # Una descartada NO se resucita por un reintento del bot.
    again = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        origen_externo=oc["origen_externo"]))
    assert again.json()["estado"] == "DESCARTADA"

    assert client.post(f"/api/v1/oc-recibidas/{oc['id']}/reabrir",
                       headers=h).json()["estado"] == "PENDIENTE"


# ─── aislamiento ────────────────────────────────────────────────────────────

def test_tenant_b_no_ve_ni_resuelve_lo_de_a(client, env, auth_as):
    auth_as(env["admin_a"]); h_a = _hdr(env["admin_a"])
    _externo(client, h_a, "RFC", "GOA180712SF5", env["ehmo"])
    oc_a = client.post("/api/v1/oc-recibidas", headers=h_a, json=_oc()).json()

    auth_as(env["admin_b"]); h_b = _hdr(env["admin_b"])
    assert client.get("/api/v1/oc-recibidas", headers=h_b).json()["total"] == 0
    assert client.get(f"/api/v1/oc-recibidas/{oc_a['id']}", headers=h_b).status_code == 404
    assert client.get("/api/v1/clientes/externos", headers=h_b).json() == []
    r = client.post("/api/v1/clientes/resolver", headers=h_b,
                    json={"pistas": [{"sistema": "RFC", "clave": "GOA180712SF5"}]})
    assert r.json()["cliente_id"] is None


# ─── reglas que salieron de la revisión ─────────────────────────────────────

def test_el_grupo_acota_pero_no_decide(client, env, auth_as):
    """Por un mismo grupo entran dos razones sociales —EHMO y MAFAN en Pachuca,
    Balles y Jubran en Hidalgo—. El grupo convierte «no sé de quién es» en «es de
    estos dos», que es lo que el operador necesita; nunca elige por su cuenta."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-compartido@g.us"
    _externo(client, h, "WHATSAPP", jid, env["balles"])
    _externo(client, h, "WHATSAPP", jid, env["jubran"])

    # El mismo grupo, dos clientes: conviven (antes el UNIQUE lo impedía).
    registrados = client.get("/api/v1/clientes/externos", headers=h,
                             params={"sistema": "WHATSAPP"}).json()
    assert {e["cliente_id"] for e in registrados} == {env["balles"], env["jubran"]}

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, ubicacion=None, jid=jid)).json()
    assert oc["cliente_id"] is None                     # no adivina
    assert oc["ambiguo"] is False                       # tampoco es un conflicto
    assert set(oc["candidatos"]) == {env["balles"], env["jubran"]}
    assert "elige cuál" in oc["motivo"]
    assert "BALLES" in oc["motivo"] and "JUBRAN" in oc["motivo"]


def test_al_asignar_se_registra_el_grupo_como_candidato(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-nuevo@g.us"
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, ubicacion=None, jid=jid)).json()
    assert oc["candidatos"] == []                       # grupo desconocido

    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["balles"], "aprender": True})

    # La próxima orden de ese grupo ya llega con la lista corta — pero sigue
    # necesitando que una persona confirme: un grupo no identifica a nadie.
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, ubicacion=None, jid=jid)).json()
    assert otra["cliente_id"] is None
    assert otra["candidatos"] == [env["balles"]]


def test_el_mismo_punto_de_entrega_sirve_a_dos_clientes(client, env, auth_as):
    """Balles y Jubran descargan en el mismo lugar, cada uno con su sucursal."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "UBICACION", "PROCU", env["balles"],
                    sucursal_id=env["suc_balles"]).status_code == 201
    assert _externo(client, h, "UBICACION", "PROCU", env["jubran"],
                    sucursal_id=env["suc_jubran"]).status_code == 201

    _externo(client, h, "NOMBRE", "JUBRAN", env["jubran"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, nombre="JUBRAN", ubicacion="PROCU")).json()
    assert oc["cliente_id"] == env["jubran"]
    assert oc["sucursal_id"] == env["suc_jubran"]       # la SUYA, no la de Balles


def test_reintento_no_pisa_la_asignacion_manual(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    body = _oc(rfc=None, ubicacion=None)          # sin pistas registrables
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=body).json()
    assert oc["cliente_id"] is None

    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": False})
    # El bot reintenta el mismo documento media hora después.
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=body).json()
    assert otra["id"] == oc["id"]
    assert otra["cliente_id"] == env["ehmo"]      # la decisión humana sobrevive


def test_descartada_no_acepta_asignacion(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    client.post(f"/api/v1/oc-recibidas/{oc['id']}/descartar", headers=h)
    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "aprender": True})
    assert r.status_code == 409
    assert client.get("/api/v1/clientes/externos", headers=h).json() == []


def test_cliente_borrado_no_resuelve(client, env, auth_as):
    """Una equivalencia huérfana dejaría la orden marcada «lista» y reventaría
    al crear la remisión; tiene que comportarse como inexistente."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    assert client.delete(f"/api/v1/clientes/{env['ehmo']}", headers=h).status_code == 204

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc["cliente_id"] is None
    assert oc["estado"] == "PENDIENTE"


def test_proyecto_sin_perfil_no_es_pista(client, env, auth_as):
    """Sin perfil la clave caería en un espacio global: 'HOSPITALES' significa
    cosas distintas en Pachuca y en Villahermosa."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "PROYECTO", "ehmo:HOSPITALES", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, ubicacion=None, perfil=None, proyecto="HOSPITALES")).json()
    assert oc["cliente_id"] is None


def test_sugerida_no_pisa_confirmada_y_avisa(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "NOMBRE", "BALLES", env["ehmo"])
    r = _externo(client, h, "NOMBRE", "BALLES", env["mafan"],
                 origen="BOT", confianza="SUGERIDA")
    assert r.status_code == 409                    # no un 201 mentiroso
    listado = client.get("/api/v1/clientes/externos", headers=h,
                         params={"sistema": "NOMBRE"}).json()
    assert listado[0]["cliente_id"] == env["ehmo"]


def test_punto_de_entrega_compartido_no_decide_el_cliente(client, env, auth_as):
    """Balles y Jubran comparten puntos de entrega. Si el punto votara por el
    cliente, toda orden de Jubran saldría «ambigua» contra Balles — o peor, se
    le facturaría a la razón social equivocada."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "NOMBRE", "BALLES", env["balles"])
    _externo(client, h, "NOMBRE", "JUBRAN", env["jubran"])
    # El mismo punto de entrega, registrado para cada razón social.
    _externo(client, h, "UBICACION", "PROCU", env["balles"], sucursal_id=env["suc_balles"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, perfil=None, nombre="JUBRAN", ubicacion="PROCU")).json()

    assert oc["ambiguo"] is False                    # el punto no contradice a nadie
    assert oc["cliente_id"] == env["jubran"]         # manda el nombre del documento
    assert oc["punto_entrega"] == "PROCU"
    # Y no se cuela la sucursal de Balles en una remisión de Jubran.
    assert oc["sucursal_id"] != env["suc_balles"]


def test_el_punto_de_entrega_se_puede_corregir_a_mano(client, env, auth_as):
    """Lo que se corrige aquí es lo que sale impreso en la remisión y la factura."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": env["suc"],
        "punto_entrega": "HOSPITAL JUAN GRAHAM (URGENCIAS)", "aprender": False})
    assert r.json()["punto_entrega"] == "HOSPITAL JUAN GRAHAM (URGENCIAS)"

    hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}],
    }).json()
    rem = client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()
    assert (rem["notas"] or "").startswith("HOSPITAL JUAN GRAHAM (URGENCIAS)")


def test_el_almacen_se_resuelve_como_la_serie(client, env, auth_as):
    """sucursal → cliente → predeterminado. El bot no puede elegir almacén, y
    dejarlo vacío significaría no descontar inventario sin que nadie lo decida."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    def _crear():
        oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
        client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "sucursal_id": env["suc"],
                           "aprender": False})
        hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
            "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}],
        }).json()
        return client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()

    # Sin nada configurado y sin predeterminado: sale sin almacén (no toca stock).
    assert _crear()["almacen_id"] is None

    # Con almacén en el CLIENTE, lo hereda.
    client.patch(f"/api/v1/clientes/{env['ehmo']}", headers=h, json={"almacen_id": env["alm"]})
    assert _crear()["almacen_id"] == env["alm"]

    # El de la SUCURSAL gana sobre el del cliente.
    r = client.post("/api/v1/almacenes", headers=h,
                    json={"codigo": "OC-SUC", "nombre": "Bodega de la sucursal"})
    alm_suc = r.json()["id"]
    client.patch(f"/api/v1/sucursales/{env['suc']}", headers=h, json={"almacen_id": alm_suc})
    assert _crear()["almacen_id"] == alm_suc


def test_la_sucursal_del_grupo_es_la_ultima_red(client, env, auth_as):
    """Un hospital que nadie ha registrado, o una orden que no dice a dónde va:
    la entrega tiene que salir de algún lado igual. Lo que diga el grupo es lo
    más cercano a la verdad sin preguntarle a nadie."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    jid = "grupo-con-destino@g.us"

    # Sin sucursal por defecto: un punto desconocido no resuelve destino.
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        jid=jid, ubicacion="HOSPITAL QUE NADIE REGISTRÓ")).json()
    assert oc["cliente_id"] == env["ehmo"] and oc["sucursal_id"] is None

    # Se le pone al grupo su sucursal por defecto para ese cliente.
    assert _externo(client, h, "WHATSAPP", jid, env["ehmo"],
                    sucursal_id=env["suc"]).status_code == 201

    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        jid=jid, ubicacion="OTRO HOSPITAL DESCONOCIDO")).json()
    assert otra["sucursal_id"] == env["suc"]
    assert otra["punto_entrega"] == "OTRO HOSPITAL DESCONOCIDO"   # el texto se conserva


def test_asignar_no_borra_la_sucursal_por_defecto_del_grupo(client, env, auth_as):
    """Aprender el grupo al asignar una orden no puede llevarse de paso su
    sucursal por defecto — el bug que motivó el centinela en `aprender`."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-conserva@g.us"
    _externo(client, h, "WHATSAPP", jid, env["ehmo"], sucursal_id=env["suc"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, ubicacion=None, jid=jid)).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": True})

    wa = next(e for e in client.get("/api/v1/clientes/externos", headers=h,
                                    params={"sistema": "WHATSAPP"}).json()
              if e["clave"] == jid)
    assert wa["sucursal_id"] == env["suc"]     # sigue ahí


def test_la_serie_del_grupo_gana_sobre_la_del_cliente(client, env, auth_as):
    """Un cliente usa varias series según la operación por la que entra el
    pedido: en SAE, EHMO factura hospitales con una y costales con otra, y el
    grupo interno de Pachuca declara tres a la vez."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])
    jid = "grupo-con-serie@g.us"

    propia = client.post("/api/v1/series", headers=h, json={
        "codigo": "GRUPO1", "tipo_documento": "REMISION", "tipo": "NO_FISCAL"}).json()

    def _remision(**over):
        oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(jid=jid, **over)).json()
        client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "aprender": False})
        hecho = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
            "lineas": [{"producto_id": env["prod"], "cantidad": "1", "precio_unitario": "10"}]}).json()
        return client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()

    # Sin serie del grupo: la resuelve como siempre (default del inquilino).
    base = _remision()
    assert not base["folio_interno"].startswith("GRUPO1")

    # Con serie del grupo, esa manda.
    _externo(client, h, "WHATSAPP", jid, env["ehmo"], serie_remision_id=propia["id"])
    assert _remision()["folio_interno"].startswith("GRUPO1")


def test_la_serie_del_grupo_sobrevive_a_aprender(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    jid = "grupo-serie-conserva@g.us"
    propia = client.post("/api/v1/series", headers=h, json={
        "codigo": "GRUPO2", "tipo_documento": "REMISION", "tipo": "NO_FISCAL"}).json()
    _externo(client, h, "WHATSAPP", jid, env["ehmo"], serie_remision_id=propia["id"])

    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(
        rfc=None, ubicacion=None, jid=jid)).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": True})

    wa = next(e for e in client.get("/api/v1/clientes/externos", headers=h,
                                    params={"sistema": "WHATSAPP"}).json()
              if e["clave"] == jid)
    assert wa["serie_remision_id"] == propia["id"]


def test_asignar_sucursal_limpia_el_motivo_viejo(client, env, auth_as):
    """El motivo es lo que la bandeja le enseña al operador: si decía «falta la
    sucursal» y la sucursal ya se asignó, dejarlo manda a revisar algo resuelto."""
    from app.core.db import SessionLocal
    from app.models import OCRecibida

    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    suc_id = env["suc"]          # sucursal de EHMO, del fixture

    db = SessionLocal()
    try:
        db.query(OCRecibida).filter(OCRecibida.id == uuid.UUID(oc["id"])).update(
            {"motivo": "Falta decir a qué sucursal pertenece «APAN»"})
        db.commit()
    finally:
        db.close()

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                     json={"cliente_id": env["ehmo"], "sucursal_id": suc_id})
    assert r.status_code == 200, r.text
    assert r.json()["motivo"] is None

    # un motivo que NO habla de la sucursal se respeta (no es la causa resuelta)
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"motivo": "revisar precios con el cliente"})
    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={"sucursal_id": suc_id})
    assert r.json()["motivo"] == "revisar precios con el cliente"

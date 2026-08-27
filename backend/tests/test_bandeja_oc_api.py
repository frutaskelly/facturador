"""Bandeja de OC + equivalencias de cliente.

Cubre lo que hace que la ingesta desatendida sea segura: idempotencia (un
reintento del bot no duplica), la regla de ambigüedad (dos pistas que se
contradicen NO eligen cliente), que una SUGERIDA no decida sola, el aprendizaje
al corregir desde la bandeja, y el aislamiento entre tenants.
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
        db.add_all([ehmo, mafan]); db.flush()
        suc = Sucursal(tenant_id=tenant_a.id, cliente_id=ehmo.id, codigo="JUA",
                       nombre="JUAN GRAHAM")
        prod = Producto(tenant_id=tenant_a.id, sku="OC-P", nombre="Jitomate Saladet",
                        clave_sat="01010101", unidad_sat="KGM")
        alm = Almacen(tenant_id=tenant_a.id, codigo="OC-BG", nombre="Bodega OC")
        db.add_all([suc, prod, alm]); db.flush()
        db.commit()
        yield {"admin_a": admin_a, "admin_b": admin_b,
               "ehmo": str(ehmo.id), "mafan": str(mafan.id), "suc": str(suc.id),
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

def test_resolver_por_rfc_y_sucursal_por_codigo(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    assert _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"]).status_code == 201

    r = client.post("/api/v1/clientes/resolver", headers=h, json={
        "pistas": [{"sistema": "RFC", "clave": "goa 180712 sf5"}],   # sucio a propósito
        "ubicacion_texto": "JUAN GRAHAM",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cliente_id"] == env["ehmo"]
    assert body["via"] == "RFC"
    assert body["ambiguo"] is False
    # La sucursal la resolvió el catálogo del cliente, sin equivalencia registrada.
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

def test_ingesta_resuelve_cliente_y_sucursal(client, env, auth_as):
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    _externo(client, h, "RFC", "GOA180712SF5", env["ehmo"])

    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc())
    assert r.status_code == 201, r.text
    oc = r.json()
    assert oc["cliente_id"] == env["ehmo"]
    assert oc["sucursal_id"] == env["suc"]
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

    # La siguiente OC igual ya no pregunta: aprendió RFC y UBICACION.
    otra = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert otra["cliente_id"] == env["ehmo"]
    assert otra["sucursal_id"] == env["suc"]

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
    assert "OC 1188" in (rem["notas"] or "")
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

def test_el_jid_se_aprende_solo_como_sugerido(client, env, auth_as):
    """El grupo es la pista más débil: por él entran EHMO y MAFAN. Una corrección
    no puede volverlo decisorio, o asignaría en silencio las del otro cliente."""
    auth_as(env["admin_a"]); h = _hdr(env["admin_a"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(jid="grupo@g.us")).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"], "aprender": True})

    externos = client.get("/api/v1/clientes/externos", headers=h).json()
    wa = next(e for e in externos if e["sistema"] == "WHATSAPP")
    assert wa["confianza"] == "SUGERIDA"
    rfc = next(e for e in externos if e["sistema"] == "RFC")
    assert rfc["confianza"] == "CONFIRMADA"

    # Y por lo mismo, el JID solo no resuelve nada todavía.
    sola = client.post("/api/v1/oc-recibidas", headers=h, json={
        "canal": "WHATSAPP", "origen_externo": "WA:grupo@g.us:otra",
        "jid": "grupo@g.us", "lineas": [{"descripcion": "X", "cantidad": "1"}]}).json()
    assert sola["cliente_id"] is None


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

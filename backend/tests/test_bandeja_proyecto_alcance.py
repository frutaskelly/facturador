"""La negociación es de su plaza (regla de alcance proyecto↔sucursal).

El caso que motivó todo (31-ago-2026): una OC de EHMO Villahermosa entró
etiquetada con el proyecto HOSPITALES de Pachuca y cobró la lista de la otra
plaza. La regla: un proyecto con sucursales asignadas (`proyecto_sucursales`,
0058) solo cruza —en la ingesta, en el PATCH de la bandeja y en las
asignaciones de precios— cuando la sucursal resuelta está en su alcance. Sin
sucursales asignadas no hay restricción: es el comportamiento de siempre y lo
que hace retrocompatible el aterrizaje.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from .conftest import crear_sucursal
from app.models import Cliente, Membership, Producto, Role, Tenant, User

_PURGE = (
    "oc_recibidas", "cliente_externos", "lineas_remision", "remisiones",
    "lista_asignaciones", "precios", "listas_precios", "producto_alias",
    "productos", "proyectos", "cliente_sucursales", "sucursales", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"alc-{suffix}", legal_name="Alcance SA",
                   rfc=f"ALC{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-alc-{suffix}"
        u = User(email=f"alc-{suffix}@t.test", auth_user_id=sub, full_name="alc")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        admin = {"sub": sub, "email": u.email, "tenant_id": t.id}

        ehmo = Cliente(tenant_id=t.id, codigo="EH", legal_name="GRUPO EHMO",
                       rfc="GOA180712SF5")
        db.add(ehmo); db.flush()
        # Las dos plazas del caso real: la negociación de una no cobra en la otra.
        tabasco = crear_sucursal(db, tenant_id=t.id, cliente_id=ehmo.id, codigo="TAB", nombre="Tabasco")
        pachuca = crear_sucursal(db, tenant_id=t.id, cliente_id=ehmo.id, codigo="PAC", nombre="Pachuca")
        prod = Producto(tenant_id=t.id, sku="ALC-P", nombre="Jitomate Saladet",
                        clave_sat="01010101", unidad_sat="KGM")
        db.add(prod); db.flush()
        db.commit()
        yield {"admin": admin, "ehmo": str(ehmo.id),
               "tabasco": str(tabasco.id), "pachuca": str(pachuca.id),
               "prod": str(prod.id)}
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
        "proyecto": "HOSPITALES",
        "ubicacion": "Tabasco",
        "lineas": [{"descripcion": "JITOMATE SALADET", "cantidad": "25", "unidad": "KG"}],
    }
    body.update(over)
    return body


def _setup(client, env, *, sucursal_id=None, nombre="HOSPITALES VH"):
    """RFC→EHMO + un proyecto (con el alcance dado) + la equivalencia PROYECTO
    aprendida corrigiendo UNA orden desde la bandeja — el flujo real."""
    h = _hdr(env["admin"])
    r = client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "GOA180712SF5", "cliente_id": env["ehmo"]})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": nombre, "cliente_id": env["ehmo"],
        "sucursal_id": sucursal_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _aprender(client, h, oc_id, *, cliente, sucursal, proyecto):
    r = client.patch(f"/api/v1/oc-recibidas/{oc_id}", headers=h, json={
        "cliente_id": cliente, "sucursal_id": sucursal,
        "proyecto_id": proyecto, "aprender": True})
    assert r.status_code == 200, r.text
    return r.json()


# ─── ingesta ─────────────────────────────────────────────────────────────────

def test_ingesta_estampa_proyecto_sin_restriccion(client, env, auth_as):
    """Regresión: un proyecto sin plaza asignada cruza como siempre."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy = _setup(client, env, sucursal_id=None)
    oc1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    _aprender(client, h, oc1["id"], cliente=env["ehmo"], sucursal=env["tabasco"], proyecto=proy)

    oc2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc2["proyecto_id"] == proy
    assert oc2["sucursal_id"] == env["tabasco"]


def test_ingesta_estampa_cuando_la_sucursal_esta_en_su_alcance(client, env, auth_as):
    """El reorden importa: la sucursal se resuelve ANTES que el proyecto. Si el
    proyecto se evaluara primero (sucursal aún None), este restringido a
    Tabasco no cruzaría nunca."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy = _setup(client, env, sucursal_id=env["tabasco"])
    oc1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    _aprender(client, h, oc1["id"], cliente=env["ehmo"], sucursal=env["tabasco"], proyecto=proy)

    oc2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc2["sucursal_id"] == env["tabasco"]
    assert oc2["proyecto_id"] == proy


def test_ingesta_no_estampa_fuera_de_plaza(client, env, auth_as):
    """El caso VH-35COM-MAR: la equivalencia apunta a la negociación de OTRA
    plaza. La orden entra sin proyecto (y por lo tanto sin sus precios)."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy = _setup(client, env, sucursal_id=env["pachuca"], nombre="HOSPITALES PAC")
    oc1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(ubicacion="Pachuca")).json()
    _aprender(client, h, oc1["id"], cliente=env["ehmo"], sucursal=env["pachuca"], proyecto=proy)

    oc2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(ubicacion="Tabasco")).json()
    assert oc2["sucursal_id"] == env["tabasco"]
    assert oc2["proyecto_id"] is None


# ─── PATCH de la bandeja ─────────────────────────────────────────────────────

def test_patch_rechaza_el_par_incompatible(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy = _setup(client, env, sucursal_id=env["pachuca"], nombre="HOSPITALES PAC")
    oc = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": env["tabasco"],
        "proyecto_id": proy, "aprender": False})
    assert r.status_code == 422
    assert "no entrega" in r.json()["detail"]

    # Restringido y sin sucursal tampoco: sin plaza no se puede afirmar nada.
    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": None,
        "proyecto_id": proy, "aprender": False})
    assert r.status_code == 422

    r = client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h, json={
        "cliente_id": env["ehmo"], "sucursal_id": env["pachuca"],
        "proyecto_id": proy, "aprender": False})
    assert r.status_code == 200, r.text
    assert r.json()["proyecto_id"] == proy


def test_aprender_reapunta_la_equivalencia(client, env, auth_as):
    """La auto-reparación: la orden entra sin proyecto (equivalencia de otra
    plaza), el operador elige el bueno, y la siguiente ya entra etiquetada."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy_pac = _setup(client, env, sucursal_id=env["pachuca"], nombre="HOSPITALES PAC")
    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "HOSPITALES VH", "cliente_id": env["ehmo"],
        "sucursal_id": env["tabasco"]})
    proy_vh = r.json()["id"]
    oc1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc(ubicacion="Pachuca")).json()
    _aprender(client, h, oc1["id"], cliente=env["ehmo"], sucursal=env["pachuca"], proyecto=proy_pac)

    oc2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc2["proyecto_id"] is None
    _aprender(client, h, oc2["id"], cliente=env["ehmo"], sucursal=env["tabasco"], proyecto=proy_vh)

    oc3 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc3["proyecto_id"] == proy_vh


# ─── órdenes ya estampadas cuando el alcance cambia después ──────────────────

def test_auto_bloquea_la_orden_con_par_incompatible(client, env, auth_as):
    """Una orden estampada ANTES de restringir el proyecto no puede volverse
    remisión de un clic: el guard la detiene con el motivo escrito."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy = _setup(client, env, sucursal_id=None)
    oc1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    _aprender(client, h, oc1["id"], cliente=env["ehmo"], sucursal=env["tabasco"], proyecto=proy)
    oc2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc2["proyecto_id"] == proy

    r = client.patch(f"/api/v1/proyectos/{proy}", headers=h,
                     json={"sucursal_id": env["pachuca"]})
    assert r.status_code == 200, r.text

    d = client.get(f"/api/v1/oc-recibidas/{oc2['id']}", headers=h).json()
    assert d["auto"]["ok"] is False
    assert "no entrega en su sucursal" in d["auto"]["motivo"]


def test_reabrir_despega_el_proyecto_que_dejo_de_aplicar(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy = _setup(client, env, sucursal_id=env["tabasco"])
    oc1 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    _aprender(client, h, oc1["id"], cliente=env["ehmo"], sucursal=env["tabasco"], proyecto=proy)
    oc2 = client.post("/api/v1/oc-recibidas", headers=h, json=_oc()).json()
    assert oc2["proyecto_id"] == proy

    client.patch(f"/api/v1/proyectos/{proy}", headers=h,
                 json={"sucursal_id": env["pachuca"]})
    r = client.post(f"/api/v1/oc-recibidas/{oc2['id']}/reabrir", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["proyecto_id"] is None


# ─── asignaciones de precios ─────────────────────────────────────────────────

def test_asignacion_de_precios_respeta_el_alcance(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    proy_pac = _setup(client, env, sucursal_id=env["pachuca"], nombre="HOSPITALES PAC")
    r = client.post("/api/v1/listas-precios", headers=h,
                    json={"codigo": "ALCVH", "nombre": "Lista VH"})
    assert r.status_code == 201, r.text
    lista = r.json()["id"]

    # El renglón que habría evitado el caso real: proyecto de Pachuca +
    # sucursal Tabasco no puede aplicar nunca.
    r = client.post("/api/v1/asignaciones-precios", headers=h, json={
        "lista_id": lista, "cliente_id": env["ehmo"],
        "sucursal_id": env["tabasco"], "proyecto_id": proy_pac})
    assert r.status_code == 422
    assert "no entrega" in r.json()["detail"]

    # En su plaza sí; y un renglón de proyecto sin sucursal sigue siendo
    # legítimo (así vive la negociación de una sola plaza).
    r = client.post("/api/v1/asignaciones-precios", headers=h, json={
        "lista_id": lista, "cliente_id": env["ehmo"],
        "sucursal_id": env["pachuca"], "proyecto_id": proy_pac})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/asignaciones-precios", headers=h, json={
        "lista_id": lista, "cliente_id": env["ehmo"], "proyecto_id": proy_pac})
    assert r.status_code == 201, r.text

"""Buzón de tickets — el «necesito una mano» del bot, resoluble desde aquí.

Lo que se prueba es lo que hace SEGURO el ir y venir con el bot: el depósito
es idempotente, una decisión humana no la deshace un reintento del bot, y una
acción pedida desde la pantalla se reclama UNA sola vez (un reproceso doble
registraría el pedido dos veces).
"""
import base64
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Membership, Role, Tenant, User


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"tk-{suffix}", legal_name="Tickets SA",
                   rfc=f"TK{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)

        def _user(nombre_rol, label):
            role = db.query(Role).filter(Role.nombre == nombre_rol, Role.es_preset.is_(True)).one()
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=t.id, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": t.id}

        admin = _user("ADMIN", "admin")
        portal = _user("PORTAL CLIENTE", "portal")
        db.commit()
        yield {"admin": admin, "portal": portal, "tenant_id": t.id}
    finally:
        for tid in created["tenants"]:
            db.execute(text("DELETE FROM tickets WHERE tenant_id = :tid"), {"tid": tid})
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


FOTO = base64.b64encode(b"\xff\xd8\xff\xe0 foto de prueba").decode()


def _depositar(client, h, **extra):
    body = {"numero": 1, "origen_externo": "WA:foto_1.jpg", "estado": "ABIERTO",
            "perfil": "villahermosa", "grupo": "PEDIDOS FyV HOSPITALES TABASCO",
            "archivo_nombre": "foto_1.jpg", "nota": "ALBERGUE PALENQUE, MIERCOLES 23/09/2026",
            "tipo": "reemplazo_sospechoso",
            "que_paso": "PALENQUE miércoles ya tiene 20 productos y esta foto trae 1",
            "acciones": ["EXTRA", "CERRAR"], "foto_b64": FOTO}
    body.update(extra)
    return client.post("/api/v1/tickets", headers=h, json=body)


def test_deposito_idempotente_con_foto(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = _depositar(client, h)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["numero"] == 1 and t["estado"] == "ABIERTO" and t["tiene_foto"]
    # el reintento del bot actualiza el MISMO ticket
    r2 = _depositar(client, h, que_paso="otra redacción", foto_b64=None)
    assert r2.status_code == 200 and r2.json()["id"] == t["id"]
    assert r2.json()["tiene_foto"]          # sin foto nueva, se conserva la que había
    f = client.get(f"/api/v1/tickets/{t['id']}/foto", headers=h)
    assert f.status_code == 200 and f.content.startswith(b"\xff\xd8")
    # dos casos distintos no comparten número
    assert _depositar(client, h, origen_externo="WA:otra.jpg").status_code == 409


def test_pedir_extra_se_reclama_una_sola_vez(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    t = _depositar(client, h).json()
    r = client.post(f"/api/v1/tickets/{t['id']}/accion", headers=h,
                    json={"accion": "EXTRA", "nota": "es el faltante del miércoles"})
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "EN_CURSO"
    # otra acción mientras la primera espera al bot: no
    assert client.post(f"/api/v1/tickets/{t['id']}/accion", headers=h,
                       json={"accion": "CERRAR"}).status_code == 409

    pend = client.get("/api/v1/tickets/acciones/pendientes", headers=h).json()
    assert [(p["numero"], p["accion"], p["nota"]) for p in pend] == [(1, "EXTRA", "es el faltante del miércoles")]
    # reclamada: la segunda pasada del bot ya no la ve
    assert client.get("/api/v1/tickets/acciones/pendientes", headers=h).json() == []

    # un ABIERTO rezagado ANTES de que el bot la tome no la revive… pero ya la tomó,
    # así que si el reproceso no salió, el ticket vuelve a ABIERTO
    assert _depositar(client, h).json()["estado"] == "ABIERTO"

    # y el RESUELTO con la OC lo cierra
    fin = _depositar(client, h, estado="RESUELTO", resolucion="VH-39ALB-MIE").json()
    assert fin["estado"] == "RESUELTO" and fin["resolucion"] == "VH-39ALB-MIE"
    # terminal es terminal: un ABIERTO tardío no lo reabre
    assert _depositar(client, h).json()["estado"] == "RESUELTO"


def test_en_curso_se_sostiene_hasta_que_el_bot_la_tome(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    t = _depositar(client, h).json()
    client.post(f"/api/v1/tickets/{t['id']}/accion", headers=h, json={"accion": "EXTRA"})
    assert _depositar(client, h).json()["estado"] == "EN_CURSO"
    # el ack negativo lo regresa a ABIERTO para que alguien más decida
    client.get("/api/v1/tickets/acciones/pendientes", headers=h)
    r = client.post(f"/api/v1/tickets/{t['id']}/ack", headers=h,
                    json={"ok": False, "detalle": "ya no tengo la foto"})
    assert r.json()["estado"] == "ABIERTO" and r.json()["accion_pedida"] is None


def test_cerrar_desde_la_pantalla_avisa_al_bot(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    t = _depositar(client, h, acciones=["CERRAR"]).json()
    # EXTRA no aplica a este caso
    assert client.post(f"/api/v1/tickets/{t['id']}/accion", headers=h,
                       json={"accion": "EXTRA"}).status_code == 422
    r = client.post(f"/api/v1/tickets/{t['id']}/accion", headers=h, json={"accion": "CERRAR"})
    assert r.json()["estado"] == "CERRADO"
    pend = client.get("/api/v1/tickets/acciones/pendientes", headers=h).json()
    assert [p["accion"] for p in pend] == ["CERRAR"]
    d = client.get(f"/api/v1/tickets/{t['id']}", headers=h).json()
    assert any("cerró" in e["texto"] for e in d["eventos"])


def test_lista_resumen_y_comentarios(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    t = _depositar(client, h).json()
    _depositar(client, h, numero=2, origen_externo="WA:foto_2.jpg", estado="RESUELTO")
    assert client.get("/api/v1/tickets/resumen", headers=h).json() == {"abiertos": 1}
    lista = client.get("/api/v1/tickets?estado=ABIERTO,EN_CURSO", headers=h).json()
    assert [x["numero"] for x in lista["items"]] == [1]
    assert [x["numero"] for x in client.get("/api/v1/tickets?q=%232", headers=h).json()["items"]] == [2]
    c = client.post(f"/api/v1/tickets/{t['id']}/comentarios", headers=h, json={"texto": "lo veo yo"})
    assert c.json()["eventos"][-1]["texto"] == "lo veo yo"


def test_portal_de_cliente_no_ve_el_buzon(client, env, auth_as):
    auth_as(env["portal"]); h = _hdr(env["portal"])
    assert client.get("/api/v1/tickets", headers=h).status_code == 403

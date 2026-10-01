"""La plaza del proyecto (`proyectos.sucursal_id`, rediseño 01-sep-2026).

Un proyecto por plaza: «HOSPITALES» de Pachuca y de Tabasco son dos filas. La
regla de negocio: si el proyecto tiene dueño (`cliente_id`), su plaza debe
estar VINCULADA a ese cliente — un proyecto en una plaza que no lo surte
cobraría con la negociación equivocada. Un proyecto del grupo (sin dueño)
acepta cualquier plaza. Todo por la API real (RLS + RBAC), como
test_catalog_api.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Cliente, ListaPrecios, Membership, Role, Tenant, User
from .conftest import crear_sucursal

_PURGE = ("lista_asignaciones", "listas_precios", "proyectos", "cliente_sucursales", "sucursales", "clientes")


@pytest.fixture
def env(db_engine):
    """Un tenant con ADMIN, dos clientes y sus plazas vinculadas."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(
            slug=f"proy-{suffix}", legal_name="Proy SA", rfc=f"P{suffix.upper()}X"[:13],
            regimen_fiscal_sat="601", domicilio_fiscal_cp="44100",
            tier="PRINCIPAL", status="ACTIVE",
        )
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        tid = tenant.id

        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-proy-{suffix}"
        u = User(email=f"proy-{suffix}@t.test", auth_user_id=sub, full_name="Admin Proy")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=tid, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)

        cli1 = Cliente(tenant_id=tid, codigo="C1", legal_name="Cliente 1 SA", rfc="XAXX010101000")
        cli2 = Cliente(tenant_id=tid, codigo="C2", legal_name="Cliente 2 SA", rfc="XEXX010101000")
        db.add_all([cli1, cli2]); db.flush()

        # cli1 se surte de Pachuca y Tulancingo; cli2 solo de Actopan.
        s1a = crear_sucursal(db, tenant_id=tid, cliente_id=cli1.id, nombre="Pachuca")
        s1b = crear_sucursal(db, tenant_id=tid, cliente_id=cli1.id, nombre="Tulancingo")
        s2a = crear_sucursal(db, tenant_id=tid, cliente_id=cli2.id, nombre="Actopan")
        lista_a = ListaPrecios(tenant_id=tid, codigo="LA", nombre="Lista A")
        lista_b = ListaPrecios(tenant_id=tid, codigo="LB", nombre="Lista B")
        db.add_all([lista_a, lista_b])
        db.commit()

        yield {
            "tenant": tid,
            "admin": {"sub": sub, "email": u.email, "tenant_id": tid},
            "cli1": str(cli1.id), "cli2": str(cli2.id),
            "s1a": str(s1a.id), "s1b": str(s1b.id), "s2a": str(s2a.id),
            "lista_a": str(lista_a.id), "lista_b": str(lista_b.id),
        }
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
        db.commit()
        db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"],
            email=user["email"],
            role="authenticated",
            claims={"sub": user["sub"]},
        )
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(user):
    return {"X-Tenant-Id": str(user["tenant_id"])}


def test_create_con_plaza_del_cliente(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Hospitales", "cliente_id": env["cli1"],
        "sucursal_id": env["s1a"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sucursal_id"] == env["s1a"]
    assert body["sucursal_nombre"] == "Pachuca"

    # La lista también la trae (hidratación en paginado).
    r = client.get("/api/v1/proyectos", headers=_hdr(env["admin"]))
    fila = next(p for p in r.json()["items"] if p["id"] == body["id"])
    assert fila["sucursal_nombre"] == "Pachuca"


def test_un_proyecto_por_plaza_caso_ehmo(client, env, auth_as):
    """El mismo nombre en dos plazas son DOS proyectos (códigos distintos)."""
    auth_as(env["admin"])
    r1 = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Hospitales", "cliente_id": env["cli1"], "sucursal_id": env["s1a"],
    })
    r2 = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Hospitales", "cliente_id": env["cli1"], "sucursal_id": env["s1b"],
    })
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    assert r1.json()["codigo"] != r2.json()["codigo"]
    assert {r1.json()["sucursal_nombre"], r2.json()["sucursal_nombre"]} == {"Pachuca", "Tulancingo"}


def test_create_rechaza_plaza_que_no_surte_al_cliente(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Cruzado", "cliente_id": env["cli1"],
        "sucursal_id": env["s2a"],
    })
    assert r.status_code == 422
    assert "no se surte" in r.json()["detail"]


def test_create_rechaza_plaza_inexistente(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Fantasma", "cliente_id": env["cli1"],
        "sucursal_id": str(uuid.uuid4()),
    })
    assert r.status_code == 422
    assert "no existe" in r.json()["detail"]


def test_proyecto_del_grupo_acepta_cualquier_plaza(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Programa Estatal", "cliente_id": None,
        "sucursal_id": env["s2a"],
    })
    assert r.status_code == 201, r.text
    assert r.json()["sucursal_nombre"] == "Actopan"


def test_update_cambia_y_omitir_no_toca(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Rotativo", "cliente_id": env["cli1"],
        "sucursal_id": env["s1a"],
    })
    pid = r.json()["id"]

    # PATCH sin sucursal_id: la plaza queda como estaba.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"notas": "sin tocar la plaza"})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_id"] == env["s1a"]

    # PATCH con otra plaza: la reemplaza.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"sucursal_id": env["s1b"]})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_nombre"] == "Tulancingo"

    # NULL explícito = sin restricción de plaza.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"sucursal_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_id"] is None


def test_cambiar_dueno_valida_la_plaza(client, env, auth_as):
    auth_as(env["admin"])
    r = client.post("/api/v1/proyectos", headers=_hdr(env["admin"]), json={
        "nombre": "Mudanza", "cliente_id": env["cli1"],
        "sucursal_id": env["s1a"],
    })
    pid = r.json()["id"]

    # Cambiar el dueño sin retocar la plaza → 422 (el nuevo no se surte de ella).
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"cliente_id": env["cli2"]})
    assert r.status_code == 422
    assert "no se surte" in r.json()["detail"]

    # Con la plaza correcta en el mismo PATCH sí pasa.
    r = client.patch(f"/api/v1/proyectos/{pid}", headers=_hdr(env["admin"]),
                     json={"cliente_id": env["cli2"], "sucursal_id": env["s2a"]})
    assert r.status_code == 200, r.text
    assert r.json()["sucursal_nombre"] == "Actopan"


def test_correos_facturas_se_guardan_normalizados_y_validados(client, env, auth_as):
    """Ticket 86bbyveu1: los correos destinatarios del proyecto se validan al
    guardar (un typo aquí viaja a todos los envíos), se normalizan, y un PATCH
    que no los menciona no los toca."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Correos Hospitales", "cliente_id": env["cli1"],
        "correos_facturas": ["  Pagos@Hospital.MX ", "cxc@hospital.mx", ""],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    # normalizados (dominio en minúsculas, sin espacios) y sin vacíos
    assert body["correos_facturas"] == ["Pagos@hospital.mx", "cxc@hospital.mx"]

    # un correo inválido se rechaza con 422
    bad = client.patch(f"/api/v1/proyectos/{body['id']}", headers=h,
                       json={"correos_facturas": ["esto-no-es-correo"]})
    assert bad.status_code == 422

    # PATCH de otra cosa no toca los correos
    p = client.patch(f"/api/v1/proyectos/{body['id']}", headers=h, json={"notas": "x"})
    assert p.status_code == 200, p.text
    assert p.json()["correos_facturas"] == ["Pagos@hospital.mx", "cxc@hospital.mx"]

    # y se pueden vaciar explícitamente
    v = client.patch(f"/api/v1/proyectos/{body['id']}", headers=h, json={"correos_facturas": []})
    assert v.status_code == 200 and v.json()["correos_facturas"] == []


# ─── La ficha del proyecto manda (migr 0094, 1-oct-2026) ────────────────────

def test_lista_de_precios_desde_la_ficha(client, env, auth_as):
    """La lista se escoge en la ficha y queda como su renglón de asignación:
    ya no hace falta ir a «Asignación de precios»."""
    auth_as(env["admin"])
    h = _hdr(env["admin"])
    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Hospitales Tuxtla", "cliente_id": env["cli1"], "lista_id": env["lista_a"],
    })
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["lista_id"] == env["lista_a"] and p["lista_nombre"] == "Lista A"
    assert p["codigo"] == "HOSPITALESTUXTLA"

    r = client.patch(f"/api/v1/proyectos/{p['id']}", headers=h, json={"lista_id": env["lista_b"]})
    assert r.status_code == 200, r.text
    assert r.json()["lista_nombre"] == "Lista B"
    db = SessionLocal()
    try:
        n = db.execute(text("SELECT count(*) FROM lista_asignaciones WHERE proyecto_id = :p"),
                       {"p": p["id"]}).scalar()
    finally:
        db.close()
    assert n == 1                                 # se cambió, no se duplicó

    r = client.patch(f"/api/v1/proyectos/{p['id']}", headers=h, json={"lista_id": None})
    assert r.json()["lista_id"] is None


def test_series_normalizadas_y_sin_choque(client, env, auth_as):
    """Una serie es de un proyecto; si dos la comparten, sólo uno puede quedarse
    sin palabras de la observación (el que se lleva el resto)."""
    auth_as(env["admin"])
    h = _hdr(env["admin"])
    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Ceresos", "series": [" zmafan ", "ZMAFAN"],
    })
    assert r.status_code == 201, r.text
    assert r.json()["series"] == ["ZMAFAN"]

    r = client.post("/api/v1/proyectos", headers=h, json={"nombre": "Otro", "series": ["ZMAFAN"]})
    assert r.status_code == 409 and "Ceresos" in r.json()["detail"]

    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Dif Hidalgo", "series": ["ZMAFAN"], "palabras_obs": ["dif", "costales"],
    })
    assert r.status_code == 201, r.text
    assert r.json()["palabras_obs"] == ["DIF", "COSTALES"]


def test_se_reporta_en_un_solo_nivel(client, env, auth_as):
    auth_as(env["admin"])
    h = _hdr(env["admin"])
    ceresos = client.post("/api/v1/proyectos", headers=h, json={"nombre": "Ceresos"}).json()
    neri = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Secretario Neri", "reporta_en_id": ceresos["id"],
    })
    assert neri.status_code == 201, neri.text
    assert neri.json()["reporta_en_nombre"] == "Ceresos"

    # Ni a sí mismo, ni a uno que ya se reporta en otro, ni el padre hacia abajo.
    r = client.patch(f"/api/v1/proyectos/{ceresos['id']}", headers=h,
                     json={"reporta_en_id": ceresos["id"]})
    assert r.status_code == 422
    r = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Nieto", "reporta_en_id": neri.json()["id"],
    })
    assert r.status_code == 422
    otro = client.post("/api/v1/proyectos", headers=h, json={"nombre": "Otro"}).json()
    r = client.patch(f"/api/v1/proyectos/{ceresos['id']}", headers=h,
                     json={"reporta_en_id": otro["id"]})
    assert r.status_code == 422


# ─── La lista se escoge donde vive la negociación (fase 3, 1-oct-2026) ──────

def _renglones_de(cliente_id):
    db = SessionLocal()
    try:
        return db.execute(text(
            "SELECT sucursal_id, proyecto_id, lista_id FROM lista_asignaciones "
            "WHERE cliente_id = :c ORDER BY sucursal_id NULLS FIRST"), {"c": cliente_id}).all()
    finally:
        db.close()


def test_lista_del_cliente_en_una_plaza(client, env, auth_as):
    """Sucursales y precios: la lista del cliente EN esa plaza vive en su
    vínculo, sin pasar por «Asignación de precios»."""
    auth_as(env["admin"])
    h = _hdr(env["admin"])
    url = f"/api/v1/sucursales/{env['s1a']}/clientes/{env['cli1']}"
    r = client.put(url, headers=h, json={"lista_id": env["lista_a"]})
    assert r.status_code == 200, r.text
    assert r.json()["lista_nombre"] == "Lista A"

    # Editar sólo las series no toca la lista.
    r = client.put(url, headers=h, json={"es_default": True})
    assert r.json()["lista_nombre"] == "Lista A"
    r = client.put(url, headers=h, json={"lista_id": env["lista_b"]})
    assert r.json()["lista_nombre"] == "Lista B"
    assert len(_renglones_de(env["cli1"])) == 1          # se cambió, no se duplicó

    vincs = client.get(f"/api/v1/sucursales/{env['s1a']}/clientes", headers=h).json()
    assert next(v for v in vincs if v["cliente_id"] == env["cli1"])["lista_nombre"] == "Lista B"

    r = client.put(url, headers=h, json={"lista_id": None})
    assert r.json()["lista_id"] is None
    assert _renglones_de(env["cli1"]) == []


def test_lista_del_cliente_en_cualquier_plaza(client, env, auth_as):
    """La ficha del cliente: su lista en cualquier plaza. No se confunde con
    la de un vínculo (otro renglón)."""
    auth_as(env["admin"])
    h = _hdr(env["admin"])
    client.put(f"/api/v1/sucursales/{env['s1a']}/clientes/{env['cli1']}", headers=h,
               json={"lista_id": env["lista_b"]})
    r = client.patch(f"/api/v1/clientes/{env['cli1']}", headers=h, json={"lista_id": env["lista_a"]})
    assert r.status_code == 200, r.text
    assert r.json()["lista_nombre"] == "Lista A"
    assert len(_renglones_de(env["cli1"])) == 2

    fila = next(c for c in client.get("/api/v1/clientes", headers=h).json()["items"]
                if c["id"] == env["cli1"])
    assert fila["lista_nombre"] == "Lista A"

    # Mandar la misma lista (la ficha la manda siempre) no cambia nada.
    r = client.patch(f"/api/v1/clientes/{env['cli1']}", headers=h,
                     json={"lista_id": env["lista_a"], "dias_credito": 15})
    assert r.status_code == 200 and r.json()["lista_nombre"] == "Lista A"

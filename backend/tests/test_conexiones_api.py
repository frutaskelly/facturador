"""Conexiones — la clave que enchufa Smart Supply.

Lo que se prueba aquí es lo que la pantalla le promete al dueño en español:
que la clave sirve para dejar órdenes, que NO sirve para timbrar ni borrar, que
revocarla la corta en el acto, y que generar otra invalida la anterior.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Cliente, Membership, Producto, Role, Tenant, User

_PURGE = ("conexiones", "oc_recibidas", "cliente_externos", "productos", "clientes")


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        def _tenant(s):
            t = Tenant(slug=f"cx-{s}-{suffix}", legal_name=f"CX {s} SA",
                       rfc=f"C{s.upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id); return t

        tenant_a, tenant_b = _tenant("a"), _tenant("b")
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()

        def _user(tenant, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tenant.id, user_id=u.id, role_id=owner_role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tenant.id}

        dueno_a = _user(tenant_a, "cx-owner-a")
        dueno_b = _user(tenant_b, "cx-owner-b")

        cli = Cliente(tenant_id=tenant_a.id, codigo="CX", legal_name="EHMO CX", rfc="GOA180712SF5")
        prod = Producto(tenant_id=tenant_a.id, sku="CX-P", nombre="Jitomate",
                        clave_sat="01010101", unidad_sat="KGM")
        db.add_all([cli, prod]); db.flush()
        db.commit()
        yield {"dueno_a": dueno_a, "dueno_b": dueno_b,
               "tenant_a": str(tenant_a.id), "cli": str(cli.id), "prod": str(prod.id)}
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


@pytest.fixture
def sin_sesion():
    """Quita el override para que la clave viaje por el flujo real de auth."""
    def _clear():
        app.dependency_overrides.pop(get_principal, None)
    return _clear


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _clave(client, u):
    return client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(u))


def _con_clave(clave):
    return {"Authorization": f"Bearer {clave}"}


def _oc(**over):
    body = {
        "canal": "WHATSAPP",
        "origen_externo": f"WA:g@g.us:{uuid.uuid4().hex[:6]}",
        "folio_externo": "1188",
        "rfc": "GOA180712SF5",
        "lineas": [{"descripcion": "JITOMATE", "cantidad": "10"}],
    }
    body.update(over)
    return body


# ─── generar / mostrar ───────────────────────────────────────────────────────

def test_generar_devuelve_la_clave_una_vez(client, env, auth_as):
    auth_as(env["dueno_a"])
    r = _clave(client, env["dueno_a"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["clave"].startswith("fi_ss_")
    assert body["conexion"]["estado"] == "PENDIENTE"
    assert body["conexion"]["clave_pista"] == body["clave"][-4:]
    assert body["clave"] in body["instruccion_whatsapp"]

    # La clave completa NO se puede volver a leer por ningún lado.
    listado = client.get("/api/v1/conexiones", headers=_hdr(env["dueno_a"])).json()
    smart = next(c for c in listado if c["tipo"] == "SMART_SUPPLY")
    assert "clave" not in str(smart) or body["clave"] not in str(smart)
    assert smart["conexion"]["clave_pista"] == body["clave"][-4:]


def test_generar_otra_revoca_la_anterior(client, env, auth_as, sin_sesion):
    auth_as(env["dueno_a"])
    vieja = _clave(client, env["dueno_a"]).json()["clave"]
    nueva = _clave(client, env["dueno_a"]).json()["clave"]
    assert vieja != nueva

    sin_sesion()
    assert client.get("/api/v1/conexiones/probar", headers=_con_clave(vieja)).status_code == 401
    assert client.get("/api/v1/conexiones/probar", headers=_con_clave(nueva)).status_code == 200


# ─── la clave en uso ─────────────────────────────────────────────────────────

def test_la_clave_deja_ordenes_y_se_activa_sola(client, env, auth_as, sin_sesion):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]

    sin_sesion()
    # Sin X-Tenant-Id: la clave YA dice de qué empresa es.
    r = client.post("/api/v1/oc-recibidas", headers=_con_clave(clave), json=_oc())
    assert r.status_code == 201, r.text
    assert r.json()["canal"] == "WHATSAPP"

    auth_as(env["dueno_a"])
    smart = next(c for c in client.get("/api/v1/conexiones", headers=_hdr(env["dueno_a"])).json()
                 if c["tipo"] == "SMART_SUPPLY")
    assert smart["conexion"]["estado"] == "ACTIVA"      # el primer uso la activa
    assert smart["conexion"]["activada_at"] is not None
    assert smart["ordenes_hoy"] == 1
    assert smart["conviene_rotar"] is False


def test_la_clave_no_puede_facturar_ni_borrar(client, env, auth_as, sin_sesion):
    """Lo que la pantalla promete en español, verificado en código."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    cli = env["cli"]

    sin_sesion()
    h = _con_clave(clave)
    assert client.get("/api/v1/facturas", headers=h).status_code == 403
    assert client.post("/api/v1/facturas/directa", headers=h, json={}).status_code == 403
    assert client.post("/api/v1/facturas/desde-remisiones", headers=h, json={}).status_code == 403
    fake = str(uuid.uuid4())
    assert client.post(f"/api/v1/facturas/{fake}/timbrar", headers=h, json={}).status_code == 403
    assert client.post(f"/api/v1/facturas/{fake}/cancelar", headers=h, json={}).status_code == 403
    assert client.delete(f"/api/v1/clientes/{cli}", headers=h).status_code == 403
    assert client.get("/api/v1/series", headers=h).status_code == 403
    assert client.get("/api/v1/memberships/usuarios", headers=h).status_code == 403
    # Ni siquiera puede gestionar sus propias conexiones (no puede auto-ampliarse).
    assert client.get("/api/v1/conexiones", headers=h).status_code == 403
    # Pero sí lo que necesita para cruzar.
    assert client.get("/api/v1/clientes", headers=h).status_code == 200
    assert client.get("/api/v1/productos", headers=h).status_code == 200


def test_revocar_corta_en_el_acto(client, env, auth_as, sin_sesion):
    auth_as(env["dueno_a"])
    gen = _clave(client, env["dueno_a"]).json()
    clave, cid = gen["clave"], gen["conexion"]["id"]

    sin_sesion()
    assert client.get("/api/v1/conexiones/probar", headers=_con_clave(clave)).status_code == 200

    auth_as(env["dueno_a"])
    assert client.post(f"/api/v1/conexiones/{cid}/revocar",
                       headers=_hdr(env["dueno_a"])).status_code == 200

    sin_sesion()
    # Sin esperar a que expire ningún caché.
    r = client.post("/api/v1/oc-recibidas", headers=_con_clave(clave), json=_oc())
    assert r.status_code == 401
    assert "revocada" in r.json()["detail"].lower()


def test_clave_inventada_es_401(client, env, sin_sesion):
    sin_sesion()
    r = client.get("/api/v1/conexiones/probar",
                   headers=_con_clave("fi_ss_AAAA-BBBB-CCCC-DDDD-EEEE-FFFF"))
    assert r.status_code == 401


def test_la_clave_no_alcanza_otro_inquilino(client, env, auth_as, sin_sesion):
    """Aunque mande el X-Tenant-Id de otra empresa: la clave manda, no el header."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]

    sin_sesion()
    h = _con_clave(clave)
    h["X-Tenant-Id"] = str(env["dueno_b"]["tenant_id"])
    r = client.post("/api/v1/oc-recibidas", headers=h, json=_oc())
    assert r.status_code == 201

    # La orden quedó en la empresa de la clave (A), no en la del header (B).
    auth_as(env["dueno_b"])
    assert client.get("/api/v1/oc-recibidas",
                      headers=_hdr(env["dueno_b"])).json()["total"] == 0
    auth_as(env["dueno_a"])
    assert client.get("/api/v1/oc-recibidas",
                      headers=_hdr(env["dueno_a"])).json()["total"] == 1


def test_actividad_muestra_lo_que_entro(client, env, auth_as, sin_sesion):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    sin_sesion()
    client.post("/api/v1/oc-recibidas", headers=_con_clave(clave),
                json=_oc(remitente="Pedidos FyV Tabasco"))

    auth_as(env["dueno_a"])
    filas = client.get("/api/v1/conexiones/SMART_SUPPLY/actividad",
                       headers=_hdr(env["dueno_a"])).json()
    assert len(filas) == 1
    assert filas[0]["remitente"] == "Pedidos FyV Tabasco"
    assert filas[0]["partidas"] == 1
    assert filas[0]["estado"] == "SIN_CLIENTE"     # nadie registró aún ese RFC


def test_el_mapa_de_grupos(client, env, auth_as, sin_sesion):
    """La pantalla tiene que poder responder: qué grupo alimenta a qué."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    h_owner = _hdr(env["dueno_a"])
    cli = env["cli"]

    # El grupo es candidato del cliente (así lo siembra el seed).
    client.post("/api/v1/clientes/externos", headers=h_owner, json={
        "sistema": "WHATSAPP", "clave": "grupo-uno@g.us", "cliente_id": cli})

    sin_sesion()
    # El bot reporta su directorio con SU clave, sin sesión de nadie.
    r = client.post("/api/v1/conexiones/grupos", headers=_con_clave(clave), json={"grupos": [
        {"jid": "grupo-uno@g.us", "nombre": "Interno SM Balles", "rol": "interno",
         "perfil": "ehmo", "activo": True},
        {"jid": "grupo-dos@g.us", "nombre": "Grupo apagado", "rol": "cliente",
         "perfil": "villahermosa", "activo": False},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["grupos"] == 2
    # Y manda una orden por el grupo uno.
    client.post("/api/v1/oc-recibidas", headers=_con_clave(clave),
                json=_oc(jid="grupo-uno@g.us"))

    auth_as(env["dueno_a"])
    gs = client.get("/api/v1/conexiones/grupos", headers=h_owner).json()
    assert len(gs) == 2
    uno = next(g for g in gs if g["jid"] == "grupo-uno@g.us")
    assert uno["nombre"] == "Interno SM Balles"
    assert uno["rol"] == "interno" and uno["perfil"] == "ehmo" and uno["activo"] is True
    assert uno["ordenes"] == 1 and uno["ordenes_24h"] == 1
    assert uno["ultima_orden_at"] is not None
    assert [c["cliente_id"] for c in uno["clientes"]] == [cli]
    assert uno["clientes"][0]["registrado"] is True

    dos = next(g for g in gs if g["jid"] == "grupo-dos@g.us")
    assert dos["activo"] is False and dos["rol"] == "cliente"
    assert dos["ordenes"] == 0 and dos["clientes"] == []


def test_un_grupo_que_deja_de_reportarse_no_se_borra(client, env, auth_as, sin_sesion):
    """Borrarlo perdería el historial de las órdenes que ya entraron por ahí.
    Y dejar de reportarse es cosa del BOT: no toca lo que decidió el dueño."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    sin_sesion()
    h = _con_clave(clave)
    client.post("/api/v1/conexiones/grupos", headers=h, json={"grupos": [
        {"jid": "viejo@g.us", "nombre": "Se va a apagar", "rol": "interno"}]})
    client.post("/api/v1/conexiones/grupos", headers=h, json={"grupos": [
        {"jid": "nuevo@g.us", "nombre": "El que queda", "rol": "interno"}]})

    auth_as(env["dueno_a"])
    gs = {g["jid"]: g for g in client.get("/api/v1/conexiones/grupos",
                                          headers=_hdr(env["dueno_a"])).json()}
    assert gs["viejo@g.us"]["reportado_activo"] is False   # el bot dejó de verlo
    assert gs["viejo@g.us"]["activo"] is True              # pero nadie lo apagó aquí
    assert gs["nuevo@g.us"]["reportado_activo"] is True


def test_la_clave_no_puede_leer_el_mapa(client, env, auth_as, sin_sesion):
    """Reportar sí; leer el mapa de clientes y series, no."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    sin_sesion()
    assert client.get("/api/v1/conexiones/grupos", headers=_con_clave(clave)).status_code == 403


def test_apagar_un_grupo_manda_sus_ordenes_a_descartadas(client, env, auth_as, sin_sesion):
    """Apagarlo no puede PERDER órdenes: se guardan descartadas, con el motivo."""
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    h_owner = _hdr(env["dueno_a"])
    client.post("/api/v1/clientes/externos", headers=h_owner, json={
        "sistema": "WHATSAPP", "clave": "g-apagable@g.us", "cliente_id": env["cli"]})

    sin_sesion()
    h = _con_clave(clave)
    client.post("/api/v1/conexiones/grupos", headers=h, json={"grupos": [
        {"jid": "g-apagable@g.us", "nombre": "Se apaga", "rol": "interno"}]})

    auth_as(env["dueno_a"])
    r = client.patch("/api/v1/conexiones/grupos/g-apagable@g.us", headers=h_owner,
                     json={"activo": False})
    assert r.status_code == 200 and r.json()["activo"] is False
    assert r.json()["reportado_activo"] is True     # el bot sigue diciendo que está vivo

    sin_sesion()
    oc = client.post("/api/v1/oc-recibidas", headers=h,
                     json=_oc(jid="g-apagable@g.us")).json()
    assert oc["estado"] == "DESCARTADA"
    assert "apagado" in oc["motivo"]

    # Y la sincronización del bot NO lo vuelve a prender.
    client.post("/api/v1/conexiones/grupos", headers=h, json={"grupos": [
        {"jid": "g-apagable@g.us", "nombre": "Se apaga", "rol": "interno", "activo": True}]})
    auth_as(env["dueno_a"])
    g = next(x for x in client.get("/api/v1/conexiones/grupos", headers=h_owner).json()
             if x["jid"] == "g-apagable@g.us")
    assert g["activo"] is False


def test_los_activos_van_arriba(client, env, auth_as, sin_sesion):
    auth_as(env["dueno_a"])
    clave = _clave(client, env["dueno_a"]).json()["clave"]
    h_owner = _hdr(env["dueno_a"])
    sin_sesion()
    client.post("/api/v1/conexiones/grupos", headers=_con_clave(clave), json={"grupos": [
        {"jid": "aaa@g.us", "nombre": "AAA primero por nombre", "rol": "interno"},
        {"jid": "zzz@g.us", "nombre": "ZZZ último por nombre", "rol": "interno"},
    ]})
    auth_as(env["dueno_a"])
    client.patch("/api/v1/conexiones/grupos/aaa@g.us", headers=h_owner, json={"activo": False})
    gs = client.get("/api/v1/conexiones/grupos", headers=h_owner).json()
    assert [g["jid"] for g in gs] == ["zzz@g.us", "aaa@g.us"]   # el activo, arriba

"""/sae/* lee EL SAE del despliegue, y ese SAE es de un solo tenant.

La conexión a SAE (`SAE_SERVER`…) es global, no del tenant. Hasta el
26-sep-2026 lo único que cuidaba estas rutas era el permiso `factura:espejo`,
y `require_permission` le deja pasar todo al OWNER: el OWNER de cualquier otro
tenant —o quien se registrara por el signup público, que nace OWNER— leía en
vivo las facturas y pedidos del SAE ajeno. Lo que se prueba aquí es que sólo el
tenant de `ESPEJO_SAE_TENANT_ID` pasa, por cualquier puerta (sesión o clave de
conexión) y en TODAS las rutas del router, incluidas las que se agreguen.

Lo mismo vale para la cola de escrituras a SAE (/productos/alta-sae,
/cambio-sae, reclamar y reportar): el escritor sólo atiende al tenant dueño,
así que a los demás se les dice que no en vez de dejarles una solicitud que
nadie va a tomar.
"""
import uuid

import pytest
from sqlalchemy import text

from app.api.v1 import sae as sae_api
from app.core.config import settings
from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import Membership, Producto, Role, SolicitudAltaSae, Tenant, User
from app.services import espejo_sae, sae_lectura

# Las que existían cuando se puso el candado; si alguna cambia de nombre, que
# la prueba lo diga en vez de quedarse revisando una lista vacía.
_CONOCIDAS = {
    ("GET", "/api/v1/sae/salud"), ("GET", "/api/v1/sae/facturas"),
    ("GET", "/api/v1/sae/partidas"), ("GET", "/api/v1/sae/catalogos"),
    ("POST", "/api/v1/sae/espejo/jalar"), ("POST", "/api/v1/sae/espejo/cuadre"),
}
# Parámetros válidos para todas: una fuga no debe poder esconderse detrás de un 422.
_PARAMS = {"empresa": "02", "q": "OC-123", "docs": "ZEHMOVH 1442"}


def _rutas_sae():
    for r in app.routes:
        if getattr(r, "path", "").startswith("/api/v1/sae/"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, r.path


def _pedir(client, metodo, ruta, headers):
    return client.request(metodo, ruta, headers=headers, params=_PARAMS)


@pytest.fixture
def dos(db_engine):
    """Dos tenants con su OWNER: el dueño de SAE y uno cualquiera."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()

        def _tenant_con_owner(s):
            t = Tenant(slug=f"sae-{s}-{suffix}", legal_name=f"SAE {s} SA",
                       rfc=f"S{s[:2].upper()}{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                       domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
            db.add(t); db.flush(); created["tenants"].append(t.id)
            sub = f"sub-sae-{s}-{suffix}"
            u = User(email=f"sae-{s}-{suffix}@t.test", auth_user_id=sub, full_name=s)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=t.id, user_id=u.id, role_id=owner_role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": t.id}

        suyo, ajeno = _tenant_con_owner("suyo"), _tenant_con_owner("ajeno")
        db.commit()
        yield {"suyo": suyo, "ajeno": ajeno}
    finally:
        for tid in created["tenants"]:
            db.execute(text("DELETE FROM conexiones WHERE tenant_id = :tid"), {"tid": tid})
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
def sae(monkeypatch, dos):
    """SAE falso que anota cada pregunta; el dueño es el tenant «suyo»."""
    llamadas = []

    def _consultar(sql, params=(), timeout=None):
        llamadas.append(sql)
        return []

    def _espejo(nombre):
        return lambda *a, **k: llamadas.append(nombre) or {}

    sae_api._catalogos_cache.clear()
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", str(dos["suyo"]["tenant_id"]))
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)
    monkeypatch.setattr(sae_lectura, "consultar", _consultar)
    monkeypatch.setattr(espejo_sae, "sincronizar", _espejo("sincronizar"))
    monkeypatch.setattr(espejo_sae, "cuadre", _espejo("cuadre"))
    yield llamadas
    sae_api._catalogos_cache.clear()


def _hdr(u, tenant_id=None):
    return {"X-Tenant-Id": str(tenant_id or u["tenant_id"])}


def test_el_router_entero_esta_cubierto():
    rutas = set(_rutas_sae())
    assert _CONOCIDAS <= rutas, _CONOCIDAS - rutas


def test_el_owner_de_otro_tenant_no_lee_nada(client, dos, auth_as, sae):
    """El caso del hallazgo: OWNER (pasa cualquier permiso) de otro tenant."""
    auth_as(dos["ajeno"])
    for metodo, ruta in _rutas_sae():
        r = _pedir(client, metodo, ruta, _hdr(dos["ajeno"]))
        assert r.status_code == 403, (metodo, ruta, r.status_code, r.text)
    assert sae == []                      # ni una pregunta llegó a SAE


def test_tampoco_pidiendo_el_tenant_ajeno_por_encabezado(client, dos, auth_as, sae):
    """El X-Tenant-Id no abre la puerta: sin membresía allá no hay contexto."""
    auth_as(dos["ajeno"])
    r = client.get("/api/v1/sae/facturas", params=_PARAMS,
                   headers=_hdr(dos["ajeno"], dos["suyo"]["tenant_id"]))
    assert r.status_code in (401, 403), r.text
    assert sae == []


def test_el_dueno_de_sae_si_lee(client, dos, auth_as, sae):
    auth_as(dos["suyo"])
    for metodo, ruta in _rutas_sae():
        if ruta.startswith("/api/v1/sae/fuentes/{codigo}"):
            continue          # las del SAE 9 piden SU empresa: ver las pruebas de abajo
        r = _pedir(client, metodo, ruta, _hdr(dos["suyo"]))
        assert r.status_code == 200, (metodo, ruta, r.status_code, r.text)
    assert any("OC-123" in str(q) or "FACTF" in str(q) for q in sae)
    assert {"sincronizar", "cuadre"} <= set(sae)


@pytest.mark.parametrize("configurado", ["", "   ", "no-es-un-uuid"])
def test_sin_dueno_configurado_nadie_pasa(client, dos, auth_as, sae, monkeypatch, configurado):
    """Falla cerrado: sin saber de quién es SAE, no se le enseña a nadie."""
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", configurado)
    auth_as(dos["suyo"])
    for metodo, ruta in _rutas_sae():
        assert _pedir(client, metodo, ruta, _hdr(dos["suyo"])).status_code == 403, ruta
    assert sae == []


def test_la_clave_de_conexion_sigue_al_tenant(client, dos, auth_as, sae):
    """Cualquier OWNER puede generarse una clave SMART_SUPPLY, que trae
    `factura:espejo`. La del tenant ajeno no lee SAE; la del dueño —la del bot
    en producción— sí."""
    claves = {}
    for quien in ("ajeno", "suyo"):
        auth_as(dos[quien])
        r = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(dos[quien]))
        assert r.status_code == 201, r.text
        claves[quien] = r.json()["clave"]
    app.dependency_overrides.pop(get_principal, None)   # la clave viaja por el auth real

    bearer = lambda c: {"Authorization": f"Bearer {c}"}    # noqa: E731
    r = client.get("/api/v1/sae/facturas", params=_PARAMS, headers=bearer(claves["ajeno"]))
    assert r.status_code == 403, r.text
    # el encabezado no mueve a la clave de tenant
    r = client.get("/api/v1/sae/facturas", params=_PARAMS,
                   headers={**bearer(claves["ajeno"]), **_hdr(dos["suyo"])})
    assert r.status_code == 403, r.text
    assert sae == []

    r = client.get("/api/v1/sae/facturas", params=_PARAMS, headers=bearer(claves["suyo"]))
    assert r.status_code == 200, r.text
    assert sae


# ── La cola de escrituras a SAE ──────────────────────────────────────────────

def _cola():
    """Las cuatro puertas de la cola, con cuerpos válidos."""
    alta = {"clave": "AJOKG", "descripcion": "AJO KILO", "unidad": "KILO",
            "linea": "FRUVE", "esquema": 2, "sat": "50161509", "sat_unidad": "KGM"}
    return [
        ("POST", "/api/v1/productos/alta-sae", alta),
        ("POST", "/api/v1/productos/cambio-sae", {"clave": "AJOKG", "descripcion": "AJO KG"}),
        ("GET", "/api/v1/productos/alta-sae/pendiente", None),
        ("POST", f"/api/v1/productos/alta-sae/{uuid.uuid4()}/reporte",
         {"por_empresa": {"02": {"ok": True, "clave": "AJOKG"}}}),
    ]


def _contar_en(tenant_id):
    db = SessionLocal()
    try:
        return (db.query(SolicitudAltaSae).filter(SolicitudAltaSae.tenant_id == tenant_id).count(),
                db.query(Producto).filter(Producto.tenant_id == tenant_id).count())
    finally:
        db.close()


def test_otro_tenant_no_toca_la_cola_de_sae(client, dos, auth_as, sae):
    """Un OWNER ajeno (pasa cualquier permiso) ni pide, ni reclama, ni reporta;
    y el alta rechazada tampoco le deja creado el producto que la acompaña."""
    auth_as(dos["ajeno"])
    for metodo, ruta, cuerpo in _cola():
        r = client.request(metodo, ruta, headers=_hdr(dos["ajeno"]), json=cuerpo)
        assert r.status_code == 403, (metodo, ruta, r.status_code, r.text)
    assert _contar_en(dos["ajeno"]["tenant_id"]) == (0, 0)
    # su propia lista (vacía) sí la puede leer
    r = client.get("/api/v1/productos/alta-sae", headers=_hdr(dos["ajeno"]))
    assert r.status_code == 200 and r.json()["items"] == [], r.text


def test_el_dueno_de_sae_si_pide_el_alta(client, dos, auth_as, sae):
    auth_as(dos["suyo"])
    # sin crear el producto: aquí sólo importa que la solicitud entre a la cola
    r = client.post("/api/v1/productos/alta-sae", headers=_hdr(dos["suyo"]),
                    json={**_cola()[0][2], "crear_producto": False})
    try:
        assert r.status_code == 201, r.text
        assert _contar_en(dos["suyo"]["tenant_id"]) == (1, 0)
    finally:
        db = SessionLocal()
        db.execute(text("DELETE FROM solicitudes_alta_sae WHERE tenant_id = :t"),
                   {"t": dos["suyo"]["tenant_id"]})
        db.commit(); db.close()


def test_la_clave_de_conexion_ajena_no_pide_altas(client, dos, auth_as, sae):
    """La clave SMART_SUPPLY trae `producto:alta_sae`: la de otro tenant, no."""
    auth_as(dos["ajeno"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(dos["ajeno"]))
    assert r.status_code == 201, r.text
    clave = r.json()["clave"]
    app.dependency_overrides.pop(get_principal, None)
    r = client.post("/api/v1/productos/alta-sae", json=_cola()[0][2],
                    headers={"Authorization": f"Bearer {clave}"})
    assert r.status_code == 403, r.text
    assert _contar_en(dos["ajeno"]["tenant_id"]) == (0, 0)


# ── El SAE 9: empresas de MÁS DE UN tenant (26-sep-2026) ─────────────────────

_RUTAS_SAE10 = [("GET", "/api/v1/sae/salud"), ("GET", "/api/v1/sae/facturas"),
                ("GET", "/api/v1/sae/partidas"), ("GET", "/api/v1/sae/catalogos"),
                ("POST", "/api/v1/sae/espejo/jalar"), ("POST", "/api/v1/sae/espejo/cuadre")]


@pytest.fixture
def con_sae9(monkeypatch, dos, sae):
    """El tenant «ajeno» es dueño de la empresa 04 del SAE 9 (código 94), como
    Gerardo; el «suyo» sigue siendo el dueño del SAE 10, como Cristian."""
    import json
    from app.core.config import settings as s   # no `sae_api.settings`: el candado se muda (#269)
    monkeypatch.setattr(s, "SAE_FB_HOST", "100.95.166.85")
    monkeypatch.setattr(s, "SAE_FB_USER", "SYSDBA")
    monkeypatch.setattr(s, "SAE_FB_PASSWORD", "x")
    monkeypatch.setattr(s, "SAE_FB_EMPRESAS", json.dumps(
        [{"numero": "04", "tenant": str(dos["ajeno"]["tenant_id"])}]))
    return sae


def test_un_tenant_de_puro_sae9_no_lee_el_sae10_por_ninguna_puerta(client, dos, auth_as, con_sae9):
    """Tener una empresa del SAE 9 NO abre las rutas que leen el SAE 10 del
    despliegue: con empresa=02 leería (o, con jalar, escribiría en su tenant)
    las facturas de otro."""
    auth_as(dos["ajeno"])
    for metodo, ruta in _RUTAS_SAE10:
        assert _pedir(client, metodo, ruta, _hdr(dos["ajeno"])).status_code == 403, ruta
    # ni por las rutas del SAE 9 pidiendo una empresa que no es suya
    for cod in ("02", "03"):
        for accion in ("clientes", "jalar", "cuadre"):
            r = client.post(f"/api/v1/sae/fuentes/{cod}/{accion}", params=_PARAMS,
                            headers=_hdr(dos["ajeno"]))
            assert r.status_code == 404, (cod, accion, r.status_code, r.text)
    assert con_sae9 == []                 # ni una pregunta llegó a SAE


def test_un_tenant_de_puro_sae9_si_usa_su_empresa(client, dos, auth_as, con_sae9):
    auth_as(dos["ajeno"])
    r = client.get("/api/v1/sae/fuentes", headers=_hdr(dos["ajeno"]))
    assert r.status_code == 200, r.text
    assert [e["codigo"] for e in r.json()["empresas"]] == ["94"]
    r = client.post("/api/v1/sae/fuentes/94/jalar", params={"series": "SLPB"},
                    headers=_hdr(dos["ajeno"]))
    assert r.status_code == 200, r.text
    assert "sincronizar" in con_sae9


def test_el_dueno_del_sae10_no_toca_la_empresa_sae9_de_otro(client, dos, auth_as, con_sae9):
    auth_as(dos["suyo"])
    r = client.get("/api/v1/sae/fuentes", headers=_hdr(dos["suyo"]))
    assert r.status_code == 200 and "94" not in [e["codigo"] for e in r.json()["empresas"]]
    for accion in ("clientes", "jalar", "cuadre"):
        r = client.post(f"/api/v1/sae/fuentes/94/{accion}", params={"series": "SLPB"},
                        headers=_hdr(dos["suyo"]))
        assert r.status_code == 404, (accion, r.status_code, r.text)
    assert con_sae9 == []

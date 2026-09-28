"""Las listas de precios ya no se ligan a SAE (26-sep-2026).

Decisión del dueño: las listas de precios de SAE ya no se usan; el precio sale
ÚNICAMENTE del Facturador y no hay espejo en ninguna dirección. Este archivo
sustituye a `test_precios_espejo_sae.py`, que probaba el depósito
PRECIO_X_PROD → lista vinculada. Lo que se prueba ahora es lo contrario, que
nadie lo pueda volver a encender:

  · los endpoints del espejo (`/espejo/vinculadas`, `/espejo/precios`) no
    existen, ni siquiera para la clave del conector;
  · crear o editar una lista con `sae_empresa`/`sae_lista` NO liga nada — el
    schema ya no los declara y pydantic los ignora (un front en caché que
    todavía mande `null` sigue guardando);
  · la salida ya no los enseña.

Las columnas siguen en la BD (borrarlas es una migración que espera el OK del
dueño), así que la prueba mira la fila directo para confirmar que quedan NULL.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    ListaPrecios,
    Membership,
    Precio,
    Producto,
    Role,
    Tenant,
    User,
)

_PURGE = (
    "precios", "lista_asignaciones", "listas_precios", "conexiones", "productos",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"lss-{suffix}", legal_name="Listas Sin SAE SA",
                   rfc=f"LSS{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        sub = f"sub-lss-{suffix}"
        u = User(email=f"lss-{suffix}@t.test", auth_user_id=sub, full_name="lss")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=owner_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        dueno = {"sub": sub, "email": u.email, "tenant_id": t.id}

        espinaca = Producto(tenant_id=t.id, sku="ESPINACA", nombre="ESPINACA",
                            clave_sat="50403700", unidad_sat="KGM",
                            unidad_base="KILO", presentaciones={"KILO": 1},
                            presentacion_default="KILO")
        db.add(espinaca); db.flush()
        manual = ListaPrecios(tenant_id=t.id, codigo="MANUAL", nombre="Manual")
        # Una lista que quedó con el vínculo viejo en la BD (como las 7 antes
        # del 20-sep): editarla no debe ni leerlo ni revivirlo.
        vieja = ListaPrecios(tenant_id=t.id, codigo="SAE9",
                             nombre="HOSPITALES (SAE lista 9)",
                             sae_empresa="02", sae_lista=9)
        db.add_all([manual, vieja]); db.flush()
        ids = {"tenant": t.id, "manual": manual.id, "vieja": vieja.id}
        db.commit()
        yield {"dueno": dueno, **ids}
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


def _vinculo(lista_id):
    """(sae_empresa, sae_lista) tal como están en la BD."""
    db = SessionLocal()
    try:
        return tuple(db.execute(
            text("SELECT sae_empresa, sae_lista FROM listas_precios WHERE id = :id"),
            {"id": str(lista_id)},
        ).one())
    finally:
        db.close()


def test_los_endpoints_del_espejo_ya_no_existen():
    rutas = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/listas-precios/espejo/vinculadas" not in rutas
    assert "/api/v1/listas-precios/espejo/precios" not in rutas


def test_la_clave_del_conector_no_encuentra_el_espejo(client, env, auth_as):
    """El bot todavía los llama en cada pasada: debe recibir un NO y no escribir
    nada. /espejo/precios cae en el patrón /{lista_id}/precios, así que no es un
    404 limpio sino un 422 (lista_id «espejo» no es UUID); lo que importa es que
    no deposita."""
    auth_as(env["dueno"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(env["dueno"]))
    assert r.status_code in (200, 201), r.text
    hk = {"Authorization": f"Bearer {r.json()['clave']}"}
    app.dependency_overrides.pop(get_principal, None)

    r = client.get("/api/v1/listas-precios/espejo/vinculadas", headers=hk)
    assert r.status_code == 404, r.text
    r = client.post("/api/v1/listas-precios/espejo/precios", headers=hk, json={
        "lista_id": str(env["vieja"]),
        "precios": [{"clave": "ESPINACA", "precio": "48.50", "unidad": "KG"}],
    })
    assert r.status_code in (404, 422), r.text

    db = SessionLocal()
    try:
        assert db.query(Precio).filter(Precio.lista_id == env["vieja"]).count() == 0
    finally:
        db.close()


def test_crear_lista_con_vinculo_sae_no_liga_nada(client, env, auth_as):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    r = client.post("/api/v1/listas-precios", headers=h, json={
        "codigo": "NUEVA", "nombre": "Nueva", "sae_empresa": "02", "sae_lista": 9,
    })
    assert r.status_code == 201, r.text
    assert "sae_empresa" not in r.json() and "sae_lista" not in r.json()
    assert _vinculo(r.json()["id"]) == (None, None)

    # El front de antes (en caché, sin Ctrl+Shift+R) manda la pareja en null:
    # tiene que seguir pudiendo guardar.
    r = client.post("/api/v1/listas-precios", headers=h, json={
        "codigo": "NUEVA2", "nombre": "Nueva 2", "sae_empresa": None, "sae_lista": None,
    })
    assert r.status_code == 201, r.text
    assert _vinculo(r.json()["id"]) == (None, None)


def test_editar_lista_con_vinculo_sae_no_liga_nada(client, env, auth_as):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    url = f"/api/v1/listas-precios/{env['manual']}"
    r = client.patch(url, headers=h, json={"sae_empresa": "03", "sae_lista": 4})
    assert r.status_code == 200, r.text
    assert _vinculo(env["manual"]) == (None, None)

    # Antes, mandar sólo la mitad era 422 («vínculo a medias»); ahora ni eso
    # se mira: se ignora y el resto del PATCH se aplica.
    r = client.patch(url, headers=h, json={"sae_lista": 4, "nombre": "Manual editada"})
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "Manual editada"
    assert _vinculo(env["manual"]) == (None, None)


def test_editar_una_lista_con_vinculo_viejo_no_lo_toca(client, env, auth_as):
    """El PATCH no borra el vínculo viejo por su cuenta (limpiar datos es otra
    decisión) ni lo enseña; sólo cambia lo que se pidió."""
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    r = client.patch(f"/api/v1/listas-precios/{env['vieja']}", headers=h,
                     json={"nombre": "HOSPITALES", "sae_empresa": "03", "sae_lista": 4})
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "HOSPITALES"
    assert "sae_empresa" not in r.json()
    assert _vinculo(env["vieja"]) == ("02", 9)


def test_la_salida_no_ensena_el_vinculo(client, env, auth_as):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    r = client.get("/api/v1/listas-precios?limit=200", headers=h)
    assert r.status_code == 200, r.text
    for lista in r.json()["items"]:
        assert "sae_empresa" not in lista and "sae_lista" not in lista


def test_el_contrato_del_api_no_ofrece_el_vinculo():
    """El cliente del front se GENERA del OpenAPI: si los campos volvieran al
    schema, reaparecerían en los tipos y alguien los volvería a pintar."""
    esquemas = app.openapi()["components"]["schemas"]
    for nombre in ("ListaPreciosCreate", "ListaPreciosUpdate", "ListaPreciosOut"):
        props = esquemas[nombre]["properties"]
        assert "sae_empresa" not in props and "sae_lista" not in props, nombre
    for nombre in ("ListaVinculadaOut", "EspejoPreciosIn", "EspejoPreciosResult",
                   "EspejoPrecioItem"):
        assert nombre not in esquemas, nombre

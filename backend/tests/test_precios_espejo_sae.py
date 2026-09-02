"""Espejo de precios SAE → listas de precios vinculadas.

El botón «Sincronizar SAE» de /listas-precios funciona igual que el de
facturas: el conector pregunta qué listas declaran origen SAE
(`/espejo/vinculadas`) y deposita lo que PRECIO_X_PROD tiene hoy
(`/espejo/precios`). Aquí se prueba lo que hace SEGURO ese depósito: solo
listas vinculadas, cruce por sku (con rescate por código de cliente no
ambiguo), $0 no se escribe, unidades no declaradas no se adivinan, y nunca
se borra un renglón por iniciativa propia.
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
    ProductoCliente,
    Cliente,
    Role,
    Tenant,
    User,
)

_PURGE = (
    "precios", "lista_asignaciones", "listas_precios", "conexiones",
    "producto_clientes", "productos", "clientes", "espejo_syncs",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"pes-{suffix}", legal_name="Precios Espejo SA",
                   rfc=f"PES{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        sub = f"sub-pes-{suffix}"
        u = User(email=f"pes-{suffix}@t.test", auth_user_id=sub, full_name="pes")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=owner_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        dueno = {"sub": sub, "email": u.email, "tenant_id": t.id}

        espinaca = Producto(tenant_id=t.id, sku="ESPINACA", nombre="ESPINACA",
                            clave_sat="50403700", unidad_sat="KGM",
                            unidad_base="KILO", presentaciones={"KILO": 1},
                            presentacion_default="KILO")
        sandia = Producto(tenant_id=t.id, sku="SANDIA", nombre="SANDIA",
                          clave_sat="50403700", unidad_sat="KGM",
                          unidad_base="KILO", presentaciones={"KILO": 1},
                          presentacion_default="KILO")
        # Producto cuyo sku NO es la clave SAE: se cruza por el código que un
        # cliente le puso (el rescate del cotizador).
        aceite = Producto(tenant_id=t.id, sku="00000077", nombre="ACEITE 20 LT",
                          clave_sat="50151500", unidad_sat="H87",
                          unidad_base="PIEZA", presentaciones={"PIEZA": 1},
                          presentacion_default="PIEZA")
        db.add_all([espinaca, sandia, aceite]); db.flush()
        cli = Cliente(tenant_id=t.id, codigo="CL1", legal_name="EHMO HOSPITALES",
                      rfc="DAP250922PY2")
        db.add(cli); db.flush()
        db.add(ProductoCliente(tenant_id=t.id, cliente_id=cli.id,
                               producto_id=aceite.id, codigo_cliente="ACEI-639"))

        vinculada = ListaPrecios(tenant_id=t.id, codigo="SAE9",
                                 nombre="HOSPITALES (SAE lista 9)",
                                 sae_empresa="02", sae_lista=9)
        manual = ListaPrecios(tenant_id=t.id, codigo="MANUAL", nombre="Manual")
        db.add_all([vinculada, manual]); db.flush()
        # Un precio que SAE ya no trae: el espejo NO debe borrarlo.
        db.add(Precio(tenant_id=t.id, lista_id=vinculada.id,
                      producto_id=sandia.id, presentacion="KILO",
                      precio_unitario="12.0000", cantidad_minima=1))
        db.commit()
        yield {"dueno": dueno, "tenant": t.id,
               "vinculada": str(vinculada.id), "manual": str(manual.id),
               "espinaca": str(espinaca.id), "sandia": str(sandia.id),
               "aceite": str(aceite.id)}
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
    def _clear():
        app.dependency_overrides.pop(get_principal, None)
    yield _clear
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _clave_bot(client, env, auth_as, sin_sesion):
    auth_as(env["dueno"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(env["dueno"]))
    assert r.status_code in (200, 201), r.text
    clave = r.json()["clave"]
    sin_sesion()
    return {"Authorization": f"Bearer {clave}"}


def test_vinculadas_solo_las_que_declaran_origen(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.get("/api/v1/listas-precios/espejo/vinculadas", headers=hk)
    assert r.status_code == 200, r.text
    filas = r.json()
    assert [f["id"] for f in filas] == [env["vinculada"]]
    assert filas[0]["sae_empresa"] == "02" and filas[0]["sae_lista"] == 9


def test_deposito_crea_actualiza_y_no_borra(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/listas-precios/espejo/precios", headers=hk, json={
        "lista_id": env["vinculada"],
        "precios": [
            # la espinaca del bug: existe en SAE, no estaba en la lista
            {"clave": "ESPINACA", "precio": "48.50", "unidad": "KG"},
            {"clave": "ACEI-639", "precio": "836.00", "unidad": "PZA"},
            {"clave": "NO-EXISTE-999", "precio": "10.00", "unidad": "KG"},
            {"clave": "SANDIA", "precio": "0", "unidad": "KG"},
        ],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["creados"] == 2
    assert res["en_cero"] == 1
    assert res["sin_cruce"] == ["NO-EXISTE-999"]
    assert res["sin_presentacion"] == []

    # el $0 de SAE ni escribió ni borró: la sandía sigue con su precio viejo
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    precios = client.get(
        f"/api/v1/listas-precios/{env['vinculada']}/precios?limit=100", headers=h
    ).json()["items"]
    por_prod = {p["producto_id"]: p for p in precios}
    assert por_prod[env["espinaca"]]["precio_unitario"] == "48.5000"
    assert por_prod[env["espinaca"]]["presentacion"] == "KILO"
    assert por_prod[env["aceite"]]["presentacion"] == "PIEZA"
    assert por_prod[env["sandia"]]["precio_unitario"] == "12.0000"

    # segunda corrida: mismo precio = sin_cambio; precio nuevo = actualizado
    sin_sesion()
    r = client.post("/api/v1/listas-precios/espejo/precios", headers=hk, json={
        "lista_id": env["vinculada"],
        "precios": [
            {"clave": "ESPINACA", "precio": "48.50", "unidad": "KG"},
            {"clave": "ACEI-639", "precio": "840.00", "unidad": "PZA"},
        ],
    })
    res = r.json()
    assert res["creados"] == 0
    assert res["sin_cambio"] == 1
    assert res["actualizados"] == 1


def test_unidad_no_declarada_no_se_adivina(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    # SAE cotiza la espinaca por MANOJO pero el producto solo declara KILO:
    # el factor lo decide una persona, no el espejo.
    r = client.post("/api/v1/listas-precios/espejo/precios", headers=hk, json={
        "lista_id": env["vinculada"],
        "precios": [{"clave": "ESPINACA", "precio": "15.00", "unidad": "MJO"}],
    })
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["sin_presentacion"] == ["ESPINACA"]
    assert res["creados"] == 0


def test_lista_sin_vinculo_rechaza_deposito(client, env, auth_as, sin_sesion):
    hk = _clave_bot(client, env, auth_as, sin_sesion)
    r = client.post("/api/v1/listas-precios/espejo/precios", headers=hk, json={
        "lista_id": env["manual"],
        "precios": [{"clave": "ESPINACA", "precio": "48.50", "unidad": "KG"}],
    })
    assert r.status_code == 422, r.text


def test_vinculo_a_medias_se_rechaza(client, env, auth_as, sin_sesion):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    r = client.patch(f"/api/v1/listas-precios/{env['manual']}", headers=h,
                     json={"sae_lista": 4})
    assert r.status_code == 422, r.text
    # la pareja completa sí entra, y se puede quitar dejando ambos vacíos
    r = client.patch(f"/api/v1/listas-precios/{env['manual']}", headers=h,
                     json={"sae_empresa": "03", "sae_lista": 4})
    assert r.status_code == 200, r.text
    assert r.json()["sae_lista"] == 4
    r = client.patch(f"/api/v1/listas-precios/{env['manual']}", headers=h,
                     json={"sae_empresa": None, "sae_lista": None})
    assert r.status_code == 200, r.text
    assert r.json()["sae_empresa"] is None

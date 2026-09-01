"""Precios v2: resolución por prioridad (override sucursal > override cliente >
lista cliente > lista base), tiers por volumen, CRUD de sucursales/overrides, RBAC.

Reproduce el ejemplo del usuario: Aguacate público $25; cliente fija $20;
sucursal SLP $15, QRO $12; otra sucursal → $20.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from .conftest import crear_sucursal
from app.models import (
    Cliente, ListaAsignacion, ListaPrecios, Membership, Precio, PrecioOverride,
    Producto, Proyecto, Role, Serie, Sucursal, Tenant, User,
)

_PURGE = (
    "lista_asignaciones", "precio_overrides", "precios", "listas_precios",
    "proyectos", "cliente_sucursales", "sucursales", "series", "productos", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        tenant = Tenant(slug=f"pre-{suffix}", legal_name="Pre SA", rfc=f"P{suffix.upper()}X"[:13],
                        regimen_fiscal_sat="601", domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(tenant); db.flush(); created["tenants"].append(tenant.id)
        tid = tenant.id
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        tomador_role = db.query(Role).filter(Role.nombre == "TOMADOR", Role.es_preset.is_(True)).one()

        def _user(role, label):
            sub = f"sub-{label}-{suffix}"
            u = User(email=f"{label}-{suffix}@t.test", auth_user_id=sub, full_name=label)
            db.add(u); db.flush(); created["users"].append(u.id)
            m = Membership(tenant_id=tid, user_id=u.id, role_id=role.id)
            db.add(m); db.flush(); created["memberships"].append(m.id)
            return {"sub": sub, "email": u.email, "tenant_id": tid}

        admin = _user(admin_role, "admin")
        tomador = _user(tomador_role, "tomador")

        prod = Producto(tenant_id=tid, sku="AGUACATE", nombre="Aguacate", clave_sat="50300000",
                        unidad_sat="KGM", unidad_base="KILO")
        unico = ListaPrecios(tenant_id=tid, codigo="UNICO", nombre="Precio único")
        menudeo = ListaPrecios(tenant_id=tid, codigo="MENUDEO", nombre="Menudeo")
        db.add_all([prod, unico, menudeo]); db.flush()

        # precio público base + tiers de menudeo
        db.add(Precio(tenant_id=tid, lista_id=unico.id, producto_id=prod.id, presentacion="KILO",
                      precio_unitario=Decimal("25"), cantidad_minima=1))
        db.add(Precio(tenant_id=tid, lista_id=menudeo.id, producto_id=prod.id, presentacion="KILO",
                      precio_unitario=Decimal("25"), cantidad_minima=1))
        db.add(Precio(tenant_id=tid, lista_id=menudeo.id, producto_id=prod.id, presentacion="KILO",
                      precio_unitario=Decimal("20"), cantidad_minima=10))

        cli1 = Cliente(tenant_id=tid, codigo="C1", legal_name="Cliente 1 SA", rfc="XAXX010101000")
        cli3 = Cliente(tenant_id=tid, codigo="C3", legal_name="Cliente 3 SA", rfc="XEXX010101000")
        db.add_all([cli1, cli3]); db.flush()
        db.add(ListaAsignacion(tenant_id=tid, lista_id=menudeo.id, cliente_id=cli3.id))
        db.flush()  # nivel del cliente 3 = menudeo

        slp = crear_sucursal(db, tenant_id=tid, cliente_id=cli1.id, nombre="SLP")
        qro = crear_sucursal(db, tenant_id=tid, cliente_id=cli1.id, nombre="QRO")
        otra = crear_sucursal(db, tenant_id=tid, cliente_id=cli1.id, nombre="Otra")
        db.flush()

        # overrides: cliente fija $20; SLP $15; QRO $12 (otra hereda del cliente)
        db.add(PrecioOverride(tenant_id=tid, cliente_id=cli1.id, producto_id=prod.id, presentacion="KILO", precio_unitario=Decimal("20")))
        db.add(PrecioOverride(tenant_id=tid, sucursal_id=slp.id, producto_id=prod.id, presentacion="KILO", precio_unitario=Decimal("15")))
        db.add(PrecioOverride(tenant_id=tid, sucursal_id=qro.id, producto_id=prod.id, presentacion="KILO", precio_unitario=Decimal("12")))
        db.commit()

        yield {"admin": admin, "tomador": tomador, "aguacate": str(prod.id),
               "cli1": str(cli1.id), "cli3": str(cli3.id),
               "slp": str(slp.id), "qro": str(qro.id), "otra": str(otra.id),
               "unico": str(unico.id), "menudeo": str(menudeo.id)}
    finally:
        for table in _PURGE:
            for t in created["tenants"]:
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": t})
        for mid in created["memberships"]:
            db.query(Membership).filter(Membership.id == mid).delete()
        for uid in created["users"]:
            db.query(User).filter(User.id == uid).delete()
        for t in created["tenants"]:
            db.query(Tenant).filter(Tenant.id == t).delete()
        db.commit(); db.close()


@pytest.fixture
def auth_as():
    def _set(user):
        app.dependency_overrides[get_principal] = lambda: Principal(
            auth_user_id=user["sub"], email=user["email"], role="authenticated", claims={"sub": user["sub"]})
    yield _set
    app.dependency_overrides.pop(get_principal, None)


def _hdr(u):
    return {"X-Tenant-Id": str(u["tenant_id"])}


def _cot(client, h, pid, **params):
    return client.get("/api/v1/precios/cotizar", headers=h, params={"producto_id": pid, **params}).json()


def test_resolucion_por_prioridad(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]

    base = _cot(client, h, pid)
    assert float(base["precio"]) == 25.0 and base["origen"] == "lista_base"

    c1 = _cot(client, h, pid, cliente_id=env["cli1"])
    assert float(c1["precio"]) == 20.0 and c1["origen"] == "override_cliente"

    slp = _cot(client, h, pid, sucursal_id=env["slp"])
    assert float(slp["precio"]) == 15.0 and slp["origen"] == "override_sucursal"

    qro = _cot(client, h, pid, sucursal_id=env["qro"])
    assert float(qro["precio"]) == 12.0

    # sucursal sin override → hereda el precio del cliente ($20). La plaza ya
    # no tiene dueño: el cliente viaja explícito, como en los flujos reales.
    otra = _cot(client, h, pid, cliente_id=env["cli1"], sucursal_id=env["otra"])
    assert float(otra["precio"]) == 20.0 and otra["origen"] == "override_cliente"


def test_tiers_por_volumen(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]
    # cli3 usa lista MENUDEO: 1–9 = $25, ≥10 = $20 (acceso a mayoreo por volumen)
    assert float(_cot(client, h, pid, cliente_id=env["cli3"], cantidad="5")["precio"]) == 25.0
    assert float(_cot(client, h, pid, cliente_id=env["cli3"], cantidad="15")["precio"]) == 20.0


def test_sucursal_crud(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/sucursales", headers=h, json={"cliente_id": env["cli1"], "nombre": "CDMX"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert client.get("/api/v1/sucursales", headers=h, params={"cliente_id": env["cli1"]}).json()["total"] == 4
    assert client.patch(f"/api/v1/sucursales/{sid}", headers=h, json={"nombre": "CDMX Centro"}).json()["nombre"] == "CDMX Centro"
    assert client.delete(f"/api/v1/sucursales/{sid}", headers=h).status_code == 204


def test_override_dimensiones(client, env, auth_as):
    """Al menos una dimensión; ambas = el precio de ESE cliente en ESA plaza
    (rediseño 01-sep-2026: la plaza es compartida, así que el par es el override
    más específico y le gana al de la plaza sola)."""
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]
    # ni cliente ni sucursal → 422
    assert client.post("/api/v1/precios/overrides", headers=h, json={
        "producto_id": pid, "precio_unitario": "10"}).status_code == 422
    # solo cliente → 201
    assert client.post("/api/v1/precios/overrides", headers=h, json={
        "cliente_id": env["cli3"], "producto_id": pid, "precio_unitario": "10"}).status_code == 201
    # ambos → 201, y gana sobre el de la plaza sola ($15) SOLO para ese cliente
    assert client.post("/api/v1/precios/overrides", headers=h, json={
        "cliente_id": env["cli1"], "sucursal_id": env["slp"],
        "producto_id": pid, "precio_unitario": "10"}).status_code == 201
    exacto = _cot(client, h, pid, cliente_id=env["cli1"], sucursal_id=env["slp"])
    assert float(exacto["precio"]) == 10.0 and exacto["origen"] == "override_sucursal"
    # otro cliente en la misma plaza sigue viendo el de la plaza
    plaza = _cot(client, h, pid, cliente_id=env["cli3"], sucursal_id=env["slp"])
    assert float(plaza["precio"]) == 15.0 and plaza["origen"] == "override_sucursal"


def test_copiar_precios(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    # nueva lista vacía
    dest = client.post("/api/v1/listas-precios", headers=h,
                       json={"codigo": "COPIA", "nombre": "Copia"}).json()["id"]
    # MENUDEO tiene 2 precios (tier 1 y tier 10) → ambos se copian
    r = client.post(f"/api/v1/listas-precios/{dest}/copiar", headers=h,
                    json={"origen_id": env["menudeo"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"created": 2, "updated": 0, "skipped": 0}
    assert client.get(f"/api/v1/listas-precios/{dest}/precios", headers=h).json()["total"] == 2
    # copiar de nuevo → todo duplicado, nada creado
    r2 = client.post(f"/api/v1/listas-precios/{dest}/copiar", headers=h,
                     json={"origen_id": env["menudeo"]})
    assert r2.json() == {"created": 0, "updated": 0, "skipped": 2}
    # origen == destino → 422
    assert client.post(f"/api/v1/listas-precios/{dest}/copiar", headers=h,
                       json={"origen_id": dest}).status_code == 422


def test_copiar_rbac(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    assert client.post(f"/api/v1/listas-precios/{env['unico']}/copiar", headers=h,
                       json={"origen_id": env["menudeo"]}).status_code == 403


def test_bulk_upsert_precios(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    dest = client.post("/api/v1/listas-precios", headers=h,
                       json={"codigo": "BULK", "nombre": "Bulk"}).json()["id"]
    items = [
        {"producto_id": env["aguacate"], "presentacion": "KILO", "precio_unitario": "30", "cantidad_minima": 1},
        {"producto_id": env["aguacate"], "presentacion": "KILO", "precio_unitario": "27", "cantidad_minima": 10},
    ]
    r = client.post(f"/api/v1/listas-precios/{dest}/precios/bulk", headers=h, json={"items": items})
    assert r.status_code == 200, r.text
    assert r.json() == {"created": 2, "updated": 0, "skipped": 0}
    # re-enviar con un precio cambiado → se actualiza, no duplica
    items[0]["precio_unitario"] = "31"
    r2 = client.post(f"/api/v1/listas-precios/{dest}/precios/bulk", headers=h, json={"items": items})
    assert r2.json() == {"created": 0, "updated": 2, "skipped": 0}
    page = client.get(f"/api/v1/listas-precios/{dest}/precios", headers=h).json()
    assert page["total"] == 2
    tier1 = next(p for p in page["items"] if p["cantidad_minima"] == 1)
    assert float(tier1["precio_unitario"]) == 31.0


def test_bulk_rbac(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    assert client.post(f"/api/v1/listas-precios/{env['unico']}/precios/bulk", headers=h,
                       json={"items": []}).status_code == 403


def test_rbac(client, env, auth_as):
    auth_as(env["tomador"]); h = _hdr(env["tomador"])
    # TOMADOR puede cotizar (menu:productos)…
    assert client.get("/api/v1/precios/cotizar", headers=h,
                      params={"producto_id": env["aguacate"]}).status_code == 200
    # …pero no crear sucursales (cliente:gestionar) ni overrides (lista_precios:gestionar)
    assert client.post("/api/v1/sucursales", headers=h,
                       json={"cliente_id": env["cli1"], "nombre": "X"}).status_code == 403
    assert client.post("/api/v1/precios/overrides", headers=h, json={
        "cliente_id": env["cli1"], "producto_id": env["aguacate"], "precio_unitario": "1"}).status_code == 403


# ─── Asignación de listas: cliente · sucursal · serie · proyecto ─────────────
# El caso real: por el grupo de Pachuca entran EHMO y MAFAN, cada uno con su
# serie y sus proyectos, y cada combinación se negoció a un precio distinto.
def _crear_escenario(client, h, env):
    """Un cliente limpio, cuatro listas a cuatro precios, su serie y su proyecto.

    Cliente propio a propósito: los del fixture ya traen overrides por producto
    o una lista asignada, y taparían justo lo que se quiere medir.
    """
    cliente = client.post("/api/v1/clientes", headers=h, json={
        "legal_name": "EHMO SA de CV", "rfc": "XAXX010101000"}).json()["id"]
    listas = {}
    for codigo, precio in (("GLOBAL", "30"), ("PLAZA", "28"), ("SERIE", "26"), ("PROY", "24")):
        lid = client.post("/api/v1/listas-precios", headers=h,
                          json={"codigo": codigo, "nombre": f"Lista {codigo}"}).json()["id"]
        client.post(f"/api/v1/listas-precios/{lid}/precios", headers=h, json={
            "producto_id": env["aguacate"], "presentacion": "KILO",
            "precio_unitario": precio, "cantidad_minima": 1})
        listas[codigo] = lid
    serie = client.post("/api/v1/series", headers=h, json={
        "codigo": "ZEHMOHOS", "tipo": "FISCAL", "tipo_documento": "FACTURA"}).json()
    proyecto = client.post("/api/v1/proyectos", headers=h, json={
        "nombre": "Hospitales e IMSS Bienestar", "cliente_id": cliente}).json()
    return cliente, listas, serie, proyecto


def test_asignacion_gana_la_mas_especifica(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]
    ehmo, listas, serie, proyecto = _crear_escenario(client, h, env)
    suc = client.post("/api/v1/sucursales", headers=h,
                      json={"cliente_id": ehmo, "nombre": "Pachuca"}).json()["id"]

    def asignar(lista, **dims):
        r = client.post("/api/v1/asignaciones-precios", headers=h,
                        json={"lista_id": listas[lista], **dims})
        assert r.status_code == 201, r.text
        return r.json()

    asignar("GLOBAL", cliente_id=ehmo)
    # El cliente solo: mismos precios en todo el país.
    assert float(_cot(client, h, pid, cliente_id=ehmo)["precio"]) == 30.0
    assert _cot(client, h, pid, cliente_id=ehmo)["origen"] == "lista_cliente"

    asignar("PLAZA", cliente_id=ehmo, sucursal_id=suc)
    plaza = _cot(client, h, pid, cliente_id=ehmo, sucursal_id=suc)
    assert float(plaza["precio"]) == 28.0 and plaza["origen"] == "lista_sucursal"
    # …y un documento SIN sucursal sigue en la del cliente: el renglón de plaza
    # tiene la dimensión llena, así que no aplica a lo que no la trae.
    assert float(_cot(client, h, pid, cliente_id=ehmo)["precio"]) == 30.0

    asignar("SERIE", cliente_id=ehmo, serie_id=serie["id"])
    con_serie = _cot(client, h, pid, cliente_id=ehmo, sucursal_id=suc, serie_id=serie["id"])
    assert float(con_serie["precio"]) == 26.0 and con_serie["origen"] == "lista_serie"

    asignar("PROY", cliente_id=ehmo, proyecto_id=proyecto["id"])
    con_proy = _cot(client, h, pid, cliente_id=ehmo, sucursal_id=suc, serie_id=serie["id"],
                    proyecto_id=proyecto["id"])
    assert float(con_proy["precio"]) == 24.0 and con_proy["origen"] == "lista_proyecto"


def test_lista_parcial_cae_a_la_siguiente_negociacion(client, env, auth_as):
    """Una lista negociada puede ser PARCIAL (la del proyecto DIF trae solo lo
    pactado para DIF): si no trae el producto, el precio correcto es el de la
    siguiente asignación que aplique (la del cliente), NO la lista base."""
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]
    ehmo, listas, serie, proyecto = _crear_escenario(client, h, env)

    # lista del PROYECTO vacía para este producto (solo existe la lista, sin precios)
    vacia = client.post("/api/v1/listas-precios", headers=h,
                        json={"codigo": "PARCIAL", "nombre": "Proyecto parcial"}).json()["id"]
    for lista, dims in (("GLOBAL", {"cliente_id": ehmo}),):
        assert client.post("/api/v1/asignaciones-precios", headers=h,
                           json={"lista_id": listas[lista], **dims}).status_code == 201
    assert client.post("/api/v1/asignaciones-precios", headers=h,
                       json={"lista_id": vacia, "cliente_id": ehmo,
                             "proyecto_id": proyecto["id"]}).status_code == 201

    r = _cot(client, h, pid, cliente_id=ehmo, proyecto_id=proyecto["id"])
    # el proyecto ganó la especificidad, pero su lista no trae el producto:
    # cae a la negociación del cliente (30.0), no a la lista base
    assert float(r["precio"]) == 30.0
    assert r["origen"] == "lista_cliente"


def test_simular_dice_que_asignacion_ganaria(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    ehmo, listas, serie, proyecto = _crear_escenario(client, h, env)
    client.post("/api/v1/asignaciones-precios", headers=h,
                json={"lista_id": listas["GLOBAL"], "cliente_id": ehmo})
    client.post("/api/v1/asignaciones-precios", headers=h,
                json={"lista_id": listas["PROY"], "cliente_id": ehmo,
                      "proyecto_id": proyecto["id"]})

    solo_cliente = client.get("/api/v1/asignaciones-precios/simular", headers=h,
                              params={"cliente_id": ehmo}).json()
    assert solo_cliente["lista_id"] == listas["GLOBAL"] and solo_cliente["especificidad"] == 1

    con_proyecto = client.get("/api/v1/asignaciones-precios/simular", headers=h,
                              params={"cliente_id": ehmo, "proyecto_id": proyecto["id"]}).json()
    assert con_proyecto["lista_id"] == listas["PROY"]
    assert con_proyecto["especificidad"] == 9          # proyecto 8 + cliente 1
    assert con_proyecto["proyecto_nombre"] == "Hospitales e IMSS Bienestar"

    # Un cliente sin ninguna asignación: null, no un renglón inventado.
    assert client.get("/api/v1/asignaciones-precios/simular", headers=h,
                      params={"cliente_id": env["cli1"]}).json() is None


def test_asignacion_incoherente_se_rechaza(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    ehmo, listas, _serie, proyecto = _crear_escenario(client, h, env)
    # `slp` es sucursal de cli1; el proyecto es de EHMO. Cruzarlos daría un
    # renglón que no puede aplicar nunca — y que en la tabla parecería activo.
    r = client.post("/api/v1/asignaciones-precios", headers=h, json={
        "lista_id": listas["PLAZA"], "cliente_id": ehmo, "sucursal_id": env["slp"]})
    assert r.status_code == 422 and "sucursal" in r.json()["detail"]
    r = client.post("/api/v1/asignaciones-precios", headers=h, json={
        "lista_id": listas["PROY"], "cliente_id": env["cli1"], "proyecto_id": proyecto["id"]})
    assert r.status_code == 422 and "proyecto" in r.json()["detail"]


def test_asignacion_duplicada_da_409(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    ehmo, listas, _serie, _proyecto = _crear_escenario(client, h, env)
    body = {"lista_id": listas["GLOBAL"], "cliente_id": ehmo}
    assert client.post("/api/v1/asignaciones-precios", headers=h, json=body).status_code == 201
    assert client.post("/api/v1/asignaciones-precios", headers=h, json=body).status_code == 409
    # Renovar la MISMA combinación con otra vigencia sí se puede.
    assert client.post("/api/v1/asignaciones-precios", headers=h, json={
        **body, "lista_id": listas["PLAZA"], "vigencia_desde": "2027-01-01"}).status_code == 201


def test_borrar_proyecto_se_lleva_sus_asignaciones(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]
    ehmo, listas, _serie, proyecto = _crear_escenario(client, h, env)
    client.post("/api/v1/asignaciones-precios", headers=h,
                json={"lista_id": listas["GLOBAL"], "cliente_id": ehmo})
    client.post("/api/v1/asignaciones-precios", headers=h,
                json={"lista_id": listas["PROY"], "cliente_id": ehmo,
                      "proyecto_id": proyecto["id"]})
    assert float(_cot(client, h, pid, cliente_id=ehmo,
                      proyecto_id=proyecto["id"])["precio"]) == 24.0

    assert client.delete(f"/api/v1/proyectos/{proyecto['id']}", headers=h).status_code == 204
    # El proyecto archivado ya no fija precios: se cae a la del cliente.
    assert float(_cot(client, h, pid, cliente_id=ehmo,
                      proyecto_id=proyecto["id"])["precio"]) == 30.0


def test_proyecto_codigo_se_autogenera(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/proyectos", headers=h,
                    json={"nombre": "Ceresos y Seguridad Pública"})
    assert r.status_code == 201, r.text
    assert r.json()["codigo"] == "CERESOSYSE"
    # Mismo nombre otra vez → sufijo, no choque contra el índice único.
    otro = client.post("/api/v1/proyectos", headers=h,
                       json={"nombre": "Ceresos y Seguridad Pública"})
    assert otro.status_code == 201 and otro.json()["codigo"] == "CERESOSYSE2"


def test_cotizar_reporta_el_tramo_aplicado(client, env, auth_as):
    """La pantalla de la bandeja ofrece «actualizar la lista» solo apuntando al
    TRAMO del que salió la referencia: cotizar lo reporta. Con cantidad 15 el
    que habla es el tramo de mayoreo (≥10), no el base."""
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]
    mayoreo = _cot(client, h, pid, cliente_id=env["cli3"], cantidad="15")
    assert float(mayoreo["precio"]) == 20.0
    assert mayoreo["cantidad_minima"] == 10
    base = _cot(client, h, pid, cliente_id=env["cli3"], cantidad="5")
    assert float(base["precio"]) == 25.0
    assert base["cantidad_minima"] == 1
    # Un override no es una lista: sin tramo que actualizar.
    ov = _cot(client, h, pid, cliente_id=env["cli1"])
    assert ov["origen"] == "override_cliente" and ov["cantidad_minima"] is None


# ── presentaciones: la lista no guarda precios que nadie puede cobrar ─────────
#
# El desplegable de presentación (en la remisión y en la propia lista) se arma
# de `producto.presentaciones`. Un precio en CAJA sobre un producto que solo
# maneja KILO es una fila que se ve configurada y no se cobra jamás.


def test_precio_rechaza_presentacion_que_el_producto_no_declara(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post(f"/api/v1/listas-precios/{env['unico']}/precios", headers=h, json={
        "producto_id": env["aguacate"], "presentacion": "CAJA",
        "precio_unitario": "400", "cantidad_minima": 1,
    })
    assert r.status_code == 422
    assert "CAJA" in r.json()["detail"]


def test_bulk_rechaza_presentacion_que_el_producto_no_declara(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post(f"/api/v1/listas-precios/{env['unico']}/precios/bulk", headers=h, json={
        "items": [{"producto_id": env["aguacate"], "presentacion": "CAJA",
                   "precio_unitario": "400", "cantidad_minima": 1}],
    })
    assert r.status_code == 422


def test_override_rechaza_presentacion_que_el_producto_no_declara(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/precios/overrides", headers=h, json={
        "cliente_id": env["cli1"], "producto_id": env["aguacate"],
        "presentacion": "CAJA", "precio_unitario": "400",
    })
    assert r.status_code == 422


def test_agregar_presentacion_abre_su_precio_propio(client, env, auth_as):
    """La historia completa: CAJA de 20 KILO, primero derivada y luego negociada."""
    auth_as(env["admin"]); h = _hdr(env["admin"]); pid = env["aguacate"]

    r = client.post(f"/api/v1/productos/{pid}/presentaciones", headers=h,
                    json={"nombre": "caja", "factor": "20"})
    assert r.status_code == 200
    pres = r.json()["presentaciones"]
    assert pres["CAJA"]["factor"] == 20            # se normaliza a mayúsculas
    assert pres["CAJA"]["sat"] == "XBX"            # unidad SAT deducida
    assert "KILO" in pres                          # no pisó lo que ya tenía

    # Sin renglón propio, la caja vale el kilo × el factor.
    assert float(_cot(client, h, pid, presentacion="CAJA")["precio"]) == 500.0

    # Con renglón propio, manda el negociado (la caja sale más barata por kilo).
    r = client.post(f"/api/v1/listas-precios/{env['unico']}/precios", headers=h, json={
        "producto_id": pid, "presentacion": "CAJA",
        "precio_unitario": "400", "cantidad_minima": 1,
    })
    assert r.status_code == 201
    caja = _cot(client, h, pid, presentacion="CAJA")
    assert float(caja["precio"]) == 400.0
    assert caja["cantidad_minima"] == 1             # tramo que habló: el de CAJA
    # Y el kilo sigue costando lo de siempre.
    assert float(_cot(client, h, pid)["precio"]) == 25.0


def test_agregar_presentacion_rechaza_la_que_ya_existe(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post(f"/api/v1/productos/{env['aguacate']}/presentaciones", headers=h,
                    json={"nombre": "KILO", "factor": "1"})
    assert r.status_code == 422


def test_agregar_presentacion_exige_factor_positivo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post(f"/api/v1/productos/{env['aguacate']}/presentaciones", headers=h,
                    json={"nombre": "CAJA", "factor": "0"})
    assert r.status_code == 422


def test_override_se_reescribe_en_vez_de_apilarse(client, env, auth_as):
    """Corregir el precio especial dos veces deja UN renglón, no tres.

    La tabla no tiene índice único y el resolutor lee el más reciente: sin esto
    cada corrección dejaba basura que se relee en cada cotización.
    """
    auth_as(env["admin"]); h = _hdr(env["admin"])
    cuerpo = {"cliente_id": env["cli1"], "producto_id": env["aguacate"],
              "presentacion": "KILO", "precio_unitario": "18"}

    primero = client.post("/api/v1/precios/overrides", headers=h, json=cuerpo)
    assert primero.status_code == 201
    segundo = client.post("/api/v1/precios/overrides", headers=h,
                          json={**cuerpo, "precio_unitario": "17"})
    assert segundo.status_code == 201
    assert segundo.json()["id"] == primero.json()["id"]
    assert float(segundo.json()["precio_unitario"]) == 17.0

    listado = client.get("/api/v1/precios/overrides", headers=h, params={
        "cliente_id": env["cli1"], "producto_id": env["aguacate"]}).json()
    assert listado["total"] == 1
    assert float(_cot(client, h, env["aguacate"], cliente_id=env["cli1"])["precio"]) == 17.0

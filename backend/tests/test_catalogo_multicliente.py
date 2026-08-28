"""Catálogo multicliente: alias con alcance, cruce por clave y remisión de un clic.

El modelo que estas pruebas defienden: UN producto canónico por cosa física;
`producto_clientes` es la vista de cada cliente (su clave y su nombre, los que
se imprimen y timbran); `producto_alias` es el vocabulario de entrada — GLOBAL
por defecto, con alcance por cliente SOLO cuando el mismo texto significa cosas
distintas. La forma de nombrar del cliente jamás crea un producto: el alta
individual pasa por el detector de duplicados y el SKU interno es numérico del
servidor. La bandeja /oc cruza por clave del cliente antes que por texto, y una
orden que cruza COMPLETA por vías deterministas (clave, alias, exacto) con
precio de lista negociada se vuelve remisión con un clic.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Almacen,
    Cliente,
    ListaAsignacion,
    ListaPrecios,
    Membership,
    Precio,
    Producto,
    ProductoAlias,
    ProductoCliente,
    Role,
    Sucursal,
    Tenant,
    User,
)
from app.services.producto_match import normalizar_unidad

_PURGE = (
    "oc_recibidas", "cliente_externos", "lineas_remision", "remisiones",
    "movimientos_inventario", "lotes_inventario", "producto_alias",
    "producto_clientes", "lista_asignaciones", "precios", "listas_precios",
    "productos", "almacenes", "sucursales", "clientes",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"mc-{suffix}", legal_name="Multicliente SA",
                   rfc=f"MC{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        admin_role = db.query(Role).filter(Role.nombre == "ADMIN", Role.es_preset.is_(True)).one()
        sub = f"sub-mc-{suffix}"
        u = User(email=f"mc-{suffix}@t.test", auth_user_id=sub, full_name="mc")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=admin_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)
        admin = {"sub": sub, "email": u.email, "tenant_id": t.id}
        # Quien captura remisiones pero NO gestiona catálogo: es el alcance de
        # la clave de conexión del bot (remision:gestionar + menu:productos).
        capt_role = db.query(Role).filter(
            Role.nombre == "CAPTURISTA_GOV", Role.es_preset.is_(True)
        ).one()
        sub_c = f"sub-mc-capt-{suffix}"
        uc = User(email=f"mc-capt-{suffix}@t.test", auth_user_id=sub_c, full_name="capt")
        db.add(uc); db.flush(); created["users"].append(uc.id)
        mc = Membership(tenant_id=t.id, user_id=uc.id, role_id=capt_role.id)
        db.add(mc); db.flush(); created["memberships"].append(mc.id)
        capturista = {"sub": sub_c, "email": uc.email, "tenant_id": t.id}

        balles = Cliente(tenant_id=t.id, codigo="BA", legal_name="OPERADORA BALLES",
                         rfc="OBV191007BS1")
        ehmo = Cliente(tenant_id=t.id, codigo="EH", legal_name="GRUPO EHMO",
                       rfc="GOA180712SF5")
        db.add_all([balles, ehmo]); db.flush()
        suc_tab = Sucursal(tenant_id=t.id, cliente_id=ehmo.id, codigo="TAB", nombre="Tabasco")
        cilantro = Producto(tenant_id=t.id, sku="00000282", nombre="CILANTRO",
                            clave_sat="50403700", unidad_sat="KGM",
                            unidad_base="KILO", presentaciones={"KILO": 1, "MANOJO": 0.1})
        serrano = Producto(tenant_id=t.id, sku="00000301", nombre="CHILE SERRANO",
                           clave_sat="50402600", unidad_sat="KGM",
                           unidad_base="KILO", presentaciones={"KILO": 1})
        jalapeno = Producto(tenant_id=t.id, sku="00000302", nombre="CHILE JALAPENO",
                            clave_sat="50402600", unidad_sat="KGM",
                            unidad_base="KILO", presentaciones={"KILO": 1})
        alm = Almacen(tenant_id=t.id, codigo="MC-BG", nombre="Bodega MC")
        db.add_all([suc_tab, cilantro, serrano, jalapeno, alm]); db.flush()
        # La clave con la que Balles conoce el cilantro (su catálogo).
        db.add(ProductoCliente(
            tenant_id=t.id, cliente_id=balles.id, producto_id=cilantro.id,
            codigo_cliente="CILA-FRUT-145", nombre_cliente="CILANTRO MANOJO DE 1 KG",
            presentacion="KILO",
        ))
        # Lista NEGOCIADA de Balles con precio del cilantro, y lista base del
        # tenant que solo tiene el chile — para probar que la lista base jamás
        # alimenta una remisión automática.
        lista = ListaPrecios(tenant_id=t.id, codigo="BALLES", nombre="Lista Balles")
        base = ListaPrecios(tenant_id=t.id, codigo="UNICO", nombre="Base", es_default=True)
        db.add_all([lista, base]); db.flush()
        db.add_all([
            Precio(tenant_id=t.id, lista_id=lista.id, producto_id=cilantro.id,
                   presentacion="KILO", precio_unitario=Decimal("32.50")),
            Precio(tenant_id=t.id, lista_id=base.id, producto_id=serrano.id,
                   presentacion="KILO", precio_unitario=Decimal("48.00")),
            Precio(tenant_id=t.id, lista_id=base.id, producto_id=cilantro.id,
                   presentacion="KILO", precio_unitario=Decimal("99.99")),
        ])
        db.add(ListaAsignacion(tenant_id=t.id, lista_id=lista.id, cliente_id=balles.id))
        db.commit()
        yield {"admin": admin, "capturista": capturista, "tenant": str(t.id),
               "balles": str(balles.id), "ehmo": str(ehmo.id), "suc_tab": str(suc_tab.id),
               "cilantro": str(cilantro.id), "serrano": str(serrano.id),
               "jalapeno": str(jalapeno.id), "alm": str(alm.id)}
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


# ─── unidad del documento → presentación ─────────────────────────────────────

def test_normalizar_unidad_traduce_el_ocr_sucio():
    assert normalizar_unidad("KILOGR AMO") == "KILO"
    assert normalizar_unidad("kg") == "KILO"
    assert normalizar_unidad("PIEZA") == "PIEZA"
    assert normalizar_unidad("MJ") == "MANOJO"
    assert normalizar_unidad("GARRA FON") == "GARRAFON"
    # Lo que no se reconoce NO se adivina.
    assert normalizar_unidad("XBH") is None
    assert normalizar_unidad("") is None
    assert normalizar_unidad(None) is None


# ─── alias con alcance ───────────────────────────────────────────────────────

def test_alias_global_por_defecto_y_alcance_en_conflicto(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    # Texto nuevo → alias GLOBAL (le sirve a todos los clientes).
    r = client.post("/api/v1/productos/alias", headers=h,
                    json={"texto": "chile tampico", "producto_id": env["serrano"]})
    assert r.status_code == 201
    # El mismo texto, pero como vocabulario PRIVADO de EHMO hacia otro producto:
    # convive con el global sin tocarlo.
    r = client.post("/api/v1/productos/alias", headers=h,
                    json={"texto": "chile tampico", "producto_id": env["jalapeno"],
                          "cliente_id": env["ehmo"]})
    assert r.status_code == 201

    db = SessionLocal()
    try:
        filas = (
            db.query(ProductoAlias)
            .filter(ProductoAlias.alias_normalizado == "chile tampico")
            .all()
        )
        por_alcance = {f.cliente_id: str(f.producto_id) for f in filas}
        assert por_alcance[None] == env["serrano"]          # el global no se movió
        assert por_alcance[uuid.UUID(env["ehmo"])] == env["jalapeno"]
    finally:
        db.close()


def test_el_cruce_de_la_bandeja_prefiere_el_vocabulario_del_cliente(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/productos/alias", headers=h,
                json={"texto": "chile verde", "producto_id": env["serrano"]})
    client.post("/api/v1/productos/alias", headers=h,
                json={"texto": "chile verde", "producto_id": env["jalapeno"],
                      "cliente_id": env["ehmo"]})

    def _oc_de(rfc, origen):
        return client.post("/api/v1/oc-recibidas", headers=h, json={
            "canal": "WHATSAPP", "origen_externo": origen, "rfc": rfc,
            "lineas": [{"descripcion": "CHILE VERDE", "cantidad": "5", "unidad": "KG"}],
        }).json()

    # Equivalencias para que la ingesta resuelva el cliente por RFC.
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "GOA180712SF5", "cliente_id": env["ehmo"]})

    oc_balles = _oc_de("OBV191007BS1", f"WA:x:{uuid.uuid4().hex[:6]}")
    oc_ehmo = _oc_de("GOA180712SF5", f"WA:x:{uuid.uuid4().hex[:6]}")

    top_balles = oc_balles["lineas"][0]["candidatos"][0]
    top_ehmo = oc_ehmo["lineas"][0]["candidatos"][0]
    # Balles usa el global (serrano); EHMO su vocabulario privado (jalapeño).
    assert top_balles["producto_id"] == env["serrano"] and top_balles["origen"] == "alias"
    assert top_ehmo["producto_id"] == env["jalapeno"] and top_ehmo["origen"] == "alias"


# ─── cruce por clave del cliente en la bandeja ───────────────────────────────

def test_la_clave_del_cliente_cruza_al_100(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    oc = client.post("/api/v1/oc-recibidas", headers=h, json={
        "canal": "WHATSAPP", "origen_externo": f"WA:x:{uuid.uuid4().hex[:6]}",
        "rfc": "OBV191007BS1",
        # La clave llega sucia del OCR: espacios de más y minúsculas.
        "lineas": [{"descripcion": "CILANTRO MANOJO DE 1 KG", "cantidad": "10",
                    "unidad": "KILOGR AMO", "clave": "cila -frut-145"}],
    }).json()
    linea = oc["lineas"][0]
    assert linea["presentacion_sugerida"] == "KILO"
    top = linea["candidatos"][0]
    assert top["producto_id"] == env["cilantro"]
    assert top["origen"] == "codigo_cliente" and top["score"] == 100


# ─── remisión automática de un clic ──────────────────────────────────────────

def _oc_auto(client, h, origen, lineas):
    return client.post("/api/v1/oc-recibidas", headers=h, json={
        "canal": "WHATSAPP", "origen_externo": origen, "rfc": "OBV191007BS1",
        "folio_externo": "777", "lineas": lineas,
    }).json()


def test_oc_determinista_se_vuelve_remision_con_un_clic(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CILANTRO MANOJO DE 1 KG", "cantidad": "10",
         "unidad": "KG", "clave": "CILA-FRUT-145", "precio": "32.50"},
    ])
    assert oc["auto"]["ok"], oc["auto"]["motivo"]
    linea = oc["auto"]["lineas"][0]
    assert linea["cruzo_por"] == "codigo_cliente"
    assert Decimal(str(linea["precio_unitario"])) == Decimal("32.50")
    assert linea["precio_origen"] == "lista_cliente"

    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-auto", headers=h)
    assert r.status_code == 200, r.text
    hecho = r.json()
    assert hecho["estado"] == "ASIGNADA" and hecho["remision_id"]

    rem = client.get(f"/api/v1/remisiones/{hecho['remision_id']}", headers=h).json()
    assert float(rem["subtotal"]) == 325.0


def test_precio_de_lista_base_no_es_automatico(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    client.post("/api/v1/productos/alias", headers=h,
                json={"texto": "chile de arbol verde", "producto_id": env["serrano"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CHILE DE ARBOL VERDE", "cantidad": "5", "unidad": "KG"},
    ])
    # El serrano solo tiene precio en la lista BASE → jamás automático.
    assert not oc["auto"]["ok"]
    assert "lista" in oc["auto"]["motivo"]
    assert client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-auto", headers=h
    ).status_code == 409


def test_precio_en_conflicto_no_es_automatico(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CILANTRO MANOJO DE 1 KG", "cantidad": "10",
         "unidad": "KG", "clave": "CILA-FRUT-145", "precio": "30.00"},
    ])
    assert not oc["auto"]["ok"]
    assert "conflicto" in oc["auto"]["motivo"]


def test_lo_difuso_jamas_decide_solo(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CILANTROO", "cantidad": "2", "unidad": "KG"},  # typo → difuso
    ])
    assert not oc["auto"]["ok"]


def test_confirmar_en_bandeja_aprende_alias_y_registra_la_clave(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "GOA180712SF5", "cliente_id": env["ehmo"]})
    oc = client.post("/api/v1/oc-recibidas", headers=h, json={
        "canal": "WHATSAPP", "origen_externo": f"WA:x:{uuid.uuid4().hex[:6]}",
        "rfc": "GOA180712SF5",
        "lineas": [{"descripcion": "RAMO DE CILANTRO", "cantidad": "3",
                    "unidad": "KG", "clave": "CILANTROKG"}],
    }).json()
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["cilantro"], "cantidad": "3",
                    "presentacion": "KILO", "precio_unitario": "20.00",
                    "texto_original": "RAMO DE CILANTRO", "clave": "CILANTROKG"}],
    })
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        alias = (
            db.query(ProductoAlias)
            .filter(ProductoAlias.alias_normalizado == "ramo de cilantro")
            .one()
        )
        # Texto nuevo → se aprendió GLOBAL (regla: alcance solo en conflicto).
        assert alias.cliente_id is None
        assert str(alias.producto_id) == env["cilantro"]
        pc = (
            db.query(ProductoCliente)
            .filter(
                ProductoCliente.cliente_id == uuid.UUID(env["ehmo"]),
                ProductoCliente.producto_id == uuid.UUID(env["cilantro"]),
            )
            .one()
        )
        # La clave del documento quedó como código de EHMO para el cilantro.
        assert pc.codigo_cliente == "CILANTROKG"
    finally:
        db.close()


# ─── alta con detector de duplicados y SKU del servidor ──────────────────────

def test_alta_con_candidato_fuerte_exige_decidir(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos", headers=h, json={
        "nombre": "CILANTRO", "clave_sat": "50403700", "unidad_sat": "KGM"})
    assert r.status_code == 409
    detalle = r.json()["detail"]
    assert any(c["producto_id"] == env["cilantro"] for c in detalle["candidatos"])
    # A sabiendas, con forzar, sí se crea.
    r = client.post("/api/v1/productos", headers=h, json={
        "nombre": "CILANTRO DESHIDRATADO EN FRASCO", "clave_sat": "50403700",
        "unidad_sat": "KGM", "forzar": True})
    assert r.status_code == 201, r.text


def test_el_sku_interno_es_numerico_del_servidor(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos", headers=h, json={
        "sku": "CILA-FRUT-999", "nombre": "PRODUCTO CON CLAVE DE CLIENTE",
        "clave_sat": "50403700", "unidad_sat": "KGM", "forzar": True})
    assert r.status_code == 422
    r = client.patch(f"/api/v1/productos/{env['serrano']}", headers=h,
                     json={"sku": "CHIL-ESPE-041"})
    assert r.status_code == 422
    # Reenviar el MISMO sku no es cambiarlo: guardar el nombre de un producto
    # viejo con clave alfanumérica no puede tronar (la pantalla lo reenvía tal cual).
    r = client.patch(f"/api/v1/productos/{env['serrano']}", headers=h,
                     json={"sku": "00000301", "nombre": "CHILE SERRANO VERDE"})
    assert r.status_code == 200, r.text


# ─── lo que NO puede decidir solo (hallazgos de la revisión adversarial) ─────

def test_unidad_adivinada_no_es_automatica(client, env, auth_as):
    """Sin unidad legible, la presentación se adivina — y adivinar cambia el
    dinero: 10 MANOJO a 3.25 no es 10 KILO a 32.50."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    for unidad in (None, "REJA"):
        oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
            {"descripcion": "CILANTRO MANOJO DE 1 KG", "cantidad": "10",
             "unidad": unidad, "clave": "CILA-FRUT-145"},
        ])
        assert not oc["auto"]["ok"], f"unidad={unidad} no debería ser automática"
        assert "unidad" in oc["auto"]["motivo"]
        assert oc["lineas"][0]["presentacion_adivinada"] is True


def test_sin_unidad_sigue_siendo_automatico_con_una_sola_presentacion(client, env, auth_as):
    """El freno es contra la adivinanza, no contra el carril: si el producto se
    vende de una sola forma, no hay nada que adivinar."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    # El serrano solo tiene KILO; se le da precio en la lista negociada.
    lista = client.get("/api/v1/listas-precios?limit=50", headers=h).json()["items"]
    lista_balles = next(l for l in lista if l["codigo"] == "BALLES")
    client.post(f"/api/v1/listas-precios/{lista_balles['id']}/precios", headers=h, json={
        "producto_id": env["serrano"], "precio_unitario": "48.00", "cantidad_minima": 1})
    client.post("/api/v1/productos/alias", headers=h,
                json={"texto": "chile serranito", "producto_id": env["serrano"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CHILE SERRANITO", "cantidad": "5", "unidad": None},
    ])
    assert oc["auto"]["ok"], oc["auto"]["motivo"]


def test_clave_ambigua_del_propio_cliente_no_decide(client, env, auth_as):
    """Dos productos del MISMO cliente con la misma clave: la elige el orden de
    la tabla, así que nadie decide — igual que con la clave de otro cliente."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    # El mismo código apuntando también al serrano (renumeración del cliente).
    r = client.put(
        f"/api/v1/clientes/{env['balles']}/catalogo/{env['serrano']}", headers=h,
        json={"codigo_cliente": "CILA-FRUT-145", "nombre_cliente": "CHILE SERRANO"})
    assert r.status_code in (200, 201), r.text
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CILANTRO MANOJO DE 1 KG", "cantidad": "10",
         "unidad": "KG", "clave": "CILA-FRUT-145"},
    ])
    origenes = {c["origen"] for c in oc["lineas"][0]["candidatos"]}
    assert "codigo_cliente" not in origenes, "una clave ambigua no puede cruzar al 100"


def test_dos_productos_con_el_mismo_nombre_no_deciden(client, env, auth_as):
    """`buscar` devuelve un 100 por cada producto que normaliza igual — a
    propósito, para exhibir el duplicado. Eso lo resuelve un humano."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    # El duplicado que este mismo trabajo viene a evitar, pero que el catálogo
    # ya podría arrastrar: mismo nombre normalizado, otra fila.
    gemelo = client.post("/api/v1/productos", headers=h, json={
        "nombre": "Cilantro", "clave_sat": "50403700",
        "unidad_sat": "KGM", "unidad_base": "KILO",
        "presentaciones": {"KILO": 1}, "forzar": True}).json()
    lista = client.get("/api/v1/listas-precios?limit=50", headers=h).json()["items"]
    lista_balles = next(l for l in lista if l["codigo"] == "BALLES")
    client.post(f"/api/v1/listas-precios/{lista_balles['id']}/precios", headers=h, json={
        "producto_id": gemelo["id"], "precio_unitario": "45.00", "cantidad_minima": 1})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CILANTRO", "cantidad": "10", "unidad": "KG"},
    ])
    exactos = [c for c in oc["lineas"][0]["candidatos"] if c["score"] == 100]
    assert len(exactos) == 2, "el duplicado debe verse, no esconderse"
    assert not oc["auto"]["ok"]
    assert "100" in oc["auto"]["motivo"]


def test_la_clave_parecida_no_tapa_el_exacto_de_la_descripcion(client, env, auth_as):
    """Clave 'CHILE' + descripción exacta: el prefijo sobre la clave no puede
    desplazar al cruce exacto — es el falso positivo papa/papaya."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CHILE JALAPENO", "cantidad": "5", "unidad": "KG",
         "clave": "CHILE"},
    ])
    top = oc["lineas"][0]["candidatos"][0]
    assert top["producto_id"] == env["jalapeno"] and top["origen"] == "exacto"


def test_la_correccion_del_humano_pisa_el_alias_del_cliente(client, env, auth_as):
    """Un alias con alcance gana al cruzar; corregir en la bandeja tiene que
    caer ahí, o la corrección no surte efecto nunca."""
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/productos/alias", headers=h,
                json={"texto": "chile verde", "producto_id": env["serrano"]})
    client.post("/api/v1/productos/alias", headers=h,
                json={"texto": "chile verde", "producto_id": env["jalapeno"],
                      "cliente_id": env["ehmo"]})
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "GOA180712SF5", "cliente_id": env["ehmo"]})
    oc = client.post("/api/v1/oc-recibidas", headers=h, json={
        "canal": "WHATSAPP", "origen_externo": f"WA:x:{uuid.uuid4().hex[:6]}",
        "rfc": "GOA180712SF5",
        "lineas": [{"descripcion": "CHILE VERDE", "cantidad": "2", "unidad": "KG"}],
    }).json()
    # El operador corrige de vuelta al serrano (lo que dice el global).
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["serrano"], "cantidad": "2",
                    "presentacion": "KILO", "precio_unitario": "48.00",
                    "texto_original": "CHILE VERDE"}],
    })
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        alias = (
            db.query(ProductoAlias)
            .filter(ProductoAlias.alias_normalizado == "chile verde",
                    ProductoAlias.cliente_id == uuid.UUID(env["ehmo"]))
            .one()
        )
        assert str(alias.producto_id) == env["serrano"], "la corrección no llegó al alias del cliente"
    finally:
        db.close()


def test_sin_permiso_de_catalogo_no_se_escribe_lo_que_se_timbra(client, env, auth_as):
    """`codigo_cliente` y `nombre_cliente` son el NoIdentificacion y la
    Descripcion del CFDI. Quien solo captura remisiones —el alcance de la clave
    del bot— crea la remisión, pero no fija lo que se firma ante el SAT."""
    auth_as(env["capturista"]); h = _hdr(env["capturista"])
    oc = client.post("/api/v1/oc-recibidas", headers=h, json={
        "canal": "WHATSAPP", "origen_externo": f"WA:x:{uuid.uuid4().hex[:6]}",
        "nombre": "GRUPO EHMO",
        "lineas": [{"descripcion": "TEXTO DEL OCR", "cantidad": "3",
                    "unidad": "KG", "clave": "XX-999"}],
    }).json()
    client.patch(f"/api/v1/oc-recibidas/{oc['id']}", headers=h,
                 json={"cliente_id": env["ehmo"]})
    r = client.post(f"/api/v1/oc-recibidas/{oc['id']}/crear-remision", headers=h, json={
        "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["serrano"], "cantidad": "3",
                    "presentacion": "KILO", "precio_unitario": "48.00",
                    "texto_original": "TEXTO DEL OCR", "clave": "XX-999"}],
    })
    assert r.status_code == 200, r.text        # la remisión SÍ se crea
    db = SessionLocal()
    try:
        filas = (
            db.query(ProductoCliente)
            .filter(ProductoCliente.cliente_id == uuid.UUID(env["ehmo"]),
                    ProductoCliente.producto_id == uuid.UUID(env["serrano"]))
            .all()
        )
        assert not filas, "sin producto:gestionar no se escribe el catálogo del cliente"
    finally:
        db.close()


def test_alias_con_sucursal_inexistente_es_422(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/productos/alias", headers=h, json={
        "texto": "chilito", "producto_id": env["serrano"],
        "cliente_id": env["ehmo"], "sucursal_id": str(uuid.uuid4())})
    assert r.status_code == 422


def test_el_clic_respeta_el_almacen_elegido(client, env, auth_as):
    auth_as(env["admin"]); h = _hdr(env["admin"])
    client.post("/api/v1/clientes/externos", headers=h, json={
        "sistema": "RFC", "clave": "OBV191007BS1", "cliente_id": env["balles"]})
    oc = _oc_auto(client, h, f"WA:x:{uuid.uuid4().hex[:6]}", [
        {"descripcion": "CILANTRO MANOJO DE 1 KG", "cantidad": "10",
         "unidad": "KG", "clave": "CILA-FRUT-145", "precio": "32.50"},
    ])
    assert oc["auto"]["ok"], oc["auto"]["motivo"]
    r = client.post(
        f"/api/v1/oc-recibidas/{oc['id']}/crear-remision-auto?almacen_id={env['alm']}",
        headers=h)
    assert r.status_code == 200, r.text
    rem = client.get(f"/api/v1/remisiones/{r.json()['remision_id']}", headers=h).json()
    assert rem["almacen_id"] == env["alm"]


# ─── el PDF de la remisión habla el idioma del cliente ───────────────────────

def test_el_pdf_imprime_el_nombre_y_la_clave_del_cliente(client, env, auth_as):
    from app.api.v1.remisiones import _nombres_para_pdf
    from app.models import Remision

    auth_as(env["admin"]); h = _hdr(env["admin"])
    r = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": env["balles"], "almacen_id": env["alm"],
        "lineas": [{"producto_id": env["cilantro"], "cantidad_solicitada": "4",
                    "presentacion": "KILO"}],
    })
    assert r.status_code == 201, r.text
    rem_id = r.json()["id"]
    db = SessionLocal()
    try:
        rem = db.query(Remision).filter(Remision.id == uuid.UUID(rem_id)).one()
        nombres = _nombres_para_pdf(db, [rem])[rem.id]
        assert nombres[uuid.UUID(env["cilantro"])] == "CILA-FRUT-145 — CILANTRO MANOJO DE 1 KG"
    finally:
        db.close()
    # Y el endpoint del PDF sigue respondiendo.
    assert client.get(f"/api/v1/remisiones/{rem_id}/pdf", headers=h).status_code == 200

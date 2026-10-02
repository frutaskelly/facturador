"""Partidas del espejo SAE → producto por CLAVE SAE (2-oct-2026).

Hasta hoy el espejo solo cruzaba la CVE_ART contra el código del cliente
(`producto_clientes.codigo_cliente`) y el 61% de sus partidas quedaba sin
producto. Regla del dueño: «todo producto tiene clave SAE, se puede unificar
así» — producto + presentación = clave. Aquí se fija la regla completa
(services/espejo_productos.py): clave única, clave de presentación con SU unidad
SAT (Mini Conta cuenta kilo o pieza con ella), gemelos desempatados por remisión
ligada, catálogo del cliente y uso, la clave que en la empresa 03 es otra cosa,
el respaldo por código del cliente, que un reenvío no voltee nada, y que el
backfill escriba exactamente lo mismo que el endpoint.

Más los hallazgos de la revisión del 2-oct (cada prueba lo dice): la remisión
ligada manda aunque la clave sea de otro, un abono no cambia una partida ya
ligada, el uso se cuenta en remisiones, la unidad de una presentación sin
`sat`, la tabla de claves distintas solo en el inquilino del SAE, y la lista
del backfill para que el dueño revise (y re-apunte a propósito) lo ya ligado.

Y la decisión del dueño del 2-oct, «gana el SAE» (la factura es la verdad
final): entre gemelos de una clave, el que se llama como el artículo del SAE
(nivel 0_nombre_sae, al final del archivo).
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.config import settings
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Cliente,
    ClienteExterno,
    Factura,
    LineaFactura,
    LineaRemision,
    Membership,
    Producto,
    ProductoCliente,
    Remision,
    Role,
    Serie,
    Tenant,
    User,
)
from app.services import espejo_productos as ep

_PURGE = (
    "timbrado_intentos", "recibo_pago_facturas", "recibos_pago",
    "nota_credito_facturas", "notas_credito",
    "lineas_factura", "facturas", "lineas_remision", "remisiones",
    "movimientos_inventario", "lotes_inventario", "almacenes",
    "conexiones", "cliente_externos", "producto_clientes", "productos",
    "cliente_sucursal_series", "cliente_sucursales", "sucursales",
    "series", "clientes", "espejo_syncs",
)


@pytest.fixture
def env(db_engine):
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    created = {"memberships": [], "users": [], "tenants": []}
    try:
        t = Tenant(slug=f"epc-{suffix}", legal_name="Espejo Clave SA",
                   rfc=f"EPC{suffix.upper()}"[:13], regimen_fiscal_sat="601",
                   domicilio_fiscal_cp="44100", tier="PRINCIPAL", status="ACTIVE")
        db.add(t); db.flush(); created["tenants"].append(t.id)
        owner_role = db.query(Role).filter(Role.nombre == "OWNER", Role.es_preset.is_(True)).one()
        sub = f"sub-epc-{suffix}"
        u = User(email=f"epc-{suffix}@t.test", auth_user_id=sub, full_name="epc")
        db.add(u); db.flush(); created["users"].append(u.id)
        m = Membership(tenant_id=t.id, user_id=u.id, role_id=owner_role.id)
        db.add(m); db.flush(); created["memberships"].append(m.id)

        serie_f = Serie(tenant_id=t.id, codigo="ZHGO", tipo="FISCAL",
                        tipo_documento="FACTURA", nombre="Hidalgo", espejo_sae=True)
        serie_r = Serie(tenant_id=t.id, codigo="RZHGO", tipo="NO_FISCAL",
                        tipo_documento="REMISION", nombre="Hidalgo")
        db.add_all([serie_f, serie_r]); db.flush()
        cli = Cliente(tenant_id=t.id, codigo="CL1", legal_name="HOSPITAL ESPEJO",
                      rfc="HES250922PY2", serie_factura_id=serie_f.id,
                      serie_remision_id=serie_r.id, metodo_pago_default="PPD",
                      forma_pago_default="99", uso_cfdi_default="G01", espejo_sae=True)
        db.add(cli); db.flush()
        # El mismo cliente en la empresa 02 (Pachuca) y en la 03 (Tabasco).
        db.add_all([
            ClienteExterno(tenant_id=t.id, sistema="SAE", clave="02:6", clave_normalizada="02 6",
                           cliente_id=cli.id, origen="MANUAL", confianza="CONFIRMADA"),
            ClienteExterno(tenant_id=t.id, sistema="SAE", clave="03:7", clave_normalizada="03 7",
                           cliente_id=cli.id, origen="MANUAL", confianza="CONFIRMADA"),
        ])
        db.commit()
        yield {"dueno": {"sub": sub, "email": u.email, "tenant_id": t.id},
               "tenant": t.id, "cli": cli.id}
    finally:
        db.rollback()
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


def _bot(client, env, auth_as):
    """La clave de conexión del conector: la que deposita el espejo."""
    auth_as(env["dueno"])
    r = client.post("/api/v1/conexiones/SMART_SUPPLY/clave", headers=_hdr(env["dueno"]))
    assert r.status_code in (200, 201), r.text
    app.dependency_overrides.pop(get_principal, None)
    return {"Authorization": f"Bearer {r.json()['clave']}"}


def _prod(env, sku, nombre, clave, *, unidad_sat="KGM", unidad_base="KILO",
          presentaciones=None, clave_sat="50401700", activo=True, borrado=False,
          en_catalogo=False, codigo_cliente=None):
    db = SessionLocal()
    try:
        p = Producto(tenant_id=env["tenant"], sku=sku, nombre=nombre, clave_sae=clave,
                     clave_sat=clave_sat, unidad_sat=unidad_sat, unidad_base=unidad_base,
                     presentacion_default=unidad_base,
                     presentaciones=presentaciones or {unidad_base: 1}, activo=activo)
        db.add(p); db.flush()
        if borrado:
            db.execute(text("UPDATE productos SET deleted_at = now() WHERE id = :id"), {"id": p.id})
        if en_catalogo or codigo_cliente:
            db.add(ProductoCliente(tenant_id=env["tenant"], cliente_id=env["cli"],
                                   producto_id=p.id, codigo_cliente=codigo_cliente))
        db.commit()
        return p.id
    finally:
        db.close()


def _espejo(hk, client, *, folio, claves, empresa="02", serie="ZHGO", cliente_sae="6",
            saldo=None, uuid_f=None, desc=None):
    """`saldo` y el mismo `uuid_f` = el reenvío de un abono (pasada o
    cuadre_saldos); `desc` = la descripción del artículo en SAE por clave."""
    lineas = [{"clave": k, "descripcion": (desc or {}).get(k, f"PARTIDA {k}"), "cantidad": "2",
               "precio_unitario": "10.00"} for k in claves]
    body = {
        "empresa": empresa, "serie": serie, "folio": folio, "cliente_sae": cliente_sae,
        "fecha": "2026-09-15T12:00:00Z", "uuid_fiscal": uuid_f or str(uuid.uuid4()),
        "subtotal": str(20 * len(claves)), "total": str(20 * len(claves)),
        "lineas": lineas,
    }
    if saldo is not None:
        body["saldo_insoluto"] = str(saldo)
    r = client.post("/api/v1/facturas/espejo", headers=hk, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _lineas_bd(factura_id):
    """Lo que quedó escrito, con la unidad y la presentación (la salida del
    API no las trae todas)."""
    db = SessionLocal()
    try:
        return [
            {"clave": ln.clave_sae, "producto_id": ln.producto_id, "presentacion": ln.presentacion,
             "clave_unidad": ln.clave_unidad, "clave_prod_serv": ln.clave_prod_serv}
            for ln in db.query(LineaFactura).filter(LineaFactura.factura_id == factura_id)
            .order_by(LineaFactura.numero_linea)
        ]
    finally:
        db.close()


def _remision(client, env, auth_as, pid, *, marca=None):
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    rem = client.post("/api/v1/remisiones", headers=h, json={
        "cliente_facturacion_id": str(env["cli"]),
        "lineas": [{"producto_id": str(pid), "cantidad_solicitada": 1, "precio_unitario": 10}]})
    assert rem.status_code == 201, rem.text
    if marca:
        r = client.patch(f"/api/v1/remisiones/{rem.json()['id']}", headers=h,
                         json={"factura_sae": marca})
        assert r.status_code == 200, r.text
    app.dependency_overrides.pop(get_principal, None)
    return rem.json()["id"]


def _remision_bd(env, pid, *, serie="RZECA", n=1):
    """Una remisión de OTRA serie, directo en la base (la API la numera con la
    serie del cliente): `n` partidas del producto."""
    db = SessionLocal()
    try:
        s = db.query(Serie).filter(Serie.tenant_id == env["tenant"], Serie.codigo == serie).first()
        if s is None:
            s = Serie(tenant_id=env["tenant"], codigo=serie, tipo="NO_FISCAL",
                      tipo_documento="REMISION", nombre=serie)
            db.add(s); db.flush()
        rem = Remision(tenant_id=env["tenant"], folio_interno=f"{serie}-{uuid.uuid4().hex[:6]}",
                       cliente_facturacion_id=env["cli"], serie_id=s.id)
        db.add(rem); db.flush()
        for i in range(1, n + 1):
            db.add(LineaRemision(tenant_id=env["tenant"], remision_id=rem.id, numero_linea=i,
                                 producto_id=pid, cantidad_solicitada=1, precio_unitario=10))
        db.commit()
    finally:
        db.close()


def _ligar(factura_id, pid):
    """Como dejó la partida el espejo de antes (por el código del cliente)."""
    db = SessionLocal()
    try:
        db.execute(text("UPDATE lineas_factura SET producto_id = :p, presentacion = NULL "
                        "WHERE factura_id = :f"), {"p": pid, "f": factura_id})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def duenio_sae(env, monkeypatch):
    """Este inquilino es el del SAE del despliegue (ESPEJO_SAE_TENANT_ID)."""
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", str(env["tenant"]))


# ─── Reglas puras ───────────────────────────────────────────────────────────

def test_normalizacion_y_series_canonicas():
    assert ep.norm_clave_sae(" piñamielpz ") == "PINAMIELPZ"
    assert ep.norm_clave_sae("Acei-Acei 639.") == "ACEI-ACEI639"
    assert ep.norm_empresa("3") == ep.norm_empresa("03") == "03"
    # la nativa FEHMOHOS sigue a la ZEHMOHOS del SAE; las remisiones RZ…/RF… también
    assert ep.serie_canonica("FEHMOHOS") == ep.serie_canonica("ZEHMOHOS") == "ZEHMOHOS"
    assert ep.serie_canonica_de_remision("RZEHMOVH") == "ZEHMOVH"
    assert ep.serie_canonica_de_remision("RFEHMOHOS") == "ZEHMOHOS"


def test_unidad_sat_la_da_la_presentacion_y_una_caja_sin_unidad_queda_en_pieza():
    class P:
        unidad_sat, unidad_base, presentacion_default = "KGM", "KILO", "KILO"
        presentaciones = {"KILO": 1,
                          "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"},
                          "CAJA": {"factor": 22, "clave_sae": "MANZANACAJA"}}
    assert ep.unidad_sat_de(P, "PIEZA") == "H87"
    assert ep.unidad_sat_de(P, "KILO") == "KGM"
    # 22 kilos por caja: con la unidad del producto Mini Conta contaría kilos
    assert ep.unidad_sat_de(P, "CAJA") == "H87"
    # sin presentación (cruce por código del cliente): lo de siempre
    assert ep.unidad_sat_de(P, None) == "KGM"


def test_presentacion_sin_sat_toma_la_unidad_de_su_nombre():
    """Revisión del 2-oct: el formulario de Productos y PUT clave-sae guardan
    `{factor, clave_sae}` sin `sat`. Con la unidad del producto, la PIEZA de
    un producto de kilo salía KGM y Mini Conta la contaba en kilos."""
    class Melon:
        unidad_sat, unidad_base, presentacion_default = "KGM", "KILO", "KILO"
        presentaciones = {"KILO": 1, "PIEZA": {"factor": 1, "clave_sae": "MELONCHPZ"},
                          "MAZO": {"factor": 1, "clave_sae": "X"}, "CAJA": {"factor": 1},
                          "TARIMA": {"factor": 40}, "RARA": {"factor": 1}}

    class Papaya:
        unidad_sat, unidad_base, presentacion_default = "H87", "PIEZA", "PIEZA"
        presentaciones = {"PIEZA": 1, "KILO": {"factor": 1, "clave_sae": "PAPAYAMARADOLKG"}}

    assert ep.unidad_sat_de(Melon, "PIEZA") == "H87"
    assert ep.unidad_sat_de(Melon, "MAZO") == "H87"
    assert ep.unidad_sat_de(Melon, "CAJA") == "H87"      # envase sin sat: por pieza
    assert ep.unidad_sat_de(Melon, "TARIMA") == "H87"    # nombre mudo, factor ≠ 1: bulto
    assert ep.unidad_sat_de(Melon, "RARA") == "KGM"      # nombre mudo, factor 1: la del producto
    assert ep.unidad_sat_de(Melon, "KILO") == "KGM"
    assert ep.unidad_sat_de(Papaya, "KILO") == "KGM"
    assert ep.unidad_sat_de(Papaya, "PIEZA") == "H87"


def test_la_regla_dice_por_que_nivel_gano():
    # (catálogo, uso serie, uso global)
    assert ep._regla((1, 0, 0), (0, 50, 50)) == (ep.CATALOGO, False)
    assert ep._regla((0, 40, 40), (0, 3, 3)) == (ep.USO_SERIE, False)
    assert ep._regla((0, 3, 3), (0, 2, 2)) == (ep.USO_SERIE, True)   # débil
    assert ep._regla((0, 0, 9), (0, 0, 2)) == (ep.USO_GLOBAL, False)
    assert ep._regla((0, 0, 7), (0, 0, 7)) == (ep.SKU_MENOR, True)


# ─── El endpoint ────────────────────────────────────────────────────────────

def test_clave_unica_liga_producto_con_su_presentacion_base_y_unidad(client, env, auth_as):
    acelga = _prod(env, "00000101", "ACELGA", "ACELGASKG", clave_sat="50401701")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=101, claves=["acelgaskg ", "NO-EXISTE-1"])
    a, b = _lineas_bd(f["id"])
    assert a == {"clave": "acelgaskg", "producto_id": acelga, "presentacion": "KILO",
                 "clave_unidad": "KGM", "clave_prod_serv": "50401701"}
    # lo que no casa se guarda igual, sin producto, como siempre
    assert b["producto_id"] is None and b["clave_unidad"] == "H87"
    assert b["clave_prod_serv"] == "01010101" and b["presentacion"] is None


def test_clave_de_presentacion_pone_esa_presentacion_y_su_unidad(client, env, auth_as):
    sandia = _prod(env, "00000391", "SANDIA", "SANDIAKG", presentaciones={
        "KILO": 1, "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"}})
    # el caso real: producto base PIEZA y la clave de su presentación KILO
    papaya = _prod(env, "00010158", "PAPAYA MARADOL", "PAPAYAMARADOLPZ",
                   unidad_sat="H87", unidad_base="PIEZA", presentaciones={
                       "PIEZA": 1, "KILO": {"sat": "KGM", "factor": 1, "clave_sae": "PAPAYAMARADOLKG"}})
    manzana = _prod(env, "00000359", "MANZANA", "MANZANAKG", presentaciones={
        "KILO": 1, "CAJA": {"factor": 22, "clave_sae": "MANZANACAJA"}})
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=102,
                claves=["SANDIAPZ", "SANDIAKG", "PAPAYAMARADOLKG", "MANZANACAJA"])
    got = [(l["producto_id"], l["presentacion"], l["clave_unidad"]) for l in _lineas_bd(f["id"])]
    assert got == [
        (sandia, "PIEZA", "H87"),
        (sandia, "KILO", "KGM"),
        (papaya, "KILO", "KGM"),      # antes: H87 → Mini Conta las contaba como piezas
        (manzana, "CAJA", "H87"),     # caja sin unidad SAT: no se inventa KGM
    ]


def test_gemelos_desempata_la_remision_ligada_antes_que_el_catalogo(client, env, auth_as):
    # El sku menor y el catálogo del cliente señalan al 299; la remisión que
    # ESTA factura ampara trae el 300 — manda la remisión.
    a = _prod(env, "00000299", "CHAYOTE SIN ESPINAS", "CHAYOTESINESPIKG", en_catalogo=True)
    b = _prod(env, "00000300", "CHAYOTE", "CHAYOTESINESPIKG")
    _remision(client, env, auth_as, b, marca="ZHGO 240")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=240, claves=["CHAYOTESINESPIKG"])
    (ln,) = _lineas_bd(f["id"])
    assert ln["producto_id"] == b
    assert a != b


def test_gemelos_desempata_el_catalogo_del_cliente(client, env, auth_as):
    _prod(env, "00000317", "EJOTE", "EJOTEKG")
    b = _prod(env, "00010954", "EJOTE", "EJOTEKG", en_catalogo=True)
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=103, claves=["EJOTEKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == b


def test_gemelos_desempata_el_uso_en_la_serie_y_luego_el_global(client, env, auth_as):
    a = _prod(env, "00000310", "CILANTRO CRIOLLO", "CILANTROKG")
    b = _prod(env, "00000311", "CILANTRO MANOJO", "CILANTROKG")
    _prod(env, "00010026", "TE DE YERBABUENA", "HIERBABUENAKG")
    d = _prod(env, "00010724", "HIERBABUENA", "HIERBABUENAKG")
    # uso en ESTA serie (remisión RZHGO, sin ligar a ninguna factura)
    _remision(client, env, auth_as, b)
    # uso SOLO en otra serie (remisión RZECA): cuenta para el global
    _remision_bd(env, d, serie="RZECA")
    # Partidas ESPEJO ya ligadas al otro gemelo, en la misma serie: no son
    # evidencia (las puso el cruce viejo o esta regla) y no cuentan — con
    # ellas, LIMONSINSEMILLKG iba al LIMON con 781 partidas espejo contra
    # 202 remisiones del LIMON SIN SEMILLA (revisión del 2-oct).
    hk = _bot(client, env, auth_as)
    vieja = _espejo(hk, client, folio=90, claves=["CILANTROKG"] * 6)
    _ligar(vieja["id"], a)

    db = SessionLocal()
    try:
        # la regla dice por qué
        res = ep.resolver_claves(ep.FuentesBD(db, env["tenant"]), factura_id=None,
                                 cliente_id=env["cli"], empresa="02", serie="ZHGO",
                                 claves=["CILANTROKG", "HIERBABUENAKG"])
    finally:
        db.close()
    assert res["CILANTROKG"].regla == ep.USO_SERIE and res["CILANTROKG"].producto_id == b
    assert res["HIERBABUENAKG"].regla == ep.USO_GLOBAL
    assert res["HIERBABUENAKG"].debil       # 1 contra 0: decidió por poco

    f = _espejo(hk, client, folio=104, claves=["CILANTROKG", "HIERBABUENAKG"])
    assert [l["producto_id"] for l in _lineas_bd(f["id"])] == [b, d]


def test_calabaza_castilla_en_tabasco_es_calabaza_criolla(client, env, auth_as, duenio_sae):
    castilla = _prod(env, "00010738", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    criolla = _prod(env, "00010233", "CALABAZA CRIOLLA", "CALABAZCRIOLLAKG", presentaciones={
        "KILO": {}, "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "CALABAZACRIOPZ"}})
    hk = _bot(client, env, auth_as)
    tab = _espejo(hk, client, folio=1500, empresa="03", serie="ZEHMOVH", cliente_sae="7",
                  claves=["CALABAZACASTILKG"])
    (ln,) = _lineas_bd(tab["id"])
    assert (ln["producto_id"], ln["presentacion"], ln["clave_unidad"]) == (criolla, "KILO", "KGM")
    # la misma clave en Pachuca (02) sí es calabaza de castilla
    pac = _espejo(hk, client, folio=105, claves=["CALABAZACASTILKG"])
    assert _lineas_bd(pac["id"])[0]["producto_id"] == castilla
    # y el reenvío deja lo mismo, presentación incluida
    otra_vez = _espejo(hk, client, folio=1500, empresa="03", serie="ZEHMOVH", cliente_sae="7",
                       claves=["CALABAZACASTILKG"])
    assert _lineas_bd(otra_vez["id"]) == [ln]


def test_la_tabla_de_claves_distintas_solo_aplica_en_el_inquilino_del_sae(client, env, auth_as,
                                                                           monkeypatch):
    """Revisión del 2-oct: los skus son de cada inquilino. En uno que no es el
    dueño del SAE, su 00010233 puede ser un aguacate: la fila de la 03 no se
    le aplica y la clave liga a lo que dice su catálogo."""
    castilla = _prod(env, "00010738", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    aguacate = _prod(env, "00010233", "AGUACATE HASS", "AGUACATEHASSKG")
    hk = _bot(client, env, auth_as)
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", str(uuid.uuid4()))   # otro inquilino
    f = _espejo(hk, client, folio=1501, empresa="03", serie="ZEHMOVH", cliente_sae="7",
                claves=["CALABAZACASTILKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == castilla
    monkeypatch.setattr(settings, "ESPEJO_SAE_TENANT_ID", "")                  # sin SAE: nadie
    g = _espejo(hk, client, folio=1502, empresa="03", serie="ZEHMOVH", cliente_sae="7",
                claves=["CALABAZACASTILKG"])
    assert _lineas_bd(g["id"])[0]["producto_id"] == castilla
    assert castilla != aguacate


def test_clave_sin_producto_cae_al_codigo_del_cliente(client, env, auth_as):
    ajo_kg = _prod(env, "00010472", "AJO KG", "AJOKG")
    # el catálogo del cliente llama AJOKG a OTRO producto: ya no manda la clave
    _prod(env, "00000284", "AJO", "AJOPRIMERAKG", codigo_cliente="AJOKG")
    aceite = _prod(env, "00000001", "ACEITE 20 LT", "ACEITE20LT", unidad_sat="H87",
                   unidad_base="PIEZA", codigo_cliente="ACEI-ACEI-639")
    # un código que apunta a un producto BORRADO no liga (pasaba con la NUEZ 00010229)
    _prod(env, "00010229", "NUEZ", "NUEZKG", borrado=True, codigo_cliente="NUEZ-FRUT-1")
    # uno desactivado sigue ligando por código, como antes
    gemelo = _prod(env, "00011135", "SANDIA", "SAND-FRUT-413X", activo=False,
                   codigo_cliente="SAND-FRUT-413")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=106,
                claves=["AJOKG", "ACEI-ACEI-639", "NUEZ-FRUT-1", "SAND-FRUT-413"])
    ajo, ace, nuez, sand = _lineas_bd(f["id"])
    assert ajo["producto_id"] == ajo_kg
    assert (ace["producto_id"], ace["presentacion"], ace["clave_unidad"]) == (aceite, None, "H87")
    assert nuez["producto_id"] is None
    assert sand["producto_id"] == gemelo


def test_reenvio_no_voltea_la_decision(client, env, auth_as):
    a = _prod(env, "00000350", "LECHUGA ROMANA", "LECHUGAROMANAKG")
    b = _prod(env, "00010383", "LECHUGA LARGA", "LECHUGAROMANAKG")
    _prod(env, "00000391", "SANDIA", "SANDIAKG", presentaciones={
        "KILO": 1, "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"}})
    _remision(client, env, auth_as, b)          # uso en la serie: gana b
    hk = _bot(client, env, auth_as)
    claves = ["LECHUGAROMANAKG", "SANDIAPZ", "NO-EXISTE-2"]
    f = _espejo(hk, client, folio=107, claves=claves)
    primera = _lineas_bd(f["id"])
    assert primera[0]["producto_id"] == b

    # El uso cambia: ahora a pesa más en la serie. Una factura NUEVA ya lo
    # toma, pero el reenvío de la vieja conserva lo que decidió.
    for _ in range(3):
        _remision(client, env, auth_as, a)
    nueva = _espejo(hk, client, folio=108, claves=["LECHUGAROMANAKG"])
    assert _lineas_bd(nueva["id"])[0]["producto_id"] == a

    otra_vez = _espejo(hk, client, folio=107, claves=claves)
    assert otra_vez["id"] == f["id"]
    assert _lineas_bd(f["id"]) == primera


def test_la_remision_ligada_manda_aunque_la_clave_sea_de_otro_producto(client, env, auth_as):
    """Revisión del 2-oct (R1): el export manda la remisión con el código del
    cliente (AJOKG para el 00000284 AJO) y SAE la factura con ese artículo,
    que en el catálogo es la clave del 00010472 AJO KG. Con la clave única
    primero, lo remisionado (AJO) y lo facturado (AJO KG) quedaban distintos."""
    ajo = _prod(env, "00000284", "AJO", "AJOPRIMERAKG", codigo_cliente="AJOKG")
    ajo_kg = _prod(env, "00010472", "AJO KG", "AJOKG")
    _remision(client, env, auth_as, ajo, marca="ZHGO 400")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=400, claves=["AJOKG"])
    (ln,) = _lineas_bd(f["id"])
    # por código del cliente: sin presentación y con la unidad del producto
    assert (ln["producto_id"], ln["presentacion"], ln["clave_unidad"]) == (ajo, None, "KGM")
    # sin remisión ligada, la clave sí manda
    g = _espejo(hk, client, folio=401, claves=["AJOKG"])
    assert _lineas_bd(g["id"])[0]["producto_id"] == ajo_kg

    # Si ya estaba ligada al otro (como la dejaba la primera versión de este
    # cambio), el backfill la lista como «solo»: el endpoint la regresa al de
    # su remisión en el siguiente reenvío, sin esperar a --recalcular.
    from scripts.backfill_espejo_producto_por_clave import correr
    _ligar(f["id"], ajo_kg)
    db = SessionLocal()
    try:
        c = correr(db, env["tenant"], salida=lambda *_: None)
    finally:
        db.close()
    (fila,) = c["tabla_paso3"]
    assert (fila["de_sku"], fila["a_sku"], fila["regla"], fila["solo"]) == \
        ("00010472", "00000284", ep.REMISION, True)
    _espejo(hk, client, folio=400, claves=["AJOKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == ajo


def test_un_abono_no_le_cambia_el_producto_a_una_partida_ligada(client, env, auth_as):
    """Revisión del 2-oct (R2/R3): un reenvío por abono, cancelación o cuadre
    no corrige la historia. La partida que el espejo viejo ligó por código del
    cliente, y la que tiene el gemelo que apagó la fusión de SANDIA, se quedan
    como están; las facturas NUEVAS ya van por la clave."""
    ajo = _prod(env, "00000284", "AJO", "AJOPRIMERAKG", codigo_cliente="AJOKG")
    ajo_kg = _prod(env, "00010472", "AJO KG", "AJOKG")
    gemelo = _prod(env, "00011135", "SANDIA PZ", "SANDIAPZ", unidad_sat="H87",
                   unidad_base="PIEZA", activo=False, codigo_cliente="SANDIAPZ")
    sandia = _prod(env, "00000391", "SANDIA", "SANDIAKG", presentaciones={
        "KILO": 1, "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"}})
    hk = _bot(client, env, auth_as)
    u = str(uuid.uuid4())
    f = _espejo(hk, client, folio=402, claves=["AJOKG", "SANDIAPZ"], uuid_f=u)
    # nueva: por la clave, al AJO KG y al sobreviviente de la fusión
    assert [l["producto_id"] for l in _lineas_bd(f["id"])] == [ajo_kg, sandia]

    # Como las dejó el espejo de antes, que cruzaba por código del cliente.
    db = SessionLocal()
    try:
        for clave, pid in (("AJOKG", ajo), ("SANDIAPZ", gemelo)):
            db.execute(text("UPDATE lineas_factura SET producto_id = :p, presentacion = NULL "
                            "WHERE factura_id = :f AND clave_sae = :k"),
                       {"p": pid, "f": f["id"], "k": clave})
        db.commit()
    finally:
        db.close()
    def _vista(lineas):     # lo que Mini Conta copia: producto y unidad
        return [(l["producto_id"], l["clave_unidad"], l["clave_prod_serv"]) for l in lineas]

    antes = _lineas_bd(f["id"])
    _espejo(hk, client, folio=402, claves=["AJOKG", "SANDIAPZ"], uuid_f=u, saldo="5.00")   # abono
    despues = _lineas_bd(f["id"])
    assert [l["producto_id"] for l in despues] == [ajo, gemelo]
    assert _vista(despues) == _vista(antes)


def test_una_correccion_del_catalogo_llega_a_lo_ligado_solo_a_proposito(client, env, auth_as):
    """Revisión del 2-oct: con la decisión previa, dar de alta el gemelo
    correcto en el catálogo del cliente no movía las partidas ya decididas y
    el backfill ni las listaba. El endpoint las sigue conservando (un abono no
    corrige la historia), pero el backfill las lista en el paso 3 y
    --recalcular-ligadas las re-apunta."""
    from scripts.backfill_espejo_producto_por_clave import correr

    x = _prod(env, "00000500", "EJOTE A", "EJOTEZZKG")
    y = _prod(env, "00000501", "EJOTE B", "EJOTEZZKG")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=9100, claves=["EJOTEZZKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == x                 # sku menor
    db = SessionLocal()
    try:
        db.add(ProductoCliente(tenant_id=env["tenant"], cliente_id=env["cli"], producto_id=y))
        db.commit()
    finally:
        db.close()
    n = _espejo(hk, client, folio=9101, claves=["EJOTEZZKG"])
    assert _lineas_bd(n["id"])[0]["producto_id"] == y                 # catálogo
    _espejo(hk, client, folio=9100, claves=["EJOTEZZKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == x                 # el reenvío no corrige

    db = SessionLocal()
    try:
        seco = correr(db, env["tenant"], salida=lambda *_: None)
        (fila,) = seco["tabla_paso3"]
        assert (fila["serie"], fila["clave"], fila["de_sku"], fila["a_sku"], fila["regla"]) == \
            ("ZHGO", "EJOTEZZKG", "00000500", "00000501", ep.CATALOGO)
        assert not fila["solo"] and seco["paso3_aplicables"] == 1
        sin = correr(db, env["tenant"], aplicar=True, salida=lambda *_: None)
        assert sin["paso3_escritas"] == 0
        assert _lineas_bd(f["id"])[0]["producto_id"] == x
        con = correr(db, env["tenant"], aplicar=True, recalcular=True, salida=lambda *_: None)
        assert con["paso3_escritas"] == 1
        assert _lineas_bd(f["id"])[0]["producto_id"] == y
        assert sum(correr(db, env["tenant"], salida=lambda *_: None)["paso3"].values()) == 0
    finally:
        db.close()
    _espejo(hk, client, folio=9100, claves=["EJOTEZZKG"])
    assert _lineas_bd(f["id"]) == _lineas_bd(n["id"])


def test_presentacion_con_clave_puesta_desde_la_app_lleva_su_unidad(client, env, auth_as):
    """Revisión del 2-oct (R5): PUT /productos/{id}/clave-sae guarda la clave
    de la presentación sin `sat`. MELONCHPZ (PIEZA de un producto de KILO)
    salía KGM y PAPAYAMARADOLKG (KILO de uno de PIEZA) H87: Mini Conta contaba
    el melón en kilos y la papaya en piezas."""
    melon = _prod(env, "00000360", "MELON CHINO", "MELONCHKG",
                  presentaciones={"KILO": 1, "PIEZA": 1})
    papaya = _prod(env, "00010158", "PAPAYA MARADOL", "PAPAYAMARADOLPZ", unidad_sat="H87",
                   unidad_base="PIEZA", presentaciones={"PIEZA": 1, "KILO": 1})
    auth_as(env["dueno"]); h = _hdr(env["dueno"])
    for pid, clave, pres in ((melon, "MELONCHPZ", "PIEZA"), (papaya, "PAPAYAMARADOLKG", "KILO")):
        r = client.put(f"/api/v1/productos/{pid}/clave-sae", headers=h,
                       json={"clave": clave, "presentacion": pres})
        assert r.status_code == 200, r.text
    app.dependency_overrides.pop(get_principal, None)
    db = SessionLocal()
    try:
        assert "sat" not in db.get(Producto, melon).presentaciones["PIEZA"]   # la forma real
    finally:
        db.close()
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=404, claves=["MELONCHPZ", "PAPAYAMARADOLKG"])
    got = [(l["producto_id"], l["presentacion"], l["clave_unidad"]) for l in _lineas_bd(f["id"])]
    assert got == [(melon, "PIEZA", "H87"), (papaya, "KILO", "KGM")]


# ─── El backfill ────────────────────────────────────────────────────────────

def test_backfill_escribe_lo_mismo_que_el_endpoint_y_es_idempotente(client, env, auth_as):
    from scripts.backfill_espejo_producto_por_clave import correr

    _prod(env, "00000101", "ACELGA", "ACELGASKG")
    papaya = _prod(env, "00010158", "PAPAYA MARADOL", "PAPAYAMARADOLPZ",
                   unidad_sat="H87", unidad_base="PIEZA", presentaciones={
                       "PIEZA": 1, "KILO": {"sat": "KGM", "factor": 1, "clave_sae": "PAPAYAMARADOLKG"}})
    _prod(env, "00000299", "CHAYOTE SIN ESPINAS", "CHAYOTESINESPIKG")
    b = _prod(env, "00000300", "CHAYOTE", "CHAYOTESINESPIKG")
    _remision(client, env, auth_as, b, marca="ZHGO 300")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=300,
                claves=["ACELGASKG", "PAPAYAMARADOLKG", "CHAYOTESINESPIKG", "NO-EXISTE-3"])
    f2 = _espejo(hk, client, folio=301, claves=["PAPAYAMARADOLKG"])
    esperado, esperado2 = _lineas_bd(f["id"]), _lineas_bd(f2["id"])

    # Como estaban antes de este cambio: las de f sin producto (el espejo viejo
    # no miraba la clave) y la papaya de f2 ligada con la unidad del producto.
    db = SessionLocal()
    try:
        db.execute(text("""UPDATE lineas_factura SET producto_id = NULL, presentacion = NULL,
                           clave_unidad = 'H87', clave_prod_serv = '01010101'
                           WHERE factura_id = :f"""), {"f": f["id"]})
        db.execute(text("""UPDATE lineas_factura SET clave_unidad = 'H87', presentacion = NULL
                           WHERE factura_id = :f"""), {"f": f2["id"]})
        db.commit()

        seco = correr(db, env["tenant"], aplicar=False, salida=lambda *_: None)
        assert seco["paso1_por_regla"] == {ep.UNICA: 2, ep.REMISION: 1, ep.SIN_PRODUCTO: 1}
        assert sum(seco["paso2_por_serie"].values()) == 1
        assert _lineas_bd(f["id"])[0]["producto_id"] is None       # dry-run no escribe

        hecho = correr(db, env["tenant"], aplicar=True, lote=1, salida=lambda *_: None)
        assert hecho["paso1_escritas"] == 3 and hecho["paso2_escritas"] == 1
        assert _lineas_bd(f["id"]) == esperado
        assert _lineas_bd(f2["id"]) == esperado2
        assert esperado2[0]["producto_id"] == papaya and esperado2[0]["clave_unidad"] == "KGM"

        otra = correr(db, env["tenant"], aplicar=True, salida=lambda *_: None)
        assert otra["paso1_escritas"] == 0 and otra["paso2_escritas"] == 0
    finally:
        db.close()

    # y el endpoint, al reenviarla, deja exactamente lo mismo
    _espejo(hk, client, folio=300,
            claves=["ACELGASKG", "PAPAYAMARADOLKG", "CHAYOTESINESPIKG", "NO-EXISTE-3"])
    assert _lineas_bd(f["id"]) == esperado


def test_backfill_lista_las_ligadas_a_otro_producto_y_solo_las_mueve_a_proposito(client, env,
                                                                                  auth_as):
    from scripts.backfill_espejo_producto_por_clave import correr

    ajo_kg = _prod(env, "00010472", "AJO KG", "AJOKG")
    ajo = _prod(env, "00000284", "AJO", "AJOPRIMERAKG")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=310, claves=["AJOKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == ajo_kg
    # como la dejó el espejo viejo: por el código del cliente, al otro AJO
    _ligar(f["id"], ajo)
    db = SessionLocal()
    try:
        c = correr(db, env["tenant"], aplicar=True, salida=lambda *_: None)
        assert sum(c["paso3"].values()) == 1 and c["paso3_aplicables"] == 1
        assert not c["tabla_paso3"][0]["solo"]          # sin remisión: el endpoint no la mueve
        assert c["paso1_escritas"] == 0 and c["paso2_escritas"] == 0 and c["paso3_escritas"] == 0
        assert _lineas_bd(f["id"])[0]["producto_id"] == ajo
        # el endpoint tampoco la mueve…
        _espejo(hk, client, folio=310, claves=["AJOKG"])
        assert _lineas_bd(f["id"])[0]["producto_id"] == ajo
        # …solo --recalcular-ligadas, y entonces queda como una nueva
        c = correr(db, env["tenant"], aplicar=True, recalcular=True, salida=lambda *_: None)
        assert c["paso3_escritas"] == 1
    finally:
        db.close()
    (ln,) = _lineas_bd(f["id"])
    assert (ln["producto_id"], ln["presentacion"], ln["clave_unidad"]) == (ajo_kg, "KILO", "KGM")


def test_backfill_y_endpoint_deciden_igual_despues_del_backfill(client, env, auth_as):
    """Revisión del 2-oct: el backfill tomaba la foto del uso ANTES de ligar sus
    ~80k partidas, y el endpoint contaba después con ellas: la misma clave de
    la misma serie podía ir a otro gemelo en las facturas nuevas. Con el uso
    en remisiones, lo que liga el backfill no mueve la foto."""
    from scripts.backfill_espejo_producto_por_clave import correr

    x = _prod(env, "00000600", "CILANTRO A", "CILANTROQQKG")
    y = _prod(env, "00000601", "CILANTRO B", "CILANTROQQKG", presentaciones={
        "KILO": 1, "MANOJO": {"sat": "H87", "factor": 1, "clave_sae": "CILANTROQQMJ"}})
    _remision(client, env, auth_as, x)            # uso en ZHGO: x=1, y=0
    hk = _bot(client, env, auth_as)
    f1 = _espejo(hk, client, folio=9200, claves=["CILANTROQQKG"])
    f0 = _espejo(hk, client, folio=9201, claves=["CILANTROQQMJ"] * 3)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE lineas_factura SET producto_id = NULL, presentacion = NULL, "
                        "clave_unidad = 'H87' WHERE factura_id IN (:a, :b)"),
                   {"a": f1["id"], "b": f0["id"]})
        db.commit()
        correr(db, env["tenant"], aplicar=True, salida=lambda *_: None)
    finally:
        db.close()
    assert _lineas_bd(f1["id"])[0]["producto_id"] == x
    assert {l["producto_id"] for l in _lineas_bd(f0["id"])} == {y}    # 3 partidas espejo de y
    nueva = _espejo(hk, client, folio=9202, claves=["CILANTROQQKG"])
    assert _lineas_bd(nueva["id"])[0]["producto_id"] == x
    _espejo(hk, client, folio=9200, claves=["CILANTROQQKG"])
    assert _lineas_bd(f1["id"])[0]["producto_id"] == x


def test_backfill_lista_quien_gana_cada_clave_gemela(client, env, auth_as):
    """Revisión del 2-oct: el dry-run solo contaba por regla. El dueño necesita
    ver qué sku gana cada clave gemela en cada serie, y que se marque cuando el
    artículo del SAE se llama como otro producto.

    Desde «gana el SAE» el caso que originó el ⚠ (TOMATEVERDELIMKG: el uso
    escogía el 00010048 y el SAE dice TOMATE VERDE LIMPIO) lo resuelve la regla
    y ya no se marca; el ⚠ queda para lo que la regla no puede tomar: aquí, el
    SAE nombra un gemelo desactivado."""
    from scripts.backfill_espejo_producto_por_clave import correr

    grande = _prod(env, "00010048", "TOMATE VERDE GRANDE Y LIMPIO", "TOMATEVERDELIMKG")
    _prod(env, "00010049", "TOMATE VERDE LIMPIO", "TOMATEVERDELIMKG")
    _remision(client, env, auth_as, grande)       # sin el nombre, el uso daba el 048
    _prod(env, "00000700", "CEBOLLA BLANCA", "CEBOLLAZZKG")
    _prod(env, "00000701", "CEBOLLA MORADA", "CEBOLLAZZKG")
    _prod(env, "00011700", "CEBOLLA CAMBRAY", "CEBOLLAZZKG", activo=False)
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=330, claves=["TOMATEVERDELIMKG"] * 2 + ["CEBOLLAZZKG"],
                desc={"TOMATEVERDELIMKG": "TOMATE VERDE LIMPIO KG",
                      "CEBOLLAZZKG": "CEBOLLA CAMBRAY KG"})
    db = SessionLocal()
    try:
        db.execute(text("UPDATE lineas_factura SET producto_id = NULL WHERE factura_id = :f"),
                   {"f": f["id"]})
        db.commit()
        impreso = []
        c = correr(db, env["tenant"], salida=impreso.append)
    finally:
        db.close()
    tomate, cebolla = sorted(c["tabla_gemelos"], key=lambda f: f["clave"], reverse=True)
    assert (tomate["serie"], tomate["clave"], tomate["sku"], tomate["regla"],
            tomate["partidas"]) == ("ZHGO", "TOMATEVERDELIMKG", "00010049", ep.NOMBRE_SAE, 2)
    # el que perdió sigue en la lista, pero ya no hay nada que marcar
    assert not tomate["debil"] and "00010048" in tomate["perdedores"]
    assert tomate["marca"] == ""
    # el SAE nombra a uno que la regla no puede tomar: se marca y se dice por qué
    assert (cebolla["sku"], cebolla["regla"]) == ("00000700", ep.SKU_MENOR)
    assert cebolla["marca"] == "⚠ el SAE la llama como 00011700 (desactivado)"
    assert not any("TOMATEVERDELIMKG" in linea and "⚠" in linea for linea in impreso)
    assert any("CEBOLLAZZKG" in linea and "⚠" in linea for linea in impreso)


def test_backfill_suelta_el_lote_si_el_espejo_tiene_la_factura(client, env, auth_as):
    """En producción el espejo reescribe partidas cada 30 s: si una está tomada,
    el lote se salta en vez de esperar, y la siguiente corrida lo recoge."""
    from scripts.backfill_espejo_producto_por_clave import correr

    acelga = _prod(env, "00000101", "ACELGA", "ACELGASKG")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=320, claves=["ACELGASKG"])
    otra, db = SessionLocal(), SessionLocal()
    try:
        db.execute(text("UPDATE lineas_factura SET producto_id = NULL WHERE factura_id = :f"),
                   {"f": f["id"]})
        db.commit()
        # otra sesión (el espejo) tiene la partida tomada
        otra.execute(text("SELECT id FROM lineas_factura WHERE factura_id = :f FOR UPDATE"),
                     {"f": f["id"]})
        c = correr(db, env["tenant"], aplicar=True, espera_candado="200ms",
                   salida=lambda *_: None)
        assert c["lotes_saltados"] == 1 and c["paso1_escritas"] == 0
        otra.rollback()
        c = correr(db, env["tenant"], aplicar=True, salida=lambda *_: None)
        assert c["lotes_saltados"] == 0 and c["paso1_escritas"] == 1
    finally:
        otra.rollback(); otra.close(); db.close()
    assert _lineas_bd(f["id"])[0]["producto_id"] == acelga


# ─── «Gana el SAE» (decisión del dueño, 2-oct-2026) ────────────────────────
#
# Entre gemelos de una clave, el que se llama como el artículo del SAE. Los
# cinco casos que el dry-run del backfill marcaba con ⚠, con datos sintéticos
# (skus y nombres de la foto del 2-oct).

# lo que el SAE escribe en cada partida
_SAE = {
    "TOMATEVERDELIMKG": "TOMATE VERDE LIMPIO KG",
    "HIERBABUENAKG": "HIERBABUENA",
    "ESPINACAPZA": "ESPINACA PZA",
    "PINAMIELPZ": "PIÑA MIEL PZA",
    "PAPAYAMARADOLKG": "PAPAYA MARADOL",
}


def _cinco_casos(client, env, auth_as) -> dict:
    """Cada clave con el gemelo que la regla escogía SIN el nombre (`antes`)
    y el que nombra el SAE (`sae`)."""
    p = {}
    # el uso en la serie daba el GRANDE
    p["tomate_grande"] = _prod(env, "00010048", "TOMATE VERDE GRANDE Y LIMPIO", "TOMATEVERDELIMKG")
    p["tomate_limpio"] = _prod(env, "00010049", "TOMATE VERDE LIMPIO", "TOMATEVERDELIMKG")
    _remision(client, env, auth_as, p["tomate_grande"])
    # el catálogo del cliente daba el TE
    p["te"] = _prod(env, "00010026", "TE DE YERBABUENA", "HIERBABUENAKG", en_catalogo=True)
    p["hierbabuena"] = _prod(env, "00010724", "HIERBABUENA", "HIERBABUENAKG")
    # el catálogo daba el MANOJO; «ESPINACA PZA» son DOS productos: entre ellos
    # desempata el uso en la serie (5 partidas del 965 en remisiones RZHGO)
    p["espinaca_manojo"] = _prod(env, "00010700", "ESPINACA MANOJO DE 1 KG", "ESPINACAPZA",
                                 en_catalogo=True)
    p["espinaca_pza"] = _prod(env, "00010761", "ESPINACA PZA", "ESPINACAPZA",
                              unidad_sat="H87", unidad_base="PIEZA")
    p["espinaca"] = _prod(env, "00010965", "ESPINACA", "ESPINACAPZA",
                          unidad_sat="H87", unidad_base="PIEZA")
    _remision_bd(env, p["espinaca"], serie="RZHGO", n=5)
    # el sku menor daba la PIÑA a secas (y el nombre del SAE trae Ñ)
    p["pina"] = _prod(env, "00000150", "PIÑA", "PINAMIELPZ", unidad_sat="H87", unidad_base="PIEZA")
    p["pina_miel"] = _prod(env, "00010161", "PIÑA MIEL", "PINAMIELPZ",
                           unidad_sat="H87", unidad_base="PIEZA")
    # el sku menor daba la PAPAYA; la MARADOL trae la clave en su presentación KILO
    p["papaya"] = _prod(env, "00000140", "PAPAYA", "PAPAYAMARADOLKG")
    p["maradol"] = _prod(env, "00010158", "PAPAYA MARADOL", "PAPAYAMARADOLPZ",
                         unidad_sat="H87", unidad_base="PIEZA", presentaciones={
                             "PIEZA": 1, "KILO": {"factor": 1, "clave_sae": "PAPAYAMARADOLKG"}})
    return p


def _resolver(env, claves, *, desc=None, factura_id=None, con_previa=True):
    db = SessionLocal()
    try:
        return ep.resolver_claves(
            ep.FuentesBD(db, env["tenant"]), factura_id=factura_id, cliente_id=env["cli"],
            empresa="02", serie="ZHGO", claves=claves,
            descripciones=[(k, (desc or {}).get(k)) for k in claves], con_previa=con_previa)
    finally:
        db.close()


def test_norm_nombre_quita_acentos_signos_y_unidades():
    assert ep.norm_nombre("TOMATE VERDE LIMPIO KG") == ep.norm_nombre("Tomate verde limpio") \
        == "TOMATE VERDE LIMPIO"
    assert ep.norm_nombre("PIÑA MIEL PZA") == "PINA MIEL"
    assert ep.norm_nombre("ESPINACA MANOJO DE 1 KG") == "ESPINACA DE 1"
    assert ep.norm_nombre("CHILE  JALAPEÑO (1/2)") == "CHILE JALAPENO 1 2"
    assert ep.norm_nombre(None) == ep.norm_nombre("KG") == ""
    # la unidad pegada al número también se va; el número se queda (revisión
    # del 2-oct: «25KG» y «25 KG» no casaban)
    assert ep.norm_nombre("FRIJOL NEGRO 25KG") == ep.norm_nombre("FRIJOL NEGRO 25 KG") \
        == "FRIJOL NEGRO 25"
    assert ep.norm_nombre("FRIJOL NEGRO 25KG") != ep.norm_nombre("FRIJOL NEGRO KG")
    assert ep.norm_nombre("AGUA 1LT") == ep.norm_nombre("AGUA 1 LT") == "AGUA 1"
    assert ep.norm_nombre("V8 4X4") == "V8 4X4"           # sin unidad, no se toca


def test_gana_el_sae_los_cinco_casos(client, env, auth_as):
    p = _cinco_casos(client, env, auth_as)
    claves = list(_SAE)

    # sin el nombre, la regla de antes
    antes = _resolver(env, claves)
    assert {k: r.producto_id for k, r in antes.items()} == {
        "TOMATEVERDELIMKG": p["tomate_grande"], "HIERBABUENAKG": p["te"],
        "ESPINACAPZA": p["espinaca_manojo"], "PINAMIELPZ": p["pina"],
        "PAPAYAMARADOLKG": p["papaya"]}

    # con el nombre del SAE
    res = _resolver(env, claves, desc=_SAE)
    assert {k: (r.producto_id, r.regla) for k, r in res.items()} == {
        "TOMATEVERDELIMKG": (p["tomate_limpio"], ep.NOMBRE_SAE),
        "HIERBABUENAKG": (p["hierbabuena"], ep.NOMBRE_SAE),
        # quedan dos: desempata el uso entre ellos, el MANOJO ya no compite
        "ESPINACAPZA": (p["espinaca"], ep.USO_SERIE),
        "PINAMIELPZ": (p["pina_miel"], ep.NOMBRE_SAE),
        "PAPAYAMARADOLKG": (p["maradol"], ep.NOMBRE_SAE)}
    assert not any(r.debil for r in res.values())
    # los que perdieron siguen listados (el backfill los enseña), el ganador primero
    assert res["TOMATEVERDELIMKG"].competidores == (p["tomate_limpio"], p["tomate_grande"])
    assert res["ESPINACAPZA"].competidores == (p["espinaca"], p["espinaca_pza"],
                                               p["espinaca_manojo"])

    # y el endpoint escribe eso, con la presentación y la unidad del que ganó
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=700, claves=claves, desc=_SAE)
    got = [(l["clave"], l["producto_id"], l["presentacion"], l["clave_unidad"])
           for l in _lineas_bd(f["id"])]
    assert got == [
        ("TOMATEVERDELIMKG", p["tomate_limpio"], "KILO", "KGM"),
        ("HIERBABUENAKG", p["hierbabuena"], "KILO", "KGM"),
        ("ESPINACAPZA", p["espinaca"], "PIEZA", "H87"),
        ("PINAMIELPZ", p["pina_miel"], "PIEZA", "H87"),
        ("PAPAYAMARADOLKG", p["maradol"], "KILO", "KGM"),   # KILO de un producto de PIEZA
    ]


def test_gana_el_sae_no_toca_una_clave_de_un_solo_producto(client, env, auth_as):
    """Solo aplica a claves gemelas. AJOKG la trae solo el 00010472 AJO KG: la
    remisión ligada con el 00000284 AJO sigue mandando aunque el SAE diga «AJO»
    (y AJO KG también se llama así), y sin remisión la clave es del AJO KG. Un
    gemelo DESACTIVADO no hace gemela a la clave: SANDIAPZ es del
    sobreviviente de la fusión aunque el SAE nombre al apagado."""
    ajo = _prod(env, "00000284", "AJO", "AJOPRIMERAKG", codigo_cliente="AJOKG")
    ajo_kg = _prod(env, "00010472", "AJO KG", "AJOKG")
    sandia = _prod(env, "00000391", "SANDIA", "SANDIAKG", presentaciones={
        "KILO": 1, "PIEZA": {"sat": "H87", "factor": 1, "clave_sae": "SANDIAPZ"}})
    _prod(env, "00011135", "SANDIA ENTERA", "SANDIAPZ", unidad_sat="H87",
          unidad_base="PIEZA", activo=False)
    _remision(client, env, auth_as, ajo, marca="ZHGO 710")
    hk = _bot(client, env, auth_as)
    desc = {"AJOKG": "AJO", "SANDIAPZ": "SANDIA ENTERA PZA"}
    f = _espejo(hk, client, folio=710, claves=["AJOKG", "SANDIAPZ"], desc=desc)
    assert [l["producto_id"] for l in _lineas_bd(f["id"])] == [ajo, sandia]
    g = _espejo(hk, client, folio=711, claves=["AJOKG", "SANDIAPZ"], desc=desc)
    assert [l["producto_id"] for l in _lineas_bd(g["id"])] == [ajo_kg, sandia]
    res = _resolver(env, ["AJOKG", "SANDIAPZ"], desc=desc)
    assert {r.regla for r in res.values()} == {ep.UNICA}


def test_gana_el_sae_un_nombre_que_no_casa_deja_la_regla_como_estaba(client, env, auth_as):
    """Si el SAE no se llama como ningún gemelo (o como uno que no trae la
    clave), la regla decide exactamente igual que sin nombre."""
    _prod(env, "00000317", "EJOTE", "EJOTEKG")
    b = _prod(env, "00010954", "EJOTE", "EJOTEKG", en_catalogo=True)
    _prod(env, "00000318", "EJOTE FRANCES", "EJOTEFRANCESKG")       # no trae la clave
    a = _prod(env, "00000500", "CHILE SERRANO", "CHILESERRKG")
    _prod(env, "00000501", "CHILE SERRANO GRANDE", "CHILESERRKG")
    _remision_bd(env, a, serie="RZHGO", n=3)
    claves = ["EJOTEKG", "CHILESERRKG"]
    desc = {"EJOTEKG": "EJOTE FRANCES KG", "CHILESERRKG": "CHILE SERRANO VERDE"}
    assert _resolver(env, claves, desc=desc) == _resolver(env, claves)
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=720, claves=claves, desc=desc)
    assert [l["producto_id"] for l in _lineas_bd(f["id"])] == [b, a]


def test_gana_el_sae_y_la_tabla_de_claves_distintas(client, env, auth_as, duenio_sae):
    """CALABAZACASTILKG la traen dos calabazas de castilla. En la 02 el SAE
    nombra a una y gana esa (sin el nombre ganaba la del catálogo); en la 03
    el SAE dice «CALABAZA CRIOLLA KG», que no es ninguna de las dos, y sigue
    mandando la tabla de claves distintas por empresa."""
    castilla = _prod(env, "00010738", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    _prod(env, "00010902", "CALABAZA CASTILLA PIEZA", "CALABAZACASTILKG", en_catalogo=True)
    criolla = _prod(env, "00010233", "CALABAZA CRIOLLA", "CALABAZCRIOLLAKG")
    hk = _bot(client, env, auth_as)
    pac = _espejo(hk, client, folio=725, claves=["CALABAZACASTILKG"],
                  desc={"CALABAZACASTILKG": "CALABAZA DE CASTILLA KG"})
    assert _lineas_bd(pac["id"])[0]["producto_id"] == castilla
    tab = _espejo(hk, client, folio=1525, empresa="03", serie="ZEHMOVH", cliente_sae="7",
                  claves=["CALABAZACASTILKG"], desc={"CALABAZACASTILKG": "CALABAZA CRIOLLA KG"})
    assert _lineas_bd(tab["id"])[0]["producto_id"] == criolla


def test_gana_el_sae_mueve_lo_ligado_en_el_reenvio_y_luego_ya_no(client, env, auth_as):
    """El nivel va antes de la remisión ligada y de la decisión previa: el
    reenvío de una factura ya ligada al gemelo equivocado la pasa al que el
    SAE nombra (eso sí cambia lo que Mini Conta tenía), y de ahí cada reenvío
    —abono, cancelación, cuadre— deja exactamente lo mismo."""
    p = _cinco_casos(client, env, auth_as)
    claves = list(_SAE)
    hk = _bot(client, env, auth_as)
    u = str(uuid.uuid4())
    # como quedó con la regla de antes (la descripción no casaba con nada)
    f = _espejo(hk, client, folio=730, claves=claves, uuid_f=u)
    assert [l["producto_id"] for l in _lineas_bd(f["id"])] == [
        p["tomate_grande"], p["te"], p["espinaca_manojo"], p["pina"], p["papaya"]]

    _espejo(hk, client, folio=730, claves=claves, uuid_f=u, desc=_SAE)
    movida = _lineas_bd(f["id"])
    assert [l["producto_id"] for l in movida] == [
        p["tomate_limpio"], p["hierbabuena"], p["espinaca"], p["pina_miel"], p["maradol"]]
    for _ in range(2):
        _espejo(hk, client, folio=730, claves=claves, uuid_f=u, desc=_SAE, saldo="5.00")
        assert _lineas_bd(f["id"]) == movida
    # y de ahí: el único nombrado gana por el nombre, y entre los dos
    # «ESPINACA PZA» la decisión previa (que ya es uno de ellos)
    res = _resolver(env, claves, desc=_SAE, factura_id=f["id"])
    assert {k: r.regla for k, r in res.items()} == {
        k: (ep.PREVIA if k == "ESPINACAPZA" else ep.NOMBRE_SAE) for k in claves}


def test_gana_el_sae_tambien_contra_la_remision_ligada(client, env, auth_as):
    """La remisión ligada trae el gemelo que el SAE NO nombra: gana el SAE (la
    factura es la verdad final). Si trae al nombrado, es el mismo."""
    grande = _prod(env, "00010048", "TOMATE VERDE GRANDE Y LIMPIO", "TOMATEVERDELIMKG")
    limpio = _prod(env, "00010049", "TOMATE VERDE LIMPIO", "TOMATEVERDELIMKG")
    _remision(client, env, auth_as, grande, marca="ZHGO 740")
    _remision(client, env, auth_as, limpio, marca="ZHGO 741")
    hk = _bot(client, env, auth_as)
    desc = {"TOMATEVERDELIMKG": _SAE["TOMATEVERDELIMKG"]}
    f = _espejo(hk, client, folio=740, claves=["TOMATEVERDELIMKG"], desc=desc)
    assert _lineas_bd(f["id"])[0]["producto_id"] == limpio
    g = _espejo(hk, client, folio=741, claves=["TOMATEVERDELIMKG"], desc=desc)
    assert _lineas_bd(g["id"])[0]["producto_id"] == limpio
    # el reenvío con el mismo nombre no se mueve; sin el nombre, la remisión
    # de la 740 mandaba (por encima de la decisión previa, como antes)
    _espejo(hk, client, folio=740, claves=["TOMATEVERDELIMKG"], desc=desc)
    assert _lineas_bd(f["id"])[0]["producto_id"] == limpio
    sin_nombre = _resolver(env, ["TOMATEVERDELIMKG"], factura_id=f["id"])["TOMATEVERDELIMKG"]
    assert (sin_nombre.producto_id, sin_nombre.regla) == (grande, ep.REMISION)


def _remision_ligada_bd(env, factura_id, pid):
    """Una remisión ligada a la factura (`remisiones.factura_id`), directo en
    la base: la API no remisiona un producto desactivado."""
    db = SessionLocal()
    try:
        rem = Remision(tenant_id=env["tenant"], folio_interno=f"RL-{uuid.uuid4().hex[:6]}",
                       cliente_facturacion_id=env["cli"], factura_id=factura_id)
        db.add(rem); db.flush()
        db.add(LineaRemision(tenant_id=env["tenant"], remision_id=rem.id, numero_linea=1,
                             producto_id=pid, cantidad_solicitada=1, precio_unitario=10))
        db.commit()
    finally:
        db.close()


def test_gana_el_sae_si_queda_un_gemelo_activo_gana_sin_mas(client, env, auth_as):
    """Revisión del 2-oct: «si queda uno, gana», al pie de la letra. Un
    producto que se llama igual que el artículo del SAE pero NO es gemelo
    activo de la clave —aquí el 00011014 HIERBABUENA desactivado— ya no gana
    por la remisión ligada ni por la decisión previa: la partida va al único
    gemelo activo que el SAE nombra. Antes el «lo que el SAE también nombra
    se respeta» lo dejaba ganar."""
    _prod(env, "00010026", "TE DE YERBABUENA NATURAL (1000 G)", "HIERBABUENAKG")
    hierbabuena = _prod(env, "00010724", "HIERBABUENA", "HIERBABUENAKG")
    apagada = _prod(env, "00011014", "HIERBABUENA", "HIERBABUENAKG", activo=False)
    hk = _bot(client, env, auth_as)
    desc = {"HIERBABUENAKG": "HIERBABUENA"}
    u = str(uuid.uuid4())
    f = _espejo(hk, client, folio=750, claves=["HIERBABUENAKG"], desc=desc, uuid_f=u)
    assert _lineas_bd(f["id"])[0]["producto_id"] == hierbabuena

    # la remisión ligada trae el desactivado que se llama igual
    _remision_ligada_bd(env, f["id"], apagada)
    r = _resolver(env, ["HIERBABUENAKG"], desc=desc, factura_id=f["id"], con_previa=False)
    assert (r["HIERBABUENAKG"].producto_id, r["HIERBABUENAKG"].regla) == (hierbabuena, ep.NOMBRE_SAE)
    # sin el nombre del SAE la remisión sí manda, como antes
    r = _resolver(env, ["HIERBABUENAKG"], factura_id=f["id"], con_previa=False)
    assert (r["HIERBABUENAKG"].producto_id, r["HIERBABUENAKG"].regla) == (apagada, ep.REMISION)

    # ligada al desactivado (como la dejaba el espejo viejo): el reenvío la
    # mueve al activo, y de ahí cada reenvío deja lo mismo
    _ligar(f["id"], apagada)
    _espejo(hk, client, folio=750, claves=["HIERBABUENAKG"], desc=desc, uuid_f=u, saldo="1.00")
    movida = _lineas_bd(f["id"])
    assert movida[0]["producto_id"] == hierbabuena
    _espejo(hk, client, folio=750, claves=["HIERBABUENAKG"], desc=desc, uuid_f=u, saldo="0.50")
    assert _lineas_bd(f["id"]) == movida


def test_gana_el_sae_con_varios_nombrados_desempata_solo_entre_ellos(client, env, auth_as):
    """Revisión del 2-oct, el caso de producción: CALABAZACASTILKG la traen dos
    activos que se llaman «CALABAZA DE CASTILLA KG» (00010738 y 00010902), y
    6 partidas de ZMAFAN estaban en el 00010058 CALABAZA DE CASTILLA, que se
    llama igual pero trae OTRA clave (CALABAZACASTIKG; el espejo viejo lo
    ligó por el código del cliente). Ni su decisión previa ni una remisión
    ligada con él entran al desempate: decide el resto de la regla, pero solo
    entre los dos nombrados. Una remisión ligada con uno de ELLOS sí decide."""
    castilla = _prod(env, "00010058", "CALABAZA DE CASTILLA", "CALABAZACASTIKG",
                     codigo_cliente="CALABAZACASTILKG")
    kg_a = _prod(env, "00010738", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    kg_b = _prod(env, "00010902", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    _remision_bd(env, kg_b, serie="RZHGO", n=3)        # el uso en la serie da el 902
    hk = _bot(client, env, auth_as)
    clave = "CALABAZACASTILKG"
    desc = {clave: "CALABAZA DE CASTILLA KG"}
    u = str(uuid.uuid4())
    f = _espejo(hk, client, folio=760, claves=[clave], desc=desc, uuid_f=u)
    assert _lineas_bd(f["id"])[0]["producto_id"] == kg_b

    # ligada al 00010058: el reenvío la pasa a un gemelo nombrado
    _ligar(f["id"], castilla)
    r = _resolver(env, [clave], desc=desc, factura_id=f["id"])[clave]
    assert (r.producto_id, r.regla) == (kg_b, ep.USO_SERIE)
    _espejo(hk, client, folio=760, claves=[clave], desc=desc, uuid_f=u, saldo="1.00")
    assert _lineas_bd(f["id"])[0]["producto_id"] == kg_b

    # una remisión ligada con el 00010058 (por el código del cliente) no cuenta
    _remision_ligada_bd(env, f["id"], castilla)
    r = _resolver(env, [clave], desc=desc, factura_id=f["id"], con_previa=False)[clave]
    assert (r.producto_id, r.regla) == (kg_b, ep.USO_SERIE)
    # una con el 00010738, que sí es de los nombrados, decide entre ellos
    _remision_ligada_bd(env, f["id"], kg_a)
    r = _resolver(env, [clave], desc=desc, factura_id=f["id"], con_previa=False)[clave]
    assert (r.producto_id, r.regla) == (kg_a, ep.REMISION)


def test_gana_el_sae_la_unidad_pegada_al_numero(client, env, auth_as):
    """Revisión del 2-oct: el SAE de la 02 escribe «FRIJOL NEGRO 25KG» y el
    catálogo tiene el 00000115 «FRIJOL NEGRO 25 KG» (la clave en su KILO) y el
    00010982 «FRIJOL NEGRO 25KG». `norm_nombre` quitaba el KG suelto y no el
    pegado, así que solo el 982 contaba como nombrado. Ahora los dos 25 KG
    compiten (y el resto de la regla desempata) y el FRIJOL NEGRO KG a secas,
    que es otro artículo, queda fuera."""
    f25 = _prod(env, "00000115", "FRIJOL NEGRO 25 KG", "FRIJ-CERE-556",
                presentaciones={"KILO": {"factor": 1, "clave_sae": "FRIJOLNEGROKG"}})
    fkg = _prod(env, "00010763", "FRIJOL NEGRO KG", "FRIJOLNEGROKG")
    f25kg = _prod(env, "00010982", "FRIJOL NEGRO 25KG", "FRIJOLNEGROKG")
    _remision_bd(env, f25kg, serie="RZHGO", n=2)
    clave = "FRIJOLNEGROKG"
    r = _resolver(env, [clave], desc={clave: "FRIJOL NEGRO 25KG"})[clave]
    assert (r.producto_id, r.regla) == (f25kg, ep.USO_SERIE)
    assert r.competidores == (f25kg, f25, fkg)          # el 115 compite; el KG a secas, fuera
    r = _resolver(env, [clave], desc={clave: "FRIJOL NEGRO KG"})[clave]
    assert (r.producto_id, r.regla) == (fkg, ep.NOMBRE_SAE)


def test_gana_el_sae_backfill_y_endpoint_mueven_igual_lo_que_no_es_gemelo_activo(
        client, env, auth_as):
    """Lo de las dos pruebas de arriba, por el backfill: las partidas ligadas
    al desactivado y al de otra clave salen en el paso 3 como «solo» (el
    siguiente reenvío las mueve), sin ⚠, hacia lo mismo que deja el
    endpoint; --recalcular-ligadas escribe eso y una segunda corrida no hace
    nada."""
    from scripts.backfill_espejo_producto_por_clave import correr

    _prod(env, "00010026", "TE DE YERBABUENA NATURAL (1000 G)", "HIERBABUENAKG")
    hierbabuena = _prod(env, "00010724", "HIERBABUENA", "HIERBABUENAKG")
    apagada = _prod(env, "00011014", "HIERBABUENA", "HIERBABUENAKG", activo=False)
    castilla = _prod(env, "00010058", "CALABAZA DE CASTILLA", "CALABAZACASTIKG")
    _prod(env, "00010738", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    kg_b = _prod(env, "00010902", "CALABAZA DE CASTILLA KG", "CALABAZACASTILKG")
    _remision_bd(env, kg_b, serie="RZHGO", n=6)        # fuerte: --recalcular la escribe
    claves = ["HIERBABUENAKG", "CALABAZACASTILKG"]
    desc = {"HIERBABUENAKG": "HIERBABUENA", "CALABAZACASTILKG": "CALABAZA DE CASTILLA KG"}
    hk = _bot(client, env, auth_as)
    u = str(uuid.uuid4())
    f = _espejo(hk, client, folio=770, claves=claves, desc=desc, uuid_f=u)
    esperado = _lineas_bd(f["id"])
    assert [l["producto_id"] for l in esperado] == [hierbabuena, kg_b]
    db = SessionLocal()
    try:
        for clave, pid in (("HIERBABUENAKG", apagada), ("CALABAZACASTILKG", castilla)):
            db.execute(text("UPDATE lineas_factura SET producto_id = :p, presentacion = NULL "
                            "WHERE factura_id = :f AND clave_sae = :k"),
                       {"p": pid, "f": f["id"], "k": clave})
        db.commit()

        seco = correr(db, env["tenant"], salida=lambda *_: None)
        filas = {f_["clave"]: f_ for f_ in seco["tabla_paso3"]}
        assert {k: (v["de_sku"], v["a_sku"], v["regla"], v["solo"], v["marca"])
                for k, v in filas.items()} == {
            "HIERBABUENAKG": ("00011014", "00010724", ep.NOMBRE_SAE, True, ""),
            "CALABAZACASTILKG": ("00010058", "00010902", ep.USO_SERIE, True, "")}

        hecho = correr(db, env["tenant"], aplicar=True, recalcular=True, salida=lambda *_: None)
        assert hecho["paso3_escritas"] == 2
        assert _lineas_bd(f["id"]) == esperado
        otra = correr(db, env["tenant"], aplicar=True, recalcular=True, salida=lambda *_: None)
        assert otra["paso1_escritas"] == otra["paso2_escritas"] == otra["paso3_escritas"] == 0
    finally:
        db.close()
    # el reenvío por el endpoint deja exactamente lo mismo
    _espejo(hk, client, folio=770, claves=claves, desc=desc, uuid_f=u, saldo="1.00")
    assert _lineas_bd(f["id"]) == esperado


def test_gana_el_sae_backfill_y_endpoint_deciden_igual(client, env, auth_as):
    """El backfill lee la descripción guardada (la que mandó el SAE) y decide
    lo mismo que el endpoint: las partidas sin producto quedan igual que una
    factura nueva, las ya ligadas al gemelo equivocado salen en el paso 3 como
    «solo» (el siguiente reenvío las mueve) con 0_nombre_sae, y nada queda
    con ⚠. Idempotente."""
    from scripts.backfill_espejo_producto_por_clave import correr

    p = _cinco_casos(client, env, auth_as)
    claves = list(_SAE)
    hk = _bot(client, env, auth_as)
    nueva = _espejo(hk, client, folio=760, claves=claves, desc=_SAE)
    vieja = _espejo(hk, client, folio=761, claves=claves, desc=_SAE)
    esperado = _lineas_bd(nueva["id"])
    db = SessionLocal()
    try:
        # la 760 como antes del backfill (sin producto); la 761 como la dejó
        # la regla de antes (al gemelo que el SAE no nombra)
        db.execute(text("UPDATE lineas_factura SET producto_id = NULL, presentacion = NULL, "
                        "clave_unidad = 'H87', clave_prod_serv = '01010101' "
                        "WHERE factura_id = :f"), {"f": nueva["id"]})
        for clave, pid in (("TOMATEVERDELIMKG", p["tomate_grande"]), ("HIERBABUENAKG", p["te"]),
                           ("ESPINACAPZA", p["espinaca_manojo"]), ("PINAMIELPZ", p["pina"]),
                           ("PAPAYAMARADOLKG", p["papaya"])):
            db.execute(text("UPDATE lineas_factura SET producto_id = :p WHERE factura_id = :f "
                            "AND clave_sae = :k"), {"p": pid, "f": vieja["id"], "k": clave})
        db.commit()

        seco = correr(db, env["tenant"], salida=lambda *_: None)
        assert seco["paso1_por_regla"] == {ep.NOMBRE_SAE: 4, ep.USO_SERIE: 1}
        assert all(f["marca"] == "" for f in seco["tabla_gemelos"] + seco["tabla_paso3"])
        assert len(seco["tabla_paso3"]) == 5
        assert all(f["solo"] for f in seco["tabla_paso3"])
        assert {f["regla"] for f in seco["tabla_paso3"]} == {ep.NOMBRE_SAE, ep.USO_SERIE}

        hecho = correr(db, env["tenant"], aplicar=True, recalcular=True, salida=lambda *_: None)
        assert hecho["paso1_escritas"] == 5 and hecho["paso3_escritas"] == 5
        assert _lineas_bd(nueva["id"]) == esperado
        assert _lineas_bd(vieja["id"]) == esperado

        otra = correr(db, env["tenant"], aplicar=True, recalcular=True, salida=lambda *_: None)
        assert otra["paso1_escritas"] == otra["paso2_escritas"] == otra["paso3_escritas"] == 0
        assert sum(otra["paso3"].values()) == 0
    finally:
        db.close()
    # y el reenvío por el endpoint deja exactamente lo mismo
    for f in (nueva, vieja):
        _espejo(hk, client, folio=f["folio"], claves=claves, desc=_SAE)
        assert _lineas_bd(f["id"]) == esperado

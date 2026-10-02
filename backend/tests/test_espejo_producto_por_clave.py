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
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.auth import Principal, get_principal
from app.core.db import SessionLocal
from app.main import app
from app.models import (
    Cliente,
    ClienteExterno,
    Factura,
    LineaFactura,
    Membership,
    Producto,
    ProductoCliente,
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


def _espejo(hk, client, *, folio, claves, empresa="02", serie="ZHGO", cliente_sae="6"):
    lineas = [{"clave": k, "descripcion": f"PARTIDA {k}", "cantidad": "2",
               "precio_unitario": "10.00"} for k in claves]
    r = client.post("/api/v1/facturas/espejo", headers=hk, json={
        "empresa": empresa, "serie": serie, "folio": folio, "cliente_sae": cliente_sae,
        "fecha": "2026-09-15T12:00:00Z", "uuid_fiscal": str(uuid.uuid4()),
        "subtotal": str(20 * len(claves)), "total": str(20 * len(claves)),
        "lineas": lineas,
    })
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


def test_la_regla_dice_por_que_nivel_gano():
    # (remisión, previa, catálogo, uso serie, uso global)
    assert ep._regla((1, 0, 0, 0, 0), (0, 0, 1, 9, 9)) == (ep.REMISION, False)
    assert ep._regla((0, 0, 1, 0, 0), (0, 0, 0, 50, 50)) == (ep.CATALOGO, False)
    assert ep._regla((0, 0, 0, 40, 40), (0, 0, 0, 3, 3)) == (ep.USO_SERIE, False)
    assert ep._regla((0, 0, 0, 3, 3), (0, 0, 0, 2, 2)) == (ep.USO_SERIE, True)   # débil
    assert ep._regla((0, 0, 0, 0, 7), (0, 0, 0, 0, 7)) == (ep.SKU_MENOR, True)


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
    _prod(env, "00000310", "CILANTRO CRIOLLO", "CILANTROKG")
    b = _prod(env, "00000311", "CILANTRO MANOJO", "CILANTROKG")
    _prod(env, "00010026", "TE DE YERBABUENA", "HIERBABUENAKG")
    d = _prod(env, "00010724", "HIERBABUENA", "HIERBABUENAKG")
    # uso en ESTA serie (remisión RZHGO, sin ligar a ninguna factura)
    _remision(client, env, auth_as, b)
    # uso SOLO en otra serie: cuenta para el global
    db = SessionLocal()
    try:
        otra = Factura(tenant_id=env["tenant"], serie="ZECA", folio=1, cliente_id=env["cli"],
                       origen="ESPEJO_SAE", espejo_empresa="02", estado="TIMBRADA")
        db.add(otra); db.flush()
        db.add(LineaFactura(tenant_id=env["tenant"], factura_id=otra.id, numero_linea=1,
                            producto_id=d, clave_prod_serv="50401700", clave_unidad="KGM",
                            descripcion="HIERBABUENA", cantidad=1, valor_unitario=1))
        db.commit()
        # la regla dice por qué (antes de depositar: después, la propia factura
        # ya sería uso en la serie)
        res = ep.resolver_claves(ep.FuentesBD(db, env["tenant"]), factura_id=None,
                                 cliente_id=env["cli"], empresa="02", serie="ZHGO",
                                 claves=["CILANTROKG", "HIERBABUENAKG"])
    finally:
        db.close()
    assert res["CILANTROKG"].regla == ep.USO_SERIE
    assert res["HIERBABUENAKG"].regla == ep.USO_GLOBAL
    assert res["HIERBABUENAKG"].debil       # 1 contra 0: decidió por poco

    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=104, claves=["CILANTROKG", "HIERBABUENAKG"])
    assert [l["producto_id"] for l in _lineas_bd(f["id"])] == [b, d]


def test_calabaza_castilla_en_tabasco_es_calabaza_criolla(client, env, auth_as):
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


def test_backfill_informa_las_ligadas_a_otro_producto_sin_tocarlas(client, env, auth_as):
    from scripts.backfill_espejo_producto_por_clave import correr

    ajo_kg = _prod(env, "00010472", "AJO KG", "AJOKG")
    ajo = _prod(env, "00000284", "AJO", "AJOPRIMERAKG")
    hk = _bot(client, env, auth_as)
    f = _espejo(hk, client, folio=310, claves=["AJOKG"])
    assert _lineas_bd(f["id"])[0]["producto_id"] == ajo_kg
    # como la dejó el espejo viejo: por el código del cliente, al otro AJO
    db = SessionLocal()
    try:
        db.execute(text("UPDATE lineas_factura SET producto_id = :p WHERE factura_id = :f"),
                   {"p": ajo, "f": f["id"]})
        db.commit()
        c = correr(db, env["tenant"], aplicar=True, salida=lambda *_: None)
        assert sum(c["info_cambiaria"].values()) == 1
        assert c["paso1_escritas"] == 0 and c["paso2_escritas"] == 0
    finally:
        db.close()
    assert _lineas_bd(f["id"])[0]["producto_id"] == ajo


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

"""El SAE 9 (Firebird 2.5) de punta a punta, contra un Firebird DE VERDAD.

No hay SAE en CI —ni debe haberlo—, así que esta prueba se SALTA salvo que
se le dé un Firebird 2.5 donde crear una base de juguete:

    docker run -d --name sae9-fb-test --platform linux/amd64 \\
        -p 127.0.0.1:3051:3050 -e ISC_PASSWORD=<una de prueba> jacobalberty/firebird:2.5-ss
    SAE9_FB_TEST_HOST=127.0.0.1 SAE9_FB_TEST_PORT=3051 SAE9_FB_TEST_PASSWORD=<la misma> \\
        pytest -q tests/test_sae9_firebird.py

Las tablas llevan los TIPOS REALES de un SAE90EMPRE01.FDB de Aspel SAE 9
(volcado de RDB$RELATION_FIELDS, 26-sep-2026): todo el texto VARCHAR en
ISO8859_1, las fechas del documento TIMESTAMP, el dinero DOUBLE PRECISION, y
las fechas del CFDI (FECHA_CERT, FECHA_CANCELA) como TEXTO. Los datos
reproducen lo que se sabe del SAE 9 de verdad: series con espacio ('FOR K'),
claves de cliente rellenas a la izquierda, acentos y Ñ, y un documento con el
folio pegado.
"""
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import ClaveSae, Cliente, ClienteExterno, Factura, NotaCredito, ReciboPago, Tenant
from app.services import clientes_sae, cobranza_sae, espejo_sae, sae_lectura
from app.services.rfc import _digito_verificador
from app.services.sae_fuentes import EmpresaSAE, ServidorSAE

HOST = os.environ.get("SAE9_FB_TEST_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="sin Firebird de prueba (SAE9_FB_TEST_HOST)")

PUERTO = int(os.environ.get("SAE9_FB_TEST_PORT") or 3050)
PASSWORD = os.environ.get("SAE9_FB_TEST_PASSWORD") or ""
DIR = os.environ.get("SAE9_FB_TEST_DIR") or "/firebird/data"

_V = "CHARACTER SET ISO8859_1"
# Sólo las columnas que el espejo lee y algunas vecinas; tipos tal cual el SAE 9.
DDL = {
    "FACTF01": f"""CVE_DOC VARCHAR(20) {_V} NOT NULL PRIMARY KEY, SERIE VARCHAR(10) {_V},
        FOLIO INTEGER, CVE_CLPV VARCHAR(10) {_V} NOT NULL, FECHA_DOC TIMESTAMP NOT NULL,
        CAN_TOT DOUBLE PRECISION, IMPORTE DOUBLE PRECISION, IMP_TOT1 DOUBLE PRECISION,
        IMP_TOT4 DOUBLE PRECISION, STATUS VARCHAR(1) {_V}, CVE_OBS INTEGER,
        UUID VARCHAR(50) {_V}, FECHA_CANCELA TIMESTAMP,
        METODODEPAGO VARCHAR(255) {_V}, FORMADEPAGOSAT VARCHAR(5) {_V}""",
    "CFDI01": f"""TIPO_DOC VARCHAR(1) {_V} NOT NULL, CVE_DOC VARCHAR(20) {_V} NOT NULL,
        UUID VARCHAR(36) {_V}, FECHA_CERT VARCHAR(30) {_V}, FECHA_CANCELA VARCHAR(30) {_V},
        XML_DOC BLOB SUB_TYPE 1 {_V}, MSJ_CANC VARCHAR(80) {_V}, UUID_REL VARCHAR(36) {_V},
        PRIMARY KEY (TIPO_DOC, CVE_DOC)""",
    "OBS_DOCF01": f"CVE_OBS INTEGER NOT NULL PRIMARY KEY, STR_OBS VARCHAR(255) {_V}",
    "PAR_FACTF01": f"""CVE_DOC VARCHAR(20) {_V} NOT NULL, NUM_PAR INTEGER NOT NULL,
        CVE_ART VARCHAR(16) {_V}, CANT DOUBLE PRECISION, PREC DOUBLE PRECISION,
        TOT_PARTIDA DOUBLE PRECISION, PRIMARY KEY (CVE_DOC, NUM_PAR)""",
    "INVE01": f"""CVE_ART VARCHAR(16) {_V} NOT NULL PRIMARY KEY, DESCR VARCHAR(40) {_V},
        STATUS VARCHAR(1) {_V}""",
    "CUEN_M01": f"""CVE_CLIE VARCHAR(10) {_V} NOT NULL, REFER VARCHAR(20) {_V} NOT NULL,
        NUM_CPTO INTEGER NOT NULL, NUM_CARGO INTEGER NOT NULL, IMPORTE DOUBLE PRECISION,
        FECHA_APLI TIMESTAMP, TIPO_MOV VARCHAR(1) {_V}, SIGNO INTEGER,
        PRIMARY KEY (CVE_CLIE, REFER, NUM_CPTO, NUM_CARGO)""",
    "CUEN_DET01": f"""CVE_CLIE VARCHAR(10) {_V} NOT NULL, REFER VARCHAR(20) {_V} NOT NULL,
        ID_MOV INTEGER NOT NULL, NUM_CPTO INTEGER, NUM_CARGO INTEGER NOT NULL,
        NO_PARTIDA INTEGER NOT NULL, DOCTO VARCHAR(20) {_V}, IMPORTE DOUBLE PRECISION,
        FECHA_APLI TIMESTAMP, TIPO_MOV VARCHAR(1) {_V}, SIGNO INTEGER,
        CVE_DOC_COMPPAGO VARCHAR(20) {_V}""",
    "FACTG01": f"""CVE_DOC VARCHAR(20) {_V} NOT NULL PRIMARY KEY, SERIE VARCHAR(10) {_V},
        FOLIO INTEGER, CVE_CLPV VARCHAR(10) {_V} NOT NULL, FECHA_DOC TIMESTAMP NOT NULL,
        STATUS VARCHAR(1) {_V}, FORMADEPAGOSAT VARCHAR(5) {_V}""",
    "CLIE01": f"""CLAVE VARCHAR(10) {_V} NOT NULL PRIMARY KEY, STATUS VARCHAR(1) {_V} NOT NULL,
        NOMBRE VARCHAR(254) {_V}, RFC VARCHAR(15) {_V}, CALLE VARCHAR(80) {_V},
        NUMEXT VARCHAR(15) {_V}, NUMINT VARCHAR(15) {_V}, COLONIA VARCHAR(50) {_V},
        CODIGO VARCHAR(5) {_V}, EMAILPRED VARCHAR(512) {_V}, REG_FISC VARCHAR(4) {_V},
        USO_CFDI VARCHAR(5) {_V}, DIASCRED INTEGER, LIMCRED DOUBLE PRECISION""",
}


def _rfc(base12: str) -> str:
    """Un RFC con su dígito verificador bueno (base de 11 o 12 sin el dígito)."""
    return base12 + _digito_verificador(base12)


RFC_IFOOD = _rfc("IME200101AB")      # moral: 3 letras + fecha + homoclave(2) + dígito
RFC_NEWREST = _rfc("NCM1905203K")
RFC_VIEJO = _rfc("CSF1001015Q")


def _doc(serie: str, folio: int) -> str:
    """CVE_DOC como lo escribe SAE: serie a 10 y folio alineado a la derecha."""
    return serie.ljust(10) + str(folio).rjust(10)


def _cli(clave: str) -> str:
    return clave.rjust(10)            # SAE rellena la clave del cliente a la izquierda


def _crear_tablas(con, nn: str):
    cur = con.cursor()
    for tabla, cols in DDL.items():
        cur.execute(f"CREATE TABLE {tabla[:-2]}{nn} ({cols})")
    con.commit()
    return cur


def _sembrar(con):
    cur = _crear_tablas(con, "01")
    ts = dt.datetime
    ins = lambda sql, *p: cur.execute(sql, p)  # noqa: E731
    # Clientes: 145 (iFood) y 9 (Newrest) facturan en 2026; 77 sólo en 2025.
    ins("INSERT INTO CLIE01 (CLAVE, STATUS, NOMBRE, RFC, CALLE, NUMEXT, COLONIA, CODIGO, "
        "REG_FISC, USO_CFDI) VALUES (?,?,?,?,?,?,?,?,?,?)",
        _cli("145"), "A", "IFOOD MÉXICO", RFC_IFOOD, "REFORMA", "222", "JUÁREZ", "06600",
        "601", "G03")
    ins("INSERT INTO CLIE01 (CLAVE, STATUS, NOMBRE, RFC, CODIGO, REG_FISC, USO_CFDI, DIASCRED, "
        "LIMCRED, EMAILPRED) VALUES (?,?,?,?,?,?,?,?,?,?)", _cli("9"), "A",
        "NEWREST CATERING MÉXICO", RFC_NEWREST, "11000", "601", "G03", 30, 50000.0,
        "CxP@newrest.mx; facturas@newrest.mx")
    ins("INSERT INTO CLIE01 (CLAVE, STATUS, NOMBRE, RFC) VALUES (?,?,?,?)",
        _cli("77"), "A", "COMEDOR QUE YA NO COMPRA", RFC_VIEJO)
    # Catálogo con Ñ y acentos.
    ins("INSERT INTO INVE01 (CVE_ART, DESCR, STATUS) VALUES (?,?,?)",
        "ESPINACA", "ESPINACA BAÑADA ÑANDÚ", "A")
    ins("INSERT INTO INVE01 (CVE_ART, DESCR, STATUS) VALUES (?,?,?)", "PAPA", "PAPA BLANCA", "B")
    ins("INSERT INTO OBS_DOCF01 (CVE_OBS, STR_OBS) VALUES (?,?)", 1, "OC 4500123 · Ñuñoa")
    # Facturas de la serie 'FOR K' (con espacio): 9 es de 2025 (fuera del piso).
    facturas = [
        # folio, cliente, fecha, subtotal, total, iva, status, uuid, cancela
        (9, "145", ts(2025, 12, 30, 10), 100.0, 116.0, 16.0, "E", "u-2025", None),
        (10, "145", ts(2026, 3, 18, 9, 30), 1000.004, 1160.005, 160.0, "E", "u-10", None),
        (11, "9", ts(2026, 4, 2, 11), 500.0, 580.0, 80.0, "E", "u-11", None),
        (12, "145", ts(2026, 9, 9, 12), 200.0, 232.0, 32.0, "C", "u-12", "2026-09-10T08:00:00"),
        (13, "145", ts(2026, 9, 20, 12), 300.0, 348.0, 48.0, "E", None, None),   # sin timbre
    ]
    for folio, cli, fecha, sub, tot, iva, st, uuid_f, cancela in facturas:
        cve = _doc("FOR K", folio)
        ins("INSERT INTO FACTF01 (CVE_DOC, SERIE, FOLIO, CVE_CLPV, FECHA_DOC, CAN_TOT, IMPORTE, "
            "IMP_TOT1, IMP_TOT4, STATUS, CVE_OBS, UUID, METODODEPAGO, FORMADEPAGOSAT) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            cve, "FOR K", folio, _cli(cli), fecha, sub, tot, 0.0, iva, st,
            1 if folio == 10 else None, "sae-movil-no-es-el-uuid",
            "PUE" if folio == 11 else "PPD", "03" if folio == 11 else "99")
        if uuid_f:
            ins("INSERT INTO CFDI01 (TIPO_DOC, CVE_DOC, UUID, FECHA_CERT, FECHA_CANCELA, MSJ_CANC) "
                "VALUES (?,?,?,?,?,?)", "F", cve, uuid_f, fecha.strftime("%Y-%m-%dT%H:%M:%S"),
                cancela, "Cancelada por error" if cancela else None)
        ins("INSERT INTO PAR_FACTF01 (CVE_DOC, NUM_PAR, CVE_ART, CANT, PREC, TOT_PARTIDA) "
            "VALUES (?,?,?,?,?,?)", cve, 1, "ESPINACA", 10.0, sub / 10, sub)
    # Una segunda serie con el folio PEGADO y relleno de ceros ('KELLYSLP0000000031').
    cve_k = "KELLYSLP0000000031"
    ins("INSERT INTO FACTF01 (CVE_DOC, SERIE, FOLIO, CVE_CLPV, FECHA_DOC, CAN_TOT, IMPORTE, "
        "IMP_TOT1, IMP_TOT4, STATUS) VALUES (?,?,?,?,?,?,?,?,?,?)",
        cve_k, "KELLYSLP", 31, _cli("9"), ts(2026, 9, 25, 16), 1000.0, 1160.0, 0.0, 160.0, "E")
    ins("INSERT INTO CFDI01 (TIPO_DOC, CVE_DOC, UUID, FECHA_CERT) VALUES (?,?,?,?)",
        "F", cve_k, "u-k31", "2026-09-25T16:05:00")
    ins("INSERT INTO PAR_FACTF01 (CVE_DOC, NUM_PAR, CVE_ART, CANT, PREC, TOT_PARTIDA) "
        "VALUES (?,?,?,?,?,?)", cve_k, 1, "PAPA", 4.0, 250.0, 1000.0)
    # CxC: cargo de FOR K 10 y un pago parcial con REP; pago de la KELLYSLP 31 (pegada).
    hoy = ts.combine(dt.date.today(), dt.time(9))
    for refer, cli, cargo in ((_doc("FOR K", 10), "145", 1160.005), (cve_k, "9", 1160.0)):
        ins("INSERT INTO CUEN_M01 (CVE_CLIE, REFER, NUM_CPTO, NUM_CARGO, IMPORTE, FECHA_APLI, "
            "TIPO_MOV, SIGNO) VALUES (?,?,?,?,?,?,?,?)", _cli(cli), refer, 1, 1, cargo, hoy, "C", 1)
    ins("INSERT INTO CUEN_DET01 (CVE_CLIE, REFER, ID_MOV, NUM_CPTO, NUM_CARGO, NO_PARTIDA, DOCTO, "
        "IMPORTE, FECHA_APLI, TIPO_MOV, SIGNO, CVE_DOC_COMPPAGO) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        _cli("145"), _doc("FOR K", 10), 1, 10, 1, 1, "PAGO1", 500.0, hoy, "A", -1, _doc("CP", 5))
    ins("INSERT INTO CUEN_DET01 (CVE_CLIE, REFER, ID_MOV, NUM_CPTO, NUM_CARGO, NO_PARTIDA, DOCTO, "
        "IMPORTE, FECHA_APLI, TIPO_MOV, SIGNO, CVE_DOC_COMPPAGO) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        _cli("9"), cve_k, 2, 10, 1, 1, "PAGO2", 1160.0, hoy, "A", -1, _doc("CP", 6))
    # Los REP (FACTG) y sus CFDI tipo G.
    for folio, cli in ((5, "145"), (6, "9")):
        ins("INSERT INTO FACTG01 (CVE_DOC, SERIE, FOLIO, CVE_CLPV, FECHA_DOC, STATUS, "
            "FORMADEPAGOSAT) VALUES (?,?,?,?,?,?,?)",
            _doc("CP", folio), "CP", folio, _cli(cli), hoy, "E", "03")
        ins("INSERT INTO CFDI01 (TIPO_DOC, CVE_DOC, UUID, FECHA_CERT) VALUES (?,?,?,?)",
            "G", _doc("CP", folio), f"u-rep-{folio}", hoy.strftime("%Y-%m-%dT%H:%M:%S"))
    # Una nota de crédito (CFDI E) con su serie en el XML y su renglón en CxC.
    ins("INSERT INTO CFDI01 (TIPO_DOC, CVE_DOC, UUID, FECHA_CERT, XML_DOC) VALUES (?,?,?,?,?)",
        "E", "NC0000000003", "u-nc-3", hoy.strftime("%Y-%m-%dT%H:%M:%S"),
        '<?xml version="1.0"?><cfdi:Comprobante Version="4.0" Serie="NC" Folio="3" '
        'TipoDeComprobante="E"></cfdi:Comprobante>')
    ins("INSERT INTO CUEN_DET01 (CVE_CLIE, REFER, ID_MOV, NUM_CPTO, NUM_CARGO, NO_PARTIDA, DOCTO, "
        "IMPORTE, FECHA_APLI, TIPO_MOV, SIGNO) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        _cli("145"), _doc("FOR K", 10), 3, 1002, 1, 1, "NC0000000003", 60.0, hoy, "A", -1)
    con.commit()
    cur.close()


def _sembrar_02(con):
    """La empresa 02 del mismo SAE: iFood también compra aquí (su clave es la 33)."""
    cur = _crear_tablas(con, "02")
    ins = lambda sql, *p: cur.execute(sql, p)  # noqa: E731
    ins("INSERT INTO CLIE02 (CLAVE, STATUS, NOMBRE, RFC) VALUES (?,?,?,?)",
        _cli("33"), "A", "IFOOD MÉXICO", RFC_IFOOD)
    ins("INSERT INTO INVE02 (CVE_ART, DESCR, STATUS) VALUES (?,?,?)", "PAPA", "PAPA", "A")
    cve = _doc("KELLYQRO", 40)
    ins("INSERT INTO FACTF02 (CVE_DOC, SERIE, FOLIO, CVE_CLPV, FECHA_DOC, CAN_TOT, IMPORTE, "
        "IMP_TOT1, IMP_TOT4, STATUS, METODODEPAGO) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        cve, "KELLYQRO", 40, _cli("33"), dt.datetime(2026, 8, 27, 10), 100.0, 116.0, 0.0,
        16.0, "E", "PPD")
    ins("INSERT INTO CFDI02 (TIPO_DOC, CVE_DOC, UUID, FECHA_CERT) VALUES (?,?,?,?)",
        "F", cve, "u-q40", "2026-08-27T10:05:00")
    ins("INSERT INTO PAR_FACTF02 (CVE_DOC, NUM_PAR, CVE_ART, CANT, PREC, TOT_PARTIDA) "
        "VALUES (?,?,?,?,?,?)", cve, 1, "PAPA", 1.0, 100.0, 100.0)
    con.commit()
    cur.close()


@pytest.fixture(scope="module")
def fb_empresa():
    import firebirdsql

    nombre = f"t{uuid.uuid4().hex[:8]}_SAE90EMPRE{{nn}}.FDB"
    ruta = f"{DIR}/{nombre}"
    con = firebirdsql.create_database(host=HOST, port=PUERTO, database=ruta.replace("{nn}", "01"),
                                      user="SYSDBA", password=PASSWORD, charset="ISO8859_1",
                                      auth_plugin_name="Legacy_Auth", wire_crypt=False)
    try:
        _sembrar(con)
    finally:
        con.close()
    con = firebirdsql.create_database(host=HOST, port=PUERTO, database=ruta.replace("{nn}", "02"),
                                      user="SYSDBA", password=PASSWORD, charset="ISO8859_1",
                                      auth_plugin_name="Legacy_Auth", wire_crypt=False)
    try:
        _sembrar_02(con)
    finally:
        con.close()
    srv = ServidorSAE(clave="SAE9", motor="firebird", host=HOST, puerto=PUERTO,
                      usuario="SYSDBA", password=PASSWORD, ruta=ruta, charset="ISO8859_1")
    yield srv


@pytest.fixture
def tenant9(db_engine, fb_empresa):
    """Un tenant con iFood ligado a '91:145'. Newrest (9) todavía no existe."""
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        t = Tenant(slug=f"sae9-{suffix}", legal_name="Kelly SA", rfc=f"KEL{suffix.upper()}"[:13],
                   regimen_fiscal_sat="612", domicilio_fiscal_cp="78000", tier="PRINCIPAL",
                   status="ACTIVE")
        db.add(t)
        db.flush()
        cli = Cliente(tenant_id=t.id, codigo="CLI-001", legal_name="IFOOD MEXICO",
                      rfc=RFC_IFOOD, espejo_sae=True)
        db.add(cli)
        db.flush()
        db.add(ClienteExterno(tenant_id=t.id, sistema="SAE", clave="91:145",
                              clave_normalizada="91 145", cliente_id=cli.id,
                              origen="MANUAL", confianza="CONFIRMADA"))
        db.commit()
        tid = t.id
    emp = EmpresaSAE(servidor=fb_empresa, numero="01", codigo="91", tenant_id=str(tid),
                     desde=dt.date(2026, 1, 1))
    yield emp
    with SessionLocal() as db:
        for tabla in ("recibo_pago_facturas", "recibos_pago", "nota_credito_facturas",
                      "notas_credito", "lineas_factura", "facturas", "claves_sae",
                      "cliente_externos", "clientes", "espejo_syncs"):
            db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": tid})
        db.query(Tenant).filter(Tenant.id == tid).delete()
        db.commit()


def _ctx(emp):
    return espejo_sae.contexto_de_sistema(emp.tenant_id)


# ── La lectura, contra Firebird 2.5 de verdad ────────────────────────────────

def test_los_lectores_hablan_firebird_y_entregan_el_formato_del_sae10(tenant9):
    emp = tenant9
    with sae_lectura.en_empresa(emp):
        assert sae_lectura.motor() == "firebird"
        assert sae_lectura.tabla("FACTF", "91") == "FACTF01"     # código → número
        with pytest.raises(ValueError):
            sae_lectura.tabla("FACTF", "02")                     # otra empresa, no
        # Las series tal como las escribe SAE, desde el piso: la del 2025 no cuenta.
        assert espejo_sae.descubrir_series("91", emp.desde) == ["FOR K", "KELLYSLP"]
        cabs = espejo_sae.leer_encabezados("91", "FOR K", desde_fecha=emp.desde)
        assert [c["folio"] for c in cabs] == [10, 11, 12, 13]
        c10 = cabs[0]
        assert c10["serie"] == "FORK"                    # como la guarda el Facturador
        assert c10["cliente_sae"] == "145"               # sin el relleno de SAE
        assert c10["fecha"] == "2026-03-18 09:30:00"
        assert (c10["subtotal"], c10["total"], c10["iva"]) == ("1000.00", "1160.01", "160.00")
        assert c10["uuid"] == "u-10"                     # el del CFDI, no el de SAE Móvil
        assert c10["observaciones"] == "OC 4500123 · Ñuñoa"
        assert cabs[2]["estado"] == "CANCELADA" and cabs[2]["cancelacion_msj"] == "Cancelada por error"
        assert cabs[3]["estado"] == "BORRADOR"           # sin timbre no hay factura
        partidas = espejo_sae.leer_partidas("91", [c10["cve_doc"]])[c10["cve_doc"]]
        assert partidas[0]["descripcion"] == "ESPINACA BAÑADA ÑANDÚ"
        assert partidas[0]["cantidad"] == "10.0000"
        # El pago (500) y la nota de crédito (60): en la CxC los dos son abonos.
        assert espejo_sae.leer_abonos("91", [c10["cve_doc"]]) == {c10["cve_doc"]: 560.0}
        # El documento con el folio pegado se parte contra las series conocidas.
        abonos = espejo_sae.folios_con_abonos("91", dt.date.today(), ["FOR K", "KELLYSLP"])
        assert abonos == {"FORK": [10], "KELLYSLP": [31]}
        assert espejo_sae.folios_en_sae("91", "FOR K", emp.desde) == {10, 11, 12, 13}
        catalogo = {c["clave"]: c for c in sae_lectura.catalogo_inve("91")}
        assert catalogo["ESPINACA"]["descripcion"] == "ESPINACA BAÑADA ÑANDÚ"
        assert catalogo["PAPA"]["activa"] is False
    assert sae_lectura.motor() == "mssql"                # fuera del bloque, el SAE 10


# ── De Firebird al espejo del Facturador ─────────────────────────────────────

def test_la_pasada_del_sae9_deposita_facturas_pagos_y_notas(tenant9):
    from app.core.rbac import tenant_session

    emp = tenant9
    with sae_lectura.en_empresa(emp), tenant_session(emp.tenant_id) as db:
        r = espejo_sae.sincronizar(db, _ctx(emp), "91", ["FOR K", "KELLYSLP"], desde=emp.desde)
    # Newrest (9) no tiene equivalencia todavía: sus facturas se omiten, no se adivinan.
    assert r["nuevas"] == 3, r                          # FOR K 10, 12 y 13 (iFood)
    assert sum("91:9" in e for e in r["errores"]) == 2    # FOR K 11 y KELLYSLP 31 (Newrest)
    with SessionLocal() as db:
        fs = {(f.serie, f.folio): f for f in db.query(Factura).filter(
            Factura.tenant_id == uuid.UUID(emp.tenant_id)).all()}
    assert set(fs) == {("FORK", 10), ("FORK", 12), ("FORK", 13)}
    assert fs[("FORK", 10)].espejo_empresa == "91" and float(fs[("FORK", 10)].saldo_insoluto) == 600.01
    assert fs[("FORK", 12)].estado == "CANCELADA" and fs[("FORK", 13)].estado == "BORRADOR"
    assert ("FORK", 9) not in fs                         # 2025: debajo del piso

    # La cobranza: la primera vez arranca en el piso; el REP de iFood entra, la
    # nota de crédito toma su serie del XML.
    with sae_lectura.en_empresa(emp), tenant_session(emp.tenant_id) as db:
        cb = cobranza_sae.sincronizar(db, _ctx(emp), "91", desde_minimo=emp.desde,
                                      series=["FOR K", "KELLYSLP"])
    assert cb["pagos"]["enviados"] == 1 and cb["notas_credito"]["enviados"] == 1, cb
    with SessionLocal() as db:
        rep = db.query(ReciboPago).filter(ReciboPago.tenant_id == uuid.UUID(emp.tenant_id)).one()
        nc = db.query(NotaCredito).filter(NotaCredito.tenant_id == uuid.UUID(emp.tenant_id)).one()
    assert rep.espejo_empresa == "91" and rep.uuid == "u-rep-5"
    assert (nc.serie, nc.folio) == ("NC", 3)


def test_el_alta_de_clientes_liga_por_rfc_crea_los_que_faltan_y_no_toca_sin_aplicar(tenant9):
    from app.core.rbac import tenant_session

    emp = tenant9
    with sae_lectura.en_empresa(emp):
        filas = clientes_sae.leer_clientes("91", emp.desde)
    # Sólo quienes facturan desde el piso: el 77 (sólo 2025) no aparece.
    assert [f["clave"] for f in filas] == ["145", "9"]
    assert filas[1]["nombre"] == "NEWREST CATERING MÉXICO"

    with tenant_session(emp.tenant_id) as db:
        seco = clientes_sae.alta_clientes(db, _ctx(emp), "91", filas, aplicar=False)
    assert seco["conteo"]["ya_ligado"] == 1 and seco["conteo"]["crear"] == 1
    with SessionLocal() as db:
        assert db.query(Cliente).filter(Cliente.tenant_id == uuid.UUID(emp.tenant_id)).count() == 1

    with tenant_session(emp.tenant_id) as db:
        hecho = clientes_sae.alta_clientes(db, _ctx(emp), "91", filas, aplicar=True)
    assert hecho["aplicado"] and hecho["conteo"]["crear"] == 1
    with SessionLocal() as db:
        nuevo = db.query(Cliente).filter(Cliente.tenant_id == uuid.UUID(emp.tenant_id),
                                         Cliente.rfc == RFC_NEWREST).one()
        assert nuevo.espejo_sae and nuevo.regimen_fiscal == "601"
        assert nuevo.domicilio_fiscal.get("cp") == "11000"
        # con su crédito: sin él toda su cartera saldría vencida
        assert nuevo.dias_credito == 30 and float(nuevo.limite_credito) == 50000
        assert nuevo.domicilio_fiscal.get("correos") == ["cxp@newrest.mx", "facturas@newrest.mx"]
        eq = db.query(ClienteExterno).filter(ClienteExterno.tenant_id == uuid.UUID(emp.tenant_id),
                                             ClienteExterno.clave == "91:9").one()
        assert eq.cliente_id == nuevo.id and eq.confianza == "CONFIRMADA"

    # Con el cliente dado de alta, su factura (y la del folio pegado) ya entran.
    with sae_lectura.en_empresa(emp), tenant_session(emp.tenant_id) as db:
        r = espejo_sae.cuadre(db, _ctx(emp), "91", ["FOR K", "KELLYSLP"], desde=emp.desde)
    assert r["reparadas"] >= 1, r
    with SessionLocal() as db:
        fs = {(f.serie, f.folio): f for f in db.query(Factura).filter(
            Factura.tenant_id == uuid.UUID(emp.tenant_id)).all()}
    assert ("FORK", 11) in fs and ("KELLYSLP", 31) in fs
    # PUE en SAE → PUE en el espejo, sin saldo; y la KELLYSLP 31, pagada, entra
    # por el cuadre CON su saldo (0), no debiendo el total.
    assert fs[("FORK", 11)].metodo_pago == "PUE" and float(fs[("FORK", 11)].saldo_insoluto) == 0
    assert fs[("FORK", 11)].forma_pago == "03"
    assert float(fs[("KELLYSLP", 31)].saldo_insoluto) == 0


def test_el_catalogo_del_sae9_se_guarda_con_su_codigo(tenant9, monkeypatch):
    from app.services import sae_fuentes

    emp = tenant9
    monkeypatch.setattr(sae_fuentes, "empresa_de", lambda t, c: emp if c == "91" else None)
    errores: list = []
    r = espejo_sae.sincronizar_claves(emp.tenant_id, ["91"], errores)
    assert errores == [] and r["91"]["creadas"] == 2, r
    with SessionLocal() as db:
        claves = {c.clave: c for c in db.query(ClaveSae).filter(
            ClaveSae.tenant_id == uuid.UUID(emp.tenant_id)).all()}
    assert claves["ESPINACA"].empresa == "91" and claves["PAPA"].activa is False


def test_el_cuadre_parcial_no_gasta_el_tope_en_clientes_sin_dueno(tenant9):
    """Newrest (FOR K 11) no está ligado: se quita ANTES del tope, así el tope
    de 1 trae una factura de iFood y no se atora en la que no puede entrar. Y
    lo que entra por el cuadre trae su saldo de la CxC."""
    from app.core.rbac import tenant_session

    emp = tenant9
    with sae_lectura.en_empresa(emp), tenant_session(emp.tenant_id) as db:
        r = espejo_sae.cuadre(db, _ctx(emp), "91", ["FOR K"], desde=emp.desde,
                              tope=1, parcial=True)
    info = r["series"]["FORK"]
    assert info["faltan"] == 4 and info["sin_equivalencia"] == 1
    assert info["reparadas"] == 1 and info["pendientes"] == 2
    with SessionLocal() as db:
        f10 = db.query(Factura).filter(Factura.tenant_id == uuid.UUID(emp.tenant_id),
                                       Factura.serie == "FORK", Factura.folio == 10).one()
    assert float(f10.saldo_insoluto) == 600.01          # total menos pago y nota


def test_quien_compra_en_la_91_y_en_la_92_se_liga_en_las_dos(tenant9):
    """iFood ya está ligado a '91:145'. En la 02 es el cliente 33: con la 91 como
    hermana se liga (no se queda en «revisar») y su factura de la 92 entra."""
    from app.core.rbac import tenant_session

    e91 = tenant9
    e92 = EmpresaSAE(servidor=e91.servidor, numero="02", codigo="92",
                     tenant_id=e91.tenant_id, desde=e91.desde)
    with sae_lectura.en_empresa(e92):
        filas = clientes_sae.leer_clientes("92", e92.desde)
    assert [f["clave"] for f in filas] == ["33"]
    with tenant_session(e92.tenant_id) as db:
        sin_hermanas = clientes_sae.alta_clientes(db, _ctx(e92), "92", filas, hermanas=set())
        con_hermanas = clientes_sae.alta_clientes(db, _ctx(e92), "92", filas, aplicar=True,
                                                  hermanas={"91", "92"})
    assert sin_hermanas["conteo"]["revisar"] == 1          # así se atoraba
    assert con_hermanas["conteo"]["ligar"] == 1 and con_hermanas["aplicado"]
    with sae_lectura.en_empresa(e92), tenant_session(e92.tenant_id) as db:
        r = espejo_sae.sincronizar(db, _ctx(e92), "92", ["KELLYQRO"], desde=e92.desde)
    assert r["nuevas"] == 1, r
    with SessionLocal() as db:
        f = db.query(Factura).filter(Factura.tenant_id == uuid.UUID(e92.tenant_id),
                                     Factura.serie == "KELLYQRO", Factura.folio == 40).one()
        assert f.espejo_empresa == "92"
        eqs = {e.clave for e in db.query(ClienteExterno).filter(
            ClienteExterno.tenant_id == uuid.UUID(e92.tenant_id),
            ClienteExterno.cliente_id == f.cliente_id).all()}
    assert eqs == {"91:145", "92:33"}

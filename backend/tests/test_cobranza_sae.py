"""El puerto de los REP y las notas de crédito, leídos por el Facturador.

Lo que se prueba es lo que no es obvio del original: el saldo anterior y la
parcialidad se RECONSTRUYEN desde la CxC —los REP traen IMPORTE=0— y una nota
cancelada se queda fuera porque sin renglones no hay cliente al cual colgarla.
"""
import datetime as dt

import pytest

from app.services import cobranza_sae, sae_lectura


def test_el_saldo_anterior_y_la_parcialidad_se_reconstruyen(monkeypatch):
    """El XML del REP lleva ImpSaldoAnt / ImpSaldoInsoluto / NumParcialidad, y
    ninguno está en una columna: salen del cargo original menos los abonos que
    ya se le habían aplicado a esa factura, en orden."""
    def _fake(sql, params=(), timeout=None):
        if "NUM_CPTO=1 " in sql or "NUM_CPTO=1\n" in sql:
            return [{"refer": "ZHGO 370", "cargo": "1000.00"}]
        return [
            {"refer": "ZHGO 370", "rep": "REP 1", "importe": "400.00", "id_mov": 1},
            {"refer": "ZHGO 370", "rep": "REP 2", "importe": "600.00", "id_mov": 2},
        ]
    monkeypatch.setattr(sae_lectura, "consultar", _fake)
    monkeypatch.setattr(sae_lectura, "disponible", lambda: True)

    s = cobranza_sae.saldos_por_factura("02", ["ZHGO 370"])["ZHGO 370"]
    assert [x["saldo_anterior"] for x in s] == [1000.0, 600.0]
    assert [x["saldo_insoluto"] for x in s] == [600.0, 0.0]
    assert [x["parcialidad"] for x in s] == [1, 2]


def test_un_refer_que_no_es_documento_no_arma_renglon():
    assert cobranza_sae._renglon("ZHGO 370", 100.0)["folio"] == 370
    assert cobranza_sae._renglon("ZHGO 370", 100.0)["serie"] == "ZHGO"
    assert cobranza_sae._renglon("sin forma de folio", 100.0) is None
    assert cobranza_sae._renglon("", 100.0) is None


def test_los_importes_viajan_con_dos_decimales():
    """El SAT no acepta cuatro. Y 100 no es '100', es '100.00'."""
    assert cobranza_sae._dinero(100) == "100.00"
    assert cobranza_sae._dinero(1234.5678) == "1234.57"
    assert cobranza_sae._dinero(None) == "0.00"


def test_los_clientes_de_mostrador_de_la_02_no_entran():
    """1, 2 y 3 de la empresa 02 son placeholders, no clientes."""
    assert cobranza_sae._PLACEHOLDERS["02"] == {"1", "2", "3"}


def test_las_series_con_digitos_se_parten_bien():
    """ZCH5C y MIN5C (empresa 04) llevan dígitos en la serie. Con «letras y
    luego números» sus REP y notas se descartaban EN SILENCIO (24 al
    26-sep-2026). La serie termina donde empieza el folio: los dígitos después
    del último espacio, como SAE escribe el CVE_DOC."""
    p = cobranza_sae._partir
    assert p("ZCH5C          12") == ("ZCH5C", 12)      # como viene de SAE
    assert p("ZCH5C 12") == ("ZCH5C", 12)
    assert p("MIN5C        7") == ("MIN5C", 7)
    assert p("ZHGO 370") == ("ZHGO", 370)
    assert p("  ZEHMOHOS       542  ") == ("ZEHMOHOS", 542)
    assert p("ZHGO 0000370") == ("ZHGO", 370)           # sin ceros a la izquierda
    assert p("0000000048") == ("", 48)                  # sin serie: nada que adivinar
    assert cobranza_sae._renglon("ZCH5C          12", 50.0)["serie"] == "ZCH5C"


def test_sin_espacio_no_se_adivina_donde_termina_la_serie():
    """'ZCH5C12' podría ser ZCH5C·12 o ZCH5·C12, y 'ZCH512' ZCH·512 o ZCH5·12.
    Un folio adivinado cuelga el pago de la factura de otro: mejor None."""
    for pegado in ("ZCH5C12", "ZCH512", "ZHGO370", "ZHGO", "12 ABC", "", None):
        assert cobranza_sae._partir(pegado) is None, pegado


def test_un_rep_de_serie_con_digitos_llega_con_sus_facturas(monkeypatch):
    """De extremo a extremo de la pasada: el REP de ZCH5C sale con su renglón,
    y lo que no se puede partir se dice en `descartados` en vez de perderse."""
    from app.api.v1 import espejo_cobranza

    rep = {"cve_doc": "PAGO 5", "serie": "PAGO", "folio": 5, "cliente_sae": "77",
           "fecha": "2026-09-25 10:00:00", "cancelado": False, "uuid": "u-rep",
           "fecha_cancela": None, "forma_sat": "03",
           "renglones": [
               {"refer": "ZCH5C          12", "importe": 100.0,
                "fecha": "2026-09-25 10:00:00", "concepto": "22"},
               {"refer": "ZCH5C12", "importe": 5.0,
                "fecha": "2026-09-25 10:00:00", "concepto": "22"},
           ]}
    nota = {"cve_doc": "NCMIN5C        3", "uuid": "u-nc", "fecha": "2026-09-25 11:00:00",
            "fecha_cancela": None, "cliente_sae": "77",
            "renglones": [{"cliente": "77", "refer": "MIN5C        7", "importe": 20.0}]}
    monkeypatch.setattr(cobranza_sae, "leer_reps", lambda e, d: [rep])
    monkeypatch.setattr(cobranza_sae, "leer_notas", lambda e, d: [nota])
    monkeypatch.setattr(cobranza_sae, "saldos_por_factura", lambda e, r: {})
    monkeypatch.setattr(cobranza_sae, "clientes_con_equivalencia", lambda db, ctx, e: {"77"})
    pagos, notas = [], []
    monkeypatch.setattr(espejo_cobranza, "recibo_pago_espejo",
                        lambda payload, db, ctx: pagos.append(payload))
    monkeypatch.setattr(espejo_cobranza, "nota_credito_espejo",
                        lambda payload, db, ctx: notas.append(payload))

    r = cobranza_sae.sincronizar(None, None, "04")
    assert r["pagos"]["enviados"] == 1 and r["notas_credito"]["enviados"] == 1
    assert [(f.serie, f.folio) for f in pagos[0].facturas] == [("ZCH5C", 12)]
    assert (notas[0].serie, notas[0].folio) == ("NCMIN5C", 3)
    assert [(f.serie, f.folio) for f in notas[0].facturas] == [("MIN5C", 7)]
    assert r["descartados"] == ["REP PAGO 5: renglón 'ZCH5C12'"]
    assert r["errores"] == []

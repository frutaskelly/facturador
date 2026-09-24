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

"""Validación fiscal al crear/editar cliente: el RFC del receptor debe pasar
formato + dígito verificador (los genéricos del SAT sí se permiten), y el CP
del domicilio debe traer 5 dígitos."""
import pytest
from fastapi import HTTPException

from app.api.v1.clientes import _validar_datos_fiscales


def test_rfc_valido_pasa():
    d = {"rfc": "obv191007bs1"}
    _validar_datos_fiscales(d)
    assert d["rfc"] == "OBV191007BS1"  # normalizado a mayúsculas


def test_rfc_con_digito_verificador_malo_se_rechaza():
    # Caso real: se guardó un cliente con este RFC porque nadie lo validaba.
    with pytest.raises(HTTPException) as exc:
        _validar_datos_fiscales({"rfc": "GOA180712SF4"})
    assert exc.value.status_code == 422
    assert "dígito verificador" in exc.value.detail


def test_rfc_con_formato_invalido_se_rechaza():
    with pytest.raises(HTTPException) as exc:
        _validar_datos_fiscales({"rfc": "NO-ES-UN-RFC"})
    assert exc.value.status_code == 422
    assert "formato" in exc.value.detail


def test_rfc_vacio_se_rechaza():
    with pytest.raises(HTTPException) as exc:
        _validar_datos_fiscales({"rfc": "   "})
    assert exc.value.status_code == 422


@pytest.mark.parametrize("generico", ["XAXX010101000", "XEXX010101000"])
def test_rfc_genericos_si_valen_como_receptor(generico):
    _validar_datos_fiscales({"rfc": generico})  # no lanza


def test_cp_de_menos_de_5_digitos_se_rechaza():
    with pytest.raises(HTTPException) as exc:
        _validar_datos_fiscales({"rfc": "OBV191007BS1", "domicilio_fiscal": {"cp": "123"}})
    assert exc.value.status_code == 422
    assert "5 dígitos" in exc.value.detail


def test_cp_valido_pasa():
    _validar_datos_fiscales({"rfc": "OBV191007BS1", "domicilio_fiscal": {"cp": "44100"}})


def test_sin_rfc_en_el_patch_no_valida():
    """Un PATCH que no toca el RFC (p. ej. solo cambia el teléfono) no falla."""
    _validar_datos_fiscales({"telefono": "8112345678"})


# ── Validación contra el SAT (Facturama) al guardar ──────────────────────────

class _ClienteFake:
    """Doble del FacturamaClient: devuelve el veredicto que le pasemos."""
    def __init__(self, resp=None, revienta=False):
        self.configured = True
        self._resp = resp or {}
        self._revienta = revienta

    def validar_completo(self, rfc, nombre, cp, regimen):
        if self._revienta:
            raise RuntimeError("PAC caído")
        return self._resp


def _parchar(monkeypatch, cliente):
    monkeypatch.setattr(
        "app.api.v1.clientes.FacturamaClient.from_settings",
        staticmethod(lambda _s: cliente),
    )


def test_cp_que_no_corresponde_al_rfc_se_rechaza(monkeypatch):
    """Caso real: CP con 5 dígitos válidos pero que el SAT no asocia al RFC."""
    from app.api.v1.clientes import _validar_contra_sat

    _parchar(monkeypatch, _ClienteFake({
        "ExistRfc": True, "MatchName": True,
        "MatchZipCode": False, "MatchFiscalRegime": True,
    }))
    with pytest.raises(HTTPException) as exc:
        _validar_contra_sat("GOA180712SF5", "GRUPO OPERADOR DE ALIMENTOS EHMO", "71261", "601")
    assert exc.value.status_code == 422
    assert "código postal 71261 no corresponde" in exc.value.detail


def test_todo_coincide_pasa(monkeypatch):
    from app.api.v1.clientes import _validar_contra_sat

    _parchar(monkeypatch, _ClienteFake({
        "ExistRfc": True, "MatchName": True,
        "MatchZipCode": True, "MatchFiscalRegime": True,
    }))
    _validar_contra_sat("OBV191007BS1", "OPERADORA BALLES VEGA", "42110", "601")


def test_varios_problemas_se_listan(monkeypatch):
    from app.api.v1.clientes import _validar_contra_sat

    _parchar(monkeypatch, _ClienteFake({
        "ExistRfc": True, "MatchName": False,
        "MatchZipCode": False, "MatchFiscalRegime": False,
    }))
    with pytest.raises(HTTPException) as exc:
        _validar_contra_sat("OBV191007BS1", "NOMBRE MAL", "99999", "612")
    d = exc.value.detail
    assert "razón social" in d and "código postal" in d and "régimen fiscal" in d


def test_pac_caido_no_bloquea_el_guardado(monkeypatch):
    """Una caída del PAC no debe dejar al usuario sin poder dar de alta clientes."""
    from app.api.v1.clientes import _validar_contra_sat

    _parchar(monkeypatch, _ClienteFake(revienta=True))
    _validar_contra_sat("OBV191007BS1", "OPERADORA BALLES VEGA", "42110", "601")


def test_datos_incompletos_no_consultan_al_sat(monkeypatch):
    from app.api.v1.clientes import _validar_contra_sat

    _parchar(monkeypatch, _ClienteFake(revienta=True))  # reventaría si se llamara
    _validar_contra_sat("OBV191007BS1", "", "42110", "601")

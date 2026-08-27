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

"""Validación local del CSD: cada campo del resultado con certificados y
llaves generados al vuelo (mismo formato que el SAT: X.509 DER + PKCS#8
encriptado DER, RFC en x500UniqueIdentifier)."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services.csd_validador import validar_csd

RFC = "EPR990101AB1"
PASSWORD = "12345678a"


def _hacer_csd(rfc=RFC, dias=365, desde_dias=-1, key_usage_fiel=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA PRUEBA"),
        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.45"), f"{rfc} / CURP010101HDFRRN09"),
    ])
    ahora = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora + timedelta(days=desde_dias))
        .not_valid_after(ahora + timedelta(days=dias))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=True,
                key_encipherment=False, data_encipherment=key_usage_fiel,
                key_agreement=key_usage_fiel, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    )
    cert = builder.sign(key, hashes.SHA256())
    cer_der = cert.public_bytes(serialization.Encoding.DER)
    key_der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(PASSWORD.encode()),
    )
    return cer_der, key_der, key


def test_csd_valido_todo_verde():
    cer, key, _ = _hacer_csd()
    r = validar_csd(cer, key, PASSWORD, RFC)
    assert r["cer_ok"] and r["key_ok"] and r["password_ok"] and r["par_ok"]
    assert r["rfc_cert"] == RFC and r["rfc_coincide"] is True
    assert r["vigente"] is True and r["es_fiel"] is False
    assert r["valido"] is True


def test_password_incorrecta_distingue_de_archivo_basura():
    cer, key, _ = _hacer_csd()
    r = validar_csd(cer, key, "otra-password", RFC)
    assert r["key_ok"] is True          # SÍ es una llave encriptada
    assert r["password_ok"] is False    # pero la contraseña no la abre
    assert "contraseña" in r["password_detalle"].lower()
    assert r["valido"] is False


def test_cer_basura():
    _, key, _ = _hacer_csd()
    r = validar_csd(b"esto no es un certificado", key, PASSWORD, RFC)
    assert r["cer_ok"] is False and "certificado" in r["cer_detalle"]
    assert r["valido"] is False


def test_key_basura():
    cer, _, _ = _hacer_csd()
    r = validar_csd(cer, b"esto no es una llave", PASSWORD, RFC)
    assert r["key_ok"] is False and r["password_ok"] is False
    assert r["valido"] is False


def test_llave_de_otro_certificado():
    cer, _, _ = _hacer_csd()
    _, otra_key, _ = _hacer_csd()  # par distinto
    r = validar_csd(cer, otra_key, PASSWORD, RFC)
    assert r["cer_ok"] and r["key_ok"] and r["password_ok"]
    assert r["par_ok"] is False and "corresponde" in r["par_detalle"]
    assert r["valido"] is False


def test_rfc_distinto():
    cer, key, _ = _hacer_csd(rfc="XAXX010101000")
    r = validar_csd(cer, key, PASSWORD, RFC)
    assert r["rfc_cert"] == "XAXX010101000"
    assert r["rfc_coincide"] is False
    assert RFC in r["cer_detalle"]
    assert r["valido"] is False


def test_certificado_vencido():
    cer, key, _ = _hacer_csd(dias=-1, desde_dias=-400)
    r = validar_csd(cer, key, PASSWORD, RFC)
    assert r["vigente"] is False and "venció" in r["cer_detalle"]
    assert r["valido"] is False


def test_fiel_detectada():
    cer, key, _ = _hacer_csd(key_usage_fiel=True)
    r = validar_csd(cer, key, PASSWORD, RFC)
    assert r["es_fiel"] is True
    assert "FIEL" in r["cer_detalle"]
    assert r["valido"] is False

"""Validación LOCAL de un CSD del SAT (.cer + .key + contraseña), sin llamar
al PAC. Da retroalimentación por campo para el onboarding:

  - cer_ok        el .cer es un certificado X.509 real (DER o PEM)
  - key_ok        el .key es una llave privada encriptada del SAT
  - password_ok   la contraseña abre la llave
  - par_ok        la llave corresponde al certificado (misma clave pública)
  - rfc_cert      RFC dentro del certificado; rfc_coincide vs el del emisor
  - vigente       el certificado está dentro de su periodo de vigencia
  - es_fiel       heurística: una e.firma (FIEL) NO sirve para timbrar

Todo es best-effort defensivo: un archivo basura nunca debe tirar el request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    load_der_private_key,
    load_pem_private_key,
)

# OID x500UniqueIdentifier: ahí pone el SAT el "RFC / CURP" del sujeto.
_OID_RFC = x509.ObjectIdentifier("2.5.4.45")

# OIDs de esquemas de cifrado PKCS#8/PKCS#12 usados por las llaves del SAT.
# Si el DER los contiene, el archivo ES una llave encriptada (aunque la
# contraseña dada no la abra) — permite distinguir "archivo equivocado" de
# "contraseña incorrecta".
_OIDS_LLAVE_ENCRIPTADA = (
    bytes.fromhex("06092a864886f70d01050d"),  # PBES2
    bytes.fromhex("06092a864886f70d010c0103"),  # PBE-SHA1-3DES (llaves viejas)
    bytes.fromhex("06092a864886f70d010c0106"),  # PBE-SHA1-RC2-40
)


def _cargar_cert(cer_data: bytes) -> Optional[x509.Certificate]:
    for loader in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            return loader(cer_data)
        except Exception:  # noqa: BLE001
            continue
    return None


def _rfc_del_cert(cert: x509.Certificate) -> Optional[str]:
    try:
        attrs = cert.subject.get_attributes_for_oid(_OID_RFC)
        if not attrs:
            return None
        # El SAT a veces manda "RFC / CURP"; el RFC es el primer token.
        valor = str(attrs[0].value).strip()
        return valor.split("/")[0].strip().upper() or None
    except Exception:  # noqa: BLE001
        return None


def _parece_llave_encriptada(key_data: bytes) -> bool:
    cabeza = key_data[:128]
    return any(oid in cabeza for oid in _OIDS_LLAVE_ENCRIPTADA) or (
        b"ENCRYPTED PRIVATE KEY" in key_data[:64]
    )


def _es_fiel(cert: x509.Certificate) -> Optional[bool]:
    """Heurística SAT: la e.firma (FIEL) trae key_agreement/data_encipherment;
    el CSD solo firma. None si el cert no trae la extensión."""
    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        extra = bool(ku.data_encipherment)
        try:
            extra = extra or bool(ku.key_agreement)
        except Exception:  # noqa: BLE001 — key_agreement puede no aplicar
            pass
        return extra
    except Exception:  # noqa: BLE001
        return None


def validar_csd(cer_data: bytes, key_data: bytes, password: str, rfc_esperado: str) -> dict:
    r = {
        "cer_ok": False, "cer_detalle": "",
        "key_ok": False, "key_detalle": "",
        "password_ok": False, "password_detalle": "",
        "par_ok": False, "par_detalle": "",
        "rfc_cert": None, "rfc_coincide": None,
        "vigente": None, "vigencia_fin": None,
        "es_fiel": None,
        "valido": False,
    }

    # ── Certificado ───────────────────────────────────────────────────────────
    cert = _cargar_cert(cer_data)
    if cert is None:
        r["cer_detalle"] = "El archivo no es un certificado .cer del SAT"
    else:
        r["cer_ok"] = True
        r["rfc_cert"] = _rfc_del_cert(cert)
        if r["rfc_cert"] and rfc_esperado:
            r["rfc_coincide"] = r["rfc_cert"] == rfc_esperado.strip().upper()
        ahora = datetime.now(timezone.utc)
        try:
            inicio = cert.not_valid_before_utc
            fin = cert.not_valid_after_utc
        except AttributeError:  # cryptography < 42
            inicio = cert.not_valid_before.replace(tzinfo=timezone.utc)
            fin = cert.not_valid_after.replace(tzinfo=timezone.utc)
        r["vigente"] = inicio <= ahora <= fin
        r["vigencia_fin"] = fin.date().isoformat()
        r["es_fiel"] = _es_fiel(cert)
        if not r["vigente"]:
            r["cer_detalle"] = f"El certificado no está vigente (venció el {r['vigencia_fin']})" if ahora > fin else "El certificado aún no entra en vigencia"
        elif r["rfc_coincide"] is False:
            r["cer_detalle"] = f"El certificado es del RFC {r['rfc_cert']}, no de {rfc_esperado.strip().upper()}"
        elif r["es_fiel"]:
            r["cer_detalle"] = "Parece una e.firma (FIEL), no un CSD — el SAT no permite timbrar con la FIEL"

    # ── Llave + contraseña ────────────────────────────────────────────────────
    llave = None
    for loader in (load_der_private_key, load_pem_private_key):
        try:
            llave = loader(key_data, password=password.encode() or None)
            break
        except (ValueError, TypeError):
            continue
        except Exception:  # noqa: BLE001
            continue
    if llave is not None:
        r["key_ok"] = True
        r["password_ok"] = True
    elif _parece_llave_encriptada(key_data):
        r["key_ok"] = True
        r["password_detalle"] = "La contraseña no abre esta llave (verifica mayúsculas y el archivo .key)"
    else:
        r["key_detalle"] = "El archivo no parece una llave .key del SAT"

    # ── Correspondencia llave ↔ certificado ───────────────────────────────────
    if llave is not None and cert is not None:
        try:
            pub_llave = llave.public_key().public_numbers()
            pub_cert = cert.public_key().public_numbers()
            r["par_ok"] = pub_llave == pub_cert
            if not r["par_ok"]:
                r["par_detalle"] = "La llave .key NO corresponde a este certificado .cer (son de pares distintos)"
        except Exception:  # noqa: BLE001
            r["par_detalle"] = "No se pudo comparar la llave con el certificado"

    r["valido"] = bool(
        r["cer_ok"] and r["key_ok"] and r["password_ok"] and r["par_ok"]
        and r["vigente"] and r["rfc_coincide"] is not False and not r["es_fiel"]
    )
    return r

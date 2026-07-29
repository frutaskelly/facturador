"""Estado de onboarding fiscal de un tenant (¿listo para timbrar?).

Calcula, de forma tolerante, qué pasos de la configuración fiscal del emisor están
completos: datos fiscales capturados, RFC con formato válido y CSD cargado en
Facturama bajo el RFC del tenant. Lo consume el wizard de onboarding
(GET /empresa/onboarding) y el gate de timbrado (facturas).

No consume folios de Facturama: el CSD se detecta con GET /api-lite/csds (listado),
no con la validación de RFC ante el SAT (que sí cobra folio y es un botón manual).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .facturama import csd_public_fields

# RFC: 3-4 letras (3 PM, 4 PF) + 6 dígitos de fecha + 3 de homoclave.
_RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


def rfc_valido(rfc: Optional[str]) -> bool:
    return bool(rfc) and bool(_RFC_RE.match(rfc.strip().upper()))


def _csd_match(csds: list, rfc: str) -> Optional[dict]:
    """Primer CSD cuyo RFC coincide con el del tenant (campo Rfc/rfc)."""
    rfc_u = (rfc or "").strip().upper()
    for c in csds or []:
        if not isinstance(c, dict):
            continue
        if str(c.get("Rfc") or c.get("rfc") or "").strip().upper() == rfc_u and rfc_u:
            return c
    return None


def compute_status(client, tenant, *, multiemisor: bool) -> dict[str, Any]:
    """Estado de onboarding del tenant.

    `client` es un FacturamaClient (o None). Si no está configurado o falla el
    listado, `csd_cargado` queda en False sin reventar.
    """
    legal_name = (tenant.legal_name or "").strip()
    rfc = (tenant.rfc or "").strip().upper()
    regimen = (tenant.regimen_fiscal_sat or "").strip()
    cp = (tenant.domicilio_fiscal_cp or "").strip()

    datos_completos = bool(legal_name) and rfc_valido(rfc) and bool(regimen) and len(cp) == 5

    csd_obj: Optional[dict] = None
    if rfc and client is not None and getattr(client, "configured", False):
        try:
            csd_obj = _csd_match(client.listar_csds(), rfc)
        except Exception:  # noqa: BLE001 — listado tolerante, no debe romper el status
            csd_obj = None
    csd_cargado = csd_obj is not None

    # En single-emisor (multiemisor=false) el CSD lo aporta la cuenta/env, así que
    # no se exige por-tenant para considerar "listo".
    listo = datos_completos and (csd_cargado or not multiemisor)

    pasos = [
        {
            "id": "datos_fiscales",
            "titulo": "Datos fiscales",
            "completo": datos_completos,
            "detalle": "Razón social, RFC, régimen y código postal del emisor.",
        },
        {
            "id": "rfc",
            "titulo": "RFC válido",
            "completo": rfc_valido(rfc),
            "detalle": "RFC con formato correcto del SAT.",
        },
        {
            "id": "csd",
            "titulo": "Sello digital (CSD)",
            "completo": csd_cargado,
            "detalle": "Certificado .cer + llave .key subidos a Facturama.",
        },
    ]

    # El CSD crudo de Facturama incluye Certificate/PrivateKey/PrivateKeyPassword;
    # al navegador solo van los campos públicos (RFC, serie, vigencia).
    csd_publico: Optional[dict] = None
    if csd_obj is not None:
        publicos = csd_public_fields([csd_obj])
        csd_publico = publicos[0] if publicos else None

    return {
        "datos_fiscales_completos": datos_completos,
        "rfc": rfc,
        "csd_cargado": csd_cargado,
        "csd": csd_publico,
        "multiemisor": multiemisor,
        "listo_para_facturar": listo,
        "pasos": pasos,
    }

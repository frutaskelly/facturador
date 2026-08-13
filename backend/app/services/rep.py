"""REP — arma el payload del Complemento de Pago 2.0 (CFDI tipo P) para Facturama.

Contrato Facturama (POST /2/cfdis): CfdiType "P", NameId "14", Receiver con
CfdiUse "P01" (sin conceptos ni forma/método a nivel raíz). Complemento.Payments[]
con Date/PaymentForm/Amount/Currency, y por cada factura RelatedDocuments[] con
TaxObject/Uuid/Serie/Folio/Currency/PaymentMethod/PartialityNumber/
PreviousBalanceAmount/AmountPaid/ImpSaldoInsoluto.
Docs: https://apisandbox.facturama.mx/guias/cfdi40/complementos/complemento-pago-20
"""
from __future__ import annotations

from datetime import timezone, timedelta
from decimal import Decimal


_MX_TZ = timezone(timedelta(hours=-6))   # CFDI: hora local del lugar de expedición


def _f(v) -> float:
    return float(Decimal(str(v)))


def build_payload_rep(recibo, cliente, tenant, docs, settings) -> dict:
    """`docs` = lista de (ReciboPagoFactura, Factura). Payload del REP."""
    expedition = (tenant.domicilio_fiscal_cp or "").strip()
    related = []
    for rf, factura in docs:
        dr = {
            "TaxObject": "01",   # DR sin desglose de impuestos en el REP (SAT lo permite: 01)
            "Uuid": factura.uuid,
            "Currency": rf.moneda_dr or "MXN",
            "PaymentMethod": "PPD",
            "PartialityNumber": int(rf.num_parcialidad),
            "PreviousBalanceAmount": _f(rf.saldo_anterior),
            "AmountPaid": _f(rf.importe_pagado),
            "ImpSaldoInsoluto": _f(rf.saldo_insoluto),
        }
        # Serie/Folio son opcionales (solo el UUID es obligatorio); omitir vacíos
        # evita el error de patrón del PAC con serie/folio en blanco.
        if (factura.serie or "").strip():
            dr["Serie"] = str(factura.serie).strip()
        if str(factura.folio or "").strip():
            dr["Folio"] = str(factura.folio).strip()
        related.append(dr)

    fecha_pago = recibo.fecha_pago
    payload = {
        "NameId": "14",              # CFDI de pago
        "CfdiType": "P",
        "ExpeditionPlace": expedition,
        "Receiver": {
            "Rfc": cliente.rfc,
            "Name": cliente.legal_name,
            "CfdiUse": "CP01",       # CFDI 4.0: uso "Pagos" (el P01 era de 3.3)
            "FiscalRegime": cliente.regimen_fiscal or "616",
            "TaxZipCode": (cliente.domicilio_fiscal or {}).get("cp") or expedition,
        },
        "Complemento": {
            "Payments": [{
                "Date": fecha_pago.astimezone(_MX_TZ).strftime("%Y-%m-%dT%H:%M:%S"),
                "PaymentForm": recibo.forma_pago or "03",
                "Amount": _f(recibo.monto),
                "Currency": recibo.moneda or "MXN",
                "RelatedDocuments": related,
            }],
        },
    }
    if getattr(settings, "FACTURAMA_SEND_SERIE", False) and recibo.serie:
        payload["Serie"] = recibo.serie
        payload["Folio"] = recibo.folio

    # Ancla propia para reconciliar un timbrado del REP que murió a media llamada
    # (mismo patrón que las facturas): OrderNumber = serie+folio del recibo. Único
    # por recibo; buscar_cfdi lo confirma en el detalle tras acotar por receptor.
    if recibo.serie and str(recibo.folio or "").strip():
        payload["OrderNumber"] = f"{recibo.serie}{recibo.folio}"

    # Emisor: mismo criterio que las facturas (override global / multiemisor / default).
    if settings.FACTURAMA_ISSUER_RFC:
        payload["Issuer"] = {
            "Rfc": settings.FACTURAMA_ISSUER_RFC,
            "Name": settings.FACTURAMA_ISSUER_NAME or tenant.legal_name,
            "FiscalRegime": settings.FACTURAMA_ISSUER_REGIMEN or tenant.regimen_fiscal_sat,
        }
    elif getattr(settings, "FACTURAMA_MULTIEMISOR", False):
        payload["Issuer"] = {
            "Rfc": tenant.rfc,
            "Name": tenant.legal_name,
            "FiscalRegime": tenant.regimen_fiscal_sat,
        }
    return payload

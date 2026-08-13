"""PDF del Recibo Electrónico de Pago (REP) — representación impresa simple."""
from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from .factura_pdf import _domicilio, _esc, _ESTILO, _ESTILO_TIT, _logo_flowable, _money, _p

_FORMA = {"01": "Efectivo", "02": "Cheque nominativo", "03": "Transferencia",
          "04": "Tarjeta de crédito", "28": "Tarjeta de débito", "99": "Por definir"}


def build_recibo_pdf(recibo, tenant, cliente, docs) -> bytes:
    """`docs` = lista de (ReciboPagoFactura, Factura). PDF del complemento de pago."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"Recibo de pago {recibo.serie}{recibo.folio}",
    )
    story: list = []

    emisor_dom = _domicilio(tenant.domicilio_fiscal or {}, tenant.domicilio_fiscal_cp or "")
    emisor = [
        _p(_esc(tenant.legal_name), _ESTILO_TIT),
        _p(f"RFC: {tenant.rfc or ''}"),
        _p(_esc(emisor_dom)),
    ]
    logo = _logo_flowable(tenant)
    header = Table([[emisor, logo or ""]], colWidths=[doc.width - 150, 150])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story.append(header)
    story.append(Spacer(1, 6))

    timbrado = bool(recibo.uuid)
    estado_txt = "" if timbrado else "  ·  SIN TIMBRAR"
    banda = Table(
        [[_p(f"<b>RECIBO DE PAGO (REP) {recibo.serie}{recibo.folio}</b>{estado_txt}", _ESTILO_TIT)]],
        colWidths=[doc.width],
    )
    banda.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef2ff")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#c7d2fe")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(banda)
    story.append(Spacer(1, 6))

    receptor = [
        _p("<b>Receptor</b>", _ESTILO_TIT),
        _p(_esc(cliente.legal_name)),
        _p(f"RFC: {cliente.rfc or ''}"),
    ]
    fecha = recibo.fecha_pago.strftime("%d/%m/%Y") if recibo.fecha_pago else ""
    pago = [
        _p("<b>Datos del pago</b>", _ESTILO_TIT),
        _p(f"Fecha: {fecha}"),
        _p(f"Forma de pago: {_FORMA.get(recibo.forma_pago, recibo.forma_pago)}"),
        _p(f"Monto: {_money(recibo.monto)} {recibo.moneda}"),
    ]
    if recibo.num_operacion:
        pago.append(_p(f"Referencia: {_esc(recibo.num_operacion)}"))
    rec = Table([[receptor, pago]], colWidths=[doc.width * 0.5, doc.width * 0.5])
    rec.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(rec)
    story.append(Spacer(1, 8))

    # Documentos relacionados.
    filas = [[_p("<b>Factura</b>", _ESTILO_TIT), _p("<b>Parc.</b>", _ESTILO_TIT),
              _p("<b>Saldo ant.</b>", _ESTILO_TIT), _p("<b>Pagado</b>", _ESTILO_TIT),
              _p("<b>Saldo insoluto</b>", _ESTILO_TIT)]]
    for rf, factura in docs:
        filas.append([
            _p(f"{factura.serie}{factura.folio}"),
            _p(str(rf.num_parcialidad)),
            _p(_money(rf.saldo_anterior)),
            _p(_money(rf.importe_pagado)),
            _p(_money(rf.saldo_insoluto)),
        ])
    tabla = Table(filas, colWidths=[doc.width * 0.28, doc.width * 0.12,
                                    doc.width * 0.2, doc.width * 0.2, doc.width * 0.2])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#94a3b8")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ]))
    story.append(tabla)
    story.append(Spacer(1, 10))

    if recibo.uuid:
        story.append(_p(f"UUID (Folio Fiscal): <font face='Courier'>{recibo.uuid}</font>", _ESTILO))
    story.append(Spacer(1, 4))
    story.append(_p(
        "Complemento para Recepción de Pagos 2.0 (CFDI tipo P). El detalle fiscal completo está en el XML.",
        _ESTILO,
    ))
    doc.build(story)
    return buf.getvalue()

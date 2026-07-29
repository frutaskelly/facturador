"""Representación impresa (PDF) de una remisión.

Reusa el MISMO diseño que la factura (factura_pdf) — encabezado del emisor,
tabla de conceptos y totales — pero marcada de forma clara y notoria como
REMISIÓN y DOCUMENTO NO FISCAL (banda ámbar, sin bloque fiscal ni QR).
"""
from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

from .factura_pdf import (
    _ESTILO,
    _ESTILO_NOTE,
    _ESTILO_TH,
    _ESTILO_TIT,
    _domicilio,
    _esc,
    _logo_flowable,
    _money,
    _p,
)


def _remision_story(doc, rem, tenant, cliente, nombres: dict) -> list:
    """Flowables de UNA remisión (para armar un PDF individual o un lote)."""
    folio = rem.folio_interno or ""
    story: list = []

    # ── Encabezado: emisor (izq) + logo (der) — igual que la factura ──
    emisor_dom = _domicilio(tenant.domicilio_fiscal or {}, tenant.domicilio_fiscal_cp or "")
    emisor_cell = [
        _p(_esc(tenant.legal_name), _ESTILO_TIT),
        _p(f"RFC: {tenant.rfc or ''}"),
        _p(_esc(emisor_dom)),
    ]
    logo = _logo_flowable(tenant)
    header = Table([[emisor_cell, logo or ""]], colWidths=[doc.width - 150, 150])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 6))

    # ── Banda: REMISIÓN + marca NO FISCAL (ámbar, notoria) ──
    banda = Table(
        [[_p(f"<b>REMISIÓN {folio}</b>", _ESTILO_TIT),
          _p(f"Fecha: {rem.fecha_remision or ''}  ·  <b>DOCUMENTO NO FISCAL</b> — no es un CFDI")]],
        colWidths=[doc.width * 0.32, doc.width * 0.68],
    )
    banda.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf3e0")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#e0a12b")),
        ("TEXTCOLOR", (0, 0), (0, 0), colors.HexColor("#9a6608")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(banda)
    story.append(Spacer(1, 8))

    # ── Cliente ──
    cliente_cell = [
        _p("<b>Cliente</b>", _ESTILO_TIT),
        _p(_esc(cliente.legal_name) if cliente else ""),
        _p(f"RFC: {cliente.rfc if cliente else ''}"),
    ]
    rec = Table([[cliente_cell]], colWidths=[doc.width])
    rec.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(rec)
    story.append(Spacer(1, 8))

    # ── Notas (arriba de los conceptos) — igual que la factura ──
    if getattr(rem, "notas", None):
        notas_tbl = Table(
            [[_p("Notas", _ESTILO_TH)], [_p(_esc(rem.notas), _ESTILO_NOTE)]],
            colWidths=[doc.width],
        )
        notas_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(notas_tbl)
        story.append(Spacer(1, 8))

    # ── Conceptos (mismo estilo que la factura; sin Clave SAT porque no es fiscal) ──
    head = ["Cant.", "Unidad", "Descripción", "P. Unitario", "Importe"]
    data = [[_p(h, _ESTILO_TH) for h in head]]
    for ln in sorted(rem.lineas, key=lambda x: x.numero_linea):
        data.append([
            _p(f"{Decimal(ln.cantidad_solicitada):g}"),
            _p(ln.presentacion or ""),
            _p(_esc(nombres.get(ln.producto_id, str(ln.producto_id)))),
            _p(_money(ln.precio_unitario)),
            _p(_money(ln.importe)),
        ])
    tabla = Table(data, colWidths=[
        doc.width * 0.10, doc.width * 0.14, doc.width * 0.50,
        doc.width * 0.13, doc.width * 0.13,
    ], repeatRows=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#94a3b8")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "RIGHT"),
        ("ALIGN", (3, 1), (4, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tabla)
    story.append(Spacer(1, 6))

    # ── Totales ──
    tot_rows = [["Subtotal", _money(rem.subtotal)]]
    if rem.descuento and Decimal(rem.descuento) > 0:
        tot_rows.append(["Descuento", _money(rem.descuento)])
    if rem.iva and Decimal(rem.iva) > 0:
        tot_rows.append(["IVA", _money(rem.iva)])
    if rem.ieps and Decimal(rem.ieps) > 0:
        tot_rows.append(["IEPS", _money(rem.ieps)])
    tot_rows.append(["TOTAL", _money(rem.total)])
    tot = Table([[_p(k), _p(f"<b>{v}</b>" if k == "TOTAL" else v)] for k, v in tot_rows],
                colWidths=[70, 90], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#94a3b8")),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(tot)
    story.append(Spacer(1, 10))
    story.append(_p("Este documento es una remisión (nota de entrega); NO es un comprobante fiscal (CFDI).", _ESTILO))
    return story


def _doc(buf, title):
    return SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=title,
    )


def build_remision_pdf(rem, tenant, cliente, nombres: dict) -> bytes:
    buf = io.BytesIO()
    doc = _doc(buf, f"Remisión {rem.folio_interno or ''}")
    doc.build(_remision_story(doc, rem, tenant, cliente, nombres))
    return buf.getvalue()


def build_remisiones_pdf(items: list, tenant) -> bytes:
    """PDF con varias remisiones, una por página. items = [(rem, cliente, nombres), …]."""
    buf = io.BytesIO()
    doc = _doc(buf, "Remisiones")
    story: list = []
    for i, (rem, cliente, nombres) in enumerate(items):
        if i > 0:
            story.append(PageBreak())
        story.extend(_remision_story(doc, rem, tenant, cliente, nombres))
    doc.build(story)
    return buf.getvalue()

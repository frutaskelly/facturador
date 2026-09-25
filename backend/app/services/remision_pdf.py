"""Representación impresa (PDF) de una remisión.

Es el MISMO documento que la factura (`factura_pdf`): el mismo encabezado con el
emisor y su logo, el mismo bloque de receptor, las mismas columnas —incluido el
desglose de IEPS e IVA por partida—, los mismos totales y el importe con letra.
Lo único suyo son la palabra REMISIÓN, la banda ámbar y la regla más clara bajo
el total (decisión del dueño, 21-sep-2026).

Hasta ese día la remisión llevaba además «DOCUMENTO NO FISCAL — no es un CFDI» en
la banda y una leyenda al pie; las dos se quitaron a petición suya. El mismo
formato lo imprime el bot de Smart Supply (`SmartSupply/bot/documento_pdf.py`):
un cliente que recibe la remisión por WhatsApp y la factura por correo tiene que
ver el mismo papel.
"""
from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

from .factura_pdf import (
    _ESTILO_CELDA_IMP,
    _ESTILO_NOTE,
    _ESTILO_TH,
    _ESTILO_TIT,
    _domicilio,
    _esc,
    _fecha,
    _logo_flowable,
    _money,
    _p,
    _tasa,
    canvas_folio,
    celda_impuesto,
    marca_documento,
    numero_a_letra,
)

# La regla bajo el TOTAL es lo único que la remisión conserva de su diseño anterior:
# más clara que la de la factura (#1e293b), para distinguirlas de un vistazo.
_REGLA_TOTAL = colors.HexColor("#94a3b8")


def _clave_y_nombre(valor) -> tuple[str, str]:
    """`_nombres_para_pdf` entrega (código del cliente, nombre); se tolera el texto
    suelto de antes para que un llamador viejo no reviente."""
    if isinstance(valor, (tuple, list)):
        return (str(valor[0] or ""), str(valor[1] or ""))
    return ("", str(valor or ""))


def _remision_story(doc, rem, tenant, cliente, nombres: dict, indice: int = 0) -> list:
    """Flowables de UNA remisión (para armar un PDF individual o un lote)."""
    folio = rem.folio_interno or ""
    story: list = [marca_documento({"id": indice, "texto": f"Remisión {folio}"})]

    # ── Encabezado: emisor (izq) + logo (der) — igual que la factura ──
    emisor_dom = _domicilio(tenant.domicilio_fiscal or {}, tenant.domicilio_fiscal_cp or "")
    emisor_cell = [
        _p(_esc(tenant.legal_name), _ESTILO_TIT),
        _p(f"RFC: {tenant.rfc or ''}"),
        _p(_esc(emisor_dom)),
        _p(f"Régimen Fiscal: {tenant.regimen_fiscal_sat or ''}"),
        _p(f"Lugar de expedición: {tenant.domicilio_fiscal_cp or ''}"),
    ]
    logo = _logo_flowable(tenant)
    header = Table([[emisor_cell, logo or ""]], colWidths=[doc.width - 150, 150])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 6))

    # ── Banda: la de la factura, en ámbar y con la palabra REMISIÓN ──
    banda = Table(
        [[_p(f"<b>REMISIÓN {folio}</b>", _ESTILO_TIT),
          _p(f"Fecha de emisión: {_fecha(rem.fecha_remision)}")]],
        colWidths=[doc.width * 0.35, doc.width * 0.65],
    )
    banda.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf3e0")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0a12b")),
        ("TEXTCOLOR", (0, 0), (0, 0), colors.HexColor("#9a6608")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(banda)
    story.append(Spacer(1, 8))

    # ── Receptor — el mismo bloque de la factura, sin lo que solo existe en un CFDI ──
    receptor_cp = (cliente.domicilio_fiscal or {}).get("cp", "") if cliente else ""
    receptor_cell = [
        _p("<b>Receptor</b>", _ESTILO_TIT),
        _p(_esc(cliente.legal_name) if cliente else ""),
        _p(f"RFC: {cliente.rfc if cliente else ''}"),
        _p(f"Código Postal: {receptor_cp}"),
        _p(f"Régimen Fiscal: {cliente.regimen_fiscal if cliente else ''}"),
    ]
    rec = Table([[receptor_cell]], colWidths=[doc.width])
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

    # ── Conceptos: las mismas columnas que la factura ──
    lineas = sorted(rem.lineas, key=lambda x: x.numero_linea)
    partidas = [(ln, *_clave_y_nombre(nombres.get(ln.producto_id, str(ln.producto_id))))
                for ln in lineas]
    # La clave y el desglose solo salen si hay algo que enseñar: un cliente sin códigos
    # propios, o una remisión de puro exento, no ganan nada con columnas de guiones.
    con_clave = any(clave for _, clave, _ in partidas)
    con_imp = any(Decimal(ln.iva_importe or 0) or Decimal(ln.ieps_importe or 0)
                  for ln in lineas)
    head = ["Cant.", "Unidad"] + (["Clave"] if con_clave else []) + ["Descripción"]
    head += ["P. Unitario", "Importe"]
    if con_imp:
        head += ["I.E.P.S.", "I.V.A."]
    data = [[_p(h, _ESTILO_TH) for h in head]]
    for ln, clave, nombre in partidas:
        fila = [_p(f"{Decimal(ln.cantidad_solicitada):g}"), _p(ln.presentacion or "")]
        if con_clave:
            fila.append(_p(_esc(clave)))
        fila += [_p(_esc(nombre)), _p(_money(ln.precio_unitario)), _p(_money(ln.importe))]
        if con_imp:
            # La línea de remisión guarda el importe del impuesto pero no su tasa, así
            # que se deriva del propio importe (ver `_tasa` en factura_pdf).
            fila += [_p(celda_impuesto(_tasa(ln.importe, ln.ieps_importe), ln.ieps_importe),
                        _ESTILO_CELDA_IMP),
                     _p(celda_impuesto(_tasa(ln.importe, ln.iva_importe), ln.iva_importe),
                        _ESTILO_CELDA_IMP)]
        data.append(fila)
    if con_clave and con_imp:
        anchos = [0.08, 0.09, 0.12, 0.30, 0.10, 0.10, 0.105, 0.105]
    elif con_clave:
        anchos = [0.09, 0.10, 0.13, 0.43, 0.125, 0.125]
    elif con_imp:
        anchos = [0.09, 0.11, 0.38, 0.105, 0.105, 0.105, 0.105]
    else:
        anchos = [0.10, 0.14, 0.50, 0.13, 0.13]
    tabla = Table(data, colWidths=[doc.width * a for a in anchos], repeatRows=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#94a3b8")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "RIGHT"),
        ("ALIGN", (len(head) - (4 if con_imp else 2), 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tabla)
    story.append(Spacer(1, 6))

    # ── Totales: el mismo orden de la factura (IEPS antes que IVA) ──
    tot_rows = [["Subtotal", _money(rem.subtotal)]]
    if rem.descuento and Decimal(rem.descuento) > 0:
        tot_rows.append(["Descuento", _money(rem.descuento)])
    if rem.ieps and Decimal(rem.ieps) > 0:
        tot_rows.append(["IEPS", _money(rem.ieps)])
    if rem.iva and Decimal(rem.iva) > 0:
        tot_rows.append(["IVA", _money(rem.iva)])
    tot_rows.append(["TOTAL", _money(rem.total)])
    tot = Table([[_p(k), _p(f"<b>{v}</b>" if k == "TOTAL" else v)] for k, v in tot_rows],
                colWidths=[70, 90], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, _REGLA_TOTAL),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(tot)
    story.append(Spacer(1, 8))
    story.append(_p(numero_a_letra(rem.total), _ESTILO_TIT))
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
    doc.build(_remision_story(doc, rem, tenant, cliente, nombres), canvasmaker=canvas_folio())
    return buf.getvalue()


def build_remisiones_pdf(items: list, tenant) -> bytes:
    """PDF con varias remisiones, una por página. items = [(rem, cliente, nombres), …]."""
    buf = io.BytesIO()
    doc = _doc(buf, "Remisiones")
    story: list = []
    for i, (rem, cliente, nombres) in enumerate(items):
        if i > 0:
            story.append(PageBreak())
        story.extend(_remision_story(doc, rem, tenant, cliente, nombres, i))
    doc.build(story, canvasmaker=canvas_folio())
    return buf.getvalue()

"""Membrete y tabla de REPORTES — el layout de Smart Supply, en el Facturador.

Pedido del dueño (29-ago-2026): "los formatos de reporte con el logo y layout
que se usa el Smart Supply se deben usar en el Facturador". Este módulo replica
el sistema de reportes del bot (sheets_push._titulos/_make_table, 13-ago-2026):

- Membrete: logo (40 mm) + datos fiscales a la IZQUIERDA, título (15 pt) y
  subtítulo (9.5 pt gris) a la DERECHA, cerrado por una regla azul #305496.
- Tabla: encabezado azul #305496 con texto blanco, rejilla 0.4 pt #BFBFBF,
  zebra blanco/#EAF0FA, fila de totales #D9E1F2 en negritas.
- Folio de página "n / total" abajo a la derecha (Helvetica 8).

Diferencia deliberada con el bot: el logo sale de tenants.logo (BYTEA, el que
se sube en Ajustes › Empresa) con el aspecto calculado del archivo real — el
bot tenía el ratio HARDCODEADO y estaba desactualizado (logo achatado).
"""
from __future__ import annotations

import io
from typing import Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

AZUL = colors.HexColor("#305496")
AZUL_CLARO = colors.HexColor("#D9E1F2")
ZEBRA = colors.HexColor("#EAF0FA")
REJILLA = colors.HexColor("#BFBFBF")

_ST = getSampleStyleSheet()
_TITULO = ParagraphStyle("rep_t", parent=_ST["Title"], fontSize=15, spaceAfter=1, alignment=2)
_SUBTITULO = ParagraphStyle("rep_s", parent=_ST["Normal"], fontSize=9.5,
                            textColor=colors.grey, alignment=2)
_EMISOR = ParagraphStyle("rep_e", parent=_ST["Normal"], fontSize=6.8, leading=8.6,
                         textColor=colors.HexColor("#444444"))
CELDA = ParagraphStyle("rep_c", parent=_ST["Normal"], fontSize=8.5, leading=10)


def _logo(tenant) -> Optional[Image]:
    """El logo del emisor a 40 mm de ancho, con el aspecto del archivo REAL."""
    data = getattr(tenant, "logo", None)
    if not data:
        return None
    try:
        iw, ih = ImageReader(io.BytesIO(data)).getSize()
        w = 40 * mm
        return Image(io.BytesIO(data), width=w, height=w * ih / iw)
    except Exception:
        return None  # un logo corrupto no debe tumbar el reporte


def membrete(tenant, titulo: str, subtitulo: str = "") -> list:
    """Encabezado común de los reportes (mismo layout que el bot)."""
    from .factura_pdf import _domicilio

    izq: list = []
    logo = _logo(tenant)
    if logo is not None:
        izq.append(logo)
        izq.append(Spacer(1, 1.5 * mm))
    dom = _domicilio(getattr(tenant, "domicilio_fiscal", None) or {},
                     getattr(tenant, "domicilio_fiscal_cp", "") or "")
    nombre = getattr(tenant, "trade_name", None) or getattr(tenant, "legal_name", "") or ""
    izq.append(Paragraph(
        f"<b>{nombre}</b><br/>RFC: {getattr(tenant, 'rfc', '') or ''}<br/>{dom}", _EMISOR))

    der = [Paragraph(titulo, _TITULO)]
    if subtitulo:
        der.append(Paragraph(subtitulo, _SUBTITULO))
    cab = Table([[izq, der]], colWidths=[72 * mm, None])
    cab.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, 0), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, AZUL),
    ]))
    return [cab, Spacer(1, 5 * mm)]


def tabla_reporte(
    headers: Sequence[str],
    filas: Sequence[Sequence],
    col_widths: Sequence,
    *,
    num_cols: Sequence[int] = (),
    filas_totales: int = 0,
) -> Table:
    """La tabla azul del bot: encabezado #305496, rejilla, zebra y totales.

    `filas_totales`: cuántas filas del FINAL son de totales (fondo #D9E1F2,
    negritas, sin zebra).
    """
    data = [list(headers)] + [list(f) for f in filas]
    tbl = Table(data, colWidths=list(col_widths), repeatRows=1)
    fin_zebra = -1 - filas_totales
    ts = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, REJILLA),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    for c in num_cols:
        ts.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if len(data) > 1 + filas_totales:
        ts.append(("ROWBACKGROUNDS", (0, 1), (-1, fin_zebra), [colors.white, ZEBRA]))
    if filas_totales:
        ts += [
            ("BACKGROUND", (0, -filas_totales), (-1, -1), AZUL_CLARO),
            ("FONTNAME", (0, -filas_totales), (-1, -1), "Helvetica-Bold"),
        ]
    tbl.setStyle(TableStyle(ts))
    return tbl


def _num_canvas():
    """Canvas que numera 'n / total' abajo a la derecha (patrón del bot)."""

    class _NumCanvas(_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st)
                w, _h = self._pagesize
                self.setFont("Helvetica", 8)
                self.setFillColor(colors.HexColor("#777777"))
                self.drawRightString(w - 12 * mm, 9 * mm, f"{self._pageNumber} / {total}")
                _canvas.Canvas.showPage(self)
            _canvas.Canvas.save(self)

    return _NumCanvas


def construir(titulo: str, flowables: list) -> bytes:
    """Carta, márgenes 18/16 mm (los del bot), con folio de página."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm, title=titulo,
    )
    doc.build(flowables, canvasmaker=_num_canvas())
    return buf.getvalue()

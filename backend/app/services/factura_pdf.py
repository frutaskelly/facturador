"""Representación impresa (PDF) de la factura, generada con reportlab.

A diferencia del PDF del PAC, esta la controlamos nosotros: muestra el folio
interno serie+folio (p. ej. "GAZA4"), el logo del emisor arriba a la derecha, y
—cuando la factura está timbrada— el bloque fiscal completo (UUID/folio fiscal,
sellos CFDI/SAT, certificados, cadena original del TFD y el QR de verificación
del SAT). El contenido comercial (emisor/receptor/conceptos/totales) sale de la
BD; el bloque fiscal se lee del XML timbrado, que es la fuente autoritativa.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Optional

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_NS = {
    "cfdi": "http://www.sat.gob.mx/cfd/4",
    "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
}

_QR_BASE = "https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx"

_TIPO_COMPROBANTE = {
    "I": "I - Ingreso", "E": "E - Egreso", "T": "T - Traslado",
    "N": "N - Nómina", "P": "P - Pago",
}

_ESTILO = ParagraphStyle("base", fontName="Helvetica", fontSize=8, leading=10)
_ESTILO_MONO = ParagraphStyle("mono", fontName="Courier", fontSize=6, leading=7)
_ESTILO_TIT = ParagraphStyle("tit", fontName="Helvetica-Bold", fontSize=8, leading=10)
# Encabezado de columnas: texto oscuro legible sobre fondo claro (el color del
# Paragraph gana sobre el TEXTCOLOR de la tabla, por eso se define aquí).
_ESTILO_TH = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, leading=10,
                            textColor=colors.HexColor("#334155"))
_ESTILO_CELDA_IMP = ParagraphStyle("imp", fontName="Helvetica", fontSize=7, leading=8.5,
                                   textColor=colors.HexColor("#475569"))
_ESTILO_NOTE = ParagraphStyle("note", fontName="Helvetica", fontSize=8, leading=11,
                              textColor=colors.HexColor("#475569"))


# ── Importe con letra ───────────────────────────────────────────────────────────
# Lo pedía el dueño y el bot ya lo imprimía (21-sep-2026): un documento que se firma
# a mano se revisa contra la cantidad escrita, no contra los dígitos.
_UNI = ("", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ",
        "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISEIS", "DIECISIETE", "DIECIOCHO",
        "DIECINUEVE", "VEINTE")
_DEC = ("", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA",
        "NOVENTA")
_CEN = ("", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS",
        "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS")


def _centenas(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    out = []
    c, r = divmod(n, 100)
    if c:
        out.append(_CEN[c])
    if r:
        if r <= 20:
            out.append(_UNI[r])
        else:
            d, u = divmod(r, 10)
            if d == 2 and u:
                out.append("VEINTI" + _UNI[u])
            else:
                out.append(_DEC[d] + (" Y " + _UNI[u] if u else ""))
    return " ".join(x for x in out if x)


def numero_a_letra(monto) -> str:
    """7199.85 -> 'SIETE MIL CIENTO NOVENTA Y NUEVE PESOS 85/100 M.N.'"""
    centavos = int((Decimal(monto or 0) * 100).to_integral_value())
    ent, cts = divmod(abs(centavos), 100)
    if ent == 0:
        letras = "CERO"
    else:
        millones, resto = divmod(ent, 1_000_000)
        miles, unidades = divmod(resto, 1000)
        partes = []
        if millones:
            partes.append("UN MILLON" if millones == 1 else _centenas(millones) + " MILLONES")
        if miles:
            partes.append("MIL" if miles == 1 else _centenas(miles) + " MIL")
        if unidades:
            partes.append(_centenas(unidades))
        letras = " ".join(partes)
    return f"{letras} PESOS {cts:02d}/100 M.N."


def _tasa(importe, impuesto) -> Decimal:
    """Tasa efectiva de una línea, en %. La remisión guarda el importe del impuesto pero
    no su tasa, así que se deriva; en la factura hay tasa propia y esto no se usa."""
    base = Decimal(importe or 0)
    if not base:
        return Decimal(0)
    return (Decimal(impuesto or 0) / base * 100).quantize(Decimal("0.01"))


def celda_impuesto(pct, monto) -> str:
    """'16% $12.48' cuando aplica, '—' cuando la partida no causa ese impuesto.

    La tasa va junto al monto para ver de un vistazo si un producto quedó con el esquema
    de impuestos equivocado (misma celda que imprime el bot desde agosto de 2026)."""
    pct, monto = Decimal(pct or 0), Decimal(monto or 0)
    if not pct and not monto:
        return "—"
    return f"{pct.normalize():f}% {_money(monto)}"


def marca_documento(datos: dict):
    """Flowable invisible que le dice al canvas de qué documento es la página que empieza.
    Sin esto, en un PDF con varias facturas no hay forma de saber a cuál pertenece cada
    hoja cuando toca dibujar el pie."""
    from reportlab.platypus import Flowable

    class _Marca(Flowable):
        def __init__(self):
            Flowable.__init__(self)
            self.width = self.height = 0

        def wrap(self, *a):
            return (0, 0)

        def draw(self):
            self.canv._marca = dict(datos)

    return _Marca()


def canvas_folio():
    """Canvas que estampa el folio y «Página n de m» SOLO cuando un documento ocupa más de
    una hoja.

    En una sola hoja el documento no cambia en nada. Cuando son varias —una factura de 60
    partidas son tres— el cliente tiene que poder saber de qué documento es cada hoja y si
    le falta alguna: hasta hoy las páginas 2 y 3 salían sin folio (21-sep-2026)."""
    from reportlab.pdfgen.canvas import Canvas

    class _CanvasFolio(Canvas):
        def __init__(self, *a, **k):
            Canvas.__init__(self, *a, **k)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            cuenta: dict = {}
            for st in self._paginas:
                i = (st.get("_marca") or {}).get("id")
                cuenta[i] = cuenta.get(i, 0) + 1
            visto: dict = {}
            for st in self._paginas:
                self.__dict__.update(st)
                m = st.get("_marca") or {}
                i = m.get("id")
                if i is not None and cuenta.get(i, 1) > 1:
                    visto[i] = visto.get(i, 0) + 1
                    self.setFont("Helvetica", 7)
                    self.setFillColor(colors.HexColor("#64748b"))
                    self.drawString(15 * mm, 8 * mm, m.get("texto", ""))
                    self.drawRightString(self._pagesize[0] - 15 * mm, 8 * mm,
                                         f"Página {visto[i]} de {cuenta[i]}")
                    self.setFillColor(colors.black)
                Canvas.showPage(self)
            Canvas.save(self)

    return _CanvasFolio


def _fecha(valor) -> str:
    """dd/mm/aaaa. El XML del SAT trae '2026-09-15T12:00:00' y el modelo un date/datetime;
    el documento que lee una persona los enseña igual (21-sep-2026)."""
    if valor is None or valor == "":
        return ""
    if hasattr(valor, "strftime"):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor)
    cabeza = texto[:10]
    if len(cabeza) == 10 and cabeza[4] == "-" and cabeza[7] == "-":
        return f"{cabeza[8:10]}/{cabeza[5:7]}/{cabeza[0:4]}"
    return texto


def _money(v) -> str:
    return f"${Decimal(v or 0):,.2f}"


def _esc(s) -> str:
    """Escapa contenido dinámico (nombres, descripciones) para el mini-HTML de
    reportlab, sin tocar el marcado <b> estático que ponemos nosotros."""
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str, style: ParagraphStyle = _ESTILO) -> Paragraph:
    return Paragraph(text or "", style)


def _parse_xml(xml: Optional[str]) -> dict:
    """Extrae del XML timbrado los datos fiscales para el pie (o {} si no hay)."""
    if not xml:
        return {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    tfd = root.find("cfdi:Complemento/tfd:TimbreFiscalDigital", _NS)
    receptor = root.find("cfdi:Receptor", _NS)
    out = {
        "sello_cfdi": root.get("Sello", ""),
        "no_certificado": root.get("NoCertificado", ""),
        "fecha": root.get("Fecha", ""),
        "tipo_comprobante": root.get("TipoDeComprobante", "I"),
        "lugar_expedicion": root.get("LugarExpedicion", ""),
        "receptor_cp": receptor.get("DomicilioFiscalReceptor", "") if receptor is not None else "",
    }
    if tfd is not None:
        out["tfd"] = {
            "version": tfd.get("Version", "1.1"),
            "uuid": tfd.get("UUID", ""),
            "fecha_timbrado": tfd.get("FechaTimbrado", ""),
            "rfc_prov": tfd.get("RfcProvCertif", ""),
            "sello_cfd": tfd.get("SelloCFD", ""),
            "no_cert_sat": tfd.get("NoCertificadoSAT", ""),
            "sello_sat": tfd.get("SelloSAT", ""),
        }
    return out


def _cadena_original_tfd(t: dict) -> str:
    return (
        f"||{t['version']}|{t['uuid']}|{t['fecha_timbrado']}|{t['rfc_prov']}"
        f"|{t['sello_cfd']}|{t['no_cert_sat']}||"
    )


def _qr_drawing(uuid: str, rfc_emisor: str, rfc_receptor: str, total, sello_cfd: str) -> Drawing:
    tt = f"{Decimal(total or 0):017.6f}"          # 10 enteros + 6 decimales, sin separadores
    fe = (sello_cfd or "")[-8:]
    url = f"{_QR_BASE}?id={uuid}&re={rfc_emisor}&rr={rfc_receptor}&tt={tt}&fe={fe}"
    widget = QrCodeWidget(url, barLevel="M")
    b = widget.getBounds()
    w, h = b[2] - b[0], b[3] - b[1]
    size = 90.0
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(widget)
    return d


def _logo_flowable(tenant) -> Optional[Image]:
    if not getattr(tenant, "logo", None):
        return None
    try:
        reader = ImageReader(io.BytesIO(tenant.logo))
        iw, ih = reader.getSize()
        max_w, max_h = 150.0, 60.0
        scale = min(max_w / iw, max_h / ih)
        return Image(io.BytesIO(tenant.logo), width=iw * scale, height=ih * scale)
    except Exception:
        return None  # un logo corrupto no debe tumbar la factura


def _domicilio(dom: dict, cp: str) -> str:
    partes = [dom.get("calle"), dom.get("colonia"), dom.get("ciudad"), dom.get("estado"), dom.get("pais")]
    linea = ", ".join(str(p) for p in partes if p)
    return f"{linea}{', CP: ' + cp if cp else ''}"


def build_factura_pdf(factura, tenant, cliente) -> bytes:
    folio_negocio = f"{factura.serie or ''}{factura.folio}"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"Factura {folio_negocio}",
    )
    doc.build(_factura_story(doc, factura, tenant, cliente), canvasmaker=canvas_folio())
    return buf.getvalue()


def build_facturas_pdf(items: list, tenant) -> bytes:
    """PDF con varias facturas, una por página. items = [(factura, cliente), …]."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title="Facturas",
    )
    story: list = []
    for i, (factura, cliente) in enumerate(items):
        if i > 0:
            story.append(PageBreak())
        story.extend(_factura_story(doc, factura, tenant, cliente, i))
    doc.build(story, canvasmaker=canvas_folio())
    return buf.getvalue()


def _factura_story(doc, factura, tenant, cliente, indice: int = 0) -> list:
    fx = _parse_xml(getattr(factura, "xml", None))
    tfd = fx.get("tfd")
    timbrada = bool(tfd and tfd.get("uuid"))
    folio_negocio = f"{factura.serie or ''}{factura.folio}"
    story: list = [marca_documento({"id": indice, "texto": f"Factura {folio_negocio}"})]

    # ── Encabezado: emisor (izq) + logo (der) ──
    emisor_dom = _domicilio(tenant.domicilio_fiscal or {}, tenant.domicilio_fiscal_cp or "")
    emisor_cell = [
        _p(_esc(tenant.legal_name), _ESTILO_TIT),
        _p(f"RFC: {tenant.rfc or ''}"),
        _p(_esc(emisor_dom)),
        _p(f"Régimen Fiscal: {tenant.regimen_fiscal_sat or ''}"),
        _p(f"Lugar de expedición: {factura.lugar_expedicion or fx.get('lugar_expedicion') or ''}"),
    ]
    logo = _logo_flowable(tenant)
    header = Table(
        [[emisor_cell, logo or ""]],
        colWidths=[doc.width - 150, 150],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 6))

    # ── Banda de folio ──
    efecto = _TIPO_COMPROBANTE.get(fx.get("tipo_comprobante", "I"), "I - Ingreso")
    estado_txt = "" if timbrada else "  ·  BORRADOR — SIN VALIDEZ FISCAL"
    # Sin timbrar no hay XML: la fecha sale del propio registro, que para eso la guarda.
    # Antes la banda del borrador decía "Fecha de emisión:" y nada.
    fecha_txt = _fecha(fx.get("fecha") or getattr(factura, "fecha", None))
    banda = Table(
        [[_p(f"<b>FACTURA {folio_negocio}</b>", _ESTILO_TIT),
          _p(f"Fecha de emisión: {fecha_txt}  ·  Efecto: {efecto}{estado_txt}")]],
        colWidths=[doc.width * 0.35, doc.width * 0.65],
    )
    banda.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(banda)
    story.append(Spacer(1, 8))

    # ── Receptor ──
    receptor_cp = fx.get("receptor_cp") or (cliente.domicilio_fiscal or {}).get("cp", "") if cliente else ""
    receptor_cell = [
        _p("<b>Receptor</b>", _ESTILO_TIT),
        _p(_esc(cliente.legal_name) if cliente else ""),
        _p(f"RFC: {cliente.rfc if cliente else ''}"),
        _p(f"Código Postal: {receptor_cp}"),
        _p(f"Uso del CFDI: {factura.uso_cfdi or ''}"),
        _p(f"Régimen Fiscal: {cliente.regimen_fiscal if cliente else ''}"),
    ]
    pago_cell = [
        _p("<b>Pago</b>", _ESTILO_TIT),
        _p(f"Forma de pago: {factura.forma_pago or ''}"),
        _p(f"Método de pago: {factura.metodo_pago or ''}"),
        _p(f"Moneda: {factura.moneda or 'MXN'}"),
    ]
    rec = Table([[receptor_cell, pago_cell]], colWidths=[doc.width * 0.6, doc.width * 0.4])
    rec.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(rec)
    story.append(Spacer(1, 8))

    # ── Notas (arriba de los conceptos) ──
    if getattr(factura, "notas", None):
        notas_tbl = Table(
            [[_p("Notas", _ESTILO_TH)], [_p(_esc(factura.notas), _ESTILO_NOTE)]],
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

    # ── Conceptos ──
    lineas = sorted(factura.lineas, key=lambda x: x.numero_linea)
    # El desglose por partida solo aparece si alguna línea causa impuesto: una factura de
    # puro producto exento no gana nada con dos columnas de guiones (21-sep-2026).
    con_imp = any(Decimal(ln.iva_importe or 0) or Decimal(ln.ieps_importe or 0)
                  for ln in lineas)
    head = ["Cant.", "Unidad", "Clave SAT", "Descripción", "P. Unitario", "Importe"]
    if con_imp:
        head += ["I.E.P.S.", "I.V.A."]
    data = [[_p(h, _ESTILO_TH) for h in head]]
    for ln in lineas:
        fila = [
            _p(f"{Decimal(ln.cantidad):g}"),
            _p(ln.clave_unidad or ""),
            _p(ln.clave_prod_serv or ""),
            _p(_esc(ln.descripcion)),
            _p(_money(ln.valor_unitario)),
            _p(_money(ln.importe)),
        ]
        if con_imp:
            # El IEPS puede ser TASA o CUOTA: con cuota no hay porcentaje que enseñar, así
            # que se deriva del importe igual que en la remisión.
            ieps_pct = (Decimal(ln.ieps_valor or 0) * 100 if ln.ieps_tipo == "TASA"
                        else _tasa(ln.importe, ln.ieps_importe))
            fila += [_p(celda_impuesto(ieps_pct, ln.ieps_importe), _ESTILO_CELDA_IMP),
                     _p(celda_impuesto(Decimal(ln.iva_tasa or 0) * 100, ln.iva_importe),
                        _ESTILO_CELDA_IMP)]
        data.append(fila)
    anchos = ([0.08, 0.09, 0.12, 0.30, 0.10, 0.10, 0.105, 0.105] if con_imp
              else [0.09, 0.10, 0.13, 0.44, 0.12, 0.12])
    tabla = Table(data, colWidths=[doc.width * a for a in anchos], repeatRows=1)
    tabla.setStyle(TableStyle([
        # Encabezado claro con texto oscuro (legible) y una regla que lo define.
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#94a3b8")),
        # Filas: líneas horizontales sutiles en vez de un grid pesado (moderno).
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -1), "RIGHT"),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tabla)
    story.append(Spacer(1, 6))

    # ── Totales ──
    tot_rows = [["Subtotal", _money(factura.subtotal)]]
    if factura.descuento and Decimal(factura.descuento) > 0:
        tot_rows.append(["Descuento", _money(factura.descuento)])
    if factura.ieps_trasladado and Decimal(factura.ieps_trasladado) > 0:
        tot_rows.append(["IEPS", _money(factura.ieps_trasladado)])
    tot_rows.append(["IVA", _money(factura.iva_trasladado)])
    if factura.ret_iva and Decimal(factura.ret_iva) > 0:
        tot_rows.append(["Ret. IVA", f"-{_money(factura.ret_iva)}"])
    if factura.ret_isr and Decimal(factura.ret_isr) > 0:
        tot_rows.append(["Ret. ISR", f"-{_money(factura.ret_isr)}"])
    tot_rows.append(["TOTAL", _money(factura.total)])
    tot = Table([[_p(k), _p(f"<b>{v}</b>" if k == "TOTAL" else v)] for k, v in tot_rows],
                colWidths=[70, 90], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#1e293b")),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(tot)
    story.append(Spacer(1, 8))
    story.append(_p(numero_a_letra(factura.total), _ESTILO_TIT))
    story.append(Spacer(1, 10))

    # ── Bloque fiscal (solo timbrada) ──
    if timbrada:
        qr = _qr_drawing(
            tfd["uuid"], tenant.rfc or "", (cliente.rfc if cliente else ""),
            factura.total, tfd["sello_cfd"],
        )
        fiscal = [
            _p("<b>Folio Fiscal (UUID):</b> " + tfd["uuid"], _ESTILO),
            _p(f"<b>No. Certificado del emisor:</b> {fx.get('no_certificado', '')}", _ESTILO),
            _p(f"<b>No. Certificado del SAT:</b> {tfd['no_cert_sat']}", _ESTILO),
            _p(f"<b>Fecha de certificación:</b> {tfd['fecha_timbrado']}   <b>RFC PAC:</b> {tfd['rfc_prov']}", _ESTILO),
            Spacer(1, 3),
            _p("<b>Sello digital del CFDI:</b>", _ESTILO),
            _p(fx.get("sello_cfdi", ""), _ESTILO_MONO),
            _p("<b>Sello del SAT:</b>", _ESTILO),
            _p(tfd["sello_sat"], _ESTILO_MONO),
            _p("<b>Cadena original del complemento de certificación digital del SAT:</b>", _ESTILO),
            _p(_cadena_original_tfd(tfd), _ESTILO_MONO),
        ]
        bloque = Table([[qr, fiscal]], colWidths=[100, doc.width - 100])
        bloque.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(bloque)
        story.append(Spacer(1, 4))
        story.append(_p("Este documento es una representación impresa de un CFDI.", _ESTILO))

    return story

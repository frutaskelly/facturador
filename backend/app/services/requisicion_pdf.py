"""El PDF de la requisición cotizada — el MISMO dibujo que el bot de WhatsApp.

Pedido del dueño (31-ago-2026): "los PDF deben ser iguales" a los que genera el
cotizador del bot (agente 1). Este módulo es el port de `pedido_pdf.py` del repo
del bot (SmartSupply/bot): canvas de reportlab a bajo nivel, formato QuickReport
de SAE — membrete con logo a proporción real, bloque de cliente "( clave )
nombre", alarma en rojo, tabla con columna CLAVE, notas del documento en negro y
notas de VALIDACIÓN DE PRECIO en rojo/negritas bajo la descripción, totales
Subtotal/Descuento/Desc. Fin./I.E.P.S./I.V.A./Total y el total con letra.

Diferencias deliberadas con el bot:
- El emisor (nombre, RFC, domicilio, CP) sale del tenant, no de las tablas
  PARAM_* de SAE; el logo sale de `tenants.logo` (el de Ajustes › Empresa) con
  el aspecto calculado del archivo real, no de un ratio hardcodeado.
- El bloque del cliente sale del catálogo de clientes del Facturador (clave =
  `clientes.codigo`), no de CLIE02.
Todo lo demás — medidas, fuentes, textos — se conserva idéntico.
"""
from __future__ import annotations

import io
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as _canvas


def _mm(v):
    return v * mm


def _wrap(c, texto, fuente, tam, ancho_max, max_lineas=2):
    """Parte el texto en líneas sin cortar palabras, midiendo con la fuente real."""
    palabras = (texto or "").split()
    lineas, actual = [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if c.stringWidth(prueba, fuente, tam) <= ancho_max or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
            if len(lineas) == max_lineas:
                break
    if actual and len(lineas) < max_lineas:
        lineas.append(actual)
    return lineas[:max_lineas]


# ----------------------------------------------------------------- importe con letra
_UNI = ("", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ",
        "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISEIS", "DIECISIETE", "DIECIOCHO",
        "DIECINUEVE", "VEINTE")
_DEC = ("", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA",
        "NOVENTA")
_CEN = ("", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS",
        "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS")


def _centenas(n):
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


def numero_a_letra(monto):
    """7199.85 -> 'SIETE MIL CIENTO NOVENTA Y NUEVE PESOS 85/100 M.N.'"""
    ent = int(round(monto * 100)) // 100
    cts = int(round(monto * 100)) % 100
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


# ----------------------------------------------------------------- emisor (tenant)
def _domicilio_sae(dom: dict, cp: str = "") -> str:
    """'Calle: X, Col. Y, CP: Z, CIUDAD, ESTADO, PAIS' — el formato del bot."""
    dom = dom or {}
    partes = []
    if dom.get("calle"):
        partes.append(f"Calle: {dom['calle']}")
    if dom.get("colonia"):
        partes.append(f"Col. {dom['colonia']}")
    cp = cp or dom.get("cp") or ""
    if cp:
        partes.append(f"CP: {cp}")
    partes += [str(p) for p in (dom.get("ciudad"), dom.get("estado"), dom.get("pais")) if p]
    return ", ".join(partes)


def _emisor_de_tenant(tenant) -> dict:
    cp_fiscal = getattr(tenant, "domicilio_fiscal_cp", "") or ""
    return {
        "nombre": getattr(tenant, "legal_name", "") or "",
        "rfc": getattr(tenant, "rfc", "") or "",
        "domicilio": _domicilio_sae(getattr(tenant, "domicilio_fiscal", None) or {}, cp_fiscal),
        "cp_expedicion": cp_fiscal,
        "logo": getattr(tenant, "logo", None),
    }


# ----------------------------------------------------------------- dibujo
def _encabezado(c, ped, emisor, ancho, alto, con_clave=False):
    """Membrete: logo a proporción real + texto en la columna derecha."""
    izq, der = _mm(12), ancho - _mm(12)
    y = alto - _mm(12)

    # --- logo, a escala real (el aspecto se calcula del archivo, no se supone)
    logo_w = _mm(46)
    logo_h = logo_w / 1.826
    data = emisor.get("logo")
    if data:
        try:
            img = ImageReader(io.BytesIO(data))
            iw, ih = img.getSize()
            logo_h = logo_w * ih / iw
            c.drawImage(img, izq, y - logo_h, width=logo_w, height=logo_h,
                        preserveAspectRatio=True, anchor="nw", mask="auto")
        except Exception:
            pass

    # --- nombre del emisor, centrado en el espacio a la derecha del logo
    x_txt = izq + logo_w + _mm(6)
    centro = (x_txt + der) / 2.0
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(centro, y - _mm(6), emisor["nombre"])

    # --- folio y fecha, esquina derecha
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(der, y - _mm(12.5), ped.get("tipo_doc") or "PEDIDO No.:")
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(der, y - _mm(17.5), ped["folio"])
    c.setFont("Helvetica", 8.5)
    c.drawRightString(der, y - _mm(22.5), ped["fecha"])

    # --- domicilio fiscal del emisor, bajo el nombre
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(x_txt, y - _mm(12.5), "Domicilio fiscal")
    c.setFont("Helvetica", 7)
    ancho_txt = (der - _mm(30)) - x_txt          # deja libre la columna del folio
    yy = y - _mm(16)
    for ln in _wrap(c, emisor["domicilio"], "Helvetica", 7, ancho_txt, 2):
        c.drawString(x_txt, yy, ln)
        yy -= _mm(3.4)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(x_txt, yy - _mm(0.6), "R.F.C.:")
    c.setFont("Helvetica", 7.5)
    c.drawString(x_txt + _mm(13), yy - _mm(0.6), emisor["rfc"])
    c.setFont("Helvetica", 7)
    c.drawString(x_txt + _mm(45), yy - _mm(0.6),
                 f"Lugar de expedicion, CP: {emisor['cp_expedicion']}")

    # el bloque de datos nunca debe invadir el logo
    y_fin = min(y - logo_h, yy - _mm(4))

    c.setLineWidth(0.8)
    c.line(izq, y_fin - _mm(2), der, y_fin - _mm(2))
    y = y_fin - _mm(7)

    # --- cliente
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(izq, y, "Cliente:")
    c.setFont("Helvetica", 8.5)
    c.drawString(izq + _mm(15), y, f"( {ped['cliente_clave']} )  {ped['cliente_nombre']}")
    y -= _mm(4.2)
    c.setFont("Helvetica", 7)
    for ln in _wrap(c, ped["cliente_domicilio"], "Helvetica", 7, der - izq, 2):
        c.drawString(izq, y, ln)
        y -= _mm(3.4)
    if ped["cliente_rfc"]:
        c.drawString(izq, y, f"RFC: {ped['cliente_rfc']}")
        y -= _mm(3.4)

    # --- observación (del documento, en negro; hasta 5 renglones, completa)
    if ped["observacion"]:
        y -= _mm(2)
        c.setFont("Helvetica", 8)
        for ln in _wrap(c, ped["observacion"], "Helvetica", 8, der - izq, 5):
            c.drawString(izq, y, ln)
            y -= _mm(3.8)

    # --- alarma: aviso que NO debe pasar desapercibido, en rojo y negritas
    if ped.get("alarma"):
        y -= _mm(2)
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 8)
        for ln in _wrap(c, ped["alarma"], "Helvetica-Bold", 8, der - izq, 4):
            c.drawString(izq, y, ln)
            y -= _mm(3.8)
        c.setFillColor(colors.black)

    # --- cabecera de la tabla
    y -= _mm(3)
    c.setFont("Helvetica-Bold", 8)
    cols = _columnas(ancho, con_clave)
    c.drawString(cols["cant"], y, "Cantidad")
    c.drawString(cols["uni"], y, "Unidad")
    if con_clave:
        c.drawString(cols["clave"], y, "Clave")
    c.drawString(cols["desc"], y, "Descripcion")
    c.drawRightString(cols["pu"], y, "P/U")
    c.drawRightString(cols["imp"], y, "Importe")
    c.drawRightString(cols["ieps"], y, "I.E.P.S.")
    c.drawRightString(cols["iva"], y, "I.V.A.")
    c.setLineWidth(0.5)
    c.line(izq, y - _mm(1.5), der, y - _mm(1.5))
    return y - _mm(5.5)


def _celda_imp(pct, monto):
    """'16% 12.48' cuando aplica, '-' cuando la partida no causa ese impuesto."""
    if not pct and not monto:
        return "-"
    return f"{pct:g}% {monto:,.2f}"


def _columnas(ancho, con_clave=False):
    """La columna CLAVE aparte, como en la requisición del propio cliente —
    solo la piden los documentos cuyas partidas traen 'clave'."""
    izq, der = _mm(12), ancho - _mm(12)
    base = {"pu": der - _mm(66), "imp": der - _mm(44), "ieps": der - _mm(22), "iva": der}
    if con_clave:
        return {"cant": izq, "uni": izq + _mm(15), "clave": izq + _mm(31),
                "desc": izq + _mm(64), **base}
    return {"cant": izq, "uni": izq + _mm(18), "desc": izq + _mm(34), **base}


def _totales(c, ped, ancho, y):
    izq, der = _mm(12), ancho - _mm(12)
    x_lbl = der - _mm(52)
    filas = [("Subtotal", ped["subtotal"]), ("Descuento", ped["descuento"]),
             ("Desc. Fin.", ped["desc_fin"]), ("I.E.P.S.", ped["ieps"]),
             ("I.V.A.", ped["iva"])]
    c.setFillColor(colors.black)
    for lbl, val in filas:
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x_lbl, y, lbl)
        c.setFont("Helvetica", 8.5)
        c.drawRightString(der, y, f"{val:,.2f}")
        y -= _mm(4.4)
    c.setLineWidth(0.8)
    c.line(x_lbl, y + _mm(2.6), der, y + _mm(2.6))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_lbl, y - _mm(0.5), "Total")
    c.drawRightString(der, y - _mm(0.5), f"{ped['total']:,.2f}")
    y -= _mm(7)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(izq, y, numero_a_letra(ped["total"]))


def generar_pdf_requisicion(ped: dict, tenant) -> bytes:
    """Una requisición cotizada → los bytes del PDF (una página o más)."""
    emisor = _emisor_de_tenant(tenant)
    ancho, alto = letter
    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"Requisicion {ped['folio']}")

    con_clave = any((it.get("clave") or "").strip() for it in ped["items"])
    cols = _columnas(ancho, con_clave)
    y = _encabezado(c, ped, emisor, ancho, alto, con_clave)
    y_min = _mm(52)
    c.setFont("Helvetica", 8)
    for it in ped["items"]:
        if y < y_min:                      # continúa en otra página
            c.showPage()
            y = _encabezado(c, ped, emisor, ancho, alto, con_clave)
            c.setFont("Helvetica", 8)
        # TODOS los valores en mayúsculas, normalizados al dibujar
        c.drawString(cols["cant"], y, f"{it['cant']:.3f}")
        c.drawString(cols["uni"], y, it["unidad"][:8].upper())
        if con_clave:
            c.drawString(cols["clave"], y, (it.get("clave") or "")[:18].upper())
        # la descripción se PARTE en dos renglones (nada de nombres mochos);
        # los importes quedan alineados al PRIMER renglón
        ancho_desc = (cols["pu"] - _mm(14)) - cols["desc"]
        l_desc = _wrap(c, (it["descr"] or "").upper(), "Helvetica", 8, ancho_desc, 2) or [""]
        c.drawString(cols["desc"], y, l_desc[0])
        c.drawRightString(cols["pu"], y, f"{it['precio']:,.2f}")
        c.drawRightString(cols["imp"], y, f"{it['importe']:,.2f}")
        # con la tasa al lado del monto: se ve de un vistazo si un producto
        # quedó con el esquema de impuestos equivocado
        c.setFont("Helvetica", 7)
        c.drawRightString(cols["ieps"], y, _celda_imp(it["ieps_pct"], it["ieps"]))
        c.drawRightString(cols["iva"], y, _celda_imp(it["iva_pct"], it["iva"]))
        c.setFont("Helvetica", 8)
        for extra in l_desc[1:]:
            y -= _mm(3.2)
            c.drawString(cols["desc"], y, extra)
        # la NOTA de la partida (la del documento del cliente) va DEBAJO de su
        # descripción, en negro
        nota = (it.get("nota") or "").strip().upper()
        if nota:
            for ln in _wrap(c, nota, "Helvetica", 8, ancho_desc, 2):
                y -= _mm(3.2)
                c.drawString(cols["desc"], y, ln)
        # nota de ALARMA (validación de precio): en ROJO y negritas para que no
        # se confunda con la nota del documento
        nota_al = (it.get("nota_alarma") or "").strip().upper()
        if nota_al:
            c.setFillColor(colors.red)
            c.setFont("Helvetica-Bold", 7.5)
            for ln in _wrap(c, nota_al, "Helvetica-Bold", 7.5, ancho_desc, 3):
                y -= _mm(3.2)
                c.drawString(cols["desc"], y, ln)
            c.setFillColor(colors.black)
            c.setFont("Helvetica", 8)
        # un renglón de aire ENTRE productos, solo en documentos con clave
        y -= _mm(6.1) if con_clave else _mm(3.9)
    _totales(c, ped, ancho, _mm(44))
    c.showPage()
    c.save()
    return buf.getvalue()

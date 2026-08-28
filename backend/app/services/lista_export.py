"""Una lista de precios que sale y regresa: Excel de ida y vuelta, y PDF.

Pedido del dueño (28-ago-2026): desde la pantalla de Precios poder (a) bajar
el PDF de la lista para mandarla, (b) bajar un Excel con los precios y
(c) subir ESE MISMO Excel para actualizar en masa — agregar renglones nuevos,
cambiar precios o quitar renglones.

El contrato del Excel (mismas columnas al exportar y al importar):

    SKU | PRODUCTO | PRESENTACION | DESDE CANTIDAD | PRECIO

- El renglón se identifica por (SKU, PRESENTACION, DESDE CANTIDAD).
- PRECIO con valor → se crea o se actualiza.
- PRECIO vacío o 0 → el renglón SE QUITA de la lista (así se poda sin borrar
  a mano). PRODUCTO es informativo: el que manda es el SKU.
- Un SKU que no existe en el catálogo se reporta como error de esa fila y las
  demás siguen — aquí no se dan de alta productos (para eso está el wizard de
  importación, que crea producto + código del cliente + todo lo demás).
"""
from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import ListaPrecios, Precio, Producto

HDR = ["SKU", "PRODUCTO", "PRESENTACION", "DESDE CANTIDAD", "PRECIO"]


def _filas(db: Session, lista: ListaPrecios) -> list[tuple]:
    q = (
        db.query(Precio, Producto.sku, Producto.nombre)
        .join(Producto, Producto.id == Precio.producto_id)
        .filter(Precio.lista_id == lista.id, Producto.deleted_at.is_(None))
        .order_by(Producto.nombre.asc(), Precio.presentacion.asc(), Precio.cantidad_minima.asc())
    )
    return [(sku, nombre, p.presentacion, p.cantidad_minima, p.precio_unitario)
            for p, sku, nombre in q.all()]


def exportar_xlsx(db: Session, lista: ListaPrecios) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Precios"
    ws.append(HDR)
    for sku, nombre, pres, cant, precio in _filas(db, lista):
        ws.append([sku, nombre, pres, float(cant), float(precio)])
    # anchos legibles: nadie quiere reacomodar columnas antes de trabajar
    for col, ancho in zip("ABCDE", (14, 46, 14, 16, 12)):
        ws.column_dimensions[col].width = ancho
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exportar_pdf(db: Session, lista: ListaPrecios, tenant_nombre: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=14 * mm, bottomMargin=14 * mm,
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            title=f"Lista de precios · {lista.nombre}")
    styles = getSampleStyleSheet()
    partes = [
        Paragraph(tenant_nombre, styles["Title"]),
        Paragraph(f"Lista de precios — {lista.nombre}", styles["Heading2"]),
    ]
    data = [["Producto", "Presentación", "Precio"]]
    for _sku, nombre, pres, cant, precio in _filas(db, lista):
        etiqueta = pres if cant in (1, Decimal("1")) else f"{pres} (desde {cant})"
        data.append([nombre, etiqueta, f"${Decimal(precio):,.2f}"])
    tabla = Table(data, colWidths=[110 * mm, 35 * mm, 30 * mm], repeatRows=1)
    tabla.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    partes.append(tabla)
    doc.build(partes)
    return buf.getvalue()


def importar_xlsx(db: Session, tenant_id: UUID, lista: ListaPrecios, data: bytes) -> dict:
    """Aplica el Excel de ida y vuelta. Devuelve el resumen + errores por fila."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    filas = list(ws.iter_rows(values_only=True))
    if not filas or [str(x or "").strip().upper() for x in filas[0][:5]] != HDR:
        return {"ok": False, "error": "El archivo no trae las columnas esperadas "
                                      f"({' | '.join(HDR)}) — baja el Excel de la lista y edítalo"}

    skus = {}
    for p in db.query(Producto).filter(Producto.tenant_id == tenant_id, Producto.deleted_at.is_(None)):
        skus[(p.sku or "").strip()] = p.id

    existentes = {
        (pr.producto_id, pr.presentacion, Decimal(pr.cantidad_minima)): pr
        for pr in db.query(Precio).filter(Precio.lista_id == lista.id)
    }

    res = {"ok": True, "actualizados": 0, "agregados": 0, "eliminados": 0,
           "sin_cambio": 0, "errores": []}
    import uuid as _uuid
    for i, fila in enumerate(filas[1:], start=2):
        sku = str(fila[0] or "").strip()
        if not sku:
            continue
        pres = str(fila[2] or "").strip().upper() or "KILO"
        try:
            cant = Decimal(str(fila[3] if fila[3] not in (None, "") else 1))
        except InvalidOperation:
            res["errores"].append(f"fila {i}: DESDE CANTIDAD ilegible"); continue
        crudo = fila[4]
        pid = skus.get(sku)
        if pid is None:
            res["errores"].append(f"fila {i}: el SKU {sku} no existe en el catálogo "
                                  "(los productos nuevos se dan de alta en Productos → Importar)")
            continue
        llave = (pid, pres, cant)
        actual = existentes.get(llave)
        if crudo in (None, "") or (isinstance(crudo, (int, float, Decimal)) and Decimal(str(crudo)) == 0):
            if actual is not None:
                db.delete(actual)
                res["eliminados"] += 1
            continue
        try:
            precio = Decimal(str(crudo)).quantize(Decimal("0.0001"))
            if precio < 0:
                raise InvalidOperation
        except InvalidOperation:
            res["errores"].append(f"fila {i}: PRECIO ilegible ({crudo!r})"); continue
        if actual is None:
            db.add(Precio(id=_uuid.uuid4(), tenant_id=tenant_id, lista_id=lista.id,
                          producto_id=pid, presentacion=pres,
                          precio_unitario=precio, cantidad_minima=cant))
            existentes[llave] = None   # evita duplicar si el archivo repite la fila
            res["agregados"] += 1
        elif Decimal(actual.precio_unitario) != precio:
            actual.precio_unitario = precio
            res["actualizados"] += 1
        else:
            res["sin_cambio"] += 1
    db.flush()
    return res

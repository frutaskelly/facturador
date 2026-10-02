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
from .inventario import claves_sae_por_presentacion, presentacion_declarada

HDR = ["SKU", "PRODUCTO", "PRESENTACION", "DESDE CANTIDAD", "PRECIO"]
# Informativa: el import la ignora (sólo valida y lee las cinco de HDR), así
# que un archivo viejo sin ella sigue subiendo igual.
HDR_CLAVE = "CLAVE SAE"
# Sólo en las listas que llevan el SKU de sus clientes (2-oct-2026). Al subir:
# vacía = no cambia; «-» = se le quita; otro valor = ése es su SKU. Se guarda en
# el catálogo de cada cliente de la lista (services/sku_cliente).
HDR_SKU = "SKU CLIENTE"


def clave_sae_de(prod: Producto, presentacion: str) -> str:
    """El artículo de SAE con el que sale esa presentación: la base usa la del
    producto; las demás, la suya (vacía si no tiene — no se inventa la base)."""
    if presentacion == (prod.unidad_base or prod.presentacion_default):
        return prod.clave_sae or ""
    return claves_sae_por_presentacion(prod.presentaciones).get(presentacion.strip().upper(), "")


def _filas(db: Session, lista: ListaPrecios) -> list[tuple]:
    q = (
        db.query(Precio, Producto)
        .join(Producto, Producto.id == Precio.producto_id)
        .filter(Precio.lista_id == lista.id, Producto.deleted_at.is_(None))
        .order_by(Producto.nombre.asc(), Precio.presentacion.asc(), Precio.cantidad_minima.asc())
    )
    return [(prod.sku, prod.nombre, p.presentacion, p.cantidad_minima, p.precio_unitario,
             clave_sae_de(prod, p.presentacion))
            for p, prod in q.all()]


def exportar_xlsx(db: Session, lista: ListaPrecios) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Precios"
    con_sku = bool(lista.lleva_sku_cliente)
    skus: dict = {}
    if con_sku:
        from .sku_cliente import skus_de_lista
        skus = {(r["producto_id"], r["presentacion"]): r for r in skus_de_lista(db, lista.tenant_id, lista)["renglones"]}
        prod_por_sku = {p.sku: p.id for p in db.query(Producto).filter(Producto.tenant_id == lista.tenant_id)}
    ws.append([*HDR, HDR_CLAVE, *([HDR_SKU] if con_sku else [])])
    for sku, nombre, pres, cant, precio, clave in _filas(db, lista):
        fila = [sku, nombre, pres, float(cant), float(precio), clave]
        if con_sku:
            r = skus.get((prod_por_sku.get(sku), (pres or "").upper())) or {}
            fila.append(r.get("sku") or "")
        ws.append(fila)
    # anchos legibles: nadie quiere reacomodar columnas antes de trabajar
    for col, ancho in zip("ABCDEFG", (14, 46, 14, 16, 12, 18, 18)):
        ws.column_dimensions[col].width = ancho
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exportar_pdf(db: Session, lista: ListaPrecios, tenant) -> bytes:
    """La lista con el MEMBRETE del negocio (layout Smart Supply, 29-ago-2026):
    logo + datos fiscales, tabla azul con zebra y folio de página."""
    from .reporte_pdf import CELDA, construir, membrete, tabla_reporte
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph

    filas = []
    for _sku, nombre, pres, cant, precio, _clave in _filas(db, lista):
        etiqueta = pres if cant in (1, Decimal("1")) else f"{pres} (desde {cant})"
        filas.append([Paragraph(nombre, CELDA), etiqueta, f"${Decimal(precio):,.2f}"])
    partes = membrete(tenant, "Lista de precios",
                      f"{lista.nombre} · {len(filas)} productos")
    partes.append(tabla_reporte(
        ["Producto", "Presentación", "Precio"], filas,
        [108 * mm, 38 * mm, 32 * mm], num_cols=(2,),
    ))
    return construir(f"Lista de precios · {lista.nombre}", partes)



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
    prods = {}
    for p in db.query(Producto).filter(Producto.tenant_id == tenant_id, Producto.deleted_at.is_(None)):
        skus[(p.sku or "").strip()] = p.id
        prods[p.id] = p

    existentes = {
        (pr.producto_id, pr.presentacion, Decimal(pr.cantidad_minima)): pr
        for pr in db.query(Precio).filter(Precio.lista_id == lista.id)
    }

    res = {"ok": True, "actualizados": 0, "agregados": 0, "eliminados": 0,
           "sin_cambio": 0, "errores": [], "skus_guardados": 0, "avisos": []}
    encabezados = [str(x or "").strip().upper() for x in filas[0]]
    col_sku = encabezados.index(HDR_SKU) if (lista.lleva_sku_cliente and HDR_SKU in encabezados) else None
    pendientes_sku: list[tuple] = []
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
        # Misma regla que el alta por pantalla: un precio en una presentación
        # que el producto no declara no lo cobra nadie, y aquí llegan archivos
        # editados a mano donde escribir CAJA en vez de KILO es un teclazo.
        if not presentacion_declarada(prods.get(pid), pres):
            res["errores"].append(
                f"fila {i}: {prods[pid].nombre} no maneja la presentación {pres} "
                "(agrégasela al producto, con cuántas unidades base trae)")
            continue
        if col_sku is not None and col_sku < len(fila) and str(fila[col_sku] or "").strip():
            pendientes_sku.append((i, pid, pres, str(fila[col_sku]).strip()))
        llave = (pid, pres, cant)
        actual = existentes.get(llave)
        # El precio se interpreta UNA sola vez: «0» tecleado como texto es el
        # mismo cero que el numérico (quitar el renglón), no un precio de $0
        # — el archivo viene editado a mano y Excel guarda texto sin avisar.
        if crudo in (None, ""):
            precio = None
        else:
            try:
                precio = Decimal(str(crudo)).quantize(Decimal("0.0001"))
                if precio < 0:
                    raise InvalidOperation
            except InvalidOperation:
                res["errores"].append(f"fila {i}: PRECIO ilegible ({crudo!r})"); continue
        if precio is None or precio == 0:
            if actual is not None:
                db.delete(actual)
                del existentes[llave]
                res["eliminados"] += 1
            continue
        if actual is None:
            nuevo = Precio(id=_uuid.uuid4(), tenant_id=tenant_id, lista_id=lista.id,
                           producto_id=pid, presentacion=pres,
                           precio_unitario=precio, cantidad_minima=cant)
            db.add(nuevo)
            # El OBJETO, no un centinela: si el archivo repite la fila, la
            # segunda pasada debe encontrarlo y actualizar — guardar None aquí
            # hacía que la repetida insertara un renglón DUPLICADO.
            existentes[llave] = nuevo
            res["agregados"] += 1
        elif Decimal(actual.precio_unitario) != precio:
            actual.precio_unitario = precio
            res["actualizados"] += 1
        else:
            res["sin_cambio"] += 1
    db.flush()
    if pendientes_sku:
        from fastapi import HTTPException

        from .sku_cliente import clientes_de_lista, guardar_sku
        clientes = clientes_de_lista(db, tenant_id, lista.id)
        if not clientes:
            res["errores"].append("La columna SKU CLIENTE no se guardó: la lista no tiene clientes asignados")
        else:
            vistos = set()
            for i, pid, pres, valor in pendientes_sku:
                if (pid, pres) in vistos:      # «DESDE CANTIDAD» repite el renglón
                    continue
                vistos.add((pid, pres))
                try:
                    r = guardar_sku(db, tenant_id, lista, pid, pres, None if valor == "-" else valor, None,
                                    clientes=clientes, avisar=True)
                except HTTPException as exc:
                    res["errores"].append(f"fila {i}: {exc.detail}")
                    continue
                res["skus_guardados"] += 1
                res["avisos"].extend(f"fila {i}: {a}" for a in r["avisos"])
    return res

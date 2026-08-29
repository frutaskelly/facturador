"""Cotizador de documentos — el «WhatsApp cotizador» dentro del Facturador.

Pedido del dueño (29-ago-2026): la pantalla /cotizador debe hacer lo que hace
el bot con las requisiciones de Balles/Jubran — subes un PDF, una foto o un
Excel con lo que el cliente quiere, y sale la cotización de TODOS los renglones
con los precios QUE A ESE CLIENTE le tocan (sus listas, sus proyectos, sus
precios especiales). El acceso se le dará al cliente para revisar sus costos.

Tubería: extracción con IA (misma mecánica que el wizard de productos: Excel
como texto tabular, PDF como documento, foto como imagen) → cruce contra el
catálogo (primero la CLAVE del propio cliente, luego alias/exacto/difuso) →
precio por resolver_precio con las dimensiones del cliente → desglose fiscal
con el MISMO cerebro de remisiones y facturas (calcular_linea_producto).

Lo que no cruza con confianza NO se cotiza a ciegas: sale en `sin_cruce` con
sus candidatos — cotizar el producto equivocado es peor que decir "este
renglón revísalo".
"""
from __future__ import annotations

import base64
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Cliente, EsquemaImpuesto, Precio, PrecioOverride, Producto, ProductoCliente, Sucursal
from . import producto_match
from .fiscal import calcular_linea_producto
from .importar_productos import _MIME_POR_EXT, _tabla_a_texto
from .precios import listas_asignadas_a_cliente, resolver_precio

logger = logging.getLogger(__name__)


class CotizadorError(Exception):
    pass


_SYSTEM = (
    "Eres el lector de órdenes de compra y requisiciones de una distribuidora "
    "de alimentos mexicana. Extrae LOS RENGLONES PEDIDOS del documento: una "
    "partida por renglón, en el orden del documento, sin fusionar ni saltar "
    "renglones parecidos. `descripcion` es el producto tal como lo escribió el "
    "cliente; `cantidad` es lo pedido (número); `unidad` la unidad si la dice "
    "(KILO, PIEZA, CAJA, MANOJO…); `clave` el código del producto si el "
    "documento lo trae. Ignora encabezados, totales, firmas y datos fiscales."
)

_TOOL = {
    "name": "registrar_partidas",
    "description": "Registra las partidas pedidas en el documento.",
    "input_schema": {
        "type": "object",
        "required": ["partidas"],
        "properties": {"partidas": {"type": "array", "items": {
            "type": "object",
            "required": ["descripcion", "cantidad"],
            "properties": {
                "descripcion": {"type": "string"},
                "cantidad": {"type": "number"},
                "unidad": {"type": "string"},
                "clave": {"type": "string"},
            },
        }}},
    },
}

# Las mismas traducciones de unidad que usa la bandeja.
_UNIDAD_ALIAS = {
    "KG": "KILO", "KGS": "KILO", "KILOS": "KILO", "KGM": "KILO",
    "PZ": "PIEZA", "PZA": "PIEZA", "PZAS": "PIEZA", "PIEZAS": "PIEZA", "H87": "PIEZA",
    "CJ": "CAJA", "CJA": "CAJA", "CAJAS": "CAJA", "BTO": "BULTO", "BULTOS": "BULTO",
    "LT": "LITRO", "LTS": "LITRO", "LITROS": "LITRO", "MJO": "MANOJO", "MANOJOS": "MANOJO",
}


def extraer_partidas(data: bytes, filename: str) -> list[dict]:
    """PDF/foto/Excel → [{descripcion, cantidad, unidad, clave}] vía Claude."""
    if not settings.ANTHROPIC_API_KEY:
        raise CotizadorError("La lectura de documentos no está configurada (falta la IA)")
    import anthropic

    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("xlsx", "xls", "csv"):
        content: list[dict] = [{"type": "text", "text":
            "Orden de compra (texto tabular, columnas separadas por tabulador):\n\n"
            + _tabla_a_texto(data, filename)}]
    elif ext == "pdf":
        content = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.standard_b64encode(data).decode()}},
            {"type": "text", "text": "Extrae las partidas pedidas en esta orden."},
        ]
    elif ext in _MIME_POR_EXT:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": _MIME_POR_EXT[ext],
                                         "data": base64.standard_b64encode(data).decode()}},
            {"type": "text", "text": "Extrae las partidas pedidas en esta orden."},
        ]
    else:
        raise CotizadorError("Formato no soportado: sube un PDF, una foto (JPG/PNG) o un Excel/CSV")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        with client.messages.stream(
            model=settings.SAT_AI_MODEL,
            max_tokens=16000,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "registrar_partidas"},
            messages=[{"role": "user", "content": content}],
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.APIError as exc:
        logger.warning("cotizador: extracción IA falló: %s", exc)
        raise CotizadorError("No se pudo leer el documento en este momento; intenta de nuevo") from exc

    partidas = []
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "registrar_partidas":
            for p in (block.input.get("partidas") or []):
                if not isinstance(p, dict):
                    continue
                desc = str(p.get("descripcion") or "").strip()
                try:
                    cant = Decimal(str(p.get("cantidad")))
                except (InvalidOperation, TypeError):
                    continue
                if desc and cant > 0:
                    partidas.append({
                        "descripcion": desc[:254],
                        "cantidad": cant,
                        "unidad": str(p.get("unidad") or "").strip()[:20],
                        "clave": str(p.get("clave") or "").strip()[:60] or None,
                    })
    if not partidas:
        raise CotizadorError("No se encontraron partidas legibles en el documento")
    return partidas


def _norm_codigo(v: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.upper() if ch.isalnum() or ch == "-")


def _presentacion_para(prod: Producto, unidad: str, preferida: Optional[str]) -> str:
    pres = list((prod.presentaciones or {}).keys())
    if preferida and any(k.upper() == preferida.upper() for k in pres):
        return preferida
    u = (unidad or "").strip().upper()
    norm = _UNIDAD_ALIAS.get(u, u)
    hit = next((k for k in pres if k.upper() == norm), None)
    return hit or prod.presentacion_default or prod.unidad_base or (pres[0] if pres else "KILO")


def productos_cotizables(db: Session, tenant_id: UUID, cliente_id: UUID) -> Optional[set[UUID]]:
    """Los productos que ESTE cliente puede cotizar (regla del dueño,
    29-ago-2026: "únicamente se puede cotizar los productos que estén en la
    lista de precios del cliente").

    = los que tienen precio en sus listas negociadas, más los que tienen
    precio especial (override) del cliente o de sus sucursales. None cuando el
    cliente NO tiene negociación alguna: compra a lista base y no hay lista
    propia que lo limite.
    """
    listas = listas_asignadas_a_cliente(db, cliente_id)
    ids: set[UUID] = set()
    if listas:
        ids.update(
            p for (p,) in db.query(Precio.producto_id)
            .filter(Precio.tenant_id == tenant_id, Precio.lista_id.in_(listas))
            .distinct()
        )
    sucs = [s for (s,) in db.query(Sucursal.id).filter(
        Sucursal.tenant_id == tenant_id, Sucursal.cliente_id == cliente_id,
        Sucursal.deleted_at.is_(None))]
    from sqlalchemy import or_ as _or
    ovr = db.query(PrecioOverride.producto_id).filter(
        PrecioOverride.tenant_id == tenant_id,
        _or(
            PrecioOverride.cliente_id == cliente_id,
            PrecioOverride.sucursal_id.in_(sucs or [None]),
        ),
    ).distinct()
    overrides = {p for (p,) in ovr}
    if not listas and not overrides:
        return None
    return ids | overrides


def cotizar_documento(
    db: Session,
    tenant_id: UUID,
    *,
    cliente_id: UUID,
    data: bytes,
    filename: str,
    sucursal_id: Optional[UUID] = None,
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
) -> dict:
    cliente = (
        db.query(Cliente).filter(Cliente.id == cliente_id, Cliente.deleted_at.is_(None)).one_or_none()
    )
    if cliente is None:
        raise CotizadorError("Ese cliente no existe")

    partidas = extraer_partidas(data, filename)

    # Cruce: la CLAVE del cliente decide al 100; lo demás va por la cascada
    # normal (alias del cliente → alias global → exacto → difuso).
    codigos: dict[str, Optional[UUID]] = {}
    presentacion_cliente: dict[UUID, str] = {}
    for pc in db.query(ProductoCliente).filter(
        ProductoCliente.tenant_id == tenant_id,
        ProductoCliente.cliente_id == cliente_id,
        ProductoCliente.codigo_cliente.isnot(None),
    ):
        cod = _norm_codigo(pc.codigo_cliente)
        codigos[cod] = None if (cod in codigos and codigos[cod] != pc.producto_id) else (
            codigos.get(cod) or pc.producto_id)
        if pc.presentacion:
            presentacion_cliente[pc.producto_id] = pc.presentacion

    prods = producto_match.productos_activos(db)
    por_id = {p.id: p for p in prods}
    esquemas = {e.id: e for e in db.query(EsquemaImpuesto).filter(
        EsquemaImpuesto.tenant_id == tenant_id, EsquemaImpuesto.deleted_at.is_(None))}
    # Solo se cotiza lo que está en la lista del cliente (None = sin negociación,
    # todo el catálogo a lista base).
    permitidos = productos_cotizables(db, tenant_id, cliente_id)

    lineas, sin_cruce = [], []
    subtotal = iva = ieps = Decimal("0")
    for pt in partidas:
        prod = None
        via = None
        if pt["clave"]:
            pid = codigos.get(_norm_codigo(pt["clave"]))
            if pid:
                prod = por_id.get(pid)
                via = "su clave"
        if prod is None:
            cands = producto_match.buscar(db, tenant_id, pt["descripcion"], limit=3,
                                          prods=prods, unidad=pt["unidad"] or None)
            fuerte = cands[0] if cands and cands[0].score >= 96 else None
            if fuerte:
                prod = por_id.get(fuerte.producto_id)
                via = fuerte.origen
            else:
                sin_cruce.append({
                    "descripcion": pt["descripcion"], "cantidad": str(pt["cantidad"]),
                    "unidad": pt["unidad"], "clave": pt["clave"],
                    "candidatos": [{"nombre": c.nombre, "score": c.score} for c in cands[:3]],
                })
                continue

        if permitidos is not None and prod.id not in permitidos:
            # Cruza, pero NO está en la lista del cliente: no se cotiza a
            # precio base a escondidas — se reporta tal cual.
            sin_cruce.append({
                "descripcion": pt["descripcion"], "cantidad": str(pt["cantidad"]),
                "unidad": pt["unidad"], "clave": pt["clave"],
                "candidatos": [],
                "motivo": f"{prod.nombre} no está en la lista de precios del cliente",
            })
            continue

        presentacion = _presentacion_para(prod, pt["unidad"], presentacion_cliente.get(prod.id))
        cot = resolver_precio(
            db, producto_id=prod.id, presentacion=presentacion, cantidad=pt["cantidad"],
            cliente_id=cliente_id, sucursal_id=sucursal_id, serie_id=serie_id,
            proyecto_id=proyecto_id,
        )
        precio = Decimal(str(cot["precio"])) if cot else None
        importe = (precio * pt["cantidad"]).quantize(Decimal("0.01")) if precio is not None else None
        fila = {
            "descripcion": pt["descripcion"], "cantidad": str(pt["cantidad"]),
            "unidad": pt["unidad"], "clave": pt["clave"],
            "producto_id": str(prod.id), "producto_nombre": prod.nombre, "sku": prod.sku,
            "presentacion": presentacion, "cruce": via,
            "precio_unitario": str(precio) if precio is not None else None,
            "importe": str(importe) if importe is not None else None,
            "origen_precio": (cot or {}).get("origen"),
        }
        if precio is not None:
            calc = calcular_linea_producto(prod, esquemas.get(prod.esquema_impuesto_id), importe, pt["cantidad"])
            fila["iva_importe"] = str(calc["iva_importe"])
            fila["ieps_importe"] = str(calc["ieps_importe"])
            subtotal += importe
            iva += Decimal(str(calc["iva_importe"]))
            ieps += Decimal(str(calc["ieps_importe"]))
        lineas.append(fila)

    return {
        "cliente_id": str(cliente_id), "cliente_nombre": cliente.legal_name,
        "archivo": filename, "lineas": lineas, "sin_cruce": sin_cruce,
        "sin_precio": sum(1 for l in lineas if l["precio_unitario"] is None),
        "subtotal": str(subtotal.quantize(Decimal("0.01"))),
        "iva": str(iva.quantize(Decimal("0.01"))),
        "ieps": str(ieps.quantize(Decimal("0.01"))),
        "total": str((subtotal + iva + ieps).quantize(Decimal("0.01"))),
    }


def cotizacion_pdf(tenant, cot: dict) -> bytes:
    """La cotización como PDF para el cliente, con el membrete del negocio
    (layout Smart Supply: logo + datos fiscales, tabla azul, folio de página)."""
    from datetime import date

    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer

    from .reporte_pdf import CELDA, construir, membrete, tabla_reporte

    filas = []
    for l in cot.get("lineas", []):
        if l.get("precio_unitario") is None:
            continue
        filas.append([Paragraph(l["producto_nombre"], CELDA), l["cantidad"], l["presentacion"],
                      f"${Decimal(l['precio_unitario']):,.2f}", f"${Decimal(l['importe']):,.2f}"])
    totales = [["", "", "", "Subtotal", f"${Decimal(cot['subtotal']):,.2f}"]]
    if Decimal(cot.get("ieps", "0")):
        totales.append(["", "", "", "IEPS", f"${Decimal(cot['ieps']):,.2f}"])
    totales.append(["", "", "", "IVA", f"${Decimal(cot['iva']):,.2f}"])
    totales.append(["", "", "", "Total", f"${Decimal(cot['total']):,.2f}"])

    partes = membrete(
        tenant, "Cotización",
        f"{cot.get('cliente_nombre') or ''} · {date.today():%d/%m/%Y} · {len(filas)} partidas")
    partes.append(tabla_reporte(
        ["Producto", "Cant.", "Present.", "Precio", "Importe"],
        filas + totales,
        [86 * mm, 18 * mm, 26 * mm, 24 * mm, 28 * mm],
        num_cols=(1, 3, 4), filas_totales=len(totales),
    ))
    if cot.get("sin_cruce"):
        from reportlab.lib.styles import getSampleStyleSheet

        partes.append(Spacer(1, 8))
        partes.append(Paragraph(
            "Renglones no cotizados (revisar a mano): "
            + "; ".join(x["descripcion"] for x in cot["sin_cruce"][:10]),
            getSampleStyleSheet()["Normal"]))
    return construir("Cotización", partes)

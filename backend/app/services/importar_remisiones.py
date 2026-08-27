"""Importación masiva: un Excel → varias remisiones.

Se reconocen DOS layouts, sin que el usuario tenga que elegir nada:

1. **SAE ASPEL** (hojas "Facturas"/"Pedidos"):
   FOLIO | CLIENTE | FECHA | SU PEDIDO | CLAVE | CANTIDAD | PRECIO | Observaciones
   (las columnas extra de factura — método/forma/uso CFDI — se toleran y se
   ignoran: la factura posterior toma los defaults del cliente).

2. **Master Ordenes** (hoja "Master", el concentrado de órdenes de compra del
   usuario): Folio | Requisicion Folio | RFC Cliente | Nombre Cliente | Fecha |
   Referencia | Cantidad | Unidad | Clave | Descripcion | Costo unitario |
   Observacion del documento | Entregar Bodega | … Cada FOLIO del master es una
   orden que se remisiona tal cual.

Reglas clave (dolores del usuario con SAE):
- Las filas se AGRUPAN por FOLIO del archivo, pero ese folio es solo una
  referencia: el folio real lo asigna el sistema con su serie (contador
  atómico) — nadie captura folios ni pueden chocar.
- Los folios se normalizan SIN ceros a la izquierda ni espacios
  ("  0000001230 " → "1230").

El parseo es determinista (encabezados tolerantes), sin IA: ambos archivos
tienen estructura fija. El cruce de cliente (código, RFC o nombre) y producto
(por CLAVE/SKU) se resuelve aquí; lo no cruzado se decide en el preview de la UI.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional


class ImportError_(Exception):
    """Error de formato del archivo, con mensaje para el usuario."""


def _norm_header(h) -> str:
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", s.upper())


# Encabezado normalizado → campo. Tolera variantes de los dos layouts
# ("PREC"/"PRECIO"/"COSTOUNITARIO", "CLIENTE"/"NOMBRECLIENTE", …).
_HEADERS = {
    # comunes
    "FOLIO": "folio",
    "FECHA": "fecha",
    "CLAVE": "clave",
    "CANTIDAD": "cantidad",
    # SAE
    "CLIENTE": "cliente",
    "SUPEDIDO": "su_pedido",
    "PREC": "precio",
    "PRECIO": "precio",
    "OBSERVACIONES": "observaciones",
    # Master Ordenes
    "NOMBRECLIENTE": "cliente",
    "RFCCLIENTE": "cliente_rfc",
    "REQUISICIONFOLIO": "requisicion",
    "ENTREGARBODEGA": "entregar_bodega",
    "REFERENCIA": "su_pedido",
    "COSTOUNITARIO": "precio",
    "DESCRIPCION": "descripcion",
    "UNIDAD": "unidad",
    "OBSERVACIONDELDOCUMENTO": "observaciones",
}

# Hojas preferidas cuando el libro trae varias (el master arrastra "Summary"
# y "Totales", que no son renglones de orden).
_HOJAS = ("MASTER", "FACTURAS", "PEDIDOS", "REMISIONES")


def _texto(v) -> Optional[str]:
    """Celda → texto limpio; None para vacíos y el 'nan' de pandas."""
    s = str(v or "").strip()
    return None if not s or s.lower() == "nan" else s


def normalizar_folio(v) -> str:
    """'  0000001230 ' → '1230'; 'ZHGO202' se queda igual (folio con serie)."""
    s = str(v or "").strip()
    if re.fullmatch(r"0*\d+", s):
        return s.lstrip("0") or "0"
    return s


def normalizar_nombre(v) -> str:
    """Nombre de cliente comparable: sin acentos, sin razón social ni puntuación.

    'Distribuidora de Alimentos, S.A. de C.V.' → 'DISTRIBUIDORA DE ALIMENTOS'.
    """
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]", " ", s.upper())
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(
        r"\b(S\s?A\s?(DE\s?C\s?V)?|S\s?DE\s?R\s?L(\s?DE\s?C\s?V)?|SAPI(\s?DE\s?C\s?V)?|SC|AC)\b\s*$",
        "", s,
    ).strip()
    return s


def _decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s or s.lower() == "nan":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _fecha(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Serial de Excel (el master exporta la fecha como número: 46209 = 2026-07-06).
    if re.fullmatch(r"\d{5}", s):
        return date(1899, 12, 30) + timedelta(days=int(s))
    return None


def _leer_hoja(data: bytes, filename: str):
    """Abre el libro y devuelve la hoja de renglones (Master/Facturas/… o la 1ª)."""
    import pandas as pd

    try:
        libro = pd.ExcelFile(io.BytesIO(data))
        hoja = next(
            (h for h in libro.sheet_names if _norm_header(h) in _HOJAS),
            libro.sheet_names[0],
        )
        return libro.parse(hoja, header=0, dtype=str)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — el error de la lib es críptico
        raise ImportError_(
            f"No se pudo leer el archivo '{filename}'. ¿Es un Excel (.xls/.xlsx)?"
        ) from exc


def parsear_excel(data: bytes, filename: str) -> list[dict]:
    """Archivo SAE o Master Ordenes (.xls/.xlsx) → filas planas {folio, cliente,
    cliente_rfc, fecha, su_pedido, clave, descripcion, unidad, cantidad, precio,
    observaciones}. Lanza ImportError_ con mensaje claro si el formato no se
    reconoce."""
    df = _leer_hoja(data, filename)

    cols: dict[int, str] = {}
    for i, h in enumerate(df.columns):
        campo = _HEADERS.get(_norm_header(h))
        if campo and campo not in cols.values():
            cols[i] = campo
    requeridas = {"folio", "clave", "cantidad"}
    faltan = requeridas - set(cols.values())
    if "cliente" not in cols.values() and "cliente_rfc" not in cols.values():
        faltan.add("cliente")
    if faltan:
        raise ImportError_(
            "El archivo no trae las columnas esperadas (formato SAE o Master "
            "Ordenes): faltan " + ", ".join(sorted(f.upper() for f in faltan))
        )

    filas: list[dict] = []
    for _, row in df.iterrows():
        r = {campo: row.iloc[i] for i, campo in cols.items()}
        folio = normalizar_folio(_texto(r.get("folio")) or "")
        clave = _texto(r.get("clave")) or ""
        cantidad = _decimal(r.get("cantidad"))
        if not folio or not clave or cantidad is None or cantidad <= 0:
            continue  # filas vacías / totales / basura
        filas.append({
            "folio": folio,
            "cliente": _texto(r.get("cliente")) or "",
            "cliente_rfc": _texto(r.get("cliente_rfc")),
            "requisicion": normalizar_folio(_texto(r.get("requisicion")) or "") or None,
            "entregar_bodega": _texto(r.get("entregar_bodega")),
            "fecha": _fecha(r.get("fecha")),
            "su_pedido": _texto(r.get("su_pedido")),
            "clave": clave,
            "descripcion": _texto(r.get("descripcion")),
            "unidad": _texto(r.get("unidad")),
            "cantidad": cantidad,
            "precio": _decimal(r.get("precio")),
            "observaciones": _texto(r.get("observaciones")),
        })
    if not filas:
        raise ImportError_("El archivo no tiene filas con datos (folio, clave y cantidad)")
    return filas


def agrupar_por_folio(filas: list[dict]) -> list[dict]:
    """Filas → grupos (una remisión por FOLIO del archivo), preservando orden."""
    grupos: dict[str, dict] = {}
    for f in filas:
        g = grupos.get(f["folio"])
        if g is None:
            g = grupos[f["folio"]] = {
                "folio_ref": f["folio"],
                "cliente": f["cliente"],
                "cliente_rfc": f.get("cliente_rfc"),
                "requisicion": f.get("requisicion"),
                "entregar_bodega": f.get("entregar_bodega"),
                "fecha": f["fecha"],
                "su_pedido": f["su_pedido"],
                "observaciones": f["observaciones"],
                "lineas": [],
            }
        g["lineas"].append({
            "clave": f["clave"],
            "descripcion": f.get("descripcion"),
            "unidad": f.get("unidad"),
            "cantidad": f["cantidad"],
            "precio": f["precio"],
        })
        # Un su_pedido/observación en cualquier fila del grupo completa el dato.
        g["cliente"] = g["cliente"] or f["cliente"]
        g["cliente_rfc"] = g["cliente_rfc"] or f.get("cliente_rfc")
        g["requisicion"] = g["requisicion"] or f.get("requisicion")
        g["entregar_bodega"] = g["entregar_bodega"] or f.get("entregar_bodega")
        g["su_pedido"] = g["su_pedido"] or f["su_pedido"]
        g["observaciones"] = g["observaciones"] or f["observaciones"]
        g["fecha"] = g["fecha"] or f["fecha"]
    return list(grupos.values())

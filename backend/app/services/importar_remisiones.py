"""Importación masiva estilo SAE ASPEL: un Excel → varias remisiones.

Formato de referencia (archivos reales del usuario, hojas "Facturas"/"Pedidos"):
FOLIO | CLIENTE | FECHA | SU PEDIDO | CLAVE | CANTIDAD | PRECIO | Observaciones
(las columnas extra de factura — método/forma/uso CFDI — se toleran y se ignoran:
la factura posterior toma los defaults del cliente).

Reglas clave (dolores del usuario con SAE):
- Las filas se AGRUPAN por FOLIO del archivo, pero ese folio es solo una
  referencia: el folio real lo asigna el sistema con su serie (contador
  atómico) — nadie captura folios ni pueden chocar.
- Los folios SAE se normalizan SIN ceros a la izquierda ni espacios
  ("  0000001230 " → "1230").

El parseo es determinista (encabezados tolerantes), sin IA: el archivo SAE
tiene estructura fija. El cruce de cliente (por código) y producto (por CLAVE/
SKU) se resuelve aquí; lo no cruzado se decide en el preview de la UI.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional


class ImportError_(Exception):
    """Error de formato del archivo, con mensaje para el usuario."""


def _norm_header(h) -> str:
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", s.upper())


# Encabezado normalizado → campo. Tolera variantes ("PREC", "PRECIO", "OBS…").
_HEADERS = {
    "FOLIO": "folio",
    "CLIENTE": "cliente",
    "FECHA": "fecha",
    "SUPEDIDO": "su_pedido",
    "CLAVE": "clave",
    "CANTIDAD": "cantidad",
    "PREC": "precio",
    "PRECIO": "precio",
    "OBSERVACIONES": "observaciones",
}


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
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parsear_excel(data: bytes, filename: str) -> list[dict]:
    """Archivo SAE (.xls/.xlsx) → filas planas {folio, cliente, fecha, su_pedido,
    clave, cantidad, precio, observaciones}. Lanza ImportError_ con mensaje claro
    si el formato no se reconoce."""
    import pandas as pd

    try:
        df = pd.read_excel(io.BytesIO(data), sheet_name=0, header=0, dtype=str)
    except Exception as exc:  # noqa: BLE001 — el error de la lib es críptico
        raise ImportError_(
            f"No se pudo leer el archivo '{filename}'. ¿Es un Excel (.xls/.xlsx)?"
        ) from exc

    cols: dict[int, str] = {}
    for i, h in enumerate(df.columns):
        campo = _HEADERS.get(_norm_header(h))
        if campo and campo not in cols.values():
            cols[i] = campo
    requeridas = {"folio", "cliente", "clave", "cantidad"}
    faltan = requeridas - set(cols.values())
    if faltan:
        raise ImportError_(
            "El archivo no trae las columnas esperadas (formato SAE): faltan "
            + ", ".join(sorted(f.upper() for f in faltan))
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
            "fecha": _fecha(r.get("fecha")),
            "su_pedido": _texto(r.get("su_pedido")),
            "clave": clave,
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
                "fecha": f["fecha"],
                "su_pedido": f["su_pedido"],
                "observaciones": f["observaciones"],
                "lineas": [],
            }
        g["lineas"].append({
            "clave": f["clave"],
            "cantidad": f["cantidad"],
            "precio": f["precio"],
        })
        # Un su_pedido/observación en cualquier fila del grupo completa el dato.
        g["su_pedido"] = g["su_pedido"] or f["su_pedido"]
        g["observaciones"] = g["observaciones"] or f["observaciones"]
        g["fecha"] = g["fecha"] or f["fecha"]
    return list(grupos.values())

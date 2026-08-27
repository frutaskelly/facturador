"""Importación masiva de productos: plantilla propia o lista de precios ajena.

Dos caminos, un mismo resultado (filas estructuradas para el preview):

1. **Plantilla** (xlsx/csv con nuestros encabezados) → parseo determinista, sin
   IA. Es el formato que descarga el usuario desde /productos.
2. **Cualquier lista de precios** (Excel/CSV con otro acomodo, PDF o foto) → la
   IA extrae {nombre, codigo, descripcion, unidad, precio} y de paso sugiere
   clave/unidad SAT por fila (una sola llamada, no una por producto).

El cruce contra el catálogo (para NO duplicar productos) y el alta viven en el
router; aquí solo archivo → filas.
"""
from __future__ import annotations

import base64
import io
import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..core.config import settings

logger = logging.getLogger(__name__)

MAX_FILAS = 2000         # tope del camino determinista (plantilla/SAE)
MAX_FILAS_IA = 500       # tope del camino IA (el output de la extracción cuesta)

# Encabezados de la plantilla oficial (en este orden).
PLANTILLA_COLUMNAS = [
    ("NOMBRE", "Nombre del producto (obligatorio)"),
    ("CODIGO", "Código del cliente o SKU deseado (opcional)"),
    ("DESCRIPCION", "Descripción (opcional)"),
    ("UNIDAD", "Unidad de venta: KILO, PIEZA, CAJA… (opcional)"),
    ("PRECIO", "Precio de venta (opcional)"),
    ("CLAVE_SAT", "Clave SAT c_ClaveProdServ, 8 dígitos (opcional)"),
    ("UNIDAD_SAT", "Clave SAT de unidad: KGM, H87, XBX… (opcional)"),
    ("CODIGO_BARRAS", "Código de barras EAN/GTIN (opcional)"),
]


class ImportProductosError(Exception):
    """Error de formato/lectura con mensaje para el usuario."""


def _norm_header(h) -> str:
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", s.upper())


# Encabezado normalizado → campo (tolera variantes).
_HEADERS = {
    "NOMBRE": "nombre",
    "PRODUCTO": "nombre",
    "DESCRIPCION": "descripcion",
    "CODIGO": "codigo",
    "CLAVE": "codigo",
    "CLAVESAE": "codigo",       # exports de SAE ASPEL
    "SKU": "codigo",
    "UNIDAD": "unidad",
    "PRESENTACION": "unidad",
    "PRECIO": "precio",
    "CLAVESAT": "clave_sat",
    "UNIDADSAT": "unidad_sat",
    "CODIGOBARRAS": "codigo_barras",
}


def _texto(v) -> str:
    s = str(v if v is not None else "").strip()
    return "" if s.lower() in ("", "nan", "none") else s


def _decimal(v) -> Optional[Decimal]:
    s = _texto(v).replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        d = Decimal(s)
        return d if d >= 0 else None
    except InvalidOperation:
        return None


# Variantes de unidad de venta → unidad canónica del sistema.
_UNIDADES_CANON = {
    "KILOGRAMO": "KILO", "KILOGRAMOS": "KILO", "KILOS": "KILO", "KG": "KILO", "KGS": "KILO",
    "PZ": "PIEZA", "PZA": "PIEZA", "PZAS": "PIEZA", "PIEZAS": "PIEZA", "PZS": "PIEZA",
    "LT": "LITRO", "LTS": "LITRO", "L": "LITRO", "LITROS": "LITRO",
    "ML": "MILILITRO", "MILILITROS": "MILILITRO",
    "GR": "GRAMO", "GRS": "GRAMO", "GRAMOS": "GRAMO", "G": "GRAMO",
    "MJ": "MANOJO", "MANOJOS": "MANOJO",
    "CJ": "CAJA", "CJA": "CAJA", "CAJAS": "CAJA",
    "PAQ": "PAQUETE", "PAQUETES": "PAQUETE",
    "BOLSAS": "BOLSA", "COSTALES": "COSTAL", "BULTOS": "BULTO",
    "DOC": "DOCENA", "DOCENAS": "DOCENA", "MALLAS": "MALLA", "REJAS": "REJA",
}


def normalizar_unidad(u: str) -> str:
    s = (u or "").strip().upper()
    return _UNIDADES_CANON.get(s, s)


def _leer_tabla(data: bytes, filename: str):
    """xlsx/xls/csv → DataFrame de textos (header en la fila 0)."""
    import pandas as pd

    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    try:
        if ext == "csv":
            return pd.read_csv(io.BytesIO(data), header=0, dtype=str, sep=None, engine="python")
        return pd.read_excel(io.BytesIO(data), sheet_name=0, header=0, dtype=str)
    except Exception as exc:  # noqa: BLE001 — el error de la lib es críptico
        raise ImportProductosError(
            f"No se pudo leer el archivo '{filename}'. ¿Es un Excel (.xlsx/.xls) o CSV?"
        ) from exc


def parsear_plantilla(data: bytes, filename: str) -> Optional[list[dict]]:
    """Camino determinista: si el archivo trae al menos la columna NOMBRE (o
    PRODUCTO) en el encabezado, se parsea sin IA. Devuelve None si el archivo
    no se parece a la plantilla (→ probar con IA)."""
    df = _leer_tabla(data, filename)

    cols: dict[int, str] = {}
    for i, h in enumerate(df.columns):
        campo = _HEADERS.get(_norm_header(h))
        if campo and campo not in cols.values():
            cols[i] = campo
    if "nombre" not in cols.values():
        # Exports estilo SAE: no hay columna NOMBRE, pero la DESCRIPCION es el
        # nombre del producto (Linea | Clave SAE | Descripcion | Unidad | Precio).
        desc_idx = next((i for i, c in cols.items() if c == "descripcion"), None)
        if desc_idx is not None:
            cols[desc_idx] = "nombre"
        else:
            return None

    filas: list[dict] = []
    for _, row in df.iterrows():
        r = {campo: row.iloc[i] for i, campo in cols.items()}
        nombre = _texto(r.get("nombre"))
        if not nombre:
            continue  # filas vacías / totales
        precio = _decimal(r.get("precio"))
        filas.append({
            "nombre": nombre,
            "codigo": _texto(r.get("codigo")),
            "descripcion": _texto(r.get("descripcion")),
            "unidad": normalizar_unidad(_texto(r.get("unidad"))),
            "precio": str(precio) if precio is not None else "",
            "clave_sat": re.sub(r"\D", "", _texto(r.get("clave_sat")))[:8],
            "unidad_sat": _texto(r.get("unidad_sat")).upper()[:3],
            "codigo_barras": _texto(r.get("codigo_barras")),
        })
        if len(filas) > MAX_FILAS:
            raise ImportProductosError(f"Máximo {MAX_FILAS} productos por archivo")
    if not filas:
        raise ImportProductosError("El archivo no tiene filas con NOMBRE de producto")
    return filas


# ─── Extracción con IA (lista de precios en cualquier formato) ───────────────

_MIME_POR_EXT = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

_SYSTEM_EXTRACT = """\
Eres un asistente que convierte LISTAS DE PRECIOS de frutas, verduras y \
abarrotes (en cualquier acomodo: Excel pegado, PDF o foto) en filas \
estructuradas de productos para darlos de alta en un catálogo mexicano (CFDI 4.0).

Reglas:
- Una fila por producto. Ignora encabezados, títulos, totales, notas y filas vacías.
- `nombre`: el nombre del producto tal como viene (limpio de espacios dobles).
- `codigo`: el código/clave del producto SI la lista lo trae (p. ej. \
"JIT-SAD-001", "A0125"); vacío si no hay.
- `descripcion`: presentación/marca/detalle extra si viene aparte del nombre.
- `unidad`: unidad de venta si se distingue (KILO, PIEZA, CAJA, BULTO, LITRO, \
MANOJO…); vacío si no.
- `precio`: número sin símbolo (1234.50); vacío si la lista no trae precio.
- `clave_sat`: tu mejor sugerencia de c_ClaveProdServ (8 dígitos exactos) para \
ese producto. Frutas frescas 503xxxxx, verduras frescas 504xxxxx, abarrotes \
50xxxxxx; si dudas usa la categoría más cercana.
- `unidad_sat`: KGM (kilogramo), H87 (pieza), XBX (caja), LTR (litro), \
GRM (gramo), XPK (paquete), XBG (bolsa) — según la unidad de venta; KGM si dudas.
- NO inventes productos ni precios; si un dato no está, déjalo vacío.
Llama SIEMPRE a la herramienta `registrar_productos` con TODAS las filas.\
"""

_TOOL_EXTRACT = {
    "name": "registrar_productos",
    "description": "Registra las filas de productos extraídas de la lista de precios.",
    "input_schema": {
        "type": "object",
        "properties": {
            "productos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "codigo": {"type": "string"},
                        "descripcion": {"type": "string"},
                        "unidad": {"type": "string"},
                        "precio": {"type": "string"},
                        "clave_sat": {"type": "string"},
                        "unidad_sat": {"type": "string"},
                    },
                    "required": ["nombre"],
                },
            },
        },
        "required": ["productos"],
    },
}


def _tabla_a_texto(data: bytes, filename: str) -> str:
    """Excel/CSV con acomodo libre → texto tabular (TSV) para la IA."""
    import pandas as pd

    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    try:
        if ext == "csv":
            df = pd.read_csv(io.BytesIO(data), header=None, dtype=str, sep=None, engine="python")
        else:
            df = pd.read_excel(io.BytesIO(data), sheet_name=0, header=None, dtype=str)
    except Exception as exc:  # noqa: BLE001
        raise ImportProductosError(
            f"No se pudo leer el archivo '{filename}'. ¿Es un Excel (.xlsx/.xls) o CSV?"
        ) from exc
    lineas = []
    for _, row in df.iterrows():
        celdas = [_texto(v) for v in row.tolist()]
        if any(celdas):
            lineas.append("\t".join(celdas))
        if len(lineas) > MAX_FILAS_IA + 50:   # margen para encabezados/notas
            raise ImportProductosError(f"Máximo {MAX_FILAS_IA} productos por archivo con IA")
    if not lineas:
        raise ImportProductosError("El archivo está vacío")
    return "\n".join(lineas)


def extraer_con_ia(data: bytes, filename: str) -> list[dict]:
    """Lista de precios en cualquier formato → filas estructuradas vía Claude.

    Excel/CSV van como texto tabular; PDF como documento; imágenes como imagen.
    Lanza ImportProductosError con mensaje claro si la IA no está disponible.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise ImportProductosError(
            "El archivo no coincide con la plantilla y la IA no está configurada. "
            "Descarga la plantilla y captura ahí los productos."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ImportProductosError("SDK de IA no instalado") from exc

    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("xlsx", "xls", "csv"):
        content: list[dict] = [{"type": "text", "text":
            "Lista de precios (texto tabular, columnas separadas por tabulador):\n\n"
            + _tabla_a_texto(data, filename)}]
    elif ext == "pdf":
        content = [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(data).decode(),
            }},
            {"type": "text", "text": "Extrae los productos de esta lista de precios."},
        ]
    elif ext in _MIME_POR_EXT:  # imágenes
        content = [
            {"type": "image", "source": {
                "type": "base64", "media_type": _MIME_POR_EXT[ext],
                "data": base64.standard_b64encode(data).decode(),
            }},
            {"type": "text", "text": "Extrae los productos de esta lista de precios."},
        ]
    else:
        raise ImportProductosError(
            "Formato no soportado. Sube un Excel (.xlsx/.xls), CSV, PDF o foto (JPG/PNG)."
        )

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        # Streaming obligatorio: con max_tokens grandes (cientos de filas) el
        # SDK rechaza la llamada no-streaming por el límite de 10 minutos.
        with client.messages.stream(
            model=settings.SAT_AI_MODEL,
            max_tokens=32000,
            system=[{"type": "text", "text": _SYSTEM_EXTRACT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=[_TOOL_EXTRACT],
            tool_choice={"type": "tool", "name": "registrar_productos"},
            messages=[{"role": "user", "content": content}],
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.APIError as exc:
        logger.warning("extracción IA de productos falló: %s", exc)
        raise ImportProductosError(
            "La IA no pudo leer el archivo en este momento. Intenta de nuevo o usa la plantilla."
        ) from exc

    filas: list[dict] = []
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "registrar_productos":
            for p in (block.input.get("productos") or []):
                if not isinstance(p, dict):
                    continue
                nombre = _texto(p.get("nombre"))
                if not nombre:
                    continue
                precio = _decimal(p.get("precio"))
                filas.append({
                    "nombre": nombre,
                    "codigo": _texto(p.get("codigo")),
                    "descripcion": _texto(p.get("descripcion")),
                    "unidad": normalizar_unidad(_texto(p.get("unidad"))),
                    "precio": str(precio) if precio is not None else "",
                    "clave_sat": re.sub(r"\D", "", _texto(p.get("clave_sat")))[:8],
                    "unidad_sat": _texto(p.get("unidad_sat")).upper()[:3],
                    "codigo_barras": "",
                })
    if not filas:
        raise ImportProductosError("La IA no encontró productos en el archivo")
    return filas[:MAX_FILAS_IA]


def generar_plantilla() -> bytes:
    """La plantilla oficial .xlsx: encabezados + 2 filas de ejemplo + notas."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Productos"

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F2937")
    for i, (nombre, _) in enumerate(PLANTILLA_COLUMNAS, start=1):
        c = ws.cell(row=1, column=i, value=nombre)
        c.font = head_font
        c.fill = head_fill
        ws.column_dimensions[get_column_letter(i)].width = max(14, len(nombre) + 4)
    ws.column_dimensions["A"].width = 34

    ejemplos = [
        ["JITOMATE SALADETT", "JIT-SAD-001", "", "KILO", "28.50", "50421800", "KGM", ""],
        ["ACEITE COMESTIBLE 20 LT", "", "Marca Cristal", "PIEZA", "935.40", "50151513", "H87", ""],
    ]
    for r, fila in enumerate(ejemplos, start=2):
        for i, v in enumerate(fila, start=1):
            ws.cell(row=r, column=i, value=v)

    notas = wb.create_sheet("Instrucciones")
    notas.column_dimensions["A"].width = 18
    notas.column_dimensions["B"].width = 70
    notas.cell(row=1, column=1, value="Columna").font = Font(bold=True)
    notas.cell(row=1, column=2, value="Qué va ahí").font = Font(bold=True)
    for r, (nombre, ayuda) in enumerate(PLANTILLA_COLUMNAS, start=2):
        notas.cell(row=r, column=1, value=nombre)
        notas.cell(row=r, column=2, value=ayuda)
    fila_extra = len(PLANTILLA_COLUMNAS) + 3
    notas.cell(row=fila_extra, column=2,
               value="Solo NOMBRE es obligatorio. Las filas de ejemplo se pueden borrar. "
                     "El SKU interno se genera automáticamente; lo que pongas en CODIGO "
                     "se guarda como código del cliente cuando importas la lista de un cliente.")

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

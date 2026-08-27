"""Cruce de productos — resuelve un texto libre al producto real del catálogo.

Cascada de mayor a menor confianza:
  1. Exacto         — sku o nombre normalizado idéntico.
  2. Alias aprendido — `producto_alias` (ya confirmado antes; no se vuelve a preguntar).
  3. Difuso         — RapidFuzz sobre nombre + sinónimos (typos: "zanahorias"→"zanahoria").
  4. IA (opcional)  — Claude para sinónimos regionales ("Chile Cuaresmeño"="Jalapeño").

Cuando el usuario confirma una sugerencia, `aprender_alias` la guarda y a partir
de ahí el paso 2 la resuelve al instante.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from rapidfuzz import fuzz
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Producto, ProductoAlias

logger = logging.getLogger(__name__)

_FUZZY_FLOOR = 60  # score mínimo (0-100) para ofrecer un candidato difuso


def normalizar(texto: str) -> str:
    """minúsculas + sin acentos + sin puntuación + espacios colapsados."""
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s)
    return " ".join(s.split())


@dataclass
class Candidato:
    producto_id: UUID
    sku: str
    nombre: str
    score: int            # 0-100
    origen: str           # exacto | alias | difuso | ia
    presentaciones: dict
    presentacion_default: Optional[str]
    unidad_base: Optional[str]
    # La categoría del producto existente: al vincular, la fila hereda la suya.
    categoria_id: Optional[UUID] = None
    esquema_impuesto_id: Optional[UUID] = None


def _cand(p: Producto, score: int, origen: str) -> "Candidato":
    return Candidato(
        producto_id=p.id, sku=p.sku, nombre=p.nombre, score=score, origen=origen,
        presentaciones=p.presentaciones or {},
        presentacion_default=p.presentacion_default,
        unidad_base=p.unidad_base,
        categoria_id=p.categoria_id,
        esquema_impuesto_id=p.esquema_impuesto_id,
    )


def productos_activos(db: Session) -> list[Producto]:
    return db.query(Producto).filter(Producto.deleted_at.is_(None), Producto.activo.is_(True)).all()


def alias_del_tenant(db: Session) -> dict[str, UUID]:
    """Todos los alias aprendidos del tenant: {alias_normalizado: producto_id}.

    Se carga UNA vez y se pasa a `buscar` cuando se cruzan muchos textos (la
    importación masiva resuelve cientos de filas): sin esto era un SELECT por
    fila — 508 filas = 508 viajes a la base, que contra una base en la nube
    convierte medio segundo en decenas.
    """
    return {
        a.alias_normalizado: a.producto_id
        for a in db.query(ProductoAlias.alias_normalizado, ProductoAlias.producto_id).all()
    }


def normalizar_catalogo(prods: list[Producto]) -> dict[UUID, tuple[str, str, list[str]]]:
    """{producto_id: (nombre_norm, sku_norm, [textos_norm])} para el catálogo.

    `buscar` normaliza el nombre y los sinónimos de CADA producto para CADA
    texto que cruza: 500 filas × 1,900 productos ≈ 1M normalizaciones por
    preview. Precalculado una vez, el cruce masivo pasa de decenas de segundos
    a menos de uno.
    """
    out: dict[UUID, tuple[str, str, list[str]]] = {}
    for p in prods:
        nombre = normalizar(p.nombre)
        out[p.id] = (
            nombre,
            normalizar(p.sku),
            [nombre] + [normalizar(s) for s in (p.sinonimos or [])],
        )
    return out


def buscar(
    db: Session,
    tenant_id: UUID,
    texto: str,
    *,
    limit: int = 5,
    prods: Optional[list[Producto]] = None,
    aliases: Optional[dict[str, UUID]] = None,
    norms: Optional[dict[UUID, tuple[str, str, list[str]]]] = None,
) -> list[Candidato]:
    """Devuelve candidatos ordenados por confianza para un texto libre.

    `prods` permite pasar el catálogo ya cargado: /productos/match resuelve hasta
    200 textos por request y sin esto haría 200 SELECT del catálogo completo.
    """
    norm = normalizar(texto)
    if not norm:
        return []

    if prods is None:
        prods = productos_activos(db)
    by_id = {p.id: p for p in prods}

    out: list[Candidato] = []
    seen: set[UUID] = set()

    # 1) exactos por nombre o sku — TODOS los que coinciden (no solo el primero).
    #    Clave para evitar duplicados: si ya existen "SANDIA", "Sandía", "Sandia"
    #    (todas normalizan igual), deben aparecer las tres para que el usuario las vea.
    for p in prods:
        nombre_n, sku_n, _ = (norms or {}).get(p.id) or (
            normalizar(p.nombre), normalizar(p.sku), [])
        if nombre_n == norm or sku_n == norm:
            out.append(_cand(p, 100, "exacto"))
            seen.add(p.id)

    # 2) alias aprendido (si apunta a un producto que aún no está incluido).
    #    `aliases` precargado evita un SELECT por texto en los cruces masivos.
    if aliases is not None:
        alias_pid = aliases.get(norm)
    else:
        fila = (
            db.query(ProductoAlias.producto_id)
            .filter(ProductoAlias.alias_normalizado == norm)
            .one_or_none()
        )
        alias_pid = fila[0] if fila is not None else None
    if alias_pid is not None and alias_pid in by_id and alias_pid not in seen:
        out.append(_cand(by_id[alias_pid], 100, "alias"))
        seen.add(alias_pid)

    # 3) por producto: prefijo / subcadena / difuso. Se evalúa CADA producto
    #    (no se colapsan por nombre normalizado), para que al teclear las primeras
    #    letras aparezcan TODAS las coincidencias — incluidos duplicados.
    #      - empieza con el texto      → 96 (prefijo, lo que el usuario espera al filtrar)
    #      - contiene el texto         → 88 (subcadena)
    #      - parecido (typos/variantes)→ token_set_ratio de RapidFuzz
    _FUZZY_MIN = 75   # los parecidos PUROS (typos) deben ser fuertes — evita ruido
    scored: list[tuple[Producto, int]] = []
    for p in prods:
        if p.id in seen:
            continue
        cacheado = (norms or {}).get(p.id)
        textos = cacheado[2] if cacheado else (
            [normalizar(p.nombre)] + [normalizar(s) for s in (p.sinonimos or [])])
        score = 0
        for h in textos:
            if not h:
                continue
            if h.startswith(norm):
                score = max(score, 96)
            elif norm in h:
                score = max(score, 88)
            else:
                fz = int(fuzz.token_set_ratio(norm, h))
                if fz >= _FUZZY_MIN:
                    score = max(score, fz)
        if score >= _FUZZY_FLOOR:
            scored.append((p, score))

    # Ordena por score y, a igualdad, alfabético para un orden estable.
    scored.sort(key=lambda kv: (-kv[1], normalizar(kv[0].nombre)))
    for p, score in scored:
        out.append(_cand(p, score, "difuso"))
        seen.add(p.id)

    return out[:limit]


def aprender_alias(
    db: Session, tenant_id: UUID, texto: str, producto_id: UUID, *, origen: str = "MANUAL", user_id=None
) -> Optional[ProductoAlias]:
    """Guarda (o reapunta) el alias normalizado → producto. Idempotente."""
    # NFKD puede ALARGAR el texto (ligaduras); sin truncar, un alias de 254
    # chars revienta la columna String(254) con DataError 500.
    norm = normalizar(texto)[:254]
    if not norm:
        return None
    existing = (
        db.query(ProductoAlias)
        .filter(ProductoAlias.alias_normalizado == norm)
        .one_or_none()
    )
    if existing is not None:
        existing.producto_id = producto_id
        existing.origen = origen
        db.flush()
        return existing
    alias = ProductoAlias(
        tenant_id=tenant_id, producto_id=producto_id, alias=texto.strip()[:254],
        alias_normalizado=norm, origen=origen, created_by=user_id,
    )
    try:
        # Savepoint: si otro request insertó el mismo alias en paralelo, el
        # UNIQUE (tenant, alias_normalizado) truena aquí sin tirar la transacción.
        with db.begin_nested():
            db.add(alias)
            db.flush()
        return alias
    except IntegrityError:
        existing = (
            db.query(ProductoAlias)
            .filter(ProductoAlias.alias_normalizado == norm)
            .one_or_none()
        )
        if existing is not None:
            existing.producto_id = producto_id
            existing.origen = origen
            db.flush()
        return existing


# ─── IA: sinónimos regionales / typos que el difuso no alcanza ────────────────
_AI_TOOL = {
    "name": "registrar_cruce",
    "description": "Asocia cada texto de entrada con el SKU del catálogo que representa, o null si ninguno.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cruces": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "texto": {"type": "string"},
                        "sku": {"type": "string", "description": "SKU exacto del catálogo, o vacío si ninguno aplica."},
                    },
                    "required": ["texto", "sku"],
                },
            }
        },
        "required": ["cruces"],
    },
}


def sugerir_con_ia(db: Session, tenant_id: UUID, textos: list[str]) -> dict[str, Optional[UUID]]:
    """Cruce por IA en una sola llamada (batch). {} si no hay API key o falla."""
    textos = [t for t in (t.strip() for t in textos) if t]
    if not textos or not settings.ANTHROPIC_API_KEY:
        return {}
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return {}

    prods = productos_activos(db)
    by_sku = {p.sku: p.id for p in prods}
    catalogo = "\n".join(
        f"- {p.sku}: {p.nombre}" + (f" (sinónimos: {', '.join(p.sinonimos)})" if p.sinonimos else "")
        for p in prods
    )
    system = (
        "Eres un asistente que cruza nombres de productos de frutas, verduras y abarrotes "
        "(incluyendo variantes regionales y errores de escritura) contra un catálogo. "
        "Ejemplos: 'zanahorias'→'zanahoria'; 'Chile Cuaresmeño'='Chile Jalapeño'. "
        "Devuelve el SKU EXACTO del catálogo para cada texto, o vacío si ninguno corresponde con seguridad."
    )
    user = f"Catálogo:\n{catalogo}\n\nTextos a cruzar (JSON): {textos}"
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=getattr(settings, "SAT_AI_MODEL", "claude-sonnet-4-5"),
            max_tokens=1024,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[_AI_TOOL],
            tool_choice={"type": "tool", "name": "registrar_cruce"},
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:  # noqa: BLE001 — degradación elegante
        logger.warning("cruce IA falló: %s", exc)
        return {}

    out: dict[str, Optional[UUID]] = {}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "registrar_cruce":
            for c in (block.input.get("cruces") or []):
                if isinstance(c, dict):
                    out[str(c.get("texto", ""))] = by_sku.get(str(c.get("sku", "")))
    return out


# ─── Parseo de un bloque pegado (Excel) → líneas estructuradas ────────────────
# Convierte texto tabular pegado en filas {producto, cantidad, precio,
# presentacion} SIN asumir el orden de las columnas ni cuántas hay, y saltando
# la fila de encabezado. Primero intenta con IA (entiende encabezados en español
# y columnas fuera de orden); si no hay API key o falla, cae a un parser
# determinista que clasifica CADA columna por su contenido en toda la tabla.

_UNIDADES = {
    "kilogramo", "kilogramos", "kilo", "kilos", "kg", "kgs", "k",
    "gramo", "gramos", "g", "gr", "grs",
    "pieza", "piezas", "pza", "pzas", "pz", "pieza(s)", "pzs",
    "litro", "litros", "lt", "lts", "l", "ml", "mililitro", "mililitros",
    "caja", "cajas", "cja", "bulto", "bultos", "costal", "costales",
    "manojo", "manojos", "paquete", "paquetes", "paq", "docena", "docenas",
    "bolsa", "bolsas", "domo", "domos", "charola", "charolas", "malla", "mallas",
    "atado", "atados", "racimo", "racimos", "unidad", "unidades", "und", "un", "pkg",
}
_HEADER_WORDS = {
    "cantidad", "cant", "cantidades", "qty", "unidad", "unidades", "um",
    "presentacion", "descripcion", "producto", "productos", "articulo",
    "articulos", "precio", "precios", "costo", "costos", "costo unitario",
    "precio unitario", "importe", "total", "concepto", "conceptos", "clave",
    "codigo", "sku", "pu", "p u", "no", "num", "numero", "partida",
}


def _es_numero(s: str) -> bool:
    s = (s or "").strip().replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _num(s: str) -> float:
    return float((s or "").strip().replace("$", "").replace(",", "").replace(" ", ""))


def _num_str(v, *, cero_vacio: bool) -> str:
    """Normaliza un número a string sin ceros de más ('2.0'→'2'); '' si 0 y
    `cero_vacio` (precio 0 = sin precio)."""
    try:
        f = float(str(v).strip().replace("$", "").replace(",", "")) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return ""
    if f == 0 and cero_vacio:
        return ""
    return f"{f:g}"


def _fila_es_encabezado(cols: list[str]) -> bool:
    """Una fila es encabezado si no trae ningún número y alguna celda es una
    palabra típica de encabezado ('Cantidad', 'Descripción', 'Costo unitario')."""
    celdas = [c.strip() for c in cols if c.strip()]
    if not celdas:
        return True
    if any(_es_numero(c) for c in celdas):
        return False
    return any(normalizar(c) in _HEADER_WORDS for c in celdas)


def _split_filas(texto: str) -> list[list[str]]:
    filas: list[list[str]] = []
    for linea in (texto or "").split("\n"):
        if not linea.strip():
            continue
        cols = linea.split("\t")
        if len(cols) == 1:  # sin tabuladores: intenta 2+ espacios como separador
            cols = re.split(r"\s{2,}", linea.strip())
        filas.append([c.strip() for c in cols])
    return filas


def parsear_pegado_deterministico(texto: str) -> list[dict]:
    """Clasifica cada COLUMNA por su contenido en toda la tabla (no fila por
    fila): la columna con unidades → presentación; las numéricas → cantidad
    (menor magnitud) y precio (mayor); el texto restante más largo → producto.
    Así 'KILOGRAMO' antes de 'AJO' se interpreta bien."""
    filas = [r for r in _split_filas(texto) if not _fila_es_encabezado(r)]
    if not filas:
        return []
    ncols = max(len(r) for r in filas)
    filas = [r + [""] * (ncols - len(r)) for r in filas]
    cols = [[r[i] for r in filas] for i in range(ncols)]

    def frac(vals, pred) -> float:
        nz = [v for v in vals if v]
        return sum(1 for v in nz if pred(v)) / len(nz) if nz else 0.0

    numericas = [i for i in range(ncols) if frac(cols[i], _es_numero) >= 0.6]
    unidades = [
        i for i in range(ncols)
        if i not in numericas and frac(cols[i], lambda v: normalizar(v) in _UNIDADES) >= 0.5
    ]

    cantidad_col = precio_col = None
    if len(numericas) >= 2:
        prom = {
            i: (sum(_num(v) for v in cols[i] if _es_numero(v))
                / max(1, sum(1 for v in cols[i] if _es_numero(v))))
            for i in numericas
        }
        cantidad_col = min(numericas, key=lambda i: prom[i])
        precio_col = max(numericas, key=lambda i: prom[i])
    elif len(numericas) == 1:
        cantidad_col = numericas[0]

    textos = [i for i in range(ncols) if i not in numericas and i not in unidades]
    if not textos and unidades:  # no hay otra columna de texto: la 'unidad' es el producto
        textos, unidades = unidades, []

    def largo(i) -> float:
        nz = [v for v in cols[i] if v]
        return sum(len(v) for v in nz) / len(nz) if nz else 0.0

    producto_col = max(textos, key=largo) if textos else None
    presentacion_col = unidades[0] if unidades else None

    out: list[dict] = []
    for r in filas:
        prod = r[producto_col].strip() if producto_col is not None else ""
        if not prod:
            continue
        out.append({
            "producto": prod,
            "cantidad": _num_str(r[cantidad_col], cero_vacio=False) if cantidad_col is not None else "",
            "precio": _num_str(r[precio_col], cero_vacio=True) if precio_col is not None else "",
            "presentacion": r[presentacion_col].strip() if presentacion_col is not None else "",
        })
    for row in out:  # cantidad nunca vacía
        if not row["cantidad"]:
            row["cantidad"] = "1"
    return out


_PARSE_TOOL = {
    "name": "registrar_lineas",
    "description": "Registra las líneas de la tabla pegada, una por producto.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lineas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "producto": {"type": "string", "description": "Nombre/descripción del producto."},
                        "cantidad": {"type": "number", "description": "Cantidad; 1 si no aparece."},
                        "precio": {"type": "number", "description": "Precio o costo unitario; 0 si no aparece."},
                        "presentacion": {"type": "string", "description": "Unidad (KILOGRAMO, PIEZA, LITRO…); vacío si no aparece."},
                    },
                    "required": ["producto", "cantidad", "precio", "presentacion"],
                },
            }
        },
        "required": ["lineas"],
    },
}


def parsear_pegado_ia(texto: str) -> Optional[list[dict]]:
    """Parsea el bloque pegado con IA. None si no hay API key o falla (→ fallback)."""
    if not (texto or "").strip() or not settings.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return None
    system = (
        "Recibes filas pegadas desde Excel u hoja de cálculo, con columnas separadas "
        "por tabuladores. El ORDEN de las columnas varía entre pegados y puede haber una "
        "fila de ENCABEZADO (p. ej. 'Cantidad  Unidad  Descripción  Costo unitario'). "
        "Para cada fila de DATOS identifica: producto (la descripción/nombre del producto), "
        "cantidad (número; 1 si no aparece), precio (precio o costo unitario, número; 0 si no "
        "aparece) y presentacion (la unidad: KILOGRAMO, PIEZA, LITRO, etc.; vacío si no aparece). "
        "OMITE la fila de encabezado y las filas vacías. No inventes filas ni valores."
    )
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=getattr(settings, "SAT_AI_MODEL", "claude-sonnet-4-5"),
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[_PARSE_TOOL],
            tool_choice={"type": "tool", "name": "registrar_lineas"},
            messages=[{"role": "user", "content": f"Filas pegadas:\n{texto}"}],
        )
    except Exception as exc:  # noqa: BLE001 — degradación elegante
        logger.warning("parseo IA falló: %s", exc)
        return None

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "registrar_lineas":
            filas: list[dict] = []
            for l in (block.input.get("lineas") or []):
                if not isinstance(l, dict):
                    continue
                prod = str(l.get("producto", "")).strip()
                if not prod:
                    continue
                filas.append({
                    "producto": prod,
                    "cantidad": _num_str(l.get("cantidad"), cero_vacio=False) or "1",
                    "precio": _num_str(l.get("precio"), cero_vacio=True),
                    "presentacion": str(l.get("presentacion", "")).strip(),
                })
            return filas
    return None


def parsear_pegado(texto: str, *, usar_ia: bool = True) -> list[dict]:
    """Bloque pegado → líneas {producto, cantidad, precio, presentacion}."""
    if usar_ia:
        filas = parsear_pegado_ia(texto)
        if filas is not None:
            return filas
    return parsear_pegado_deterministico(texto)

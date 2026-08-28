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


# Unidad del documento → nombre de presentación del catálogo. Cubre el OCR
# sucio de los PDFs ("KILOGR AMO", "GARRA FON"): se compara sin espacios.
_UNIDAD_A_PRESENTACION = {
    "kilogramo": "KILO", "kilogramos": "KILO", "kilo": "KILO", "kilos": "KILO",
    "kg": "KILO", "kgs": "KILO", "kgm": "KILO", "k": "KILO",
    "pieza": "PIEZA", "piezas": "PIEZA", "pza": "PIEZA", "pzas": "PIEZA",
    "pz": "PIEZA", "pzs": "PIEZA", "h87": "PIEZA", "unidad": "PIEZA",
    "unidades": "PIEZA", "und": "PIEZA", "un": "PIEZA",
    "litro": "LITRO", "litros": "LITRO", "lt": "LITRO", "lts": "LITRO", "l": "LITRO",
    "mililitro": "MILILITRO", "mililitros": "MILILITRO", "ml": "MILILITRO",
    "manojo": "MANOJO", "manojos": "MANOJO", "mj": "MANOJO", "mjo": "MANOJO",
    "caja": "CAJA", "cajas": "CAJA", "cja": "CAJA", "cj": "CAJA",
    "paquete": "PAQUETE", "paquetes": "PAQUETE", "paq": "PAQUETE", "pkg": "PAQUETE",
    "bulto": "BULTO", "bultos": "BULTO", "costal": "COSTAL", "costales": "COSTAL",
    "bolsa": "BOLSA", "bolsas": "BOLSA", "domo": "DOMO", "domos": "DOMO",
    "charola": "CHAROLA", "charolas": "CHAROLA", "atado": "ATADO", "atados": "ATADO",
    "docena": "DOCENA", "docenas": "DOCENA", "garrafon": "GARRAFON",
    "gramo": "GRAMO", "gramos": "GRAMO", "g": "GRAMO", "gr": "GRAMO", "grs": "GRAMO",
    "rollo": "ROLLO", "rollos": "ROLLO", "malla": "MALLA", "mallas": "MALLA",
}


def normalizar_unidad(texto: Optional[str]) -> Optional[str]:
    """'KILOGR AMO' → 'KILO'; None si no se reconoce (mejor no adivinar).

    El OCR parte las palabras y el catálogo de cada cliente escribe la unidad a
    su manera; sin este puente, una OC de "5 CAJA" entraría como 5 KILO.
    """
    llave = normalizar(texto or "").replace(" ", "")
    return _UNIDAD_A_PRESENTACION.get(llave)


def _singular(token: str) -> str:
    """'acelgas' → 'acelga', 'limones' → 'limon'.

    Solo tokens largos: 'gas' o 'mes' no son plurales de nada. No pretende ser
    un lematizador — basta para que la lista del cliente ("ACELGAS") encuentre
    el producto del catálogo ("ACELGA MANOJO DE 1 KG")."""
    if len(token) >= 6 and token.endswith("es"):
        return token[:-2]
    if len(token) >= 5 and token.endswith("s"):
        return token[:-1]
    return token


def variantes_de(norm: str) -> list[str]:
    """El texto normalizado y, si trae plurales, su versión en singular."""
    sing = " ".join(_singular(t) for t in norm.split())
    return [norm] if sing == norm else [norm, sing]


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
    # Lo fiscal del producto YA dado de alta: al vincular es lo que manda, y
    # por definición ya está registrado (no hay clave inexistente que avisar).
    clave_sat: Optional[str] = None
    unidad_sat: Optional[str] = None


def _cand(p: Producto, score: int, origen: str) -> "Candidato":
    return Candidato(
        producto_id=p.id, sku=p.sku, nombre=p.nombre, score=score, origen=origen,
        presentaciones=p.presentaciones or {},
        presentacion_default=p.presentacion_default,
        unidad_base=p.unidad_base,
        categoria_id=p.categoria_id,
        esquema_impuesto_id=p.esquema_impuesto_id,
        clave_sat=p.clave_sat,
        unidad_sat=p.unidad_sat,
    )


def productos_activos(db: Session) -> list[Producto]:
    return db.query(Producto).filter(Producto.deleted_at.is_(None), Producto.activo.is_(True)).all()


def alias_del_tenant(db: Session) -> dict[str, UUID]:
    """Los alias GLOBALES del tenant: {alias_normalizado: producto_id}.

    Se carga UNA vez y se pasa a `buscar` cuando se cruzan muchos textos (la
    importación masiva resuelve cientos de filas): sin esto era un SELECT por
    fila — 508 filas = 508 viajes a la base, que contra una base en la nube
    convierte medio segundo en decenas.

    Solo los globales (cliente_id NULL): las importaciones y los cruces de
    catálogo son del tenant entero; el vocabulario privado de un cliente se
    carga aparte con `alias_de_cliente` cuando el documento ya sabe de quién es.
    """
    return {
        a.alias_normalizado: a.producto_id
        for a in db.query(ProductoAlias.alias_normalizado, ProductoAlias.producto_id)
        .filter(ProductoAlias.cliente_id.is_(None))
        .all()
    }


def alias_de_cliente(
    db: Session, cliente_id: Optional[UUID], sucursal_id: Optional[UUID] = None
) -> dict[str, UUID]:
    """El vocabulario privado del cliente: {alias_normalizado: producto_id}.

    El más específico pisa al más general al armar el dict (cliente+sucursal >
    cliente), así `buscar` solo consulta un mapa. Vacío si no hay cliente."""
    if cliente_id is None:
        return {}
    filas = (
        db.query(ProductoAlias)
        .filter(ProductoAlias.cliente_id == cliente_id)
        .all()
    )
    out: dict[str, UUID] = {}
    # Primero los del cliente a secas; encima, los de la sucursal del documento.
    for a in filas:
        if a.sucursal_id is None:
            out[a.alias_normalizado] = a.producto_id
    if sucursal_id is not None:
        for a in filas:
            if a.sucursal_id == sucursal_id:
                out[a.alias_normalizado] = a.producto_id
    return out


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
    aliases_cliente: Optional[dict[str, UUID]] = None,
    norms: Optional[dict[UUID, tuple[str, str, list[str]]]] = None,
    unidad: Optional[str] = None,
) -> list[Candidato]:
    """Devuelve candidatos ordenados por confianza para un texto libre.

    `prods` permite pasar el catálogo ya cargado: /productos/match resuelve hasta
    200 textos por request y sin esto haría 200 SELECT del catálogo completo.

    `aliases_cliente` (de `alias_de_cliente`) gana sobre el alias global: es el
    vocabulario privado del cliente del documento. `unidad` (ya normalizada con
    `normalizar_unidad`) frena el paso difuso: un parecido que ni siquiera se
    vende en esa unidad no puede ser candidato fuerte — el clúster papa/papaya
    demostró que la distancia de edición sola engaña en nombres cortos.
    """
    norm = normalizar(texto)
    if not norm:
        return []
    # El plural del archivo del cliente no debe esconder el producto: se cruza
    # con el texto tal cual Y con su singular, y gana el mejor de los dos.
    variantes = variantes_de(norm)

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
        if nombre_n in variantes or sku_n in variantes:
            out.append(_cand(p, 100, "exacto"))
            seen.add(p.id)

    # 2) alias aprendido (si apunta a un producto que aún no está incluido).
    #    El del CLIENTE gana sobre el global: es su vocabulario privado.
    #    `aliases` precargado evita un SELECT por texto en los cruces masivos.
    alias_pid = (aliases_cliente or {}).get(norm)
    if alias_pid is None:
        if aliases is not None:
            alias_pid = aliases.get(norm)
        else:
            fila = (
                db.query(ProductoAlias.producto_id)
                .filter(
                    ProductoAlias.alias_normalizado == norm,
                    ProductoAlias.cliente_id.is_(None),
                )
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
        score_difuso = 0
        for h in textos:
            if not h:
                continue
            for v in variantes:
                if h.startswith(v):
                    score = max(score, 96)
                elif v in h:
                    score = max(score, 88)
                else:
                    fz = int(fuzz.token_set_ratio(v, h))
                    if fz >= _FUZZY_MIN:
                        score_difuso = max(score_difuso, fz)
        # Freno por unidad SOLO al parecido difuso puro (typos): si la partida
        # trae unidad y el producto ni la vende, ese parecido no puede ser
        # fuerte — el clúster papa/papaya/papalo nació exactamente así. El
        # prefijo/subcadena no se degrada: "CILANTRO" sigue siendo la sugerencia
        # correcta aunque el producto aún no venda MANOJO.
        if score_difuso and unidad:
            pres = {(k or "").strip().upper() for k in (p.presentaciones or {})}
            pres.add((p.unidad_base or "").strip().upper())
            if unidad.strip().upper() not in pres:
                score_difuso = _FUZZY_MIN - 1
        score = max(score, score_difuso)
        if score >= _FUZZY_FLOOR:
            scored.append((p, score))

    # Ordena por score y, a igualdad, alfabético para un orden estable.
    scored.sort(key=lambda kv: (-kv[1], normalizar(kv[0].nombre)))
    for p, score in scored:
        out.append(_cand(p, score, "difuso"))
        seen.add(p.id)

    return out[:limit]


def _alias_en_alcance(db: Session, norm: str, cliente_id, sucursal_id) -> Optional[ProductoAlias]:
    """El alias de ESE alcance exacto (NULL cuenta como valor), o None.

    El lookup filtra por cliente/sucursal a propósito: aprender el alias de un
    cliente jamás debe reapuntar el global — eso rompería el vocabulario de
    todos los demás."""
    q = db.query(ProductoAlias).filter(ProductoAlias.alias_normalizado == norm)
    q = q.filter(ProductoAlias.cliente_id == cliente_id) if cliente_id is not None \
        else q.filter(ProductoAlias.cliente_id.is_(None))
    q = q.filter(ProductoAlias.sucursal_id == sucursal_id) if sucursal_id is not None \
        else q.filter(ProductoAlias.sucursal_id.is_(None))
    return q.one_or_none()


def aprender_alias(
    db: Session, tenant_id: UUID, texto: str, producto_id: UUID, *,
    origen: str = "MANUAL", user_id=None,
    cliente_id: Optional[UUID] = None, sucursal_id: Optional[UUID] = None,
) -> Optional[ProductoAlias]:
    """Guarda (o reapunta) el alias normalizado → producto EN SU ALCANCE. Idempotente.

    Sin cliente_id escribe el alias global (comportamiento histórico); con
    cliente_id escribe/reapunta SOLO el de ese cliente (y sucursal), sin tocar
    el global."""
    # NFKD puede ALARGAR el texto (ligaduras); sin truncar, un alias de 254
    # chars revienta la columna String(254) con DataError 500.
    norm = normalizar(texto)[:254]
    if not norm:
        return None
    existing = _alias_en_alcance(db, norm, cliente_id, sucursal_id)
    if existing is not None:
        existing.producto_id = producto_id
        existing.origen = origen
        db.flush()
        return existing
    alias = ProductoAlias(
        tenant_id=tenant_id, producto_id=producto_id, alias=texto.strip()[:254],
        alias_normalizado=norm, origen=origen, created_by=user_id,
        cliente_id=cliente_id, sucursal_id=sucursal_id,
    )
    try:
        # Savepoint: si otro request insertó el mismo alias en paralelo, el
        # índice único del alcance truena aquí sin tirar la transacción.
        with db.begin_nested():
            db.add(alias)
            db.flush()
        return alias
    except IntegrityError:
        existing = _alias_en_alcance(db, norm, cliente_id, sucursal_id)
        if existing is not None:
            existing.producto_id = producto_id
            existing.origen = origen
            db.flush()
        return existing


def _alias_vigente_de_cliente(
    db: Session, norm: str, cliente_id, sucursal_id
) -> Optional[ProductoAlias]:
    """El alias CON ALCANCE que `buscar` usaría para este documento, o None.

    Misma precedencia que `alias_de_cliente`: el de la sucursal pisa al del
    cliente. Existe para que corregir en la bandeja caiga donde el resolutor
    va a leer — si no, la corrección se escribe en un lugar que nadie mira."""
    if cliente_id is None:
        return None
    filas = (
        db.query(ProductoAlias)
        .filter(
            ProductoAlias.alias_normalizado == norm,
            ProductoAlias.cliente_id == cliente_id,
        )
        .all()
    )
    if sucursal_id is not None:
        de_sucursal = next((a for a in filas if a.sucursal_id == sucursal_id), None)
        if de_sucursal is not None:
            return de_sucursal
    return next((a for a in filas if a.sucursal_id is None), None)


def aprender_alias_con_alcance(
    db: Session, tenant_id: UUID, texto: str, producto_id: UUID, *,
    cliente_id: Optional[UUID] = None, sucursal_id: Optional[UUID] = None,
    origen: str = "MANUAL", user_id=None,
) -> Optional[ProductoAlias]:
    """La regla del catálogo multicliente: GLOBAL por defecto, alcance en conflicto.

    - Sin alias global para ese texto → se aprende GLOBAL (le sirve a todos:
      Balles, Jubran y MAFAN comparten vocabulario gratis).
    - Global ya apunta al MISMO producto → no hay nada que aprender, SALVO que
      este cliente arrastre un alias con alcance que diga otra cosa: ese es el
      que gana al cruzar, así que la corrección tiene que caer ahí.
    - Global apunta a OTRO producto → el texto es ambiguo entre clientes: se
      aprende con alcance (cliente, y sucursal si viene) SIN tocar el global —
      el "LIMON" de Pachuca deja de pelearse con el de Villahermosa.
    """
    norm = normalizar(texto)[:254]
    if not norm:
        return None
    # Lo que HOY resuelve para este documento: si el cliente tiene su propio
    # alias, es el que manda en `buscar` y el que hay que corregir.
    vigente = _alias_vigente_de_cliente(db, norm, cliente_id, sucursal_id)
    if vigente is not None and vigente.producto_id != producto_id:
        return aprender_alias(
            db, tenant_id, texto, producto_id, origen=origen, user_id=user_id,
            cliente_id=vigente.cliente_id, sucursal_id=vigente.sucursal_id,
        )
    global_ = _alias_en_alcance(db, norm, None, None)
    if global_ is None:
        return aprender_alias(
            db, tenant_id, texto, producto_id, origen=origen, user_id=user_id
        )
    if global_.producto_id == producto_id:
        return vigente if vigente is not None else global_
    if cliente_id is None:
        # Sin cliente no hay alcance posible: reapuntar el global es decisión
        # del que gestiona el catálogo, no de este helper.
        return None
    return aprender_alias(
        db, tenant_id, texto, producto_id, origen=origen, user_id=user_id,
        cliente_id=cliente_id, sucursal_id=sucursal_id,
    )


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

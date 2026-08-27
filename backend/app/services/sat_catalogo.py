"""Búsqueda, validación y sugerencia de claves SAT contra el catálogo OFICIAL.

Las tablas `sat_clave_prodserv` / `sat_clave_unidad` (migración 0041, sembradas
del catCFDI oficial) son la ÚNICA fuente: aquí no se inventa ninguna clave.

- `buscar_claves` / `buscar_unidades`: FTS en español + ILIKE por token (el FTS
  no atrapa variantes como "saladett"/"saladette").
- `validar_clave` / `validar_unidad`: ¿existe en el catálogo?
- `sugerir_batch`: para N productos en UNA pasada — candidatos por texto y, si
  hay IA disponible, una sola llamada que ELIGE entre esos candidatos (nunca
  fuera de ellos). Sin IA, gana el mejor candidato del FTS. Sin candidatos,
  la genérica 01010101.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import SatClaveProdServ, SatClaveUnidad

logger = logging.getLogger(__name__)

GENERICA_PRODSERV = "01010101"   # "No existe en el catálogo" (comodín del SAT)
GENERICA_UNIDAD = "H87"          # Pieza

# La MISMA expresión que indexa la migración 0041 (GIN).
_TS = "to_tsvector('spanish', coalesce(descripcion,'') || ' ' || coalesce(palabras_similares,''))"

_STOP = {
    "de", "del", "la", "el", "los", "las", "a", "en", "con", "y", "o", "para",
    "kg", "gr", "ml", "lt", "pza", "pz", "mm", "cm",
}


def _tokens(texto: str) -> list[str]:
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s).lower()
    # Solo palabras con contenido (sin números sueltos ni tallas).
    return [t for t in s.split() if len(t) >= 4 and t not in _STOP and not t.isdigit()]


def buscar_claves(db: Session, texto: str, *, limit: int = 10) -> list[dict]:
    """Candidatos [{clave, descripcion}] del catálogo para un texto libre."""
    texto = (texto or "").strip()
    if not texto:
        return []
    out: list[dict] = []
    vistas: set[str] = set()

    # 1) Si el texto ES una clave (prefijo numérico), busca por clave.
    solo_digitos = re.sub(r"\D", "", texto)
    if solo_digitos and solo_digitos == texto.strip():
        rows = (
            db.query(SatClaveProdServ)
            .filter(SatClaveProdServ.clave.like(f"{solo_digitos}%"))
            .order_by(SatClaveProdServ.clave)
            .limit(limit)
            .all()
        )
        return [{"clave": r.clave, "descripcion": r.descripcion} for r in rows]

    # 2) FTS en español, rankeado.
    rows = db.execute(
        sql(f"""
            SELECT clave, descripcion,
                   ts_rank({_TS}, plainto_tsquery('spanish', :q)) AS rank
            FROM sat_clave_prodserv
            WHERE {_TS} @@ plainto_tsquery('spanish', :q)
            ORDER BY rank DESC, clave
            LIMIT :n
        """),
        {"q": texto, "n": limit},
    ).fetchall()
    for r in rows:
        if r.clave not in vistas:
            out.append({"clave": r.clave, "descripcion": r.descripcion})
            vistas.add(r.clave)

    # 3) ILIKE por token (variantes que el stemmer no une), hasta llenar.
    for tk in _tokens(texto):
        if len(out) >= limit:
            break
        rows = db.execute(
            sql("""
                SELECT clave, descripcion FROM sat_clave_prodserv
                WHERE descripcion ILIKE :tk OR palabras_similares ILIKE :tk
                ORDER BY clave LIMIT :n
            """),
            {"tk": f"%{tk}%", "n": limit},
        ).fetchall()
        for r in rows:
            if r.clave not in vistas:
                out.append({"clave": r.clave, "descripcion": r.descripcion})
                vistas.add(r.clave)
            if len(out) >= limit:
                break
    return out


def buscar_unidades(db: Session, texto: str, *, limit: int = 10) -> list[dict]:
    texto = (texto or "").strip()
    if not texto:
        return []
    rows = (
        db.query(SatClaveUnidad)
        .filter(
            (SatClaveUnidad.clave.ilike(f"{texto}%"))
            | (SatClaveUnidad.nombre.ilike(f"%{texto}%"))
        )
        .order_by(SatClaveUnidad.clave)
        .limit(limit)
        .all()
    )
    return [{"clave": r.clave, "nombre": r.nombre} for r in rows]


def validar_clave(db: Session, clave: str) -> Optional[str]:
    """Descripción oficial si la clave existe en el catálogo; None si no."""
    if not (clave or "").strip():
        return None
    row = db.query(SatClaveProdServ).filter(SatClaveProdServ.clave == clave.strip()).one_or_none()
    return row.descripcion if row else None


def validar_unidad(db: Session, clave: str) -> Optional[str]:
    if not (clave or "").strip():
        return None
    row = (
        db.query(SatClaveUnidad)
        .filter(SatClaveUnidad.clave == clave.strip().upper())
        .one_or_none()
    )
    return row.nombre if row else None


# Unidad de venta normalizada → clave SAT (validada contra el catálogo al usarse).
UNIDAD_A_SAT = {
    "KILO": "KGM", "GRAMO": "GRM", "LITRO": "LTR", "MILILITRO": "MLT",
    "PIEZA": "H87", "CAJA": "XBX", "PAQUETE": "XPK", "BOLSA": "XBG",
    "COSTAL": "XSA", "BULTO": "XSA", "DOCENA": "DPC",
    "MANOJO": "H87", "MALLA": "XBG", "REJA": "XBX", "ATADO": "H87", "PAR": "PR",
}


def unidad_sat_para(db: Session, unidad_venta: str) -> str:
    """Clave de unidad SAT para una unidad de venta ('KILO' → KGM), validada."""
    clave = UNIDAD_A_SAT.get((unidad_venta or "").strip().upper(), GENERICA_UNIDAD)
    return clave if validar_unidad(db, clave) else GENERICA_UNIDAD


_SYSTEM_ELEGIR = """\
Eres un experto del catálogo c_ClaveProdServ del SAT (CFDI 4.0, México). Para \
cada producto de abarrotes/frutas/verduras se te dan OPCIONES del catálogo \
oficial. Elige la clave MÁS específica y correcta de ESAS opciones (jamás una \
clave que no esté en las opciones). Si ninguna opción corresponde razonablemente \
al producto, responde la genérica 01010101. Llama SIEMPRE a `elegir_claves`.\
"""

_TOOL_ELEGIR = {
    "name": "elegir_claves",
    "description": "Registra la clave elegida (de las opciones dadas) para cada producto.",
    "input_schema": {
        "type": "object",
        "properties": {
            "selecciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "clave": {"type": "string", "description": "Una de las opciones dadas, o 01010101."},
                    },
                    "required": ["nombre", "clave"],
                },
            },
        },
        "required": ["selecciones"],
    },
}


def sugerir_batch(db: Session, productos: list[dict]) -> list[dict]:
    """[{nombre, unidad}] → [{nombre, clave_sat, descripcion_sat, unidad_sat,
    unidad_sat_generica}] en una sola pasada, SIEMPRE dentro del catálogo.
    """
    if not productos:
        return []

    # Candidatos por texto para cada producto (una consulta por producto).
    candidatos: dict[str, list[dict]] = {}
    for p in productos:
        candidatos[p["nombre"]] = buscar_claves(db, p["nombre"], limit=8)

    # Elección: IA en UNA llamada si está disponible; si no, el mejor del FTS.
    eleccion: dict[str, str] = {}
    con_opciones = {n: c for n, c in candidatos.items() if c}
    if con_opciones and settings.ANTHROPIC_API_KEY:
        try:
            import anthropic

            lineas = []
            for nombre, cands in con_opciones.items():
                ops = "; ".join(f"{c['clave']}={c['descripcion']}" for c in cands)
                lineas.append(f"- {nombre} → opciones: {ops}")
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            with client.messages.stream(
                model=settings.SAT_AI_MODEL,
                max_tokens=16000,
                system=[{"type": "text", "text": _SYSTEM_ELEGIR,
                         "cache_control": {"type": "ephemeral"}}],
                tools=[_TOOL_ELEGIR],
                tool_choice={"type": "tool", "name": "elegir_claves"},
                messages=[{"role": "user", "content": "\n".join(lineas)}],
            ) as stream:
                resp = stream.get_final_message()
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "elegir_claves":
                    for s in (block.input.get("selecciones") or []):
                        if isinstance(s, dict):
                            eleccion[str(s.get("nombre", ""))] = str(s.get("clave", "")).strip()
        except Exception as exc:  # noqa: BLE001 — degradación al mejor candidato
            logger.warning("elección IA de claves SAT falló: %s", exc)

    out = []
    for p in productos:
        nombre = p["nombre"]
        cands = candidatos.get(nombre) or []
        validas = {c["clave"]: c["descripcion"] for c in cands}
        clave = eleccion.get(nombre, "")
        # Solo se aceptan claves de los candidatos (o la genérica).
        if clave not in validas and clave != GENERICA_PRODSERV:
            clave = cands[0]["clave"] if cands else GENERICA_PRODSERV
        out.append({
            "nombre": nombre,
            "clave_sat": clave,
            "descripcion_sat": validas.get(clave, "No existe en el catálogo"),
            "unidad_sat": unidad_sat_para(db, p.get("unidad") or ""),
            "unidad_sat_generica": GENERICA_UNIDAD,
        })
    return out

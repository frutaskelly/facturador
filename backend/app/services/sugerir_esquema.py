"""Qué esquema de impuesto le toca a cada producto, y a qué categoría existente
corresponde cada categoría del archivo.

Sin esquema de impuesto un producto NO se puede facturar bien (el CFDI saldría
sin IVA/IEPS), así que la importación no puede dejar 500 productos en blanco:
aquí se propone uno por producto entre los esquemas QUE YA TIENE el tenant.

La propuesta sale de dos fuentes, en este orden:
  1. Reglas fiscales mexicanas por clave SAT / nombre — deterministas y gratis:
     alimentos y bebidas no saborizadas → IVA 0%; abarrotes no comestibles
     (limpieza, plásticos, desechables) → IVA 16%; refrescos y botanas → IEPS.
  2. IA (una sola llamada para todo el lote) para lo que la regla no resuelve,
     eligiendo SIEMPRE entre los esquemas del tenant — nunca inventa tasas.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional
from uuid import UUID

from rapidfuzz import fuzz

from ..core.config import settings
from ..models import CategoriaProducto, EsquemaImpuesto

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


# ── Reglas fiscales (México, CFDI 4.0) ───────────────────────────────────────
# Familias del catálogo SAT c_ClaveProdServ que son ALIMENTO (IVA 0% en su
# presentación normal). 50xxxxxx = alimentos, bebidas y tabaco.
_PREFIJOS_ALIMENTO = ("501", "502", "503", "504", "505", "506", "507", "508", "509")

# EXCEPCIONES que viven DENTRO del segmento 50 y NO son tasa 0% (LIVA 2-A-I-b):
# el segmento incluye alcohol, tabaco, refrescos, jugos y chicles. Sin esto,
# una botella de vino o un jugo se timbrarían sin IVA.
_CLAVE_HIELO = "50202302"                       # 2-A-I-c: hielo sí es 0%
_PREFIJOS_ALCOHOL = ("502022",)                 # bebidas alcohólicas, cerveza, vino
_PREFIJOS_TABACO = ("5021",)                    # cigarros y puros
_PREFIJOS_JUGO = ("502024", "502025", "502026", "502027", "502028", "502029",
                  "502030", "50202304", "50202305")
_CLAVE_CHICLE = "50161815"                      # 2-A-I-b-5: goma de mascar
# Agua: 16% en envase menor a 10 L o gaseosa; 0% el garrafón. Lo decide el humano.
_PREFIJOS_AGUA = ("50202301", "50202310")
# Pistas de texto para lo mismo cuando la clave es genérica.
_PISTAS_REVISAR = (
    "cerveza", "tequila", "vino", "ron", "whisky", "vodka", "mezcal", "licor",
    "brandy", "champagne", "cigarro", "cigarros", "tabaco", "jugo", "jugos",
    "nectar", "chicle", "chicles", "goma de mascar",
)


def _tiene_pista(texto: str, pistas: tuple[str, ...]) -> bool:
    """¿El texto contiene alguna pista como PALABRA completa?

    Con `in` a secas, "ron" casaba dentro de "PIMIENTO MORRON" y mandaba cuatro
    verduras a revisión de bebidas alcohólicas.
    """
    return any(re.search(rf"\b{re.escape(p)}\b", texto) for p in pistas)
# No comestibles frecuentes en una lista de abarrotes → IVA 16%.
_PREFIJOS_NO_ALIMENTO = ("47", "48", "52", "53", "56", "14", "24", "31", "41", "60")

# Palabras que delatan el trato fiscal cuando la clave SAT es genérica.
_PISTAS_16 = (
    "jabon", "detergente", "cloro", "pinol", "fabuloso", "limpiador", "escoba",
    "trapeador", "fibra", "estropajo", "bolsa", "plato", "vaso", "cuchara",
    "tenedor", "servilleta", "papel higienico", "toalla", "aluminio", "plastico",
    "guante", "cubrebocas", "desengrasante", "sarricida", "insecticida",
    "cuaderno", "pluma", "desechable", "ziploc", "film", "egapack", "charola",
)
_PISTAS_IEPS_REFRESCO = ("refresco", "coca", "pepsi", "jarritos", "agua saborizada", "energizante")
_PISTAS_IEPS_BOTANA = ("botana", "papas fritas", "sabritas", "churro", "cacahuate japones", "chicharron")


def _familia_fiscal(nombre: str, clave_sat: str) -> str:
    """Familia fiscal del producto.

    'IVA0' | 'IVA16' | 'IEPS_REFRESCO' | 'IEPS_BOTANA' | 'REVISAR' (la ley
    depende de un dato que el archivo no trae — envase, grados, azúcares — así
    que lo decide una persona) | '' (sin regla, se intenta con IA).
    """
    n = _norm(nombre)
    clave_pre = (clave_sat or "").strip()
    # 1) Excepciones del segmento 50 que NO son tasa 0%: nunca se adivinan.
    if clave_pre == _CLAVE_HIELO:
        return "IVA0"
    if clave_pre and (
        clave_pre.startswith(_PREFIJOS_ALCOHOL)
        or clave_pre.startswith(_PREFIJOS_TABACO)
        or clave_pre.startswith(_PREFIJOS_JUGO)
        or clave_pre == _CLAVE_CHICLE
        or clave_pre.startswith(_PREFIJOS_AGUA)
    ):
        return "REVISAR"
    # 2) El IEPS no se distingue por clave SAT (una botana y una galleta
    #    comparten familia), así que aquí sí manda el nombre.
    if _tiene_pista(n, _PISTAS_IEPS_REFRESCO):
        return "IEPS_REFRESCO"
    if _tiene_pista(n, _PISTAS_IEPS_BOTANA):
        return "IEPS_BOTANA"
    # 3) La clave SAT del archivo, cuando dice algo.
    clave = clave_pre
    if clave and clave != "01010101":
        if clave.startswith(_PREFIJOS_ALIMENTO):
            return "IVA0"
        if clave.startswith(_PREFIJOS_NO_ALIMENTO):
            return "IVA16"
    # 4) Sin clave útil, el nombre es lo único que queda.
    if _tiene_pista(n, _PISTAS_REVISAR):
        return "REVISAR"
    if _tiene_pista(n, _PISTAS_16):
        return "IVA16"
    return ""


def _elegir_esquema(familia: str, esquemas: list[EsquemaImpuesto]) -> Optional[EsquemaImpuesto]:
    """El esquema del tenant que corresponde a la familia fiscal."""
    def sin_ieps(e) -> bool:
        return float(e.ieps_tasa or 0) == 0 and float(e.ieps_cuota or 0) == 0

    if familia == "IVA0":
        cands = [e for e in esquemas if float(e.iva_tasa or 0) == 0 and sin_ieps(e)]
    elif familia == "IVA16":
        cands = [e for e in esquemas if abs(float(e.iva_tasa or 0) - 0.16) < 0.001 and sin_ieps(e)]
    elif familia == "IEPS_REFRESCO":
        # Bebida saborizada: 16% de IVA + IEPS por cuota ($/litro).
        cands = [e for e in esquemas
                 if float(e.ieps_cuota or 0) > 0 and abs(float(e.iva_tasa or 0) - 0.16) < 0.001] \
                or [e for e in esquemas if float(e.ieps_cuota or 0) > 0]
    elif familia == "IEPS_BOTANA":
        # Botana/dulce: SIGUE siendo alimento → IVA 0%, más IEPS 8% por tasa.
        # (La LIVA 2-A-I-b no excluye las botanas de la tasa cero; solo bebidas,
        # jarabes, caviar, saborizantes, chicles y alimento para mascotas.)
        cands = [e for e in esquemas
                 if float(e.ieps_tasa or 0) > 0 and float(e.iva_tasa or 0) == 0]
    else:
        return None
    # A igualdad, el de código más corto (los "IVA0"/"IVA16" del alta estándar).
    return sorted(cands, key=lambda e: (len(e.codigo or ""), e.codigo or ""))[0] if cands else None


_SYSTEM_IA = """\
Eres contador fiscal mexicano. Para cada producto de una lista de abarrotes, \
frutas, verduras y artículos de limpieza, elige el ESQUEMA DE IMPUESTO que le \
corresponde de entre los esquemas que te doy (son los del negocio).

Reglas del IVA en México:
- Alimentos y bebidas NO saborizadas para consumo humano: IVA 0% (tasa cero).
  Incluye frutas, verduras, carnes, lácteos, huevo, harinas, pan, tortillas, \
aceite comestible, azúcar, café, especias, abarrotes comestibles.
- NO comestibles: IVA 16%. Artículos de limpieza, plásticos, desechables, \
papelería, utensilios.
- Bebidas saborizadas con azúcar (refrescos): IVA 16% + IEPS por cuota.
- Botanas, dulces, chocolates y galletas dulces de alta densidad calórica \
(papas fritas, cacahuate japonés, chicharrón): siguen siendo alimento, IVA 0% \
+ IEPS 8% por tasa.
- Jugos y néctares, chicles, bebidas alcohólicas y tabaco: NO son tasa 0%.
- Agua natural sin gas en presentaciones mayores a 10 L: IVA 0%.

Responde SIEMPRE con la herramienta `asignar_esquemas`, usando SOLO códigos de \
esquema de la lista dada. Si ninguno aplica razonablemente, deja el código vacío.\
"""

_TOOL_IA = {
    "name": "asignar_esquemas",
    "description": "Asigna a cada producto el código de esquema de impuesto que le corresponde.",
    "input_schema": {
        "type": "object",
        "properties": {
            "asignaciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "codigo_esquema": {"type": "string", "description": "Código de la lista dada, o vacío."},
                    },
                    "required": ["nombre", "codigo_esquema"],
                },
            },
        },
        "required": ["asignaciones"],
    },
}


# Qué esquema le falta al negocio cuando la regla sí decidió la familia.
_FALTA = {
    "IVA0": "No tienes un esquema de IVA 0% (alimentos): créalo en Esquemas de impuesto.",
    "IVA16": "No tienes un esquema de IVA 16%: créalo en Esquemas de impuesto.",
    "IEPS_REFRESCO": "Bebida saborizada: necesitas un esquema con IVA 16% e IEPS por CUOTA ($/litro).",
    "IEPS_BOTANA": "Botana/dulce: necesitas un esquema con IVA 0% e IEPS por TASA (8%).",
}


def sugerir_esquemas(
    productos: list[dict], esquemas: list[EsquemaImpuesto], *, usar_ia: bool = True
) -> list[dict]:
    """[{nombre, clave_sat, categoria}] → [{nombre, esquema_id, esquema_codigo,
    origen}] donde origen es 'regla' | 'ia' | '' (sin sugerencia).
    """
    if not productos or not esquemas:
        return [{"nombre": p.get("nombre", ""), "esquema_id": None,
                 "esquema_codigo": "", "origen": "", "motivo": ""} for p in productos]

    por_codigo = {(e.codigo or "").strip().upper(): e for e in esquemas}
    out: list[dict] = []
    sin_resolver: list[dict] = []
    for p in productos:
        familia = _familia_fiscal(p.get("nombre", ""), p.get("clave_sat", ""))
        esq = _elegir_esquema(familia, esquemas) if familia else None
        if esq is not None:
            out.append({"nombre": p.get("nombre", ""), "esquema_id": esq.id,
                        "esquema_codigo": esq.codigo, "origen": "regla", "motivo": ""})
        elif familia == "REVISAR":
            # La ley depende de un dato que el archivo no trae: no se adivina.
            out.append({"nombre": p.get("nombre", ""), "esquema_id": None,
                        "esquema_codigo": "", "origen": "revisar",
                        "motivo": "Su trato fiscal depende del envase o del "
                                  "contenido (agua, jugos, alcohol, tabaco, "
                                  "chicles): elígelo tú."})
        elif familia:
            # La regla decidió, pero el negocio no tiene un esquema así.
            out.append({"nombre": p.get("nombre", ""), "esquema_id": None,
                        "esquema_codigo": "", "origen": "falta_esquema",
                        "motivo": _FALTA[familia]})
        else:
            out.append({"nombre": p.get("nombre", ""), "esquema_id": None,
                        "esquema_codigo": "", "origen": "", "motivo": ""})
            sin_resolver.append(p)

    # Lo que la regla no resolvió va a la IA, en UNA sola llamada.
    if usar_ia and sin_resolver and settings.ANTHROPIC_API_KEY:
        try:
            import anthropic

            catalogo = "\n".join(
                f"- {e.codigo}: {e.nombre} (IVA {float(e.iva_tasa or 0) * 100:.0f}%"
                + (f", IEPS tasa {float(e.ieps_tasa or 0) * 100:.0f}%" if float(e.ieps_tasa or 0) else "")
                + (f", IEPS cuota {float(e.ieps_cuota or 0)}/L" if float(e.ieps_cuota or 0) else "")
                + ")"
                for e in esquemas
            )
            lineas = "\n".join(
                f"- {p.get('nombre','')}"
                + (f" [clave SAT {p['clave_sat']}]" if p.get("clave_sat") else "")
                + (f" [categoría {p['categoria']}]" if p.get("categoria") else "")
                for p in sin_resolver[:400]
            )
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            with client.messages.stream(
                model=settings.SAT_AI_MODEL,
                max_tokens=16000,
                system=[{"type": "text", "text": _SYSTEM_IA,
                         "cache_control": {"type": "ephemeral"}}],
                tools=[_TOOL_IA],
                tool_choice={"type": "tool", "name": "asignar_esquemas"},
                messages=[{"role": "user", "content":
                           f"Esquemas del negocio:\n{catalogo}\n\nProductos:\n{lineas}"}],
            ) as stream:
                resp = stream.get_final_message()
            elecciones: dict[str, str] = {}
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "asignar_esquemas":
                    for a in (block.input.get("asignaciones") or []):
                        if isinstance(a, dict):
                            elecciones[str(a.get("nombre", ""))] = str(a.get("codigo_esquema", "")).strip().upper()
            for fila in out:
                if fila["esquema_id"] is None:
                    esq = por_codigo.get(elecciones.get(fila["nombre"], ""))
                    if esq is not None:
                        fila["esquema_id"] = esq.id
                        fila["esquema_codigo"] = esq.codigo
                        fila["origen"] = "ia"
        except Exception as exc:  # noqa: BLE001 — degradación: se queda la regla
            logger.warning("sugerencia IA de esquemas falló: %s", exc)
    return out


# ── Match de categorías del archivo contra las del tenant ────────────────────
def match_categorias(
    nombres_archivo: list[str], categorias: list[CategoriaProducto]
) -> list[dict]:
    """Para cada categoría del archivo, la categoría EXISTENTE que le
    corresponde (si la hay) o la marca de que sería nueva.

    Evita el duplicado tonto: "ABARROTE" del archivo contra "Abarrotes" del
    sistema son la misma, y crear la segunda parte el catálogo en dos.
    """
    out: list[dict] = []
    for nombre in nombres_archivo:
        n = _norm(nombre)
        mejor: Optional[CategoriaProducto] = None
        mejor_score = 0
        for c in categorias:
            cn = _norm(c.nombre)
            if not cn:
                continue
            if cn == n:
                mejor, mejor_score = c, 100
                break
            # Plurales y variantes: "abarrote" ↔ "abarrotes". Se mide con
            # token_sort (SIMÉTRICO): token_set ignora los tokens sobrantes y
            # daba 100 a "LACTEOS Y EMBUTIDOS" ↔ "Lácteos", que son distintas
            # (la del archivo es más amplia) — y las habría fundido en una.
            score = int(fuzz.token_sort_ratio(n, cn))
            if score > mejor_score:
                mejor, mejor_score = c, score
        usar = mejor is not None and mejor_score >= 88
        out.append({
            "nombre_archivo": nombre,
            "categoria_id": mejor.id if usar else None,
            "categoria_nombre": mejor.nombre if usar else "",
            "score": mejor_score if usar else 0,
            "es_nueva": not usar,
        })
    return out

"""Revisión del catálogo: qué productos son el mismo y cómo quedarían.

Reglas del dueño (30-sep … 2-oct-2026):
  1. Un producto es UNA cosa física y su nombre no lleva la unidad: «ESPINACA»,
     no «ESPINACA PZA» ni «ESPINACA MANOJO DE 1 KG».
  2. La unidad vive en la columna Unidad y cada unidad lleva SU clave de SAE
     (SANDIA: KILO→SANDIAKG, PIEZA→SANDIAPZ).
  3. Si el nombre trae gramaje o empaque (PAQ 454 GR, BOLSA…) se vende por
     pieza, nunca por kilo.
  4. Variedad o gramaje distinto es OTRO producto (ESPINACA BABY ≠ ESPINACA;
     FRIJOL 900 G no se une con FRIJOL a granel).

Este módulo es puro: recibe los productos ya leídos y devuelve los grupos con
su propuesta. No escribe nada; las decisiones las guarda el endpoint y unirlos
es un paso aparte.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# Token de unidad suelto en el nombre → unidad del sistema. «25 KG» (número +
# unidad) es un TAMAÑO, no la unidad de venta, y se queda en el nombre.
UNIDAD_TOKEN = {
    "KG": "KILO", "KGS": "KILO", "KILO": "KILO", "KILOS": "KILO",
    "PZ": "PIEZA", "PZA": "PIEZA", "PZAS": "PIEZA", "PZS": "PIEZA", "PIEZA": "PIEZA", "PIEZAS": "PIEZA",
    "CAJA": "CAJA", "CJ": "CAJA", "CJA": "CAJA", "CAJAS": "CAJA",
    "MANOJO": "MANOJO", "MANOJOS": "MANOJO", "MJ": "MANOJO",
    "LITRO": "LITRO", "LITROS": "LITRO", "LT": "LITRO", "LTS": "LITRO",
}
_NUMERO = re.compile(r"\d+([.,]\d+)?")
# Sólo el paréntesis con número y medida es calibre o peso: «(11 - 12 KG)» se
# va; «(CODO)» o «(UVA)» se queda porque es otro producto.
_PARENTESIS_PESO = re.compile(
    r"\([^)]*\d\s*(?:KG|KGS|KILOS?|G|GR|GRS|GRAMOS|LT|LTS|L|ML)\b[^)]*\)", re.I)
# «MANOJO DE 1 KG» en un producto que se vende por kilo: el kilo ya lo dice la
# unidad (dueño, 2-oct-2026).
_UN_KILO_AL_FINAL = re.compile(r"\s*\b(DE\s+)?1\s+(KG|KGS|KILO|KILOS)$")
GRAMAJE = re.compile(r"\b\d+([.,]\d+)?\s*(G|GR|GRS|GRAMOS|ML|MILILITROS)\b")
EMPAQUE = re.compile(r"\b(PAQ|PAQUETE|BOLSA|FRASCO|LATA|BOTELLA|SOBRE|ENVASE)\b")
CLAVE_VIEJA = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-\d+$")


def norm(texto: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return " ".join(s.split())


def raiz_y_unidad(nombre: str, unidad_base: str) -> tuple[str, Optional[str]]:
    """«SANDIA PZA» → («SANDIA», PIEZA); «ESPINACA MANOJO DE 1 KG» en KILO →
    («ESPINACA», MANOJO). La unidad que devuelve es la que DICE el nombre; con
    la que se une un producto es la suya de verdad (unidad base y presentaciones)."""
    toks = norm(_PARENTESIS_PESO.sub(" ", nombre or "")).split()
    unidad, resto = None, []
    for i, t in enumerate(toks):
        if t in UNIDAD_TOKEN and not (i > 0 and _NUMERO.fullmatch(toks[i - 1])):
            unidad = unidad or UNIDAD_TOKEN[t]
        else:
            resto.append(t)
    raiz = " ".join(resto)
    if (unidad_base or "").upper() == "KILO":
        raiz = _UN_KILO_AL_FINAL.sub("", raiz).strip()
    # «ESPINACA DE» tras quitar la unidad del medio
    raiz = re.sub(r"\s+(DE|CON|EN|X)$", "", raiz)
    return raiz, unidad


# «NUEZ (1000G)», «CANELA PAQ 1 KG»: un empaque de un kilo SÍ se vende por kilo.
_DE_UN_KILO = re.compile(r"\b(1\s*(KG|KGS|KILO|KILOS)|1000\s*(G|GR|GRS|GRAMOS))\b")


def sin_unidad(nombre: str) -> str:
    """El nombre sin la palabra de unidad suelta, pero con su gramaje y su
    paréntesis: para un empaque, «(500 G)» es lo que lo distingue."""
    toks = norm(nombre).split()
    return " ".join(t for i, t in enumerate(toks)
                    if not (t in UNIDAD_TOKEN and not (i > 0 and _NUMERO.fullmatch(toks[i - 1]))))


def es_empaque(nombre: str) -> bool:
    n = norm(nombre)
    return bool(GRAMAJE.search(n) or EMPAQUE.search(n)) and not _DE_UN_KILO.search(n)


def es_clave_vieja(clave: str) -> bool:
    return bool(CLAVE_VIEJA.match((clave or "").strip().upper()))


@dataclass
class ProductoRev:
    id: str
    sku: str
    nombre: str
    unidad_base: str
    presentaciones: dict
    clave_sae: Optional[str]
    clave_sat: str = ""
    esquema: str = ""
    categoria: str = ""
    alta: Optional[date] = None
    ventas: int = 0                      # partidas no canceladas, último año
    precios: int = 0                     # en listas vigentes
    alias: int = 0
    catalogo: int = 0
    # unidad → folios de remisiones en BORRADOR que la usan
    borradores: dict = field(default_factory=dict)

    def unidades(self) -> dict[str, str]:
        """unidad → clave SAE ('' si no tiene); la base primero."""
        base = (self.unidad_base or "KILO").upper()
        out = {base: (self.clave_sae or "").strip().upper()}
        for k, v in (self.presentaciones or {}).items():
            if k == base:
                continue
            out[k] = (str(v.get("clave_sae") or "").strip().upper() if isinstance(v, dict) else "")
        return out


@dataclass
class Catalogo:
    """Lo que se sabe de cada clave: si existe en SAE y cuántas veces se facturó."""
    en_sae: dict[str, list[str]]         # clave → empresas donde está activa
    facturas: dict[str, int]

    def existe(self, clave: str) -> bool:
        return bool(self.en_sae.get((clave or "").strip().upper()))

    def info(self, clave: str) -> dict:
        c = (clave or "").strip().upper()
        return {"clave": c, "en_sae": self.en_sae.get(c, []), "facturas": self.facturas.get(c, 0),
                "formato_viejo": es_clave_vieja(c)}


def _orden_queda(p: ProductoRev, raiz: str):
    # El de nombre limpio, luego el que más vende, luego el más viejo.
    return (norm(p.nombre) != raiz, -p.ventas, p.alta or date.max, p.sku)


def proponer(miembros: list[ProductoRev], cat: Catalogo, *, tipo: str, raiz: str,
             queda_sku: Optional[str] = None, nombre_final: Optional[str] = None,
             claves: Optional[dict[str, str]] = None, quitar: Optional[list[str]] = None) -> dict:
    """La propuesta de un grupo. Los opcionales son lo que la persona cambió en
    pantalla; sin ellos sale la propuesta automática."""
    ordenados = sorted(miembros, key=lambda p: _orden_queda(p, raiz))
    queda = next((p for p in ordenados if p.sku == queda_sku), ordenados[0])
    if not nombre_final:
        nombre_final = queda.nombre if norm(queda.nombre) == raiz else raiz
    nombre_final = " ".join(nombre_final.split()).upper()

    # Unidades resultantes y sus candidatas: cada miembro aporta SUS unidades.
    candidatas: dict[str, list[tuple[str, str]]] = {}
    for p in [queda, *[m for m in ordenados if m is not queda]]:
        for u, c in p.unidades().items():
            lista = candidatas.setdefault(u, [])
            if c and all(c != x for x, _ in lista):
                lista.append((c, p.sku))
    base = (queda.unidad_base or "KILO").upper()

    alertas: list[str] = []
    empaque = es_empaque(nombre_final)
    if quitar is None:
        quitar = ["KILO"] if empaque and "KILO" in candidatas and len(candidatas) > 1 else []
        if empaque and candidatas.keys() == {"KILO"}:
            quitar = ["KILO"]
    quitar = [u for u in quitar if u in candidatas]
    if empaque and "KILO" in candidatas and "KILO" not in quitar:
        alertas.append("El nombre trae gramaje o empaque y se sigue vendiendo por KILO")

    nueva_base = base
    if base in quitar:
        restantes = [u for u in candidatas if u not in quitar]
        _, dice = raiz_y_unidad(queda.nombre, base)
        if dice and dice != "KILO" and dice not in quitar:
            # «TE LIMON MANOJO DE 100G» en KILO pasa a MANOJO
            nueva_base = dice
        elif restantes:
            nueva_base = "PIEZA" if "PIEZA" in restantes else restantes[0]
        else:
            nueva_base = "PIEZA"
        if not candidatas.get(nueva_base):
            # Conserva su clave, como los 25 que pasaron de KILO a PIEZA el 1-oct.
            candidatas[nueva_base] = list(candidatas.get(base, []))
    unidades = []
    for u in sorted(set(candidatas) - set(quitar), key=lambda k: (k != nueva_base, k)):
        opciones = candidatas.get(u, [])
        elegida = (claves or {}).get(u)
        if elegida is None and opciones:
            # Gana la que existe en SAE; luego la de formato nuevo; luego la más facturada.
            elegida = max(opciones, key=lambda o: (cat.existe(o[0]), not es_clave_vieja(o[0]),
                                                    cat.facturas.get(o[0], 0)))[0]
        elegida = (elegida or "").strip().upper()
        de = next((s for c, s in opciones if c == elegida), None)
        unidades.append({
            "unidad": u, **cat.info(elegida), "de_sku": de,
            "alternativas": [{**cat.info(c), "de_sku": s} for c, s in opciones if c != elegida],
        })
        if not elegida:
            alertas.append(f"{u}: falta escoger la clave de SAE")
        elif not cat.existe(elegida):
            alertas.append(f"{u}: la clave {elegida} no existe en SAE")
        if (u == "KILO" and re.search(r"(PZ|PZA)$", elegida)) or (u == "PIEZA" and elegida.endswith("KG")):
            alertas.append(f"{u} con clave {elegida}: la clave dice otra unidad")
        otras = [c for c, _ in opciones if c != elegida]
        if otras and tipo != "EMPAQUE":
            alertas.append(f"{u}: se queda {elegida or '—'}; también existían {', '.join(otras)}")

    por_clave: dict[str, list[str]] = {}
    for x in unidades:
        if x["clave"]:
            por_clave.setdefault(x["clave"], []).append(x["unidad"])
    for c, us in por_clave.items():
        if len(us) > 1:
            alertas.append(f"{' y '.join(us)} usan la misma clave {c}: ¿son la misma unidad? Quita la que sobre")

    bloqueos = []
    for u in quitar:
        folios = sorted({f for p in miembros for f in p.borradores.get(u, [])})
        if folios:
            muestra = ", ".join(folios[:5]) + (f" y {len(folios) - 5} más" if len(folios) > 5 else "")
            bloqueos.append(f"Hay {len(folios)} remisiones en borrador que venden {u}: {muestra}. "
                            "Corrígelas antes de quitar esa unidad.")

    esquemas = {p.esquema for p in miembros if p.esquema}
    if len(esquemas) > 1:
        alertas.append("Esquemas de impuesto distintos (" + " / ".join(sorted(esquemas)) + ")")
    familias = {p.clave_sat[:6] for p in miembros if p.clave_sat and p.clave_sat != "01010101"}
    if len(familias) > 1:
        alertas.append("Clave SAT de otra familia (" + ", ".join(sorted({p.clave_sat for p in miembros})) +
                       "): ¿es otro producto?")
    _, dice = raiz_y_unidad(nombre_final, nueva_base)
    if dice:
        alertas.append(f"El nombre que queda todavía dice {dice}: la unidad va en su columna")
    abs_borr = [p for p in miembros if p is not queda and any(p.borradores.values())]
    if abs_borr:
        alertas.append("Sus remisiones en borrador se quedan como están (sólo cambian las nuevas): " +
                       ", ".join(p.sku for p in abs_borr))

    return {
        "queda_sku": queda.sku,
        "nombre_final": nombre_final,
        "unidad_base": nueva_base,
        "unidades": unidades,
        "quitar": quitar,
        "se_unen": [p.sku for p in ordenados if p is not queda],
        "alertas": alertas,
        "bloqueos": bloqueos,
    }


def firma(miembros: list[ProductoRev]) -> str:
    """Quiénes forman el grupo y con qué unidades: si cambia, la decisión vieja ya
    no aplica tal cual."""
    return "|".join(f"{p.sku}:{','.join(sorted(p.unidades()))}" for p in sorted(miembros, key=lambda p: p.sku))


def es_exclusivo(p: ProductoRev) -> bool:
    """Clave de formato viejo (CEBO-FRUT-109, ZANA-FRUT-508) en cualquiera de sus
    unidades: es el artículo de Balles y Jubran en SAE. Se queda como está y no
    se une con nada (dueño, 2-oct-2026)."""
    return any(es_clave_vieja(c) for c in p.unidades().values())


def armar_grupos(productos: list[ProductoRev], cat: Catalogo) -> list[dict]:
    """Todos los grupos del catálogo: productos con la misma raíz de nombre y, aparte,
    los empaques que se venden por kilo sin tener con quién unirse. Los exclusivos
    de Balles y Jubran (`es_exclusivo`) no entran a ningún grupo."""
    productos = [p for p in productos if not es_exclusivo(p)]
    por_raiz: dict[str, list[ProductoRev]] = {}
    for p in productos:
        raiz, _ = raiz_y_unidad(p.nombre, p.unidad_base)
        if raiz:
            por_raiz.setdefault(raiz, []).append(p)

    grupos = []
    agrupados = set()
    for raiz, ms in por_raiz.items():
        if len(ms) < 2:
            continue
        agrupados.update(p.sku for p in ms)
        gemelos = len({norm(p.nombre) for p in ms}) == 1 and len({tuple(sorted(p.unidades())) for p in ms}) == 1
        tipo = "GEMELOS" if gemelos else "UNIDADES"
        grupos.append({"clave": f"R:{raiz}", "tipo": tipo, "raiz": raiz, "miembros": ms})

    for p in productos:
        if p.sku in agrupados or "KILO" not in p.unidades() or not es_empaque(p.nombre):
            continue
        grupos.append({"clave": f"E:{p.sku}", "tipo": "EMPAQUE",
                       "raiz": sin_unidad(p.nombre) or norm(p.nombre), "miembros": [p]})
    return grupos

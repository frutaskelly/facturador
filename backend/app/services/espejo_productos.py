"""¿Qué producto es cada partida de una factura que llega del SAE?

POR QUÉ EXISTE (2-oct-2026). El espejo (`POST /facturas/espejo`, que el reloj
de `espejo_sae` llama cada 30 s) solo cruzaba la CVE_ART contra
`producto_clientes.codigo_cliente` del cliente. Nunca miró `productos.clave_sae`
ni la clave de cada presentación, y el resultado medido en producción fue
81,427 partidas sin producto: el 61% del espejo, 22,758 de las 38,281 de
ZEHMOVH. Mini Conta recibe esas partidas sin sku y la merma de Tabasco no se
puede medir.

LA REGLA DEL DUEÑO (2-oct-2026): «todo producto tiene clave SAE, se puede
unificar así». Producto + presentación = clave: la de la presentación si la
trae, si no la del producto. Una clave puede amparar VARIOS productos (los
gemelos; la 0085 quitó el índice único), así que hace falta desempate.

EL ORDEN, por clave y una sola vez por factura (todas las partidas con la misma
clave quedan con el mismo producto). Gana el primer nivel que decida:

  0. EL NOMBRE DEL SAE (decisión del dueño, 2-oct-2026: «gana el SAE»; la
     factura es la verdad final). Solo en claves gemelas —dos o más productos
     ACTIVOS la traen—: si la descripción de la partida del SAE se llama como
     uno o más de ellos (`norm_nombre`: sin acentos ni palabras de unidad),
     los candidatos se RESTRINGEN a esos gemelos activos nombrados. Si queda
     uno, gana sin más: TOMATEVERDELIMKG dice «TOMATE VERDE LIMPIO KG» y es
     el 00010049 TOMATE VERDE LIMPIO, aunque el uso dijera 00010048 TOMATE
     VERDE GRANDE Y LIMPIO, y aunque la remisión ligada o la decisión previa
     traigan otro producto — también uno que se llame igual pero no sea
     gemelo activo de la clave: un gemelo desactivado (00011014 HIERBABUENA)
     o el que trae OTRA clave (00010058 CALABAZA DE CASTILLA, de
     CALABAZACASTIKG, en partidas CALABAZACASTILKG; revisión del 2-oct). Si
     quedan varios (ESPINACAPZA: «ESPINACA PZA» son el 00010761 y el
     00010965), el resto de la regla —remisión ligada, decisión previa,
     catálogo, uso, sku— desempata SOLO entre ellos y el MANOJO DE 1 KG ya no
     compite. Una clave de un solo producto activo no cambia (AJOKG sigue
     siendo el AJO de la remisión ligada), ni la que manda la tabla de claves
     distintas por empresa, y un nombre que no casa con ningún gemelo activo
     no cambia nada.
  1. REMISIÓN LIGADA. La remisión ligada a ESTA factura (`remisiones.factura_id`)
     trae uno de los productos que la clave puede ser: los que la traen (en
     `clave_sae` o en una presentación, activos o no) y el que el catálogo del
     cliente llama con ese código. Es evidencia de ESA factura y por eso va
     antes que todo: el export manda la remisión con el código del cliente
     cuando lo hay (`codigo_cliente_de`), así que la partida AJOKG de una
     remisión con el 00000284 AJO es ese AJO aunque AJOKG sea la clave del
     00010472 AJO KG (revisión del 2-oct: con la clave única primero, lo
     remisionado y lo facturado quedaban con productos distintos). Si el
     nivel 0 dejó varios nombrados, solo cuenta si trae a uno de ellos.
  2. LA DECISIÓN PREVIA: el producto que la partida ya tenía en esa clave.
     Ver «una partida ligada no cambia sola» abajo. Igual que el 1: con
     varios nombrados, solo si es uno de ellos.
  3. La clave significa OTRA cosa en esa empresa de SAE: manda la tabla
     `CLAVES_DISTINTAS_POR_EMPRESA` (CALABAZACASTILKG en la 03). Solo en el
     inquilino dueño del SAE (`ESPEJO_SAE_TENANT_ID`): los skus son de cada
     inquilino y el 00010233 de otro puede ser cualquier cosa.
  4. Un solo producto ACTIVO trae la clave.
  5. Varios (gemelos) — gana el primero que se despegue:
     a. está en el catálogo del cliente (`producto_clientes`);
     b. es el más remisionado en la serie (remisiones RZ…/RF… de esa serie);
     c. es el más remisionado en todo el inquilino;
     d. el sku menor.
  6. Ninguno trae la clave: lo de antes, el `codigo_cliente` del catálogo del
     cliente — pero ya no hacia productos borrados (ligaba al 00010229 NUEZ,
     borrado). Los desactivados sí cuentan aquí, como antes.

EL USO SE CUENTA EN REMISIONES, no en facturas. Las partidas de factura espejo
con producto no son evidencia de qué se vendió: las puso el cruce viejo por
código del cliente o esta misma regla. Contarlas hacía que la regla se
reforzara sola —LIMONSINSEMILLKG en ZEHMOHOS: las remisiones dan 202 a 00000352
LIMON SIN SEMILLA contra 48 a 00000353 LIMON, pero 781 partidas espejo ya
ligadas al 353 volteaban el desempate— y hacía que el backfill decidiera con
otros números que el endpoint en cuanto terminaba de ligar sus ~80k partidas.
Las facturas nativas salen de remisiones (ya contadas). De paso, la consulta
usa `ix_lineas_remision_producto_id`; sobre `lineas_factura` era un seq scan.

UNA PARTIDA LIGADA NO CAMBIA SOLA. Cada reenvío de una factura (el cuadre, los
abonos, las cancelaciones) borra y recrea sus partidas. Sin memoria, un abono
le cambiaba el producto a una venta de hace meses: la ligada por código del
cliente pasaba al de la clave y la que tenía un gemelo que apagó la fusión de
SANDIA pasaba al sobreviviente — y Mini Conta, que guarda su copia, no se
entera. Por eso el nivel 2 conserva el producto que la factura ya tenía en esa
clave, sea o no de los que hoy traen la clave y aunque hoy esté desactivado o
borrado (el espejo viejo ligaba borrados por código; eso tampoco se corrige
con un abono). Solo lo mueven dos cosas de ESA factura: una remisión ligada y,
desde «gana el SAE», el nombre del artículo cuando nombra a gemelos activos de
la clave y la partida no está en uno de ellos (nivel 0) — el reenvío la pasa
al gemelo que el SAE nombra, y de ahí no se vuelve a mover mientras el SAE lo
siga llamando igual. Las facturas NUEVAS
sí van por la clave (SANDIAPZ al sobreviviente de la fusión, que «únicamente
impacta a nuevas remisiones»). Re-decidir lo ya
ligado (un cambio en el catálogo, una fila nueva en la tabla de claves) es a
propósito y con lista: `scripts/backfill_espejo_producto_por_clave.py
--recalcular-ligadas`, que calcula la regla SIN el nivel 2.

LA UNIDAD NO ES UN DETALLE. Mini Conta decide kilo o pieza con la
`clave_unidad` (`linea_facturada_es_kg`): una partida H87 CON sku cuenta como
piezas. Poner el producto y dejar H87 volteaba 72,447 partidas de kilo a
piezas. La unidad sale de la presentación que casó: su `sat`; si no la trae
(el formulario de Productos y `PUT /productos/{id}/clave-sae` guardan
`{factor, clave_sae}` sin `sat`), la del NOMBRE de la presentación (PIEZA →
H87, KILO → KGM); y si el nombre no dice nada, una presentación con factor ≠ 1
se queda en H87. Las cajas sin unidad SAT (la CAJA de 22 kg de MANZANACAJA)
quedan en H87: no se inventa.

Este módulo es la ÚNICA copia de la regla: lo usan el endpoint y el backfill.
Una consulta por cosa y por factura, nunca por partida.
"""
from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.rbac import es_duenio_de_sae
from ..models import LineaFactura, LineaRemision, Producto, ProductoCliente, Remision, Serie
from .inventario import presentacion_factor
from .sat_catalogo import UNIDAD_A_SAT


# ─── Reglas (los nombres salen en el reporte del backfill) ──────────────────

NOMBRE_SAE = "0_nombre_sae"
REMISION = "1_remision_ligada"
PREVIA = "2_decision_previa"
OVERRIDE = "3_clave_distinta_en_empresa"
OVERRIDE_SIN_PRODUCTO = "3_clave_distinta_sin_producto"
UNICA = "4_clave_unica"
CATALOGO = "5a_catalogo_cliente"
USO_SERIE = "5b_uso_en_serie"
USO_GLOBAL = "5c_uso_global"
SKU_MENOR = "5d_sku_menor"
CODIGO_CLIENTE = "6_codigo_cliente"
SIN_PRODUCTO = "X_sin_producto"

_NIVELES = (CATALOGO, USO_SERIE, USO_GLOBAL)


def norm_clave_sae(v: Optional[str]) -> str:
    """Misma tolerancia que el cruce por clave de la bandeja (PR #32): sin
    acentos (Ñ→N), mayúsculas, solo letras, dígitos y guion."""
    s = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.upper() if ch.isalnum() or ch == "-")


# Palabras de unidad que el SAE pega al nombre del artículo («CALABAZA
# CRIOLLA KG», «ESPINACA PZA»): no cuentan para comparar nombres.
_UNIDADES_EN_NOMBRE = {"KG", "KGS", "KILO", "KILOS", "PZ", "PZA", "PZAS", "PIEZA", "PIEZAS",
                       "MJ", "MANOJO", "MAZO", "LT", "LTS", "LITRO", "GR", "GRS", "G"}


def _sin_unidad(t: str) -> str:
    """Una palabra sin su unidad: «KG» → «», «25KG» → «25» (la unidad pegada
    al número; revisión del 2-oct: «FRIJOL NEGRO 25KG» del SAE y «FRIJOL NEGRO
    25 KG» del catálogo no casaban). El número se queda: 25 KG no es KILO."""
    if t in _UNIDADES_EN_NOMBRE:
        return ""
    num = t.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if num and num.isdigit() and t[len(num):] in _UNIDADES_EN_NOMBRE:
        return num
    return t


def norm_nombre(v: Optional[str]) -> str:
    """El nombre de un artículo para compararlo con el de otro: sin acentos
    (PIÑA → PINA), mayúsculas, solo palabras y sin las de unidad, sueltas o
    pegadas a un número. «TOMATE VERDE LIMPIO KG» del SAE y «TOMATE VERDE
    LIMPIO» del catálogo son lo mismo. Lo usan el nivel 0 de la regla y el ⚠
    del backfill: una sola copia para que el reporte marque exactamente lo que
    la regla no resuelve."""
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode("ascii")
    s = "".join(ch if ch.isalnum() else " " for ch in s.upper())
    return " ".join(t for t in map(_sin_unidad, s.split()) if t)


def norm_empresa(v: Optional[str]) -> str:
    """'3' y '03' son la misma empresa de SAE."""
    s = (v or "").strip()
    return s.zfill(2) if s.isdigit() else s.upper()


# ─── Claves que en UNA empresa de SAE significan otro producto ──────────────

@dataclass(frozen=True)
class ClaveDistinta:
    empresa: str                       # empresa de SAE ("03")
    clave: str                         # CVE_ART tal como la factura esa empresa
    sku: str                           # el producto que de verdad es
    presentacion: Optional[str] = None  # None = la que case, o la base
    porque: str = ""


# Datos, no lógica: para sumar un caso basta otra fila. Solo entra lo que se
# VERIFICÓ contra la descripción del SAE de esa empresa (INVE.DESCR), nunca una
# sospecha — una fila mal puesta liga mal TODAS las partidas nuevas de esa
# clave. Si el sku no existe o no está vivo, la partida se queda sin producto:
# antes eso que el producto equivocado.
#
# Las empresas y los skus son los del SAE del despliegue y de SU inquilino
# (`ESPEJO_SAE_TENANT_ID`, hallazgo de la revisión del 2-oct): en cualquier
# otro inquilino esta tabla no aplica. El SAE 9 deposita con sus propios
# códigos (91/92/94), así que su empresa no se confunde con la 03 de aquí.
CLAVES_DISTINTAS_POR_EMPRESA: tuple[ClaveDistinta, ...] = (
    ClaveDistinta(
        empresa="03", clave="CALABAZACASTILKG", sku="00010233",
        porque=("En el SAE de Tabasco (empresa 03, serie ZEHMOVH) esa clave se "
                "describe «CALABAZA CRIOLLA KG»: 560 partidas, $261,429 al "
                "2-oct-2026. En la 02 y la 04 sí es calabaza de castilla, que es lo "
                "que dice el catálogo (00010738/00010902). Era la única clave con "
                "descripción distinta entre empresas."),
    ),
)

_DISTINTAS = {(norm_empresa(c.empresa), norm_clave_sae(c.clave)): c
              for c in CLAVES_DISTINTAS_POR_EMPRESA}


def clave_distinta(tenant_id, empresa: Optional[str], clave: str) -> Optional[ClaveDistinta]:
    if not es_duenio_de_sae(tenant_id):
        return None
    return _DISTINTAS.get((norm_empresa(empresa), norm_clave_sae(clave)))


# ─── Series: una misma serie con varios nombres ─────────────────────────────

def _limpia(serie: Optional[str]) -> str:
    return "".join(str(serie or "").split()).upper()


def serie_canonica(serie: Optional[str]) -> str:
    """La serie de FACTURA llevada a su nombre del espejo: la nativa FEHMOHOS
    es la continuación de la ZEHMOHOS de SAE."""
    s = _limpia(serie)
    return "Z" + s[1:] if s.startswith("F") else s


def serie_canonica_de_remision(codigo: Optional[str]) -> str:
    """La serie de REMISIÓN llevada a la de su factura: RZEHMOVH → ZEHMOVH,
    RFEHMOHOS → ZEHMOHOS."""
    s = _limpia(codigo)
    return "Z" + s[2:] if s.startswith("RF") else s[1:]


# ─── Producto: presentación base y unidad SAT ───────────────────────────────

def presentacion_base(prod) -> Optional[str]:
    """La presentación a la que pertenece `productos.clave_sae` (la misma que
    usa `lista_export.clave_sae_de`)."""
    return getattr(prod, "unidad_base", None) or getattr(prod, "presentacion_default", None) or None


# Nombres con los que las presentaciones viven en el catálogo (la foto del
# 1-oct trae MAZO, MZ, PQ, CAJITA además de los de UNIDAD_A_SAT).
_ALIAS_PRESENTACION = {
    "KG": "KILO", "KGS": "KILO", "KILOS": "KILO", "KILOGRAMO": "KILO",
    "PZ": "PIEZA", "PZA": "PIEZA", "PZAS": "PIEZA", "PIEZAS": "PIEZA",
    "MAZO": "MANOJO", "MZ": "MANOJO", "MJ": "MANOJO", "MANOJOS": "MANOJO",
    "CAJITA": "CAJA", "CJ": "CAJA", "CAJAS": "CAJA", "PQ": "PAQUETE",
    "LT": "LITRO", "LTS": "LITRO", "LITROS": "LITRO",
}
# Envases: una caja, bolsa, costal o paquete sin `sat` propio es un bulto que
# se cuenta por pieza (la regla de MANZANACAJA), no XBX/XSA que nadie lee.
_ENVASES = {"XBX", "XBG", "XSA", "XPK"}


def sat_por_nombre(presentacion: Optional[str]) -> Optional[str]:
    n = str(presentacion or "").strip().upper()
    return UNIDAD_A_SAT.get(_ALIAS_PRESENTACION.get(n, n))


def unidad_sat_de(prod: Producto, presentacion: Optional[str]) -> str:
    """La clave_unidad de la partida espejo.

    1. La `sat` de la presentación si la trae (SANDIAPZ → H87 aunque el
       producto sea de kilo).
    2. La presentación base: la unidad del producto.
    3. Otra presentación sin `sat`: la de su nombre. Es la forma en que la
       guardan el formulario de Productos y `PUT /productos/{id}/clave-sae`
       (`{factor, clave_sae}`): con la unidad del producto, MELONCHPZ (PIEZA
       de un producto de KILO) salía KGM y PAPAYAMARADOLKG (KILO de uno de
       PIEZA) H87 — Mini Conta contaba al revés (revisión del 2-oct). Un
       envase sin `sat` (CAJA, COSTAL…) se cuenta por pieza: H87.
    4. Un nombre que no dice nada: factor ≠ 1 es un bulto (H87); factor 1, la
       unidad del producto.
    Sin presentación (cruce por código del cliente): la unidad del producto,
    lo de siempre.
    """
    if not presentacion:
        return prod.unidad_sat or "H87"
    raw = (getattr(prod, "presentaciones", None) or {}).get(presentacion)
    if isinstance(raw, dict) and str(raw.get("sat") or "").strip():
        return str(raw["sat"]).strip().upper()
    if presentacion == presentacion_base(prod):
        return prod.unidad_sat or "H87"
    sat = sat_por_nombre(presentacion)
    if sat:
        return "H87" if sat in _ENVASES else sat
    if presentacion_factor(prod, presentacion) != Decimal("1"):
        return "H87"
    return prod.unidad_sat or "H87"


def campos_de_linea(res: Optional["Resolucion"], prod: Optional[Producto]) -> dict:
    """Lo que la regla le escribe a la partida — el endpoint y el backfill lo
    toman de aquí para que nunca escriban distinto."""
    if prod is None or res is None or res.producto_id != prod.id:
        return {"producto_id": None, "presentacion": None,
                "clave_unidad": "H87", "clave_prod_serv": "01010101"}
    return {"producto_id": prod.id, "presentacion": res.presentacion,
            "clave_unidad": unidad_sat_de(prod, res.presentacion),
            "clave_prod_serv": prod.clave_sat}


# ─── Índice clave → productos ───────────────────────────────────────────────

def _elegir_presentacion(casadas: set, base: Optional[str], default: Optional[str]) -> Optional[str]:
    """Una clave que casa varias veces con el MISMO producto: la clave del
    producto (None) es la de la base; entre presentaciones, la base, la de
    default y luego la de nombre menor (CILANTROMJ está en PIEZA y en MANOJO)."""
    if None in casadas:
        return base
    for preferida in (base, default):
        if preferida and preferida in casadas:
            return preferida
    return min(casadas)


class IndiceClaves:
    """Clave SAE normalizada → {producto_id: presentación} de los productos
    sin borrar, activos o no. Los desactivados sirven para reconocer lo que
    trae la remisión ligada o lo que la partida ya tenía; solo los activos son
    candidatos para una partida nueva."""

    def __init__(self, productos: Iterable):
        self.sku: dict = {}                 # pid → sku, de todos los sin borrar
        self.nombre: dict = {}
        self._nombre_norm: dict = {}        # pid → norm_nombre, para el nivel 0
        self.activos: set = set()
        self.por_sku: dict[str, UUID] = {}  # sku → pid, solo activos
        self._base: dict = {}
        casan: dict[str, dict] = defaultdict(lambda: defaultdict(set))
        defaults: dict = {}
        for p in productos:
            sku = (p.sku or "").strip()
            self.sku[p.id] = sku
            self.nombre[p.id] = getattr(p, "nombre", None) or ""
            self._nombre_norm[p.id] = norm_nombre(self.nombre[p.id])
            if getattr(p, "activo", True):
                self.activos.add(p.id)
                self.por_sku[sku] = p.id
            self._base[p.id] = presentacion_base(p)
            defaults[p.id] = getattr(p, "presentacion_default", None)
            k = norm_clave_sae(p.clave_sae)
            if k:
                casan[k][p.id].add(None)
            for nombre, raw in (p.presentaciones or {}).items():
                if isinstance(raw, dict):
                    kp = norm_clave_sae(str(raw.get("clave_sae") or ""))
                    if kp:
                        casan[kp][p.id].add(str(nombre))
        self._por_clave: dict[str, dict] = {
            k: {pid: _elegir_presentacion(pres, self._base[pid], defaults[pid])
                for pid, pres in por_pid.items()}
            for k, por_pid in casan.items()
        }

    def vivo(self, pid) -> bool:
        """Sin borrar (activo o no)."""
        return pid in self.sku

    def base(self, pid) -> Optional[str]:
        return self._base.get(pid)

    def todos(self, clave: str) -> dict:
        """Los que traen la clave, activos o no."""
        return self._por_clave.get(clave, {})

    def candidatos(self, clave: str) -> dict:
        """Los que traen la clave y están activos: los únicos que pueden
        ganar una partida que no tiene más evidencia."""
        return {pid: pres for pid, pres in self.todos(clave).items() if pid in self.activos}

    def se_llama(self, pid, nombres) -> bool:
        """¿El producto se llama como alguno de `nombres` (ya normalizados)?
        Uno borrado no se llama de ninguna forma: no está en el índice."""
        n = self._nombre_norm.get(pid)
        return bool(n) and n in nombres

    def nombrados(self, clave: str, nombres) -> dict:
        """El nivel 0: de los gemelos ACTIVOS de la clave, los que se llaman
        como el SAE llama a la partida — los únicos candidatos desde ahí.
        Vacío si la clave no es gemela (uno o ningún activo la trae) o si el
        nombre no casa con ninguno: entonces la regla sigue como si no hubiera
        nombre."""
        if not nombres:
            return {}
        cands = self.candidatos(clave)
        if len(cands) < 2:
            return {}
        return {pid: pres for pid, pres in cands.items() if self.se_llama(pid, nombres)}


# ─── Evidencia para el desempate ────────────────────────────────────────────

@dataclass
class Catalogo:
    """Lo del cliente: qué productos tiene y con qué código."""
    productos: set = field(default_factory=set)
    codigos: dict = field(default_factory=dict)      # código normalizado → pid | None (ambiguo)

    @classmethod
    def de_filas(cls, filas) -> "Catalogo":
        cat = cls()
        for pid, codigo in filas:
            cat.productos.add(pid)
            k = norm_clave_sae(codigo) if codigo else ""
            if not k:
                continue
            # Ambiguo (dos productos con el mismo código) no decide — igual que la bandeja.
            if k in cat.codigos and cat.codigos[k] != pid:
                cat.codigos[k] = None
            else:
                cat.codigos.setdefault(k, pid)
        return cat


@dataclass
class Uso:
    """Partidas de REMISIÓN con producto por (serie canónica, producto) y en
    total."""
    por_serie: dict = field(default_factory=dict)
    total: dict = field(default_factory=dict)

    @classmethod
    def de_filas(cls, filas_remision) -> "Uso":
        uso = cls(defaultdict(int), defaultdict(int))
        for codigo, pid, n in filas_remision:
            uso.por_serie[(serie_canonica_de_remision(codigo), pid)] += int(n)
            uso.total[pid] += int(n)
        return uso


def previas_de(filas) -> dict:
    """{clave normalizada: producto} de las partidas que la factura YA tiene.
    Dos productos distintos en la misma clave no deciden nada (None)."""
    out: dict = {}
    for clave, pid in filas:
        k = norm_clave_sae(clave)
        if not k or pid is None:
            continue
        if k in out and out[k] != pid:
            out[k] = None
        else:
            out.setdefault(k, pid)
    return out


class FuentesBD:
    """Lo que la regla lee de la base, una consulta por cosa y por factura.

    El filtro por inquilino va explícito aunque RLS lo haga en producción: la
    BD de pruebas corre sin RLS (y el backfill, como postgres, también)."""

    def __init__(self, db: Session, tenant_id):
        self.db = db
        self.tenant_id = tenant_id
        self._indice: Optional[IndiceClaves] = None
        self._catalogos: dict = {}

    def indice(self) -> IndiceClaves:
        if self._indice is None:
            filas = (
                self.db.query(Producto.id, Producto.sku, Producto.nombre, Producto.clave_sae,
                              Producto.presentaciones, Producto.unidad_base,
                              Producto.presentacion_default, Producto.activo)
                .filter(Producto.tenant_id == self.tenant_id,
                        Producto.deleted_at.is_(None))
                .all()
            )
            self._indice = IndiceClaves(filas)
        return self._indice

    def catalogo(self, cliente_id) -> Catalogo:
        """El catálogo del cliente sin productos borrados. Los desactivados se
        quedan: para el desempate da igual (los candidatos ya son solo activos)
        y el respaldo por código sigue ligando lo que ligaba antes."""
        if cliente_id not in self._catalogos:
            filas = (
                self.db.query(ProductoCliente.producto_id, ProductoCliente.codigo_cliente)
                .join(Producto, Producto.id == ProductoCliente.producto_id)
                .filter(ProductoCliente.tenant_id == self.tenant_id,
                        ProductoCliente.cliente_id == cliente_id,
                        Producto.deleted_at.is_(None))
                .order_by(ProductoCliente.id)
                .all()
            )
            self._catalogos[cliente_id] = Catalogo.de_filas(filas)
        return self._catalogos[cliente_id]

    def remision(self, factura_id) -> set:
        filas = (
            self.db.query(LineaRemision.producto_id)
            .join(Remision, Remision.id == LineaRemision.remision_id)
            .filter(Remision.tenant_id == self.tenant_id,
                    Remision.factura_id == factura_id,
                    Remision.deleted_at.is_(None))
            .distinct()
            .all()
        )
        return {pid for (pid,) in filas if pid is not None}

    def previas(self, factura_id) -> dict:
        filas = (
            self.db.query(LineaFactura.clave_sae, LineaFactura.producto_id)
            .filter(LineaFactura.tenant_id == self.tenant_id,
                    LineaFactura.factura_id == factura_id,
                    LineaFactura.producto_id.isnot(None))
            .all()
        )
        return previas_de(filas)

    def uso(self, pids: set) -> Uso:
        """Solo de los candidatos que siguen empatados: es la única consulta
        que recorre partidas, y casi nunca hace falta."""
        return self._consultar_uso(pids) if pids else Uso()

    def _consultar_uso(self, pids: Optional[set]) -> Uso:
        """Partidas de remisión con producto por serie (borradas fuera,
        canceladas dentro; sin serie cuentan solo para el total). Nada de
        `lineas_factura`: ver «el uso se cuenta en remisiones» arriba.
        `pids=None` cuenta todos los productos."""
        q = (
            self.db.query(Serie.codigo, LineaRemision.producto_id, func.count())
            .select_from(LineaRemision)
            .join(Remision, Remision.id == LineaRemision.remision_id)
            .outerjoin(Serie, Serie.id == Remision.serie_id)
            .filter(Remision.tenant_id == self.tenant_id,
                    Remision.deleted_at.is_(None),
                    LineaRemision.producto_id.isnot(None))
        )
        if pids is not None:
            q = q.filter(LineaRemision.producto_id.in_(list(pids)))
        return Uso.de_filas(q.group_by(Serie.codigo, LineaRemision.producto_id).all())


class FuentesEnLote(FuentesBD):
    """Las mismas fuentes para recorrer miles de facturas (el backfill).

    Misma regla, otra forma de leer: el uso se toma UNA vez al empezar —una
    foto— en vez de una consulta por factura, y las remisiones ligadas y las
    partidas previas se cargan por lote. Como el uso sale solo de remisiones,
    lo que el backfill liga no lo mueve: la foto es la misma que verá el
    endpoint al terminar (revisión del 2-oct), y el resultado no depende del
    orden en que se recorran las facturas.
    """

    def __init__(self, db: Session, tenant_id):
        super().__init__(db, tenant_id)
        self._uso_fijo: Optional[Uso] = None
        self._rem: dict = {}
        self._prev: dict = {}

    def fijar_uso(self) -> None:
        self._uso_fijo = self._consultar_uso(None)

    def precargar(self, factura_ids, lineas) -> None:
        """`lineas`: (factura_id, clave_sae, producto_id) de TODAS las partidas
        de esas facturas — lo mismo que el endpoint leería antes de borrarlas."""
        self._rem = defaultdict(set)
        if factura_ids:
            for fid, pid in (
                self.db.query(Remision.factura_id, LineaRemision.producto_id)
                .join(LineaRemision, LineaRemision.remision_id == Remision.id)
                .filter(Remision.tenant_id == self.tenant_id,
                        Remision.factura_id.in_(list(factura_ids)),
                        Remision.deleted_at.is_(None))
                .distinct()
                .all()
            ):
                if pid is not None:
                    self._rem[fid].add(pid)
        por_factura: dict = defaultdict(list)
        for fid, clave, pid in lineas:
            if pid is not None:
                por_factura[fid].append((clave, pid))
        self._prev = {fid: previas_de(filas) for fid, filas in por_factura.items()}

    def remision(self, factura_id) -> set:
        return self._rem.get(factura_id, set())

    def previas(self, factura_id) -> dict:
        return self._prev.get(factura_id, {})

    def uso(self, pids: set) -> Uso:
        return self._uso_fijo if self._uso_fijo is not None else super().uso(pids)


# ─── La regla ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolucion:
    producto_id: Optional[UUID]
    presentacion: Optional[str]
    regla: str
    # El uso decidió por poco (ganador < 5 o < 2× el segundo) o decidió el sku:
    # el backfill las cuenta aparte para que alguien las mire.
    debil: bool = False
    # Los que competían (gemelos o los de la remisión), el ganador primero, y
    # al final los gemelos que el nombre del SAE dejó fuera: el backfill los
    # lista.
    competidores: tuple = ()


_NADA = Resolucion(None, None, SIN_PRODUCTO)


def _regla(ganador: tuple, segundo: tuple) -> tuple[str, bool]:
    for i, nombre in enumerate(_NIVELES):
        if ganador[i] > segundo[i]:
            debil = nombre in (USO_SERIE, USO_GLOBAL) and (
                ganador[i] < 5 or ganador[i] < 2 * segundo[i])
            return nombre, debil
    return SKU_MENOR, True


def _desempatar(fuentes: FuentesBD, *, catalogo: Catalogo, serie, pendientes: dict) -> dict:
    """`pendientes`: {clave: (candidatos {pid: presentación}, regla fija o None,
    gemelos que el nombre del SAE dejó fuera)}. La regla fija es REMISION
    cuando la remisión ligada trae a más de uno. Los que el nombre dejó fuera
    no compiten, pero van al final de `competidores` para que el backfill los
    liste como perdedores."""
    indice = fuentes.indice()
    # Primero el catálogo (ya está en memoria). El uso solo se pregunta para
    # los que siguen empatados: ordenar por niveles es lexicográfico, así que
    # a los demás nunca les llega a importar.
    en_cat = {k: {pid: int(pid in catalogo.productos) for pid in cands}
              for k, (cands, _, _) in pendientes.items()}
    arriba: dict = {}
    for k, ll in en_cat.items():
        top = max(ll.values())
        empate = {pid for pid, v in ll.items() if v == top}
        if len(empate) > 1:
            arriba[k] = empate
    uso = fuentes.uso(set().union(*arriba.values())) if arriba else Uso()
    canon = serie_canonica(serie)

    out = {}
    for k, (cands, fija, fuera) in pendientes.items():
        filas = []
        for pid in cands:
            en_juego = pid in arriba.get(k, ())
            filas.append((
                (en_cat[k][pid],
                 uso.por_serie.get((canon, pid), 0) if en_juego else 0,
                 uso.total.get(pid, 0) if en_juego else 0),
                pid,
            ))
        filas.sort(key=lambda f: (tuple(-x for x in f[0]), indice.sku.get(f[1], ""), str(f[1])))
        (llave, pid), (segunda, _) = filas[0], filas[1]
        regla, debil = _regla(llave, segunda)
        out[k] = Resolucion(pid, cands[pid], fija or regla, debil,
                            tuple(p for _, p in filas) + fuera)
    return out


def _por_sku(indice: IndiceClaves, pids) -> tuple:
    return tuple(sorted(pids, key=lambda p: (indice.sku.get(p, ""), str(p))))


def resolver_claves(fuentes: FuentesBD, *, factura_id, cliente_id, empresa, serie,
                    claves: Iterable[Optional[str]],
                    descripciones: Iterable[tuple[Optional[str], Optional[str]]] = (),
                    con_previa: bool = True,
                    ) -> dict[str, Resolucion]:
    """{clave normalizada: Resolucion} para las claves de UNA factura.

    `factura_id` es la factura ya creada (sus remisiones ligadas y sus partidas
    actuales son evidencia); `empresa` y `serie` son las de SAE.
    `descripciones`: (clave, descripción) de cada partida tal como la manda el
    SAE — el nivel 0. Si dos partidas de la misma clave traen nombres
    distintos, cuentan los dos (la clave se decide una vez por factura).
    `con_previa=False` decide como si la factura no tuviera partidas: lo usa el
    endpoint con una factura recién creada (se ahorra la consulta) y el
    backfill para comparar contra lo ya ligado (`--recalcular-ligadas`).
    """
    ks = sorted({norm_clave_sae(c) for c in claves if c} - {""})
    if not ks:
        return {}
    indice = fuentes.indice()
    rem = fuentes.remision(factura_id) if factura_id else set()
    prev = fuentes.previas(factura_id) if (factura_id and con_previa) else {}
    nombres_sae: dict[str, set] = defaultdict(set)
    for clave, desc in descripciones:
        kd, nd = norm_clave_sae(clave), norm_nombre(desc)
        if kd and nd:
            nombres_sae[kd].add(nd)
    _cat: list = []

    def catalogo() -> Catalogo:
        if not _cat:
            _cat.append(fuentes.catalogo(cliente_id))
        return _cat[0]

    out: dict[str, Resolucion] = {}
    pendientes: dict = {}
    for k in ks:
        # Lo que la clave PUEDE ser en esta factura, con la presentación que
        # le toca a cada uno (None = por código del cliente: la unidad del
        # producto, como siempre).
        distinta = clave_distinta(fuentes.tenant_id, empresa, k)
        nombres = nombres_sae.get(k, set())
        nombrados: dict = {}
        fuera: tuple = ()
        if distinta is not None:
            pid_d = indice.por_sku.get(distinta.sku.strip())
            posibles = ({pid_d: distinta.presentacion or indice.todos(k).get(pid_d)
                         or indice.base(pid_d)} if pid_d else {})
        else:
            # 0. El nombre del SAE: entre gemelos ACTIVOS, los candidatos se
            # restringen a los que se llaman como dice la factura. Uno solo
            # gana sin más; con varios, lo que sigue desempata entre ellos.
            # Un producto que se llama igual pero no es gemelo activo (uno
            # desactivado, el que trae otra clave) ya no entra ni por la
            # remisión ligada ni por la decisión previa (revisión del 2-oct).
            nombrados = indice.nombrados(k, nombres)
            if nombrados:
                fuera = _por_sku(indice, set(indice.candidatos(k)) - set(nombrados))
                if len(nombrados) == 1:
                    ((pid, pres),) = nombrados.items()
                    out[k] = Resolucion(pid, pres, NOMBRE_SAE, competidores=(pid,) + fuera)
                    continue
                posibles = dict(nombrados)
            else:
                posibles = dict(indice.todos(k))
                if rem:
                    cod = catalogo().codigos.get(k)
                    if cod is not None and indice.vivo(cod):
                        posibles.setdefault(cod, None)

        # 1. La remisión ligada a esta factura.
        en_rem = {pid: pres for pid, pres in posibles.items() if pid in rem}
        if len(en_rem) == 1:
            ((pid, pres),) = en_rem.items()
            out[k] = Resolucion(pid, pres, REMISION)
            continue
        if en_rem:
            pendientes[k] = (en_rem, REMISION, fuera)
            continue

        # 2. Lo que la partida ya tenía, sea o no de los posibles y aunque hoy
        # esté desactivado o borrado: un reenvío no corrige la historia —
        # salvo que el SAE nombre a gemelos activos y no sea uno de ellos
        # (nivel 0).
        pid_p = prev.get(k)
        if pid_p is not None and (not nombrados or pid_p in nombrados):
            pres_p = posibles[pid_p] if pid_p in posibles else indice.todos(k).get(pid_p)
            out[k] = Resolucion(pid_p, pres_p, PREVIA)
            continue

        # 3. La clave es otra cosa en esta empresa.
        if distinta is not None:
            out[k] = (Resolucion(pid_d, posibles[pid_d], OVERRIDE) if pid_d
                      else Resolucion(None, None, OVERRIDE_SIN_PRODUCTO))
            continue

        # 0. (sin remisión ni decisión previa entre los nombrados) el
        # desempate entre los gemelos que el SAE nombra.
        if nombrados:
            pendientes[k] = (nombrados, None, fuera)
            continue

        # 4. Un solo activo con la clave; 5. gemelos; 6. código del cliente.
        cands = indice.candidatos(k)
        if len(cands) == 1:
            ((pid, pres),) = cands.items()
            out[k] = Resolucion(pid, pres, UNICA)
        elif cands:
            pendientes[k] = (cands, None, ())
        else:
            pid = catalogo().codigos.get(k)
            out[k] = (Resolucion(pid, None, CODIGO_CLIENTE)
                      if pid is not None and indice.vivo(pid) else _NADA)
    if pendientes:
        out.update(_desempatar(fuentes, catalogo=catalogo(), serie=serie,
                               pendientes=pendientes))
    return out

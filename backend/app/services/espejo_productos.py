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
clave quedan con el mismo producto):

  0. La clave significa OTRA cosa en esa empresa de SAE: manda la tabla
     `CLAVES_DISTINTAS_POR_EMPRESA` de abajo (CALABAZACASTILKG en la 03).
  1. Un solo producto vivo trae la clave (en `clave_sae` o en una presentación).
  2. Varios — se ordena por, y gana el primero que se despegue:
     a. está en la remisión ligada a ESTA factura (`remisiones.factura_id`);
     p. es el que la factura ya tenía en esa clave (ver «la decisión se
        guarda» abajo);
     b. está en el catálogo del cliente (`producto_clientes`);
     c. es el más usado en la serie (partidas de remisión RZ…/RF… y de factura
        Z…/F… con producto);
     d. es el más usado en todo el inquilino;
     e. el sku menor.
  3. Ninguna clave casa: lo de antes, el `codigo_cliente` del catálogo del
     cliente — pero ya no hacia productos borrados (ligaba al 00010229 NUEZ,
     borrado). Los desactivados sí cuentan aquí, como antes: los gemelos que
     apagó la fusión de SANDIA conservan su clave y su historia.

LA DECISIÓN SE GUARDA EN LA PROPIA PARTIDA. Cada reenvío de una factura (el
cuadre, los abonos, las cancelaciones) borra y recrea sus partidas, y los
conteos de uso cambian con el tiempo: sin memoria, un reenvío podía voltear el
producto de una partida vieja (CILANTROKG va a un gemelo en ZEHMO* y al otro en
ZECA por 4 contra 0). Por eso el paso «p»: si la factura ya tenía en esa clave
uno de los candidatos, se queda con él. Solo una remisión ligada (evidencia de
ESA factura) lo puede mover.

LA UNIDAD NO ES UN DETALLE. Mini Conta decide kilo o pieza con la
`clave_unidad` (`linea_facturada_es_kg`): una partida H87 CON sku cuenta como
piezas. Poner el producto y dejar H87 volteaba 72,447 partidas de kilo a
piezas. La unidad sale de la presentación que casó (`sat`), o del producto si
casó la clave base; una presentación sin unidad SAT y con factor distinto de 1
(la CAJA de 22 kg de MANZANACAJA) se queda en H87: no se inventa.

Este módulo es la ÚNICA copia de la regla: lo usan el endpoint y el backfill
(`scripts/backfill_espejo_producto_por_clave.py`). Una consulta por cosa y por
factura, nunca por partida.
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

from ..models import Factura, LineaFactura, LineaRemision, Producto, ProductoCliente, Remision, Serie
from .inventario import presentacion_factor


# ─── Reglas (los nombres salen en el reporte del backfill) ──────────────────

OVERRIDE = "0_clave_distinta_en_empresa"
OVERRIDE_SIN_PRODUCTO = "0_clave_distinta_sin_producto"
UNICA = "1_clave_unica"
REMISION = "2a_remision_ligada"
PREVIA = "2p_decision_previa"
CATALOGO = "2b_catalogo_cliente"
USO_SERIE = "2c_uso_en_serie"
USO_GLOBAL = "2d_uso_global"
SKU_MENOR = "2e_sku_menor"
CODIGO_CLIENTE = "3_codigo_cliente"
SIN_PRODUCTO = "X_sin_producto"

_NIVELES = (REMISION, PREVIA, CATALOGO, USO_SERIE, USO_GLOBAL)


def norm_clave_sae(v: Optional[str]) -> str:
    """Misma tolerancia que el cruce por clave de la bandeja (PR #32): sin
    acentos (Ñ→N), mayúsculas, solo letras, dígitos y guion."""
    s = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.upper() if ch.isalnum() or ch == "-")


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
# sospecha — una fila mal puesta liga mal TODAS las partidas de esa clave.
# Si el sku no existe o no está vivo, la partida se queda sin producto: antes
# eso que el producto equivocado.
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


def clave_distinta(empresa: Optional[str], clave: str) -> Optional[ClaveDistinta]:
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


def unidad_sat_de(prod: Producto, presentacion: Optional[str]) -> str:
    """La clave_unidad de la partida espejo.

    La `sat` de la presentación si la trae (SANDIAPZ → H87 aunque el producto
    sea de kilo; PAPAYAMARADOLKG → KGM aunque el producto sea de pieza). Una
    presentación que no es la base, sin `sat` y con factor ≠ 1 es una caja o un
    bulto: H87 — con la unidad del producto Mini Conta contaría 22 kilos por
    cada caja de MANZANACAJA. Sin presentación (cruce por código del cliente),
    lo de siempre: la unidad del producto.
    """
    raw = (getattr(prod, "presentaciones", None) or {}).get(presentacion) if presentacion else None
    if isinstance(raw, dict) and raw.get("sat"):
        return str(raw["sat"])
    if presentacion and presentacion != presentacion_base(prod) \
            and presentacion_factor(prod, presentacion) != Decimal("1"):
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


# ─── Índice clave → productos vivos ─────────────────────────────────────────

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
    """Clave SAE normalizada → {producto_id: presentación} de los productos que
    se le pasen (el endpoint pasa los vivos: activos y sin borrar)."""

    def __init__(self, productos: Iterable):
        self.sku: dict = {}
        self.por_sku: dict[str, UUID] = {}
        self._base: dict = {}
        casan: dict[str, dict] = defaultdict(lambda: defaultdict(set))
        defaults: dict = {}
        for p in productos:
            self.sku[p.id] = (p.sku or "").strip()
            self.por_sku[(p.sku or "").strip()] = p.id
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

    def candidatos(self, clave: str) -> dict:
        return self._por_clave.get(clave, {})

    def presentacion_de(self, pid, clave: str) -> Optional[str]:
        """La presentación con la que `pid` trae la clave; la base si no la trae."""
        return self.candidatos(clave).get(pid, self._base.get(pid))


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
    """Partidas con producto por (serie canónica, producto) y en total."""
    por_serie: dict = field(default_factory=dict)
    total: dict = field(default_factory=dict)

    @classmethod
    def de_filas(cls, filas_remision, filas_factura) -> "Uso":
        uso = cls(defaultdict(int), defaultdict(int))
        for codigo, pid, n in filas_remision:
            uso.por_serie[(serie_canonica_de_remision(codigo), pid)] += int(n)
            uso.total[pid] += int(n)
        for serie, pid, n in filas_factura:
            uso.por_serie[(serie_canonica(serie), pid)] += int(n)
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
                self.db.query(Producto.id, Producto.sku, Producto.clave_sae,
                              Producto.presentaciones, Producto.unidad_base,
                              Producto.presentacion_default)
                .filter(Producto.tenant_id == self.tenant_id,
                        Producto.activo.is_(True),
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
        """Partidas con producto por serie: las de remisión (borradas fuera,
        canceladas dentro) y las de factura (nativas y espejo). `pids=None`
        cuenta todos los productos."""
        rem = (
            self.db.query(Serie.codigo, LineaRemision.producto_id, func.count())
            .select_from(LineaRemision)
            .join(Remision, Remision.id == LineaRemision.remision_id)
            .join(Serie, Serie.id == Remision.serie_id)
            .filter(Remision.tenant_id == self.tenant_id,
                    Remision.deleted_at.is_(None),
                    LineaRemision.producto_id.isnot(None))
        )
        fac = (
            self.db.query(Factura.serie, LineaFactura.producto_id, func.count())
            .select_from(LineaFactura)
            .join(Factura, Factura.id == LineaFactura.factura_id)
            .filter(LineaFactura.tenant_id == self.tenant_id,
                    Factura.deleted_at.is_(None),
                    LineaFactura.producto_id.isnot(None))
        )
        if pids is not None:
            rem = rem.filter(LineaRemision.producto_id.in_(list(pids)))
            fac = fac.filter(LineaFactura.producto_id.in_(list(pids)))
        return Uso.de_filas(
            rem.group_by(Serie.codigo, LineaRemision.producto_id).all(),
            fac.group_by(Factura.serie, LineaFactura.producto_id).all(),
        )


class FuentesEnLote(FuentesBD):
    """Las mismas fuentes para recorrer miles de facturas (el backfill).

    Misma regla, otra forma de leer: el uso se toma UNA vez al empezar —una
    foto, como el SQL verificado del 2-oct— en vez de una consulta por factura,
    y las remisiones ligadas y las partidas previas se cargan por lote. Con la
    foto, el resultado no depende del orden en que se recorran las facturas.
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


_NADA = Resolucion(None, None, SIN_PRODUCTO)


def _regla(ganador: tuple, segundo: tuple) -> tuple[str, bool]:
    for i, nombre in enumerate(_NIVELES):
        if ganador[i] > segundo[i]:
            debil = nombre in (USO_SERIE, USO_GLOBAL) and (
                ganador[i] < 5 or ganador[i] < 2 * segundo[i])
            return nombre, debil
    return SKU_MENOR, True


def _desempatar(fuentes: FuentesBD, *, factura_id, catalogo: Catalogo, serie,
                empatadas: dict) -> dict:
    indice = fuentes.indice()
    rem = fuentes.remision(factura_id) if factura_id else set()
    prev = fuentes.previas(factura_id) if factura_id else {}

    # Primero lo barato (remisión, decisión previa, catálogo). El uso solo se
    # pregunta para los que siguen empatados arriba: ordenar por niveles es
    # lexicográfico, así que a los demás nunca les llega a importar.
    baratos = {k: {pid: (int(pid in rem), int(prev.get(k) == pid), int(pid in catalogo.productos))
                   for pid in cands}
               for k, cands in empatadas.items()}
    arriba: dict = {}
    for k, ll in baratos.items():
        top = max(ll.values())
        empate = {pid for pid, v in ll.items() if v == top}
        if len(empate) > 1:
            arriba[k] = empate
    uso = fuentes.uso(set().union(*arriba.values())) if arriba else Uso()
    canon = serie_canonica(serie)

    out = {}
    for k, cands in empatadas.items():
        filas = []
        for pid in cands:
            en_juego = pid in arriba.get(k, ())
            filas.append((
                baratos[k][pid] + (uso.por_serie.get((canon, pid), 0) if en_juego else 0,
                                   uso.total.get(pid, 0) if en_juego else 0),
                pid,
            ))
        filas.sort(key=lambda f: (tuple(-x for x in f[0]), indice.sku.get(f[1], ""), str(f[1])))
        (llave, pid), (segunda, _) = filas[0], filas[1]
        regla, debil = _regla(llave, segunda)
        out[k] = Resolucion(pid, cands[pid], regla, debil)
    return out


def resolver_claves(fuentes: FuentesBD, *, factura_id, cliente_id, empresa, serie,
                    claves: Iterable[Optional[str]]) -> dict[str, Resolucion]:
    """{clave normalizada: Resolucion} para las claves de UNA factura.

    `factura_id` es la factura ya creada (sus remisiones ligadas y sus partidas
    actuales son evidencia); `empresa` y `serie` son las de SAE.
    """
    indice = fuentes.indice()
    out: dict[str, Resolucion] = {}
    empatadas: dict = {}
    catalogo: Optional[Catalogo] = None
    for k in sorted({norm_clave_sae(c) for c in claves if c} - {""}):
        distinta = clave_distinta(empresa, k)
        if distinta is not None:
            pid = indice.por_sku.get(distinta.sku.strip())
            if pid is None:
                out[k] = Resolucion(None, None, OVERRIDE_SIN_PRODUCTO)
            else:
                out[k] = Resolucion(pid, distinta.presentacion or indice.presentacion_de(pid, k), OVERRIDE)
            continue
        cands = indice.candidatos(k)
        if len(cands) == 1:
            ((pid, pres),) = cands.items()
            out[k] = Resolucion(pid, pres, UNICA)
        elif cands:
            empatadas[k] = cands
        else:
            if catalogo is None:
                catalogo = fuentes.catalogo(cliente_id)
            pid = catalogo.codigos.get(k)
            out[k] = Resolucion(pid, None, CODIGO_CLIENTE) if pid else _NADA
    if empatadas:
        if catalogo is None:
            catalogo = fuentes.catalogo(cliente_id)
        out.update(_desempatar(fuentes, factura_id=factura_id, catalogo=catalogo,
                               serie=serie, empatadas=empatadas))
    return out

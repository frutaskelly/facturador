"""Smart Supply · panel — qué puede leer cada clave de cuenta y cómo se acota.

Smart Supply (app.smartsupply.mx) es un sistema APARTE que mide la merma de
cada bodega cruzando lo que se compró (Mini Conta) contra lo que se pidió, se
remisionó y se facturó aquí. Entra solo por API y con una clave POR CUENTA: la
cuenta es una bodega/plaza (Tabasco · Villahermosa) y su clave lee lo de esa
plaza y nada más.

La clave es una conexión tipo `SMART_SUPPLY_PANEL`, con UN permiso
(`abasto:leer`, ver core/rbac.py::PERMISOS_POR_TIPO): solo lee. No es la clave
del bot (`SMART_SUPPLY`), que escribe y es de toda la empresa, ni la de Mini
Conta, que lee ventas por serie.

Lo que comparte cada clave vive en `conexiones.alcance`:

- `plaza`: el nombre de la plaza de la cuenta. Es la etiqueta; no filtra.
- `series`: series de FACTURA cuyo facturado puede leer (ZEHMOVH).
- `series_remision`: series de REMISIÓN cuyo remisionado puede leer
  (RZEHMOVH). También abren las OC que se volvieron remisión de esas series.
- `perfiles`: por dónde entran sus órdenes, como prefijo de `origen_externo`
  (`EHMO:villahermosa` = todo lo que el bot deposita con
  `EHMO:villahermosa:<folio>`). Abren las OC aunque todavía no tengan remisión
  o se hayan descartado.
- `remisiones`, `oc`, `catalogo`: si además del facturado lee lo remisionado,
  la bandeja de OC y el catálogo de productos.

Una clave de este tipo SIN alcance no lee nada (a diferencia de las de Mini
Conta de antes de 0092, que leían todo): no hay claves viejas que respetar.
Una PERSONA con el permiso (el dueño, por el bypass de OWNER) lee todo; sirve
para probar la API desde una sesión.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models import (
    ClienteSucursal,
    Conexion,
    Factura,
    OCRecibida,
    Remision,
    Serie,
    Sucursal,
)
from .mini_conta import clave_nombre, fechas_de_entrega, series_factura, series_por_plaza

TIPO = "SMART_SUPPLY_PANEL"
ZONA = "America/Mexico_City"

# Lo que una clave puede leer además del facturado. Una bandera que falta es False.
DATOS = ("remisiones", "oc", "catalogo")

# «EHMO:villahermosa», «WA:120363…@g.us», «MANUAL». Sin comodines de LIKE ni
# espacios: el perfil se usa como prefijo exacto de `origen_externo`.
_PERFIL = re.compile(r"^[A-Z]{2,12}(:[A-Za-z0-9@.\-]{1,100})?$")


def perfil_de(origen: Optional[str]) -> Optional[str]:
    """El perfil de un `origen_externo`: todo menos el folio.

    `EHMO:villahermosa:HO-34VIL-MIE` → `EHMO:villahermosa`;
    `WA:<jid>:<folio>` → `WA:<jid>`; `MANUAL:<uuid>` → `MANUAL`. Se corta por
    los dos primeros «:» y no por el último: un folio con «:» no mueve el perfil.
    """
    partes = (origen or "").split(":")
    if len(partes) >= 3:
        return f"{partes[0]}:{partes[1]}"
    return partes[0] or None


def _patron_like(perfil: str) -> str:
    escapado = perfil.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escapado}:%"


@dataclass(frozen=True)
class AlcancePanel:
    plaza: Optional[str]
    series: Optional[frozenset[str]]            # None = todas (persona)
    series_remision: Optional[frozenset[str]]   # None = todas
    perfiles: Optional[frozenset[str]]          # None = todos
    remisiones: bool = False
    oc: bool = False
    catalogo: bool = False

    @staticmethod
    def todo() -> "AlcancePanel":
        return AlcancePanel(plaza=None, series=None, series_remision=None, perfiles=None,
                            **{d: True for d in DATOS})

    @staticmethod
    def nada() -> "AlcancePanel":
        return AlcancePanel(plaza=None, series=frozenset(), series_remision=frozenset(),
                            perfiles=frozenset())

    @property
    def sin_limite(self) -> bool:
        return self.series is None

    def datos(self) -> dict:
        return {d: getattr(self, d) for d in DATOS}


def alcance_panel_de(db: Session, ctx: AuthContext) -> AlcancePanel:
    """El alcance de quien pide. Una persona lee todo, pero una persona con
    candado por cliente (portal) no entra a esto: el panel es por plaza, no
    por cliente, y recortarlo a medias daría cifras de merma falsas."""
    if ctx.conexion_id is None:
        if ctx.cliente_scope:
            raise HTTPException(status_code=403,
                                detail="Tu usuario solo ve algunos clientes: no lee el abasto")
        return AlcancePanel.todo()
    con = db.get(Conexion, ctx.conexion_id)
    if con is None or con.tipo != TIPO or not con.alcance:
        return AlcancePanel.nada()
    a = con.alcance
    return AlcancePanel(
        plaza=a.get("plaza") or None,
        series=frozenset(a.get("series") or []),
        series_remision=frozenset(a.get("series_remision") or []),
        perfiles=frozenset(a.get("perfiles") or []),
        **{d: bool(a.get(d)) for d in DATOS},
    )


def requiere(bandera: bool, que: str) -> None:
    if not bandera:
        raise HTTPException(status_code=403, detail=f"Esta conexión no comparte {que}")


# ─── Universo de la empresa ─────────────────────────────────────────────────

def series_de_remision(db: Session, tenant_id) -> list[str]:
    """Todas las series de REMISIÓN, activas o no (una apagada tiene historia)."""
    return sorted({
        c for (c,) in db.query(Serie.codigo)
        .filter(Serie.tenant_id == tenant_id, Serie.tipo_documento == "REMISION")
        .all()
    })


def ids_de_series(db: Session, tenant_id, codigos: Iterable[str], tipo: str) -> list[UUID]:
    codigos = list(codigos)
    if not codigos:
        return []
    return [i for (i,) in db.query(Serie.id).filter(
        Serie.tenant_id == tenant_id, Serie.tipo_documento == tipo, Serie.codigo.in_(codigos),
    ).all()]


def perfiles_vistos(db: Session, tenant_id, dias: int = 93) -> dict[str, Optional[str]]:
    """{perfil: plaza} de las OC de los últimos `dias`. La plaza sale de la OC o,
    si no la trae (pasa en ~1/4 de las de Pachuca), de su remisión; un perfil
    que cae en dos plazas se queda con la que más órdenes tiene."""
    desde = date.today() - timedelta(days=dias)
    plaza = sa.func.coalesce(OCRecibida.sucursal_id, Remision.sucursal_id)
    filas = (
        db.query(OCRecibida.origen_externo, Sucursal.nombre)
        .outerjoin(Remision, Remision.id == OCRecibida.remision_id)
        .outerjoin(Sucursal, Sucursal.id == plaza)
        .filter(OCRecibida.tenant_id == tenant_id,
                sa.cast(sa.func.timezone(ZONA, OCRecibida.recibida_at), sa.Date) >= desde)
        .all()
    )
    conteo: dict[str, dict[Optional[str], int]] = {}
    for origen, nombre in filas:
        p = perfil_de(origen)
        if not p or not _PERFIL.match(p):
            continue
        por_plaza = conteo.setdefault(p, {})
        k = nombre.strip() if nombre else None
        por_plaza[k] = por_plaza.get(k, 0) + 1
    out: dict[str, Optional[str]] = {}
    for p, por_plaza in conteo.items():
        con_nombre = {k: v for k, v in por_plaza.items() if k}
        out[p] = max(con_nombre, key=lambda k: (con_nombre[k], k)) if con_nombre else None
    return out


def opciones(db: Session, tenant_id) -> dict:
    """Lo que se le puede compartir a una cuenta: cada plaza con sus series de
    factura, sus series de remisión y los perfiles por los que entran sus OC,
    más todo lo suelto. La pantalla marca la plaza y se lleva lo suyo."""
    plazas: dict[str, dict] = {}
    for k, (nombre, codigos) in series_por_plaza(db, tenant_id).items():
        plazas[k] = {"nombre": nombre, "series": set(codigos), "series_remision": set(),
                     "perfiles": set()}

    vivas = sa.and_(Sucursal.deleted_at.is_(None), Sucursal.activo.is_(True))
    rem_vinculo = (
        db.query(Sucursal.nombre, Serie.codigo)
        .join(ClienteSucursal, ClienteSucursal.sucursal_id == Sucursal.id)
        .join(Serie, Serie.id == ClienteSucursal.serie_remision_id)
        .filter(ClienteSucursal.tenant_id == tenant_id, vivas,
                Serie.tipo_documento == "REMISION")
        .all()
    )
    desde = date.today() - timedelta(days=93)
    rem_uso = (
        db.query(Sucursal.nombre, Serie.codigo)
        .join(Remision, Remision.sucursal_id == Sucursal.id)
        .join(Serie, Serie.id == Remision.serie_id)
        .filter(Remision.tenant_id == tenant_id, vivas, Remision.deleted_at.is_(None),
                Remision.fecha_entrega >= desde, Serie.tipo_documento == "REMISION")
        .distinct()
        .all()
    )
    for nombre, codigo in list(rem_vinculo) + list(rem_uso):
        k = clave_nombre(nombre)
        if k:
            plazas.setdefault(k, {"nombre": nombre.strip(), "series": set(),
                                  "series_remision": set(), "perfiles": set()})
            plazas[k]["series_remision"].add(codigo)

    vistos = perfiles_vistos(db, tenant_id)
    for p, nombre in vistos.items():
        k = clave_nombre(nombre) if nombre else ""
        if k in plazas:
            plazas[k]["perfiles"].add(p)

    return {
        "plazas": [
            {"nombre": v["nombre"], "series": sorted(v["series"]),
             "series_remision": sorted(v["series_remision"]), "perfiles": sorted(v["perfiles"])}
            for v in sorted(plazas.values(), key=lambda v: v["nombre"].casefold())
        ],
        "series": series_factura(db, tenant_id),
        "series_remision": series_de_remision(db, tenant_id),
        "perfiles": sorted(vistos),
    }


def validar_alcance(db: Session, tenant_id, *, plaza: Optional[str], series, series_remision,
                    perfiles, **datos) -> dict:
    """Normaliza lo que el dueño marcó y lo deja listo para `conexiones.alcance`.

    Rechaza en vez de recortar: una serie que no es de esta empresa, o una
    bandera encendida sin nada que la abra, es un error de quien configura y
    tiene que verlo ahora, no descubrirlo cuando la merma salga en cero."""
    fac = {s.strip() for s in (series or []) if s and s.strip()}
    rem = {s.strip() for s in (series_remision or []) if s and s.strip()}
    pfs = {p.strip() for p in (perfiles or []) if p and p.strip()}
    if not fac:
        raise HTTPException(status_code=422, detail="Marca al menos una serie de factura")
    ajenas = fac - set(series_factura(db, tenant_id))
    if ajenas:
        raise HTTPException(
            status_code=422,
            detail=f"Estas series no son de factura en esta empresa: {', '.join(sorted(ajenas))}",
        )
    ajenas = rem - set(series_de_remision(db, tenant_id))
    if ajenas:
        raise HTTPException(
            status_code=422,
            detail=f"Estas series no son de remisión en esta empresa: {', '.join(sorted(ajenas))}",
        )
    malos = sorted(p for p in pfs if not _PERFIL.match(p))
    if malos:
        raise HTTPException(
            status_code=422,
            detail=f"Perfil inválido: {', '.join(malos)} (forma: EHMO:villahermosa)",
        )
    banderas = {d: bool(datos.get(d)) for d in DATOS}
    if banderas["remisiones"] and not rem:
        raise HTTPException(status_code=422,
                            detail="Para compartir remisiones marca al menos una serie de remisión")
    if banderas["oc"] and not (rem or pfs):
        raise HTTPException(
            status_code=422,
            detail="Para compartir las OC marca un perfil o una serie de remisión",
        )
    nombre_plaza = " ".join((plaza or "").split()) or None
    if nombre_plaza is not None:
        existe = db.query(Sucursal.id).filter(
            Sucursal.tenant_id == tenant_id, Sucursal.deleted_at.is_(None),
            sa.func.lower(sa.func.trim(Sucursal.nombre)) == nombre_plaza.lower(),
        ).first()
        if existe is None:
            raise HTTPException(status_code=422, detail=f"No hay plaza «{nombre_plaza}»")
    return {
        "plaza": nombre_plaza,
        "series": sorted(fac),
        "series_remision": sorted(rem),
        "perfiles": sorted(pfs),
        **banderas,
    }


# ─── Filtros por alcance ────────────────────────────────────────────────────

def filtro_oc(db: Session, tenant_id, a: AlcancePanel, serie_remision_col):
    """La condición que deja ver una OC: entró por uno de sus perfiles, o se
    volvió remisión de una de sus series. `serie_remision_col` es la columna
    `remisiones.serie_id` ya unida a la consulta (outer join)."""
    if a.perfiles is None and a.series_remision is None:
        return sa.true()
    condiciones = [
        OCRecibida.origen_externo.like(_patron_like(p), escape="\\")
        for p in sorted(a.perfiles or [])
    ]
    ids = ids_de_series(db, tenant_id, a.series_remision or [], "REMISION")
    if ids:
        condiciones.append(serie_remision_col.in_(ids))
    return sa.or_(*condiciones) if condiciones else sa.false()


# ─── Facturas por fecha de entrega ──────────────────────────────────────────

# La fecha de entrega de una factura sale de su remisión, de sus notas o de la
# factura misma (services/mini_conta.fechas_de_entrega). La de las notas solo
# vale entre factura−120 y factura+7 días (services/fecha_entrega.py), así que
# para un rango de ENTREGA basta con mirar facturas de ese rango ensanchado,
# más las que tengan una remisión entregada dentro del rango.
_NOTAS_ANTES = 120
_NOTAS_DESPUES = 7


def facturas_del_rango(db: Session, tenant_id, series: Optional[frozenset[str]],
                       desde: date, hasta: date, por: str) -> dict:
    """{factura_id: (fecha de la factura, fecha de entrega, de dónde salió)} de
    las facturas timbradas de ingreso de esas series cuya fecha `por`
    («entrega» o «factura») cae en [desde, hasta]."""
    fecha_mx = sa.cast(sa.func.timezone(ZONA, Factura.fecha), sa.Date)
    base = [
        Factura.tenant_id == tenant_id,
        Factura.estado == "TIMBRADA",
        Factura.deleted_at.is_(None),
        Factura.tipo_comprobante == "I",
    ]
    if series is not None:
        if not series:
            return {}
        base.append(Factura.serie.in_(sorted(series)))

    if por == "factura":
        filas = (db.query(Factura.id, Factura.notas, fecha_mx)
                 .filter(*base, fecha_mx >= desde, fecha_mx <= hasta).all())
    else:
        con_remision = (
            db.query(Remision.factura_id)
            .filter(Remision.tenant_id == tenant_id, Remision.deleted_at.is_(None),
                    Remision.factura_id.isnot(None),
                    Remision.fecha_entrega >= desde, Remision.fecha_entrega <= hasta)
        )
        filas = (
            db.query(Factura.id, Factura.notas, fecha_mx)
            .filter(*base, sa.or_(
                sa.and_(fecha_mx >= desde - timedelta(days=_NOTAS_DESPUES),
                        fecha_mx <= hasta + timedelta(days=_NOTAS_ANTES)),
                Factura.id.in_(con_remision),
            ))
            .all()
        )
    fechas = {fid: fecha for fid, _n, fecha in filas}
    entregas = fechas_de_entrega(db, {fid: (notas, fecha) for fid, notas, fecha in filas})
    out = {}
    for fid, (entrega, origen) in entregas.items():
        if por == "entrega" and not (desde <= entrega <= hasta):
            continue
        out[fid] = (fechas[fid], entrega, origen)
    return out

"""Mini Conta — qué puede leer cada clave y cómo se agrupan las series.

Cada cuenta de Mini Conta (Kelly Chiapas, Kelly Tabasco…) es un cliente aparte
con su propia clave, y el dueño del Facturador decide qué comparte cada una
(`conexiones.alcance`):

- `series`: las series de FACTURA cuyas ventas puede leer. Es lo que separa a
  una plaza de otra.
- `clientes`: None = todos los de esas series; una lista = solo esos.
- `catalogo`: si puede leer los productos que se facturan en esas series.
- `remisiones`: lo entregado (remisiones) en esas series, para los días que
  todavía no se facturan.
- `notas_credito`: las notas de crédito aplicadas a facturas de esas series.
- `cobranza`: los pagos recibidos (REP) y los saldos por cobrar de esas facturas.
- `precios`: el precio de lista que le toca a cada cliente de esas series.

Una clave sin `alcance` es la de antes de 0092: lee todas las series y todos los
clientes, solo ventas, hasta que el dueño le ponga un límite. Una PERSONA con
`venta:leer_lineas` (el dueño) lee todo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models import (
    Cliente,
    ClienteSucursal,
    ClienteSucursalSerie,
    Conexion,
    Factura,
    Remision,
    Serie,
    Sucursal,
)
from .fecha_entrega import fecha_entrega_de_notas


def clave_nombre(nombre: str) -> str:
    return " ".join((nombre or "").split()).casefold()


# Lo que una clave puede leer además de las ventas. Se guardan como banderas en
# `conexiones.alcance`; una bandera que falta es False.
DATOS = ("catalogo", "remisiones", "notas_credito", "cobranza", "precios")


@dataclass(frozen=True)
class Alcance:
    series: Optional[frozenset[str]]      # None = todas las de factura
    clientes: Optional[frozenset[UUID]]   # None = todos
    catalogo: bool = False
    remisiones: bool = False
    notas_credito: bool = False
    cobranza: bool = False
    precios: bool = False

    @staticmethod
    def todo() -> "Alcance":
        return Alcance(series=None, clientes=None, **{d: True for d in DATOS})

    def datos(self) -> dict:
        return {d: getattr(self, d) for d in DATOS}


def alcance_de(db: Session, ctx: AuthContext) -> Alcance:
    if ctx.conexion_id is None:
        return Alcance.todo()
    con = db.get(Conexion, ctx.conexion_id)
    datos = con.alcance if con is not None else None
    if not datos:
        return Alcance(series=None, clientes=None)
    clientes = datos.get("clientes")
    return Alcance(
        series=frozenset(datos.get("series") or []),
        clientes=None if clientes is None else frozenset(UUID(str(c)) for c in clientes),
        **{d: bool(datos.get(d)) for d in DATOS},
    )


def series_previstas(db: Session, tenant_id, pares, tipo: str = "FACTURA",
                     como: str = "codigo") -> dict:
    """{(cliente_id, sucursal_id): serie de `tipo`} con la misma cascada que
    `services.series.resolver_serie` (vínculo cliente×plaza → cliente →
    predeterminada; solo series activas de ese tipo), pero en cuatro consultas
    para todos los pares en vez de cuatro por par. `como` = "codigo" o "id"."""
    pares = set(pares)
    if not pares:
        return {}
    campo = "serie_factura_id" if tipo == "FACTURA" else "serie_remision_id"
    activas = {
        s.id: (s.codigo if como == "codigo" else s.id) for s in db.query(Serie)
        .filter(Serie.tenant_id == tenant_id, Serie.tipo_documento == tipo,
                Serie.activa.is_(True))
        .all()
    }
    default = (
        db.query(Serie.id)
        .filter(Serie.tenant_id == tenant_id, Serie.tipo_documento == tipo,
                Serie.activa.is_(True), Serie.es_default.is_(True))
        .order_by(Serie.created_at)
        .first()
    )
    clientes = {c for c, _ in pares if c}
    vinculo = {
        (cs.cliente_id, cs.sucursal_id): getattr(cs, campo)
        for cs in db.query(ClienteSucursal)
        .filter(ClienteSucursal.tenant_id == tenant_id, ClienteSucursal.cliente_id.in_(clientes))
        .all()
    } if clientes else {}
    del_cliente = dict(
        db.query(Cliente.id, getattr(Cliente, campo))
        .filter(Cliente.tenant_id == tenant_id, Cliente.id.in_(clientes))
        .all()
    ) if clientes else {}
    out = {}
    for cli, suc in pares:
        valor = activas.get(vinculo.get((cli, suc))) if suc else None
        if valor is None:
            valor = activas.get(del_cliente.get(cli))
        if valor is None and default is not None:
            valor = activas.get(default[0])
        out[(cli, suc)] = valor
    return out


def series_factura(db: Session, tenant_id) -> list[str]:
    """Todas las series de FACTURA de la empresa, activas o no: una serie que se
    apagó sigue teniendo ventas en el histórico."""
    return sorted({
        c for (c,) in db.query(Serie.codigo)
        .filter(Serie.tenant_id == tenant_id, Serie.tipo_documento == "FACTURA")
        .all()
    })


def series_por_plaza(db: Session, tenant_id) -> dict[str, tuple[str, set[str]]]:
    """{nombre normalizado: (nombre para mostrar, {códigos de serie})}.

    Serie de factura DEFAULT del vínculo cliente×plaza + su abanico. Solo
    plazas vivas y activas, y solo series de FACTURA (el abanico puede traer
    series de remisión, que aquí no cuentan). Una plaza puede tener varias filas
    con el mismo nombre, así que se agrupa por nombre."""
    vivas = sa.and_(Sucursal.deleted_at.is_(None), Sucursal.activo.is_(True))
    principal = (
        db.query(Sucursal.nombre, Serie.codigo)
        .join(ClienteSucursal, ClienteSucursal.sucursal_id == Sucursal.id)
        .join(Serie, Serie.id == ClienteSucursal.serie_factura_id)
        .filter(ClienteSucursal.tenant_id == tenant_id, vivas,
                Serie.tipo_documento == "FACTURA")
    )
    abanico = (
        db.query(Sucursal.nombre, Serie.codigo)
        .join(ClienteSucursal, ClienteSucursal.sucursal_id == Sucursal.id)
        .join(ClienteSucursalSerie, ClienteSucursalSerie.cliente_sucursal_id == ClienteSucursal.id)
        .join(Serie, Serie.id == ClienteSucursalSerie.serie_id)
        .filter(ClienteSucursal.tenant_id == tenant_id, vivas,
                Serie.tipo_documento == "FACTURA")
    )
    out: dict[str, tuple[str, set[str]]] = {}
    for nombre, codigo in sorted(list(principal) + list(abanico)):
        k = clave_nombre(nombre)
        if not k:
            continue
        out.setdefault(k, (nombre.strip(), set()))[1].add(codigo)
    return out


def clientes_por_serie(db: Session, tenant_id) -> dict[UUID, set[str]]:
    """{cliente_id: {series de factura}}: las que tiene configuradas (vínculo
    cliente×plaza vivo, su abanico, la del propio cliente) más las que de verdad
    ha usado en una factura timbrada."""
    out: dict[UUID, set[str]] = {}
    vivas = sa.and_(Sucursal.deleted_at.is_(None), Sucursal.activo.is_(True))

    def _add(filas):
        for cid, codigo in filas:
            if cid is not None and codigo:
                out.setdefault(cid, set()).add(codigo)

    _add(
        db.query(ClienteSucursal.cliente_id, Serie.codigo)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .join(Serie, Serie.id == ClienteSucursal.serie_factura_id)
        .filter(ClienteSucursal.tenant_id == tenant_id, vivas,
                Serie.tipo_documento == "FACTURA")
        .all()
    )
    _add(
        db.query(ClienteSucursal.cliente_id, Serie.codigo)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .join(ClienteSucursalSerie, ClienteSucursalSerie.cliente_sucursal_id == ClienteSucursal.id)
        .join(Serie, Serie.id == ClienteSucursalSerie.serie_id)
        .filter(ClienteSucursal.tenant_id == tenant_id, vivas,
                Serie.tipo_documento == "FACTURA")
        .all()
    )
    _add(
        db.query(Cliente.id, Serie.codigo)
        .join(Serie, Serie.id == Cliente.serie_factura_id)
        .filter(Cliente.tenant_id == tenant_id, Cliente.deleted_at.is_(None),
                Serie.tipo_documento == "FACTURA")
        .all()
    )
    _add(
        db.query(Factura.cliente_id, Factura.serie)
        .filter(Factura.tenant_id == tenant_id, Factura.estado == "TIMBRADA",
                Factura.tipo_comprobante == "I", Factura.deleted_at.is_(None))
        .distinct()
        .all()
    )
    return out


def mapa(db: Session, tenant_id, alcance: Alcance) -> dict:
    """Lo que el alcance deja ver, listo para escoger: plazas con sus series,
    todas las series y los clientes (con las series en que aparecen)."""
    universo = set(series_factura(db, tenant_id))
    series = universo if alcance.series is None else universo & set(alcance.series)

    sucursales = []
    for nombre, codigos in sorted(series_por_plaza(db, tenant_id).values(),
                                  key=lambda x: x[0].casefold()):
        propias = sorted(codigos & series)
        if propias:
            sucursales.append({"nombre": nombre, "series": propias})

    por_cliente = clientes_por_serie(db, tenant_id)
    ids = [
        cid for cid, cs in por_cliente.items()
        if cs & series and (alcance.clientes is None or cid in alcance.clientes)
    ]
    clientes = []
    if ids:
        for c in (
            db.query(Cliente)
            .filter(Cliente.tenant_id == tenant_id, Cliente.id.in_(ids),
                    Cliente.deleted_at.is_(None))
            .all()
        ):
            clientes.append({
                "id": c.id,
                "nombre": c.legal_name,
                "rfc": c.rfc,
                "series": sorted(por_cliente[c.id] & series),
            })
    clientes.sort(key=lambda c: c["nombre"].casefold())
    return {
        "sucursales": sucursales,
        "series": sorted(series),
        "clientes": clientes,
        **alcance.datos(),
    }


def fechas_de_entrega(db: Session, facturas: dict) -> dict:
    """{factura_id: (fecha de entrega, de dónde salió)} para {factura_id: (notas,
    fecha de la factura)}: la primera entrega de sus remisiones, si no la que
    dicen sus notas, si no la de la factura. Una consulta para todas.

    Vive aquí y no en el router porque la usan Mini Conta y el panel de Smart
    Supply (services/smart_supply.py): dos copias darían dos fechas distintas
    para la misma factura."""
    por_remision: dict = {}
    if facturas:
        por_remision = dict(
            db.query(Remision.factura_id, sa.func.min(Remision.fecha_entrega))
            .filter(
                Remision.factura_id.in_(list(facturas)),
                Remision.deleted_at.is_(None),
                Remision.fecha_entrega.isnot(None),
            )
            .group_by(Remision.factura_id)
            .all()
        )
    out = {}
    for fid, (notas, fecha_factura) in facturas.items():
        if por_remision.get(fid) is not None:
            out[fid] = (por_remision[fid], "remision")
        else:
            de_notas = fecha_entrega_de_notas(notas, fecha_factura)
            out[fid] = (de_notas, "notas") if de_notas is not None else (fecha_factura, "factura")
    return out


def validar_alcance(db: Session, tenant_id, series, clientes, **datos) -> dict:
    """Normaliza lo que el dueño marcó en la pantalla y lo deja listo para
    guardarse en `conexiones.alcance`. Una clave sin series no lee nada: se
    rechaza en vez de crear una conexión inútil."""
    universo = set(series_factura(db, tenant_id))
    pedidas = {s.strip() for s in (series or []) if s and s.strip()}
    if not pedidas:
        raise HTTPException(status_code=422, detail="Marca al menos una serie")
    ajenas = pedidas - universo
    if ajenas:
        raise HTTPException(
            status_code=422,
            detail=f"Estas series no son de factura en esta empresa: {', '.join(sorted(ajenas))}",
        )
    ids = None
    if clientes is not None:
        ids = sorted({UUID(str(c)) for c in clientes}, key=str)
        if not ids:
            raise HTTPException(status_code=422, detail="Marca al menos un cliente o deja «Todos»")
        encontrados = {
            cid for (cid,) in db.query(Cliente.id)
            .filter(Cliente.tenant_id == tenant_id, Cliente.id.in_(ids),
                    Cliente.deleted_at.is_(None))
            .all()
        }
        if len(encontrados) != len(ids):
            raise HTTPException(status_code=422, detail="Algún cliente ya no existe")
    return {
        "series": sorted(pedidas),
        "clientes": None if ids is None else [str(i) for i in ids],
        **{d: bool(datos.get(d)) for d in DATOS},
    }

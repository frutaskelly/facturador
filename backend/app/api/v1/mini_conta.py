"""Mini Conta — las ventas facturadas de una plaza, línea por línea.

Mini Conta lleva la contabilidad por sucursal y cruza lo que COMPRÓ contra lo
que aquí se FACTURÓ para sacar merma y ganancia por producto. Entra con una
clave de conexión tipo MINI_CONTA, cuyo único permiso es `venta:leer_lineas`
(ver core/rbac.py::PERMISOS_POR_TIPO): lee y nada más.

Cada cuenta de Mini Conta tiene SU clave, y cada clave su alcance (qué series,
qué clientes, si ve el catálogo; ver services/mini_conta.py). Todo lo de aquí
responde solo dentro de ese alcance: una cuenta nunca lee lo de otra.

Mini Conta pide las ventas por `series` (lo que la cuenta escogió traer) y,
opcionalmente, por `clientes`. La forma vieja —por «sucursal», un NOMBRE
(«Chiapas») que se traduce a las series de sus vínculos cliente×plaza— sigue
sirviendo para las cuentas que todavía no escogen series.

Lo que más importa es la FECHA DE ENTREGA: la factura se hace días después de
entregar, y el reporte de Mini Conta va por el día en que salió la mercancía.
Se toma, en orden, de la remisión ligada, de las notas de la factura
(services/fecha_entrega.py) o, a falta de ambas, de la fecha de la factura; y
se dice de dónde salió (`fecha_entrega_origen`).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, Conexion, Factura, LineaFactura, Producto, Remision, Tenant
from ...services.fecha_entrega import fecha_entrega_de_notas
from ...services.mini_conta import (
    Alcance,
    alcance_de,
    clave_nombre,
    mapa,
    series_factura,
    series_por_plaza,
)

router = APIRouter(prefix="/mini-conta", tags=["mini-conta"])

_LEER = "venta:leer_lineas"
_ZONA = "America/Mexico_City"
_MAX_DIAS = 400
_CENTAVO = Decimal("0.01")


class SucursalSeriesOut(BaseModel):
    nombre: str
    series: list[str]


class ClienteAlcanceOut(BaseModel):
    id: UUID
    nombre: str
    rfc: Optional[str] = None
    series: list[str]


class EmpresaOut(BaseModel):
    id: UUID
    nombre: str


class ConexionBreveOut(BaseModel):
    id: UUID
    nombre: str


class AlcanceOut(BaseModel):
    """Lo que esta clave comparte, listo para que Mini Conta escoja qué traer."""
    empresa: EmpresaOut
    conexion: Optional[ConexionBreveOut] = None
    sin_limite: bool = False      # clave de antes de 0092: todavía sin alcance
    sucursales: list[SucursalSeriesOut]
    series: list[str]
    clientes: list[ClienteAlcanceOut]
    catalogo: bool


class LineaVentaOut(BaseModel):
    linea_id: UUID
    factura_id: UUID
    uuid_cfdi: Optional[str] = None
    serie: str
    folio: int
    fecha_factura: date
    fecha_entrega: date
    fecha_entrega_origen: Literal["remision", "notas", "factura"]
    cliente_id: Optional[UUID] = None
    cliente: Optional[str] = None
    sku: Optional[str] = None
    producto: Optional[str] = None
    descripcion: str
    clave_unidad: str
    presentacion: Optional[str] = None
    cantidad: str               # decimal como texto: sin pérdida por float
    importe: str                # importe − descuento, SIN IVA


class VentasOut(BaseModel):
    sucursal: Optional[str] = None
    series: list[str]
    clientes: Optional[list[UUID]] = None
    desde: date
    hasta: date
    lineas: list[LineaVentaOut]


class ProductoVendidoOut(BaseModel):
    sku: str
    nombre: str
    unidad_sat: str
    unidad_base: Optional[str] = None
    presentacion_default: Optional[str] = None
    lineas: int
    ultima_venta: date


class ProductosOut(BaseModel):
    series: list[str]
    desde: date
    hasta: date
    productos: list[ProductoVendidoOut]


def _lista(texto: Optional[str]) -> list[str]:
    """`?series=A,B` → ["A", "B"]. Mini Conta arma la URL desde Postgres y ahí
    una lista repetida es incómoda; una coma basta."""
    return [x.strip() for x in (texto or "").split(",") if x.strip()]


def _permitidas(db: Session, ctx: AuthContext, alcance: Alcance) -> set[str]:
    universo = set(series_factura(db, ctx.tenant_id))
    return universo if alcance.series is None else universo & set(alcance.series)


def _resolver_series(
    db: Session, ctx: AuthContext, alcance: Alcance,
    series: Optional[str], sucursal: Optional[str],
) -> tuple[list[str], Optional[str]]:
    """Las series a leer, siempre dentro del alcance. Pedir una que la clave no
    comparte es un 403 que lo dice, no un recorte silencioso: si el dueño le
    quitó una serie a la cuenta, Mini Conta tiene que enterarse."""
    permitidas = _permitidas(db, ctx, alcance)
    pedidas = _lista(series)
    if pedidas:
        fuera = sorted(set(pedidas) - permitidas)
        if fuera:
            raise HTTPException(
                status_code=403,
                detail=f"Esta conexión no comparte la serie {', '.join(fuera)}",
            )
        return sorted(set(pedidas)), None
    if sucursal:
        encontrada = series_por_plaza(db, ctx.tenant_id).get(clave_nombre(sucursal))
        if encontrada is None:
            raise HTTPException(status_code=404, detail=f"No hay sucursal «{sucursal}» con series")
        nombre, propias = encontrada
        visibles = sorted(propias & permitidas)
        if not visibles:
            raise HTTPException(
                status_code=403,
                detail=f"Esta conexión no comparte las series de «{nombre}»",
            )
        return visibles, nombre
    if not permitidas:
        raise HTTPException(status_code=403, detail="Esta conexión no comparte ninguna serie")
    return sorted(permitidas), None


def _resolver_clientes(alcance: Alcance, clientes: Optional[str]) -> Optional[list[UUID]]:
    pedidos = _lista(clientes)
    if not pedidos:
        return None if alcance.clientes is None else sorted(alcance.clientes, key=str)
    try:
        ids = {UUID(c) for c in pedidos}
    except ValueError:
        raise HTTPException(status_code=422, detail="«clientes» trae un id que no es válido")
    if alcance.clientes is not None and not ids <= alcance.clientes:
        raise HTTPException(
            status_code=403, detail="Esta conexión no comparte alguno de esos clientes"
        )
    return sorted(ids, key=str)


def _rango(desde: date, hasta: date) -> None:
    if hasta < desde:
        raise HTTPException(status_code=422, detail="«hasta» no puede ser antes de «desde»")
    if (hasta - desde).days > _MAX_DIAS:
        raise HTTPException(
            status_code=422, detail=f"El rango no puede pasar de {_MAX_DIAS} días"
        )


@router.get("/alcance", response_model=AlcanceOut)
def alcance(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Qué comparte esta clave: plazas con sus series, clientes y si hay
    catálogo. Mini Conta lo pide al conectar y al cambiar qué trae."""
    a = alcance_de(db, ctx)
    t = db.get(Tenant, ctx.tenant_id)
    con = db.get(Conexion, ctx.conexion_id) if ctx.conexion_id else None
    m = mapa(db, ctx.tenant_id, a)
    return AlcanceOut(
        empresa=EmpresaOut(id=ctx.tenant_id, nombre=(t.legal_name if t else "") or ""),
        conexion=ConexionBreveOut(id=con.id, nombre=con.nombre) if con else None,
        sin_limite=con is not None and not con.alcance,
        sucursales=[SucursalSeriesOut(**s) for s in m["sucursales"]],
        series=m["series"],
        clientes=[ClienteAlcanceOut(**c) for c in m["clientes"]],
        catalogo=m["catalogo"],
    )


@router.get("/sucursales", response_model=list[SucursalSeriesOut])
def sucursales(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Cada plaza con las series de factura que le pertenecen y que esta clave
    comparte. Las plazas sin ninguna serie compartida no salen."""
    return [SucursalSeriesOut(**s) for s in mapa(db, ctx.tenant_id, alcance_de(db, ctx))["sucursales"]]


@router.get("/ventas", response_model=VentasOut)
def ventas(
    desde: date = Query(...),
    hasta: date = Query(...),
    series: Optional[str] = Query(None, description="Códigos separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    sucursal: Optional[str] = Query(None, description="Forma vieja: el nombre de la plaza"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las líneas de las facturas timbradas (ingreso, sin notas de crédito) de
    las series pedidas —o las de la plaza, o todas las que comparte la clave—,
    con `desde`/`hasta` sobre la fecha de la factura (hora de México), ambos
    inclusive. `clientes` recorta a esos clientes."""
    _rango(desde, hasta)
    a = alcance_de(db, ctx)
    series_leer, nombre = _resolver_series(db, ctx, a, series, sucursal)
    clientes_leer = _resolver_clientes(a, clientes)

    fecha_mx = sa.cast(sa.func.timezone(_ZONA, Factura.fecha), sa.Date)
    filas = (
        db.query(
            LineaFactura.id.label("linea_id"),
            LineaFactura.numero_linea,
            LineaFactura.descripcion,
            LineaFactura.clave_unidad,
            LineaFactura.presentacion,
            LineaFactura.cantidad,
            LineaFactura.importe,
            LineaFactura.descuento,
            Factura.id.label("factura_id"),
            Factura.uuid.label("uuid_cfdi"),
            Factura.serie,
            Factura.folio,
            Factura.notas,
            fecha_mx.label("fecha_factura"),
            Factura.cliente_id,
            Cliente.legal_name.label("cliente"),
            Producto.sku,
            Producto.nombre.label("producto"),
        )
        .join(Factura, Factura.id == LineaFactura.factura_id)
        .outerjoin(Cliente, Cliente.id == Factura.cliente_id)
        .outerjoin(Producto, Producto.id == LineaFactura.producto_id)
        .filter(
            Factura.tenant_id == ctx.tenant_id,
            Factura.estado == "TIMBRADA",
            Factura.deleted_at.is_(None),
            Factura.tipo_comprobante == "I",
            Factura.serie.in_(series_leer),
            fecha_mx >= desde,
            fecha_mx <= hasta,
        )
    )
    if clientes_leer is not None:
        filas = filas.filter(Factura.cliente_id.in_(clientes_leer))
    filas = (
        filas.order_by(fecha_mx, Factura.serie, Factura.folio, LineaFactura.numero_linea)
        .all()
    )

    # Fecha de entrega por remisión: una consulta para todas las facturas. Si
    # una factura junta varias remisiones, cuenta la primera entrega.
    factura_ids = list({f.factura_id for f in filas})
    por_remision: dict = {}
    if factura_ids:
        por_remision = dict(
            db.query(Remision.factura_id, sa.func.min(Remision.fecha_entrega))
            .filter(
                Remision.factura_id.in_(factura_ids),
                Remision.deleted_at.is_(None),
                Remision.fecha_entrega.isnot(None),
            )
            .group_by(Remision.factura_id)
            .all()
        )

    entrega_de: dict = {}
    lineas: list[LineaVentaOut] = []
    for f in filas:
        if f.factura_id not in entrega_de:
            if por_remision.get(f.factura_id) is not None:
                entrega_de[f.factura_id] = (por_remision[f.factura_id], "remision")
            else:
                de_notas = fecha_entrega_de_notas(f.notas, f.fecha_factura)
                entrega_de[f.factura_id] = (
                    (de_notas, "notas") if de_notas is not None else (f.fecha_factura, "factura")
                )
        entrega, origen = entrega_de[f.factura_id]
        importe = (Decimal(f.importe or 0) - Decimal(f.descuento or 0)).quantize(_CENTAVO)
        lineas.append(LineaVentaOut(
            linea_id=f.linea_id,
            factura_id=f.factura_id,
            uuid_cfdi=f.uuid_cfdi,
            serie=f.serie,
            folio=f.folio,
            fecha_factura=f.fecha_factura,
            fecha_entrega=entrega,
            fecha_entrega_origen=origen,
            cliente_id=f.cliente_id,
            cliente=f.cliente,
            sku=f.sku,
            producto=f.producto,
            descripcion=f.descripcion,
            clave_unidad=f.clave_unidad,
            presentacion=f.presentacion,
            cantidad=str(f.cantidad),
            importe=str(importe),
        ))

    return VentasOut(sucursal=nombre, series=series_leer, clientes=clientes_leer,
                     desde=desde, hasta=hasta, lineas=lineas)


@router.get("/productos", response_model=ProductosOut)
def productos(
    desde: date = Query(...),
    hasta: date = Query(...),
    series: Optional[str] = Query(None, description="Códigos separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Los productos que se facturaron en esas series entre `desde` y `hasta`:
    el catálogo que le sirve a ESTA cuenta, no los 1,500 de la empresa. Solo si
    la conexión comparte el catálogo."""
    a = alcance_de(db, ctx)
    if not a.catalogo:
        raise HTTPException(status_code=403, detail="Esta conexión no comparte el catálogo")
    _rango(desde, hasta)
    series_leer, _ = _resolver_series(db, ctx, a, series, None)
    clientes_leer = _resolver_clientes(a, clientes)

    fecha_mx = sa.cast(sa.func.timezone(_ZONA, Factura.fecha), sa.Date)
    q = (
        db.query(
            Producto.sku,
            Producto.nombre,
            Producto.unidad_sat,
            Producto.unidad_base,
            Producto.presentacion_default,
            sa.func.count(LineaFactura.id).label("lineas"),
            sa.func.max(fecha_mx).label("ultima_venta"),
        )
        .join(LineaFactura, LineaFactura.producto_id == Producto.id)
        .join(Factura, Factura.id == LineaFactura.factura_id)
        .filter(
            Factura.tenant_id == ctx.tenant_id,
            Factura.estado == "TIMBRADA",
            Factura.deleted_at.is_(None),
            Factura.tipo_comprobante == "I",
            Factura.serie.in_(series_leer),
            fecha_mx >= desde,
            fecha_mx <= hasta,
            Producto.deleted_at.is_(None),
        )
    )
    if clientes_leer is not None:
        q = q.filter(Factura.cliente_id.in_(clientes_leer))
    filas = (
        q.group_by(Producto.id, Producto.sku, Producto.nombre, Producto.unidad_sat,
                   Producto.unidad_base, Producto.presentacion_default)
        .order_by(Producto.nombre)
        .all()
    )
    return ProductosOut(
        series=series_leer, desde=desde, hasta=hasta,
        productos=[ProductoVendidoOut(
            sku=f.sku, nombre=f.nombre, unidad_sat=f.unidad_sat, unidad_base=f.unidad_base,
            presentacion_default=f.presentacion_default, lineas=f.lineas,
            ultima_venta=f.ultima_venta,
        ) for f in filas],
    )

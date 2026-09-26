"""Mini Conta — las ventas facturadas de una plaza, línea por línea.

Mini Conta lleva la contabilidad por sucursal y cruza lo que COMPRÓ contra lo
que aquí se FACTURÓ para sacar merma y ganancia por producto. Entra con una
clave de conexión tipo MINI_CONTA, cuyo único permiso es `venta:leer_lineas`
(ver core/rbac.py::PERMISOS_POR_TIPO): lee y nada más.

La «sucursal» de Mini Conta es un NOMBRE («Chiapas», «Pachuca»), y aquí una
plaza puede tener varias filas con el mismo nombre; así que se agrupa por
nombre sin distinguir mayúsculas, y una plaza son todas las series de factura
que sus vínculos cliente×plaza tienen asignadas.

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
from ...models import (
    ClienteSucursal,
    ClienteSucursalSerie,
    Factura,
    LineaFactura,
    Producto,
    Remision,
    Serie,
    Sucursal,
)
from ...services.fecha_entrega import fecha_entrega_de_notas

router = APIRouter(prefix="/mini-conta", tags=["mini-conta"])

_LEER = "venta:leer_lineas"
_ZONA = "America/Mexico_City"
_MAX_DIAS = 400
_CENTAVO = Decimal("0.01")


class SucursalSeriesOut(BaseModel):
    nombre: str
    series: list[str]


class LineaVentaOut(BaseModel):
    linea_id: UUID
    factura_id: UUID
    uuid_cfdi: Optional[str] = None
    serie: str
    folio: int
    fecha_factura: date
    fecha_entrega: date
    fecha_entrega_origen: Literal["remision", "notas", "factura"]
    sku: Optional[str] = None
    producto: Optional[str] = None
    descripcion: str
    clave_unidad: str
    presentacion: Optional[str] = None
    cantidad: str               # decimal como texto: sin pérdida por float
    importe: str                # importe − descuento, SIN IVA


class VentasOut(BaseModel):
    sucursal: str
    series: list[str]
    desde: date
    hasta: date
    lineas: list[LineaVentaOut]


def _clave(nombre: str) -> str:
    return " ".join((nombre or "").split()).casefold()


def _series_por_sucursal(db: Session, tenant_id) -> dict[str, tuple[str, set[str]]]:
    """{nombre normalizado: (nombre para mostrar, {códigos de serie})}.

    Serie de factura DEFAULT del vínculo cliente×plaza + su abanico. Solo
    plazas vivas y activas, y solo series de FACTURA (el abanico puede traer
    series de remisión, que aquí no cuentan)."""
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
        k = _clave(nombre)
        if not k:
            continue
        out.setdefault(k, (nombre.strip(), set()))[1].add(codigo)
    return out


@router.get("/sucursales", response_model=list[SucursalSeriesOut])
def sucursales(
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Cada plaza del inquilino con las series de factura que le pertenecen."""
    mapa = _series_por_sucursal(db, ctx.tenant_id)
    return [
        SucursalSeriesOut(nombre=nombre, series=sorted(series))
        for nombre, series in sorted(mapa.values(), key=lambda x: x[0].casefold())
    ]


@router.get("/ventas", response_model=VentasOut)
def ventas(
    sucursal: str = Query(..., min_length=1),
    desde: date = Query(...),
    hasta: date = Query(...),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las líneas de las facturas timbradas (ingreso, sin notas de crédito) de
    las series de la plaza, con `desde`/`hasta` sobre la fecha de la factura
    (hora de México), ambos inclusive."""
    if hasta < desde:
        raise HTTPException(status_code=422, detail="«hasta» no puede ser antes de «desde»")
    if (hasta - desde).days > _MAX_DIAS:
        raise HTTPException(
            status_code=422, detail=f"El rango no puede pasar de {_MAX_DIAS} días"
        )

    encontrada = _series_por_sucursal(db, ctx.tenant_id).get(_clave(sucursal))
    if encontrada is None:
        raise HTTPException(status_code=404, detail=f"No hay sucursal «{sucursal}» con series")
    nombre, series_set = encontrada
    series = sorted(series_set)

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
            Producto.sku,
            Producto.nombre.label("producto"),
        )
        .join(Factura, Factura.id == LineaFactura.factura_id)
        .outerjoin(Producto, Producto.id == LineaFactura.producto_id)
        .filter(
            Factura.tenant_id == ctx.tenant_id,
            Factura.estado == "TIMBRADA",
            Factura.deleted_at.is_(None),
            Factura.tipo_comprobante == "I",
            Factura.serie.in_(series),
            fecha_mx >= desde,
            fecha_mx <= hasta,
        )
        .order_by(fecha_mx, Factura.serie, Factura.folio, LineaFactura.numero_linea)
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
            sku=f.sku,
            producto=f.producto,
            descripcion=f.descripcion,
            clave_unidad=f.clave_unidad,
            presentacion=f.presentacion,
            cantidad=str(f.cantidad),
            importe=str(importe),
        ))

    return VentasOut(sucursal=nombre, series=series, desde=desde, hasta=hasta, lineas=lineas)

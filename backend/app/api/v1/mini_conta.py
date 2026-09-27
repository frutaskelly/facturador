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

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Literal, Optional
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Cliente,
    ClienteSucursal,
    Conexion,
    Factura,
    LineaFactura,
    LineaRemision,
    NotaCredito,
    NotaCreditoFactura,
    Producto,
    ReciboPago,
    ReciboPagoFactura,
    Remision,
    Sucursal,
    Tenant,
)
from ...services.fecha_entrega import fecha_entrega_de_notas
from ...services.mini_conta import (
    Alcance,
    alcance_de,
    clave_nombre,
    mapa,
    series_factura,
    series_por_plaza,
    series_previstas,
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
    """Nombre y series, nada más: Mini Conta no necesita el RFC para escoger."""
    id: UUID
    nombre: str
    series: list[str]


class MCEmpresaOut(BaseModel):
    id: UUID
    nombre: str


class ConexionBreveOut(BaseModel):
    id: UUID
    nombre: str


class AlcanceOut(BaseModel):
    """Lo que esta clave comparte, listo para que Mini Conta escoja qué traer."""
    empresa: MCEmpresaOut
    conexion: Optional[ConexionBreveOut] = None
    sin_limite: bool = False      # clave de antes de 0092: todavía sin alcance
    sucursales: list[SucursalSeriesOut]
    series: list[str]
    clientes: list[ClienteAlcanceOut]
    catalogo: bool
    remisiones: bool = False
    notas_credito: bool = False
    cobranza: bool = False
    precios: bool = False


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


def _entregas(db: Session, facturas: dict) -> dict:
    """{factura_id: (fecha de entrega, de dónde salió)} para {factura_id: (notas,
    fecha de la factura)}: la primera entrega de sus remisiones, si no la que
    dicen sus notas, si no la de la factura. Una consulta para todas."""
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


def _requiere(bandera: bool, que: str) -> None:
    if not bandera:
        raise HTTPException(status_code=403, detail=f"Esta conexión no comparte {que}")


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
        empresa=MCEmpresaOut(id=ctx.tenant_id, nombre=(t.legal_name if t else "") or ""),
        conexion=ConexionBreveOut(id=con.id, nombre=con.nombre) if con else None,
        sin_limite=con is not None and not con.alcance,
        sucursales=[SucursalSeriesOut(**s) for s in m["sucursales"]],
        series=m["series"],
        clientes=[ClienteAlcanceOut(id=c["id"], nombre=c["nombre"], series=c["series"])
                  for c in m["clientes"]],
        **a.datos(),
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

    entrega_de = _entregas(db, {f.factura_id: (f.notas, f.fecha_factura) for f in filas})
    lineas: list[LineaVentaOut] = []
    for f in filas:
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



# ─── Fase 2: remisiones y notas de crédito ───────────────────────────────────


class MCLineaRemisionOut(BaseModel):
    linea_id: UUID
    remision_id: UUID
    folio: str
    estado: str
    facturada: bool             # ya tiene factura TIMBRADA ligada
    fecha_entrega: date
    serie: str                  # la serie de FACTURA que le toca (prevista)
    plaza: Optional[str] = None
    cliente_id: UUID
    cliente: Optional[str] = None
    sku: Optional[str] = None
    producto: Optional[str] = None
    descripcion: str
    clave_unidad: Optional[str] = None
    presentacion: Optional[str] = None
    cantidad: str
    importe: str                # sin IVA, con el descuento del encabezado prorrateado


class MCRemisionesOut(BaseModel):
    series: list[str]
    desde: date
    hasta: date
    lineas: list[MCLineaRemisionOut]


@router.get("/remisiones", response_model=MCRemisionesOut)
def remisiones(
    desde: date = Query(...),
    hasta: date = Query(...),
    series: Optional[str] = Query(None, description="Códigos de FACTURA separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Lo ENTREGADO: las líneas de las remisiones no canceladas con `fecha_entrega`
    en el rango cuya serie de factura prevista es de las pedidas.

    Van todas, facturadas o no (`facturada` lo dice): la factura se hace días
    después y muchas remisiones que el SAE ya facturó no quedan ligadas aquí, así
    que «sin factura» no es confiable. Quien las lea las usa EN LUGAR de las
    facturas para los días que todavía no se facturan, nunca además de ellas."""
    a = alcance_de(db, ctx)
    _requiere(a.remisiones, "las remisiones")
    _rango(desde, hasta)
    series_leer, _ = _resolver_series(db, ctx, a, series, None)
    clientes_leer = _resolver_clientes(a, clientes)

    rems = (
        db.query(Remision)
        .filter(
            Remision.tenant_id == ctx.tenant_id,
            Remision.deleted_at.is_(None),
            Remision.estado != "CANCELADA",
            Remision.fecha_entrega >= desde,
            Remision.fecha_entrega <= hasta,
        )
        .all()
    )
    if clientes_leer is not None:
        permitidos = set(clientes_leer)
        rems = [r for r in rems if r.cliente_facturacion_id in permitidos]
    prevista = series_previstas(
        db, ctx.tenant_id, {(r.cliente_facturacion_id, r.sucursal_id) for r in rems})
    quedan = set(series_leer)
    rems = [r for r in rems if prevista.get((r.cliente_facturacion_id, r.sucursal_id)) in quedan]
    if not rems:
        return MCRemisionesOut(series=series_leer, desde=desde, hasta=hasta, lineas=[])

    por_id = {r.id: r for r in rems}
    timbradas = {
        fid for (fid,) in db.query(Factura.id)
        .filter(Factura.id.in_([r.factura_id for r in rems if r.factura_id]),
                Factura.estado == "TIMBRADA")
        .all()
    } if any(r.factura_id for r in rems) else set()
    nombres = dict(
        db.query(Cliente.id, Cliente.legal_name)
        .filter(Cliente.id.in_({r.cliente_facturacion_id for r in rems}))
        .all()
    )
    plazas = dict(
        db.query(Sucursal.id, Sucursal.nombre)
        .filter(Sucursal.id.in_({r.sucursal_id for r in rems if r.sucursal_id}))
        .all()
    ) if any(r.sucursal_id for r in rems) else {}

    from ...services.inventario import presentacion_sat

    lineas: list[MCLineaRemisionOut] = []
    filas = (
        db.query(LineaRemision, Producto)
        .join(Producto, Producto.id == LineaRemision.producto_id)
        .filter(LineaRemision.remision_id.in_(list(por_id)))
        .order_by(LineaRemision.remision_id, LineaRemision.numero_linea)
        .all()
    )
    for ln, prod in filas:
        r = por_id[ln.remision_id]
        if prod.peso_variable and (ln.cantidad_surtida or 0) > 0:
            cantidad, clave = Decimal(ln.cantidad_surtida), prod.unidad_sat
        else:
            cantidad = Decimal(ln.cantidad_solicitada or 0)
            clave = presentacion_sat(prod, ln.presentacion) or prod.unidad_sat
        if cantidad <= 0:
            continue    # devuelta completa: la factura tampoco la llevaría
        importe = Decimal(ln.importe or 0)
        sub = Decimal(r.subtotal or 0)
        if r.descuento and sub > 0:
            importe = importe * (1 - Decimal(r.descuento) / sub)
        lineas.append(MCLineaRemisionOut(
            linea_id=ln.id,
            remision_id=r.id,
            folio=r.folio_interno,
            estado=str(r.estado),
            facturada=r.factura_id in timbradas,
            fecha_entrega=r.fecha_entrega,
            serie=prevista[(r.cliente_facturacion_id, r.sucursal_id)],
            plaza=plazas.get(r.sucursal_id),
            cliente_id=r.cliente_facturacion_id,
            cliente=nombres.get(r.cliente_facturacion_id),
            sku=prod.sku,
            producto=prod.nombre,
            descripcion=prod.nombre,
            clave_unidad=clave,
            presentacion=ln.presentacion,
            cantidad=str(cantidad),
            importe=str(importe.quantize(_CENTAVO)),
        ))
    return MCRemisionesOut(series=series_leer, desde=desde, hasta=hasta, lineas=lineas)


class MCLineaNotaOut(BaseModel):
    linea_id: UUID              # estable: nota × aplicación × línea de la factura
    nota_id: UUID
    nota: str                   # «NC 45»
    uuid_cfdi: Optional[str] = None
    fecha_nota: date
    factura_id: UUID
    serie: str                  # de la FACTURA a la que se aplicó
    folio: int
    fecha_entrega: date         # la de esa factura: la nota corrige esa venta
    fecha_entrega_origen: Literal["remision", "notas", "factura"]
    cliente_id: Optional[UUID] = None
    cliente: Optional[str] = None
    sku: Optional[str] = None
    producto: Optional[str] = None
    descripcion: str
    clave_unidad: str
    presentacion: Optional[str] = None
    cantidad: str               # siempre 0: la nota es solo dinero
    importe: str                # NEGATIVO, sin IVA


class MCNotasOut(BaseModel):
    series: list[str]
    desde: date
    hasta: date
    lineas: list[MCLineaNotaOut]
    sin_factura: int = 0        # aplicaciones a facturas que aquí no existen


def _uuid_de(*partes) -> UUID:
    import uuid as _uuid

    return _uuid.uuid5(_uuid.NAMESPACE_URL, "mini-conta:" + ":".join(str(p) for p in partes))


@router.get("/notas-credito", response_model=MCNotasOut)
def notas_credito(
    desde: date = Query(...),
    hasta: date = Query(...),
    series: Optional[str] = Query(None, description="Códigos de FACTURA separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Las notas de crédito vigentes con fecha en el rango, aplicadas a facturas
    de las series pedidas.

    Una nota aquí es solo DINERO (el SAE no trae sus productos): cada aplicación
    se reparte entre las líneas de la factura en proporción a su importe, con
    cantidad 0 e importe negativo, y con la fecha de entrega de esa factura —
    la nota corrige esa venta, no una del día en que se emitió."""
    a = alcance_de(db, ctx)
    _requiere(a.notas_credito, "las notas de crédito")
    _rango(desde, hasta)
    series_leer, _ = _resolver_series(db, ctx, a, series, None)
    clientes_leer = _resolver_clientes(a, clientes)

    fecha_mx = sa.cast(sa.func.timezone(_ZONA, NotaCredito.fecha), sa.Date)
    q = (
        db.query(NotaCredito, NotaCreditoFactura, Factura, fecha_mx.label("fecha_nota"),
                 sa.cast(sa.func.timezone(_ZONA, Factura.fecha), sa.Date).label("fecha_factura"))
        .join(NotaCreditoFactura, NotaCreditoFactura.nota_id == NotaCredito.id)
        .outerjoin(Factura, Factura.id == NotaCreditoFactura.factura_id)
        .filter(
            NotaCredito.tenant_id == ctx.tenant_id,
            NotaCredito.estado == "VIGENTE",
            fecha_mx >= desde,
            fecha_mx <= hasta,
        )
    )
    aplicaciones = q.all()
    sin_factura = sum(1 for _, _, f, _, _ in aplicaciones if f is None)
    permitidos = None if clientes_leer is None else set(clientes_leer)
    aplicaciones = [
        x for x in aplicaciones
        if x[2] is not None and x[2].serie in series_leer and x[2].estado == "TIMBRADA"
        and (permitidos is None or x[2].cliente_id in permitidos)
    ]
    if not aplicaciones:
        return MCNotasOut(series=series_leer, desde=desde, hasta=hasta, lineas=[],
                        sin_factura=sin_factura)

    facturas = {f.id: (f.notas, ff) for _, _, f, _, ff in aplicaciones}
    entrega_de = _entregas(db, facturas)
    nombres = dict(
        db.query(Cliente.id, Cliente.legal_name)
        .filter(Cliente.id.in_({f.cliente_id for _, _, f, _, _ in aplicaciones}))
        .all()
    )
    lineas_de: dict = {}
    for ln, sku, nombre in (
        db.query(LineaFactura, Producto.sku, Producto.nombre)
        .outerjoin(Producto, Producto.id == LineaFactura.producto_id)
        .filter(LineaFactura.factura_id.in_(list(facturas)))
        .order_by(LineaFactura.factura_id, LineaFactura.numero_linea)
        .all()
    ):
        lineas_de.setdefault(ln.factura_id, []).append((ln, sku, nombre))

    salida: list[MCLineaNotaOut] = []
    for nota, app_, f, fecha_nota, _ in aplicaciones:
        partes = [
            (ln, sku, nombre, Decimal(ln.importe or 0) - Decimal(ln.descuento or 0))
            for ln, sku, nombre in lineas_de.get(f.id, [])
        ]
        base = sum((p[3] for p in partes if p[3] > 0), Decimal(0))
        partes = [p for p in partes if p[3] > 0] if base > 0 else partes[:1]
        if not partes:
            continue
        total = Decimal(app_.importe or 0).quantize(_CENTAVO)
        entrega, origen = entrega_de[f.id]
        repartido = Decimal(0)
        for i, (ln, sku, nombre, imp) in enumerate(partes):
            if i == len(partes) - 1:
                parte = total - repartido           # el último se lleva el redondeo
            else:
                parte = (total * imp / base).quantize(_CENTAVO) if base > 0 else total
            repartido += parte
            salida.append(MCLineaNotaOut(
                linea_id=_uuid_de(app_.id, ln.id),
                nota_id=nota.id,
                nota=f"{nota.serie or ''} {nota.folio or ''}".strip(),
                uuid_cfdi=nota.uuid,
                fecha_nota=fecha_nota,
                factura_id=f.id,
                serie=f.serie,
                folio=f.folio,
                fecha_entrega=entrega,
                fecha_entrega_origen=origen,
                cliente_id=f.cliente_id,
                cliente=nombres.get(f.cliente_id),
                sku=sku,
                producto=nombre,
                descripcion=ln.descripcion,
                clave_unidad=ln.clave_unidad,
                presentacion=ln.presentacion,
                cantidad="0",
                importe=str(-parte),
            ))
    return MCNotasOut(series=series_leer, desde=desde, hasta=hasta, lineas=salida,
                    sin_factura=sin_factura)


# ─── Fase 3: cobranza, saldos y precios ──────────────────────────────────────


class MCCobroOut(BaseModel):
    id: UUID                    # la aplicación del pago a UNA factura
    recibo_id: UUID
    recibo: str
    fecha_pago: date
    forma_pago: Optional[str] = None
    cliente_id: Optional[UUID] = None
    cliente: Optional[str] = None
    factura_id: UUID
    serie: str
    folio: int
    importe: str
    saldo_insoluto: Optional[str] = None


class MCCobranzaOut(BaseModel):
    series: list[str]
    desde: date
    hasta: date
    cobros: list[MCCobroOut]


@router.get("/cobranza", response_model=MCCobranzaOut)
def cobranza(
    desde: date = Query(...),
    hasta: date = Query(...),
    series: Optional[str] = Query(None, description="Códigos de FACTURA separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Lo cobrado: cada pago (REP timbrado) aplicado a una factura de las series
    pedidas, con fecha de pago en el rango (hora de México)."""
    a = alcance_de(db, ctx)
    _requiere(a.cobranza, "la cobranza")
    _rango(desde, hasta)
    series_leer, _ = _resolver_series(db, ctx, a, series, None)
    clientes_leer = _resolver_clientes(a, clientes)

    fecha_mx = sa.cast(sa.func.timezone(_ZONA, ReciboPago.fecha_pago), sa.Date)
    q = (
        db.query(ReciboPagoFactura, ReciboPago, Factura, fecha_mx.label("fecha"),
                 Cliente.legal_name.label("cliente"))
        .join(ReciboPago, ReciboPago.id == ReciboPagoFactura.recibo_id)
        .join(Factura, Factura.id == ReciboPagoFactura.factura_id)
        .outerjoin(Cliente, Cliente.id == Factura.cliente_id)
        .filter(
            ReciboPago.tenant_id == ctx.tenant_id,
            ReciboPago.estado == "TIMBRADO",
            Factura.serie.in_(series_leer),
            fecha_mx >= desde,
            fecha_mx <= hasta,
        )
    )
    if clientes_leer is not None:
        q = q.filter(Factura.cliente_id.in_(clientes_leer))
    cobros = [
        MCCobroOut(
            id=rpf.id, recibo_id=rp.id,
            recibo=f"{rp.serie or ''} {rp.folio or ''}".strip() or (rp.uuid or "")[:8],
            fecha_pago=fecha, forma_pago=rp.forma_pago,
            cliente_id=f.cliente_id, cliente=cliente,
            factura_id=f.id, serie=f.serie, folio=f.folio,
            importe=str(Decimal(rpf.importe_pagado or 0).quantize(_CENTAVO)),
            saldo_insoluto=None if rpf.saldo_insoluto is None
            else str(Decimal(rpf.saldo_insoluto).quantize(_CENTAVO)),
        )
        for rpf, rp, f, fecha, cliente in q.order_by(fecha_mx, Factura.serie, Factura.folio).all()
    ]
    return MCCobranzaOut(series=series_leer, desde=desde, hasta=hasta, cobros=cobros)


class MCSaldoOut(BaseModel):
    factura_id: UUID
    serie: str
    folio: int
    uuid_cfdi: Optional[str] = None
    fecha: date
    vencimiento: date
    dias_vencida: int           # negativo = faltan días para vencer
    cliente_id: UUID
    cliente: Optional[str] = None
    total: str
    saldo: str


class MCCarteraOut(BaseModel):
    series: list[str]
    corte: date
    facturas: list[MCSaldoOut]


@router.get("/cartera", response_model=MCCarteraOut)
def cartera(
    series: Optional[str] = Query(None, description="Códigos de FACTURA separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """Lo que falta por cobrar HOY: facturas timbradas PPD de las series pedidas
    con saldo, con el mismo filtro que el estado de cuenta (fuera las que ya van
    en cancelación). Vence a los días de crédito del cliente."""
    from .cobranza import _en_cancelacion

    a = alcance_de(db, ctx)
    _requiere(a.cobranza, "la cobranza")
    series_leer, _ = _resolver_series(db, ctx, a, series, None)
    clientes_leer = _resolver_clientes(a, clientes)
    hoy = datetime.now(ZoneInfo(_ZONA)).date()

    fecha_mx = sa.cast(sa.func.timezone(_ZONA, Factura.fecha), sa.Date)
    q = (
        db.query(Factura, fecha_mx.label("fecha"), Cliente.legal_name, Cliente.dias_credito)
        .join(Cliente, Cliente.id == Factura.cliente_id)
        .filter(
            Factura.tenant_id == ctx.tenant_id,
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            Factura.metodo_pago == "PPD",
            Factura.saldo_insoluto > 0,
            Factura.serie.in_(series_leer),
        )
    )
    if clientes_leer is not None:
        q = q.filter(Factura.cliente_id.in_(clientes_leer))
    salida = []
    for f, fecha, cliente, dias in q.order_by(fecha_mx, Factura.serie, Factura.folio).all():
        if _en_cancelacion(f.cancelacion_msj):
            continue
        vence = fecha + timedelta(days=int(dias or 0))
        salida.append(MCSaldoOut(
            factura_id=f.id, serie=f.serie, folio=f.folio, uuid_cfdi=f.uuid,
            fecha=fecha, vencimiento=vence, dias_vencida=(hoy - vence).days,
            cliente_id=f.cliente_id, cliente=cliente,
            total=str(Decimal(f.total or 0).quantize(_CENTAVO)),
            saldo=str(Decimal(f.saldo_insoluto or 0).quantize(_CENTAVO)),
        ))
    return MCCarteraOut(series=series_leer, corte=hoy, facturas=salida)


class MCPrecioVentaOut(BaseModel):
    sku: str
    producto: str
    presentacion: str
    cliente_id: UUID
    cliente: Optional[str] = None
    plaza: Optional[str] = None
    precio: str                 # sin IVA, por `presentacion`
    origen: str                 # override_sucursal, lista asignada, lista_base…


class MCPreciosOut(BaseModel):
    series: list[str]
    fecha: date
    precios: list[MCPrecioVentaOut]


@router.get("/precios", response_model=MCPreciosOut)
def precios(
    series: Optional[str] = Query(None, description="Códigos de FACTURA separados por coma"),
    clientes: Optional[str] = Query(None, description="Ids separados por coma"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_LEER)),
):
    """El precio de lista que HOY le toca a cada cliente de esas series, para los
    productos que se le facturaron en el último año.

    Sale de `resolver_precios_lote`, la MISMA cascada que cotiza y factura
    (override → asignación → lista base), una vez por cada cliente×plaza cuya
    serie de factura prevista es de las pedidas; con la serie de REMISIÓN que le
    toca, porque las asignaciones por serie son por serie de remisión."""
    from ...services.precios import resolver_precios_lote
    from ...services.series import resolver_serie

    a = alcance_de(db, ctx)
    _requiere(a.precios, "los precios")
    series_leer, _ = _resolver_series(db, ctx, a, series, None)
    clientes_leer = _resolver_clientes(a, clientes)
    hoy = datetime.now(ZoneInfo(_ZONA)).date()

    vivas = sa.and_(Sucursal.deleted_at.is_(None), Sucursal.activo.is_(True))
    pares_q = (
        db.query(ClienteSucursal.cliente_id, ClienteSucursal.sucursal_id, Sucursal.nombre)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .filter(ClienteSucursal.tenant_id == ctx.tenant_id, vivas)
    )
    if clientes_leer is not None:
        pares_q = pares_q.filter(ClienteSucursal.cliente_id.in_(clientes_leer))
    pares = {(c, s): n for c, s, n in pares_q.all()}
    prevista = series_previstas(db, ctx.tenant_id, pares)
    pares = {k: v for k, v in pares.items() if prevista.get(k) in set(series_leer)}
    if not pares:
        return MCPreciosOut(series=series_leer, fecha=hoy, precios=[])

    fecha_mx = sa.cast(sa.func.timezone(_ZONA, Factura.fecha), sa.Date)
    salida: list[MCPrecioVentaOut] = []
    nombres = dict(
        db.query(Cliente.id, Cliente.legal_name)
        .filter(Cliente.id.in_({c for c, _ in pares}))
        .all()
    )
    for (cli, suc), plaza in sorted(pares.items(), key=lambda x: (str(x[0][0]), x[1] or "")):
        prods = (
            db.query(Producto)
            .join(LineaFactura, LineaFactura.producto_id == Producto.id)
            .join(Factura, Factura.id == LineaFactura.factura_id)
            .filter(
                Factura.tenant_id == ctx.tenant_id,
                Factura.estado == "TIMBRADA",
                Factura.deleted_at.is_(None),
                Factura.cliente_id == cli,
                Factura.serie == prevista[(cli, suc)],
                fecha_mx >= hoy - timedelta(days=365),
                Producto.deleted_at.is_(None),
            )
            .distinct()
            .all()
        )
        if not prods:
            continue
        serie_rem = resolver_serie(db, ctx.tenant_id, "REMISION", sucursal_id=suc, cliente_id=cli)
        items = [
            {"producto_id": p.id, "presentacion": p.presentacion_default or p.unidad_base,
             "cantidad": Decimal(1)}
            for p in prods
        ]
        resueltos = resolver_precios_lote(
            db, items=items, cliente_id=cli, sucursal_id=suc,
            serie_id=serie_rem.id if serie_rem else None, fecha=hoy)
        for p, it, r in zip(prods, items, resueltos):
            if not r or r.get("precio") is None:
                continue
            salida.append(MCPrecioVentaOut(
                sku=p.sku, producto=p.nombre, presentacion=it["presentacion"],
                cliente_id=cli, cliente=nombres.get(cli), plaza=plaza,
                precio=str(Decimal(r["precio"]).quantize(Decimal("0.0001"))),
                origen=str(r.get("origen") or ""),
            ))
    return MCPreciosOut(series=series_leer, fecha=hoy, precios=salida)

"""Reportes de dirección: ventas y cobranza.

El tablero del dueño. Dos preguntas, que son las dos que importan:

  · ¿Cuánto se está facturando? (día, semana, mes, y contra el periodo anterior)
  · ¿Cuánto nos deben y desde hace cuánto? (por proyecto, cliente o plaza)

Todo sale de las mismas facturas que alimentan el estado de cuenta —espejo de
SAE incluido—, así que un número de aquí siempre se puede abrir y cuadrar en el
detalle. Nada se calcula dos veces: la clasificación por proyecto y el criterio
de "en cancelación" se importan de cobranza.py, que es donde nacieron.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import (
    Cliente, ClienteSucursal, ClienteSucursalSerie, Factura, NotaCredito, NotaCreditoFactura,
    ReciboPago, ReciboPagoFactura, Serie, Sucursal,
)
from .cobranza import _en_cancelacion, _fila_de_reporte, _recibo_out

router = APIRouter(prefix="/reportes", tags=["reportes"])

_READ = "menu:facturas"      # ver reportes de ventas y cobranza = ver facturas
ZERO = Decimal("0")

# Antigüedad en MESES, que es como el dueño la pide y la lee ("me deben dos
# meses"). Equivale a las cubetas de 30 días del estado de cuenta, solo que
# nombradas por mes: el número de una vista cuadra con el de la otra.
_CUBETAS = ("por_vencer", "mes_1", "mes_2", "mes_3", "mes_4_mas")


def _cubeta(dias_vencida: int) -> str:
    if dias_vencida <= 0:
        return "por_vencer"
    if dias_vencida <= 30:
        return "mes_1"
    if dias_vencida <= 60:
        return "mes_2"
    if dias_vencida <= 90:
        return "mes_3"
    return "mes_4_mas"


def _serie_a_plaza(db: Session, tenant_id) -> dict[str, str]:
    """{código de serie: nombre de la plaza}, leído de los vínculos cliente×plaza.

    La serie ES la plaza dentro de un cliente (EHMO factura Pachuca con
    ZEHMOHOS y Tabasco con ZEHMOVH), así que el mapa sale de donde ese vínculo
    ya vive —`cliente_sucursales.serie_factura_id` y su abanico— y no de una
    lista escrita a mano que envejecería con la primera plaza nueva.
    """
    mapa: dict[str, str] = {}
    principal = (
        db.query(Serie.codigo, Sucursal.nombre)
        .join(ClienteSucursal, ClienteSucursal.serie_factura_id == Serie.id)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .filter(ClienteSucursal.tenant_id == tenant_id)
    )
    abanico = (
        db.query(Serie.codigo, Sucursal.nombre)
        .join(ClienteSucursalSerie, ClienteSucursalSerie.serie_id == Serie.id)
        .join(ClienteSucursal, ClienteSucursal.id == ClienteSucursalSerie.cliente_sucursal_id)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .filter(ClienteSucursalSerie.tenant_id == tenant_id)
    )
    for codigo, plaza in list(principal) + list(abanico):
        mapa.setdefault(codigo, plaza)
    return mapa


def _plaza_unica(db: Session, tenant_id) -> dict:
    """{cliente_id: plaza} de los clientes que se surten de UNA sola.

    Es el rescate cuando la serie no está mapeada: si el cliente solo tiene una
    plaza, la factura es de ahí sin lugar a duda. Con varias no se adivina.
    """
    filas = (
        db.query(ClienteSucursal.cliente_id, Sucursal.nombre)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .filter(ClienteSucursal.tenant_id == tenant_id)
        .all()
    )
    cuenta: dict = {}
    for cliente_id, plaza in filas:
        cuenta.setdefault(cliente_id, set()).add(plaza)
    return {c: next(iter(p)) for c, p in cuenta.items() if len(p) == 1}


Agrupar = Literal["proyecto", "cliente", "sucursal"]


def _etiquetador(db: Session, tenant_id, agrupar: str):
    """La fila a la que va cada factura según la dimensión pedida.

    Cartera y sumario de ventas reparten por el mismo criterio —proyecto por
    serie, cliente por razón social, plaza por serie o por plaza única—, así
    que una factura cae en la misma fila en las dos vistas.
    """
    if agrupar == "cliente":
        return lambda f, nombre_cliente, cliente_id: nombre_cliente
    if agrupar == "sucursal":
        serie_plaza = _serie_a_plaza(db, tenant_id)
        plaza_unica = _plaza_unica(db, tenant_id)
        return lambda f, nombre_cliente, cliente_id: (
            serie_plaza.get(f.serie or "") or plaza_unica.get(cliente_id) or "Sin plaza"
        )
    return lambda f, nombre_cliente, cliente_id: _fila_de_reporte(f, nombre_cliente)


@router.get("/cartera")
def cartera(
    agrupar: Agrupar = Query(default="proyecto"),
    incluir_en_cancelacion: bool = Query(default=False),
    desde: date | None = Query(default=None, description="Solo facturas emitidas desde esta fecha"),
    hasta: date | None = Query(default=None, description="Solo facturas emitidas hasta esta fecha"),
    cliente_id: UUID | None = Query(default=None, description="Acota el reporte a un cliente"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cuentas por cobrar: una fila por proyecto, cliente o plaza, más la
    antigüedad global por meses.

    Vencido y cubetas usan la fecha de vencimiento (fecha + días de crédito del
    cliente), el mismo criterio del estado de cuenta.

    `desde`/`hasta` acotan por FECHA DE EMISIÓN de la factura, que es lo que
    hace que los filtros globales del tablero muevan también la cartera. Ojo
    con leerlo: el saldo y el vencido siguen siendo los de HOY —solo se mira un
    subconjunto de facturas—, así que un rango corto no es "lo que me debían
    entonces" sino "lo que me deben de lo que facturé en ese tramo".
    """
    hoy = datetime.now(timezone.utc).date()
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    etiqueta_de = _etiquetador(db, ctx.tenant_id, agrupar)

    filas: dict[str, dict] = {}
    antiguedad = {c: ZERO for c in _CUBETAS}
    en_cancelacion = ZERO
    total = vencido_total = ZERO

    q = (
        db.query(Factura, Cliente.legal_name, Cliente.dias_credito, Cliente.id)
        .join(Cliente, Cliente.id == Factura.cliente_id)
        .filter(
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            Factura.metodo_pago == "PPD",
            Factura.saldo_insoluto > 0,
        )
    )
    if desde is not None:
        q = q.filter(sa.cast(Factura.fecha, sa.Date) >= desde)
    if hasta is not None:
        q = q.filter(sa.cast(Factura.fecha, sa.Date) <= hasta)
    if cliente_id is not None:
        q = q.filter(Factura.cliente_id == cliente_id)
    for f, nombre_cliente, dias_credito, fila_cliente_id in q.all():
        if not ctx.cliente_permitido(fila_cliente_id):
            continue
        saldo = Decimal(f.saldo_insoluto)
        if _en_cancelacion(f.cancelacion_msj):
            en_cancelacion += saldo
            if not incluir_en_cancelacion:
                continue
        etiqueta = etiqueta_de(f, nombre_cliente, fila_cliente_id)

        fila = filas.setdefault(etiqueta, {
            "saldo": ZERO, "vencido": ZERO, "facturas": 0,
            "cliente_id": str(fila_cliente_id), "serie": f.serie,
            # El reparto por cubeta de CADA fila: deja que la pantalla filtre
            # por antigüedad sin volver a pedir el reporte.
            "antiguedad": {c: ZERO for c in _CUBETAS},
            "facturas_por_cubeta": {c: 0 for c in _CUBETAS},
        })
        fila["saldo"] += saldo
        fila["facturas"] += 1
        f_fecha = f.fecha.date() if isinstance(f.fecha, datetime) else f.fecha
        dias_vencida = (hoy - (f_fecha + timedelta(days=int(dias_credito or 0)))).days
        antiguedad[_cubeta(dias_vencida)] += saldo
        fila["antiguedad"][_cubeta(dias_vencida)] += saldo
        fila["facturas_por_cubeta"][_cubeta(dias_vencida)] += 1
        total += saldo
        if dias_vencida > 0:
            fila["vencido"] += saldo
            vencido_total += saldo
        # Una fila que mezcla clientes o series no puede enlazar a un estado de
        # cuenta acotado: el enlace se deja en blanco antes que llevar a otro lado.
        if fila["serie"] != f.serie:
            fila["serie"] = None
        if fila["cliente_id"] != str(fila_cliente_id):
            fila["cliente_id"] = None

    return {
        "corte": hoy,
        "agrupar": agrupar,
        "desde": desde,
        "hasta": hasta,
        "cliente_id": cliente_id,
        "filas": [
            {"etiqueta": nombre, **datos}
            for nombre, datos in sorted(filas.items(), key=lambda kv: kv[1]["saldo"], reverse=True)
        ],
        "saldo_total": total,
        "vencido_total": vencido_total,
        "antiguedad": antiguedad,
        "incluye_en_cancelacion": incluir_en_cancelacion,
        "saldo_en_cancelacion": en_cancelacion,
    }


# ── Ventas ───────────────────────────────────────────────────────────────────
#
# Una sola serie de tiempo: el rango manda y la granularidad es el paso con que
# se recorre. Antes eran dos gráficas fijas —30 días y 6 meses— que no se podían
# mover; "el mes pasado", "los últimos 12 meses" o "esa semana de agosto" son
# ahora el mismo endpoint con otro rango, y así los filtros del tablero mueven
# todo a la vez en lugar de cada gráfica por su cuenta.

_MAX_DIAS = 1827          # 5 años; tope para que un rango absurdo no arme mil cubetas
_DIAS_DEFAULT = 30
Granularidad = Literal["auto", "dia", "semana", "mes"]


def _fin_de_mes(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _sumar_meses(d: date, n: int) -> date:
    """El mismo día n meses adelante o atrás, recortado al último del mes cuando
    ese día no existe (31 de marzo − 1 mes = 28 o 29 de febrero)."""
    indice = d.year * 12 + (d.month - 1) + n
    primero = date(indice // 12, indice % 12 + 1, 1)
    return primero.replace(day=min(d.day, _fin_de_mes(primero).day))


def _inicio_de_cubeta(d: date, granularidad: str) -> date:
    if granularidad == "dia":
        return d
    if granularidad == "semana":
        return d - timedelta(days=d.weekday())   # lunes, como el resto del app
    return d.replace(day=1)


def _cubeta_siguiente(d: date, granularidad: str) -> date:
    if granularidad == "dia":
        return d + timedelta(days=1)
    if granularidad == "semana":
        return d + timedelta(days=7)
    return _sumar_meses(d, 1)


def _resolver_granularidad(desde: date, hasta: date, pedida: Granularidad) -> str:
    """El paso que hace legible la gráfica cuando nadie lo eligió.

    Los cortes son los que dejan la barra visible: por arriba de mes y medio,
    una barra por día ya no se puede ni tocar con el dedo; por arriba de medio
    año, ni las semanas caben.
    """
    if pedida != "auto":
        return pedida
    dias = (hasta - desde).days + 1
    if dias <= 45:
        return "dia"
    if dias <= 180:
        return "semana"
    return "mes"


def _rango_pedido(desde: date | None, hasta: date | None, hoy: date) -> tuple[date, date]:
    """Normaliza el rango: por omisión, los últimos 30 días hasta hoy."""
    if hasta is None:
        hasta = hoy
    if desde is None:
        desde = hasta - timedelta(days=_DIAS_DEFAULT - 1)
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days + 1 > _MAX_DIAS:
        raise HTTPException(status_code=400, detail="El rango no puede pasar de 5 años")
    return desde, hasta


def _rango_anterior(desde: date, hasta: date) -> tuple[date, date]:
    """El tramo con el que se compara: el equivalente inmediatamente anterior.

    "Equivalente" no siempre es "los N días de antes". Un periodo a medias se
    compara contra el MISMO tramo del periodo pasado —del 1 al 22 de septiembre
    contra el 1 al 22 de agosto—, porque medirlo contra los 22 días corridos
    previos partiría agosto por la mitad y el "vs mes pasado" dejaría de serlo.
    Y un periodo completo retrocede por periodos enteros, no por días: febrero
    contra enero, no contra los 28 días anteriores.
    """
    dias = (hasta - desde).days + 1
    # Meses completos (uno o varios): se retrocede el mismo número de meses.
    if desde.day == 1 and hasta == _fin_de_mes(hasta):
        meses = (hasta.year - desde.year) * 12 + (hasta.month - desde.month) + 1
        return _sumar_meses(desde, -meses), _fin_de_mes(_sumar_meses(desde, -1))
    # Mes empezado: el mismo tramo del mes pasado, recortado si aquel fue más corto.
    if desde.day == 1:
        previo = _sumar_meses(desde, -1)
        return previo, min(previo + timedelta(days=dias - 1), _fin_de_mes(previo))
    # Semana empezada: el mismo tramo de la semana pasada.
    if desde.weekday() == 0 and dias <= 7:
        return desde - timedelta(days=7), hasta - timedelta(days=7)
    # Cualquier otro rango: la ventana inmediata anterior, del mismo tamaño.
    return desde - timedelta(days=dias), desde - timedelta(days=1)


def _acotar(q, ctx: AuthContext, cliente_id: UUID | None):
    """El candado del portal y el filtro global de cliente, en ese orden: el
    filtro elige dentro de lo permitido, nunca lo ensancha."""
    if ctx.cliente_scope:
        q = q.filter(Factura.cliente_id.in_(ctx.cliente_scope))
    if cliente_id is not None:
        q = q.filter(Factura.cliente_id == cliente_id)
    return q


def _timbradas_por_dia(
    db: Session, ctx: AuthContext, desde: date, hasta: date, cliente_id: UUID | None,
) -> dict[date, tuple[Decimal, int]]:
    """{día: (facturado, facturas)} del rango. Solo los días CON facturas: el
    relleno con ceros lo hace quien arma las cubetas."""
    q = (
        db.query(
            sa.cast(Factura.fecha, sa.Date).label("dia"),
            sa.func.sum(Factura.total).label("total"),
            sa.func.count().label("facturas"),
        )
        .filter(
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            sa.cast(Factura.fecha, sa.Date) >= desde,
            sa.cast(Factura.fecha, sa.Date) <= hasta,
        )
        .group_by(sa.text("dia"))
    )
    return {r.dia: (Decimal(r.total or 0), r.facturas) for r in _acotar(q, ctx, cliente_id).all()}


def _facturado(
    db: Session, ctx: AuthContext, desde: date, hasta: date, cliente_id: UUID | None = None,
) -> tuple[Decimal, int]:
    """(facturado, facturas) de un rango, ambos extremos incluidos."""
    q = (
        db.query(
            sa.func.coalesce(sa.func.sum(Factura.total), 0),
            sa.func.count(),
        )
        .filter(
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            sa.cast(Factura.fecha, sa.Date) >= desde,
            sa.cast(Factura.fecha, sa.Date) <= hasta,
        )
    )
    total, cuantas = _acotar(q, ctx, cliente_id).one()
    return Decimal(total or 0), int(cuantas or 0)


def _variacion(actual: Decimal, previo: Decimal) -> float | None:
    """Porcentaje de cambio. Sin base previa no hay porcentaje que dar: None, y
    que la pantalla diga "sin comparativo" en vez de un 100% falso."""
    if previo == 0:
        return None
    return float(round((actual - previo) / previo * 100, 1))


@router.get("/ventas")
def ventas(
    desde: date | None = Query(default=None, description="Inicio del rango (por omisión, hace 30 días)"),
    hasta: date | None = Query(default=None, description="Fin del rango (por omisión, hoy)"),
    granularidad: Granularidad = Query(default="auto", description="Paso de la serie"),
    cliente_id: UUID | None = Query(default=None, description="Acota el reporte a un cliente"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Facturación del rango: la serie de tiempo y el corte contra el tramo
    anterior del mismo tamaño.

    Las cubetas de los extremos se RECORTAN al rango (si el rango empieza un
    miércoles, esa primera semana son tres días): la barra vale lo que se
    facturó dentro del filtro y no lo que se facturó el lunes anterior, que
    quedó fuera de lo que el usuario pidió ver.
    """
    hoy = datetime.now(timezone.utc).date()
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    desde, hasta = _rango_pedido(desde, hasta, hoy)
    paso = _resolver_granularidad(desde, hasta, granularidad)

    por_dia = _timbradas_por_dia(db, ctx, desde, hasta, cliente_id)

    serie: list[dict] = []
    cursor = _inicio_de_cubeta(desde, paso)
    while cursor <= hasta:
        fin_cubeta = min(_cubeta_siguiente(cursor, paso) - timedelta(days=1), hasta)
        inicio = max(cursor, desde)
        total = ZERO
        facturas = 0
        dia = inicio
        while dia <= fin_cubeta:
            monto, cuantas = por_dia.get(dia, (ZERO, 0))
            total += monto
            facturas += cuantas
            dia += timedelta(days=1)
        serie.append({"inicio": inicio, "fin": fin_cubeta, "total": total, "facturas": facturas})
        cursor = _cubeta_siguiente(cursor, paso)

    total_rango = sum((c["total"] for c in serie), ZERO)
    facturas_rango = sum(c["facturas"] for c in serie)
    dias = (hasta - desde).days + 1

    previo_desde, previo_hasta = _rango_anterior(desde, hasta)
    total_previo, facturas_previo = _facturado(db, ctx, previo_desde, previo_hasta, cliente_id)

    mejor = max(serie, key=lambda c: c["total"], default=None)
    hoy_total = por_dia.get(hoy, (ZERO, 0))[0] if desde <= hoy <= hasta else None

    return {
        "hoy": hoy,
        "desde": desde,
        "hasta": hasta,
        "dias": dias,
        "granularidad": paso,
        "cliente_id": cliente_id,
        "serie": serie,
        "total": total_rango,
        "facturas": facturas_rango,
        # El ticket promedio con cero facturas es cero, no una división rota.
        "ticket_promedio": (total_rango / facturas_rango) if facturas_rango else ZERO,
        "promedio_dia": total_rango / dias,
        "promedio_cubeta": (total_rango / len(serie)) if serie else ZERO,
        "mejor": mejor if mejor and mejor["total"] > 0 else None,
        "hoy_total": hoy_total,
        "anterior": {
            "desde": previo_desde,
            "hasta": previo_hasta,
            "total": total_previo,
            "facturas": facturas_previo,
            "variacion": _variacion(total_rango, total_previo),
        },
    }


@router.get("/ventas/sumario")
def ventas_sumario(
    agrupar: Agrupar = Query(default="cliente"),
    desde: date | None = Query(default=None, description="Inicio del rango (por omisión, hace 30 días)"),
    hasta: date | None = Query(default=None, description="Fin del rango (por omisión, hoy)"),
    cliente_id: UUID | None = Query(default=None, description="Acota el reporte a un cliente"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Sumario de venta: lo facturado en el rango, repartido por cliente, plaza
    o proyecto.

    Mismo universo que `/ventas` —timbradas del rango, con el candado del
    portal y el filtro de cliente—, así que el total de aquí ES el «Facturado»
    del tablero y la suma de las barras. Las facturas con la cancelación ya
    pedida al SAT siguen contando (la gráfica también las cuenta); se informan
    aparte para que se sepa cuánto de la venta está en riesgo de caerse.
    """
    hoy = datetime.now(timezone.utc).date()
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    desde, hasta = _rango_pedido(desde, hasta, hoy)
    etiqueta_de = _etiquetador(db, ctx.tenant_id, agrupar)

    q = (
        db.query(Factura, Cliente.legal_name, Cliente.id)
        .join(Cliente, Cliente.id == Factura.cliente_id)
        .filter(
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            sa.cast(Factura.fecha, sa.Date) >= desde,
            sa.cast(Factura.fecha, sa.Date) <= hasta,
        )
    )
    filas: dict[str, dict] = {}
    total = en_cancelacion = ZERO
    facturas = 0
    for f, nombre_cliente, fila_cliente_id in _acotar(q, ctx, cliente_id).all():
        monto = Decimal(f.total or 0)
        etiqueta = etiqueta_de(f, nombre_cliente, fila_cliente_id)
        fila = filas.setdefault(etiqueta, {
            "total": ZERO, "facturas": 0,
            "cliente_id": str(fila_cliente_id), "serie": f.serie,
        })
        fila["total"] += monto
        fila["facturas"] += 1
        total += monto
        facturas += 1
        if _en_cancelacion(f.cancelacion_msj):
            en_cancelacion += monto
        if fila["serie"] != f.serie:
            fila["serie"] = None
        if fila["cliente_id"] != str(fila_cliente_id):
            fila["cliente_id"] = None

    return {
        "desde": desde,
        "hasta": hasta,
        "agrupar": agrupar,
        "cliente_id": cliente_id,
        "filas": [
            {"etiqueta": nombre, **datos}
            for nombre, datos in sorted(filas.items(), key=lambda kv: kv[1]["total"], reverse=True)
        ],
        "total": total,
        "facturas": facturas,
        "total_en_cancelacion": en_cancelacion,
    }


# ── Comprobantes de pago ─────────────────────────────────────────────────────
#
# Los REP (CFDI tipo P), los que timbra el Facturador y los que el espejo trae
# de SAE, por FECHA DE PAGO: es la fecha que el SAT lee en el complemento y la
# que cuadra con el banco. Los borradores no son comprobantes —nunca llegaron
# al SAT— y se quedan fuera.


@router.get("/pagos")
def pagos(
    desde: date | None = Query(default=None, description="Inicio del rango (por omisión, hace 30 días)"),
    hasta: date | None = Query(default=None, description="Fin del rango (por omisión, hoy)"),
    cliente_id: UUID | None = Query(default=None, description="Acota el reporte a un cliente"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Comprobantes de pago timbrados o cancelados del rango, con sus facturas
    relacionadas. El total es lo VIGENTE; lo cancelado se informa aparte."""
    hoy = datetime.now(timezone.utc).date()
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    desde, hasta = _rango_pedido(desde, hasta, hoy)

    q = (
        db.query(ReciboPago, Cliente.legal_name)
        .join(Cliente, Cliente.id == ReciboPago.cliente_id)
        .filter(
            ReciboPago.tenant_id == ctx.tenant_id,
            ReciboPago.estado.in_(("TIMBRADO", "CANCELADO")),
            sa.cast(ReciboPago.fecha_pago, sa.Date) >= desde,
            sa.cast(ReciboPago.fecha_pago, sa.Date) <= hasta,
        )
    )
    if ctx.cliente_scope:
        q = q.filter(ReciboPago.cliente_id.in_(ctx.cliente_scope))
    if cliente_id is not None:
        q = q.filter(ReciboPago.cliente_id == cliente_id)
    filas = q.order_by(ReciboPago.fecha_pago.desc(), ReciboPago.folio.desc()).all()

    # Mismo armado que el listado de cobranza, precargado en dos consultas.
    ids = [r.id for r, _ in filas]
    detalle = (
        db.query(ReciboPagoFactura).filter(ReciboPagoFactura.recibo_id.in_(ids)).all()
        if ids else []
    )
    por_recibo: dict = {}
    for d in detalle:
        por_recibo.setdefault(d.recibo_id, []).append(d)
    folios = {
        f.id: f for f in db.query(Factura.id, Factura.serie, Factura.folio).filter(
            Factura.id.in_({d.factura_id for d in detalle if d.factura_id} or [None])
        ).all()
    } if detalle else {}

    # El desglose fiscal de cada factura abonada, para el detalle que se
    # despliega en la fila del comprobante (una sola consulta).
    fiscal = {
        f.id: f for f in db.query(
            Factura.id, Factura.fecha, Factura.subtotal, Factura.descuento,
            Factura.ieps_trasladado, Factura.iva_trasladado, Factura.total,
        ).filter(Factura.id.in_({d.factura_id for d in detalle if d.factura_id})).all()
    } if any(d.factura_id for d in detalle) else {}

    total = cancelado = ZERO
    vigentes = cancelados = 0
    items = []
    for r, nombre_cliente in filas:
        if r.estado == "TIMBRADO":
            total += Decimal(r.monto)
            vigentes += 1
        else:
            cancelado += Decimal(r.monto)
            cancelados += 1
        out = _recibo_out(db, r, filas_pre=por_recibo.get(r.id, []), folios_pre=folios)
        for fr in out["facturas"]:
            f = fiscal.get(UUID(fr["factura_id"])) if fr["factura_id"] else None
            fr.update({
                "fecha": f.fecha if f else None,
                "subtotal": f.subtotal if f else None,
                "descuento": f.descuento if f else None,
                "ieps": f.ieps_trasladado if f else None,
                "iva": f.iva_trasladado if f else None,
                "total": f.total if f else None,
            })
        items.append({**out, "cliente": nombre_cliente})

    return {
        "desde": desde,
        "hasta": hasta,
        "cliente_id": cliente_id,
        "items": items,
        "total": total,
        "comprobantes": vigentes,
        "total_cancelado": cancelado,
        "cancelados": cancelados,
    }


# ── Notas de crédito ─────────────────────────────────────────────────────────
#
# Los CFDI de egreso que SAE timbra y aplica en la CxC (concepto 1002). Llegan
# por el espejo (migración 0086) y restan del saldo de sus facturas allá, así
# que aquí sólo se listan: el saldo de la cartera ya las trae descontadas.


@router.get("/notas-credito")
def notas_credito(
    desde: date | None = Query(default=None, description="Inicio del rango (por omisión, hace 30 días)"),
    hasta: date | None = Query(default=None, description="Fin del rango (por omisión, hoy)"),
    cliente_id: UUID | None = Query(default=None, description="Acota el reporte a un cliente"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Notas de crédito del rango con las facturas a las que se aplicaron. El
    total es lo VIGENTE; lo cancelado se informa aparte."""
    hoy = datetime.now(timezone.utc).date()
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    desde, hasta = _rango_pedido(desde, hasta, hoy)

    q = (
        db.query(NotaCredito, Cliente.legal_name)
        .join(Cliente, Cliente.id == NotaCredito.cliente_id)
        .filter(
            NotaCredito.tenant_id == ctx.tenant_id,
            sa.cast(NotaCredito.fecha, sa.Date) >= desde,
            sa.cast(NotaCredito.fecha, sa.Date) <= hasta,
        )
    )
    if ctx.cliente_scope:
        q = q.filter(NotaCredito.cliente_id.in_(ctx.cliente_scope))
    if cliente_id is not None:
        q = q.filter(NotaCredito.cliente_id == cliente_id)
    filas = q.order_by(NotaCredito.fecha.desc(), NotaCredito.folio.desc()).all()

    ids = [n.id for n, _ in filas]
    detalle = (
        db.query(NotaCreditoFactura).filter(NotaCreditoFactura.nota_id.in_(ids)).all()
        if ids else []
    )
    por_nota: dict = {}
    for d in detalle:
        por_nota.setdefault(d.nota_id, []).append(d)
    folios = {
        f.id: f for f in db.query(Factura.id, Factura.serie, Factura.folio).filter(
            Factura.id.in_({d.factura_id for d in detalle if d.factura_id} or [None])
        ).all()
    } if detalle else {}

    total = cancelado = ZERO
    vigentes = canceladas = 0
    items = []
    for n, nombre_cliente in filas:
        if n.estado == "CANCELADA":
            cancelado += Decimal(n.total)
            canceladas += 1
        else:
            total += Decimal(n.total)
            vigentes += 1
        items.append({
            "id": str(n.id), "serie": n.serie, "folio": n.folio,
            "fecha": n.fecha, "cliente_id": str(n.cliente_id), "cliente": nombre_cliente,
            "total": n.total, "moneda": n.moneda, "estado": n.estado, "uuid": n.uuid,
            "facturas": [{
                "factura_id": str(d.factura_id) if d.factura_id else None,
                "serie": folios[d.factura_id].serie if d.factura_id in folios else None,
                "folio": folios[d.factura_id].folio if d.factura_id in folios else None,
                "factura_ref": d.factura_ref,
                "importe": d.importe,
            } for d in por_nota.get(n.id, [])],
        })

    return {
        "desde": desde,
        "hasta": hasta,
        "cliente_id": cliente_id,
        "items": items,
        "total": total,
        "notas": vigentes,
        "total_cancelado": cancelado,
        "canceladas": canceladas,
    }


# ── Master de facturas ───────────────────────────────────────────────────────
#
# Una fila por factura con su remisión, su OC, su cobranza y sus NC. El armado
# vive en services/master_facturas.py; aquí solo el rango, los candados y los
# dos rescates (plaza y proyecto por serie) que ya usa la cartera.

def _master(db: Session, ctx: AuthContext, desde, hasta, cliente_id, estado):
    from ...services import master_facturas as mf

    hoy = datetime.now(timezone.utc).date()
    if cliente_id is not None and not ctx.cliente_permitido(cliente_id):
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    desde, hasta = _rango_pedido(desde, hasta, hoy)
    validos = {k for k, _ in mf.ESTADOS}
    pedidos = {e.strip().upper() for e in (estado or []) if e.strip()}
    if pedidos - validos:
        raise HTTPException(status_code=400, detail=f"Estado desconocido: {', '.join(sorted(pedidos - validos))}")
    serie_plaza = _serie_a_plaza(db, ctx.tenant_id)
    plaza_unica = _plaza_unica(db, ctx.tenant_id)
    filas = mf.construir(
        db, ctx, desde=desde, hasta=hasta, cliente_id=cliente_id, estados=pedidos or None,
        plaza_de=lambda f, cid: serie_plaza.get(f.serie or "") or plaza_unica.get(cid),
        proyecto_de_serie=_fila_de_reporte,
    )
    return desde, hasta, filas


@router.get("/master-facturas")
def master_facturas(
    desde: date | None = Query(default=None, description="Inicio del rango (por omisión, hace 30 días)"),
    hasta: date | None = Query(default=None, description="Fin del rango (por omisión, hoy)"),
    cliente_id: UUID | None = Query(default=None, description="Acota el reporte a un cliente"),
    estado: list[str] | None = Query(default=None, description="Estados del master a incluir (por omisión, todos)"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Master de facturas: una fila por factura emitida en el rango, con
    remisión, OC (y la liga al documento original), cobranza, notas de crédito,
    cancelación y el estado real (siete, no los tres de la columna)."""
    from ...services.master_facturas import ESTADOS

    desde, hasta, filas = _master(db, ctx, desde, hasta, cliente_id, estado)
    conteo = {k: 0 for k, _ in ESTADOS}
    for f in filas:
        conteo[f["estado"]] += 1
    return {
        "desde": desde,
        "hasta": hasta,
        "cliente_id": cliente_id,
        "estados": [{"clave": k, "etiqueta": e, "facturas": conteo[k]} for k, e in ESTADOS],
        "items": filas,
    }


@router.get("/master-facturas/xlsx")
def master_facturas_xlsx(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    cliente_id: UUID | None = Query(default=None),
    estado: list[str] | None = Query(default=None),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """El mismo master en Excel: hoja «Master» con autofiltro, liga a la OC y
    pie de totales que respeta el filtro, más la hoja «OC por factura»."""
    from fastapi import Response

    from ...services.master_facturas import generar_xlsx

    desde, hasta, filas = _master(db, ctx, desde, hasta, cliente_id, estado)
    contenido = generar_xlsx(
        filas, titulo=f"MASTER DE FACTURAS · {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}")
    nombre = f"master-facturas_{desde:%Y%m%d}-{hasta:%Y%m%d}.xlsx"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )

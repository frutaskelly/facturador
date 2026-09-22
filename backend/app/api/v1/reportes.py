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

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...core.rbac import AuthContext, get_tenant_db, require_permission
from ...models import Cliente, ClienteSucursal, ClienteSucursalSerie, Factura, Serie, Sucursal
from .cobranza import _en_cancelacion, _fila_de_reporte

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


@router.get("/cartera")
def cartera(
    agrupar: Literal["proyecto", "cliente", "sucursal"] = Query(default="proyecto"),
    incluir_en_cancelacion: bool = Query(default=False),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Cuentas por cobrar: una fila por proyecto, cliente o plaza, más la
    antigüedad global por meses.

    Vencido y cubetas usan la fecha de vencimiento (fecha + días de crédito del
    cliente), el mismo criterio del estado de cuenta.
    """
    hoy = datetime.now(timezone.utc).date()
    serie_plaza = _serie_a_plaza(db, ctx.tenant_id) if agrupar == "sucursal" else {}
    plaza_unica = _plaza_unica(db, ctx.tenant_id) if agrupar == "sucursal" else {}

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
    for f, nombre_cliente, dias_credito, cliente_id in q.all():
        if not ctx.cliente_permitido(cliente_id):
            continue
        saldo = Decimal(f.saldo_insoluto)
        if _en_cancelacion(f.cancelacion_msj):
            en_cancelacion += saldo
            if not incluir_en_cancelacion:
                continue
        if agrupar == "cliente":
            etiqueta = nombre_cliente
        elif agrupar == "sucursal":
            etiqueta = serie_plaza.get(f.serie or "") or plaza_unica.get(cliente_id) or "Sin plaza"
        else:
            etiqueta = _fila_de_reporte(f, nombre_cliente)

        fila = filas.setdefault(etiqueta, {
            "saldo": ZERO, "vencido": ZERO, "facturas": 0,
            "cliente_id": str(cliente_id), "serie": f.serie,
        })
        fila["saldo"] += saldo
        fila["facturas"] += 1
        f_fecha = f.fecha.date() if isinstance(f.fecha, datetime) else f.fecha
        dias_vencida = (hoy - (f_fecha + timedelta(days=int(dias_credito or 0)))).days
        antiguedad[_cubeta(dias_vencida)] += saldo
        total += saldo
        if dias_vencida > 0:
            fila["vencido"] += saldo
            vencido_total += saldo
        # Una fila que mezcla clientes o series no puede enlazar a un estado de
        # cuenta acotado: el enlace se deja en blanco antes que llevar a otro lado.
        if fila["serie"] != f.serie:
            fila["serie"] = None
        if fila["cliente_id"] != str(cliente_id):
            fila["cliente_id"] = None

    return {
        "corte": hoy,
        "agrupar": agrupar,
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


def _facturado(db: Session, ctx: AuthContext, desde: date, hasta: date) -> Decimal:
    """Lo timbrado en un rango, ambos extremos incluidos."""
    q = (
        db.query(sa.func.coalesce(sa.func.sum(Factura.total), 0))
        .filter(
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            sa.cast(Factura.fecha, sa.Date) >= desde,
            sa.cast(Factura.fecha, sa.Date) <= hasta,
        )
    )
    if ctx.cliente_scope:
        q = q.filter(Factura.cliente_id.in_(ctx.cliente_scope))
    return Decimal(q.scalar() or 0)


@router.get("/ventas")
def ventas(
    dias: int = Query(default=30, ge=7, le=180, description="Días del detalle diario"),
    meses: int = Query(default=6, ge=3, le=24, description="Meses de la serie mensual"),
    db: Session = Depends(get_tenant_db),
    ctx: AuthContext = Depends(require_permission(_READ)),
):
    """Facturación: el detalle diario, el mes a mes, y el corte contra el
    periodo anterior.

    La comparación es contra el MISMO tramo del periodo pasado (los días
    transcurridos de la semana contra esos mismos días de la semana anterior,
    y del mes contra el mes anterior). Comparar una semana a medias contra una
    semana completa siempre pinta una caída que no existe.
    """
    hoy = datetime.now(timezone.utc).date()

    # ── detalle diario ──
    desde = hoy - timedelta(days=dias - 1)
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
        )
        .group_by(sa.text("dia"))
    )
    if ctx.cliente_scope:
        q = q.filter(Factura.cliente_id.in_(ctx.cliente_scope))
    por_dia = {r.dia: (Decimal(r.total or 0), r.facturas) for r in q.all()}
    # Los días sin factura van con cero: una gráfica que salta del lunes al
    # jueves miente sobre el ritmo.
    diario = [
        {
            "fecha": desde + timedelta(days=i),
            "total": por_dia.get(desde + timedelta(days=i), (ZERO, 0))[0],
            "facturas": por_dia.get(desde + timedelta(days=i), (ZERO, 0))[1],
        }
        for i in range(dias)
    ]

    # ── semana en curso contra el mismo tramo de la anterior (lunes a hoy) ──
    lunes = hoy - timedelta(days=hoy.weekday())
    transcurridos = (hoy - lunes).days
    semana_actual = _facturado(db, ctx, lunes, hoy)
    lunes_previo = lunes - timedelta(days=7)
    semana_previa = _facturado(db, ctx, lunes_previo, lunes_previo + timedelta(days=transcurridos))
    semana_previa_total = _facturado(db, ctx, lunes_previo, lunes - timedelta(days=1))

    # ── mes en curso contra el mismo tramo del anterior ──
    primero = hoy.replace(day=1)
    fin_mes_previo = primero - timedelta(days=1)
    primero_previo = fin_mes_previo.replace(day=1)
    mes_actual = _facturado(db, ctx, primero, hoy)
    # Si el mes pasado es más corto, el tramo se recorta a su último día.
    hasta_previo = min(primero_previo + timedelta(days=(hoy - primero).days), fin_mes_previo)
    mes_previo = _facturado(db, ctx, primero_previo, hasta_previo)
    mes_previo_total = _facturado(db, ctx, primero_previo, fin_mes_previo)

    def variacion(actual: Decimal, previo: Decimal):
        """Porcentaje de cambio. Sin base previa no hay porcentaje que dar:
        None y que la pantalla diga "sin comparativo" en vez de un 100% falso."""
        if previo == 0:
            return None
        return float(round((actual - previo) / previo * 100, 1))

    # ── serie mensual ──
    inicio = (primero - timedelta(days=1)).replace(day=1)
    for _ in range(meses - 2):
        inicio = (inicio - timedelta(days=1)).replace(day=1)
    q2 = (
        db.query(
            sa.func.date_trunc("month", Factura.fecha).label("mes"),
            sa.func.sum(Factura.total).label("total"),
        )
        .filter(
            Factura.deleted_at.is_(None),
            Factura.estado == "TIMBRADA",
            sa.cast(Factura.fecha, sa.Date) >= inicio,
        )
        .group_by(sa.text("mes"))
        .order_by(sa.text("mes"))
    )
    if ctx.cliente_scope:
        q2 = q2.filter(Factura.cliente_id.in_(ctx.cliente_scope))
    mensual = [
        {"mes": r.mes.date() if hasattr(r.mes, "date") else r.mes, "total": Decimal(r.total or 0)}
        for r in q2.all()
    ]

    return {
        "hoy": hoy,
        "diario": diario,
        "mensual": mensual,
        "semana": {
            "actual": semana_actual,
            "previa_mismo_tramo": semana_previa,
            "previa_completa": semana_previa_total,
            "variacion": variacion(semana_actual, semana_previa),
            "dias_transcurridos": transcurridos + 1,
        },
        "mes": {
            "actual": mes_actual,
            "previo_mismo_tramo": mes_previo,
            "previo_completo": mes_previo_total,
            "variacion": variacion(mes_actual, mes_previo),
            "dias_transcurridos": (hoy - primero).days + 1,
        },
    }

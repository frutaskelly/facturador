"""Resolutor de precios (precios v2).

Devuelve el precio unitario para (cliente, sucursal, producto, presentación,
cantidad, fecha) aplicando prioridad MÁS-ESPECÍFICO-GANA (estándar wholesale,
no "precio más bajo"):

  1. Override de la sucursal + producto
  2. Override del cliente + producto
  3. Lista de precios de la sucursal (tier por cantidad)
  4. Lista de precios del cliente (tier por cantidad)
  5. Lista base/default del tenant (código UNICO, o la primera activa)

Dentro de una lista se toma el tier cuyo `cantidad_minima` ≤ cantidad más alto
(así "compra más → mejor precio"). Todo filtrado por vigencia.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Cliente, ListaPrecios, Precio, PrecioOverride, Producto, Sucursal


def _vigente(query, model, fecha):
    return query.filter(
        or_(model.vigencia_desde.is_(None), model.vigencia_desde <= fecha),
        or_(model.vigencia_hasta.is_(None), model.vigencia_hasta >= fecha),
    )


def _override(db, *, producto_id, presentacion, fecha, cliente_id=None, sucursal_id=None):
    q = db.query(PrecioOverride.precio_unitario).filter(
        PrecioOverride.producto_id == producto_id,
        PrecioOverride.presentacion == presentacion,
    )
    q = q.filter(PrecioOverride.sucursal_id == sucursal_id) if sucursal_id else q.filter(PrecioOverride.cliente_id == cliente_id)
    q = _vigente(q, PrecioOverride, fecha).order_by(PrecioOverride.created_at.desc())
    row = q.first()
    return row[0] if row else None


def _precio_lista(db, lista_id, producto_id, presentacion, cantidad, fecha):
    q = db.query(Precio.precio_unitario).filter(
        Precio.lista_id == lista_id,
        Precio.producto_id == producto_id,
        Precio.presentacion == presentacion,
        Precio.cantidad_minima <= cantidad,
    )
    q = _vigente(q, Precio, fecha).order_by(Precio.cantidad_minima.desc())
    row = q.first()
    return row[0] if row else None


def _lista_default(db):
    # 1) la marcada explícitamente como default (wizard de importación /
    # administración de listas); 2) la convención histórica codigo='UNICO';
    # 3) la más vieja activa.
    marcada = (
        db.query(ListaPrecios)
        .filter(ListaPrecios.es_default.is_(True), ListaPrecios.deleted_at.is_(None))
        .order_by(ListaPrecios.created_at.asc())
        .first()
    )
    if marcada is not None:
        return marcada
    base = (
        db.query(ListaPrecios)
        .filter(ListaPrecios.codigo == "UNICO", ListaPrecios.deleted_at.is_(None))
        .one_or_none()
    )
    if base is None:
        base = (
            db.query(ListaPrecios)
            .filter(ListaPrecios.status == "ACTIVO", ListaPrecios.deleted_at.is_(None))
            .order_by(ListaPrecios.created_at.asc())
            .first()
        )
    return base


def _factor(v) -> Decimal:
    """Factor de una presentación: soporta forma simple (número) o rica ({factor})."""
    if isinstance(v, dict):
        return Decimal(str(v.get("factor", 1)))
    return Decimal(str(v))


def resolver_precio(
    db: Session,
    *,
    producto_id: UUID,
    presentacion: str = "KILO",
    cantidad: Decimal = Decimal("1"),
    cliente_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    fecha: Optional[date] = None,
) -> Optional[dict]:
    """Precio resuelto + origen, o None si no hay ninguna regla aplicable.

    Si la presentación pedida no tiene precio propio, se deriva del precio de la
    unidad base × el factor de la presentación (p. ej. CAJA = PIEZA × 12). La
    cantidad se convierte a unidades base para elegir el tramo correcto.
    """
    fecha = fecha or date.today()
    cantidad = Decimal(cantidad)

    suc = None
    if sucursal_id:
        suc = db.query(Sucursal).filter(Sucursal.id == sucursal_id, Sucursal.deleted_at.is_(None)).one_or_none()
        if suc and cliente_id is None:
            cliente_id = suc.cliente_id
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).one_or_none() if cliente_id else None

    # Intentos de presentación: exacta primero; si falla, la unidad base × factor.
    # Cada intento es (presentacion, multiplicador_del_precio, cantidad_para_el_tramo).
    intentos: list[tuple[str, Decimal, Decimal]] = [(presentacion, Decimal(1), cantidad)]
    prod = (
        db.query(Producto)
        .filter(Producto.id == producto_id, Producto.deleted_at.is_(None))
        .one_or_none()
    )
    if prod:
        pres = prod.presentaciones or {}
        base = prod.unidad_base or prod.presentacion_default
        if base and base != presentacion and presentacion in pres and base in pres:
            ratio = _factor(pres[presentacion]) / _factor(pres[base])
            if ratio > 0:
                intentos.append((base, ratio, cantidad * ratio))

    def _resolver(src) -> Optional[Decimal]:
        """src(presentacion, cantidad) -> precio_unitario | None, aplicando intentos."""
        for pres_try, mult, cant_try in intentos:
            p = src(pres_try, cant_try)
            if p is not None:
                return p if mult == 1 else (p * mult).quantize(Decimal("0.01"))
        return None

    # 1. override sucursal
    if sucursal_id:
        p = _resolver(lambda pr, _c: _override(db, sucursal_id=sucursal_id, producto_id=producto_id, presentacion=pr, fecha=fecha))
        if p is not None:
            return {"precio": p, "origen": "override_sucursal"}
    # 2. override cliente
    if cliente_id:
        p = _resolver(lambda pr, _c: _override(db, cliente_id=cliente_id, producto_id=producto_id, presentacion=pr, fecha=fecha))
        if p is not None:
            return {"precio": p, "origen": "override_cliente"}
    # 3. lista de la sucursal
    if suc and suc.lista_precios_id:
        p = _resolver(lambda pr, cant: _precio_lista(db, suc.lista_precios_id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_sucursal", "lista_id": str(suc.lista_precios_id)}
    # 4. lista del cliente
    if cliente and cliente.lista_precios_id:
        p = _resolver(lambda pr, cant: _precio_lista(db, cliente.lista_precios_id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_cliente", "lista_id": str(cliente.lista_precios_id)}
    # 5. lista base/default
    base_lp = _lista_default(db)
    if base_lp is not None:
        p = _resolver(lambda pr, cant: _precio_lista(db, base_lp.id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_base", "lista_id": str(base_lp.id)}
    return None

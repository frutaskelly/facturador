"""Resolutor de precios (precios v2).

Devuelve el precio unitario para (cliente, sucursal, serie, proyecto, producto,
presentación, cantidad, fecha) aplicando prioridad MÁS-ESPECÍFICO-GANA (estándar
wholesale, no "precio más bajo"):

  1. Override de la sucursal + producto
  2. Override del cliente + producto
  3. Lista FORZADA a mano en el documento (`remisiones.lista_precios_id`)
  4. Lista asignada — el renglón de `lista_asignaciones` que coincide en las
     dimensiones más específicas (proyecto 8 · serie 4 · sucursal 2 · cliente 1)
  5. Lista base/default del tenant (`es_default`, o código UNICO, o la 1ª activa)

El paso 4 es el que sustituyó a "lista de la sucursal / lista del cliente": el
negocio también pacta por SERIE y por PROYECTO, y esas cuatro dimensiones se
combinan (EHMO en Pachuca bajo HOSPITALES es una negociación distinta de EHMO en
Pachuca a secas). Los pesos NO viven aquí: son la columna generada
`lista_asignaciones.especificidad`, para declararlos una sola vez.

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

from ..models import (
    ListaAsignacion,
    ListaPrecios,
    Precio,
    PrecioOverride,
    Producto,
    Sucursal,
)


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


def resolver_asignacion(
    db: Session,
    *,
    cliente_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
    fecha: Optional[date] = None,
) -> Optional[ListaAsignacion]:
    """El renglón de `lista_asignaciones` que aplica, o None.

    Una dimensión en NULL es comodín: coincide con lo que sea. Una dimensión
    llena sólo coincide con ese valor exacto — por eso un renglón de sucursal
    NO aplica a un documento sin sucursal, que es justo lo que se quiere.
    """
    fecha = fecha or date.today()
    q = db.query(ListaAsignacion).filter(
        or_(ListaAsignacion.cliente_id.is_(None), ListaAsignacion.cliente_id == cliente_id),
        or_(ListaAsignacion.sucursal_id.is_(None), ListaAsignacion.sucursal_id == sucursal_id),
        or_(ListaAsignacion.serie_id.is_(None), ListaAsignacion.serie_id == serie_id),
        or_(ListaAsignacion.proyecto_id.is_(None), ListaAsignacion.proyecto_id == proyecto_id),
    )
    q = _vigente(q, ListaAsignacion, fecha)
    # A igual especificidad gana la más reciente: es la última negociación.
    return q.order_by(
        ListaAsignacion.especificidad.desc(), ListaAsignacion.created_at.desc()
    ).first()


def origen_de(a: ListaAsignacion) -> str:
    """Cómo se le llama en pantalla al renglón que ganó: por su dimensión más
    específica, que es la que el vendedor reconoce ("es el precio del proyecto")."""
    if a.proyecto_id is not None:
        return "lista_proyecto"
    if a.serie_id is not None:
        return "lista_serie"
    if a.sucursal_id is not None:
        return "lista_sucursal"
    return "lista_cliente"


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
    serie_id: Optional[UUID] = None,
    proyecto_id: Optional[UUID] = None,
    lista_id: Optional[UUID] = None,
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
    # 3. lista forzada a mano en el documento
    if lista_id:
        p = _resolver(lambda pr, cant: _precio_lista(db, lista_id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_forzada", "lista_id": str(lista_id)}
    # 4. la asignación que coincide en las dimensiones más específicas
    asignacion = resolver_asignacion(
        db, cliente_id=cliente_id, sucursal_id=sucursal_id,
        serie_id=serie_id, proyecto_id=proyecto_id, fecha=fecha,
    )
    if asignacion is not None:
        p = _resolver(lambda pr, cant: _precio_lista(db, asignacion.lista_id, producto_id, pr, cant, fecha))
        if p is not None:
            return {
                "precio": p,
                "origen": origen_de(asignacion),
                "lista_id": str(asignacion.lista_id),
                "asignacion_id": str(asignacion.id),
            }
    # 5. lista base/default
    base_lp = _lista_default(db)
    if base_lp is not None:
        p = _resolver(lambda pr, cant: _precio_lista(db, base_lp.id, producto_id, pr, cant, fecha))
        if p is not None:
            return {"precio": p, "origen": "lista_base", "lista_id": str(base_lp.id)}
    return None

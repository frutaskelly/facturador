"""Resolución y reserva de folios consecutivos sin huecos.

`resolver_serie` elige qué serie usar al emitir un documento, de mayor a menor
prioridad: (1) serie elegida manualmente, (2) serie de la sucursal, (3) serie
del cliente, (4) serie predeterminada del inquilino (`es_default`). Devuelve el
objeto `Serie` (activo) o None si no hay ninguna aplicable.

`consumir_folio` incrementa el contador de una serie de forma atómica
(`UPDATE … RETURNING`, fila bloqueada por la transacción), así dos emisiones
simultáneas no duplican ni dejan huecos.

`siguiente_folio` se conserva por compatibilidad (consume por código + tipo).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Cliente, Serie, Sucursal


def resolver_serie(
    db: Session,
    tenant_id: UUID,
    tipo_documento: str,
    *,
    serie_id: Optional[UUID] = None,
    sucursal_id: Optional[UUID] = None,
    cliente_id: Optional[UUID] = None,
) -> Optional[Serie]:
    """Devuelve la `Serie` activa a usar según prioridad override→sucursal→cliente→default."""
    campo = "serie_factura_id" if tipo_documento == "FACTURA" else "serie_remision_id"

    def _activa(sid: Optional[UUID]) -> Optional[Serie]:
        if not sid:
            return None
        s = db.query(Serie).filter(Serie.id == sid, Serie.activa.is_(True)).one_or_none()
        return s if s and s.tipo_documento == tipo_documento else None

    # 1) elección manual al emitir
    s = _activa(serie_id)
    if s:
        return s

    # 2) serie de la sucursal
    if sucursal_id:
        suc = db.query(Sucursal).filter(Sucursal.id == sucursal_id).one_or_none()
        if suc:
            s = _activa(getattr(suc, campo))
            if s:
                return s

    # 3) serie del cliente
    if cliente_id:
        cli = db.query(Cliente).filter(Cliente.id == cliente_id).one_or_none()
        if cli:
            s = _activa(getattr(cli, campo))
            if s:
                return s

    # 4) serie predeterminada del inquilino para ese tipo de documento.
    # Usa first() (no one_or_none) por robustez: la unicidad de es_default se
    # cuida en la app, pero si por una carrera quedaran dos, no debe tronar el
    # timbrado con MultipleResultsFound (500). La más antigua gana, determinista.
    return (
        db.query(Serie)
        .filter(
            Serie.tenant_id == tenant_id,
            Serie.tipo_documento == tipo_documento,
            Serie.es_default.is_(True),
            Serie.activa.is_(True),
        )
        .order_by(Serie.created_at.asc())
        .first()
    )


def consumir_folio(db: Session, serie_id: UUID) -> Optional[int]:
    """Incrementa y devuelve el folio de la serie (atómico). None si no existe/inactiva."""
    row = db.execute(
        text(
            """
            UPDATE series
               SET folio_actual = folio_actual + 1, updated_at = now()
             WHERE id = :sid AND activa = true
            RETURNING folio_actual
            """
        ),
        {"sid": str(serie_id)},
    ).first()
    return int(row[0]) if row else None


def siguiente_folio(db: Session, tenant_id: UUID, *, codigo: str, tipo_documento: str) -> Optional[int]:
    row = db.execute(
        text(
            """
            UPDATE series
               SET folio_actual = folio_actual + 1, updated_at = now()
             WHERE tenant_id = :tid AND codigo = :codigo
               AND tipo_documento = :td AND activa = true
            RETURNING folio_actual
            """
        ),
        {"tid": str(tenant_id), "codigo": codigo, "td": tipo_documento},
    ).first()
    return int(row[0]) if row else None


# ─── Almacén: de dónde sale la mercancía ─────────────────────────────────────
# Vive aquí, junto a la resolución de serie, porque es EXACTAMENTE la misma
# cascada y conviene que no se separen: si una cambia, la otra debería cambiar
# con ella. Orden: elección manual → sucursal → cliente → predeterminado.


def resolver_almacen(
    db: Session,
    tenant_id,
    *,
    almacen_id=None,
    sucursal_id=None,
    cliente_id=None,
):
    """El almacén del que sale una remisión, o None si no hay ninguno aplicable.

    None es una respuesta legítima, no un error: una remisión sin almacén no
    mueve inventario, que es como funcionó todo hasta ahora.
    """
    from ..models import Almacen

    def _vivo(aid):
        if not aid:
            return None
        a = (
            db.query(Almacen)
            .filter(Almacen.id == aid, Almacen.deleted_at.is_(None))
            .one_or_none()
        )
        return a.id if a else None

    # 1) elección manual
    hit = _vivo(almacen_id)
    if hit:
        return hit

    # 2) almacén de la sucursal
    if sucursal_id:
        suc = db.query(Sucursal).filter(Sucursal.id == sucursal_id).one_or_none()
        if suc:
            hit = _vivo(suc.almacen_id)
            if hit:
                return hit

    # 3) almacén del cliente
    if cliente_id:
        cli = db.query(Cliente).filter(Cliente.id == cliente_id).one_or_none()
        if cli:
            hit = _vivo(cli.almacen_id)
            if hit:
                return hit

    # 4) el predeterminado del inquilino
    row = (
        db.query(Almacen.id)
        .filter(
            Almacen.tenant_id == tenant_id,
            Almacen.es_default.is_(True),
            Almacen.deleted_at.is_(None),
        )
        .order_by(Almacen.created_at.asc())
        .first()
    )
    return row[0] if row else None

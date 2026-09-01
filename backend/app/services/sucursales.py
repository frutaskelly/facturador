"""Vínculos cliente ↔ sucursal (plaza).

Un solo lugar para la pregunta que antes respondía `sucursales.cliente_id`:
¿este cliente se surte de esta plaza? Desde el rediseño 01-sep-2026 la sucursal
es unidad de negocio del tenant y la relación vive en `cliente_sucursales`, así
que la bandeja, las remisiones, las equivalencias y las asignaciones de precio
deben preguntar aquí — y decir lo MISMO.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import ClienteSucursal, Sucursal


def vinculo_de(db: Session, cliente_id: UUID, sucursal_id: UUID) -> Optional[ClienteSucursal]:
    """La fila del vínculo, o None si el cliente no se surte de esa plaza."""
    if cliente_id is None or sucursal_id is None:
        return None
    return (
        db.query(ClienteSucursal)
        .filter(
            ClienteSucursal.cliente_id == cliente_id,
            ClienteSucursal.sucursal_id == sucursal_id,
        )
        .one_or_none()
    )


def es_sucursal_de(db: Session, sucursal_id: UUID, cliente_id: UUID) -> bool:
    """¿La plaza surte a ese cliente? (existe el vínculo)."""
    return vinculo_de(db, cliente_id, sucursal_id) is not None


def sucursales_de_cliente(db: Session, cliente_id: UUID) -> list[UUID]:
    """Ids de las plazas vinculadas al cliente (vivas)."""
    return [
        s for (s,) in db.query(ClienteSucursal.sucursal_id)
        .join(Sucursal, Sucursal.id == ClienteSucursal.sucursal_id)
        .filter(
            ClienteSucursal.cliente_id == cliente_id,
            Sucursal.deleted_at.is_(None),
        )
    ]

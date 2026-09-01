"""Alcance de un proyecto: en qué plaza entrega.

La regla del negocio (reporte del dueño, 31-ago-2026): la negociación es de su
plaza. «HOSPITALES» de Pachuca no puede etiquetar —ni terminar cobrando— una
orden de Villahermosa. Desde el rediseño 01-sep-2026 el alcance es LA plaza del
proyecto (`proyectos.sucursal_id`, un proyecto por plaza): NULL = sin
restricción, aplica en cualquier plaza (retrocompatible con los proyectos
viejos); con plaza, solo aplica ahí.

Un solo helper para que la ingesta de la bandeja, el PATCH de asignación y la
validación de asignaciones de precios digan lo MISMO. Si la regla cambia,
cambia aquí.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import Proyecto


def alcance_de(db: Session, proyecto_id: UUID) -> Optional[set[UUID]]:
    """La plaza del proyecto como conjunto, o None si no tiene restricción."""
    row = db.query(Proyecto.sucursal_id).filter(Proyecto.id == proyecto_id).first()
    if row is None or row[0] is None:
        return None
    return {row[0]}


def proyecto_aplica(db: Session, proyecto_id: UUID, sucursal_id: Optional[UUID]) -> bool:
    """¿La negociación aplica en esa plaza?

    Sin restricción → siempre. Restringido → solo si la sucursal es la suya; y
    sin sucursal resuelta NO se puede afirmar que aplica, así que un proyecto
    con plaza y `sucursal_id=None` devuelve False.
    """
    alcance = alcance_de(db, proyecto_id)
    if alcance is None:
        return True
    return sucursal_id is not None and sucursal_id in alcance

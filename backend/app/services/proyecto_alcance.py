"""Alcance de un proyecto: en qué sucursales entrega.

La regla del negocio (reporte del dueño, 31-ago-2026): la negociación es de su
plaza. «HOSPITALES» de Pachuca no puede etiquetar —ni terminar cobrando— una
orden de Villahermosa. El alcance vive en `proyecto_sucursales` (migración
0058): un proyecto SIN filas no tiene restricción y aplica en cualquier plaza
(el comportamiento de siempre, y lo que hace retrocompatible el aterrizaje);
con filas, solo aplica en esas sucursales.

Un solo helper para que la ingesta de la bandeja, el PATCH de asignación y la
validación de asignaciones de precios digan lo MISMO. Si la regla cambia,
cambia aquí.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import ProyectoSucursal


def alcance_de(db: Session, proyecto_id: UUID) -> Optional[set[UUID]]:
    """Las sucursales asignadas al proyecto, o None si no tiene restricción."""
    filas = (
        db.query(ProyectoSucursal.sucursal_id)
        .filter(ProyectoSucursal.proyecto_id == proyecto_id)
        .all()
    )
    return {f[0] for f in filas} or None


def proyecto_aplica(db: Session, proyecto_id: UUID, sucursal_id: Optional[UUID]) -> bool:
    """¿La negociación aplica en esa plaza?

    Sin restricción → siempre. Restringido → solo si la sucursal está en su
    alcance; y sin sucursal resuelta NO se puede afirmar que aplica, así que
    un proyecto restringido con `sucursal_id=None` devuelve False.
    """
    alcance = alcance_de(db, proyecto_id)
    if alcance is None:
        return True
    return sucursal_id is not None and sucursal_id in alcance

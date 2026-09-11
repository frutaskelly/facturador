"""Proyectos — la negociación con nombre propio.

El `codigo` no se acepta al crear: se deriva del nombre (misma convención que
categorías, sucursales y proveedores) y se devuelve de solo lectura.
"""
import uuid
from datetime import datetime
from typing import Optional

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, Field, field_validator

from .common import ORMModel


def _normalizar_correos(v: Optional[list[str]]) -> Optional[list[str]]:
    """Cada correo se valida y normaliza AL GUARDAR: un typo aquí viaja después
    a todos los envíos del proyecto sin que nadie lo vuelva a leer."""
    if v is None:
        return v
    limpios: list[str] = []
    for c in v:
        c = (c or "").strip()
        if not c:
            continue
        try:
            limpios.append(validate_email(c, check_deliverability=False).normalized)
        except EmailNotValidError:
            raise ValueError(f"Correo inválido: {c}")
    return limpios


class ProyectoCreate(BaseModel):
    nombre: str = Field(max_length=254)
    cliente_id: Optional[uuid.UUID] = None
    activo: bool = True
    notas: Optional[str] = None
    # LA plaza del proyecto (un proyecto por plaza; «HOSPITALES» de Pachuca y
    # de Tabasco son dos filas). None = aplica en cualquier plaza.
    sucursal_id: Optional[uuid.UUID] = None
    # Destinatarios predeterminados de las facturas del proyecto (86bbyveu1).
    correos_facturas: list[str] = Field(default_factory=list, max_length=20)

    _correos = field_validator("correos_facturas")(_normalizar_correos)


class ProyectoUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=254)
    cliente_id: Optional[uuid.UUID] = None
    activo: Optional[bool] = None
    notas: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None
    correos_facturas: Optional[list[str]] = Field(default=None, max_length=20)

    _correos = field_validator("correos_facturas")(_normalizar_correos)


class ProyectoOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    codigo: str
    nombre: str
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    activo: bool
    notas: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None
    # Para pintar la columna sin otra consulta ("Pachuca").
    sucursal_nombre: Optional[str] = None
    correos_facturas: list[str] = []
    created_at: datetime
    updated_at: datetime

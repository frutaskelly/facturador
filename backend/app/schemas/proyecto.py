"""Proyectos — la negociación con nombre propio.

El `codigo` no se acepta al crear: se deriva del nombre (misma convención que
categorías, sucursales y proveedores) y se devuelve de solo lectura.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class ProyectoCreate(BaseModel):
    nombre: str = Field(max_length=254)
    cliente_id: Optional[uuid.UUID] = None
    activo: bool = True
    notas: Optional[str] = None
    # Sucursales donde entrega el proyecto (una o más). Si el proyecto tiene
    # dueño, deben ser de ese cliente; uno del grupo acepta de varios.
    sucursal_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class ProyectoUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=254)
    cliente_id: Optional[uuid.UUID] = None
    activo: Optional[bool] = None
    notas: Optional[str] = None
    # None = no tocar las asignaciones; lista (aun vacía) = reemplazarlas.
    sucursal_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=100)


class ProyectoOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    codigo: str
    nombre: str
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    activo: bool
    notas: Optional[str] = None
    sucursal_ids: list[uuid.UUID] = Field(default_factory=list)
    # Para pintar la columna sin otra consulta ("Pachuca, Tulancingo").
    sucursales_nombres: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

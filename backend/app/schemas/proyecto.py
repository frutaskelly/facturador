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
    # LA plaza del proyecto (un proyecto por plaza; «HOSPITALES» de Pachuca y
    # de Tabasco son dos filas). None = aplica en cualquier plaza.
    sucursal_id: Optional[uuid.UUID] = None


class ProyectoUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=254)
    cliente_id: Optional[uuid.UUID] = None
    activo: Optional[bool] = None
    notas: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None


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
    created_at: datetime
    updated_at: datetime

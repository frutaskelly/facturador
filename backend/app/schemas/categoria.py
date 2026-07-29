"""Category schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class CategoriaBase(BaseModel):
    nombre: str = Field(max_length=100)
    descripcion: Optional[str] = None
    activo: bool = True


class CategoriaCreate(CategoriaBase):
    # `codigo` opcional: si no viene, el servidor lo deriva del nombre
    # (services/categoria_codigo.py), igual que proveedores/sucursales.
    codigo: Optional[str] = Field(default=None, min_length=1, max_length=10)


class CategoriaUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=10)
    nombre: Optional[str] = Field(default=None, max_length=100)
    descripcion: Optional[str] = None
    activo: Optional[bool] = None


class CategoriaOut(ORMModel, CategoriaBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    codigo: str
    created_at: datetime
    updated_at: datetime

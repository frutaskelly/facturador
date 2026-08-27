"""Price-list and price schemas."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from .common import ORMModel


# ─── price lists ─────────────────────────────────────────────────────────────
class ListaPreciosBase(BaseModel):
    codigo: str = Field(max_length=20)
    nombre: str = Field(max_length=254)
    status: str = Field(default="ACTIVO", max_length=20)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    moneda: str = Field(default="MXN", max_length=3)
    notas: Optional[str] = None
    es_default: bool = False


class ListaPreciosCreate(ListaPreciosBase):
    pass


class ListaPreciosUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    nombre: Optional[str] = Field(default=None, max_length=254)
    status: Optional[str] = Field(default=None, max_length=20)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    moneda: Optional[str] = Field(default=None, max_length=3)
    notas: Optional[str] = None
    es_default: Optional[bool] = None


class ListaPreciosOut(ORMModel, ListaPreciosBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ─── prices (line items) ─────────────────────────────────────────────────────
class PrecioBase(BaseModel):
    producto_id: uuid.UUID
    presentacion: str = Field(default="KILO", max_length=20)
    precio_unitario: Decimal = Field(ge=0)
    cantidad_minima: int = Field(default=1, ge=1)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None


class PrecioCreate(PrecioBase):
    pass


class PrecioUpdate(BaseModel):
    presentacion: Optional[str] = Field(default=None, max_length=20)
    precio_unitario: Optional[Decimal] = Field(default=None, ge=0)
    cantidad_minima: Optional[int] = Field(default=None, ge=1)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None


class PrecioOut(ORMModel, PrecioBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    lista_id: uuid.UUID


# ─── bulk / copy operations ──────────────────────────────────────────────────
class PrecioCopiarRequest(BaseModel):
    origen_id: uuid.UUID


class PrecioBulkItem(BaseModel):
    producto_id: uuid.UUID
    presentacion: str = Field(default="KILO", max_length=20)
    precio_unitario: Decimal = Field(ge=0)
    cantidad_minima: int = Field(default=1, ge=1)


class PrecioBulkRequest(BaseModel):
    items: List[PrecioBulkItem]


class PrecioBulkResult(BaseModel):
    created: int
    updated: int
    skipped: int


# ─── Asignación de la lista (wizard de importación / administración) ─────────
class ListaAsignarIn(BaseModel):
    """Asignar la lista como default del negocio y/o a clientes específicos."""
    default: bool = False
    cliente_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


class ListaAsignarOut(BaseModel):
    default: bool
    clientes_asignados: int

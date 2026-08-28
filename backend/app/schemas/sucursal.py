"""Schemas de precios v2: sucursales, overrides de precio y cotización."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .common import ORMModel


# ── Sucursal ──
class SucursalBase(BaseModel):
    # La lista de precios NO se elige aquí: se asigna en Listas de precios ›
    # Asignaciones, que además sabe de serie y proyecto (migración 0050).
    cliente_id: uuid.UUID
    codigo: Optional[str] = Field(default=None, max_length=20)
    nombre: str = Field(max_length=254)
    domicilio: dict = Field(default_factory=dict)
    contacto: Optional[str] = Field(default=None, max_length=254)
    telefono: Optional[str] = Field(default=None, max_length=20)
    activo: bool = True
    # series de la sucursal (ganan sobre las del cliente)
    almacen_id: Optional[uuid.UUID] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None


class SucursalCreate(SucursalBase):
    # Series DISPONIBLES en la sucursal (una o más; la primera de cada tipo se
    # vuelve la default si no vino serie_factura_id/serie_remision_id).
    series_factura_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    series_remision_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class SucursalUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    nombre: Optional[str] = Field(default=None, max_length=254)
    domicilio: Optional[dict] = None
    contacto: Optional[str] = Field(default=None, max_length=254)
    telefono: Optional[str] = Field(default=None, max_length=20)
    activo: Optional[bool] = None
    almacen_id: Optional[uuid.UUID] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    series_factura_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)
    series_remision_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)


class SucursalOut(ORMModel, SucursalBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    # El abanico completo de series disponibles (la default va aparte en los FK).
    series_factura_ids: list[uuid.UUID] = Field(default_factory=list)
    series_remision_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ── Override de precio ──
class PrecioOverrideCreate(BaseModel):
    cliente_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    producto_id: uuid.UUID
    presentacion: str = Field(default="KILO", max_length=20)
    precio_unitario: Decimal = Field(ge=0)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None

    @model_validator(mode="after")
    def _xor_scope(self):
        if bool(self.cliente_id) == bool(self.sucursal_id):
            raise ValueError("Indica exactamente uno: cliente_id o sucursal_id")
        return self


class PrecioOverrideOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    cliente_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    producto_id: uuid.UUID
    presentacion: str
    precio_unitario: Decimal
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    created_at: datetime
    updated_at: datetime


# ── Cotización (precio resuelto) ──
class CotizacionOut(BaseModel):
    producto_id: uuid.UUID
    presentacion: str
    cantidad: Decimal
    precio: Optional[Decimal] = None
    origen: Optional[str] = None
    lista_id: Optional[uuid.UUID] = None

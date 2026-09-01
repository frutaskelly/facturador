"""Schemas de sucursales (plazas), vínculos cliente×plaza, overrides y cotización."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .common import ORMModel


# ── Sucursal (plaza del negocio) ──
class SucursalBase(BaseModel):
    # La lista de precios NO se elige aquí: se asigna en Listas de precios ›
    # Asignaciones, que además sabe de serie y proyecto (migración 0050).
    codigo: Optional[str] = Field(default=None, max_length=20)
    nombre: str = Field(max_length=254)
    domicilio: dict = Field(default_factory=dict)
    contacto: Optional[str] = Field(default=None, max_length=254)
    telefono: Optional[str] = Field(default=None, max_length=20)
    activo: bool = True
    # De dónde sale la mercancía de la plaza (para todos sus clientes).
    almacen_id: Optional[uuid.UUID] = None


class SucursalCreate(SucursalBase):
    # Conveniencia de alta: crear la plaza ya vinculada a un cliente. Las
    # series (default y abanico) son DEL VÍNCULO con ese cliente.
    cliente_id: Optional[uuid.UUID] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    series_factura_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    series_remision_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _series_requieren_cliente(self):
        if self.cliente_id is None and any(
            (self.serie_factura_id, self.serie_remision_id,
             self.series_factura_ids, self.series_remision_ids)
        ):
            raise ValueError("Las series son del vínculo: indica cliente_id")
        return self


class SucursalUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    nombre: Optional[str] = Field(default=None, max_length=254)
    domicilio: Optional[dict] = None
    contacto: Optional[str] = Field(default=None, max_length=254)
    telefono: Optional[str] = Field(default=None, max_length=20)
    activo: Optional[bool] = None
    almacen_id: Optional[uuid.UUID] = None


class SucursalOut(ORMModel, SucursalBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    # Qué clientes se surten de la plaza (para pintar la columna sin otra consulta).
    clientes_ids: list[uuid.UUID] = Field(default_factory=list)
    clientes_nombres: list[str] = Field(default_factory=list)
    # Al listar con ?cliente_id=X: la serie default y el abanico DEL VÍNCULO con
    # ese cliente (compatibilidad con los selectores de emisión). Sin filtro,
    # vienen vacíos — la plaza sola ya no tiene serie.
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    series_factura_ids: list[uuid.UUID] = Field(default_factory=list)
    series_remision_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ── Vínculo cliente ↔ plaza ──
class ClienteSucursalUpsert(BaseModel):
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    # None = no tocar el abanico; lista (aun vacía) = reemplazarlo.
    series_factura_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)
    series_remision_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=20)


class ClienteSucursalOut(BaseModel):
    id: uuid.UUID
    cliente_id: uuid.UUID
    sucursal_id: uuid.UUID
    cliente_nombre: Optional[str] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    series_factura_ids: list[uuid.UUID] = Field(default_factory=list)
    series_remision_ids: list[uuid.UUID] = Field(default_factory=list)


# ── Override de precio ──
class PrecioOverrideCreate(BaseModel):
    # Al menos uno; ambos = precio de ESE cliente en ESA plaza (el más
    # específico). Solo sucursal = precio de la plaza para todos sus clientes.
    cliente_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    producto_id: uuid.UUID
    presentacion: str = Field(default="KILO", max_length=20)
    precio_unitario: Decimal = Field(ge=0)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None

    @model_validator(mode="after")
    def _alguna_dimension(self):
        if not self.cliente_id and not self.sucursal_id:
            raise ValueError("Indica cliente_id, sucursal_id o ambos")
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
    # El cantidad_minima del TRAMO que habló, cuando el precio salió de una fila
    # de esta presentación. Sin él, «actualizar la lista» apuntaría al tramo
    # base aunque la referencia viniera de un tramo por volumen.
    cantidad_minima: Optional[int] = None

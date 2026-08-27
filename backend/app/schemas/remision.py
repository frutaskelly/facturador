"""Remisión schemas."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

RemisionEstado = Literal["BORRADOR", "CONFIRMADA", "FACTURADA", "CANCELADA"]
Canal = Literal["MANUAL", "WEB", "API"]


class LineaRemisionCreate(BaseModel):
    producto_id: uuid.UUID
    presentacion: str = Field(default="KILO", max_length=20)
    cantidad_solicitada: Decimal = Field(gt=0)
    # Opcional: si se omite, se resuelve automáticamente (cliente/sucursal/volumen).
    precio_unitario: Optional[Decimal] = Field(default=None, ge=0)
    notas: Optional[str] = None


class LineaRemisionOut(ORMModel):
    id: uuid.UUID
    numero_linea: int
    producto_id: uuid.UUID
    producto_nombre: Optional[str] = None
    presentacion: str
    cantidad_solicitada: Decimal
    cantidad_surtida: Optional[Decimal] = None
    precio_unitario: Decimal
    importe: Decimal
    iva_importe: Decimal = Decimal("0")
    ieps_importe: Decimal = Decimal("0")
    lote_id: Optional[uuid.UUID] = None
    notas: Optional[str] = None


class RemisionCreate(BaseModel):
    cliente_facturacion_id: uuid.UUID
    almacen_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    # Lista forzada a mano: gana sobre la resolución por cliente/sucursal/serie/proyecto.
    lista_precios_id: Optional[uuid.UUID] = None
    # La negociación bajo la que se vende; también decide qué lista aplica.
    proyecto_id: Optional[uuid.UUID] = None
    # Override manual de serie; si es None se resuelve por sucursal/cliente/default.
    serie_id: Optional[uuid.UUID] = None
    fecha_remision: Optional[date] = None
    fecha_entrega: Optional[date] = None
    canal: Canal = "MANUAL"
    descuento: Decimal = Field(default=Decimal("0"), ge=0)
    notas: Optional[str] = None
    nota_entrega: Optional[str] = None
    lineas: list[LineaRemisionCreate] = Field(min_length=1)


class RemisionUpdate(BaseModel):
    # Edición de una remisión en BORRADOR. Si se envían `lineas`, se reemplazan
    # todas y se recalculan los totales. El folio NO cambia (la serie ya se
    # consumió al crear), por eso no se acepta serie_id aquí.
    cliente_facturacion_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    almacen_id: Optional[uuid.UUID] = None
    lista_precios_id: Optional[uuid.UUID] = None
    proyecto_id: Optional[uuid.UUID] = None
    fecha_remision: Optional[date] = None
    fecha_entrega: Optional[date] = None
    descuento: Optional[Decimal] = Field(default=None, ge=0)
    notas: Optional[str] = None
    nota_entrega: Optional[str] = None
    lineas: Optional[list[LineaRemisionCreate]] = None
    # Sobregiro autorizado al re-descontar inventario de una CONFIRMADA
    # (misma política que confirmar/facturar — decisión 2026-07-29 #5).
    permitir_negativos: bool = False


class RemisionOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    folio_interno: str
    # POS: estación donde espera (None = fuera del POS) y quién completó qué.
    pos_etapa: Optional[str] = None
    pos_asignaciones: dict = {}
    cliente_facturacion_id: uuid.UUID
    almacen_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    lista_precios_id: Optional[uuid.UUID] = None
    proyecto_id: Optional[uuid.UUID] = None
    serie_id: Optional[uuid.UUID] = None
    fecha_remision: date
    fecha_entrega: Optional[date] = None
    estado: str
    canal: str
    factura_id: Optional[uuid.UUID] = None
    # Folio (serie+folio) y estado de la ÚLTIMA factura de la remisión, para la
    # columna "Factura" de la lista (incluye facturas canceladas).
    factura_folio: Optional[str] = None
    factura_estado: Optional[str] = None
    subtotal: Decimal
    descuento: Decimal
    iva: Decimal
    ieps: Decimal
    total: Decimal
    notas: Optional[str] = None
    nota_entrega: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LineaDevolucionOut(ORMModel):
    id: uuid.UUID
    producto_id: uuid.UUID
    presentacion: str
    cantidad: Decimal
    cantidad_base: Decimal


class DevolucionOut(ORMModel):
    id: uuid.UUID
    motivo: Optional[str] = None
    created_at: datetime
    lineas: list[LineaDevolucionOut] = []


class RemisionDetailOut(RemisionOut):
    lineas: list[LineaRemisionOut] = []
    devoluciones: list[DevolucionOut] = []


class PesoLinea(BaseModel):
    linea_id: uuid.UUID
    # Peso/medida real en UNIDADES BASE (catch-weight) para esta línea.
    cantidad_base: Decimal = Field(gt=0)


class ConfirmarRemisionIn(BaseModel):
    """Cuerpo opcional al confirmar: pesos reales por línea (peso variable).
    Si no se envía, se reserva el estimado cantidad×factor."""
    pesos: Optional[list[PesoLinea]] = None
    # Permite confirmar aunque no haya existencia suficiente; el inventario
    # disponible queda en negativo (venta sin stock / sobregiro autorizado).
    permitir_negativos: bool = False

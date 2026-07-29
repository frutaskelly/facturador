"""Factura (CFDI 4.0) schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

from .common import ORMModel


class EnviarFacturaIn(BaseModel):
    to: List[EmailStr] = Field(min_length=1, max_length=10)
    mensaje: Optional[str] = Field(default=None, max_length=2000)


class FacturaDesdeRemisionesIn(BaseModel):
    remision_ids: List[uuid.UUID] = Field(min_length=1, max_length=200)
    # Override manual de serie al emitir; si es None se resuelve por sucursal/cliente/default.
    serie_id: Optional[uuid.UUID] = None
    serie: Optional[str] = Field(default=None, max_length=10)  # back-compat por código
    uso_cfdi: Optional[str] = Field(default=None, max_length=5)
    forma_pago: Optional[str] = Field(default=None, max_length=5)
    metodo_pago: Optional[str] = Field(default=None, max_length=5)
    notas: Optional[str] = None
    # True = suma las líneas del mismo producto/unidad en un solo concepto.
    agrupar_productos: bool = Field(default=False)
    # Autoriza sobregiro al auto-confirmar remisiones en BORRADOR sin existencia
    # suficiente (el inventario queda en negativo). Ignorado para CONFIRMADAS.
    permitir_negativos: bool = Field(default=False)


class CancelarFacturaIn(BaseModel):
    # 01 errores con relación (requiere uuid_sustitucion) | 02 sin relación |
    # 03 no se llevó a cabo | 04 operación nominativa en factura global
    motivo: str = Field(default="02", pattern="^0[1-4]$")
    uuid_sustitucion: Optional[uuid.UUID] = None
    # Qué hacer con el inventario reservado por las remisiones de la factura:
    #  - "devolucion": regresa a disponible; las remisiones vuelven a BORRADOR.
    #  - "perdida": se da de baja como MERMA (no regresa); remisiones CANCELADAS.
    inventario: Literal["devolucion", "perdida"] = "devolucion"


class LineaFacturaDirectaIn(BaseModel):
    producto_id: uuid.UUID
    cantidad: Decimal = Field(gt=0)
    precio_unitario: Decimal = Field(ge=0)
    presentacion: Optional[str] = Field(default=None, max_length=20)


class FacturaDirectaIn(BaseModel):
    """Factura capturada a mano (sin remisión). Descuenta inventario del almacén
    indicado al timbrar y lo regresa al cancelar."""
    cliente_id: uuid.UUID
    almacen_id: uuid.UUID                 # de dónde sale el inventario al timbrar
    serie_id: Optional[uuid.UUID] = None
    serie: Optional[str] = Field(default=None, max_length=10)
    uso_cfdi: Optional[str] = Field(default=None, max_length=5)
    forma_pago: Optional[str] = Field(default=None, max_length=5)
    metodo_pago: Optional[str] = Field(default=None, max_length=5)
    notas: Optional[str] = None
    lineas: List[LineaFacturaDirectaIn] = Field(min_length=1)


class LineaFacturaOut(ORMModel):
    numero_linea: int
    producto_id: uuid.UUID
    clave_prod_serv: str
    clave_unidad: str
    descripcion: str
    cantidad: Decimal
    valor_unitario: Decimal
    importe: Decimal
    descuento: Decimal
    objeto_imp: str
    iva_tasa: Decimal
    iva_importe: Decimal
    ieps_tipo: Optional[str] = None
    ieps_valor: Decimal
    ieps_importe: Decimal
    ret_iva_importe: Decimal
    ret_isr_importe: Decimal


class FacturaOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    serie: str
    folio: int
    cliente_id: uuid.UUID
    uso_cfdi: str
    forma_pago: str
    metodo_pago: str
    moneda: str
    tipo_comprobante: str
    lugar_expedicion: Optional[str] = None
    fecha: datetime
    subtotal: Decimal
    descuento: Decimal
    iva_trasladado: Decimal
    ieps_trasladado: Decimal
    ret_iva: Decimal
    ret_isr: Decimal
    total: Decimal
    estado: str
    uuid: Optional[str] = None
    fecha_timbrado: Optional[datetime] = None
    fecha_cancelacion: Optional[datetime] = None
    motivo_cancelacion: Optional[str] = None
    pdf_url: Optional[str] = None
    notas: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FacturaDetailOut(FacturaOut):
    lineas: List[LineaFacturaOut] = []

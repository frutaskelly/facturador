"""Customer schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

ClienteTipo = Literal["PRINCIPAL_GOV", "SUB", "PRIVADO", "OTRO"]


class ClienteBase(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    tipo: ClienteTipo = "PRIVADO"
    status: str = Field(default="ACTIVO", max_length=20)
    # fiscal identity (CFDI receptor)
    legal_name: str = Field(max_length=254)
    rfc: str = Field(max_length=15)
    regimen_fiscal: Optional[str] = Field(default=None, max_length=4)
    uso_cfdi_default: Optional[str] = Field(default=None, max_length=5)
    forma_pago_default: Optional[str] = Field(default=None, max_length=5)
    metodo_pago_default: Optional[str] = Field(default=None, max_length=5)
    domicilio_fiscal: dict = Field(default_factory=dict)
    # commercial
    condiciones_pago: Optional[str] = Field(default=None, max_length=50)
    limite_credito: Decimal = Field(default=Decimal("0"), ge=0)
    dias_credito: int = Field(default=0, ge=0)
    descuento_default: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    config_addenda: dict = Field(default_factory=dict)
    custom_fields: dict = Field(default_factory=dict)
    # series de folios predeterminadas del cliente (la sucursal gana sobre esto)
    almacen_id: Optional[uuid.UUID] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None


class ClienteCreate(ClienteBase):
    pass


class ClienteUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    tipo: Optional[ClienteTipo] = None
    status: Optional[str] = Field(default=None, max_length=20)
    legal_name: Optional[str] = Field(default=None, max_length=254)
    rfc: Optional[str] = Field(default=None, max_length=15)
    regimen_fiscal: Optional[str] = Field(default=None, max_length=4)
    uso_cfdi_default: Optional[str] = Field(default=None, max_length=5)
    forma_pago_default: Optional[str] = Field(default=None, max_length=5)
    metodo_pago_default: Optional[str] = Field(default=None, max_length=5)
    domicilio_fiscal: Optional[dict] = None
    condiciones_pago: Optional[str] = Field(default=None, max_length=50)
    limite_credito: Optional[Decimal] = Field(default=None, ge=0)
    dias_credito: Optional[int] = Field(default=None, ge=0)
    descuento_default: Optional[Decimal] = Field(default=None, ge=0, le=100)
    config_addenda: Optional[dict] = None
    custom_fields: Optional[dict] = None
    almacen_id: Optional[uuid.UUID] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None


class ClienteOut(ORMModel, ClienteBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    saldo_actual: Decimal
    ventas_ytd: Decimal
    ultima_venta_at: Optional[datetime] = None
    ultimo_pago_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

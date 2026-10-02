"""Customer schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .common import ORMModel

ClienteTipo = Literal["PRINCIPAL_GOV", "SUB", "PRIVADO", "OTRO"]


def _nombre_corto(v: Optional[str]) -> Optional[str]:
    """Sin espacios de sobra; vacío = sin nombre corto (sale la razón social)."""
    v = " ".join((v or "").split())
    return v or None


class ClienteBase(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    tipo: ClienteTipo = "PRIVADO"
    status: str = Field(default="ACTIVO", max_length=20)
    # fiscal identity (CFDI receptor)
    legal_name: str = Field(max_length=254)
    # Como se le dice en las tablas de cobranza (EHMO en vez de la razón social).
    nombre_corto: Optional[str] = Field(default=None, max_length=40)
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

    _limpia_nombre_corto = field_validator("nombre_corto")(_nombre_corto)


class ClienteCreate(ClienteBase):
    # La lista con la que se le cobra en cualquier plaza (su renglón de
    # asignación; el vínculo con una plaza y el proyecto le ganan).
    lista_id: Optional[uuid.UUID] = None


class ClienteUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    tipo: Optional[ClienteTipo] = None
    status: Optional[str] = Field(default=None, max_length=20)
    legal_name: Optional[str] = Field(default=None, max_length=254)
    nombre_corto: Optional[str] = Field(default=None, max_length=40)
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
    # Candado de la migración: mientras esté prendido, el cliente factura en
    # SAE (aquí solo se refleja) y crear facturas nativas para él devuelve 409.
    # Se apaga cliente por cliente en el corte (Etapa 4 del plan).
    espejo_sae: Optional[bool] = None
    # Omitido = no tocar; null = quitarla.
    lista_id: Optional[uuid.UUID] = None

    _limpia_nombre_corto = field_validator("nombre_corto")(_nombre_corto)


class ClienteOut(ORMModel, ClienteBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    espejo_sae: bool = False
    saldo_actual: Decimal
    ventas_ytd: Decimal
    ultima_venta_at: Optional[datetime] = None
    ultimo_pago_at: Optional[datetime] = None
    # La lista «en cualquier plaza» (la llenan los endpoints de clientes).
    lista_id: Optional[uuid.UUID] = None
    lista_nombre: Optional[str] = None
    created_at: datetime
    updated_at: datetime

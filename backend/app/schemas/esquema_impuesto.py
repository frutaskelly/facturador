"""Tax-scheme schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

TipoIeps = Literal["TASA", "CUOTA"]


class EsquemaImpuestoBase(BaseModel):
    # `codigo` se autogenera en el servidor (ESQ-NNN); opcional como entrada.
    codigo: Optional[str] = Field(default=None, max_length=10)
    nombre: str = Field(max_length=120)
    descripcion: Optional[str] = None
    iva_tasa: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    ieps_tasa: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    # IEPS: TASA = porcentaje (fracción, p.ej. 0.08); CUOTA = $/litro (ieps_cuota)
    tipo_ieps: TipoIeps = "TASA"
    ieps_cuota: Decimal = Field(default=Decimal("0"), ge=0)
    iva_exento: bool = False
    retencion_iva_tasa: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    retencion_isr_tasa: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    activo: bool = True


class EsquemaImpuestoCreate(EsquemaImpuestoBase):
    pass


class EsquemaImpuestoUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=10)
    nombre: Optional[str] = Field(default=None, max_length=120)
    descripcion: Optional[str] = None
    iva_tasa: Optional[Decimal] = Field(default=None, ge=0, le=1)
    ieps_tasa: Optional[Decimal] = Field(default=None, ge=0, le=1)
    tipo_ieps: Optional[TipoIeps] = None
    ieps_cuota: Optional[Decimal] = Field(default=None, ge=0)
    iva_exento: Optional[bool] = None
    retencion_iva_tasa: Optional[Decimal] = Field(default=None, ge=0, le=1)
    retencion_isr_tasa: Optional[Decimal] = Field(default=None, ge=0, le=1)
    activo: Optional[bool] = None


class EsquemaImpuestoOut(ORMModel, EsquemaImpuestoBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    # Cuántos productos vivos usan el esquema (lo calcula la lista).
    productos: int = 0
    created_at: datetime
    updated_at: datetime

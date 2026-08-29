"""Schemas de series de folios."""
import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

TipoSerie = Literal["FISCAL", "NO_FISCAL"]
TipoDoc = Literal["FACTURA", "NOTA_CREDITO", "REMISION", "PAGO"]


class SerieBase(BaseModel):
    codigo: str = Field(max_length=25)
    tipo: TipoSerie = "FISCAL"
    tipo_documento: TipoDoc
    nombre: Optional[str] = Field(default=None, max_length=120)
    activa: bool = True
    es_default: bool = False
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    notas: Optional[str] = None


class SerieCreate(SerieBase):
    folio_actual: int = Field(default=0, ge=0)  # folio inicial; el primero emitido será +1


class SeriePairCreate(BaseModel):
    """Crea de un golpe la serie de factura (fiscal) y la de remisión (no fiscal)."""
    codigo_factura: str = Field(max_length=25)      # p. ej. "SLP" → folios SLP1, SLP2…
    codigo_remision: str = Field(max_length=25)     # p. ej. "RSLP" → folios RSLP1, RSLP2…
    nombre: Optional[str] = Field(default=None, max_length=120)
    es_default: bool = False
    folio_inicial_factura: int = Field(default=0, ge=0)
    folio_inicial_remision: int = Field(default=0, ge=0)


class SerieUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, max_length=120)
    activa: Optional[bool] = None
    es_default: Optional[bool] = None
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    notas: Optional[str] = None
    folio_actual: Optional[int] = Field(default=None, ge=0)


class SerieOut(ORMModel, SerieBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    folio_actual: int
    created_at: datetime
    updated_at: datetime


class SerieFolioSugerido(BaseModel):
    """En qué folio dejar la serie al cortar un cliente de SAE.

    `sugerido` es None cuando la serie no tiene facturas espejo: no hay dato de
    SAE con el cual proponer nada y mover el contador a ciegas rompería la
    consecutividad.
    """
    serie: str
    folio_actual: int
    folio_espejo_max: Optional[int]
    sugerido: Optional[int]
    facturas_espejo: int

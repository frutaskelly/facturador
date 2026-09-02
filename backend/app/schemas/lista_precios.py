"""Listas de precios, sus renglones y a qué aplican."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

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
    # Espejo de SAE: empresa de Aspel ("02", "03") + número de lista
    # (CVE_PRECIO). Ambos o ninguno — el conector solo escribe en listas que
    # declaran su origen completo.
    sae_empresa: Optional[str] = Field(default=None, max_length=4)
    sae_lista: Optional[int] = Field(default=None, ge=1, le=10)


class ListaPreciosCreate(ListaPreciosBase):
    @model_validator(mode="after")
    def _vinculo_sae_completo(self):
        if (self.sae_empresa is None) != (self.sae_lista is None):
            raise ValueError(
                "El vínculo con SAE lleva empresa Y número de lista; "
                "deja ambos vacíos para una lista manual."
            )
        return self


class ListaPreciosUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=20)
    nombre: Optional[str] = Field(default=None, max_length=254)
    status: Optional[str] = Field(default=None, max_length=20)
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    moneda: Optional[str] = Field(default=None, max_length=3)
    notas: Optional[str] = None
    es_default: Optional[bool] = None
    sae_empresa: Optional[str] = Field(default=None, max_length=4)
    sae_lista: Optional[int] = Field(default=None, ge=1, le=10)


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


# ─── espejo de precios SAE (lo usa el conector, no la UI) ────────────────────
class ListaVinculadaOut(BaseModel):
    """Una lista que declara su origen en SAE — lo único que el conector
    necesita para saber qué consultar en PRECIO_X_PROD."""
    id: uuid.UUID
    codigo: str
    nombre: str
    sae_empresa: str
    sae_lista: int


class EspejoPrecioItem(BaseModel):
    clave: str = Field(min_length=1, max_length=60)  # CVE_ART tal cual (RTRIM)
    precio: Decimal = Field(ge=0)
    unidad: Optional[str] = Field(default=None, max_length=20)  # UNI_MED de INVE


class EspejoPreciosIn(BaseModel):
    lista_id: uuid.UUID
    precios: List[EspejoPrecioItem] = Field(max_length=10000)


class EspejoPreciosResult(BaseModel):
    recibidos: int
    creados: int
    actualizados: int
    sin_cambio: int
    # Renglones que NO se escribieron y por qué — el conector los reporta para
    # que la corrida deje rastro de lo que falta cruzar, no para fallar.
    en_cero: int
    sin_cruce: List[str]
    sin_presentacion: List[str]


# ─── Asignación de la lista (wizard de importación / administración) ─────────
class ListaAsignarIn(BaseModel):
    """Asignar la lista como default del negocio y/o a clientes específicos."""
    default: bool = False
    cliente_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


class ListaAsignarOut(BaseModel):
    default: bool
    clientes_asignados: int


# ─── A qué aplica una lista: cliente, sucursal, serie y/o proyecto ──────────
class ListaAsignacionBase(BaseModel):
    """Cada campo en None es un COMODÍN: "aplica a cualquiera".

    Sólo cliente → el mismo precio en todo el país. Cliente + sucursal → la
    plaza. + serie o + proyecto → la negociación concreta.
    """
    lista_id: uuid.UUID
    cliente_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    serie_id: Optional[uuid.UUID] = None
    proyecto_id: Optional[uuid.UUID] = None
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    notas: Optional[str] = None


class ListaAsignacionCreate(ListaAsignacionBase):
    @model_validator(mode="after")
    def _alguna_dimension(self):
        if not any((self.cliente_id, self.sucursal_id, self.serie_id, self.proyecto_id)):
            raise ValueError(
                "Elige al menos cliente, sucursal, serie o proyecto. "
                "Para la lista base del negocio usa «lista predeterminada»."
            )
        return self


class ListaAsignacionUpdate(BaseModel):
    lista_id: Optional[uuid.UUID] = None
    vigencia_desde: Optional[date] = None
    vigencia_hasta: Optional[date] = None
    notas: Optional[str] = None


class ListaAsignacionOut(ORMModel, ListaAsignacionBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    # Suma de pesos de las dimensiones llenas (la calcula Postgres). Es
    # literalmente el orden de prioridad, y por eso se muestra en pantalla.
    especificidad: int
    created_at: datetime
    updated_at: datetime
    # Nombres ya resueltos: la tabla se lee sin que el navegador cruce cinco
    # catálogos para pintar un renglón.
    lista_nombre: Optional[str] = None
    cliente_nombre: Optional[str] = None
    sucursal_nombre: Optional[str] = None
    serie_codigo: Optional[str] = None
    proyecto_nombre: Optional[str] = None

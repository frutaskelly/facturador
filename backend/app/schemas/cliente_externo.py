"""Equivalencias de cliente — schemas."""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

Sistema = Literal["RFC", "SAE", "PROYECTO", "NOMBRE", "UBICACION", "WHATSAPP"]
Origen = Literal["MANUAL", "BOT", "IMPORT", "IA"]
Confianza = Literal["CONFIRMADA", "SUGERIDA"]


class ClienteExternoCreate(BaseModel):
    sistema: Sistema
    clave: str = Field(min_length=1, max_length=254)
    cliente_id: uuid.UUID
    sucursal_id: Optional[uuid.UUID] = None
    # Solo tienen sentido en una equivalencia de grupo (WHATSAPP): la serie que
    # usa ESE grupo para ESE cliente. Vacío = hereda la del cliente.
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    origen: Origen = "MANUAL"
    confianza: Confianza = "CONFIRMADA"
    notas: Optional[str] = None


class ClienteExternoOut(ORMModel):
    id: uuid.UUID
    sistema: str
    clave: str
    clave_normalizada: str
    cliente_id: uuid.UUID
    sucursal_id: Optional[uuid.UUID] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    origen: str
    confianza: str
    notas: Optional[str] = None
    created_at: datetime


class PistaIn(BaseModel):
    sistema: Sistema
    clave: str = Field(min_length=1, max_length=254)


class ResolverIn(BaseModel):
    """Las pistas leídas de un documento. Todas opcionales: se manda lo que haya."""
    pistas: list[PistaIn] = Field(default_factory=list)
    # Texto crudo de la ubicación/bodega, para cruzar contra las sucursales del
    # cliente cuando ninguna equivalencia UBICACION lo resolvió.
    ubicacion_texto: Optional[str] = None


class CoincidenciaOut(BaseModel):
    sistema: str
    clave: str
    cliente_id: uuid.UUID
    sucursal_id: Optional[uuid.UUID] = None


class ResolucionOut(BaseModel):
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None
    sucursal_nombre: Optional[str] = None
    via: Optional[str] = None
    ambiguo: bool = False
    motivo: Optional[str] = None
    coincidencias: list[CoincidenciaOut] = Field(default_factory=list)

"""Bandeja de órdenes de compra — schemas."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

Canal = Literal["WHATSAPP", "EMAIL", "MANUAL", "API"]
EstadoOC = Literal["PENDIENTE", "ASIGNADA", "DESCARTADA"]


class LineaOCRecibidaIn(BaseModel):
    """Una partida tal como venía en el documento, sin cruzar todavía."""
    descripcion: str = Field(min_length=1, max_length=500)
    cantidad: Decimal = Field(gt=0)
    unidad: Optional[str] = Field(default=None, max_length=20)
    clave: Optional[str] = Field(default=None, max_length=60)   # clave del cliente / SAE
    precio: Optional[Decimal] = Field(default=None, ge=0)
    notas: Optional[str] = None


class OCRecibidaIn(BaseModel):
    """Ingesta desde el bot: la OC ya parseada + de dónde vino.

    `origen_externo` es la llave de idempotencia — el bot manda siempre la misma
    para la misma orden ('WA:<jid>:<folio>'), así un reintento actualiza en vez
    de duplicar.
    """
    canal: Canal
    origen_externo: str = Field(min_length=1, max_length=120)
    folio_externo: Optional[str] = Field(default=None, max_length=60)
    remitente: Optional[str] = Field(default=None, max_length=254)
    archivo_nombre: Optional[str] = Field(default=None, max_length=254)
    archivo_url: Optional[str] = None
    fecha: Optional[date] = None
    fecha_entrega: Optional[date] = None
    observaciones: Optional[str] = None

    # Pistas para resolver el cliente (todas opcionales; se manda lo que haya).
    rfc: Optional[str] = Field(default=None, max_length=20)
    nombre: Optional[str] = Field(default=None, max_length=254)
    proyecto: Optional[str] = Field(default=None, max_length=100)
    ubicacion: Optional[str] = Field(default=None, max_length=254)
    clave_sae: Optional[str] = Field(default=None, max_length=60)
    jid: Optional[str] = Field(default=None, max_length=120)
    perfil: Optional[str] = Field(default=None, max_length=40)

    lineas: list[LineaOCRecibidaIn] = Field(default_factory=list)


class OCRecibidaUpdate(BaseModel):
    """Corrección manual desde la bandeja."""
    cliente_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None
    folio_externo: Optional[str] = Field(default=None, max_length=60)
    punto_entrega: Optional[str] = Field(default=None, max_length=254)
    motivo: Optional[str] = None
    # Guardar la corrección como equivalencia: la próxima OC igual ya no pregunta.
    aprender: bool = True


class CandidatoLineaOut(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    score: int
    origen: str
    # Necesarias para traducir la UNIDAD de la orden ("CAJA", "KG") a la
    # presentación del producto. Sin esto toda partida entraría como KILO y una
    # OC de 5 CAJA registraría 5 kg en vez de 100.
    presentaciones: dict = Field(default_factory=dict)
    presentacion_default: Optional[str] = None


class LineaOCRecibidaOut(BaseModel):
    numero: int
    descripcion: str
    cantidad: Decimal
    unidad: Optional[str] = None
    clave: Optional[str] = None
    precio: Optional[Decimal] = None
    notas: Optional[str] = None
    candidatos: list[CandidatoLineaOut] = Field(default_factory=list)


class OCRecibidaOut(ORMModel):
    id: uuid.UUID
    canal: str
    origen_externo: str
    folio_externo: Optional[str] = None
    remitente: Optional[str] = None
    archivo_nombre: Optional[str] = None
    archivo_url: Optional[str] = None
    recibida_at: datetime
    estado: str
    motivo: Optional[str] = None
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None
    sucursal_nombre: Optional[str] = None
    resuelto_via: Optional[str] = None
    punto_entrega: Optional[str] = None
    ambiguo: bool = False
    remision_id: Optional[uuid.UUID] = None
    remision_folio: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class OCRecibidaDetailOut(OCRecibidaOut):
    payload: dict = Field(default_factory=dict)
    lineas: list[LineaOCRecibidaOut] = Field(default_factory=list)


class LineaCrearIn(BaseModel):
    """Una partida ya resuelta a producto, lista para volverse línea de remisión."""
    producto_id: uuid.UUID
    cantidad: Decimal = Field(gt=0)
    presentacion: str = Field(default="KILO", max_length=20)
    precio_unitario: Optional[Decimal] = Field(default=None, ge=0)
    notas: Optional[str] = None
    # Texto original de la partida: si viene, se aprende como alias del producto.
    texto_original: Optional[str] = Field(default=None, max_length=254)


class CrearRemisionIn(BaseModel):
    almacen_id: Optional[uuid.UUID] = None
    fecha_remision: Optional[date] = None
    fecha_entrega: Optional[date] = None
    lineas: list[LineaCrearIn] = Field(min_length=1)

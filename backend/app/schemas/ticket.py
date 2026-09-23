from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import ORMModel


class TicketIn(BaseModel):
    """Lo que manda el bot. Idempotente por `origen_externo`: se re-manda cada
    vez que el caso cambia (se abre, se resuelve, se cierra)."""

    numero: int = Field(ge=1)
    origen_externo: str = Field(min_length=1, max_length=160)
    canal: str = Field(default="WHATSAPP", max_length=20)
    estado: Literal["ABIERTO", "RESUELTO", "CERRADO"] = "ABIERTO"
    perfil: Optional[str] = Field(default=None, max_length=40)
    grupo: Optional[str] = Field(default=None, max_length=160)
    jid: Optional[str] = Field(default=None, max_length=120)
    remitente: Optional[str] = Field(default=None, max_length=160)
    archivo_nombre: Optional[str] = Field(default=None, max_length=254)
    nota: Optional[str] = Field(default=None, max_length=2000)
    tipo: Optional[str] = Field(default=None, max_length=40)
    que_paso: Optional[str] = Field(default=None, max_length=4000)
    acciones: list[Literal["EXTRA", "CERRAR"]] = Field(default_factory=list)
    resolucion: Optional[str] = Field(default=None, max_length=1000)
    resuelto_por: Optional[str] = Field(default=None, max_length=160)
    # base64 de la foto; ~6 MB de texto = ~4.5 MB de imagen, sobra para un JPEG
    # de WhatsApp. Si no viene, se conserva la que ya estaba.
    foto_b64: Optional[str] = Field(default=None, max_length=6_000_000)
    foto_mime: Optional[str] = Field(default=None, max_length=40)
    evento: Optional[str] = Field(default=None, max_length=1000)


class TicketAccionIn(BaseModel):
    accion: Literal["EXTRA", "CERRAR"]
    nota: Optional[str] = Field(default=None, max_length=1000)


class TicketComentarioIn(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)


class TicketAckIn(BaseModel):
    """El bot ya ejecutó (o no pudo ejecutar) la acción pedida desde aquí."""

    ok: bool
    detalle: Optional[str] = Field(default=None, max_length=1000)


class TicketOut(ORMModel):
    id: UUID
    numero: int
    canal: str
    estado: str
    perfil: Optional[str] = None
    grupo: Optional[str] = None
    remitente: Optional[str] = None
    archivo_nombre: Optional[str] = None
    nota: Optional[str] = None
    tipo: Optional[str] = None
    que_paso: Optional[str] = None
    acciones: list[str] = []
    tiene_foto: bool = False
    accion_pedida: Optional[str] = None
    accion_pedida_por: Optional[str] = None
    accion_pedida_at: Optional[datetime] = None
    accion_tomada_at: Optional[datetime] = None
    resolucion: Optional[str] = None
    resuelto_at: Optional[datetime] = None
    resuelto_por: Optional[str] = None
    recibido_at: datetime
    updated_at: datetime


class TicketDetailOut(TicketOut):
    eventos: list[dict[str, Any]] = []


class TicketAccionPendienteOut(BaseModel):
    """Lo que el bot reclama: qué ticket, qué hacer, quién lo pidió."""

    id: UUID
    numero: int
    origen_externo: str
    archivo_nombre: Optional[str] = None
    accion: str
    pedida_por: Optional[str] = None
    nota: Optional[str] = None


class TicketResumenOut(BaseModel):
    abiertos: int

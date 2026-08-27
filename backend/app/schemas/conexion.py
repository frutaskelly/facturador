"""Conexiones — schemas."""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import ORMModel

TipoConexion = Literal["SMART_SUPPLY"]
EstadoConexion = Literal["PENDIENTE", "ACTIVA", "REVOCADA"]


class ConexionOut(ORMModel):
    id: uuid.UUID
    tipo: str
    nombre: str
    clave_pista: str            # últimos 4 caracteres, para nombrarla en pantalla
    estado: str
    created_at: datetime
    activada_at: Optional[datetime] = None
    ultimo_uso_at: Optional[datetime] = None


class ConexionEstadoOut(BaseModel):
    """Lo que la pantalla necesita para responder «¿está entrando lo que debe?»."""
    tipo: str
    nombre: str
    conexion: Optional[ConexionOut] = None
    # Actividad real, no configuración.
    ordenes_hoy: int = 0
    ordenes_sin_resolver: int = 0
    ultima_orden_at: Optional[datetime] = None
    # Sin vencimiento (decisión del dueño): en vez de caducar, se avisa.
    conviene_rotar: bool = False
    dias_desde_creacion: Optional[int] = None


class ClaveNuevaOut(BaseModel):
    """La clave en claro. Se devuelve UNA vez y no se vuelve a poder leer."""
    clave: str
    conexion: ConexionOut
    # El comando exacto que hay que mandar por WhatsApp, ya armado.
    instruccion_whatsapp: str


class ActividadConexionOut(BaseModel):
    recibida_at: datetime
    folio_externo: Optional[str] = None
    remitente: Optional[str] = None
    cliente_nombre: Optional[str] = None
    estado: str
    partidas: int = 0


class PruebaOut(BaseModel):
    ok: bool
    mensaje: str
    tenant: Optional[str] = None
    permisos: list[str] = Field(default_factory=list)

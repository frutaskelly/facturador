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


class GrupoIn(BaseModel):
    """Un grupo tal como lo reporta el bot desde su config."""
    jid: str = Field(min_length=1, max_length=120)
    nombre: Optional[str] = Field(default=None, max_length=254)
    rol: Optional[str] = Field(default=None, max_length=12)      # interno | cliente
    perfil: Optional[str] = Field(default=None, max_length=40)
    activo: bool = True
    config: dict = Field(default_factory=dict)


class SincronizarGruposIn(BaseModel):
    grupos: list[GrupoIn] = Field(default_factory=list)


class SucursalBreve(BaseModel):
    id: uuid.UUID
    nombre: str


class ClienteDelGrupoOut(BaseModel):
    """Un cliente que recibe órdenes por ese grupo, con lo suyo."""
    # Id de la equivalencia, para poder desconectarlo desde la pantalla.
    externo_id: Optional[uuid.UUID] = None
    cliente_id: uuid.UUID
    nombre: str
    serie_factura: Optional[str] = None
    serie_remision: Optional[str] = None
    sucursales: list[SucursalBreve] = Field(default_factory=list)
    # La sucursal POR DEFECTO de este grupo para este cliente (última red del
    # destino cuando el punto de entrega no resuelve nada).
    sucursal_grupo_id: Optional[uuid.UUID] = None
    almacen: Optional[str] = None
    serie_factura_id: Optional[uuid.UUID] = None
    serie_remision_id: Optional[uuid.UUID] = None
    almacen_id: Optional[uuid.UUID] = None
    # Si es candidato del grupo o solo se le han asignado órdenes de ahí.
    registrado: bool = True


class GrupoOut(BaseModel):
    jid: str
    nombre: Optional[str] = None
    rol: Optional[str] = None            # interno | cliente
    perfil: Optional[str] = None
    activo: bool = True                  # lo que decidió el dueño aquí
    reportado_activo: bool = True        # lo que dice la config del bot
    clientes: list[ClienteDelGrupoOut] = Field(default_factory=list)
    # Resumen de lo que ha entrado por ahí.
    ordenes: int = 0
    ordenes_24h: int = 0
    ultima_orden_at: Optional[datetime] = None
    sin_resolver: int = 0
    sincronizado_at: Optional[datetime] = None


class GrupoUpdate(BaseModel):
    """Lo único que el dueño decide aquí: si el grupo entra o no."""
    activo: bool

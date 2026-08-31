"""Bandeja de órdenes de compra — schemas."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

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
    proyecto_id: Optional[uuid.UUID] = None
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
    # La unidad del documento ya traducida a presentación del catálogo
    # ("KILOGR AMO" → KILO); si el documento no dice, la habitual del cliente
    # para el producto sugerido. None = no se reconoció: que decida el humano.
    presentacion_sugerida: Optional[str] = None
    # True cuando la de arriba NO salió del documento sino de lo habitual del
    # cliente o del producto. El carril automático no acepta una adivinanza en
    # productos con varias presentaciones: el factor cambia cantidad y precio.
    presentacion_adivinada: bool = False
    candidatos: list[CandidatoLineaOut] = Field(default_factory=list)


class LineaAutoOut(BaseModel):
    """Una partida ya resuelta por vía determinista, lista para el clic."""
    numero: int
    producto_id: uuid.UUID
    nombre: str
    presentacion: str
    cantidad: Decimal
    precio_unitario: Decimal
    precio_origen: str
    texto_original: Optional[str] = None
    clave: Optional[str] = None
    cruzo_por: str


class GrupoBandejaOut(BaseModel):
    """Un ORIGEN para el filtro de la bandeja, con sus clientes para encadenar
    los demás filtros. Dos mundos conviven: los grupos de WhatsApp (tipo
    "grupo", se filtra por su jid) y lo que entra por la conexión de Smart
    Supply, que no trae jid — ahí el origen es el REMITENTE (tipo "remitente",
    se filtra por el texto exacto, p. ej. «EHMO villahermosa»)."""
    tipo: str                       # "grupo" | "remitente"
    clave: str                      # el jid, o el texto del remitente
    nombre: Optional[str] = None
    activo: bool = True
    cliente_ids: list[uuid.UUID] = Field(default_factory=list)


class ProblemaLineaOut(BaseModel):
    """Lo que le impide a UNA partida entrar sola, con su número para poder
    señalarla en la tabla en vez de dejar el aviso suelto arriba."""
    numero: int
    tipo: str            # sin_cruce | ambiguo | unidad | sin_precio | precio_base | precio_conflicto
    mensaje: str
    # Solo en `precio_conflicto`: las dos cifras que no coinciden y de dónde
    # sale la de la casa, para poder ofrecer «cobra esta» sin salir de la tabla.
    precio_documento: Optional[str] = None
    precio_lista: Optional[str] = None
    fuente_precio: Optional[str] = None


class AutoRemisionOut(BaseModel):
    """¿La orden entera puede volverse remisión con un clic? Y con qué líneas.

    `ok` solo cuando TODAS las partidas cruzaron por clave del cliente, alias o
    exacto, con unidad vendible y precio de lista negociada (no lista base)."""
    ok: bool
    motivo: Optional[str] = None
    lineas: list[LineaAutoOut] = Field(default_factory=list)
    problemas: list[ProblemaLineaOut] = Field(default_factory=list)


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
    proyecto_id: Optional[uuid.UUID] = None
    proyecto_nombre: Optional[str] = None
    # Lo que el documento decía fuera de las partidas ("entregar antes de las
    # 9", la referencia). Sale del payload; la lista lo enseña en su columna.
    observaciones: Optional[str] = None
    # Clientes posibles según el grupo del que llegó, cuando el documento no
    # alcanza a decidir. Vacío = no hay pista de grupo o ya se resolvió.
    candidatos: list[uuid.UUID] = Field(default_factory=list)
    ambiguo: bool = False
    remision_id: Optional[uuid.UUID] = None
    remision_folio: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("candidatos", mode="before")
    @classmethod
    def _lista(cls, v):
        """La columna es NULL mientras nadie haya calculado candidatos; para
        quien consume la API eso es simplemente una lista vacía."""
        return v or []


class OCRecibidaDetailOut(OCRecibidaOut):
    payload: dict = Field(default_factory=dict)
    lineas: list[LineaOCRecibidaOut] = Field(default_factory=list)
    auto: Optional[AutoRemisionOut] = None
    # La serie con la que se foliaria la remisión de esta orden; la pantalla
    # cotiza los precios con ella (una asignación por serie pesa más que
    # sucursal+cliente). None en modo vistazo o sin cliente.
    serie_prevista_id: Optional[uuid.UUID] = None


class LineaCrearIn(BaseModel):
    """Una partida ya resuelta a producto, lista para volverse línea de remisión."""
    producto_id: uuid.UUID
    cantidad: Decimal = Field(gt=0)
    presentacion: str = Field(default="KILO", max_length=20)
    precio_unitario: Optional[Decimal] = Field(default=None, ge=0)
    notas: Optional[str] = None
    # Texto original de la partida: si viene, se aprende como alias del producto.
    texto_original: Optional[str] = Field(default=None, max_length=254)
    # Clave del documento: si viene y el producto no tiene código para este
    # cliente, se registra en su catálogo — la próxima OC cruza al 100 por clave.
    clave: Optional[str] = Field(default=None, max_length=60)


class CrearRemisionIn(BaseModel):
    almacen_id: Optional[uuid.UUID] = None
    fecha_remision: Optional[date] = None
    fecha_entrega: Optional[date] = None
    lineas: list[LineaCrearIn] = Field(min_length=1)

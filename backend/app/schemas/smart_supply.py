"""Panel de Smart Supply — lo que lee la clave de una cuenta (solo lectura).

Las cantidades y los importes viajan como TEXTO decimal (sin pérdida por float)
y los importes sin IVA. Las fechas son de México. Cada lista se pagina por su
llave estable: la respuesta trae `siguiente` y la página que sigue se pide con
`despues=<siguiente>`; `siguiente` en null es la última página.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T")


class Pagina(BaseModel, Generic[T]):
    items: list[T]
    limit: int
    # Cursor opaco para pedir la página que sigue (`despues=`). Null = ya no hay más.
    siguiente: Optional[str] = None


# ─── alcance ─────────────────────────────────────────────────────────────────

class EmpresaPanelOut(BaseModel):
    id: UUID
    nombre: str


class ConexionPanelBreveOut(BaseModel):
    id: UUID
    nombre: str
    pista: str                     # últimos 4 de la clave, para nombrarla


class PlazaAlcanceOut(BaseModel):
    nombre: str
    series: list[str]
    series_remision: list[str]
    perfiles: list[str]


class AlcancePanelOut(BaseModel):
    """Lo que comparte esta clave. Smart Supply lo pide al guardar la clave y
    antes de cada sincronización: si el dueño le quita algo, se entera aquí."""
    empresa: EmpresaPanelOut
    conexion: Optional[ConexionPanelBreveOut] = None   # None = una persona (prueba)
    sin_limite: bool = False                           # persona: lee todo
    plaza: Optional[str] = None
    series: list[str]
    series_remision: list[str]
    perfiles: list[str]
    remisiones: bool
    oc: bool
    catalogo: bool
    plazas: list[PlazaAlcanceOut] = Field(default_factory=list)
    zona_horaria: str = "America/Mexico_City"
    max_dias: int
    max_limit: int


# ─── OC ──────────────────────────────────────────────────────────────────────

class RemisionDeOCOut(BaseModel):
    id: UUID
    folio: str
    serie: Optional[str] = None
    estado: str
    fecha_entrega: Optional[date] = None
    total: str


class FacturaDeOCOut(BaseModel):
    id: UUID
    serie: str
    folio: int
    uuid: Optional[str] = None
    estado: str
    origen: str                    # NATIVA | ESPEJO_SAE


class OCPanelOut(BaseModel):
    """Una orden de compra con su cadena: OC → remisión → factura."""
    id: UUID
    canal: str                     # WHATSAPP | EMAIL | MANUAL | API
    origen_externo: str
    perfil: Optional[str] = None   # «EHMO:villahermosa»: origen_externo sin el folio
    folio_externo: Optional[str] = None
    remitente: Optional[str] = None
    archivo_nombre: Optional[str] = None
    recibida_at: datetime
    fecha_entrega: Optional[date] = None   # la de la OC o, si no la trae, la de su remisión
    estado: str                    # PENDIENTE | ASIGNADA | DESCARTADA
    motivo: Optional[str] = None
    cliente_id: Optional[UUID] = None
    cliente: Optional[str] = None
    plaza: Optional[str] = None
    punto_entrega: Optional[str] = None
    proyecto: Optional[str] = None
    partidas: int                  # del documento vigente
    documento: Literal["payload", "payload_nuevo"]
    cambio_abierto: bool
    cambio_resumen: Optional[str] = None
    remision: Optional[RemisionDeOCOut] = None
    factura: Optional[FacturaDeOCOut] = None
    # greatest(OC, remisión, factura).updated_at: la llave del sondeo incremental.
    actualizado_at: datetime


class OCLineaOut(BaseModel):
    """Una partida del documento VIGENTE de la OC, tal como la pidió el cliente
    (sin cruzar a producto: el cruce es la remisión)."""
    oc_id: UUID
    numero: int                    # posición en el documento vigente (1…n)
    documento: Literal["payload", "payload_nuevo"]
    cambio_abierto: bool
    perfil: Optional[str] = None
    folio_externo: Optional[str] = None
    fecha_entrega: date
    estado: str                    # PENDIENTE | ASIGNADA
    cliente_id: Optional[UUID] = None
    plaza: Optional[str] = None
    remision_id: Optional[UUID] = None
    remision_folio: Optional[str] = None
    remision_estado: Optional[str] = None
    clave_doc: Optional[str] = None     # como venía
    clave: Optional[str] = None         # normalizada (mayúsculas, sin acentos): la de SAE
    descripcion: Optional[str] = None
    unidad_doc: Optional[str] = None    # texto del cliente
    cantidad: Optional[str] = None      # None si el documento no trae un número


# ─── remisionado ─────────────────────────────────────────────────────────────

class RemisionadoOut(BaseModel):
    remision_id: UUID
    numero_linea: int
    folio: str
    serie: Optional[str] = None
    estado: str
    fecha_entrega: date
    factura_id: Optional[UUID] = None
    facturada: bool                # su factura está TIMBRADA
    su_pedido: Optional[str] = None
    cliente_id: UUID
    cliente: Optional[str] = None
    plaza: Optional[str] = None
    punto_entrega: Optional[str] = None
    producto_id: UUID
    sku: str
    producto: str
    clave: Optional[str] = None          # SAE de la PRESENTACIÓN (o la del producto)
    clave_producto: Optional[str] = None
    presentacion: str
    cantidad_solicitada: str
    cantidad_surtida: Optional[str] = None
    cantidad: str                  # la que cuenta: el peso real si es de peso variable
    unidad: str                    # en qué viene `cantidad`
    factor_kg: Optional[str] = None
    kg: Optional[str] = None       # None = no hay factor: no se inventa el peso
    kg_estimado: bool = False      # el factor de la presentación es un estimado
    precio_unitario: str
    importe: str                   # sin IVA, con el descuento del encabezado prorrateado


# ─── facturado ───────────────────────────────────────────────────────────────

class FacturadoOut(BaseModel):
    factura_id: UUID
    numero_linea: int
    origen: str                    # NATIVA | ESPEJO_SAE
    espejo_empresa: Optional[str] = None
    serie: str
    folio: int
    uuid: Optional[str] = None
    fecha_factura: date
    fecha_entrega: date
    fecha_entrega_origen: Literal["remision", "notas", "factura"]
    cliente_id: Optional[UUID] = None
    cliente: Optional[str] = None
    su_pedido: Optional[str] = None
    remision_ids: list[UUID] = Field(default_factory=list)
    # Tal como lo guardó la línea (el espejo lo pone por clave desde #313): no
    # se recalcula al leer. None = la partida no cruzó con ningún producto.
    producto_id: Optional[UUID] = None
    sku: Optional[str] = None
    producto: Optional[str] = None
    clave_sae: Optional[str] = None
    descripcion: str
    presentacion: Optional[str] = None
    clave_unidad: str              # unidad SAT de la línea
    unidad: Optional[str] = None   # la presentación, o KILO si la unidad SAT es KGM
    cantidad: str
    factor_kg: Optional[str] = None
    kg: Optional[str] = None
    kg_estimado: bool = False
    importe: str                   # importe − descuento, sin IVA


# ─── catálogo ────────────────────────────────────────────────────────────────

class PresentacionOut(BaseModel):
    factor: Optional[str] = None   # unidades base por presentación
    clave_sae: Optional[str] = None
    sat: Optional[str] = None
    estimado: bool = False


class ProductoPanelOut(BaseModel):
    id: UUID
    sku: str
    nombre: str
    categoria: Optional[str] = None
    unidad_base: str
    unidad_sat: str
    peso_variable: bool
    activo: bool
    clave_sae: Optional[str] = None
    presentaciones: dict[str, PresentacionOut]

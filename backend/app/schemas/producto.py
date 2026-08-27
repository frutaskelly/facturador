"""Product schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class ProductoBase(BaseModel):
    sku: str = Field(max_length=50)
    nombre: str = Field(max_length=254)
    descripcion: Optional[str] = None
    categoria_id: Optional[uuid.UUID] = None
    esquema_impuesto_id: Optional[uuid.UUID] = None
    # SAT / CFDI 4.0
    clave_sat: str = Field(max_length=8)
    unidad_sat: str = Field(max_length=3)
    objeto_imp: str = Field(default="02", max_length=2)
    iva_tasa: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    ieps_tasa: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    # units / presentations
    unidad_base: str = Field(default="KILO", max_length=20)
    presentaciones: dict = Field(default_factory=lambda: {"KILO": 1})
    presentacion_default: Optional[str] = Field(default="KILO", max_length=20)
    unidad_entrada: Optional[str] = Field(default=None, max_length=20)
    unidad_salida: Optional[str] = Field(default=None, max_length=20)
    peso_variable: bool = False
    codigo_barras: Optional[str] = Field(default=None, max_length=20)
    contenido_litros: Optional[Decimal] = Field(default=None, ge=0)
    # inventory attributes
    perecedero: bool = False
    cold_chain: bool = False
    requiere_lote: bool = False
    requiere_caducidad: bool = False
    vida_util_dias: Optional[int] = Field(default=None, ge=0)
    sinonimos: list[str] = Field(default_factory=list)
    activo: bool = True
    custom_fields: dict = Field(default_factory=dict)


class ProductoCreate(ProductoBase):
    # SKU is optional on create — leave blank to auto-generate an 8-digit code.
    sku: Optional[str] = Field(default=None, max_length=50)


class ProductoUpdate(BaseModel):
    sku: Optional[str] = Field(default=None, max_length=50)
    nombre: Optional[str] = Field(default=None, max_length=254)
    descripcion: Optional[str] = None
    categoria_id: Optional[uuid.UUID] = None
    esquema_impuesto_id: Optional[uuid.UUID] = None
    clave_sat: Optional[str] = Field(default=None, max_length=8)
    unidad_sat: Optional[str] = Field(default=None, max_length=3)
    objeto_imp: Optional[str] = Field(default=None, max_length=2)
    iva_tasa: Optional[Decimal] = Field(default=None, ge=0, le=1)
    ieps_tasa: Optional[Decimal] = Field(default=None, ge=0, le=1)
    unidad_base: Optional[str] = Field(default=None, max_length=20)
    presentaciones: Optional[dict] = None
    presentacion_default: Optional[str] = Field(default=None, max_length=20)
    unidad_entrada: Optional[str] = Field(default=None, max_length=20)
    unidad_salida: Optional[str] = Field(default=None, max_length=20)
    peso_variable: Optional[bool] = None
    codigo_barras: Optional[str] = Field(default=None, max_length=20)
    contenido_litros: Optional[Decimal] = Field(default=None, ge=0)
    perecedero: Optional[bool] = None
    cold_chain: Optional[bool] = None
    requiere_lote: Optional[bool] = None
    requiere_caducidad: Optional[bool] = None
    vida_util_dias: Optional[int] = Field(default=None, ge=0)
    sinonimos: Optional[list[str]] = None
    activo: Optional[bool] = None
    custom_fields: Optional[dict] = None


class ProductoOut(ORMModel, ProductoBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ─── Cruce de productos (match / alias aprendidos) ───────────────────────────
class MatchIn(BaseModel):
    textos: list[Annotated[str, Field(max_length=500)]] = Field(min_length=1, max_length=200)
    usar_ia: bool = False         # complementa con IA los textos sin buen candidato
    limit: int = Field(default=5, ge=1, le=20)


class CandidatoOut(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    score: int
    origen: str                   # exacto | alias | difuso | ia
    presentaciones: dict = {}
    presentacion_default: Optional[str] = None
    unidad_base: Optional[str] = None


class MatchResultOut(BaseModel):
    texto: str
    candidatos: list[CandidatoOut]


class ParsePegadoIn(BaseModel):
    texto: str = Field(min_length=1, max_length=20000)
    usar_ia: bool = True          # IA para detectar columnas fuera de orden / encabezados


class LineaPegadaOut(BaseModel):
    texto: str                    # producto tal como se pegó
    cantidad: str
    precio: str                   # '' si no venía
    presentacion: str             # '' si no venía
    candidatos: list[CandidatoOut]


class AliasIn(BaseModel):
    texto: str = Field(min_length=1, max_length=254)
    producto_id: uuid.UUID


# ─── Importación masiva (plantilla o lista de precios con IA) ────────────────
class ImportFilaPreview(BaseModel):
    fila: int
    nombre: str
    codigo: str = ""
    descripcion: str = ""
    unidad: str = ""
    precio: str = ""
    clave_sat: str = ""
    unidad_sat: str = ""
    codigo_barras: str = ""
    # Mejor candidato del cruce (≥ score de confianza) — sugerencia "vincular".
    producto_id: Optional[uuid.UUID] = None
    candidatos: list[CandidatoOut] = Field(default_factory=list)
    # El cliente elegido ya tiene código/nombre guardado para ese producto.
    ya_vinculado: bool = False
    # El archivo repite este producto (mismo nombre o código): fila original.
    duplicada_de: Optional[int] = None
    # La repetición trae OTRO precio que la fila original — conflicto a revisar.
    precio_distinto: bool = False


class ImportPreviewOut(BaseModel):
    formato: str                  # "plantilla" (determinista) | "ia"
    filas: list[ImportFilaPreview]


class ImportFilaIn(BaseModel):
    accion: str = Field(pattern="^(crear|vincular|omitir)$")
    producto_id: Optional[uuid.UUID] = None      # requerido para "vincular"
    nombre: str = Field(min_length=1, max_length=254)
    sku: Optional[str] = Field(default=None, max_length=50)
    descripcion: Optional[str] = Field(default=None, max_length=1000)
    unidad_base: Optional[str] = Field(default=None, max_length=20)
    clave_sat: Optional[str] = Field(default=None, max_length=8)
    unidad_sat: Optional[str] = Field(default=None, max_length=3)
    codigo_barras: Optional[str] = Field(default=None, max_length=20)
    categoria_id: Optional[uuid.UUID] = None
    esquema_impuesto_id: Optional[uuid.UUID] = None
    # Solo cuando la importación es la lista de un cliente:
    codigo_cliente: Optional[str] = Field(default=None, max_length=50)
    nombre_cliente: Optional[str] = Field(default=None, max_length=254)
    precio: Optional[Decimal] = Field(default=None, ge=0)


class ImportIn(BaseModel):
    cliente_id: Optional[uuid.UUID] = None
    guardar_precios: bool = False
    lista_id: Optional[uuid.UUID] = None          # default: la lista del cliente
    filas: list[ImportFilaIn] = Field(min_length=1, max_length=2000)


class ImportErrorFila(BaseModel):
    fila: int
    error: str


class ImportResultOut(BaseModel):
    creados: int
    vinculados: int
    alias_guardados: int
    precios_guardados: int
    omitidos: int
    errores: list[ImportErrorFila] = Field(default_factory=list)


# ─── Catálogo del cliente (codigo/nombre por cliente → CFDI) ─────────────────
class ProductoClienteOut(BaseModel):
    producto_id: uuid.UUID
    producto_sku: str
    producto_nombre: str
    codigo_cliente: Optional[str] = None
    nombre_cliente: Optional[str] = None


class ProductoClienteUpsert(BaseModel):
    codigo_cliente: Optional[str] = Field(default=None, max_length=50)
    nombre_cliente: Optional[str] = Field(default=None, max_length=254)

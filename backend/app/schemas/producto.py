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
    categoria: str = ""
    esquema: str = ""
    # ESTATUS BAJA del archivo (SAE): se omite por default, reversible en la UI.
    baja: bool = False
    # Validación contra el catálogo SAT oficial (None = campo vacío).
    clave_sat_valida: Optional[bool] = None
    unidad_sat_valida: Optional[bool] = None
    # La fila cruza a un producto existente pero con una unidad que el producto
    # aún no maneja → ofrecer "agregar presentación" con su factor.
    nueva_presentacion: bool = False
    # Mejor candidato del cruce (≥ score de confianza) — sugerencia "vincular".
    producto_id: Optional[uuid.UUID] = None
    candidatos: list[CandidatoOut] = Field(default_factory=list)
    # El cliente elegido ya tiene código/nombre guardado para ese producto.
    ya_vinculado: bool = False
    # El archivo repite este producto (mismo nombre o código): fila original.
    duplicada_de: Optional[int] = None
    # La repetición trae OTRO precio que la fila original — conflicto a revisar.
    precio_distinto: bool = False
    # Otra fila del archivo ya se vinculó al MISMO producto del catálogo: si se
    # importan ambas, la última pisa el código/nombre/precio del cliente.
    mismo_producto_que: Optional[int] = None


class ImportColumnaOut(BaseModel):
    """Una columna del archivo y a qué campo del sistema se está leyendo."""
    indice: int
    encabezado: str
    campo: str = ""               # "" = no se importa
    muestras: list[str] = Field(default_factory=list)


class ImportPreviewOut(BaseModel):
    formato: str                  # "plantilla" (determinista) | "ia"
    filas: list[ImportFilaPreview]
    # Mapeo columna→campo (solo archivos tabulares): el usuario lo revisa y
    # corrige antes de aprobar. Vacío en la rama IA (no hay columnas fijas).
    columnas: list[ImportColumnaOut] = Field(default_factory=list)
    campos_mapeables: list[dict] = Field(default_factory=list)
    # No se reconoció qué columna trae la descripción: el usuario debe mapear.
    requiere_mapeo: bool = False
    # Renglones con datos descartados por no traer nombre (se avisan, no se
    # esconden: el preview traería menos productos que el archivo).
    filas_sin_nombre: int = 0
    # Para las preguntas en LOTE del wizard (una respuesta para todo el archivo):
    faltan_clave_sat: int = 0         # filas sin clave SAT → P1 sugerida/genérica
    faltan_unidad_sat: int = 0        # filas sin unidad SAT → P2 sugerida/genérica
    categorias_nuevas: list[str] = Field(default_factory=list)   # → P3 crearlas o no
    esquemas_no_encontrados: list[str] = Field(default_factory=list)
    filas_sin_esquema: int = 0        # → P4 esquema default del lote
    tiene_precios: bool = False       # → crear/actualizar lista de precios


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
    # Nombre de la categoría del archivo (se resuelve/crea según crear_categorias).
    categoria: Optional[str] = Field(default=None, max_length=100)
    esquema_impuesto_id: Optional[uuid.UUID] = None
    # Código o nombre del esquema del archivo (IVA16, IVA0…), se cruza por texto.
    esquema: Optional[str] = Field(default=None, max_length=100)
    activo: bool = True                          # ESTATUS BAJA importa inactivo
    # Al VINCULAR con otra unidad: cuántas unidades base trae 1 de esta unidad
    # ("1 MANOJO = 0.5 KILO"). Solo aplica si la unidad no existe en el producto.
    presentacion_factor: Optional[Decimal] = Field(default=None, gt=0)
    # Solo cuando la importación es la lista de un cliente:
    codigo_cliente: Optional[str] = Field(default=None, max_length=50)
    nombre_cliente: Optional[str] = Field(default=None, max_length=254)
    precio: Optional[Decimal] = Field(default=None, ge=0)


class ImportIn(BaseModel):
    cliente_id: Optional[uuid.UUID] = None                    # compat: uno solo
    # La misma lista puede ser de VARIOS clientes (grupo/cadena): los códigos,
    # nombres y presentaciones se guardan para cada uno.
    cliente_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    guardar_precios: bool = False
    lista_id: Optional[uuid.UUID] = None          # default: la lista del cliente
    # Sin cliente y sin lista_id: crear una lista nueva con este nombre.
    lista_nombre: Optional[str] = Field(default=None, max_length=254)
    # Pregunta 3 del lote: crear las categorías nuevas que trae el archivo.
    crear_categorias: bool = False
    # Pregunta 4: esquema de impuesto para las filas que no traen uno.
    esquema_default_id: Optional[uuid.UUID] = None
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
    categorias_creadas: int = 0
    presentaciones_agregadas: int = 0
    # Lista de precios que recibió los precios (para el paso de asignación).
    lista_id: Optional[uuid.UUID] = None
    lista_nombre: Optional[str] = None
    errores: list[ImportErrorFila] = Field(default_factory=list)


# ─── Sugerencia SAT en lote (Pregunta 1/2 del wizard) ────────────────────────
class SugerirSatBatchIn(BaseModel):
    productos: list[dict] = Field(min_length=1, max_length=2000)
    # cada item: {"nombre": str, "unidad": str}


class SugerenciaSatOut(BaseModel):
    nombre: str
    clave_sat: str
    descripcion_sat: str
    unidad_sat: str
    unidad_sat_generica: str


# ─── Catálogo del cliente (codigo/nombre por cliente → CFDI) ─────────────────
class ProductoClienteOut(BaseModel):
    producto_id: uuid.UUID
    producto_sku: str
    producto_nombre: str
    codigo_cliente: Optional[str] = None
    nombre_cliente: Optional[str] = None
    presentacion: Optional[str] = None


class ProductoClienteUpsert(BaseModel):
    codigo_cliente: Optional[str] = Field(default=None, max_length=50)
    nombre_cliente: Optional[str] = Field(default=None, max_length=254)
    presentacion: Optional[str] = Field(default=None, max_length=20)

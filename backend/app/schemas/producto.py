"""Product schemas."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class PresentacionCreate(BaseModel):
    """Alta de UNA presentación sobre un producto que ya existe.

    El `factor` no es cosmético: el inventario descuenta en unidad base
    multiplicando por él (una CAJA de 20 saca 20 KILO), así que se pide siempre
    y nunca se asume 1.
    """
    nombre: str = Field(min_length=1, max_length=20)
    factor: Decimal = Field(gt=0)
    unidad_sat: Optional[str] = Field(default=None, max_length=3)


class ProductoBase(BaseModel):
    sku: str = Field(max_length=50)
    nombre: str = Field(max_length=254)
    descripcion: Optional[str] = None
    categoria_id: Optional[uuid.UUID] = None
    esquema_impuesto_id: Optional[uuid.UUID] = None
    # SAT / CFDI 4.0
    clave_sat: str = Field(max_length=8)
    unidad_sat: str = Field(max_length=3)
    # Clave del artículo en SAE, la misma en todas sus empresas. El catálogo del
    # cliente sólo la pisa cuando ESE cliente usa otra en su plaza.
    clave_sae: Optional[str] = Field(default=None, max_length=50)
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
    # Si viene, debe ser numérico: los códigos del cliente (CILA-FRUT-145) van
    # al catálogo del cliente, nunca al SKU interno.
    sku: Optional[str] = Field(default=None, max_length=50)
    # Sin `forzar`, el alta truena con 409 si el catálogo ya tiene un candidato
    # fuerte con ese nombre — el detector de duplicados deja de ser opcional.
    forzar: bool = False


class ProductoUpdate(BaseModel):
    sku: Optional[str] = Field(default=None, max_length=50)
    nombre: Optional[str] = Field(default=None, max_length=254)
    descripcion: Optional[str] = None
    categoria_id: Optional[uuid.UUID] = None
    esquema_impuesto_id: Optional[uuid.UUID] = None
    clave_sat: Optional[str] = Field(default=None, max_length=8)
    unidad_sat: Optional[str] = Field(default=None, max_length=3)
    clave_sae: Optional[str] = Field(default=None, max_length=50)
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
    # Derivado del catálogo SAT oficial a partir de `clave_sat` — no se guarda
    # en el producto: la clave es el dato, la descripción es cómo se lee. La
    # resuelve el listado en una sola consulta (ver `preparar` en la ruta).
    clave_sat_descripcion: Optional[str] = None


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
    # Lo que ya tiene el producto existente: al vincular, la fila lo hereda y
    # la pantalla lo muestra en vez de un "Sin categoría" que engaña.
    categoria_id: Optional[uuid.UUID] = None
    categoria_nombre: str = ""
    esquema_impuesto_id: Optional[uuid.UUID] = None
    esquema_codigo: str = ""
    # Lo fiscal del producto existente: al vincular, la fila usa esto.
    clave_sat: Optional[str] = None
    unidad_sat: Optional[str] = None


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


class AliasOut(BaseModel):
    """Un alias como se lee en pantalla: con su alcance ya resuelto a nombres.

    `ambiguo` marca que el MISMO texto normalizado apunta a otro producto en
    otro alcance. A veces es correcto —EHMO pide «limón» y le toca el agrio,
    Jubran pide «limón» y le toca el liso— y a veces es el error que nadie
    veía, así que la pantalla lo señala y deja que la persona decida.
    """
    id: uuid.UUID
    texto: str
    origen: str
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None
    sucursal_nombre: Optional[str] = None
    ambiguo: bool = False
    # Los otros productos a los que va el mismo texto, para explicar el aviso.
    tambien_en: list[str] = []
    created_at: datetime


class AliasReapuntarIn(BaseModel):
    """Corrige una fila del vocabulario: el texto, el producto, o los dos.

    El CLIENTE no se toca aquí: pasar el alias de un cliente al global
    convertiría su corrección en una regla para todos, y esa es otra decisión
    (con otro permiso). Para eso se quita y se vuelve a escribir.

    La SUCURSAL sí, dentro del mismo cliente: mandarla con valor acota la regla
    a esa plaza; mandarla en `null` la abre a todas las sucursales del cliente.
    Omitirla la deja como está (por eso se distingue ausente de `null`).
    """
    texto: Optional[str] = Field(default=None, min_length=1, max_length=254)
    producto_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None


class VocabularioOut(BaseModel):
    """Una fila de la tabla puente: «lo que escribe el cliente» = «qué es»."""
    id: uuid.UUID
    texto: str
    producto_id: uuid.UUID
    producto_sku: str
    producto_nombre: str
    cliente_id: Optional[uuid.UUID] = None
    cliente_nombre: Optional[str] = None
    sucursal_id: Optional[uuid.UUID] = None
    sucursal_nombre: Optional[str] = None
    origen: str
    # Dos reglas del MISMO alcance llevan a productos distintos: nadie decide.
    # Que el mismo texto lleve a otro producto para otro cliente NO entra aquí
    # — eso lo resuelve la cascada (cliente+sucursal > cliente > global).
    ambiguo: bool = False
    # Sólo en las filas globales: a cuántos clientes se les dijo que ese texto
    # es OTRO producto. Un global contradicho por todos suele estar mal puesto.
    pisado_por: int = 0


class AliasIn(BaseModel):
    texto: str = Field(min_length=1, max_length=254)
    producto_id: uuid.UUID
    # Alcance opcional: con cliente (y sucursal) el alias es vocabulario privado
    # de ese cliente y NO toca el global — el "LIMON" de un cliente deja de
    # pelearse con el de otro.
    cliente_id: Optional[uuid.UUID] = None
    sucursal_id: Optional[uuid.UUID] = None


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
    categoria: str = ""                    # texto tal como viene en el archivo
    categoria_id: Optional[uuid.UUID] = None   # categoría del sistema resuelta
    esquema: str = ""                      # texto del archivo
    esquema_id: Optional[uuid.UUID] = None     # esquema del sistema resuelto
    esquema_origen: str = ""               # "archivo" | "regla" | "ia" | ""
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


class ImportCategoriaMatch(BaseModel):
    """Una categoría del archivo y a cuál existente corresponde (o si es nueva)."""
    nombre_archivo: str
    categoria_id: Optional[uuid.UUID] = None
    categoria_nombre: str = ""
    score: int = 0
    es_nueva: bool = True


class ImportPreviewOut(BaseModel):
    formato: str                  # "plantilla" (determinista) | "ia"
    filas: list[ImportFilaPreview]
    # Mapeo columna→campo (solo archivos tabulares): el usuario lo revisa y
    # corrige antes de aprobar. Vacío en la rama IA (no hay columnas fijas).
    columnas: list[ImportColumnaOut] = Field(default_factory=list)
    campos_mapeables: list[dict] = Field(default_factory=list)
    # Categorías del archivo cruzadas contra las que YA tiene el tenant: se
    # reusa la existente en vez de duplicarla ("ABARROTE" ↔ "Abarrotes").
    categorias_match: list["ImportCategoriaMatch"] = Field(default_factory=list)
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
    # Solo para la bitácora: el archivo no se guarda, pero su nombre es con lo
    # que el usuario reconoce de qué lista salió cada producto.
    archivo_nombre: Optional[str] = Field(default=None, max_length=254)
    filas: list[ImportFilaIn] = Field(min_length=1, max_length=2000)


class ImportErrorFila(BaseModel):
    fila: int
    error: str


class ImportProductoResultado(BaseModel):
    """Qué producto quedó en cada fila: lo necesita el último paso para
    guardar el catálogo del cliente sin volver a subir el archivo."""
    fila: int
    producto_id: uuid.UUID
    codigo: str = ""          # el que traía el archivo
    nombre: str = ""
    presentacion: str = ""


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
    # Filas que sí entraron, con el producto que les corresponde.
    productos: list[ImportProductoResultado] = Field(default_factory=list)
    errores: list[ImportErrorFila] = Field(default_factory=list)


# ─── Sugerencia SAT en lote (Pregunta 1/2 del wizard) ────────────────────────
class SugerirSatBatchIn(BaseModel):
    productos: list[dict] = Field(min_length=1, max_length=2000)
    # cada item: {"nombre": str, "unidad": str}


class SugerirEsquemaBatchIn(BaseModel):
    productos: list[dict] = Field(min_length=1, max_length=2000)
    # cada item: {"nombre": str, "clave_sat": str, "categoria": str}
    usar_ia: bool = True


class SugerenciaEsquemaOut(BaseModel):
    nombre: str
    esquema_id: Optional[uuid.UUID] = None
    esquema_codigo: str = ""
    # "regla" | "ia" | "revisar" (la ley depende del envase/contenido) |
    # "falta_esquema" (el negocio no tiene uno así) | ""
    origen: str = ""
    motivo: str = ""


class SugerirCategoriaBatchIn(BaseModel):
    productos: list[dict] = Field(min_length=1, max_length=2000)
    # cada item: {"nombre": str, "clave_sat": str}
    usar_ia: bool = True


class SugerenciaCategoriaOut(BaseModel):
    nombre: str
    categoria_id: Optional[uuid.UUID] = None
    categoria_nombre: str = ""
    origen: str = ""              # "ia" | ""


class SugerenciaSatOut(BaseModel):
    nombre: str
    clave_sat: str
    descripcion_sat: str
    unidad_sat: str
    unidad_sat_generica: str


class CatalogoClienteBatchIn(BaseModel):
    """Guardar de golpe el código/nombre/presentación que usan uno o varios
    clientes para una lista de productos (el último paso de la importación)."""
    cliente_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    items: list[ImportProductoResultado] = Field(min_length=1, max_length=2000)
    # Para la bitácora: liga este paso con la pasada de /importar que lo generó.
    archivo_nombre: Optional[str] = Field(default=None, max_length=254)


class CatalogoClienteBatchOut(BaseModel):
    clientes: int
    productos: int
    guardados: int


# ─── Catálogo del cliente (codigo/nombre por cliente → CFDI) ─────────────────
class ProductoClienteOut(BaseModel):
    producto_id: uuid.UUID
    producto_sku: str
    producto_nombre: str
    codigo_cliente: Optional[str] = None
    nombre_cliente: Optional[str] = None
    presentacion: Optional[str] = None
    # None = fila genérica (todas las plazas); con valor = clave de ESA plaza.
    sucursal_id: Optional[uuid.UUID] = None
    sucursal_nombre: Optional[str] = None


class ProductoClienteUpsert(BaseModel):
    codigo_cliente: Optional[str] = Field(default=None, max_length=50)
    nombre_cliente: Optional[str] = Field(default=None, max_length=254)
    presentacion: Optional[str] = Field(default=None, max_length=20)
    # Omitida/None = upsert de la fila genérica; con valor, el de la plaza.
    sucursal_id: Optional[uuid.UUID] = None


class ImpuestosPorClaveIn(BaseModel):
    """Claves de SAE (o SKU) de las que se quiere saber su fiscalidad."""
    claves: list[str] = Field(min_length=1, max_length=500)


class ImpuestoDeClaveOut(BaseModel):
    """Lo que el bot necesita saber de un producto para decidir impuestos, en
    una sola pregunta por lote. Antes lo resolvía contra SAE clave por clave
    (INVE.CVE_ESQIMPU → IMPU.IMPUESTO4).

    `iva` e `ieps` van como FRACCIÓN (0.16 = 16%), que es como el Facturador las
    guarda — quien imprime un porcentaje multiplica, y así nadie tiene que
    adivinar en qué unidad viene."""
    clave: str
    encontrado: bool = False
    producto_id: Optional[uuid.UUID] = None
    nombre: str = ""
    esquema: str = ""
    iva: Decimal = Decimal("0")
    ieps: Decimal = Decimal("0")
    tipo_ieps: str = "TASA"
    ieps_cuota: Decimal = Decimal("0")
    iva_exento: bool = False
    objeto_imp: str = "02"
    activo: bool = True


# ── Altas en SAE: la cola entre el Facturador y el conector ──────────────────
# El backend no ve SAE; el conector sí. Mismo reparto que el espejo de facturas.

class AltaSaeIn(BaseModel):
    """Pide crear un producto en SAE. `clave` es la identidad de la alta (CVE_ART).

    `empresas` son las de SAE donde debe nacer. Vacío = las cuatro: es lo que
    pidió el dueño («las 4 empresas») y evita que un olvido cree el producto en
    una sola, que es el estado que hoy duele.
    """
    clave: str = Field(min_length=1, max_length=20)
    producto_id: Optional[uuid.UUID] = None
    descripcion: str = Field(min_length=1, max_length=60)
    unidad: str = Field(default="PIEZA", max_length=20)
    # Línea y esquema de SAE (su categorización interna, no la del SAT) y las
    # claves del SAT. Quien pide ya las resolvió; aquí se guardan tal cual.
    linea: Optional[str] = Field(default=None, max_length=10)
    esquema: Optional[int] = None
    sat: Optional[str] = Field(default=None, max_length=20)
    sat_unidad: Optional[str] = Field(default=None, max_length=10)
    empresas: list[str] = Field(default_factory=list)
    origen: str = Field(default="UI", max_length=12)
    nota: Optional[str] = Field(default=None, max_length=300)
    # Sin `producto_id`, el alta crea el producto del catálogo con estos mismos
    # datos (o reusa el del mismo nombre exacto). En `False` la solicitud se
    # encola suelta: útil cuando la clave se va a ligar a mano después.
    crear_producto: bool = True


class AltaSaeOut(ORMModel):
    """Una alta pedida: en qué estado va y qué contestó SAE por empresa."""
    id: uuid.UUID
    estado: str            # PENDIENTE | EN_CURSO | OK | PARCIAL | ERROR
    tipo: str = "ALTA"     # ALTA | CAMBIO
    origen: str
    clave: str
    producto_id: Optional[uuid.UUID] = None
    datos: dict = {}
    empresas: list = []
    solicitada_at: datetime
    iniciada_at: Optional[datetime] = None
    terminada_at: Optional[datetime] = None
    resultado: Optional[dict] = None
    motivo: Optional[str] = None


class CambioSaeIn(BaseModel):
    """Pide cambiar un artículo que ya existe en SAE. Sólo viajan los campos
    que cambian; al menos uno. `empresas` vacío = las cuatro."""
    clave: str = Field(min_length=1, max_length=20)
    producto_id: Optional[uuid.UUID] = None
    descripcion: Optional[str] = Field(default=None, max_length=60)
    linea: Optional[str] = Field(default=None, max_length=10)
    unidad: Optional[str] = Field(default=None, max_length=20)
    esquema: Optional[int] = None
    sat: Optional[str] = Field(default=None, max_length=20)
    # Pasa el artículo de baja ('B') a activo ('A'). Lo contrario no existe.
    reactivar: bool = False
    empresas: list[str] = Field(default_factory=list)
    origen: str = Field(default="UI", max_length=12)


class AltaSaeReporteIn(BaseModel):
    """El conector reporta qué creó. `por_empresa` es la verdad de la alta:
    {"02": {"ok": true, "clave": "AJOKG"}, "03": {"ok": false, "error": "..."}}.

    No hay reintento: lo que aquí se reporte como creado no se vuelve a
    intentar nunca, porque un INSERT repetido duplica el producto en SAE.
    """
    por_empresa: dict = Field(default_factory=dict)
    motivo: Optional[str] = Field(default=None, max_length=300)

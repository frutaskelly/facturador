// API response types (numeric/Decimal fields arrive as strings over JSON).

// ── IAM (roles, permissions, memberships) ──
export type Permission = {
  id: string;
  recurso: string;
  accion: string;
  vertical?: string | null;
  descripcion?: string | null;
};

export type Role = {
  id: string;
  tenant_id?: string | null;
  nombre: string;
  vertical?: string | null;
  descripcion?: string | null;
  es_preset: boolean;
  created_at: string;
  updated_at: string;
};

export type RoleDetail = Role & { permissions: string[] };

export type Membership = {
  id: string;
  tenant_id: string;
  user_id: string;
  role_id: string;
  active: boolean;
  acceso_todas_sucursales: boolean;
  created_at: string;
  updated_at: string;
  user_email?: string | null;
  user_full_name?: string | null;
  role_nombre?: string | null;
};

export type Categoria = {
  id: string;
  tenant_id: string;
  codigo: string;
  nombre: string;
  descripcion?: string | null;
  activo: boolean;
  created_at: string;
  updated_at: string;
};

export type EsquemaImpuesto = {
  id: string;
  tenant_id: string;
  codigo: string;
  nombre: string;
  descripcion?: string | null;
  iva_tasa: string;
  ieps_tasa: string;
  iva_exento: boolean;
  retencion_iva_tasa: string;
  retencion_isr_tasa: string;
  activo: boolean;
  created_at: string;
  updated_at: string;
};

export type ListaPrecios = {
  id: string;
  tenant_id: string;
  codigo: string;
  nombre: string;
  status: string;
  vigencia_desde?: string | null;
  vigencia_hasta?: string | null;
  moneda: string;
  notas?: string | null;
  created_at: string;
  updated_at: string;
};

export type Precio = {
  id: string;
  tenant_id: string;
  lista_id: string;
  producto_id: string;
  presentacion: string;
  precio_unitario: string;
  cantidad_minima: number;
  vigencia_desde?: string | null;
  vigencia_hasta?: string | null;
};

export type Sucursal = {
  id: string;
  tenant_id: string;
  cliente_id: string;
  codigo?: string | null;
  nombre: string;
  lista_precios_id?: string | null;
  domicilio: Record<string, unknown>;
  contacto?: string | null;
  telefono?: string | null;
  activo: boolean;
  almacen_id?: string | null;
  serie_factura_id?: string | null;
  serie_remision_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type TipoSerie = "FISCAL" | "NO_FISCAL";
export type TipoDocSerie = "FACTURA" | "NOTA_CREDITO" | "REMISION" | "PAGO";

export type Serie = {
  id: string;
  tenant_id: string;
  codigo: string;
  tipo: TipoSerie;
  tipo_documento: TipoDocSerie;
  nombre?: string | null;
  folio_actual: number;
  activa: boolean;
  es_default: boolean;
  vigencia_desde?: string | null;
  vigencia_hasta?: string | null;
  notas?: string | null;
  created_at: string;
  updated_at: string;
};

export type PrecioOverride = {
  id: string;
  tenant_id: string;
  cliente_id?: string | null;
  sucursal_id?: string | null;
  producto_id: string;
  presentacion: string;
  precio_unitario: string;
  vigencia_desde?: string | null;
  vigencia_hasta?: string | null;
  created_at: string;
  updated_at: string;
};

export type Cotizacion = {
  producto_id: string;
  presentacion: string;
  cantidad: string;
  precio?: string | null;
  origen?: string | null;
  lista_id?: string | null;
};

export type Cliente = {
  id: string;
  tenant_id: string;
  codigo?: string | null;
  tipo: string;
  status: string;
  legal_name: string;
  rfc: string;
  regimen_fiscal?: string | null;
  uso_cfdi_default?: string | null;
  forma_pago_default?: string | null;
  metodo_pago_default?: string | null;
  domicilio_fiscal: Record<string, unknown>;
  lista_precios_id?: string | null;
  condiciones_pago?: string | null;
  limite_credito: string;
  dias_credito: number;
  descuento_default: string;
  config_addenda: Record<string, unknown>;
  almacen_id?: string | null;
  serie_factura_id?: string | null;
  serie_remision_id?: string | null;
  saldo_actual: string;
  ventas_ytd: string;
  ultima_venta_at?: string | null;
  ultimo_pago_at?: string | null;
  custom_fields: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ExistenciaRow = {
  producto_id: string;
  producto_sku: string | null;
  producto_nombre: string | null;
  almacen_id: string;
  almacen_nombre: string | null;
  disponible: string;
  reservada: string;
  costo_promedio: string;
  valor: string;
};

export type LineaRemision = {
  id: string;
  numero_linea: number;
  producto_id: string;
  producto_nombre?: string | null;
  presentacion: string;
  cantidad_solicitada: string;
  cantidad_surtida?: string | null;
  precio_unitario: string;
  importe: string;
  iva_importe?: string;
  ieps_importe?: string;
  lote_id?: string | null;
  notas?: string | null;
};

export type Remision = {
  id: string;
  tenant_id: string;
  folio_interno: string;
  cliente_facturacion_id: string;
  almacen_id?: string | null;
  sucursal_id?: string | null;
  lista_precios_id?: string | null;
  fecha_remision: string;
  fecha_entrega?: string | null;
  estado: "BORRADOR" | "CONFIRMADA" | "FACTURADA" | "CANCELADA";
  canal: string;
  factura_folio?: string | null;
  factura_estado?: "BORRADOR" | "TIMBRADA" | "CANCELADA" | null;
  factura_id?: string | null;
  subtotal: string;
  descuento: string;
  iva: string;
  ieps: string;
  total: string;
  notas?: string | null;
  nota_entrega?: string | null;
  pos_etapa?: string | null;
  pos_asignaciones?: Record<string, { user_id: string; at: string }>;
  created_at: string;
  updated_at: string;
};

export type LineaDevolucion = {
  id: string;
  producto_id: string;
  presentacion: string;
  cantidad: string;
  cantidad_base: string;
};

export type Devolucion = {
  id: string;
  motivo?: string | null;
  created_at: string;
  lineas: LineaDevolucion[];
};

export type RemisionDetail = Remision & { lineas: LineaRemision[]; devoluciones?: Devolucion[] };

export type LineaFactura = {
  numero_linea: number;
  producto_id: string;
  clave_prod_serv: string;
  clave_unidad: string;
  descripcion: string;
  cantidad: string;
  valor_unitario: string;
  importe: string;
  descuento: string;
  objeto_imp: string;
  iva_tasa: string;
  iva_importe: string;
  ieps_importe: string;
  ret_iva_importe: string;
  ret_isr_importe: string;
};

export type Factura = {
  id: string;
  tenant_id: string;
  serie: string;
  folio: number;
  cliente_id: string;
  uso_cfdi: string;
  forma_pago: string;
  metodo_pago: string;
  moneda: string;
  tipo_comprobante: string;
  fecha: string;
  subtotal: string;
  iva_trasladado: string;
  total: string;
  saldo_insoluto: string;
  estado: "BORRADOR" | "TIMBRADA" | "CANCELADA";
  uuid?: string | null;
  fecha_timbrado?: string | null;
  fecha_cancelacion?: string | null;
  motivo_cancelacion?: string | null;
  // Sustitución CFDI (refacturación, relación "04"):
  //  - sustituye_a_factura_id: esta factura NUEVA sustituye a esa vieja.
  //  - uuid_sustitucion: al cancelar con motivo 01, UUID de quien la sustituye.
  sustituye_a_factura_id?: string | null;
  uuid_sustitucion?: string | null;
  notas?: string | null;
  created_at: string;
  updated_at: string;
};

export type FacturaDetail = Factura & { lineas: LineaFactura[] };

// Cruce de productos (match)
export type Candidato = {
  producto_id: string;
  sku: string;
  nombre: string;
  score: number;
  origen: "exacto" | "alias" | "difuso" | "ia";
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
  unidad_base?: string | null;
  // Lo que YA tiene el producto existente: al vincular, la fila lo hereda.
  categoria_id?: string | null;
  categoria_nombre?: string;
  esquema_impuesto_id?: string | null;
  esquema_codigo?: string;
};
export type MatchResult = { texto: string; candidatos: Candidato[] };

// ─── Importación masiva de productos (plantilla o lista de precios con IA) ───
export type ImportFilaPreview = {
  fila: number;
  nombre: string;
  codigo: string;
  descripcion: string;
  unidad: string;
  precio: string;
  clave_sat: string;
  unidad_sat: string;
  codigo_barras: string;
  categoria: string;                 // texto del archivo
  categoria_id?: string | null;      // categoría del sistema resuelta
  esquema: string;                   // texto del archivo
  esquema_id?: string | null;        // esquema del sistema resuelto
  esquema_origen?: string;           // "archivo" | "regla" | "ia" | ""
  baja: boolean;                   // ESTATUS BAJA en el archivo → omitir default
  clave_sat_valida?: boolean | null;   // validada contra el catálogo SAT oficial
  unidad_sat_valida?: boolean | null;
  nueva_presentacion: boolean;     // cruza a un producto con OTRA unidad → variante
  producto_id?: string | null;     // sugerencia: vincular a este existente
  candidatos: Candidato[];
  ya_vinculado: boolean;           // el cliente ya tiene código/nombre para él
  duplicada_de?: number | null;    // el archivo repite este producto (fila original)
  precio_distinto?: boolean;       // la repetición trae OTRO precio (conflicto)
  mismo_producto_que?: number | null; // otra fila ya se vinculó al mismo producto
};
// Una categoría del archivo y a cuál existente corresponde (o si es nueva).
export type ImportCategoriaMatch = {
  nombre_archivo: string;
  categoria_id?: string | null;
  categoria_nombre: string;
  score: number;
  es_nueva: boolean;
};
export type SugerenciaEsquema = {
  nombre: string;
  esquema_id?: string | null;
  esquema_codigo: string;
  // "regla" | "ia" | "revisar" (la ley depende del envase/contenido) |
  // "falta_esquema" (el negocio no tiene uno así) | ""
  origen: string;
  motivo?: string;
};
export type SugerenciaCategoria = {
  nombre: string;
  categoria_id?: string | null;
  categoria_nombre: string;
  origen: string;   // "ia" | ""
};
// Una columna del archivo y a qué campo del sistema se está leyendo.
export type ImportColumna = {
  indice: number;
  encabezado: string;
  campo: string;              // "" = no se importa
  muestras: string[];
};
export type ImportPreview = {
  formato: "plantilla" | "ia";
  filas: ImportFilaPreview[];
  columnas: ImportColumna[];
  campos_mapeables: { valor: string; etiqueta: string }[];
  categorias_match: ImportCategoriaMatch[];   // categorías del archivo ↔ del sistema
  requiere_mapeo: boolean;      // no se reconoció la columna de descripción
  filas_sin_nombre: number;     // renglones con datos descartados por no traer nombre
  // Meta para las preguntas en lote:
  faltan_clave_sat: number;
  faltan_unidad_sat: number;
  categorias_nuevas: string[];
  esquemas_no_encontrados: string[];
  filas_sin_esquema: number;
  tiene_precios: boolean;
};
// Qué producto quedó en cada fila: lo usa el último paso para guardar el
// catálogo del cliente sin volver a subir el archivo.
export type ImportProductoResultado = {
  fila: number;
  producto_id: string;
  codigo: string;
  nombre: string;
  presentacion: string;
};
export type ImportResult = {
  creados: number;
  vinculados: number;
  alias_guardados: number;
  precios_guardados: number;
  omitidos: number;
  categorias_creadas: number;
  presentaciones_agregadas: number;
  lista_id?: string | null;
  lista_nombre?: string | null;
  productos: ImportProductoResultado[];
  errores: { fila: number; error: string }[];
};
export type SugerenciaSat = {
  nombre: string;
  clave_sat: string;
  descripcion_sat: string;
  unidad_sat: string;
  unidad_sat_generica: string;
};
// Catálogo del cliente: su código (NoIdentificacion) y su nombre (Descripcion CFDI).
export type ProductoClienteRow = {
  producto_id: string;
  producto_sku: string;
  producto_nombre: string;
  codigo_cliente?: string | null;
  nombre_cliente?: string | null;
  presentacion?: string | null;    // la unidad con la que ese cliente compra
};
// Línea parseada desde un pegado de Excel (backend detecta columnas + cruza).
export type LineaPegada = {
  texto: string;
  cantidad: string;
  precio: string;
  presentacion: string;
  candidatos: Candidato[];
};


export type Proveedor = {
  id: string;
  tenant_id: string;
  codigo: string;
  nombre: string;
  rfc?: string | null;
  contacto?: string | null;
  telefono?: string | null;
  email?: string | null;
  categorias: string[];
  condiciones_pago?: string | null;
  activo: boolean;
  notas?: string | null;
  created_at: string;
  updated_at: string;
};

export type Almacen = {
  id: string;
  tenant_id: string;
  codigo: string;
  nombre: string;
  calle?: string | null;
  colonia?: string | null;
  cp?: string | null;
  ciudad?: string | null;
  estado?: string | null;
  es_default: boolean;
  created_at: string;
  updated_at: string;
};

export type LineaOrdenCompra = {
  id: string;
  producto_id: string;
  cantidad_solicitada: string;
  cantidad_recibida: string;
  presentacion?: string | null;
  precio_unitario: string;
  importe: string;
  notas?: string | null;
};

export type OrdenCompra = {
  id: string;
  tenant_id: string;
  folio?: string | null;
  proveedor_id: string;
  almacen_destino_id?: string | null;
  fecha: string;
  fecha_entrega_esperada?: string | null;
  fecha_recibida?: string | null;
  estado: string;
  subtotal: string;
  iva_total: string;
  total_estimado: string;
  total_recibido: string;
  notas?: string | null;
  created_at: string;
  updated_at: string;
};

// La LISTA de órdenes no trae líneas; solo el detalle (GET /{id}) las incluye.
export type OrdenCompraDetail = OrdenCompra & { lineas: LineaOrdenCompra[] };

export type Conversion = {
  id: string;
  tenant_id: string;
  producto_catalogado_id: string;
  producto_no_catalogado_id: string;
  factor: string;
  merma_pct: string;
  precio_no_cat?: string | null;
  mezcla_grupo_id?: string | null;
  mezcla_proporcion?: string | null;
  prioridad: number;
  requiere_aprobacion: boolean;
  activo: boolean;
  notas?: string | null;
  created_at: string;
  updated_at: string;
};

export type Producto = {
  id: string;
  tenant_id: string;
  sku: string;
  nombre: string;
  descripcion?: string | null;
  categoria_id?: string | null;
  esquema_impuesto_id?: string | null;
  clave_sat: string;
  unidad_sat: string;
  objeto_imp: string;
  iva_tasa: string;
  ieps_tasa: string;
  unidad_base: string;
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
  unidad_entrada?: string | null;
  unidad_salida?: string | null;
  perecedero: boolean;
  cold_chain: boolean;
  requiere_lote: boolean;
  requiere_caducidad: boolean;
  peso_variable: boolean;
  vida_util_dias?: number | null;
  sinonimos: string[];
  activo: boolean;
  custom_fields: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

// ── Empresas del grupo (Ajustes › Empresas) ──
export type EmpresaGrupo = {
  tenant_id: string;
  slug: string;
  legal_name: string;
  trade_name: string;
  rfc: string;
  regimen_fiscal_sat: string;
  domicilio_fiscal_cp: string;
  domicilio_fiscal: Record<string, unknown>;
  /** Color elegido; null = automático (se deriva del id en lib/empresa-color). */
  color: string | null;
  es_principal: boolean;
  es_actual: boolean;
  /** ¿Es del grupo de la empresa activa? (falso = invitado por otro dueño). */
  en_grupo: boolean;
  rol: string;
  puede_editar: boolean;
  datos_fiscales: boolean;
  csd: boolean;
  logo: boolean;
  series: boolean;
  correo: boolean;
  listo_para_facturar: boolean;
};

export type EmpresasGrupo = {
  empresas: EmpresaGrupo[];
  grupo_total: number;
  grupo_max: number;
  puede_agregar: boolean;
};

// ─── Bandeja de OC + equivalencias de cliente ────────────────────────────────
// Una orden que llega por WhatsApp/correo aterriza en la bandeja antes de
// volverse remisión: ahí se verifica a qué cliente se asignó y, si el sistema
// no pudo, se resuelve a mano (y lo aprende para la próxima).

export type SistemaExterno = "RFC" | "SAE" | "PROYECTO" | "NOMBRE" | "UBICACION" | "WHATSAPP";

export type ClienteExterno = {
  id: string;
  sistema: SistemaExterno;
  clave: string;
  clave_normalizada: string;
  cliente_id: string;
  sucursal_id?: string | null;
  origen: "MANUAL" | "BOT" | "IMPORT" | "IA";
  confianza: "CONFIRMADA" | "SUGERIDA";
  notas?: string | null;
  created_at: string;
};

export type CandidatoLinea = {
  producto_id: string;
  sku: string;
  nombre: string;
  score: number;
  origen: string;
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
};

export type LineaOC = {
  numero: number;
  descripcion: string;
  cantidad: string;
  unidad?: string | null;
  clave?: string | null;
  precio?: string | null;
  notas?: string | null;
  candidatos: CandidatoLinea[];
};

export type OCRecibida = {
  id: string;
  canal: "WHATSAPP" | "EMAIL" | "MANUAL" | "API";
  origen_externo: string;
  folio_externo?: string | null;
  remitente?: string | null;
  archivo_nombre?: string | null;
  archivo_url?: string | null;
  recibida_at: string;
  estado: "PENDIENTE" | "ASIGNADA" | "DESCARTADA";
  motivo?: string | null;
  cliente_id?: string | null;
  cliente_nombre?: string | null;
  sucursal_id?: string | null;
  sucursal_nombre?: string | null;
  resuelto_via?: string | null;
  punto_entrega?: string | null;
  candidatos: string[];        // clientes posibles según el grupo del que llegó
  ambiguo: boolean;
  remision_id?: string | null;
  remision_folio?: string | null;
  created_at: string;
  updated_at: string;
};

export type OCRecibidaDetalle = OCRecibida & {
  payload: Record<string, unknown>;
  lineas: LineaOC[];
};

// ─── Conexiones ──────────────────────────────────────────────────────────────
// Una clave con la que un sistema externo (Smart Supply) deja órdenes en la
// bandeja, sin que nadie tenga que repartir la contraseña de una persona.

export type Conexion = {
  id: string;
  tipo: string;
  nombre: string;
  clave_pista: string;          // últimos 4 caracteres, para nombrarla
  estado: "PENDIENTE" | "ACTIVA" | "REVOCADA";
  created_at: string;
  activada_at?: string | null;
  ultimo_uso_at?: string | null;
};

export type ConexionEstado = {
  tipo: string;
  nombre: string;
  conexion?: Conexion | null;
  ordenes_hoy: number;
  ordenes_sin_resolver: number;
  ultima_orden_at?: string | null;
  conviene_rotar: boolean;
  dias_desde_creacion?: number | null;
};

export type ClaveNueva = {
  clave: string;                // en claro; solo existe en memoria, una vez
  conexion: Conexion;
  instruccion_whatsapp: string;
};

export type ActividadConexion = {
  recibida_at: string;
  folio_externo?: string | null;
  remitente?: string | null;
  cliente_nombre?: string | null;
  estado: string;
  partidas: number;
};

export type ClienteDelGrupo = {
  /** Id de la equivalencia, para poder desconectar al cliente del grupo. */
  externo_id?: string | null;
  cliente_id: string;
  nombre: string;
  serie_factura?: string | null;
  serie_remision?: string | null;
  sucursales: { id: string; nombre: string }[];
  /** Sucursal por defecto de ESTE grupo para ESTE cliente (última red del destino). */
  sucursal_grupo_id?: string | null;
  almacen?: string | null;
  serie_factura_id?: string | null;
  serie_remision_id?: string | null;
  almacen_id?: string | null;
  /** false = le han llegado órdenes de ese grupo sin estar registrado como candidato. */
  registrado: boolean;
};

export type GrupoWhatsapp = {
  jid: string;
  nombre?: string | null;
  rol?: string | null;              // interno | cliente
  perfil?: string | null;
  activo: boolean;            // lo que decidiste tú aquí
  reportado_activo: boolean;  // lo que dice la config de Smart Supply
  clientes: ClienteDelGrupo[];
  ordenes: number;
  ordenes_24h: number;
  ultima_orden_at?: string | null;
  sin_resolver: number;
  sincronizado_at?: string | null;
};

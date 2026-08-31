// Piezas compartidas entre la LISTA de la bandeja (/oc) y el DETALLE de una
// orden (/oc/[id]). El detalle dejó de ser un popup el 28-ago-2026: es la
// pantalla de trabajo diaria y merece la pantalla completa y su propia URL.
import type { LineaOC, OCRecibida, OCRecibidaDetalle, ProblemaLinea } from "@/lib/types";

export const CANAL_TONE: Record<string, "accent" | "muted" | "default"> = {
  WHATSAPP: "accent",
  EMAIL: "default",
  MANUAL: "muted",
  API: "muted",
};

/** Una partida con el producto que se le va a asignar. `producto_id` vacío =
 *  sin resolver: la remisión no se puede crear hasta que todas tengan producto. */
export type LineaEdit = {
  numero: number;
  texto: string;
  cantidad: string;
  unidad: string;
  /** La clave que traía el documento: al confirmar se registra como el código
   *  de ese cliente para el producto, y la próxima orden cruza al 100. */
  clave: string | null;
  producto_id: string;
  presentacion: string;
  /** Las presentaciones que el producto ELEGIDO de verdad vende: son las
   *  opciones válidas del selector. Vacío = producto sin elegir o de una sola
   *  presentación — entonces no hay nada que escoger. */
  presentaciones: string[];
  precio: string;
  /** El precio que la lista del cliente resuelve HOY para producto+presentación
   *  (cotizado al cruzar). Es lo que llena el campo cuando está vacío y la
   *  referencia del aviso «la lista dice X» al teclear otro. */
  precioLista: string | null;
  /** De qué lista salió `precioLista` — solo con lista_id se puede ofrecer
   *  «actualizar la lista»; un override no es una lista. */
  precioListaId: string | null;
  /** El cantidad_minima del TRAMO que habló. Sin él no se ofrece actualizar:
   *  escribir el tramo base con un precio de volumen es un subcobro permanente. */
  precioTramo: number | null;
  /** El valor del campo vino de la cotización, no del teclado: al crear se
   *  manda vacío para que el backend resuelva (tramos incluidos) — el número
   *  en pantalla es informativo, no una orden de cobrar eso. */
  precioAuto: boolean;
  notas: string | null;
  candidatos: LineaOC["candidatos"];
  /** El operador pidió buscar fuera de los candidatos sugeridos. */
  buscando: boolean;
  /** Partida que el operador agregó a mano: el documento no la traía (el bot
   *  puede saltarse un renglón). No aprende alias ni clave — no hay texto del
   *  cliente del cual aprender. */
  agregada: boolean;
};

/** De dónde salió el cruce de una partida, en palabras del negocio: se lee
 *  mejor que un porcentaje. Lo que no está aquí (difuso, IA) cae al score. */
export const ORIGEN_CRUCE: Record<string, string> = {
  codigo_cliente: "su clave",
  codigo_otro_cliente: "clave de otro cliente",
  alias: "aprendido",
  exacto: "exacto",
};

// Abreviaturas con las que los clientes escriben la unidad en sus órdenes.
const UNIDAD_ALIAS: Record<string, string> = {
  KG: "KILO", KGS: "KILO", KILOS: "KILO", KGM: "KILO",
  PZ: "PIEZA", PZA: "PIEZA", PZAS: "PIEZA", PIEZAS: "PIEZA", H87: "PIEZA",
  CJ: "CAJA", CJA: "CAJA", CAJAS: "CAJA",
  BTO: "BULTO", BULTOS: "BULTO", COSTALES: "COSTAL",
  LT: "LITRO", LTS: "LITRO", LITROS: "LITRO",
  MJO: "MANOJO", MANOJOS: "MANOJO", BOLSAS: "BOLSA",
};

/** La unidad de la orden traducida a una presentación que el producto tenga.
 *  Sin coincidencia se usa la presentación default del producto — nunca se
 *  asume KILO, porque 5 CAJA registradas como 5 KILO son 95 kg de menos. */
export function presentacionDe(unidad: string, cand?: LineaOC["candidatos"][number]): string {
  const u = (unidad || "").trim().toUpperCase();
  const norm = UNIDAD_ALIAS[u] ?? u;
  const mapa = cand?.presentaciones ?? {};
  const claves = Object.keys(mapa);
  const hit = claves.find((k) => k.toUpperCase() === norm);
  return hit ?? cand?.presentacion_default ?? claves[0] ?? "KILO";
}

/** La tabla de partidas a partir del detalle del servidor.
 *
 *  El cruce depende del CLIENTE: candidatos, preselección y presentación se
 *  calculan contra SU catálogo y SU vocabulario. Si el operador cambia de
 *  cliente, la tabla tiene que rehacerse — dejarla colgada hacía que el banner
 *  dijera una cosa y la tabla otra, y la remisión salía con el producto del
 *  cliente anterior. */
export function tablaDe(oc: OCRecibidaDetalle): LineaEdit[] {
  return oc.lineas.map((l) => {
    // Se preselecciona el mejor candidato solo si es un cruce fuerte (exacto o
    // alias ya confirmado). Un difuso al 76% lo revisa la persona.
    const fuerte = l.candidatos[0] && l.candidatos[0].score >= 96 ? l.candidatos[0] : undefined;
    const unidad = l.unidad ?? "";
    // El backend ya tradujo la unidad del documento (incluido el OCR partido).
    // Se acepta solo si el producto preseleccionado de verdad la vende: una
    // presentación que no existe entra como cantidad y precio equivocados.
    const sugerida = l.presentacion_sugerida ?? "";
    const vendidas = Object.keys(fuerte?.presentaciones ?? {});
    const sirve =
      sugerida && (!fuerte || vendidas.some((k) => k.toUpperCase() === sugerida.toUpperCase()));
    return {
      numero: l.numero,
      texto: l.descripcion,
      cantidad: String(l.cantidad ?? ""),
      unidad,
      clave: l.clave ?? null,
      producto_id: fuerte ? fuerte.producto_id : "",
      presentacion: sirve ? sugerida : presentacionDe(unidad, fuerte),
      presentaciones: Object.keys(fuerte?.presentaciones ?? {}),
      precio: l.precio != null ? String(l.precio) : "",
      precioLista: null,
      precioListaId: null,
      precioTramo: null,
      precioAuto: false,
      notas: l.notas ?? null,
      candidatos: l.candidatos,
      buscando: false,
      agregada: false,
    };
  });
}

/** Las unidades base que ofrece el alta rápida de producto. */
const UNIDADES_BASE = ["KILO", "PIEZA", "LITRO", "CAJA", "BULTO", "COSTAL", "MANOJO", "BOLSA"];

/** La unidad del documento traducida a una unidad base del catálogo, para que
 *  el alta de producto arranque con la que trae la orden ("PIEZAS" → PIEZA) en
 *  vez de con KILO. Sin traducción posible, KILO: es la unidad de casi todo lo
 *  que se vende, y de todos modos el operador la ve y la puede cambiar. */
export function unidadBaseDe(unidad: string): string {
  const u = (unidad || "").trim().toUpperCase();
  const norm = UNIDAD_ALIAS[u] ?? u;
  return UNIDADES_BASE.includes(norm) ? norm : "KILO";
}

/** Lo que le falta a una partida para poder salir en la remisión, dicho como lo
 *  que hay que HACER. `null` = la partida está lista.
 *
 *  Antes el único aviso era un contador ámbar arriba de la tabla ("2 sin
 *  cruzar"): con quince renglones en pantalla había que recorrerlos a ojo para
 *  encontrar cuáles eran. El renglón se marca en rojo y dice su salida. */
export function problemaDe(l: LineaEdit): { corto: string; comoResolver: string } | null {
  if (l.producto_id) return null;
  // Con candidatos el desplegable ya trae la respuesta: es un clic, no una búsqueda.
  if (l.candidatos.length && !l.buscando)
    return {
      corto: "Sin cruzar",
      comoResolver: "Elige el producto en la lista de al lado y sigue.",
    };
  return {
    corto: "No está en el catálogo",
    comoResolver: "Búscalo por otro nombre, o dalo de alta con «Crear Producto Nuevo».",
  };
}

/** Precio tecleado a la mexicana ("1,234.50" o "12,50") → decimal del backend. */
export function precioNormalizado(v: string): string {
  const t = v.trim();
  if (!t) return "";
  if (t.includes(",") && !t.includes(".")) return t.replace(",", ".");
  return t.replace(/,/g, "");
}

/** El problema que reportó el servidor, contrastado con lo que el operador
 *  tiene tecleado AHORA. El servidor evalúa la orden como está GUARDADA; si ya
 *  se corrigió en la tabla, el rojo se apaga sin recargar ni volver a guardar.
 *  `null` = ese problema ya no aplica. */
export function problemaVivo(p: ProblemaLinea | undefined, l: LineaEdit): ProblemaLinea | null {
  if (!p) return null;
  if (p.tipo === "precio_conflicto") {
    const v = precioNormalizado(l.precio);
    // En blanco = que mande la lista, que es justo lo que se discutía.
    if (!v) return null;
    const doc = Number(v);
    const lista = Number(p.precio_lista ?? "");
    if (!Number.isFinite(doc) || !Number.isFinite(lista)) return p;
    return Math.abs(doc - lista) > 0.01 ? p : null;
  }
  // Cruzar la partida a mano resuelve tanto el "no cruza" como la ambigüedad.
  if ((p.tipo === "sin_cruce" || p.tipo === "ambiguo") && l.producto_id) return null;
  return p;
}

export function estadoTexto(oc: OCRecibida): { texto: string; tone: "success" | "muted" | "danger" | "warning" } {
  if (oc.estado === "ASIGNADA") return { texto: `Remisión ${oc.remision_folio ?? "creada"}`, tone: "success" };
  if (oc.estado === "DESCARTADA") return { texto: "Descartada", tone: "muted" };
  if (oc.ambiguo) return { texto: "Ambigua", tone: "danger" };
  if (!oc.cliente_id) return { texto: "Sin cliente", tone: "warning" };
  return { texto: "Por revisar", tone: "warning" };
}

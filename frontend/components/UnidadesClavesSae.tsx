"use client";

// «Unidades y claves SAE» del editor de producto (2-oct-2026).
//
// Regla del dueño: en el tenant dueño de SAE NINGÚN producto activo se guarda
// sin clave SAE, y cada UNIDAD lleva la suya. KILO y PIEZA son dos artículos en
// SAE (TORTILLABURREKG / TORTILLABURREPZ), así que la sección es una fila por
// unidad —la base, fija con factor 1, y cada presentación— y cada fila termina
// con su clave.
//
// El camino que se empuja es LIGAR una clave que ya existe: casi siempre el
// artículo está en SAE con otro nombre, y crear otro igual es el duplicado que
// después nadie sabe cuál facturar. «Crear clave nueva en SAE» sólo aparece al
// pie de la búsqueda, cuando ninguna sirvió. Y aquí no se escribe en SAE: el
// producto se guarda YA con su clave (así no existe sin ella) y el alta se
// encola junto con él; una escritura a SAE nunca se reintenta.
//
// Al ligar se enseña «Así está en SAE» contra lo capturado en Datos. Si la
// clave SAT o el esquema no coinciden, o se usa lo de SAE o se pide el cambio
// allá: lo que no se ve al ligar es lo que después sale mal en la factura.
//
// Para un tenant SIN SAE la sección es la de siempre: la clave es opcional y
// no hay altas ni «Así está en SAE».

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, Plus, Search, Trash2, X } from "lucide-react";

import { ClaveSaeInline } from "@/components/ClaveSaeInline";
import { useDescripcionSat } from "@/components/DescripcionSat";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox, Input, Select } from "@/components/ui/Field";
import { ApiError, apiFetch } from "@/lib/api";

// ── Constantes ───────────────────────────────────────────────────────────────

/** Las cuatro empresas del SAE 10, que es donde se escribe. */
export const EMPRESAS_SAE = ["02", "03", "04", "05"] as const;

/** Las unidades que el escritor sabe traducir a UNI_MED
 *  (sae_escritura.UNIDADES_CANONICAS). */
export const UNIDADES_SAE = ["CAJA", "KILO", "LITRO", "PAQUETE", "PIEZA"] as const;
export type UnidadSae = (typeof UNIDADES_SAE)[number];

/** Unidades de inventario que se ofrecen como base o presentación. */
export const UNIDADES_INVENTARIO = [
  "KILO", "PIEZA", "LITRO", "GRAMO", "MILILITRO", "CAJA", "BULTO", "COSTAL",
  "PAQUETE", "MANOJO", "MALLA", "REJA", "DOCENA", "ATADO",
];

/** Una clave NUEVA, como la acepta el escritor (sae_escritura._RE_CLAVE). Las
 *  viejas con punto se pueden ligar, pero no crear. */
export const RE_CLAVE_NUEVA = /^[A-Z0-9-]{1,16}$/;
const RE_LINEA = /^[A-Z0-9]{1,10}$/;

// Formato del dueño: producto + unidad. KILO→KG, PIEZA→PZ…
const SUFIJO_UNIDAD: Record<string, string> = {
  KILO: "KG", PIEZA: "PZ", CAJA: "CJ", LITRO: "LT", PAQUETE: "PQ",
  MANOJO: "MJ", BOLSA: "BS", BULTO: "BT", COSTAL: "CS",
};

// Para leer la unidad de una clave por su sufijo. Las más largas primero.
const SUFIJOS_CLAVE: [string, string][] = [
  ["KGS", "KILO"], ["KG", "KILO"], ["PZA", "PIEZA"], ["PZ", "PIEZA"], ["CJ", "CAJA"],
  ["LT", "LITRO"], ["PQ", "PAQUETE"], ["MJ", "MANOJO"], ["BS", "BOLSA"],
  ["BT", "BULTO"], ["CS", "COSTAL"],
];

// La unidad SAT que pone el escritor según la unidad, si no se le manda otra.
const SAT_UNIDAD_DE: Record<UnidadSae, string> = {
  KILO: "KGM", PIEZA: "H87", CAJA: "XBX", LITRO: "LTR", PAQUETE: "XPK",
};

// Palabras que no aportan a una clave de 16 caracteres.
const VACIAS = new Set([
  "DE", "DEL", "LA", "EL", "LOS", "LAS", "EN", "CON", "Y", "A", "PARA", "POR", "GRANEL", "BOTELLA",
]);
// La unidad ya va en el sufijo: repetirla en el cuerpo gasta caracteres.
const PALABRAS_UNIDAD = new Set([
  "KILO", "KILOS", "KG", "KGS", "KILOGRAMO", "KILOGRAMOS", "PIEZA", "PIEZAS", "PZ", "PZA", "PZAS",
  "CAJA", "CAJAS", "LITRO", "LITROS", "PAQUETE", "PAQUETES", "MANOJO", "MANOJOS", "BOLSA",
  "BOLSAS", "BULTO", "BULTOS", "COSTAL", "COSTALES",
]);
// «750 ML» → «750ML»: la medida es parte del artículo y no se abrevia.
const RE_MEDIDA_SUELTA = /(\d)\s+(ML|MLS|L|LT|LTS|G|GR|GRS|KG|KGS|MG|OZ|CM|MM|M|PZ|PZS|PZA|PZAS)\b/g;

// ── Tipos ────────────────────────────────────────────────────────────────────

export type EstadoFila = "vacia" | "ligada" | "nueva" | "existente_sin_verificar";

export type EmpresasEspejo = Record<string, { activa: boolean; descripcion?: string | null }>;

/** Los datos del alta de una fila 'nueva'. Esquema, clave SAT y unidad SAT NO
 *  van aquí: salen de Datos al guardar. */
export type AltaFila = {
  /** La clave la sugirió el sistema: sigue al nombre hasta que se edita. */
  claveAuto: boolean;
  descripcion: string;
  descripcionAuto: boolean;
  unidad: UnidadSae | "";
  linea: string;
  /** Las empresas que eligió el usuario. NO se tocan solas: donde la clave ya
   *  existe se descuenta con `existeEn` al armar el alta. */
  empresas: string[];
  /** Dónde existe ya la clave según el espejo (lo pone el panel del alta).
   *  Vale sólo mientras la clave sea esa: si se edita, deja de contar y las
   *  empresas que el usuario marcó vuelven solas. */
  existeEn?: { clave: string; empresas: string[] } | null;
};

/** Las empresas a las que de verdad se pide el alta: las marcadas, menos donde
 *  la clave ya existe (viva o de baja, ahí no se puede insertar otra vez). */
export function empresasDelAlta(alta: AltaFila, clave: string): string[] {
  const c = normalizarClave(clave);
  const ya = alta.existeEn && alta.existeEn.clave === c ? alta.existeEn.empresas : [];
  return alta.empresas.filter((e) => !ya.includes(e)).sort();
}

/** «Dejar la mía y pedir cambio en SAE»: qué se pide cambiar y lo que SAE
 *  tenía al ligar, para no pedir un cambio que ya no hace falta. */
export type CambioFila = {
  sat: boolean;
  esquema: boolean;
  empresas: string[];
  saeSat: string | null;
  saeEsquema: number | null;
};

export type FilaClave = {
  key: string;
  esBase: boolean;
  unidad: string;
  factor: string;
  clave: string;
  estado: EstadoFila;
  alta: AltaFila | null;
  pedirCambio: CambioFila | null;
  /** En qué empresas vive la clave según el espejo (sólo al ligar aquí). */
  sae: EmpresasEspejo | null;
  /** Lo demás de la presentación rica ({sat, estimado…}): se conserva tal cual. */
  extra: Record<string, unknown>;
};

/** Un alta que viaja en POST/PATCH /productos (`altas_sae`). */
export type AltaSaePedida = {
  clave: string;
  unidad: UnidadSae;
  descripcion: string;
  linea: string;
  empresas: string[];
  sat_unidad: string | null;
};

/** Lo que el backend contesta en `altas_sae` (AltaSaeOut). */
export type AltaSaeResumen = {
  id: string;
  clave: string;
  estado: string;
  tipo?: string;
  empresas: string[];
  solicitada_at?: string;
  motivo?: string | null;
};

/** Un cambio que se pide con POST /productos/cambio-sae. */
export type CambioSaePedido = {
  clave: string;
  sat?: string;
  esquema?: number;
  empresas: string[];
  producto_id: string;
  origen: "UI";
};

/** GET /productos/claves-sae/estado: una clave y cómo va en SAE. */
export type ClaveSaeEstado = {
  clave: string;
  empresas: Record<string, { activa: boolean }>;
  solicitud: {
    id: string;
    tipo: string;
    estado: string;
    empresas: string[];
    solicitada_at: string;
    motivo?: string | null;
  } | null;
};

/** GET /productos/claves-sae (el espejo). */
export type ClaveBuscada = {
  clave: string;
  descripcion?: string | null;
  empresas: EmpresasEspejo;
  producto_id?: string | null;
  producto_nombre?: string | null;
};

/** GET /productos/claves-sae/{clave}/en-sae (SAE en vivo). */
export type EmpresaEnSae = {
  existe: boolean;
  activa: boolean | null;
  descripcion: string | null;
  unidad: string | null;
  unidad_canonica: string | null;
  linea: string | null;
  esquema: number | null;
  esquema_descripcion: string | null;
  sat: string | null;
  sat_descripcion: string | null;
  sat_unidad: string | null;
  sat_unidad_descripcion: string | null;
};
export type ArticuloSae = {
  clave: string;
  disponible: boolean;
  motivo: string | null;
  empresas: Record<string, EmpresaEnSae>;
};

type LineaSae = { codigo: string; nombre: string };

// ── Utilidades (exportadas: las usan la página y el alta rápida) ─────────────

/** MAYÚSCULAS sin acentos, como SAE guarda claves y descripciones. */
export const mayusSinAcentos = (s: string) =>
  (s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();

/** Una clave que YA existe (ligada o guardada), como la guarda y compara el
 *  backend (norm_clave): trim + mayúsculas, nada más. Una clave vieja de SAE
 *  con Ñ, acento o un espacio adentro existe ASÍ en INVE; «limpiarla» la
 *  volvería otra clave que SAE no tiene, y al guardar se reescribiría
 *  (2-oct-2026). */
export const normalizarClave = (c: string | null | undefined) =>
  (c ?? "").trim().toUpperCase();

/** Una clave NUEVA, mientras se teclea: sin acentos (Ñ→N) y sólo lo que el
 *  escritor acepta (A-Z, 0-9 y guion). Sólo para altas: las existentes van
 *  con normalizarClave. */
export const limpiarClaveNueva = (c: string | null | undefined) =>
  mayusSinAcentos(c ?? "").replace(/[^A-Z0-9-]/g, "");

export function unidadCanonica(u: string | null | undefined): UnidadSae | "" {
  const x = mayusSinAcentos(u ?? "").trim();
  if (["KG", "KGS", "KILO", "KILOS", "KILOGRAMO", "KILOGRAMOS"].includes(x)) return "KILO";
  if (["PZ", "PZA", "PZAS", "PIEZA", "PIEZAS"].includes(x)) return "PIEZA";
  if (["CJ", "CAJA", "CAJAS"].includes(x)) return "CAJA";
  if (["LT", "LTS", "LITRO", "LITROS"].includes(x)) return "LITRO";
  if (["PQ", "PAQUETE", "PAQUETES"].includes(x)) return "PAQUETE";
  return "";
}

function sufijoDeUnidad(unidad: string): string {
  return SUFIJO_UNIDAD[unidadCanonica(unidad) || mayusSinAcentos(unidad).trim()] ?? "";
}

/** La unidad que dice el sufijo de una clave (SANDIAPZ → PIEZA). Un sufijo
 *  pegado a un número es una medida, no la unidad: ACEITE1LT es una PIEZA. */
export function unidadDeClave(clave: string): string | null {
  const c = normalizarClave(clave);
  for (const [suf, uni] of SUFIJOS_CLAVE) {
    if (c.length > suf.length && c.endsWith(suf) && !/\d/.test(c[c.length - suf.length - 1])) return uni;
  }
  return null;
}

/**
 * La clave que se le sugiere a un alta: producto + unidad, en 16 caracteres.
 * «Tortilla burrera», KILO → TORTILLABURREKG; «Aceite de oliva 750 ml», PIEZA →
 * ACEITEOLI750MLPZ. Se quitan acentos, puntos y palabras vacías; la medida
 * («750 ML») se junta y nunca se abrevia; si no cabe, se acortan las palabras
 * que siguen a la primera (a 5, 4, 3 letras), luego se sueltan palabras del
 * final y sólo al último se acorta la primera. Es una SUGERENCIA: se edita.
 */
export function sugerirClaveSae(nombre: string, unidad: string): string {
  const MAX = 16;
  const sufijo = sufijoDeUnidad(unidad);
  const limpio = mayusSinAcentos(nombre ?? "")
    .replace(RE_MEDIDA_SUELTA, "$1$2")
    .replace(/\./g, "")
    .replace(/[^A-Z0-9]+/g, " ");
  const tokens = limpio.split(" ").filter((w) => w && !VACIAS.has(w) && !PALABRAS_UNIDAD.has(w));
  if (!tokens.length) return "";
  const esMedida = (w: string) => /^\d/.test(w);
  const primera = tokens.findIndex((w) => !esMedida(w));
  const largo = (ts: string[]) => ts.join("").length + sufijo.length;
  const armar = (ts: string[]) => ts.join("") + sufijo;
  if (largo(tokens) <= MAX) return armar(tokens);

  // Las palabras que se pueden abreviar o soltar: todas menos la primera y las
  // medidas. Van siempre DESPUÉS de la primera (antes de ella sólo puede haber
  // medidas), así que soltarlas no le mueve el índice.
  const sueltas = tokens.map((w, i) => i).filter((i) => i !== primera && !esMedida(tokens[i]));
  let ts = tokens;
  for (let k = 0; k <= sueltas.length; k++) {
    const fuera = new Set(sueltas.slice(sueltas.length - k));
    const quedan = tokens.filter((_, i) => !fuera.has(i));
    for (let cap = 5; cap >= 3; cap--) {
      ts = quedan.map((w, i) => (i === primera || esMedida(w) ? w : w.slice(0, cap)));
      if (largo(ts) <= MAX) return armar(ts);
    }
  }
  // Ni así: la primera palabra también se acorta (hasta 4) y, al final, se corta.
  if (primera >= 0) {
    for (let cap = tokens[primera].length - 1; cap >= 4; cap--) {
      ts = ts.map((w, i) => (i === primera ? tokens[primera].slice(0, cap) : w));
      if (largo(ts) <= MAX) return armar(ts);
    }
  }
  return (ts.join("").slice(0, Math.max(1, MAX - sufijo.length)) + sufijo).slice(0, MAX);
}

/** La descripción automática del alta: la base, el nombre; otra unidad, el
 *  nombre + la unidad. MAYÚSCULAS sin acentos y hasta 60, como en SAE. */
function descripcionAutomatica(nombre: string, unidad: string, esBase: boolean): string {
  const n = mayusSinAcentos(nombre).replace(/\s+/g, " ").trim();
  const u = mayusSinAcentos(unidad).trim();
  return (esBase || !u ? n : `${n} ${u}`).slice(0, 60);
}

export function descripcionAlta(f: FilaClave, nombre: string): string {
  if (!f.alta) return "";
  return f.alta.descripcionAuto
    ? descripcionAutomatica(nombre, f.unidad, f.esBase)
    : mayusSinAcentos(f.alta.descripcion).slice(0, 60);
}

let seq = 0;
const nuevaKey = () => `u${Date.now().toString(36)}${(seq++).toString(36)}`;

function filaVacia(esBase: boolean, unidad: string, factor: string, clave = "", extra: Record<string, unknown> = {}): FilaClave {
  const c = normalizarClave(clave);
  return {
    key: nuevaKey(), esBase, unidad, factor, clave: c,
    estado: c ? "existente_sin_verificar" : "vacia",
    alta: null, pedirCambio: null, sae: null, extra,
  };
}

/** La fila de la unidad base de un producto nuevo. */
export function filaBase(unidad: string): FilaClave {
  return filaVacia(true, unidad, "1");
}

/** Una fila de presentación sin nada (botón «Agregar unidad»). */
export function filaPresentacion(): FilaClave {
  return filaVacia(false, "", "");
}

/** Las filas de un producto guardado: la base y cada presentación con su clave.
 *  Soporta la forma simple (número) y la rica ({factor, clave_sae, sat…}). */
export function filasDesdeProducto(p: {
  unidad_base?: string | null;
  clave_sae?: string | null;
  presentaciones?: Record<string, unknown> | null;
}): FilaClave[] {
  const base = p.unidad_base || "KILO";
  const out = [filaVacia(true, base, "1", p.clave_sae ?? "")];
  for (const [nombre, v] of Object.entries(p.presentaciones ?? {})) {
    if (nombre === base) continue;
    if (v && typeof v === "object") {
      const { factor, clave_sae, ...extra } = v as { factor?: number; clave_sae?: string };
      out.push(filaVacia(false, nombre, String(factor ?? 1), clave_sae ?? "", extra));
    } else {
      out.push(filaVacia(false, nombre, String(v)));
    }
  }
  return out;
}

/** Cambia la unidad base desde Datos. Si la fila tenía un alta con la unidad
 *  canónica de antes, el alta sigue a la nueva (o queda por elegir). */
export function cambiarUnidadBase(filas: FilaClave[], unidad: string): FilaClave[] {
  return filas.map((f) => {
    if (!f.esBase) return f;
    const alta = f.alta && f.alta.unidad === unidadCanonica(f.unidad)
      ? { ...f.alta, unidad: unidadCanonica(unidad) }
      : f.alta;
    return { ...f, unidad, alta };
  });
}

/** El mapa `presentaciones` y la clave de la base para el POST/PATCH. */
export function armarPresentaciones(filas: FilaClave[]): {
  unidadBase: string;
  claveBase: string | null;
  presentaciones: Record<string, number | Record<string, unknown>>;
  error: string | null;
} {
  const base = filas.find((f) => f.esBase);
  const unidadBase = (base?.unidad ?? "").trim() || "KILO";
  const presentaciones: Record<string, number | Record<string, unknown>> = { [unidadBase]: 1 };
  const fuera = (error: string) => ({ unidadBase, claveBase: null, presentaciones, error });
  for (const f of filas) {
    if (f.esBase) continue;
    const nombre = f.unidad.trim();
    const clave = normalizarClave(f.clave);
    if (!nombre) {
      if (clave) return fuera(`Elige la unidad de la clave ${clave}`);
      continue;   // renglón agregado y dejado en blanco: no es nada
    }
    if (nombre in presentaciones) return fuera(`${nombre} está dos veces en las unidades`);
    const factor = Number(f.factor);
    if (!Number.isFinite(factor) || factor <= 0) {
      return fuera(`Factor inválido para "${nombre}" (debe ser mayor a 0)`);
    }
    // Número a secas sólo si no hay nada más que guardar (la forma de siempre).
    presentaciones[nombre] = clave || Object.keys(f.extra).length
      ? { ...f.extra, factor, ...(clave ? { clave_sae: clave } : {}) }
      : factor;
  }
  return { unidadBase, claveBase: normalizarClave(base?.clave) || null, presentaciones, error: null };
}

/** Las unidades que se quedarían sin clave SAE (para «Falta la clave SAE de
 *  KILO, PIEZA»). Sin SAE conectado la clave no se exige: lista vacía. */
export function validarClavesSae(filas: FilaClave[], saeConectado: boolean): string[] {
  if (!saeConectado) return [];
  return filas
    .filter((f) => (f.esBase || f.unidad.trim()) && !normalizarClave(f.clave))
    .map((f) => f.unidad.trim() || "la unidad base");
}

/** Lo que falta para poder guardar las claves: claves repetidas entre
 *  unidades y altas incompletas. */
export function problemasClavesSae(filas: FilaClave[], nombre: string, saeConectado: boolean): string[] {
  if (!saeConectado) return [];
  const out: string[] = [];
  const vistas = new Map<string, string>();
  for (const f of filas) {
    const c = normalizarClave(f.clave);
    if (!c) continue;
    const otra = vistas.get(c);
    if (otra !== undefined) out.push(`${c} está en ${otra} y en ${f.unidad}: cada unidad lleva su propia clave`);
    else vistas.set(c, f.unidad);
  }
  for (const f of filas) {
    if (f.estado !== "nueva" || !f.alta) continue;
    const u = f.unidad.trim() || "la unidad nueva";
    const c = normalizarClave(f.clave);
    if (!RE_CLAVE_NUEVA.test(c)) out.push(`La clave nueva de ${u} no sirve: sólo A-Z, 0-9 y guion, hasta 16`);
    if (!descripcionAlta(f, nombre).trim()) out.push(`Falta la descripción en SAE de ${c || u}`);
    if (!f.alta.unidad) out.push(`Elige la unidad en SAE de ${c || u}`);
    if (!RE_LINEA.test(f.alta.linea)) out.push(`Elige la línea de SAE de ${c || u}`);
    if (!empresasDelAlta(f.alta, c).length) {
      out.push(f.alta.empresas.length
        ? `${c} ya existe en SAE en las empresas marcadas: lígala en lugar de pedir el alta`
        : `Marca al menos una empresa para el alta de ${c || u}`);
    }
  }
  return out;
}

/** Las altas que viajan con el producto (`altas_sae`). La unidad SAT de la
 *  base es la del producto; la de otra unidad la pone el escritor según la
 *  unidad (PIEZA→H87, CAJA→XBX…). */
export function altasDesdeFilas(filas: FilaClave[], nombre: string, unidadSatBase: string): AltaSaePedida[] {
  return filas
    .filter((f) => f.estado === "nueva" && f.alta && f.alta.unidad)
    .map((f) => ({
      clave: normalizarClave(f.clave),
      unidad: f.alta!.unidad as UnidadSae,
      descripcion: descripcionAlta(f, nombre),
      linea: f.alta!.linea,
      empresas: empresasDelAlta(f.alta!, f.clave),
      sat_unidad: f.esBase ? unidadSatBase.trim() || null : null,
    }));
}

/** Los cambios en SAE que se marcaron con «Dejar la mía». Sólo lo que SIGUE
 *  siendo distinto al guardar: si después se usó lo de SAE, ya no se pide. */
export function cambiosSaeDesdeFilas(
  filas: FilaClave[],
  claveSat: string,
  esquemaCodigo: string | null,
  productoId: string,
): CambioSaePedido[] {
  const sat = claveSat.trim();
  const esq = /^\d+$/.test((esquemaCodigo ?? "").trim()) ? Number(esquemaCodigo) : null;
  const out: CambioSaePedido[] = [];
  for (const f of filas) {
    const c = f.pedirCambio;
    if (!c || f.estado === "nueva" || f.estado === "vacia") continue;
    const clave = normalizarClave(f.clave);
    if (!clave) continue;
    const pedido: CambioSaePedido = { clave, empresas: c.empresas, producto_id: productoId, origen: "UI" };
    if (c.sat && /^\d{8}$/.test(sat) && sat !== (c.saeSat ?? "").trim()) pedido.sat = sat;
    if (c.esquema && esq !== null && esq !== c.saeEsquema) pedido.esquema = esq;
    if (pedido.sat !== undefined || pedido.esquema !== undefined) out.push(pedido);
  }
  return out;
}

// ── Estado de una clave guardada ─────────────────────────────────────────────

export type EstadoClaveVista = {
  codigo: "ligada" | "alta_pendiente" | "alta_error" | "no_existe" | "sin_clave" | "desconocido";
  texto: string;
  tono: "success" | "accent" | "danger" | "warning" | "muted";
  detalle: string | null;
};

const fechaCorta = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" });
};

/** Cómo va una clave en SAE: estar en el espejo manda (Ligada), luego el alta
 *  viva, luego la última alta terminada. */
export function vistaDeEstado(e: ClaveSaeEstado): EstadoClaveVista {
  const donde = Object.keys(e.empresas ?? {})
    .filter((k) => (EMPRESAS_SAE as readonly string[]).includes(k))
    .sort();
  const s = e.solicitud;
  if (donde.length) {
    const baja = donde.filter((k) => !e.empresas[k].activa);
    return {
      codigo: "ligada", texto: "Ligada", tono: "success",
      detalle: `En SAE: ${donde.join(", ")}${baja.length ? ` · de baja en ${baja.join(", ")}` : ""}`,
    };
  }
  if (s && (s.estado === "PENDIENTE" || s.estado === "EN_CURSO")) {
    return {
      codigo: "alta_pendiente", texto: "Alta pendiente", tono: "accent",
      detalle: `Pedida el ${fechaCorta(s.solicitada_at)} para ${s.empresas.join(", ")}`,
    };
  }
  if (s && s.estado === "OK") {
    return { codigo: "ligada", texto: "Ligada", tono: "success", detalle: "Alta confirmada; el espejo de SAE todavía no la trae" };
  }
  if (s && (s.estado === "ERROR" || s.estado === "PARCIAL")) {
    return { codigo: "alta_error", texto: "Alta con error", tono: "danger", detalle: s.motivo || "SAE no la dio de alta" };
  }
  return { codigo: "no_existe", texto: "No existe en SAE", tono: "warning", detalle: null };
}

export function estadoDeClave(
  clave: string | null | undefined,
  estados: Record<string, ClaveSaeEstado> | null | undefined,
): EstadoClaveVista {
  const c = normalizarClave(clave);
  if (!c) return { codigo: "sin_clave", texto: "Sin clave", tono: "danger", detalle: null };
  const e = estados?.[c];
  if (!e) return { codigo: "desconocido", texto: "—", tono: "muted", detalle: null };
  return vistaDeEstado(e);
}

/** La lista del endpoint de estado, indexada por clave normalizada. */
export function indexarEstados(lista: ClaveSaeEstado[] | null | undefined): Record<string, ClaveSaeEstado> {
  const out: Record<string, ClaveSaeEstado> = {};
  for (const e of lista ?? []) out[normalizarClave(e.clave)] = e;
  return out;
}

// ── Lecturas ─────────────────────────────────────────────────────────────────

// «Así está en SAE» va EN VIVO a SAE por la red del Mini: lo leído se guarda
// cinco minutos para no repetir la consulta cada vez que se abre el panel.
const cacheEnSae = new Map<string, { t: number; data: ArticuloSae }>();

function useArticuloSae(clave: string | null) {
  const [res, setRes] = useState<{ clave: string; data: ArticuloSae } | null>(null);
  useEffect(() => {
    if (!clave) return;
    const hit = cacheEnSae.get(clave);
    if (hit && Date.now() - hit.t < 5 * 60_000) { setRes({ clave, data: hit.data }); return; }
    let vivo = true;
    apiFetch<ArticuloSae>(`/api/v1/productos/claves-sae/${encodeURIComponent(clave)}/en-sae`)
      .then((d) => {
        if (d.disponible) cacheEnSae.set(clave, { t: Date.now(), data: d });
        if (vivo) setRes({ clave, data: d });
      })
      .catch((e) => {
        // La pantalla degrada: sin lectura se puede ligar igual.
        if (vivo) setRes({
          clave,
          data: { clave, disponible: false, motivo: e instanceof ApiError ? e.message : "SAE no contestó", empresas: {} },
        });
      });
    return () => { vivo = false; };
  }, [clave]);
  return clave && res?.clave === clave ? res.data : null;
}

// Las líneas de SAE cambian casi nunca: una lectura por sesión basta.
let lineasCache: LineaSae[] | null = null;

// ── Piezas chicas ────────────────────────────────────────────────────────────

function ChipsEmpresas({ empresas }: { empresas: EmpresasEspejo | Record<string, { activa: boolean | null }> | null | undefined }) {
  const ks = EMPRESAS_SAE.filter((e) => empresas?.[e]);
  if (!ks.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {ks.map((e) => {
        const activa = empresas?.[e]?.activa !== false;
        return <Badge key={e} tone={activa ? "success" : "muted"}>{activa ? e : `${e} de baja`}</Badge>;
      })}
    </span>
  );
}

const BTN_CHICO = "px-2.5 py-1 text-xs";

function Renglon({
  etiqueta, valor, coincide, nota, acciones,
}: {
  etiqueta: string;
  valor: ReactNode;
  coincide?: boolean | null;
  nota?: ReactNode;
  acciones?: ReactNode;
}) {
  return (
    <>
      <dt className="text-muted">{etiqueta}</dt>
      <dd className="min-w-0">
        <div className="break-words">{valor}</div>
        {nota ? <div className="mt-0.5 text-xs text-warning">{nota}</div> : null}
        {acciones ? <div className="mt-1">{acciones}</div> : null}
      </dd>
      <dd className="text-right">
        {coincide === true ? <Badge tone="success">Coincide</Badge> : null}
        {coincide === false ? <Badge tone="warning">Distinta</Badge> : null}
      </dd>
    </>
  );
}

// ── «Así está en SAE» ────────────────────────────────────────────────────────

function AsiEstaEnSae({
  clave, unidadFila, esquemaCodigo, claveSat, espejo, cambio, onCambio, onUsarSat, onUsarEsquema,
}: {
  clave: string;
  unidadFila: string;
  esquemaCodigo: string | null;
  claveSat: string;
  /** Lo que dice el espejo, por si SAE no contesta. */
  espejo: EmpresasEspejo | null;
  cambio: CambioFila | null;
  onCambio: (c: CambioFila | null) => void;
  onUsarSat: (clave: string) => void;
  onUsarEsquema: (codigo: number) => void;
}) {
  const data = useArticuloSae(clave);
  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-surface-2/40 px-3 py-2 text-sm text-muted">
        Leyendo <span className="font-mono">{clave}</span> en SAE…
      </div>
    );
  }
  if (!data.disponible) {
    return (
      <Alert tone="warning" title="No pude leer SAE ahora">
        {data.motivo || "SAE no contestó"}. Puedes ligarla igual; la comparación con SAE queda pendiente.
      </Alert>
    );
  }
  const donde = EMPRESAS_SAE.filter((e) => data.empresas?.[e]?.existe);
  const emp = donde.includes("02") ? "02" : donde[0];
  if (!emp) {
    return (
      <Alert tone="warning" title={`${clave} no está en SAE`}>
        {espejo && Object.keys(espejo).length
          ? "El espejo la tenía, pero SAE ya no la encuentra en 02-05: revísala antes de ligarla."
          : "SAE no la encuentra en 02-05."}
      </Alert>
    );
  }
  const a = data.empresas[emp];
  const filaCanon = unidadCanonica(unidadFila);
  const saeCanon = unidadCanonica(a.unidad_canonica) || unidadCanonica(a.unidad);
  const esqMio = /^\d+$/.test((esquemaCodigo ?? "").trim()) ? Number(esquemaCodigo) : null;
  const satMio = claveSat.trim();
  const uniCoincide = filaCanon ? filaCanon === saeCanon : null;
  const esqCoincide = a.esquema != null ? esqMio === a.esquema : null;
  const satCoincide = a.sat ? satMio === a.sat.trim() : null;
  // POST /productos/cambio-sae pasa la clave por el escritor, que sólo acepta
  // A-Z, 0-9 y guion hasta 16. Una clave vieja (con punto, Ñ o más larga) se
  // puede ligar, pero no pedirle cambios desde aquí: ofrecerlo era guardar el
  // producto con lo mío y que el cambio tronara después, con SAE ya distinto.
  const cambioPosible = RE_CLAVE_NUEVA.test(clave);
  const TITULO_SIN_CAMBIO = "Esta clave es vieja (sólo A-Z, 0-9 y guion, hasta 16): SAE no deja pedirle cambios desde aquí";

  const empresasDonde = [...donde];
  const base: CambioFila = cambio
    ? { ...cambio, empresas: empresasDonde, saeSat: a.sat, saeEsquema: a.esquema }
    : { sat: false, esquema: false, empresas: empresasDonde, saeSat: a.sat, saeEsquema: a.esquema };
  const quitar = (campo: "sat" | "esquema") => {
    const n = { ...base, [campo]: false };
    onCambio(n.sat || n.esquema ? n : null);
  };

  return (
    <div className="rounded-lg border border-border bg-surface-2/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Así está en SAE</span>
        <span className="text-xs text-muted">(empresa {emp})</span>
        <span className="ml-auto">
          <ChipsEmpresas empresas={Object.fromEntries(donde.map((e) => [e, { activa: data.empresas[e].activa }]))} />
        </span>
      </div>
      <dl className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2 text-sm">
        <Renglon etiqueta="Descripción" valor={a.descripcion || "—"} />
        <Renglon
          etiqueta="Unidad"
          valor={<>{a.unidad || "—"}{saeCanon && saeCanon !== a.unidad ? <span className="text-muted"> ({saeCanon})</span> : null}</>}
          coincide={uniCoincide}
          nota={uniCoincide === false ? `Esta fila es ${unidadFila}: ligarla vendería ${unidadFila} con un artículo de ${saeCanon || a.unidad || "otra unidad"}` : null}
        />
        <Renglon
          etiqueta="Esquema de impuesto"
          valor={a.esquema != null ? `${a.esquema}${a.esquema_descripcion ? ` · ${a.esquema_descripcion}` : ""}` : "—"}
          coincide={esqCoincide}
          nota={esqCoincide === false
            ? esqMio === null ? "En Datos no hay esquema con número de SAE" : `En Datos: ${esqMio}`
            : null}
          acciones={esqCoincide === false && a.esquema != null ? (
            cambio?.esquema ? (
              <span className="text-xs text-blue-700">
                Se pedirá en SAE cambiarlo a {esqMio} al guardar ·{" "}
                <button type="button" className="underline" onClick={() => quitar("esquema")}>Deshacer</button>
              </span>
            ) : (
              <span className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" className={BTN_CHICO} onClick={() => onUsarEsquema(a.esquema!)}>
                  Usar la de SAE
                </Button>
                <Button
                  type="button" variant="ghost" className={BTN_CHICO}
                  disabled={esqMio === null || !cambioPosible}
                  title={cambioPosible ? undefined : TITULO_SIN_CAMBIO}
                  onClick={() => onCambio({ ...base, esquema: true })}
                >
                  Dejar la mía y pedir cambio en SAE
                </Button>
              </span>
            )
          ) : null}
        />
        <Renglon
          etiqueta="Clave SAT"
          valor={a.sat ? <><span className="font-mono">{a.sat}</span>{a.sat_descripcion ? ` · ${a.sat_descripcion}` : ""}</> : "—"}
          coincide={satCoincide}
          nota={satCoincide === false ? (satMio ? <>En Datos: <span className="font-mono">{satMio}</span></> : "En Datos no hay clave SAT") : null}
          acciones={satCoincide === false && a.sat ? (
            cambio?.sat ? (
              <span className="text-xs text-blue-700">
                Se pedirá en SAE cambiarla a <span className="font-mono">{satMio}</span> al guardar ·{" "}
                <button type="button" className="underline" onClick={() => quitar("sat")}>Deshacer</button>
              </span>
            ) : (
              <span className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" className={BTN_CHICO} onClick={() => onUsarSat(a.sat!.trim())}>
                  Usar la de SAE
                </Button>
                <Button
                  type="button" variant="ghost" className={BTN_CHICO}
                  disabled={!/^\d{8}$/.test(satMio) || !cambioPosible}
                  title={cambioPosible ? undefined : TITULO_SIN_CAMBIO}
                  onClick={() => onCambio({ ...base, sat: true })}
                >
                  Dejar la mía y pedir cambio en SAE
                </Button>
              </span>
            )
          ) : null}
        />
        {a.sat_unidad ? (
          <Renglon
            etiqueta="Unidad SAT"
            valor={<><span className="font-mono">{a.sat_unidad}</span>{a.sat_unidad_descripcion ? ` · ${a.sat_unidad_descripcion}` : ""}</>}
          />
        ) : null}
        {a.linea ? <Renglon etiqueta="Línea" valor={a.linea} /> : null}
      </dl>
      {a.activa === false ? (
        <p className="mt-2 text-xs text-warning">
          Está dada de baja en la empresa {emp}: no factura hasta que la reactiven en SAE.
        </p>
      ) : null}
      {!cambioPosible && (esqCoincide === false || satCoincide === false) ? (
        <p className="mt-2 text-xs text-warning">
          <span className="font-mono">{clave}</span> es una clave vieja: desde aquí no se le pueden
          pedir cambios en SAE. Usa lo de SAE, o cámbialo allá a mano antes de ligarla.
        </p>
      ) : null}
    </div>
  );
}

// ── Panel ámbar del alta ─────────────────────────────────────────────────────

function PanelAlta({
  fila, nombre, claveSat, esquemaCodigo, esquemaNombre, unidadSat, lineas, lineasError,
  onChange, onBuscarExistente, onLigar,
}: {
  fila: FilaClave;
  nombre: string;
  claveSat: string;
  esquemaCodigo: string | null;
  esquemaNombre: string | null;
  unidadSat: string;
  lineas: LineaSae[] | null;
  lineasError: string | null;
  onChange: (f: FilaClave) => void;
  onBuscarExistente: () => void;
  onLigar: (clave: string, empresas: EmpresasEspejo) => void;
}) {
  const alta = fila.alta!;
  const clave = normalizarClave(fila.clave);
  const claveOk = RE_CLAVE_NUEVA.test(clave);
  // ¿Ya existe en SAE? El espejo lo sabe al instante.
  const [existe, setExiste] = useState<{ clave: string; empresas: EmpresasEspejo } | null>(null);

  useEffect(() => {
    if (!claveOk) return;
    let vivo = true;
    const t = setTimeout(() => {
      apiFetch<ClaveBuscada[]>(`/api/v1/productos/claves-sae?${new URLSearchParams({ clave, limit: "5" })}`)
        .then((r) => {
          if (!vivo) return;
          const ex = r.find((c) => normalizarClave(c.clave) === clave);
          setExiste({ clave, empresas: ex?.empresas ?? {} });
        })
        .catch(() => { if (vivo) setExiste(null); });
    }, 300);
    return () => { vivo = false; clearTimeout(t); };
  }, [clave, claveOk]);

  const ya = existe && existe.clave === clave ? existe.empresas : null;
  const yaEn = EMPRESAS_SAE.filter((e) => ya?.[e]);

  // Donde ya existe (viva o de baja) no se puede insertar otra vez. Se ANOTA
  // junto con la clave, sin desmarcar nada: antes se desmarcaba sola y, si
  // luego cambiaba la clave (SANDIAKG → SANDIAAMARILLAKG), la 02 se quedaba
  // fuera del alta sin que nadie la hubiera quitado.
  const yaEnTxt = yaEn.join(",");
  useEffect(() => {
    if (!ya || !fila.alta) return;
    const prev = fila.alta.existeEn;
    if (prev && prev.clave === clave && prev.empresas.join(",") === yaEnTxt) return;
    onChange({ ...fila, alta: { ...fila.alta, existeEn: { clave, empresas: yaEnTxt ? yaEnTxt.split(",") : [] } } });
  }, [ya, clave, yaEnTxt, fila, onChange]);

  const sat = useDescripcionSat(claveSat);
  const setAlta = (cambios: Partial<AltaFila>) => onChange({ ...fila, alta: { ...alta, ...cambios } });
  const descripcion = descripcionAlta(fila, nombre);
  const satUnidad = fila.esBase ? unidadSat : alta.unidad ? SAT_UNIDAD_DE[alta.unidad] : "";

  return (
    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50/60 p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-amber-800">
          Crear en SAE · <span className="font-mono">{clave || "—"}</span>
        </span>
        <button type="button" onClick={onBuscarExistente} className="ml-auto text-xs text-accent hover:underline">
          Mejor buscar una existente
        </button>
      </div>

      {yaEn.length ? (
        <div className="mb-3">
          <Alert tone="warning" title={`${clave} ya existe en SAE — ligarla`}>
            <span className="inline-flex flex-wrap items-center gap-2">
              Está en <ChipsEmpresas empresas={ya} />
              <Button type="button" variant="secondary" className={BTN_CHICO} onClick={() => onLigar(clave, ya ?? {})}>
                <Check size={14} /> Ligar {clave} a {fila.unidad || "esta unidad"}
              </Button>
            </span>
            {yaEn.length < EMPRESAS_SAE.length ? (
              <span className="mt-1 block text-xs">
                Si sólo falta en otras empresas, déjala así: el alta se pide únicamente donde no está.
              </span>
            ) : null}
          </Alert>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Clave en SAE <span className="text-danger">*</span></span>
          <Input
            value={fila.clave}
            maxLength={16}
            className="font-mono"
            placeholder="AJOKG"
            onChange={(e) => onChange({
              ...fila,
              clave: limpiarClaveNueva(e.target.value),
              alta: { ...alta, claveAuto: false },
            })}
          />
          <span className={`mt-1 block text-xs ${clave && !claveOk ? "text-danger" : "text-muted"}`}>
            {clave && !claveOk ? "Sólo A-Z, 0-9 y guion; hasta 16." : `${clave.length}/16 · producto + unidad (${sufijoDeUnidad(fila.unidad) || "…"})`}
          </span>
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Unidad en SAE <span className="text-danger">*</span></span>
          <Select value={alta.unidad} onChange={(e) => setAlta({ unidad: e.target.value as UnidadSae | "" })}>
            <option value="">— Elige —</option>
            {UNIDADES_SAE.map((u) => <option key={u} value={u}>{u}</option>)}
          </Select>
        </label>
        <label className="block sm:col-span-2">
          <span className="mb-1 block text-sm font-medium">Descripción en SAE <span className="text-danger">*</span></span>
          <Input
            value={descripcion}
            maxLength={60}
            onChange={(e) => setAlta({ descripcion: mayusSinAcentos(e.target.value).slice(0, 60), descripcionAuto: false })}
          />
          <span className="mt-1 flex justify-between text-xs text-muted">
            <span>
              {alta.descripcionAuto ? "Sale del nombre del producto." : (
                <button type="button" className="text-accent hover:underline" onClick={() => setAlta({ descripcionAuto: true })}>
                  Volver a la del nombre
                </button>
              )}
            </span>
            <span>{descripcion.length}/60</span>
          </span>
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Línea de SAE <span className="text-danger">*</span></span>
          {lineas && lineas.length ? (
            <Select value={alta.linea} onChange={(e) => setAlta({ linea: e.target.value })}>
              <option value="">— Elige —</option>
              {lineas.map((l) => <option key={l.codigo} value={l.codigo}>{l.codigo} · {l.nombre}</option>)}
            </Select>
          ) : (
            <Input
              value={alta.linea}
              maxLength={10}
              placeholder={lineas === null && !lineasError ? "Leyendo líneas de SAE…" : "FRUVE"}
              onChange={(e) => setAlta({ linea: mayusSinAcentos(e.target.value).replace(/[^A-Z0-9]/g, "") })}
            />
          )}
          {lineasError ? (
            <span className="mt-1 block text-xs text-warning">
              No pude leer las líneas de SAE ({lineasError}). Escríbela tal como está en SAE.
            </span>
          ) : null}
        </label>
        <div className="text-sm">
          <span className="mb-1 block font-medium">Salen de Datos</span>
          <div className="space-y-0.5 text-xs text-muted">
            <div>
              Esquema: {esquemaCodigo
                ? <>{esquemaCodigo}{esquemaNombre ? ` · ${esquemaNombre}` : ""}</>
                : <span className="text-danger">elige el esquema de impuesto</span>}
            </div>
            <div>
              Clave SAT: {claveSat.trim() ? <span className="font-mono">{claveSat.trim()}</span> : <span className="text-danger">sin clave SAT</span>}
              {sat.descripcion ? ` · ${sat.descripcion}` : null}
              {sat.existe === false ? <span className="text-danger"> · no está en el catálogo del SAT</span> : null}
            </div>
            <div>
              Unidad SAT: {satUnidad ? <span className="font-mono">{satUnidad}</span> : "—"}
              {fila.esBase ? "" : " (por la unidad)"}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-3">
        <span className="mb-1 block text-sm font-medium">Empresas de SAE</span>
        <div className="flex flex-wrap gap-4">
          {EMPRESAS_SAE.map((e) => {
            const esta = ya?.[e];
            return (
              <label key={e} className={`flex items-center gap-2 text-sm ${esta ? "text-muted" : ""}`}>
                <Checkbox
                  disabled={!!esta}
                  checked={!esta && alta.empresas.includes(e)}
                  onChange={(ev) => setAlta({
                    empresas: ev.target.checked
                      ? [...alta.empresas.filter((x) => x !== e), e].sort()
                      : alta.empresas.filter((x) => x !== e),
                  })}
                />
                {e}
                {esta ? <Badge tone={esta.activa ? "success" : "muted"}>{esta.activa ? "ya está" : "de baja"}</Badge> : null}
              </label>
            );
          })}
        </div>
      </div>
      <p className="mt-2 text-xs text-amber-800">
        El producto se guarda ya con esta clave y el alta queda pedida; SAE la crea en uno o dos minutos.
      </p>
    </div>
  );
}

// ── Una fila (tenant con SAE) ────────────────────────────────────────────────

type ContextoFila = {
  nombre: string;
  productoId: string | null;
  esquemaCodigo: string | null;
  esquemaNombre: string | null;
  claveSat: string;
  unidadSat: string;
  lineas: LineaSae[] | null;
  lineasError: string | null;
  estados: Record<string, ClaveSaeEstado> | null | undefined;
  onUsarSat: (clave: string) => void;
  onUsarEsquema: (codigo: number) => void;
};

function FilaSae({
  fila, ctx, soloBase, unidadesOpciones, clavesOtras, onChange, onQuitar,
}: {
  fila: FilaClave;
  ctx: ContextoFila;
  soloBase: boolean;
  unidadesOpciones: string[];
  /** Claves de las OTRAS filas del producto → su unidad. */
  clavesOtras: Record<string, string>;
  onChange: (f: FilaClave) => void;
  onQuitar?: () => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [cambiando, setCambiando] = useState(false);
  const [texto, setTexto] = useState("");
  const [busqueda, setBusqueda] = useState<{ q: string; items: ClaveBuscada[] } | null>(null);
  const [cargando, setCargando] = useState(false);
  const [errorBusqueda, setErrorBusqueda] = useState<string | null>(null);
  const [candidata, setCandidata] = useState<ClaveBuscada | null>(null);
  const [cambioPrevio, setCambioPrevio] = useState<CambioFila | null>(null);
  const [verSae, setVerSae] = useState(false);
  const [estadoExacto, setEstadoExacto] = useState<ClaveSaeEstado | null>(null);

  const clave = normalizarClave(fila.clave);
  const buscando = (fila.estado === "vacia" || cambiando) && !candidata;

  // Búsqueda en el espejo: lo tecleado o, sin teclear, el nombre del producto.
  // ILIKE busca la frase entera, así que si el nombre completo no casa se
  // reintenta con su primera palabra (TORTILLA BURRERA → TORTILLA).
  useEffect(() => {
    if (!abierto || !buscando) return;
    const tecleado = mayusSinAcentos(texto).replace(/\s+/g, " ").trim();
    const nombreQ = mayusSinAcentos(ctx.nombre).replace(/\s+/g, " ").trim();
    const primera = nombreQ.split(" ").find((w) => w.length >= 3 && !VACIAS.has(w)) ?? "";
    const consultas = (tecleado ? [tecleado] : [nombreQ, primera])
      .map((q) => q.slice(0, 80))
      .filter((q, i, a) => q && a.indexOf(q) === i);
    if (!consultas.length) {
      setBusqueda(null);
      setCargando(false);
      return;
    }
    let vivo = true;
    setCargando(true);
    const t = setTimeout(async () => {
      try {
        let items: ClaveBuscada[] = [];
        let usada = consultas[0];
        for (const q of consultas) {
          usada = q;
          items = await apiFetch<ClaveBuscada[]>(
            `/api/v1/productos/claves-sae?${new URLSearchParams({ q, limit: "20" })}`,
          );
          if (items.length || !vivo) break;
        }
        if (!vivo) return;
        setBusqueda({ q: usada, items });
        setErrorBusqueda(null);
      } catch (e) {
        if (vivo) {
          setBusqueda({ q: consultas[0], items: [] });
          setErrorBusqueda(e instanceof ApiError ? e.message : "No pude buscar en SAE");
        }
      } finally {
        if (vivo) setCargando(false);
      }
    }, 250);
    return () => { vivo = false; clearTimeout(t); };
  }, [abierto, buscando, texto, ctx.nombre]);

  // Una clave guardada que el endpoint de estado no trae (o que todavía no
  // llega): se pregunta al espejo por la clave exacta.
  const estadoProp = fila.estado === "existente_sin_verificar" && clave ? ctx.estados?.[clave] : undefined;
  const pedirExacto = fila.estado === "existente_sin_verificar" && !!clave && !estadoProp && ctx.estados !== null;
  useEffect(() => {
    if (!pedirExacto) return;
    let vivo = true;
    apiFetch<ClaveBuscada[]>(`/api/v1/productos/claves-sae?${new URLSearchParams({ clave, limit: "5" })}`)
      .then((r) => {
        if (!vivo) return;
        const ex = r.find((c) => normalizarClave(c.clave) === clave);
        setEstadoExacto({ clave, empresas: ex?.empresas ?? {}, solicitud: null });
      })
      .catch(() => { if (vivo) setEstadoExacto(null); });
    return () => { vivo = false; };
  }, [pedirExacto, clave]);

  const estadoGuardado = estadoProp ?? (estadoExacto && estadoExacto.clave === clave ? estadoExacto : null);
  const vista: EstadoClaveVista | null =
    fila.estado === "existente_sin_verificar"
      ? estadoGuardado ? vistaDeEstado(estadoGuardado) : null
      : fila.estado === "ligada"
        ? { codigo: "ligada", texto: "Ligada", tono: "success", detalle: null }
        : null;
  const empresasDeLaClave: EmpresasEspejo | null =
    fila.estado === "ligada" ? fila.sae : estadoGuardado?.empresas ?? null;

  function abrirBusqueda() {
    setCandidata(null);
    setCambioPrevio(null);
    setTexto("");
    setAbierto(true);
  }

  function elegir(c: ClaveBuscada) {
    setCandidata(c);
    setCambioPrevio(null);
    setAbierto(false);
  }

  function ligar(claveLigada: string, empresas: EmpresasEspejo | null, cambio: CambioFila | null) {
    onChange({
      ...fila,
      clave: normalizarClave(claveLigada),
      estado: "ligada",
      alta: null,
      pedirCambio: cambio,
      sae: empresas,
    });
    setCandidata(null);
    setCambioPrevio(null);
    setCambiando(false);
    setAbierto(false);
    setVerSae(false);
  }

  function crearNueva(claveDada?: string) {
    const unidad = unidadCanonica(fila.unidad);
    onChange({
      ...fila,
      clave: claveDada ?? sugerirClaveSae(ctx.nombre, fila.unidad),
      estado: "nueva",
      alta: {
        claveAuto: claveDada === undefined,
        descripcion: "",
        descripcionAuto: true,
        unidad,
        linea: fila.alta?.linea ?? "",
        empresas: [...EMPRESAS_SAE],
        existeEn: null,
      },
      pedirCambio: null,
      sae: null,
    });
    setCandidata(null);
    setCambiando(false);
    setAbierto(false);
  }

  // ── Área de la clave, según el estado de la fila ──
  let area: ReactNode;
  if (candidata) {
    area = (
      <div className="flex flex-wrap items-center gap-2 py-1.5">
        <span className="font-mono text-sm font-medium">{normalizarClave(candidata.clave)}</span>
        <Badge tone="muted">Por ligar</Badge>
      </div>
    );
  } else if (buscando) {
    area = (
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            value={texto}
            className="pl-8"
            placeholder={ctx.nombre.trim() ? `Buscar en SAE: «${ctx.nombre.trim().slice(0, 30)}» o una clave…` : "Buscar en SAE: nombre o clave…"}
            onFocus={() => setAbierto(true)}
            onChange={(e) => { setTexto(e.target.value); setAbierto(true); }}
            onKeyDown={(e) => { if (e.key === "Escape") setAbierto(false); }}
          />
        </div>
        {cambiando ? (
          <Button type="button" variant="ghost" className={BTN_CHICO} onClick={() => { setCambiando(false); setAbierto(false); }}>
            Cancelar
          </Button>
        ) : null}
      </div>
    );
  } else {
    const puedeCrear = RE_CLAVE_NUEVA.test(clave);
    area = (
      <div className="flex flex-wrap items-center gap-2 py-1.5">
        <span className="font-mono text-sm font-medium">{clave}</span>
        {fila.estado === "nueva" ? <Badge tone="warning">Alta nueva</Badge> : null}
        {vista ? (
          <span title={vista.detalle ?? undefined}><Badge tone={vista.tono}>{vista.texto}</Badge></span>
        ) : fila.estado === "existente_sin_verificar" ? (
          <span className="text-xs text-muted">revisando…</span>
        ) : null}
        {vista?.codigo === "ligada" ? <ChipsEmpresas empresas={empresasDeLaClave} /> : null}
        <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {vista?.codigo === "ligada" ? (
            <button type="button" className="text-accent hover:underline" onClick={() => setVerSae((v) => !v)}>
              {verSae ? "Ocultar SAE" : "Ver en SAE"}
            </button>
          ) : null}
          {(vista?.codigo === "no_existe" || vista?.codigo === "alta_error") ? (
            <button
              type="button"
              disabled={!puedeCrear}
              title={puedeCrear
                ? vista.codigo === "alta_error" ? "Revisa en SAE que no haya quedado creada antes de pedirla otra vez" : undefined
                : "Esta clave no se puede crear (sólo A-Z, 0-9 y guion, hasta 16): búscale otra"}
              className="text-accent hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline"
              onClick={() => crearNueva(clave)}
            >
              {vista.codigo === "alta_error" ? "Pedirla de nuevo" : "Crear en SAE"}
            </button>
          ) : null}
          {fila.estado !== "nueva" ? (
            <button type="button" className="text-accent hover:underline" onClick={() => { setCambiando(true); abrirBusqueda(); }}>
              Cambiar
            </button>
          ) : null}
        </span>
      </div>
    );
  }

  // ── La lista de candidatas ──
  const filaUni = unidadCanonica(fila.unidad) || mayusSinAcentos(fila.unidad).trim();
  // La clave ya puesta dice OTRA unidad que la fila (2-oct-2026). El aviso de
  // la búsqueda sólo sale al elegir; si DESPUÉS se cambia la unidad (la base
  // desde Datos, o el selector de la fila), la clave se quedaba y la fila
  // seguía en «Ligada»: PIEZA → SANDIAKG sin que nadie lo notara.
  const uniClave = (fila.estado === "ligada" || fila.estado === "existente_sin_verificar") && clave
    ? unidadDeClave(clave)
    : null;
  const unidadChoca = !!uniClave && !!filaUni && uniClave !== filaUni;
  const lista = abierto && buscando ? (
    <div className="mt-2 overflow-hidden rounded-lg border border-border bg-background">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-surface-2/60 px-3 py-1.5">
        <span className="text-[11px] uppercase tracking-wide text-muted">
          Ya existen en SAE{busqueda?.q ? <> · buscando «{busqueda.q}»</> : null}
        </span>
        <button type="button" onClick={() => setAbierto(false)} className="rounded p-0.5 text-muted hover:text-foreground" aria-label="Cerrar la búsqueda">
          <X size={14} />
        </button>
      </div>
      <div className="max-h-72 overflow-auto">
        {cargando ? <div className="px-3 py-2 text-sm text-muted">Buscando…</div> : null}
        {!cargando && errorBusqueda ? <div className="px-3 py-2 text-sm text-danger">{errorBusqueda}</div> : null}
        {!cargando && !errorBusqueda && busqueda && busqueda.items.length === 0 ? (
          <div className="px-3 py-2 text-sm text-muted">Nada en SAE con «{busqueda.q}». Prueba otra palabra o una clave.</div>
        ) : null}
        {!cargando && !busqueda && !ctx.nombre.trim() && !texto.trim() ? (
          <div className="px-3 py-2 text-sm text-muted">Escribe el nombre del producto o una clave.</div>
        ) : null}
        {!cargando && (busqueda?.items ?? []).map((c) => {
          const k = normalizarClave(c.clave);
          const uni = unidadDeClave(k);
          const activas = Object.values(c.empresas ?? {}).some((e) => e.activa);
          const avisos = [
            uni && filaUni && uni !== filaUni ? `Es de ${uni} y esta fila es ${fila.unidad}` : null,
            clavesOtras[k] ? `ya está en la fila de ${clavesOtras[k]}` : null,
            c.producto_id && c.producto_id !== ctx.productoId ? `también la usa ${c.producto_nombre ?? "otro producto"}` : null,
            !activas ? "dada de baja en SAE: no factura" : null,
          ].filter(Boolean) as string[];
          return (
            <button
              key={k}
              type="button"
              onClick={() => elegir(c)}
              className="flex w-full items-start gap-3 border-b border-border/60 px-3 py-2 text-left last:border-b-0 hover:bg-surface-2"
            >
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-mono text-sm font-medium">{k}</span>
                  {uni ? <span className="text-xs text-muted">{uni}</span> : null}
                  <ChipsEmpresas empresas={c.empresas} />
                </span>
                {c.descripcion ? <span className="block truncate text-xs text-muted">{c.descripcion}</span> : null}
                {avisos.map((a) => <span key={a} className="block text-xs text-warning">{a}</span>)}
              </span>
              <Check size={14} className="mt-1 shrink-0 text-muted" />
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-surface-2/60 px-3 py-2">
        <span className="text-xs text-muted">¿Ninguna es este producto?</span>
        <Button type="button" variant="secondary" className={BTN_CHICO} onClick={() => crearNueva()}>
          <Plus size={14} /> Crear clave nueva en SAE
        </Button>
      </div>
    </div>
  ) : null;

  const gridCols = soloBase
    ? "sm:grid-cols-[8rem_minmax(0,1fr)]"
    : "sm:grid-cols-[9rem_6rem_minmax(0,1fr)_2rem]";

  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <div className={`grid grid-cols-1 gap-2 sm:items-start ${gridCols}`}>
        {fila.esBase ? (
          <div className="py-1.5 text-sm">
            <b>{fila.unidad || "—"}</b> <span className="text-xs text-muted">base</span>
          </div>
        ) : (
          <Select
            value={fila.unidad}
            onChange={(e) => {
              const u = e.target.value;
              const alta = fila.alta && fila.alta.unidad === unidadCanonica(fila.unidad)
                ? { ...fila.alta, unidad: unidadCanonica(u) }
                : fila.alta;
              onChange({ ...fila, unidad: u, alta });
            }}
          >
            <option value="">— Unidad —</option>
            {unidadesOpciones.map((u) => <option key={u} value={u}>{u}</option>)}
          </Select>
        )}
        {soloBase ? null : fila.esBase ? (
          <div className="py-1.5 text-sm text-muted">= 1</div>
        ) : (
          <Input
            type="number"
            step="0.0001"
            min="0"
            placeholder="Factor"
            title="Cuántas unidades base trae una"
            value={fila.factor}
            onChange={(e) => onChange({ ...fila, factor: e.target.value })}
          />
        )}
        <div className="min-w-0">{area}</div>
        {soloBase ? null : onQuitar ? (
          <button
            type="button"
            onClick={onQuitar}
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
            aria-label="Quitar unidad"
          >
            <Trash2 size={16} />
          </button>
        ) : <span />}
      </div>

      {fila.estado === "vacia" && !abierto && !candidata ? (
        <p className="mt-1 text-xs text-warning">
          Sin clave SAE. Búscala en SAE y lígala; sólo si no existe, pide una nueva.
        </p>
      ) : null}
      {unidadChoca && !buscando && !candidata ? (
        <p className="mt-1 text-xs text-warning">
          <span className="font-mono">{clave}</span> es de {uniClave} y esta fila es {fila.unidad}: en SAE
          son dos artículos distintos. Con «Cambiar» búscale la de {fila.unidad}.
        </p>
      ) : null}
      {vista && vista.codigo !== "ligada" && vista.detalle && !buscando ? (
        <p className={`mt-1 text-xs ${vista.codigo === "alta_error" ? "text-danger" : "text-muted"}`}>{vista.detalle}</p>
      ) : null}
      {fila.pedirCambio && (fila.pedirCambio.sat || fila.pedirCambio.esquema) && !verSae && !buscando ? (
        <p className="mt-1 text-xs text-blue-700">
          Al guardar se pedirá cambiar en SAE {[
            fila.pedirCambio.sat ? "la clave SAT" : null,
            fila.pedirCambio.esquema ? "el esquema" : null,
          ].filter(Boolean).join(" y ")} ({fila.pedirCambio.empresas.join(", ")}).
        </p>
      ) : null}

      {lista}

      {candidata ? (
        <div className="mt-2 space-y-2">
          <AsiEstaEnSae
            clave={normalizarClave(candidata.clave)}
            unidadFila={fila.unidad}
            esquemaCodigo={ctx.esquemaCodigo}
            claveSat={ctx.claveSat}
            espejo={candidata.empresas}
            cambio={cambioPrevio}
            onCambio={setCambioPrevio}
            onUsarSat={ctx.onUsarSat}
            onUsarEsquema={ctx.onUsarEsquema}
          />
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => { setCandidata(null); setAbierto(true); }}>
              Elegir otra
            </Button>
            <Button
              type="button"
              disabled={!fila.unidad.trim()}
              title={fila.unidad.trim() ? undefined : "Elige primero la unidad de esta fila"}
              onClick={() => ligar(candidata.clave, candidata.empresas, cambioPrevio)}
            >
              <Check size={16} /> Ligar {normalizarClave(candidata.clave)} a {fila.unidad || "esta unidad"}
            </Button>
          </div>
        </div>
      ) : null}

      {verSae && !buscando && vista?.codigo === "ligada" ? (
        <div className="mt-2">
          <AsiEstaEnSae
            clave={clave}
            unidadFila={fila.unidad}
            esquemaCodigo={ctx.esquemaCodigo}
            claveSat={ctx.claveSat}
            espejo={empresasDeLaClave}
            cambio={fila.pedirCambio}
            onCambio={(c) => onChange({ ...fila, pedirCambio: c })}
            onUsarSat={ctx.onUsarSat}
            onUsarEsquema={ctx.onUsarEsquema}
          />
        </div>
      ) : null}

      {fila.estado === "nueva" && fila.alta && !buscando ? (
        <PanelAlta
          fila={fila}
          nombre={ctx.nombre}
          claveSat={ctx.claveSat}
          esquemaCodigo={ctx.esquemaCodigo}
          esquemaNombre={ctx.esquemaNombre}
          unidadSat={ctx.unidadSat}
          lineas={ctx.lineas}
          lineasError={ctx.lineasError}
          onChange={onChange}
          onBuscarExistente={() => {
            onChange({ ...fila, clave: "", estado: "vacia", alta: null, pedirCambio: null, sae: null });
            abrirBusqueda();
          }}
          onLigar={(c, empresas) => ligar(c, empresas, null)}
        />
      ) : null}
    </div>
  );
}

// ── Una fila (tenant sin SAE): la de siempre ─────────────────────────────────

function FilaSimple({
  fila, nombre, productoId, unidadesOpciones, onChange, onQuitar,
}: {
  fila: FilaClave;
  nombre: string;
  productoId: string | null;
  unidadesOpciones: string[];
  onChange: (f: FilaClave) => void;
  onQuitar?: () => void;
}) {
  return (
    <div className={`grid grid-cols-[1fr_6rem_minmax(0,12rem)_2rem] items-center gap-2 ${fila.esBase ? "rounded-md bg-surface-2 px-3 py-2 text-sm" : "px-3"}`}>
      {fila.esBase ? (
        <span>Base: <b>{fila.unidad}</b> <span className="text-xs text-muted">(inventario)</span></span>
      ) : (
        <Select value={fila.unidad} onChange={(e) => onChange({ ...fila, unidad: e.target.value })}>
          <option value="">— Presentación —</option>
          {unidadesOpciones.map((u) => <option key={u} value={u}>{u}</option>)}
        </Select>
      )}
      {fila.esBase ? (
        <span className="text-muted">= 1</span>
      ) : (
        <Input
          type="number" step="0.0001" min="0" placeholder="Factor"
          value={fila.factor}
          onChange={(e) => onChange({ ...fila, factor: e.target.value })}
        />
      )}
      <ClaveSaeInline
        compacto
        productoId={productoId ?? ""}
        productoNombre={nombre}
        value={fila.clave}
        onChange={(v) => onChange({ ...fila, clave: normalizarClave(v) })}
      />
      {onQuitar ? (
        <button
          type="button"
          onClick={onQuitar}
          className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
          aria-label="Quitar presentación"
        >
          <Trash2 size={16} />
        </button>
      ) : <span />}
    </div>
  );
}

// ── La sección ───────────────────────────────────────────────────────────────

export function UnidadesClavesSae({
  nombre,
  filas,
  onChange,
  esquemaCodigo,
  esquemaNombre = null,
  claveSat,
  unidadSat,
  onUsarSatDeSae,
  onUsarEsquemaDeSae,
  productoId = null,
  saeConectado,
  soloBase = false,
  estados,
}: {
  nombre: string;
  /** filas[0] es la base (esBase); las demás, las presentaciones. */
  filas: FilaClave[];
  onChange: (filas: FilaClave[]) => void;
  /** Código SAE (numérico) del esquema elegido en Datos. */
  esquemaCodigo: string | null;
  esquemaNombre?: string | null;
  claveSat: string;
  /** Unidad SAT del producto: la del alta de la base. */
  unidadSat: string;
  onUsarSatDeSae: (clave: string) => void;
  onUsarEsquemaDeSae: (codigo: number) => void;
  productoId?: string | null;
  saeConectado: boolean;
  /** Alta rápida: sólo la fila de la unidad base. */
  soloBase?: boolean;
  /** Estado de las claves guardadas (GET /productos/claves-sae/estado),
   *  indexado por clave. `null` = cargando; sin pasar = no hay. */
  estados?: Record<string, ClaveSaeEstado> | null;
}) {
  const visibles = soloBase ? filas.filter((f) => f.esBase) : filas;
  const base = filas.find((f) => f.esBase);
  const hayNuevas = saeConectado && filas.some((f) => f.estado === "nueva");

  // Líneas de SAE para las altas: sólo se piden si hay una que crear.
  const [lineas, setLineas] = useState<LineaSae[] | null>(lineasCache);
  const [lineasError, setLineasError] = useState<string | null>(null);
  useEffect(() => {
    if (!hayNuevas || lineas || lineasError) return;
    let vivo = true;
    apiFetch<{ lineas?: LineaSae[] }>("/api/v1/sae/catalogos?empresa=02")
      .then((c) => {
        lineasCache = c.lineas ?? [];
        if (vivo) setLineas(lineasCache);
      })
      .catch((e) => { if (vivo) setLineasError(e instanceof ApiError ? e.message : "SAE no contestó"); });
    return () => { vivo = false; };
  }, [hayNuevas, lineas, lineasError]);

  // Una clave sugerida sigue al nombre y a la unidad hasta que alguien la toca.
  useEffect(() => {
    if (!saeConectado) return;
    let cambio = false;
    const next = filas.map((f) => {
      if (f.estado !== "nueva" || !f.alta?.claveAuto) return f;
      const sug = sugerirClaveSae(nombre, f.unidad);
      if (sug === f.clave) return f;
      cambio = true;
      return { ...f, clave: sug };
    });
    if (cambio) onChange(next);
  }, [nombre, filas, saeConectado, onChange]);

  const ctx: ContextoFila = {
    nombre,
    productoId,
    esquemaCodigo: (esquemaCodigo ?? "").trim() || null,
    esquemaNombre,
    claveSat,
    unidadSat,
    lineas,
    lineasError,
    estados,
    onUsarSat: onUsarSatDeSae,
    onUsarEsquema: onUsarEsquemaDeSae,
  };

  const usadas = useMemo(() => filas.map((f) => f.unidad), [filas]);
  const opcionesPara = (f: FilaClave) => {
    const libres = UNIDADES_INVENTARIO.filter((u) => u === f.unidad || !usadas.includes(u));
    return f.unidad && !libres.includes(f.unidad) ? [f.unidad, ...libres] : libres;
  };
  const clavesOtrasDe = (f: FilaClave) => {
    const out: Record<string, string> = {};
    for (const o of filas) {
      const c = normalizarClave(o.clave);
      if (o.key !== f.key && c) out[c] = o.unidad || "otra unidad";
    }
    return out;
  };

  const cambiar = (key: string, nueva: FilaClave) => onChange(filas.map((f) => (f.key === key ? nueva : f)));
  const quitar = (key: string) => onChange(filas.filter((f) => f.key !== key));
  const agregar = () => onChange([...filas, filaPresentacion()]);

  if (!saeConectado) {
    // Sin SAE: la sección de siempre, con la clave opcional.
    return (
      <div className="rounded-lg border border-border bg-surface-2/40 p-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm font-medium">Presentaciones y claves de SAE</span>
          <span className="text-xs text-muted">Factor = unidades base por presentación</span>
        </div>
        <p className="mb-2 text-xs text-muted">
          SAE tiene un artículo por unidad (SANDIAKG, SANDIAPZ): pon la clave de cada una y la
          línea sale a SAE con la de SU presentación. Sin clave propia, usa la de la base.
        </p>
        <div className="space-y-2">
          {visibles.map((f) => (
            <FilaSimple
              key={f.key}
              fila={f}
              nombre={nombre}
              productoId={productoId}
              unidadesOpciones={opcionesPara(f).filter((u) => u !== base?.unidad)}
              onChange={(n) => cambiar(f.key, n)}
              onQuitar={f.esBase ? undefined : () => quitar(f.key)}
            />
          ))}
          {!soloBase && filas.length === 1 ? (
            <p className="text-xs text-muted">Solo la unidad base. Agrega CAJA/BULTO si compras o vendes en esas presentaciones.</p>
          ) : null}
        </div>
        {soloBase ? null : (
          <Button type="button" variant="secondary" className="mt-2" onClick={agregar}>
            <Plus size={16} /> Agregar presentación
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-surface-2/40 p-3">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">
          Unidades y claves SAE <span className="text-danger">*</span>
        </span>
        {soloBase ? null : <span className="text-xs text-muted">Factor = unidades base por unidad</span>}
      </div>
      <p className="mb-2 text-xs text-muted">
        Cada unidad es un artículo en SAE (SANDIAKG, SANDIAPZ) y lleva su propia clave. Busca la
        que ya existe y lígala; sólo si ninguna es este producto, pide una nueva.
      </p>
      <div className="space-y-2">
        {visibles.map((f) => (
          <FilaSae
            key={f.key}
            fila={f}
            ctx={ctx}
            soloBase={soloBase}
            unidadesOpciones={opcionesPara(f).filter((u) => u !== base?.unidad)}
            clavesOtras={clavesOtrasDe(f)}
            onChange={(n) => cambiar(f.key, n)}
            onQuitar={f.esBase ? undefined : () => quitar(f.key)}
          />
        ))}
      </div>
      {soloBase ? null : (
        <Button type="button" variant="secondary" className="mt-2" onClick={agregar}>
          <Plus size={16} /> Agregar unidad
        </Button>
      )}
    </div>
  );
}

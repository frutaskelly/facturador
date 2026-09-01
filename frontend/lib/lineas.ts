// Editor de líneas compartido por Remisiones y Factura directa: tipo de línea,
// parseo del pegado de Excel y cruce de productos (Match IA). Son funciones
// puras + un fetch de fallback; el estado y el JSX viven en cada pantalla porque
// las acciones difieren (remisión = inventario, factura = fiscal).

import { apiFetch } from "@/lib/api";
import type { Candidato, LineaPegada, MatchResult } from "@/lib/types";

export type LineaForm = {
  key: string;
  texto: string;            // lo que se mostró/pegó (para aprender alias / revisar)
  producto_id: string;
  label: string;            // "sku · nombre" cuando hay producto
  presentacion: string;
  presentaciones: string[];
  cantidad: string;
  precio: string;           // vacío = se resuelve en backend
  precioManual: boolean;
  importe: number;
  // De dónde salió el precio SUGERIDO, para poder llevar de vuelta al catálogo
  // el que se teclee encima sin adivinar destino ni tramo. `precioLista` en
  // null = el producto no tenía precio ahí (es el caso "agrégalo").
  precioLista?: string | null;
  precioListaId?: string | null;   // null cuando no se puede/debe reescribir
  precioTramo?: number | null;     // tramo del que habló la referencia
  // Solo para líneas venidas de "Pegar de Excel": alimentan la columna Match IA
  // (visible únicamente mientras haya líneas pegadas por revisar).
  fromPaste?: boolean;
  candidatos?: Candidato[];  // sugerencias del cruce (para el selector Match IA)
  presPegada?: string;       // presentación tal como se pegó (texto libre)
};

let _seq = 0;
export const nuevaLinea = (over: Partial<LineaForm> = {}): LineaForm => ({
  key: `l${_seq++}`,
  texto: "",
  producto_id: "",
  label: "",
  presentacion: "",
  presentaciones: [],
  cantidad: "1",
  precio: "",
  precioManual: false,
  importe: 0,
  ...over,
});

// Valor especial del selector Match IA: "no está en el catálogo, crear nuevo".
export const NUEVO_PRODUCTO = "__nuevo__";

// Interpreta una fila pegada SIN asumir un orden fijo de columnas (fallback
// local si el backend no está disponible): las celdas numéricas son
// cantidad/precio; la celda que es una unidad conocida es la presentación y
// el texto restante es el producto — así 'KILOGRAMO' antes de 'AJO' se lee bien.
const UNIDADES = new Set([
  "kilogramo", "kilogramos", "kilo", "kilos", "kg", "kgs", "gramo", "gramos", "g", "gr",
  "pieza", "piezas", "pza", "pzas", "pz", "litro", "litros", "lt", "lts", "l", "ml",
  "caja", "cajas", "bulto", "bultos", "costal", "manojo", "paquete", "paq", "docena",
  "bolsa", "domo", "charola", "malla", "atado", "racimo", "unidad", "unidades", "und",
]);
const HEADER_WORDS = new Set([
  "cantidad", "cant", "unidad", "unidades", "presentacion", "descripcion", "producto",
  "articulo", "precio", "costo", "importe", "total", "concepto", "clave", "codigo", "sku",
]);

export const norm = (s: string) =>
  s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").trim().replace(/\s+/g, " ");

const esNum = (s: string) => {
  const t = s.replace(/[$,%\s]/g, "");
  return t !== "" && !Number.isNaN(Number(t));
};

function filaEsEncabezado(cols: string[]) {
  const celdas = cols.map((c) => c.trim()).filter(Boolean);
  if (celdas.length === 0) return true;
  if (celdas.some(esNum)) return false;
  return celdas.some((c) => HEADER_WORDS.has(norm(c)));
}

function parseFilaPegada(cols: string[]) {
  const celdas = cols.map((c) => c.replace(/\s+/g, " ").trim()).filter(Boolean);
  const numericas: string[] = [];
  const textos: string[] = [];
  for (const c of celdas) {
    if (esNum(c)) numericas.push(String(Number(c.replace(/[$,%\s]/g, ""))));
    else textos.push(c);
  }
  const unidad = textos.find((t) => UNIDADES.has(norm(t)));
  const producto = textos.find((t) => t !== unidad) ?? "";
  return {
    texto: producto,
    presentacion: unidad ?? "",
    cantidad: numericas[0] && Number(numericas[0]) ? numericas[0] : "1",
    precio: numericas[1] && Number(numericas[1]) ? numericas[1] : "",
  };
}

// Fallback local: parsea filas y cruza con /match (IA incluida) si el
// endpoint /parse-pegado no responde (backend viejo).
export async function pegarLocalFallback(raw: string): Promise<LineaPegada[]> {
  const parsed = raw
    .split("\n")
    .map((r) => r.split("\t"))
    .filter((c) => c.some((x) => x.trim()))
    .filter((c) => !filaEsEncabezado(c))
    .map(parseFilaPegada)
    .filter((p) => p.texto);
  let matches: MatchResult[] = [];
  try {
    matches = await apiFetch("/api/v1/productos/match", {
      method: "POST",
      body: JSON.stringify({ textos: parsed.map((p) => p.texto), usar_ia: true, limit: 1 }),
    });
  } catch { matches = []; }
  return parsed.map((p, i) => ({ ...p, candidatos: matches[i]?.candidatos ?? [] }));
}

// Mapea la presentación pegada (texto libre) a una de las presentaciones REALES
// del producto elegido: 'KILOGRAMOS' → 'KILO'. Si no calza, usa la default /
// unidad base / la primera disponible.
export function matchPresentacion(
  pegada: string,
  cand?: Pick<Candidato, "presentaciones" | "presentacion_default" | "unidad_base">,
): string {
  const keys = Object.keys(cand?.presentaciones ?? {});
  if (keys.length === 0) return (pegada || cand?.unidad_base || "PIEZA").toUpperCase();
  const p = norm(pegada);
  const fallback = cand?.presentacion_default ?? cand?.unidad_base ?? keys[0];
  if (!p) return keys.includes(fallback) ? fallback : keys[0];
  let hit = keys.find((k) => norm(k) === p);
  if (!hit) hit = keys.find((k) => { const nk = norm(k); return nk.startsWith(p) || p.startsWith(nk) || nk.includes(p) || p.includes(nk); });
  return hit ?? (keys.includes(fallback) ? fallback : keys[0]);
}

// Auto-resuelve solo con confianza alta (exacto/alias/IA/≥85); lo demás entra
// como "por revisar" (Match IA en 'Crear nuevo', pero con las sugerencias a un clic).
export const esConfiable = (c?: Candidato) =>
  !!c && (c.origen === "exacto" || c.origen === "alias" || c.origen === "ia" || c.score >= 85);

// Convierte una fila pegada en una línea de la tabla, guardando sus candidatos
// para la columna Match IA.
export function lineaDesdePegado(f: LineaPegada): LineaForm {
  const precioOver: Partial<LineaForm> = f.precio ? { precio: f.precio, precioManual: true } : {};
  const meta = { candidatos: f.candidatos ?? [], presPegada: f.presentacion, fromPaste: true };
  const top = f.candidatos?.[0];
  if (esConfiable(top)) {
    const presKeys = Object.keys(top!.presentaciones ?? {});
    return nuevaLinea({
      texto: f.texto, producto_id: top!.producto_id, label: top!.nombre,
      presentaciones: presKeys, presentacion: matchPresentacion(f.presentacion, top),
      cantidad: f.cantidad || "1", ...meta, ...precioOver,
    });
  }
  return nuevaLinea({
    texto: f.texto, cantidad: f.cantidad || "1",
    presentacion: f.presentacion || "PIEZA", ...meta, ...precioOver,
  });
}

// Deriva la unidad base del alta rápida desde la presentación pegada
// ('KILOGRAMO' → 'KILO', 'PZA' → 'PIEZA').
export function unidadBaseDesde(pres?: string): string {
  const p = norm(pres ?? "");
  if (!p) return "KILO";
  const hit = ["KILO", "PIEZA", "LITRO", "CAJA", "BULTO", "COSTAL", "MANOJO", "BOLSA"]
    .find((u) => { const n = norm(u); return p.startsWith(n) || n.startsWith(p) || p.includes(n); });
  return hit ?? "KILO";
}

// ── Preview fiscal calculado por el SERVIDOR ──────────────────────────────────
// Regla "el backend calcula todo": el frontend nunca deriva IVA/IEPS por su
// cuenta; este helper pide los totales a /remisiones/preview-totales (el mismo
// cerebro fiscal que guarda remisiones y facturas).
export type FiscalPreview = {
  subtotal: number;
  iva: number;
  ieps: number;
  total: number;
  /** Desglose por línea, indexado por la `key` de la línea enviada. */
  porKey: Record<string, { iva: number; ieps: number }>;
};

type PreviewResp = {
  subtotal: string; iva: string; ieps: string; total: string;
  lineas: { importe: string; iva_importe: string; ieps_importe: string }[];
};

/** Pide al servidor los totales fiscales de las líneas completas (producto,
 * cantidad > 0 y precio). Devuelve null si no hay líneas listas. */
export async function fetchFiscalPreview(lineas: LineaForm[]): Promise<FiscalPreview | null> {
  const listas = lineas.filter(
    (l) => l.producto_id && Number(l.cantidad) > 0 && l.precio !== "" && Number(l.precio) >= 0,
  );
  if (!listas.length) return null;
  const r = await apiFetch<PreviewResp>("/api/v1/remisiones/preview-totales", {
    method: "POST",
    body: JSON.stringify({
      lineas: listas.map((l) => ({
        producto_id: l.producto_id,
        cantidad: l.cantidad,
        precio_unitario: l.precio,
        presentacion: l.presentacion || null,
      })),
    }),
  });
  return {
    subtotal: Number(r.subtotal),
    iva: Number(r.iva),
    ieps: Number(r.ieps),
    total: Number(r.total),
    porKey: Object.fromEntries(
      listas.map((l, i) => [
        l.key,
        { iva: Number(r.lineas[i]?.iva_importe ?? 0), ieps: Number(r.lineas[i]?.ieps_importe ?? 0) },
      ]),
    ),
  };
}

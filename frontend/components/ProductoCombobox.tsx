"use client";

import { useEffect, useRef, useState } from "react";
import type { ClipboardEvent } from "react";
import { Plus, Sparkles } from "lucide-react";

import { apiFetch } from "@/lib/api";
import type { Candidato, MatchResult } from "@/lib/types";
import { CrearProductoModal } from "@/components/CrearProductoModal";

const BASE =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60";

export type ProductoPick = {
  producto_id: string;
  sku: string;
  nombre: string;
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
  unidad_base?: string | null;
};

/**
 * Buscador de producto con cruce inteligente (exacto → alias aprendido → difuso → IA).
 * Al elegir un candidato que vino de un texto inexacto, aprende el alias para que
 * la próxima vez se resuelva solo. `texto` se reporta para soporte de pegado Excel.
 */
export function ProductoCombobox({
  label,
  onSelect,
  placeholder = "Buscar producto…",
  autoFocus,
  onPaste,
  clienteId = null,
  unidadBase,
  presentacion,
  sugerencias,
  onCrear,
  aliasTexto,
}: {
  label?: string;
  onSelect: (p: ProductoPick | null, texto: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  onPaste?: (e: ClipboardEvent<HTMLInputElement>) => void;
  /** Cliente de la orden: deja capturar el precio en SU lista al dar de alta. */
  clienteId?: string | null;
  /** Unidad que traía el renglón, para arrancar el alta con la que toca. */
  unidadBase?: string;
  /** Presentación a la que va el precio del alta. */
  presentacion?: string;
  /** Coincidencias ya calculadas (columna Match IA): se muestran al abrir, sin
   * teclear nada. En cuanto el usuario escribe, manda la búsqueda del catálogo. */
  sugerencias?: Candidato[];
  /** Si se pasa, "Crear Producto Nuevo" delega en la pantalla (que ya tiene su
   * propio popup con el texto pegado) en vez de abrir el modal interno. */
  onCrear?: (texto: string) => void;
  /** Texto con el que se aprende el alias al confirmar un cruce. Por defecto es
   * lo tecleado; en Match IA es el texto ORIGINAL del cliente. */
  aliasTexto?: string;
}) {
  const [q, setQ] = useState(label ?? "");
  const [open, setOpen] = useState(false);
  const [tecleado, setTecleado] = useState(false);
  const [cands, setCands] = useState<Candidato[]>([]);
  const [loading, setLoading] = useState(false);
  const [iaTried, setIaTried] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Alta rápida de producto desde el buscador ("+ Crear Producto Nuevo").
  // Se delega en el modal compartido: trae el candado de duplicados, la
  // sugerencia de clave SAT y el precio a la lista del cliente.
  const [createOpen, setCreateOpen] = useState(false);
  const [createNombre, setCreateNombre] = useState("");

  useEffect(() => { setQ(label ?? ""); setTecleado(false); }, [label]);
  // Mientras no se teclee, la lista son las sugerencias que ya trae la línea.
  const mostrandoSug = !tecleado && !!sugerencias?.length;
  const lista = mostrandoSug ? sugerencias! : cands;
  useEffect(() => setHi(0), [cands, sugerencias]);
  // Enfoca cuando el flujo encadenado apunta a esta caja (no solo al montar).
  useEffect(() => {
    if (autoFocus) {
      inputRef.current?.focus();
      setOpen(true);
    }
  }, [autoFocus]);

  useEffect(() => {
    if (!open || mostrandoSug) return;
    const t = q.trim();
    if (t.length < 2) {
      setCands([]);
      return;
    }
    let active = true;
    setLoading(true);
    setIaTried(false);
    const timer = setTimeout(async () => {
      try {
        const res = await apiFetch<MatchResult[]>("/api/v1/productos/match", {
          method: "POST",
          body: JSON.stringify({ textos: [t], usar_ia: false, limit: 8 }),
        });
        if (active) setCands(res[0]?.candidatos ?? []);
      } catch {
        if (active) setCands([]);
      } finally {
        if (active) setLoading(false);
      }
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [q, open, mostrandoSug]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  async function buscarIa() {
    const t = q.trim();
    if (t.length < 2) return;
    setLoading(true);
    setIaTried(true);
    try {
      const res = await apiFetch<MatchResult[]>("/api/v1/productos/match", {
        method: "POST",
        body: JSON.stringify({ textos: [t], usar_ia: true, limit: 8 }),
      });
      setCands(res[0]?.candidatos ?? []);
    } catch {
      setCands([]);
    } finally {
      setLoading(false);
    }
  }

  async function pick(c: Candidato) {
    const texto = (aliasTexto ?? q).trim();
    onSelect({
      producto_id: c.producto_id, sku: c.sku, nombre: c.nombre,
      presentaciones: c.presentaciones, presentacion_default: c.presentacion_default,
      unidad_base: c.unidad_base,
    }, texto);
    setQ(c.nombre);   // solo el nombre, sin SKU
    setOpen(false);
    // Con `aliasTexto` (Match IA) el origen habla de LO BUSCADO, no del texto del
    // cliente: buscar "manzana amarilla" da un match exacto y aun así hay que
    // aprender que "MANZANA GOLDEN SIN PICADURAS…" es ese producto.
    const aprender = aliasTexto
      ? !!texto && texto.toLowerCase() !== c.nombre.toLowerCase()
      : c.origen !== "exacto" && !!texto && texto.toLowerCase() !== c.nombre.toLowerCase();
    if (aprender) {
      // El usuario confirmó el cruce → se aprende para no volver a preguntar.
      try {
        await apiFetch("/api/v1/productos/alias", {
          method: "POST",
          body: JSON.stringify({ texto, producto_id: c.producto_id }),
        });
      } catch {
        /* no bloquea la captura */
      }
    }
  }

  function abrirCrear() {
    const texto = (aliasTexto ?? q).trim();
    setOpen(false);
    if (onCrear) { onCrear(texto); return; }
    setCreateNombre(texto);
    setCreateOpen(true);
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        ref={inputRef}
        className={BASE}
        aria-label="Buscar producto"
        value={q}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQ(e.target.value);
          setTecleado(true);
          setOpen(true);
          onSelect(null, e.target.value); // limpia la selección mientras escribe
        }}
        onFocus={(e) => { setOpen(true); if (sugerencias?.length) e.currentTarget.select(); }}
        onPaste={onPaste}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, Math.max(lista.length - 1, 0))); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          else if (e.key === "Enter") { if (lista[hi]) { e.preventDefault(); pick(lista[hi]); } }
          else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && (mostrandoSug || q.trim().length >= 2) && (
        <div className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-border bg-surface shadow-lg">
          {loading && <div className="px-3 py-2 text-sm text-muted">Buscando…</div>}
          {!loading && mostrandoSug && (
            <div className="px-3 pt-2 text-[11px] uppercase tracking-wide text-muted">
              Sugerencias · escribe para buscar en todo el catálogo
            </div>
          )}
          {!loading && lista.length === 0 && (
            <div className="px-3 py-2 text-sm text-muted">
              <div>Sin coincidencias.</div>
              {!iaTried && (
                <button
                  type="button"
                  onClick={buscarIa}
                  className="mt-1 inline-flex items-center gap-1 text-accent hover:underline"
                >
                  <Sparkles size={14} /> Buscar con IA
                </button>
              )}
            </div>
          )}
          {!loading &&
            lista.map((c, i) => (
              <button
                key={c.producto_id}
                type="button"
                onClick={() => pick(c)}
                onMouseEnter={() => setHi(i)}
                className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${i === hi ? "bg-accent/10" : "hover:bg-surface-2"}`}
              >
                <span>
                  <span className="font-medium">{c.nombre}</span>
                  <span className="ml-2 text-xs text-muted">{c.sku}</span>
                </span>
                {c.origen !== "exacto" && (
                  <span className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">
                    {c.origen === "ia" ? "IA" : c.origen === "alias" ? "alias" : `${c.score}%`}
                  </span>
                )}
              </button>
            ))}
          {!loading && (
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={abrirCrear}
              className="flex w-full items-center gap-1.5 border-t border-border px-3 py-2 text-left text-sm font-medium text-accent hover:bg-accent/5"
            >
              <Plus size={14} /> Crear Producto Nuevo{q.trim() ? ` «${q.trim()}»` : ""}
            </button>
          )}
        </div>
      )}

      <CrearProductoModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        nombreInicial={createNombre}
        unidadBaseInicial={unidadBase || "KILO"}
        clienteId={clienteId}
        presentacionInicial={presentacion}
        onCreated={(prod) => {
          // El producto recién creado se selecciona en esta caja (como un candidato).
          onSelect({
            producto_id: prod.id, sku: prod.sku, nombre: prod.nombre,
            presentaciones: prod.presentaciones, presentacion_default: prod.presentacion_default,
            unidad_base: prod.unidad_base,
          }, prod.nombre);
          setQ(prod.nombre);
        }}
      />
    </div>
  );
}

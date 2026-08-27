"use client";

// Qué hacer con una fila de la importación, en UNA sola caja: crear el producto
// nuevo, vincularlo a uno de los parecidos que encontró el cruce, o buscar
// CUALQUIER producto del catálogo. Los parecidos que ofrece el preview son solo
// los que pasan el 60% de similitud; sin buscador, un producto que se llama
// distinto en el archivo ("ROMA" ↔ "JITOMATE SALADETTE") era inalcanzable.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Plus, Search } from "lucide-react";

import { apiFetch } from "@/lib/api";
import type { Candidato, MatchResult } from "@/lib/types";

const MIN_BUSQUEDA = 2;      // teclear una sola letra traería el catálogo entero
const DEBOUNCE_MS = 250;
const ANCHO_MIN = 340;       // los nombres del catálogo no caben en la columna

/** Etiqueta de un producto en la lista: nombre, SKU y de dónde salió el cruce. */
function pista(c: Candidato): string {
  if (c.origen === "alias") return "alias";
  if (c.origen === "ia") return "IA";
  if (c.origen === "exacto") return "exacto";
  return `${c.score}%`;
}

export function ProductoAccionCombobox({
  valor,
  candidatos,
  onCrear,
  onVincular,
  ariaLabel,
}: {
  /** "" = crear producto nuevo; si no, el id del producto vinculado. */
  valor: string;
  /** Parecidos que trajo el preview (≥60%). */
  candidatos: Candidato[];
  onCrear: () => void;
  onVincular: (c: Candidato) => void;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [resultados, setResultados] = useState<Candidato[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [hi, setHi] = useState(0);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; maxHeight: number } | null>(null);
  const [mounted, setMounted] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setMounted(true), []);

  const seleccionado = candidatos.find((c) => c.producto_id === valor) ?? null;
  const buscandoAlgo = q.trim().length >= MIN_BUSQUEDA;
  // Con búsqueda manda el catálogo; sin ella, los parecidos del cruce. Mientras
  // la petición viaja la lista queda VACÍA: si no, el panel enseñaba "Buscando…"
  // y Enter vinculaba a un resultado de la búsqueda anterior que ya no se ve.
  const lista = buscandoAlgo ? (buscando ? [] : resultados) : candidatos;
  // Índice 0 es siempre "Crear producto nuevo": es una acción, no un producto,
  // y debe poder elegirse tanto al abrir como a media búsqueda.
  const total = lista.length + 1;

  const place = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 4;
    const width = Math.max(r.width, ANCHO_MIN);
    // Sin este tope, un panel más ancho que la columna se sale por la derecha.
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    const espacioAbajo = window.innerHeight - r.bottom - 8;
    const espacioArriba = r.top - 8;
    const arriba = espacioAbajo < 220 && espacioArriba > espacioAbajo;
    const maxHeight = Math.max(180, Math.min(400, Math.floor(arriba ? espacioArriba : espacioAbajo)));
    setRect({ left, top: arriba ? r.top - gap - maxHeight : r.bottom + gap, width, maxHeight });
  }, []);

  const abrir = useCallback(() => {
    place();
    setQ("");
    setResultados([]);
    // Sobre lo que la fila ya tiene: abrir con teclado y dar Enter debe
    // confirmar lo elegido, no convertirlo en "Crear producto nuevo".
    const i = candidatos.findIndex((c) => c.producto_id === valor);
    setHi(valor && i >= 0 ? i + 1 : 0);
    setOpen(true);
  }, [place, candidatos, valor]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Búsqueda contra el catálogo (mismo cruce que usa el capturista: exacto →
  // alias aprendido → prefijo → subcadena → parecido). Sin IA: aquí el usuario
  // ya está mirando la lista y decide él.
  useEffect(() => {
    if (!open) return;
    const t = q.trim();
    if (t.length < MIN_BUSQUEDA) {
      setResultados([]);
      setBuscando(false);
      return;
    }
    let vivo = true;
    setBuscando(true);
    const timer = setTimeout(async () => {
      try {
        const res = await apiFetch<MatchResult[]>("/api/v1/productos/match", {
          method: "POST",
          body: JSON.stringify({ textos: [t], usar_ia: false, limit: 20 }),
        });
        if (vivo) setResultados(res[0]?.candidatos ?? []);
      } catch {
        if (vivo) setResultados([]);   // el estado vacío ya dice "sin coincidencias"
      } finally {
        if (vivo) setBuscando(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      vivo = false;
      clearTimeout(timer);
    };
  }, [q, open]);

  // Al teclear, el resaltado va al primer resultado en cuanto llega la lista;
  // dar Enter después de buscar tiene que vincular a lo que se está viendo, no
  // crear un producto nuevo. Sin texto, vuelve a "Crear".
  useEffect(() => {
    if (q.trim().length < MIN_BUSQUEDA) setHi(0);
  }, [q]);
  useEffect(() => {
    if (buscandoAlgo && !buscando) setHi(resultados.length > 0 ? 1 : 0);
  }, [resultados, buscando, buscandoAlgo]);

  // Cierre por clic fuera / Escape / scroll de la página / resize. El scroll se
  // escucha en captura (no burbujea), ignorando el del propio panel para que su
  // lista pueda scrollearse sin cerrarse.
  useEffect(() => {
    if (!open) return;
    // El foco vive en la caja de búsqueda del portal; al desmontarla, sin esto
    // el foco cae en <body> y se pierde el lugar del tabulado. `preventScroll`
    // para que devolverlo no brinque la página que el usuario está scrolleando.
    function cerrar() {
      const dentro = panelRef.current?.contains(document.activeElement);
      setOpen(false);
      if (dentro) triggerRef.current?.focus({ preventScroll: true });
    }
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      cerrar();
    }
    function onScroll(e: Event) {
      if (e.target instanceof Node && panelRef.current?.contains(e.target)) return;
      cerrar();
    }
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", cerrar);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", cerrar);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  function elegir(i: number) {
    if (i === 0) onCrear();
    else {
      const c = lista[i - 1];
      if (!c) return;
      onVincular(c);
    }
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onKeyDownPanel(e: ReactKeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((h) => Math.min(h + 1, total - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      elegir(hi);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
  }

  const etiqueta = useMemo(() => {
    if (!valor) return "Crear producto nuevo";
    if (seleccionado) {
      return `Vincular a ${seleccionado.nombre} (${seleccionado.sku}) · ${seleccionado.score}%`;
    }
    return "Vincular a un producto existente";
  }, [valor, seleccionado]);

  return (
    <div className="relative">
      <div
        ref={triggerRef}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        tabIndex={0}
        onClick={() => (open ? setOpen(false) : abrir())}
        onKeyDown={(e) => {
          if (open) return;
          if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
            e.preventDefault();
            abrir();
          }
        }}
        className={`flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg border bg-background px-3 py-2 text-sm outline-none transition hover:border-accent/60 focus:border-accent ${
          open ? "border-accent" : "border-border"
        }`}
      >
        <span className="truncate">{etiqueta}</span>
        <ChevronDown
          size={15}
          className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </div>

      {mounted &&
        open &&
        rect &&
        createPortal(
          <div
            ref={panelRef}
            style={{
              position: "fixed",
              left: rect.left,
              top: rect.top,
              width: rect.width,
              maxHeight: rect.maxHeight,
              zIndex: 50,
            }}
            className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-lg"
          >
            <div className="relative shrink-0 border-b border-border">
              <Search
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
              />
              <input
                ref={inputRef}
                type="text"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={onKeyDownPanel}
                placeholder="Buscar en todo el catálogo…"
                aria-label="Buscar producto en el catálogo"
                className="w-full bg-transparent py-2 pl-8 pr-3 text-sm outline-none"
              />
            </div>

            <div role="listbox" className="overflow-auto">
              <button
                type="button"
                role="option"
                aria-selected={!valor}
                onClick={() => elegir(0)}
                onMouseEnter={() => setHi(0)}
                className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                  hi === 0 ? "bg-accent/10" : "hover:bg-surface-2"
                }`}
              >
                <span className="flex items-center gap-1.5 truncate font-medium text-accent">
                  <Plus size={14} className="shrink-0" /> Crear producto nuevo
                </span>
                {!valor && <Check size={15} className="shrink-0 text-accent" />}
              </button>

              {!buscandoAlgo && candidatos.length > 0 ? (
                <div className="border-t border-border px-3 pb-1 pt-2 text-xs uppercase tracking-wide text-muted">
                  Parecidos
                </div>
              ) : null}

              {buscando ? (
                <div className="border-t border-border px-3 py-2 text-sm text-muted">Buscando…</div>
              ) : buscandoAlgo && lista.length === 0 ? (
                <div className="border-t border-border px-3 py-2 text-sm text-muted">
                  Sin coincidencias en el catálogo.
                </div>
              ) : (
                lista.map((c, i) => {
                  const idx = i + 1;
                  const esSel = c.producto_id === valor;
                  return (
                    <button
                      key={c.producto_id}
                      type="button"
                      role="option"
                      aria-selected={esSel}
                      onClick={() => elegir(idx)}
                      onMouseEnter={() => setHi(idx)}
                      className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                        idx === hi ? "bg-accent/10" : "hover:bg-surface-2"
                      } ${i === 0 && !buscandoAlgo ? "" : "border-t border-border/50"}`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate">{c.nombre}</span>
                        <span className="block truncate text-xs text-muted">
                          {c.sku}
                          {c.categoria_nombre ? ` · ${c.categoria_nombre}` : ""}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">
                          {pista(c)}
                        </span>
                        {esSel && <Check size={15} className="text-accent" />}
                      </span>
                    </button>
                  );
                })
              )}

              {!buscandoAlgo && candidatos.length === 0 ? (
                <div className="border-t border-border px-3 py-2 text-xs text-muted">
                  No se encontró ningún parecido. Búscalo arriba si ya existe en el catálogo.
                </div>
              ) : null}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}

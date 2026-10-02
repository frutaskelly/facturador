"use client";

// Selector de categoría: busca entre las categorías ACTIVAS del negocio
// (las de /categorias), permite dejarlo sin categoría y dar de alta una nueva
// sin salir de la pantalla. Mismo patrón que ProductoCombobox.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Check, Plus, X } from "lucide-react";

import { ApiError, apiFetch } from "@/lib/api";
import { FloatingPanel } from "@/components/ui/FloatingPanel";
import { useToast } from "@/components/ui/Toast";
import type { Categoria } from "@/lib/types";

const BASE =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-accent";

function norm(t: string): string {
  return t
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

/** El panel vive al final de <body> (FloatingPanel), no junto al botón: el Tab
 *  de la página ya no sigue de sus opciones al siguiente campo. Se recorre a
 *  mano y, al salir por una orilla, avisa para regresar el foco al botón. */
function tabEnPanel(e: ReactKeyboardEvent<HTMLElement>): boolean {
  e.preventDefault();
  const f = Array.from(
    e.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"),
  );
  const j = f.indexOf(document.activeElement as HTMLElement) + (e.shiftKey ? -1 : 1);
  if (j >= 0 && j < f.length) {
    f[j].focus();
    return false;
  }
  return true;
}

export function CategoriaCombobox({
  value,
  categorias,
  onChange,
  onCreada,
  sugerida,
  disabled,
  ariaLabel,
}: {
  /** id de la categoría elegida; "" = sin categoría */
  value: string;
  /** categorías activas del negocio */
  categorias: Categoria[];
  onChange: (id: string) => void;
  /** avisa al padre para que la nueva categoría entre en la lista compartida */
  onCreada?: (c: Categoria) => void;
  /** nombre que trae el archivo: se ofrece como alta rápida */
  sugerida?: string;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [creando, setCreando] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const elegida = categorias.find((c) => c.id === value) ?? null;
  const filtro = norm(q);
  const opciones = filtro
    ? categorias.filter((c) => norm(c.nombre).includes(filtro))
    : categorias;

  // El texto para "crear": lo tecleado, o el nombre que venía en el archivo.
  const aCrear = (q.trim() || sugerida || "").trim();
  const yaExiste = aCrear
    ? categorias.some((c) => norm(c.nombre) === norm(aCrear))
    : true;

  // El panel vive en un portal (FloatingPanel), fuera de boxRef: un clic en él
  // no cuenta como «fuera».
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (boxRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
      setQ("");
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  // FloatingPanel monta su contenido un render DESPUÉS de abrir (primero mide
  // el botón), así que un efecto sobre `open` todavía no encuentra la caja: se
  // enfoca al montarse. Estable para no robar el foco en cada render.
  const enfocarAlMontar = useCallback((el: HTMLInputElement | null) => {
    el?.focus({ preventScroll: true });
  }, []);

  /** Cierra el panel y deja el foco en el botón (el panel se desmonta y el
   *  foco caería en <body>). */
  function cerrarAlBoton() {
    setOpen(false);
    triggerRef.current?.focus({ preventScroll: true });
  }

  async function crear() {
    if (!aCrear || creando) return;
    setCreando(true);
    try {
      const cat = await apiFetch<Categoria>("/api/v1/categorias", {
        method: "POST",
        body: JSON.stringify({ nombre: aCrear }),
      });
      onCreada?.(cat);
      onChange(cat.id);
      setQ("");
      cerrarAlBoton();
      toast.success(`Categoría «${cat.nombre}» creada`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la categoría");
    } finally {
      setCreando(false);
    }
  }

  function elegir(id: string) {
    onChange(id);
    setQ("");
    cerrarAlBoton();
  }

  const etiqueta = elegida
    ? elegida.nombre
    : sugerida
      ? `Crear «${sugerida}»`
      : "Sin categoría";

  return (
    <div ref={boxRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel ?? "Categoría"}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`${BASE} flex items-center justify-between gap-2 text-left ${
          disabled ? "cursor-not-allowed opacity-60" : "hover:bg-surface-2"
        } ${!elegida && !sugerida ? "text-muted" : ""}`}
      >
        <span className="truncate">{etiqueta}</span>
        <span className="shrink-0 text-muted">▾</span>
      </button>

      {/* En portal: dentro de un Modal (cuerpo con overflow-auto) ya no se
          corta. Columna flex: si la pantalla no da para el alto completo, la
          que se encoge (y scrollea) es la lista, no la caja ni «Crear». */}
      <FloatingPanel
        ref={panelRef}
        anchorRef={triggerRef}
        open={open && !disabled}
        minWidth={256}
        maxHeight={340}
        role="listbox"
        aria-label="Categorías"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            cerrarAlBoton();
          } else if (e.key === "Tab") {
            if (tabEnPanel(e)) cerrarAlBoton();
          }
        }}
        className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-lg"
      >
        <div className="shrink-0 border-b border-border p-2">
          <input
            ref={enfocarAlMontar}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar o escribir una nueva…"
            aria-label="Buscar categoría"
            className={BASE}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !yaExiste) {
                e.preventDefault();
                void crear();
              }
            }}
          />
        </div>

        <div className="min-h-0 max-h-60 overflow-auto py-1">
          {/* Dejarlo vacío es una opción legítima. */}
          <button
            type="button"
            role="option"
            aria-selected={value === ""}
            onClick={() => elegir("")}
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-muted hover:bg-surface-2"
          >
            <span className="inline-flex items-center gap-1.5">
              <X size={14} /> Sin categoría
            </span>
            {value === "" ? <Check size={14} /> : null}
          </button>

          {opciones.map((c) => (
            <button
              key={c.id}
              type="button"
              role="option"
              aria-selected={value === c.id}
              onClick={() => elegir(c.id)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2"
            >
              <span className="truncate">{c.nombre}</span>
              {value === c.id ? <Check size={14} className="shrink-0" /> : null}
            </button>
          ))}

          {opciones.length === 0 && filtro ? (
            <div className="px-3 py-2 text-sm text-muted">Sin coincidencias</div>
          ) : null}
        </div>

        {!yaExiste && aCrear ? (
          <button
            type="button"
            onClick={crear}
            disabled={creando}
            className="flex w-full shrink-0 items-center gap-1.5 border-t border-border px-3 py-2 text-left text-sm text-accent hover:bg-surface-2 disabled:opacity-60"
          >
            <Plus size={14} />
            {creando ? "Creando…" : `Crear «${aCrear}»`}
          </button>
        ) : null}
      </FloatingPanel>
    </div>
  );
}

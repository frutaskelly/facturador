"use client";

// Selector de categoría: busca entre las categorías ACTIVAS del negocio
// (las de /categorias), permite dejarlo sin categoría y dar de alta una nueva
// sin salir de la pantalla. Mismo patrón que ProductoCombobox.
import { useEffect, useRef, useState } from "react";
import { Check, Plus, X } from "lucide-react";

import { ApiError, apiFetch } from "@/lib/api";
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
  const inputRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQ("");
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

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
      setOpen(false);
      setQ("");
      toast.success(`Categoría «${cat.nombre}» creada`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la categoría");
    } finally {
      setCreando(false);
    }
  }

  function elegir(id: string) {
    onChange(id);
    setOpen(false);
    setQ("");
  }

  const etiqueta = elegida
    ? elegida.nombre
    : sugerida
      ? `Crear «${sugerida}»`
      : "Sin categoría";

  return (
    <div ref={boxRef} className="relative">
      <button
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

      {open && !disabled ? (
        <div className="absolute z-30 mt-1 w-full min-w-[16rem] rounded-lg border border-border bg-surface shadow-lg">
          <div className="border-b border-border p-2">
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar o escribir una nueva…"
              aria-label="Buscar categoría"
              className={BASE}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
                if (e.key === "Enter" && !yaExiste) {
                  e.preventDefault();
                  void crear();
                }
              }}
            />
          </div>

          <div className="max-h-60 overflow-auto py-1">
            {/* Dejarlo vacío es una opción legítima. */}
            <button
              type="button"
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
              className="flex w-full items-center gap-1.5 border-t border-border px-3 py-2 text-left text-sm text-accent hover:bg-surface-2 disabled:opacity-60"
            >
              <Plus size={14} />
              {creando ? "Creando…" : `Crear «${aCrear}»`}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

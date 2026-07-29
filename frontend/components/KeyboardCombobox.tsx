"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const BASE =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60";

export type ComboOption = { value: string; label: string };

/**
 * Caja de búsqueda + dropdown navegable por teclado.
 * - Al enfocar se abre y limpia para escribir; filtra por texto.
 * - ↑/↓ mueven el resaltado; Enter selecciona el resaltado y llama `onAdvance`.
 * - Si ya hay valor y se presiona Enter sin abrir, solo avanza.
 * - `autoOpen` enfoca y abre automáticamente (para el flujo encadenado).
 */
export function KeyboardCombobox({
  options,
  value,
  onSelect,
  onAdvance,
  onBack,
  autoOpen = false,
  placeholder = "Escribe para buscar…",
  disabled,
  emptyText = "Sin opciones",
  ariaLabel,
}: {
  options: ComboOption[];
  value: string;
  onSelect: (value: string) => void;
  onAdvance?: () => void;
  onBack?: () => void;
  autoOpen?: boolean;
  placeholder?: string;
  disabled?: boolean;
  emptyText?: string;
  /** Nombre accesible del campo (p. ej. "Cliente"). Sin él se usa el
   *  placeholder, y "Buscar" solo como último recurso. */
  ariaLabel?: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selectedLabel = options.find((o) => o.value === value)?.label ?? "";
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, query]);

  // autoOpen: enfoca + abre + limpia para escribir, resaltando el valor actual
  useEffect(() => {
    if (autoOpen && !disabled) {
      inputRef.current?.focus();
      setOpen(true);
      setQuery("");
      setHi(Math.max(options.findIndex((o) => o.value === value), 0));
    }
  }, [autoOpen, disabled]); // eslint-disable-line react-hooks/exhaustive-deps

  // mantener el resaltado a la vista
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-i="${hi}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [hi, open]);

  function choose(opt: ComboOption) {
    onSelect(opt.value);
    setQuery("");
    setOpen(false);
    onAdvance?.();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setHi((h) => Math.min(h + 1, Math.max(filtered.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && filtered[hi]) {
        choose(filtered[hi]);
      } else if (value) {
        setOpen(false);
        onAdvance?.();
      } else if (filtered.length === 1) {
        choose(filtered[0]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "ArrowRight" && (inputRef.current?.selectionStart ?? 0) >= query.length) {
      if (value) {
        e.preventDefault();
        onAdvance?.();
      }
    } else if (e.key === "ArrowLeft" && (inputRef.current?.selectionStart ?? 0) === 0) {
      if (onBack) {
        e.preventDefault();
        onBack();
      }
    }
  }

  return (
    <div className="relative">
      <input
        ref={inputRef}
        className={BASE}
        aria-label={ariaLabel ?? (placeholder || "Buscar")}
        disabled={disabled}
        value={open ? query : selectedLabel}
        placeholder={selectedLabel || placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setHi(0);
        }}
        onFocus={() => {
          setOpen(true);
          setQuery("");
          setHi(Math.max(options.findIndex((o) => o.value === value), 0));
        }}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={onKeyDown}
      />
      {open && (
        <div ref={listRef} className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-border bg-surface shadow-lg">
          {filtered.length === 0 && <div className="px-3 py-2 text-sm text-muted">{emptyText}</div>}
          {filtered.map((o, i) => (
            <button
              key={o.value}
              data-i={i}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(o)}
              onMouseEnter={() => setHi(i)}
              className={`block w-full px-3 py-2 text-left text-sm ${i === hi ? "bg-accent/10 text-foreground" : "hover:bg-surface-2"}`}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

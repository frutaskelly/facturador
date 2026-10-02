"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, Search, X } from "lucide-react";

import { FloatingPanel } from "@/components/ui/FloatingPanel";

/** Normaliza para comparar sin acentos ni mayúsculas. */
const norm = (s: string) => s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

// ─────────────────────────────────────────────────────────────────────────────
// 1) SearchBox — caja de búsqueda sencilla (ícono + limpiar)
// ─────────────────────────────────────────────────────────────────────────────
export function SearchBox({
  value,
  onChange,
  placeholder = "Buscar…",
  className,
  autoFocus,
  "aria-label": ariaLabel = "Buscar",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
  "aria-label"?: string;
}) {
  return (
    <div className={`relative ${className ?? ""}`}>
      <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
      <input
        type="text"
        value={value}
        autoFocus={autoFocus}
        aria-label={ariaLabel}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-9 text-sm outline-none focus:border-accent"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Limpiar búsqueda"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted hover:bg-surface-2 hover:text-foreground"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// 2) SearchSelect — caja de búsqueda + dropdown (combobox con filtrado local)
// ─────────────────────────────────────────────────────────────────────────────
export type SearchOption = { value: string; label: string; hint?: string };

export function SearchSelect({
  options,
  value,
  onSelect,
  placeholder = "Buscar y seleccionar…",
  emptyText = "Sin coincidencias.",
  className,
}: {
  options: SearchOption[];
  value?: string | null;
  onSelect: (opt: SearchOption | null) => void;
  placeholder?: string;
  emptyText?: ReactNode;
  className?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listaId = useId();

  const selected = options.find((o) => o.value === value) ?? null;
  const ql = norm(q.trim());
  const filtered = !open || !ql ? options : options.filter((o) => norm(`${o.label} ${o.hint ?? ""}`).includes(ql));

  // texto visible: lo que se escribe (al estar abierto) o la etiqueta seleccionada
  const text = open ? q : selected?.label ?? "";

  useEffect(() => setHi(0), [q, open]);
  useEffect(() => {
    // La lista vive en un portal (FloatingPanel), fuera de boxRef: un clic en
    // ella no cuenta como «fuera».
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (boxRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function pick(o: SearchOption) {
    onSelect(o);
    setQ("");
    setOpen(false);
  }

  function clear() {
    onSelect(null);
    setQ("");
    inputRef.current?.focus();
  }

  return (
    <div ref={boxRef} className={`relative ${className ?? ""}`}>
      <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listaId : undefined}
        aria-label={placeholder}
        value={text}
        placeholder={placeholder}
        onFocus={() => {
          setOpen(true);
          setQ("");
        }}
        // Tras elegir, la caja sigue enfocada (la lista no le roba el foco) y
        // onFocus ya no se dispara: el clic es el que la reabre.
        onClick={() => {
          if (!open) {
            setQ("");
            setOpen(true);
          }
        }}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setHi((h) => Math.min(h + 1, Math.max(filtered.length - 1, 0)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setHi((h) => Math.max(h - 1, 0));
          } else if (e.key === "Enter") {
            if (open && filtered[hi]) {
              e.preventDefault();
              pick(filtered[hi]);
            }
          } else if (e.key === "Escape") {
            // Con la lista abierta, Escape cierra solo la lista.
            if (open) {
              e.preventDefault();
              e.stopPropagation();
            }
            setOpen(false);
          } else if (e.key === "Tab") {
            // La lista vive al final de <body>: el Tab ya no pasa por ella. Se
            // cierra y el foco sigue al siguiente campo (las flechas eligen).
            setOpen(false);
          }
        }}
        className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-16 text-sm outline-none focus:border-accent"
      />
      <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
        {selected && (
          <button
            type="button"
            onClick={clear}
            aria-label="Quitar selección"
            className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-foreground"
          >
            <X size={14} />
          </button>
        )}
        <ChevronDown size={15} className={`text-muted transition-transform ${open ? "rotate-180" : ""}`} />
      </div>

      {/* En portal: dentro de un Modal (cuerpo con overflow-auto) ya no se corta.
          mousedown sin default: la caja nunca pierde el foco. Si una opción se
          lo quedara, al desmontarse la lista el siguiente Tab partiría del final
          de <body> (la X del Modal) y no del campo de abajo. */}
      <FloatingPanel
        ref={panelRef}
        anchorRef={boxRef}
        open={open}
        maxHeight={288}
        id={listaId}
        role="listbox"
        onMouseDown={(e) => e.preventDefault()}
        className="overflow-auto rounded-lg border border-border bg-surface shadow-lg"
      >
        {filtered.length === 0 ? (
          <div className="px-3 py-2 text-sm text-muted">{emptyText}</div>
        ) : (
          filtered.map((o, i) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              onClick={() => pick(o)}
              onMouseEnter={() => setHi(i)}
              className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                i === hi ? "bg-accent/10" : "hover:bg-surface-2"
              }`}
            >
              <span className="truncate">
                <span className="font-medium">{o.label}</span>
                {o.hint && <span className="ml-2 text-xs text-muted">{o.hint}</span>}
              </span>
              {o.value === value && <Check size={15} className="shrink-0 text-accent" />}
            </button>
          ))
        )}
      </FloatingPanel>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// 3) MultiSearchSelect — búsqueda + dropdown que junta VARIOS valores (chips)
// ─────────────────────────────────────────────────────────────────────────────
/**
 * Como SearchSelect, pero cada elección se suma como chip. Con `allowCustom`
 * se puede agregar lo escrito aunque no esté en la lista (p. ej. una serie que
 * sólo existe en el SAE): Enter o la opción «Agregar …». `normalize` pasa lo
 * escrito a su forma canónica antes de agregarlo (mayúsculas, sin espacios).
 */
export function MultiSearchSelect({
  options,
  values,
  onChange,
  placeholder = "Buscar y agregar…",
  emptyText = "Sin coincidencias.",
  allowCustom = false,
  normalize = (s: string) => s.trim(),
  className,
}: {
  options: SearchOption[];
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  emptyText?: ReactNode;
  allowCustom?: boolean;
  normalize?: (s: string) => string;
  className?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listaId = useId();

  const ql = norm(q.trim());
  const filtered = options.filter(
    (o) => !values.includes(o.value) && (!ql || norm(`${o.label} ${o.hint ?? ""}`).includes(ql)),
  );
  const custom = allowCustom ? normalize(q) : "";
  const ofrecerCustom =
    !!custom && !values.includes(custom) && !options.some((o) => o.value === custom);
  // Lista navegable: las coincidencias y, al final, «Agregar …» si aplica.
  const items: SearchOption[] = ofrecerCustom
    ? [...filtered, { value: custom, label: `Agregar «${custom}»` }]
    : filtered;
  const etiqueta = (v: string) => options.find((o) => o.value === v)?.label ?? v;

  useEffect(() => setHi(0), [q, open]);
  useEffect(() => {
    // La lista vive en un portal (FloatingPanel), fuera de boxRef: un clic en
    // ella no cuenta como «fuera».
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (boxRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function add(v: string) {
    if (v && !values.includes(v)) onChange([...values, v]);
    setQ("");
    inputRef.current?.focus();
  }

  return (
    <div ref={boxRef} className={`relative ${className ?? ""}`}>
      <div
        className="flex min-h-[38px] w-full flex-wrap items-center gap-1 rounded-lg border border-border bg-background py-1 pl-2 pr-8 text-sm focus-within:border-accent"
        onClick={() => inputRef.current?.focus()}
      >
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1 rounded-md bg-surface-2 px-2 py-0.5 text-xs font-medium"
          >
            {etiqueta(v)}
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onChange(values.filter((x) => x !== v));
              }}
              aria-label={`Quitar ${etiqueta(v)}`}
              className="rounded text-muted hover:text-foreground"
            >
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={open ? listaId : undefined}
          aria-label={placeholder}
          value={q}
          placeholder={values.length ? "" : placeholder}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setHi((h) => Math.min(h + 1, Math.max(items.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHi((h) => Math.max(h - 1, 0));
            } else if (e.key === "Enter" || e.key === ",") {
              // Coma = separador, como en el campo de texto de antes.
              if (e.key === "," && !q.trim()) {
                e.preventDefault();
              } else if (open && items[hi]) {
                e.preventDefault();
                add(items[hi].value);
              }
            } else if (e.key === "Backspace" && !q && values.length) {
              onChange(values.slice(0, -1));
            } else if (e.key === "Escape") {
              // Con la lista abierta, Escape cierra solo la lista.
              if (open) {
                e.preventDefault();
                e.stopPropagation();
              }
              setOpen(false);
            } else if (e.key === "Tab") {
              // La lista vive al final de <body>: el Tab ya no pasa por ella. Se
              // cierra y el foco sigue al siguiente campo (las flechas eligen).
              setOpen(false);
            }
          }}
          className="min-w-[6rem] flex-1 bg-transparent py-1 outline-none"
        />
      </div>
      <ChevronDown
        size={15}
        className={`pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted transition-transform ${open ? "rotate-180" : ""}`}
      />

      {/* En portal: dentro de un Modal (cuerpo con overflow-auto) ya no se corta.
          mousedown sin default: el foco se queda en la caja mientras se suman
          chips (add() lo devolvía ahí de todos modos). */}
      <FloatingPanel
        ref={panelRef}
        anchorRef={boxRef}
        open={open}
        maxHeight={240}
        id={listaId}
        role="listbox"
        aria-multiselectable
        onMouseDown={(e) => e.preventDefault()}
        className="overflow-auto rounded-lg border border-border bg-surface shadow-lg"
      >
        {items.length === 0 ? (
          <div className="px-3 py-2 text-sm text-muted">{emptyText}</div>
        ) : (
          items.map((o, i) => (
            <button
              key={o.value + (i === filtered.length ? ":custom" : "")}
              type="button"
              role="option"
              aria-selected={false}
              onClick={(e) => {
                e.preventDefault();
                add(o.value);
              }}
              onMouseEnter={() => setHi(i)}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                i === hi ? "bg-accent/10" : "hover:bg-surface-2"
              }`}
            >
              <span className="font-medium">{o.label}</span>
              {o.hint && <span className="text-xs text-muted">{o.hint}</span>}
            </button>
          ))
        )}
      </FloatingPanel>
    </div>
  );
}

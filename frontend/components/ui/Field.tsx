"use client";

import {
  Children,
  forwardRef,
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Eye, EyeOff, Search } from "lucide-react";
import type {
  ChangeEvent,
  InputHTMLAttributes,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

const BASE =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent disabled:bg-surface-2 disabled:text-muted disabled:opacity-60";

export function Field({
  label,
  children,
  hint,
  required,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">
        {label}
        {required && <span className="text-danger"> *</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className = "", ...rest }, ref) {
    return <input ref={ref} {...rest} className={`${BASE} ${className}`} />;
  },
);

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className = "", ...rest } = props;
  return <textarea {...rest} className={`${BASE} ${className}`} />;
}

/** Input de contraseña con ojo de mostrar/ocultar. */
export function PasswordInput({
  className = "",
  disabled,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <Input type={show ? "text" : "password"} disabled={disabled} className={`pr-9 ${className}`} {...rest} />
      <button
        type="button"
        onClick={() => setShow((v) => !v)}
        disabled={disabled}
        aria-label={show ? "Ocultar contraseña" : "Mostrar contraseña"}
        className="absolute inset-y-0 right-0 flex items-center px-2.5 text-muted hover:text-foreground disabled:opacity-60"
      >
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Select — dropdown con el diseño de la app (no usa el <select> nativo del SO).
// Mantiene la API del <select>: `value`, `onChange(e => e.target.value)`,
// `disabled`, `className` y `<option>` como hijos. El panel se renderiza en un
// portal con posición fija (flip arriba/abajo) para no recortarse en modales.
// ─────────────────────────────────────────────────────────────────────────────
type Opt = { value: string; label: string; disabled: boolean };

/** Convierte los hijos de un <option> a texto plano (soporta strings concatenados). */
function optionText(node: ReactNode): string {
  if (node == null || node === false || node === true) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(optionText).join("");
  if (isValidElement(node)) return optionText((node.props as { children?: ReactNode }).children);
  return "";
}

/** Recoge los <option> (incluidos los que vienen dentro de fragmentos / arrays). */
function collectOptions(children: ReactNode, out: Opt[] = []): Opt[] {
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    if (child.type === "option") {
      const p = child.props as { value?: string | number; disabled?: boolean; children?: ReactNode };
      const label = optionText(p.children);
      out.push({ value: p.value !== undefined ? String(p.value) : label, label, disabled: !!p.disabled });
    } else {
      // Fragmentos u otros wrappers: baja un nivel.
      collectOptions((child.props as { children?: ReactNode }).children, out);
    }
  });
  return out;
}

/** Siguiente índice habilitado en la dirección dada (salta deshabilitados). */
function nextEnabled(opts: Opt[], from: number, dir: 1 | -1): number {
  for (let i = from + dir; i >= 0 && i < opts.length; i += dir) {
    if (!opts[i].disabled) return i;
  }
  return from >= 0 && from < opts.length && !opts[from]?.disabled ? from : from;
}

/** Normaliza para filtrar sin acentos ni mayúsculas. */
const norm = (s: string) => s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

/** A partir de cuántas opciones el panel muestra una caja de filtro (catálogos
 * SAT largos: régimen fiscal, uso de CFDI, forma de pago...). Listas cortas
 * (p. ej. Activo/Suspendido/Baja) no la necesitan. */
const FILTER_THRESHOLD = 8;

export function Select({
  value,
  onChange,
  disabled,
  className = "",
  children,
}: SelectHTMLAttributes<HTMLSelectElement>) {
  const options = useMemo(() => collectOptions(children), [children]);
  const current = value != null ? String(value) : "";
  const selected = options.find((o) => o.value === current) ?? null;
  // Listas largas (catálogos SAT: régimen fiscal, uso de CFDI, forma de pago…)
  // muestran una caja de filtro arriba del panel; listas cortas no la necesitan.
  const filterable = options.length > FILTER_THRESHOLD;

  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const [q, setQ] = useState("");
  const [rect, setRect] = useState<
    { left: number; top: number; width: number; maxWidth: number; maxHeight: number } | null
  >(null);
  const [mounted, setMounted] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);

  useEffect(() => setMounted(true), []);

  const filtered = useMemo(() => {
    const ql = norm(q.trim());
    return ql ? options.filter((o) => norm(o.label).includes(ql)) : options;
  }, [options, q]);

  const place = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 4;
    const spaceBelow = window.innerHeight - r.bottom - 8;
    const spaceAbove = r.top - 8;
    // El alto deseado se calcula sobre TODAS las opciones (no las filtradas) +
    // la caja de filtro si aplica, para que el panel no cambie de tamaño en
    // cada tecleo — solo su contenido interno crece/scrollea.
    const desired = Math.min(options.length * 38 + 8, 380) + (filterable ? 44 : 0);
    const up = spaceBelow < Math.min(desired, 160) && spaceAbove > spaceBelow;
    // Tope al alto deseado además del espacio disponible: con muchas opciones
    // (p. ej. régimen fiscal SAT) el panel scrollea en vez de crecer hasta
    // salirse de la pantalla y dejar opciones inalcanzables.
    const maxHeight = Math.max(120, Math.min(desired, Math.floor(up ? spaceAbove : spaceBelow)));
    const top = up ? r.top - gap - Math.min(desired, maxHeight) : r.bottom + gap;
    // El panel arranca del ancho del trigger pero puede CRECER con el
    // contenido: los nombres largos (razones sociales) salían cortados en un
    // filtro angosto. Tope: lo que quepa en pantalla.
    setRect({
      left: r.left,
      top,
      width: r.width,
      maxWidth: Math.min(560, window.innerWidth - 16),
      maxHeight,
    });
  }, [options.length, filterable]);

  // Si el panel creció más allá del borde derecho, se recorre a la izquierda.
  // Corre una sola vez por apertura: al mover `left` el desbordamiento se anula.
  useLayoutEffect(() => {
    if (!open || !rect || !panelRef.current) return;
    const ancho = panelRef.current.offsetWidth;
    const sobra = rect.left + ancho - (window.innerWidth - 8);
    if (sobra > 0) setRect((r) => (r ? { ...r, left: Math.max(8, r.left - sobra) } : r));
  }, [open, rect]);

  const openMenu = useCallback(() => {
    if (disabled) return;
    place();
    setQ("");
    setHi(Math.max(options.findIndex((o) => o.value === current), 0));
    setOpen(true);
  }, [disabled, place, options, current]);

  // Al abrir con filtro, foco directo a la caja de búsqueda (como los diálogos
  // "Ayuda de catálogo" de referencia: escribir para filtrar de inmediato).
  useEffect(() => {
    if (open && filterable) filterRef.current?.focus();
  }, [open, filterable]);

  // Cierre por clic fuera, Escape, scroll de la página o resize (evita que el
  // panel quede a la deriva si el trigger se mueve). El listener de scroll usa
  // fase de captura porque `scroll` no burbujea — pero eso también lo dispara
  // al scrollear la LISTA PROPIA del panel (su `overflow-auto` interno), lo
  // que cerraba el dropdown en vez de dejarlo scrollear. Se ignora cuando el
  // scroll viene de dentro del panel mismo.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onClose() {
      setOpen(false);
    }
    function onWindowScroll(e: Event) {
      const t = e.target;
      if (t instanceof Node && panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", onClose);
    window.addEventListener("scroll", onWindowScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("scroll", onWindowScroll, true);
    };
  }, [open]);

  // `hi` corre sobre `filtered`, no `options`: al teclear en la caja de filtro
  // la lista visible cambia, así que el índice resaltado también debe partir
  // de cero para no apuntar a algo que ya no está en pantalla.
  useEffect(() => setHi(0), [q]);

  function pick(o: Opt) {
    if (o.disabled) return;
    onChange?.({
      target: { value: o.value },
      currentTarget: { value: o.value },
    } as unknown as ChangeEvent<HTMLSelectElement>);
    setOpen(false);
    triggerRef.current?.focus();
  }

  /** Navegación por teclado del panel (flechas/Enter/Escape/Home/End), común al
   * trigger y a la caja de filtro. `allowSpacePick` distingue ambos: en el
   * trigger la barra espaciadora selecciona (como un <select> nativo); en la
   * caja de filtro debe poder escribirse un espacio normalmente. */
  function navigate(e: ReactKeyboardEvent, allowSpacePick: boolean) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((h) => nextEnabled(filtered, h, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => nextEnabled(filtered, h, -1));
    } else if (e.key === "Enter" || (allowSpacePick && e.key === " ")) {
      e.preventDefault();
      const o = filtered[hi];
      if (o) pick(o);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      setHi(nextEnabled(filtered, -1, 1));
    } else if (e.key === "End") {
      e.preventDefault();
      setHi(nextEnabled(filtered, filtered.length, -1));
    }
  }

  function onKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (disabled) return;
    if (!open) {
      if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    // Con caja de filtro, el foco vive ahí (ver `useEffect` de arriba) y es
    // ella quien maneja la navegación; el trigger solo abre/cierra.
    if (!filterable) navigate(e, true);
  }

  function onFilterKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    navigate(e, false);
  }

  return (
    <div className="relative">
      <div
        ref={triggerRef}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-disabled={disabled || undefined}
        tabIndex={disabled ? -1 : 0}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        className={`flex w-full items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm outline-none transition ${
          disabled
            ? "cursor-not-allowed border-border bg-surface-2 text-muted"
            : `cursor-pointer border-border bg-background hover:border-accent/60 focus:border-accent ${
                open ? "border-accent" : ""
              }`
        } ${className}`}
      >
        <span className={`truncate ${selected ? "" : "text-muted"}`}>
          {selected ? selected.label : " "}
        </span>
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
              width: "max-content",
              minWidth: rect.width,
              maxWidth: rect.maxWidth,
              maxHeight: rect.maxHeight,
              zIndex: 50,
            }}
            className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-lg"
          >
            {filterable && (
              <div className="relative shrink-0 border-b border-border">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
                />
                <input
                  ref={filterRef}
                  type="text"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={onFilterKeyDown}
                  placeholder="Filtrar…"
                  aria-label="Filtrar opciones"
                  className="w-full bg-transparent py-2 pl-8 pr-3 text-sm outline-none"
                />
              </div>
            )}
            <div role="listbox" className="overflow-auto">
              {options.length === 0 ? (
                <div className="px-3 py-2 text-sm text-muted">Sin opciones</div>
              ) : filtered.length === 0 ? (
                <div className="px-3 py-2 text-sm text-muted">Sin coincidencias.</div>
              ) : (
                filtered.map((o, i) => {
                  const isSel = o.value === current;
                  return (
                    <button
                      key={`${o.value}-${i}`}
                      type="button"
                      role="option"
                      aria-selected={isSel}
                      disabled={o.disabled}
                      onClick={() => pick(o)}
                      onMouseEnter={() => !o.disabled && setHi(i)}
                      className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                        o.disabled
                          ? "cursor-not-allowed text-muted/60"
                          : i === hi
                            ? "bg-accent/10"
                            : "hover:bg-surface-2"
                      }`}
                    >
                      <span className="truncate">{o.label}</span>
                      {isSel && <Check size={15} className="shrink-0 text-accent" />}
                    </button>
                  );
                })
              )}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}

export const Checkbox = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Checkbox({ className = "", ...rest }, ref) {
    return (
      <input
        ref={ref}
        type="checkbox"
        {...rest}
        className={`h-4 w-4 rounded border-border accent-accent disabled:opacity-60 ${className}`}
      />
    );
  },
);

export function Switch({
  checked,
  onChange,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
        checked ? "bg-accent" : "bg-border"
      } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${
          checked ? "translate-x-5" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

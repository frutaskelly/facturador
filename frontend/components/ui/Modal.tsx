"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

// Selector de elementos enfocables para el trap de Tab.
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
  resizable = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  /** Asa de resize en la esquina inferior derecha. Desactívala en diálogos
   * chicos (confirmaciones sí/no): su botón de acción suele caer justo ahí, y
   * un clic que roce el asa nativa del navegador arranca un arrastre en vez
   * de disparar el botón — se siente como que la app se congeló. */
  resizable?: boolean;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // Trap de Tab: el foco circula dentro del modal (patrón de diálogo).
      if (e.key === "Tab") {
        const root = dialogRef.current;
        if (!root) return;
        const focusables = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (focusables.length === 0) {
          e.preventDefault();
          root.focus();
          return;
        }
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (!(active instanceof Node) || !root.contains(active)) {
          // Si el foco está en OTRO diálogo (modal anidado encima), su propio
          // trap lo maneja; este no debe robárselo.
          if (active instanceof Element && active.closest('[role="dialog"]')) return;
          // El foco se escapó del modal: se recaptura.
          e.preventDefault();
          first.focus();
        } else if (e.shiftKey && (active === first || active === root)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    if (open) document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Foco: al abrir, guarda el elemento activo y enfoca el contenedor del
  // diálogo; al cerrar, lo restaura donde estaba.
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    return () => prev?.focus();
  }, [open]);

  if (!open) return null;

  // Posición fija (no flex/margin-auto, no transform): `resize` asume que la
  // esquina superior-izquierda queda quieta y solo width/height crecen. Un
  // contenedor que recentra (flex/margin:auto/translate(-50%)) recalcula la
  // posición en cada frame del arrastre, peleando contra el resize — crecía a
  // medias hacia la derecha y podía "desaparecer" al crecer hacia abajo.
  // `left`/`top` de abajo son constantes (no dependen del tamaño propio del
  // modal), calculadas para que arranque centrado según su ancho máximo.
  const maxWidthRem = wide ? 48 : 32; // max-w-3xl / max-w-lg, en rem

  return (
    <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={{
          left: `max(1rem, calc(50vw - ${maxWidthRem / 2}rem))`,
          top: "6vh",
          maxWidth: `min(calc(100vw - 2rem), ${maxWidthRem}rem)`,
        }}
        // `resize` + `overflow-hidden` habilita el asa nativa del navegador en la
        // esquina inferior derecha (líneas diagonales) para agrandar/achicar el
        // modal hacia abajo y hacia la derecha. min-w/min-h evitan que se pueda
        // arrastrar hasta desaparecer; max-width es el tamaño de arranque y
        // también el tope al agrandar (con clamp aparte para no desbordar en
        // pantallas angostas).
        className={`fixed flex max-h-[90vh] w-full min-w-[22rem] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-xl outline-none ${
          resizable ? "min-h-[16rem] resize" : ""
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3">
          <h2 id={titleId} className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Cerrar" className="rounded-lg p-1.5 text-muted hover:bg-surface-2">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex shrink-0 justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>
        )}
        {resizable && (
          // Refuerzo visual del asa de resize nativa (líneas diagonales), por si
          // el navegador no la dibuja con suficiente contraste. No intercepta
          // clics: el arrastre real lo maneja el navegador vía `resize`.
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute bottom-0.5 right-0.5 text-muted/50"
            width="10"
            height="10"
            viewBox="0 0 10 10"
          >
            <line x1="9" y1="1" x2="1" y2="9" stroke="currentColor" strokeWidth="1" />
            <line x1="9" y1="5" x2="5" y2="9" stroke="currentColor" strokeWidth="1" />
          </svg>
        )}
      </div>
    </div>
  );
}

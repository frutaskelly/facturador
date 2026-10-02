"use client";

import {
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { X } from "lucide-react";

// Selector de elementos enfocables para el trap de Tab.
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Los cuatro tamaños de popup de la app. Elige por lo que lleva adentro:
 * - `sm`: confirmar o avisar (sí/no, «listo», 1–2 campos). Sin esquina para agrandar.
 * - `md`: capturar o buscar (formulario de una columna, buscador, enviar por correo). Por defecto.
 * - `lg`: formularios grandes (dos columnas, una tabla chica de partidas).
 * - `xl`: tablas grandes (precios de una lista, importar, generar factura). Alto fijo.
 */
export type ModalSize = "sm" | "md" | "lg" | "xl";

// Posición fija anclada arriba y centrada (no flex/margin-auto, no transform):
// `resize` asume que la esquina superior-izquierda queda quieta y solo
// width/height crecen. Un contenedor que recentra recalcula la posición en
// cada frame del arrastre y pelea contra el resize. `left`/`top` son
// constantes (no dependen del tamaño propio del modal), calculadas para que
// arranque centrado según su ancho. El CSS que las usa vive en globals.css
// (`.app-modal`): en celular el popup ocupa toda la pantalla.
const SIZES: Record<ModalSize, Record<string, string>> = {
  sm: {
    "--m-w": "min(28rem, calc(100vw - 2rem))",
    "--m-left": "max(1rem, calc(50vw - 14rem))",
    "--m-top": "8vh",
    "--m-h": "auto",
    "--m-min-h": "0px",
  },
  md: {
    "--m-w": "min(36rem, calc(100vw - 2rem))",
    "--m-left": "max(1rem, calc(50vw - 18rem))",
    "--m-top": "8vh",
    "--m-h": "auto",
    // Piso de alto: los buscadores no se ven chaparros mientras llegan resultados.
    "--m-min-h": "min(20rem, 85vh)",
  },
  lg: {
    "--m-w": "min(52rem, calc(100vw - 2rem))",
    "--m-left": "max(1rem, calc(50vw - 26rem))",
    "--m-top": "8vh",
    "--m-h": "auto",
    // Piso al encogerlo con la esquina: que no esconda el pie.
    "--m-min-h": "min(16rem, 85vh)",
  },
  xl: {
    "--m-w": "min(90vw, 80rem)",
    "--m-left": "max(1rem, calc(50vw - min(45vw, 40rem)))",
    "--m-top": "7.5vh",
    // Alto fijo: la tabla no brinca al filtrar ni al paginar.
    "--m-h": "85vh",
    "--m-min-h": "min(16rem, 85vh)",
  },
};
// Todas las variables se definen en cada tamaño: las custom properties se
// heredan, y un modal anidado (vive dentro del DOM del padre, sin portal) se
// llevaría el alto fijo de un xl o el piso de un md.

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  footerStart,
  size = "md",
  resizable,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Una línea de ayuda bajo el título (p. ej. «Busca primero para evitar
   *  duplicados»). Va en el encabezado, que no hace scroll. */
  description?: ReactNode;
  children: ReactNode;
  /** Botones de la derecha del pie, en este orden: Cancelar/Cerrar primero y la
   *  acción principal al final. Marca la principal con `data-modal-primary`
   *  para que Enter la ejecute desde una caja de texto (no en las destructivas). */
  footer?: ReactNode;
  /** Lado izquierdo del pie: notas o acciones secundarias. */
  footerStart?: ReactNode;
  size?: ModalSize;
  /** Asa de resize en la esquina inferior derecha. Por defecto en md, lg y xl;
   * apagada en sm: en las confirmaciones el botón de acción cae justo ahí, y un
   * clic que roce el asa nativa del navegador arranca un arrastre en vez de
   * disparar el botón — se siente como que la app se congeló. */
  resizable?: boolean;
}) {
  const titleId = useId();
  const descId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const conAsa = resizable ?? size !== "sm";

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // El modal NO se cierra con Escape ni con clic fuera: solo con sus
      // botones de acción (Cancelar / Guardar / la X). Cerrarlo por accidente
      // a media captura tira todo lo que el usuario llevaba escrito.
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
          // Una lista flotante (FloatingPanel) vive fuera del modal, en <body>:
          // tabular desde ella no debe recapturar el foco.
          if (active instanceof Element && active.closest('[role="listbox"]')) return;
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

  // Foco: quien abrió el modal se recuerda durante el render en que `open`
  // pasa a true, ANTES de que los hijos se enfoquen solos (autoFocus, o un
  // buscador que se enfoca en su efecto). Al abrir, el contenedor solo toma el
  // foco si ningún hijo lo tomó ya; al cerrar, vuelve a quien lo abrió.
  const opener = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  if (open && !wasOpen.current && typeof document !== "undefined") {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  wasOpen.current = open;
  useEffect(() => {
    if (!open) return;
    const root = dialogRef.current;
    if (root && !root.contains(document.activeElement)) root.focus();
    return () => {
      const prev = opener.current;
      if (prev && prev.isConnected) prev.focus();
    };
  }, [open]);

  // Enter en una caja de texto ejecuta la acción principal marcada con
  // `data-modal-primary`. No interviene si alguien ya manejó la tecla (un
  // buscador eligiendo de su lista), si la caja vive dentro de un <form> (el
  // form ya hace submit) o si la tecla viene de un modal anidado.
  function onDialogKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    // `repeat`: la tecla sostenida no dispara la acción dos veces.
    if (e.key !== "Enter" || e.defaultPrevented || e.repeat) return;
    if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || e.nativeEvent.isComposing) return;
    const t = e.target;
    if (!(t instanceof HTMLInputElement) || t.form) return;
    if (["checkbox", "radio", "button", "submit", "reset", "file"].includes(t.type)) return;
    if (t.getAttribute("aria-expanded") === "true") return;
    const root = dialogRef.current;
    if (!root || t.closest('[role="dialog"]') !== root) return;
    const primary = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-modal-primary]")).find(
      (b) => b.closest('[role="dialog"]') === root,
    );
    if (!primary || primary.disabled) return;
    e.preventDefault();
    primary.click();
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 bg-black/30">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        data-size={size}
        data-resizable={conAsa}
        onKeyDown={onDialogKeyDown}
        style={SIZES[size] as CSSProperties}
        // `.app-modal` (globals.css) pone posición y tamaño. `overflow-hidden`
        // además habilita el asa nativa de `resize` en la esquina inferior
        // derecha: agranda hacia abajo y hacia la derecha hasta la orilla de la
        // pantalla.
        className="app-modal flex flex-col overflow-hidden border-border bg-background shadow-xl outline-none"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-5 py-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-base font-semibold">{title}</h2>
            {description && (
              <p id={descId} className="mt-0.5 text-sm text-muted">{description}</p>
            )}
          </div>
          <button onClick={onClose} aria-label="Cerrar" className="-mr-1.5 shrink-0 rounded-lg p-1.5 text-muted hover:bg-surface-2">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4">{children}</div>
        {(footer || footerStart) && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-border px-5 py-3">
            {footerStart && <div className="flex flex-wrap items-center gap-2">{footerStart}</div>}
            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{footer}</div>
          </div>
        )}
        {conAsa && (
          // Refuerzo visual del asa de resize nativa (líneas diagonales), por si
          // el navegador no la dibuja con suficiente contraste. No intercepta
          // clics: el arrastre real lo maneja el navegador vía `resize`. En
          // celular no hay asa (el popup ya ocupa la pantalla).
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute bottom-0.5 right-0.5 hidden text-muted/50 sm:block"
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

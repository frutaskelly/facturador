"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

// ─────────────────────────────────────────────────────────────────────────────
// FloatingPanel — la lista de un buscador, pintada ENCIMA de todo.
//
// Los buscadores (producto, cliente, clave SAT, categoría…) pintaban su lista
// con `absolute` dentro del cuerpo del Modal, que tiene `overflow-auto`: la
// lista quedaba atrapada y se cortaba. Este panel va en un portal a <body> con
// posición fija, anclado al elemento que se le pase, y se voltea hacia arriba
// cuando abajo no cabe. Mismo patrón que el <Select> de Field.tsx.
//
// OJO para quien lo use: como el panel ya no vive dentro del contenedor del
// buscador, el "clic fuera" que cierra la lista debe revisar TAMBIÉN el panel
// (el ref que se pasa a este componente), o un clic en una opción la cierra
// antes de elegirla.
// ─────────────────────────────────────────────────────────────────────────────

type Pos = { left: number; width: number; maxHeight: number; top?: number; bottom?: number };

/** Rectángulo visible del elemento: la ventana recortada por cada ancestro que
 *  corta su contenido (el cuerpo con scroll de un Modal, una tabla, un panel). */
function recorte(el: HTMLElement) {
  let top = 0;
  let left = 0;
  let bottom = window.innerHeight;
  let right = window.innerWidth;
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (/(auto|scroll|hidden|clip)/.test(`${s.overflowX} ${s.overflowY}`)) {
      const r = p.getBoundingClientRect();
      top = Math.max(top, r.top);
      left = Math.max(left, r.left);
      bottom = Math.min(bottom, r.bottom);
      right = Math.min(right, r.right);
    }
  }
  return { top, left, bottom, right };
}

export const FloatingPanel = forwardRef<
  HTMLDivElement,
  {
    /** Elemento al que se ancla (normalmente la caja de texto o su contenedor). */
    anchorRef: RefObject<HTMLElement | null>;
    open: boolean;
    children: ReactNode;
    /** Alto máximo en px (se recorta además a lo que quepa en pantalla). */
    maxHeight?: number;
    /** Ancho mínimo en px; por defecto el del ancla. El panel nunca es más
     *  angosto que el ancla. */
    minWidth?: number;
    /** `end` alinea la orilla derecha del panel con la del ancla. */
    align?: "start" | "end";
  } & Omit<HTMLAttributes<HTMLDivElement>, "style">
>(function FloatingPanel(
  { anchorRef, open, children, maxHeight = 288, minWidth = 0, align = "start", className = "", ...rest },
  ref,
) {
  const [mounted, setMounted] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setMounted(true), []);

  const setRefs = useCallback(
    (el: HTMLDivElement | null) => {
      panelRef.current = el;
      if (typeof ref === "function") ref(el);
      else if (ref) ref.current = el;
    },
    [ref],
  );

  const place = useCallback(() => {
    const a = anchorRef.current;
    if (!a) return;
    const r = a.getBoundingClientRect();
    // Si al scrollear el ancla se sale de la vista (debajo del encabezado del
    // Modal, fuera del cuerpo de una tabla), la lista se esconde en vez de
    // quedarse flotando suelta; reaparece al volver.
    const c = recorte(a);
    const altoVisible = Math.min(r.bottom, c.bottom) - Math.max(r.top, c.top);
    if (altoVisible < r.height / 2 || r.right <= c.left || r.left >= c.right) {
      setPos(null);
      return;
    }
    const gap = 4;
    const margin = 8;
    const width = Math.min(Math.max(r.width, minWidth), window.innerWidth - margin * 2);
    let left = align === "end" ? r.right - width : r.left;
    left = Math.max(margin, Math.min(left, window.innerWidth - margin - width));
    const below = window.innerHeight - r.bottom - gap - margin;
    const above = r.top - gap - margin;
    // Abajo por defecto; arriba solo si abajo no cabe ni lo mínimo y arriba hay más.
    const up = below < Math.min(maxHeight, 160) && above > below;
    const room = Math.max(96, Math.floor(up ? above : below));
    setPos(
      up
        ? { left, width, maxHeight: Math.min(maxHeight, room), bottom: window.innerHeight - r.top + gap }
        : { left, width, maxHeight: Math.min(maxHeight, room), top: r.bottom + gap },
    );
  }, [anchorRef, maxHeight, minWidth, align]);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  // Sigue al ancla: si la página, el cuerpo del Modal o la ventana se mueven,
  // el panel se reacomoda (o se esconde, si el ancla salió de la vista). Se
  // ignora el scroll de la propia lista (`scroll` no burbujea; se escucha en
  // captura).
  useEffect(() => {
    if (!open) return;
    function onScroll(e: Event) {
      const t = e.target;
      if (t instanceof Node && panelRef.current?.contains(t)) return;
      place();
    }
    window.addEventListener("resize", place);
    window.addEventListener("scroll", onScroll, true);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    if (ro && anchorRef.current) ro.observe(anchorRef.current);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", onScroll, true);
      ro?.disconnect();
    };
  }, [open, place, anchorRef]);

  if (!mounted || !open || !pos) return null;

  const style: CSSProperties = {
    position: "fixed",
    left: pos.left,
    width: pos.width,
    maxHeight: pos.maxHeight,
    top: pos.top,
    bottom: pos.bottom,
    // Encima del Modal (z-40) y por debajo de los avisos (z-70).
    zIndex: 50,
  };

  return createPortal(
    <div ref={setRefs} style={style} className={className} {...rest}>
      {children}
    </div>,
    document.body,
  );
});

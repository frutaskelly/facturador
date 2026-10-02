"use client";

// Autocompletar de clave SAT (c_ClaveProdServ) contra el catálogo OFICIAL
// cargado en la base (GET /sat/claves): escribe texto ("cilantro") o un
// prefijo de clave ("50404") y elige del catálogo — nunca claves inventadas.
// Muestra la descripción oficial de la clave elegida como confirmación.
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { Input } from "@/components/ui/Field";
import { FloatingPanel } from "@/components/ui/FloatingPanel";
import { apiFetch } from "@/lib/api";

type Opcion = { clave: string; descripcion: string };

/** La lista vive al final de <body> (FloatingPanel), no junto a la caja: el Tab
 *  de la página ya no pasa por sus opciones como antes. Se recorre a mano y, al
 *  salir por una orilla, dice por cuál para que el foco regrese a la caja. */
function tabEnLista(e: ReactKeyboardEvent<HTMLElement>): "dentro" | "antes" | "despues" {
  e.preventDefault();
  const f = Array.from(
    e.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"),
  );
  const j = f.indexOf(document.activeElement as HTMLElement) + (e.shiftKey ? -1 : 1);
  if (j >= 0 && j < f.length) {
    f[j].focus();
    return "dentro";
  }
  return e.shiftKey ? "antes" : "despues";
}

export function SatClaveCombobox({
  value,
  onChange,
  placeholder = "Texto o clave: cilantro, 50404…",
}: {
  value: string;
  onChange: (clave: string) => void;
  placeholder?: string;
}) {
  const [texto, setTexto] = useState(value);
  const [opciones, setOpciones] = useState<Opcion[]>([]);
  const [abierto, setAbierto] = useState(false);
  const [descripcion, setDescripcion] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listaId = useId();
  // Al elegir una opción, o con un valor puesto desde fuera, no se re-busca.
  // Arranca en true: el valor inicial no abre la lista al montar.
  const skipSearch = useRef(true);
  const sinAbrir = useRef(false);     // foco devuelto a la caja sin reabrir la lista
  const mostrar = abierto && opciones.length > 0;

  // El valor puede cambiar desde fuera (sugerencia IA, abrir edición). Los
  // padres devuelven como `value` lo mismo que se teclea (onChange): ese eco no
  // es un cambio de fuera, y marcar el salto ahí cancelaba cada búsqueda.
  useEffect(() => {
    if (value === texto.trim()) return;
    setTexto(value);
    skipSearch.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // Búsqueda con debounce contra el catálogo oficial.
  useEffect(() => {
    if (skipSearch.current) {
      skipSearch.current = false;
      // Aun así se resuelve la descripción oficial de una clave completa.
      if (/^\d{8}$/.test(texto.trim())) {
        apiFetch<Opcion[]>(`/api/v1/sat/claves?q=${encodeURIComponent(texto.trim())}`)
          .then((ops) => {
            const exacta = ops.find((o) => o.clave === texto.trim());
            setDescripcion(exacta ? exacta.descripcion : "");
          })
          .catch(() => setDescripcion(""));
      } else {
        setDescripcion("");
      }
      return;
    }
    const q = texto.trim();
    if (q.length < 2) {
      setOpciones([]);
      return;
    }
    const t = setTimeout(() => {
      apiFetch<Opcion[]>(`/api/v1/sat/claves?q=${encodeURIComponent(q)}&limit=8`)
        .then((ops) => {
          setOpciones(ops);
          setAbierto(true);
          const exacta = ops.find((o) => o.clave === q);
          setDescripcion(exacta ? exacta.descripcion : "");
        })
        .catch(() => setOpciones([]));
    }, 300);
    return () => clearTimeout(t);
  }, [texto]);

  // Cierra el panel al hacer clic fuera. La lista vive en un portal
  // (FloatingPanel), fuera de wrapRef: un clic en ella no es «fuera».
  useEffect(() => {
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (wrapRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setAbierto(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  function elegir(o: Opcion) {
    skipSearch.current = true;
    setTexto(o.clave);
    setDescripcion(o.descripcion);
    // Cierra y regresa el foco a la caja: la opción (clic o Tab + Enter) se
    // desmonta al final de <body> y el siguiente Tab saltaría a la X del Modal
    // en vez de seguir a «Unidad SAT».
    volverACaja(true);
    onChange(o.clave);
  }

  /** Devuelve el foco a la caja desde la lista; `cerrar` la cierra sin que el
   *  onFocus de la caja la vuelva a abrir. */
  function volverACaja(cerrar: boolean) {
    sinAbrir.current = cerrar;
    if (cerrar) setAbierto(false);
    inputRef.current?.focus({ preventScroll: true });
    sinAbrir.current = false;
  }

  return (
    <div ref={wrapRef} className="relative">
      <Input
        ref={inputRef}
        role="combobox"
        aria-autocomplete="list"
        // Con la lista abierta, Enter no dispara la acción principal del Modal.
        aria-expanded={mostrar}
        aria-controls={mostrar ? listaId : undefined}
        value={texto}
        placeholder={placeholder}
        onChange={(e) => {
          setTexto(e.target.value);
          onChange(e.target.value.trim());
        }}
        onFocus={() => {
          if (sinAbrir.current) return;
          if (opciones.length > 0) setAbierto(true);
        }}
        // Tras elegir, la caja ya tiene el foco y onFocus no se repite.
        onClick={() => {
          if (!abierto && opciones.length > 0) setAbierto(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape" && mostrar) {
            // Cierra solo la lista, no lo que la contiene.
            e.preventDefault();
            e.stopPropagation();
            setAbierto(false);
          } else if (e.key === "Tab" && mostrar) {
            // Como cuando la lista iba pegada a la caja: Tab entra a las opciones.
            const primera = e.shiftKey
              ? null
              : panelRef.current?.querySelector<HTMLElement>("button:not([disabled])");
            if (primera) {
              e.preventDefault();
              primera.focus();
            } else {
              setAbierto(false);
            }
          }
        }}
      />
      {/* En portal: dentro de un Modal (cuerpo con overflow-auto) ya no se corta. */}
      <FloatingPanel
        ref={panelRef}
        anchorRef={inputRef}
        open={mostrar}
        maxHeight={224}
        id={listaId}
        role="listbox"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            volverACaja(true);
          } else if (e.key === "Tab") {
            const r = tabEnLista(e);
            if (r !== "dentro") volverACaja(r === "despues");
          }
        }}
        className="overflow-auto rounded-lg border border-border bg-background shadow-lg"
      >
        {opciones.map((o) => (
          <button
            key={o.clave}
            type="button"
            role="option"
            aria-selected={o.clave === value}
            onClick={() => elegir(o)}
            className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2"
          >
            <span className="font-mono shrink-0">{o.clave}</span>
            <span className="text-muted">{o.descripcion}</span>
          </button>
        ))}
      </FloatingPanel>
      {descripcion ? (
        <span className="mt-1 block text-xs text-muted">Catálogo SAT: {descripcion}</span>
      ) : null}
    </div>
  );
}

"use client";

// Autocompletar de clave SAT (c_ClaveProdServ) contra el catálogo OFICIAL
// cargado en la base (GET /sat/claves): escribe texto ("cilantro") o un
// prefijo de clave ("50404") y elige del catálogo — nunca claves inventadas.
// Muestra la descripción oficial de la clave elegida como confirmación.
import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api";

type Opcion = { clave: string; descripcion: string };

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
  const skipSearch = useRef(false);   // al elegir una opción no re-buscamos

  // El valor puede cambiar desde fuera (sugerencia IA, abrir edición).
  useEffect(() => {
    setTexto(value);
    skipSearch.current = true;
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

  // Cierra el panel al hacer clic fuera.
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setAbierto(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  function elegir(o: Opcion) {
    skipSearch.current = true;
    setTexto(o.clave);
    setDescripcion(o.descripcion);
    setAbierto(false);
    onChange(o.clave);
  }

  return (
    <div ref={wrapRef} className="relative">
      <Input
        value={texto}
        placeholder={placeholder}
        onChange={(e) => {
          setTexto(e.target.value);
          onChange(e.target.value.trim());
        }}
        onFocus={() => {
          if (opciones.length > 0) setAbierto(true);
        }}
      />
      {abierto && opciones.length > 0 ? (
        <div className="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-border bg-background shadow-lg">
          {opciones.map((o) => (
            <button
              key={o.clave}
              type="button"
              onClick={() => elegir(o)}
              className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2"
            >
              <span className="font-mono shrink-0">{o.clave}</span>
              <span className="text-muted">{o.descripcion}</span>
            </button>
          ))}
        </div>
      ) : null}
      {descripcion ? (
        <span className="mt-1 block text-xs text-muted">Catálogo SAT: {descripcion}</span>
      ) : null}
    </div>
  );
}

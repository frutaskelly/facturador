"use client";

// La descripción oficial de una clave SAT, al lado del puro número (2-oct-2026).
//
// Regla del dueño: toda clave SAT se enseña con su descripción del catálogo
// (50407006 · Rúcula). Es informativa —no cambia nada—, pero ocho dígitos no
// dicen si la clave es la buena, y una clave mal puesta no rebota al timbrar:
// clasifica mal la factura y se descubre meses después.
//
// Sale de GET /sat/describir (el catálogo oficial cargado en la base). Una
// clave que no viene en la respuesta NO existe en el catálogo, y eso también
// se dice.

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

// Las descripciones no cambian: una clave consultada no se vuelve a pedir en la
// sesión. `null` = se consultó y no está en el catálogo.
const cache = new Map<string, string | null>();

export type DescripcionSatEstado = {
  descripcion: string | null;
  /** true = está en el catálogo; false = no está; null = no se sabe (vacía,
   *  mal formada o el backend no contestó). */
  existe: boolean | null;
  cargando: boolean;
};

export function useDescripcionSat(clave: string): DescripcionSatEstado {
  const c = (clave ?? "").trim();
  const valida = /^\d{8}$/.test(c);
  const [res, setRes] = useState<{ clave: string; descripcion: string | null; existe: boolean | null }>({
    clave: "", descripcion: null, existe: null,
  });

  useEffect(() => {
    if (!valida) return;
    if (cache.has(c)) {
      const d = cache.get(c) ?? null;
      setRes({ clave: c, descripcion: d, existe: d !== null });
      return;
    }
    let vivo = true;
    const t = setTimeout(() => {
      apiFetch<{ claves?: Record<string, string> }>(`/api/v1/sat/describir?claves=${encodeURIComponent(c)}`)
        .then((r) => {
          const d = r.claves?.[c] ?? null;
          cache.set(c, d);
          if (vivo) setRes({ clave: c, descripcion: d, existe: d !== null });
        })
        .catch(() => {
          // Sin respuesta no se afirma nada: ni que existe ni que no.
          if (vivo) setRes({ clave: c, descripcion: null, existe: null });
        });
    }, 250);
    return () => { vivo = false; clearTimeout(t); };
  }, [c, valida]);

  if (!valida) return { descripcion: null, existe: null, cargando: false };
  if (res.clave !== c) return { descripcion: null, existe: null, cargando: true };
  return { descripcion: res.descripcion, existe: res.existe, cargando: false };
}

/** El renglón chico que va debajo de un campo de clave SAT. */
export function DescripcionSat({ clave, className = "" }: { clave: string; className?: string }) {
  const c = (clave ?? "").trim();
  const { descripcion, existe, cargando } = useDescripcionSat(c);
  if (!c) return null;
  const base = `mt-1 block text-xs ${className}`;
  if (!/^\d{8}$/.test(c)) {
    return <span className={`${base} text-warning`}>La clave SAT lleva 8 dígitos.</span>;
  }
  if (cargando) return <span className={`${base} text-muted`}>Buscando en el catálogo del SAT…</span>;
  if (existe === false) {
    return <span className={`${base} text-danger`}>{c} no está en el catálogo del SAT: revísala.</span>;
  }
  if (!descripcion) return null;
  return (
    <span className={`${base} text-muted`}>
      <span className="font-mono">{c}</span> · {descripcion}
    </span>
  );
}

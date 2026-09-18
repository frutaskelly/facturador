"use client";

// Elegir la clave de SAE de un producto SIN salir del aviso de la remisión.
//
// NO guarda: reporta lo elegido y la pantalla escribe todas juntas con el botón
// «Guardar». Corregir cinco claves son cinco elecciones y UNA recarga, no cinco
// recargas que te devuelven al principio de la lista cada vez.
//
// Lo primero que muestra es «lo que este producto ya usa», porque casi nunca
// falta la clave: está guardada donde no ampara. El CILANTRO de EHMO tenía
// CILANTROKG amarrado a Tabasco y la remisión era de Pachuca — la clave existía
// y la empresa 02 la tenía viva.
//
// Y deja capturar a mano lo que no esté en el espejo: quien no corre el bot no
// tiene espejo, y aun así tiene que poder trabajar.

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import { apiFetch } from "@/lib/api";

type ClaveSugerida = {
  clave: string;
  descripcion?: string | null;
  activa: boolean;
  producto_id?: string | null;
  producto_nombre?: string | null;
};
type ClaveEnUso = {
  clave: string;
  descripcion?: string | null;
  activa?: boolean | null;
  de_donde: string;
};
type Respuesta = {
  empresa?: string | null;
  espejo: boolean;
  motivo?: string | null;
  claves: ClaveSugerida[];
  ya_usa: ClaveEnUso[];
};

export function ClaveSaeInline({
  remisionId,
  productoId,
  productoNombre,
  value,
  onChange,
}: {
  remisionId: string;
  productoId: string;
  productoNombre: string;
  /** La clave elegida y todavía sin guardar. */
  value: string;
  onChange: (clave: string) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [texto, setTexto] = useState(value);
  const [datos, setDatos] = useState<Respuesta | null>(null);
  const [cargando, setCargando] = useState(false);
  const caja = useRef<HTMLDivElement>(null);

  // El valor que manda es el de la pantalla (se limpia al guardar).
  useEffect(() => setTexto(value), [value]);

  useEffect(() => {
    function fuera(e: MouseEvent) {
      if (caja.current && !caja.current.contains(e.target as Node)) setAbierto(false);
    }
    document.addEventListener("mousedown", fuera);
    return () => document.removeEventListener("mousedown", fuera);
  }, []);

  // Al abrir busca por el nombre del producto; después, por lo que se teclee.
  useEffect(() => {
    if (!abierto) return;
    let vivo = true;
    setCargando(true);
    const q = (texto.trim() || productoNombre).slice(0, 80);
    const t = setTimeout(() => {
      const p = new URLSearchParams({ q, producto_id: productoId });
      apiFetch<Respuesta>(`/api/v1/remisiones/${remisionId}/claves-sae?${p.toString()}`)
        .then((r) => { if (vivo) { setDatos(r); setCargando(false); } })
        .catch(() => { if (vivo) { setDatos(null); setCargando(false); } });
    }, 250);
    return () => { vivo = false; clearTimeout(t); };
  }, [abierto, texto, productoId, productoNombre, remisionId]);

  const limpia = texto.trim().toUpperCase();
  const enLista = (datos?.claves ?? []).some((c) => c.clave.toUpperCase() === limpia);
  const enUso = (datos?.ya_usa ?? []).some((c) => c.clave.toUpperCase() === limpia);

  function elegir(clave: string) {
    setTexto(clave);
    onChange(clave);
    setAbierto(false);
  }

  function Fila({
    clave, detalle, aviso, tono = "normal",
  }: { clave: string; detalle?: string | null; aviso?: string | null; tono?: "normal" | "usa" }) {
    return (
      <button
        type="button"
        onClick={() => elegir(clave)}
        className={`flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2 disabled:opacity-60 ${
          tono === "usa" ? "bg-success/5" : ""
        }`}
      >
        <span className="min-w-0">
          <span className="font-medium">{clave}</span>
          {detalle ? <span className="ml-2 text-xs text-muted">{detalle}</span> : null}
          {aviso ? <span className="block text-xs text-warning">{aviso}</span> : null}
        </span>
        <Check size={14} className="mt-0.5 shrink-0 text-muted" />
      </button>
    );
  }

  return (
    <div ref={caja} className="relative inline-block w-72 align-middle">
      <div className={`flex items-center gap-1 rounded-lg border bg-background px-2 py-1 ${
        value ? "border-accent" : "border-border"
      }`}>
        <input
          className="w-full bg-transparent text-sm outline-none"
          placeholder="Clave SAE: buscar o escribir…"
          value={texto}
          onFocus={() => setAbierto(true)}
          onChange={(e) => { setTexto(e.target.value); onChange(e.target.value.trim().toUpperCase()); setAbierto(true); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && limpia) { e.preventDefault(); elegir(limpia); }
            else if (e.key === "Escape") setAbierto(false);
          }}
        />
        <ChevronDown size={14} className="shrink-0 text-muted" />
      </div>

      {abierto ? (
        <div className="absolute z-30 mt-1 max-h-80 w-96 overflow-auto rounded-lg border border-border bg-surface shadow-lg">
          {(datos?.ya_usa?.length ?? 0) > 0 ? (
            <>
              <div className="px-3 pt-2 text-[11px] uppercase tracking-wide text-muted">
                Este producto ya usa
              </div>
              {datos!.ya_usa.map((c) => (
                <Fila
                  key={`usa-${c.clave}`}
                  tono="usa"
                  clave={c.clave}
                  detalle={`${c.descripcion ?? ""}${c.descripcion ? " · " : ""}${c.de_donde}`}
                  aviso={
                    c.activa === false
                      ? "existe en SAE pero está de BAJA"
                      : c.activa === null
                      ? "sin espejo: no se puede verificar"
                      : null
                  }
                />
              ))}
            </>
          ) : null}

          <div className="px-3 pt-2 text-[11px] uppercase tracking-wide text-muted">
            {datos?.espejo
              ? `Catálogo de SAE · empresa ${datos.empresa}`
              : datos?.motivo ?? "Catálogo de SAE"}
          </div>
          {cargando ? <div className="px-3 py-2 text-sm text-muted">Buscando…</div> : null}
          {!cargando && (datos?.claves?.length ?? 0) === 0 ? (
            <div className="px-3 py-2 text-sm text-muted">
              {datos?.espejo ? "Sin coincidencias." : "No hay espejo con el que buscar."}
            </div>
          ) : null}
          {!cargando &&
            (datos?.claves ?? []).map((c) => (
              <Fila
                key={c.clave}
                clave={c.clave}
                detalle={c.descripcion}
                aviso={
                  !c.activa
                    ? "dada de BAJA en SAE: no factura"
                    : c.producto_id && c.producto_id !== productoId
                    ? `ya es la clave de ${c.producto_nombre}`
                    : null
                }
              />
            ))}

          {limpia && !enLista && !enUso ? (
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => elegir(limpia)}
              className="flex w-full flex-col items-start border-t border-border px-3 py-2 text-left text-sm hover:bg-accent/5"
            >
              <span className="font-medium text-accent">Usar «{limpia}» tal cual</span>
              {datos?.espejo ? (
                <span className="text-xs text-warning">
                  SAE no la conoce: el masivo se seguiría deteniendo por esta partida
                </span>
              ) : null}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

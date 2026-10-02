"use client";

// Selector de periodo de los listados (Facturas, Remisiones): acota lo que se
// descarga. Con miles de documentos, «todo el histórico» por defecto hace
// esperar a quien solo quiere ver lo de este mes; con un tope fijo de filas lo
// viejo se perdía sin avisar. Aquí se elige a propósito y se dice qué se ve.
//
// Va DENTRO de la barra de la tabla, junto al buscador (prop `toolbarStart`):
// es el único filtro de fuera; cliente, sucursal y estado se filtran con los
// embudos de cada columna, que ven todo lo que el periodo descargó.

import type { ReactNode } from "react";
import { etiquetaRango, hoyISO, rangoPreset } from "@/app/(app)/reportes/rango";

export type Periodo = "mes" | "mes_pasado" | "anio" | "todas" | "rango";

const PERIODOS: { key: Periodo; label: string }[] = [
  { key: "mes", label: "Este mes" },
  { key: "mes_pasado", label: "Mes pasado" },
  { key: "anio", label: "Este año" },
  { key: "todas", label: "Todas" },
  { key: "rango", label: "Rango" },
];

export function esPeriodo(v: string | null): v is Periodo {
  return PERIODOS.some((p) => p.key === v);
}

/** Las fechas que manda el periodo; "" = sin límite de ese lado. */
export function rangoDePeriodo(periodo: Periodo, desde: string, hasta: string): { desde: string; hasta: string } {
  switch (periodo) {
    case "mes":
    case "mes_pasado":
    case "anio":
      return rangoPreset(periodo);
    case "todas":
      return { desde: "", hasta: "" };
    case "rango":
      return { desde, hasta };
  }
}

// Mismo alto y borde que el buscador y los botones de la barra de la tabla.
const FECHA = "rounded-lg border border-border bg-background px-2.5 py-1.5 text-sm outline-none focus:border-accent";

export function PeriodoFiltro({
  periodo,
  onPeriodo,
  desde,
  hasta,
  onDesde,
  onHasta,
  ignorado,
  conteo,
}: {
  periodo: Periodo;
  onPeriodo: (p: Periodo) => void;
  /** Solo para «Rango»: las fechas que captura el usuario. */
  desde: string;
  hasta: string;
  onDesde: (v: string) => void;
  onHasta: (v: string) => void;
  /** Por qué el periodo no aplica ahora (p. ej. la búsqueda por folio recorre
   *  todo el historial); se muestra en lugar del rango. */
  ignorado?: string;
  /** Cuántos documentos trajo el periodo («40 facturas»); va tras la leyenda. */
  conteo?: ReactNode;
}) {
  const r = rangoDePeriodo(periodo, desde, hasta);
  const leyenda = ignorado
    ? ignorado
    : periodo === "todas"
      ? "Todo el historial"
      : periodo !== "rango"
        ? etiquetaRango({ desde: r.desde, hasta: r.hasta || hoyISO() })
        : null;
  return (
    <>
      <div
        role="group"
        aria-label="Periodo"
        className={`inline-flex rounded-lg border border-border bg-background p-0.5 ${ignorado ? "opacity-50" : ""}`}
      >
        {PERIODOS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => {
              // «Rango» arranca de lo que se estaba viendo, no de dos
              // fechas vacías: casi siempre es para ajustar ese periodo.
              if (p.key === "rango" && !desde && !hasta && r.desde) {
                onDesde(r.desde);
                onHasta(r.hasta);
              }
              onPeriodo(p.key);
            }}
            aria-pressed={periodo === p.key}
            className={`whitespace-nowrap rounded-md px-2.5 py-1 text-sm transition ${
              periodo === p.key
                ? "bg-surface-2 font-medium text-foreground"
                : "text-muted hover:text-foreground"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
      {periodo === "rango" && (
        <span className="inline-flex items-center gap-1.5">
          <input type="date" aria-label="Desde" value={desde} onChange={(e) => onDesde(e.target.value)} className={FECHA} />
          <span className="text-sm text-muted">a</span>
          <input type="date" aria-label="Hasta" value={hasta} onChange={(e) => onHasta(e.target.value)} className={FECHA} />
        </span>
      )}
      {(leyenda || conteo) && (
        <span className="whitespace-nowrap text-sm text-muted">
          {leyenda}{leyenda && conteo ? " · " : null}{conteo}
        </span>
      )}
    </>
  );
}

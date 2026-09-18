"use client";

// Gráficas de barras en SVG, sin librería. Son dos formas muy acotadas —una
// serie de tiempo y una barra segmentada— y traer un paquete de gráficas
// entero para eso pesaría más que el resto del reporte junto.
//
// El SVG lleva `viewBox` y ancho 100%: se adapta al contenedor sin JS, que es
// lo que hace que sirva igual en el teléfono. Los colores salen de las mismas
// variables del tema que el resto del app (`currentColor` sobre clases de
// Tailwind), así que el modo oscuro no necesita nada especial.

import { fmtMoney } from "@/lib/format";

export type Punto = { etiqueta: string; valor: number; detalle?: string };

/** Serie de tiempo en barras. `destacar` marca la última (el día u hoy). */
export function BarrasTiempo({
  puntos,
  alto = 120,
  titulo,
}: {
  puntos: Punto[];
  alto?: number;
  titulo: string;
}) {
  const max = Math.max(1, ...puntos.map((p) => p.valor));
  const n = Math.max(1, puntos.length);
  // Ancho virtual: 10 unidades por barra. El viewBox lo escala al contenedor,
  // así que el número solo fija la PROPORCIÓN entre barra y separación.
  const ancho = n * 10;

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${ancho} ${alto}`}
        preserveAspectRatio="none"
        className="h-28 w-full sm:h-32"
        role="img"
        aria-label={titulo}
      >
        {puntos.map((p, i) => {
          const h = Math.max(p.valor > 0 ? 1.5 : 0, (p.valor / max) * (alto - 4));
          const ultimo = i === puntos.length - 1;
          return (
            <rect
              key={p.etiqueta}
              x={i * 10 + 1.2}
              y={alto - h}
              width={7.6}
              height={h}
              rx={1}
              className={ultimo ? "fill-accent" : "fill-accent/45"}
            >
              <title>{`${p.etiqueta}: ${fmtMoney(p.valor)}${p.detalle ? ` · ${p.detalle}` : ""}`}</title>
            </rect>
          );
        })}
      </svg>
      {/* Solo los extremos y el medio: con 30 barras, una etiqueta por barra es
          ilegible en cualquier pantalla. El resto vive en el tooltip. */}
      <figcaption className="mt-1 flex justify-between text-[11px] text-muted">
        <span>{puntos[0]?.etiqueta}</span>
        {puntos.length > 2 && <span className="hidden sm:inline">{puntos[Math.floor(n / 2)]?.etiqueta}</span>}
        <span>{puntos[puntos.length - 1]?.etiqueta}</span>
      </figcaption>
    </figure>
  );
}

export type Tramo = { etiqueta: string; valor: number; clase: string };

/** Barra segmentada: la antigüedad de la cartera de un vistazo. */
export function BarraSegmentada({ tramos, titulo }: { tramos: Tramo[]; titulo: string }) {
  const total = tramos.reduce((s, t) => s + t.valor, 0);
  if (total <= 0) return null;
  return (
    <div role="img" aria-label={titulo}>
      <div className="flex h-3 w-full overflow-hidden rounded-full">
        {tramos.map((t) =>
          t.valor <= 0 ? null : (
            <div
              key={t.etiqueta}
              className={t.clase}
              style={{ width: `${(t.valor / total) * 100}%` }}
              title={`${t.etiqueta}: ${fmtMoney(t.valor)}`}
            />
          ),
        )}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-5">
        {tramos.map((t) => (
          <div key={t.etiqueta}>
            <div className="flex items-center gap-1.5 text-[11px] text-muted">
              <span className={`h-2 w-2 shrink-0 rounded-sm ${t.clase}`} aria-hidden />
              {t.etiqueta}
            </div>
            <div className="mt-0.5 text-sm font-medium tabular-nums">{fmtMoney(t.valor)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

"use client";

// Gráficas del tablero, sin librería: una serie de tiempo y una barra
// segmentada. Traer un paquete de gráficas entero para dos formas tan acotadas
// pesaría más que el resto del reporte junto.
//
// La serie se dibuja con cajas (no SVG) porque el SVG que tenía `viewBox` con
// `preserveAspectRatio="none"` deformaba todo lo que le metieras: con un eje de
// pesos encima, los números habrían salido estirados. Con cajas el alto de cada
// barra es un porcentaje y el navegador hace el reparto responsivo solo.

import { useState } from "react";

import { fmtMoney, fmtMoneyCorto } from "@/lib/format";

export type Punto = {
  /** Clave estable de la barra (la fecha de inicio de su cubeta). */
  clave: string;
  /** Lo que va bajo el eje X: corto ("8 sep", "sep 26"). */
  etiqueta: string;
  /** Lo que va en la lectura al pasar el cursor: puede ser largo ("7 – 13 sep"). */
  detalle?: string;
  /** Segunda línea de la lectura ("12 facturas"). */
  nota?: string;
  valor: number;
};

/** Escala del eje Y con cortes redondos (1, 2, 2.5, 5 × 10ⁿ).
 *
 * Un eje que terminara justo en el máximo daría topes como $1,347,912: se lee
 * peor que $1.5 M y además deja la barra más alta pegada al techo. */
function escala(max: number, divisiones = 4): { tope: number; cortes: number[] } {
  if (!(max > 0)) return { tope: 1, cortes: [0] };
  const bruto = max / divisiones;
  const magnitud = Math.pow(10, Math.floor(Math.log10(bruto)));
  const paso = [1, 2, 2.5, 5, 10].map((m) => m * magnitud).find((v) => v >= bruto) ?? magnitud * 10;
  const tope = Math.ceil(max / paso) * paso;
  const cortes: number[] = [];
  for (let v = 0; v <= tope + paso / 2; v += paso) cortes.push(Number(v.toFixed(6)));
  return { tope, cortes };
}

/** Hasta 5 marcas en el eje X, repartidas parejo. Una etiqueta por barra es
 *  ilegible en cuanto pasan de diez; el resto vive en la lectura de arriba. */
function marcas(n: number, cuantas = 5): number[] {
  if (n <= cuantas) return Array.from({ length: n }, (_, i) => i);
  return Array.from({ length: cuantas }, (_, k) => Math.round((k * (n - 1)) / (cuantas - 1)));
}

/**
 * Serie de tiempo en barras, con eje de pesos y lectura por barra.
 *
 * La barra completa —no solo su alto pintado— es el blanco del cursor: con 90
 * barras, apuntarle a un rectángulo de tres pixeles de alto es imposible. Y
 * como es un `<button>`, el teclado la recorre igual que el ratón, y en el
 * teléfono (donde no hay "pasar el cursor") el toque la deja fija.
 */
export function SerieTiempo({
  puntos,
  titulo,
  alto = 190,
  destacar,
  vacio = "Sin facturación en el rango.",
}: {
  puntos: Punto[];
  titulo: string;
  alto?: number;
  /** Barras que van en tono lleno (el día de hoy, el mes en curso…). */
  destacar?: (p: Punto, i: number) => boolean;
  vacio?: string;
}) {
  // `fijo` es el toque/clic; `sobre` el cursor. El cursor manda mientras esté
  // encima, y al salirse vuelve a verse lo que el usuario dejó fijo.
  const [fijo, setFijo] = useState<number | null>(null);
  const [sobre, setSobre] = useState<number | null>(null);

  const n = puntos.length;
  const max = Math.max(0, ...puntos.map((p) => p.valor));
  const { tope, cortes } = escala(max);
  const visible = sobre ?? fijo;
  const sel = visible !== null ? puntos[visible] : undefined;
  const ejeX = marcas(n);
  const separacion = n > 60 ? 1 : n > 24 ? 2 : 4;

  if (n === 0) return <p className="py-10 text-center text-sm text-muted">{vacio}</p>;

  return (
    <figure className="m-0">
      {/* Lectura de la barra activa. Ocupa su renglón siempre, aunque no haya
          nada seleccionado: si apareciera y desapareciera, la gráfica entera
          brincaría cada vez que el cursor entra y sale. */}
      <div className="mb-3 flex min-h-[1.25rem] items-baseline justify-between gap-3 text-xs">
        {sel ? (
          <>
            <span className="truncate text-muted">{sel.detalle ?? sel.etiqueta}</span>
            <span className="shrink-0 tabular-nums">
              <span className="font-semibold">{fmtMoney(sel.valor)}</span>
              {sel.nota && <span className="ml-1.5 text-muted">· {sel.nota}</span>}
            </span>
          </>
        ) : (
          <span className="text-muted">Pasa el cursor o toca una barra para ver su total</span>
        )}
      </div>

      {/* El `pt-2` es aire para la marca de arriba del eje Y: va centrada en
          su línea y sobresale media altura hacia arriba, donde chocaba con la
          lectura ("Pasa el cursor…" encimado sobre el "$2 M"). */}
      <div className="flex gap-2 pt-2">
        {/* Eje Y, en pesos abreviados. */}
        <div className="relative w-12 shrink-0 sm:w-14" style={{ height: alto }} aria-hidden>
          {cortes.map((v) => (
            <span
              key={v}
              className="absolute right-0 -translate-y-1/2 text-[10px] tabular-nums text-muted"
              style={{ bottom: `${(v / tope) * 100}%` }}
            >
              {fmtMoneyCorto(v)}
            </span>
          ))}
        </div>

        <div className="min-w-0 flex-1">
          <div className="relative" style={{ height: alto }} role="img" aria-label={titulo}>
            {cortes.map((v) => (
              <div
                key={v}
                className={`absolute inset-x-0 border-t ${v === 0 ? "border-border" : "border-border/50"}`}
                style={{ bottom: `${(v / tope) * 100}%` }}
                aria-hidden
              />
            ))}
            <div className="absolute inset-0 flex items-end" style={{ gap: separacion }}>
              {puntos.map((p, i) => {
                const activa = visible === i;
                const lleno = activa || destacar?.(p, i);
                return (
                  <button
                    key={p.clave}
                    type="button"
                    // Toda la columna es el blanco: apuntarle al alto pintado de
                    // una barra en cero sería imposible.
                    className="flex h-full min-w-0 flex-1 items-end rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    onMouseEnter={() => setSobre(i)}
                    onMouseLeave={() => setSobre((s) => (s === i ? null : s))}
                    onFocus={() => setSobre(i)}
                    onBlur={() => setSobre((s) => (s === i ? null : s))}
                    onClick={() => setFijo((f) => (f === i ? null : i))}
                    aria-pressed={fijo === i}
                    title={`${p.detalle ?? p.etiqueta}: ${fmtMoney(p.valor)}${p.nota ? ` · ${p.nota}` : ""}`}
                  >
                    <span
                      className={`w-full rounded-t-sm transition-colors ${
                        lleno ? "bg-accent" : "bg-accent/40"
                      }`}
                      // Mínimo visible en los periodos con venta pequeña: una
                      // barra de medio pixel se lee como un cero que no es.
                      style={{ height: `${p.valor > 0 ? Math.max(2, (p.valor / tope) * 100) : 0}%` }}
                    />
                  </button>
                );
              })}
            </div>
          </div>

          <figcaption className="relative mt-1 h-4 text-[10px] text-muted">
            {ejeX.map((i) => (
              <span
                key={puntos[i].clave}
                className="absolute whitespace-nowrap"
                style={
                  i === 0
                    ? { left: 0 }
                    : i === n - 1
                      ? { right: 0 }
                      : { left: `${((i + 0.5) / n) * 100}%`, transform: "translateX(-50%)" }
                }
              >
                {puntos[i].etiqueta}
              </span>
            ))}
          </figcaption>
        </div>
      </div>
    </figure>
  );
}

export type Tramo = { etiqueta: string; valor: number; clase: string; key?: string; detalle?: string };

/** Barra segmentada: la antigüedad de la cartera de un vistazo. Con
 *  `onToggle`, cada tramo de la leyenda es una caja que se marca y desmarca
 *  (una o varias) para filtrar lo de abajo. */
export function BarraSegmentada({ tramos, titulo, seleccion = [], onToggle }: {
  tramos: Tramo[]; titulo: string;
  seleccion?: string[]; onToggle?: (key: string) => void;
}) {
  const total = tramos.reduce((s, t) => s + t.valor, 0);
  if (total <= 0) return null;
  return (
    <div role="img" aria-label={titulo}>
      <div className="flex h-3 w-full overflow-hidden rounded-full">
        {tramos.map((t) =>
          t.valor <= 0 ? null : (
            <div
              key={t.etiqueta}
              className={`${t.clase} transition-opacity ${
                seleccion.length > 0 && !seleccion.includes(t.key ?? t.etiqueta) ? "opacity-25" : ""}`}
              style={{ width: `${(t.valor / total) * 100}%` }}
              title={`${t.etiqueta}: ${fmtMoney(t.valor)}`}
            />
          ),
        )}
      </div>
      {onToggle ? (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {tramos.map((t) => {
            const k = t.key ?? t.etiqueta;
            const activo = seleccion.includes(k);
            return (
              <button key={k} type="button" aria-pressed={activo} onClick={() => onToggle(k)}
                      className={`rounded-xl border px-3 py-2.5 text-left transition ${activo
                        ? "border-accent bg-accent/5 ring-1 ring-accent"
                        : "border-border bg-background hover:bg-surface-2"}`}>
                <div className="flex items-center gap-1.5 text-[11px] text-muted">
                  <span className={`h-2 w-2 shrink-0 rounded-sm ${t.clase}`} aria-hidden />
                  {t.etiqueta}
                  {t.detalle && <span className="ml-auto">{t.detalle}</span>}
                </div>
                <div className="mt-0.5 text-sm font-medium tabular-nums">{fmtMoney(t.valor)}</div>
              </button>
            );
          })}
        </div>
      ) : (
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
      )}
    </div>
  );
}

"use client";

// Una tabla de montos repartidos por una dimensión (proyecto, cliente, plaza),
// con el selector de dimensión y el total arriba. La usan la cartera («cuánto
// nos deben, por quién») y el sumario de venta («cuánto facturamos, a quién»):
// son la misma lectura con otro monto, y así se ven y se tocan igual.
import Link from "next/link";
import type { ReactNode } from "react";

import { fmtMoney } from "@/lib/format";

export type Agrupar = "proyecto" | "cliente" | "sucursal";

export const ETIQUETA_AGRUPAR: Record<Agrupar, string> = {
  proyecto: "Proyecto",
  cliente: "Cliente",
  sucursal: "Sucursal",
};

export type FilaSumario = {
  etiqueta: string;
  monto: string | number;
  facturas: number;
  href: string | null;
  /** Monto en rojo de la segunda columna (el vencido de la cartera). */
  alerta?: string | number;
};

export function SumarioAgrupado({
  opciones,
  agrupar,
  onAgrupar,
  total,
  filas,
  columnaMonto,
  columnaAlerta,
  etiquetaAlerta,
  pie,
  vacio,
  cargando = false,
}: {
  /** En el orden en que se muestran; la primera suele ser la de omisión. */
  opciones: Agrupar[];
  agrupar: Agrupar;
  onAgrupar: (a: Agrupar) => void;
  /** Lo que va a la derecha del selector ("Total: $…"). */
  total: ReactNode;
  filas: FilaSumario[];
  columnaMonto: string;
  /** Si viene, se agrega una segunda columna con `fila.alerta`. */
  columnaAlerta?: string;
  /** Cómo se nombra la alerta en el renglón del teléfono ("vencido"). */
  etiquetaAlerta?: string;
  pie?: ReactNode;
  vacio: string;
  cargando?: boolean;
}) {
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        {/* El mismo total visto por la dimensión que se quiera: el total no
            cambia entre pestañas, solo el reparto. */}
        <div className="inline-flex rounded-lg border border-border p-0.5">
          {opciones.map((a) => (
            <button
              key={a}
              type="button"
              onClick={() => onAgrupar(a)}
              aria-pressed={agrupar === a}
              className={`rounded-md px-3 py-1 text-sm transition ${
                agrupar === a ? "bg-surface-2 font-medium text-foreground" : "text-muted hover:text-foreground"
              }`}
            >
              {ETIQUETA_AGRUPAR[a]}
            </button>
          ))}
        </div>
        <div className="text-sm">{total}</div>
      </div>

      {filas.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">{vacio}</p>
      ) : (
        <div className={cargando ? "opacity-50 transition-opacity" : "transition-opacity"}>
          {/* Teléfono: renglones apilados. Escritorio: tabla. */}
          <div className="sm:hidden">
            {filas.map((f) => {
              const fila = (
                <div className="flex items-baseline justify-between gap-2 border-b border-border/60 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm">{f.etiqueta}</div>
                    <div className="text-xs text-muted">
                      {f.facturas} fact.
                      {columnaAlerta && Number(f.alerta ?? 0) > 0 && (
                        <> · {etiquetaAlerta ?? columnaAlerta.toLowerCase()}{" "}
                          <span className="font-medium text-danger">{fmtMoney(f.alerta ?? 0)}</span></>
                      )}
                    </div>
                  </div>
                  <div className="shrink-0 text-sm font-medium tabular-nums">{fmtMoney(f.monto)}</div>
                </div>
              );
              return f.href
                ? <Link key={f.etiqueta} href={f.href} className="block">{fila}</Link>
                : <div key={f.etiqueta}>{fila}</div>;
            })}
          </div>
          <table className="hidden w-full text-sm sm:table">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="py-1.5">{ETIQUETA_AGRUPAR[agrupar]}</th>
                <th className="py-1.5 text-right">{columnaMonto}</th>
                {columnaAlerta && <th className="py-1.5 text-right">{columnaAlerta}</th>}
              </tr>
            </thead>
            <tbody>
              {filas.map((f) => (
                <tr key={f.etiqueta} className="border-b border-border/60">
                  <td className="py-1.5 pr-2">
                    {f.href ? <Link href={f.href} className="hover:underline">{f.etiqueta}</Link> : f.etiqueta}
                    <span className="text-xs text-muted"> · {f.facturas}</span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{fmtMoney(f.monto)}</td>
                  {columnaAlerta && (
                    <td className="py-1.5 text-right tabular-nums">
                      {Number(f.alerta ?? 0) > 0
                        ? <span className="font-medium text-danger">{fmtMoney(f.alerta ?? 0)}</span>
                        : <span className="text-muted">—</span>}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pie && <p className="mt-3 text-xs text-muted">{pie}</p>}
    </>
  );
}

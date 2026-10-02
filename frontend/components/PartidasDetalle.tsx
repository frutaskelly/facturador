"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

import { fmtMoney, fmtNumber } from "@/lib/format";

/** Una partida tal como se enseña en el detalle de una remisión o factura. */
export type PartidaVista = {
  key: string | number;
  cantidad: string | number;
  /** Presentación o clave de unidad; se abrevia con {@link unidadCorta}. */
  unidad?: string | null;
  descripcion: ReactNode;
  /** Segunda línea, en chico, bajo la descripción (p. ej. «Como venía…»). */
  nota?: ReactNode;
  precio: string | number;
  ieps: string | number;
  iva: string | number;
  importe: string | number;
};

const UNIDADES: Record<string, string> = {
  KILO: "KG", KILOS: "KG", KILOGRAMO: "KG", KILOGRAMOS: "KG", KG: "KG", KGM: "KG",
  PIEZA: "PZ", PIEZAS: "PZ", PZA: "PZ", PZ: "PZ", H87: "PZ",
  LITRO: "L", LITROS: "L", LTR: "L",
};

/** «KILO» → «KG», «PIEZA» → «PZ»; lo que no conoce lo deja tal cual. */
export function unidadCorta(u?: string | null): string {
  const k = (u ?? "").trim().toUpperCase();
  return UNIDADES[k] ?? k;
}

const noCero = (v: string | number | null | undefined) => Math.abs(Number(v ?? 0)) > 0.000001;

/** Tabla de partidas compacta para el panel de detalle (remisiones y facturas).
 *
 *  - Cantidad y unidad en una columna («44 KG»), sin ceros de relleno.
 *  - IEPS e IVA, como columna y como renglón del pie, sólo si algo no es cero:
 *    casi todo lo que se vende son comestibles a tasa 0.
 *  - Los totales van en el pie de la MISMA tabla, así caen exactamente bajo
 *    la columna Importe.
 *  - La tabla mide lo que su contenido (la descripción, con su nota, se topa
 *    y envuelve): los montos quedan junto a lo que describen, no a media
 *    pantalla de distancia. */
export function PartidasDetalle({
  partidas,
  subtotal,
  ieps,
  iva,
  total,
  vacio = "Sin partidas",
}: {
  partidas: PartidaVista[];
  subtotal: string | number;
  ieps: string | number;
  iva: string | number;
  total: string | number;
  vacio?: string;
}) {
  const conIeps = noCero(ieps) || partidas.some((p) => noCero(p.ieps));
  const conIva = noCero(iva) || partidas.some((p) => noCero(p.iva));
  const columnas = 4 + (conIeps ? 1 : 0) + (conIva ? 1 : 0);
  const th = "px-3 py-2 font-medium";
  const num = "w-px whitespace-nowrap px-3 py-1.5 text-right";
  const pie = (etiqueta: string, valor: string | number, fuerte = false) => (
    <tr className={fuerte ? "text-base font-semibold" : ""}>
      <td colSpan={columnas - 1} className={`px-3 text-right ${fuerte ? "pb-2 pt-1" : "py-0.5 text-muted"}`}>
        {etiqueta}
      </td>
      <td className={`${num} ${fuerte ? "pb-2 pt-1" : "py-0.5"}`}>{fmtMoney(valor)}</td>
    </tr>
  );
  return (
    <div className="w-fit max-w-full overflow-x-auto rounded-lg border border-border">
      <table className="min-w-[28rem] text-sm tabular-nums">
        <thead className="bg-surface-2 text-xs uppercase tracking-wide text-muted">
          <tr>
            <th className={`${th} text-right`}>Cant.</th>
            <th className={`${th} text-left`}>Descripción</th>
            <th className={`${th} text-right`}>P/U</th>
            {conIeps && <th className={`${th} text-right`}>IEPS</th>}
            {conIva && <th className={`${th} text-right`}>IVA</th>}
            <th className={`${th} text-right`}>Importe</th>
          </tr>
        </thead>
        <tbody>
          {partidas.length === 0 ? (
            <tr className="border-t border-border">
              <td colSpan={columnas} className="px-3 py-4 text-center text-muted">{vacio}</td>
            </tr>
          ) : (
            partidas.map((p) => (
              <tr key={p.key} className="border-t border-border align-top">
                <td className={num}>
                  {fmtNumber(p.cantidad)} <span className="text-muted">{unidadCorta(p.unidad)}</span>
                </td>
                <td className="px-3 py-1.5">
                  <div className="max-w-xl">
                    {p.descripcion}
                    {p.nota ? <span className="block text-xs text-muted">{p.nota}</span> : null}
                  </div>
                </td>
                <td className={num}>{fmtMoney(p.precio)}</td>
                {conIeps && <td className={num}>{fmtMoney(p.ieps)}</td>}
                {conIva && <td className={num}>{fmtMoney(p.iva)}</td>}
                <td className={num}>{fmtMoney(p.importe)}</td>
              </tr>
            ))
          )}
        </tbody>
        <tfoot className="border-t border-border">
          <tr><td colSpan={columnas} className="h-1" /></tr>
          {pie("Subtotal", subtotal)}
          {conIeps && pie("IEPS", ieps)}
          {conIva && pie("IVA", iva)}
          {pie("Total", total, true)}
        </tfoot>
      </table>
    </div>
  );
}

/** UUID abreviado (primeros 8 … últimos 7) con botón para copiarlo completo. */
export function UuidCopiable({ uuid }: { uuid: string }) {
  const [copiado, setCopiado] = useState(false);
  const corto = uuid.length > 18 ? `${uuid.slice(0, 8)}…${uuid.slice(-7)}` : uuid;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-mono text-xs" title={uuid}>{corto}</span>
      <button
        type="button"
        onClick={async (e) => {
          e.stopPropagation();
          try {
            await navigator.clipboard.writeText(uuid);
            setCopiado(true);
            setTimeout(() => setCopiado(false), 1500);
          } catch {
            /* sin permiso de portapapeles: el UUID completo sigue en el tooltip */
          }
        }}
        aria-label="Copiar UUID"
        title={copiado ? "Copiado" : "Copiar UUID completo"}
        className="rounded p-0.5 text-muted hover:bg-surface-2 hover:text-foreground"
      >
        {copiado ? <Check size={13} className="text-success" /> : <Copy size={13} />}
      </button>
    </span>
  );
}

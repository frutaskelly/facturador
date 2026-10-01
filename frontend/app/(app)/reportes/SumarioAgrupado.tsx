"use client";

// Una tabla de montos repartidos por una dimensión (proyecto, cliente, plaza),
// con el selector de dimensión y el total arriba. La usan la cartera («cuánto
// nos deben, por quién») y el sumario de venta («cuánto facturamos, a quién»):
// son la misma lectura con otro monto, y así se ven y se tocan igual.
import { useRouter } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";

import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
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
  const router = useRouter();
  // Lo que queda tras el buscador y los embudos; null = aún sin filtrar.
  const [visibles, setVisibles] = useState<FilaSumario[] | null>(null);

  const cols: Column<FilaSumario>[] = useMemo(() => [
    // Clave fija: el encabezado cambia con la dimensión, el ancho no.
    { key: "etiqueta", header: ETIQUETA_AGRUPAR[agrupar], truncate: true, sortable: true,
      sortValue: (f) => f.etiqueta, exportValue: (f) => f.etiqueta,
      cell: (f) => (
        <span title={f.etiqueta} className={f.href ? "hover:underline" : undefined}>{f.etiqueta}</span>
      ) },
    { key: "facturas", header: "Facturas", className: "text-right tabular-nums", sortable: true,
      sortValue: (f) => f.facturas, exportValue: (f) => f.facturas,
      cell: (f) => <span className="text-muted">{f.facturas}</span> },
    { key: "monto", header: columnaMonto, className: "whitespace-nowrap text-right tabular-nums",
      sortable: true, sortValue: (f) => Number(f.monto), exportValue: (f) => Number(f.monto),
      cell: (f) => fmtMoney(f.monto) },
    ...(columnaAlerta ? [{
      key: "alerta", header: columnaAlerta, className: "whitespace-nowrap text-right tabular-nums",
      sortable: true, sortValue: (f: FilaSumario) => Number(f.alerta ?? 0),
      exportValue: (f: FilaSumario) => Number(f.alerta ?? 0),
      cell: (f: FilaSumario) => Number(f.alerta ?? 0) > 0
        ? <span className="font-medium text-danger">{fmtMoney(f.alerta ?? 0)}</span>
        : <span className="text-muted">—</span>,
    }] : []),
  ], [agrupar, columnaMonto, columnaAlerta]);

  const sumaVisible = useMemo(() => {
    const base = visibles ?? filas;
    return {
      monto: base.reduce((t, f) => t + Number(f.monto), 0),
      alerta: base.reduce((t, f) => t + Number(f.alerta ?? 0), 0),
    };
  }, [visibles, filas]);

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
          {visibles && visibles.length !== filas.length && (
            <p className="mb-2 text-right text-xs text-muted">
              Lo filtrado ({visibles.length} de {filas.length}):{" "}
              <span className="font-semibold tabular-nums text-foreground">{fmtMoney(sumaVisible.monto)}</span>
              {columnaAlerta && (
                <> · {etiquetaAlerta ?? columnaAlerta.toLowerCase()}{" "}
                  <span className="font-semibold tabular-nums text-danger">{fmtMoney(sumaVisible.alerta)}</span></>
              )}
            </p>
          )}
          <DataTableSmart
            rows={filas}
            rowKey={(f) => f.etiqueta}
            columns={cols}
            empty={vacio}
            storageKey={`reportes-sumario-${columnaMonto.toLowerCase()}`}
            searchPlaceholder={`Buscar ${ETIQUETA_AGRUPAR[agrupar].toLowerCase()}…`}
            exportFilename={`${columnaMonto.toLowerCase()}-por-${agrupar}`}
            defaultPageSize={50}
            onRowClick={(f) => { if (f.href) router.push(f.href); }}
            onFilteredRowsChange={setVisibles}
          />
        </div>
      )}

      {pie && <p className="mt-3 text-xs text-muted">{pie}</p>}
    </>
  );
}

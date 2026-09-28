"use client";

// Notas de crédito (CFDI de egreso) del periodo: las timbra SAE y las aplica en
// su CxC; el espejo las trae con las facturas a las que se aplicaron. Cuelgan de
// los mismos filtros que ventas —rango y cliente— por su fecha de emisión. No
// mueven nada aquí: la cartera ya llega con ellas descontadas desde SAE. La
// tabla es DataTableSmart: el embudo de «Facturas relacionadas» lista cada
// factura por separado, y el conteo y el total siguen a lo que se ve.
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Spinner } from "@/components/ui/Spinner";
import { folioRelacionado } from "@/lib/cobranza";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useResource } from "@/lib/hooks";

type Nota = {
  id: string; serie: string; folio: number; fecha: string;
  cliente_id: string; cliente: string; total: string; moneda: string;
  estado: "VIGENTE" | "CANCELADA"; uuid: string | null;
  facturas: { factura_id: string | null; serie: string | null; folio: number | null;
              factura_ref: string | null; importe: string }[];
};
type Notas = {
  desde: string; hasta: string; items: Nota[];
  total: string; notas: number; total_cancelado: string; canceladas: number;
};

// Sin repetir: el espejo puede traer dos renglones de la misma factura.
const foliosRelacionados = (n: Nota) => [...new Set(n.facturas.map(folioRelacionado))];
const relacionadas = (n: Nota) => foliosRelacionados(n).join(", ");

export function NotasCredito({
  filtros, rango, clienteNombre,
}: {
  /** El query string de los filtros de la página (desde, hasta, cliente_id). */
  filtros: string;
  rango: string;
  clienteNombre?: string;
}) {
  const res = useResource<Notas>(`/api/v1/reportes/notas-credito?${filtros}`);
  const d = res.data;
  // Lo que queda tras el buscador y los embudos; null = aún sin filtrar.
  const [visibles, setVisibles] = useState<Nota[] | null>(null);

  const cols: Column<Nota>[] = useMemo(() => [
    { header: "Fecha", className: "whitespace-nowrap", sortable: true,
      sortValue: (n) => n.fecha, exportValue: (n) => fmtDate(n.fecha),
      cell: (n) => fmtDate(n.fecha) },
    { header: "Folio", className: "whitespace-nowrap", sortable: true,
      sortValue: (n) => `${n.serie}${String(n.folio).padStart(8, "0")}`,
      exportValue: (n) => `${n.serie}${n.folio}`,
      cell: (n) => <span className="font-medium">{n.serie}{n.folio}</span> },
    { header: "Cliente", truncate: true, sortable: true, sortValue: (n) => n.cliente,
      exportValue: (n) => n.cliente, cell: (n) => <span title={n.cliente}>{n.cliente}</span> },
    { header: "UUID", exportValue: (n) => n.uuid ?? "",
      cell: (n) => n.uuid
        ? <span className="font-mono text-xs text-muted" title={n.uuid}>{n.uuid.slice(0, 8)}…</span>
        : <span className="text-muted">—</span> },
    { header: "Facturas relacionadas", truncate: true, exportValue: relacionadas,
      filterValues: foliosRelacionados,
      cell: (n) => <span title={relacionadas(n)}>{relacionadas(n) || "—"}</span> },
    { header: "Importe", className: "whitespace-nowrap text-right tabular-nums", sortable: true,
      sortValue: (n) => Number(n.total), exportValue: (n) => n.total,
      cell: (n) => (
        <span className={n.estado === "CANCELADA" ? "text-muted line-through" : "font-medium"}>
          {fmtMoney(n.total)}
        </span>
      ) },
    { header: "Estado", sortable: true, sortValue: (n) => n.estado,
      exportValue: (n) => (n.estado === "VIGENTE" ? "Vigente" : "Cancelada"),
      cell: (n) => n.estado === "VIGENTE"
        ? <Badge tone="success">Vigente</Badge>
        : <Badge tone="danger">Cancelada</Badge> },
  ], []);

  // El conteo y el total siguen a lo VISIBLE (buscador y embudos), y como el
  // del servidor, solo cuentan las vigentes: lo cancelado va aparte.
  const suma = useMemo(() => {
    const base = visibles ?? d?.items ?? [];
    const vig = base.filter((n) => n.estado === "VIGENTE");
    const can = base.filter((n) => n.estado === "CANCELADA");
    const monto = (xs: Nota[]) => xs.reduce((t, n) => t + Number(n.total), 0);
    return { vigentes: vig.length, total: monto(vig), canceladas: can.length, totalCancelado: monto(can) };
  }, [visibles, d]);

  if (res.error) {
    return (
      <Card title="Notas de crédito">
        <p className="py-8 text-center text-sm text-muted">No se pudieron cargar las notas de crédito.</p>
      </Card>
    );
  }

  return (
    <Card
      title="Notas de crédito"
      subtitle={`CFDI de egreso emitidos en ${rango}${clienteNombre ? ` · solo ${clienteNombre}` : ""}`}
    >
      {!d ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="text-muted">
              {fmtNumber(suma.vigentes, 0)} {suma.vigentes === 1 ? "nota vigente" : "notas vigentes"}
              {suma.vigentes !== d.notas && ` de ${fmtNumber(d.notas, 0)}`}
            </span>
            <span>
              Total: <span className="font-semibold tabular-nums">{fmtMoney(suma.total)}</span>
            </span>
          </div>
          <div className={res.loading ? "opacity-50 transition-opacity" : "transition-opacity"}>
            <DataTableSmart
              rows={d.items}
              rowKey={(n) => n.id}
              columns={cols}
              empty="Sin notas de crédito en el rango."
              storageKey="reportes-notas-credito"
              searchPlaceholder="Folio, cliente o factura (p. ej. FEHMOHOS12)…"
              exportFilename="notas-de-credito"
              defaultPageSize={50}
              onFilteredRowsChange={setVisibles}
            />
          </div>
          {suma.canceladas > 0 && (
            <p className="mt-3 text-xs text-muted">
              Fuera del total: {fmtMoney(suma.totalCancelado)} en {fmtNumber(suma.canceladas, 0)}{" "}
              {suma.canceladas === 1 ? "nota cancelada" : "notas canceladas"}.
            </p>
          )}
        </>
      )}
    </Card>
  );
}

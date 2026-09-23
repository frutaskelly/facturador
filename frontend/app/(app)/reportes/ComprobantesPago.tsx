"use client";

// Comprobantes de pago (REP, CFDI tipo P) del periodo: cuelga de los mismos
// filtros que ventas —rango y cliente—, pero por FECHA DE PAGO, que es la que
// lleva el complemento y la que cuadra con el banco. Los borradores no salen:
// nunca llegaron al SAT.
import { useMemo } from "react";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Spinner } from "@/components/ui/Spinner";
import { folioRelacionado, type Recibo } from "@/lib/cobranza";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useResource } from "@/lib/hooks";

type Comprobante = Recibo & { cliente: string };
type Pagos = {
  desde: string; hasta: string; items: Comprobante[];
  total: string; comprobantes: number; total_cancelado: string; cancelados: number;
};

const relacionadas = (r: Comprobante) =>
  r.facturas.map(folioRelacionado).join(", ");

export function ComprobantesPago({
  filtros, rango, clienteNombre,
}: {
  /** El query string de los filtros de la página (desde, hasta, cliente_id). */
  filtros: string;
  rango: string;
  clienteNombre?: string;
}) {
  const res = useResource<Pagos>(`/api/v1/reportes/pagos?${filtros}`);
  const d = res.data;

  const cols: Column<Comprobante>[] = useMemo(() => [
    { header: "Fecha", className: "whitespace-nowrap", sortable: true,
      sortValue: (r) => r.fecha_pago, exportValue: (r) => fmtDate(r.fecha_pago),
      cell: (r) => fmtDate(r.fecha_pago) },
    { header: "Folio", className: "whitespace-nowrap", sortable: true, sortValue: (r) => `${r.serie}${String(r.folio).padStart(8, "0")}`,
      exportValue: (r) => `${r.serie}${r.folio}`,
      cell: (r) => (
        <span className="font-medium">
          {r.serie}{r.folio}
          {r.origen === "ESPEJO_SAE" && <span className="ml-1.5"><Badge tone="muted">SAE</Badge></span>}
        </span>
      ) },
    { header: "Cliente", truncate: true, sortable: true, sortValue: (r) => r.cliente,
      exportValue: (r) => r.cliente, cell: (r) => <span title={r.cliente}>{r.cliente}</span> },
    { header: "UUID", exportValue: (r) => r.uuid ?? "",
      cell: (r) => r.uuid
        ? <span className="font-mono text-xs text-muted" title={r.uuid}>{r.uuid.slice(0, 8)}…</span>
        : <span className="text-muted">—</span> },
    { header: "Facturas relacionadas", truncate: true, exportValue: relacionadas,
      cell: (r) => <span title={relacionadas(r)}>{relacionadas(r) || "—"}</span> },
    { header: "Monto pagado", className: "whitespace-nowrap text-right tabular-nums", sortable: true,
      sortValue: (r) => Number(r.monto), exportValue: (r) => r.monto,
      cell: (r) => (
        <span className={r.estado === "CANCELADO" ? "text-muted line-through" : "font-medium"}>
          {fmtMoney(r.monto)}
        </span>
      ) },
    { header: "Estado", sortable: true, sortValue: (r) => r.estado,
      exportValue: (r) => (r.estado === "TIMBRADO" ? "Vigente" : "Cancelado"),
      cell: (r) => r.estado === "TIMBRADO"
        ? <Badge tone="success">Vigente</Badge>
        : <Badge tone="danger">Cancelado</Badge> },
  ], []);

  if (res.error) {
    return (
      <Card title="Comprobantes de pago">
        <p className="py-8 text-center text-sm text-muted">No se pudieron cargar los comprobantes de pago.</p>
      </Card>
    );
  }

  return (
    <Card
      title="Comprobantes de pago"
      subtitle={`Complementos de pago (REP) con fecha de pago en ${rango}${clienteNombre ? ` · solo ${clienteNombre}` : ""}`}
    >
      {!d ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="text-muted">
              {fmtNumber(d.comprobantes, 0)} {d.comprobantes === 1 ? "comprobante vigente" : "comprobantes vigentes"}
            </span>
            <span>
              Total: <span className="font-semibold tabular-nums">{fmtMoney(d.total)}</span>
            </span>
          </div>
          <div className={res.loading ? "opacity-50 transition-opacity" : "transition-opacity"}>
            <DataTable
              rows={d.items}
              rowKey={(r) => r.id}
              columns={cols}
              empty="Sin comprobantes de pago en el rango."
              exportable
              exportFilename="comprobantes-de-pago"
              paginated
              defaultPageSize={50}
            />
          </div>
          {d.cancelados > 0 && (
            <p className="mt-3 text-xs text-muted">
              Fuera del total: {fmtMoney(d.total_cancelado)} en {fmtNumber(d.cancelados, 0)}{" "}
              {d.cancelados === 1 ? "comprobante cancelado" : "comprobantes cancelados"}.
            </p>
          )}
        </>
      )}
    </Card>
  );
}

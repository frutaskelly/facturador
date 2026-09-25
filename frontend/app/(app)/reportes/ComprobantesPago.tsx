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
import { folioRelacionado, type Recibo, type ReciboFactura } from "@/lib/cobranza";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useResource } from "@/lib/hooks";

// El reporte agrega a cada factura abonada su desglose fiscal (null si SAE
// abonó a una factura que aquí no está).
type FacturaAbonada = ReciboFactura & {
  fecha: string | null; subtotal: string | null; descuento: string | null;
  ieps: string | null; iva: string | null; total: string | null;
};
type Comprobante = Omit<Recibo, "facturas"> & { cliente: string; facturas: FacturaAbonada[] };
type Pagos = {
  desde: string; hasta: string; items: Comprobante[];
  total: string; comprobantes: number; total_cancelado: string; cancelados: number;
};

const relacionadas = (r: Comprobante) =>
  r.facturas.map(folioRelacionado).join(", ");

const suma = (fs: FacturaAbonada[], k: "subtotal" | "ieps" | "iva" | "total" | "importe_pagado") =>
  fs.reduce((t, f) => t + Number(f[k] ?? 0), 0);
const dinero = (v: string | number | null) => (v === null ? "—" : fmtMoney(v));

/** El detalle que se despliega bajo el comprobante: cada factura que abona. */
function DetalleFacturas({ r }: { r: Comprobante }) {
  if (r.facturas.length === 0) {
    return <p className="px-4 py-3 text-sm text-muted">El comprobante no trae facturas relacionadas.</p>;
  }
  const num = "px-3 py-1.5 text-right tabular-nums";
  return (
    <div className="overflow-x-auto px-4 py-3">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
            <th className="px-3 py-1.5 text-left font-medium">Factura</th>
            <th className="px-3 py-1.5 text-left font-medium">Fecha</th>
            <th className={`${num} font-medium`}>Subtotal</th>
            <th className={`${num} font-medium`}>IEPS</th>
            <th className={`${num} font-medium`}>IVA</th>
            <th className={`${num} font-medium`}>Total</th>
            <th className={`${num} font-medium`}>Pagado</th>
            <th className={`${num} font-medium`}>Saldo</th>
          </tr>
        </thead>
        <tbody>
          {r.facturas.map((f, i) => (
            <tr key={f.factura_id ?? `${f.factura_ref}-${i}`} className="border-b border-border/60">
              <td className="whitespace-nowrap px-3 py-1.5 font-medium">{folioRelacionado(f)}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-muted">{f.fecha ? fmtDate(f.fecha) : "—"}</td>
              <td className={num}>{dinero(f.subtotal)}</td>
              <td className={num}>{dinero(f.ieps)}</td>
              <td className={num}>{dinero(f.iva)}</td>
              <td className={num}>{dinero(f.total)}</td>
              <td className={`${num} font-medium`}>{dinero(f.importe_pagado)}</td>
              <td className={`${num} text-muted`}>{dinero(f.saldo_insoluto)}</td>
            </tr>
          ))}
        </tbody>
        {r.facturas.length > 1 && (
          <tfoot>
            <tr className="font-semibold">
              <td className="px-3 py-1.5" colSpan={2}>{r.facturas.length} facturas</td>
              <td className={num}>{fmtMoney(suma(r.facturas, "subtotal"))}</td>
              <td className={num}>{fmtMoney(suma(r.facturas, "ieps"))}</td>
              <td className={num}>{fmtMoney(suma(r.facturas, "iva"))}</td>
              <td className={num}>{fmtMoney(suma(r.facturas, "total"))}</td>
              <td className={num}>{fmtMoney(suma(r.facturas, "importe_pagado"))}</td>
              <td />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

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
              renderExpanded={(r) => <DetalleFacturas r={r} />}
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

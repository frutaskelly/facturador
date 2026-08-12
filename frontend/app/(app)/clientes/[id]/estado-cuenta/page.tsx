"use client";

// Estado de cuenta del cliente (Cobranza F1) — sus facturas PPD timbradas con
// saldo pendiente + antigüedad de saldos por fecha de vencimiento (estilo SAE).
// Solo lectura; los abonos (REP) llegan en F2.
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { apiFetch } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";

type Doc = {
  factura_id: string; serie: string; folio: number; uuid: string | null;
  fecha: string; vencimiento: string; dias_vencida: number;
  total: string; saldo_insoluto: string;
};
type EstadoCuenta = {
  cliente_nombre: string; dias_credito: number; limite_credito: string;
  corte: string; saldo_total: string;
  antiguedad: { por_vencer: string; d1_30: string; d31_60: string; d61_90: string; d90_mas: string };
  facturas: Doc[];
};

const BUCKETS: { key: keyof EstadoCuenta["antiguedad"]; label: string }[] = [
  { key: "por_vencer", label: "Por vencer" },
  { key: "d1_30", label: "1-30 días" },
  { key: "d31_60", label: "31-60 días" },
  { key: "d61_90", label: "61-90 días" },
  { key: "d90_mas", label: "90+ días" },
];

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<EstadoCuenta | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    apiFetch<EstadoCuenta>(`/api/v1/cobranza/estado-cuenta/${id}`)
      .then(setData)
      .catch(() => setError(true));
  }, [id]);

  if (error) return <Alert tone="danger">No se pudo cargar el estado de cuenta.</Alert>;
  if (!data) return <div className="flex justify-center py-16"><Spinner /></div>;

  const cols: Column<Doc>[] = [
    { header: "Factura", cell: (d) => <span className="font-medium">{d.serie}{d.folio}</span> },
    { header: "Fecha", cell: (d) => fmtDate(d.fecha) },
    { header: "Vence", cell: (d) => fmtDate(d.vencimiento) },
    { header: "Días vencida", className: "text-right tabular-nums",
      cell: (d) => d.dias_vencida > 0
        ? <span className="text-danger">{d.dias_vencida}</span>
        : <span className="text-muted">Por vencer</span> },
    { header: "Total", className: "text-right tabular-nums", cell: (d) => fmtMoney(d.total) },
    { header: "Saldo", className: "text-right tabular-nums font-medium", cell: (d) => fmtMoney(d.saldo_insoluto) },
  ];

  return (
    <div>
      <PageHeader
        title={`Estado de cuenta — ${data.cliente_nombre}`}
        subtitle={`Crédito: ${fmtMoney(data.limite_credito)} · ${data.dias_credito} días · corte ${fmtDate(data.corte)}`}
        actions={
          <Link href="/clientes" className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-surface-2">
            <ArrowLeft size={16} /> Clientes
          </Link>
        }
      />

      {/* Antigüedad de saldos */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {BUCKETS.map((b) => (
          <Card key={b.key}>
            <div className="text-xs text-muted">{b.label}</div>
            <div className="text-lg font-semibold tabular-nums">{fmtMoney(data.antiguedad[b.key])}</div>
          </Card>
        ))}
      </div>

      <Card>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-medium">Facturas PPD con saldo</div>
          <div className="text-sm">Saldo total: <span className="font-semibold tabular-nums">{fmtMoney(data.saldo_total)}</span></div>
        </div>
        {data.facturas.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted">El cliente no tiene saldos pendientes.</div>
        ) : (
          <DataTable rows={data.facturas} rowKey={(d) => d.factura_id} columns={cols} empty="Sin saldos" />
        )}
      </Card>
    </div>
  );
}

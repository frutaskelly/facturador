"use client";

// Estado de cuenta del cliente (Cobranza F1) — sus facturas PPD timbradas con
// saldo pendiente + antigüedad de saldos por fecha de vencimiento (estilo SAE).
// Filtrable por serie (en EHMO cada plaza factura con la suya) y fecha de
// corte, y descargable como el Excel que SAE le manda al cliente (con la
// semana de entrega derivada de las observaciones, ya sin capturarla a mano).
import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { ArrowLeft, FileSpreadsheet } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";

type Doc = {
  factura_id: string; serie: string; folio: number; uuid: string | null;
  fecha: string; vencimiento: string; dias_vencida: number;
  total: string; saldo_insoluto: string;
  semana: number | null; proyecto: string | null;
};
type EstadoCuenta = {
  cliente_nombre: string; dias_credito: number; limite_credito: string;
  corte: string; saldo_total: string;
  serie: string | null;
  series: { serie: string; facturas: number; saldo: string }[];
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

function query(serie: string, corte: string): string {
  const p = new URLSearchParams();
  if (serie) p.set("serie", serie);
  if (corte) p.set("corte", corte);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const toast = useToast();
  const [data, setData] = useState<EstadoCuenta | null>(null);
  const [error, setError] = useState(false);
  const [serie, setSerie] = useState("");
  const [corte, setCorte] = useState("");
  const [bajando, setBajando] = useState(false);

  useEffect(() => {
    let vivo = true;
    apiFetch<EstadoCuenta>(`/api/v1/cobranza/estado-cuenta/${id}${query(serie, corte)}`)
      .then((d) => { if (vivo) { setData(d); setError(false); } })
      .catch(() => { if (vivo) setError(true); });
    return () => { vivo = false; };
  }, [id, serie, corte]);

  const bajarExcel = async () => {
    if (!data || bajando) return;
    setBajando(true);
    try {
      const nombre = `estado-cuenta${serie ? `-${serie}` : ""}-${data.corte.replaceAll("-", "")}.xlsx`;
      await apiDownload(`/api/v1/cobranza/estado-cuenta/${id}/xlsx${query(serie, corte)}`, nombre);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo generar el Excel.");
    } finally {
      setBajando(false);
    }
  };

  // Antes de los returns tempranos: es un hook, y no depende de `data`.
  const cols = useMemo<Column<Doc>[]>(() => [
    { header: "Sem", className: "text-muted",
      sortValue: (d) => d.semana ?? -1,
      exportValue: (d) => (d.semana != null ? `SEM ${d.semana}` : ""),
      cell: (d) => d.semana != null ? `SEM ${d.semana}` : "—" },
    { header: "Factura",
      sortValue: (d) => `${d.serie}${String(d.folio).padStart(10, "0")}`,
      exportValue: (d) => `${d.serie}${d.folio}`,
      cell: (d) => <span className="font-medium">{d.serie}{d.folio}</span> },
    { header: "Proyecto", className: "text-muted",
      sortValue: (d) => d.proyecto ?? "",
      exportValue: (d) => d.proyecto ?? "",
      cell: (d) => d.proyecto ?? "—" },
    { header: "Fecha", sortValue: (d) => d.fecha, exportValue: (d) => d.fecha,
      cell: (d) => fmtDate(d.fecha) },
    { header: "Vence", sortValue: (d) => d.vencimiento, exportValue: (d) => d.vencimiento,
      cell: (d) => fmtDate(d.vencimiento) },
    { header: "Días vencida", className: "text-right tabular-nums",
      sortValue: (d) => d.dias_vencida, exportValue: (d) => d.dias_vencida,
      cell: (d) => d.dias_vencida > 0
        ? <span className="text-danger">{d.dias_vencida}</span>
        : <span className="text-muted">Por vencer</span> },
    { header: "Total", className: "text-right tabular-nums",
      sortValue: (d) => Number(d.total), exportValue: (d) => Number(d.total),
      cell: (d) => fmtMoney(d.total) },
    { header: "Abonos", className: "text-right tabular-nums",
      sortValue: (d) => Number(d.total) - Number(d.saldo_insoluto),
      exportValue: (d) => Number(d.total) - Number(d.saldo_insoluto),
      cell: (d) => Number(d.total) > Number(d.saldo_insoluto)
        ? fmtMoney(Number(d.total) - Number(d.saldo_insoluto)) : "—" },
    { header: "Saldo", className: "text-right tabular-nums font-medium",
      sortValue: (d) => Number(d.saldo_insoluto), exportValue: (d) => Number(d.saldo_insoluto),
      cell: (d) => fmtMoney(d.saldo_insoluto) },
  ], []);

  if (error) return <Alert tone="danger">No se pudo cargar el estado de cuenta.</Alert>;
  if (!data) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title={`Estado de cuenta — ${data.cliente_nombre}`}
        subtitle={`Crédito: ${fmtMoney(data.limite_credito)} · ${data.dias_credito} días · corte ${fmtDate(data.corte)}`}
        actions={
          <div className="flex items-center gap-2">
            <Button onClick={bajarExcel} disabled={bajando}>
              <FileSpreadsheet size={16} /> {bajando ? "Generando…" : "Excel"}
            </Button>
            <Link href="/clientes" className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-surface-2">
              <ArrowLeft size={16} /> Clientes
            </Link>
          </div>
        }
      />

      {/* Filtros: serie (plaza) y fecha de corte */}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Serie">
          <Select className="min-w-52" value={serie} onChange={(e) => setSerie(e.target.value)} aria-label="Filtrar por serie">
            <option value="">Todas</option>
            {data.series.map((s) => (
              <option key={s.serie} value={s.serie}>
                {s.serie} · {s.facturas} fact. · {fmtMoney(s.saldo)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Corte">
          <Input type="date" value={corte} onChange={(e) => setCorte(e.target.value)} aria-label="Fecha de corte" />
        </Field>
      </div>

      {/* Antigüedad de saldos */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {BUCKETS.map((b) => (
          <Card key={b.key}>
            <div className="text-xs text-muted">{b.label}</div>
            <div className="text-lg font-semibold tabular-nums">{fmtMoney(data.antiguedad[b.key])}</div>
          </Card>
        ))}
      </div>

      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm font-medium">
          Facturas PPD con saldo{serie ? ` · serie ${serie}` : ""}
        </div>
        <div className="text-sm">Saldo total: <span className="font-semibold tabular-nums">{fmtMoney(data.saldo_total)}</span></div>
      </div>
      <DataTableSmart
        rows={data.facturas}
        rowKey={(d) => d.factura_id}
        columns={cols}
        storageKey="estado-cuenta-facturas"
        empty="El cliente no tiene saldos pendientes."
      />
    </div>
  );
}

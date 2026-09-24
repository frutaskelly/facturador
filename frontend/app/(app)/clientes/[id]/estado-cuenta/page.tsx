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
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownloadPost, apiFetch } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";

type Doc = {
  factura_id: string; serie: string; folio: number; uuid: string | null;
  fecha: string; vencimiento: string; dias_vencida: number;
  total: string; saldo_insoluto: string;
  semana: number | null; proyecto: string | null;
  cancelacion_msj: string | null;
};
type EstadoCuenta = {
  cliente_nombre: string; dias_credito: number; limite_credito: string;
  corte: string; saldo_total: string;
  serie: string | null;
  series: { serie: string; facturas: number; saldo: string }[];
  antiguedad: { por_vencer: string; d1_30: string; d31_60: string; d61_90: string; d90_mas: string };
  // Las que ya tienen la cancelación pedida al SAT: fuera del saldo por
  // omisión, pero siempre contadas, para poder ofrecer verlas.
  incluye_en_cancelacion: boolean;
  saldo_en_cancelacion: string;
  facturas_en_cancelacion: number;
  facturas: Doc[];
};

const BUCKETS: { key: keyof EstadoCuenta["antiguedad"]; label: string }[] = [
  { key: "por_vencer", label: "Por vencer" },
  { key: "d1_30", label: "1-30 días" },
  { key: "d31_60", label: "31-60 días" },
  { key: "d61_90", label: "61-90 días" },
  { key: "d90_mas", label: "90+ días" },
];

type Bucket = keyof EstadoCuenta["antiguedad"];

// La misma cubeta que calcula el backend (`_bucket` en cobranza.py).
function bucketDe(diasVencida: number): Bucket {
  if (diasVencida <= 0) return "por_vencer";
  if (diasVencida <= 30) return "d1_30";
  if (diasVencida <= 60) return "d31_60";
  if (diasVencida <= 90) return "d61_90";
  return "d90_mas";
}

function query(serie: string, corte: string, enCancelacion = false): string {
  const p = new URLSearchParams();
  if (serie) p.set("serie", serie);
  if (corte) p.set("corte", corte);
  if (enCancelacion) p.set("incluir_en_cancelacion", "true");
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
  const [verEnCancelacion, setVerEnCancelacion] = useState(false);
  const [bajando, setBajando] = useState(false);
  // Cajas de antigüedad marcadas (vacío = todas): filtran la tabla.
  const [buckets, setBuckets] = useState<Bucket[]>([]);
  // Lo que la tabla deja ver tras TODOS sus filtros: es lo que se descarga.
  const [visibles, setVisibles] = useState<Doc[]>([]);

  useEffect(() => {
    let vivo = true;
    apiFetch<EstadoCuenta>(`/api/v1/cobranza/estado-cuenta/${id}${query(serie, corte, verEnCancelacion)}`)
      .then((d) => { if (vivo) { setData(d); setError(false); } })
      .catch(() => { if (vivo) setError(true); });
    return () => { vivo = false; };
  }, [id, serie, corte, verEnCancelacion]);

  const bajarEstadoCuenta = async () => {
    if (!data || bajando) return;
    setBajando(true);
    try {
      const nombre = `estado-cuenta${serie ? `-${serie}` : ""}-${data.corte.replaceAll("-", "")}.xlsx`;
      await apiDownloadPost(
        `/api/v1/cobranza/estado-cuenta/${id}/xlsx${query(serie, corte, verEnCancelacion)}`,
        { facturas: visibles.map((d) => d.factura_id) },
        nombre,
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo generar el estado de cuenta.");
    } finally {
      setBajando(false);
    }
  };

  const toggleBucket = (b: Bucket) =>
    setBuckets((prev) => (prev.includes(b) ? prev.filter((x) => x !== b) : [...prev, b]));

  // Antes de los returns tempranos: es un hook, y no depende de `data`.
  const cols = useMemo<Column<Doc>[]>(() => [
    { header: "Sem", className: "text-muted",
      sortValue: (d) => d.semana ?? -1,
      exportValue: (d) => (d.semana != null ? `SEM ${d.semana}` : ""),
      cell: (d) => d.semana != null ? `SEM ${d.semana}` : "—" },
    { header: "Factura",
      sortValue: (d) => `${d.serie}${String(d.folio).padStart(10, "0")}`,
      exportValue: (d) => `${d.serie}${d.folio}`,
      cell: (d) => (
        <span className="font-medium">
          {d.serie}{d.folio}
          {d.cancelacion_msj && (
            <span className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-xs font-normal text-warning"
                  title={d.cancelacion_msj}>en cancelación</span>
          )}
        </span>
      ) },
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
        {/* Por omisión el saldo NO incluye las facturas cuya cancelación ya se
            pidió al SAT: perseguirlas es cobrar algo que no va a llegar. El
            interruptor las trae de vuelta para poder revisarlas. */}
        {data.facturas_en_cancelacion > 0 && (
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-warning"
                   checked={verEnCancelacion}
                   onChange={(e) => setVerEnCancelacion(e.target.checked)} />
            <span>
              Incluir {data.facturas_en_cancelacion} en proceso de cancelación
              <span className="ml-1 font-medium tabular-nums text-warning">
                {fmtMoney(data.saldo_en_cancelacion)}
              </span>
            </span>
          </label>
        )}
      </div>

      {/* Antigüedad de saldos: cada caja es un filtro de la tabla (una o
          varias; ninguna marcada = todas). */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {BUCKETS.map((b) => {
          const activo = buckets.includes(b.key);
          return (
            <button key={b.key} type="button" aria-pressed={activo}
                    onClick={() => toggleBucket(b.key)}
                    className={`rounded-xl border p-4 text-left transition ${activo
                      ? "border-accent bg-accent/5 ring-1 ring-accent"
                      : "border-border bg-background hover:bg-surface-2"}`}>
              <div className="text-xs text-muted">{b.label}</div>
              <div className="text-lg font-semibold tabular-nums">{fmtMoney(data.antiguedad[b.key])}</div>
            </button>
          );
        })}
      </div>

      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium">
          Facturas PPD con saldo{serie ? ` · serie ${serie}` : ""}
          {buckets.length > 0 && (
            <button type="button" className="ml-2 text-xs font-normal text-accent hover:underline"
                    onClick={() => setBuckets([])}>
              Quitar filtro de antigüedad
            </button>
          )}
        </div>
        <div className="text-sm">
          Saldo total: <span className="font-semibold tabular-nums">{fmtMoney(data.saldo_total)}</span>
          {visibles.length !== data.facturas.length && (
            <span className="ml-2 text-muted">
              · filtrado: <span className="font-semibold tabular-nums text-foreground">
                {fmtMoney(visibles.reduce((t, d) => t + Number(d.saldo_insoluto), 0))}
              </span>
            </span>
          )}
        </div>
      </div>
      <DataTableSmart
        rows={data.facturas}
        rowKey={(d) => d.factura_id}
        columns={cols}
        storageKey="estado-cuenta-facturas"
        rowFilter={buckets.length ? (d) => buckets.includes(bucketDe(d.dias_vencida)) : undefined}
        rowFilterKey={buckets.join(",")}
        onFilteredRowsChange={setVisibles}
        toolbarExtra={
          <Button onClick={bajarEstadoCuenta} disabled={bajando || visibles.length === 0}
                  title="Descargar el estado de cuenta (formato SAE) de las facturas filtradas">
            <FileSpreadsheet size={16} /> {bajando ? "Generando…" : "Estado de cuenta"}
          </Button>
        }
        empty="El cliente no tiene saldos pendientes."
      />
    </div>
  );
}

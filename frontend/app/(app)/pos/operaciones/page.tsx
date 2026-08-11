"use client";

// POS · OPERACIONES (Fase 4) — tablero de supervisión: métricas del día y los
// pedidos activos con su progreso por etapa. Se refresca casi en tiempo real
// (pulso del POS) además de un backstop periódico.
import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, CircleDot } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { apiFetch } from "@/lib/api";
import { fmtDateTime, fmtMoney } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import { usePosPulse } from "@/lib/usePosPulse";
import type { OperacionesData } from "@/lib/pos";
import type { Cliente } from "@/lib/types";

export default function Page() {
  const [data, setData] = useState<OperacionesData | null>(null);
  const [error, setError] = useState(false);

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const cliName = useMemo(
    () => Object.fromEntries((clientesRes.data?.items ?? []).map((c) => [c.id, c.legal_name])),
    [clientesRes.data],
  );

  const cargar = useCallback(() => {
    apiFetch<OperacionesData>("/api/v1/pos/operaciones").then(setData).catch(() => setError(true));
  }, []);
  useEffect(() => {
    cargar();
    const t = setInterval(cargar, 30_000);
    return () => clearInterval(t);
  }, [cargar]);
  usePosPulse(cargar);

  if (error) return <Alert tone="danger">No se pudo cargar el tablero. Recarga la página.</Alert>;
  if (!data) return <div className="flex justify-center py-16"><Spinner /></div>;

  const colas = data.flujo.filter((e) => e !== "pedido");

  return (
    <div>
      <PageHeader title="Operaciones" subtitle="Tablero del día — se actualiza solo" />

      {/* Métricas del día */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Pedidos hoy" value={String(data.pedidos_hoy)} />
        <Metric label="Ventas hoy" value={fmtMoney(data.ventas_hoy_total)} />
        <Metric label="Cobrado hoy" value={fmtMoney(data.cobrado_hoy_total)} />
        <Metric label="En proceso" value={String(colas.reduce((s, e) => s + (data.por_etapa[e] ?? 0), 0))} />
      </div>

      {/* Cobrado por forma de pago */}
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Metric label="Efectivo" value={fmtMoney(data.cobrado_hoy.efectivo)} sub />
        <Metric label="Tarjeta" value={fmtMoney(data.cobrado_hoy.tarjeta)} sub />
        <Metric label="Crédito" value={fmtMoney(data.cobrado_hoy.credito)} sub />
      </div>

      {/* Conteo por etapa (embudo) */}
      <Card className="mb-4">
        <div className="mb-2 text-sm font-medium">Pedidos por estación</div>
        <div className="flex flex-wrap gap-3">
          {colas.map((e) => (
            <div key={e} className="flex min-w-24 flex-col items-center rounded-lg border border-border px-4 py-2">
              <span className="text-2xl font-semibold tabular-nums">{data.por_etapa[e] ?? 0}</span>
              <span className="text-xs text-muted">{data.etiquetas[e] ?? e}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Pedidos activos con su progreso */}
      <Card>
        <div className="mb-3 text-sm font-medium">Pedidos en curso ({data.activos.length})</div>
        {data.activos.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted">Nada en proceso ahora mismo.</div>
        ) : (
          <div className="space-y-2">
            {data.activos.map((p) => (
              <div key={p.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
                <div className="min-w-0">
                  <div className="text-sm font-medium">
                    {p.folio_interno} <span className="text-muted">· {cliName[p.cliente_id] ?? "—"}</span>
                  </div>
                  <div className="text-xs text-muted">{fmtDateTime(p.created_at)} · {fmtMoney(p.total)}</div>
                </div>
                <Stepper flujo={data.flujo} etiquetas={data.etiquetas} actual={p.pos_etapa}
                  hechas={Object.keys(p.pos_asignaciones)} />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: boolean }) {
  return (
    <Card>
      <div className="text-xs text-muted">{label}</div>
      <div className={sub ? "text-lg font-semibold tabular-nums" : "text-2xl font-semibold tabular-nums"}>{value}</div>
    </Card>
  );
}

function Stepper({ flujo, etiquetas, actual, hechas }: {
  flujo: string[]; etiquetas: Record<string, string>; actual: string; hechas: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {flujo.map((e) => {
        const done = hechas.includes(e);
        const here = e === actual;
        return (
          <span key={e}
            className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${
              here ? "bg-accent/15 font-medium text-accent"
              : done ? "text-success" : "text-muted"
            }`}
          >
            {done ? <CheckCircle2 size={12} /> : <CircleDot size={12} />}
            {etiquetas[e] ?? e}
          </span>
        );
      })}
    </div>
  );
}

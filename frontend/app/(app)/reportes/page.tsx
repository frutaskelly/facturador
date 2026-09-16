"use client";

// Reportes — la vista rápida del negocio. Arranca con el que el dueño llevaba
// a mano en la hoja GRAL de su Excel: saldos por proyecto, con su vencido y el
// total. Vive en su propio menú (no en el dashboard) para que crezca: aquí es
// donde caerán los siguientes cortes sin enterrar la pantalla de inicio.
import Link from "next/link";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { apiFetch } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";

type FilaProyecto = {
  proyecto: string; saldo: string; vencido: string; facturas: number;
  cliente_id: string | null; serie: string | null;
};
type SaldosPorProyecto = {
  corte: string;
  proyectos: FilaProyecto[];
  saldo_total: string; vencido_total: string;
  saldo_en_cancelacion: string;
};

export default function ReportesPage() {
  const [data, setData] = useState<SaldosPorProyecto | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let vivo = true;
    apiFetch<SaldosPorProyecto>("/api/v1/cobranza/saldos-por-proyecto")
      .then((d) => { if (vivo) setData(d); })
      .catch(() => { if (vivo) setError(true); });
    return () => { vivo = false; };
  }, []);

  if (error) return <Alert tone="danger">No se pudo cargar el reporte de saldos.</Alert>;
  if (!data) return <div className="flex justify-center py-16"><Spinner /></div>;

  const destino = (p: FilaProyecto) =>
    p.cliente_id ? `/clientes/${p.cliente_id}/estado-cuenta${p.serie ? `?serie=${p.serie}` : ""}` : null;

  return (
    <div>
      <PageHeader
        title="Reportes"
        subtitle="Se recalculan solos con cada pasada del espejo de SAE."
      />

      <div className="max-w-3xl">
        <Card title={`Saldos por proyecto · corte ${fmtDate(data.corte)}`}>
          {/* En el teléfono la tabla de tres columnas aprieta: cada proyecto se
              apila como renglón de dos líneas. En sm+ vuelve la tabla clásica. */}
          <div className="sm:hidden">
            {data.proyectos.map((p) => {
              const href = destino(p);
              const fila = (
                <div className="flex items-baseline justify-between gap-2 border-b border-border/60 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm">{p.proyecto}</div>
                    <div className="text-xs text-muted">
                      {p.facturas} fact.
                      {Number(p.vencido) > 0 && (
                        <> · vencido <span className="font-medium text-danger">{fmtMoney(p.vencido)}</span></>
                      )}
                    </div>
                  </div>
                  <div className="shrink-0 text-right text-sm font-medium tabular-nums">{fmtMoney(p.saldo)}</div>
                </div>
              );
              return href
                ? <Link key={p.proyecto} href={href} className="block">{fila}</Link>
                : <div key={p.proyecto}>{fila}</div>;
            })}
            <div className="flex items-baseline justify-between py-2 font-semibold">
              <span>Total</span>
              <span className="text-right tabular-nums">
                {fmtMoney(data.saldo_total)}
                <span className="block text-xs font-medium text-danger">vencido {fmtMoney(data.vencido_total)}</span>
              </span>
            </div>
          </div>
          <table className="hidden w-full text-sm sm:table">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="py-1.5">Proyecto</th>
                <th className="py-1.5 text-right">Saldo</th>
                <th className="py-1.5 text-right">Vencido</th>
              </tr>
            </thead>
            <tbody>
              {data.proyectos.map((p) => {
                const href = destino(p);
                const nombre = href
                  ? <Link href={href} className="hover:underline">{p.proyecto}</Link>
                  : p.proyecto;
                return (
                  <tr key={p.proyecto} className="border-b border-border/60">
                    <td className="py-1.5 pr-2">{nombre} <span className="text-xs text-muted">· {p.facturas}</span></td>
                    <td className="py-1.5 text-right tabular-nums">{fmtMoney(p.saldo)}</td>
                    <td className="py-1.5 text-right tabular-nums">
                      {Number(p.vencido) > 0
                        ? <span className="font-medium text-danger">{fmtMoney(p.vencido)}</span>
                        : <span className="text-muted">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="font-semibold">
                <td className="py-2">Total</td>
                <td className="py-2 text-right tabular-nums">{fmtMoney(data.saldo_total)}</td>
                <td className="py-2 text-right tabular-nums text-danger">{fmtMoney(data.vencido_total)}</td>
              </tr>
            </tfoot>
          </table>
          {Number(data.saldo_en_cancelacion) > 0 && (
            <p className="mt-2 text-xs text-muted">
              Fuera del total: {fmtMoney(data.saldo_en_cancelacion)} en proceso de cancelación ante el SAT.
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}

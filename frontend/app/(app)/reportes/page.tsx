"use client";

// Reportes de dirección — las dos preguntas del dueño: cuánto se está
// facturando y cuánto nos deben. Arriba las ventas (hoy, semana y mes, contra
// el mismo tramo del periodo anterior) y abajo la cobranza (cartera agrupada
// como se quiera ver, con su antigüedad por meses).
//
// Los comparativos son contra los MISMOS días transcurridos del periodo
// pasado: comparar una semana a medias contra una completa pinta una caída
// que no existe, y en un tablero de dirección eso se lee como un problema.
import Link from "next/link";
import { useEffect, useState } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";

import { BarraSegmentada, BarrasTiempo, type Punto } from "@/components/ui/Barras";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { apiFetch } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";

type Agrupar = "proyecto" | "cliente" | "sucursal";

type FilaCartera = {
  etiqueta: string; saldo: string; vencido: string; facturas: number;
  cliente_id: string | null; serie: string | null;
};
type Cartera = {
  corte: string; agrupar: Agrupar; filas: FilaCartera[];
  saldo_total: string; vencido_total: string;
  antiguedad: { por_vencer: string; mes_1: string; mes_2: string; mes_3: string; mes_4_mas: string };
  saldo_en_cancelacion: string;
};
type Ventas = {
  hoy: string;
  diario: { fecha: string; total: string; facturas: number }[];
  mensual: { mes: string; total: string }[];
  semana: { actual: string; previa_mismo_tramo: string; previa_completa: string; variacion: number | null; dias_transcurridos: number };
  mes: { actual: string; previo_mismo_tramo: string; previo_completo: string; variacion: number | null; dias_transcurridos: number };
};

const AGRUPAR: { key: Agrupar; label: string }[] = [
  { key: "proyecto", label: "Proyecto" },
  { key: "cliente", label: "Cliente" },
  { key: "sucursal", label: "Sucursal" },
];

const CUBETAS = [
  { key: "por_vencer", label: "Por vencer", clase: "bg-success/70" },
  { key: "mes_1", label: "1 mes", clase: "bg-favorite/80" },
  { key: "mes_2", label: "2 meses", clase: "bg-favorite" },
  { key: "mes_3", label: "3 meses", clase: "bg-danger/70" },
  { key: "mes_4_mas", label: "4+ meses", clase: "bg-danger" },
] as const;

/** Un día como "12 sep"; el mes como "sep 26". Sin año en los días: con 30
 *  barras el año se repetiría 30 veces sin aportar nada. */
function etiquetaDia(iso: string) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("es-MX", { day: "numeric", month: "short" });
}
function etiquetaMes(iso: string) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("es-MX", { month: "short", year: "2-digit" });
}

/** La flecha del comparativo. Verde si subió, rojo si bajó — y "sin
 *  comparativo" cuando no hubo periodo previo, en vez de un 0% engañoso. */
function Variacion({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-xs text-muted">sin comparativo</span>;
  const sube = pct >= 0;
  const Icono = sube ? TrendingUp : TrendingDown;
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${sube ? "text-success" : "text-danger"}`}>
      <Icono size={13} aria-hidden />
      {sube ? "+" : ""}{pct}%
    </span>
  );
}

function KPI({ titulo, monto, pie, pct }: { titulo: string; monto: string; pie: string; pct?: number | null }) {
  return (
    <Card>
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{titulo}</div>
      <div className="mt-1.5 text-xl font-semibold tabular-nums sm:text-2xl">{fmtMoney(monto)}</div>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {pct !== undefined && <Variacion pct={pct} />}
        <span className="text-xs text-muted">{pie}</span>
      </div>
    </Card>
  );
}

export default function ReportesPage() {
  const [ventas, setVentas] = useState<Ventas | null>(null);
  const [cartera, setCartera] = useState<Cartera | null>(null);
  const [agrupar, setAgrupar] = useState<Agrupar>("proyecto");
  const [error, setError] = useState(false);

  useEffect(() => {
    let vivo = true;
    apiFetch<Ventas>("/api/v1/reportes/ventas?dias=30&meses=6")
      .then((d) => { if (vivo) setVentas(d); })
      .catch(() => { if (vivo) setError(true); });
    return () => { vivo = false; };
  }, []);

  useEffect(() => {
    let vivo = true;
    setCartera(null);
    apiFetch<Cartera>(`/api/v1/reportes/cartera?agrupar=${agrupar}`)
      .then((d) => { if (vivo) setCartera(d); })
      .catch(() => { if (vivo) setError(true); });
    return () => { vivo = false; };
  }, [agrupar]);

  if (error) return <Alert tone="danger">No se pudieron cargar los reportes.</Alert>;
  if (!ventas) return <div className="flex justify-center py-16"><Spinner /></div>;

  const hoyTotal = ventas.diario[ventas.diario.length - 1]?.total ?? "0";
  const puntosDia: Punto[] = ventas.diario.map((d) => ({
    etiqueta: etiquetaDia(d.fecha),
    valor: Number(d.total),
    detalle: `${d.facturas} fact.`,
  }));
  const puntosMes: Punto[] = ventas.mensual.map((m) => ({
    etiqueta: etiquetaMes(m.mes),
    valor: Number(m.total),
  }));

  const destino = (f: FilaCartera) =>
    f.cliente_id ? `/clientes/${f.cliente_id}/estado-cuenta${f.serie ? `?serie=${f.serie}` : ""}` : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reportes"
        subtitle={`Ventas y cobranza al ${fmtDate(ventas.hoy)} · se recalculan con cada pasada del espejo de SAE`}
      />

      {/* ── Ventas ── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KPI titulo="Facturado hoy" monto={hoyTotal} pie={fmtDate(ventas.hoy)} />
        <KPI
          titulo="Semana en curso"
          monto={ventas.semana.actual}
          pct={ventas.semana.variacion}
          pie={`vs mismos ${ventas.semana.dias_transcurridos} días de la semana pasada`}
        />
        <KPI
          titulo="Mes en curso"
          monto={ventas.mes.actual}
          pct={ventas.mes.variacion}
          pie={`vs mismos ${ventas.mes.dias_transcurridos} días del mes pasado`}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Facturación diaria · últimos 30 días">
          <BarrasTiempo puntos={puntosDia} titulo="Facturación por día de los últimos 30 días" />
        </Card>
        <Card title="Facturación por mes">
          <BarrasTiempo puntos={puntosMes} titulo="Facturación mensual" />
          <div className="mt-3 flex flex-wrap justify-between gap-2 border-t border-border pt-3 text-xs text-muted">
            <span>
              Semana pasada completa:{" "}
              <span className="font-medium text-foreground">{fmtMoney(ventas.semana.previa_completa)}</span>
            </span>
            <span>
              Mes pasado completo:{" "}
              <span className="font-medium text-foreground">{fmtMoney(ventas.mes.previo_completo)}</span>
            </span>
          </div>
        </Card>
      </div>

      {/* ── Cobranza ── */}
      <Card title="Cuentas por cobrar">
        {!cartera ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : (
          <>
            <div className="mb-4">
              <BarraSegmentada
                titulo="Antigüedad de la cartera"
                tramos={CUBETAS.map((c) => ({
                  etiqueta: c.label,
                  valor: Number(cartera.antiguedad[c.key]),
                  clase: c.clase,
                }))}
              />
            </div>

            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
              {/* La misma cartera vista por la dimensión que se quiera: el total
                  no cambia entre pestañas, solo el reparto. */}
              <div className="inline-flex rounded-lg border border-border p-0.5">
                {AGRUPAR.map((a) => (
                  <button
                    key={a.key}
                    onClick={() => setAgrupar(a.key)}
                    aria-pressed={agrupar === a.key}
                    className={`rounded-md px-3 py-1 text-sm transition ${
                      agrupar === a.key ? "bg-surface-2 font-medium text-foreground" : "text-muted hover:text-foreground"
                    }`}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
              <div className="text-sm">
                Total: <span className="font-semibold tabular-nums">{fmtMoney(cartera.saldo_total)}</span>
                <span className="ml-2 text-danger">
                  vencido <span className="font-semibold tabular-nums">{fmtMoney(cartera.vencido_total)}</span>
                </span>
              </div>
            </div>

            {/* Teléfono: renglones apilados. Escritorio: tabla. */}
            <div className="sm:hidden">
              {cartera.filas.map((f) => {
                const href = destino(f);
                const fila = (
                  <div className="flex items-baseline justify-between gap-2 border-b border-border/60 py-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm">{f.etiqueta}</div>
                      <div className="text-xs text-muted">
                        {f.facturas} fact.
                        {Number(f.vencido) > 0 && (
                          <> · vencido <span className="font-medium text-danger">{fmtMoney(f.vencido)}</span></>
                        )}
                      </div>
                    </div>
                    <div className="shrink-0 text-sm font-medium tabular-nums">{fmtMoney(f.saldo)}</div>
                  </div>
                );
                return href
                  ? <Link key={f.etiqueta} href={href} className="block">{fila}</Link>
                  : <div key={f.etiqueta}>{fila}</div>;
              })}
            </div>
            <table className="hidden w-full text-sm sm:table">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                  <th className="py-1.5">{AGRUPAR.find((a) => a.key === agrupar)?.label}</th>
                  <th className="py-1.5 text-right">Saldo</th>
                  <th className="py-1.5 text-right">Vencido</th>
                </tr>
              </thead>
              <tbody>
                {cartera.filas.map((f) => {
                  const href = destino(f);
                  return (
                    <tr key={f.etiqueta} className="border-b border-border/60">
                      <td className="py-1.5 pr-2">
                        {href ? <Link href={href} className="hover:underline">{f.etiqueta}</Link> : f.etiqueta}
                        <span className="text-xs text-muted"> · {f.facturas}</span>
                      </td>
                      <td className="py-1.5 text-right tabular-nums">{fmtMoney(f.saldo)}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {Number(f.vencido) > 0
                          ? <span className="font-medium text-danger">{fmtMoney(f.vencido)}</span>
                          : <span className="text-muted">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {Number(cartera.saldo_en_cancelacion) > 0 && (
              <p className="mt-3 text-xs text-muted">
                Fuera del total: {fmtMoney(cartera.saldo_en_cancelacion)} en facturas con la
                cancelación ya pedida al SAT.
              </p>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

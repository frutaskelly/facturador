"use client";

// Reportes de dirección — las dos preguntas del dueño: cuánto se está
// facturando y cuánto nos deben. Arriba las ventas, abajo la cobranza.
//
// Todo cuelga de DOS filtros globales —un rango de fechas y un cliente— que
// mueven la pantalla completa: KPIs, gráfica y cartera salen del mismo recorte,
// así que no hay forma de leer un total de un periodo junto a un detalle de
// otro. Antes cada gráfica traía su propia ventana fija (30 días, 6 meses) y
// no se podía mirar atrás.
//
// Los comparativos son contra el tramo equivalente anterior —un mes empezado
// contra los mismos días del mes pasado, no contra el mes completo—: comparar
// un periodo a medias contra uno entero pinta una caída que no existe, y en un
// tablero de dirección eso se lee como un problema.
import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, RotateCcw, TrendingDown, TrendingUp } from "lucide-react";
import type { ReactNode } from "react";

import { BarraSegmentada, SerieTiempo, type Punto } from "@/components/ui/Barras";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { can, useAuth } from "@/lib/auth";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import type { Cliente } from "@/lib/types";
import {
  PRESETS, correr, diasDe, etiquetaDia, etiquetaMes, etiquetaRango, hoyISO,
  presetDe, rangoPreset, type PresetKey, type Rango,
} from "./rango";

type Agrupar = "proyecto" | "cliente" | "sucursal";
type Granularidad = "dia" | "semana" | "mes";

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
type Cubeta = { inicio: string; fin: string; total: string; facturas: number };
type Ventas = {
  hoy: string; desde: string; hasta: string; dias: number;
  granularidad: Granularidad;
  serie: Cubeta[];
  total: string; facturas: number;
  ticket_promedio: string; promedio_dia: string; promedio_cubeta: string;
  mejor: Cubeta | null;
  hoy_total: string | null;
  anterior: { desde: string; hasta: string; total: string; facturas: number; variacion: number | null };
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

const PASOS: { key: Granularidad; label: string; unidad: string }[] = [
  { key: "dia", label: "Día", unidad: "día" },
  { key: "semana", label: "Semana", unidad: "semana" },
  { key: "mes", label: "Mes", unidad: "mes" },
];

/** ¿Cabe la gráfica con este paso? Un año por día son 365 barras de dos pixeles:
 *  la opción se apaga en vez de ofrecer algo que no se puede ni tocar. */
function pasoCabe(paso: Granularidad, dias: number): boolean {
  if (paso === "dia") return dias <= 120;
  if (paso === "semana") return dias <= 900;
  return true;
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

function KPI({ titulo, valor, pie, pct }: { titulo: string; valor: ReactNode; pie: string; pct?: number | null }) {
  return (
    <Card>
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{titulo}</div>
      <div className="mt-1.5 text-xl font-semibold tabular-nums sm:text-2xl">{valor}</div>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {pct !== undefined && <Variacion pct={pct} />}
        <span className="text-xs text-muted">{pie}</span>
      </div>
    </Card>
  );
}

const NAV =
  "flex items-center justify-center rounded-lg border border-border px-2 text-muted transition hover:border-accent/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40";

export default function ReportesPage() {
  const { me } = useAuth();
  const hoy = hoyISO();

  const [rango, setRango] = useState<Rango>(() => rangoPreset("30d"));
  // "auto" = que el backend elija el paso por el tamaño del rango. En cuanto el
  // usuario toca los botones, manda él.
  const [paso, setPaso] = useState<Granularidad | "auto">("auto");
  const [clienteId, setClienteId] = useState("");
  const [agrupar, setAgrupar] = useState<Agrupar>("proyecto");

  const dias = diasDe(rango);
  const preset = presetDe(rango, hoy);
  const pasoQuery = paso !== "auto" && pasoCabe(paso, dias) ? paso : "auto";

  // Un solo juego de filtros para las dos consultas: si divergieran, la cartera
  // hablaría de un periodo y la gráfica de otro.
  const filtros = useMemo(() => {
    const qs = new URLSearchParams({ desde: rango.desde, hasta: rango.hasta });
    if (clienteId) qs.set("cliente_id", clienteId);
    return qs.toString();
  }, [rango.desde, rango.hasta, clienteId]);

  const ventasRes = useResource<Ventas>(
    `/api/v1/reportes/ventas?${filtros}${pasoQuery === "auto" ? "" : `&granularidad=${pasoQuery}`}`,
  );
  const carteraRes = useResource<Cartera>(`/api/v1/reportes/cartera?agrupar=${agrupar}&${filtros}`);
  const verClientes = can(me, "menu:clientes");
  const clientesRes = useResource<Page<Cliente>>(verClientes ? "/api/v1/clientes?limit=1000" : null);
  const clientes = clientesRes.data?.items ?? [];
  const clienteNombre = clientes.find((c) => c.id === clienteId)?.legal_name;

  const ventas = ventasRes.data;
  const cartera = carteraRes.data;
  const limpio = preset === "30d" && !clienteId;

  const grafica = useMemo<{ puntos: Punto[]; claveHoy: string | undefined }>(() => {
    if (!ventas) return { puntos: [], claveHoy: undefined };
    return {
      puntos: ventas.serie.map((c) => ({
        clave: c.inicio,
        // Bajo el eje va lo corto; el rango completo de la cubeta (que en los
        // extremos viene RECORTADO al filtro) se lee al pasar el cursor.
        etiqueta: ventas.granularidad === "mes" ? etiquetaMes(c.inicio) : etiquetaDia(c.inicio),
        detalle: etiquetaRango({ desde: c.inicio, hasta: c.fin }),
        nota: `${fmtNumber(c.facturas, 0)} ${c.facturas === 1 ? "factura" : "facturas"}`,
        valor: Number(c.total),
      })),
      // La cubeta donde cae hoy: es la única que todavía se está llenando, y
      // por eso va en tono lleno aunque no sea la más alta.
      claveHoy: ventas.serie.find((c) => c.inicio <= ventas.hoy && ventas.hoy <= c.fin)?.inicio,
    };
  }, [ventas]);

  if (ventasRes.error) return <Alert tone="danger">No se pudieron cargar los reportes.</Alert>;

  const unidad = PASOS.find((p) => p.key === ventas?.granularidad)?.unidad ?? "periodo";
  const destino = (f: FilaCartera) =>
    f.cliente_id ? `/clientes/${f.cliente_id}/estado-cuenta${f.serie ? `?serie=${f.serie}` : ""}` : null;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Reportes"
        subtitle="Ventas y cobranza del periodo que elijas · se recalculan con cada pasada del espejo de SAE"
      />

      {/* ── Filtros globales: mandan sobre TODO lo de abajo ── */}
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <span className="mb-1 block text-sm font-medium">Periodo</span>
            <div className="flex items-stretch gap-1">
              <button
                type="button"
                className={NAV}
                aria-label="Periodo anterior"
                title="Periodo anterior"
                onClick={() => setRango((r) => correr(r, -1))}
              >
                <ChevronLeft size={16} />
              </button>
              <Select
                className="min-w-[11rem]"
                value={preset ?? "personalizado"}
                onChange={(e) => setRango(rangoPreset(e.target.value as PresetKey, hoy))}
                aria-label="Periodo"
              >
                {PRESETS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                <option value="personalizado" disabled>Personalizado</option>
              </Select>
              <button
                type="button"
                className={NAV}
                aria-label="Periodo siguiente"
                title="Periodo siguiente"
                // Adelantarse a hoy solo traería barras en cero.
                disabled={rango.hasta >= hoy}
                onClick={() => setRango((r) => correr(r, 1))}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>

          <div className="w-36 sm:w-40">
            <Field label="Desde">
              <Input
                type="date"
                value={rango.desde}
                max={rango.hasta}
                onChange={(e) => {
                  const desde = e.target.value;
                  if (desde) setRango((r) => ({ desde, hasta: desde > r.hasta ? desde : r.hasta }));
                }}
              />
            </Field>
          </div>
          <div className="w-36 sm:w-40">
            <Field label="Hasta">
              <Input
                type="date"
                value={rango.hasta}
                min={rango.desde}
                onChange={(e) => {
                  const hasta = e.target.value;
                  if (hasta) setRango((r) => ({ hasta, desde: hasta < r.desde ? hasta : r.desde }));
                }}
              />
            </Field>
          </div>

          {/* Sin permiso de clientes no hay catálogo que ofrecer: el filtro se
              esconde en vez de quedarse como un desplegable muerto. */}
          {verClientes && (
            <Field label="Cliente">
              <Select
                className="min-w-56"
                value={clienteId}
                onChange={(e) => setClienteId(e.target.value)}
                disabled={clientes.length === 0}
                aria-label="Filtrar por cliente"
              >
                <option value="">Todos los clientes</option>
                {clientes.map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
              </Select>
            </Field>
          )}

          {!limpio && (
            <button
              type="button"
              onClick={() => { setRango(rangoPreset("30d", hoy)); setClienteId(""); setPaso("auto"); }}
              className="inline-flex items-center gap-1.5 rounded-lg px-2 py-2 text-sm text-muted transition hover:text-foreground"
            >
              <RotateCcw size={14} aria-hidden /> Limpiar
            </button>
          )}
        </div>

        <p className="mt-3 border-t border-border pt-3 text-xs text-muted">
          Mostrando <span className="font-medium text-foreground">{etiquetaRango(rango)}</span>
          {" "}({fmtNumber(dias, 0)} {dias === 1 ? "día" : "días"})
          {clienteNombre && <> · solo <span className="font-medium text-foreground">{clienteNombre}</span></>}
        </p>
      </Card>

      {!ventas ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <KPI
              titulo="Facturado"
              valor={fmtMoney(ventas.total)}
              pct={ventas.anterior.variacion}
              pie={`vs ${etiquetaRango({ desde: ventas.anterior.desde, hasta: ventas.anterior.hasta })}: ${fmtMoney(ventas.anterior.total)}`}
            />
            <KPI
              titulo="Promedio por día"
              valor={fmtMoney(ventas.promedio_dia)}
              pie={`${fmtNumber(ventas.dias, 0)} días en el rango`}
            />
            <KPI
              titulo="Facturas"
              valor={fmtNumber(ventas.facturas, 0)}
              pie={`ticket promedio ${fmtMoney(ventas.ticket_promedio)}`}
            />
            {ventas.hoy_total !== null ? (
              <KPI titulo="Facturado hoy" valor={fmtMoney(ventas.hoy_total)} pie={fmtDate(ventas.hoy)} />
            ) : (
              <KPI
                titulo={`Mejor ${unidad}`}
                valor={fmtMoney(ventas.mejor?.total ?? 0)}
                pie={ventas.mejor ? etiquetaRango({ desde: ventas.mejor.inicio, hasta: ventas.mejor.fin }) : "sin facturación"}
              />
            )}
          </div>

          <Card
            title="Facturación"
            subtitle={`Una barra por ${unidad} · ${etiquetaRango(rango)}`}
            actions={
              <div className="inline-flex rounded-lg border border-border p-0.5">
                {PASOS.map((p) => {
                  const cabe = pasoCabe(p.key, dias);
                  return (
                    <button
                      key={p.key}
                      type="button"
                      disabled={!cabe}
                      onClick={() => setPaso(p.key)}
                      aria-pressed={ventas.granularidad === p.key}
                      title={cabe ? `Una barra por ${p.unidad}` : `El rango es muy largo para verlo por ${p.unidad}`}
                      className={`rounded-md px-2.5 py-1 text-sm transition disabled:cursor-not-allowed disabled:opacity-40 ${
                        ventas.granularidad === p.key
                          ? "bg-surface-2 font-medium text-foreground"
                          : "text-muted hover:text-foreground"
                      }`}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            }
            footer={
              <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted">
                <span>
                  Promedio por {unidad}:{" "}
                  <span className="font-medium text-foreground">{fmtMoney(ventas.promedio_cubeta)}</span>
                </span>
                {ventas.mejor && (
                  <span>
                    Mejor {unidad}:{" "}
                    <span className="font-medium text-foreground">
                      {etiquetaRango({ desde: ventas.mejor.inicio, hasta: ventas.mejor.fin })} · {fmtMoney(ventas.mejor.total)}
                    </span>
                  </span>
                )}
                <span>
                  Periodo anterior:{" "}
                  <span className="font-medium text-foreground">{fmtMoney(ventas.anterior.total)}</span>
                </span>
              </div>
            }
          >
            <div className={ventasRes.loading ? "opacity-50 transition-opacity" : "transition-opacity"}>
              <SerieTiempo
                puntos={grafica.puntos}
                titulo={`Facturación por ${unidad} · ${etiquetaRango(rango)}`}
                destacar={(p) => p.clave === grafica.claveHoy}
              />
            </div>
          </Card>

          {/* ── Cobranza ── */}
          <Card
            title="Cuentas por cobrar"
            subtitle={`Saldos de hoy (${fmtDate(cartera?.corte ?? ventas.hoy)}) sobre las facturas emitidas ${etiquetaRango(rango)}`}
          >
            {!cartera ? (
              <div className="flex justify-center py-8"><Spinner /></div>
            ) : cartera.filas.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                Ninguna factura emitida en este periodo tiene saldo pendiente.
              </p>
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
                        type="button"
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
        </>
      )}
    </div>
  );
}

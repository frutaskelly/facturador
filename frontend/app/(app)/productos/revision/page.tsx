"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Ban, Check, RefreshCw, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Select, Textarea } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { SearchBox } from "@/components/ui/SearchBox";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import type { components } from "@/lib/api-types.gen";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";

type S = components["schemas"];
type Grupo = S["GrupoRevision"];
type Revision = S["RevisionOut"];
type Ajustes = { queda_sku?: string; nombre_final?: string; claves?: Record<string, string>; quitar?: string[] };

const WRITE = "producto:gestionar";
const URL = "/api/v1/revision-catalogo";
const PAGINA = 25;

const TIPO: Record<Grupo["tipo"], { label: string; ayuda: string }> = {
  GEMELOS: { label: "Gemelos", ayuda: "Mismo nombre y misma unidad: el mismo producto dado de alta dos veces." },
  UNIDADES: { label: "Unidades", ayuda: "El mismo producto en otra unidad: queda uno solo con una clave por unidad." },
  EMPAQUE: { label: "Empaque por kilo", ayuda: "El nombre trae gramaje o empaque: se vende por pieza, no por kilo." },
};
const ESTADO_TONO = { PENDIENTE: "muted", APROBADO: "success", RECHAZADO: "danger", APLICADO: "accent" } as const;
const FILTROS = [
  { k: "PENDIENTE", label: "Pendientes" },
  { k: "APROBADO", label: "Aprobados" },
  { k: "RECHAZADO", label: "Rechazados" },
  { k: "TODOS", label: "Todos" },
] as const;

function fecha(iso?: string | null) {
  return iso ? new Date(iso).toLocaleDateString("es-MX", { day: "numeric", month: "short" }) : "";
}

/** Tras aprobar, la pantalla lee los ajustes que se guardaron: el backend los
 *  devuelve ya aplicados en la propuesta. Aquí sólo se deduce lo que difiere
 *  de la propuesta automática para volver a mandarlo. */
function ajustesDe(g: Grupo, aj: Ajustes): Ajustes {
  return {
    ...aj,
    queda_sku: aj.queda_sku ?? g.propuesta.queda_sku,
    nombre_final: aj.nombre_final ?? g.propuesta.nombre_final,
    claves: aj.claves ?? Object.fromEntries(g.propuesta.unidades.map((u) => [u.unidad, u.clave])),
    quitar: aj.quitar ?? g.propuesta.quitar,
  };
}

export default function RevisionCatalogoPage() {
  const { me } = useAuth();
  const puede = can(me, WRITE);
  const { data, loading, error, reload, setData } = useResource<Revision>(URL);
  const [filtro, setFiltro] = useState<(typeof FILTROS)[number]["k"]>("PENDIENTE");
  const [tipo, setTipo] = useState<"" | Grupo["tipo"]>("");
  const [q, setQ] = useState("");
  const [mostrar, setMostrar] = useState(PAGINA);

  const reemplazar = useCallback(
    (g: Grupo) =>
      setData((d) => (d ? { ...d, grupos: d.grupos.map((x) => (x.clave === g.clave ? g : x)) } : d)),
    [setData],
  );

  const visibles = useMemo(() => {
    const ql = q.trim().toUpperCase();
    return (data?.grupos ?? []).filter(
      (g) =>
        (filtro === "TODOS" || g.estado === filtro) &&
        (!tipo || g.tipo === tipo) &&
        (!ql || g.raiz.includes(ql) || g.miembros.some((m) => m.sku.includes(ql) || m.nombre.toUpperCase().includes(ql))),
    );
  }, [data, filtro, tipo, q]);

  useEffect(() => setMostrar(PAGINA), [filtro, tipo, q]);

  const cuenta = (k: string) =>
    k === "TODOS" ? data?.resumen.grupos ?? 0 : (data?.grupos ?? []).filter((g) => g.estado === k).length;

  return (
    <div>
      <PageHeader
        title="Revisión del catálogo"
        subtitle="Productos que son el mismo, calculados con los datos de este momento. Aprueba cómo queda cada grupo; unirlos es un paso aparte."
        actions={
          <Button variant="secondary" onClick={reload} disabled={loading}>
            <RefreshCw size={16} /> Recalcular
          </Button>
        }
      />

      <div className="mb-4 rounded-xl border border-border bg-surface-2 px-4 py-3 text-sm text-muted">
        <p className="font-medium text-foreground">Cómo se arma la propuesta</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          <li>El nombre no lleva la unidad: «ESPINACA», no «ESPINACA PZA» ni «MANOJO DE 1 KG».</li>
          <li>Cada unidad trae su clave de SAE. Gana la que existe en SAE, luego la de formato nuevo, luego la más facturada.</li>
          <li>Si el nombre trae gramaje o empaque (PAQ 454 GR, BOLSA) se vende por pieza, no por kilo.</li>
          <li>Otra variedad u otro gramaje es otro producto. Se queda el de nombre limpio; si no hay, el que más vende.</li>
          <li>Las remisiones en borrador de los que se unen no cambian; sólo las nuevas.</li>
        </ul>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {FILTROS.map((f) => (
          <button
            key={f.k}
            type="button"
            onClick={() => setFiltro(f.k)}
            className={`rounded-full border px-3 py-1 text-sm transition ${
              filtro === f.k ? "border-accent bg-accent text-white" : "border-border hover:bg-surface-2"
            }`}
          >
            {f.label} <span className="opacity-70">{cuenta(f.k)}</span>
          </button>
        ))}
        <div className="w-48">
          <Select value={tipo} onChange={(e) => setTipo(e.target.value as typeof tipo)}>
            <option value="">Todos los tipos</option>
            {Object.entries(TIPO).map(([k, t]) => (
              <option key={k} value={k}>
                {t.label} ({data?.resumen.por_tipo[k] ?? 0})
              </option>
            ))}
          </Select>
        </div>
        <SearchBox value={q} onChange={setQ} placeholder="Buscar producto o SKU…" className="min-w-[220px] flex-1" />
      </div>

      {error && <p className="mb-4 text-sm text-danger">{error}</p>}
      {loading && !data ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : visibles.length === 0 ? (
        <EmptyState
          title={filtro === "PENDIENTE" ? "No hay grupos pendientes" : "No hay grupos con ese filtro"}
          hint={data?.resumen.aplicados ? `${data.resumen.aplicados} grupos ya se unieron.` : undefined}
        />
      ) : (
        <div className="space-y-4">
          {visibles.slice(0, mostrar).map((g) => (
            <GrupoCard key={g.clave} grupo={g} puede={puede} onCambio={reemplazar} />
          ))}
          {visibles.length > mostrar && (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={() => setMostrar((n) => n + PAGINA)}>
                Ver más ({visibles.length - mostrar})
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GrupoCard({ grupo, puede, onCambio }: { grupo: Grupo; puede: boolean; onCambio: (g: Grupo) => void }) {
  const toast = useToast();
  const { post, loading: guardando } = useMutation();
  const [g, setG] = useState(grupo);
  const [aj, setAj] = useState<Ajustes>({});
  const [nota, setNota] = useState(grupo.decision?.nota ?? "");
  const [calculando, setCalculando] = useState(false);
  const [otra, setOtra] = useState<Record<string, string>>({});
  const seq = useRef(0);

  useEffect(() => setG(grupo), [grupo]);

  const editable = puede && g.estado !== "APLICADO";
  const p = g.propuesta;
  const unidadesTodas = [...p.unidades.map((u) => u.unidad), ...p.quitar];

  const recalcular = useCallback(
    async (nuevo: Ajustes) => {
      setAj(nuevo);
      const n = ++seq.current;
      setCalculando(true);
      try {
        const r = await post<Grupo>(`${URL}/propuesta`, { grupo: g.clave, ...nuevo });
        if (n === seq.current) setG(r);
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : "No se pudo recalcular");
      } finally {
        if (n === seq.current) setCalculando(false);
      }
    },
    [post, g.clave, toast],
  );

  async function decidir(estado: "APROBADO" | "RECHAZADO" | "PENDIENTE") {
    try {
      const cuerpo = estado === "APROBADO" ? ajustesDe(g, aj) : aj;
      const r = await post<Grupo>(`${URL}/decision`, { grupo: g.clave, estado, nota, ...cuerpo });
      setG(r);
      onCambio(r);
      toast.success(
        estado === "APROBADO" ? "Aprobado" : estado === "RECHAZADO" ? "Rechazado: no se unen" : "Regresó a pendiente",
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  const cambiarClave = (unidad: string, clave: string) =>
    recalcular({ ...aj, claves: { ...Object.fromEntries(p.unidades.map((u) => [u.unidad, u.clave])), ...aj.claves, [unidad]: clave } });
  const alternarQuitar = (unidad: string) => {
    const actual = aj.quitar ?? p.quitar;
    recalcular({ ...aj, quitar: actual.includes(unidad) ? actual.filter((u) => u !== unidad) : [...actual, unidad] });
  };

  return (
    <div className="rounded-xl border border-border bg-background">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{p.nombre_final}</span>
            <Badge tone="default">{TIPO[g.tipo].label}</Badge>
            <Badge tone={ESTADO_TONO[g.estado]}>{g.estado.charAt(0) + g.estado.slice(1).toLowerCase()}</Badge>
            {calculando && <Spinner className="h-4 w-4" />}
          </div>
          <p className="mt-0.5 text-xs text-muted">{TIPO[g.tipo].ayuda}</p>
          {g.decision && (
            <p className="mt-1 text-xs text-muted">
              {g.decision.desactualizada ? (
                <span className="text-amber-700">
                  Se había {g.decision.estado.toLowerCase()} el {fecha(g.decision.at)}, pero el grupo cambió: revísalo otra vez.
                </span>
              ) : (
                <>
                  {g.decision.estado.charAt(0) + g.decision.estado.slice(1).toLowerCase()} por {g.decision.por ?? "—"} el{" "}
                  {fecha(g.decision.at)}
                </>
              )}
            </p>
          )}
        </div>
        <div className="text-right text-xs text-muted">
          {g.miembros.length} productos · {g.ventas} partidas en el último año
        </div>
      </div>

      <div className="overflow-x-auto px-4 py-3">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-muted">
              {g.tipo !== "EMPAQUE" && <th className="py-1 pr-2">Se queda</th>}
              <th className="py-1 pr-2">SKU</th>
              <th className="py-1 pr-2">Nombre</th>
              <th className="py-1 pr-2">Unidades → clave SAE</th>
              <th className="py-1 pr-2 text-right">Ventas</th>
              <th className="py-1 pr-2 text-right">Precios</th>
              <th className="py-1 pr-2 text-right">Sinónimos</th>
              <th className="py-1">Alta</th>
            </tr>
          </thead>
          <tbody>
            {g.miembros.map((m) => {
              const queda = m.sku === p.queda_sku;
              return (
                <tr key={m.id} className={`border-t border-border ${queda ? "" : "text-muted"}`}>
                  {g.tipo !== "EMPAQUE" && (
                    <td className="py-1.5 pr-2">
                      <input
                        type="radio"
                        name={`queda-${g.clave}`}
                        checked={queda}
                        disabled={!editable}
                        onChange={() => recalcular({ ...aj, queda_sku: m.sku, claves: undefined, quitar: undefined })}
                        aria-label={`Se queda ${m.sku}`}
                      />
                    </td>
                  )}
                  <td className="py-1.5 pr-2 font-mono text-xs">{m.sku}</td>
                  <td className="py-1.5 pr-2">
                    <span className={queda ? "font-medium text-foreground" : ""}>{m.nombre}</span>
                    <div className="text-xs text-muted">
                      {[m.categoria, m.esquema, m.clave_sat && `SAT ${m.clave_sat}`].filter(Boolean).join(" · ")}
                    </div>
                  </td>
                  <td className="py-1.5 pr-2">
                    <div className="flex flex-wrap gap-1">
                      {m.unidades.map((u) => (
                        <span
                          key={u.unidad}
                          className={`rounded-md border px-1.5 py-0.5 text-xs ${
                            u.en_sae ? "border-border" : "border-red-300 text-red-700"
                          }`}
                          title={u.en_sae ? "Existe en SAE" : "No existe en SAE"}
                        >
                          {u.unidad} → {u.clave || "sin clave"}
                          {u.borradores > 0 && <span className="ml-1 text-amber-700">· {u.borradores} borr.</span>}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{m.ventas}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{m.precios}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{m.alias}</td>
                  <td className="py-1.5 text-xs">{fecha(m.alta)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="border-t border-border bg-surface-2/50 px-4 py-3">
        <p className="mb-2 text-xs font-semibold uppercase text-muted">Así queda</p>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted">Nombre</span>
          <Input
            key={`${g.clave}-${p.queda_sku}`}
            defaultValue={p.nombre_final}
            disabled={!editable}
            className="max-w-md"
            onBlur={(e) => {
              const v = e.target.value.trim().toUpperCase();
              if (v && v !== p.nombre_final) recalcular({ ...aj, nombre_final: v });
            }}
          />
          <span className="text-sm text-muted">
            Unidad base <span className="font-medium text-foreground">{p.unidad_base}</span>
          </span>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-muted">
              <th className="py-1 pr-2">Unidad</th>
              <th className="py-1 pr-2">Clave SAE</th>
              <th className="py-1 pr-2">En SAE</th>
              <th className="py-1 pr-2 text-right">Facturas</th>
              <th className="py-1">Se vende</th>
            </tr>
          </thead>
          <tbody>
            {unidadesTodas.map((unidad) => {
              const u = p.unidades.find((x) => x.unidad === unidad);
              const quitada = !u;
              const opciones = u ? [u, ...u.alternativas] : [];
              return (
                <tr key={unidad} className={`border-t border-border ${quitada ? "text-muted line-through" : ""}`}>
                  <td className="py-1.5 pr-2 font-medium">{unidad}</td>
                  <td className="py-1.5 pr-2">
                    {u && (
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="w-56">
                          <Select value={u.clave} disabled={!editable} onChange={(e) => cambiarClave(unidad, e.target.value)}>
                            {!u.clave && <option value="">— escoge —</option>}
                            {opciones.map((o) => (
                              <option key={o.clave} value={o.clave}>
                                {o.clave}
                                {o.formato_viejo ? " (formato viejo)" : ""}
                                {o.de_sku ? ` · de ${o.de_sku}` : ""}
                              </option>
                            ))}
                          </Select>
                        </div>
                        {editable && (
                          <div className="w-40">
                          <Input
                            placeholder="otra clave…"
                            value={otra[unidad] ?? ""}
                            onChange={(e) => setOtra((o) => ({ ...o, [unidad]: e.target.value.toUpperCase() }))}
                            onKeyDown={(e) => {
                              const v = (otra[unidad] ?? "").trim();
                              if (e.key === "Enter" && v) {
                                cambiarClave(unidad, v);
                                setOtra((o) => ({ ...o, [unidad]: "" }));
                              }
                            }}
                          />
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pr-2 text-xs">
                    {u ? (u.en_sae.length ? u.en_sae.join(", ") : <span className="text-red-700">No existe</span>) : ""}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{u ? u.facturas : ""}</td>
                  <td className="py-1.5">
                    <input
                      type="checkbox"
                      checked={!quitada}
                      disabled={!editable || (!quitada && p.unidades.length === 1)}
                      onChange={() => alternarQuitar(unidad)}
                      aria-label={`Se vende en ${unidad}`}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {(p.bloqueos.length > 0 || p.alertas.length > 0) && (
          <ul className="mt-3 space-y-1 text-sm">
            {p.bloqueos.map((b) => (
              <li key={b} className="flex gap-2 text-red-700">
                <Ban size={16} className="mt-0.5 shrink-0" /> {b}
              </li>
            ))}
            {p.alertas.map((a) => (
              <li key={a} className="flex gap-2 text-amber-700">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {a}
              </li>
            ))}
          </ul>
        )}

        {puede && (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div className="min-w-[240px] flex-1">
              <Textarea
                rows={1}
                placeholder="Nota (opcional)"
                value={nota}
                disabled={!editable}
                onChange={(e) => setNota(e.target.value)}
              />
            </div>
            {g.estado === "PENDIENTE" ? (
              <>
                <Button
                  variant="success"
                  onClick={() => decidir("APROBADO")}
                  disabled={guardando || calculando || p.bloqueos.length > 0}
                  title={p.bloqueos.length ? "Resuelve lo que está en rojo primero" : undefined}
                >
                  <Check size={16} /> Aprobar
                </Button>
                <Button variant="secondary" onClick={() => decidir("RECHAZADO")} disabled={guardando}>
                  <Ban size={16} /> No se unen
                </Button>
              </>
            ) : (
              g.estado !== "APLICADO" && (
                <Button variant="secondary" onClick={() => decidir("PENDIENTE")} disabled={guardando}>
                  <RotateCcw size={16} /> Regresar a pendiente
                </Button>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}

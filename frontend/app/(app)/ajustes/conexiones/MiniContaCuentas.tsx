"use client";

// Mini Conta: una clave por CUENTA (Kelly Chiapas, Kelly Tabasco…). Cada cuenta
// de Mini Conta es un cliente aparte: su clave solo lee las series, los clientes
// y —si se le da— el catálogo que aquí se le comparte. Generar, cambiar o
// desconectar la de una cuenta no toca a las demás.
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Calculator,
  Check,
  Copy,
  KeyRound,
  Pencil,
  Plus,
  Power,
  RotateCw,
  X,
} from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Checkbox, Field, Input, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  AlcanceMiniConta,
  ClaveNueva,
  Conexion,
  ConexionEstado,
  OpcionesMiniConta,
} from "@/lib/types";

const PUEDE = [
  "Leer las líneas de las facturas timbradas de las series que le compartes",
  "Ver qué clientes hay en esas series (o solo los que marques)",
  "Solo si se lo das: el catálogo, las remisiones, las notas de crédito, la cobranza y los precios de esas series",
];

// Lo que una cuenta puede leer además de las ventas, en el orden de la pantalla.
const DATOS: { clave: keyof Omit<AlcanceMiniConta, "series" | "clientes">; titulo: string; texto: string }[] = [
  {
    clave: "catalogo",
    titulo: "Catálogo",
    texto: "Solo los productos que se facturan en esas series, para darlos de alta y ligarlos por SKU. Nunca el catálogo completo.",
  },
  {
    clave: "remisiones",
    titulo: "Remisiones",
    texto: "Lo entregado en esas series, para que Compra vs venta cuente los días que todavía no se facturan.",
  },
  {
    clave: "notas_credito",
    titulo: "Notas de crédito",
    texto: "Las aplicadas a facturas de esas series; se restan de la venta que corrigen.",
  },
  {
    clave: "cobranza",
    titulo: "Cobranza y saldos",
    texto: "Los pagos recibidos de esas facturas y lo que falta por cobrar, con su antigüedad.",
  },
  {
    clave: "precios",
    titulo: "Precios de venta",
    texto: "El precio de lista que hoy le toca a cada cliente, para ver el margen contra lo comprado.",
  },
];
const NO_PUEDE = [
  "Ver las series, los clientes o las ventas de otra cuenta",
  "Crear, timbrar ni cancelar facturas; tocar remisiones, clientes ni productos",
  "Ver tus sellos, tus usuarios ni tus precios",
];

function haceCuanto(iso?: string | null): string {
  if (!iso) return "nunca";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} d`;
}

/** «Tabasco · ZMAFAN», con las plazas completas por nombre y lo suelto por código. */
function resumenSeries(series: string[], op: OpcionesMiniConta | null): string {
  if (!op) return series.join(", ");
  const quedan = new Set(series);
  const partes: string[] = [];
  for (const s of op.sucursales) {
    if (s.series.length && s.series.every((x) => quedan.has(x))) {
      partes.push(s.nombre);
      s.series.forEach((x) => quedan.delete(x));
    }
  }
  return [...partes, ...[...quedan].sort()].join(" · ");
}

function resumenClientes(ids: string[] | null | undefined, op: OpcionesMiniConta | null): string {
  if (!ids) return "Todos los clientes de esas series";
  const nombres = ids.map((id) => op?.clientes.find((c) => c.id === id)?.nombre ?? "cliente borrado");
  return nombres.length <= 3
    ? nombres.join(", ")
    : `${nombres.slice(0, 3).join(", ")} y ${nombres.length - 3} más`;
}

export function MiniContaCuentas({
  estado,
  canWrite,
  onCambio,
}: {
  estado: ConexionEstado;
  canWrite: boolean;
  onCambio: () => void;
}) {
  const toast = useToast();
  const cuentas = estado.conexiones ?? [];
  const [opciones, setOpciones] = useState<OpcionesMiniConta | null>(null);
  // undefined = cerrado; null = cuenta nueva; Conexion = editar esa.
  const [editando, setEditando] = useState<Conexion | null | undefined>(undefined);
  // La clave en claro solo vive aquí, en memoria, hasta que Mini Conta la usa.
  const [nueva, setNueva] = useState<ClaveNueva | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [aRegenerar, setARegenerar] = useState<Conexion | null>(null);
  const [aDesconectar, setADesconectar] = useState<Conexion | null>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    if (!canWrite) return;
    apiFetch<OpcionesMiniConta>("/api/v1/conexiones/MINI_CONTA/opciones")
      .then(setOpciones)
      .catch(() => setOpciones(null));
  }, [canWrite]);

  // Cuando Mini Conta usa la clave por primera vez, la pantalla se pone en
  // verde sola y la clave se quita de en medio.
  const usada = nueva ? cuentas.find((c) => c.id === nueva.conexion.id)?.estado === "ACTIVA" : false;
  useEffect(() => {
    if (usada && nueva) {
      toast.success(`Conectado — ${nueva.conexion.nombre} ya puede leer en Mini Conta.`);
      setNueva(null);
    }
  }, [usada, nueva, toast]);

  async function copiar(texto: string) {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 2500);
    } catch {
      toast.error("No se pudo copiar; selecciónala y cópiala a mano");
    }
  }

  async function guardar(nombre: string, alcance: AlcanceMiniConta) {
    try {
      if (editando) {
        await apiFetch(`/api/v1/conexiones/${editando.id}`, {
          method: "PATCH",
          body: JSON.stringify({ nombre, alcance }),
        });
        toast.success(`Guardado. Mini Conta lo verá la próxima vez que lea.`);
      } else {
        const r = await apiFetch<ClaveNueva>("/api/v1/conexiones/MINI_CONTA/clave", {
          method: "POST",
          body: JSON.stringify({ nombre, alcance }),
        });
        setNueva(r);
        setCopiado(false);
      }
      setEditando(undefined);
      onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  async function regenerar() {
    const c = aRegenerar;
    if (!c) return;
    setOcupado(true);
    try {
      const r = await apiFetch<ClaveNueva>(`/api/v1/conexiones/${c.id}/regenerar`, { method: "POST" });
      setNueva(r);
      setCopiado(false);
      setARegenerar(null);
      onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo generar la clave");
    } finally {
      setOcupado(false);
    }
  }

  async function desconectar() {
    const c = aDesconectar;
    if (!c) return;
    try {
      await apiFetch(`/api/v1/conexiones/${c.id}/revocar`, { method: "POST" });
      toast.success(`Desconectado. ${c.nombre} dejó de poder leer aquí.`);
      setADesconectar(null);
      if (nueva?.conexion.id === c.id) setNueva(null);
      onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo desconectar");
    }
  }

  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg border border-border bg-surface-2">
            <Calculator size={18} />
          </div>
          <div>
            <h2 className="font-semibold">{estado.nombre}</h2>
            <p className="text-sm text-muted">
              Una clave por cuenta de Mini Conta. Cada una lee solo lo que le compartes.
            </p>
          </div>
        </div>
        {canWrite && cuentas.length ? (
          <Button variant="secondary" onClick={() => setEditando(null)} disabled={!opciones}>
            <Plus size={16} /> Conectar otra cuenta
          </Button>
        ) : null}
      </div>

      {/* ── La clave recién generada: se muestra UNA vez ─────────────────── */}
      {nueva ? (
        <div className="mt-5 rounded-lg border border-border p-4">
          <p className="mb-2 text-sm font-semibold">Clave de {nueva.conexion.nombre}</p>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-background px-4 py-3">
            <code className="break-all font-mono text-sm">{nueva.clave}</code>
            <Button variant="secondary" onClick={() => copiar(nueva.clave)}>
              {copiado ? <Check size={15} /> : <Copy size={15} />}
              {copiado ? "Copiada" : "Copiar"}
            </Button>
          </div>
          <div className="mt-3">
            <Alert tone="warning">
              Esta clave se muestra una sola vez y es solo de {nueva.conexion.nombre}. No la
              pegues en otra cuenta: cada cuenta necesita la suya.
            </Alert>
          </div>
          <ol className="mt-4 space-y-2.5">
            {[
              "Cópiala.",
              `Entra a Mini Conta con la cuenta ${nueva.conexion.nombre} › Configuración › Conexiones › Conectar Facturador, y pégala.`,
              "Ahí escoges qué traer de lo que le compartiste. Esta pantalla se pone en verde sola.",
            ].map((paso, i) => (
              <li key={i} className="flex items-start gap-3 text-sm">
                <span className="mt-0.5 grid h-5 w-5 flex-shrink-0 place-items-center rounded-full border border-border bg-surface-2 text-[11px] font-semibold text-muted tabular-nums">
                  {i + 1}
                </span>
                <span>{paso}</span>
              </li>
            ))}
          </ol>
          <p className="mt-3 flex items-center gap-2 text-sm text-muted">
            <RotateCw size={14} className="animate-spin" />
            Esperando a que Mini Conta la use por primera vez…
          </p>
        </div>
      ) : null}

      {/* ── Sin cuentas: un solo botón ──────────────────────────────────── */}
      {!cuentas.length && !nueva ? (
        <div className="px-4 py-8 text-center">
          <KeyRound size={44} className="mx-auto mb-4 text-muted opacity-50" />
          <h3 className="font-semibold">Ninguna cuenta conectada</h3>
          <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">
            Genera una clave para cada cuenta de Mini Conta y di qué series y clientes puede
            leer. Con ella cruza lo que compró contra lo que aquí se facturó.
          </p>
          {canWrite ? (
            <div className="mt-5">
              <Button onClick={() => setEditando(null)} disabled={!opciones}>
                <KeyRound size={16} /> Conectar una cuenta
              </Button>
            </div>
          ) : (
            <p className="mt-4 text-xs text-muted">
              Solo quien administra la empresa puede conectar sistemas.
            </p>
          )}
        </div>
      ) : null}

      {/* ── Las cuentas conectadas ──────────────────────────────────────── */}
      {cuentas.length ? (
        <ul className="mt-5 divide-y divide-border overflow-hidden rounded-lg border border-border">
          {cuentas.map((c) => {
            const a = c.alcance;
            return (
              <li key={c.id} className="bg-surface px-4 py-3.5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{c.nombre}</span>
                      {c.estado === "ACTIVA" ? (
                        <Badge tone="success">Leyendo</Badge>
                      ) : (
                        <Badge tone="warning">Falta pegar la clave</Badge>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-muted">
                      clave …{c.clave_pista} · última lectura {haceCuanto(c.ultimo_uso_at)}
                    </p>
                    {a ? (
                      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
                        <dt className="text-muted">Series</dt>
                        <dd>{resumenSeries(a.series, opciones)}</dd>
                        <dt className="text-muted">Clientes</dt>
                        <dd>{resumenClientes(a.clientes, opciones)}</dd>
                        <dt className="text-muted">Además</dt>
                        <dd>
                          {DATOS.filter((d) => a[d.clave]).map((d) => d.titulo).join(", ") ||
                            "Solo ventas"}
                        </dd>
                      </dl>
                    ) : (
                      <p className="mt-2 flex items-start gap-1.5 text-sm text-favorite">
                        <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
                        Clave de antes: lee todas las series y todos los clientes. Ponle el nombre
                        de su cuenta y di qué comparte.
                      </p>
                    )}
                  </div>
                  {canWrite ? (
                    <div className="flex flex-wrap gap-2">
                      <Button variant="secondary" onClick={() => setEditando(c)} disabled={!opciones}>
                        <Pencil size={15} /> Qué comparte
                      </Button>
                      <Button variant="secondary" onClick={() => setARegenerar(c)}>
                        <KeyRound size={15} /> Clave nueva
                      </Button>
                      <Button variant="secondary" onClick={() => setADesconectar(c)}>
                        <Power size={15} /> Desconectar
                      </Button>
                    </div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}

      {/* ── Qué puede y qué no. En español, siempre visible. ─────────────── */}
      <div className="mt-6 grid grid-cols-1 gap-4 border-t border-border pt-5 sm:grid-cols-2">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <Check size={15} className="text-success" /> Sí puede
          </h3>
          <ul className="mt-2 space-y-1.5">
            {PUEDE.map((t) => (
              <li key={t} className="flex items-start gap-2 text-sm text-muted">
                <Check size={14} className="mt-1 flex-shrink-0 text-success" />
                {t}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <X size={15} className="text-danger" /> No puede
          </h3>
          <ul className="mt-2 space-y-1.5">
            {NO_PUEDE.map((t) => (
              <li key={t} className="flex items-start gap-2 text-sm text-muted">
                <X size={14} className="mt-1 flex-shrink-0 text-danger" />
                {t}
              </li>
            ))}
          </ul>
        </div>
      </div>

      {editando !== undefined && opciones ? (
        <FormAlcance
          key={editando?.id ?? "nueva"}
          opciones={opciones}
          conexion={editando}
          onGuardar={guardar}
          onCerrar={() => setEditando(undefined)}
        />
      ) : null}

      <ConfirmDialog
        open={aRegenerar !== null}
        title={`Clave nueva para ${aRegenerar?.nombre ?? ""}`}
        message="La clave actual de esta cuenta deja de servir en el momento y hay que pegar la nueva en su Mini Conta. Las demás cuentas no se enteran."
        onClose={() => setARegenerar(null)}
        onConfirm={regenerar}
        loading={ocupado}
      />
      <ConfirmDialog
        open={aDesconectar !== null}
        title={`Desconectar ${aDesconectar?.nombre ?? ""}`}
        message="La clave deja de servir en el momento: esa cuenta de Mini Conta ya no podrá leer nada de aquí. Las demás cuentas siguen igual."
        confirmVariant="danger"
        onClose={() => setADesconectar(null)}
        onConfirm={desconectar}
      />
    </Card>
  );
}

/** Nombre de la cuenta + qué comparte: series (por plaza), clientes y catálogo. */
function FormAlcance({
  opciones,
  conexion,
  onGuardar,
  onCerrar,
}: {
  opciones: OpcionesMiniConta;
  conexion: Conexion | null;
  onGuardar: (nombre: string, alcance: AlcanceMiniConta) => Promise<void>;
  onCerrar: () => void;
}) {
  const inicial = conexion?.alcance ?? null;
  const [nombre, setNombre] = useState(conexion?.nombre ?? "");
  // Una clave de antes (sin alcance) arranca SIN series: hay que escogerlas.
  const [series, setSeries] = useState<Set<string>>(new Set(inicial?.series ?? []));
  const [todos, setTodos] = useState(!inicial?.clientes);
  const [clientes, setClientes] = useState<Set<string>>(new Set(inicial?.clientes ?? []));
  const [datos, setDatos] = useState(() =>
    Object.fromEntries(DATOS.map((d) => [d.clave, inicial?.[d.clave] ?? false])) as Record<
      (typeof DATOS)[number]["clave"],
      boolean
    >
  );
  const [guardando, setGuardando] = useState(false);

  const enPlaza = useMemo(() => new Set(opciones.sucursales.flatMap((s) => s.series)), [opciones]);
  const sueltas = opciones.series.filter((s) => !enPlaza.has(s));
  // Qué clientes hay en cada serie, para que «ZHGO» diga algo.
  const clientesDeSerie = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const c of opciones.clientes) {
      for (const s of c.series) m.set(s, [...(m.get(s) ?? []), c.nombre]);
    }
    return m;
  }, [opciones]);
  const visibles = opciones.clientes.filter((c) => c.series.some((s) => series.has(s)));
  const elegidos = visibles.filter((c) => clientes.has(c.id));

  function alternar(codigos: string[], prender: boolean) {
    setSeries((prev) => {
      const n = new Set(prev);
      codigos.forEach((c) => (prender ? n.add(c) : n.delete(c)));
      return n;
    });
  }

  const faltaNombre = !nombre.trim();
  const faltanSeries = series.size === 0;
  const faltanClientes = !todos && elegidos.length === 0;

  async function enviar() {
    setGuardando(true);
    await onGuardar(nombre.trim(), {
      series: [...series].sort(),
      clientes: todos ? null : elegidos.map((c) => c.id),
      ...datos,
    });
    setGuardando(false);
  }

  function filaSerie(codigo: string) {
    const quienes = clientesDeSerie.get(codigo) ?? [];
    return (
      <label key={codigo} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1 hover:bg-surface-2">
        <Checkbox
          className="mt-0.5"
          checked={series.has(codigo)}
          onChange={(e) => alternar([codigo], e.target.checked)}
        />
        <span className="min-w-0 text-sm">
          <span className="font-mono">{codigo}</span>
          {quienes.length ? (
            <span className="ml-2 text-xs text-muted">{quienes.join(", ")}</span>
          ) : (
            <span className="ml-2 text-xs text-muted">sin clientes</span>
          )}
        </span>
      </label>
    );
  }

  return (
    <Modal
      open
      wide
      onClose={onCerrar}
      title={conexion ? `Qué comparte ${conexion.nombre}` : "Conectar una cuenta de Mini Conta"}
      footer={
        <>
          <Button variant="secondary" onClick={onCerrar} disabled={guardando}>
            Cancelar
          </Button>
          <Button onClick={enviar} disabled={guardando || faltaNombre || faltanSeries || faltanClientes}>
            {conexion ? "Guardar" : <><KeyRound size={16} /> Generar clave</>}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <Field label="Cuenta de Mini Conta" hint="El nombre de la cuenta que la va a usar." required>
          <Input
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="Kelly Tabasco"
            maxLength={80}
            autoFocus={!conexion}
          />
        </Field>

        <section>
          <h3 className="text-sm font-medium">
            Series que puede leer <span className="text-danger">*</span>
          </h3>
          <p className="mb-2 text-xs text-muted">
            Sus ventas son las facturas de estas series. Marca la plaza para llevarte todas las suyas.
          </p>
          <div className="space-y-3">
            {opciones.sucursales.map((s) => {
              const marcadas = s.series.filter((x) => series.has(x)).length;
              return (
                <div key={s.nombre} className="rounded-lg border border-border p-2">
                  <label className="flex cursor-pointer items-center gap-2 px-2 py-1 font-medium">
                    <Checkbox
                      checked={marcadas === s.series.length}
                      ref={(el) => {
                        if (el) el.indeterminate = marcadas > 0 && marcadas < s.series.length;
                      }}
                      onChange={(e) => alternar(s.series, e.target.checked)}
                    />
                    <span className="text-sm">{s.nombre}</span>
                  </label>
                  <div className="ml-5">{s.series.map(filaSerie)}</div>
                </div>
              );
            })}
            {sueltas.length ? (
              <div className="rounded-lg border border-border p-2">
                <p className="px-2 py-1 text-sm font-medium text-muted">Series sin plaza</p>
                <div className="ml-5">{sueltas.map(filaSerie)}</div>
              </div>
            ) : null}
          </div>
        </section>

        <section>
          <h3 className="mb-2 text-sm font-medium">Clientes</h3>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex cursor-pointer items-center gap-2">
              <input type="radio" className="accent-accent" checked={todos} onChange={() => setTodos(true)} />
              Todos los de esas series
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input type="radio" className="accent-accent" checked={!todos} onChange={() => setTodos(false)} />
              Solo estos
            </label>
          </div>
          {!todos ? (
            visibles.length ? (
              <div className="mt-2 grid grid-cols-1 gap-0.5 sm:grid-cols-2">
                {visibles.map((c) => (
                  <label key={c.id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1 hover:bg-surface-2">
                    <Checkbox
                      className="mt-0.5"
                      checked={clientes.has(c.id)}
                      onChange={(e) =>
                        setClientes((prev) => {
                          const n = new Set(prev);
                          if (e.target.checked) n.add(c.id);
                          else n.delete(c.id);
                          return n;
                        })
                      }
                    />
                    <span className="text-sm">
                      {c.nombre}
                      <span className="ml-1.5 font-mono text-xs text-muted">
                        {c.series.filter((s) => series.has(s)).join(", ")}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted">Marca primero alguna serie.</p>
            )
          ) : null}
        </section>

        <section>
          <h3 className="text-sm font-medium">Además de las ventas</h3>
          <p className="mb-2 text-xs text-muted">Siempre de las mismas series y clientes de arriba.</p>
          <div className="divide-y divide-border rounded-lg border border-border">
            {DATOS.map((d) => (
              <div key={d.clave} className="flex items-start justify-between gap-4 p-3">
                <div>
                  <p className="text-sm font-medium">{d.titulo}</p>
                  <p className="text-xs text-muted">{d.texto}</p>
                </div>
                <Switch
                  checked={datos[d.clave]}
                  onChange={(v) => setDatos((prev) => ({ ...prev, [d.clave]: v }))}
                />
              </div>
            ))}
          </div>
        </section>

        {conexion && !conexion.alcance ? (
          <Alert tone="warning">
            Esta clave hoy lee todo. En cuanto guardes, solo leerá lo que marques aquí.
          </Alert>
        ) : null}
      </div>
    </Modal>
  );
}

"use client";

// Panel de Smart Supply: una clave por CUENTA (cada bodega/plaza de
// app.smartsupply.mx). Con ella Smart Supply LEE lo pedido (OC), lo remisionado
// y lo facturado de esa plaza para medir la merma; no escribe nada. Generar,
// cambiar o desconectar la de una cuenta no toca a las demás.
import { useEffect, useMemo, useState } from "react";
import { Check, Copy, KeyRound, Pencil, Plus, Power, RotateCw, Warehouse, X } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Checkbox, Field, Input, Select, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import type { AlcancePanel, ClaveNueva, Conexion, ConexionEstado, OpcionesPanel } from "@/lib/types";

const PUEDE = [
  "Leer las facturas timbradas de las series de factura que le compartes, por día de entrega",
  "Solo si se lo das: lo remisionado de sus series de remisión, las órdenes de compra de sus perfiles y el catálogo (claves SAE y presentaciones, sin precios)",
];
const NO_PUEDE = [
  "Ver lo de otra plaza u otra cuenta",
  "Crear, cambiar ni cancelar nada: ni órdenes, ni remisiones, ni facturas",
  "Ver tus sellos, tus usuarios, tus precios ni tu cobranza",
];

// Lo que una cuenta puede leer además del facturado, en el orden de la pantalla.
const DATOS: { clave: "remisiones" | "oc" | "catalogo"; titulo: string; texto: string }[] = [
  {
    clave: "remisiones",
    titulo: "Remisiones",
    texto: "Lo entregado en sus series de remisión, para los días que todavía no se facturan.",
  },
  {
    clave: "oc",
    titulo: "Órdenes de compra",
    texto: "Lo que pidió el cliente y por dónde entró: las OC de sus perfiles y las que se volvieron remisión de sus series.",
  },
  {
    clave: "catalogo",
    titulo: "Catálogo",
    texto: "Productos con su clave SAE y la de cada presentación, para juntar el conteo de bodega con lo vendido. Sin precios ni costos.",
  },
];

const VACIO: AlcancePanel = {
  plaza: null,
  series: [],
  series_remision: [],
  perfiles: [],
  remisiones: true,
  oc: true,
  catalogo: true,
};

// Misma forma que valida el backend (services/smart_supply.py::_PERFIL): siempre
// canal:origen. «MANUAL» o «EHMO» solos abrirían las órdenes de todas las plazas.
const PERFIL_VALIDO = /^[A-Z]{2,12}:[A-Za-z0-9@.-]{1,100}$/;

function haceCuanto(iso?: string | null): string {
  if (!iso) return "nunca";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} d`;
}

const lista = (xs: string[]) => (xs.length ? xs.join(", ") : "—");

/** La serie de remisión pareja de una de factura: `POST /series/par` la crea
 *  como R{factura} (ZEHMOVH ↔ RZEHMOVH). null si esa pareja no existe. */
function parDe(codigo: string, op: OpcionesPanel): string | null {
  const par = `R${codigo}`;
  return op.series_remision.includes(par) ? par : null;
}

/** Lo de la plaza de la clave que la clave NO comparte. Las series de remisión
 *  cuentan si comparte remisiones u OC (las que se volvieron remisión), y los
 *  perfiles si comparte OC. Vacío si la plaza no está en las opciones. */
function faltantesDePlaza(op: OpcionesPanel | null, a: AlcancePanel): string[] {
  const p = op?.plazas.find((x) => x.nombre === a.plaza);
  if (!p) return [];
  return [
    ...p.series.filter((c) => !a.series.includes(c)),
    ...(a.remisiones || a.oc ? p.series_remision.filter((c) => !a.series_remision.includes(c)) : []),
    ...(a.oc ? p.perfiles.filter((x) => !a.perfiles.includes(x)) : []),
  ];
}

export function SmartSupplyPanelCuentas({
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
  const [opciones, setOpciones] = useState<OpcionesPanel | null>(null);
  // undefined = cerrado; null = cuenta nueva; Conexion = editar esa.
  const [editando, setEditando] = useState<Conexion | null | undefined>(undefined);
  // La clave en claro solo vive aquí, en memoria, hasta que Smart Supply la usa.
  const [nueva, setNueva] = useState<ClaveNueva | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [aRegenerar, setARegenerar] = useState<Conexion | null>(null);
  const [aDesconectar, setADesconectar] = useState<Conexion | null>(null);
  const [ocupado, setOcupado] = useState(false);

  useEffect(() => {
    if (!canWrite) return;
    apiFetch<OpcionesPanel>("/api/v1/conexiones/SMART_SUPPLY_PANEL/opciones")
      .then(setOpciones)
      .catch(() => setOpciones(null));
  }, [canWrite]);

  // Cuando Smart Supply usa la clave por primera vez, la pantalla se pone en
  // verde sola y la clave se quita de en medio.
  const usada = nueva ? cuentas.find((c) => c.id === nueva.conexion.id)?.estado === "ACTIVA" : false;
  useEffect(() => {
    if (usada && nueva) {
      toast.success(`Conectado — Smart Supply ya lee ${nueva.conexion.nombre}.`);
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

  async function guardar(nombre: string, alcance: AlcancePanel) {
    try {
      if (editando) {
        await apiFetch(`/api/v1/conexiones/${editando.id}`, {
          method: "PATCH",
          body: JSON.stringify({ nombre, alcance_panel: alcance }),
        });
        toast.success("Guardado. Smart Supply lo verá la próxima vez que lea.");
      } else {
        const r = await apiFetch<ClaveNueva>("/api/v1/conexiones/SMART_SUPPLY_PANEL/clave", {
          method: "POST",
          body: JSON.stringify({ nombre, alcance_panel: alcance }),
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
      toast.success(`Desconectado. Smart Supply ya no puede leer ${c.nombre}.`);
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
            <Warehouse size={18} />
          </div>
          <div>
            <h2 className="font-semibold">{estado.nombre}</h2>
            <p className="text-sm text-muted">
              Una clave por bodega. Smart Supply lee con ella lo pedido, lo remisionado y lo
              facturado de esa plaza para medir la merma.
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
              Esta clave se muestra una sola vez y es solo de {nueva.conexion.nombre}. No la pegues
              en otra cuenta: cada bodega necesita la suya.
            </Alert>
          </div>
          <ol className="mt-4 space-y-2.5">
            {[
              "Cópiala.",
              `En Smart Supply entra a Ajustes › Conexiones, escoge la cuenta ${nueva.conexion.nombre} y pégala en «Facturador».`,
              "Smart Supply la prueba y la guarda cifrada. Esta pantalla se pone en verde sola.",
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
            Esperando a que Smart Supply la use por primera vez…
          </p>
        </div>
      ) : null}

      {/* ── Sin cuentas: un solo botón ──────────────────────────────────── */}
      {!cuentas.length && !nueva ? (
        <div className="px-4 py-8 text-center">
          <KeyRound size={44} className="mx-auto mb-4 text-muted opacity-50" />
          <h3 className="font-semibold">Ninguna bodega conectada</h3>
          <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">
            Genera una clave para cada bodega de Smart Supply y di qué plaza puede leer: sus series
            de factura, sus series de remisión y por dónde entran sus órdenes.
          </p>
          {canWrite ? (
            <div className="mt-5">
              <Button onClick={() => setEditando(null)} disabled={!opciones}>
                <KeyRound size={16} /> Conectar una bodega
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
            const a = c.alcance_panel;
            const faltan = a ? faltantesDePlaza(opciones, a) : [];
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
                      {faltan.length ? (
                        <Badge tone="warning">
                          No comparte {faltan.length === 1 ? "1 cosa" : `${faltan.length} cosas`} de {a?.plaza}
                        </Badge>
                      ) : null}
                    </div>
                    <p className="mt-0.5 text-xs text-muted">
                      clave …{c.clave_pista} · última lectura {haceCuanto(c.ultimo_uso_at)}
                    </p>
                    {a ? (
                      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
                        <dt className="text-muted">Plaza</dt>
                        <dd>{a.plaza || "—"}</dd>
                        <dt className="text-muted">Facturas</dt>
                        <dd className="font-mono text-xs leading-5">{lista(a.series)}</dd>
                        <dt className="text-muted">Remisiones</dt>
                        <dd className="font-mono text-xs leading-5">{lista(a.series_remision)}</dd>
                        <dt className="text-muted">Órdenes</dt>
                        <dd className="font-mono text-xs leading-5">{lista(a.perfiles)}</dd>
                        <dt className="text-muted">Además</dt>
                        <dd>
                          {DATOS.filter((d) => a[d.clave]).map((d) => d.titulo).join(", ") ||
                            "Solo facturado"}
                        </dd>
                        {faltan.length ? (
                          <>
                            <dt className="text-amber-700">No comparte</dt>
                            <dd className="font-mono text-xs leading-5 text-amber-700">
                              {lista(faltan)}{" "}
                              <span className="font-sans text-muted">
                                (de su plaza; corrígelo en «Qué comparte», sin clave nueva)
                              </span>
                            </dd>
                          </>
                        ) : null}
                      </dl>
                    ) : (
                      <p className="mt-2 text-sm text-muted">Sin alcance: no lee nada.</p>
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
        <FormAlcancePanel
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
        message="La clave actual de esta bodega deja de servir en el momento y hay que pegar la nueva en Smart Supply. Las demás cuentas no se enteran."
        onClose={() => setARegenerar(null)}
        onConfirm={regenerar}
        loading={ocupado}
      />
      <ConfirmDialog
        open={aDesconectar !== null}
        title={`Desconectar ${aDesconectar?.nombre ?? ""}`}
        message="La clave deja de servir en el momento: Smart Supply ya no podrá leer esta bodega. Las demás cuentas siguen igual."
        confirmVariant="danger"
        onClose={() => setADesconectar(null)}
        onConfirm={desconectar}
      />
    </Card>
  );
}

/** Nombre de la cuenta + qué comparte: la plaza (que prellena lo suyo), sus
 *  series de factura y de remisión, sus perfiles de OC y qué datos además. */
function FormAlcancePanel({
  opciones,
  conexion,
  onGuardar,
  onCerrar,
}: {
  opciones: OpcionesPanel;
  conexion: Conexion | null;
  onGuardar: (nombre: string, alcance: AlcancePanel) => Promise<void>;
  onCerrar: () => void;
}) {
  const inicial = conexion?.alcance_panel ?? VACIO;
  const [nombre, setNombre] = useState(conexion?.nombre ?? "");
  const [plaza, setPlaza] = useState(inicial.plaza ?? "");
  const [series, setSeries] = useState<Set<string>>(new Set(inicial.series));
  const [rems, setRems] = useState<Set<string>>(new Set(inicial.series_remision));
  const [perfiles, setPerfiles] = useState<Set<string>>(new Set(inicial.perfiles));
  const [otroPerfil, setOtroPerfil] = useState("");
  const [datos, setDatos] = useState({
    remisiones: inicial.remisiones,
    oc: inicial.oc,
    catalogo: inicial.catalogo,
  });
  const [guardando, setGuardando] = useState(false);

  // Los perfiles que ya traía la clave siguen a la vista aunque ya no lleguen OC por ahí.
  const todosPerfiles = useMemo(
    () => [...new Set([...opciones.perfiles, ...inicial.perfiles])].sort(),
    [opciones, inicial.perfiles]
  );
  const dePlaza = opciones.plazas.find((p) => p.nombre === plaza) ?? null;

  /** Escoger la plaza se lleva lo suyo; lo demás se puede ajustar a mano. */
  function escogerPlaza(nombrePlaza: string) {
    setPlaza(nombrePlaza);
    const p = opciones.plazas.find((x) => x.nombre === nombrePlaza);
    if (!p) return;
    setSeries(new Set(p.series));
    setRems(new Set(p.series_remision));
    setPerfiles(new Set(p.perfiles));
    if (!nombre.trim()) setNombre(`Kelly ${p.nombre}`);
  }

  /** Vuelve a marcar todo lo de la plaza SIN quitar lo que se agregó a mano.
   *  Para una clave ya creada (re-escoger la misma plaza no dispara nada) o
   *  cuando la plaza estrenó una serie o un perfil después de crear la clave. */
  function marcarPlaza() {
    if (!dePlaza) return;
    setSeries(new Set([...series, ...dePlaza.series]));
    setRems(new Set([...rems, ...dePlaza.series_remision]));
    setPerfiles(new Set([...perfiles, ...dePlaza.perfiles]));
  }

  function alternar(set: Set<string>, fijar: (s: Set<string>) => void, codigo: string, prender: boolean) {
    const n = new Set(set);
    if (prender) n.add(codigo);
    else n.delete(codigo);
    fijar(n);
  }

  /** Marcar una serie de factura se lleva su pareja de remisión (si existe). */
  function alternarFactura(codigo: string, prender: boolean) {
    alternar(series, setSeries, codigo, prender);
    const par = parDe(codigo, opciones);
    if (prender && par) setRems(new Set([...rems, par]));
  }

  const actual: AlcancePanel = {
    plaza: plaza || null,
    series: [...series],
    series_remision: [...rems],
    perfiles: [...perfiles],
    ...datos,
  };
  // Avisos, no candados: dejar algo fuera puede ser a propósito, pero nunca sin verlo.
  const fueraDePlaza = faltantesDePlaza(opciones, actual);
  const sinPareja = datos.remisiones
    ? [...series]
        .sort()
        .map((c) => [c, parDe(c, opciones)] as const)
        .filter(([, par]) => par && !rems.has(par) && !fueraDePlaza.includes(par))
    : [];

  const perfilNuevo = otroPerfil.trim();
  const perfilNuevoValido = PERFIL_VALIDO.test(perfilNuevo);
  const faltaNombre = !nombre.trim();
  const faltanSeries = series.size === 0;
  const faltaRemision = datos.remisiones && rems.size === 0;
  const faltaOrigen = datos.oc && rems.size === 0 && perfiles.size === 0;
  // El perfil solo abre las órdenes de la plaza de la clave: sin plaza, el backend da 422.
  const faltaPlaza = perfiles.size > 0 && !plaza;

  async function enviar() {
    setGuardando(true);
    await onGuardar(nombre.trim(), {
      plaza: plaza || null,
      series: [...series].sort(),
      series_remision: [...rems].sort(),
      perfiles: [...perfiles].sort(),
      ...datos,
    });
    setGuardando(false);
  }

  function grupo(
    titulo: string,
    ayuda: string,
    codigos: string[],
    marcados: Set<string>,
    fijar: (s: Set<string>) => void,
    propios: string[],
    alAlternar?: (codigo: string, prender: boolean) => void
  ) {
    return (
      <section>
        <h3 className="text-sm font-medium">{titulo}</h3>
        <p className="mb-2 text-xs text-muted">{ayuda}</p>
        {codigos.length ? (
          <div className="grid grid-cols-2 gap-0.5 rounded-lg border border-border p-2 sm:grid-cols-3">
            {codigos.map((codigo) => (
              <label
                key={codigo}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 hover:bg-surface-2"
              >
                <Checkbox
                  checked={marcados.has(codigo)}
                  onChange={(e) =>
                    alAlternar
                      ? alAlternar(codigo, e.target.checked)
                      : alternar(marcados, fijar, codigo, e.target.checked)
                  }
                />
                <span className={`font-mono text-xs ${propios.includes(codigo) ? "font-semibold" : ""}`}>
                  {codigo}
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No hay ninguna en esta empresa.</p>
        )}
      </section>
    );
  }

  return (
    <Modal
      open
      size="lg"
      onClose={onCerrar}
      title={conexion ? `Qué comparte ${conexion.nombre}` : "Conectar una bodega de Smart Supply"}
      footer={
        <>
          <Button variant="secondary" onClick={onCerrar} disabled={guardando}>
            Cancelar
          </Button>
          <Button
            data-modal-primary
            onClick={enviar}
            disabled={
              guardando || faltaNombre || faltanSeries || faltaRemision || faltaOrigen || faltaPlaza
            }
          >
            {conexion ? "Guardar" : <><KeyRound size={16} /> Generar clave</>}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field
            label="Plaza"
            hint="Marca todas sus series de factura y de remisión y sus perfiles; luego puedes ajustarlos."
          >
            <Select value={plaza} onChange={(e) => escogerPlaza(e.target.value)}>
              <option value="">Escoge la plaza…</option>
              {opciones.plazas.map((p) => (
                <option key={p.nombre} value={p.nombre}>
                  {p.nombre}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Cuenta de Smart Supply" hint="El nombre de la bodega que la va a usar." required>
            <Input
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              placeholder="Kelly Tabasco"
              maxLength={80}
              autoFocus={!conexion}
            />
          </Field>
        </div>

        {grupo(
          "Series de factura *",
          "Lo facturado de estas series es la venta de la bodega. En negritas, las de la plaza. Marcar una marca también su serie de remisión.",
          opciones.series,
          series,
          setSeries,
          dePlaza?.series ?? [],
          alternarFactura
        )}
        {grupo(
          "Series de remisión",
          "Lo entregado de estas series, y las órdenes que se volvieron remisión de ellas.",
          opciones.series_remision,
          rems,
          setRems,
          dePlaza?.series_remision ?? []
        )}

        <section>
          <h3 className="text-sm font-medium">Perfiles de órdenes de compra</h3>
          <p className="mb-2 text-xs text-muted">
            Por dónde entran sus órdenes (el inicio de su origen, p. ej. EHMO:villahermosa). Abren
            también las que todavía no tienen remisión o se descartaron, pero solo las de la plaza
            de arriba o las que aún no tienen plaza.
          </p>
          <div className="grid grid-cols-1 gap-0.5 rounded-lg border border-border p-2 sm:grid-cols-2">
            {todosPerfiles.map((p) => (
              <label
                key={p}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 hover:bg-surface-2"
              >
                <Checkbox
                  checked={perfiles.has(p)}
                  onChange={(e) => alternar(perfiles, setPerfiles, p, e.target.checked)}
                />
                <span
                  className={`break-all font-mono text-xs ${dePlaza?.perfiles.includes(p) ? "font-semibold" : ""}`}
                >
                  {p}
                </span>
              </label>
            ))}
            {[...perfiles].filter((p) => !todosPerfiles.includes(p)).map((p) => (
              <label key={p} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 hover:bg-surface-2">
                <Checkbox checked onChange={() => alternar(perfiles, setPerfiles, p, false)} />
                <span className="break-all font-mono text-xs">{p}</span>
              </label>
            ))}
          </div>
          <div className="mt-2 flex gap-2">
            <Input
              value={otroPerfil}
              onChange={(e) => setOtroPerfil(e.target.value)}
              placeholder="Otro perfil: EHMO:campeche"
              className="font-mono text-xs"
            />
            <Button
              variant="secondary"
              disabled={!perfilNuevoValido}
              onClick={() => {
                alternar(perfiles, setPerfiles, perfilNuevo, true);
                setOtroPerfil("");
              }}
            >
              <Plus size={15} /> Agregar
            </Button>
          </div>
        </section>

        <section>
          <h3 className="text-sm font-medium">Además del facturado</h3>
          <p className="mb-2 text-xs text-muted">Siempre de la misma plaza de arriba.</p>
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

        {fueraDePlaza.length ? (
          <Alert tone="warning">
            <p>
              No compartes esto de {plaza}: <span className="font-mono text-xs">{lista(fueraDePlaza)}</span>.
              Lo de ahí no llegará a Smart Supply.
            </p>
            <Button variant="secondary" className="mt-2" onClick={marcarPlaza}>
              <Check size={15} /> Marcar todo lo de {plaza}
            </Button>
          </Alert>
        ) : null}
        {sinPareja.length ? (
          <Alert tone="warning">
            Compartes {sinPareja.map(([c]) => c).join(", ")} sin su serie de remisión (
            {sinPareja.map(([, par]) => par).join(", ")}): lo remisionado de ellas no llegará a Smart
            Supply.
          </Alert>
        ) : null}
        {faltaRemision ? (
          <Alert tone="warning">Para compartir remisiones marca al menos una serie de remisión.</Alert>
        ) : null}
        {faltaOrigen ? (
          <Alert tone="warning">Para compartir las órdenes marca un perfil o una serie de remisión.</Alert>
        ) : null}
        {faltaPlaza ? (
          <Alert tone="warning">Para abrir órdenes por perfil escoge la plaza: el perfil solo abre las de ella.</Alert>
        ) : null}
      </div>
    </Modal>
  );
}

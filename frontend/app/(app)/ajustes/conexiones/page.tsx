"use client";

// Conexiones: enchufar Smart Supply sin repartir contraseñas.
//
// La pantalla tiene dos vidas. Antes de conectar es un instructivo de un solo
// botón. Después de conectar deja de ser configuración y pasa a responder una
// sola pregunta —¿está entrando lo que debe?—, que es lo único que alguien
// viene a mirar aquí una vez que funciona.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Inbox,
  KeyRound,
  MessageCircle,
  Power,
  RefreshCw,
  RotateCw,
  Unlink,
  X,
} from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Field, Select, Switch } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type {
  ActividadConexion,
  ClienteDelGrupo,
  Almacen,
  ClaveNueva,
  Cliente,
  ConexionEstado,
  GrupoWhatsapp,
  Serie,
} from "@/lib/types";

const WRITE = "membership:gestionar";

const PUEDE = [
  "Dejar órdenes de compra en la bandeja",
  "Leer tu lista de clientes y productos para cruzarlas",
  "Proponer a qué cliente pertenece cada orden",
];
const NO_PUEDE = [
  "Timbrar ni cancelar una factura ante el SAT",
  "Borrar clientes, productos ni remisiones",
  "Ver tus sellos, tu contabilidad ni tus usuarios",
];

function haceCuanto(iso?: string | null): string {
  if (!iso) return "—";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} d`;
}

export default function Page() {
  const { me } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [estados, setEstados] = useState<ConexionEstado[] | null>(null);
  const [error, setError] = useState(false);
  const [actividad, setActividad] = useState<ActividadConexion[]>([]);
  const [grupos, setGrupos] = useState<GrupoWhatsapp[]>([]);
  const [abierto, setAbierto] = useState<string | null>(null);   // jid desplegado
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [seriesFac, setSeriesFac] = useState<Serie[]>([]);
  const [seriesRem, setSeriesRem] = useState<Serie[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [agregando, setAgregando] = useState("");
  // La clave en claro solo vive aquí, en memoria, hasta que se recarga la página.
  const [nueva, setNueva] = useState<ClaveNueva | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [aDesconectar, setADesconectar] = useState<ConexionEstado | null>(null);
  const [aRegenerar, setARegenerar] = useState<ConexionEstado | null>(null);
  const [refrescando, setRefrescando] = useState(false);
  // Para saber si la conexión acaba de ponerse en verde mientras mirabas.
  const eraPendiente = useRef(false);

  const reload = useCallback(() => {
    setError(false);
    return apiFetch<ConexionEstado[]>("/api/v1/conexiones")
      .then((cs) => {
        setEstados(cs);
        // El momento en que se pega la clave en WhatsApp: la pantalla se pone en
        // verde sola y se quita la clave de en medio, sin que nadie recargue.
        if (eraPendiente.current && cs.some((c) => c.conexion?.estado === "ACTIVA")) {
          setNueva(null);
          toast.success("Conectado — Smart Supply ya puede dejar órdenes.");
        }
        eraPendiente.current = cs.some((c) => c.conexion?.estado === "PENDIENTE");

        if (cs.some((c) => c.conexion && c.conexion.estado !== "REVOCADA")) {
          apiFetch<ActividadConexion[]>("/api/v1/conexiones/SMART_SUPPLY/actividad")
            .then(setActividad)
            .catch(() => setActividad([]));
          apiFetch<GrupoWhatsapp[]>("/api/v1/conexiones/grupos")
            .then(setGrupos)
            .catch(() => setGrupos([]));
        } else {
          setActividad([]);
          setGrupos([]);
        }
      })
      .catch(() => setError(true));
  }, [toast]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Catálogos para poder EDITAR desde el detalle sin salir de la pantalla.
  useEffect(() => {
    apiFetch<{ items: Cliente[] }>("/api/v1/clientes?limit=1000")
      .then((p) => setClientes(p.items)).catch(() => undefined);
    apiFetch<{ items: Serie[] }>("/api/v1/series?tipo_documento=FACTURA&activa=true&limit=200")
      .then((p) => setSeriesFac(p.items)).catch(() => undefined);
    apiFetch<{ items: Serie[] }>("/api/v1/series?tipo_documento=REMISION&activa=true&limit=200")
      .then((p) => setSeriesRem(p.items)).catch(() => undefined);
    apiFetch<{ items: Almacen[] }>("/api/v1/almacenes?limit=200")
      .then((p) => setAlmacenes(p.items)).catch(() => undefined);
  }, []);

  /** Prender/apagar un grupo. Apagarlo no toca a Smart Supply. */
  async function togglear(g: GrupoWhatsapp, activo: boolean) {
    try {
      await apiFetch(`/api/v1/conexiones/grupos/${encodeURIComponent(g.jid)}`, {
        method: "PATCH", body: JSON.stringify({ activo }),
      });
      toast.success(activo
        ? `${g.nombre ?? "El grupo"} vuelve a entrar a la bandeja`
        : `${g.nombre ?? "El grupo"} apagado — sus órdenes ya no entran a la bandeja`);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cambiar");
    }
  }

  /** Conecta un cliente más a un grupo (queda como candidato, no decide solo). */
  async function conectarCliente(jid: string, clienteId: string) {
    try {
      await apiFetch("/api/v1/clientes/externos", {
        method: "POST",
        body: JSON.stringify({ sistema: "WHATSAPP", clave: jid, cliente_id: clienteId }),
      });
      setAgregando("");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo conectar");
    }
  }

  /** Lo que es "de este grupo para este cliente" —sucursal y series— vive en la
   *  propia equivalencia del grupo. Los campos que no se mandan no se tocan. */
  async function cambiarDelGrupo(
    g: GrupoWhatsapp,
    c: ClienteDelGrupo,
    campo: "sucursal_id" | "serie_factura_id" | "serie_remision_id",
    valor: string
  ) {
    try {
      await apiFetch("/api/v1/clientes/externos", {
        method: "POST",
        body: JSON.stringify({
          sistema: "WHATSAPP",
          clave: g.jid,
          cliente_id: c.cliente_id,
          // Se reenvía lo que ya había: el endpoint reapunta la fila entera.
          sucursal_id: c.sucursal_grupo_id ?? null,
          serie_factura_id: c.serie_factura_grupo_id ?? null,
          serie_remision_id: c.serie_remision_grupo_id ?? null,
          [campo]: valor || null,
        }),
      });
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  async function desconectarCliente(externoId: string) {
    try {
      await apiFetch(`/api/v1/clientes/externos/${externoId}`, { method: "DELETE" });
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo desconectar");
    }
  }

  /** Serie o almacén de un cliente. Es campo del CLIENTE, no del grupo: cambiarlo
   *  aquí lo cambia en todos lados, y por eso la pantalla lo dice. */
  async function cambiarCliente(clienteId: string, campo: string, valor: string) {
    try {
      await apiFetch(`/api/v1/clientes/${clienteId}`, {
        method: "PATCH", body: JSON.stringify({ [campo]: valor || null }),
      });
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  // Se refresca sola. Rápido mientras espera la clave —que es justo cuando estás
  // viendo la pantalla— y despacio una vez conectada, donde lo único que cambia
  // son los contadores. En una pestaña de fondo no consulta nada.
  const esperandoClave = estados?.some((e) => e.conexion?.estado === "PENDIENTE") ?? false;
  const hayConexion = estados?.some((e) => e.conexion && e.conexion.estado !== "REVOCADA") ?? false;
  useEffect(() => {
    if (!esperandoClave && !hayConexion) return;
    const cada = esperandoClave ? 4000 : 30000;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      reload();
    }, cada);
    return () => clearInterval(id);
  }, [esperandoClave, hayConexion, reload]);

  async function refrescar() {
    setRefrescando(true);
    await reload();
    setRefrescando(false);
  }

  async function generar(tipo: string) {
    setOcupado(true);
    try {
      const r = await apiFetch<ClaveNueva>(`/api/v1/conexiones/${tipo}/clave`, { method: "POST" });
      setNueva(r);
      setCopiado(false);
      setARegenerar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo generar la clave");
    } finally {
      setOcupado(false);
    }
  }

  async function copiar(texto: string) {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 2500);
    } catch {
      toast.error("No se pudo copiar; selecciónala y cópiala a mano");
    }
  }

  async function desconectar() {
    const c = aDesconectar?.conexion;
    if (!c) return;
    try {
      await apiFetch(`/api/v1/conexiones/${c.id}/revocar`, { method: "POST" });
      toast.success("Desconectado. Smart Supply dejó de poder escribir aquí.");
      setADesconectar(null);
      setNueva(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo desconectar");
    }
  }

  async function probar() {
    setOcupado(true);
    try {
      await apiFetch("/api/v1/conexiones/probar");
      toast.success("El Facturador responde bien.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No hubo respuesta");
    } finally {
      setOcupado(false);
    }
  }

  if (error) return <Alert tone="danger">No se pudieron cargar las conexiones.</Alert>;
  if (estados === null) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title="Conexiones"
        subtitle="Lo que otros sistemas pueden hacer dentro de tu Facturador"
        actions={
          <Button variant="secondary" onClick={refrescar} disabled={refrescando}>
            <RotateCw size={16} className={refrescando ? "animate-spin" : ""} />
            Actualizar
          </Button>
        }
      />

      {estados.map((e) => {
        const con = e.conexion;
        const conectado = !!con && con.estado !== "REVOCADA";
        const mostrandoClave = nueva !== null && nueva.conexion.tipo === e.tipo;

        return (
          <Card key={e.tipo} className="mb-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="grid h-9 w-9 place-items-center rounded-lg border border-border bg-surface-2">
                  <MessageCircle size={18} />
                </div>
                <div>
                  <h2 className="font-semibold">{e.nombre}</h2>
                  <p className="text-sm text-muted">
                    {conectado && con
                      ? `Conectada el ${new Date(con.created_at).toLocaleDateString("es-MX", {
                          day: "numeric",
                          month: "long",
                        })} · clave …${con.clave_pista}`
                      : "Órdenes de compra por WhatsApp"}
                  </p>
                </div>
              </div>
              {conectado && con ? (
                con.estado === "ACTIVA" ? (
                  <Badge tone="success">Recibiendo órdenes</Badge>
                ) : (
                  <Badge tone="warning">Falta pegar la clave</Badge>
                )
              ) : (
                <Badge tone="muted">Sin conectar</Badge>
              )}
            </div>

            {/* ── La clave recién generada: se muestra UNA vez ─────────────── */}
            {mostrandoClave && nueva ? (
              <div className="mt-5">
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-background px-4 py-3">
                  <code className="break-all font-mono text-sm">{nueva.clave}</code>
                  <Button variant="secondary" onClick={() => copiar(nueva.clave)}>
                    {copiado ? <Check size={15} /> : <Copy size={15} />}
                    {copiado ? "Copiada" : "Copiar"}
                  </Button>
                </div>

                <div className="mt-3">
                  <Alert tone="warning">
                    Esta clave se muestra una sola vez. Si la pierdes, generas otra y la anterior
                    deja de servir — nada más se rompe.
                  </Alert>
                </div>

                <p className="mt-3 flex items-center gap-2 text-sm text-muted">
                  <RotateCw size={14} className="animate-spin" />
                  Esperando a que la pegues en Smart Supply… esta pantalla se pone en verde sola.
                </p>

                <ol className="mt-4 space-y-2.5">
                  {[
                    "Cópiala.",
                    "En el grupo interno de WhatsApp manda el mensaje de abajo.",
                    "El bot responde «listo», borra tu mensaje y esta pantalla se pone en verde sola.",
                  ].map((paso, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm">
                      <span className="mt-0.5 grid h-5 w-5 flex-shrink-0 place-items-center rounded-full border border-border bg-surface-2 text-[11px] font-semibold text-muted tabular-nums">
                        {i + 1}
                      </span>
                      <span>{paso}</span>
                    </li>
                  ))}
                </ol>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface-2 px-4 py-3">
                  <code className="break-all font-mono text-xs">{nueva.instruccion_whatsapp}</code>
                  <Button variant="secondary" onClick={() => copiar(nueva.instruccion_whatsapp)}>
                    <Copy size={15} /> Copiar mensaje
                  </Button>
                </div>
              </div>
            ) : null}

            {/* ── Sin conectar: un solo botón ──────────────────────────────── */}
            {!conectado && !mostrandoClave ? (
              <div className="px-4 py-8 text-center">
                <KeyRound size={44} className="mx-auto mb-4 text-muted opacity-50" />
                <h3 className="font-semibold">Todavía no está conectado</h3>
                <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">
                  Genera una clave y pégala en Smart Supply. A partir de ahí las órdenes que
                  lleguen por WhatsApp aparecen solas en la bandeja.
                </p>
                {canWrite ? (
                  <div className="mt-5">
                    <Button onClick={() => generar(e.tipo)} disabled={ocupado}>
                      <KeyRound size={16} />
                      {ocupado ? "Generando…" : "Generar clave de conexión"}
                    </Button>
                  </div>
                ) : (
                  <p className="mt-4 text-xs text-muted">
                    Solo quien administra la empresa puede conectar sistemas.
                  </p>
                )}
              </div>
            ) : null}

            {/* ── Conectado: la pantalla responde «¿está entrando lo que debe?» ─ */}
            {conectado && !mostrandoClave ? (
              <>
                {e.conviene_rotar ? (
                  <div className="mt-4">
                    <Alert tone="warning">
                      <span className="inline-flex items-center gap-2">
                        <AlertTriangle size={15} />
                        Esta clave tiene {e.dias_desde_creacion} días. No caduca, pero conviene
                        generar una nueva de vez en cuando.
                      </span>
                    </Alert>
                  </div>
                ) : null}

                <div className="mt-5 grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
                  <div className="bg-surface px-4 py-3.5">
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">
                      Última orden
                    </div>
                    <div className="mt-0.5 text-2xl font-semibold tabular-nums">
                      {haceCuanto(e.ultima_orden_at)}
                    </div>
                  </div>
                  <div className="bg-surface px-4 py-3.5">
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">
                      Últimas 24 h
                    </div>
                    <div className="mt-0.5 text-2xl font-semibold tabular-nums">
                      {e.ordenes_hoy} <span className="text-sm font-normal text-muted">órdenes</span>
                    </div>
                  </div>
                  <div className="bg-surface px-4 py-3.5">
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">
                      Sin resolver
                    </div>
                    <div
                      className={`mt-0.5 text-2xl font-semibold tabular-nums ${
                        e.ordenes_sin_resolver ? "text-favorite" : ""
                      }`}
                    >
                      {e.ordenes_sin_resolver}{" "}
                      <span className="text-sm font-normal text-muted">en bandeja</span>
                    </div>
                  </div>
                </div>

                {actividad.length ? (
                  <div className="mt-4 overflow-x-auto rounded-lg border border-border">
                    <table className="w-full min-w-[460px] text-sm">
                      <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-muted">
                        <tr>
                          <th className="px-3.5 py-2 text-left font-semibold">Hora</th>
                          <th className="px-3.5 py-2 text-left font-semibold">OC</th>
                          <th className="px-3.5 py-2 text-left font-semibold">Llegó de</th>
                          <th className="px-3.5 py-2 text-left font-semibold">Cliente</th>
                          <th className="px-3.5 py-2 text-right font-semibold">Partidas</th>
                        </tr>
                      </thead>
                      <tbody>
                        {actividad.map((a, i) => (
                          <tr key={i} className="border-t border-border">
                            <td className="px-3.5 py-2.5 tabular-nums">
                              {new Date(a.recibida_at).toLocaleTimeString("es-MX", {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </td>
                            <td className="px-3.5 py-2.5">{a.folio_externo ?? "—"}</td>
                            <td className="px-3.5 py-2.5 text-muted">{a.remitente ?? "—"}</td>
                            <td className="px-3.5 py-2.5">
                              {a.cliente_nombre ?? (
                                <span className="text-favorite">Falta asignar</span>
                              )}
                            </td>
                            <td className="px-3.5 py-2.5 text-right tabular-nums">{a.partidas}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-muted">
                    Conectado, pero todavía no ha llegado ninguna orden.
                  </p>
                )}

                <div className="mt-4 flex flex-wrap gap-2">
                  <Link
                    href="/oc"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-surface-2"
                  >
                    <Inbox size={16} /> Ir a la bandeja
                  </Link>
                  <Button variant="secondary" onClick={probar} disabled={ocupado}>
                    <RefreshCw size={16} /> Probar conexión
                  </Button>
                  {canWrite ? (
                    <>
                      <Button variant="secondary" onClick={() => setARegenerar(e)}>
                        <KeyRound size={16} /> Generar clave nueva
                      </Button>
                      <Button variant="secondary" onClick={() => setADesconectar(e)}>
                        <Power size={16} /> Desconectar
                      </Button>
                    </>
                  ) : null}
                </div>
              </>
            ) : null}

            {/* ── Qué puede y qué no. En español, siempre visible. ─────────── */}
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
          </Card>
        );
      })}

      {/* ── El mapa: qué grupo alimenta a qué ──────────────────────────── */}
      {grupos.length ? (
        <Card className="mt-6">
          <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-semibold">Grupos de WhatsApp</h2>
            <span className="text-xs text-muted">
              {grupos.filter((g) => g.activo).length} activos de {grupos.length}
            </span>
          </div>
          <p className="mb-4 text-sm text-muted">
            De dónde llega cada orden y a quién se le factura. Lo reporta Smart Supply;
            para cambiarlo se edita allá.
          </p>

          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-muted">
                <tr>
                  <th className="w-8 px-2 py-2" />
                  <th className="px-3 py-2 text-left font-semibold">Grupo</th>
                  <th className="px-3 py-2 text-left font-semibold">Le factura a</th>
                  <th className="px-3 py-2 text-right font-semibold">Órdenes</th>
                  <th className="px-3 py-2 text-center font-semibold">Entra</th>
                </tr>
              </thead>
              <tbody>
                {grupos.map((g) => {
                  const open = abierto === g.jid;
                  return (
                    <Fragment key={g.jid}>
                      <tr
                        className={`cursor-pointer border-t border-border hover:bg-surface-2 ${
                          g.activo ? "" : "opacity-60"
                        }`}
                        onClick={() => setAbierto(open ? null : g.jid)}
                      >
                        <td className="px-2 py-2.5 text-muted">
                          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">{g.nombre ?? "Grupo sin nombre"}</span>
                            {g.rol === "cliente" ? (
                              <Badge tone="accent">Del cliente</Badge>
                            ) : (
                              <Badge tone="muted">Interno</Badge>
                            )}
                            {g.perfil ? <Badge tone="default">{g.perfil}</Badge> : null}
                            {!g.reportado_activo ? (
                              <span
                                className="text-xs text-muted"
                                title="Smart Supply ya no reporta este grupo"
                              >
                                sin reportar
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          {g.clientes.length ? (
                            <span>
                              {g.clientes.map((c) => c.nombre.split(" ").slice(0, 2).join(" ")).join(" · ")}
                            </span>
                          ) : (
                            <span className="text-favorite">Sin cliente</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">
                          {g.ordenes}
                          {g.sin_resolver ? (
                            <span className="text-favorite"> · {g.sin_resolver}⚠</span>
                          ) : null}
                        </td>
                        <td className="px-3 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                          <Switch
                            checked={g.activo}
                            disabled={!canWrite}
                            onChange={(v) => togglear(g, v)}
                          />
                        </td>
                      </tr>

                      {open ? (
                        <tr className="border-t border-border bg-surface">
                          <td colSpan={5} className="px-4 py-4">
                            <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
                              <span className="font-mono">{g.jid}</span>
                              <span>
                                {g.ultima_orden_at
                                  ? `última orden ${haceCuanto(g.ultima_orden_at)}`
                                  : "sin órdenes aún"}
                              </span>
                              {g.ordenes_24h ? <span>{g.ordenes_24h} en 24 h</span> : null}
                            </div>

                            <div className="overflow-x-auto rounded-lg border border-border">
                              <table className="w-full min-w-[620px] text-sm">
                                <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-muted">
                                  <tr>
                                    <th className="px-3 py-1.5 text-left font-semibold">Cliente</th>
                                    <th className="px-3 py-1.5 text-left font-semibold">Serie factura</th>
                                    <th className="px-3 py-1.5 text-left font-semibold">Serie remisión</th>
                                    <th className="px-3 py-1.5 text-left font-semibold">Almacén</th>
                                    <th className="px-3 py-1.5 text-left font-semibold">Sucursal por defecto</th>
                                    <th className="w-8 px-2 py-1.5" />
                                  </tr>
                                </thead>
                                <tbody>
                                  {g.clientes.map((c) => (
                                    <tr key={c.cliente_id} className="border-t border-border align-top">
                                      <td className="px-3 py-2">
                                        {c.nombre}
                                        {!c.registrado ? (
                                          <div className="text-xs text-favorite">
                                            no registrado — le llegaron órdenes de aquí
                                          </div>
                                        ) : null}
                                      </td>
                                      <td className="px-3 py-2">
                                        <Select
                                          value={c.serie_factura_grupo_id ?? ""}
                                          disabled={!canWrite}
                                          onChange={(e) =>
                                            cambiarDelGrupo(g, c, "serie_factura_id", e.target.value)
                                          }
                                        >
                                          <option value="">
                                            {c.serie_factura
                                              ? `hereda ${c.serie_factura}`
                                              : "hereda del cliente"}
                                          </option>
                                          {seriesFac.map((s) => (
                                            <option key={s.id} value={s.id}>{s.codigo}</option>
                                          ))}
                                        </Select>
                                      </td>
                                      <td className="px-3 py-2">
                                        <Select
                                          value={c.serie_remision_grupo_id ?? ""}
                                          disabled={!canWrite}
                                          onChange={(e) =>
                                            cambiarDelGrupo(g, c, "serie_remision_id", e.target.value)
                                          }
                                        >
                                          <option value="">
                                            {c.serie_remision
                                              ? `hereda ${c.serie_remision}`
                                              : "hereda del cliente"}
                                          </option>
                                          {seriesRem.map((s) => (
                                            <option key={s.id} value={s.id}>{s.codigo}</option>
                                          ))}
                                        </Select>
                                      </td>
                                      <td className="px-3 py-2">
                                        <Select
                                          value={c.almacen_id ?? ""}
                                          disabled={!canWrite}
                                          onChange={(e) =>
                                            cambiarCliente(c.cliente_id, "almacen_id", e.target.value)
                                          }
                                        >
                                          <option value="">predeterminado</option>
                                          {almacenes.map((a) => (
                                            <option key={a.id} value={a.id}>
                                              {a.codigo ? `${a.codigo} · ${a.nombre}` : a.nombre}
                                            </option>
                                          ))}
                                        </Select>
                                      </td>
                                      <td className="px-3 py-2">
                                        <Select
                                          value={c.sucursal_grupo_id ?? ""}
                                          disabled={!canWrite}
                                          onChange={(e) => {
                                            if (e.target.value === "__crear__") {
                                              router.push(`/sucursales?cliente=${c.cliente_id}`);
                                              return;
                                            }
                                            cambiarDelGrupo(g, c, "sucursal_id", e.target.value);
                                          }}
                                        >
                                          <option value="">
                                            {c.sucursales.length
                                              ? "— la que diga la orden —"
                                              : "— sin sucursales —"}
                                          </option>
                                          {c.sucursales.map((s) => (
                                            <option key={s.id} value={s.id}>{s.nombre}</option>
                                          ))}
                                          <option value="__crear__">+ Crear sucursal…</option>
                                        </Select>
                                      </td>
                                      <td className="px-2 py-2 text-right">
                                        {canWrite && c.externo_id ? (
                                          <button
                                            onClick={() => desconectarCliente(c.externo_id!)}
                                            className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-danger"
                                            title="Desconectar del grupo"
                                          >
                                            <Unlink size={15} />
                                          </button>
                                        ) : null}
                                      </td>
                                    </tr>
                                  ))}
                                  {!g.clientes.length ? (
                                    <tr>
                                      <td colSpan={6} className="px-3 py-4 text-center text-sm text-muted">
                                        Sin clientes conectados: las órdenes de este grupo llegan sin asignar.
                                      </td>
                                    </tr>
                                  ) : null}
                                </tbody>
                              </table>
                            </div>

                            {canWrite ? (
                              <div className="mt-3 flex flex-wrap items-end gap-2">
                                <div className="w-72">
                                  <Field label="Conectar otro cliente a este grupo">
                                    <Select
                                      value={agregando}
                                      onChange={(e) => setAgregando(e.target.value)}
                                    >
                                      <option value="">— Elegir —</option>
                                      {clientes
                                        .filter((c) => !g.clientes.some((x) => x.cliente_id === c.id))
                                        .map((c) => (
                                          <option key={c.id} value={c.id}>{c.legal_name}</option>
                                        ))}
                                    </Select>
                                  </Field>
                                </div>
                                <Button
                                  variant="secondary"
                                  disabled={!agregando}
                                  onClick={() => conectarCliente(g.jid, agregando)}
                                >
                                  Conectar
                                </Button>
                              </div>
                            ) : null}

                            <p className="mt-3 text-xs text-muted">
                              La <strong>sucursal por defecto</strong> y las <strong>series</strong>{" "}
                              son de ESTE grupo: un cliente usa series distintas según la operación
                              por la que entra el pedido, y en blanco hereda la suya. La sucursal
                              solo se usa cuando la orden no dice a dónde va, o nombra un punto de
                              entrega que nadie ha registrado. El <strong>almacén</strong> sí es del
                              CLIENTE: cambiarlo aquí lo cambia en todos sus documentos.
                            </p>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-4 text-xs text-muted">
            Un grupo puede alimentar a varias razones sociales —Balles y Jubran comparten los
            suyos—: el documento decide cuál, y si no lo dice, la orden espera en la bandeja.
            Apagar un grupo aquí no toca a Smart Supply; solo deja de entrar a esta bandeja.
          </p>
        </Card>
      ) : null}

      <ConfirmDialog
        open={aDesconectar !== null}
        title="Desconectar Smart Supply"
        message="La clave deja de servir en el momento. Las órdenes que ya están en la bandeja se quedan; las nuevas dejarán de llegar hasta que generes otra clave."
        onClose={() => setADesconectar(null)}
        onConfirm={desconectar}
      />

      <ConfirmDialog
        open={aRegenerar !== null}
        title="Generar una clave nueva"
        message="La clave actual deja de servir en el momento y hay que pegar la nueva en Smart Supply. Mientras no la pegues, las órdenes no van a llegar."
        onClose={() => setARegenerar(null)}
        onConfirm={() => aRegenerar && generar(aRegenerar.tipo)}
      />
    </div>
  );
}

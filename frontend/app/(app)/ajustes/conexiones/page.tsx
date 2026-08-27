"use client";

// Conexiones: enchufar Smart Supply sin repartir contraseñas.
//
// La pantalla tiene dos vidas. Antes de conectar es un instructivo de un solo
// botón. Después de conectar deja de ser configuración y pasa a responder una
// sola pregunta —¿está entrando lo que debe?—, que es lo único que alguien
// viene a mirar aquí una vez que funciona.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Copy,
  Inbox,
  KeyRound,
  MessageCircle,
  Power,
  RefreshCw,
  X,
} from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type { ActividadConexion, ClaveNueva, ConexionEstado } from "@/lib/types";

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
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [estados, setEstados] = useState<ConexionEstado[] | null>(null);
  const [error, setError] = useState(false);
  const [actividad, setActividad] = useState<ActividadConexion[]>([]);
  // La clave en claro solo vive aquí, en memoria, hasta que se recarga la página.
  const [nueva, setNueva] = useState<ClaveNueva | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [aDesconectar, setADesconectar] = useState<ConexionEstado | null>(null);
  const [aRegenerar, setARegenerar] = useState<ConexionEstado | null>(null);

  const reload = useCallback(() => {
    setError(false);
    apiFetch<ConexionEstado[]>("/api/v1/conexiones")
      .then((cs) => {
        setEstados(cs);
        if (cs.some((c) => c.conexion && c.conexion.estado !== "REVOCADA")) {
          apiFetch<ActividadConexion[]>("/api/v1/conexiones/SMART_SUPPLY/actividad")
            .then(setActividad)
            .catch(() => setActividad([]));
        } else {
          setActividad([]);
        }
      })
      .catch(() => setError(true));
  }, []);

  useEffect(() => reload(), [reload]);

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

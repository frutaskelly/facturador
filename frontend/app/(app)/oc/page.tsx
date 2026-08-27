"use client";

// Bandeja de órdenes de compra. Todo documento que entra —WhatsApp, correo o
// captura— aterriza aquí antes de volverse remisión: se ve de dónde vino, a qué
// cliente se asignó y, si el sistema no pudo resolverlo, se decide a mano.
//
// Corregir aquí no es solo arreglar UNA orden: al asignar se guarda la
// equivalencia, así que la próxima orden que llegue igual ya no pregunta.
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, FileText, Inbox, RotateCcw, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type { Page } from "@/lib/hooks";
import type {
  Almacen,
  Cliente,
  LineaOC,
  OCRecibida,
  OCRecibidaDetalle,
  Sucursal,
} from "@/lib/types";

const WRITE = "remision:gestionar";

const CANAL_TONE: Record<string, "accent" | "muted" | "default"> = {
  WHATSAPP: "accent",
  EMAIL: "default",
  MANUAL: "muted",
  API: "muted",
};

function estadoBadge(oc: OCRecibida) {
  if (oc.estado === "ASIGNADA")
    return <Badge tone="success">Remisión {oc.remision_folio ?? "creada"}</Badge>;
  if (oc.estado === "DESCARTADA") return <Badge tone="muted">Descartada</Badge>;
  if (oc.ambiguo) return <Badge tone="danger">Ambigua</Badge>;
  if (!oc.cliente_id) return <Badge tone="warning">Sin cliente</Badge>;
  return <Badge tone="warning">Por revisar</Badge>;
}

/** Una partida con el producto que se le va a asignar. `producto_id` vacío =
 *  sin resolver: la remisión no se puede crear hasta que todas tengan producto. */
type LineaEdit = {
  numero: number;
  texto: string;
  cantidad: string;
  producto_id: string;
  precio: string;
  candidatos: LineaOC["candidatos"];
};

export default function Page() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [rows, setRows] = useState<OCRecibida[] | null>(null);
  const [error, setError] = useState(false);
  const [estado, setEstado] = useState("PENDIENTE");
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);

  const [abierta, setAbierta] = useState<OCRecibidaDetalle | null>(null);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  const [clienteSel, setClienteSel] = useState("");
  const [sucursalSel, setSucursalSel] = useState("");
  const [almacenSel, setAlmacenSel] = useState("");
  const [lineas, setLineas] = useState<LineaEdit[]>([]);
  const [guardando, setGuardando] = useState(false);
  const [aDescartar, setADescartar] = useState<OCRecibida | null>(null);

  const reload = useCallback(() => {
    const qs = estado ? `?estado=${estado}&limit=200` : "?limit=200";
    apiFetch<Page<OCRecibida>>(`/api/v1/oc-recibidas${qs}`)
      .then((p) => setRows(p.items))
      .catch(() => setError(true));
  }, [estado]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    apiFetch<Page<Cliente>>("/api/v1/clientes?limit=1000")
      .then((p) => setClientes(p.items))
      .catch(() => undefined);
    apiFetch<Page<Almacen>>("/api/v1/almacenes?limit=200")
      .then((p) => setAlmacenes(p.items))
      .catch(() => undefined);
  }, []);

  const abrir = useCallback(async (id: string) => {
    try {
      const oc = await apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${id}`);
      setAbierta(oc);
      setClienteSel(oc.cliente_id ?? "");
      setSucursalSel(oc.sucursal_id ?? "");
      setAlmacenSel("");
      setLineas(
        oc.lineas.map((l) => ({
          numero: l.numero,
          texto: l.descripcion,
          cantidad: String(l.cantidad ?? ""),
          // Se preselecciona el mejor candidato solo si es un cruce fuerte
          // (exacto o alias ya confirmado). Un difuso al 76% lo revisa la persona.
          producto_id: l.candidatos[0] && l.candidatos[0].score >= 96 ? l.candidatos[0].producto_id : "",
          precio: l.precio != null ? String(l.precio) : "",
          candidatos: l.candidatos,
        }))
      );
    } catch {
      toast.error("No se pudo abrir la orden");
    }
  }, [toast]);

  // Las sucursales dependen del cliente elegido, no del que traía la orden.
  useEffect(() => {
    if (!clienteSel) {
      setSucursales([]);
      return;
    }
    apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${clienteSel}&limit=500`)
      .then((p) => setSucursales(p.items))
      .catch(() => setSucursales([]));
  }, [clienteSel]);

  async function asignar() {
    if (!abierta || !clienteSel) {
      toast.error("Elige el cliente");
      return;
    }
    setGuardando(true);
    try {
      const oc = await apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${abierta.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          cliente_id: clienteSel,
          sucursal_id: sucursalSel || null,
          aprender: true,
        }),
      });
      setAbierta(oc);
      toast.success("Asignada — la próxima orden igual ya se resuelve sola");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo asignar");
    } finally {
      setGuardando(false);
    }
  }

  const sinProducto = useMemo(() => lineas.filter((l) => !l.producto_id).length, [lineas]);

  async function crearRemision() {
    if (!abierta) return;
    if (!clienteSel) {
      toast.error("Asigna primero el cliente");
      return;
    }
    if (!lineas.length || sinProducto) {
      toast.error(`Faltan ${sinProducto} partida(s) por cruzar con un producto`);
      return;
    }
    setGuardando(true);
    try {
      const oc = await apiFetch<OCRecibidaDetalle>(
        `/api/v1/oc-recibidas/${abierta.id}/crear-remision`,
        {
          method: "POST",
          body: JSON.stringify({
            almacen_id: almacenSel || null,
            lineas: lineas.map((l) => ({
              producto_id: l.producto_id,
              cantidad: l.cantidad,
              precio_unitario: l.precio.trim() ? l.precio : null,
              texto_original: l.texto,
            })),
          }),
        }
      );
      toast.success(`Remisión ${oc.remision_folio ?? ""} creada en borrador`);
      setAbierta(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la remisión");
    } finally {
      setGuardando(false);
    }
  }

  async function descartar() {
    if (!aDescartar) return;
    try {
      await apiFetch(`/api/v1/oc-recibidas/${aDescartar.id}/descartar`, { method: "POST" });
      toast.success("Descartada");
      setADescartar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descartar");
    }
  }

  async function reabrir(oc: OCRecibida) {
    try {
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}/reabrir`, { method: "POST" });
      toast.success("Reabierta");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo reabrir");
    }
  }

  const columns: Column<OCRecibida>[] = [
    {
      header: "Origen",
      cell: (r) => (
        <div>
          <Badge tone={CANAL_TONE[r.canal] ?? "default"}>{r.canal}</Badge>
          <div className="mt-0.5 text-xs text-muted">{r.remitente ?? "—"}</div>
        </div>
      ),
    },
    {
      header: "OC",
      cell: (r) => (
        <div>
          <div className="font-medium">{r.folio_externo || "—"}</div>
          <div className="text-xs text-muted">
            {new Date(r.recibida_at).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" })}
          </div>
        </div>
      ),
    },
    {
      header: "Cliente",
      cell: (r) => (
        <div>
          <div>{r.cliente_nombre ?? <span className="text-muted">Sin asignar</span>}</div>
          {r.sucursal_nombre ? <div className="text-xs text-muted">{r.sucursal_nombre}</div> : null}
        </div>
      ),
    },
    { header: "Estado", cell: (r) => estadoBadge(r) },
    {
      header: "Detalle",
      cell: (r) => <span className="text-xs text-muted">{r.motivo ?? "—"}</span>,
    },
    {
      header: "",
      className: "text-right w-1",
      cell: (r) =>
        canWrite ? (
          <div className="flex justify-end gap-1">
            {r.archivo_url ? (
              <a
                href={r.archivo_url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2"
                aria-label="Abrir el archivo original"
              >
                <ExternalLink size={16} />
              </a>
            ) : null}
            {r.estado === "DESCARTADA" ? (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  reabrir(r);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2"
                aria-label="Reabrir"
              >
                <RotateCcw size={16} />
              </button>
            ) : r.remision_id ? null : (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setADescartar(r);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                aria-label="Descartar"
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        ) : null,
    },
  ];

  if (error) return <Alert tone="danger">No se pudo cargar la bandeja.</Alert>;
  if (rows === null) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title="Bandeja de órdenes"
        subtitle="Lo que llega por WhatsApp o correo, antes de volverse remisión"
        actions={
          <div className="w-52">
            <Select value={estado} onChange={(e) => setEstado(e.target.value)}>
              <option value="PENDIENTE">Por revisar</option>
              <option value="ASIGNADA">Ya con remisión</option>
              <option value="DESCARTADA">Descartadas</option>
              <option value="">Todas</option>
            </Select>
          </div>
        }
      />

      <DataTable
        columns={columns}
        rows={rows}
        empty={
          estado === "PENDIENTE"
            ? "Nada por revisar. Las órdenes que lleguen por WhatsApp o correo aparecen aquí."
            : "Sin órdenes en este estado."
        }
        onRowClick={(r) => abrir(r.id)}
      />

      <Modal
        open={abierta !== null}
        onClose={() => setAbierta(null)}
        title={
          abierta
            ? `OC ${abierta.folio_externo || "sin folio"} · ${abierta.canal}`
            : ""
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setAbierta(null)}>Cerrar</Button>
            {canWrite && abierta && !abierta.remision_id ? (
              <>
                <Button variant="secondary" onClick={asignar} disabled={guardando || !clienteSel}>
                  Guardar asignación
                </Button>
                <Button onClick={crearRemision} disabled={guardando}>
                  {guardando ? "Creando…" : "Crear remisión"}
                </Button>
              </>
            ) : null}
          </>
        }
      >
        {abierta ? (
          <div className="space-y-5">
            {abierta.ambiguo ? (
              <Alert tone="warning">
                <span className="inline-flex items-center gap-1.5">
                  <AlertTriangle size={15} /> {abierta.motivo}
                </span>
              </Alert>
            ) : null}

            {abierta.remision_id ? (
              <Alert tone="success">
                Esta orden ya generó la remisión <strong>{abierta.remision_folio}</strong>. Los
                cambios de kilos, líneas y precios se hacen en la remisión.
              </Alert>
            ) : null}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Field label="Cliente">
                <Select
                  value={clienteSel}
                  onChange={(e) => {
                    setClienteSel(e.target.value);
                    setSucursalSel("");
                  }}
                  disabled={!canWrite || !!abierta.remision_id}
                >
                  <option value="">— Elegir —</option>
                  {clientes.map((c) => (
                    <option key={c.id} value={c.id}>{c.legal_name}</option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Sucursal / destino"
                hint={
                  (abierta.payload?.ubicacion as string) ??
                  "El documento no trae ubicación"
                }
              >
                <Select
                  value={sucursalSel}
                  onChange={(e) => setSucursalSel(e.target.value)}
                  disabled={!canWrite || !clienteSel || !!abierta.remision_id}
                >
                  <option value="">— Sin sucursal —</option>
                  {sucursales.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.codigo ? `${s.codigo} · ${s.nombre}` : s.nombre}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Almacén de salida">
                <Select
                  value={almacenSel}
                  onChange={(e) => setAlmacenSel(e.target.value)}
                  disabled={!canWrite || !!abierta.remision_id}
                >
                  <option value="">— Sin almacén —</option>
                  {almacenes.map((a) => (
                    <option key={a.id} value={a.id}>{a.nombre}</option>
                  ))}
                </Select>
              </Field>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">Partidas del documento</h3>
                {sinProducto ? (
                  <span className="text-xs text-amber-700">
                    {sinProducto} sin cruzar con un producto
                  </span>
                ) : null}
              </div>
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-xs text-muted">
                    <tr>
                      <th className="px-3 py-2 text-left">Como venía en la orden</th>
                      <th className="px-3 py-2 text-right">Cantidad</th>
                      <th className="px-3 py-2 text-left">Producto del catálogo</th>
                      <th className="px-3 py-2 text-right">Precio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lineas.map((l, i) => (
                      <tr key={l.numero} className="border-t border-border">
                        <td className="px-3 py-2">{l.texto}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{l.cantidad}</td>
                        <td className="px-3 py-2">
                          {l.candidatos.length ? (
                            <Select
                              value={l.producto_id}
                              disabled={!canWrite || !!abierta.remision_id}
                              onChange={(e) => {
                                const v = e.target.value;
                                setLineas((prev) =>
                                  prev.map((x, j) => (j === i ? { ...x, producto_id: v } : x))
                                );
                              }}
                            >
                              <option value="">— Sin cruzar —</option>
                              {l.candidatos.map((c) => (
                                <option key={c.producto_id} value={c.producto_id}>
                                  {c.nombre} ({c.sku}) · {c.score}%
                                </option>
                              ))}
                            </Select>
                          ) : (
                            <span className="text-xs text-muted">
                              Sin candidatos — da de alta el producto primero
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <Input
                            value={l.precio}
                            disabled={!canWrite || !!abierta.remision_id}
                            placeholder="lista"
                            className="text-right"
                            onChange={(e) => {
                              const v = e.target.value;
                              setLineas((prev) =>
                                prev.map((x, j) => (j === i ? { ...x, precio: v } : x))
                              );
                            }}
                          />
                        </td>
                      </tr>
                    ))}
                    {!lineas.length ? (
                      <tr>
                        <td colSpan={4} className="px-3 py-6 text-center text-sm text-muted">
                          El documento no trae partidas legibles.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-muted">
                El precio vacío lo resuelve la lista del cliente. Los kilos, las líneas y los
                precios se pueden seguir cambiando en la remisión después de crearla.
              </p>
            </div>

            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
              <span className="inline-flex items-center gap-1">
                <Inbox size={13} /> {abierta.origen_externo}
              </span>
              {abierta.archivo_nombre ? (
                <span className="inline-flex items-center gap-1">
                  <FileText size={13} />
                  {abierta.archivo_url ? (
                    <a href={abierta.archivo_url} target="_blank" rel="noreferrer" className="hover:underline">
                      {abierta.archivo_nombre}
                    </a>
                  ) : (
                    abierta.archivo_nombre
                  )}
                </span>
              ) : null}
              {abierta.resuelto_via ? <span>Resuelto por {abierta.resuelto_via}</span> : null}
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={aDescartar !== null}
        title="Descartar la orden"
        message={`¿Descartar la OC ${aDescartar?.folio_externo ?? ""}? No se crea ninguna remisión. Se puede reabrir después.`}
        onClose={() => setADescartar(null)}
        onConfirm={descartar}
      />
    </div>
  );
}

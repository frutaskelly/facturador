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
import { ProductoCombobox } from "@/components/ProductoCombobox";
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
  unidad: string;
  producto_id: string;
  presentacion: string;
  precio: string;
  notas: string | null;
  candidatos: LineaOC["candidatos"];
  /** El operador pidió buscar fuera de los candidatos sugeridos. */
  buscando: boolean;
};

// Abreviaturas con las que los clientes escriben la unidad en sus órdenes.
const UNIDAD_ALIAS: Record<string, string> = {
  KG: "KILO", KGS: "KILO", KILOS: "KILO", KGM: "KILO",
  PZ: "PIEZA", PZA: "PIEZA", PZAS: "PIEZA", PIEZAS: "PIEZA", H87: "PIEZA",
  CJ: "CAJA", CJA: "CAJA", CAJAS: "CAJA",
  BTO: "BULTO", BULTOS: "BULTO", COSTALES: "COSTAL",
  LT: "LITRO", LTS: "LITRO", LITROS: "LITRO",
  MJO: "MANOJO", MANOJOS: "MANOJO", BOLSAS: "BOLSA",
};

/** La unidad de la orden traducida a una presentación que el producto tenga.
 *  Sin coincidencia se usa la presentación default del producto — nunca se
 *  asume KILO, porque 5 CAJA registradas como 5 KILO son 95 kg de menos. */
function presentacionDe(unidad: string, cand?: LineaOC["candidatos"][number]): string {
  const u = (unidad || "").trim().toUpperCase();
  const norm = UNIDAD_ALIAS[u] ?? u;
  const mapa = cand?.presentaciones ?? {};
  const claves = Object.keys(mapa);
  const hit = claves.find((k) => k.toUpperCase() === norm);
  return hit ?? cand?.presentacion_default ?? claves[0] ?? "KILO";
}

/** Precio tecleado a la mexicana ("1,234.50" o "12,50") → decimal del backend. */
function precioNormalizado(v: string): string {
  const t = v.trim();
  if (!t) return "";
  if (t.includes(",") && !t.includes(".")) return t.replace(",", ".");
  return t.replace(/,/g, "");
}

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
  // A dónde se descarga (hospital, plantel). NO es la sucursal: es un punto
  // dentro de ella, y su texto sale impreso en la remisión y en la factura.
  const [puntoEntrega, setPuntoEntrega] = useState("");
  const [lineas, setLineas] = useState<LineaEdit[]>([]);
  const [guardando, setGuardando] = useState(false);
  const [aDescartar, setADescartar] = useState<OCRecibida | null>(null);
  // Con candidatos, el desplegable arranca acotado a ellos: elegir entre dos es
  // otra cosa que buscar entre todo el padrón.
  const [verTodos, setVerTodos] = useState(false);

  const reload = useCallback(() => {
    setError(false);          // un fallo transitorio no puede dejar la bandeja muerta
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
      setPuntoEntrega(oc.punto_entrega ?? "");
      setVerTodos(!oc.candidatos?.length);
      setAlmacenSel("");
      setLineas(
        oc.lineas.map((l) => {
          // Se preselecciona el mejor candidato solo si es un cruce fuerte
          // (exacto o alias ya confirmado). Un difuso al 76% lo revisa la persona.
          const fuerte = l.candidatos[0] && l.candidatos[0].score >= 96 ? l.candidatos[0] : undefined;
          const unidad = l.unidad ?? "";
          return {
            numero: l.numero,
            texto: l.descripcion,
            cantidad: String(l.cantidad ?? ""),
            unidad,
            producto_id: fuerte ? fuerte.producto_id : "",
            presentacion: presentacionDe(unidad, fuerte),
            precio: l.precio != null ? String(l.precio) : "",
            notas: l.notas ?? null,
            candidatos: l.candidatos,
            buscando: false,
          };
        })
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

  /** ¿El operador cambió cliente/sucursal y todavía no se ha guardado? */
  const sinGuardar = useMemo(
    () =>
      !!abierta &&
      (clienteSel !== (abierta.cliente_id ?? "") ||
        sucursalSel !== (abierta.sucursal_id ?? "") ||
        puntoEntrega.trim() !== (abierta.punto_entrega ?? "")),
    [abierta, clienteSel, sucursalSel, puntoEntrega]
  );

  /** Persiste cliente/sucursal. Devuelve la OC guardada, o null si falló. */
  async function guardarAsignacion(): Promise<OCRecibidaDetalle | null> {
    if (!abierta || !clienteSel) {
      toast.error("Elige el cliente");
      return null;
    }
    try {
      const oc = await apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${abierta.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          cliente_id: clienteSel,
          sucursal_id: sucursalSel || null,
          punto_entrega: puntoEntrega.trim() || null,
          aprender: true,
        }),
      });
      setAbierta(oc);
      return oc;
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo asignar");
      return null;
    }
  }

  async function asignar() {
    setGuardando(true);
    const oc = await guardarAsignacion();
    setGuardando(false);
    if (oc) {
      toast.success("Asignada — la próxima orden igual ya se resuelve sola");
      reload();
    }
  }

  const sinProducto = useMemo(() => lineas.filter((l) => !l.producto_id).length, [lineas]);
  // Ya tiene remisión, o está descartada: nada se edita desde la bandeja.
  const bloqueada = !!abierta && (!!abierta.remision_id || abierta.estado === "DESCARTADA");

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
      // La remisión se crea con el cliente y la sucursal PERSISTIDOS, no con los
      // del Select. Si el operador los cambió y no guardó, se guarda aquí antes
      // — si no, la remisión saldría a nombre del cliente anterior y con la serie
      // equivocada, quemando un folio que no se recupera.
      if (sinGuardar && !(await guardarAsignacion())) return;

      const oc = await apiFetch<OCRecibidaDetalle>(
        `/api/v1/oc-recibidas/${abierta.id}/crear-remision`,
        {
          method: "POST",
          body: JSON.stringify({
            almacen_id: almacenSel || null,
            lineas: lineas.map((l) => ({
              producto_id: l.producto_id,
              cantidad: l.cantidad,
              presentacion: l.presentacion,
              precio_unitario: precioNormalizado(l.precio) || null,
              notas: l.notas,
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
            {canWrite && abierta && !abierta.remision_id && abierta.estado === "DESCARTADA" ? (
              <Button onClick={() => reabrir(abierta).then(() => abrir(abierta.id))}>
                Reabrir
              </Button>
            ) : null}
            {canWrite && abierta && !abierta.remision_id && abierta.estado !== "DESCARTADA" ? (
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
            {abierta.estado === "DESCARTADA" ? (
              <Alert tone="warning">
                Orden descartada. Reábrela para poder asignarla — asignar desde aquí
                registraría equivalencias de un documento que ya se dio por bueno descartar.
              </Alert>
            ) : null}
            {abierta.ambiguo ? (
              <Alert tone="warning">
                <span className="inline-flex items-center gap-1.5">
                  <AlertTriangle size={15} /> {abierta.motivo}
                </span>
              </Alert>
            ) : abierta.candidatos?.length && !abierta.cliente_id ? (
              <Alert tone="info">{abierta.motivo}</Alert>
            ) : null}

            {abierta.remision_id ? (
              <Alert tone="success">
                Esta orden ya generó la remisión <strong>{abierta.remision_folio}</strong>. Los
                cambios de kilos, líneas y precios se hacen en la remisión.
              </Alert>
            ) : null}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field
                label="Cliente"
                hint={
                  abierta.candidatos?.length && !verTodos
                    ? "Los que usan este grupo de WhatsApp"
                    : "A quién se le factura"
                }
              >
                <Select
                  value={clienteSel}
                  onChange={(e) => {
                    if (e.target.value === "__todos__") {
                      setVerTodos(true);
                      return;
                    }
                    setClienteSel(e.target.value);
                    setSucursalSel("");
                  }}
                  disabled={!canWrite || bloqueada}
                >
                  <option value="">— Elegir —</option>
                  {(abierta.candidatos?.length && !verTodos
                    ? clientes.filter((c) => abierta.candidatos.includes(c.id))
                    : clientes
                  ).map((c) => (
                    <option key={c.id} value={c.id}>{c.legal_name}</option>
                  ))}
                  {abierta.candidatos?.length && !verTodos ? (
                    <option value="__todos__">Ver todos los clientes…</option>
                  ) : null}
                </Select>
              </Field>
              <Field label="Sucursal" hint="De aquí salen la serie y la lista de precios">
                <Select
                  value={sucursalSel}
                  onChange={(e) => setSucursalSel(e.target.value)}
                  disabled={!canWrite || !clienteSel || bloqueada}
                >
                  <option value="">— Sin sucursal —</option>
                  {sucursales.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.codigo ? `${s.codigo} · ${s.nombre}` : s.nombre}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Punto de entrega"
                hint="A dónde se descarga. Sale impreso en la remisión y en la factura."
              >
                <Input
                  value={puntoEntrega}
                  onChange={(e) => setPuntoEntrega(e.target.value)}
                  disabled={!canWrite || bloqueada}
                  placeholder="HOSPITAL JUAN GRAHAM"
                />
              </Field>
              <Field label="Almacén de salida">
                <Select
                  value={almacenSel}
                  onChange={(e) => setAlmacenSel(e.target.value)}
                  disabled={!canWrite || bloqueada}
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
                      <th className="px-3 py-2 text-left">Presentación</th>
                      <th className="px-3 py-2 text-right">Precio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lineas.map((l, i) => (
                      <tr key={l.numero} className="border-t border-border">
                        <td className="px-3 py-2">{l.texto}</td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {l.cantidad}
                          {l.unidad ? <span className="ml-1 text-xs text-muted">{l.unidad}</span> : null}
                        </td>
                        <td className="px-3 py-2">
                          {l.candidatos.length && !l.buscando ? (
                            <Select
                              value={l.producto_id}
                              disabled={!canWrite || bloqueada}
                              onChange={(e) => {
                                const v = e.target.value;
                                if (v === "__buscar__") {
                                  setLineas((prev) =>
                                    prev.map((x, j) =>
                                      j === i ? { ...x, buscando: true, producto_id: "" } : x
                                    )
                                  );
                                  return;
                                }
                                setLineas((prev) =>
                                  prev.map((x, j) =>
                                    j === i
                                      ? {
                                          ...x,
                                          producto_id: v,
                                          presentacion: presentacionDe(
                                            x.unidad,
                                            x.candidatos.find((c) => c.producto_id === v)
                                          ),
                                        }
                                      : x
                                  )
                                );
                              }}
                            >
                              <option value="">— Sin cruzar —</option>
                              {l.candidatos.map((c) => (
                                <option key={c.producto_id} value={c.producto_id}>
                                  {c.nombre} ({c.sku}) · {c.score}%
                                </option>
                              ))}
                              <option value="__buscar__">Buscar otro producto…</option>
                            </Select>
                          ) : (
                            // Sin candidatos, o el operador pidió buscar: el cruce
                            // difuso deja fuera productos que sí existen, y sin esto
                            // la orden se quedaba bloqueada para siempre.
                            <ProductoCombobox
                              label={l.texto}
                              placeholder="Buscar en el catálogo…"
                              onSelect={(prod) =>
                                setLineas((prev) =>
                                  prev.map((x, j) =>
                                    j === i
                                      ? {
                                          ...x,
                                          producto_id: prod ? prod.producto_id : "",
                                          presentacion: prod
                                            ? presentacionDe(x.unidad, {
                                                producto_id: prod.producto_id,
                                                sku: prod.sku,
                                                nombre: prod.nombre,
                                                score: 100,
                                                origen: "manual",
                                                presentaciones: prod.presentaciones,
                                                presentacion_default: prod.presentacion_default,
                                              })
                                            : x.presentacion,
                                        }
                                      : x
                                  )
                                )
                              }
                            />
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <span className="text-xs text-muted">{l.presentacion}</span>
                        </td>
                        <td className="px-3 py-2 text-right">
                          <Input
                            value={l.precio}
                            disabled={!canWrite || bloqueada}
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
                        <td colSpan={5} className="px-3 py-6 text-center text-sm text-muted">
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

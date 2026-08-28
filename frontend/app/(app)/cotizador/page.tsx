"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Calculator, Download, FileText, FileUp } from "lucide-react";

import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiDownloadPost, apiFetch } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import type { Cliente, Cotizacion, Proyecto, Serie, Sucursal } from "@/lib/types";

// Espejo de los orígenes que devuelve el resolutor (services/precios.py). Los
// de lista dicen POR QUÉ dimensión ganó la asignación, que es lo que el
// vendedor necesita saber para defender el precio por teléfono.
const ORIGEN_LABEL: Record<string, string> = {
  override_sucursal: "Precio especial de la sucursal",
  override_cliente: "Precio especial del cliente",
  lista_forzada: "Lista forzada en el documento",
  lista_proyecto: "Lista asignada al proyecto",
  lista_serie: "Lista asignada a la serie",
  lista_sucursal: "Lista asignada a la sucursal",
  lista_cliente: "Lista del cliente (global)",
  lista_base: "Lista base (público)",
};

// Lo que devuelve POST /precios/cotizar-documento (services/cotizador.py).
type LineaCot = {
  descripcion: string;
  cantidad: string;
  unidad: string | null;
  clave: string | null;
  producto_id: string;
  producto_nombre: string;
  sku: string | null;
  presentacion: string;
  cruce: string | null;
  precio_unitario: string | null;
  importe: string | null;
  origen_precio: string | null;
  iva_importe?: string;
  ieps_importe?: string;
};
type SinCruce = {
  descripcion: string;
  cantidad: string;
  unidad: string | null;
  clave: string | null;
  candidatos: { nombre: string; score: number }[];
};
type CotDoc = {
  cliente_id: string;
  cliente_nombre: string;
  archivo: string;
  lineas: LineaCot[];
  sin_cruce: SinCruce[];
  sin_precio: number;
  subtotal: string;
  iva: string;
  ieps: string;
  total: string;
};
type ListaDelCliente = { lista_id: string; nombre: string; alcance: string };

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.csv";

export default function CotizadorPage() {
  const toast = useToast();

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=200");
  const clientes = clientesRes.data?.items ?? [];

  const [clienteId, setClienteId] = useState("");
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  const [sucursalId, setSucursalId] = useState("");

  const seriesRes = useResource<Page<Serie>>("/api/v1/series?limit=200");
  const proyectosRes = useResource<Page<Proyecto>>("/api/v1/proyectos?activo=true&limit=500");
  const series = seriesRes.data?.items ?? [];
  // Los del cliente elegido + los del grupo (sin dueño), que aplican a todos.
  const proyectos = (proyectosRes.data?.items ?? []).filter(
    (p) => !p.cliente_id || !clienteId || p.cliente_id === clienteId,
  );
  const [serieId, setSerieId] = useState("");
  const [proyectoId, setProyectoId] = useState("");

  // ── Documento ──────────────────────────────────────────────────────────
  const [archivo, setArchivo] = useState<File | null>(null);
  const [cotizando, setCotizando] = useState(false);
  const [cotDoc, setCotDoc] = useState<CotDoc | null>(null);

  // ── Un producto ────────────────────────────────────────────────────────
  const [prod, setProd] = useState<ProductoPick | null>(null);
  const [presentacion, setPresentacion] = useState("");
  const [cantidad, setCantidad] = useState("1");
  const [cotRes, setCotRes] = useState<Cotizacion | null>(null);

  // ── Listas del cliente ─────────────────────────────────────────────────
  const [listas, setListas] = useState<ListaDelCliente[] | null>(null);

  // Presentaciones válidas del producto elegido, con su default al frente. El
  // precio se guarda por presentación, así que cotizar con una que no existe
  // ("KILO" en un producto que se vende por "PIEZA") no resuelve precio.
  const presentaciones = useMemo(() => {
    if (!prod) return [];
    const keys = Object.keys(prod.presentaciones ?? {});
    const def = prod.presentacion_default ?? prod.unidad_base ?? "";
    return def && keys.includes(def) ? [def, ...keys.filter((k) => k !== def)] : keys;
  }, [prod]);

  // Carga las sucursales y las listas del cliente elegido.
  useEffect(() => {
    setSucursalId("");
    setProyectoId("");
    setCotDoc(null);
    setListas(null);
    if (!clienteId) {
      setSucursales([]);
      return;
    }
    let active = true;
    (async () => {
      try {
        const [s, l] = await Promise.all([
          apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${clienteId}&limit=200`),
          apiFetch<{ listas: ListaDelCliente[] }>(
            `/api/v1/precios/listas-del-cliente?cliente_id=${clienteId}`,
          ),
        ]);
        if (active) {
          setSucursales(s.items);
          setListas(l.listas);
        }
      } catch (e) {
        if (active) toast.error(e instanceof ApiError ? e.message : "No se pudo cargar el cliente");
      }
    })();
    return () => {
      active = false;
    };
  }, [clienteId]); // eslint-disable-line react-hooks/exhaustive-deps

  function onPickProducto(p: ProductoPick | null) {
    setProd(p);
    setCotRes(null);
    const def = p?.presentacion_default ?? p?.unidad_base ?? "";
    const keys = Object.keys(p?.presentaciones ?? {});
    setPresentacion(def && keys.includes(def) ? def : keys[0] ?? "");
  }

  async function cotizarDocumento() {
    if (!clienteId) {
      toast.error("Elige el cliente: la cotización sale con SUS precios");
      return;
    }
    if (!archivo) {
      toast.error("Sube la orden (PDF, foto o Excel)");
      return;
    }
    const fd = new FormData();
    fd.set("archivo", archivo);
    fd.set("cliente_id", clienteId);
    if (sucursalId) fd.set("sucursal_id", sucursalId);
    if (serieId) fd.set("serie_id", serieId);
    if (proyectoId) fd.set("proyecto_id", proyectoId);
    setCotizando(true);
    setCotDoc(null);
    try {
      // La IA lee el documento completo: puede tardar más que el timeout normal.
      const r = await apiFetch<CotDoc>(
        "/api/v1/precios/cotizar-documento",
        { method: "POST", body: fd },
        { timeoutMs: 240_000 },
      );
      setCotDoc(r);
      if (r.lineas.length === 0 && r.sin_cruce.length === 0) {
        toast.error("No se encontraron partidas en el documento");
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cotizar el documento");
    } finally {
      setCotizando(false);
    }
  }

  async function descargarPdfCotizacion() {
    if (!cotDoc) return;
    try {
      await apiDownloadPost("/api/v1/precios/cotizacion-pdf", cotDoc, "cotizacion.pdf");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo generar el PDF");
    }
  }

  async function cotizar() {
    if (!prod) {
      toast.error("Elige un producto");
      return;
    }
    const p = new URLSearchParams({
      producto_id: prod.producto_id,
      presentacion: presentacion || "KILO",
      cantidad: cantidad || "1",
    });
    // Se mandan TODAS las dimensiones que se hayan elegido: el resolutor las
    // combina (no son excluyentes) y gana la asignación más específica.
    if (clienteId) p.set("cliente_id", clienteId);
    if (sucursalId) p.set("sucursal_id", sucursalId);
    if (serieId) p.set("serie_id", serieId);
    if (proyectoId) p.set("proyecto_id", proyectoId);
    try {
      setCotRes(await apiFetch<Cotizacion>(`/api/v1/precios/cotizar?${p.toString()}`));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cotizar");
    }
  }

  async function bajarLista(l: ListaDelCliente, fmt: "pdf" | "export") {
    try {
      await apiDownload(
        `/api/v1/listas-precios/${l.lista_id}/${fmt}`,
        `${l.nombre}.${fmt === "pdf" ? "pdf" : "xlsx"}`,
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descargar la lista");
    }
  }

  const tabDocumento = (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm text-muted">
        Sube tu <b>orden de compra o pedido</b> (PDF, foto o Excel) y te regresamos la{" "}
        <b>cotización completa</b> con los precios negociados — igual que el cotizador de WhatsApp.
      </div>
      <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <Field label="Documento (PDF, foto o Excel)">
            <input
              type="file"
              accept={ACCEPT}
              onChange={(e) => {
                setArchivo(e.target.files?.[0] ?? null);
                setCotDoc(null);
              }}
              className="block w-full cursor-pointer rounded-lg border border-border bg-background text-sm file:mr-3 file:cursor-pointer file:rounded-l-lg file:border-0 file:bg-surface-2 file:px-3.5 file:py-2 file:text-sm file:font-medium"
            />
          </Field>
        </div>
        <div>
          <Button onClick={() => void cotizarDocumento()} disabled={!archivo || !clienteId || cotizando}>
            {cotizando ? <Spinner className="h-4 w-4" /> : <FileUp size={16} />}
            {cotizando ? "Leyendo y cotizando…" : "Cotizar documento"}
          </Button>
        </div>
      </div>

      {cotDoc && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm">
              <b>{cotDoc.cliente_nombre}</b> · {cotDoc.archivo} · {cotDoc.lineas.length} partida
              {cotDoc.lineas.length === 1 ? "" : "s"}
            </div>
            <Button variant="secondary" onClick={() => void descargarPdfCotizacion()}>
              <FileText size={16} /> Descargar PDF
            </Button>
          </div>

          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-3 py-2">Producto</th>
                  <th className="px-3 py-2">Lo que decía el documento</th>
                  <th className="px-3 py-2 text-right">Cantidad</th>
                  <th className="px-3 py-2">Presentación</th>
                  <th className="px-3 py-2 text-right">Precio</th>
                  <th className="px-3 py-2 text-right">Importe</th>
                </tr>
              </thead>
              <tbody>
                {cotDoc.lineas.map((l, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="px-3 py-2">
                      <div className="font-medium">{l.producto_nombre}</div>
                      {l.origen_precio && (
                        <div className="text-xs text-muted">
                          {ORIGEN_LABEL[l.origen_precio] ?? l.origen_precio}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted">
                      {l.descripcion}
                      {l.clave ? ` · ${l.clave}` : ""}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{l.cantidad}</td>
                    <td className="px-3 py-2">{l.presentacion}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {l.precio_unitario != null ? (
                        fmtMoney(l.precio_unitario)
                      ) : (
                        <span className="text-danger">Sin precio</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {l.importe != null ? fmtMoney(l.importe) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-border bg-surface-2 text-sm">
                <tr>
                  <td colSpan={4} />
                  <td className="px-3 py-1.5 text-right text-muted">Subtotal</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(cotDoc.subtotal)}</td>
                </tr>
                <tr>
                  <td colSpan={4} />
                  <td className="px-3 py-1.5 text-right text-muted">IVA</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(cotDoc.iva)}</td>
                </tr>
                {Number(cotDoc.ieps) > 0 && (
                  <tr>
                    <td colSpan={4} />
                    <td className="px-3 py-1.5 text-right text-muted">IEPS</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(cotDoc.ieps)}</td>
                  </tr>
                )}
                <tr className="font-semibold">
                  <td colSpan={4} />
                  <td className="px-3 py-2 text-right">Total</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(cotDoc.total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          {cotDoc.sin_precio > 0 && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30 p-3 text-sm">
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-600" />
              <div>
                {cotDoc.sin_precio} partida{cotDoc.sin_precio === 1 ? "" : "s"} sin precio en las
                listas de este cliente: no entran al total ni al PDF.
              </div>
            </div>
          )}

          {cotDoc.sin_cruce.length > 0 && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30 p-3 text-sm">
              <div className="mb-1 flex items-center gap-2 font-medium">
                <AlertTriangle size={16} className="text-amber-600" />
                {cotDoc.sin_cruce.length} renglón{cotDoc.sin_cruce.length === 1 ? "" : "es"} que no
                pudimos identificar con certeza (no se cotizaron):
              </div>
              <ul className="ml-5 list-disc space-y-0.5">
                {cotDoc.sin_cruce.map((s, i) => (
                  <li key={i}>
                    <b>{s.descripcion}</b> — {s.cantidad} {s.unidad ?? ""}
                    {s.candidatos.length > 0 && (
                      <span className="text-muted">
                        {" "}
                        · ¿quizá {s.candidatos.map((c) => c.nombre).join(", ")}?
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );

  const tabProducto = (
    <div className="max-w-3xl space-y-3">
      <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-4">
        <div className="sm:col-span-2">
          <label className="mb-1 block text-sm font-medium text-foreground">Producto</label>
          <ProductoCombobox onSelect={onPickProducto} />
        </div>
        <Field label="Presentación">
          <Select
            value={presentacion}
            onChange={(e) => setPresentacion(e.target.value)}
            disabled={!prod}
          >
            {presentaciones.length === 0 && <option value="">—</option>}
            {presentaciones.map((pr) => (
              <option key={pr} value={pr}>
                {pr}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Cantidad">
          <Input type="number" value={cantidad} onChange={(e) => setCantidad(e.target.value)} />
        </Field>
      </div>

      <Button onClick={cotizar} disabled={!prod}>
        <Calculator size={16} /> Cotizar
      </Button>

      {cotRes && (
        <div className="rounded-lg bg-surface-2 px-4 py-3 text-sm">
          {cotRes.precio != null ? (
            <>
              Precio: <b>{fmtMoney(cotRes.precio)}</b> · origen:{" "}
              <b>{ORIGEN_LABEL[cotRes.origen ?? ""] ?? cotRes.origen ?? "—"}</b>
            </>
          ) : (
            <span className="text-danger">Sin precio resoluble (configura una lista u override).</span>
          )}
        </div>
      )}
    </div>
  );

  const tabListas = (
    <div className="max-w-3xl space-y-3">
      {!clienteId ? (
        <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm text-muted">
          Elige un cliente arriba para ver sus listas de precios.
        </div>
      ) : listas == null ? (
        <Spinner className="h-5 w-5" />
      ) : listas.length === 0 ? (
        <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm text-muted">
          Este cliente no tiene listas de precios asignadas.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs uppercase text-muted">
              <tr>
                <th className="px-3 py-2">Lista</th>
                <th className="px-3 py-2">Aplica a</th>
                <th className="px-3 py-2 text-right">Descargar</th>
              </tr>
            </thead>
            <tbody>
              {listas.map((l) => (
                <tr key={l.lista_id} className="border-t border-border">
                  <td className="px-3 py-2 font-medium">{l.nombre}</td>
                  <td className="px-3 py-2 text-muted">{l.alcance}</td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" className="px-2.5 py-1" onClick={() => void bajarLista(l, "pdf")}>
                        <FileText size={14} /> PDF
                      </Button>
                      <Button variant="secondary" className="px-2.5 py-1" onClick={() => void bajarLista(l, "export")}>
                        <Download size={14} /> Excel
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  return (
    <div>
      <PageHeader
        title="Cotizador"
        subtitle="Sube tu orden y te la cotizamos completa con tus precios; o consulta un producto o descarga tu lista."
      />

      {/* Las dimensiones de la negociación aplican a las tres pestañas. */}
      <section className="mb-4 max-w-5xl rounded-xl border border-border p-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <Field label="Cliente">
            <Select
              value={clienteId}
              onChange={(e) => {
                setClienteId(e.target.value);
                setCotRes(null);
              }}
            >
              <option value="">— Sin cliente (lista base) —</option>
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_name}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Sucursal">
            <Select
              value={sucursalId}
              onChange={(e) => {
                setSucursalId(e.target.value);
                setCotRes(null);
              }}
              disabled={!clienteId || sucursales.length === 0}
            >
              <option value="">(usa el cliente)</option>
              {sucursales.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.nombre}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Serie">
            <Select
              value={serieId}
              onChange={(e) => {
                setSerieId(e.target.value);
                setCotRes(null);
              }}
            >
              <option value="">(cualquiera)</option>
              {series.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.codigo} · {s.tipo_documento}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Proyecto">
            <Select
              value={proyectoId}
              onChange={(e) => {
                setProyectoId(e.target.value);
                setCotRes(null);
              }}
            >
              <option value="">(cualquiera)</option>
              {proyectos.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.nombre}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </section>

      <Tabs
        tabs={[
          { id: "documento", label: "Cotizar un documento", content: tabDocumento },
          { id: "producto", label: "Cotizar un producto", content: tabProducto },
          { id: "listas", label: "Lista de precios", content: tabListas },
        ]}
      />
    </div>
  );
}

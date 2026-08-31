"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Calculator, Download, FileText, FileUp } from "lucide-react";

import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
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

// Lo que devuelve POST /precios/cotizar-requisicion (services/cotizador.py):
// el flujo del bot de WhatsApp — el resultado ES el PDF, listo para verse y
// descargarse; los demás campos son para el encabezado y los avisos.
type CotReq = {
  cliente_id: string;
  cliente_nombre: string;
  archivo: string;
  folio: string;
  fecha: string;
  lineas: number;
  alarma: string;
  warnings: string[];
  total: string;
  pdf_filename: string;
  pdf_base64: string;
};
type ListaDelCliente = { lista_id: string; nombre: string; alcance: string };

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.csv";

// Lo que devuelve GET /precios/productos-cotizables (mismo shape que ProductoPick).
type ProductoCotizable = {
  producto_id: string;
  sku: string;
  nombre: string;
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
  unidad_base?: string | null;
};

const INPUT_BASE =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60";

/**
 * Buscador acotado a la lista de precios del cliente: los usuarios de portal
 * (cliente_scope) no deben ver el catálogo completo ni precios ajenos, así que
 * en vez del ProductoCombobox general se consulta productos-cotizables.
 */
function CotizableCombobox({
  clienteId,
  onSelect,
}: {
  clienteId: string;
  onSelect: (p: ProductoPick | null) => void;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ProductoCotizable[]>([]);
  const [loading, setLoading] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => setHi(0), [items]);

  useEffect(() => {
    if (!open) return;
    const t = q.trim();
    if (t.length < 1) {
      setItems([]);
      return;
    }
    let active = true;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const r = await apiFetch<{ limitado: boolean; items: ProductoCotizable[] }>(
          `/api/v1/precios/productos-cotizables?cliente_id=${clienteId}&q=${encodeURIComponent(t)}&limit=10`,
        );
        if (active) setItems(r.items);
      } catch {
        if (active) setItems([]);
      } finally {
        if (active) setLoading(false);
      }
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [q, open, clienteId]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function pick(it: ProductoCotizable) {
    onSelect({
      producto_id: it.producto_id,
      sku: it.sku,
      nombre: it.nombre,
      presentaciones: it.presentaciones,
      presentacion_default: it.presentacion_default,
      unidad_base: it.unidad_base,
    });
    setQ(it.nombre);
    setOpen(false);
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        className={INPUT_BASE}
        aria-label="Buscar producto en la lista del cliente"
        value={q}
        placeholder="Buscar en la lista del cliente…"
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          onSelect(null); // limpia la selección mientras escribe
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setHi((h) => Math.min(h + 1, Math.max(items.length - 1, 0)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setHi((h) => Math.max(h - 1, 0));
          } else if (e.key === "Enter") {
            // Con búsqueda en vuelo, items es de la consulta ANTERIOR: elegir
            // con Enter tomaría un producto que no corresponde a lo tecleado.
            if (!loading && items[hi]) {
              e.preventDefault();
              pick(items[hi]);
            }
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q.trim().length >= 1 && (
        <div className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-border bg-surface shadow-lg">
          {loading && <div className="px-3 py-2 text-sm text-muted">Buscando…</div>}
          {!loading && items.length === 0 && (
            <div className="px-3 py-2 text-sm text-muted">
              Sin coincidencias en la lista de precios del cliente.
            </div>
          )}
          {!loading &&
            items.map((it, i) => (
              <button
                key={it.producto_id}
                type="button"
                onClick={() => pick(it)}
                onMouseEnter={() => setHi(i)}
                className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                  i === hi ? "bg-accent/10" : "hover:bg-surface-2"
                }`}
              >
                <span>
                  <span className="font-medium">{it.nombre}</span>
                  <span className="ml-2 text-xs text-muted">{it.sku}</span>
                </span>
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

export default function CotizadorPage() {
  const toast = useToast();
  const { me } = useAuth();

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
  const [cotReq, setCotReq] = useState<CotReq | null>(null);

  // El PDF llega en base64; para el <iframe> y la descarga se vuelve blob URL
  // (y se libera al cambiar de resultado).
  const pdfUrl = useMemo(() => {
    if (!cotReq) return null;
    const bytes = Uint8Array.from(atob(cotReq.pdf_base64), (ch) => ch.charCodeAt(0));
    return URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
  }, [cotReq]);
  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

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
    setCotReq(null);
    setListas(null);
    // El producto elegido puede no estar en la lista del nuevo cliente: se
    // descarta junto con su cotización para no enseñar un precio ajeno.
    setProd(null);
    setPresentacion("");
    setCotRes(null);
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
        if (active) {
          toast.error(e instanceof ApiError ? e.message : "No se pudo cargar el cliente");
          // null significa "cargando": si se quedara así, la pestaña de listas
          // enseñaría un spinner eterno tras el error.
          setListas([]);
        }
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
    if (!archivo) {
      toast.error("Sube la requisición (PDF, foto o Excel)");
      return;
    }
    const fd = new FormData();
    fd.set("archivo", archivo);
    // El cliente se detecta del propio documento (RFC/nombre); elegirlo aquí
    // solo lo fuerza — igual que el bot, que sabe de qué grupo llegó.
    if (clienteId) fd.set("cliente_id", clienteId);
    if (sucursalId) fd.set("sucursal_id", sucursalId);
    if (serieId) fd.set("serie_id", serieId);
    if (proyectoId) fd.set("proyecto_id", proyectoId);
    setCotizando(true);
    setCotReq(null);
    try {
      // Si el PDF viene en un acomodo raro entra la IA: puede tardar.
      const r = await apiFetch<CotReq>(
        "/api/v1/precios/cotizar-requisicion",
        { method: "POST", body: fd },
        { timeoutMs: 240_000 },
      );
      setCotReq(r);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cotizar el documento");
    } finally {
      setCotizando(false);
    }
  }

  function descargarPdfRequisicion() {
    if (!cotReq || !pdfUrl) return;
    const a = document.createElement("a");
    a.href = pdfUrl;
    a.download = cotReq.pdf_filename;
    a.click();
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
        Sube la <b>requisición del cliente</b> (PDF, foto o Excel) y te regresamos el{" "}
        <b>mismo PDF que manda el cotizador de WhatsApp</b>: precio validado partida por
        partida, con sus notas en rojo — y aquí lo puedes descargar.
      </div>
      <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <Field label="Requisición (PDF, foto o Excel)">
            <input
              type="file"
              accept={ACCEPT}
              onChange={(e) => {
                setArchivo(e.target.files?.[0] ?? null);
                setCotReq(null);
              }}
              className="block w-full cursor-pointer rounded-lg border border-border bg-background text-sm file:mr-3 file:cursor-pointer file:rounded-l-lg file:border-0 file:bg-surface-2 file:px-3.5 file:py-2 file:text-sm file:font-medium"
            />
          </Field>
        </div>
        <div>
          <Button onClick={() => void cotizarDocumento()} disabled={!archivo || cotizando}>
            {cotizando ? <Spinner className="h-4 w-4" /> : <FileUp size={16} />}
            {cotizando ? "Leyendo y cotizando…" : "Cotizar requisición"}
          </Button>
        </div>
      </div>

      {cotReq && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm">
              ✅ Requisición <b>{cotReq.folio}</b> · <b>{cotReq.cliente_nombre}</b> ·{" "}
              {cotReq.lineas} línea{cotReq.lineas === 1 ? "" : "s"} · Total{" "}
              {fmtMoney(cotReq.total)}
            </div>
            <Button variant="secondary" onClick={descargarPdfRequisicion}>
              <Download size={16} /> Descargar PDF
            </Button>
          </div>

          {cotReq.warnings.length > 0 && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30 p-3 text-sm">
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-600" />
              <ul className="space-y-0.5">
                {cotReq.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* La pantalla enseña EXACTAMENTE el PDF (pedido del dueño): lo que
              ve el operador es lo que recibiría el cliente por WhatsApp. */}
          {pdfUrl && (
            <iframe
              src={pdfUrl}
              title={cotReq.pdf_filename}
              className="h-[75vh] w-full rounded-xl border border-border bg-white"
            />
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
          {clienteId ? (
            <>
              {/* key: al cambiar de cliente el combobox se reinicia (texto y resultados). */}
              <CotizableCombobox key={clienteId} clienteId={clienteId} onSelect={onPickProducto} />
              <span className="mt-1 block text-xs text-muted">
                Solo productos de la lista de precios del cliente.
              </span>
            </>
          ) : can(me, "menu:productos") ? (
            <ProductoCombobox onSelect={onPickProducto} />
          ) : (
            <div className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-muted">
              Elige un cliente para buscar en su lista de precios.
            </div>
          )}
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
        subtitle="Sube la requisición y sale el mismo PDF que el cotizador de WhatsApp, listo para descargar; o consulta un producto o descarga tu lista."
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

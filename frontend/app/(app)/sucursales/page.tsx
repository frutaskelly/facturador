"use client";

import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtMoney } from "@/lib/format";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import type { Almacen, Cliente, ListaAsignacion, PrecioOverride, Producto, Serie, Sucursal } from "@/lib/types";

// Permisos de escritura (igual que la versión previa de esta página):
//  - alta/baja de sucursales -> cliente:gestionar
//  - precios especiales (overrides) -> lista_precios:gestionar
const WRITE_SUC = "cliente:gestionar";
const WRITE_OVR = "lista_precios:gestionar";

export default function SucursalesPage() {
  // ?cliente=<id> — se llega aquí desde la lista de Clientes ("Sucursales"):
  // se muestra solo ese cliente para agregarle sucursales sin buscarlo.
  const clienteParam =
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("cliente")
      : null;

  const { me } = useAuth();
  const toast = useToast();
  const canSuc = can(me, WRITE_SUC);
  const canOvr = can(me, WRITE_OVR);
  const { post, del } = useMutation();

  // ── catálogos (una sola carga) ──
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=500");
  const sucursalesRes = useResource<Page<Sucursal>>("/api/v1/sucursales?limit=1000");
  const productosRes = useResource<Page<Producto>>("/api/v1/productos?limit=1000");
  // Qué lista le toca a cada quien ya no es una columna del cliente ni de la
  // sucursal: son renglones de asignación. Se leen aquí solo para MOSTRARLOS
  // (se editan en Asignación de precios).
  const asignacionesRes = useResource<Page<ListaAsignacion>>("/api/v1/asignaciones-precios?limit=1000");
  const almacenesRes = useResource<Page<Almacen>>("/api/v1/almacenes?limit=200");
  const seriesFacRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=FACTURA&activa=true&limit=200");
  const seriesRemRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=REMISION&activa=true&limit=200");

  const todosLosClientes = clientesRes.data?.items ?? [];
  const clientes = clienteParam
    ? todosLosClientes.filter((c) => c.id === clienteParam)
    : todosLosClientes;
  const allSucursales = sucursalesRes.data?.items ?? [];
  const productos = productosRes.data?.items ?? [];
  const asignaciones = asignacionesRes.data?.items ?? [];
  const almacenes = almacenesRes.data?.items ?? [];
  const seriesFac = seriesFacRes.data?.items ?? [];
  const seriesRem = seriesRemRes.data?.items ?? [];

  const prodName = useMemo(() => Object.fromEntries(productos.map((p) => [p.id, p.nombre])), [productos]);
  const prodById = useMemo(() => Object.fromEntries(productos.map((p) => [p.id, p])), [productos]);
  // Lista asignada a UNA sucursal (sin serie ni proyecto de por medio): es lo
  // único que esta pantalla puede afirmar sin simular la resolución completa.
  const listaDeSucursal = useMemo(() => {
    const m: Record<string, string> = {};
    for (const a of asignaciones) {
      if (a.sucursal_id && !a.serie_id && !a.proyecto_id && a.lista_nombre) {
        m[a.sucursal_id] = a.lista_nombre;
      }
    }
    return m;
  }, [asignaciones]);
  // serie_factura_id -> codigo (para la columna "Serie" del cliente)
  const serieCodigo = useMemo(
    () => Object.fromEntries([...seriesFac, ...seriesRem].map((s) => [s.id, s.codigo])),
    [seriesFac, seriesRem],
  );

  // # sucursales por cliente (agrupando la carga única de sucursales)
  const sucPorCliente = useMemo(() => {
    const m: Record<string, number> = {};
    for (const s of allSucursales) m[s.cliente_id] = (m[s.cliente_id] ?? 0) + 1;
    return m;
  }, [allSucursales]);

  // ── detalle por cliente, cargado bajo demanda al expandir la fila ──
  type Detalle = { sucursales: Sucursal[]; overrides: PrecioOverride[]; loading: boolean };
  const [detalle, setDetalle] = useState<Record<string, Detalle>>({});

  async function loadDetalle(clienteId: string) {
    setDetalle((d) => ({ ...d, [clienteId]: { sucursales: d[clienteId]?.sucursales ?? [], overrides: d[clienteId]?.overrides ?? [], loading: true } }));
    try {
      const [sucs, ovrs] = await Promise.all([
        apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${clienteId}&limit=200`),
        apiFetch<Page<PrecioOverride>>(`/api/v1/precios/overrides?cliente_id=${clienteId}&limit=200`),
      ]);
      setDetalle((d) => ({ ...d, [clienteId]: { sucursales: sucs.items, overrides: ovrs.items, loading: false } }));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cargar el detalle del cliente");
      setDetalle((d) => ({ ...d, [clienteId]: { sucursales: [], overrides: [], loading: false } }));
    }
  }

  function reloadDetalle(clienteId: string) {
    loadDetalle(clienteId);
    sucursalesRes.reload(); // refresca el conteo "# Sucursales"
  }

  // ── modales de alta ──
  const emptySuc = { nombre: "", contacto: "", telefono: "", serie_factura_id: "", serie_remision_id: "", almacen_id: "", series_factura_ids: [] as string[], series_remision_ids: [] as string[] };
  const [sucModal, setSucModal] = useState<{ clienteId: string } | null>(null);
  const [nuevaSuc, setNuevaSuc] = useState(emptySuc);
  // "Basarse en una existente": el mismo punto de entrega suele repetirse entre
  // razones sociales hermanas (Balles y Jubran comparten Pachuca) — elegirla
  // PREFILLEA nombre/contacto/series/almacén y solo queda crear.
  const cliName = useMemo(() => {
    const map: Record<string, string> = {};
    for (const c of clientes) map[c.id] = c.legal_name;
    return map;
  }, [clientes]);
  const [todasSucs, setTodasSucs] = useState<Sucursal[]>([]);
  const [baseSuc, setBaseSuc] = useState("");
  useEffect(() => {
    apiFetch<Page<Sucursal>>("/api/v1/sucursales?limit=1000")
      .then((p) => setTodasSucs(p.items))
      .catch(() => undefined);
  }, []);
  function basarseEn(id: string) {
    setBaseSuc(id);
    const s = todasSucs.find((x) => x.id === id);
    if (!s) return;
    setNuevaSuc({
      nombre: s.nombre,
      contacto: s.contacto ?? "",
      telefono: s.telefono ?? "",
      serie_factura_id: s.serie_factura_id ?? "",
      serie_remision_id: s.serie_remision_id ?? "",
      almacen_id: s.almacen_id ?? "",
      series_factura_ids: (s.series_factura_ids ?? []) as string[],
      series_remision_ids: (s.series_remision_ids ?? []) as string[],
    });
  }

  const emptyOvr = { producto_id: "", presentacion: "", sucursal_id: "", precio_unitario: "" };
  const [ovrModal, setOvrModal] = useState<{ clienteId: string } | null>(null);
  const [nuevoOvr, setNuevoOvr] = useState(emptyOvr);

  function presentacionesDe(productoId: string): string[] {
    const p = prodById[productoId];
    if (!p) return [];
    const keys = Object.keys(p.presentaciones ?? {});
    const def = p.presentacion_default ?? p.unidad_base;
    return def && keys.includes(def) ? [def, ...keys.filter((k) => k !== def)] : keys;
  }

  function openSucModal(clienteId: string) {
    setNuevaSuc(emptySuc);
    setBaseSuc("");
    setSucModal({ clienteId });
  }
  function openOvrModal(clienteId: string) {
    setNuevoOvr(emptyOvr);
    setOvrModal({ clienteId });
  }

  async function createSucursal() {
    if (!sucModal) return;
    if (!nuevaSuc.nombre.trim()) { toast.error("El nombre de la sucursal es obligatorio"); return; }
    try {
      // codigo se autogenera en el backend (SUC-01, …): NO se envía.
      await post("/api/v1/sucursales", {
        cliente_id: sucModal.clienteId,
        nombre: nuevaSuc.nombre.trim(),
        contacto: nuevaSuc.contacto.trim() || null,
        telefono: nuevaSuc.telefono.trim() || null,
        serie_factura_id: nuevaSuc.serie_factura_id || null,
        serie_remision_id: nuevaSuc.serie_remision_id || null,
        series_factura_ids: nuevaSuc.series_factura_ids,
        series_remision_ids: nuevaSuc.series_remision_ids,
        almacen_id: nuevaSuc.almacen_id || null,
      });
      toast.success("Sucursal creada");
      reloadDetalle(sucModal.clienteId);
      setSucModal(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la sucursal");
    }
  }

  async function delSucursal(clienteId: string, s: Sucursal) {
    try {
      await del(`/api/v1/sucursales/${s.id}`);
      toast.success("Sucursal eliminada");
      reloadDetalle(clienteId);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  async function createOverride() {
    if (!ovrModal) return;
    if (!nuevoOvr.producto_id || !nuevoOvr.precio_unitario) { toast.error("Elige producto y precio"); return; }
    const body: Record<string, unknown> = {
      producto_id: nuevoOvr.producto_id,
      presentacion: nuevoOvr.presentacion.trim() || "KILO",
      precio_unitario: nuevoOvr.precio_unitario,
    };
    // El backend exige exactamente uno: cliente_id XOR sucursal_id.
    if (nuevoOvr.sucursal_id) body.sucursal_id = nuevoOvr.sucursal_id;
    else body.cliente_id = ovrModal.clienteId;
    try {
      await post("/api/v1/precios/overrides", body);
      toast.success("Precio especial agregado");
      reloadDetalle(ovrModal.clienteId);
      setOvrModal(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo agregar el precio especial");
    }
  }

  async function delOverride(clienteId: string, o: PrecioOverride) {
    try {
      await del(`/api/v1/precios/overrides/${o.id}`);
      toast.success("Precio especial eliminado");
      reloadDetalle(clienteId);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  // ── columnas de la tabla principal (clientes) ──
  const cols: Column<Cliente>[] = [
    { header: "Razón social", cell: (c) => <span className="font-medium">{c.legal_name}</span>, sortable: true, sortValue: (c) => c.legal_name },
    {
      header: "# Sucursales",
      className: "text-right tabular-nums",
      cell: (c) => sucPorCliente[c.id] ?? 0,
      sortable: true,
      sortValue: (c) => sucPorCliente[c.id] ?? 0,
    },
    {
      header: "Serie",
      cell: (c) => (c.serie_factura_id && serieCodigo[c.serie_factura_id] ? <Badge tone="muted">{serieCodigo[c.serie_factura_id]}</Badge> : "—"),
      sortValue: (c) => (c.serie_factura_id ? serieCodigo[c.serie_factura_id] ?? "" : ""),
    },
  ];

  // ── panel expandible por cliente ──
  function ExpandedCliente({ cliente }: { cliente: Cliente }) {
    const d = detalle[cliente.id];
    const sucs = d?.sucursales ?? [];
    const ovrs = d?.overrides ?? [];

    const sucCols: Column<Sucursal>[] = [
      { header: "Sucursal", cell: (s) => <span className="font-medium">{s.nombre}</span> },
      { header: "Código", cell: (s) => s.codigo ?? "—" },
      { header: "Contacto", cell: (s) => s.contacto ?? "—" },
      { header: "Teléfono", cell: (s) => s.telefono ?? "—" },
      { header: "Lista propia", cell: (s) => listaDeSucursal[s.id] ?? "(hereda del cliente)" },
      ...(canSuc
        ? [{
            header: "", className: "text-right w-1",
            cell: (s: Sucursal) => (
              <button onClick={() => delSucursal(cliente.id, s)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Eliminar sucursal"><Trash2 size={16} /></button>
            ),
          }]
        : []),
    ];

    const ovrCols: Column<PrecioOverride>[] = [
      { header: "Producto", cell: (o) => prodName[o.producto_id] ?? o.producto_id },
      { header: "Present.", cell: (o) => o.presentacion },
      { header: "Alcance", cell: (o) => (o.sucursal_id ? `Solo ${sucName(sucs, o.sucursal_id)}` : "Todo el cliente") },
      { header: "Precio", className: "text-right tabular-nums", cell: (o) => fmtMoney(o.precio_unitario) },
      ...(canOvr
        ? [{
            header: "", className: "text-right w-1",
            cell: (o: PrecioOverride) => (
              <button onClick={() => delOverride(cliente.id, o)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Eliminar precio especial"><Trash2 size={16} /></button>
            ),
          }]
        : []),
    ];

    return (
      <div className="space-y-5 pt-2">
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Sucursales</h3>
            {canSuc && (
              <Button variant="secondary" onClick={() => openSucModal(cliente.id)}><Plus size={15} /> Agregar sucursal</Button>
            )}
          </div>
          <DataTable
            columns={sucCols}
            rows={sucs}
            loading={d?.loading}
            empty="Sin sucursales (las ventas usan el precio del cliente)"
          />
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Precios especiales</h3>
            {canOvr && (
              <Button variant="secondary" onClick={() => openOvrModal(cliente.id)}><Plus size={15} /> Precio especial</Button>
            )}
          </div>
          <DataTable
            columns={ovrCols}
            rows={ovrs}
            loading={d?.loading}
            empty="Sin precios especiales para este cliente"
          />
        </section>
      </div>
    );
  }

  const sucModalCliente = sucModal?.clienteId;
  const ovrModalCliente = ovrModal?.clienteId;
  const ovrModalSucursales = ovrModalCliente ? detalle[ovrModalCliente]?.sucursales ?? [] : [];

  return (
    <div>
      <PageHeader
        title="Sucursales y precios por cliente"
        subtitle="Cada cliente: sus sucursales de entrega y sus precios especiales negociados."
      />

      <DataTableSmart
        columns={cols}
        rows={clientes}
        loading={clientesRes.loading}
        error={clientesRes.error}
        empty="Sin clientes"
        storageKey="sucursales"
        rowKey={(c) => c.id}
        searchPlaceholder="Buscar cliente…"
        renderExpanded={(c) => <ExpandedCliente cliente={c} />}
        onRowExpand={(c) => { if (!detalle[c.id]) loadDetalle(c.id); }}
      />

      {/* Modal: nueva sucursal */}
      <Modal
        open={!!sucModal}
        onClose={() => setSucModal(null)}
        title="Agregar sucursal"
        footer={
          <>
            <Button variant="secondary" onClick={() => setSucModal(null)}>Cancelar</Button>
            <Button onClick={createSucursal}>Crear sucursal</Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field
            label="Basarse en una sucursal existente"
            hint="El mismo punto suele repetirse entre razones sociales hermanas: elegirlo prellena todo y solo queda crear."
          >
            <Select value={baseSuc} onChange={(e) => basarseEn(e.target.value)}>
              <option value="">— Empezar en blanco —</option>
              {todasSucs
                .filter((s) => s.cliente_id !== sucModal?.clienteId)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.nombre}{s.codigo ? ` (${s.codigo})` : ""} · {cliName[s.cliente_id] ?? "otro cliente"}
                  </option>
                ))}
            </Select>
          </Field>
          <Field label="Nombre" required>
            <Input placeholder="Ej. Matriz Centro" value={nuevaSuc.nombre} onChange={(e) => setNuevaSuc({ ...nuevaSuc, nombre: e.target.value })} autoFocus />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Contacto">
              <Input value={nuevaSuc.contacto} onChange={(e) => setNuevaSuc({ ...nuevaSuc, contacto: e.target.value })} />
            </Field>
            <Field label="Teléfono">
              <Input value={nuevaSuc.telefono} onChange={(e) => setNuevaSuc({ ...nuevaSuc, telefono: e.target.value })} />
            </Field>
          </div>
          <p className="text-xs text-muted">
            La lista de precios de esta sucursal se asigna en{" "}
            <b>Listas de precios › Asignación de precios</b>, junto con las de serie y proyecto.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Series de factura" hint="Una o más. La primera palomeada es la default; gana sobre la del cliente.">
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-border p-2">
                {seriesFac.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={nuevaSuc.series_factura_ids.includes(s.id)}
                      onChange={(e) =>
                        setNuevaSuc((prev) => ({
                          ...prev,
                          series_factura_ids: e.target.checked
                            ? [...prev.series_factura_ids, s.id]
                            : prev.series_factura_ids.filter((x) => x !== s.id),
                          serie_factura_id: e.target.checked && !prev.series_factura_ids.length ? s.id
                            : prev.serie_factura_id === s.id && !e.target.checked ? "" : prev.serie_factura_id,
                        }))
                      }
                    />
                    {s.codigo}{s.nombre ? ` · ${s.nombre}` : ""}
                  </label>
                ))}
                {!seriesFac.length ? <span className="text-xs text-muted">Sin series de factura</span> : null}
              </div>
            </Field>
            <Field label="Series de remisión" hint="Una o más; la primera palomeada es la default.">
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-border p-2">
                {seriesRem.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={nuevaSuc.series_remision_ids.includes(s.id)}
                      onChange={(e) =>
                        setNuevaSuc((prev) => ({
                          ...prev,
                          series_remision_ids: e.target.checked
                            ? [...prev.series_remision_ids, s.id]
                            : prev.series_remision_ids.filter((x) => x !== s.id),
                          serie_remision_id: e.target.checked && !prev.series_remision_ids.length ? s.id
                            : prev.serie_remision_id === s.id && !e.target.checked ? "" : prev.serie_remision_id,
                        }))
                      }
                    />
                    {s.codigo}{s.nombre ? ` · ${s.nombre}` : ""}
                  </label>
                ))}
                {!seriesRem.length ? <span className="text-xs text-muted">Sin series de remisión</span> : null}
              </div>
            </Field>
          </div>
          <p className="text-xs text-muted">
            Sin serie palomeada, la sucursal usa las del cliente (o la default del negocio).
          </p>
          <Field label="Almacén" hint="De dónde sale la mercancía de esta sucursal. Gana sobre el del cliente.">
            <Select value={nuevaSuc.almacen_id} onChange={(e) => setNuevaSuc({ ...nuevaSuc, almacen_id: e.target.value })}>
              <option value="">(usa el del cliente / predeterminado)</option>
              {almacenes.map((a) => (
                <option key={a.id} value={a.id}>{a.codigo ? `${a.codigo} · ${a.nombre}` : a.nombre}</option>
              ))}
            </Select>
          </Field>
          <p className="text-xs text-muted">El código (SUC-01, SUC-02, …) se genera automáticamente.</p>
        </div>
      </Modal>

      {/* Modal: precio especial */}
      <Modal
        open={!!ovrModal}
        onClose={() => setOvrModal(null)}
        title="Nuevo precio especial"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOvrModal(null)}>Cancelar</Button>
            <Button onClick={createOverride}>Agregar precio</Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Producto" required>
            <Select
              value={nuevoOvr.producto_id}
              onChange={(e) => setNuevoOvr({ ...nuevoOvr, producto_id: e.target.value, presentacion: presentacionesDe(e.target.value)[0] ?? "" })}
            >
              <option value="">— Elige un producto —</option>
              {productos.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Presentación">
              <Select value={nuevoOvr.presentacion} onChange={(e) => setNuevoOvr({ ...nuevoOvr, presentacion: e.target.value })} disabled={!nuevoOvr.producto_id}>
                {presentacionesDe(nuevoOvr.producto_id).map((pr) => <option key={pr} value={pr}>{pr}</option>)}
              </Select>
            </Field>
            <Field label="Precio" required>
              <Input type="number" step="0.0001" value={nuevoOvr.precio_unitario} onChange={(e) => setNuevoOvr({ ...nuevoOvr, precio_unitario: e.target.value })} />
            </Field>
          </div>
          <Field label="Alcance" hint="Aplica a todo el cliente o solo a una de sus sucursales.">
            <Select value={nuevoOvr.sucursal_id} onChange={(e) => setNuevoOvr({ ...nuevoOvr, sucursal_id: e.target.value })}>
              <option value="">Todo el cliente</option>
              {ovrModalSucursales.map((s) => <option key={s.id} value={s.id}>Solo {s.nombre}</option>)}
            </Select>
          </Field>
        </div>
      </Modal>
    </div>
  );
}

function sucName(sucs: Sucursal[], id: string): string {
  return sucs.find((s) => s.id === id)?.nombre ?? "sucursal";
}

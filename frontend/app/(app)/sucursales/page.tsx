"use client";

import { useMemo, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";

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
import type {
  Almacen,
  Cliente,
  ClienteSucursal,
  PrecioOverride,
  Producto,
  Serie,
  Sucursal,
} from "@/lib/types";

// La sucursal es la PLAZA del negocio (Pachuca, Tabasco): una sola fila de la
// que se surten varios clientes (rediseño 01-sep-2026). La serie de folios es
// del VÍNCULO cliente×plaza — EHMO factura en Tabasco con ZEHMOVH mientras
// Balles y Jubran comparten ZHGO en Pachuca — y el almacén es de la plaza.
//
// Permisos de escritura (igual que la versión previa de esta página):
//  - plazas y vínculos -> cliente:gestionar
//  - precios especiales (overrides) -> lista_precios:gestionar
const WRITE_SUC = "cliente:gestionar";
const WRITE_OVR = "lista_precios:gestionar";

type FormVinculo = {
  cliente_id: string;
  series_factura_ids: string[];
  series_remision_ids: string[];
  serie_factura_id: string;
  serie_remision_id: string;
  es_default: boolean;
};

export default function SucursalesPage() {
  // ?cliente=<id> — se llega aquí desde la lista de Clientes ("Sucursales"):
  // se muestran solo las plazas que surten a ese cliente.
  const clienteParam =
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("cliente")
      : null;

  const { me } = useAuth();
  const toast = useToast();
  const canSuc = can(me, WRITE_SUC);
  const canOvr = can(me, WRITE_OVR);
  const verOvr = can(me, "menu:listas_precios");
  const { post, put, del } = useMutation();

  // ── catálogos (una sola carga) ──
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=500");
  const sucursalesRes = useResource<Page<Sucursal>>("/api/v1/sucursales?limit=1000");
  const productosRes = useResource<Page<Producto>>("/api/v1/productos?limit=1000");
  const almacenesRes = useResource<Page<Almacen>>("/api/v1/almacenes?limit=200");
  const seriesFacRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=FACTURA&activa=true&limit=200");
  const seriesRemRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=REMISION&activa=true&limit=200");
  const overridesRes = useResource<Page<PrecioOverride>>(
    // El endpoint topa `limit` en 200. Con ?cliente= la vista es de ESE cliente,
    // así que sus precios especiales también.
    verOvr
      ? `/api/v1/precios/overrides?limit=200${clienteParam ? `&cliente_id=${clienteParam}` : ""}`
      : null,
  );

  const clientes = clientesRes.data?.items ?? [];
  const todasLasPlazas = sucursalesRes.data?.items ?? [];
  const plazas = clienteParam
    ? todasLasPlazas.filter((s) => (s.clientes_ids ?? []).includes(clienteParam))
    : todasLasPlazas;
  const productos = productosRes.data?.items ?? [];
  const almacenes = almacenesRes.data?.items ?? [];
  const seriesFac = seriesFacRes.data?.items ?? [];
  const seriesRem = seriesRemRes.data?.items ?? [];
  const overrides = overridesRes.data?.items ?? [];

  const cliName = useMemo(
    () => Object.fromEntries(clientes.map((c) => [c.id, c.legal_name])),
    [clientes],
  );
  const sucName = useMemo(
    () => Object.fromEntries(todasLasPlazas.map((s) => [s.id, s.nombre])),
    [todasLasPlazas],
  );
  const almName = useMemo(
    () => Object.fromEntries(almacenes.map((a) => [a.id, a.codigo ? `${a.codigo} · ${a.nombre}` : a.nombre])),
    [almacenes],
  );
  const serieCodigo = useMemo(
    () => Object.fromEntries([...seriesFac, ...seriesRem].map((s) => [s.id, s.codigo])),
    [seriesFac, seriesRem],
  );
  const prodName = useMemo(() => Object.fromEntries(productos.map((p) => [p.id, p.nombre])), [productos]);
  const prodById = useMemo(() => Object.fromEntries(productos.map((p) => [p.id, p])), [productos]);

  // ── vínculos por plaza, cargados bajo demanda al expandir la fila ──
  type Detalle = { vinculos: ClienteSucursal[]; loading: boolean };
  const [detalle, setDetalle] = useState<Record<string, Detalle>>({});

  async function loadDetalle(sucursalId: string) {
    setDetalle((d) => ({ ...d, [sucursalId]: { vinculos: d[sucursalId]?.vinculos ?? [], loading: true } }));
    try {
      const vincs = await apiFetch<ClienteSucursal[]>(`/api/v1/sucursales/${sucursalId}/clientes`);
      setDetalle((d) => ({ ...d, [sucursalId]: { vinculos: vincs, loading: false } }));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cargar el detalle de la sucursal");
      // El error NO se cachea como "no tiene clientes": se suelta la entrada
      // para que volver a expandir reintente. Decir "ninguno" cuando en
      // realidad falló la red invita a revincular y pisar lo que ya existía.
      setDetalle((d) => {
        const { [sucursalId]: _fuera, ...resto } = d;
        return resto;
      });
    }
  }

  function reloadDetalle(sucursalId: string) {
    loadDetalle(sucursalId);
    sucursalesRes.reload(); // refresca la columna "Clientes"
  }

  // ── modal: nueva plaza ──
  const emptySuc = { nombre: "", contacto: "", telefono: "", almacen_id: "" };
  const [sucModal, setSucModal] = useState(false);
  const [nuevaSuc, setNuevaSuc] = useState(emptySuc);

  async function createSucursal() {
    if (!nuevaSuc.nombre.trim()) { toast.error("El nombre de la sucursal es obligatorio"); return; }
    try {
      // codigo se autogenera en el backend (SUC-01, …): NO se envía. Si se
      // llegó desde un cliente, la plaza nace ya vinculada a él.
      await post("/api/v1/sucursales", {
        nombre: nuevaSuc.nombre.trim(),
        contacto: nuevaSuc.contacto.trim() || null,
        telefono: nuevaSuc.telefono.trim() || null,
        almacen_id: nuevaSuc.almacen_id || null,
        cliente_id: clienteParam || null,
      });
      toast.success("Sucursal creada");
      sucursalesRes.reload();
      setSucModal(false);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la sucursal");
    }
  }

  async function delSucursal(s: Sucursal) {
    try {
      await del(`/api/v1/sucursales/${s.id}`);
      toast.success("Sucursal eliminada");
      sucursalesRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  // ── modal: vincular / editar vínculo de un cliente con la plaza ──
  const emptyVinc: FormVinculo = {
    cliente_id: "",
    series_factura_ids: [],
    series_remision_ids: [],
    serie_factura_id: "",
    serie_remision_id: "",
    es_default: false,
  };
  const [vincModal, setVincModal] = useState<{ sucursalId: string; existente: boolean } | null>(null);
  const [vinc, setVinc] = useState<FormVinculo>(emptyVinc);

  function openVincular(sucursalId: string) {
    // El cliente del ?cliente= se preselecciona SOLO si aún no está vinculado:
    // si ya lo está, dejarlo puesto haría que "Vincular" pisara su vínculo con
    // series vacías (o sea, le borrara la serie con la que folia).
    const yaVinculado = (detalle[sucursalId]?.vinculos ?? [])
      .some((v) => v.cliente_id === clienteParam);
    setVinc({ ...emptyVinc, cliente_id: clienteParam && !yaVinculado ? clienteParam : "" });
    setVincModal({ sucursalId, existente: false });
  }
  function openEditarVinculo(sucursalId: string, v: ClienteSucursal) {
    setVinc({
      cliente_id: v.cliente_id,
      series_factura_ids: v.series_factura_ids ?? [],
      series_remision_ids: v.series_remision_ids ?? [],
      serie_factura_id: v.serie_factura_id ?? "",
      serie_remision_id: v.serie_remision_id ?? "",
      es_default: v.es_default ?? false,
    });
    setVincModal({ sucursalId, existente: true });
  }

  async function saveVinculo() {
    if (!vincModal) return;
    if (!vinc.cliente_id) { toast.error("Elige el cliente a vincular"); return; }
    if (!vincModal.existente
        && (detalle[vincModal.sucursalId]?.vinculos ?? []).some((v) => v.cliente_id === vinc.cliente_id)) {
      toast.error("Ese cliente ya está vinculado: edítalo con el lápiz para no borrarle sus series");
      return;
    }
    try {
      await put(`/api/v1/sucursales/${vincModal.sucursalId}/clientes/${vinc.cliente_id}`, {
        serie_factura_id: vinc.serie_factura_id || null,
        serie_remision_id: vinc.serie_remision_id || null,
        series_factura_ids: vinc.series_factura_ids,
        series_remision_ids: vinc.series_remision_ids,
        es_default: vinc.es_default,
      });
      toast.success(vincModal.existente ? "Vínculo actualizado" : "Cliente vinculado");
      reloadDetalle(vincModal.sucursalId);
      setVincModal(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar el vínculo");
    }
  }

  async function delVinculo(sucursalId: string, v: ClienteSucursal) {
    try {
      await del(`/api/v1/sucursales/${sucursalId}/clientes/${v.cliente_id}`);
      toast.success("Cliente desvinculado");
      reloadDetalle(sucursalId);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo desvincular");
    }
  }

  // ── modal: precio especial (cliente, plaza o ambos) ──
  const emptyOvr = { producto_id: "", presentacion: "", cliente_id: "", sucursal_id: "", precio_unitario: "" };
  const [ovrModal, setOvrModal] = useState(false);
  const [nuevoOvr, setNuevoOvr] = useState(emptyOvr);

  function presentacionesDe(productoId: string): string[] {
    const p = prodById[productoId];
    if (!p) return [];
    const keys = Object.keys(p.presentaciones ?? {});
    const def = p.presentacion_default ?? p.unidad_base;
    return def && keys.includes(def) ? [def, ...keys.filter((k) => k !== def)] : keys;
  }

  async function createOverride() {
    if (!nuevoOvr.producto_id || !nuevoOvr.precio_unitario) { toast.error("Elige producto y precio"); return; }
    if (!nuevoOvr.cliente_id && !nuevoOvr.sucursal_id) { toast.error("Elige cliente, sucursal o ambos"); return; }
    try {
      await post("/api/v1/precios/overrides", {
        producto_id: nuevoOvr.producto_id,
        presentacion: nuevoOvr.presentacion.trim() || "KILO",
        precio_unitario: nuevoOvr.precio_unitario,
        cliente_id: nuevoOvr.cliente_id || null,
        sucursal_id: nuevoOvr.sucursal_id || null,
      });
      toast.success("Precio especial agregado");
      overridesRes.reload();
      setOvrModal(false);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo agregar el precio especial");
    }
  }

  async function delOverride(o: PrecioOverride) {
    try {
      await del(`/api/v1/precios/overrides/${o.id}`);
      toast.success("Precio especial eliminado");
      overridesRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  // ── columnas de la tabla principal (plazas) ──
  const cols: Column<Sucursal>[] = [
    { header: "Sucursal", cell: (s) => <span className="font-medium">{s.nombre}</span>, sortable: true, sortValue: (s) => s.nombre },
    { header: "Código", cell: (s) => s.codigo ?? "—", sortable: true, sortValue: (s) => s.codigo ?? "" },
    {
      header: "Clientes",
      cell: (s) =>
        (s.clientes_nombres ?? []).length
          ? (s.clientes_nombres ?? []).join(", ")
          : <span className="text-muted">(sin clientes vinculados)</span>,
      // Texto, no cuenta: es lo que el buscador indexa y lo que sale al Excel.
      sortValue: (s) => (s.clientes_nombres ?? []).join(", "),
    },
    { header: "Almacén", cell: (s) => (s.almacen_id ? almName[s.almacen_id] ?? "—" : "(predeterminado)") },
    {
      header: "Estado",
      cell: (s) => <Badge tone={s.activo ? "success" : "muted"}>{s.activo ? "Activa" : "Inactiva"}</Badge>,
      sortValue: (s) => (s.activo ? 1 : 0),
    },
    ...(canSuc
      ? [{
          header: "", className: "text-right w-1",
          cell: (s: Sucursal) => (
            <button onClick={(e) => { e.stopPropagation(); delSucursal(s); }} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Eliminar sucursal"><Trash2 size={16} /></button>
          ),
        }]
      : []),
  ];

  // ── panel expandible por plaza: sus clientes vinculados ──
  function ExpandedPlaza({ plaza }: { plaza: Sucursal }) {
    const d = detalle[plaza.id];
    const vincs = d?.vinculos ?? [];

    const vCols: Column<ClienteSucursal>[] = [
      {
        header: "Cliente",
        cell: (v) => (
          <span className="font-medium">
            {v.cliente_nombre ?? cliName[v.cliente_id] ?? "—"}
            {v.es_default && <span className="ml-2"><Badge tone="success">default</Badge></span>}
          </span>
        ),
      },
      {
        header: "Serie de factura",
        cell: (v) => (v.serie_factura_id && serieCodigo[v.serie_factura_id]
          ? <Badge tone="muted">{serieCodigo[v.serie_factura_id]}</Badge>
          : <span className="text-muted">(la del cliente / default)</span>),
      },
      {
        header: "Serie de remisión",
        cell: (v) => (v.serie_remision_id && serieCodigo[v.serie_remision_id]
          ? <Badge tone="muted">{serieCodigo[v.serie_remision_id]}</Badge>
          : <span className="text-muted">(la del cliente / default)</span>),
      },
      {
        header: "Abanico",
        cell: (v) => {
          const n = (v.series_factura_ids?.length ?? 0) + (v.series_remision_ids?.length ?? 0);
          return n ? `${n} serie${n === 1 ? "" : "s"}` : "—";
        },
      },
      ...(canSuc
        ? [{
            header: "", className: "text-right w-1 whitespace-nowrap",
            cell: (v: ClienteSucursal) => (
              <>
                <button onClick={() => openEditarVinculo(plaza.id, v)} className="rounded-md p-1.5 text-muted hover:bg-surface-2" aria-label="Editar series del vínculo"><Pencil size={16} /></button>
                <button onClick={() => delVinculo(plaza.id, v)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Desvincular cliente"><Trash2 size={16} /></button>
              </>
            ),
          }]
        : []),
    ];

    return (
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Clientes que se surten de esta sucursal</h3>
          {canSuc && (
            <Button variant="secondary" onClick={() => openVincular(plaza.id)}><Plus size={15} /> Vincular cliente</Button>
          )}
        </div>
        <DataTable
          columns={vCols}
          rows={vincs}
          loading={d?.loading}
          empty="Ningún cliente se surte de esta sucursal todavía"
        />
        <p className="text-xs text-muted">
          La serie es del vínculo cliente×sucursal (cada cliente folia distinto en la misma plaza).
          La lista de precios se asigna en <b>Listas de precios › Asignación de precios</b>.
        </p>
      </div>
    );
  }

  // ── sección: precios especiales ──
  const ovrCols: Column<PrecioOverride>[] = [
    { header: "Producto", cell: (o) => prodName[o.producto_id] ?? o.producto_id },
    { header: "Present.", cell: (o) => o.presentacion },
    { header: "Cliente", cell: (o) => (o.cliente_id ? cliName[o.cliente_id] ?? "—" : <span className="text-muted">(todos los de la sucursal)</span>) },
    { header: "Sucursal", cell: (o) => (o.sucursal_id ? sucName[o.sucursal_id] ?? "—" : <span className="text-muted">(donde sea)</span>) },
    { header: "Precio", className: "text-right tabular-nums", cell: (o) => fmtMoney(o.precio_unitario) },
    ...(canOvr
      ? [{
          header: "", className: "text-right w-1",
          cell: (o: PrecioOverride) => (
            <button onClick={() => delOverride(o)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Eliminar precio especial"><Trash2 size={16} /></button>
          ),
        }]
      : []),
  ];

  const clientesVinculables = vincModal && !vincModal.existente
    ? clientes.filter((c) => {
        const d = detalle[vincModal.sucursalId];
        return !(d?.vinculos ?? []).some((v) => v.cliente_id === c.id);
      })
    : clientes;

  return (
    <div>
      <PageHeader
        title={clienteParam ? `Sucursales de ${cliName[clienteParam] ?? "…"}` : "Sucursales y precios"}
        subtitle="Las plazas del negocio. Cada una: qué clientes se surten de ella, con qué serie folia cada uno y de qué almacén sale la mercancía."
        actions={canSuc ? (
          <Button onClick={() => { setNuevaSuc(emptySuc); setSucModal(true); }}><Plus size={15} /> Nueva sucursal</Button>
        ) : undefined}
      />

      <DataTableSmart
        columns={cols}
        rows={plazas}
        loading={sucursalesRes.loading}
        error={sucursalesRes.error}
        empty="Sin sucursales"
        storageKey="sucursales-plazas"
        rowKey={(s) => s.id}
        searchPlaceholder="Buscar sucursal…"
        renderExpanded={(s) => <ExpandedPlaza plaza={s} />}
        onRowExpand={(s) => { if (!detalle[s.id]) loadDetalle(s.id); }}
      />

      {verOvr && (
        <section className="mt-8">
          <div className="mb-2 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold">Precios especiales</h2>
              <p className="text-sm text-muted">
                Un producto a precio fijo para un cliente, una sucursal, o ese cliente EN esa sucursal. Ganan sobre cualquier lista.
              </p>
            </div>
            {canOvr && (
              <Button variant="secondary" onClick={() => { setNuevoOvr(emptyOvr); setOvrModal(true); }}><Plus size={15} /> Precio especial</Button>
            )}
          </div>
          <DataTable
            columns={ovrCols}
            rows={overrides}
            loading={overridesRes.loading}
            empty="Sin precios especiales"
          />
        </section>
      )}

      {/* Modal: nueva plaza */}
      <Modal
        open={sucModal}
        onClose={() => setSucModal(false)}
        title="Nueva sucursal"
        footer={
          <>
            <Button variant="secondary" onClick={() => setSucModal(false)}>Cancelar</Button>
            <Button onClick={createSucursal}>Crear sucursal</Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Nombre" required hint="La plaza es una sola para todo el negocio: Pachuca existe una vez y de ella se surten todos sus clientes.">
            <Input placeholder="Ej. Pachuca" value={nuevaSuc.nombre} onChange={(e) => setNuevaSuc({ ...nuevaSuc, nombre: e.target.value })} autoFocus />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Contacto">
              <Input value={nuevaSuc.contacto} onChange={(e) => setNuevaSuc({ ...nuevaSuc, contacto: e.target.value })} />
            </Field>
            <Field label="Teléfono">
              <Input value={nuevaSuc.telefono} onChange={(e) => setNuevaSuc({ ...nuevaSuc, telefono: e.target.value })} />
            </Field>
          </div>
          <Field label="Almacén" hint="De dónde sale la mercancía de esta sucursal, para todos sus clientes.">
            <Select value={nuevaSuc.almacen_id} onChange={(e) => setNuevaSuc({ ...nuevaSuc, almacen_id: e.target.value })}>
              <option value="">(el predeterminado del negocio)</option>
              {almacenes.map((a) => (
                <option key={a.id} value={a.id}>{a.codigo ? `${a.codigo} · ${a.nombre}` : a.nombre}</option>
              ))}
            </Select>
          </Field>
          {clienteParam ? (
            <p className="text-xs text-muted">Se creará ya vinculada a <b>{cliName[clienteParam] ?? "este cliente"}</b>.</p>
          ) : null}
          <p className="text-xs text-muted">
            El código (SUC-01, SUC-02, …) se genera automáticamente. Las series se
            configuran al vincular cada cliente.
          </p>
        </div>
      </Modal>

      {/* Modal: vincular cliente / editar series del vínculo */}
      <Modal
        open={!!vincModal}
        onClose={() => setVincModal(null)}
        title={vincModal?.existente ? "Series del cliente en esta sucursal" : "Vincular cliente"}
        footer={
          <>
            <Button variant="secondary" onClick={() => setVincModal(null)}>Cancelar</Button>
            <Button onClick={saveVinculo}>{vincModal?.existente ? "Guardar" : "Vincular"}</Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Cliente" required>
            <Select
              value={vinc.cliente_id}
              onChange={(e) => setVinc({ ...vinc, cliente_id: e.target.value })}
              disabled={vincModal?.existente}
            >
              <option value="">— Elige un cliente —</option>
              {clientesVinculables.map((c) => (
                <option key={c.id} value={c.id}>{c.legal_name}</option>
              ))}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Series de factura" hint="Una o más. La primera palomeada es la default de ESTE cliente en ESTA sucursal.">
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-border p-2">
                {seriesFac.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={vinc.series_factura_ids.includes(s.id)}
                      onChange={(e) =>
                        setVinc((prev) => ({
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
                      checked={vinc.series_remision_ids.includes(s.id)}
                      onChange={(e) =>
                        setVinc((prev) => ({
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
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={vinc.es_default}
              onChange={(e) => setVinc({ ...vinc, es_default: e.target.checked })}
            />
            Sucursal por defecto de este cliente
            <span className="text-xs text-muted">
              (se preselecciona al capturar remisiones/facturas; marca a lo más una por cliente)
            </span>
          </label>
          <p className="text-xs text-muted">
            Sin serie palomeada, este cliente usa aquí sus series de cliente (o la default del negocio).
          </p>
        </div>
      </Modal>

      {/* Modal: precio especial */}
      <Modal
        open={ovrModal}
        onClose={() => setOvrModal(false)}
        title="Nuevo precio especial"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOvrModal(false)}>Cancelar</Button>
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
          <div className="grid grid-cols-2 gap-3">
            <Field label="Cliente" hint="Vacío = todos los clientes de la sucursal elegida.">
              <Select value={nuevoOvr.cliente_id} onChange={(e) => setNuevoOvr({ ...nuevoOvr, cliente_id: e.target.value })}>
                <option value="">(cualquiera)</option>
                {clientes.map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
              </Select>
            </Field>
            <Field label="Sucursal" hint="Vacío = el cliente en cualquier plaza. Ambos = lo más específico.">
              <Select value={nuevoOvr.sucursal_id} onChange={(e) => setNuevoOvr({ ...nuevoOvr, sucursal_id: e.target.value })}>
                <option value="">(cualquiera)</option>
                {todasLasPlazas.map((s) => <option key={s.id} value={s.id}>{s.nombre}</option>)}
              </Select>
            </Field>
          </div>
        </div>
      </Modal>
    </div>
  );
}

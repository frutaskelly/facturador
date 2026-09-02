"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Download, FileText, ListPlus, Pencil, Plus, Tag, Trash2, Upload } from "lucide-react";

import { NuevaPresentacionDialog } from "@/components/NuevaPresentacionDialog";
import { SincronizarSae } from "@/components/SincronizarSae";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch, apiOpenInTab } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtMoney } from "@/lib/format";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import type { Categoria, ListaPrecios, Precio, Producto } from "@/lib/types";

const WRITE = "lista_precios:gestionar";
// Borrar la LISTA entera (con todos sus precios) lo pide el backend aparte;
// quitar un precio suelto sigue siendo gestionar.
const DELETE = "lista_precios:eliminar";

// Valor especial de los selectores de presentación: "darla de alta ahora".
const NUEVA_PRESENTACION = "__nueva_pres__";

/** Presentación options for a producto: its declared presentaciones keys. */
function presentacionOptions(p: Producto | undefined): string[] {
  if (!p) return [];
  const keys = Object.keys(p.presentaciones ?? {});
  return keys.length > 0 ? keys : [];
}

/** Default presentación for a producto: presentacion_default, else unidad_base. */
function defaultPresentacion(p: Producto | undefined): string {
  if (!p) return "";
  return p.presentacion_default ?? p.unidad_base ?? Object.keys(p.presentaciones ?? {})[0] ?? "";
}

export default function ListasPreciosPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  const canDelete = can(me, DELETE);
  // Dar de alta una presentación escribe en el PRODUCTO, no en la lista:
  // se ofrece solo a quien puede editar productos.
  const canWriteProductos = can(me, "producto:gestionar");
  const { post, patch, del, loading: saving } = useMutation();

  const listasRes = useResource<Page<ListaPrecios>>("/api/v1/listas-precios?limit=200");
  const productosRes = useResource<Page<Producto>>("/api/v1/productos?limit=1000");
  const categoriasRes = useResource<Page<Categoria>>("/api/v1/categorias?limit=200");
  const listas = listasRes.data?.items ?? [];
  const productos = productosRes.data?.items ?? [];
  const categorias = categoriasRes.data?.items ?? [];
  const prodName = useMemo(
    () => Object.fromEntries(productos.map((p) => [p.id, p.nombre])),
    [productos]
  );
  const prodById = useMemo(
    () => Object.fromEntries(productos.map((p) => [p.id, p])) as Record<string, Producto>,
    [productos]
  );

  // ── editor de lista ──
  const [listaForm, setListaForm] = useState<{ id?: string; codigo: string; nombre: string; status: string; es_default: boolean; copiarDe: string; saeEmpresa: string; saeLista: string } | null>(null);

  async function saveLista() {
    if (!listaForm) return;
    if (!listaForm.codigo.trim() || !listaForm.nombre.trim()) {
      toast.error("Código y nombre son obligatorios");
      return;
    }
    // El vínculo con SAE va completo o no va: empresa Y número de lista.
    if (!listaForm.saeEmpresa.trim() !== !listaForm.saeLista.trim()) {
      toast.error("El vínculo con SAE lleva empresa y número de lista; deja ambos vacíos para una lista manual");
      return;
    }
    const body = { codigo: listaForm.codigo.trim(), nombre: listaForm.nombre.trim(),
                   status: listaForm.status, es_default: listaForm.es_default,
                   sae_empresa: listaForm.saeEmpresa.trim() || null,
                   sae_lista: listaForm.saeLista.trim() ? Number(listaForm.saeLista) : null };
    try {
      if (listaForm.id) {
        await patch(`/api/v1/listas-precios/${listaForm.id}`, body);
        toast.success("Lista guardada");
        setListaForm(null);
        listasRes.reload();
        return;
      }
      const created = await post<ListaPrecios>("/api/v1/listas-precios", body);
      if (listaForm.copiarDe) {
        try {
          const res = await post<{ created: number; skipped: number }>(
            `/api/v1/listas-precios/${created.id}/copiar`,
            { origen_id: listaForm.copiarDe }
          );
          toast.success(`Lista creada (${res.created} precios copiados, ${res.skipped} omitidos)`);
        } catch (e) {
          toast.error(e instanceof ApiError ? `Lista creada, pero no se copiaron precios: ${e.message}` : "Lista creada, pero no se copiaron precios");
        }
      } else {
        toast.success("Lista guardada");
      }
      setListaForm(null);
      await listasRes.reload();
      await openPrecios(created);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  // ── gestor de precios ──
  const [activeLista, setActiveLista] = useState<ListaPrecios | null>(null);
  const [precios, setPrecios] = useState<Precio[]>([]);
  // Buscador del gestor de precios: con 500 renglones, sin filtro no se trabaja.
  const [buscaPrecio, setBuscaPrecio] = useState("");
  const importRef = useRef<HTMLInputElement>(null);
  const [importando, setImportando] = useState(false);

  async function importarExcelLista(f: File) {
    if (!activeLista) return;
    setImportando(true);
    try {
      const fd = new FormData();
      fd.append("archivo", f);
      const r = await apiFetch<{ actualizados: number; agregados: number; eliminados: number; sin_cambio: number; errores: string[] }>(
        `/api/v1/listas-precios/${activeLista.id}/importar`,
        { method: "POST", body: fd }
      );
      const partes = [`${r.agregados} agregados`, `${r.actualizados} actualizados`, `${r.eliminados} quitados`];
      if (r.errores.length) partes.push(`${r.errores.length} con error (${r.errores[0]})`);
      toast[r.errores.length ? "error" : "success"](partes.join(" · "));
      void loadPrecios(activeLista.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo importar");
    } finally {
      setImportando(false);
      if (importRef.current) importRef.current.value = "";
    }
  }
  const [loadingPrecios, setLoadingPrecios] = useState(false);
  const [nuevo, setNuevo] = useState({ producto_id: "", presentacion: "", cantidad_minima: "1", precio_unitario: "" });
  // Producto al que se le está agregando una presentación desde esta pantalla.
  const [nuevaPres, setNuevaPres] = useState<string | null>(null);

  async function openPrecios(lista: ListaPrecios) {
    setActiveLista(lista);
    setNuevo({ producto_id: "", presentacion: "", cantidad_minima: "1", precio_unitario: "" });
    await loadPrecios(lista.id);
  }

  async function loadPrecios(listaId: string) {
    setLoadingPrecios(true);
    try {
      const res = await apiFetch<Page<Precio>>(`/api/v1/listas-precios/${listaId}/precios?limit=500`);
      setPrecios(res.items);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron cargar los precios");
    } finally {
      setLoadingPrecios(false);
    }
  }

  async function addPrecio() {
    if (!activeLista) return;
    if (!nuevo.producto_id || !nuevo.precio_unitario) {
      toast.error("Elige producto y precio");
      return;
    }
    if (!nuevo.presentacion) {
      toast.error("Elige una presentación");
      return;
    }
    try {
      await post(`/api/v1/listas-precios/${activeLista.id}/precios`, {
        producto_id: nuevo.producto_id,
        presentacion: nuevo.presentacion,
        cantidad_minima: Number(nuevo.cantidad_minima) || 1,
        precio_unitario: nuevo.precio_unitario,
      });
      toast.success("Precio agregado");
      setNuevo({ producto_id: "", presentacion: "", cantidad_minima: "1", precio_unitario: "" });
      await loadPrecios(activeLista.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo agregar (¿tier duplicado?)");
    }
  }

  // ── cargar productos del catálogo ──
  type CatalogRow = { producto_id: string; presentacion: string; precio_unitario: string };
  const [cargarOpen, setCargarOpen] = useState(false);
  const [cargarCategoria, setCargarCategoria] = useState("");
  const [cargarRows, setCargarRows] = useState<CatalogRow[]>([]);

  function openCargar() {
    setCargarCategoria("");
    rebuildCargarRows("");
    setCargarOpen(true);
  }

  function rebuildCargarRows(categoriaId: string) {
    const filtered = categoriaId
      ? productos.filter((p) => p.categoria_id === categoriaId)
      : productos;
    setCargarRows(
      filtered.map((p) => ({
        producto_id: p.id,
        presentacion: defaultPresentacion(p),
        precio_unitario: "",
      }))
    );
  }

  function setCargarRow(producto_id: string, patch: Partial<CatalogRow>) {
    setCargarRows((rows) => rows.map((r) => (r.producto_id === producto_id ? { ...r, ...patch } : r)));
  }

  async function submitCargar() {
    if (!activeLista) return;
    const items = cargarRows
      .filter((r) => r.precio_unitario.trim() !== "" && r.presentacion)
      .map((r) => ({
        producto_id: r.producto_id,
        presentacion: r.presentacion,
        precio_unitario: r.precio_unitario,
        cantidad_minima: 1,
      }));
    if (items.length === 0) {
      toast.error("Captura al menos un precio");
      return;
    }
    try {
      const res = await post<{ created: number; updated: number }>(
        `/api/v1/listas-precios/${activeLista.id}/precios/bulk`,
        { items }
      );
      toast.success(`${res.created} agregados, ${res.updated} actualizados`);
      setCargarOpen(false);
      await loadPrecios(activeLista.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron agregar los precios");
    }
  }

  async function delPrecio(p: Precio) {
    if (!activeLista) return;
    try {
      await del(`/api/v1/listas-precios/${activeLista.id}/precios/${p.id}`);
      await loadPrecios(activeLista.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const [toDelete, setToDelete] = useState<ListaPrecios | null>(null);
  async function confirmDeleteLista() {
    if (!toDelete) return;
    try {
      await del(`/api/v1/listas-precios/${toDelete.id}`);
      toast.success("Lista eliminada");
      setToDelete(null);
      listasRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const columns: Column<ListaPrecios>[] = [
    { header: "Código", cell: (l) => <span className="font-medium">{l.codigo}</span> },
    { header: "Nombre", truncate: true, cell: (l) => <span title={l.nombre}>{l.nombre}</span> },
    { header: "Estado", cell: (l) => <Badge tone={l.status === "ACTIVO" ? "success" : "muted"}>{l.status}</Badge> },
    { header: "Base", cell: (l) => (l.es_default ? <Badge tone="accent">★ Base</Badge> : <span className="text-muted">—</span>) },
    {
      header: "SAE",
      cell: (l) => (l.sae_empresa && l.sae_lista != null
        ? <span title={`Se actualiza desde SAE: empresa ${l.sae_empresa}, lista ${l.sae_lista}`}><Badge tone="muted">{`${l.sae_empresa} · L${l.sae_lista}`}</Badge></span>
        : <span className="text-muted">—</span>),
    },
    { header: "Moneda", cell: (l) => <span className="text-muted">{l.moneda}</span> },
    {
      header: "",
      className: "text-right w-1",
      cell: (l) => (
        <div className="flex justify-end gap-1">
          <Button variant="secondary" onClick={(e) => { e.stopPropagation(); openPrecios(l); }}>
            <Tag size={14} /> Precios
          </Button>
          {canWrite && (
            <button
              onClick={(e) => { e.stopPropagation(); setListaForm({ id: l.id, codigo: l.codigo, nombre: l.nombre, status: l.status, es_default: !!l.es_default, copiarDe: "", saeEmpresa: l.sae_empresa ?? "", saeLista: l.sae_lista != null ? String(l.sae_lista) : "" }); }}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground" aria-label="Editar">
              <Pencil size={16} />
            </button>
          )}
          {canDelete && (
            <button
              onClick={(e) => { e.stopPropagation(); setToDelete(l); }}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Eliminar">
              <Trash2 size={16} />
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Listas de precios"
        subtitle="Niveles de venta (único, menudeo, mayoreo…) con precios por presentación y volumen."
        actions={
          <>
            {/* La misma solicitud del espejo de /facturas: el conector, además
                de las facturas, refresca los precios de las listas vinculadas. */}
            <SincronizarSae onSynced={() => { listasRes.reload(); if (activeLista) void loadPrecios(activeLista.id); }} />
            {canWrite && (
              <Button onClick={() => setListaForm({ codigo: "", nombre: "", status: "ACTIVO", es_default: false, copiarDe: "", saeEmpresa: "", saeLista: "" })}>
                <Plus size={16} /> Nueva lista de precios
              </Button>
            )}
          </>
        }
      />

      <DataTableSmart
        columns={columns}
        rows={listas}
        loading={listasRes.loading}
        error={listasRes.error}
        empty="Sin listas de precios"
        onRowClick={(l) => openPrecios(l)}
        storageKey="listas-precios"
      />

      {/* editor de lista */}
      <Modal
        open={listaForm !== null}
        onClose={() => setListaForm(null)}
        title={listaForm?.id ? "Editar lista" : "Nueva lista"}
        footer={
          <>
            <Button variant="secondary" onClick={() => setListaForm(null)}>Cancelar</Button>
            <Button onClick={saveLista} disabled={saving}>{saving ? "Guardando…" : "Guardar"}</Button>
          </>
        }
      >
        {listaForm && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Código" required>
              <Input value={listaForm.codigo} onChange={(e) => setListaForm({ ...listaForm, codigo: e.target.value.toUpperCase() })} />
            </Field>
            <Field label="Nombre" required>
              <Input value={listaForm.nombre} onChange={(e) => setListaForm({ ...listaForm, nombre: e.target.value })} />
            </Field>
            <Field label="Estado">
              <Select value={listaForm.status} onChange={(e) => setListaForm({ ...listaForm, status: e.target.value })}>
                <option value="ACTIVO">Activo</option>
                <option value="INACTIVO">Inactivo</option>
              </Select>
            </Field>
            {/* Marcarla es una DECISIÓN de negocio: la base la cobra cualquier
                cliente cuyo producto no esté en su lista negociada. Sin ninguna
                marcada no hay precio base, que es lo correcto — antes el sistema
                adivinaba con la lista más vieja y cobraba con la de otro. */}
            <Field
              label="Lista base del negocio"
              hint="La que se cobra cuando el producto no está en la lista negociada del cliente. No la actives en una lista negociada."
            >
              <Switch
                checked={listaForm.es_default}
                onChange={(v) => setListaForm({ ...listaForm, es_default: v })}
              />
            </Field>
            {/* El vínculo con SAE: de qué lista de Aspel se alimenta esta lista
                cuando se presiona «Sincronizar SAE». Vacío = lista manual. */}
            <Field label="Empresa SAE" hint="La empresa de Aspel: 02 Pachuca, 03 Tabasco. Vacío = lista manual.">
              <Input
                value={listaForm.saeEmpresa}
                placeholder="02"
                maxLength={4}
                onChange={(e) => setListaForm({ ...listaForm, saeEmpresa: e.target.value.replace(/[^0-9]/g, "") })}
              />
            </Field>
            <Field label="Lista SAE (número)" hint="El número de lista de precios en SAE (1–10).">
              <Input
                type="number" min="1" max="10"
                value={listaForm.saeLista}
                onChange={(e) => setListaForm({ ...listaForm, saeLista: e.target.value })}
              />
            </Field>
            {!listaForm.id && (
              <Field label="Copiar precios de" hint="Opcional: copia todos los precios de una lista existente.">
                <Select value={listaForm.copiarDe} onChange={(e) => setListaForm({ ...listaForm, copiarDe: e.target.value })}>
                  <option value="">— No copiar —</option>
                  {listas
                    .filter((l) => l.status === "ACTIVO")
                    .map((l) => <option key={l.id} value={l.id}>{l.codigo} — {l.nombre}</option>)}
                </Select>
              </Field>
            )}
          </div>
        )}
      </Modal>

      {/* gestor de precios de la lista */}
      <Modal
        open={activeLista !== null}
        onClose={() => setActiveLista(null)}
        wide
        title={activeLista ? `Precios — ${activeLista.nombre}` : ""}
        footer={<Button variant="secondary" onClick={() => setActiveLista(null)}>Cerrar</Button>}
      >
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-2">
            <p className="text-xs text-muted">
              Cada renglón es un <b>tier por volumen</b>: el precio aplica a partir de “Desde cant.”. Un solo renglón = precio fijo.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  const win = window.open("", "_blank");
                  void apiOpenInTab(`/api/v1/listas-precios/${activeLista?.id}/pdf`, win).catch((e) =>
                    toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el PDF"));
                }}
              >
                <FileText size={14} /> PDF
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  void apiDownload(`/api/v1/listas-precios/${activeLista?.id}/export`,
                    `precios_${activeLista?.codigo ?? "lista"}.xlsx`).catch((e) =>
                    toast.error(e instanceof ApiError ? e.message : "No se pudo exportar"));
                }}
              >
                <Download size={14} /> Excel
              </Button>
              {canWrite && (
                <Button variant="secondary" onClick={() => importRef.current?.click()} disabled={importando}>
                  <Upload size={14} /> {importando ? "Importando…" : "Importar Excel"}
                </Button>
              )}
              {canWrite && (
                <Button variant="secondary" onClick={openCargar}>
                  <ListPlus size={14} /> Cargar productos del catálogo
                </Button>
              )}
            </div>
          </div>
          <input
            ref={importRef} type="file" accept=".xlsx" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void importarExcelLista(f); }}
          />
          <p className="text-xs text-muted">
            El Excel baja y sube con las mismas columnas: cambia PRECIO para actualizar, agrega
            renglones nuevos por SKU, o deja el PRECIO vacío para quitar el renglón de la lista.
          </p>
          <Input
            placeholder="Buscar un producto en la lista…"
            value={buscaPrecio}
            onChange={(e) => setBuscaPrecio(e.target.value)}
          />

          {canWrite && (
            <div className="grid grid-cols-2 items-end gap-2 rounded-lg border border-border p-3 sm:grid-cols-5">
              <div className="col-span-2">
                <Field label="Producto">
                  <Select
                    value={nuevo.producto_id}
                    onChange={(e) => {
                      const prod = prodById[e.target.value];
                      setNuevo({ ...nuevo, producto_id: e.target.value, presentacion: defaultPresentacion(prod) });
                    }}
                  >
                    <option value="">— Elige —</option>
                    {productos.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                  </Select>
                </Field>
              </div>
              <Field label="Present.">
                <Select
                  value={nuevo.presentacion}
                  onChange={(e) => {
                    if (e.target.value === NUEVA_PRESENTACION) { setNuevaPres(nuevo.producto_id); return; }
                    setNuevo({ ...nuevo, presentacion: e.target.value });
                  }}
                  disabled={!nuevo.producto_id}
                >
                  {presentacionOptions(prodById[nuevo.producto_id]).map((k) => (
                    <option key={k} value={k}>{k}</option>
                  ))}
                  {/* Cobrar por CAJA empieza por que el producto sepa qué es
                      una caja: se da de alta aquí y no en otra pantalla. */}
                  {nuevo.producto_id && canWriteProductos && (
                    <option value={NUEVA_PRESENTACION}>＋ Nueva presentación…</option>
                  )}
                </Select>
              </Field>
              <Field label="Desde cant.">
                <Input type="number" min="1" value={nuevo.cantidad_minima} onChange={(e) => setNuevo({ ...nuevo, cantidad_minima: e.target.value })} />
              </Field>
              <Field label="Precio">
                <div className="flex gap-1">
                  <Input type="number" step="0.0001" value={nuevo.precio_unitario} onChange={(e) => setNuevo({ ...nuevo, precio_unitario: e.target.value })} />
                  <Button onClick={addPrecio} disabled={saving}><Plus size={16} /></Button>
                </div>
              </Field>
            </div>
          )}

          {loadingPrecios ? (
            <div className="flex justify-center py-8"><Spinner /></div>
          ) : (
            <DataTable
              columns={[
                { header: "Producto", cell: (p: Precio) => prodName[p.producto_id] ?? p.producto_id },
                { header: "Present.", cell: (p: Precio) => p.presentacion },
                { header: "Desde cant.", cell: (p: Precio) => p.cantidad_minima, className: "text-right" },
                { header: "Precio", cell: (p: Precio) => fmtMoney(p.precio_unitario), className: "text-right" },
                {
                  header: "", className: "text-right w-1",
                  cell: (p: Precio) => canWrite ? (
                    <button onClick={() => delPrecio(p)} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger" aria-label="Eliminar">
                      <Trash2 size={16} />
                    </button>
                  ) : null,
                },
              ]}
              rows={buscaPrecio.trim()
                ? precios.filter((p) =>
                    (prodName[p.producto_id] ?? "").toLowerCase().includes(buscaPrecio.trim().toLowerCase()))
                : precios}
              empty="Sin precios en esta lista"
            />
          )}
        </div>
      </Modal>

      {/* cargar productos del catálogo */}
      <Modal
        open={cargarOpen}
        onClose={() => setCargarOpen(false)}
        wide
        title="Cargar productos del catálogo"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCargarOpen(false)}>Cancelar</Button>
            <Button onClick={submitCargar} disabled={saving}>{saving ? "Agregando…" : "Agregar a la lista"}</Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Categoría" hint="Vacío = todos los productos.">
            <Select
              value={cargarCategoria}
              onChange={(e) => { setCargarCategoria(e.target.value); rebuildCargarRows(e.target.value); }}
            >
              <option value="">— Todas —</option>
              {categorias.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
            </Select>
          </Field>

          <p className="text-xs text-muted">Captura un precio en los productos que quieras agregar; los renglones vacíos se omiten.</p>

          <div className="max-h-[50vh] overflow-y-auto">
            <DataTable
              columns={[
                { header: "Producto", cell: (r: CatalogRow) => prodName[r.producto_id] ?? r.producto_id },
                {
                  header: "Present.",
                  cell: (r: CatalogRow) => (
                    <Select
                      value={r.presentacion}
                      onChange={(e) => {
                        if (e.target.value === NUEVA_PRESENTACION) { setNuevaPres(r.producto_id); return; }
                        setCargarRow(r.producto_id, { presentacion: e.target.value });
                      }}
                    >
                      {presentacionOptions(prodById[r.producto_id]).map((k) => (
                        <option key={k} value={k}>{k}</option>
                      ))}
                      {canWriteProductos && (
                        <option value={NUEVA_PRESENTACION}>＋ Nueva presentación…</option>
                      )}
                    </Select>
                  ),
                },
                {
                  header: "Precio",
                  className: "w-40",
                  cell: (r: CatalogRow) => (
                    <Input
                      type="number"
                      step="0.0001"
                      min="0"
                      value={r.precio_unitario}
                      onChange={(e) => setCargarRow(r.producto_id, { precio_unitario: e.target.value })}
                    />
                  ),
                },
              ]}
              rows={cargarRows}
              empty="No hay productos en esta categoría"
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Eliminar lista"
        message={`¿Eliminar la lista "${toDelete?.nombre}"? Sus precios se quitan también.`}
        onConfirm={confirmDeleteLista}
        onClose={() => setToDelete(null)}
        loading={saving}
      />

      <NuevaPresentacionDialog
        open={nuevaPres !== null}
        producto={nuevaPres ? prodById[nuevaPres] ?? null : null}
        onClose={() => setNuevaPres(null)}
        onCreated={(prod) => {
          const keys = Object.keys(prod.presentaciones ?? {});
          const nueva = keys[keys.length - 1] ?? "";
          // Queda elegida donde se pidió: el renglón que se estaba capturando
          // o el del producto en la rejilla del catálogo.
          if (nuevaPres === nuevo.producto_id) setNuevo({ ...nuevo, presentacion: nueva });
          setCargarRow(prod.id, { presentacion: nueva });
          setNuevaPres(null);
          productosRes.reload();
        }}
      />
    </div>
  );
}

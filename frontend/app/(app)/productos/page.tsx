"use client";

import { useMemo, useState } from "react";
import { FileUp, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { ImportarProductosModal } from "@/components/ImportarProductosModal";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import { useToast } from "@/components/ui/Toast";
import type { Categoria, EsquemaImpuesto, Producto } from "@/lib/types";

const WRITE = "producto:gestionar";

// Unidades base más comunes (unidad interna de inventario).
const UNIDADES_BASE = [
  "KILO", "PIEZA", "LITRO", "GRAMO", "MILILITRO", "CAJA", "BULTO", "COSTAL",
  "PAQUETE", "MANOJO", "MALLA", "REJA", "DOCENA", "ATADO",
];

// Unidades SAT (c_ClaveUnidad) frecuentes, con su nombre.
const UNIDADES_SAT: { code: string; nombre: string }[] = [
  { code: "KGM", nombre: "Kilogramo" },
  { code: "GRM", nombre: "Gramo" },
  { code: "LTR", nombre: "Litro" },
  { code: "MLT", nombre: "Mililitro" },
  { code: "H87", nombre: "Pieza" },
  { code: "XBX", nombre: "Caja" },
  { code: "XPK", nombre: "Paquete" },
  { code: "XBG", nombre: "Bolsa" },
  { code: "XSA", nombre: "Saco / Costal" },
  { code: "DPC", nombre: "Docena" },
  { code: "MTR", nombre: "Metro" },
  { code: "E48", nombre: "Unidad de servicio" },
];

type SatOpcion = { clave_sat: string; descripcion: string };
type PresRow = { nombre: string; factor: string };

type FormState = {
  sku: string;
  nombre: string;
  descripcion: string;
  categoria_id: string;
  esquema_impuesto_id: string;
  clave_sat: string;
  unidad_sat: string;
  unidad_base: string;
  presentaciones: PresRow[];
  activo: boolean;
  perecedero: boolean;
  requiere_lote: boolean;
};

function emptyForm(): FormState {
  return {
    sku: "",
    nombre: "",
    descripcion: "",
    categoria_id: "",
    esquema_impuesto_id: "",
    clave_sat: "01010101",
    unidad_sat: "KGM",
    unidad_base: "KILO",
    presentaciones: [],   // adicionales a la base (la base es 1:1 implícita)
    activo: true,
    perecedero: false,
    requiere_lote: false,
  };
}

function toForm(p: Producto): FormState {
  const base = p.unidad_base ?? "KILO";
  // Presentaciones adicionales (excluye la base). Soporta forma simple (número)
  // y rica ({factor, sat, estimado}).
  const rows: PresRow[] = Object.entries(p.presentaciones ?? {})
    .filter(([nombre]) => nombre !== base)
    .map(([nombre, factor]) => {
      const f = factor as unknown;
      const num = typeof f === "object" && f !== null ? (f as { factor?: number }).factor ?? 1 : (f as number);
      return { nombre, factor: String(num) };
    });
  return {
    sku: p.sku,
    nombre: p.nombre,
    descripcion: p.descripcion ?? "",
    categoria_id: p.categoria_id ?? "",
    esquema_impuesto_id: p.esquema_impuesto_id ?? "",
    clave_sat: p.clave_sat,
    unidad_sat: p.unidad_sat,
    unidad_base: base,
    presentaciones: rows,
    activo: p.activo,
    perecedero: p.perecedero,
    requiere_lote: p.requiere_lote,
  };
}

export default function ProductosPage() {
  const { me } = useAuth();
  const toast = useToast();
  const { post, patch, del, loading: saving } = useMutation();
  const canWrite = can(me, WRITE);

  const categoriasRes = useResource<Page<Categoria>>("/api/v1/categorias?limit=200");
  const categorias = categoriasRes.data?.items ?? [];
  const catName = useMemo(
    () => Object.fromEntries(categorias.map((c) => [c.id, c.nombre])),
    [categorias]
  );

  // Esquemas de impuesto ya dados de alta (para asignarlos al producto).
  const esquemasRes = useResource<Page<EsquemaImpuesto>>("/api/v1/esquemas-impuesto?limit=200");
  const esquemas = (esquemasRes.data?.items ?? []).filter((e) => e.activo);

  // Se carga la lista completa: DataTableSmart se encarga de la paginación,
  // búsqueda y orden en cliente sobre todas las filas.
  const { data, loading, error, reload } = useResource<Page<Producto>>(
    "/api/v1/productos?limit=1000"
  );
  const rows = data?.items ?? [];

  const [form, setForm] = useState<FormState | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Producto | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [satOpciones, setSatOpciones] = useState<SatOpcion[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerText, setPickerText] = useState("");
  const [importOpen, setImportOpen] = useState(false);

  async function suggestSat() {
    if (!form) return;
    if (!form.nombre.trim()) {
      toast.error("Escribe el nombre del producto primero");
      return;
    }
    setSuggesting(true);
    try {
      const s = await apiFetch<{
        opciones: SatOpcion[];
        unidad_sat: string;
        descripcion_unidad: string;
        confianza: string;
      }>("/api/v1/sat/sugerir", {
        method: "POST",
        body: JSON.stringify({ nombre: form.nombre, descripcion: form.descripcion || null }),
      });
      setSatOpciones(s.opciones);
      setForm((f) =>
        f ? { ...f, clave_sat: s.opciones[0]?.clave_sat ?? f.clave_sat, unidad_sat: s.unidad_sat || f.unidad_sat } : f
      );
      toast.success(`Sugerencias SAT (confianza ${s.confianza}) — elige la clave`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo sugerir");
    } finally {
      setSuggesting(false);
    }
  }

  function openCreate() {
    setEditingId(null);
    setSatOpciones([]);
    setForm(emptyForm());
  }
  // Alta tras el buscador: crea con el nombre ya tecleado (evita duplicados).
  function openCreateWith(nombre: string) {
    setEditingId(null);
    setSatOpciones([]);
    setForm({ ...emptyForm(), nombre });
  }
  function openEdit(p: Producto) {
    setEditingId(p.id);
    setSatOpciones([]);
    setForm(toForm(p));
  }

  async function save() {
    if (!form) return;
    if (!form.nombre.trim()) {
      toast.error("El nombre es obligatorio");
      return;
    }
    const unidadBase = form.unidad_base.trim() || "KILO";
    // Build the presentation→base-units map; the base unit is always 1:1.
    const presentaciones: Record<string, number> = { [unidadBase]: 1 };
    for (const r of form.presentaciones) {
      const nombre = r.nombre.trim();
      if (!nombre || nombre === unidadBase) continue;
      const factor = Number(r.factor);
      if (!Number.isFinite(factor) || factor <= 0) {
        toast.error(`Factor inválido para "${nombre}" (debe ser mayor a 0)`);
        return;
      }
      presentaciones[nombre] = factor;
    }
    const payload = {
      ...(form.sku.trim() ? { sku: form.sku.trim() } : {}),  // vacío → backend autogenera
      nombre: form.nombre.trim(),
      descripcion: form.descripcion.trim() || null,
      categoria_id: form.categoria_id || null,
      esquema_impuesto_id: form.esquema_impuesto_id || null,
      clave_sat: form.clave_sat.trim(),
      unidad_sat: form.unidad_sat.trim(),
      unidad_base: unidadBase,
      presentaciones,
      activo: form.activo,
      perecedero: form.perecedero,
      requiere_lote: form.requiere_lote,
    };
    try {
      if (editingId) {
        await patch(`/api/v1/productos/${editingId}`, payload);
        toast.success("Producto actualizado");
      } else {
        await post("/api/v1/productos", payload);
        toast.success("Producto creado");
      }
      setForm(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    try {
      await del(`/api/v1/productos/${toDelete.id}`);
      toast.success("Producto eliminado");
      setToDelete(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const columns: Column<Producto>[] = [
    { header: "SKU", cell: (p) => <span className="font-medium">{p.sku}</span> },
    { header: "Nombre", cell: (p) => p.nombre },
    { header: "Categoría", cell: (p) => (p.categoria_id ? catName[p.categoria_id] ?? "—" : "—") },
    { header: "Clave SAT", cell: (p) => <span className="text-muted">{p.clave_sat}</span> },
    {
      header: "Estado",
      cell: (p) => <Badge tone={p.activo ? "success" : "muted"}>{p.activo ? "Activo" : "Inactivo"}</Badge>,
    },
    {
      header: "",
      className: "text-right w-1",
      cell: (p) =>
        canWrite ? (
          <div className="flex justify-end gap-1">
            <button
              onClick={(e) => {
                e.stopPropagation();
                openEdit(p);
              }}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
              aria-label="Editar"
            >
              <Pencil size={16} />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setToDelete(p);
              }}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
              aria-label="Eliminar"
            >
              <Trash2 size={16} />
            </button>
          </div>
        ) : null,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Productos"
        subtitle="Catálogo de productos"
        actions={
          canWrite ? (
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setImportOpen(true)}>
                <FileUp size={16} /> Importar
              </Button>
              <Button onClick={() => { setPickerText(""); setPickerOpen(true); }}>
                <Plus size={16} /> Nuevo producto
              </Button>
            </div>
          ) : undefined
        }
      />

      <ImportarProductosModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onDone={reload}
      />

      <DataTableSmart columns={columns} rows={rows} loading={loading} error={error} empty="Sin productos" storageKey="productos" />

      {/* Alta: buscar primero (evita duplicados) → elegir existente o crear nuevo */}
      <Modal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        title="Nuevo producto"
        footer={<Button variant="secondary" onClick={() => setPickerOpen(false)}>Cancelar</Button>}
      >
        <div className="space-y-3">
          <p className="text-sm text-muted">Busca primero para evitar duplicados. Si no existe, créalo.</p>
          <ProductoCombobox
            autoFocus
            placeholder="Buscar producto por nombre o SKU…"
            onSelect={async (p, texto) => {
              setPickerText(texto);
              if (!p) return;
              setPickerOpen(false);
              // Un match existente NUNCA debe crear un duplicado: si está en la
              // lista cargada lo editamos directo; si no (catálogo grande), lo
              // traemos por id. Solo se crea desde el botón "Crear nuevo".
              const full = rows.find((r) => r.id === p.producto_id);
              if (full) { openEdit(full); return; }
              try {
                openEdit(await apiFetch<Producto>(`/api/v1/productos/${p.producto_id}`));
              } catch {
                toast.error("No se pudo abrir el producto seleccionado");
              }
            }}
          />
          <Button
            variant="secondary"
            onClick={() => { setPickerOpen(false); openCreateWith(pickerText.trim()); }}
          >
            <Plus size={16} /> Crear nuevo producto{pickerText.trim() ? ` «${pickerText.trim()}»` : ""}
          </Button>
        </div>
      </Modal>

      <Modal
        open={form !== null}
        onClose={() => setForm(null)}
        title={editingId ? "Editar producto" : "Nuevo producto"}
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Cancelar
            </Button>
            <Button onClick={save} disabled={saving}>
              {saving ? "Guardando…" : "Guardar"}
            </Button>
          </>
        }
      >
        {form && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* SKU — automático */}
            <div className="sm:col-span-2">
              <Field label="SKU" hint={editingId ? undefined : "Se genera automáticamente al guardar"}>
                <Input value={editingId ? form.sku : ""} placeholder="(automático)" disabled className="max-w-[14rem]" />
              </Field>
            </div>
            {/* nombre + unidad base */}
            <Field label="Nombre" required>
              <Input value={form.nombre} onChange={(e) => setForm({ ...form, nombre: e.target.value })} />
            </Field>
            <Field label="Unidad base" hint="Unidad de inventario (todo el stock se guarda aquí)">
              <Select value={form.unidad_base} onChange={(e) => setForm({ ...form, unidad_base: e.target.value })}>
                {(UNIDADES_BASE.includes(form.unidad_base) ? UNIDADES_BASE : [form.unidad_base, ...UNIDADES_BASE]).map((u) => (
                  <option key={u} value={u}>{u}</option>
                ))}
              </Select>
            </Field>
            <div className="sm:col-span-2">
              <Field label="Categoría">
                <Select value={form.categoria_id} onChange={(e) => setForm({ ...form, categoria_id: e.target.value })}>
                  <option value="">— Sin categoría —</option>
                  {categorias.map((c) => (<option key={c.id} value={c.id}>{c.nombre}</option>))}
                </Select>
              </Field>
            </div>
            <div className="sm:col-span-2">
              <Field
                label="Esquema de impuestos"
                hint={
                  esquemas.length === 0
                    ? "No hay esquemas dados de alta — créalos en Ajustes › Esquemas de impuesto"
                    : "Define IVA/IEPS/retenciones aplicables al producto"
                }
              >
                <Select
                  value={form.esquema_impuesto_id}
                  onChange={(e) => setForm({ ...form, esquema_impuesto_id: e.target.value })}
                  disabled={esquemas.length === 0}
                >
                  <option value="">— Sin esquema —</option>
                  {esquemas.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.codigo} · {e.nombre} (IVA {Math.round(Number(e.iva_tasa) * 100)}%)
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            {/* Clasificación SAT (CFDI) */}
            <div className="sm:col-span-2 rounded-lg border border-border bg-surface-2/40 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">Clasificación SAT (CFDI)</span>
                <Button type="button" variant="secondary" onClick={suggestSat} disabled={suggesting}>
                  <Sparkles size={16} /> {suggesting ? "Sugiriendo…" : "Sugerir con IA"}
                </Button>
              </div>
              {satOpciones.length > 0 && (
                <div className="mb-3 space-y-1">
                  <span className="text-xs text-muted">Opciones sugeridas — elige la clave:</span>
                  {satOpciones.map((o) => (
                    <button
                      key={o.clave_sat}
                      type="button"
                      onClick={() => setForm({ ...form, clave_sat: o.clave_sat })}
                      className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-sm ${
                        form.clave_sat === o.clave_sat ? "border-accent bg-accent/10" : "border-border hover:bg-surface-2"
                      }`}
                    >
                      <span className="font-mono">{o.clave_sat}</span>
                      <span className="text-muted">— {o.descripcion}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Clave SAT (producto/servicio)">
                  <Input value={form.clave_sat} onChange={(e) => setForm({ ...form, clave_sat: e.target.value })} />
                </Field>
                <Field label="Unidad SAT">
                  <Select value={form.unidad_sat} onChange={(e) => setForm({ ...form, unidad_sat: e.target.value })}>
                    {(UNIDADES_SAT.some((u) => u.code === form.unidad_sat)
                      ? UNIDADES_SAT
                      : [{ code: form.unidad_sat, nombre: form.unidad_sat }, ...UNIDADES_SAT]
                    ).map((u) => (
                      <option key={u.code} value={u.code}>{u.code} — {u.nombre}</option>
                    ))}
                  </Select>
                </Field>
              </div>
            </div>

            <div className="sm:col-span-2 rounded-lg border border-border bg-surface-2/40 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">Presentaciones</span>
                <span className="text-xs text-muted">Factor = unidades base por presentación</span>
              </div>
              <div className="mb-2 rounded-md bg-surface-2 px-3 py-2 text-sm">
                Base: <b>{form.unidad_base}</b> = 1 — la unidad de inventario
              </div>
              <div className="space-y-2">
                {form.presentaciones.map((r, i) => {
                  // Opciones predefinidas, sin repetir la unidad base; conserva el valor
                  // actual aunque no esté en la lista (datos previos).
                  const opts = UNIDADES_BASE.filter((u) => u !== form.unidad_base);
                  const nombreOpts = r.nombre && !opts.includes(r.nombre) ? [r.nombre, ...opts] : opts;
                  return (
                  <div key={i} className="grid grid-cols-[1fr_6rem_auto] items-center gap-2">
                    <Select
                      value={r.nombre}
                      onChange={(e) => {
                        const next = [...form.presentaciones];
                        next[i] = { ...next[i], nombre: e.target.value };
                        setForm({ ...form, presentaciones: next });
                      }}
                    >
                      <option value="">— Presentación —</option>
                      {nombreOpts.map((u) => (
                        <option key={u} value={u}>{u}</option>
                      ))}
                    </Select>
                    <Input
                      type="number"
                      step="0.0001"
                      min="0"
                      placeholder="Factor"
                      value={r.factor}
                      onChange={(e) => {
                        const next = [...form.presentaciones];
                        next[i] = { ...next[i], factor: e.target.value };
                        setForm({ ...form, presentaciones: next });
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => setForm({ ...form, presentaciones: form.presentaciones.filter((_, j) => j !== i) })}
                      className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                      aria-label="Quitar presentación"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  );
                })}
                {form.presentaciones.length === 0 && (
                  <p className="text-xs text-muted">Solo la unidad base. Agrega CAJA/BULTO si compras o vendes en esas presentaciones.</p>
                )}
              </div>
              <Button
                type="button"
                variant="secondary"
                className="mt-2"
                onClick={() => setForm({ ...form, presentaciones: [...form.presentaciones, { nombre: "", factor: "" }] })}
              >
                <Plus size={16} /> Agregar presentación
              </Button>
            </div>

            <div className="sm:col-span-2">
              <Field label="Descripción">
                <Textarea
                  rows={2}
                  value={form.descripcion}
                  onChange={(e) => setForm({ ...form, descripcion: e.target.value })}
                />
              </Field>
            </div>
            <div className="flex items-center gap-3">
              <Switch checked={form.activo} onChange={(v) => setForm({ ...form, activo: v })} />
              <span className="text-sm">Activo</span>
            </div>
            <div className="flex items-center gap-3">
              <Switch checked={form.perecedero} onChange={(v) => setForm({ ...form, perecedero: v })} />
              <span className="text-sm">Perecedero</span>
            </div>
            <div className="flex items-center gap-3">
              <Switch checked={form.requiere_lote} onChange={(v) => setForm({ ...form, requiere_lote: v })} />
              <span className="text-sm">Requiere lote</span>
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Eliminar producto"
        message={`¿Eliminar "${toDelete?.nombre}"? Se puede recrear después.`}
        onConfirm={confirmDelete}
        onClose={() => setToDelete(null)}
        loading={saving}
      />
    </div>
  );
}

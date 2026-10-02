"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { FileUp, PackagePlus, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";

import { AltaSaeModal } from "@/components/AltaSaeModal";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { candidatosDuplicados, type CandidatoDuplicado } from "@/components/CrearProductoModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { ClaveSaeInline } from "@/components/ClaveSaeInline";
import { ProductoAliasPanel } from "@/components/ProductoAliasPanel";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { SatClaveCombobox } from "@/components/SatClaveCombobox";
import { ApiError, apiFetch } from "@/lib/api";
import { can, canAny, useAuth } from "@/lib/auth";
import { useListadoCompleto, useMutation, useResource, type Page } from "@/lib/hooks";
import { useToast } from "@/components/ui/Toast";
import type { Categoria, EsquemaImpuesto, Producto } from "@/lib/types";

const WRITE = "producto:gestionar";
// Borrar un producto es un permiso aparte de gestionarlo (producto:eliminar).
const DELETE = "producto:eliminar";
// Pedir el alta en SAE: quien gestiona el catálogo o quien sólo puede pedirlas.
const ALTA_SAE = "producto:alta_sae";

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
// `extra` guarda lo demás de la forma rica ({sat, estimado, …}): el formulario
// solo edita factor y clave, y reescribir la presentación como número a secas
// borraba la unidad SAT de la CAJA/PIEZA al guardar cualquier otra cosa.
type PresRow = { nombre: string; factor: string; clave_sae: string; extra: Record<string, unknown> };

/** La clave de SAE de cada presentación del producto ({PIEZA: "SANDIAPZ"}). */
function clavesPorPresentacion(p: Producto): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [nombre, v] of Object.entries(p.presentaciones ?? {})) {
    const raw = v as unknown;
    if (raw && typeof raw === "object") {
      const clave = String((raw as { clave_sae?: string }).clave_sae ?? "").trim();
      if (clave) out[nombre] = clave;
    }
  }
  return out;
}

/** Un renglón de la tabla: el producto EN UNA de sus unidades. La SANDIA sale
 *  dos veces (KILO → SANDIAKG, PIEZA → SANDIAPZ): es un solo producto con un
 *  solo SKU, pero en SAE son dos artículos y cada unidad tiene su precio. */
type FilaUnidad = { p: Producto; unidad: string; esBase: boolean; clave: string | null };

function filasPorUnidad(productos: Producto[]): FilaUnidad[] {
  const out: FilaUnidad[] = [];
  for (const p of productos) {
    const base = p.unidad_base ?? "KILO";
    const porPres = clavesPorPresentacion(p);
    // La base primero; luego las demás en el orden en que están guardadas.
    const unidades = [base, ...Object.keys(p.presentaciones ?? {}).filter((k) => k !== base)];
    for (const unidad of unidades) {
      const esBase = unidad === base;
      out.push({ p, unidad, esBase, clave: (esBase ? p.clave_sae : porPres[unidad]) || null });
    }
  }
  return out;
}

type FormState = {
  sku: string;
  nombre: string;
  descripcion: string;
  categoria_id: string;
  esquema_impuesto_id: string;
  clave_sat: string;
  clave_sae: string;
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
    clave_sae: "",
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
      if (typeof f === "object" && f !== null) {
        const { factor: num, clave_sae, ...extra } = f as { factor?: number; clave_sae?: string };
        return { nombre, factor: String(num ?? 1), clave_sae: clave_sae ?? "", extra };
      }
      return { nombre, factor: String(f as number), clave_sae: "", extra: {} };
    });
  return {
    sku: p.sku,
    nombre: p.nombre,
    descripcion: p.descripcion ?? "",
    categoria_id: p.categoria_id ?? "",
    esquema_impuesto_id: p.esquema_impuesto_id ?? "",
    clave_sat: p.clave_sat,
    clave_sae: p.clave_sae ?? "",
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
  const canDelete = can(me, DELETE);
  // Sólo el tenant dueño de SAE: a cualquier otro la cola le contesta 403.
  const canAltaSae = !!me?.active_tenant.sae_conectado && canAny(me, [WRITE, ALTA_SAE]);
  const [altaSae, setAltaSae] = useState<Producto | null>(null);

  const categoriasRes = useResource<Page<Categoria>>("/api/v1/categorias?limit=200");
  const categorias = useMemo(() => categoriasRes.data?.items ?? [], [categoriasRes.data]);
  const catName = useMemo(
    () => Object.fromEntries(categorias.map((c) => [c.id, c.nombre])),
    [categorias]
  );

  // Esquemas de impuesto ya dados de alta (para asignarlos al producto).
  const esquemasRes = useResource<Page<EsquemaImpuesto>>("/api/v1/esquemas-impuesto?limit=200");
  const esquemasTodos = useMemo(() => esquemasRes.data?.items ?? [], [esquemasRes.data]);
  const esquemas = esquemasTodos.filter((e) => e.activo);
  // Para la columna se usan TODOS, no solo los activos: un producto puede
  // seguir apuntando a un esquema dado de baja y la tabla debe poder nombrarlo
  // en vez de dejar un guion que parece "sin esquema".
  const esqName = useMemo(
    () => Object.fromEntries(esquemasTodos.map((e) => [e.id, e.nombre || e.codigo])),
    [esquemasTodos]
  );

  // Se carga la lista completa: DataTableSmart se encarga de la paginación,
  // búsqueda y orden en cliente sobre todas las filas.
  const { data, loading, error, reload } = useListadoCompleto<Producto>(
    "/api/v1/productos"
  );
  const rows = useMemo(() => data?.items ?? [], [data]);
  const filas = useMemo(() => filasPorUnidad(rows), [rows]);
  // Los desactivados (gemelos ya fusionados) estorban al buscar: se esconden
  // salvo que se pidan.
  const [verInactivos, setVerInactivos] = useState(false);

  const [form, setForm] = useState<FormState | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [tab, setTab] = useState<"datos" | "alias">("datos");
  const [toDelete, setToDelete] = useState<Producto | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [satOpciones, setSatOpciones] = useState<SatOpcion[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerText, setPickerText] = useState("");
  // Productos del catálogo que se parecen al que se está dando de alta: el
  // backend los manda en el 409 para poder abrir el que ya existe en vez de
  // fabricar el segundo "cilantro".
  const [parecidos, setParecidos] = useState<CandidatoDuplicado[]>([]);

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
    setParecidos([]);
    setForm(emptyForm());
  }
  // Alta tras el buscador: crea con el nombre ya tecleado (evita duplicados).
  function openCreateWith(nombre: string) {
    setEditingId(null);
    setSatOpciones([]);
    setParecidos([]);
    setForm({ ...emptyForm(), nombre });
  }
  const openEdit = useCallback((p: Producto) => {
    setEditingId(p.id);
    setSatOpciones([]);
    setParecidos([]);
    setTab("datos");        // abrir siempre en Datos, aunque el anterior se cerró en alias
    setForm(toForm(p));
  }, []);

  async function save(forzar = false) {
    if (!form) return;
    if (!form.nombre.trim()) {
      toast.error("El nombre es obligatorio");
      return;
    }
    // Sin esquema el producto nace sin IVA y su CFDI sale mal. Solo al CREAR:
    // editar un producto viejo que arrastra el hueco no debe quedar bloqueado
    // (es justo la pantalla donde se arregla).
    if (!editingId && !form.esquema_impuesto_id) {
      toast.error("Elige el esquema de impuesto");
      return;
    }
    const unidadBase = form.unidad_base.trim() || "KILO";
    // Build the presentation→base-units map; the base unit is always 1:1.
    const presentaciones: Record<string, number | Record<string, unknown>> = { [unidadBase]: 1 };
    for (const r of form.presentaciones) {
      const nombre = r.nombre.trim();
      if (!nombre || nombre === unidadBase) continue;
      const factor = Number(r.factor);
      if (!Number.isFinite(factor) || factor <= 0) {
        toast.error(`Factor inválido para "${nombre}" (debe ser mayor a 0)`);
        return;
      }
      const clave = r.clave_sae.trim().toUpperCase();
      // Número a secas solo si no hay nada más que guardar (la forma de siempre).
      presentaciones[nombre] =
        clave || Object.keys(r.extra).length
          ? { ...r.extra, factor, ...(clave ? { clave_sae: clave } : {}) }
          : factor;
    }
    const payload = {
      ...(form.sku.trim() ? { sku: form.sku.trim() } : {}),  // vacío → backend autogenera
      nombre: form.nombre.trim(),
      descripcion: form.descripcion.trim() || null,
      categoria_id: form.categoria_id || null,
      esquema_impuesto_id: form.esquema_impuesto_id || null,
      clave_sat: form.clave_sat.trim(),
      // La clave del artículo en SAE: vacía se manda como null (quitarla es
      // legítimo), y el backend la normaliza a mayúsculas sin espacios.
      clave_sae: form.clave_sae.trim() || null,
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
        await post("/api/v1/productos", { ...payload, forzar });
        toast.success("Producto creado");
      }
      setForm(null);
      setParecidos([]);
      reload();
    } catch (e) {
      // 409 con candidatos: el catálogo ya tiene algo muy parecido. No es un
      // error que se cierre con un toast — hay que decidir entre usar el que
      // existe o crear otro a sabiendas.
      const dups = candidatosDuplicados(e);
      if (dups.length) {
        setParecidos(dups);
        return;
      }
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

  // Cada columna lleva `sortValue`: es el texto que el buscador de la tabla
  // indexa (y el que sale al exportar a Excel). Sin él, las celdas JSX no
  // aportan texto y buscar no encontraba nada.
  const columns = useMemo<Column<FilaUnidad>[]>(() => [
    { header: "SKU", sortValue: ({ p }) => p.sku, cell: ({ p }) => <span className="font-medium">{p.sku}</span> },
    { header: "Nombre", truncate: true, sortValue: ({ p }) => p.nombre, cell: ({ p }) => <span title={p.nombre}>{p.nombre}</span> },
    { header: "Unidad", sortValue: (f) => f.unidad, cell: (f) => f.unidad },
    {
      header: "Clave SAE",
      sortValue: (f) => f.clave ?? "",
      cell: (f) =>
        f.clave ? (
          <span className="tabular-nums">{f.clave}</span>
        ) : (
          <span
            className="text-warning"
            title={f.esBase
              ? "Sin ella, este producto sale «sin clave» en cada cliente que no lo tenga en su catálogo"
              : `${f.unidad} no tiene clave propia: al exportar saldría con la de ${f.p.unidad_base ?? "la unidad base"}`}
          >
            —
          </span>
        ),
    },
    {
      header: "Categoría",
      sortValue: ({ p }) => (p.categoria_id ? catName[p.categoria_id] ?? "" : ""),
      cell: ({ p }) => (p.categoria_id ? catName[p.categoria_id] ?? "—" : "—"),
    },
    {
      header: "Esquema de impuesto",
      sortValue: ({ p }) => (p.esquema_impuesto_id ? esqName[p.esquema_impuesto_id] ?? "" : "Sin esquema"),
      cell: ({ p }) =>
        p.esquema_impuesto_id ? (
          esqName[p.esquema_impuesto_id] ?? "—"
        ) : (
          <span className="text-danger">Sin esquema</span>
        ),
    },
    { header: "Clave SAT", sortValue: ({ p }) => p.clave_sat, cell: ({ p }) => <span className="text-muted">{p.clave_sat}</span> },
    {
      // La descripción oficial del SAT para esa clave: la resuelve el backend
      // (el producto solo guarda la clave). Sin ella, los 8 dígitos no dicen
      // nada al revisar si la clave que quedó es la correcta.
      header: "Descripción SAT",
      truncate: true,
      sortValue: ({ p }) => p.clave_sat_descripcion ?? "",
      cell: ({ p }) => <span className="text-muted" title={p.clave_sat_descripcion ?? ""}>{p.clave_sat_descripcion || "—"}</span>,
    },
    {
      header: "Estado",
      sortValue: ({ p }) => (p.activo ? "Activo" : "Inactivo"),
      cell: ({ p }) => <Badge tone={p.activo ? "success" : "muted"}>{p.activo ? "Activo" : "Inactivo"}</Badge>,
    },
    {
      header: "",
      className: "text-right w-1",
      cell: ({ p }) =>
        canWrite || canDelete || canAltaSae ? (
          <div className="flex justify-end gap-1">
            {canAltaSae && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setAltaSae(p);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
                aria-label="Dar de alta en SAE"
                title="Dar de alta en SAE"
              >
                <PackagePlus size={16} />
              </button>
            )}
            {canWrite && (
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
            )}
            {canDelete && (
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
            )}
          </div>
        ) : null,
    },
  ], [catName, esqName, canWrite, canDelete, canAltaSae, openEdit]);

  return (
    <div>
      <PageHeader
        title="Productos"
        subtitle="Catálogo de productos"
        actions={
          canWrite ? (
            <div className="flex gap-2">
              <Link
                href="/productos/importar"
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-background px-3.5 py-2 text-sm font-medium transition hover:bg-surface-2"
              >
                <FileUp size={16} /> Importar
              </Link>
              <Button onClick={() => { setPickerText(""); setPickerOpen(true); }}>
                <Plus size={16} /> Nuevo producto
              </Button>
            </div>
          ) : undefined
        }
      />

      <AltaSaeModal
        producto={altaSae}
        esquemas={esquemasTodos}
        escritor={me?.active_tenant.sae_escritor}
        onClose={() => setAltaSae(null)}
      />

      <DataTableSmart
        columns={columns}
        rows={filas}
        rowKey={(f) => `${f.p.id}:${f.unidad}`}
        rowFilter={(f) => verInactivos || f.p.activo}
        rowFilterKey={verInactivos ? "todos" : "activos"}
        toolbarExtra={
          <label className="flex items-center gap-2 text-sm text-muted">
            <Switch checked={verInactivos} onChange={setVerInactivos} /> Ver inactivos
          </label>
        }
        loading={loading}
        error={error}
        empty="Sin productos"
        // Clave nueva: la tabla cambió de forma (un renglón por unidad) y el
        // orden/visibilidad guardados de la anterior no le aplican.
        storageKey="productos-por-unidad"
      />

      {/* Alta: buscar primero (evita duplicados) → elegir existente o crear nuevo */}
      <Modal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        title="Nuevo producto"
        description="Busca primero para evitar duplicados. Si no existe, créalo."
        size="md"
        footer={<Button variant="secondary" onClick={() => setPickerOpen(false)}>Cancelar</Button>}
      >
        <div className="space-y-3">
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
        // Ancho: el vocabulario de un producto se lee agrupado por cliente y en
        // el modal angosto el texto se partía en vertical, una letra por renglón.
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Cancelar
            </Button>
            {parecidos.length ? (
              <Button variant="secondary" onClick={() => save(true)} disabled={saving}>
                {saving ? "Guardando…" : "Es distinto — crearlo igual"}
              </Button>
            ) : (
              <Button onClick={() => save()} disabled={saving}>
                {saving ? "Guardando…" : "Guardar"}
              </Button>
            )}
          </>
        }
      >
        {form && parecidos.length ? (
          <Alert tone="warning">
            <div className="font-medium">
              Esto ya podría estar en el catálogo. El mismo producto con dos nombres se
              vuelve dos inventarios y dos precios — si es el mismo, ábrelo y ponle el
              nombre del cliente en su catálogo.
            </div>
            <ul className="mt-2 space-y-1">
              {parecidos.map((c) => (
                <li key={c.producto_id} className="flex items-center justify-between gap-3">
                  <span className="text-sm">
                    {c.nombre} <span className="text-xs text-muted">({c.sku})</span>
                  </span>
                  <Button
                    variant="secondary"
                    onClick={async () => {
                      const full = rows.find((r) => r.id === c.producto_id);
                      if (full) {
                        openEdit(full);
                        return;
                      }
                      try {
                        openEdit(await apiFetch<Producto>(`/api/v1/productos/${c.producto_id}`));
                      } catch {
                        toast.error("No se pudo abrir el producto");
                      }
                    }}
                  >
                    Abrir el que existe
                  </Button>
                </li>
              ))}
            </ul>
          </Alert>
        ) : null}

        {/* Las pestañas van sueltas y no con <Tabs/>: el pie del modal ("Guardar")
            tiene que seguir atado al formulario, y meter todo el formulario dentro
            del prop `content` de Tabs lo desconectaría. Mismas clases, mismo look. */}
        {form && editingId && (
          <div className="flex gap-1 border-b border-border">
            {([
              ["datos", "Datos"],
              ["alias", "Así lo escriben"],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
                  tab === id
                    ? "border-accent text-foreground"
                    : "border-transparent text-muted hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {form && editingId && tab === "alias" && (
          <div className="mt-3">
            <ProductoAliasPanel productoId={editingId} productoNombre={form.nombre} />
          </div>
        )}

        {form && (!editingId || tab === "datos") && (
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* SKU — automático */}
            <div className="sm:col-span-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
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
                  <SatClaveCombobox
                    value={form.clave_sat}
                    onChange={(v) => setForm((f) => (f ? { ...f, clave_sat: v } : f))}
                  />
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
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-medium">Presentaciones y claves de SAE</span>
                <span className="text-xs text-muted">Factor = unidades base por presentación</span>
              </div>
              <p className="mb-2 text-xs text-muted">
                SAE tiene un artículo por unidad (SANDIAKG, SANDIAPZ): pon la clave de cada una y la
                línea sale a SAE con la de SU presentación. Sin clave propia, usa la de la base.
              </p>
              <div className="mb-2 grid grid-cols-[1fr_6rem_minmax(0,12rem)_2rem] items-center gap-2 rounded-md bg-surface-2 px-3 py-2 text-sm">
                <span>Base: <b>{form.unidad_base}</b> <span className="text-xs text-muted">(inventario)</span></span>
                <span className="text-muted">= 1</span>
                <ClaveSaeInline
                  compacto
                  productoId={editingId ?? ""}
                  productoNombre={form.nombre}
                  value={form.clave_sae}
                  onChange={(v) => setForm((f) => (f ? { ...f, clave_sae: v.toUpperCase() } : f))}
                />
                <span />
              </div>
              <div className="space-y-2">
                {form.presentaciones.map((r, i) => {
                  // Opciones predefinidas, sin repetir la unidad base; conserva el valor
                  // actual aunque no esté en la lista (datos previos).
                  const opts = UNIDADES_BASE.filter((u) => u !== form.unidad_base);
                  const nombreOpts = r.nombre && !opts.includes(r.nombre) ? [r.nombre, ...opts] : opts;
                  return (
                  <div key={i} className="grid grid-cols-[1fr_6rem_minmax(0,12rem)_2rem] items-center gap-2 px-3">
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
                    <ClaveSaeInline
                      compacto
                      productoId={editingId ?? ""}
                      productoNombre={form.nombre}
                      value={r.clave_sae}
                      onChange={(v) =>
                        setForm((f) => {
                          if (!f) return f;
                          const next = [...f.presentaciones];
                          next[i] = { ...next[i], clave_sae: v.toUpperCase() };
                          return { ...f, presentaciones: next };
                        })
                      }
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
                onClick={() => setForm({ ...form, presentaciones: [...form.presentaciones, { nombre: "", factor: "", clave_sae: "", extra: {} }] })}
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

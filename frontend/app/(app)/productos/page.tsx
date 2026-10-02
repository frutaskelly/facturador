"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { FileUp, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { candidatosDuplicados, type CandidatoDuplicado } from "@/components/CrearProductoModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { DescripcionSat } from "@/components/DescripcionSat";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { SatClaveCombobox } from "@/components/SatClaveCombobox";
import {
  UNIDADES_INVENTARIO,
  UnidadesClavesSae,
  altasDesdeFilas,
  armarPresentaciones,
  cambiarUnidadBase,
  cambiosSaeDesdeFilas,
  estadoDeClave,
  filaBase,
  filasDesdeProducto,
  indexarEstados,
  normalizarClave,
  problemasClavesSae,
  validarClavesSae,
  type AltaSaeResumen,
  type ClaveSaeEstado,
  type FilaClave,
} from "@/components/UnidadesClavesSae";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useListadoCompleto, useMutation, useResource, type Page } from "@/lib/hooks";
import { useToast } from "@/components/ui/Toast";
import type { Categoria, EsquemaImpuesto, Producto } from "@/lib/types";

const WRITE = "producto:gestionar";
// Borrar un producto es un permiso aparte de gestionarlo (producto:eliminar).
const DELETE = "producto:eliminar";

// Unidades base más comunes (unidad interna de inventario).
const UNIDADES_BASE = UNIDADES_INVENTARIO;

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

/** Lo que contestan POST/PATCH /productos: el producto y las altas en SAE que
 *  se encolaron (o se reusaron) en esa misma llamada. */
type ProductoGuardado = Producto & { altas_sae?: AltaSaeResumen[] };

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

/** SKU exclusivo de cliente: el artículo de SAE con el que ESOS clientes facturan
 *  el producto en esa unidad (ZANA-FRUT-508 de Balles y Jubran). */
type ClaveCliente = { producto_id: string; unidad: string; clave: string; clientes: string[] };

// «OPERADORA BALLES VEGA DE HIDALGO» → BALLES: la palabra que lo distingue.
const RELLENO = new Set([
  "OPERADORA", "OPERADOR", "DISTRIBUIDORA", "COMERCIALIZADORA", "GRUPO", "MEDIOS", "DE", "DEL", "LA", "LOS",
  "Y", "ALIMENTOS", "ALIMENTACION", "PRODUCTOS", "SA", "CV", "S", "A", "C", "V",
]);
function nombreCorto(legal: string): string {
  return legal.toUpperCase().split(/[\s.,]+/).find((w) => w && !RELLENO.has(w)) ?? legal;
}

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
  unidad_sat: string;
  unidad_base: string;
  // Una fila por unidad: la base (filas[0]) y cada presentación, cada una con
  // su clave de SAE. Las filas guardan además lo demás de la presentación rica
  // ({sat, estimado, …}): reescribirla como número a secas borraba la unidad
  // SAT de la CAJA/PIEZA al guardar cualquier otra cosa.
  filas: FilaClave[];
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
    filas: [filaBase("KILO")],   // la base es 1:1; las demás se agregan
    activo: true,
    perecedero: false,
    requiere_lote: false,
  };
}

function toForm(p: Producto): FormState {
  const base = p.unidad_base ?? "KILO";
  return {
    sku: p.sku,
    nombre: p.nombre,
    descripcion: p.descripcion ?? "",
    categoria_id: p.categoria_id ?? "",
    esquema_impuesto_id: p.esquema_impuesto_id ?? "",
    clave_sat: p.clave_sat,
    unidad_sat: p.unidad_sat,
    unidad_base: base,
    filas: filasDesdeProducto(p),
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
  // Sólo el tenant dueño de SAE exige clave SAE por unidad y pide altas allá
  // (regla del dueño, 2-oct-2026). Las altas ya no tienen botón propio en la
  // fila: viven en el editor, junto a la unidad que las necesita.
  const saeConectado = !!me?.active_tenant.sae_conectado;

  // Cómo va cada clave del catálogo en SAE: ligada, alta pendiente, con error…
  const estadosRes = useResource<ClaveSaeEstado[]>(
    saeConectado ? "/api/v1/productos/claves-sae/estado" : null
  );
  const estados = useMemo(() => indexarEstados(estadosRes.data), [estadosRes.data]);
  const clavesClienteRes = useResource<ClaveCliente[]>("/api/v1/productos/claves-cliente");
  const clavesCliente = useMemo(() => {
    const m: Record<string, ClaveCliente[]> = {};
    for (const c of clavesClienteRes.data ?? []) (m[`${c.producto_id}:${c.unidad}`] ??= []).push(c);
    return m;
  }, [clavesClienteRes.data]);

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
  const [enviando, setEnviando] = useState(false);
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
    setForm(toForm(p));
  }, []);

  // El esquema elegido: su `codigo` es el número de esquema de SAE.
  const esquemaSel = form ? esquemasTodos.find((e) => e.id === form.esquema_impuesto_id) ?? null : null;
  const esquemaCodigo = esquemaSel?.codigo.trim() || null;
  const nAltas = saeConectado && form ? form.filas.filter((f) => f.estado === "nueva").length : 0;

  // Lo que impide guardar, en orden: el primero va en rojo al pie del modal y
  // el botón se apaga. Reglas del dueño (2-oct-2026): categoría obligatoria y,
  // en el tenant dueño de SAE, ningún producto activo sin clave en CADA unidad.
  const faltas: string[] = [];
  if (form) {
    if (!form.categoria_id) faltas.push("Elige la categoría");
    const sinClave = form.activo ? validarClavesSae(form.filas, saeConectado) : [];
    if (sinClave.length) faltas.push(`Falta la clave SAE de ${sinClave.join(", ")}`);
    faltas.push(...problemasClavesSae(form.filas, form.nombre, saeConectado));
    if (nAltas) {
      // El alta lleva el esquema y la clave SAT del producto: sin ellos SAE
      // la rechaza media hora después, cuando ya nadie está viendo.
      if (!esquemaCodigo || !/^\d+$/.test(esquemaCodigo)) {
        faltas.push("Para pedir el alta en SAE, elige un esquema de impuesto con número de SAE");
      }
      if (!/^\d{8}$/.test(form.clave_sat.trim())) {
        faltas.push("Para pedir el alta en SAE, la clave SAT lleva 8 dígitos");
      }
    }
  }

  // «Usar la de SAE» desde «Así está en SAE»: se cambia en Datos.
  const usarSatDeSae = useCallback((clave: string) => {
    setForm((f) => (f ? { ...f, clave_sat: clave } : f));
  }, []);
  function usarEsquemaDeSae(codigo: number) {
    const esq = esquemas.find((e) => e.codigo.trim() === String(codigo));
    if (!esq) {
      toast.error(`No hay un esquema activo con el número ${codigo} de SAE: créalo en Esquemas de impuesto`);
      return;
    }
    setForm((f) => (f ? { ...f, esquema_impuesto_id: esq.id } : f));
  }

  async function save(forzar = false) {
    if (!form || enviando) return;
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
    if (faltas.length) {
      toast.error(faltas[0]);
      return;
    }
    // La base es siempre 1:1; cada presentación lleva su factor y su clave.
    const armado = armarPresentaciones(form.filas);
    if (armado.error) {
      toast.error(armado.error);
      return;
    }
    // Las claves nuevas viajan CON el producto: el backend las encola en la
    // misma transacción, así el producto nunca existe sin su clave.
    const altas = saeConectado ? altasDesdeFilas(form.filas, form.nombre.trim(), form.unidad_sat) : [];
    const payload = {
      ...(form.sku.trim() ? { sku: form.sku.trim() } : {}),  // vacío → backend autogenera
      nombre: form.nombre.trim(),
      descripcion: form.descripcion.trim() || null,
      categoria_id: form.categoria_id,
      esquema_impuesto_id: form.esquema_impuesto_id || null,
      clave_sat: form.clave_sat.trim(),
      // La clave del artículo en SAE: vacía se manda como null (quitarla es
      // legítimo sin SAE), y el backend la normaliza a mayúsculas sin espacios.
      clave_sae: armado.claveBase,
      unidad_sat: form.unidad_sat.trim(),
      unidad_base: armado.unidadBase,
      presentaciones: armado.presentaciones,
      activo: form.activo,
      perecedero: form.perecedero,
      requiere_lote: form.requiere_lote,
      ...(altas.length ? { altas_sae: altas } : {}),
    };
    setEnviando(true);
    try {
      const prod = editingId
        ? await patch<ProductoGuardado>(`/api/v1/productos/${editingId}`, payload)
        : await post<ProductoGuardado>("/api/v1/productos", { ...payload, forzar });

      const avisos = [editingId ? "Producto actualizado" : "Producto creado"];
      const pedidas = prod.altas_sae ?? [];
      for (const a of pedidas) avisos.push(`Alta de ${a.clave} pedida (${a.empresas.join(", ")})`);
      // Una clave que se mandó a crear y no volvió como alta ya existía en las
      // empresas pedidas: el backend la dejó ligada, no la encoló.
      const ligadas = altas
        .filter((a) => !pedidas.some((x) => normalizarClave(x.clave) === a.clave))
        .map((a) => a.clave);
      if (ligadas.length) avisos.push(`${ligadas.join(", ")} ya existía en SAE: quedó ligada`);

      // «Dejar la mía y pedir cambio en SAE»: va DESPUÉS de guardar, y si
      // falla el producto ya quedó guardado — se avisa, no se deshace.
      const cambios = saeConectado
        ? cambiosSaeDesdeFilas(form.filas, form.clave_sat, esquemaCodigo, prod.id)
        : [];
      const fallidos: string[] = [];
      for (const c of cambios) {
        try {
          await apiFetch("/api/v1/productos/cambio-sae", { method: "POST", body: JSON.stringify(c) });
          avisos.push(`Cambio de ${c.clave} pedido en SAE`);
        } catch (e) {
          fallidos.push(`${c.clave}: ${e instanceof ApiError ? e.message : "no se pudo"}`);
        }
      }
      toast.success(avisos.join(" · "));
      if (fallidos.length) {
        toast.error(`El producto sí se guardó, pero no pude pedir el cambio en SAE — ${fallidos.join(" · ")}`);
      }
      setForm(null);
      setParecidos([]);
      reload();
      if (saeConectado) estadosRes.reload();
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
    } finally {
      setEnviando(false);
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
          <div>
            <span className="tabular-nums">{f.clave}</span>
            {(clavesCliente[`${f.p.id}:${f.unidad}`] ?? []).map((c) => (
              <div
                key={c.clave}
                className="text-xs text-muted"
                title={`Para ${c.clientes.join(", ")} sale como ${c.clave}: es su artículo en SAE`}
              >
                {c.clave} · {c.clientes.map(nombreCorto).join(", ")}
              </div>
            ))}
          </div>
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
    // Cómo va la clave de cada renglón-unidad en SAE (sólo el tenant dueño de
    // SAE): lo que antes había que abrir producto por producto para saber.
    ...(saeConectado
      ? [{
          header: "SAE",
          sortValue: (f: FilaUnidad) => estadoDeClave(f.clave, estados).texto,
          cell: (f: FilaUnidad) => {
            const v = estadoDeClave(f.clave, estados);
            if (v.codigo === "desconocido") {
              return <span className="text-muted">{estadosRes.loading ? "…" : "—"}</span>;
            }
            // Un producto inactivo sin clave no rompe nada: se ve, pero gris.
            const tono = v.codigo === "sin_clave" && !f.p.activo ? "muted" : v.tono;
            return (
              <span title={v.detalle ?? undefined}>
                <Badge tone={tono}>{v.texto}</Badge>
              </span>
            );
          },
        } satisfies Column<FilaUnidad>]
      : []),
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
        canWrite || canDelete ? (
          <div className="flex justify-end gap-1">
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
  ], [catName, esqName, canWrite, canDelete, openEdit, saeConectado, estados, estadosRes.loading, clavesCliente]);

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
        // Ancho: la sección de claves SAE lista candidatas con su descripción y
        // sus empresas, y en el modal angosto se partía en vertical.
        size="lg"
        // Lo que falta va a la izquierda del pie: el Modal mete `footer` en un
        // bloque alineado a la derecha.
        footerStart={faltas.length ? (
          <span className="text-sm text-danger">{faltas[0]}</span>
        ) : undefined}
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Cancelar
            </Button>
            {parecidos.length ? (
              <Button variant="secondary" onClick={() => save(true)} disabled={saving || enviando || faltas.length > 0}>
                {saving || enviando ? "Guardando…" : "Es distinto — crearlo igual"}
              </Button>
            ) : (
              <Button onClick={() => save()} disabled={saving || enviando || faltas.length > 0}>
                {saving || enviando
                  ? "Guardando…"
                  : nAltas
                    ? `${editingId ? "Guardar" : "Crear"} y pedir ${nAltas} alta${nAltas === 1 ? "" : "s"} en SAE`
                    : editingId ? "Guardar" : "Crear producto"}
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

        {/* Sin pestañas: «Así lo escriben» se fue a Vocabulario (2-oct-2026),
            que es donde se administra cómo escribe cada cliente un producto. */}
        {form && (
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* SKU — automático */}
            <div className="sm:col-span-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="SKU" hint={editingId ? undefined : "Se genera automáticamente al guardar"}>
                <Input value={editingId ? form.sku : ""} placeholder="(automático)" disabled className="max-w-[14rem]" />
              </Field>
            </div>
            {/* nombre + categoría: la categoría es obligatoria en el alta */}
            <div className="sm:col-span-2 grid grid-cols-1 gap-4 sm:grid-cols-[2fr_1fr]">
              <Field label="Nombre" required>
                <Input value={form.nombre} onChange={(e) => setForm({ ...form, nombre: e.target.value })} />
              </Field>
              <Field label="Categoría" required>
                <Select value={form.categoria_id} onChange={(e) => setForm({ ...form, categoria_id: e.target.value })}>
                  <option value="">— Elige la categoría —</option>
                  {categorias
                    .filter((c) => c.activo || c.id === form.categoria_id)
                    .map((c) => (<option key={c.id} value={c.id}>{c.nombre}</option>))}
                </Select>
              </Field>
            </div>
            <Field label="Unidad base" hint="Unidad de inventario (todo el stock se guarda aquí)">
              <Select
                value={form.unidad_base}
                onChange={(e) => setForm({
                  ...form,
                  unidad_base: e.target.value,
                  filas: cambiarUnidadBase(form.filas, e.target.value),
                })}
              >
                {(UNIDADES_BASE.includes(form.unidad_base) ? UNIDADES_BASE : [form.unidad_base, ...UNIDADES_BASE]).map((u) => (
                  <option key={u} value={u}>{u}</option>
                ))}
              </Select>
            </Field>
            <Field
              label="Esquema de impuestos"
              required={!editingId}
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
                <div>
                  <Field label="Clave SAT (producto/servicio)">
                    <SatClaveCombobox
                      value={form.clave_sat}
                      mostrarDescripcion={false}
                      onChange={(v) => setForm((f) => (f ? { ...f, clave_sat: v } : f))}
                    />
                  </Field>
                  {/* La descripción oficial, y si la clave no está en el catálogo, se dice. */}
                  <DescripcionSat clave={form.clave_sat} />
                </div>
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

            <div className="sm:col-span-2">
              <UnidadesClavesSae
                nombre={form.nombre}
                filas={form.filas}
                onChange={(filas) => setForm((f) => (f ? { ...f, filas } : f))}
                esquemaCodigo={esquemaCodigo}
                esquemaNombre={esquemaSel?.nombre ?? null}
                claveSat={form.clave_sat}
                unidadSat={form.unidad_sat}
                onUsarSatDeSae={usarSatDeSae}
                onUsarEsquemaDeSae={usarEsquemaDeSae}
                productoId={editingId}
                saeConectado={saeConectado}
                estados={saeConectado ? (estadosRes.loading && !estadosRes.data ? null : estados) : undefined}
              />
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

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, ClipboardPaste, FileText, Mail, Pencil, Plus, Printer, Sparkles, Trash2, Undo2, X } from "lucide-react";

import { KeyboardCombobox, type ComboOption } from "@/components/KeyboardCombobox";
import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { CrearProductoModal, type ProductoCreado } from "@/components/CrearProductoModal";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column, type RowAction } from "@/components/ui/DataTable";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { LoadingDots } from "@/components/ui/LoadingDots";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch, apiOpenInTab } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import {
  NUEVO_PRODUCTO, fetchFiscalPreview, lineaDesdePegado, matchPresentacion, norm,
  nuevaLinea, pegarLocalFallback, unidadBaseDesde,
  type FiscalPreview, type LineaForm,
} from "@/lib/lineas";
import type { Almacen, Cliente, LineaPegada, MatchResult, Producto, Remision, RemisionDetail, Serie, Sucursal } from "@/lib/types";

const WRITE = "remision:gestionar";

// Referencia estable para cuando `data` aún no llega: `?? []` crearía un
// arreglo nuevo en cada render, lo que invalida el memo de `filteredRows` y
// dispara un loop infinito de render en la tabla con selección (ver DataTable
// selectedRows/onSelectionChange).
const EMPTY_REMISIONES: Remision[] = [];

const ESTADO_TONE: Record<string, "default" | "success" | "warning" | "muted" | "danger" | "accent"> = {
  BORRADOR: "warning",
  // Reservada: la ampara una factura de SAE, pero la mercancía no ha salido.
  RESERVADO: "default",
  CONFIRMADA: "success",
  FACTURADA: "accent",
  CANCELADA: "danger",
};

// Tono del estado de la FACTURA vinculada (columna "Factura" de la lista).
const FACTURA_TONE: Record<string, "success" | "warning" | "danger"> = {
  BORRADOR: "warning",
  TIMBRADA: "success",
  CANCELADA: "danger",
};

// Facturable: borrador/confirmada y sin factura vigente (sin factura o con la
// última CANCELADA → refacturación).
function puedeFacturar(r: Remision): boolean {
  return (
    (r.estado === "BORRADOR" || r.estado === "CONFIRMADA") &&
    (!r.factura_id || r.factura_estado === "CANCELADA")
  );
}

export default function RemisionesPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  const { post, patch, loading: saving } = useMutation();
  const router = useRouter();

  // catálogos
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=200");
  const almacenesRes = useResource<Page<Almacen>>("/api/v1/almacenes?limit=200");
  const productosRes = useResource<Page<Producto>>("/api/v1/productos?limit=1000");
  const clientes = clientesRes.data?.items ?? [];
  const almacenes = almacenesRes.data?.items ?? [];
  const productos = productosRes.data?.items ?? [];
  const prodById = useMemo(() => Object.fromEntries(productos.map((p) => [p.id, p])), [productos]);
  const cliName = useMemo(() => Object.fromEntries(clientes.map((c) => [c.id, c.legal_name])), [clientes]);
  // Correos por cliente (array `correos` o el `email` legado), unidos por coma.
  const cliEmail = useMemo(
    () => Object.fromEntries(clientes.map((c) => {
      const dom = (c.domicilio_fiscal ?? {}) as Record<string, unknown>;
      const arr = Array.isArray(dom.correos)
        ? (dom.correos as string[])
        : (dom.email ? [String(dom.email)] : []);
      return [c.id, arr.join(", ")];
    })),
    [clientes],
  );

  // ── filtros de lista (server-side: el backend filtra sobre TODO el
  // historial, no solo la página cargada — decisión 2026-07-29 #6) ──
  const [fDesde, setFDesde] = useState("");
  const [fHasta, setFHasta] = useState("");
  const [fCliente, setFCliente] = useState("");
  const listPath = useMemo(() => {
    const p = new URLSearchParams({ limit: "200" });
    if (fDesde) p.set("fecha_desde", fDesde);
    if (fHasta) p.set("fecha_hasta", fHasta);
    if (fCliente) p.set("cliente_id", fCliente);
    return `/api/v1/remisiones?${p.toString()}`;
  }, [fDesde, fHasta, fCliente]);

  // lista
  const { data, loading, error, reload } = useResource<Page<Remision>>(listPath);
  const rows = data?.items ?? EMPTY_REMISIONES;
  const filteredRows = rows;

  // ── selección de filas (acciones en lote) ──
  const [selected, setSelected] = useState<Remision[]>([]);
  const [selectionResetKey, setSelectionResetKey] = useState(0);
  const clearSelection = () => { setSelected([]); setSelectionResetKey((k) => k + 1); };

  // modo crear/editar. `editId` = remisión (BORRADOR) que se está editando; null = alta nueva.
  const [mode, setMode] = useState<"list" | "create">("list");
  const [editId, setEditId] = useState<string | null>(null);
  const [editEstado, setEditEstado] = useState<string | null>(null);   // estado de la remisión editada
  const [editFolio, setEditFolio] = useState<string | null>(null);     // folio real de la remisión editada
  const [clienteId, setClienteId] = useState("");
  const [sucursalId, setSucursalId] = useState("");
  const [almacenId, setAlmacenId] = useState("");
  const [fecha, setFecha] = useState("");
  const [serieOverride, setSerieOverride] = useState("");
  const [notas, setNotas] = useState("");
  const [facturaSae, setFacturaSae] = useState("");
  const [suPedido, setSuPedido] = useState("");
  const [lineas, setLineas] = useState<LineaForm[]>([nuevaLinea()]);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  // paso del flujo por teclado: cuál caja se auto-abre/enfoca
  const [step, setStep] = useState<"cliente" | "sucursal" | "almacen" | "serie" | "lineas" | null>(null);

  // series de remisión (para override) + preview de la serie que aplicaría
  const seriesRemRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=REMISION&activa=true&limit=200");
  const seriesRem = seriesRemRes.data?.items ?? [];
  const [serieResuelta, setSerieResuelta] = useState<Serie | null>(null);
  useEffect(() => {
    // En edición el folio ya está fijo: no se resuelve serie (se muestra el real).
    if (mode !== "create" || editId || !clienteId) { setSerieResuelta(null); return; }
    let active = true; // descarta la respuesta si los parámetros cambian antes de llegar
    const p = new URLSearchParams({ tipo_documento: "REMISION", cliente_id: clienteId });
    if (sucursalId) p.set("sucursal_id", sucursalId);
    if (serieOverride) p.set("serie_id", serieOverride);
    apiFetch<Serie | null>(`/api/v1/series/resolver?${p.toString()}`)
      .then((s) => { if (active) setSerieResuelta(s); })
      .catch(() => { if (active) setSerieResuelta(null); });
    return () => { active = false; };
  }, [mode, editId, clienteId, sucursalId, serieOverride]);

  const folioPreview = serieResuelta ? `${serieResuelta.codigo}${serieResuelta.folio_actual + 1}` : "—";

  // Totales del alta calculados por el SERVIDOR (regla "el backend calcula
  // todo"): debounce por tecleo + secuencia contra respuestas fuera de orden.
  const [fiscalPreview, setFiscalPreview] = useState<FiscalPreview | null>(null);
  const fiscalSeq = useRef(0);
  useEffect(() => {
    const seq = ++fiscalSeq.current;
    const t = setTimeout(() => {
      fetchFiscalPreview(lineas)
        .then((p) => { if (seq === fiscalSeq.current) setFiscalPreview(p); })
        .catch(() => { if (seq === fiscalSeq.current) setFiscalPreview(null); });
    }, 300);
    return () => clearTimeout(t);
  }, [lineas]);

  const subtotalPreview = fiscalPreview?.subtotal ?? lineas.reduce((s, l) => s + (l.importe || 0), 0);
  const iepsPreview = fiscalPreview?.ieps ?? 0;
  const ivaPreview = fiscalPreview?.iva ?? 0;
  const totalPreview = fiscalPreview?.total ?? subtotalPreview;

  // opciones para los comboboxes
  const clienteOpts: ComboOption[] = useMemo(() => clientes.map((c) => ({ value: c.id, label: c.legal_name })), [clientes]);
  const sucursalOpts: ComboOption[] = useMemo(() => sucursales.map((s) => ({ value: s.id, label: s.nombre })), [sucursales]);
  const almacenOpts: ComboOption[] = useMemo(() => almacenes.map((a) => ({ value: a.id, label: a.nombre })), [almacenes]);
  const serieOpts: ComboOption[] = useMemo(
    () => [
      { value: "", label: `Automática${serieResuelta ? ` · ${serieResuelta.codigo}` : ""}` },
      ...seriesRem.map((s) => ({ value: s.id, label: `${s.codigo}${s.nombre ? ` · ${s.nombre}` : ""}` })),
    ],
    [seriesRem, serieResuelta],
  );

  const today = () => new Date().toISOString().slice(0, 10);

  // Avanza al siguiente campo saltando los que tienen 0 o 1 opción (auto-selección).
  function resolveFrom(target: "sucursal" | "almacen" | "serie" | "lineas", sucs = sucursales, alms = almacenes) {
    if (target === "sucursal") {
      if (sucs.length === 0) { setSucursalId(""); return resolveFrom("almacen", sucs, alms); }
      if (sucs.length === 1) { setSucursalId(sucs[0].id); return resolveFrom("almacen", sucs, alms); }
      return setStep("sucursal");
    }
    if (target === "almacen") {
      if (alms.length === 0) { setAlmacenId(""); return resolveFrom("lineas", sucs, alms); }
      if (alms.length === 1) { setAlmacenId(alms[0].id); return resolveFrom("lineas", sucs, alms); }
      return setStep("almacen");
    }
    // La serie se resuelve sola (automática o la del cliente) → NO detiene el flujo.
    // Sigue editable manualmente; el Enter pasa directo a las líneas.
    if (!fecha) setFecha(today());
    setStep("lineas");
    setLineFocus({ key: lineas[0]?.key, field: "cantidad" });   // enfoca la primera línea (Cantidad)
  }

  // Secuencia de la carga de sucursales: si el usuario cambia de cliente antes
  // de que llegue la respuesta, solo la petición más reciente aplica su resultado.
  const selectClienteSeq = useRef(0);

  async function selectCliente(v: string) {
    const seq = ++selectClienteSeq.current;
    setClienteId(v);
    setStep(null);
    const cli = clientes.find((c) => c.id === v);
    setSerieOverride(cli?.serie_remision_id ?? "");   // la serie del cliente se aplica sola
    let sucs: Sucursal[] = [];
    try {
      sucs = (await apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${v}&limit=200`)).items;
    } catch {
      sucs = [];
    }
    if (seq !== selectClienteSeq.current) return;    // ya se eligió otro cliente
    setSucursales(sucs);
    resolveFrom("sucursal", sucs);
  }

  // Limpia el formulario dejándolo como recién abierto (sin salir de la pantalla).
  // Lo usa "Borrar" y también openCreate al entrar.
  function resetForm() {
    setClienteId(""); setSucursalId(""); setAlmacenId(""); setSerieOverride("");
    setSucursales([]); setNotas(""); setFacturaSae(""); setSuPedido(""); setLineas([nuevaLinea()]);
    setFecha(today());            // fecha de hoy por defecto (el flujo la salta)
    setStep("cliente");           // arranca con el cliente abierto
  }

  function openCreate() {
    setEditId(null);
    setEditEstado(null);
    setEditFolio(null);
    resetForm();
    setMode("create");
  }

  // Abre esta misma pantalla para EDITAR una remisión en BORRADOR: carga sus
  // datos y líneas. Solo BORRADOR es editable (el backend lo exige).
  async function openEdit(r: Remision) {
    if (r.estado !== "BORRADOR" && r.estado !== "RESERVADO" && r.estado !== "CONFIRMADA") {
      toast.error("Solo se puede editar una remisión en borrador o confirmada");
      return;
    }
    if (r.factura_id && r.factura_estado !== "CANCELADA") {
      toast.error("La remisión está ligada a una factura; cancélala o descártala antes de editar");
      return;
    }
    try {
      const det = await apiFetch<RemisionDetail>(`/api/v1/remisiones/${r.id}`);
      setClienteId(det.cliente_facturacion_id);
      setSucursalId(det.sucursal_id ?? "");
      setAlmacenId(det.almacen_id ?? "");
      setSerieOverride("");                       // el folio ya está fijo; no se re-serie al editar
      setFecha(det.fecha_remision ?? today());
      setNotas(det.notas ?? "");
      setFacturaSae(det.factura_sae ?? "");
      setSuPedido(det.su_pedido ?? "");
      setLineas((det.lineas ?? []).map((ln) => {
        const presKeys = Object.keys(prodById[ln.producto_id]?.presentaciones ?? {});
        return nuevaLinea({
          texto: ln.producto_nombre ?? "",
          producto_id: ln.producto_id,
          label: ln.producto_nombre ?? "",
          presentaciones: presKeys.length ? presKeys : [ln.presentacion],
          presentacion: ln.presentacion,
          cantidad: String(ln.cantidad_solicitada),
          precio: String(ln.precio_unitario),
          precioManual: true,                     // conserva el precio guardado
          importe: Number(ln.importe),
        });
      }));
      setStep(null);                              // no auto-abre el cliente al editar
      setEditId(r.id);
      setEditEstado(r.estado);
      setEditFolio(det.folio_interno);
      setMode("create");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cargar la remisión");
    }
  }

  // Crea (POST) o actualiza (PATCH) según si estamos editando.
  function persistirRemision(payload: unknown) {
    return editId
      ? patch<RemisionDetail>(`/api/v1/remisiones/${editId}`, payload)
      : post<RemisionDetail>("/api/v1/remisiones", payload);
  }

  function setLinea(key: string, patch: Partial<LineaForm>) {
    setLineas((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }

  // ── foco encadenado dentro de las líneas (producto → presentación → cantidad → precio → siguiente) ──
  type LineField = "producto" | "presentacion" | "cantidad" | "precio";
  const [lineFocus, setLineFocus] = useState<{ key: string; field: LineField } | null>(null);
  const cellRefs = useRef<Record<string, HTMLInputElement | null>>({});
  useEffect(() => {
    if (!lineFocus) return;
    if (lineFocus.field === "cantidad" || lineFocus.field === "precio") {
      const el = cellRefs.current[`${lineFocus.key}:${lineFocus.field}`];
      el?.focus();
      el?.select();
    }
  }, [lineFocus]);

  function advanceLine(key: string, from: LineField) {
    // Secuencia: Cantidad → Producto → Presentación → Precio → (siguiente línea).
    if (from === "cantidad") return setLineFocus({ key, field: "producto" });
    if (from === "producto") return setLineFocus({ key, field: "presentacion" });
    if (from === "presentacion") return setLineFocus({ key, field: "precio" });
    // precio → siguiente línea (o crea una nueva si es la última), enfocando Cantidad
    const idx = lineas.findIndex((l) => l.key === key);
    if (idx === lineas.length - 1) {
      const nl = nuevaLinea();
      setLineas((ls) => [...ls, nl]);
      return setLineFocus({ key: nl.key, field: "cantidad" });
    }
    return setLineFocus({ key: lineas[idx + 1].key, field: "cantidad" });
  }

  // Secuencia por línea: cada llamada a cotizar (aunque no llegue a pedir precio)
  // invalida las anteriores en vuelo, para que una respuesta vieja no pise el
  // precio/importe calculados con la cantidad más reciente.
  const cotizaSeq = useRef<Record<string, number>>({});

  async function cotizar(key: string, producto_id: string, presentacion: string, cantidad: string) {
    const seq = (cotizaSeq.current[key] ?? 0) + 1;
    cotizaSeq.current[key] = seq;
    if (!producto_id || !Number(cantidad)) return;
    try {
      const p = new URLSearchParams({ producto_id, presentacion, cantidad });
      if (clienteId) p.set("cliente_id", clienteId);
      if (sucursalId) p.set("sucursal_id", sucursalId);
      const r = await apiFetch<{ precio: string | null }>(`/api/v1/precios/cotizar?${p.toString()}`);
      if (cotizaSeq.current[key] !== seq) return;   // hay una cotización más nueva en curso
      setLineas((ls) =>
        ls.map((l) => {
          if (l.key !== key) return l;
          if (l.precioManual) return { ...l, importe: Number(l.precio || 0) * Number(cantidad) };
          const precio = r.precio ?? "";
          return { ...l, precio: precio === "" ? "" : String(precio), importe: Number(precio || 0) * Number(cantidad) };
        }),
      );
    } catch {
      /* sin precio: se resolverá al guardar */
    }
  }

  function onPickProducto(key: string, pick: ProductoPick | null, texto: string) {
    if (!pick) {
      // Sin producto no hay precio: se limpian también precio/importe para que
      // la línea no siga sumando en los totales.
      setLinea(key, { producto_id: "", label: "", texto, precio: "", precioManual: false, importe: 0 });
      return;
    }
    // La presentación viene del propio producto (no de un fetch separado), así el
    // default real (p. ej. PIEZA) se respeta y el precio se cotiza con la presentación correcta.
    const pres = Object.keys(pick.presentaciones ?? {});
    const def = pick.presentacion_default ?? pick.unidad_base ?? pres[0] ?? "PIEZA";
    const presentacion = pres.includes(def) ? def : pres[0] ?? def;
    setLinea(key, {
      producto_id: pick.producto_id,
      label: pick.nombre,        // sin SKU
      texto,
      presentaciones: pres,
      presentacion,
    });
    const ln = lineas.find((l) => l.key === key);
    cotizar(key, pick.producto_id, presentacion, ln?.cantidad ?? "1");
    setLineFocus({ key, field: "presentacion" });   // Enter en producto → presentación
  }

  // ── pegar como Excel ──
  const [pasteText, setPasteText] = useState("");
  const [procesando, setProcesando] = useState(false);
  // Línea (key) que pidió "Crear producto nuevo" desde la columna Match IA.
  const [crearParaKey, setCrearParaKey] = useState<string | null>(null);

  function cerrarPaste() {
    setPasteOpen(false);
    setPasteText("");
  }

  // "Procesar": parsea + cruza e inserta las líneas DIRECTO en la tabla; la
  // columna Match IA aparece para revisarlas ahí mismo (sin popup).
  async function procesarPaste() {
    const raw = pasteText;
    if (!raw.trim()) return;
    setProcesando(true);
    try {
      let filas: LineaPegada[] = [];
      try {
        filas = await apiFetch("/api/v1/productos/parse-pegado", {
          method: "POST",
          body: JSON.stringify({ texto: raw, usar_ia: true }),
        });
      } catch {
        filas = await pegarLocalFallback(raw); // backend sin el endpoint nuevo
      }
      if (filas.length === 0) { toast.error("No se detectaron líneas en el texto pegado"); return; }
      const nuevas = filas.map(lineaDesdePegado);
      setLineas((ls) => {
        const base = ls.filter((l) => l.producto_id || l.texto);
        return [...base, ...nuevas];
      });
      nuevas.forEach((l) => l.producto_id && cotizar(l.key, l.producto_id, l.presentacion, l.cantidad));
      const porRevisar = nuevas.filter((l) => !l.producto_id).length;
      cerrarPaste();
      toast.success(`${nuevas.length} líneas agregadas` + (porRevisar ? ` · ${porRevisar} por revisar en Match IA` : ""));
    } finally {
      setProcesando(false);
    }
  }

  // Cambia el producto de una línea desde el selector Match IA (columna nueva).
  // "Crear nuevo" abre el popup de alta de producto precargado con el texto pegado.
  function resolverMatchIA(key: string, sel: string) {
    const l = lineas.find((x) => x.key === key);
    if (!l) return;
    if (sel === NUEVO_PRODUCTO) {
      setLinea(key, { producto_id: "", label: "", presentaciones: [], presentacion: l.presPegada || l.presentacion, importe: 0 });
      setCrearParaKey(key);   // abre el popup para crear el producto en el catálogo
      return;
    }
    const cand = (l.candidatos ?? []).find((c) => c.producto_id === sel);
    if (!cand) return;
    // Aprende el alias cuando el usuario confirma un cruce no exacto.
    if (cand.origen !== "exacto" && norm(l.texto) !== norm(cand.nombre)) {
      apiFetch("/api/v1/productos/alias", {
        method: "POST",
        body: JSON.stringify({ texto: l.texto, producto_id: cand.producto_id }),
      }).catch(() => {});
    }
    const presKeys = Object.keys(cand.presentaciones ?? {});
    const presentacion = matchPresentacion(l.presPegada ?? l.presentacion, cand);
    setLinea(key, { producto_id: cand.producto_id, label: cand.nombre, presentaciones: presKeys, presentacion });
    cotizar(key, cand.producto_id, presentacion, l.cantidad);
  }

  // Producto recién creado desde el popup → resuelve la línea que lo pidió.
  function aplicarProductoCreado(prod: ProductoCreado) {
    const key = crearParaKey;
    if (!key) return;
    const l = lineas.find((x) => x.key === key);
    const presKeys = Object.keys(prod.presentaciones ?? {});
    const presentacion = prod.presentacion_default ?? prod.unidad_base ?? presKeys[0] ?? (l?.presPegada || "PIEZA");
    setLinea(key, { producto_id: prod.id, label: prod.nombre, presentaciones: presKeys, presentacion });
    if (l) cotizar(key, prod.id, presentacion, l.cantidad);
    setCrearParaKey(null);
  }

  // La columna Match IA solo existe mientras haya líneas venidas de "Pegar de Excel".
  const showMatchIA = lineas.some((l) => l.fromPaste);
  const lineaCrear = crearParaKey ? lineas.find((l) => l.key === crearParaKey) : undefined;

  // ── pegar una columna directo en la celda (como Excel): pega varias líneas
  // sobre Cantidad o Producto y se reparten hacia abajo desde la fila actual,
  // creando filas nuevas si el pegado trae más líneas de las que ya existen.
  function pegarColumnaCantidad(key: string, text: string) {
    const valores = text.split("\n").map((v) => v.split("\t")[0].trim()).filter(Boolean);
    if (valores.length === 0) return;
    const idx = lineas.findIndex((l) => l.key === key);
    if (idx < 0) return;
    setLineas((ls) => {
      const next = [...ls];
      valores.forEach((v, i) => {
        const cantidad = v.replace(",", ".");
        const pos = idx + i;
        if (pos < next.length) {
          const l = next[pos];
          next[pos] = { ...l, cantidad, importe: Number(l.precio || 0) * Number(cantidad || 0) };
        } else {
          next.push(nuevaLinea({ cantidad }));
        }
      });
      return next;
    });
    valores.forEach((v, i) => {
      const l = lineas[idx + i];
      if (l?.producto_id) cotizar(l.key, l.producto_id, l.presentacion, v.replace(",", "."));
    });
    toast.success(`${valores.length} cantidades pegadas`);
  }

  async function pegarColumnaProducto(key: string, text: string) {
    const valores = text.split("\n").map((v) => v.split("\t")[0].trim()).filter(Boolean);
    if (valores.length === 0) return;
    const idx = lineas.findIndex((l) => l.key === key);
    if (idx < 0) return;
    let matches: MatchResult[] = [];
    try {
      matches = await apiFetch("/api/v1/productos/match", {
        method: "POST",
        body: JSON.stringify({ textos: valores, usar_ia: false, limit: 1 }),
      });
    } catch {
      matches = [];
    }
    // Todo se calcula FUERA del updater: React puede invocarlo dos veces
    // (StrictMode) y duplicaría nuevaLinea() y la lista a cotizar.
    const paraCotizar: LineaForm[] = [];
    const next = [...lineas];
    valores.forEach((texto, i) => {
      const pos = idx + i;
      const base = pos < next.length ? next[pos] : nuevaLinea();
      const top = matches[i]?.candidatos?.[0];
      const auto = top && (top.origen === "exacto" || top.origen === "alias" || top.score >= 85) ? top : null;
      let linea: LineaForm;
      if (auto) {
        const presKeys = Object.keys(auto.presentaciones ?? {});
        const def = auto.presentacion_default ?? auto.unidad_base ?? presKeys[0] ?? "PIEZA";
        const presentacion = presKeys.includes(def) ? def : presKeys[0] ?? def;
        linea = { ...base, texto, producto_id: auto.producto_id, label: auto.nombre, presentaciones: presKeys, presentacion };
        paraCotizar.push(linea);
      } else {
        linea = { ...base, texto, producto_id: "", label: "" };
      }
      if (pos < next.length) next[pos] = linea;
      else next.push(linea);
    });
    setLineas(next);
    paraCotizar.forEach((l) => cotizar(l.key, l.producto_id, l.presentacion, l.cantidad));
    toast.success(`${valores.length} productos pegados`);
  }

  // Construye el payload de creación desde el formulario. Devuelve null (con
  // toast) si falta el cliente o líneas válidas.
  function construirPayload() {
    if (!clienteId) { toast.error("Elige un cliente"); return null; }
    const lns = lineas.filter((l) => l.producto_id && Number(l.cantidad) > 0);
    if (lns.length === 0) { toast.error("Agrega al menos una línea con producto y cantidad"); return null; }
    return {
      cliente_facturacion_id: clienteId,
      sucursal_id: sucursalId || null,
      almacen_id: almacenId || null,
      serie_id: serieOverride || null,
      // La columna es NOT NULL: con fecha vacía se omite el campo (mandar null
      // en el PATCH provoca un 500); el backend conserva/asigna la suya.
      ...(fecha ? { fecha_remision: fecha } : {}),
      notas: notas || null,
      // Vacío se manda como cadena vacía (no null): así el backend distingue
      // "quítalo" —y regresa la remisión a BORRADOR— de "no lo toques".
      factura_sae: facturaSae.trim(),
      su_pedido: suPedido.trim(),
      lineas: lns.map((l) => ({
        producto_id: l.producto_id,
        presentacion: l.presentacion,
        cantidad_solicitada: l.cantidad,
        precio_unitario: l.precioManual && l.precio ? l.precio : undefined,
      })),
    };
  }

  // "Guardar": valida. Editando una CONFIRMADA guarda directo (PATCH re-reserva
  // inventario y sigue confirmada); en alta/borrador abre el diálogo de elección.
  function abrirGuardar() {
    if (saving) return;
    if (!construirPayload()) return; // valida (togglea el toast si falta algo)
    if (editId && editEstado === "CONFIRMADA") { void guardarEdicionConfirmada(); return; }
    setGuardarChoiceOpen(true);
  }

  // Guard síncrono contra doble clic en Guardar: el estado `saving` llega tarde
  // si el segundo clic entra antes del re-render (serían dos POST y dos folios).
  const inFlight = useRef(false);

  // Sobregiro al EDITAR una confirmada (misma política que confirmar/facturar):
  // si el re-descuento no alcanza, se ofrece guardar dejando negativo.
  const [editSobregiro, setEditSobregiro] = useState<Record<string, unknown> | null>(null);

  // Guarda la edición de una remisión CONFIRMADA: el backend libera la reserva
  // previa y re-reserva con las líneas nuevas (sigue CONFIRMADA, mismo folio).
  async function guardarEdicionConfirmada(permitirNegativos = false) {
    if (inFlight.current) return;
    const payload = construirPayload();
    if (!payload) return;
    inFlight.current = true;
    try {
      const rem = await persistirRemision(
        permitirNegativos ? { ...payload, permitir_negativos: true } : payload,
      );
      invalidarDetalles([rem.id]);
      toast.success(`Remisión ${rem.folio_interno} actualizada`);
      setEditSobregiro(null);
      setEditId(null); setEditEstado(null);
      resetForm();
      setMode("list");
      reload();
    } catch (e) {
      if (e instanceof ApiError && /existencia insuficiente/i.test(e.message) && !permitirNegativos) {
        setEditSobregiro(payload as Record<string, unknown>);
      } else {
        toast.error(e instanceof ApiError ? e.message : "No se pudo guardar la remisión");
      }
    } finally {
      inFlight.current = false;
    }
  }

  // Opción "Borrador": crea la remisión en BORRADOR, sin afectar inventario.
  async function guardarBorrador() {
    if (inFlight.current) return; // evita doble envío
    const payload = construirPayload();
    if (!payload) return;
    inFlight.current = true;
    setGuardarChoiceOpen(false);
    try {
      const rem = await persistirRemision(payload);
      invalidarDetalles([rem.id]);
      toast.success(`Remisión ${rem.folio_interno} ${editId ? "actualizada" : "guardada (borrador)"}`);
      setEditId(null);
      resetForm();
      setMode("list");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar la remisión");
    } finally {
      inFlight.current = false;
    }
  }

  // Opción "Confirmar salida (Inventario)": crea la remisión y la confirma
  // (reserva/descuenta inventario). Sin existencia → confirmarRemision abre el
  // popup de sobregiro. Pase lo que pase salimos al listado: el borrador ya
  // existe, así un reintento no duplica (el popup flota sobre la lista).
  async function guardarYConfirmar() {
    if (inFlight.current) return;
    const payload = construirPayload();
    if (!payload) return;
    inFlight.current = true;
    setGuardarChoiceOpen(false);
    try {
      const rem = await persistirRemision(payload);
      invalidarDetalles([rem.id]);
      await confirmarRemision(rem.id, rem.folio_interno);
      setEditId(null);
      resetForm();
      setMode("list");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar la remisión");
    } finally {
      inFlight.current = false;
    }
  }

  // Opción "Timbrar": crea la remisión y abre el popup de facturar apuntando a
  // ESA remisión (facturar auto-confirma y timbra de inmediato ante el PAC).
  async function guardarYTimbrar() {
    if (inFlight.current) return;
    const payload = construirPayload();
    if (!payload) return;
    inFlight.current = true;
    setGuardarChoiceOpen(false);
    try {
      const rem = await persistirRemision(payload);
      invalidarDetalles([rem.id]);
      setEditId(null);
      resetForm();
      setMode("list");
      reload();
      // 1 remisión sin productos repetidos → no hace falta elegir modo: timbra
      // directo. Si hay repetidos, abre el popup para elegir sumatoria o no.
      if (hayProductosRepetidos(rem.lineas)) {
        setFacturarSolo(rem);
        setFacturarDups(true);
        setFacturarOpen(true);
      } else {
        await facturarRems([rem], "sin_sumar");
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la remisión");
    } finally {
      inFlight.current = false;
    }
  }

  // ── acciones de lista ──
  // Detalle por fila: se carga bajo demanda al expandir la fila (slide-down).
  const [detalles, setDetalles] = useState<Record<string, RemisionDetail>>({});
  const [detalleLoading, setDetalleLoading] = useState<Set<string>>(new Set());
  const [toConfirm, setToConfirm] = useState<Remision | null>(null);
  const [toCancel, setToCancel] = useState<Remision | null>(null);
  // Diálogo de elección al guardar el alta: Borrador vs Confirmar salida.
  const [guardarChoiceOpen, setGuardarChoiceOpen] = useState(false);
  // Sobregiro: cuando confirmar falla por existencia insuficiente, ofrecemos
  // confirmar de todas formas dejando el inventario en negativo.
  const [negStock, setNegStock] = useState<{ remId: string; folio: string } | null>(null);

  // Saca del caché el detalle de las remisiones que acaban de mutar (editar,
  // confirmar, cancelar, facturar): el slide-down y getDetalle vuelven a pedir
  // datos frescos en el siguiente uso.
  function invalidarDetalles(ids: string[]) {
    setDetalles((m) => {
      const n = { ...m };
      for (const id of ids) delete n[id];
      return n;
    });
  }

  async function verDetalle(r: Remision) {
    if (detalles[r.id] || detalleLoading.has(r.id)) return; // ya cargado / en curso
    setDetalleLoading((s) => new Set(s).add(r.id));
    try {
      const d = await apiFetch<RemisionDetail>(`/api/v1/remisiones/${r.id}`);
      setDetalles((m) => ({ ...m, [r.id]: d }));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cargar");
    } finally {
      setDetalleLoading((s) => { const n = new Set(s); n.delete(r.id); return n; });
    }
  }

  // Contenido del panel que se despliega bajo la fila al hacer clic.
  function renderDetalle(r: Remision) {
    const d = detalles[r.id];
    if (!d) {
      return <div className="flex justify-center py-6"><Spinner /></div>;
    }
    // Totales OFICIALES guardados por el backend (mismo cerebro fiscal que la
    // factura) — el frontend solo los muestra.
    const subtotal = Number(d.subtotal);
    const ieps = Number(d.ieps);
    const iva = Number(d.iva);
    const total = Number(d.total);
    return (
      <div className="rounded-xl border border-border bg-background p-4">
        <div className="mb-3 flex flex-wrap gap-4 text-sm">
          <div><span className="text-muted">Cliente:</span> {cliName[d.cliente_facturacion_id] ?? "—"}</div>
          <div><span className="text-muted">Fecha:</span> {fmtDate(d.fecha_remision)}</div>
          <div><span className="text-muted">Estado:</span> <Badge tone={ESTADO_TONE[d.estado] ?? "muted"}>{d.estado}</Badge></div>
        </div>
        <DataTable
          rows={d.lineas}
          rowKey={(l) => l.id}
          empty="Sin líneas"
          columns={[
            { header: "Cant.", className: "text-right tabular-nums", cell: (l) => fmtNumber(l.cantidad_solicitada) },
            { header: "Pres.", cell: (l) => l.presentacion },
            { header: "Descr.", cell: (l) => l.producto_nombre ?? prodById[l.producto_id]?.nombre ?? l.producto_id },
            { header: "P/U", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.precio_unitario) },
            { header: "IEPS", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.ieps_importe ?? 0) },
            { header: "IVA", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.iva_importe ?? 0) },
            { header: "Importe", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.importe) },
          ]}
        />
        <div className="mt-3 flex flex-col items-end gap-1 text-sm">
          <div className="flex gap-4"><span className="text-muted">Subtotal</span><span className="tabular-nums">{fmtMoney(subtotal)}</span></div>
          <div className="flex gap-4"><span className="text-muted">IEPS</span><span className="tabular-nums">{fmtMoney(ieps)}</span></div>
          <div className="flex gap-4"><span className="text-muted">IVA</span><span className="tabular-nums">{fmtMoney(iva)}</span></div>
          <div className="flex gap-4 text-base font-semibold"><span className="text-muted">Total</span><span className="tabular-nums">{fmtMoney(total)}</span></div>
        </div>
        {(d.devoluciones?.length ?? 0) > 0 && (
          <div className="mt-3 rounded-lg border border-border bg-surface-2 p-3 text-sm">
            <div className="mb-1 font-medium">Devoluciones registradas</div>
            {d.devoluciones!.map((dev) => (
              <div key={dev.id} className="text-muted">
                {fmtDate(dev.created_at)} — {dev.lineas.map((l) =>
                  `${fmtNumber(l.cantidad)} ${l.presentacion || ""} ${prodById[l.producto_id]?.nombre ?? ""}`.trim()
                ).join(", ")}
                {dev.motivo ? ` · ${dev.motivo}` : ""}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }
  // Confirma una remisión existente (reserva inventario). Si falta existencia,
  // abre el popup de sobregiro (inventario en negativo). Lógica ÚNICA que
  // comparten el ícono Confirmar de la tabla y el botón Confirmar del alta.
  // La navegación al listado la maneja quien llama (el alta hace setMode).
  async function confirmarRemision(remId: string, folio: string): Promise<boolean> {
    try {
      await post(`/api/v1/remisiones/${remId}/confirmar`, {});
      invalidarDetalles([remId]);
      toast.success(`Remisión ${folio} confirmada (inventario reservado)`);
      reload();
      return true;
    } catch (e) {
      // Sin existencia: la remisión queda en BORRADOR; ofrecemos sobregiro.
      if (e instanceof ApiError && /existencia insuficiente/i.test(e.message)) {
        setNegStock({ remId, folio });
        return false;
      }
      toast.error(e instanceof ApiError ? e.message : "No se pudo confirmar");
      return false;
    }
  }
  async function confirmar() {
    if (!toConfirm) return;
    const r = toConfirm;
    setToConfirm(null);
    await confirmarRemision(r.id, r.folio_interno);
  }
  // Reintenta la confirmación autorizando el sobregiro (inventario negativo).
  async function confirmarNegativo() {
    if (!negStock) return;
    try {
      await post(`/api/v1/remisiones/${negStock.remId}/confirmar`, { permitir_negativos: true });
      invalidarDetalles([negStock.remId]);
      toast.success(`Remisión ${negStock.folio} confirmada (inventario en negativo)`);
      setNegStock(null);
      setMode("list");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo confirmar");
    }
  }
  async function cancelar() {
    if (!toCancel) return;
    try {
      await post(`/api/v1/remisiones/${toCancel.id}/cancelar`, {});
      invalidarDetalles([toCancel.id]);
      toast.success("Remisión cancelada");
      setToCancel(null); reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cancelar");
    }
  }

  // Carga el detalle de una remisión (usa caché `detalles` si está disponible).
  // ── Importación masiva estilo SAE (Excel → varias remisiones) ──
  type ImportLinea = {
    clave: string; cruce: "sku" | "cliente" | "descripcion" | null;
    descripcion: string | null; unidad: string | null;
    cantidad: string; precio: string | null;
    producto_id: string | null; producto_nombre: string | null; presentacion: string | null;
    candidatos: { producto_id: string; sku: string; nombre: string; score: number; origen: string; parecido: number }[];
    omitir?: boolean;
  };
  type ImportGrupo = {
    folio_ref: string; fecha: string | null; su_pedido: string | null; observaciones: string | null;
    cliente_codigo: string; cliente_rfc: string | null;
    requisicion: string | null; entregar_bodega: string | null;
    cliente_id: string | null; cliente_nombre: string | null;
    lineas: ImportLinea[];
  };
  const importInputRef = useRef<HTMLInputElement>(null);
  const [importGrupos, setImportGrupos] = useState<ImportGrupo[] | null>(null);
  const [importAlmacen, setImportAlmacen] = useState("");
  const [importBusy, setImportBusy] = useState(false);

  async function importarArchivo(file: File) {
    const fd = new FormData();
    fd.append("archivo", file);
    setImportBusy(true);
    try {
      const p = await apiFetch<{ grupos: ImportGrupo[] }>("/api/v1/remisiones/importar-preview", {
        method: "POST", body: fd,
      });
      setImportAlmacen(almacenes[0]?.id ?? "");
      setImportGrupos(p.grupos);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo leer el archivo");
    } finally {
      setImportBusy(false);
      if (importInputRef.current) importInputRef.current.value = "";
    }
  }

  const importListo = (importGrupos ?? []).every(
    (g) => g.cliente_id && g.lineas.every((l) => l.omitir || l.producto_id),
  );

  async function crearImportadas() {
    if (!importGrupos || importBusy) return;
    setImportBusy(true);
    try {
      let ok = 0, fail = 0;
      for (const g of importGrupos) {
        const lineas = g.lineas.filter((l) => !l.omitir && l.producto_id).map((l) => ({
          producto_id: l.producto_id,
          cantidad_solicitada: l.cantidad,
          ...(l.precio != null && l.precio !== "" ? { precio_unitario: l.precio } : {}),
          ...(l.presentacion ? { presentacion: l.presentacion } : {}),
        }));
        if (!g.cliente_id || lineas.length === 0) { fail += 1; continue; }
        const notas = [
          g.requisicion ? `Req ${g.requisicion}` : null,
          g.su_pedido ? `Su pedido: ${g.su_pedido}` : null,
          g.entregar_bodega ? `Entregar en bodega: ${g.entregar_bodega}` : null,
          g.observaciones,
        ].filter(Boolean).join(" · ");
        try {
          await post("/api/v1/remisiones", {
            cliente_facturacion_id: g.cliente_id,
            su_pedido: g.folio_ref,
            ...(importAlmacen ? { almacen_id: importAlmacen } : {}),
            ...(g.fecha ? { fecha_remision: g.fecha } : {}),
            notas,
            lineas,
          });
          ok += 1;
        } catch { fail += 1; }
      }
      const partes = [`Remisiones creadas: ${ok}`];
      if (fail) partes.push(`${fail} con error`);
      toast[fail === 0 ? "success" : "error"](partes.join(" · "));
      setImportGrupos(null);
      reload();
    } finally {
      setImportBusy(false);
    }
  }

  // ── Devolución de una CONFIRMADA (ajusta la remisión a lo neto entregado) ──
  const [devolucionDe, setDevolucionDe] = useState<RemisionDetail | null>(null);
  const [devolCant, setDevolCant] = useState<Record<string, string>>({});
  const [devolMotivo, setDevolMotivo] = useState("");
  const [devolBusy, setDevolBusy] = useState(false);

  async function abrirDevolucion(r: Remision) {
    const d = await getDetalle(r);
    if (!d) { toast.error("No se pudo cargar la remisión"); return; }
    setDevolCant({});
    setDevolMotivo("");
    setDevolucionDe(d);
  }

  async function confirmarDevolucion() {
    if (!devolucionDe || devolBusy) return;
    const lineas = Object.entries(devolCant)
      .map(([linea_id, v]) => ({ linea_id, cantidad: v.trim() }))
      .filter((l) => Number(l.cantidad) > 0);
    if (lineas.length === 0) { toast.error("Indica al menos una cantidad a devolver"); return; }
    setDevolBusy(true);
    try {
      await post(`/api/v1/remisiones/${devolucionDe.id}/devolucion`, {
        lineas, motivo: devolMotivo.trim() || undefined,
      });
      toast.success(`Devolución registrada — ${devolucionDe.folio_interno} quedó por lo neto entregado`);
      invalidarDetalles([devolucionDe.id]);
      setDevolucionDe(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo registrar la devolución");
    } finally {
      setDevolBusy(false);
    }
  }

  async function getDetalle(r: Remision): Promise<RemisionDetail | null> {
    if (detalles[r.id]) return detalles[r.id];
    try {
      const d = await apiFetch<RemisionDetail>(`/api/v1/remisiones/${r.id}`);
      setDetalles((m) => ({ ...m, [r.id]: d }));
      return d;
    } catch {
      return null;
    }
  }

  // Abre el PDF de la remisión (mismo diseño que la factura). La ventana se abre
  // ANTES del fetch para que el navegador no la bloquee como pop-up.
  function imprimirRemision(r: Remision) {
    const win = window.open("", "_blank");
    apiOpenInTab(`/api/v1/remisiones/${r.id}/pdf`, win).catch((e) => {
      toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el PDF");
    });
  }

  // ── enviar por correo ──
  const [toSend, setToSend] = useState<Remision | null>(null);
  const [sendTo, setSendTo] = useState("");
  const [sendMensaje, setSendMensaje] = useState("");
  const [sending, setSending] = useState(false);
  // Envío masivo desglosado por cliente.
  const [bulkSendOpen, setBulkSendOpen] = useState(false);
  const [bulkSendRows, setBulkSendRows] = useState<
    { clienteId: string; nombre: string; remIds: string[]; correos: string }[]
  >([]);
  const [bulkSendMensaje, setBulkSendMensaje] = useState("");
  const [bulkSendBusy, setBulkSendBusy] = useState(false);

  function enviarRemision(r: Remision) {
    setSendTo(cliEmail[r.cliente_facturacion_id] ?? "");
    setSendMensaje("");
    setToSend(r);
  }

  async function confirmarEnvio() {
    if (!toSend) return;
    if (!sendTo.trim()) {
      toast.error("Indica un correo destinatario");
      return;
    }
    setSending(true);
    try {
      await apiFetch(`/api/v1/remisiones/${toSend.id}/enviar`, {
        method: "POST",
        body: JSON.stringify({ to: sendTo.trim(), mensaje: sendMensaje.trim() || undefined }),
      });
      toast.success(`Remisión enviada a ${sendTo.trim()}`);
      setToSend(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo enviar la remisión");
    } finally {
      setSending(false);
    }
  }

  // ── acciones en lote sobre las remisiones seleccionadas ──
  const [bulkBusy, setBulkBusy] = useState(false);
  const [facturarOpen, setFacturarOpen] = useState(false);
  // Cuando se factura UNA remisión puntual (p. ej. "Timbrar" desde el alta) en
  // vez de la selección de la lista. null = usar la selección.
  const [facturarSolo, setFacturarSolo] = useState<Remision | null>(null);
  // ¿Hay productos repetidos entre las elegibles? Solo entonces "sumatoria vs
  // sin sumatoria" cambia algo; si no, se muestra un único botón de facturar.
  const [facturarDups, setFacturarDups] = useState(false);
  // Facturas que fallaron por existencia y esperan decisión de sobregiro.
  const [facturarSobregiro, setFacturarSobregiro] = useState<
    { grupos: string[][]; agrupar: boolean; timbradas: number; borrador: number; omitidas: number; otras: number } | null
  >(null);
  const [confirmarBulkOpen, setConfirmarBulkOpen] = useState(false);
  // Remisiones del lote sin existencia que esperan decisión de sobregiro.
  const [confirmarSobregiro, setConfirmarSobregiro] = useState<
    { rems: Remision[]; ok: number; otras: number } | null
  >(null);
  // Cancelación masiva con doble verificación: paso 1 → paso 2 → ejecuta.
  const [cancelBulkStep, setCancelBulkStep] = useState<0 | 1 | 2>(0);

  // Imprimir todas las seleccionadas en una sola ventana.
  // Abre un solo PDF con todas las remisiones seleccionadas (una por página),
  // con el mismo diseño que el PDF individual.
  function bulkImprimir() {
    const ids = selected.map((r) => r.id);
    if (ids.length === 0) return;
    const win = window.open("", "_blank");
    apiOpenInTab(`/api/v1/remisiones/pdf?ids=${ids.join(",")}`, win).catch((e) => {
      toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el PDF");
    });
  }

  // Confirma un lote de remisiones. Devuelve cuántas se confirmaron, cuáles
  // fallaron por falta de existencia (para ofrecer sobregiro) y cuántas por otro
  // motivo.
  async function enviarConfirmaciones(
    rems: Remision[],
    permitir_negativos: boolean,
  ): Promise<{ ok: number; sinStock: Remision[]; otras: number }> {
    const results = await Promise.allSettled(
      rems.map((r) => post(`/api/v1/remisiones/${r.id}/confirmar`, { permitir_negativos })),
    );
    invalidarDetalles(rems.map((r) => r.id));
    const sinStock: Remision[] = [];
    let ok = 0;
    let otras = 0;
    results.forEach((res, i) => {
      if (res.status === "fulfilled") ok += 1;
      else if (res.reason instanceof ApiError && /existencia insuficiente/i.test(res.reason.message)) sinStock.push(rems[i]);
      else otras += 1;
    });
    return { ok, sinStock, otras };
  }

  function reportarConfirmar(ok: number, fallidas: number) {
    const partes = [`Confirmadas: ${ok}`];
    if (fallidas) partes.push(`${fallidas} con error`);
    toast[fallidas === 0 ? "success" : "error"](partes.join(" · "));
    clearSelection();
    reload();
  }

  // Confirmar en lote las remisiones en BORRADOR seleccionadas (reserva stock).
  // Si alguna no tiene existencia, se ofrece confirmarla con sobregiro.
  async function bulkConfirmar() {
    const elegibles = selected.filter((r) => r.estado === "BORRADOR" || r.estado === "RESERVADO");
    if (elegibles.length === 0) {
      toast.error("No hay remisiones en borrador en la selección");
      setConfirmarBulkOpen(false);
      return;
    }
    setBulkBusy(true);
    try {
      const { ok, sinStock, otras } = await enviarConfirmaciones(elegibles, false);
      setConfirmarBulkOpen(false);
      if (sinStock.length > 0) {
        setConfirmarSobregiro({ rems: sinStock, ok, otras });
        return;
      }
      reportarConfirmar(ok, otras);
    } finally {
      setBulkBusy(false);
    }
  }

  // Reintenta con sobregiro (inventario negativo) las remisiones del lote que
  // fallaron por falta de existencia al confirmar.
  async function aceptarConfirmarSobregiro() {
    if (!confirmarSobregiro) return;
    const { rems, ok, otras } = confirmarSobregiro;
    setBulkBusy(true);
    try {
      const r = await enviarConfirmaciones(rems, true);
      setConfirmarSobregiro(null);
      reportarConfirmar(ok + r.ok, otras + r.otras);
    } finally {
      setBulkBusy(false);
    }
  }

  function declinarConfirmarSobregiro() {
    if (!confirmarSobregiro) return;
    const { rems, ok, otras } = confirmarSobregiro;
    setConfirmarSobregiro(null);
    reportarConfirmar(ok, otras + rems.length);
  }

  // Cancelar en lote (libera inventario de las confirmadas). Doble verificación.
  async function bulkCancelar() {
    // Mismo criterio que `cancelablesSel` (y los diálogos): las FACTURADAS se
    // omiten; POSTearlas solo produce 409 del backend y un conteo de errores falso.
    const elegibles = selected.filter((r) => r.estado !== "CANCELADA" && r.estado !== "FACTURADA");
    if (elegibles.length === 0) {
      toast.error("No hay remisiones cancelables en la selección");
      setCancelBulkStep(0);
      return;
    }
    setBulkBusy(true);
    try {
      const results = await Promise.allSettled(
        elegibles.map((r) => post(`/api/v1/remisiones/${r.id}/cancelar`, {})),
      );
      invalidarDetalles(elegibles.map((r) => r.id));
      const ok = results.filter((x) => x.status === "fulfilled").length;
      const fail = results.length - ok;
      const partes = [`Canceladas: ${ok}`];
      if (fail) partes.push(`${fail} con error`);
      toast[fail === 0 ? "success" : "error"](partes.join(" · "));
      setCancelBulkStep(0);
      clearSelection();
      reload();
    } finally {
      setBulkBusy(false);
    }
  }

  // Envío masivo: desglosa las seleccionadas POR CLIENTE (un renglón por cliente,
  // con sus correos editables) y abre el popup. Vacío = ignorar ese cliente.
  function bulkEnviar() {
    if (selected.length === 0) return;
    const byCliente = new Map<string, string[]>();
    for (const r of selected) {
      const arr = byCliente.get(r.cliente_facturacion_id) ?? [];
      arr.push(r.id);
      byCliente.set(r.cliente_facturacion_id, arr);
    }
    setBulkSendRows([...byCliente.entries()].map(([clienteId, remIds]) => ({
      clienteId, nombre: cliName[clienteId] ?? "—", remIds, correos: cliEmail[clienteId] ?? "",
    })));
    setBulkSendMensaje("");
    setBulkSendOpen(true);
  }

  async function confirmarBulkEnvio() {
    const activos = bulkSendRows.filter((row) => row.correos.trim());
    if (activos.length === 0) { toast.error("No hay correos: agrega al menos uno o cancela"); return; }
    const mensaje = bulkSendMensaje.trim() || undefined;
    setBulkSendBusy(true);
    // Un correo por cliente: cada cliente recibe TODAS sus remisiones en un solo
    // correo (un PDF adjunto por remisión). ok/fail cuentan correos (clientes).
    let ok = 0, fail = 0;
    try {
      for (const row of activos) {
        try {
          await apiFetch(`/api/v1/remisiones/enviar-lote`, {
            method: "POST",
            body: JSON.stringify({ ids: row.remIds, to: row.correos.trim(), mensaje }),
          });
          ok += 1;
        } catch { fail += 1; }
      }
      const ignorados = bulkSendRows.filter((r) => !r.correos.trim()).length;
      const partes = [`Correos enviados: ${ok}`];
      if (fail) partes.push(`${fail} fallidos`);
      if (ignorados) partes.push(`${ignorados} cliente(s) ignorado(s)`);
      toast[fail === 0 ? "success" : "error"](partes.join(" · "));
      setBulkSendOpen(false);
      clearSelection();
    } finally {
      setBulkSendBusy(false);
    }
  }

  // Facturar las seleccionadas. `modo`:
  //  - "sumar":     una factura por cliente, sumando líneas del mismo producto en un concepto
  //  - "sin_sumar": una factura por cliente, una línea por cada partida de remisión
  //  - "separado":  una factura por remisión
  // Envía un lote de grupos a facturar. Devuelve cuántas se crearon, cuáles
  // fallaron por falta de existencia (para ofrecer sobregiro) y cuántas por otro
  // motivo. Facturar un BORRADOR lo auto-confirma en el backend (reserva stock).
  async function enviarFacturas(
    grupos: string[][],
    agrupar_productos: boolean,
    permitir_negativos: boolean,
  ): Promise<{ timbradas: number; sinStock: string[][]; borrador: number; otras: number }> {
    // Facturar directo: por cada grupo se crea la factura y SE TIMBRA de inmediato.
    // Si el PAC rechaza (o Facturama no está listo), la factura queda en BORRADOR
    // y se puede reintentar/descartar desde Facturas — no se pierde el trabajo.
    const resultados = await Promise.all(
      grupos.map(async (remision_ids): Promise<"timbrada" | "borrador" | "sinStock" | "otra"> => {
        let fac: { id: string };
        try {
          fac = await post<{ id: string }>(
            "/api/v1/facturas/desde-remisiones",
            { remision_ids, agrupar_productos, permitir_negativos },
          );
        } catch (e) {
          if (e instanceof ApiError && /existencia insuficiente/i.test(e.message)) return "sinStock";
          return "otra";
        }
        try {
          await post(`/api/v1/facturas/${fac.id}/timbrar`);
          return "timbrada";
        } catch {
          return "borrador"; // creada pero el timbrado falló
        }
      }),
    );
    invalidarDetalles(grupos.flat());
    const sinStock: string[][] = [];
    let timbradas = 0, borrador = 0, otras = 0;
    resultados.forEach((r, i) => {
      if (r === "timbrada") timbradas += 1;
      else if (r === "borrador") borrador += 1;
      else if (r === "sinStock") sinStock.push(grupos[i]);
      else otras += 1;
    });
    return { timbradas, sinStock, borrador, otras };
  }

  function reportarFacturar(timbradas: number, borrador: number, fallidas: number, omitidas: number) {
    const partes = [`Facturas timbradas: ${timbradas}`];
    if (borrador) partes.push(`${borrador} sin timbrar (quedaron en borrador, revisa en Facturas)`);
    if (fallidas) partes.push(`${fallidas} fallidas`);
    if (omitidas) partes.push(`${omitidas} omitidas`);
    toast[borrador === 0 && fallidas === 0 ? "success" : "error"](partes.join(" · "));
    clearSelection();
    setFacturarSolo(null);
    reload();
  }

  // Sumatoria vs "sin sumatoria" solo cambian el resultado si hay renglones del
  // mismo producto+presentación repetidos; si no, elegir modo es innecesario.
  function hayProductosRepetidos(lineas: { producto_id: string; presentacion: string }[]): boolean {
    const seen = new Set<string>();
    for (const l of lineas) {
      const k = `${l.producto_id}|${l.presentacion}`;
      if (seen.has(k)) return true;
      seen.add(k);
    }
    return false;
  }

  // Núcleo: factura la lista `base` de remisiones con el `modo` elegido.
  // Solo elegibles: BORRADOR o CONFIRMADA sin factura previa. El resto se omite.
  async function facturarRems(base: Remision[], modo: "sumar" | "sin_sumar" | "separado") {
    const elegibles = base.filter(puedeFacturar);
    const omitidas = base.length - elegibles.length;
    if (elegibles.length === 0) {
      toast.error(`Ninguna remisión elegible (se omitieron ${omitidas} canceladas o ya facturadas)`);
      setFacturarOpen(false);
      return;
    }
    // grupos de ids según el modo
    let grupos: string[][];
    if (modo === "separado") {
      grupos = elegibles.map((r) => [r.id]);
    } else {
      const byCliente = new Map<string, string[]>();
      for (const r of elegibles) {
        const arr = byCliente.get(r.cliente_facturacion_id) ?? [];
        arr.push(r.id);
        byCliente.set(r.cliente_facturacion_id, arr);
      }
      grupos = [...byCliente.values()];
    }
    const agrupar_productos = modo === "sumar";
    setBulkBusy(true);
    try {
      const { timbradas, sinStock, borrador, otras } = await enviarFacturas(grupos, agrupar_productos, false);
      setFacturarOpen(false);
      if (sinStock.length > 0) {
        // Hay remisiones sin existencia: ofrece facturar con sobregiro.
        setFacturarSobregiro({ grupos: sinStock, agrupar: agrupar_productos, timbradas, borrador, omitidas, otras });
        return;
      }
      reportarFacturar(timbradas, borrador, otras, omitidas);
    } finally {
      setBulkBusy(false);
    }
  }

  // Modo elegido en el popup: opera sobre `facturarSolo` (p. ej. "Timbrar" del
  // alta) o la selección de la lista.
  async function bulkFacturar(modo: "sumar" | "sin_sumar" | "separado") {
    await facturarRems(facturarSolo ? [facturarSolo] : selected, modo);
  }

  // "Facturar" desde la lista. Salta el popup SOLO si es inequívoco: seleccionaste
  // exactamente 1 remisión, es elegible y sin productos repetidos → timbra directo.
  // Si seleccionaste varias (aunque solo 1 sea elegible), abre el popup para que
  // veas CUÁLES se van a facturar y elijas el modo si aplica.
  async function abrirFacturarLista() {
    const elegibles = selected.filter(puedeFacturar);
    if (elegibles.length === 0) return;
    // Trae detalles de las elegibles para saber si hay productos repetidos
    // (define si "sumatoria vs sin sumatoria" aporta algo).
    const dets = (await Promise.all(elegibles.map((r) => getDetalle(r))))
      .filter((d): d is RemisionDetail => d != null);
    const dups = hayProductosRepetidos(dets.flatMap((d) => d.lineas));
    setFacturarDups(dups);
    // Salto directo: seleccionaste exactamente 1, elegible, sin repetidos.
    if (selected.length === 1 && elegibles.length === 1 && !dups) {
      await facturarRems([elegibles[0]], "sin_sumar");
      return;
    }
    setFacturarOpen(true);
  }

  // Reintenta con sobregiro (inventario negativo) las facturas que fallaron por
  // falta de existencia al auto-confirmar un borrador.
  async function confirmarFacturarSobregiro() {
    if (!facturarSobregiro) return;
    const { grupos, agrupar, timbradas, borrador, omitidas, otras } = facturarSobregiro;
    setBulkBusy(true);
    try {
      const r = await enviarFacturas(grupos, agrupar, true);
      setFacturarSobregiro(null);
      reportarFacturar(timbradas + r.timbradas, borrador + r.borrador, otras + r.otras, omitidas);
    } finally {
      setBulkBusy(false);
    }
  }

  function declinarFacturarSobregiro() {
    if (!facturarSobregiro) return;
    const { grupos, timbradas, borrador, omitidas, otras } = facturarSobregiro;
    setFacturarSobregiro(null);
    // Las que no se facturaron por falta de existencia cuentan como fallidas.
    reportarFacturar(timbradas, borrador, otras + grupos.length, omitidas);
  }

  const columns: Column<Remision>[] = [
    { header: "Folio", sortable: true, sortValue: (r) => r.folio_interno, cell: (r) => <span className="font-medium">{r.folio_interno}</span> },
    {
      header: "Factura",
      sortable: true,
      sortValue: (r) => r.factura_folio ?? "",
      cell: (r) =>
        r.factura_folio ? (
          <span className="inline-flex items-center gap-1.5">
            {r.factura_id ? (
              <button
                type="button"
                title="Ver la factura"
                onClick={(e) => { e.stopPropagation(); router.push(`/facturas?ver=${r.factura_id}`); }}
                className="font-medium text-accent hover:underline"
              >
                {r.factura_folio}
              </button>
            ) : (
              <span className="font-medium">{r.factura_folio}</span>
            )}
            {r.factura_estado && (
              <Badge tone={FACTURA_TONE[r.factura_estado] ?? "muted"}>{r.factura_estado}</Badge>
            )}
          </span>
        ) : (
          <span className="text-muted">—</span>
        ),
    },
    {
      header: "Su pedido",
      sortable: true,
      sortValue: (r) => r.su_pedido ?? "",
      cell: (r) => r.su_pedido
        ? <span className="tabular-nums">{r.su_pedido}</span>
        : <span className="text-muted">—</span>,
    },
    {
      header: "Factura SAE",
      sortable: true,
      sortValue: (r) => r.factura_sae ?? "",
      cell: (r) => r.factura_sae
        ? <span className="font-medium tabular-nums">{r.factura_sae}</span>
        : <span className="text-muted">—</span>,
    },
    { header: "Cliente", sortable: true, sortValue: (r) => cliName[r.cliente_facturacion_id] ?? "", cell: (r) => cliName[r.cliente_facturacion_id] ?? "—" },
    { header: "Fecha", sortable: true, sortValue: (r) => r.fecha_remision, cell: (r) => fmtDate(r.fecha_remision) },
    { header: "Estado", sortable: true, sortValue: (r) => r.estado, cell: (r) => <Badge tone={ESTADO_TONE[r.estado] ?? "muted"}>{r.estado}</Badge> },
    { header: "Subtotal", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.subtotal) },
    { header: "IEPS", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.ieps) },
    { header: "IVA", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.iva) },
    { header: "Total", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.total) },
    { header: "Nota", cell: (r) => r.notas ?? "—" },
  ];

  const rowActions: RowAction<Remision>[] = [
    { id: "editar", label: "Editar", icon: <Pencil size={15} />, onClick: (r) => { void openEdit(r); },
      hidden: (r) => !(canWrite && (r.estado === "BORRADOR" || r.estado === "RESERVADO" || r.estado === "CONFIRMADA")
        && (!r.factura_id || r.factura_estado === "CANCELADA")) },
    { id: "confirmar", label: "Confirmar", icon: <Check size={15} />, tone: "success",
      onClick: (r) => setToConfirm(r),
      hidden: (r) => !(canWrite && (r.estado === "BORRADOR" || r.estado === "RESERVADO")) },
    { id: "cancelar", label: "Cancelar", icon: <X size={15} />, tone: "danger",
      onClick: (r) => setToCancel(r), hidden: (r) => !(canWrite && r.estado !== "CANCELADA" && r.estado !== "FACTURADA") },
    { id: "devolucion", label: "Devolución", icon: <Undo2 size={15} />,
      onClick: (r) => { void abrirDevolucion(r); },
      hidden: (r) => !(canWrite && r.estado === "CONFIRMADA") },
    { id: "imprimir", label: "Imprimir", icon: <Printer size={15} />, onClick: (r) => { void imprimirRemision(r); } },
    { id: "enviar", label: "Enviar por correo", icon: <Mail size={15} />, onClick: enviarRemision,
      hidden: () => !canWrite },
  ];

  // ───────────────────────── render ─────────────────────────
  if (mode === "create") {
    return (
      <div>
        <PageHeader
          title={editId ? "Editar remisión" : "Nueva remisión"}
          subtitle={editEstado === "CONFIRMADA"
            ? "Confirmada — al guardar se reajusta el inventario reservado"
            : "Borrador — al confirmar se reserva el inventario"}
          actions={<Button variant="secondary" onClick={() => { setEditId(null); setEditEstado(null); setMode("list"); }}><X size={16} /> Cancelar</Button>}
        />

        <div className="grid grid-cols-1 gap-4 rounded-xl border border-border p-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Cliente" required hint="Escribe y usa ↑/↓ · Enter para seleccionar y avanzar">
            <KeyboardCombobox
              options={clienteOpts}
              value={clienteId}
              onSelect={(v) => selectCliente(v)}
              autoOpen={step === "cliente"}
              placeholder="Buscar cliente…"
              emptyText="Sin clientes"
            />
          </Field>
          <Field label="Sucursal">
            <KeyboardCombobox
              options={sucursalOpts}
              value={sucursalId}
              onSelect={setSucursalId}
              onAdvance={() => resolveFrom("almacen")}
              autoOpen={step === "sucursal"}
              placeholder="(matriz / sin sucursal)"
              emptyText="Sin sucursales"
              disabled={!clienteId}
            />
          </Field>
          <Field label="Almacén">
            <KeyboardCombobox
              options={almacenOpts}
              value={almacenId}
              onSelect={setAlmacenId}
              onAdvance={() => resolveFrom("lineas")}
              autoOpen={step === "almacen"}
              placeholder="Sin almacén"
              emptyText="Sin almacén"
            />
          </Field>
          <Field label="Fecha">
            <Input type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} />
          </Field>
          {/* En edición el folio ya está asignado: el backend ignora serie_id en
              el PATCH, así que la Serie se bloquea y se muestra el folio real. */}
          <Field label="Serie">
            <KeyboardCombobox
              options={serieOpts}
              value={serieOverride}
              onSelect={setSerieOverride}
              onAdvance={() => resolveFrom("lineas")}
              autoOpen={step === "serie"}
              placeholder="Serie…"
              disabled={editId != null}
            />
          </Field>
          <Field label="Consecutivo (informativo)">
            <Input value={editId ? editFolio ?? "—" : folioPreview} readOnly disabled aria-label="Folio consecutivo" />
          </Field>
        </div>

        <div className="mt-4 rounded-xl border border-border p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Líneas</h2>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setPasteOpen(true)}><ClipboardPaste size={16} /> Pegar de Excel</Button>
              <Button variant="secondary" onClick={() => setLineas((ls) => [...ls, nuevaLinea()])}><Plus size={16} /> Línea</Button>
            </div>
          </div>

          <div className="space-y-2">
            <div className="hidden grid-cols-12 gap-2 px-1 text-xs text-muted sm:grid">
              <div className={showMatchIA ? "col-span-1" : "col-span-2"}>Cantidad</div>
              <div className="col-span-3">Producto</div>
              {showMatchIA && <div className="col-span-3 inline-flex items-center gap-1"><Sparkles size={12} /> Match IA</div>}
              <div className="col-span-2">Presentación</div>
              <div className="col-span-2">Precio</div>
              {!showMatchIA && <div className="col-span-1 text-right">IEPS</div>}
              {!showMatchIA && <div className="col-span-1 text-right">IVA</div>}
              <div className="col-span-1 text-right">Importe</div>
            </div>
            {lineas.map((l) => {
              // En el desplegable Match IA solo se ofrecen coincidencias ≥80%.
              const cands = (l.candidatos ?? []).filter((c) => c.score >= 80);
              const enCands = cands.some((c) => c.producto_id === l.producto_id);
              return (
              <div key={l.key} className="grid grid-cols-12 items-start gap-2">
                <div className={`col-span-3 ${showMatchIA ? "sm:col-span-1" : "sm:col-span-2"}`}>
                  <Input
                    inputMode="decimal" value={l.cantidad}
                    ref={(el) => { cellRefs.current[`${l.key}:cantidad`] = el; }}
                    onChange={(e) => { const v = e.target.value.replace(",", "."); setLinea(l.key, { cantidad: v, importe: Number(l.precio || 0) * Number(v || 0) }); cotizar(l.key, l.producto_id, l.presentacion, v); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); advanceLine(l.key, "cantidad"); } }}
                    onPaste={(e) => {
                      const text = e.clipboardData.getData("text");
                      if (text.includes("\n")) { e.preventDefault(); pegarColumnaCantidad(l.key, text); }
                    }}
                  />
                </div>
                <div className="col-span-12 sm:col-span-3">
                  <ProductoCombobox
                    label={l.label || l.texto}
                    onSelect={(p, t) => onPickProducto(l.key, p, t)}
                    autoFocus={lineFocus?.key === l.key && lineFocus?.field === "producto"}
                    onPaste={(e) => {
                      const text = e.clipboardData.getData("text");
                      if (text.includes("\n")) { e.preventDefault(); void pegarColumnaProducto(l.key, text); }
                    }}
                  />
                </div>
                {showMatchIA && (
                  <div className="col-span-12 sm:col-span-3">
                    {l.fromPaste ? (
                      <Select
                        value={l.producto_id || NUEVO_PRODUCTO}
                        onChange={(e) => resolverMatchIA(l.key, e.target.value)}
                      >
                        {l.producto_id && !enCands && <option value={l.producto_id}>{l.label || "Producto elegido"}</option>}
                        {cands.map((c) => (
                          <option key={c.producto_id} value={c.producto_id}>{c.nombre} · {c.score}%{c.origen === "ia" ? " (IA)" : ""}</option>
                        ))}
                        <option value={NUEVO_PRODUCTO}>＋ Crear producto nuevo</option>
                      </Select>
                    ) : (
                      <span className="text-xs text-muted">—</span>
                    )}
                  </div>
                )}
                <div className="col-span-4 sm:col-span-2">
                  <KeyboardCombobox
                    options={(l.presentaciones.length ? l.presentaciones : [l.presentacion]).map((p) => ({ value: p, label: p }))}
                    value={l.presentacion}
                    onSelect={(v) => { setLinea(l.key, { presentacion: v }); cotizar(l.key, l.producto_id, v, l.cantidad); }}
                    onAdvance={() => advanceLine(l.key, "presentacion")}
                    autoOpen={lineFocus?.key === l.key && lineFocus?.field === "presentacion"}
                    placeholder="Presentación"
                  />
                </div>
                <div className="col-span-3 sm:col-span-2">
                  <Input
                    inputMode="decimal" placeholder="auto" value={l.precio}
                    ref={(el) => { cellRefs.current[`${l.key}:precio`] = el; }}
                    onChange={(e) => { const v = e.target.value.replace(",", "."); setLinea(l.key, { precio: v, precioManual: true, importe: Number(v || 0) * Number(l.cantidad || 0) }); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); advanceLine(l.key, "precio"); } }}
                  />
                </div>
                {!showMatchIA && (
                  <div className="col-span-2 flex items-center justify-end sm:col-span-1">
                    <span className="text-sm tabular-nums">{fmtMoney(fiscalPreview?.porKey[l.key]?.ieps ?? 0)}</span>
                  </div>
                )}
                {!showMatchIA && (
                  <div className="col-span-2 flex items-center justify-end sm:col-span-1">
                    <span className="text-sm tabular-nums">{fmtMoney(fiscalPreview?.porKey[l.key]?.iva ?? 0)}</span>
                  </div>
                )}
                <div className="col-span-2 flex items-center justify-end gap-1 sm:col-span-1">
                  <span className="text-sm tabular-nums">{fmtMoney(l.importe)}</span>
                  <button aria-label="Quitar línea" onClick={() => setLineas((ls) => ls.filter((x) => x.key !== l.key))} className="text-muted hover:text-danger"><Trash2 size={15} /></button>
                </div>
              </div>
              );
            })}
          </div>

          <div className="grid gap-3 sm:grid-cols-[1fr_14rem_14rem]">
            <Field label="Notas">
              <Textarea rows={2} value={notas} onChange={(e) => setNotas(e.target.value)} />
            </Field>
            <Field label="Su pedido" hint="Orden de compra del cliente">
              <Input value={suPedido} onChange={(e) => setSuPedido(e.target.value)}
                placeholder="24478" maxLength={30} />
            </Field>
            <Field label="Factura SAE" hint="Folio en SAE; al ponerlo la remisión queda RESERVADA">
              <Input value={facturaSae} onChange={(e) => setFacturaSae(e.target.value)}
                placeholder="ZHGO 233" maxLength={30} />
            </Field>
          </div>

          <div className="mt-4 flex items-start justify-between border-t border-border pt-4">
            <div />
            <div className="flex flex-col items-end gap-4">
              <div className="flex flex-col items-end gap-1 text-sm">
                <div className="flex gap-4"><span className="text-muted">Subtotal</span><span className="tabular-nums">{fmtMoney(subtotalPreview)}</span></div>
                <div className="flex gap-4"><span className="text-muted">IEPS</span><span className="tabular-nums">{fmtMoney(iepsPreview)}</span></div>
                <div className="flex gap-4"><span className="text-muted">IVA</span><span className="tabular-nums">{fmtMoney(ivaPreview)}</span></div>
                <div className="flex gap-4 text-base font-semibold"><span className="text-muted">Total</span><span className="tabular-nums">{fmtMoney(totalPreview)}</span></div>
              </div>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={resetForm} disabled={saving}>Borrar</Button>
                <Button onClick={abrirGuardar} disabled={saving}>{saving ? "Guardando…" : "Guardar"}</Button>
              </div>
            </div>
          </div>
        </div>

        <Modal open={pasteOpen} onClose={() => { if (!procesando) cerrarPaste(); }} title="Pegar líneas desde Excel"
          footer={<>
            <Button variant="secondary" onClick={cerrarPaste} disabled={procesando}>Cancelar</Button>
            <Button onClick={procesarPaste} disabled={procesando}>{procesando ? <>Procesando<LoadingDots /></> : "Procesar"}</Button>
          </>}>
          <p className="mb-2 text-sm text-muted">Pega las columnas desde Excel (una fila por línea). La <strong>IA detecta</strong> qué columna es <strong>producto</strong>, <strong>cantidad</strong>, <strong>precio</strong> y <strong>presentación</strong> aunque vengan en cualquier orden, e <strong>ignora el encabezado</strong>. Las líneas entran a la tabla y una columna <strong>Match IA</strong> aparece para elegir el producto del catálogo o crearlo, ahí mismo.</p>
          <Textarea rows={8} value={pasteText} onChange={(e) => setPasteText(e.target.value)} placeholder={"zanahoria\t10\tKILO\njitomate\t5\t12.50"} />
        </Modal>

        <CrearProductoModal
          open={crearParaKey !== null}
          onClose={() => setCrearParaKey(null)}
          nombreInicial={lineaCrear?.texto ?? ""}
          unidadBaseInicial={unidadBaseDesde(lineaCrear?.presPegada)}
          onCreated={aplicarProductoCreado}
        />

        <Modal open={guardarChoiceOpen} onClose={() => setGuardarChoiceOpen(false)} title="Guardar remisión"
          footer={
            <>
              <Button variant="secondary" onClick={() => setGuardarChoiceOpen(false)} disabled={saving}>Cancelar</Button>
              <Button variant="secondary" onClick={guardarBorrador} disabled={saving}>Borrador</Button>
              <Button variant="secondary" onClick={guardarYConfirmar} disabled={saving}>Confirmar salida (Inventario)</Button>
              <Button onClick={guardarYTimbrar} disabled={saving}>Timbrar</Button>
            </>
          }>
          <p className="mb-2 text-sm text-muted">Elige cómo guardar la remisión:</p>
          <ul className="ml-4 list-disc space-y-1 text-sm text-muted">
            <li><strong>Borrador</strong>: se guarda sin afectar el inventario.</li>
            <li><strong>Confirmar salida (Inventario)</strong>: reserva y descuenta el inventario. Si no hay existencia suficiente, se ofrece remisionar en negativo (sobregiro).</li>
            <li><strong>Timbrar</strong>: crea la remisión y la factura de inmediato (se elige el formato y se timbra ante el PAC).</li>
          </ul>
        </Modal>
      </div>
    );
  }

  // Remisiones elegibles a facturar dentro de la selección y sus clientes distintos.
  // Una sola factura solo es válida con un único cliente: si hay varios, esas
  // opciones se deshabilitan en el popup (solo queda "Separado").
  const facturarElegibles = (facturarSolo ? [facturarSolo] : selected).filter(puedeFacturar);
  const clientesElegibles = [...new Set(facturarElegibles.map((r) => r.cliente_facturacion_id))];
  const multiCliente = clientesElegibles.length > 1;
  // Borradores (confirmables) y no-canceladas (cancelables) dentro de la selección.
  const borradoresSel = selected.filter((r) => r.estado === "BORRADOR" || r.estado === "RESERVADO");
  const cancelablesSel = selected.filter((r) => r.estado !== "CANCELADA" && r.estado !== "FACTURADA");

  return (
    <div>
      <PageHeader
        title="Remisiones"
        subtitle="Notas de entrega (no fiscales)"
        actions={canWrite ? (
          <>
            <Button variant="secondary" onClick={() => importInputRef.current?.click()}>
              <ClipboardPaste size={16} /> Importar Excel
            </Button>
            <Button onClick={openCreate}><Plus size={16} /> Nueva remisión</Button>
          </>
        ) : undefined}
      />

      {/* Filtros */}
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Field label="Desde">
          <Input type="date" value={fDesde} onChange={(e) => setFDesde(e.target.value)} />
        </Field>
        <Field label="Hasta">
          <Input type="date" value={fHasta} onChange={(e) => setFHasta(e.target.value)} />
        </Field>
        <Field label="Cliente">
          <Select className="min-w-64" value={fCliente} onChange={(e) => setFCliente(e.target.value)} aria-label="Filtrar por cliente">
            <option value="">Todos</option>
            {clientes.map((c) => (
              <option key={c.id} value={c.id}>{c.legal_name}</option>
            ))}
          </Select>
        </Field>
        {(fDesde || fHasta || fCliente) && (
          <Button variant="secondary" onClick={() => { setFDesde(""); setFHasta(""); setFCliente(""); }}>
            Limpiar filtros
          </Button>
        )}
      </div>

      {/* Barra de acciones en lote */}
      {selected.length > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2 px-4 py-2.5 shadow-sm">
          <span className="text-sm font-medium">{selected.length} seleccionada(s)</span>
          <span className="text-sm text-muted">·</span>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => { void bulkImprimir(); }} disabled={bulkBusy}>
              <Printer size={16} /> Imprimir ({selected.length})
            </Button>
            {canWrite && (
              <Button variant="secondary" onClick={() => { void bulkEnviar(); }} disabled={bulkBusy}>
                <Mail size={16} /> Enviar por correo ({selected.length})
              </Button>
            )}
            {canWrite && borradoresSel.length > 0 && (
              <Button variant="success" onClick={() => setConfirmarBulkOpen(true)} disabled={bulkBusy}>
                <Check size={16} /> Confirmar ({borradoresSel.length})
              </Button>
            )}
            {canWrite && facturarElegibles.length > 0 && (
              <Button onClick={() => { void abrirFacturarLista(); }} disabled={bulkBusy}>
                <FileText size={16} /> Facturar ({facturarElegibles.length})
              </Button>
            )}
            {canWrite && cancelablesSel.length > 0 && (
              <Button variant="danger" onClick={() => setCancelBulkStep(1)} disabled={bulkBusy}>
                <X size={16} /> Cancelar ({cancelablesSel.length})
              </Button>
            )}
          </div>
          <button
            type="button"
            onClick={clearSelection}
            className="ml-auto text-sm text-muted hover:text-foreground"
          >
            Limpiar selección
          </button>
        </div>
      )}

      <DataTableSmart
        columns={columns}
        rows={filteredRows}
        loading={loading}
        error={error}
        empty="Sin remisiones"
        rowKey={(r) => r.id}
        actions={rowActions}
        onRowExpand={verDetalle}
        renderExpanded={renderDetalle}
        storageKey="remisiones"
        selectable
        onSelectionChange={setSelected}
        selectionResetKey={selectionResetKey}
      />

      <ConfirmDialog open={toConfirm !== null} title="Confirmar remisión"
        message={`¿Confirmar ${toConfirm?.folio_interno}? Se reservará el inventario.`}
        confirmLabel="Confirmar" confirmVariant="success"
        onConfirm={confirmar} onClose={() => setToConfirm(null)} loading={saving} />
      <ConfirmDialog open={toCancel !== null} title="Cancelar remisión"
        message={`¿Cancelar ${toCancel?.folio_interno}? Se liberará el inventario reservado.`}
        confirmLabel="Cancelada" confirmVariant="danger"
        onConfirm={cancelar} onClose={() => setToCancel(null)} loading={saving} />
      <ConfirmDialog open={facturarSobregiro !== null} title="Existencia insuficiente"
        message={`${facturarSobregiro?.grupos.length ?? 0} factura(s) no tienen existencia suficiente para el/los borrador(es). ¿Facturar de todas formas? El inventario quedará en negativo (sobregiro).`}
        confirmLabel="Facturar con sobregiro" confirmVariant="danger"
        onConfirm={confirmarFacturarSobregiro} onClose={declinarFacturarSobregiro} loading={bulkBusy} />
      <ConfirmDialog open={negStock !== null} title="Existencia insuficiente"
        message={`No hay existencia suficiente para confirmar ${negStock?.folio}. ¿Deseas remisionar de todas formas? El inventario quedará en negativo (sobregiro).`}
        confirmLabel="Remisionar sin existencias" confirmVariant="primary"
        onConfirm={confirmarNegativo} onClose={() => setNegStock(null)} loading={saving} />

      {/* ── Importación masiva (SAE → remisiones) ── */}
      <input
        ref={importInputRef} type="file" accept=".xls,.xlsx" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void importarArchivo(f); }}
      />
      <Modal open={importGrupos !== null} onClose={() => setImportGrupos(null)}
        title={`Importar ${importGrupos?.length ?? 0} remisión(es) desde Excel`} wide
        footer={<>
          <Button variant="secondary" onClick={() => setImportGrupos(null)} disabled={importBusy}>Cancelar</Button>
          <Button onClick={() => void crearImportadas()} disabled={importBusy || !importListo}>
            {importBusy ? "Creando…" : `Crear ${importGrupos?.length ?? 0} remisiones`}
          </Button>
        </>}>
        <p className="mb-3 text-sm text-muted">
          Los folios los asigna el sistema con tu serie (sin capturas ni choques); el folio del
          archivo queda como referencia en las notas, sin ceros. Resuelve lo marcado en ámbar
          antes de crear.
        </p>
        <div className="mb-3 max-w-xs">
          <Field label="Almacén (para todas)" hint="Necesario para confirmar la salida después">
            <Select value={importAlmacen} onChange={(e) => setImportAlmacen(e.target.value)}>
              <option value="">— Sin almacén —</option>
              {almacenes.map((a) => <option key={a.id} value={a.id}>{a.nombre}</option>)}
            </Select>
          </Field>
        </div>
        <div className="max-h-[50vh] space-y-3 overflow-auto pr-1">
          {(importGrupos ?? []).map((g, gi) => {
            const cruzadas = g.lineas.filter((l) => l.producto_id && !l.omitir).length;
            const pendientes = g.lineas.filter((l) => !l.producto_id && !l.omitir);
            const importe = g.lineas
              .filter((l) => !l.omitir)
              .reduce((s, l) => s + Number(l.cantidad || 0) * Number(l.precio || 0), 0);
            return (
              <div key={g.folio_ref} className="rounded-xl border border-border p-3">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-medium">Ref {g.folio_ref}</span>
                  {g.requisicion && <span className="text-muted">Req {g.requisicion}</span>}
                  {g.fecha && <span className="text-muted">{fmtDate(g.fecha)}</span>}
                  {g.su_pedido && <span className="text-muted">Su pedido: {g.su_pedido}</span>}
                  {g.entregar_bodega && (
                    <span className="text-muted">Bodega: {g.entregar_bodega}</span>
                  )}
                  <span className="text-muted">
                    · {g.lineas.length} línea(s), {cruzadas} cruzada(s)
                    {pendientes.length > 0 ? `, ${pendientes.length} por resolver` : ""}
                    {" · "}{fmtMoney(importe)}
                  </span>
                  {g.cliente_id ? (
                    <Badge tone="success">{g.cliente_nombre}</Badge>
                  ) : (
                    <span className="flex items-center gap-2">
                      <Badge tone="warning">Cliente {g.cliente_rfc ?? g.cliente_codigo} sin cruce</Badge>
                      <Select
                        value=""
                        onChange={(e) => {
                          const id = e.target.value;
                          const nombre = clientes.find((c) => c.id === id)?.legal_name ?? null;
                          setImportGrupos((gs) => (gs ?? []).map((x, j) =>
                            j === gi ? { ...x, cliente_id: id || null, cliente_nombre: nombre } : x));
                        }}>
                        <option value="">— Elegir cliente —</option>
                        {clientes.map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
                      </Select>
                    </span>
                  )}
                </div>
                {g.observaciones && (
                  <p className="mt-1 text-xs text-muted">{g.observaciones}</p>
                )}
                {(
                  <div className="mt-2 space-y-1">
                    {g.lineas.map((l, li) => l.omitir ? (
                      <div key={`${g.folio_ref}-${li}`} className="flex flex-wrap items-center gap-2 text-sm opacity-50">
                        <Badge>{l.clave}</Badge>
                        <span className="text-muted line-through">{l.descripcion ?? "—"}</span>
                        <span className="text-muted">omitida</span>
                      </div>
                    ) : l.producto_id ? (
                      <div key={`${g.folio_ref}-${li}`} className="flex flex-wrap items-center gap-2 text-sm">
                        <Badge tone="success">{l.clave}</Badge>
                        <span>{l.producto_nombre}</span>
                        {l.cruce === "descripcion" && l.descripcion && (
                          <span className="text-muted">← {l.descripcion}</span>
                        )}
                        <span className="text-muted">
                          {fmtNumber(l.cantidad)}{l.unidad ? ` ${l.unidad.toLowerCase()}` : ""}
                          {l.precio ? ` × ${fmtMoney(Number(l.precio))}` : ""}
                        </span>
                      </div>
                    ) : (
                      <div key={`${g.folio_ref}-${li}`} className="flex flex-wrap items-center gap-2 text-sm">
                        <Badge tone="warning">{l.clave}</Badge>
                        {l.descripcion && <span className="text-muted">{l.descripcion}</span>}
                        <span className="text-muted">
                          x{fmtNumber(l.cantidad)}{l.unidad ? ` ${l.unidad.toLowerCase()}` : ""}
                        </span>
                        <Select
                          value=""
                          onChange={(e) => {
                            const v = e.target.value;
                            setImportGrupos((gs) => (gs ?? []).map((x, j) => {
                              if (j !== gi) return x;
                              const lineas = x.lineas.map((y, k) => {
                                if (k !== li) return y;
                                if (v === "__omitir__") return { ...y, omitir: true };
                                const cand = y.candidatos.find((c) => c.producto_id === v);
                                return v ? { ...y, producto_id: v, producto_nombre: cand?.nombre ?? null } : y;
                              });
                              return { ...x, lineas };
                            }));
                          }}>
                          <option value="">— Elegir producto —</option>
                          {l.candidatos.map((c) => (
                            <option key={c.producto_id} value={c.producto_id}>
                              {c.sku} · {c.nombre} ({c.score}%)
                            </option>
                          ))}
                          <option value="__omitir__">Omitir esta línea</option>
                        </Select>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Modal>

      {/* ── Devolución (ajusta la remisión a lo neto) ── */}
      <Modal open={devolucionDe !== null} onClose={() => setDevolucionDe(null)}
        title={`Devolución — ${devolucionDe?.folio_interno ?? ""}`} wide
        footer={<>
          <Button variant="secondary" onClick={() => setDevolucionDe(null)} disabled={devolBusy}>Cancelar</Button>
          <Button onClick={() => void confirmarDevolucion()} disabled={devolBusy}>
            {devolBusy ? "Registrando…" : "Registrar devolución"}
          </Button>
        </>}>
        <p className="mb-3 text-sm text-muted">
          Captura cuánto regresó de cada línea. La remisión quedará por lo <strong>neto entregado</strong>
          {" "}(si luego la facturas, el CFDI sale por lo neto) y el inventario regresa al almacén.
        </p>
        <div className="space-y-2">
          {(devolucionDe?.lineas ?? []).filter((l) => Number(l.cantidad_solicitada) > 0).map((l) => (
            <div key={l.id} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[2fr_1fr_1fr]">
              <div className="text-sm">
                <span className="font-medium">{l.producto_nombre ?? prodById[l.producto_id]?.nombre ?? "—"}</span>
                {l.presentacion && <span className="text-muted"> · {l.presentacion}</span>}
              </div>
              <div className="text-sm text-muted">Entregado: {fmtNumber(l.cantidad_solicitada)}</div>
              <Input
                type="number" min="0" max={l.cantidad_solicitada} step="0.01"
                placeholder="0"
                value={devolCant[l.id] ?? ""}
                onChange={(e) => setDevolCant((m) => ({ ...m, [l.id]: e.target.value }))}
              />
            </div>
          ))}
        </div>
        <div className="mt-4">
          <Field label="Motivo (opcional)">
            <Input value={devolMotivo} placeholder="Rechazo del cliente, producto dañado…"
              onChange={(e) => setDevolMotivo(e.target.value)} />
          </Field>
        </div>
      </Modal>

      {/* Sobregiro al guardar la edición de una CONFIRMADA */}
      <ConfirmDialog open={editSobregiro !== null} title="Existencia insuficiente"
        message="Los cambios requieren más existencia de la disponible. ¿Guardar de todas formas? El inventario quedará en negativo (sobregiro)."
        confirmLabel="Guardar con sobregiro" confirmVariant="danger"
        onConfirm={() => void guardarEdicionConfirmada(true)}
        onClose={() => setEditSobregiro(null)} loading={saving} />

      {/* Sobregiro al confirmar en lote: las que no tienen existencia */}
      <ConfirmDialog open={confirmarSobregiro !== null} title="Existencia insuficiente"
        message={`${confirmarSobregiro?.rems.length ?? 0} remisión(es) no tienen existencia suficiente. ¿Confirmarlas de todas formas? El inventario quedará en negativo (sobregiro).`}
        confirmLabel="Confirmar con sobregiro" confirmVariant="danger"
        onConfirm={aceptarConfirmarSobregiro} onClose={declinarConfirmarSobregiro} loading={bulkBusy} />

      {/* Confirmar en lote (borradores → reserva inventario) */}
      <ConfirmDialog open={confirmarBulkOpen} title="Confirmar remisiones"
        message={`¿Confirmar ${borradoresSel.length} remisión(es) en borrador? Se reservará el inventario de cada una. Las que no tengan existencia suficiente se reportarán como error.`}
        confirmLabel="Confirmar" confirmVariant="success"
        onConfirm={() => { void bulkConfirmar(); }} onClose={() => setConfirmarBulkOpen(false)} loading={bulkBusy} />

      {/* Cancelar en lote — doble verificación */}
      <ConfirmDialog open={cancelBulkStep === 1} title="Cancelar remisiones"
        message={`Se cancelarán ${cancelablesSel.length} remisión(es): ${cancelablesSel.map((r) => r.folio_interno).join(", ")}. Se liberará el inventario reservado de las confirmadas. Las ya facturadas se omiten. ¿Continuar?`}
        confirmLabel="Continuar" confirmVariant="danger"
        onConfirm={() => setCancelBulkStep(2)} onClose={() => setCancelBulkStep(0)} loading={bulkBusy} />
      <ConfirmDialog open={cancelBulkStep === 2} title="Confirmación final"
        message={`Esta acción no se puede deshacer. Se cancelarán de forma definitiva: ${cancelablesSel.map((r) => r.folio_interno).join(", ")}. ¿Confirmas la cancelación?`}
        confirmLabel="Sí, cancelar definitivamente" confirmVariant="danger"
        onConfirm={() => { void bulkCancelar(); }} onClose={() => setCancelBulkStep(0)} loading={bulkBusy} />

      <Modal
        open={toSend !== null}
        onClose={() => setToSend(null)}
        title={`Enviar remisión ${toSend?.folio_interno ?? ""}`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setToSend(null)}>Cancelar</Button>
            <Button onClick={confirmarEnvio} disabled={sending}>
              <Mail size={16} /> {sending ? "Enviando…" : "Enviar"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Destinatario(s)" required hint="Separa varios correos con coma o espacio">
            <Input
              placeholder="cliente@ejemplo.com"
              value={sendTo}
              onChange={(e) => setSendTo(e.target.value)}
            />
          </Field>
          <Field label="Mensaje (opcional)" hint="Se incluirá arriba del cuerpo del correo">
            <Textarea rows={3} value={sendMensaje} onChange={(e) => setSendMensaje(e.target.value)} />
          </Field>
          <p className="text-sm text-muted">
            Se adjunta el <strong>PDF</strong> de la remisión, usando la cuenta de correo configurada en Ajustes › Correo.
          </p>
        </div>
      </Modal>

      <Modal
        open={bulkSendOpen}
        onClose={() => setBulkSendOpen(false)}
        title={`Enviar ${selected.length} remisión(es) por correo`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setBulkSendOpen(false)} disabled={bulkSendBusy}>Cancelar</Button>
            <Button onClick={() => { void confirmarBulkEnvio(); }} disabled={bulkSendBusy}>
              <Mail size={16} /> {bulkSendBusy ? "Enviando…" : "Enviar"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-muted">
            Cada cliente recibe <strong>un solo correo</strong> con el <strong>PDF</strong> de todas
            sus remisiones adjunto. Edita o agrega correos (coma/espacio); deja el campo{" "}
            <strong>en blanco para ignorar</strong> ese cliente.
          </p>
          <div className="space-y-2">
            {bulkSendRows.map((row, i) => (
              <div key={row.clienteId} className="rounded-lg border border-border p-2.5">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{row.nombre}</span>
                  <span className="shrink-0 text-xs text-muted">{row.remIds.length} remisión(es)</span>
                </div>
                <Input
                  placeholder="correo@cliente.com  ·  vacío = ignorar"
                  value={row.correos}
                  onChange={(e) =>
                    setBulkSendRows((rows) => rows.map((r, j) => (j === i ? { ...r, correos: e.target.value } : r)))
                  }
                />
              </div>
            ))}
          </div>
          <Field label="Mensaje (opcional)" hint="Se incluirá arriba del cuerpo de todos los correos">
            <Textarea rows={2} value={bulkSendMensaje} onChange={(e) => setBulkSendMensaje(e.target.value)} />
          </Field>
        </div>
      </Modal>

      <Modal
        open={facturarOpen}
        onClose={() => { setFacturarOpen(false); setFacturarSolo(null); }}
        title={facturarSolo
          ? `Facturar remisión ${facturarSolo.folio_interno}`
          : `Facturar ${facturarElegibles.length} remisión(es)${selected.length !== facturarElegibles.length ? ` (de ${selected.length} seleccionadas)` : ""}`}
        footer={
          <Button variant="secondary" onClick={() => { setFacturarOpen(false); setFacturarSolo(null); }} disabled={bulkBusy}>Cerrar</Button>
        }
      >
        <p className="mb-2 text-sm text-muted">
          Se facturarán <strong>{facturarElegibles.length}</strong> remisión(es) (borrador o confirmadas sin
          factura vigente); las ya facturadas se omiten. Un borrador se confirma automáticamente (reserva
          inventario) al facturar, y cada factura <strong>se timbra de inmediato</strong> ante el PAC (si falla,
          queda en borrador para reintentar desde Facturas).
        </p>
        <div className="mb-3 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm">
          <span className="font-medium">A facturar:</span>{" "}
          {facturarElegibles.map((r) => r.folio_interno).join(", ")}
        </div>
        <p className="mb-3 text-sm text-muted">Elige cómo generar la(s) factura(s):</p>
        {multiCliente && (
          <div className="mb-3">
            <Alert tone="warning">
              Seleccionaste remisiones de <strong>{clientesElegibles.length} clientes distintos</strong>.
              No se puede emitir una sola factura con varios clientes; usa <strong>Separado</strong>
              {" "}(una factura por remisión).
            </Alert>
          </div>
        )}
        <div className="flex flex-col gap-2">
          {facturarDups ? (
            <>
              {/* Hay productos repetidos → sumar vs no sí cambia el resultado. */}
              <button
                type="button"
                disabled={bulkBusy || multiCliente}
                title={multiCliente ? "No disponible: la selección tiene varios clientes" : undefined}
                onClick={() => { void bulkFacturar("sumar"); }}
                className="rounded-lg border border-border p-3 text-left transition hover:border-accent hover:bg-accent/5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border disabled:hover:bg-transparent"
              >
                <div className="font-medium">Sumatoria de productos en una factura</div>
                <div className="text-sm text-muted">
                  Una factura. Las líneas del mismo producto se suman en un solo concepto.
                </div>
              </button>
              <button
                type="button"
                disabled={bulkBusy || multiCliente}
                title={multiCliente ? "No disponible: la selección tiene varios clientes" : undefined}
                onClick={() => { void bulkFacturar("sin_sumar"); }}
                className="rounded-lg border border-border p-3 text-left transition hover:border-accent hover:bg-accent/5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border disabled:hover:bg-transparent"
              >
                <div className="font-medium">Productos sin sumatoria en una factura</div>
                <div className="text-sm text-muted">
                  Una factura. Cada partida de cada remisión queda como una línea independiente.
                </div>
              </button>
            </>
          ) : (
            /* Sin productos repetidos → un solo botón (sumar y no sumar dan lo mismo). */
            <button
              type="button"
              disabled={bulkBusy || multiCliente}
              title={multiCliente ? "No disponible: la selección tiene varios clientes" : undefined}
              onClick={() => { void bulkFacturar("sin_sumar"); }}
              className="rounded-lg border border-border p-3 text-left transition hover:border-accent hover:bg-accent/5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border disabled:hover:bg-transparent"
            >
              <div className="font-medium">Facturar y timbrar{facturarElegibles.length > 1 ? " en una factura" : ""}</div>
              <div className="text-sm text-muted">
                {facturarElegibles.length > 1
                  ? "Una sola factura con todas las partidas."
                  : "Genera la factura y la timbra ante el PAC."}
              </div>
            </button>
          )}
          {/* "Separado" solo tiene sentido con 2+ remisiones (una factura por
              cada una); con una sola produce lo mismo que los otros modos. */}
          {facturarElegibles.length > 1 && (
            <button
              type="button"
              disabled={bulkBusy}
              onClick={() => { void bulkFacturar("separado"); }}
              className="rounded-lg border border-border p-3 text-left transition hover:border-accent hover:bg-accent/5 disabled:opacity-50"
            >
              <div className="font-medium">Separado</div>
              <div className="text-sm text-muted">Una factura por cada remisión.</div>
            </button>
          )}
        </div>
      </Modal>
    </div>
  );
}

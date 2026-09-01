"use client";

import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronRight, ChevronsUpDown, Columns3, Download, Eye, EyeOff, GripVertical, MoreVertical } from "lucide-react";

import { Alert } from "./Alert";
import { EmptyState } from "./EmptyState";
import { Checkbox, Select } from "./Field";
import { Spinner } from "./Spinner";

export type Column<T> = {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
  /** Identificador estable para orden/visibilidad/ancho. Por defecto usa `header`. */
  key?: string;
  /** Habilita ordenar por esta columna. Clic en el encabezado alterna:
   *  ascendente → descendente → sin orden. */
  sortable?: boolean;
  /** Valor a comparar al ordenar. Indícalo cuando `cell` no devuelve un
   *  texto/número simple (p. ej. un Badge o JSX). */
  sortValue?: (row: T) => string | number | null | undefined;
  /** Excluye la columna del menú de columnas y la deja fija (al final). Las
   *  columnas con encabezado vacío (acciones) se fijan automáticamente. */
  fixed?: boolean;
  /** Arranca oculta: no ocupa ancho, pero sigue estando en el menú «Columnas»
   *  para quien la necesite (y en la exportación a CSV). Úsalo con el detalle
   *  fiscal o los campos de consulta ocasional, no con datos que se leen a
   *  diario. Sólo aplica la primera vez: si la persona ya cambió las columnas
   *  de esta tabla, manda lo que ella eligió. */
  hiddenByDefault?: boolean;
  /** Columna «elástica»: se queda en UN renglón y corta con puntos suspensivos
   *  (…) en vez de partirse en varios. Es la que cede ancho cuando la tabla no
   *  cabe, así que márcala en el texto largo (cliente, descripción, nota) para
   *  que las filas no crezcan a lo alto. */
  truncate?: boolean;
  /** Valor a escribir al exportar a Excel/CSV. Si no se indica, usa `sortValue`
   *  o el `cell` cuando sea texto/número. Las columnas de acciones se omiten. */
  exportValue?: (row: T) => string | number | null | undefined;
};

type SortState = { id: string; dir: "asc" | "desc" };

/** Acción por fila que se muestra como un ícono en la columna de acciones. */
export type RowAction<T> = {
  /** Identidad estable (para recordar orden/visibilidad en el menú ⋮). */
  id: string;
  /** Ícono a mostrar. Puede depender de la fila. */
  icon: ReactNode | ((row: T) => ReactNode);
  /** Texto del tooltip y nombre en el menú ⋮. */
  label: string;
  /** Qué hacer al hacer clic en el ícono. */
  onClick: (row: T) => void;
  /** Color del ícono. */
  tone?: "default" | "danger" | "success";
  /** Oculta la acción en filas concretas (cuando no aplica a esa fila). */
  hidden?: (row: T) => boolean;
};

/** Menú ⋮ con las acciones que no caben como ícono suelto en la fila.
 *  Se dibuja en `position: fixed` porque el contenedor de la tabla tiene
 *  `overflow-x-auto` y recortaría un desplegable normal. */
function RowOverflowMenu<T>({ actions, row }: { actions: RowAction<T>[]; row: T }) {
  const [open, setOpen] = useState(false);
  // `abajo` = el menú cuelga hacia abajo del botón; si la fila está al final de
  // la pantalla se ancla al revés para no quedar cortado.
  const [pos, setPos] = useState<{ top?: number; bottom?: number; right: number }>({ top: 0, right: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    document.addEventListener("mousedown", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      document.removeEventListener("mousedown", close);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        title="Más acciones"
        aria-label="Más acciones"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          const r = btnRef.current?.getBoundingClientRect();
          if (r) {
            const alto = actions.length * 36 + 8; // alto aproximado del menú
            const derecha = Math.max(8, window.innerWidth - r.right);
            setPos(
              r.bottom + 4 + alto > window.innerHeight - 8
                ? { bottom: Math.max(8, window.innerHeight - r.top + 4), right: derecha }
                : { top: r.bottom + 4, right: derecha },
            );
          }
          setOpen((v) => !v);
        }}
        className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
      >
        <MoreVertical size={16} />
      </button>
      {open && (
        <div
          role="menu"
          style={{ top: pos.top, bottom: pos.bottom, right: pos.right }}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          className="fixed z-50 w-56 overflow-hidden rounded-xl border border-border bg-background py-1 text-left shadow-xl"
        >
          {actions.map((a) => {
            const icon = typeof a.icon === "function" ? a.icon(row) : a.icon;
            const toneCls =
              a.tone === "danger" ? "text-danger"
              : a.tone === "success" ? "text-success"
              : "text-foreground";
            return (
              <button
                key={a.id}
                type="button"
                role="menuitem"
                onClick={() => { setOpen(false); a.onClick(row); }}
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-surface-2 ${toneCls}`}
              >
                <span className="shrink-0 text-muted">{icon}</span>
                <span className="truncate">{a.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}

const MIN_W = 64; // ancho mínimo de columna al redimensionar (px)
const EXPAND_W = 40; // ancho de la columna del chevron (modo Excel)

function comparable<T>(col: Column<T>, row: T): string | number | null {
  const raw = col.sortValue ? col.sortValue(row) : col.cell(row);
  if (raw == null) return null;
  return typeof raw === "number" || typeof raw === "string" ? raw : null;
}

/** Valor textual para exportar: `exportValue` → `sortValue` → `cell` (si es
 *  texto/número). JSX sin un accessor explícito sale vacío. */
function exportText<T>(col: Column<T>, row: T): string {
  const raw = col.exportValue
    ? col.exportValue(row)
    : col.sortValue
      ? col.sortValue(row)
      : col.cell(row);
  if (raw == null) return "";
  return typeof raw === "number" || typeof raw === "string" ? String(raw) : "";
}

/** Escapa un campo CSV (comillas dobles, comas, saltos de línea). */
function csvCell(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Genera el CSV (con BOM UTF-8 para que Excel respete acentos) y dispara la
 *  descarga. Excel abre el .csv como hoja de cálculo con un doble clic. */
function downloadCsv(headers: string[], matrix: string[][], filename: string) {
  const lines = [headers, ...matrix].map((r) => r.map(csvCell).join(","));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Normaliza para búsqueda: minúsculas + sin acentos. */
function norm(s: string): string {
  return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export type DataTableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  loading?: boolean;
  error?: string | null;
  empty?: string;
  onRowClick?: (row: T) => void;
  /** Si se indica, cada fila es expandible: al hacer clic se despliega un panel
   *  (slide-down) debajo con este contenido. Sustituye a `onRowClick`. */
  renderExpanded?: (row: T) => ReactNode;
  /** Clave estable por fila para recordar cuáles están expandidas (necesaria al
   *  ordenar/paginar/buscar). Por defecto usa el índice. */
  rowKey?: (row: T, index: number) => string | number;
  /** Se llama al expandir una fila (útil para cargar el detalle bajo demanda). */
  onRowExpand?: (row: T) => void;
  /** Clave (rowKey) de la fila a expandir automáticamente al montar — para
   *  deep-links (p. ej. llegar a Facturas con una factura ya abierta). Se aplica
   *  una sola vez, cuando esa fila ya está en `rows`. */
  initialExpandedKey?: string | number;
  /** Columna de acciones por fila: una lista de íconos (Ver/Editar/Eliminar…).
   *  Se renderiza fija al final. */
  actions?: RowAction<T>[];
  /** Muestra el menú ⋮ (puntos) en la cabecera de acciones para reordenar y
   *  mostrar/ocultar los íconos. Se recuerda con `storageKey`. */
  actionsMenu?: boolean;
  /** Cuántas acciones se muestran como ícono suelto en cada fila. El resto se
   *  agrupa en un menú ⋮ al final de la fila. Con 8 acciones sueltas la columna
   *  medía 217 px y empujaba la mitad de la tabla fuera de la pantalla; con 2
   *  mide ~90 px. Ponlo en `Infinity` para volver a verlas todas. */
  maxInlineActions?: number;
  /** Deja la columna de acciones pegada al borde derecho: aunque la tabla
   *  necesite scroll horizontal, los íconos nunca se salen de la vista. */
  stickyActions?: boolean;
  /** Muestra el botón "Columnas" para mostrar/ocultar y reordenar columnas. */
  columnsMenu?: boolean;
  /** Permite cambiar el ancho de las columnas arrastrando el borde derecho. */
  resizable?: boolean;
  /** Muestra el botón "Excel" para descargar la tabla (respeta columnas
   *  visibles, su orden y el ordenamiento actual). */
  exportable?: boolean;
  /** Nombre base del archivo descargado (sin extensión). */
  exportFilename?: string;
  /** Si se indica, recuerda orden/visibilidad/ancho en localStorage bajo esta clave. */
  storageKey?: string;
  /** Muestra un buscador que filtra (cliente) sobre TODAS las columnas:
   *  normalizado (sin acentos/mayúsculas) y por tokens (cada palabra debe aparecer). */
  searchable?: boolean;
  searchPlaceholder?: string;
  /** Pagina del lado del cliente (sobre lo filtrado) con selector de filas/página. */
  paginated?: boolean;
  pageSizeOptions?: number[];
  defaultPageSize?: number;
  /** Activa una columna de casillas (checkbox) a la izquierda para seleccionar
   *  filas. La selección persiste entre orden/búsqueda/paginación. */
  selectable?: boolean;
  /** Se llama con los OBJETOS de fila seleccionados cuando cambia la selección. */
  onSelectionChange?: (rows: T[]) => void;
  /** Al cambiar este valor, el componente limpia su selección interna (útil para
   *  que el padre la resetee tras una acción en lote). */
  selectionResetKey?: number | string;
  /** Claves marcadas al montar (y cada vez que cambia `selectionResetKey`).
   *  Útil cuando la casilla significa "incluido" y lo normal es que todo lo
   *  esté: sin esto, la tabla arrancaría con todo desmarcado. */
  initialSelectedKeys?: (string | number)[];
  /** Filtro externo que decide qué filas se VEN (se combina con el buscador).
   *  Es solo de presentación: `rows` no cambia, así que la selección de las
   *  filas ocultas se conserva intacta — filtrar no des-selecciona nada. */
  rowFilter?: (row: T) => boolean;
  /** Identidad del filtro externo. Al cambiar, se vuelve a la primera página.
   *  Va aparte porque `rowFilter` suele ser una arrow inline (referencia nueva
   *  en cada render) y no sirve como dependencia. */
  rowFilterKey?: string | number;
  /** Clases extra por fila (p. ej. resaltar en rojo las que faltan por completar). */
  rowClassName?: (row: T) => string | undefined;
};

export function DataTable<T>({
  columns,
  rows,
  loading,
  error,
  empty,
  onRowClick,
  renderExpanded,
  rowKey,
  onRowExpand,
  initialExpandedKey,
  actions,
  actionsMenu,
  maxInlineActions = 2,
  stickyActions = true,
  columnsMenu,
  resizable,
  exportable,
  exportFilename = "tabla",
  storageKey,
  searchable,
  searchPlaceholder,
  paginated,
  pageSizeOptions = [10, 25, 50, 100],
  defaultPageSize = 25,
  selectable,
  onSelectionChange,
  selectionResetKey,
  initialSelectedKeys,
  rowFilter,
  rowFilterKey,
  rowClassName,
}: DataTableProps<T>) {
  // ── identidad estable de cada columna ──
  const cols = useMemo(() => {
    const seen = new Map<string, number>();
    return columns.map((col, i) => {
      let base = col.key ?? col.header ?? "";
      if (base.trim() === "") base = `col-${i}`;
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      const id = n === 0 ? base : `${base}-${n}`;
      const manageable = !col.fixed && col.header.trim() !== "";
      return { col, id, manageable };
    });
  }, [columns]);
  const byId = useMemo(() => Object.fromEntries(cols.map((c) => [c.id, c])), [cols]);
  const managedIds = useMemo(() => cols.filter((c) => c.manageable).map((c) => c.id), [cols]);

  // ── filas expandibles (slide-down) ──
  const expandable = !!renderExpanded;
  const [expanded, setExpanded] = useState<Set<string | number>>(new Set());
  function toggleExpand(key: string | number, row: T) {
    const willExpand = !expanded.has(key);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
    // side effect fuera del updater: llamarlo dentro provoca un setState del
    // padre durante el render de DataTable (warning "Cannot update a component
    // while rendering a different component").
    if (willExpand) onRowExpand?.(row);
  }

  // ── selección de filas (opt-in con `selectable`) ──
  // Se guarda el `rowKey` de cada fila seleccionada (persiste entre orden,
  // búsqueda y paginación, que solo cambian qué filas se muestran).
  const keyOf = (row: T, index: number): string | number => (rowKey ? rowKey(row, index) : index);

  // Deep-link: expande automáticamente la fila `initialExpandedKey` una sola vez,
  // cuando ya llegó a `rows`. No re-expande si el usuario la cierra ni al recargar.
  const appliedInitialExpand = useRef(false);
  useEffect(() => {
    if (appliedInitialExpand.current || initialExpandedKey == null) return;
    const idx = rows.findIndex((r, i) => keyOf(r, i) === initialExpandedKey);
    if (idx < 0) return; // aún no cargan las filas
    appliedInitialExpand.current = true;
    setExpanded((prev) => new Set(prev).add(initialExpandedKey));
    onRowExpand?.(rows[idx]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialExpandedKey, rows]);

  const [selectedKeys, setSelectedKeys] = useState<Set<string | number>>(
    () => new Set(initialSelectedKeys ?? []),
  );
  // Vuelve al estado inicial cuando el padre cambia `selectionResetKey`
  // (sin `initialSelectedKeys`, eso es "sin nada marcado").
  const initialKeysRef = useRef(initialSelectedKeys);
  initialKeysRef.current = initialSelectedKeys;
  const primerReset = useRef(true);
  useEffect(() => {
    if (primerReset.current) {
      primerReset.current = false;   // el useState ya sembró el inicial
      return;
    }
    setSelectedKeys(new Set(initialKeysRef.current ?? []));
  }, [selectionResetKey]);

  // ── columna de acciones (íconos por fila + menú ⋮ para reordenar/ocultar) ──
  const hasActions = !!actions && actions.length > 0;
  const actionById = useMemo(() => Object.fromEntries((actions ?? []).map((a) => [a.id, a])), [actions]);
  const actionIds = useMemo(() => (actions ?? []).map((a) => a.id), [actions]);
  const [actionOrder, setActionOrder] = useState<string[]>([]);
  const [actionHidden, setActionHidden] = useState<string[]>([]);
  const [actionsMenuOpen, setActionsMenuOpen] = useState(false);
  const [actionDragId, setActionDragId] = useState<string | null>(null);
  const actionsMenuRef = useRef<HTMLDivElement>(null);
  // orden efectivo de los íconos (reconcilia con las acciones actuales)
  const effectiveActionOrder = useMemo(() => {
    const fromState = actionOrder.filter((id) => actionIds.includes(id));
    const missing = actionIds.filter((id) => !fromState.includes(id));
    return [...fromState, ...missing];
  }, [actionOrder, actionIds]);
  const visibleActions = useMemo(
    () => effectiveActionOrder.filter((id) => !actionHidden.includes(id)).map((id) => actionById[id]).filter(Boolean),
    [effectiveActionOrder, actionHidden, actionById],
  );
  function dropActionOn(targetId: string) {
    setActionOrder(() => {
      const base = effectiveActionOrder.slice();
      if (!actionDragId || actionDragId === targetId) return base;
      const from = base.indexOf(actionDragId);
      if (from < 0) return base;
      base.splice(from, 1);
      base.splice(base.indexOf(targetId), 0, actionDragId);
      return base;
    });
    setActionDragId(null);
  }
  function toggleActionHidden(id: string) {
    setActionHidden((h) => (h.includes(id) ? h.filter((x) => x !== id) : [...h, id]));
  }

  // ── estado de orden de filas (por id de columna) ──
  const [sort, setSort] = useState<SortState | null>(null);
  const [search, setSearch] = useState("");
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [pageIndex, setPageIndex] = useState(0);

  // ── estado del menú de columnas ──
  const [order, setOrder] = useState<string[]>([]); // orden explícito de columnas manejables
  const [hidden, setHidden] = useState<string[]>(() =>
    columns.filter((c) => c.hiddenByDefault).map((c) => c.key ?? c.header),
  );
  const [widths, setWidths] = useState<Record<string, number>>({}); // ancho px por id
  const [menuOpen, setMenuOpen] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef(false);

  // persistencia
  useEffect(() => {
    if (!storageKey) { loadedRef.current = true; return; }
    try {
      const raw = localStorage.getItem(`dt:${storageKey}`);
      if (raw) {
        const p = JSON.parse(raw);
        if (Array.isArray(p.order)) setOrder(p.order);
        if (Array.isArray(p.hidden)) setHidden(p.hidden);
        if (p.widths && typeof p.widths === "object") setWidths(p.widths);
        if (Array.isArray(p.actionOrder)) setActionOrder(p.actionOrder);
        if (Array.isArray(p.actionHidden)) setActionHidden(p.actionHidden);
      }
    } catch { /* ignora storage corrupto */ }
    loadedRef.current = true;
  }, [storageKey]);
  useEffect(() => {
    if (!storageKey || !loadedRef.current) return;
    try { localStorage.setItem(`dt:${storageKey}`, JSON.stringify({ order, hidden, widths, actionOrder, actionHidden })); } catch { /* noop */ }
  }, [order, hidden, widths, actionOrder, actionHidden, storageKey]);

  // cerrar los menús (columnas / acciones) al hacer clic fuera o con Escape
  useEffect(() => {
    if (!menuOpen && !actionsMenuOpen) return;
    function onDown(e: MouseEvent) {
      if (menuOpen && menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
      if (actionsMenuOpen && actionsMenuRef.current && !actionsMenuRef.current.contains(e.target as Node)) setActionsMenuOpen(false);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") { setMenuOpen(false); setActionsMenuOpen(false); } }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menuOpen, actionsMenuOpen]);

  // orden efectivo de columnas manejables (reconcilia con columnas actuales)
  const effectiveOrder = useMemo(() => {
    const fromState = order.filter((id) => managedIds.includes(id));
    const missing = managedIds.filter((id) => !fromState.includes(id));
    return [...fromState, ...missing];
  }, [order, managedIds]);

  // columnas a renderizar: manejables (en orden, visibles) + fijas al final
  const renderCols = useMemo(() => {
    if (!columnsMenu) return cols;
    const visible = effectiveOrder.filter((id) => !hidden.includes(id)).map((id) => byId[id]);
    const fixed = cols.filter((c) => !c.manageable);
    return [...visible, ...fixed];
  }, [columnsMenu, cols, effectiveOrder, hidden, byId]);

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const entry = byId[sort.id];
    if (!entry?.col.sortable) return rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = comparable(entry.col, a);
      const vb = comparable(entry.col, b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * factor;
      return String(va).localeCompare(String(vb), "es", { numeric: true, sensitivity: "base" }) * factor;
    });
  }, [rows, byId, sort]);

  // ── filtro externo + búsqueda (cliente, todas las columnas, normalizada) ──
  // El filtro externo va primero: acota el universo y el buscador afina dentro.
  const rowFilterRef = useRef(rowFilter);
  rowFilterRef.current = rowFilter;
  const filteredRows = useMemo(() => {
    const fn = rowFilterRef.current;
    const base = fn ? sortedRows.filter((row) => fn(row)) : sortedRows;
    const q = norm(search.trim());
    if (!q) return base;
    const tokens = q.split(/\s+/).filter(Boolean);
    return base.filter((row) => {
      const text = norm(cols.map(({ col }) => exportText(col, row)).join("  "));
      return tokens.every((t) => text.includes(t));
    });
    // `rowFilter` entra por ref + `rowFilterKey`: como arrow inline cambiaría
    // de identidad en cada render y recalcularía este memo siempre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortedRows, search, cols, rowFilterKey]);

  // ── selección: derivados + notificación al padre ──
  // Objetos seleccionados: todas las filas (de `rows`) cuya clave esté marcada.
  const selectedRows = useMemo(
    () => rows.filter((row, i) => selectedKeys.has(keyOf(row, i))),
    // keyOf depende de rowKey, pero los callers casi siempre pasan un arrow
    // inline (nueva referencia en cada render) — incluirlo aquí recalcula
    // este memo en cada render y, junto con `onSelectionChange`, entra en un
    // loop infinito de render (setState en el padre → nuevo `rows`/`rowKey` →
    // este memo cambia → vuelve a notificar). El valor de `keyOf` es estable
    // en la práctica (misma función de extracción de clave), así que no hace
    // falta como dependencia.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, selectedKeys],
  );
  // Notifica al padre cuando cambia la selección (objetos de fila). Compara el
  // CONTENIDO (las claves seleccionadas presentes en `rows`) antes de llamar:
  // `selectedRows` cambia de identidad con cada identidad nueva de `rows`, y un
  // caller que pase `rows={data?.items ?? []}` inline + `selectable` entraría
  // en loop infinito (notificar → setState del padre → re-render → nuevo
  // `rows` → volver a notificar…).
  const onSelectionChangeRef = useRef(onSelectionChange);
  onSelectionChangeRef.current = onSelectionChange;
  const lastSelectionSigRef = useRef("[]");
  useEffect(() => {
    const sig = JSON.stringify(rows.map((row, i) => keyOf(row, i)).filter((k) => selectedKeys.has(k)));
    if (sig === lastSelectionSigRef.current) return;
    lastSelectionSigRef.current = sig;
    onSelectionChangeRef.current?.(selectedRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ver nota de `selectedRows` sobre `rowKey`
  }, [selectedRows]);

  // Casilla de cabecera: marca/indeterminada según las filas FILTRADAS.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- ver nota de `selectedRows` sobre `rowKey`
  const filteredKeys = useMemo(() => filteredRows.map((row, i) => keyOf(row, i)), [filteredRows]);
  const selectedFilteredCount = useMemo(
    () => filteredKeys.reduce<number>((n, k) => (selectedKeys.has(k) ? n + 1 : n), 0),
    [filteredKeys, selectedKeys],
  );
  const allFilteredSelected = filteredKeys.length > 0 && selectedFilteredCount === filteredKeys.length;
  const someFilteredSelected = selectedFilteredCount > 0 && !allFilteredSelected;
  const headerCheckRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (headerCheckRef.current) headerCheckRef.current.indeterminate = someFilteredSelected;
  }, [someFilteredSelected]);

  function toggleRowSelected(key: string | number) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  function toggleSelectAll() {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        // deselecciona solo las filtradas (respeta selección fuera del filtro)
        filteredKeys.forEach((k) => next.delete(k));
      } else {
        filteredKeys.forEach((k) => next.add(k));
      }
      return next;
    });
  }

  // ── paginación (cliente, sobre lo filtrado) ──
  const pageCount = paginated ? Math.max(1, Math.ceil(filteredRows.length / pageSize)) : 1;
  const safePage = Math.min(pageIndex, pageCount - 1);
  const pagedRows = paginated
    ? filteredRows.slice(safePage * pageSize, safePage * pageSize + pageSize)
    : filteredRows;
  // volver a la primera página cuando cambia la búsqueda, el filtro externo o
  // el tamaño de página
  useEffect(() => {
    setPageIndex(0);
  }, [search, pageSize, rowFilterKey]);

  function toggleSort(id: string) {
    setSort((s) => {
      if (!s || s.id !== id) return { id, dir: "asc" };
      if (s.dir === "asc") return { id, dir: "desc" };
      return null;
    });
  }
  // Cuántas columnas manejables quedarían visibles si ocultamos `id`.
  const visibleManagedCount = managedIds.filter((mid) => !hidden.includes(mid)).length;
  function toggleHidden(id: string) {
    setHidden((h) => {
      if (h.includes(id)) return h.filter((x) => x !== id); // mostrar: siempre permitido
      // Ocultar: estándar de data-grids — nunca dejar la tabla sin columnas.
      if (visibleManagedCount <= 1) return h;
      return [...h, id];
    });
  }
  function dropOn(targetId: string) {
    setOrder(() => {
      const base = effectiveOrder.slice();
      if (!dragId || dragId === targetId) return base;
      const from = base.indexOf(dragId);
      if (from < 0) return base;
      base.splice(from, 1);
      base.splice(base.indexOf(targetId), 0, dragId);
      return base;
    });
    setDragId(null);
  }

  // ── redimensionar columnas (modelo Excel) ──
  // Al arrastrar el borde de una columna, SOLO cambia esa columna: la tabla
  // crece/encoge a lo ancho y aparece scroll horizontal. Las demás conservan su
  // ancho (no se comprimen). Para lograrlo, al iniciar el primer resize se
  // "congela" el ancho actual de TODAS las columnas; a partir de ahí cada una
  // tiene un ancho explícito e independiente.
  const theadRef = useRef<HTMLTableSectionElement>(null);
  function startResize(e: React.MouseEvent, id: string) {
    e.preventDefault();
    e.stopPropagation();
    const ths = Array.from(theadRef.current?.querySelectorAll("th") ?? []) as HTMLElement[];
    // columnas que preceden a las de datos: casilla de selección y/o chevron
    const lead = (selectable ? 1 : 0) + (expandable ? 1 : 0);
    const base: Record<string, number> = { ...widths };
    renderCols.forEach(({ id: cid }, i) => {
      if (base[cid] == null) {
        base[cid] = Math.round(ths[i + lead]?.getBoundingClientRect().width ?? MIN_W);
      }
    });
    const startX = e.clientX;
    const startW = base[id] ?? MIN_W;
    setWidths(base); // congela el layout actual (todas las columnas con ancho fijo)

    function onMove(ev: MouseEvent) {
      // Sin tope superior: igual que Excel, la tabla se ensancha y hace scroll.
      const next = Math.max(MIN_W, startW + (ev.clientX - startX));
      setWidths((w) => ({ ...w, [id]: Math.round(next) }));
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  // ── exportar a Excel (CSV) ──
  function doExport() {
    const expCols = renderCols.filter(({ col }) => col.header.trim() !== ""); // sin columna de acciones
    const headers = expCols.map(({ col }) => col.header);
    const matrix = filteredRows.map((row) => expCols.map(({ col }) => exportText(col, row)));
    downloadCsv(headers, matrix, exportFilename);
  }

  const customized = order.length > 0 || hidden.length > 0 || Object.keys(widths).length > 0 || actionOrder.length > 0 || actionHidden.length > 0;
  const hasToolbar = searchable || columnsMenu || exportable;

  const toolbar = hasToolbar ? (
    <div className="mb-2 flex items-center justify-between gap-2">
      <div className="flex-1">
        {searchable && (
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={searchPlaceholder ?? "Buscar en la tabla…"}
            className="w-full max-w-xs rounded-lg border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-accent"
          />
        )}
      </div>
      <div className="flex shrink-0 gap-2">
      {exportable && (
        <button
          type="button"
          onClick={doExport}
          disabled={rows.length === 0}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-50"
          title="Descargar la tabla en Excel"
        >
          <Download size={15} /> Excel
        </button>
      )}
      {columnsMenu && (
        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-sm hover:bg-surface-2"
          >
            <Columns3 size={15} /> Columnas
          </button>
          {menuOpen && (
            <div className="absolute right-0 z-30 mt-1 w-64 overflow-hidden rounded-xl border border-border bg-background shadow-xl">
              <div className="border-b border-border px-3 py-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted">Columnas</div>
                <p className="mt-0.5 text-xs text-muted">Arrastra ⠿ para reordenar. 👁 muestra/oculta (siempre queda al menos una).</p>
              </div>
              <div className="max-h-72 overflow-auto py-1">
                {effectiveOrder.map((id) => {
                  const entry = byId[id];
                  if (!entry) return null;
                  const isHidden = hidden.includes(id);
                  // No se puede ocultar la última columna visible (siempre debe
                  // quedar al menos una).
                  const lockHide = !isHidden && visibleManagedCount <= 1;
                  return (
                    <div
                      key={id}
                      draggable
                      onDragStart={() => setDragId(id)}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => dropOn(id)}
                      onDragEnd={() => setDragId(null)}
                      className={`flex items-center gap-2 px-2.5 py-1.5 text-sm ${dragId === id ? "opacity-40" : ""}`}
                    >
                      <GripVertical size={15} className="shrink-0 cursor-grab text-muted" />
                      <span className={`flex-1 truncate ${isHidden ? "text-muted line-through" : ""}`}>{entry.col.header}</span>
                      <button
                        type="button"
                        onClick={() => toggleHidden(id)}
                        disabled={lockHide}
                        aria-label={isHidden ? "Mostrar" : "Ocultar"}
                        title={lockHide ? "Debe quedar al menos una columna visible" : isHidden ? "Mostrar" : "Ocultar"}
                        className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
                      >
                        {isHidden ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  );
                })}
              </div>
              {customized && (
                <div className="border-t border-border px-2.5 py-1.5">
                  <button type="button" onClick={() => { setOrder([]); setHidden([]); setWidths({}); setActionOrder([]); setActionHidden([]); }} className="text-xs text-muted hover:text-foreground">
                    Restablecer
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  ) : null;

  const hasWidths = resizable && Object.keys(widths).length > 0;
  // Total de columnas reales + extras (casilla, chevron y acciones), para los colSpan.
  const totalCols = renderCols.length + (selectable ? 1 : 0) + (expandable ? 1 : 0) + (hasActions ? 1 : 0);
  // Ancho de la columna de acciones: los íconos sueltos (tope `maxInlineActions`)
  // + el ⋮ de desborde si sobra alguna + el ⋮ de configuración del encabezado.
  // Es fija (no se redimensiona).
  const inlineCount = Math.min(maxInlineActions, visibleActions.length);
  const anyOverflow = visibleActions.length > inlineCount;
  const actionsWidth =
    (actionsMenu ? 34 : 0) + Math.max(inlineCount, 1) * 32 + (anyOverflow ? 32 : 0) + 16;
  // Clases de la columna pegada al borde derecho. El fondo tiene que ser opaco
  // (si no, las columnas de abajo se transparentan al hacer scroll) y seguir el
  // hover de la fila, por eso el `<tr>` lleva `group`.
  const stickyOn = stickyActions && hasActions;
  const stickyHeadCls = stickyOn
    ? "sticky right-0 z-20 bg-surface-2 shadow-[-8px_0_8px_-6px_rgb(0_0_0/0.12)]"
    : "";
  const stickyCellCls = stickyOn
    ? "sticky right-0 z-10 shadow-[-8px_0_8px_-6px_rgb(0_0_0/0.12)]"
    : "";
  // Ancho total de la tabla en modo Excel = suma de las columnas (las que aún no
  // tienen ancho explícito cuentan con el mínimo) + las columnas fijas (chevron y
  // acciones). La tabla se ensancha y el contenedor hace scroll; agrandar una
  // columna NO comprime a las demás.
  const totalWidth = hasWidths
    ? renderCols.reduce((sum, { id }) => sum + (widths[id] ?? MIN_W), 0) +
      (selectable ? EXPAND_W : 0) +
      (expandable ? EXPAND_W : 0) +
      (hasActions ? actionsWidth : 0)
    : undefined;

  let body: ReactNode;
  if (loading) {
    body = <div className="flex justify-center py-16"><Spinner /></div>;
  } else if (error) {
    body = <Alert tone="danger">{error}</Alert>;
  } else if (rows.length === 0) {
    body = <EmptyState title={empty ?? "Sin resultados"} />;
  } else {
    body = (
      <div className="overflow-x-auto rounded-xl border border-border">
        {/* Con anchos definidos (modo Excel): table-fixed + ancho explícito = la
            tabla se ensancha y el contenedor hace scroll, sin comprimir columnas.
            Sin anchos: w-full normal (la tabla se ajusta al contenedor). */}
        <table
          className={`text-sm ${hasWidths ? "table-fixed" : "w-full"}`}
          style={hasWidths ? { width: totalWidth, minWidth: "100%" } : undefined}
        >
          {hasWidths && (
            <colgroup>
              {selectable && <col style={{ width: EXPAND_W }} />}
              {expandable && <col style={{ width: EXPAND_W }} />}
              {renderCols.map(({ id }) => (
                <col key={id} style={{ width: widths[id] ?? MIN_W }} />
              ))}
              {hasActions && <col style={{ width: actionsWidth }} />}
            </colgroup>
          )}
          <thead ref={theadRef} className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              {selectable && (
                <th className="w-10 px-3 py-2.5">
                  <Checkbox
                    ref={headerCheckRef}
                    checked={allFilteredSelected}
                    onChange={toggleSelectAll}
                    disabled={filteredKeys.length === 0}
                    aria-label="Seleccionar todo"
                  />
                </th>
              )}
              {expandable && <th className="w-8 px-2 py-2.5" aria-hidden />}
              {renderCols.map(({ col, id }, ci) => {
                const active = sort?.id === id;
                const Icon = active ? (sort!.dir === "asc" ? ArrowUp : ArrowDown) : ChevronsUpDown;
                // En modo Excel (con anchos) todas las columnas se pueden
                // redimensionar, incluida la última (la tabla hace scroll). Sin
                // anchos aún, no tiene sentido en la última (comprimiría).
                const canResize = resizable && (hasWidths || hasActions || ci < renderCols.length - 1);
                return (
                  <th
                    key={id}
                    className={`relative px-3 py-2.5 font-medium ${col.className ?? ""}`}
                    aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {col.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(id)}
                        className={`-ml-1 inline-flex max-w-full items-center gap-1 truncate rounded px-1 py-0.5 transition hover:text-foreground ${active ? "text-foreground" : ""}`}
                        title="Ordenar"
                      >
                        <span className="truncate">{col.header}</span>
                        <Icon size={13} className={active ? "shrink-0" : "shrink-0 opacity-40"} />
                      </button>
                    ) : (
                      <span className="block truncate">{col.header}</span>
                    )}
                    {canResize && (
                      <span
                        onMouseDown={(e) => startResize(e, id)}
                        onClick={(e) => e.stopPropagation()}
                        role="separator"
                        aria-orientation="vertical"
                        title="Arrastra para cambiar el ancho"
                        className="absolute right-0 top-0 z-10 flex h-full w-2 cursor-col-resize touch-none items-center justify-center hover:bg-accent/20"
                      >
                        <span className="h-1/2 w-px bg-border" />
                      </span>
                    )}
                  </th>
                );
              })}
              {hasActions && (
                <th className={`px-2 py-2.5 text-right font-medium ${stickyHeadCls}`}>
                  <span className="inline-flex items-center justify-end gap-1.5">
                    <span>Opciones</span>
                  {actionsMenu ? (
                    <div className="relative inline-block" ref={actionsMenuRef}>
                      <button
                        type="button"
                        onClick={() => setActionsMenuOpen((o) => !o)}
                        aria-label="Configurar acciones"
                        title="Reordenar / mostrar íconos de acciones"
                        className="rounded-md p-1 text-muted hover:bg-background hover:text-foreground"
                      >
                        <MoreVertical size={15} />
                      </button>
                      {actionsMenuOpen && (
                        <div className="absolute right-0 z-30 mt-1 w-60 overflow-hidden rounded-xl border border-border bg-background text-left normal-case shadow-xl">
                          <div className="border-b border-border px-3 py-2">
                            <div className="text-xs font-semibold uppercase tracking-wide text-muted">Acciones</div>
                            <p className="mt-0.5 text-xs text-muted normal-case">Arrastra ⠿ para reordenar. 👁 muestra/oculta el ícono.</p>
                          </div>
                          <div className="max-h-72 overflow-auto py-1">
                            {effectiveActionOrder.map((id) => {
                              const a = actionById[id];
                              if (!a) return null;
                              const isHidden = actionHidden.includes(id);
                              const previewIcon = typeof a.icon === "function" ? null : a.icon;
                              return (
                                <div
                                  key={id}
                                  draggable
                                  onDragStart={() => setActionDragId(id)}
                                  onDragOver={(e) => e.preventDefault()}
                                  onDrop={() => dropActionOn(id)}
                                  onDragEnd={() => setActionDragId(null)}
                                  className={`flex items-center gap-2 px-2.5 py-1.5 text-sm ${actionDragId === id ? "opacity-40" : ""}`}
                                >
                                  <GripVertical size={15} className="shrink-0 cursor-grab text-muted" />
                                  {previewIcon && <span className="shrink-0 text-muted">{previewIcon}</span>}
                                  <span className={`flex-1 truncate ${isHidden ? "text-muted line-through" : ""}`}>{a.label}</span>
                                  <button
                                    type="button"
                                    onClick={() => toggleActionHidden(id)}
                                    aria-label={isHidden ? "Mostrar" : "Ocultar"}
                                    title={isHidden ? "Mostrar" : "Ocultar"}
                                    className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-foreground"
                                  >
                                    {isHidden ? <EyeOff size={15} /> : <Eye size={15} />}
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : null}
                  </span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={totalCols} className="px-4 py-8 text-center text-sm text-muted">
                  Sin coincidencias{search.trim() ? ` para “${search.trim()}”` : ""}
                </td>
              </tr>
            ) : (
              pagedRows.map((row, ri) => {
                const key = rowKey ? rowKey(row, ri) : ri;
                const isOpen = expandable && expanded.has(key);
                const clickable = expandable || !!onRowClick;
                return (
                  <Fragment key={key}>
                    <tr
                      onClick={() => (expandable ? toggleExpand(key, row) : onRowClick?.(row))}
                      className={`group border-t border-border ${clickable ? "cursor-pointer hover:bg-surface-2" : ""} ${isOpen ? "bg-surface-2" : ""} ${rowClassName?.(row) ?? ""}`}
                    >
                      {selectable && (
                        <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                          <Checkbox
                            checked={selectedKeys.has(key)}
                            onChange={() => toggleRowSelected(key)}
                            aria-label="Seleccionar fila"
                          />
                        </td>
                      )}
                      {expandable && (
                        <td className="px-2 py-2.5 text-muted">
                          <ChevronRight size={16} className={`transition-transform ${isOpen ? "rotate-90" : ""}`} />
                        </td>
                      )}
                      {renderCols.map(({ col, id }) => (
                        <td
                          key={id}
                          className={`px-3 py-2.5 ${hasWidths || col.truncate ? "truncate" : ""} ${col.truncate && !hasWidths ? "max-w-0" : ""} ${col.className ?? ""}`}
                        >
                          {col.cell(row)}
                        </td>
                      ))}
                      {hasActions && (() => {
                        // Las que aplican a ESTA fila: las primeras van sueltas
                        // como ícono, las demás al menú ⋮.
                        const aplican = visibleActions.filter((a) => !a.hidden?.(row));
                        const sueltas = aplican.slice(0, maxInlineActions);
                        const enMenu = aplican.slice(maxInlineActions);
                        return (
                          <td
                            className={`px-2 py-2.5 text-right ${stickyCellCls} ${isOpen ? "bg-surface-2" : "bg-surface group-hover:bg-surface-2"}`}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <div className="flex items-center justify-end gap-0.5">
                              {sueltas.map((a) => {
                                const icon = typeof a.icon === "function" ? a.icon(row) : a.icon;
                                const toneCls =
                                  a.tone === "danger" ? "text-danger hover:bg-surface-2"
                                  : a.tone === "success" ? "text-success hover:bg-surface-2"
                                  : "text-muted hover:bg-surface-2 hover:text-foreground";
                                return (
                                  <button
                                    key={a.id}
                                    type="button"
                                    title={a.label}
                                    aria-label={a.label}
                                    onClick={(e) => { e.stopPropagation(); a.onClick(row); }}
                                    className={`rounded-md p-1.5 ${toneCls}`}
                                  >
                                    {icon}
                                  </button>
                                );
                              })}
                              {enMenu.length > 0 && <RowOverflowMenu actions={enMenu} row={row} />}
                            </div>
                          </td>
                        );
                      })()}
                    </tr>
                    {isOpen && (
                      <tr className="border-t border-border bg-surface-2/40">
                        <td colSpan={totalCols} className="px-4 pb-4">
                          <ExpandedPanel>{renderExpanded!(row)}</ExpandedPanel>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    );
  }

  const from = filteredRows.length === 0 ? 0 : safePage * pageSize + 1;
  const to = Math.min((safePage + 1) * pageSize, filteredRows.length);
  const footer =
    paginated && !loading && !error && rows.length > 0 ? (
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
        <div className="flex items-center gap-2">
          <span>Filas por página</span>
          <div className="w-20">
            <Select
              value={String(pageSize)}
              onChange={(e) => setPageSize(Number(e.target.value))}
              aria-label="Filas por página"
            >
              {pageSizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="tabular-nums">
            {from}–{to} de {filteredRows.length}
          </span>
          <div className="flex gap-1">
            <button
              type="button"
              disabled={safePage <= 0}
              onClick={() => setPageIndex(safePage - 1)}
              className="rounded-lg border border-border bg-background px-2.5 py-1 hover:bg-surface-2 disabled:opacity-40"
            >
              Anterior
            </button>
            <button
              type="button"
              disabled={safePage >= pageCount - 1}
              onClick={() => setPageIndex(safePage + 1)}
              className="rounded-lg border border-border bg-background px-2.5 py-1 hover:bg-surface-2 disabled:opacity-40"
            >
              Siguiente
            </button>
          </div>
        </div>
      </div>
    ) : null;

  return (
    <div>
      {toolbar}
      {body}
      {footer}
    </div>
  );
}

/** Panel de detalle que se despliega (slide-down) al expandir una fila. Anima la
 *  altura con el truco de `grid-template-rows: 0fr → 1fr` (sin medir alturas). */
function ExpandedPanel({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return (
    <div className={`grid transition-[grid-template-rows] duration-200 ease-out ${open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}>
      <div className="overflow-hidden">{children}</div>
    </div>
  );
}

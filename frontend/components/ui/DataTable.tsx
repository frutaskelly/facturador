"use client";

import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronRight, ChevronsUpDown, Columns3, Download, Eye, EyeOff, GripVertical, MoreVertical, Filter } from "lucide-react";

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
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // El cierre por clic-fuera decide por CONTAINS, no por stopPropagation:
    // el listener nativo del document recibía el mousedown de los propios
    // items ANTES de que React frenara la propagación, desmontaba el menú y
    // el click ya no encontraba botón — «Dar por revisada» y compañía
    // literalmente no hacían nada (tickets 86bbyp2rw/86bbyuxgt/86bbyw70p).
    const clicFuera = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    };
    // Y el cierre por scroll perdona el primer instante: enfocar el botón
    // (pegado al borde del contenedor con overflow) provoca un micro-scroll
    // que cerraba el menú antes de verse — el «no abre» de pantallas chicas.
    const abiertoEn = Date.now();
    const scrollLejos = () => { if (Date.now() - abiertoEn > 250) setOpen(false); };
    const close = () => setOpen(false);
    window.addEventListener("scroll", scrollLejos, true);
    window.addEventListener("resize", close);
    document.addEventListener("mousedown", clicFuera);
    return () => {
      window.removeEventListener("scroll", scrollLejos, true);
      window.removeEventListener("resize", close);
      document.removeEventListener("mousedown", clicFuera);
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
          ref={menuRef}
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
/** Texto plano de un nodo React: lo que el usuario VE en la celda.

    Es el último recurso de `exportText` para columnas sin `exportValue` ni
    `sortValue` cuya celda es JSX (un Badge, un span con title). Sin esto, el
    filtro de valores del encabezado listaba «(vacío)» para toda la columna y
    el CSV exportaba vacío (ticket 86bbyeny7: Folio/Cliente/Estado en
    Facturas). */
function nodeText(n: unknown): string {
  if (n == null || typeof n === "boolean") return "";
  if (typeof n === "string" || typeof n === "number") return String(n);
  if (Array.isArray(n)) return n.map(nodeText).filter(Boolean).join(" ");
  if (typeof n === "object" && "props" in (n as { props?: { children?: unknown } })) {
    return nodeText((n as { props?: { children?: unknown } }).props?.children);
  }
  return "";
}

function exportText<T>(col: Column<T>, row: T): string {
  const raw = col.exportValue
    ? col.exportValue(row)
    : col.sortValue
      ? col.sortValue(row)
      : col.cell(row);
  if (raw == null) return "";
  if (typeof raw === "number" || typeof raw === "string") return String(raw);
  return nodeText(raw).replace(/\s+/g, " ").trim();
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

// ── filtro por condición (estilo Excel: «Contiene», «Mayor que»…) ──
type CondOp = "contiene" | "no_contiene" | "igual" | "distinto" | "empieza" | "termina" | "mayor" | "menor";
type ColCond = { op: CondOp; val: string };
const COND_OPS: { op: CondOp; label: string }[] = [
  { op: "contiene", label: "Contiene" },
  { op: "no_contiene", label: "No contiene" },
  { op: "igual", label: "Es igual a" },
  { op: "distinto", label: "Es distinto de" },
  { op: "empieza", label: "Empieza con" },
  { op: "termina", label: "Termina con" },
  { op: "mayor", label: "Mayor que" },
  { op: "menor", label: "Menor que" },
];

/** Número dentro de un texto de celda («$1,234.50» → 1234.5) o null si no hay. */
function parseNum(s: string): number | null {
  const limpio = s.replace(/[^0-9.,-]/g, "").replace(/,/g, "");
  if (!/[0-9]/.test(limpio)) return null;
  const n = Number(limpio);
  return Number.isFinite(n) ? n : null;
}

/** ¿El texto de la celda cumple la condición? Compara normalizado (sin
 *  acentos/mayúsculas); «mayor/menor» intenta numérico primero (para importes
 *  con `$` y comas) y cae a orden alfabético si no hay número. */
function cumpleCond(texto: string, c: ColCond): boolean {
  const t = norm(texto);
  const v = norm(c.val.trim());
  switch (c.op) {
    case "contiene": return t.includes(v);
    case "no_contiene": return !t.includes(v);
    case "igual": return t === v;
    case "distinto": return t !== v;
    case "empieza": return t.startsWith(v);
    case "termina": return t.endsWith(v);
    case "mayor":
    case "menor": {
      const a = parseNum(texto);
      const b = parseNum(c.val);
      if (a != null && b != null) return c.op === "mayor" ? a > b : a < b;
      const cmp = texto.localeCompare(c.val, "es", { numeric: true, sensitivity: "base" });
      return c.op === "mayor" ? cmp > 0 : cmp < 0;
    }
  }
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
  /** Búsqueda CONTROLADA por el padre (va al servidor): el input de la tabla
   *  escribe aquí y el filtrado local por texto se apaga — las filas ya
   *  llegan filtradas. Sin esto, el buscador solo ve la página cargada y un
   *  folio viejo «no aparece» (ticket 86bbxx1cf). */
  searchValue?: string;
  onSearchChange?: (v: string) => void;
  /** Filtros por columna, estilo Excel (ticket 86bby31f9): un embudo en cada
   *  encabezado abre el diálogo de autofiltro — ordenar asc/desc, condición
   *  («Contiene», «Mayor que»…), buscador y la lista de valores con
   *  «(Seleccionar todo)». Se combinan entre columnas (Y). Filtran las filas
   *  CARGADAS: refinan lo que los filtros de servidor ya trajeron. */
  headerFilters?: boolean;
  /** Pagina del lado del cliente (sobre lo filtrado) con selector de filas/página. */
  paginated?: boolean;
  pageSizeOptions?: number[];
  defaultPageSize?: number;
  /** Activa una columna de casillas (checkbox) a la izquierda para seleccionar
   *  filas. La selección persiste entre orden/búsqueda/paginación. */
  selectable?: boolean;
  /** Decide qué filas admiten casilla. Las que devuelvan `false` se dibujan sin
   *  ella y quedan fuera del «seleccionar todo»: para tablas donde conviven
   *  filas de otra naturaleza, sobre las que las acciones en lote no aplican. */
  selectableRow?: (row: T) => boolean;
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
  searchValue,
  onSearchChange,
  headerFilters,
  paginated,
  pageSizeOptions = [10, 25, 50, 100],
  defaultPageSize = 25,
  selectable,
  selectableRow,
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
    if (!entry) return rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    // El popup de filtro permite ordenar CUALQUIER columna (no solo las
    // `sortable`): si la celda es JSX sin `sortValue`, compara por el texto
    // visible (el mismo que exporta/filtra).
    const valorDe = (row: T): string | number | null => {
      const v = comparable(entry.col, row);
      if (v != null) return v;
      const t = exportText(entry.col, row);
      return t === "" ? null : t;
    };
    return [...rows].sort((a, b) => {
      const va = valorDe(a);
      const vb = valorDe(b);
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
  // En modo servidor el texto vive en el padre; el de aquí queda sin uso.
  const searchText = onSearchChange ? (searchValue ?? "") : search;

  // ── filtros por columna (autofiltro estilo Excel) ──
  // Valores marcados por columna. SIN entrada = columna sin filtro (todo se
  // ve); presente = solo esos valores ([] = ninguno, como des-marcar
  // «(Seleccionar todo)» en Excel). Aparte, una condición opcional por
  // columna («Contiene x», «Mayor que n»…). Ambos se combinan con Y.
  const [colFilters, setColFilters] = useState<Record<string, string[]>>({});
  const [colConds, setColConds] = useState<Record<string, ColCond>>({});
  const [filterOpen, setFilterOpen] = useState<string | null>(null);
  // El popup va en `position: fixed` (como RowOverflowMenu): el contenedor de
  // la tabla tiene overflow y lo recortaría — p. ej. al filtrar a cero filas,
  // la tabla se encoge y cortaba la lista justo cuando hay que re-marcar.
  const [filterPos, setFilterPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const filterPopRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!filterOpen) return;
    // Cierra al hacer scroll FUERA del popup (la lista interna sí scrollea);
    // perdona el primer instante, igual que el menú ⋮ (ver nota ahí).
    const abiertoEn = Date.now();
    const alScroll = (e: Event) => {
      if (filterPopRef.current?.contains(e.target as Node)) return;
      if (Date.now() - abiertoEn > 250) setFilterOpen(null);
    };
    const alResize = () => setFilterOpen(null);
    window.addEventListener("scroll", alScroll, true);
    window.addEventListener("resize", alResize);
    return () => {
      window.removeEventListener("scroll", alScroll, true);
      window.removeEventListener("resize", alResize);
    };
  }, [filterOpen]);
  const hayColFilters = Object.keys(colFilters).length > 0 || Object.keys(colConds).length > 0;
  const filteredRows = useMemo(() => {
    const fn = rowFilterRef.current;
    let base = fn ? sortedRows.filter((row) => fn(row)) : sortedRows;
    // Filtros por columna: Y entre columnas, O entre los valores de una misma.
    const activos = Object.entries(colFilters);
    const conds = Object.entries(colConds);
    if (activos.length > 0 || conds.length > 0) {
      const porId = new Map(cols.map((c) => [c.id, c.col]));
      base = base.filter((row) =>
        activos.every(([id, vals]) => {
          const col = porId.get(id);
          return col ? vals.includes(exportText(col, row)) : true;
        }) &&
        conds.every(([id, c]) => {
          const col = porId.get(id);
          return col ? cumpleCond(exportText(col, row), c) : true;
        }),
      );
    }
    const q = onSearchChange ? "" : norm(search.trim());
    if (!q) return base;
    const tokens = q.split(/\s+/).filter(Boolean);
    return base.filter((row) => {
      const text = norm(cols.map(({ col }) => exportText(col, row)).join("  "));
      return tokens.every((t) => text.includes(t));
    });
    // `rowFilter` entra por ref + `rowFilterKey`: como arrow inline cambiaría
    // de identidad en cada render y recalcularía este memo siempre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortedRows, search, cols, rowFilterKey, onSearchChange, colFilters, colConds]);

  // Filas de referencia para la LISTA de valores del popup: lo cargado, con el
  // filtro externo y los filtros de las DEMÁS columnas aplicados (como Excel:
  // cada autofiltro lista lo que queda visible bajo los otros).
  function baseParaFiltro(excludeId: string): T[] {
    const fn = rowFilterRef.current;
    const base = fn ? sortedRows.filter((r) => fn(r)) : sortedRows;
    const porId = new Map(cols.map((c) => [c.id, c.col]));
    return base.filter((row) =>
      Object.entries(colFilters).every(([cid, vals]) => {
        if (cid === excludeId) return true;
        const col = porId.get(cid);
        return col ? vals.includes(exportText(col, row)) : true;
      }) &&
      Object.entries(colConds).every(([cid, c]) => {
        if (cid === excludeId) return true;
        const col = porId.get(cid);
        return col ? cumpleCond(exportText(col, row), c) : true;
      }),
    );
  }

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

  // Casilla de cabecera: marca/indeterminada según las filas FILTRADAS que
  // además admiten casilla (`selectableRow`); las que no, ni cuentan ni se
  // marcan con «seleccionar todo».
  const filteredKeys = useMemo(
    () => filteredRows.flatMap((row, i) => (selectableRow?.(row) ?? true ? [keyOf(row, i)] : [])),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ver nota de `selectedRows` sobre `rowKey`; `selectableRow` es igual de estable
    [filteredRows],
  );
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
  }, [search, searchValue, pageSize, rowFilterKey, colFilters, colConds]);

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

  // Quita una entrada de un record de filtros (dejar la clave con [] ya no es
  // «sin filtro»: significa «ningún valor», como en Excel).
  function sinClave<V>(f: Record<string, V>, id: string): Record<string, V> {
    const rest = { ...f };
    delete rest[id];
    return rest;
  }

  // Chips de los filtros por columna activos + «Limpiar todos» (86bby31f9.7)
  const chipsFiltros = hayColFilters ? (
    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
      {Object.entries(colFilters).map(([id, vals]) => {
        const col = cols.find((c) => c.id === id)?.col;
        return (
          <span key={`v-${id}`} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-1">
            <b>{col?.header ?? id}:</b>{" "}
            {vals.length === 0 ? "(ninguno)" : <>{vals.slice(0, 3).join(", ")}{vals.length > 3 ? ` +${vals.length - 3}` : ""}</>}
            <button
              type="button"
              aria-label={`Quitar filtro de ${col?.header ?? id}`}
              className="text-muted hover:text-danger"
              onClick={() => setColFilters((f) => sinClave(f, id))}
            >
              ×
            </button>
          </span>
        );
      })}
      {Object.entries(colConds).map(([id, c]) => {
        const col = cols.find((cc) => cc.id === id)?.col;
        const label = COND_OPS.find((o) => o.op === c.op)?.label ?? c.op;
        return (
          <span key={`c-${id}`} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-1">
            <b>{col?.header ?? id}:</b> {label.toLowerCase()} «{c.val}»
            <button
              type="button"
              aria-label={`Quitar condición de ${col?.header ?? id}`}
              className="text-muted hover:text-danger"
              onClick={() => setColConds((f) => sinClave(f, id))}
            >
              ×
            </button>
          </span>
        );
      })}
      <button type="button" className="text-accent hover:underline" onClick={() => { setColFilters({}); setColConds({}); }}>
        Limpiar todos los filtros
      </button>
    </div>
  ) : null;

  const toolbar = hasToolbar ? (
    <div className="mb-2 flex items-center justify-between gap-2">
      <div className="flex-1">
        {searchable && (
          <input
            type="search"
            value={searchText}
            onChange={(e) => (onSearchChange ? onSearchChange(e.target.value) : setSearch(e.target.value))}
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
  // El contenedor con scroll horizontal: la barra flotante (abajo) se
  // sincroniza contra él para que moverse a los lados no exija bajar al
  // fondo de una tabla larga (ticket 86bbyvxqw).
  const scrollerRef = useRef<HTMLDivElement>(null);

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
      <div ref={scrollerRef} className="overflow-x-auto rounded-xl border border-border">
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
                const fVals = colFilters[id];
                const fCond = colConds[id];
                const fActivo = fVals != null || fCond != null;
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
                      <span className="inline-flex max-w-full items-center gap-1">
                        <span className="truncate">{col.header}</span>
                        {/* Orden aplicado desde el popup en una columna sin
                            `sortable`: sin este ícono no habría ninguna seña. */}
                        {active && <Icon size={13} className="shrink-0" />}
                      </span>
                    )}
                    {headerFilters && col.header.trim() !== "" && (
                      <button
                        type="button"
                        onClick={(e) => {
                          const r = e.currentTarget.getBoundingClientRect();
                          const POP_W = 288; // w-72
                          setFilterPos({
                            top: r.bottom + 4,
                            left: Math.max(8, Math.min(r.left, window.innerWidth - POP_W - 8)),
                          });
                          setFilterOpen((f) => (f === id ? null : id));
                        }}
                        className={`ml-1 rounded p-0.5 align-middle transition hover:text-foreground ${fActivo ? "text-accent" : "opacity-40 hover:opacity-100"}`}
                        title={fActivo ? `Filtrado${fVals?.length ? `: ${fVals.slice(0, 5).join(", ")}` : ""}` : "Filtrar y ordenar"}
                        aria-label={`Filtrar ${col.header}`}
                      >
                        <Filter size={12} fill={fActivo ? "currentColor" : "none"} />
                      </button>
                    )}
                    {filterOpen === id && (
                      <>
                        {/* clic fuera = cerrar; va ANTES para quedar debajo */}
                        <div className="fixed inset-0 z-20" onMouseDown={() => setFilterOpen(null)} />
                        <div
                          ref={filterPopRef}
                          style={{ top: filterPos.top, left: filterPos.left, maxHeight: `calc(100vh - ${filterPos.top + 8}px)` }}
                          className="fixed z-30 w-72 overflow-auto rounded-lg border border-border bg-background p-2 text-left shadow-lg normal-case tracking-normal"
                        >
                          <HeaderFilterPopup
                            col={col}
                            rows={baseParaFiltro(id)}
                            filtro={fVals}
                            cond={fCond}
                            sortDir={active ? sort!.dir : null}
                            onFiltro={(vals) =>
                              setColFilters((f) => (vals == null ? sinClave(f, id) : { ...f, [id]: vals }))
                            }
                            onCond={(c) =>
                              setColConds((f) => (c == null ? sinClave(f, id) : { ...f, [id]: c }))
                            }
                            onSort={(dir) => setSort(dir ? { id, dir } : null)}
                            onClose={() => setFilterOpen(null)}
                          />
                        </div>
                      </>
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
                  Sin coincidencias{searchText.trim() ? ` para “${searchText.trim()}”` : ""}
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
                          {selectableRow?.(row) ?? true ? (
                            <Checkbox
                              checked={selectedKeys.has(key)}
                              onChange={() => toggleRowSelected(key)}
                              aria-label="Seleccionar fila"
                            />
                          ) : null}
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
      {chipsFiltros}
      {body}
      {!loading && !error && rows.length > 0 && <FloatingHScroll contRef={scrollerRef} />}
      {footer}
    </div>
  );
}

/** Diálogo de autofiltro por columna, estilo Excel (sin filtros por color:
 *  en el Facturador no manejamos colores). Secciones: ordenar asc/desc,
 *  condición («Contiene», «Mayor que»…), buscador de valores y la lista con
 *  «(Seleccionar todo)». Todo aplica en vivo (como el Auto Apply de Excel).
 *
 *  Semántica de la selección: sin filtro = todo marcado. Desmarcar guarda
 *  «solo estos valores»; si la selección vuelve a cubrir todo, el filtro se
 *  quita solo. `[]` = ningún valor (des-marcar «Seleccionar todo» y elegir
 *  de cero, el gesto clásico de Excel). */
function HeaderFilterPopup<T>({
  col,
  rows,
  filtro,
  cond,
  sortDir,
  onFiltro,
  onCond,
  onSort,
  onClose,
}: {
  col: Column<T>;
  /** Filas visibles bajo los filtros de las DEMÁS columnas: de aquí sale la lista. */
  rows: T[];
  /** Valores marcados; `undefined` = sin filtro (todo marcado). */
  filtro: string[] | undefined;
  cond: ColCond | undefined;
  sortDir: "asc" | "desc" | null;
  /** `null` = quitar el filtro de valores. */
  onFiltro: (vals: string[] | null) => void;
  onCond: (c: ColCond | null) => void;
  onSort: (dir: "asc" | "desc" | null) => void;
  onClose: () => void;
}) {
  const [busca, setBusca] = useState("");
  // Borradores de la condición: el operador elegido no se pierde mientras el
  // valor está vacío (solo las condiciones CON valor viven en el padre).
  const [opDraft, setOpDraft] = useState<CondOp>(cond?.op ?? "contiene");
  const [valDraft, setValDraft] = useState(cond?.val ?? "");
  const todosRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const valores = useMemo(() => {
    const vistos = new Map<string, number>();
    for (const r of rows) {
      const v = exportText(col, r);
      vistos.set(v, (vistos.get(v) ?? 0) + 1);
    }
    return [...vistos.entries()].sort((a, b) => a[0].localeCompare(b[0], "es", { numeric: true }));
  }, [rows, col]);

  const q = norm(busca.trim());
  const listados = q ? valores.filter(([v]) => norm(v || "(vacío)").includes(q)) : valores;

  const marcado = (v: string) => (filtro ? filtro.includes(v) : true);
  const todosListadosMarcados = listados.length > 0 && listados.every(([v]) => marcado(v));
  const algunoListadoMarcado = listados.some(([v]) => marcado(v));
  useEffect(() => {
    if (todosRef.current) todosRef.current.indeterminate = algunoListadoMarcado && !todosListadosMarcados;
  }, [algunoListadoMarcado, todosListadosMarcados]);

  // Si la selección cubre todos los valores, el filtro sobra: se quita.
  function aplicar(sel: Set<string>) {
    onFiltro(valores.every(([v]) => sel.has(v)) ? null : [...sel]);
  }
  function toggleValor(v: string) {
    const sel = new Set(filtro ?? valores.map(([x]) => x));
    if (sel.has(v)) sel.delete(v);
    else sel.add(v);
    aplicar(sel);
  }
  // «(Seleccionar todo)» opera sobre lo LISTADO (respeta el buscador):
  // buscar «lech» y marcar todo agrega solo las lechugas, como en Excel.
  function toggleTodos() {
    const sel = new Set(filtro ?? valores.map(([x]) => x));
    if (todosListadosMarcados) listados.forEach(([v]) => sel.delete(v));
    else listados.forEach(([v]) => sel.add(v));
    aplicar(sel);
  }

  function emitirCond(op: CondOp, val: string) {
    setOpDraft(op);
    setValDraft(val);
    onCond(val.trim() === "" ? null : { op, val });
  }

  const inputCls =
    "rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground outline-none focus:border-accent";

  return (
    <>
      <div className="mb-1.5 flex items-center justify-between gap-2 px-1">
        <span className="truncate text-[11px] font-semibold uppercase text-muted">{col.header}</span>
        {(filtro != null || cond != null) && (
          <button
            type="button"
            className="shrink-0 text-xs text-accent hover:underline"
            onClick={() => { onFiltro(null); emitirCond("contiene", ""); }}
          >
            Limpiar filtro
          </button>
        )}
      </div>

      {/* Ordenar (clic en el orden activo lo quita) */}
      <div className="mb-2 grid grid-cols-2 gap-1 px-1">
        <button
          type="button"
          onClick={() => onSort(sortDir === "asc" ? null : "asc")}
          className={`inline-flex items-center justify-center gap-1 rounded-md border px-2 py-1 text-xs ${
            sortDir === "asc" ? "border-accent text-accent" : "border-border text-foreground hover:bg-surface-2"
          }`}
        >
          <ArrowUp size={12} /> Ascendente
        </button>
        <button
          type="button"
          onClick={() => onSort(sortDir === "desc" ? null : "desc")}
          className={`inline-flex items-center justify-center gap-1 rounded-md border px-2 py-1 text-xs ${
            sortDir === "desc" ? "border-accent text-accent" : "border-border text-foreground hover:bg-surface-2"
          }`}
        >
          <ArrowDown size={12} /> Descendente
        </button>
      </div>

      {/* Condición */}
      <div className="mb-2 flex items-center gap-1 px-1">
        <select
          value={opDraft}
          onChange={(e) => emitirCond(e.target.value as CondOp, valDraft)}
          aria-label="Condición"
          className={`${inputCls} shrink-0`}
        >
          {COND_OPS.map((o) => (
            <option key={o.op} value={o.op}>{o.label}</option>
          ))}
        </select>
        <input
          type="text"
          value={valDraft}
          onChange={(e) => emitirCond(opDraft, e.target.value)}
          placeholder="Valor…"
          aria-label="Valor de la condición"
          className={`${inputCls} w-full min-w-0 flex-1`}
        />
      </div>

      {/* Buscador de valores */}
      <div className="mb-1 px-1">
        <input
          type="search"
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
          placeholder="Buscar valores…"
          aria-label="Buscar valores"
          className={`${inputCls} w-full`}
        />
      </div>

      {/* Lista de valores */}
      <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs font-semibold text-foreground hover:bg-surface-2">
        <input
          ref={todosRef}
          type="checkbox"
          className="h-3.5 w-3.5 rounded border-border"
          checked={todosListadosMarcados}
          onChange={toggleTodos}
          disabled={listados.length === 0}
        />
        <span className="min-w-0 flex-1 truncate">(Seleccionar todo)</span>
        <span className="tabular-nums font-normal text-muted">{listados.reduce((n, [, c]) => n + c, 0)}</span>
      </label>
      <div className="max-h-44 overflow-auto">
        {listados.length === 0 && <div className="px-1 py-1 text-xs text-muted">Sin valores</div>}
        {listados.slice(0, 150).map(([v, n]) => (
          <label key={v} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs font-normal text-foreground hover:bg-surface-2">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-border"
              checked={marcado(v)}
              onChange={() => toggleValor(v)}
            />
            <span className="min-w-0 flex-1 truncate">{v || "(vacío)"}</span>
            <span className="tabular-nums text-muted">{n}</span>
          </label>
        ))}
        {listados.length > 150 && (
          <div className="px-1 py-1 text-[11px] text-muted">
            …{listados.length - 150} valores más: usa el buscador para acotar
          </div>
        )}
      </div>
    </>
  );
}

/** Barra de scroll horizontal SIEMPRE a la vista (ticket 86bbyvxqw).
 *
 *  La barra real vive en el borde inferior del contenedor: con una tabla
 *  larga hay que bajar hasta el fondo para poder moverse a los lados. Esta
 *  flota pegada al borde inferior de la VENTANA mientras (a) la tabla sí
 *  desborda a lo ancho y (b) su final queda fuera de pantalla; en cuanto la
 *  barra real entra a la vista, esta se esconde. Sincronizada en ambos
 *  sentidos (arrastrarla mueve la tabla y viceversa). */
function FloatingHScroll({ contRef }: { contRef: React.RefObject<HTMLDivElement | null> }) {
  const barRef = useRef<HTMLDivElement>(null);
  const [geo, setGeo] = useState<{ visible: boolean; left: number; width: number; inner: number }>(
    { visible: false, left: 0, width: 0, inner: 0 },
  );

  useEffect(() => {
    const cont = contRef.current;
    if (!cont) return;
    const medir = () => {
      const r = cont.getBoundingClientRect();
      const desborda = cont.scrollWidth > cont.clientWidth + 1;
      // La barra real (el fondo del contenedor) está fuera de pantalla y la
      // tabla sigue a la vista: es exactamente cuando la flotante ayuda.
      const visible = desborda && r.bottom > window.innerHeight && r.top < window.innerHeight - 60;
      setGeo({ visible, left: r.left, width: cont.clientWidth, inner: cont.scrollWidth });
      if (visible && barRef.current && Math.abs(barRef.current.scrollLeft - cont.scrollLeft) > 1) {
        barRef.current.scrollLeft = cont.scrollLeft;
      }
    };
    medir();
    // La igualdad corta el eco: asignar el mismo scrollLeft no dispara evento.
    const desdeCont = () => {
      if (barRef.current && Math.abs(barRef.current.scrollLeft - cont.scrollLeft) > 1) {
        barRef.current.scrollLeft = cont.scrollLeft;
      }
    };
    cont.addEventListener("scroll", desdeCont, { passive: true });
    window.addEventListener("scroll", medir, { passive: true, capture: true });
    window.addEventListener("resize", medir);
    const ro = new ResizeObserver(medir);
    ro.observe(cont);
    if (cont.firstElementChild) ro.observe(cont.firstElementChild);
    return () => {
      cont.removeEventListener("scroll", desdeCont);
      window.removeEventListener("scroll", medir, { capture: true } as EventListenerOptions);
      window.removeEventListener("resize", medir);
      ro.disconnect();
    };
  }, [contRef]);

  if (!geo.visible) return null;
  return (
    <div
      ref={barRef}
      aria-hidden
      className="fixed bottom-0 z-40 overflow-x-auto overflow-y-hidden"
      style={{ left: geo.left, width: geo.width }}
      onScroll={() => {
        const cont = contRef.current;
        if (cont && barRef.current && Math.abs(cont.scrollLeft - barRef.current.scrollLeft) > 1) {
          cont.scrollLeft = barRef.current.scrollLeft;
        }
      }}
    >
      {/* El «contenido» es un espaciador del ancho real de la tabla: es lo que
          da a la barra su proporción correcta. */}
      <div style={{ width: geo.inner, height: 1 }} />
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

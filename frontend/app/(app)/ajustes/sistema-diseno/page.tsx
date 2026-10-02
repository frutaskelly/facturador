"use client";

import { useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Circle,
  Eye,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column, type RowAction } from "@/components/ui/DataTable";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { EmptyState } from "@/components/ui/EmptyState";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal, type ModalSize } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { SearchBox, SearchSelect, type SearchOption } from "@/components/ui/SearchBox";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { ComingSoon } from "@/components/ComingSoon";
import { KeyboardCombobox, type ComboOption } from "@/components/KeyboardCombobox";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { useToast } from "@/components/ui/Toast";

const SWATCHES = [
  { name: "background", cls: "bg-background border border-border" },
  { name: "surface-2", cls: "bg-surface-2" },
  { name: "accent", cls: "bg-accent" },
  { name: "danger", cls: "bg-danger" },
  { name: "border", cls: "bg-border" },
  { name: "foreground", cls: "bg-foreground" },
];

type Demo = { id: number; nombre: string; estado: string };
const DEMO_ROWS: Demo[] = [
  { id: 1, nombre: "Jitomate saladette", estado: "Activo" },
  { id: 2, nombre: "Aceite Nutrioli 850 ml", estado: "Activo" },
  { id: 3, nombre: "Coca-Cola 600 ml", estado: "Inactivo" },
  { id: 4, nombre: "Plátano Tabasco", estado: "Activo" },
  { id: 5, nombre: "Lechuga romana", estado: "Activo" },
  { id: 6, nombre: "Papel higiénico 4 rollos", estado: "Inactivo" },
];

const PRESENTACIONES: ComboOption[] = [
  { value: "KGM", label: "Kilogramo" },
  { value: "H87", label: "Pieza" },
  { value: "XBX", label: "Caja" },
  { value: "XPK", label: "Paquete" },
  { value: "LTR", label: "Litro" },
];

// Pasos de ejemplo para la réplica visual del checklist de onboarding.
const ONBOARDING_PASOS = [
  { titulo: "Datos fiscales del emisor", detalle: "Razón social, RFC, régimen y CP.", completo: true },
  { titulo: "Certificado de sello digital (CSD)", detalle: "Sube el .cer + .key y su contraseña.", completo: true },
  { titulo: "Serie de folios", detalle: "Define la serie por defecto para tus facturas.", completo: false },
  { titulo: "Primer cliente con RFC válido", detalle: "Valida el RFC contra el padrón del SAT.", completo: false },
];

// Los cuatro tamaños del Modal: para qué sirve cada uno (se elige por lo que
// lleva adentro, no por gusto).
const MODAL_SIZES: { size: ModalSize; uso: string; detalle: string }[] = [
  { size: "sm", uso: "Confirmar o avisar", detalle: "Sí/no, «listo» o uno o dos campos. Ancho 28rem, sin esquina para agrandar." },
  { size: "md", uso: "Capturar o buscar (por defecto)", detalle: "Formulario de una columna, buscador, enviar por correo. Ancho 36rem." },
  { size: "lg", uso: "Formularios grandes", detalle: "Dos columnas o una tabla chica de partidas. Ancho 52rem." },
  { size: "xl", uso: "Tablas grandes", detalle: "Precios de una lista, importar, generar factura. Alto fijo (85vh): la tabla no brinca al filtrar y solo hace scroll el cuerpo." },
];

const UNIDADES_SAT: SearchOption[] = [
  { value: "KGM", label: "Kilogramo", hint: "KGM" },
  { value: "H87", label: "Pieza", hint: "H87" },
  { value: "LTR", label: "Litro", hint: "LTR" },
  { value: "XBX", label: "Caja", hint: "XBX" },
  { value: "MTR", label: "Metro", hint: "MTR" },
  { value: "GRM", label: "Gramo", hint: "GRM" },
  { value: "XPK", label: "Paquete", hint: "XPK" },
  { value: "E48", label: "Unidad de servicio", hint: "E48" },
];

// Catálogo largo de ejemplo (>8 opciones) para mostrar el filtro del Select.
const CATALOGO_DEMO: { value: string; label: string }[] = [
  { value: "601", label: "601 — General de Ley Personas Morales" },
  { value: "603", label: "603 — Personas Morales con Fines no Lucrativos" },
  { value: "605", label: "605 — Sueldos y Salarios e Ingresos Asimilados a Salarios" },
  { value: "606", label: "606 — Arrendamiento" },
  { value: "608", label: "608 — Demás ingresos" },
  { value: "612", label: "612 — Personas Físicas con Actividades Empresariales y Profesionales" },
  { value: "616", label: "616 — Sin obligaciones fiscales" },
  { value: "621", label: "621 — Incorporación Fiscal" },
  { value: "626", label: "626 — Régimen Simplificado de Confianza (RESICO)" },
  { value: "630", label: "630 — Enajenación de acciones en bolsa de valores" },
];

export default function SistemaDisenoPage() {
  const toast = useToast();
  const [sw, setSw] = useState(true);
  const [chk, setChk] = useState(true);
  const [modal, setModal] = useState<ModalSize | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busqueda, setBusqueda] = useState("");
  const [prodSel, setProdSel] = useState<string | null>(null);
  const [unidadSat, setUnidadSat] = useState<string | null>("KGM");
  const [kb, setKb] = useState("KGM");
  const [selLargo, setSelLargo] = useState("");
  const [sel, setSel] = useState("a");

  const cols: Column<Demo>[] = [
    { header: "ID", cell: (r) => r.id, className: "w-1", sortable: true, sortValue: (r) => r.id },
    { header: "Nombre", cell: (r) => <span className="font-medium">{r.nombre}</span>, sortable: true, sortValue: (r) => r.nombre },
    {
      header: "Estado",
      cell: (r) => <Badge tone={r.estado === "Activo" ? "success" : "muted"}>{r.estado}</Badge>,
      sortable: true,
      sortValue: (r) => r.estado,
    },
  ];

  // Columna de acciones: íconos configurables (reordenar/ocultar con el menú ⋮).
  // Todo ícono da un pulso al pulsarlo; si `onClick` devuelve una promesa
  // («Ver» simula una carga de 1.5 s), gira como spinner hasta que termina.
  const rowActions: RowAction<Demo>[] = [
    {
      id: "ver", label: "Ver", icon: <Eye size={16} />,
      onClick: async (r) => {
        await new Promise((ok) => setTimeout(ok, 1500));
        toast.success(`Ver ${r.nombre}`);
      },
    },
    { id: "editar", label: "Editar", icon: <Pencil size={16} />, onClick: (r) => toast.success(`Editar ${r.nombre}`) },
    { id: "eliminar", label: "Eliminar", icon: <Trash2 size={16} />, tone: "danger", onClick: (r) => toast.error(`Eliminar ${r.nombre}`) },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sistema de diseño"
        subtitle="Tokens, tipografía y todos los componentes reutilizables de la app."
      />

      {/* Colores */}
      <Card title="Colores / tokens">
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
          {SWATCHES.map((s) => (
            <div key={s.name} className="text-center">
              <div className={`mx-auto mb-1 h-12 w-full rounded-lg ${s.cls}`} />
              <span className="text-xs text-muted">{s.name}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Tipografía */}
      <Card title="Tipografía">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold">Título H1 — 2xl bold</h1>
          <h2 className="text-lg font-semibold">Subtítulo H2 — lg semibold</h2>
          <p className="text-sm">Cuerpo — text-sm. El texto base de tablas, formularios y contenido.</p>
          <p className="text-sm text-muted">Texto secundario — text-muted, para pistas y metadatos.</p>
          <p className="text-xs text-muted">Caption — text-xs text-muted.</p>
        </div>
      </Card>

      {/* Botones */}
      <Card title="Botones" subtitle="variant: primary · secondary · danger · ghost">
        <div className="flex flex-wrap items-center gap-2">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="ghost">Ghost</Button>
          <Button disabled>Deshabilitado</Button>
          <Button>
            <Plus size={16} /> Con ícono
          </Button>
        </div>
      </Card>

      {/* Badges */}
      <Card title="Badges" subtitle="tone: default · success · warning · danger · muted · accent">
        <div className="flex flex-wrap gap-2">
          <Badge>Default</Badge>
          <Badge tone="success">Activo</Badge>
          <Badge tone="warning">Por vencer</Badge>
          <Badge tone="danger">Vencido</Badge>
          <Badge tone="muted">Inactivo</Badge>
          <Badge tone="accent">Nuevo</Badge>
        </div>
      </Card>

      {/* Alertas */}
      <Card title="Alertas / callouts" subtitle="tone: info · success · warning · danger">
        <div className="space-y-2">
          <Alert tone="info" title="Informativo">Mensaje neutral para contexto o ayuda.</Alert>
          <Alert tone="success" title="Éxito">La operación se completó correctamente.</Alert>
          <Alert tone="warning" title="Atención">Revisa la caducidad de los perecederos.</Alert>
          <Alert tone="danger" title="Error">Existencia insuficiente para la línea 2.</Alert>
        </div>
      </Card>

      {/* Formulario */}
      <Card title="Formulario" subtitle="Field, Input, Select, Textarea, Switch">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Campo de texto" required>
            <Input placeholder="Escribe algo…" />
          </Field>
          <Field label="Selector" hint="dropdown con el diseño de la app (no el nativo del SO)">
            <Select value={sel} onChange={(e) => setSel(e.target.value)}>
              <option value="a">Opción A</option>
              <option value="b">Opción B</option>
              <option value="c">Opción C</option>
              <option value="d" disabled>Opción D (no disponible)</option>
            </Select>
          </Field>
          <Field
            label="Selector con filtro"
            hint="con más de 8 opciones aparece una caja de filtro arriba (catálogos SAT: régimen fiscal, uso de CFDI, forma de pago…)"
          >
            <Select value={selLargo} onChange={(e) => setSelLargo(e.target.value)}>
              {CATALOGO_DEMO.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Número">
            <Input type="number" placeholder="0" />
          </Field>
          <Field label="Input deshabilitado">
            <Input value="No editable" disabled readOnly />
          </Field>
          <Field label="Selector deshabilitado" hint="tono gris suave indica que no se puede usar">
            <Select value="a" disabled onChange={() => {}}>
              <option value="a">No editable</option>
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Área de texto">
              <Textarea rows={2} placeholder="Descripción…" />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={sw} onChange={setSw} />
            <span className="text-sm">Switch ({sw ? "on" : "off"})</span>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={chk} onChange={(e) => setChk(e.target.checked)} />
            <span>Checkbox ({chk ? "marcado" : "sin marcar"})</span>
          </label>
        </div>
      </Card>

      {/* Tabs */}
      <Card title="Tabs">
        <Tabs
          tabs={[
            { id: "a", label: "General", content: <p className="text-sm text-muted">Contenido de la pestaña General.</p> },
            { id: "b", label: "Fiscal", content: <p className="text-sm text-muted">Contenido de la pestaña Fiscal.</p> },
            { id: "c", label: "Historial", content: <p className="text-sm text-muted">Contenido de la pestaña Historial.</p> },
          ]}
        />
      </Card>

      {/* Feedback */}
      <Card title="Feedback" subtitle="Toast, Spinner, EmptyState">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => toast.success("Guardado correctamente")}>
            <Check size={16} /> Toast éxito
          </Button>
          <Button variant="secondary" onClick={() => toast.error("Algo salió mal")}>Toast error</Button>
          <Button variant="secondary" onClick={() => toast.info("Información")}>Toast info</Button>
          <span className="inline-flex items-center gap-2 text-sm text-muted"><Spinner /> cargando…</span>
        </div>
        <div className="mt-4">
          <EmptyState title="Sin resultados" />
        </div>
      </Card>

      {/* Overlays */}
      <Card
        title="Overlays"
        subtitle="Modal (4 tamaños: sm · md · lg · xl), ConfirmDialog — se cierra solo con Cancelar o la X; Enter en una caja de texto ejecuta la acción marcada con data-modal-primary; la esquina para agrandar (líneas diagonales) está en md, lg y xl"
      >
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {MODAL_SIZES.map((m) => (
              <Button key={m.size} variant="secondary" onClick={() => setModal(m.size)}>
                Modal {m.size}
              </Button>
            ))}
            <Button variant="danger" onClick={() => setConfirm(true)}>
              <Trash2 size={16} /> Abrir Confirm
            </Button>
          </div>
          <ul className="space-y-1 text-sm">
            {MODAL_SIZES.map((m) => (
              <li key={m.size}>
                <code className="rounded bg-surface-2 px-1">size=&quot;{m.size}&quot;</code>{" "}
                <span className="font-medium">{m.uso}</span>
                <span className="text-muted"> — {m.detalle}</span>
              </li>
            ))}
          </ul>
        </div>
      </Card>

      {/* Search box */}
      <Card title="Search box" subtitle="SearchBox (sencillo) · SearchSelect (búsqueda + dropdown) · ProductoCombobox (buscador inteligente)">
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {/* 1) Sencillo */}
          <div>
            <h3 className="mb-1 text-sm font-semibold">1. Search box sencillo</h3>
            <p className="mb-2 text-xs text-muted">
              Ícono de lupa, botón para limpiar. Para filtrar listas o tablas en vivo.
            </p>
            <SearchBox value={busqueda} onChange={setBusqueda} placeholder="Buscar…" />
            <p className="mt-2 text-xs text-muted">
              Valor: <code className="rounded bg-surface-2 px-1">{busqueda || "—"}</code>
            </p>
          </div>

          {/* 2) Búsqueda + dropdown */}
          <div>
            <h3 className="mb-1 text-sm font-semibold">2. Search box + dropdown</h3>
            <p className="mb-2 text-xs text-muted">
              Combobox: escribe para filtrar (sin acentos), navega con ↑/↓, Enter selecciona.
            </p>
            <SearchSelect
              options={UNIDADES_SAT}
              value={unidadSat}
              onSelect={(o) => setUnidadSat(o?.value ?? null)}
              placeholder="Buscar unidad SAT…"
            />
            <p className="mt-2 text-xs text-muted">
              Selección: <code className="rounded bg-surface-2 px-1">{unidadSat ?? "—"}</code>
            </p>
          </div>

          {/* 3) Buscador de producto (inteligente, backend) */}
          <div>
            <h3 className="mb-1 text-sm font-semibold">3. Buscador de producto</h3>
            <p className="mb-2 text-xs text-muted">
              ProductoCombobox: cruce inteligente (exacto → alias → difuso → IA). Usado en remisiones, compras e inventario.
            </p>
            <ProductoCombobox onSelect={(p) => setProdSel(p ? p.nombre : null)} placeholder="Buscar producto…" />
            <p className="mt-2 text-xs text-muted">
              Selección: <code className="rounded bg-surface-2 px-1">{prodSel ?? "—"}</code>
            </p>
          </div>
        </div>
      </Card>

      {/* KeyboardCombobox */}
      <Card
        title="KeyboardCombobox"
        subtitle="Combobox 100% teclado — para flujos encadenados de alta rápida"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <p className="mb-2 text-xs text-muted">
              Enfoca y abre; escribe para filtrar; <b>↑/↓</b> resaltan, <b>Enter</b> selecciona y avanza,
              <b> ←/→</b> saltan entre campos. Usado en el alta por teclado (remisiones/compras) para capturar
              líneas sin tocar el mouse.
            </p>
            <div className="max-w-xs">
              <KeyboardCombobox options={PRESENTACIONES} value={kb} onSelect={setKb} placeholder="Presentación…" />
            </div>
            <p className="mt-2 text-xs text-muted">
              Selección: <code className="rounded bg-surface-2 px-1">{kb || "—"}</code>
            </p>
          </div>
        </div>
      </Card>

      {/* ComingSoon */}
      <Card title="ComingSoon" subtitle="Placeholder para secciones aún no disponibles">
        <div className="rounded-xl border border-dashed border-border p-4">
          <ComingSoon title="Reportes avanzados" />
        </div>
      </Card>

      {/* Onboarding (réplica visual — componentes data-driven) */}
      <Card
        title="Onboarding fiscal"
        subtitle="OnboardingBanner + OnboardingChecklist — se alimentan del estado real del tenant (aquí, muestra de referencia)"
      >
        <div className="space-y-4">
          {/* Banner */}
          <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
            <AlertTriangle size={18} className="shrink-0" />
            <span className="flex-1">
              <span className="font-medium">Completa la configuración de tu empresa</span> para emitir
              facturas a tu nombre — falta: Serie de folios, Primer cliente con RFC válido.
            </span>
            <ArrowRight size={16} className="shrink-0" />
          </div>

          {/* Checklist */}
          <div className="rounded-2xl border border-border bg-surface p-4">
            <div className="mb-3 flex items-start justify-between gap-2">
              <div>
                <div className="text-sm font-semibold">Configuración para facturar</div>
                <div className="text-xs text-muted">
                  {ONBOARDING_PASOS.filter((p) => p.completo).length} de {ONBOARDING_PASOS.length} pasos completos
                </div>
              </div>
              <Badge tone="warning">Configuración pendiente</Badge>
            </div>
            <ol className="space-y-3">
              {ONBOARDING_PASOS.map((p, i) => (
                <li key={p.titulo} className="flex items-start gap-3">
                  {p.completo ? (
                    <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-600" />
                  ) : (
                    <Circle size={20} className="mt-0.5 shrink-0 text-muted" />
                  )}
                  <div>
                    <div className="text-sm font-medium">
                      <span className="text-muted">{i + 1}.</span> {p.titulo}
                    </div>
                    <div className="text-xs text-muted">{p.detalle}</div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </Card>

      {/* Plantillas y estructura (no renderizables en aislado) */}
      <Card
        title="Plantillas y estructura"
        subtitle="Componentes de página / layout — no se renderizan aislados; se documentan aquí"
      >
        <ul className="space-y-2 text-sm">
          <li>
            <code className="rounded bg-surface-2 px-1">CrudPage</code>{" "}
            <span className="text-muted">
              — plantilla de página CRUD por configuración (campos, columnas, lookups, códigos auto). Alimenta
              categorías, sucursales y proveedores; el formulario reutiliza Field/Input/Select/Switch de arriba.
            </span>
          </li>
          <li>
            <code className="rounded bg-surface-2 px-1">Sidebar</code> /{" "}
            <code className="rounded bg-surface-2 px-1">Topbar</code>{" "}
            <span className="text-muted">— estructura del shell de la app (navegación por permisos, tenant, usuario).</span>
          </li>
          <li>
            <code className="rounded bg-surface-2 px-1">ProductoCombobox</code>{" "}
            <span className="text-muted">— ver la sección “Search box” (buscador inteligente con backend).</span>
          </li>
        </ul>
      </Card>

      {/* Tabla */}
      <Card title="DataTable">
        <p className="mb-3 text-sm text-muted">
          <b>Buscar</b> filtra en todas las columnas (sin acentos, por palabras). Clic en el encabezado para
          ordenar (asc → desc → sin orden). Botón <b>Columnas</b> para mostrar/ocultar y reordenar arrastrando.
          Arrastra el <b>borde derecho</b> de un encabezado para el ancho (estilo Excel: la tabla se ensancha y
          aparece scroll, sin comprimir las demás). Botón <b>Excel</b> para descargar. Columna de <b>acciones</b>
          con su menú <b>⋮</b> para reordenar/ocultar íconos. Pie de tabla con <b>paginado</b>. Todo se recuerda por tabla.
        </p>
        <DataTable columns={cols} rows={DEMO_ROWS} empty="Sin datos" searchable searchPlaceholder="Buscar producto…" columnsMenu resizable exportable exportFilename="demo" storageKey="demo-sistema-diseno" paginated defaultPageSize={5} pageSizeOptions={[5, 10, 25, 50]} actions={rowActions} actionsMenu />
      </Card>

      {/* Tabla con filas expandibles */}
      <Card title="DataTableSmart">
        <p className="mb-3 text-sm text-muted">
          Misma <b>DataTable</b> con todas sus funciones activadas por defecto (buscar, ordenar, columnas,
          ancho estilo Excel, Excel, paginado) más <b>filas expandibles</b>: haz clic en una fila y se despliega
          (<i>slide-down</i>) un panel con su detalle. El detalle se puede cargar bajo demanda con
          <code className="mx-1 rounded bg-surface-2 px-1">onRowExpand</code>. La columna de <b>acciones</b> y su
          menú <b>⋮</b> vienen incluidos.
        </p>
        <DataTableSmart
          columns={cols}
          rows={DEMO_ROWS}
          empty="Sin datos"
          searchPlaceholder="Buscar producto…"
          exportFilename="demo-smart"
          storageKey="demo-smart-sistema-diseno"
          defaultPageSize={5}
          pageSizeOptions={[5, 10, 25, 50]}
          actions={rowActions}
          rowKey={(r) => r.id}
          renderExpanded={(r) => (
            <div className="rounded-xl border border-border bg-background p-4 text-sm">
              <div className="mb-2 font-medium">{r.nombre}</div>
              <div className="flex flex-wrap gap-4 text-muted">
                <div><span className="text-foreground">ID:</span> {r.id}</div>
                <div><span className="text-foreground">Estado:</span> <Badge tone={r.estado === "Activo" ? "success" : "muted"}>{r.estado}</Badge></div>
              </div>
              <p className="mt-2 text-muted">
                Este panel es el contenido de <code className="rounded bg-surface-2 px-1">renderExpanded</code>.
                Aquí iría el detalle de la fila (líneas, totales, etc.).
              </p>
            </div>
          )}
        />
      </Card>

      <Modal
        open={modal !== null}
        onClose={() => setModal(null)}
        title={`Modal de ejemplo · ${modal ?? "md"}`}
        size={modal ?? "md"}
        description={MODAL_SIZES.find((m) => m.size === modal)?.uso}
        footer={
          <>
            <Button variant="secondary" onClick={() => setModal(null)}>Cancelar</Button>
            <Button
              data-modal-primary
              onClick={() => {
                setModal(null);
                toast.success("Aceptar");
              }}
            >
              Aceptar
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          <p>{MODAL_SIZES.find((m) => m.size === modal)?.detalle}</p>
          <Field label="Prueba Enter" hint="Escribe algo y pulsa Enter: ejecuta «Aceptar», el botón marcado con data-modal-primary.">
            <Input placeholder="Escribe y pulsa Enter…" />
          </Field>
          <p className="text-muted">
            Se cierra solo con Cancelar o la X: ni Esc ni un clic en el fondo lo cierran, para no perder lo
            capturado.
            {modal !== "sm" && " Arrastra la esquina inferior derecha para agrandarlo o achicarlo."}
          </p>
        </div>
      </Modal>

      <ConfirmDialog
        open={confirm}
        title="¿Eliminar elemento?"
        message="Esta acción se puede revertir recreando el elemento."
        onConfirm={() => { setConfirm(false); toast.success("Eliminado"); }}
        onClose={() => setConfirm(false)}
      />
    </div>
  );
}

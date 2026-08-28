"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Eye, Lock, Pencil, Plus, Shield, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Checkbox, Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import type { Permission, Role, RoleDetail } from "@/lib/types";

const WRITE = "role:gestionar";

/** Humanize an action segment ("ajustes.usuarios" → "Ajustes · Usuarios"). */
function humanize(s: string): string {
  return s
    .split(".")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" · ");
}

const RECURSO_LABEL: Record<string, string> = {
  producto: "Productos",
  categoria: "Categorías",
  esquema_impuesto: "Esquemas de impuesto",
  lista_precios: "Listas de precios",
  cliente: "Clientes",
  proveedor: "Proveedores",
  almacen: "Almacenes",
  inventario: "Inventario",
  compra: "Compras",
  conversion: "Conversiones",
  remision: "Remisiones",
  factura: "Facturas",
  serie: "Series de folios",
  pedido: "Pedidos (POS)",
  devolucion: "Devoluciones (POS)",
  role: "Roles",
  membership: "Usuarios / Membresías",
  menu: "Menús",
};

// ── Matriz de permisos por pantalla ──
// Cada celda controla un GRUPO de permission ids: marcar la casilla los pone
// todos, desmarcarla los quita todos. Se agrupan porque separarlos en la UI
// (p. ej. inventario:gestionar vs almacen:gestionar) solo confunde: para el
// operador es una sola pantalla. Lo que no aparece aquí vive en "Permisos
// avanzados" para que ningún permiso quede ineditable.
type MatrixRow = { pantalla: string; ver: string[]; editar: string[]; borrar: string[]; borrarTitle?: string };
const MATRIX: MatrixRow[] = [
  { pantalla: "Dashboard", ver: ["menu:dashboard"], editar: [], borrar: [] },
  { pantalla: "Bandeja de órdenes", ver: ["menu:oc"], editar: ["remision:gestionar"], borrar: [] },
  { pantalla: "Remisiones", ver: ["menu:remisiones"], editar: ["remision:gestionar"], borrar: ["remision:eliminar"] },
  {
    pantalla: "Facturas",
    ver: ["menu:facturas"],
    editar: ["factura:gestionar"],
    borrar: ["factura:eliminar", "factura:cancelar"],
    borrarTitle: "Eliminar borradores y cancelar timbradas",
  },
  { pantalla: "Cotizador", ver: ["menu:cotizador"], editar: [], borrar: [] },
  {
    pantalla: "Clientes (incluye sucursales y proyectos)",
    ver: ["menu:clientes"],
    editar: ["cliente:gestionar"],
    borrar: ["cliente:eliminar"],
  },
  { pantalla: "Productos", ver: ["menu:productos"], editar: ["producto:gestionar"], borrar: ["producto:eliminar"] },
  {
    pantalla: "Categorías",
    ver: ["menu:productos.categorias"],
    editar: ["categoria:gestionar"],
    borrar: ["categoria:eliminar"],
  },
  {
    pantalla: "Esquemas de impuesto",
    ver: ["menu:esquemas_impuesto"],
    editar: ["esquema_impuesto:gestionar"],
    borrar: ["esquema_impuesto:eliminar"],
  },
  {
    pantalla: "Listas de precios",
    ver: ["menu:listas_precios"],
    editar: ["lista_precios:gestionar"],
    borrar: ["lista_precios:eliminar"],
  },
  {
    pantalla: "Inventario y almacenes",
    ver: ["menu:inventario"],
    editar: ["inventario:gestionar", "almacen:gestionar"],
    borrar: ["almacen:eliminar"],
  },
  {
    pantalla: "Compras y proveedores",
    ver: ["menu:compras"],
    editar: ["compra:gestionar", "proveedor:gestionar"],
    borrar: ["proveedor:eliminar"],
  },
  {
    pantalla: "Conversiones",
    ver: ["menu:conversiones"],
    editar: ["conversion:gestionar"],
    borrar: ["conversion:eliminar"],
  },
  { pantalla: "Series de folios", ver: ["menu:series"], editar: ["serie:gestionar"], borrar: ["serie:eliminar"] },
  { pantalla: "Ajustes · Empresa", ver: ["menu:ajustes.empresa"], editar: [], borrar: [] },
  { pantalla: "Ajustes · Facturación", ver: ["menu:ajustes.facturacion"], editar: [], borrar: [] },
  {
    pantalla: "Ajustes · Usuarios",
    ver: ["menu:ajustes.usuarios"],
    editar: ["membership:gestionar"],
    borrar: ["membership:eliminar"],
  },
  { pantalla: "Ajustes · Roles", ver: ["menu:ajustes.roles"], editar: ["role:gestionar"], borrar: ["role:eliminar"] },
];

/** Todos los permission ids referenciados por la matriz (para excluirlos de "avanzados"). */
const MATRIX_IDS = new Set(MATRIX.flatMap((r) => [...r.ver, ...r.editar, ...r.borrar]));

export default function RolesPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  const { post, patch, del, loading: saving } = useMutation();

  const rolesRes = useResource<Role[]>("/api/v1/roles");
  const permsRes = useResource<Permission[]>("/api/v1/permissions");
  const roles = rolesRes.data ?? [];
  const perms = permsRes.data ?? [];

  // La matriz solo ofrece permisos que EXISTEN en el catálogo del backend
  // (mandar un id desconocido rompe el guardado), y todo lo que la matriz no
  // cubre (POS, espejo, CRM…) se agrupa aparte para que siga siendo editable.
  const { matrixRows, advancedGroups } = useMemo(() => {
    const catalogIds = new Set(perms.map((p) => p.id));
    const matrixRows = MATRIX.map((r) => ({
      ...r,
      ver: r.ver.filter((id) => catalogIds.has(id)),
      editar: r.editar.filter((id) => catalogIds.has(id)),
      borrar: r.borrar.filter((id) => catalogIds.has(id)),
    }));
    const byRecurso: Record<string, Permission[]> = {};
    for (const p of perms) {
      if (MATRIX_IDS.has(p.id)) continue;
      (byRecurso[p.recurso] ??= []).push(p);
    }
    const advancedGroups = Object.entries(byRecurso)
      .map(([recurso, items]) => ({
        recurso,
        label: RECURSO_LABEL[recurso] ?? humanize(recurso),
        items: items.sort((a, b) => a.accion.localeCompare(b.accion)),
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
    return { matrixRows, advancedGroups };
  }, [perms]);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [advOpen, setAdvOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Role | null>(null);

  function openNew() {
    setEditing(null);
    setReadOnly(false);
    setNombre("");
    setDescripcion("");
    setSelected(new Set());
    setAdvOpen(false);
    setOpen(true);
  }

  async function openRole(role: Role) {
    setEditing(role);
    setReadOnly(role.es_preset || !canWrite);
    setNombre(role.nombre);
    setDescripcion(role.descripcion ?? "");
    setSelected(new Set());
    setAdvOpen(false);
    setOpen(true);
    setLoadingDetail(true);
    try {
      const detail = await apiFetch<RoleDetail>(`/api/v1/roles/${role.id}`);
      setSelected(new Set(detail.permissions));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cargar el rol");
    } finally {
      setLoadingDetail(false);
    }
  }

  function toggle(id: string) {
    if (readOnly) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Una celda de la matriz controla VARIOS ids a la vez. Se muestra marcada si
  // hay AL MENOS uno (un rol viejo con permisos parciales no debe verse "sin
  // nada"); al hacer clic se normaliza: con algo → quitar todos, sin nada →
  // poner todos.
  function toggleGroup(ids: string[]) {
    if (readOnly || ids.length === 0) return;
    setSelected((prev) => {
      const next = new Set(prev);
      const some = ids.some((id) => next.has(id));
      for (const id of ids) {
        if (some) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }

  async function save() {
    if (!nombre.trim()) {
      toast.error("El nombre del rol es obligatorio");
      return;
    }
    const payload = {
      nombre: nombre.trim(),
      descripcion: descripcion.trim() || null,
      permissions: [...selected],
    };
    try {
      if (editing) {
        await patch(`/api/v1/roles/${editing.id}`, payload);
        toast.success("Rol actualizado");
      } else {
        await post("/api/v1/roles", payload);
        toast.success("Rol creado");
      }
      setOpen(false);
      rolesRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    try {
      await del(`/api/v1/roles/${toDelete.id}`);
      toast.success("Rol eliminado");
      setToDelete(null);
      rolesRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const columns: Column<Role>[] = [
    {
      header: "Rol",
      cell: (r) => (
        <span className="flex items-center gap-2 font-medium">
          <Shield size={15} className="text-muted" />
          {r.nombre}
        </span>
      ),
    },
    {
      header: "Tipo",
      cell: (r) =>
        r.es_preset ? <Badge tone="muted">Predefinido</Badge> : <Badge tone="success">Personalizado</Badge>,
    },
    { header: "Descripción", cell: (r) => <span className="text-muted">{r.descripcion ?? "—"}</span> },
    {
      header: "",
      className: "text-right w-1",
      cell: (r) => (
        <div className="flex justify-end gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              openRole(r);
            }}
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
            aria-label={r.es_preset ? "Ver" : "Editar"}
          >
            {r.es_preset || !canWrite ? <Eye size={16} /> : <Pencil size={16} />}
          </button>
          {canWrite && !r.es_preset && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setToDelete(r);
              }}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
              aria-label="Eliminar"
            >
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
        title="Roles y permisos"
        subtitle="Define qué puede ver y hacer cada rol. Los roles predefinidos son de solo lectura."
        actions={
          canWrite ? (
            <Button onClick={openNew}>
              <Plus size={16} /> Nuevo rol
            </Button>
          ) : undefined
        }
      />

      <DataTableSmart
        columns={columns}
        rows={roles}
        loading={rolesRes.loading}
        error={rolesRes.error}
        empty="Sin roles"
        onRowClick={(r) => openRole(r)}
        storageKey="roles"
      />

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        wide
        title={
          editing ? (readOnly ? `Rol: ${editing.nombre}` : `Editar rol: ${editing.nombre}`) : "Nuevo rol"
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              {readOnly ? "Cerrar" : "Cancelar"}
            </Button>
            {!readOnly && (
              <Button onClick={save} disabled={saving || loadingDetail}>
                {saving ? "Guardando…" : "Guardar"}
              </Button>
            )}
          </>
        }
      >
        <div className="space-y-4">
          {readOnly && (
            <div className="flex items-center gap-2 rounded-md bg-surface-2 px-3 py-2 text-sm text-muted">
              <Lock size={14} /> Rol predefinido del sistema — solo lectura.
            </div>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Nombre" required>
              <Input value={nombre} onChange={(e) => setNombre(e.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Descripción">
              <Input
                value={descripcion}
                onChange={(e) => setDescripcion(e.target.value)}
                disabled={readOnly}
              />
            </Field>
          </div>

          {loadingDetail ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : (
            <>
              <PermSection
                title="Permisos por pantalla"
                hint="Ver abre la pantalla; Editar permite crear y modificar; Borrar permite eliminar"
              >
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs uppercase text-muted">
                      <tr>
                        <th className="px-2 py-1.5">Pantalla</th>
                        <th className="w-16 px-2 py-1.5 text-center">Ver</th>
                        <th className="w-16 px-2 py-1.5 text-center">Editar</th>
                        <th className="w-16 px-2 py-1.5 text-center">Borrar</th>
                      </tr>
                    </thead>
                    <tbody>
                      {matrixRows.map((r) => (
                        <tr key={r.pantalla} className="border-t border-border">
                          <td className="px-2 py-1.5">{r.pantalla}</td>
                          <MatrixCell ids={r.ver} selected={selected} disabled={readOnly} onToggle={toggleGroup} />
                          <MatrixCell ids={r.editar} selected={selected} disabled={readOnly} onToggle={toggleGroup} />
                          <MatrixCell
                            ids={r.borrar}
                            title={r.borrarTitle}
                            selected={selected}
                            disabled={readOnly}
                            onToggle={toggleGroup}
                          />
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PermSection>

              {/* Todo permiso del catálogo que la matriz no cubre (POS, espejo,
                  CRM…) sigue editable aquí; si no, quedaría huérfano. */}
              <div className="rounded-lg border border-border">
                <button
                  type="button"
                  onClick={() => setAdvOpen((v) => !v)}
                  className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm font-semibold hover:bg-surface-2"
                >
                  {advOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  Permisos avanzados
                  <span className="text-xs font-normal text-muted">
                    (POS y otros permisos que no aparecen en la tabla)
                  </span>
                </button>
                {advOpen && (
                  <div className="space-y-3 border-t border-border p-3">
                    {advancedGroups.length === 0 && (
                      <div className="text-sm text-muted">No hay permisos adicionales.</div>
                    )}
                    {advancedGroups.map((g) => (
                      <div key={g.recurso}>
                        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
                          {g.label}
                        </div>
                        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                          {g.items.map((p) => (
                            <PermCheck
                              key={p.id}
                              label={p.descripcion ?? humanize(p.accion)}
                              checked={selected.has(p.id)}
                              disabled={readOnly}
                              onChange={() => toggle(p.id)}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Eliminar rol"
        message={`¿Eliminar el rol "${toDelete?.nombre}"? Si está asignado a usuarios, reasígnalos primero.`}
        onConfirm={confirmDelete}
        onClose={() => setToDelete(null)}
        loading={saving}
      />
    </div>
  );
}

function PermSection({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-2">
        <div className="text-sm font-semibold">{title}</div>
        <div className="text-xs text-muted">{hint}</div>
      </div>
      {children}
    </div>
  );
}

/** Celda de la matriz: una casilla que controla un grupo de permission ids. */
function MatrixCell({
  ids,
  title,
  selected,
  disabled,
  onToggle,
}: {
  ids: string[];
  title?: string;
  selected: Set<string>;
  disabled: boolean;
  onToggle: (ids: string[]) => void;
}) {
  if (ids.length === 0) {
    return <td className="px-2 py-1.5 text-center text-muted">—</td>;
  }
  return (
    <td className="px-2 py-1.5 text-center" title={title}>
      <Checkbox
        checked={ids.some((id) => selected.has(id))}
        disabled={disabled}
        onChange={() => onToggle(ids)}
      />
    </td>
  );
}

function PermCheck({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <label
      className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
        disabled ? "opacity-70" : "cursor-pointer hover:bg-surface-2"
      }`}
    >
      <Checkbox checked={checked} disabled={disabled} onChange={onChange} />
      <span>{label}</span>
    </label>
  );
}

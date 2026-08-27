"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { Check, Pencil, Plus, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource, type Page } from "@/lib/hooks";

export type FormValues = Record<string, string | boolean>;

export type CrudField = {
  name: string;
  label: string;
  type?: "text" | "number" | "decimal" | "textarea" | "switch" | "select";
  required?: boolean;
  hint?: string;
  step?: string;
  placeholder?: string;
  options?: { value: string; label: string }[];
  colSpan?: 1 | 2;
  /** Campo no editable (se muestra deshabilitado). */
  readOnly?: boolean;
  /** Alias de `readOnly`: campo no editable (lo rellena el servidor). */
  readonly?: boolean;
  /** Valor derivado de otros campos; se recalcula al cambiar el formulario. */
  derive?: (form: FormValues) => string;
  /**
   * Botón de acción secundario junto al input. Al pulsarlo se ejecuta `run`
   * con el valor actual del campo y el formulario completo (por si la acción
   * necesita otros campos, p. ej. validar RFC+Nombre+CP+Régimen juntos); el
   * mensaje devuelto se muestra con toast.success y los errores con
   * toast.error. `watch` son nombres de otros campos cuyo cambio también
   * invalida el estado "Verificado" (por defecto solo se vigila este campo).
   */
  action?: {
    label: string;
    run: (value: string, form: FormValues) => Promise<{ ok: boolean; message: string }>;
    watch?: string[];
  };
  /**
   * Validación LOCAL del campo (sin red): devuelve el motivo del error o null
   * si está bien. Se muestra en rojo bajo el campo mientras se escribe y
   * BLOQUEA el guardado — así un dato fiscal mal capturado (p. ej. un RFC que
   * no pasa el dígito verificador) nunca llega a la base ni al timbrado.
   */
  validate?: (value: string, form: FormValues) => string | null;
  /**
   * Permite CREAR el catálogo desde el propio select, sin salir del formulario
   * (menos pasos: dar de alta un cliente ya no obliga a ir antes a Series o a
   * Listas de precios). `run` recibe lo capturado en el mini-formulario y
   * devuelve el id a seleccionar; `refreshes` son los campos cuyos lookups hay
   * que recargar después (p. ej. crear el par de series refresca los dos).
   */
  createInline?: {
    label: string;
    /** Permiso del catálogo que se crea (es OTRO recurso, con otro permiso que
     *  el de la pantalla). Sin él, el botón no se muestra: evita ofrecer una
     *  acción que el backend va a rechazar con 403. */
    perm?: string;
    title: string;
    fields: { name: string; label: string; placeholder?: string; hint?: string; required?: boolean }[];
    run: (values: Record<string, string>) => Promise<{ id: string; extra?: Record<string, string> }>;
    refreshes?: string[];
  };
};

/** A select whose options come from another list endpoint. */
export type Lookup = {
  path: string;
  value: (row: Record<string, unknown>) => string;
  label: (row: Record<string, unknown>) => string;
};

export type CrudConfig<T> = {
  title: string;
  subtitle?: string;
  /** Texto del botón de crear (p. ej. "Nueva categoría"). */
  newLabel?: string;
  basePath: string;
  writePerm: string;
  searchable?: boolean;
  deletable?: boolean;
  columns: Column<T>[];
  fields: CrudField[];
  newValues: () => FormValues;
  toForm: (row: T) => FormValues;
  toPayload: (v: FormValues) => Record<string, unknown>;
  rowLabel: (row: T) => string;
  lookups?: Record<string, Lookup>;
  /**
   * Accesos por fila que se pintan como ICONOS junto a editar/eliminar (en vez
   * de enlaces de texto, que ensanchan la tabla). `title` es el tooltip y el
   * texto para lectores de pantalla.
   */
  rowLinks?: (row: T) => { href: string; title: string; icon: ReactNode }[];
  /** Advertencia opcional al eliminar (impacto + alternativa). Si devuelve texto,
   * se muestra en el diálogo de confirmación antes de borrar. */
  deleteWarning?: (row: T) => Promise<string | null>;
  /** Modal de alta/edición más ancho — para formularios con muchos campos o
   * selects con etiquetas largas (p. ej. catálogos SAT) que se truncan. El
   * modal también se puede agrandar a mano desde su esquina inferior derecha. */
  wide?: boolean;
};

const LIMIT = 20;

export function CrudPage<T extends { id: string }>({ config }: { config: CrudConfig<T> }) {
  const { me } = useAuth();
  const toast = useToast();
  const { post, patch, del, loading: saving } = useMutation();
  const canWrite = can(me, config.writePerm);

  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => {
      setDq(q);
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const listPath = useMemo(() => {
    const p = new URLSearchParams();
    p.set("limit", String(LIMIT));
    p.set("offset", String(page * LIMIT));
    if (config.searchable && dq.trim()) p.set("q", dq.trim());
    return `${config.basePath}?${p.toString()}`;
  }, [config.basePath, config.searchable, page, dq]);

  const { data, loading, error, reload } = useResource<Page<T>>(listPath);
  const rows = data?.items ?? [];
  const total = data?.total ?? 0;

  const [lookupOpts, setLookupOpts] = useState<Record<string, { value: string; label: string }[]>>({});
  const lookupsLoaded = useRef(false);

  // Campo cuyo catálogo se está creando desde el propio formulario (mini-modal).
  const [inlineFor, setInlineFor] = useState<CrudField | null>(null);
  const puedeCrearInline = (f: CrudField) =>
    !f.createInline?.perm || can(me, f.createInline.perm);

  const [deleteWarn, setDeleteWarn] = useState<string | null>(null);
  function askDelete(row: T) {
    setDeleteWarn(null);
    setToDelete(row);
    config.deleteWarning?.(row).then(setDeleteWarn).catch(() => setDeleteWarn(null));
  }

  const [form, setForm] = useState<FormValues | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<T | null>(null);

  // Lookups de los selects del formulario: se cargan la primera vez que se abre
  // el formulario, no al montar la página (ahorra peticiones en la carga inicial).
  useEffect(() => {
    if (!config.lookups || form === null || lookupsLoaded.current) return;
    lookupsLoaded.current = true;
    let active = true;
    (async () => {
      const out: Record<string, { value: string; label: string }[]> = {};
      for (const [field, lk] of Object.entries(config.lookups!)) {
        try {
          const pageData = await apiFetch<Page<Record<string, unknown>>>(lk.path);
          out[field] = pageData.items.map((r) => ({ value: lk.value(r), label: lk.label(r) }));
        } catch {
          out[field] = [];
        }
      }
      // Merge conservando lo ya presente: si una creación inline refrescó un
      // lookup mientras esta carga inicial seguía en vuelo, no se pisa con la
      // foto vieja (el id recién creado quedaría seleccionado pero invisible).
      if (active) setLookupOpts((prev) => ({ ...out, ...prev }));
    })();
    return () => {
      active = false;
    };
  }, [config.lookups, form]);

  // Recarga los lookups indicados (tras crear un catálogo desde el formulario).
  async function refrescarLookups(campos: string[]) {
    if (!config.lookups) return;
    const out: Record<string, { value: string; label: string }[]> = {};
    for (const campo of campos) {
      const lk = config.lookups[campo];
      if (!lk) continue;
      try {
        const pageData = await apiFetch<Page<Record<string, unknown>>>(lk.path);
        out[campo] = pageData.items.map((r) => ({ value: lk.value(r), label: lk.label(r) }));
      } catch {
        /* se conserva lo que ya había */
      }
    }
    setLookupOpts((prev) => ({ ...prev, ...out }));
  }

  function openCreate() {
    setEditingId(null);
    setForm(config.newValues());
  }
  function openEdit(row: T) {
    setEditingId(row.id);
    setForm(config.toForm(row));
  }
  function setField(name: string, value: string | boolean) {
    setForm((f) => {
      if (!f) return f;
      const next = { ...f, [name]: value };
      for (const fld of config.fields) {
        if (fld.derive) next[fld.name] = fld.derive(next);
      }
      return next;
    });
  }

  async function save() {
    if (!form) return;
    for (const f of config.fields) {
      const isReadonly = f.readonly || f.readOnly;
      const v = form[f.name];
      if (f.required && !isReadonly && typeof v === "string" && !v.trim()) {
        toast.error(`${f.label} es obligatorio`);
        return;
      }
      // Validación local del campo: bloquea el guardado con el motivo exacto.
      if (f.validate && !isReadonly) {
        const motivo = f.validate(String(v ?? ""), form);
        if (motivo) {
          toast.error(`${f.label}: ${motivo}`);
          return;
        }
      }
    }
    try {
      const payload = config.toPayload(form);
      if (editingId) {
        await patch(`${config.basePath}/${editingId}`, payload);
        toast.success("Guardado");
      } else {
        await post(config.basePath, payload);
        toast.success("Creado");
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
      await del(`${config.basePath}/${toDelete.id}`);
      toast.success("Eliminado");
      setToDelete(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const actionsCol: Column<T> = {
    header: "",
    className: "text-right w-1",
    cell: (row) => (
      <div className="flex justify-end gap-1">
        {canWrite && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              openEdit(row);
            }}
            aria-label="Editar"
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
          >
            <Pencil size={16} />
          </button>
        )}
        {config.rowLinks?.(row).map((l) => (
          <Link
            key={l.href}
            href={l.href}
            onClick={(e) => e.stopPropagation()}
            title={l.title}
            aria-label={l.title}
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
          >
            {l.icon}
          </Link>
        ))}
        {canWrite && config.deletable !== false && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              askDelete(row);
            }}
            aria-label="Eliminar"
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
          >
            <Trash2 size={16} />
          </button>
        )}
      </div>
    ),
  };
  const columns =
    canWrite || config.rowLinks ? [...config.columns, actionsCol] : config.columns;

  const from = total === 0 ? 0 : page * LIMIT + 1;
  const to = Math.min((page + 1) * LIMIT, total);
  const lower = config.title.toLowerCase();

  return (
    <div>
      <PageHeader
        title={config.title}
        subtitle={config.subtitle}
        actions={
          canWrite ? (
            <Button onClick={openCreate}>
              <Plus size={16} /> {config.newLabel ?? `Nuevo ${config.title.toLowerCase()}`}
            </Button>
          ) : undefined
        }
      />

      {config.searchable && (
        <div className="mb-4">
          <Input placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        </div>
      )}

      <DataTableSmart columns={columns} rows={rows} loading={loading} error={error} empty="Sin resultados" storageKey={`crud-${config.basePath}`} />

      <div className="mt-4 flex items-center justify-between text-sm text-muted">
        <span>
          {from}–{to} de {total}
        </span>
        <div className="flex gap-2">
          <Button variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Anterior
          </Button>
          <Button variant="secondary" disabled={to >= total} onClick={() => setPage((p) => p + 1)}>
            Siguiente
          </Button>
        </div>
      </div>

      <Modal
        open={form !== null}
        onClose={() => setForm(null)}
        title={editingId ? `Editar ${lower}` : `Nuevo ${lower}`}
        wide={config.wide}
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
            {config.fields.map((f) => {
              const val = form[f.name];
              const opts = f.options ?? lookupOpts[f.name] ?? [];
              const cls = f.colSpan === 2 ? "sm:col-span-2" : "";
              if (f.type === "switch") {
                return (
                  <div key={f.name} className={`flex items-center gap-3 pt-6 ${cls}`}>
                    <Switch checked={Boolean(val)} onChange={(v) => setField(f.name, v)} />
                    <span className="text-sm">{f.label}</span>
                  </div>
                );
              }
              const isReadonly = f.readonly || f.readOnly;
              const errorCampo = f.validate ? f.validate(String(val ?? ""), form) : null;
              return (
                <div key={f.name} className={cls}>
                  <Field label={f.label} required={f.required} hint={f.hint}>
                    {f.type === "textarea" ? (
                      <Textarea rows={2} value={String(val ?? "")} onChange={(e) => setField(f.name, e.target.value)} />
                    ) : f.type === "select" ? (
                      <Select value={String(val ?? "")} onChange={(e) => setField(f.name, e.target.value)}>
                        <option value="">— Selecciona —</option>
                        {opts.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </Select>
                    ) : (
                      // `decimal` usa un input de texto (no el nativo `number`,
                      // que muestra el separador decimal según el locale del SO
                      // y acaba pintando "0,0000"). Aquí siempre se usa punto.
                      <div className={f.action ? "flex items-center gap-2" : undefined}>
                        <Input
                          type={f.type === "number" ? "number" : "text"}
                          inputMode={f.type === "decimal" ? "decimal" : undefined}
                          step={f.step}
                          placeholder={f.placeholder}
                          value={String(val ?? "")}
                          onChange={(e) =>
                            setField(
                              f.name,
                              f.type === "decimal" ? e.target.value.replace(",", ".") : e.target.value,
                            )
                          }
                          disabled={isReadonly}
                          readOnly={isReadonly}
                        />
                        {f.action && <FieldActionButton action={f.action} value={String(val ?? "")} form={form} />}
                      </div>
                    )}
                  </Field>
                  {errorCampo && (
                    <span className="mt-1 block text-xs text-danger">{errorCampo}</span>
                  )}
                  {f.createInline && puedeCrearInline(f) && (
                    <button
                      type="button"
                      onClick={() => setInlineFor(f)}
                      className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
                    >
                      <Plus size={13} aria-hidden="true" /> {f.createInline.label}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Modal>

      {inlineFor?.createInline && (
        <CrearInlineModal
          key={inlineFor.name}
          field={inlineFor}
          onClose={() => setInlineFor(null)}
          onCreated={async (id, extra) => {
            // Un id vacío significa que la respuesta del backend no trajo lo
            // esperado: no se limpia el campo en silencio.
            if (!id) {
              toast.error("Se creó, pero no se pudo seleccionar automáticamente. Elígelo en la lista.");
              await refrescarLookups(inlineFor.createInline?.refreshes ?? [inlineFor.name]);
              setInlineFor(null);
              return;
            }
            setField(inlineFor.name, id);
            // Campos extra que el catálogo nuevo también rellena (p. ej. el par
            // de series devuelve la de factura y la de remisión).
            for (const [campo, valor] of Object.entries(extra ?? {})) setField(campo, valor);
            const refrescar = inlineFor.createInline?.refreshes ?? [inlineFor.name];
            await refrescarLookups(refrescar);
            setInlineFor(null);
          }}
        />
      )}

      <ConfirmDialog
        open={toDelete !== null}
        title={`Eliminar ${lower}`}
        message={
          deleteWarn
            ? `⚠️ ${deleteWarn}`
            : `¿Eliminar "${toDelete ? config.rowLabel(toDelete) : ""}"? Esta acción no se puede deshacer fácilmente.`
        }
        confirmLabel={deleteWarn ? "Eliminar de todas formas" : "Eliminar"}
        onConfirm={confirmDelete}
        onClose={() => { setToDelete(null); setDeleteWarn(null); }}
        loading={saving}
      />
    </div>
  );
}

/** Botón de acción secundario asociado a un campo (p. ej. "Verificar RFC"). */
function FieldActionButton({
  action,
  value,
  form,
}: {
  action: {
    label: string;
    run: (value: string, form: FormValues) => Promise<{ ok: boolean; message: string }>;
    watch?: string[];
  };
  value: string;
  form: FormValues;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  // Huella (este campo + los de `watch`) contra la que se verificó con éxito.
  // Si cualquiera cambia después, se limpia "Verificado" para no mostrar un
  // resultado obsoleto (p. ej. el RFC quedó igual pero el CP cambió).
  const fingerprint = [value, ...(action.watch ?? []).map((k) => String(form[k] ?? ""))].join(" ");
  const [verifiedFingerprint, setVerifiedFingerprint] = useState<string | null>(null);
  // Huella con la que la verificación FALLÓ: pinta el botón en rojo hasta que
  // el usuario corrija el dato (antes solo salía un aviso y el botón quedaba
  // igual, así que el rechazo pasaba desapercibido).
  const [failedFingerprint, setFailedFingerprint] = useState<string | null>(null);

  useEffect(() => {
    if (verifiedFingerprint !== null && fingerprint !== verifiedFingerprint) setVerifiedFingerprint(null);
    if (failedFingerprint !== null && fingerprint !== failedFingerprint) setFailedFingerprint(null);
  }, [fingerprint, verifiedFingerprint, failedFingerprint]);

  async function run() {
    setBusy(true);
    try {
      const { ok, message } = await action.run(value, form);
      if (ok) {
        toast.success(message);
        setVerifiedFingerprint(fingerprint);
        setFailedFingerprint(null);
      } else {
        toast.error(message);
        setVerifiedFingerprint(null);
        setFailedFingerprint(fingerprint);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "La acción falló");
      setVerifiedFingerprint(null);
      setFailedFingerprint(fingerprint);
    } finally {
      setBusy(false);
    }
  }

  const verified = verifiedFingerprint !== null && verifiedFingerprint === fingerprint;
  const failed = failedFingerprint !== null && failedFingerprint === fingerprint;

  return (
    <Button
      type="button"
      variant="secondary"
      onClick={run}
      disabled={busy || !value.trim()}
      className={`shrink-0 whitespace-nowrap ${
        verified
          ? "border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-600 hover:opacity-90"
          : failed
          ? "border-red-600 bg-red-600 text-white hover:bg-red-600 hover:opacity-90"
          : ""
      }`}
    >
      {busy ? "…" : verified ? (
        <>
          <Check size={16} /> Verificado
        </>
      ) : failed ? (
        <>
          <X size={16} /> No válido
        </>
      ) : (
        action.label
      )}
    </Button>
  );
}

/** Mini-formulario para crear un catálogo (serie, lista de precios…) sin salir
 *  del alta que se está capturando. */
function CrearInlineModal({
  field,
  onClose,
  onCreated,
}: {
  field: CrudField;
  onClose: () => void;
  onCreated: (id: string, extra?: Record<string, string>) => void | Promise<void>;
}) {
  const spec = field.createInline!;
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(spec.fields.map((f) => [f.name, ""])),
  );
  const [busy, setBusy] = useState(false);

  async function crear() {
    for (const f of spec.fields) {
      if (f.required && !values[f.name]?.trim()) {
        toast.error(`${f.label} es obligatorio`);
        return;
      }
    }
    setBusy(true);
    let creado: { id: string; extra?: Record<string, string> };
    try {
      creado = await spec.run(values);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear");
      setBusy(false);
      return;
    }
    // Ya está creado en el servidor: el post-proceso (seleccionarlo, refrescar
    // el catálogo) no debe reportarse como si la creación hubiera fallado, ni
    // dejar el mini-modal abierto invitando a crearlo dos veces.
    toast.success(`${spec.title} creada`);
    try {
      await onCreated(creado.id, creado.extra);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={spec.title}
      resizable={false}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button onClick={crear} disabled={busy}>
            {busy ? "Creando…" : "Crear"}
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-4">
        {spec.fields.map((f) => (
          <Field key={f.name} label={f.label} required={f.required} hint={f.hint}>
            <Input
              placeholder={f.placeholder}
              value={values[f.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
            />
          </Field>
        ))}
      </div>
    </Modal>
  );
}

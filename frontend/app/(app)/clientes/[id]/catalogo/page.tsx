"use client";

// Catálogo del cliente: cómo llama ESTE cliente a cada producto. Su código sale
// como NoIdentificacion y su nombre como Descripción en sus CFDI — un solo
// producto interno, sin duplicados. Se alimenta desde aquí o importando su
// lista de precios en Productos → Importar.
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type { Page } from "@/lib/hooks";
import type { Cliente, ProductoClienteRow, Sucursal } from "@/lib/types";

const WRITE = "cliente:gestionar";

type FormState = {
  producto_id: string;
  producto_nombre: string;
  codigo_cliente: string;
  nombre_cliente: string;
  // "" = fila genérica (todas las plazas); un id = la clave de ESA plaza.
  // Identifica la fila junto con producto_id: un producto puede tener la
  // genérica Y una por sucursal (claves SAE distintas por plaza, caso EHMO).
  sucursal_id: string;
  esNuevo: boolean;
};

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [rows, setRows] = useState<ProductoClienteRow[] | null>(null);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  const [error, setError] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);
  const [toDelete, setToDelete] = useState<ProductoClienteRow | null>(null);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(() => {
    apiFetch<ProductoClienteRow[]>(`/api/v1/clientes/${id}/catalogo`)
      .then(setRows)
      .catch(() => setError(true));
  }, [id]);

  useEffect(() => {
    apiFetch<Cliente>(`/api/v1/clientes/${id}`).then(setCliente).catch(() => setError(true));
    // Las plazas del cliente, para capturar una clave por sucursal (EHMO).
    apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${id}&limit=200`)
      .then((p) => setSucursales(p.items))
      .catch(() => setSucursales([]));
    reload();
  }, [id, reload]);

  async function save() {
    if (!form) return;
    if (!form.producto_id) {
      toast.error("Elige el producto del catálogo");
      return;
    }
    if (!form.codigo_cliente.trim() && !form.nombre_cliente.trim()) {
      toast.error("Captura el código y/o el nombre que usa el cliente");
      return;
    }
    setSaving(true);
    try {
      await apiFetch(`/api/v1/clientes/${id}/catalogo/${form.producto_id}`, {
        method: "PUT",
        body: JSON.stringify({
          codigo_cliente: form.codigo_cliente.trim() || null,
          nombre_cliente: form.nombre_cliente.trim() || null,
          sucursal_id: form.sucursal_id || null,
        }),
      });
      toast.success("Guardado");
      setForm(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    try {
      // La sucursal identifica la fila: borrar la de una plaza no debe
      // arrastrar la genérica (ni al revés).
      const qs = toDelete.sucursal_id ? `?sucursal_id=${toDelete.sucursal_id}` : "";
      await apiFetch(`/api/v1/clientes/${id}/catalogo/${toDelete.producto_id}${qs}`, {
        method: "DELETE",
      });
      toast.success("Eliminado — sus facturas usarán el nombre y SKU internos");
      setToDelete(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const columns: Column<ProductoClienteRow>[] = [
    { header: "Producto interno", cell: (r) => (
      <div>
        <div className="font-medium">{r.producto_nombre}</div>
        <div className="text-xs text-muted">SKU {r.producto_sku}</div>
      </div>
    ) },
    { header: "Código del cliente", cell: (r) => r.codigo_cliente ?? "—" },
    { header: "Nombre del cliente", cell: (r) => r.nombre_cliente ?? "—" },
    {
      header: "Sucursal",
      cell: (r) =>
        r.sucursal_id ? (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">
            {r.sucursal_nombre ?? "plaza"}
          </span>
        ) : (
          <span className="text-xs text-muted">Todas</span>
        ),
    },
    {
      header: "",
      className: "text-right w-1",
      cell: (r) =>
        canWrite ? (
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
        ) : null,
    },
  ];

  if (error) return <Alert tone="danger">No se pudo cargar el catálogo del cliente.</Alert>;
  if (!cliente || rows === null)
    return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title={`Catálogo de ${cliente.legal_name}`}
        subtitle="Su código sale como NoIdentificacion y su nombre como Descripción en sus facturas"
        actions={
          <div className="flex items-center gap-2">
            <Link
              href="/clientes"
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-surface-2"
            >
              <ArrowLeft size={16} /> Clientes
            </Link>
            {canWrite ? (
              <Button
                onClick={() =>
                  setForm({
                    producto_id: "",
                    producto_nombre: "",
                    codigo_cliente: "",
                    nombre_cliente: "",
                    sucursal_id: "",
                    esNuevo: true,
                  })
                }
              >
                <Plus size={16} /> Agregar producto
              </Button>
            ) : null}
          </div>
        }
      />

      <DataTable
        columns={columns}
        rows={rows}
        empty="Este cliente aún no tiene códigos ni nombres propios — sus facturas usan el nombre y SKU internos. Impórtalos desde Productos → Importar con su lista de precios."
        onRowClick={
          canWrite
            ? (r) =>
                setForm({
                  producto_id: r.producto_id,
                  producto_nombre: `${r.producto_nombre} (${r.producto_sku})`,
                  codigo_cliente: r.codigo_cliente ?? "",
                  nombre_cliente: r.nombre_cliente ?? "",
                  sucursal_id: r.sucursal_id ?? "",
                  esNuevo: false,
                })
            : undefined
        }
      />

      <Modal
        open={form !== null}
        onClose={() => setForm(null)}
        title={form?.esNuevo ? "Agregar producto al catálogo del cliente" : "Editar código/nombre del cliente"}
        resizable={false}
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>Cancelar</Button>
            <Button onClick={save} disabled={saving}>
              {saving ? "Guardando…" : "Guardar"}
            </Button>
          </>
        }
      >
        {form ? (
          <div className="space-y-4">
            {form.esNuevo ? (
              <Field label="Producto del catálogo">
                {form.producto_id ? (
                  <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm">
                    <span>{form.producto_nombre}</span>
                    <button
                      className="text-xs text-accent hover:underline"
                      onClick={() => setForm({ ...form, producto_id: "", producto_nombre: "" })}
                    >
                      Cambiar
                    </button>
                  </div>
                ) : (
                  <ProductoCombobox
                    autoFocus
                    onSelect={(p) => {
                      if (p)
                        setForm((f) =>
                          f
                            ? { ...f, producto_id: p.producto_id, producto_nombre: `${p.nombre} (${p.sku})` }
                            : f
                        );
                    }}
                  />
                )}
              </Field>
            ) : (
              <Field label="Producto interno">
                <Input value={form.producto_nombre} disabled />
              </Field>
            )}
            <Field
              label="Código del cliente"
              hint="Sale como NoIdentificacion en el XML (vacío = SKU interno)"
            >
              <Input
                value={form.codigo_cliente}
                onChange={(e) => setForm({ ...form, codigo_cliente: e.target.value })}
                placeholder="JIT-SAD-001"
              />
            </Field>
            <Field
              label="Nombre que usa el cliente"
              hint="Sale como Descripción en el XML (vacío = nombre interno)"
            >
              <Input
                value={form.nombre_cliente}
                onChange={(e) => setForm({ ...form, nombre_cliente: e.target.value })}
                placeholder="JITOMATE ROMA"
              />
            </Field>
            <Field
              label="Sucursal"
              hint="Solo si esta plaza usa una clave distinta a la general — la exportación a SAE usa la de la plaza y cae a la general"
            >
              {form.esNuevo ? (
                <select
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
                  value={form.sucursal_id}
                  onChange={(e) => setForm({ ...form, sucursal_id: e.target.value })}
                >
                  <option value="">Todas (clave general)</option>
                  {sucursales.map((s) => (
                    <option key={s.id} value={s.id}>{s.nombre}</option>
                  ))}
                </select>
              ) : (
                // La sucursal identifica la fila: cambiarla sería otra fila,
                // no una edición — se crea desde "Agregar producto".
                <Input
                  value={
                    form.sucursal_id
                      ? sucursales.find((s) => s.id === form.sucursal_id)?.nombre ?? "Plaza"
                      : "Todas (clave general)"
                  }
                  disabled
                />
              )}
            </Field>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Quitar del catálogo del cliente"
        message={
          toDelete?.sucursal_id
            ? `¿Quitar la clave de "${toDelete?.producto_nombre}" para ${toDelete?.sucursal_nombre ?? "esa plaza"}? La plaza volverá a usar la clave general del cliente.`
            : `¿Quitar "${toDelete?.producto_nombre}"? Sus facturas volverán a usar el nombre y SKU internos.`
        }
        onClose={() => setToDelete(null)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}

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
import type { Cliente, ProductoClienteRow } from "@/lib/types";

const WRITE = "cliente:gestionar";

type FormState = {
  producto_id: string;
  producto_nombre: string;
  codigo_cliente: string;
  nombre_cliente: string;
  esNuevo: boolean;
};

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [rows, setRows] = useState<ProductoClienteRow[] | null>(null);
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
      await apiFetch(`/api/v1/clientes/${id}/catalogo/${toDelete.producto_id}`, {
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
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Quitar del catálogo del cliente"
        message={`¿Quitar "${toDelete?.producto_nombre}"? Sus facturas volverán a usar el nombre y SKU internos.`}
        onClose={() => setToDelete(null)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}

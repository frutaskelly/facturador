"use client";

import { useMemo, useState } from "react";
import { PackagePlus } from "lucide-react";

import { ProductoCombobox } from "@/components/ProductoCombobox";
import { Button } from "@/components/ui/Button";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtMoney, fmtNumber } from "@/lib/format";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import type { Almacen, ExistenciaRow, Producto } from "@/lib/types";

const READ = "menu:inventario";
const WRITE = "inventario:gestionar";

const TIPOS = [
  { value: "ENTRADA_COMPRA", label: "Entrada (compra)" },
  { value: "AJUSTE", label: "Ajuste" },
  { value: "MERMA", label: "Merma" },
  { value: "TRANSFERENCIA", label: "Transferencia" },
];
const MERMA_MOTIVOS = ["CADUCIDAD", "CALIDAD", "DEVOLUCION_CLIENTE", "ROBO", "DESCOMPOSICION", "OTRO"];

type MovForm = {
  tipo: string;
  producto_id: string;
  almacen_id: string;
  cantidad: string;
  costo_unitario: string;
  numero_lote: string;
  almacen_destino_id: string;
  merma_motivo: string;
  motivo: string;
};

function emptyMov(): MovForm {
  return {
    tipo: "ENTRADA_COMPRA",
    producto_id: "",
    almacen_id: "",
    cantidad: "",
    costo_unitario: "",
    numero_lote: "",
    almacen_destino_id: "",
    merma_motivo: "CADUCIDAD",
    motivo: "",
  };
}

export default function InventarioPage() {
  const { me } = useAuth();
  const toast = useToast();
  const { post, loading: saving } = useMutation();
  const canWrite = can(me, WRITE);

  const productosRes = useResource<Page<Producto>>("/api/v1/productos?limit=500");
  const almacenesRes = useResource<Page<Almacen>>("/api/v1/almacenes?limit=200");
  const productos = productosRes.data?.items ?? [];
  const almacenes = almacenesRes.data?.items ?? [];

  const prodName = useMemo(
    () => Object.fromEntries(productos.map((p) => [p.id, p.nombre])),
    [productos]
  );
  const almName = useMemo(
    () => Object.fromEntries(almacenes.map((a) => [a.id, a.nombre])),
    [almacenes]
  );

  const { data: existencias, loading, error, reload } = useResource<ExistenciaRow[]>(
    "/api/v1/inventario/existencias"
  );
  const rows = existencias ?? [];

  const [form, setForm] = useState<MovForm | null>(null);
  function set<K extends keyof MovForm>(k: K, v: string) {
    setForm((f) => (f ? { ...f, [k]: v } : f));
  }

  async function save() {
    if (!form) return;
    if (!form.producto_id || !form.almacen_id || !form.cantidad) {
      toast.error("Producto, almacén y cantidad son obligatorios");
      return;
    }
    const payload: Record<string, unknown> = {
      tipo: form.tipo,
      producto_id: form.producto_id,
      almacen_id: form.almacen_id,
      cantidad: form.cantidad,
      motivo: form.motivo || null,
    };
    if (form.tipo === "ENTRADA_COMPRA") payload.costo_unitario = form.costo_unitario || "0";
    // AJUSTE positivo: costo opcional; si va vacío, el backend usa el último
    // costo de compra del producto (decisión 2026-07-29 #9 — nada entra a $0).
    if (form.tipo === "AJUSTE" && form.costo_unitario) payload.costo_unitario = form.costo_unitario;
    if (form.numero_lote) payload.numero_lote = form.numero_lote;
    if (form.tipo === "TRANSFERENCIA") payload.almacen_destino_id = form.almacen_destino_id || null;
    if (form.tipo === "MERMA") payload.merma_motivo = form.merma_motivo;
    try {
      await post("/api/v1/inventario/movimientos", payload);
      toast.success("Movimiento registrado");
      setForm(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo registrar");
    }
  }

  const columns: Column<ExistenciaRow>[] = [
    {
      header: "Producto",
      truncate: true,
      cell: (r) => (
        <span className="font-medium">
          {r.producto_nombre ?? prodName[r.producto_id] ?? r.producto_id}
        </span>
      ),
    },
    { header: "Almacén", cell: (r) => r.almacen_nombre ?? almName[r.almacen_id] ?? r.almacen_id },
    { header: "Disponible", cell: (r) => fmtNumber(r.disponible), className: "text-right" },
    // "Reservada" ya no se muestra: confirmar = salida directa (decisión
    // 2026-07-29 #2); la cubeta quedó siempre en 0.
    { header: "Costo prom.", cell: (r) => fmtMoney(r.costo_promedio), className: "text-right" },
    { header: "Valor", cell: (r) => fmtMoney(r.valor), className: "text-right" },
  ];

  const totalValor = rows.reduce((s, r) => s + Number(r.valor), 0);

  return (
    <div>
      <PageHeader
        title="Inventario"
        subtitle="Existencias por producto y almacén"
        actions={
          canWrite ? (
            <Button onClick={() => setForm(emptyMov())}>
              <PackagePlus size={16} /> Registrar movimiento
            </Button>
          ) : undefined
        }
      />

      <DataTableSmart columns={columns} rows={rows} loading={loading} error={error} empty="Sin existencias" storageKey="inventario" />

      {rows.length > 0 && (
        <div className="mt-3 text-right text-sm text-muted">
          Valor total del inventario: <span className="font-semibold text-foreground">{fmtMoney(totalValor)}</span>
        </div>
      )}

      <Modal
        open={form !== null}
        onClose={() => setForm(null)}
        title="Registrar movimiento"
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Cancelar
            </Button>
            <Button onClick={save} disabled={saving}>
              {saving ? "Registrando…" : "Registrar"}
            </Button>
          </>
        }
      >
        {form && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Tipo" required>
              <Select value={form.tipo} onChange={(e) => set("tipo", e.target.value)}>
                {TIPOS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Cantidad" required hint={form.tipo === "AJUSTE" ? "puede ser negativa" : undefined}>
              <Input type="number" step="0.0001" value={form.cantidad} onChange={(e) => set("cantidad", e.target.value)} />
            </Field>
            <Field label="Producto" required>
              <ProductoCombobox
                placeholder="Buscar producto por nombre o SKU…"
                onSelect={(p) => set("producto_id", p ? p.producto_id : "")}
              />
            </Field>
            <Field label={form.tipo === "TRANSFERENCIA" ? "Almacén origen" : "Almacén"} required>
              <Select value={form.almacen_id} onChange={(e) => set("almacen_id", e.target.value)}>
                <option value="">— Selecciona —</option>
                {almacenes.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.nombre}
                  </option>
                ))}
              </Select>
            </Field>
            {form.tipo === "ENTRADA_COMPRA" && (
              <Field label="Costo unitario" required>
                <Input type="number" step="0.0001" value={form.costo_unitario} onChange={(e) => set("costo_unitario", e.target.value)} />
              </Field>
            )}
            {form.tipo === "AJUSTE" && (
              <Field label="Costo unitario" hint="Opcional: si se deja vacío se usa el último costo de compra del producto">
                <Input type="number" step="0.0001" value={form.costo_unitario} onChange={(e) => set("costo_unitario", e.target.value)} />
              </Field>
            )}
            {form.tipo === "TRANSFERENCIA" && (
              <Field label="Almacén destino" required>
                <Select value={form.almacen_destino_id} onChange={(e) => set("almacen_destino_id", e.target.value)}>
                  <option value="">— Selecciona —</option>
                  {almacenes.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.nombre}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {form.tipo === "MERMA" && (
              <Field label="Motivo de merma" required>
                <Select value={form.merma_motivo} onChange={(e) => set("merma_motivo", e.target.value)}>
                  {MERMA_MOTIVOS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <Field label="Lote (opcional)">
              <Input value={form.numero_lote} onChange={(e) => set("numero_lote", e.target.value)} />
            </Field>
            <div className="sm:col-span-2">
              <Field label="Nota (opcional)">
                <Input value={form.motivo} onChange={(e) => set("motivo", e.target.value)} />
              </Field>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

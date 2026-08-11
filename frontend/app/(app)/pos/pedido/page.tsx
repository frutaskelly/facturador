"use client";

// POS · Estación PEDIDO (Fase 1) — captura rápida de mostrador. Reusa el
// buscador con cruce IA (ProductoCombobox) y el preview fiscal del servidor,
// igual que remisiones/factura directa, con BORRADORES multi-pestaña (patrón v1):
// varios pedidos abiertos a la vez. Guardar → crea la remisión y la mete al
// pipeline del POS (POST /remisiones + /pos/remisiones/{id}/iniciar).
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Settings, Trash2, X } from "lucide-react";

import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { KeyboardCombobox } from "@/components/KeyboardCombobox";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import { fetchFiscalPreview, nuevaLinea, type FiscalPreview, type LineaForm } from "@/lib/lineas";
import type { PosConfig } from "@/lib/pos";
import type { Almacen, Cliente } from "@/lib/types";

type Draft = {
  id: string;
  num: number;
  clienteId: string;
  lineas: LineaForm[];
  notas: string;
};

let _draftSeq = 0;
const nuevoDraft = (num: number): Draft => ({
  id: `d${_draftSeq++}`,
  num,
  clienteId: "",
  lineas: [nuevaLinea()],
  notas: "",
});

export default function Page() {
  const toast = useToast();
  const [cfg, setCfg] = useState<PosConfig | null>(null);
  const [cfgError, setCfgError] = useState(false);
  const [almacenId, setAlmacenId] = useState("");
  const [drafts, setDrafts] = useState<Draft[]>([nuevoDraft(1)]);
  const [activo, setActivo] = useState(0);
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const contador = useRef(1);

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const clientes = clientesRes.data?.items ?? [];
  const almacenesRes = useResource<Page<Almacen>>("/api/v1/almacenes?limit=200");
  const almacenes = almacenesRes.data?.items ?? [];

  useEffect(() => {
    apiFetch<PosConfig>("/api/v1/pos/config").then(setCfg).catch(() => setCfgError(true));
  }, []);
  useEffect(() => {
    if (!almacenId && almacenes[0]) setAlmacenId(almacenes[0].id);
  }, [almacenes, almacenId]);

  const draft = drafts[activo] ?? drafts[0];
  const setDraft = (patch: Partial<Draft>) =>
    setDrafts((ds) => ds.map((d, i) => (i === activo ? { ...d, ...patch } : d)));
  const setLineas = (fn: (ls: LineaForm[]) => LineaForm[]) =>
    setDrafts((ds) => ds.map((d, i) => (i === activo ? { ...d, lineas: fn(d.lineas) } : d)));

  // ── Totales del servidor (regla "el backend calcula todo") ──
  const [preview, setPreview] = useState<FiscalPreview | null>(null);
  const previewSeq = useRef(0);
  useEffect(() => {
    const seq = ++previewSeq.current;
    const t = setTimeout(() => {
      fetchFiscalPreview(draft.lineas)
        .then((p) => { if (seq === previewSeq.current) setPreview(p); })
        .catch(() => { if (seq === previewSeq.current) setPreview(null); });
    }, 300);
    return () => clearTimeout(t);
  }, [draft.lineas]);

  const clienteOpts = useMemo(
    () => clientes.map((c) => ({ value: c.id, label: c.legal_name })),
    [clientes],
  );

  function onPick(key: string, pick: ProductoPick | null, texto: string) {
    setLineas((ls) => ls.map((l) => {
      if (l.key !== key) return l;
      if (!pick) return { ...l, texto, producto_id: "", label: "", precio: "", precioManual: false, importe: 0 };
      const presentaciones = Object.keys(pick.presentaciones ?? {});
      const presentacion = pick.presentacion_default ?? presentaciones[0] ?? "";
      return { ...l, texto, producto_id: pick.producto_id, label: pick.nombre,
               presentaciones, presentacion };
    }));
  }

  function setLinea(key: string, patch: Partial<LineaForm>) {
    setLineas((ls) => ls.map((l) => {
      if (l.key !== key) return l;
      const next = { ...l, ...patch };
      const cant = Number(next.cantidad) || 0;
      const precio = Number(next.precio) || 0;
      next.importe = cant * precio;
      return next;
    }));
  }

  function addLinea() {
    setLineas((ls) => [...ls, nuevaLinea()]);
  }
  function quitarLinea(key: string) {
    setLineas((ls) => (ls.length > 1 ? ls.filter((l) => l.key !== key) : ls));
  }

  function nuevaPestana() {
    contador.current += 1;
    setDrafts((ds) => [...ds, nuevoDraft(contador.current)]);
    setActivo(drafts.length);
  }
  function cerrarPestana(i: number) {
    setDrafts((ds) => {
      const next = ds.filter((_, j) => j !== i);
      return next.length ? next : [nuevoDraft(++contador.current)];
    });
    setActivo((a) => (a >= i && a > 0 ? a - 1 : a));
  }

  const listas = draft.lineas.filter((l) => l.producto_id && Number(l.cantidad) > 0);
  const puedeGuardar = !!draft.clienteId && listas.length > 0 && !!almacenId;

  async function crearPedido() {
    if (!puedeGuardar || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    try {
      const lineas = listas.map((l) => ({
        producto_id: l.producto_id,
        cantidad_solicitada: l.cantidad,
        ...(l.precio !== "" ? { precio_unitario: l.precio } : {}),
        ...(l.presentacion ? { presentacion: l.presentacion } : {}),
      }));
      const rem = await apiFetch<{ id: string; folio_interno: string }>("/api/v1/remisiones", {
        method: "POST",
        body: JSON.stringify({
          cliente_facturacion_id: draft.clienteId,
          almacen_id: almacenId,
          notas: draft.notas.trim() || undefined,
          lineas,
        }),
      });
      await apiFetch(`/api/v1/pos/remisiones/${rem.id}/iniciar`, { method: "POST" });
      toast.success(`Pedido ${rem.folio_interno} enviado al flujo`);
      cerrarPestana(activo);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear el pedido");
    } finally {
      setSaving(false);
      inFlight.current = false;
    }
  }

  if (cfgError) return <Alert tone="danger">No se pudo cargar el POS. Recarga la página.</Alert>;
  if (!cfg) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (!cfg.activo) {
    return (
      <div>
        <PageHeader title="Pedido" subtitle="Punto de venta" />
        <Card>
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <div className="font-medium">El POS está desactivado</div>
            {cfg.puede_configurar && (
              <Link href="/ajustes/pos" className="inline-flex items-center gap-2 text-sm text-accent">
                <Settings size={15} /> Activarlo en Ajustes › Punto de venta
              </Link>
            )}
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Pedido"
        subtitle="Captura rápida — se envía al flujo del POS"
        actions={
          <div className="w-56">
            <Select value={almacenId} onChange={(e) => setAlmacenId(e.target.value)}>
              <option value="">— Almacén —</option>
              {almacenes.map((a) => <option key={a.id} value={a.id}>{a.nombre}</option>)}
            </Select>
          </div>
        }
      />

      {/* Pestañas de borradores */}
      <div className="mb-3 flex flex-wrap items-center gap-1">
        {drafts.map((d, i) => (
          <button
            key={d.id}
            onClick={() => setActivo(i)}
            className={`flex items-center gap-2 rounded-t-lg border-b-2 px-3 py-1.5 text-sm ${
              i === activo ? "border-accent bg-surface-2 font-medium" : "border-transparent text-muted hover:bg-surface-2"
            }`}
          >
            Pedido {d.num}
            <span
              role="button"
              aria-label={`Cerrar pedido ${d.num}`}
              onClick={(e) => { e.stopPropagation(); cerrarPestana(i); }}
              className="text-muted hover:text-danger"
            >
              <X size={13} />
            </span>
          </button>
        ))}
        <button onClick={nuevaPestana} aria-label="Nuevo pedido"
          className="ml-1 rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-foreground">
          <Plus size={16} />
        </button>
      </div>

      <Card>
        <div className="mb-4 max-w-md">
          <Field label="Cliente" required>
            <KeyboardCombobox
              options={clienteOpts}
              value={draft.clienteId}
              onSelect={(v) => setDraft({ clienteId: v })}
              ariaLabel="Cliente"
              placeholder="Buscar cliente…"
            />
          </Field>
        </div>

        {/* Líneas */}
        <div className="space-y-2">
          <div className="hidden gap-2 px-1 text-xs font-medium text-muted sm:grid sm:grid-cols-[3fr_1.4fr_1fr_1fr_1fr_auto]">
            <span>Producto</span><span>Presentación</span><span className="text-right">Cantidad</span>
            <span className="text-right">Precio</span><span className="text-right">Importe</span><span />
          </div>
          {draft.lineas.map((l) => (
            <div key={l.key} className="grid grid-cols-1 items-end gap-2 sm:grid-cols-[3fr_1.4fr_1fr_1fr_1fr_auto]">
              <ProductoCombobox
                label={l.label}
                onSelect={(p, texto) => onPick(l.key, p, texto)}
              />
              <Select
                value={l.presentacion}
                onChange={(e) => setLinea(l.key, { presentacion: e.target.value })}
                disabled={!l.producto_id}
              >
                {l.presentaciones.length === 0 && <option value="">—</option>}
                {l.presentaciones.map((p) => <option key={p} value={p}>{p}</option>)}
              </Select>
              <Input
                type="number" min="0" step="0.01" className="text-right"
                aria-label="Cantidad" placeholder="0"
                value={l.cantidad}
                onChange={(e) => setLinea(l.key, { cantidad: e.target.value })}
              />
              <Input
                type="number" min="0" step="0.01" className="text-right"
                aria-label="Precio" placeholder="auto"
                value={l.precio}
                onChange={(e) => setLinea(l.key, { precio: e.target.value, precioManual: true })}
              />
              <div className="flex items-center justify-end px-1 tabular-nums">{fmtMoney(l.importe)}</div>
              <button aria-label="Quitar línea" onClick={() => quitarLinea(l.key)}
                className="flex items-center justify-center text-muted hover:text-danger">
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
        <Button variant="secondary" onClick={addLinea} className="mt-3">
          <Plus size={16} /> Agregar línea
        </Button>

        <div className="mt-4">
          <Field label="Notas">
            <Textarea rows={2} value={draft.notas} onChange={(e) => setDraft({ notas: e.target.value })} />
          </Field>
        </div>

        {/* Totales del servidor + acción */}
        <div className="mt-4 flex items-start justify-between border-t border-border pt-4">
          <div />
          <div className="flex flex-col items-end gap-4">
            <div className="flex flex-col items-end gap-1 text-sm">
              <div className="flex gap-6"><span className="text-muted">Subtotal</span><span className="tabular-nums">{fmtMoney(preview?.subtotal ?? 0)}</span></div>
              <div className="flex gap-6"><span className="text-muted">IEPS</span><span className="tabular-nums">{fmtMoney(preview?.ieps ?? 0)}</span></div>
              <div className="flex gap-6"><span className="text-muted">IVA</span><span className="tabular-nums">{fmtMoney(preview?.iva ?? 0)}</span></div>
              <div className="flex gap-6 text-base font-semibold"><span className="text-muted">Total</span><span className="tabular-nums">{fmtMoney(preview?.total ?? 0)}</span></div>
            </div>
            <Button onClick={() => void crearPedido()} disabled={!puedeGuardar || saving}>
              {saving ? "Enviando…" : "Enviar al flujo"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

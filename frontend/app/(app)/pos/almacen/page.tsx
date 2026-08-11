"use client";

// POS · Estación ALMACÉN (Fase 3) — cola de surtido: checklist por línea y peso
// real (catch-weight). "Marcar surtido" completa la etapa; si el inventario sale
// aquí, usa el peso real capturado.
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, PackageCheck } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import type { Cliente, Producto, Remision, RemisionDetail } from "@/lib/types";

export default function Page() {
  const toast = useToast();
  const cola = useResource<Page<Remision>>("/api/v1/pos/cola/almacen?limit=100");
  useEffect(() => {
    const t = setInterval(() => cola.reload(), 10_000);
    return () => clearInterval(t);
  }, [cola]);

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const cliName = useMemo(
    () => Object.fromEntries((clientesRes.data?.items ?? []).map((c) => [c.id, c.legal_name])),
    [clientesRes.data],
  );
  const prodRes = useResource<Page<Producto>>("/api/v1/productos?limit=1000");
  const prodById = useMemo(
    () => Object.fromEntries((prodRes.data?.items ?? []).map((p) => [p.id, p])),
    [prodRes.data],
  );

  const [surtir, setSurtir] = useState<RemisionDetail | null>(null);

  async function abrir(r: Remision) {
    try {
      setSurtir(await apiFetch<RemisionDetail>(`/api/v1/remisiones/${r.id}`));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el pedido");
    }
  }

  if (cola.error) return <Alert tone="danger">No se pudo cargar la cola. Recarga la página.</Alert>;

  return (
    <div>
      <PageHeader title="Almacén" subtitle="Surtido de pedidos" />
      {cola.loading && !cola.data ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (cola.data?.items.length ?? 0) === 0 ? (
        <Card><div className="py-10 text-center text-sm text-muted">No hay pedidos por surtir.</div></Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(cola.data?.items ?? []).map((r) => (
            <Card key={r.id}>
              <div className="font-medium">{r.folio_interno}</div>
              <div className="text-sm text-muted">{cliName[r.cliente_facturacion_id] ?? "—"}</div>
              <Button className="mt-3 w-full" onClick={() => void abrir(r)}>
                <PackageCheck size={16} /> Surtir
              </Button>
            </Card>
          ))}
        </div>
      )}
      {surtir && (
        <SurtirModal rem={surtir} prodById={prodById}
          onClose={() => setSurtir(null)}
          onDone={() => { setSurtir(null); cola.reload(); }} />
      )}
    </div>
  );
}

function SurtirModal({ rem, prodById, onClose, onDone }: {
  rem: RemisionDetail; prodById: Record<string, Producto>; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [pesos, setPesos] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  const todo = rem.lineas.every((l) => checked[l.id]);

  async function marcar() {
    if (!todo || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      // Peso real solo para productos de peso variable con captura.
      const pesosPayload = rem.lineas
        .filter((l) => prodById[l.producto_id]?.peso_variable && Number(pesos[l.id]) > 0)
        .map((l) => ({ linea_id: l.id, cantidad_base: Number(pesos[l.id]).toString() }));
      await apiFetch(`/api/v1/pos/remisiones/${rem.id}/avanzar`, {
        method: "POST",
        body: JSON.stringify({ etapa: "almacen", ...(pesosPayload.length ? { pesos: pesosPayload } : {}) }),
      });
      toast.success(`${rem.folio_interno} surtido`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo surtir");
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  return (
    <Modal open onClose={onClose} title={`Surtir ${rem.folio_interno}`} wide
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button onClick={() => void marcar()} disabled={!todo || busy}>
          {busy ? "Marcando…" : "Marcar surtido"}
        </Button>
      </>}>
      <p className="mb-3 text-sm text-muted">Marca cada línea al tomarla del almacén.</p>
      <div className="space-y-1">
        {rem.lineas.map((l) => {
          const prod = prodById[l.producto_id];
          const variable = !!prod?.peso_variable;
          return (
            <div key={l.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-border px-3 py-2">
              <button
                type="button" aria-label={`Marcar ${prod?.nombre ?? "línea"}`}
                onClick={() => setChecked((c) => ({ ...c, [l.id]: !c[l.id] }))}
                className={`flex h-6 w-6 items-center justify-center rounded border ${
                  checked[l.id] ? "border-success bg-success text-white" : "border-border"
                }`}
              >
                {checked[l.id] && <Check size={15} />}
              </button>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{prod?.nombre ?? l.producto_id}</div>
                <div className="text-xs text-muted">
                  {fmtNumber(l.cantidad_solicitada)} {l.presentacion}
                </div>
              </div>
              {variable && (
                <div className="flex items-center gap-1">
                  <span className="text-xs text-muted">Peso real</span>
                  <Input type="number" min="0" step="0.001" className="w-24 text-right"
                    aria-label="Peso real" placeholder={fmtNumber(l.cantidad_solicitada)}
                    value={pesos[l.id] ?? ""}
                    onChange={(e) => setPesos((p) => ({ ...p, [l.id]: e.target.value }))} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

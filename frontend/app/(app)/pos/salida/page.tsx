"use client";

// POS · Estación SALIDA (Fase 3) — cola de entrega: registra quién recibe y
// completa el pedido (si el inventario sale aquí, se descuenta al entregar).
import { useEffect, useMemo, useRef, useState } from "react";
import { Truck } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import { usePosPulse } from "@/lib/usePosPulse";
import type { Cliente, Remision } from "@/lib/types";

export default function Page() {
  const cola = useResource<Page<Remision>>("/api/v1/pos/cola/salida?limit=100");
  useEffect(() => {
    const t = setInterval(() => cola.reload(), 30_000);   // backstop
    return () => clearInterval(t);
  }, [cola]);
  usePosPulse(() => cola.reload());   // realtime: recarga al detectar cambios

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const cliName = useMemo(
    () => Object.fromEntries((clientesRes.data?.items ?? []).map((c) => [c.id, c.legal_name])),
    [clientesRes.data],
  );
  const [entregar, setEntregar] = useState<Remision | null>(null);

  if (cola.error) return <Alert tone="danger">No se pudo cargar la cola. Recarga la página.</Alert>;

  return (
    <div>
      <PageHeader title="Salida" subtitle="Entrega de pedidos" />
      {cola.loading && !cola.data ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (cola.data?.items.length ?? 0) === 0 ? (
        <Card><div className="py-10 text-center text-sm text-muted">No hay pedidos por entregar.</div></Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(cola.data?.items ?? []).map((r) => (
            <Card key={r.id}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-medium">{r.folio_interno}</div>
                  <div className="text-sm text-muted">{cliName[r.cliente_facturacion_id] ?? "—"}</div>
                </div>
                <div className="text-right font-semibold tabular-nums">{fmtMoney(r.total)}</div>
              </div>
              <Button className="mt-3 w-full" onClick={() => setEntregar(r)}>
                <Truck size={16} /> Entregar
              </Button>
            </Card>
          ))}
        </div>
      )}
      {entregar && (
        <EntregarModal rem={entregar} cliente={cliName[entregar.cliente_facturacion_id] ?? "—"}
          onClose={() => setEntregar(null)}
          onDone={() => { setEntregar(null); cola.reload(); }} />
      )}
    </div>
  );
}

function EntregarModal({ rem, cliente, onClose, onDone }: {
  rem: Remision; cliente: string; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [recibe, setRecibe] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function entregar() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await apiFetch(`/api/v1/pos/remisiones/${rem.id}/avanzar`, {
        method: "POST",
        body: JSON.stringify({
          etapa: "salida",
          ...(recibe.trim() ? { nota: `Recibió: ${recibe.trim()}` } : {}),
        }),
      });
      toast.success(`${rem.folio_interno} entregado`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo entregar");
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  return (
    <Modal open onClose={onClose} title={`Entregar ${rem.folio_interno}`}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button onClick={() => void entregar()} disabled={busy}>
          {busy ? "Entregando…" : "Confirmar entrega"}
        </Button>
      </>}>
      <div className="mb-3 text-sm text-muted">{cliente}</div>
      <Field label="¿Quién recibe? (opcional)">
        <Input value={recibe} onChange={(e) => setRecibe(e.target.value)}
          placeholder="Nombre de quien recibe" autoFocus />
      </Field>
    </Modal>
  );
}

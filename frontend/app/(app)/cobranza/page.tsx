"use client";

// Cobranza — Recibos de Pago (REP, Complemento de Pago 2.0). Se registra el
// pago, se le anexan las facturas PPD que cubre (parcial = abono con saldo), y
// se timbra el REP ante el SAT. Estilo SAE.
import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Stamp } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Field, Input, Select } from "@/components/ui/Field";
import { KeyboardCombobox } from "@/components/KeyboardCombobox";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDate, fmtMoney } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import { FORMA_PAGO_SAT, type FacturaSaldo, type Recibo } from "@/lib/cobranza";
import type { Cliente } from "@/lib/types";

const TONE: Record<Recibo["estado"], "success" | "muted" | "danger"> = {
  TIMBRADO: "success", BORRADOR: "muted", CANCELADO: "danger",
};

export default function Page() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, "factura:gestionar");

  const recibos = useResource<{ items: Recibo[] }>("/api/v1/cobranza/recibos-pago?limit=100");
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const clientes = clientesRes.data?.items ?? [];
  const cliName = useMemo(() => Object.fromEntries(clientes.map((c) => [c.id, c.legal_name])), [clientes]);

  const [nuevo, setNuevo] = useState(false);
  const [timbrando, setTimbrando] = useState<string | null>(null);

  async function timbrar(r: Recibo) {
    setTimbrando(r.id);
    try {
      await apiFetch(`/api/v1/cobranza/recibos-pago/${r.id}/timbrar`, { method: "POST" });
      toast.success(`REP ${r.serie}${r.folio} timbrado`);
      recibos.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo timbrar");
    } finally {
      setTimbrando(null);
    }
  }

  const cols: Column<Recibo>[] = [
    { header: "Recibo", cell: (r) => <span className="font-medium">{r.serie}{r.folio}</span> },
    { header: "Cliente", cell: (r) => cliName[r.cliente_id] ?? "—" },
    { header: "Fecha pago", cell: (r) => fmtDate(r.fecha_pago) },
    { header: "Monto", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.monto) },
    { header: "Facturas", className: "text-right", cell: (r) => r.facturas.length },
    { header: "Estado", cell: (r) => <Badge tone={TONE[r.estado]}>{r.estado}</Badge> },
    { header: "", cell: (r) => canWrite && r.estado === "BORRADOR"
      ? <Button variant="secondary" onClick={() => void timbrar(r)} disabled={timbrando === r.id}>
          <Stamp size={14} /> {timbrando === r.id ? "Timbrando…" : "Timbrar"}
        </Button>
      : r.uuid ? <span className="font-mono text-xs text-muted">{r.uuid.slice(0, 8)}…</span> : null },
  ];

  return (
    <div>
      <PageHeader
        title="Cobranza — Recibos de pago"
        subtitle="Complementos de pago (REP) de facturas PPD"
        actions={canWrite ? <Button onClick={() => setNuevo(true)}><Plus size={16} /> Registrar pago</Button> : undefined}
      />
      {recibos.error ? (
        <Alert tone="danger">No se pudieron cargar los recibos.</Alert>
      ) : (
        <Card>
          <DataTable rows={recibos.data?.items ?? []} rowKey={(r) => r.id} columns={cols}
            loading={recibos.loading} empty="No hay recibos de pago aún." />
        </Card>
      )}
      {nuevo && (
        <RegistrarPago clientes={clientes}
          onClose={() => setNuevo(false)}
          onDone={() => { setNuevo(false); recibos.reload(); }} />
      )}
    </div>
  );
}

function RegistrarPago({ clientes, onClose, onDone }: {
  clientes: Cliente[]; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [clienteId, setClienteId] = useState("");
  const [fecha, setFecha] = useState(() => new Date().toISOString().slice(0, 10));
  const [forma, setForma] = useState("03");
  const [referencia, setReferencia] = useState("");
  const [saldos, setSaldos] = useState<FacturaSaldo[]>([]);
  const [aplicar, setAplicar] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  const clienteOpts = useMemo(() => clientes.map((c) => ({ value: c.id, label: c.legal_name })), [clientes]);

  // Al elegir cliente, trae sus facturas PPD con saldo (estado de cuenta).
  useEffect(() => {
    if (!clienteId) { setSaldos([]); return; }
    let active = true;
    apiFetch<{ facturas: FacturaSaldo[] }>(`/api/v1/cobranza/estado-cuenta/${clienteId}`)
      .then((d) => { if (active) { setSaldos(d.facturas); setAplicar({}); } })
      .catch(() => { if (active) setSaldos([]); });
    return () => { active = false; };
  }, [clienteId]);

  const monto = Object.values(aplicar).reduce((s, v) => s + (Number(v) || 0), 0);
  const lineas = saldos
    .filter((f) => Number(aplicar[f.factura_id]) > 0)
    .map((f) => ({ factura_id: f.factura_id, importe: Number(aplicar[f.factura_id]) }));
  const puede = clienteId && lineas.length > 0 && monto > 0
    && saldos.every((f) => (Number(aplicar[f.factura_id]) || 0) <= Number(f.saldo_insoluto) + 0.005);

  async function guardar() {
    if (!puede || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await apiFetch("/api/v1/cobranza/recibos-pago", {
        method: "POST",
        body: JSON.stringify({
          cliente_id: clienteId,
          fecha_pago: new Date(`${fecha}T12:00:00`).toISOString(),
          forma_pago: forma,
          monto: monto.toFixed(2),
          num_operacion: referencia.trim() || undefined,
          facturas: lineas.map((l) => ({ factura_id: l.factura_id, importe: l.importe.toFixed(2) })),
        }),
      });
      toast.success("Pago registrado — ya puedes timbrar el REP");
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo registrar el pago");
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  return (
    <Modal open onClose={onClose} title="Registrar pago" wide
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button onClick={() => void guardar()} disabled={!puede || busy}>
          {busy ? "Guardando…" : `Registrar ${fmtMoney(monto)}`}
        </Button>
      </>}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Cliente" required>
          <KeyboardCombobox options={clienteOpts} value={clienteId} onSelect={setClienteId}
            ariaLabel="Cliente" placeholder="Buscar cliente…" />
        </Field>
        <Field label="Fecha de pago" required>
          <Input type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} />
        </Field>
        <Field label="Forma de pago" required>
          <Select value={forma} onChange={(e) => setForma(e.target.value)}>
            {FORMA_PAGO_SAT.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </Select>
        </Field>
      </div>
      <div className="mt-3">
        <Field label="Referencia / operación (opcional)">
          <Input value={referencia} onChange={(e) => setReferencia(e.target.value)}
            placeholder="Folio de transferencia, cheque…" />
        </Field>
      </div>

      <div className="mt-4">
        <div className="mb-2 text-sm font-medium">Facturas por cobrar</div>
        {!clienteId ? (
          <div className="py-6 text-center text-sm text-muted">Elige un cliente para ver sus facturas.</div>
        ) : saldos.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted">El cliente no tiene facturas PPD con saldo.</div>
        ) : (
          <div className="space-y-2">
            {saldos.map((f) => (
              <div key={f.factura_id} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2 rounded-lg border border-border px-3 py-2">
                <div className="text-sm">
                  <span className="font-medium">{f.serie}{f.folio}</span>
                  <span className="ml-2 text-muted">{fmtDate(f.fecha)}</span>
                </div>
                <div className="text-right text-sm text-muted">Saldo: <span className="tabular-nums">{fmtMoney(f.saldo_insoluto)}</span></div>
                <div className="flex items-center gap-1">
                  <Input type="number" min="0" step="0.01" max={f.saldo_insoluto} className="w-28 text-right"
                    aria-label={`Aplicar a ${f.serie}${f.folio}`} placeholder="0.00"
                    value={aplicar[f.factura_id] ?? ""}
                    onChange={(e) => setAplicar((m) => ({ ...m, [f.factura_id]: e.target.value }))} />
                  <button className="text-xs text-accent hover:underline"
                    onClick={() => setAplicar((m) => ({ ...m, [f.factura_id]: Number(f.saldo_insoluto).toFixed(2) }))}>
                    todo
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="mt-3 flex justify-end text-sm">
        Monto del pago: <span className="ml-2 font-semibold tabular-nums">{fmtMoney(monto)}</span>
      </div>
    </Modal>
  );
}

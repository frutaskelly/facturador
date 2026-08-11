"use client";

// POS · Estación CAJA (Fase 2) — cobra los pedidos que esperan en la cola de
// caja (efectivo/tarjeta/crédito), con cálculo de cambio, validación de crédito
// del lado del servidor, y corte de caja (fondo inicial + arqueo).
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DollarSign, Lock, Settings, Unlock, Wallet } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
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
import { FORMA_LABEL, type CorteResumen, type FormaPago, type PosConfig } from "@/lib/pos";
import type { Cliente, Remision } from "@/lib/types";

const FORMAS: FormaPago[] = ["efectivo", "tarjeta", "credito"];

export default function Page() {
  const toast = useToast();
  const [cfg, setCfg] = useState<PosConfig | null>(null);
  const [cfgError, setCfgError] = useState(false);
  const [corte, setCorte] = useState<CorteResumen | null>(null);

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const cliName = useMemo(
    () => Object.fromEntries((clientesRes.data?.items ?? []).map((c) => [c.id, c.legal_name])),
    [clientesRes.data],
  );

  // Cola de caja (polling cada 10 s — el realtime llega en Fase 4).
  const cola = useResource<Page<Remision>>("/api/v1/pos/cola/caja?limit=100");
  useEffect(() => {
    const t = setInterval(() => cola.reload(), 10_000);
    return () => clearInterval(t);
  }, [cola]);

  const cargarCorte = useCallback(() => {
    apiFetch<CorteResumen | null>("/api/v1/pos/corte/actual").then(setCorte).catch(() => {});
  }, []);
  useEffect(() => {
    apiFetch<PosConfig>("/api/v1/pos/config").then(setCfg).catch(() => setCfgError(true));
    cargarCorte();
  }, [cargarCorte]);

  if (cfgError) return <Alert tone="danger">No se pudo cargar el POS. Recarga la página.</Alert>;
  if (!cfg) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title="Caja"
        subtitle="Cobra los pedidos en espera"
        actions={<CorteBar corte={corte} onChange={setCorte} onRefresh={cargarCorte} />}
      />

      {(cola.data?.items.length ?? 0) === 0 ? (
        <Card>
          <div className="py-10 text-center text-sm text-muted">No hay pedidos por cobrar.</div>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(cola.data?.items ?? []).map((r) => (
            <PedidoCard key={r.id} rem={r} cliente={cliName[r.cliente_facturacion_id] ?? "—"}
              credito={cfg.credito} onCobrado={() => { cola.reload(); cargarCorte(); }} />
          ))}
        </div>
      )}
    </div>
  );
}

function PedidoCard({ rem, cliente, credito, onCobrado }: {
  rem: Remision; cliente: string; credito: boolean; onCobrado: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Card>
        <div className="flex items-start justify-between">
          <div>
            <div className="font-medium">{rem.folio_interno}</div>
            <div className="text-sm text-muted">{cliente}</div>
          </div>
          <div className="text-right">
            <div className="text-lg font-semibold tabular-nums">{fmtMoney(rem.total)}</div>
          </div>
        </div>
        <Button className="mt-3 w-full" onClick={() => setOpen(true)}>
          <DollarSign size={16} /> Cobrar
        </Button>
      </Card>
      {open && (
        <CobroModal rem={rem} cliente={cliente} credito={credito}
          onClose={() => setOpen(false)}
          onDone={() => { setOpen(false); onCobrado(); }} />
      )}
    </>
  );
}

function CobroModal({ rem, cliente, credito, onClose, onDone }: {
  rem: Remision; cliente: string; credito: boolean; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const total = Number(rem.total);
  // Montos por forma de pago (string para captura libre).
  const [montos, setMontos] = useState<Record<FormaPago, string>>({ efectivo: "", tarjeta: "", credito: "" });
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  const formas = FORMAS.filter((f) => f !== "credito" || credito);
  const suma = FORMAS.reduce((s, f) => s + (Number(montos[f]) || 0), 0);
  const noEfectivo = (Number(montos.tarjeta) || 0) + (Number(montos.credito) || 0);
  const cambio = Number(montos.efectivo || 0) > Math.max(0, total - noEfectivo)
    ? Number(montos.efectivo) - Math.max(0, total - noEfectivo)
    : 0;
  const falta = total - suma;
  const puedeCobrar = suma >= total - 0.005 && noEfectivo <= total + 0.005;

  // Atajo: "exacto en efectivo" llena el total en efectivo.
  function exacto() {
    setMontos({ efectivo: total.toFixed(2), tarjeta: "", credito: "" });
  }

  async function cobrar() {
    if (!puedeCobrar || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const pagos = FORMAS
        .map((f) => ({ forma: f, monto: f === "efectivo"
          ? Math.min(Number(montos.efectivo) || 0, Math.max(0, total - noEfectivo))
          : Number(montos[f]) || 0 }))
        .filter((p) => p.monto > 0)
        .map((p) => ({ forma: p.forma, monto: p.monto.toFixed(2) }));
      await apiFetch(`/api/v1/pos/remisiones/${rem.id}/cobrar`, {
        method: "POST", body: JSON.stringify({ pagos }),
      });
      toast.success(`${rem.folio_interno} cobrado${cambio > 0 ? ` · cambio ${fmtMoney(cambio)}` : ""}`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cobrar");
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  return (
    <Modal open onClose={onClose} title={`Cobrar ${rem.folio_interno}`}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button onClick={() => void cobrar()} disabled={!puedeCobrar || busy}>
          {busy ? "Cobrando…" : "Confirmar cobro"}
        </Button>
      </>}>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm text-muted">{cliente}</span>
        <span className="text-xl font-semibold tabular-nums">{fmtMoney(total)}</span>
      </div>
      <div className="space-y-2">
        {formas.map((f) => (
          <div key={f} className="grid grid-cols-[1fr_auto] items-center gap-2">
            <span className="text-sm">{FORMA_LABEL[f]}</span>
            <Input type="number" min="0" step="0.01" className="w-36 text-right"
              aria-label={FORMA_LABEL[f]} placeholder="0.00"
              value={montos[f]}
              onChange={(e) => setMontos((m) => ({ ...m, [f]: e.target.value }))} />
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <button className="text-xs text-accent hover:underline" onClick={exacto}>Efectivo exacto</button>
      </div>
      <div className="mt-3 space-y-1 border-t border-border pt-3 text-sm">
        {falta > 0.005 && (
          <div className="flex justify-between text-danger">
            <span>Falta</span><span className="tabular-nums">{fmtMoney(falta)}</span>
          </div>
        )}
        {cambio > 0.005 && (
          <div className="flex justify-between font-medium">
            <span>Cambio</span><span className="tabular-nums">{fmtMoney(cambio)}</span>
          </div>
        )}
      </div>
    </Modal>
  );
}

function CorteBar({ corte, onChange, onRefresh }: {
  corte: CorteResumen | null; onChange: (c: CorteResumen | null) => void; onRefresh: () => void;
}) {
  const toast = useToast();
  const [abrirOpen, setAbrirOpen] = useState(false);
  const [cerrarOpen, setCerrarOpen] = useState(false);
  const [fondo, setFondo] = useState("");
  const [contado, setContado] = useState("");
  const [busy, setBusy] = useState(false);

  async function abrir() {
    setBusy(true);
    try {
      const c = await apiFetch<CorteResumen>("/api/v1/pos/corte/abrir", {
        method: "POST", body: JSON.stringify({ fondo_inicial: fondo || "0" }),
      });
      onChange(c); setAbrirOpen(false); setFondo("");
      toast.success("Corte abierto");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el corte");
    } finally { setBusy(false); }
  }

  async function cerrar() {
    setBusy(true);
    try {
      const c = await apiFetch<CorteResumen>("/api/v1/pos/corte/cerrar", {
        method: "POST", body: JSON.stringify({ efectivo_contado: contado || "0" }),
      });
      const d = Number(c.descuadre);
      toast[d === 0 ? "success" : "info"](
        d === 0 ? "Corte cuadrado" : `Corte cerrado · ${d < 0 ? "faltante" : "sobrante"} ${fmtMoney(Math.abs(d))}`,
      );
      onChange(null); setCerrarOpen(false); setContado(""); onRefresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cerrar el corte");
    } finally { setBusy(false); }
  }

  if (!corte) {
    return (
      <>
        <Button variant="secondary" onClick={() => setAbrirOpen(true)}>
          <Unlock size={16} /> Abrir caja
        </Button>
        <Modal open={abrirOpen} onClose={() => setAbrirOpen(false)} title="Abrir caja"
          footer={<>
            <Button variant="secondary" onClick={() => setAbrirOpen(false)} disabled={busy}>Cancelar</Button>
            <Button onClick={() => void abrir()} disabled={busy}>Abrir turno</Button>
          </>}>
          <Field label="Fondo inicial (efectivo en caja)">
            <Input type="number" min="0" step="0.01" value={fondo}
              onChange={(e) => setFondo(e.target.value)} placeholder="0.00" autoFocus />
          </Field>
        </Modal>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center gap-3 rounded-lg border border-border px-3 py-1.5 text-sm">
        <Wallet size={15} className="text-muted" />
        <span className="text-muted">En caja:</span>
        <span className="font-medium tabular-nums">{fmtMoney(corte.efectivo_esperado)}</span>
        <Button variant="secondary" onClick={() => setCerrarOpen(true)}>
          <Lock size={15} /> Cerrar caja
        </Button>
      </div>
      <Modal open={cerrarOpen} onClose={() => setCerrarOpen(false)} title="Cerrar caja (arqueo)"
        footer={<>
          <Button variant="secondary" onClick={() => setCerrarOpen(false)} disabled={busy}>Cancelar</Button>
          <Button onClick={() => void cerrar()} disabled={busy}>Cerrar turno</Button>
        </>}>
        <div className="mb-3 space-y-1 text-sm">
          <div className="flex justify-between"><span className="text-muted">Fondo inicial</span><span className="tabular-nums">{fmtMoney(corte.fondo_inicial)}</span></div>
          <div className="flex justify-between"><span className="text-muted">Ventas en efectivo</span><span className="tabular-nums">{fmtMoney(corte.efectivo_ventas)}</span></div>
          <div className="flex justify-between border-t border-border pt-1 font-medium"><span>Efectivo esperado</span><span className="tabular-nums">{fmtMoney(corte.efectivo_esperado)}</span></div>
        </div>
        <Field label="Efectivo contado en caja">
          <Input type="number" min="0" step="0.01" value={contado}
            onChange={(e) => setContado(e.target.value)} placeholder="0.00" autoFocus />
        </Field>
        {contado !== "" && (
          <div className="mt-2 text-sm">
            {(() => {
              const d = Number(contado) - Number(corte.efectivo_esperado);
              if (Math.abs(d) < 0.005) return <span className="text-success">Cuadra exacto ✓</span>;
              return <span className={d < 0 ? "text-danger" : "text-foreground"}>
                {d < 0 ? "Faltante" : "Sobrante"}: {fmtMoney(Math.abs(d))}
              </span>;
            })()}
          </div>
        )}
      </Modal>
    </>
  );
}

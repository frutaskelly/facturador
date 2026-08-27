"use client";

// Cobranza — Recibos de Pago (REP, Complemento de Pago 2.0). Se registra el
// pago, se le anexan las facturas PPD que cubre (parcial = abono con saldo), y
// se timbra el REP ante el SAT. Estilo SAE. Un REP timbrado se puede descargar
// (PDF/XML), enviar por correo y cancelar (revierte el saldo de las facturas).
import { useEffect, useMemo, useRef, useState } from "react";
import { Ban, Download, FileText, Mail, Plus, Stamp } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column, type RowAction } from "@/components/ui/DataTable";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { KeyboardCombobox } from "@/components/KeyboardCombobox";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch, apiOpenInTab } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
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
  // Correos por cliente (para autollenar el envío): array `correos` o el `email` legado.
  const cliCorreos = useMemo(() => Object.fromEntries(clientes.map((c) => {
    const dom = (c.domicilio_fiscal ?? {}) as Record<string, unknown>;
    const arr = Array.isArray(dom.correos)
      ? (dom.correos as string[])
      : (dom.email ? [String(dom.email)] : []);
    return [c.id, arr.join(", ")];
  })), [clientes]);

  const [nuevo, setNuevo] = useState(false);
  const [timbrando, setTimbrando] = useState<string | null>(null);
  const [aTimbrar, setATimbrar] = useState<Recibo | null>(null);
  const emisor = (() => {
    const t = me?.tenants.find((x) => x.tenant_id === me.active_tenant.tenant_id);
    return t ? ` Emisor: ${t.name}${t.rfc ? ` — RFC ${t.rfc}` : ""}.` : "";
  })();
  const [enviar, setEnviar] = useState<Recibo | null>(null);
  const [cancelar, setCancelar] = useState<Recibo | null>(null);

  async function timbrar(r: Recibo) {
    setTimbrando(r.id);
    try {
      await apiFetch(`/api/v1/cobranza/recibos-pago/${r.id}/timbrar`, { method: "POST" });
      toast.success(`REP ${r.serie}${r.folio} timbrado`);
      setATimbrar(null);
      recibos.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo timbrar");
    } finally {
      setTimbrando(null);
    }
  }

  function descargar(r: Recibo, tipo: "pdf" | "xml") {
    const nombre = `REP-${r.serie}${r.folio}`;
    if (tipo === "xml") {
      apiDownload(`/api/v1/cobranza/recibos-pago/${r.id}/xml`, `${nombre}.xml`)
        .catch((e) => toast.error(e instanceof ApiError ? e.message : "No se pudo descargar el XML"));
      return;
    }
    const win = window.open("", "_blank");
    apiOpenInTab(`/api/v1/cobranza/recibos-pago/${r.id}/pdf`, win)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el PDF"));
  }

  const cols: Column<Recibo>[] = [
    { header: "Recibo", cell: (r) => <span className="font-medium">{r.serie}{r.folio}</span> },
    { header: "Cliente", cell: (r) => cliName[r.cliente_id] ?? "—" },
    { header: "Fecha pago", cell: (r) => fmtDate(r.fecha_pago) },
    { header: "Monto", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.monto) },
    { header: "Facturas", className: "text-right", cell: (r) => r.facturas.length },
    { header: "Estado", cell: (r) => <Badge tone={TONE[r.estado]}>{r.estado}</Badge> },
    { header: "UUID", cell: (r) => r.uuid
      ? <span className="font-mono text-xs text-muted">{r.uuid.slice(0, 8)}…</span> : <span className="text-muted">—</span> },
  ];

  const rowActions: RowAction<Recibo>[] = [
    { id: "timbrar", label: timbrando ? "Timbrando…" : "Timbrar", icon: <Stamp size={15} />,
      onClick: (r) => setATimbrar(r),
      hidden: (r) => !(canWrite && r.estado === "BORRADOR") },
    { id: "pdf", label: "Descargar PDF", icon: <FileText size={15} />,
      onClick: (r) => descargar(r, "pdf"), hidden: (r) => r.estado !== "TIMBRADO" },
    { id: "xml", label: "Descargar XML", icon: <Download size={15} />,
      onClick: (r) => descargar(r, "xml"), hidden: (r) => r.estado !== "TIMBRADO" },
    { id: "enviar", label: "Enviar por correo", icon: <Mail size={15} />,
      onClick: (r) => setEnviar(r), hidden: (r) => !(canWrite && r.estado === "TIMBRADO") },
    { id: "cancelar", label: "Cancelar REP", icon: <Ban size={15} />, tone: "danger",
      onClick: (r) => setCancelar(r), hidden: (r) => !(canWrite && r.estado === "TIMBRADO") },
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
            actions={rowActions} loading={recibos.loading} empty="No hay recibos de pago aún." />
        </Card>
      )}
      {nuevo && (
        <RegistrarPago clientes={clientes}
          onClose={() => setNuevo(false)}
          onDone={() => { setNuevo(false); recibos.reload(); }} />
      )}
      {enviar && (
        <EnviarRecibo recibo={enviar} defaultTo={cliCorreos[enviar.cliente_id] ?? ""}
          onClose={() => setEnviar(null)} onDone={() => setEnviar(null)} />
      )}
      {cancelar && (
        <CancelarRecibo recibo={cancelar}
          onClose={() => setCancelar(null)}
          onDone={() => { setCancelar(null); recibos.reload(); }} />
      )}

      {/* Un REP timbrado con el emisor equivocado se cancela y se rehace igual
          que una factura: antes de mandarlo al PAC, decimos quién emite. */}
      <ConfirmDialog
        open={aTimbrar !== null}
        title="Timbrar REP"
        message={`¿Timbrar el recibo ${aTimbrar?.serie}${aTimbrar?.folio} por ${fmtMoney(aTimbrar?.monto ?? "0")}? Se enviará al PAC.${emisor}`}
        confirmLabel="Timbrar"
        confirmVariant="success"
        onConfirm={() => { if (aTimbrar) void timbrar(aTimbrar); }}
        onClose={() => setATimbrar(null)}
        loading={timbrando !== null}
      />
    </div>
  );
}

function EnviarRecibo({ recibo, defaultTo, onClose, onDone }: {
  recibo: Recibo; defaultTo: string; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [to, setTo] = useState(defaultTo);
  const [mensaje, setMensaje] = useState("");
  const [busy, setBusy] = useState(false);

  async function enviar() {
    const destinatarios = to.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    if (destinatarios.length === 0) { toast.error("Agrega al menos un correo"); return; }
    setBusy(true);
    try {
      await apiFetch(`/api/v1/cobranza/recibos-pago/${recibo.id}/enviar`, {
        method: "POST",
        body: JSON.stringify({ to: destinatarios, mensaje: mensaje.trim() || undefined }),
      });
      toast.success(`REP ${recibo.serie}${recibo.folio} enviado`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo enviar el recibo");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Enviar REP ${recibo.serie}${recibo.folio}`}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cerrar</Button>
        <Button onClick={() => void enviar()} disabled={busy}>{busy ? "Enviando…" : "Enviar"}</Button>
      </>}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Se adjuntan el PDF y el XML del recibo.</p>
        <Field label="Para" hint="Separa varios correos con coma o espacio">
          <Input value={to} onChange={(e) => setTo(e.target.value)} placeholder="cliente@correo.com" />
        </Field>
        <Field label="Mensaje (opcional)">
          <Textarea value={mensaje} onChange={(e) => setMensaje(e.target.value)} rows={3}
            placeholder="Gracias por su pago…" />
        </Field>
      </div>
    </Modal>
  );
}

function CancelarRecibo({ recibo, onClose, onDone }: {
  recibo: Recibo; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [motivo, setMotivo] = useState("02");
  const [busy, setBusy] = useState(false);

  async function confirmar() {
    setBusy(true);
    try {
      await apiFetch(`/api/v1/cobranza/recibos-pago/${recibo.id}/cancelar`, {
        method: "POST",
        body: JSON.stringify({ motivo }),
      });
      toast.success(`REP ${recibo.serie}${recibo.folio} cancelado`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cancelar el recibo");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={`Cancelar REP ${recibo.serie}${recibo.folio}`}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cerrar</Button>
        <Button variant="danger" onClick={() => void confirmar()} disabled={busy}>
          {busy ? "Cancelando…" : "Cancelar REP"}
        </Button>
      </>}>
      <div className="space-y-4">
        <Alert tone="warning">
          Al cancelar, el pago de {fmtMoney(recibo.monto)} regresa como saldo pendiente en las
          facturas que cubría. El SAT requiere la aceptación del receptor (positiva ficta a 3 días).
        </Alert>
        <Field label="Motivo SAT">
          <Select value={motivo} onChange={(e) => setMotivo(e.target.value)}>
            <option value="02">02 — Comprobante sin relación</option>
            <option value="03">03 — No se llevó a cabo la operación</option>
          </Select>
        </Field>
      </div>
    </Modal>
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

"use client";

// Cobranza → «Recibos de pago (REP)», Complemento de Pago 2.0. Un pago
// registrado en «Por cobrar» llega aquí en BORRADOR y se timbra ante el SAT.
// Un REP timbrado se puede descargar (PDF/XML), enviar por correo y cancelar
// (revierte el saldo de las facturas). Los del espejo de SAE sólo se consultan.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Ban, Download, FileText, Mail, Stamp } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column, type RowAction } from "@/components/ui/DataTable";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch, apiOpenInTab } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { fmtDate, fmtMoney } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import type { Recibo } from "@/lib/cobranza";
import type { Cliente } from "@/lib/types";

const TONE: Record<Recibo["estado"], "success" | "muted" | "danger"> = {
  TIMBRADO: "success", BORRADOR: "muted", CANCELADO: "danger",
};

/** `rev` sube cada vez que se registra un pago desde el botón de arriba de la
 *  página: el borrador nuevo aparece sin recargar. */
export function RecibosPago({ clientes, canWrite, rev }: { clientes: Cliente[]; canWrite: boolean; rev: number }) {
  const { me } = useAuth();
  const toast = useToast();

  const recibos = useResource<{ items: Recibo[] }>("/api/v1/cobranza/recibos-pago?limit=100");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (rev) recibos.reload(); }, [rev]);
  const cliName = useMemo(() => Object.fromEntries(clientes.map((c) => [c.id, c.legal_name])), [clientes]);
  // Correos por cliente (para autollenar el envío): array `correos` o el `email` legado.
  const cliCorreos = useMemo(() => Object.fromEntries(clientes.map((c) => {
    const dom = (c.domicilio_fiscal ?? {}) as Record<string, unknown>;
    const arr = Array.isArray(dom.correos)
      ? (dom.correos as string[])
      : (dom.email ? [String(dom.email)] : []);
    return [c.id, arr.join(", ")];
  })), [clientes]);

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

  // Estable entre renders: es dependencia de las acciones memoizadas de la tabla.
  const descargar = useCallback((r: Recibo, tipo: "pdf" | "xml") => {
    const nombre = `REP-${r.serie}${r.folio}`;
    if (tipo === "xml") {
      apiDownload(`/api/v1/cobranza/recibos-pago/${r.id}/xml`, `${nombre}.xml`)
        .catch((e) => toast.error(e instanceof ApiError ? e.message : "No se pudo descargar el XML"));
      return;
    }
    const win = window.open("", "_blank");
    apiOpenInTab(`/api/v1/cobranza/recibos-pago/${r.id}/pdf`, win)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el PDF"));
  }, [toast]);

  const cols: Column<Recibo>[] = useMemo(() => [
    { header: "Recibo", cell: (r) => (
      <span className="font-medium">
        {r.serie}{r.folio}
        {r.origen === "ESPEJO_SAE" && <span className="ml-1.5"><Badge tone="muted">SAE</Badge></span>}
      </span>
    ) },
    { header: "Cliente", cell: (r) => cliName[r.cliente_id] ?? "—" },
    { header: "Fecha pago", cell: (r) => fmtDate(r.fecha_pago) },
    { header: "Monto", className: "text-right tabular-nums", cell: (r) => fmtMoney(r.monto) },
    { header: "Facturas", className: "text-right", cell: (r) => r.facturas.length },
    { header: "Estado", cell: (r) => <Badge tone={TONE[r.estado]}>{r.estado}</Badge> },
    { header: "UUID", cell: (r) => r.uuid
      ? <span className="font-mono text-xs text-muted">{r.uuid.slice(0, 8)}…</span> : <span className="text-muted">—</span> },
  ], [cliName]);

  // Un REP del espejo lo timbró SAE: su PDF, su envío y su cancelación viven
  // allá (el backend los rechaza con 409). Aquí sólo se consulta.
  const esSae = (r: Recibo) => r.origen === "ESPEJO_SAE";
  const rowActions: RowAction<Recibo>[] = useMemo(() => [
    { id: "timbrar", label: timbrando ? "Timbrando…" : "Timbrar", icon: <Stamp size={15} />,
      onClick: (r) => setATimbrar(r),
      hidden: (r) => !(canWrite && r.estado === "BORRADOR") },
    { id: "pdf", label: "Descargar PDF", icon: <FileText size={15} />,
      onClick: (r) => descargar(r, "pdf"), hidden: (r) => esSae(r) || r.estado !== "TIMBRADO" },
    { id: "xml", label: "Descargar XML", icon: <Download size={15} />,
      onClick: (r) => descargar(r, "xml"), hidden: (r) => esSae(r) || r.estado !== "TIMBRADO" },
    { id: "enviar", label: "Enviar por correo", icon: <Mail size={15} />,
      onClick: (r) => setEnviar(r), hidden: (r) => esSae(r) || !(canWrite && r.estado === "TIMBRADO") },
    { id: "cancelar", label: "Cancelar REP", icon: <Ban size={15} />, tone: "danger",
      onClick: (r) => setCancelar(r), hidden: (r) => esSae(r) || !(canWrite && r.estado === "TIMBRADO") },
  ], [timbrando, canWrite, descargar]);

  return (
    <div>
      {recibos.error ? (
        <Alert tone="danger">No se pudieron cargar los recibos.</Alert>
      ) : (
        <Card>
          <DataTable rows={recibos.data?.items ?? []} rowKey={(r) => r.id} columns={cols}
            actions={rowActions} loading={recibos.loading} empty="No hay recibos de pago aún."
            paginated defaultPageSize={50} />
        </Card>
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

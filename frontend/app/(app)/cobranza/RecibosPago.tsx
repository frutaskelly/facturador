"use client";

// Cobranza → «Recibos de pago (REP)», Complemento de Pago 2.0. Una sola tabla
// para trabajar y para consultar (antes eran dos: esta, con las acciones pero
// solo los últimos 100, y «Comprobantes de pago» en Reportes, con periodo y el
// desglose fiscal pero sin acciones).
//
// Un pago registrado en «Por cobrar» llega aquí en BORRADOR —arriba, sin
// importar el periodo, porque es trabajo pendiente— y se timbra ante el SAT. Un
// REP timbrado se descarga (PDF/XML), se envía por correo y se cancela
// (revierte el saldo de las facturas). Los del espejo de SAE sólo se consultan.
// Al hacer clic en la fila se despliega cada factura que abona con su desglose.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Ban, Download, FileText, Mail, Stamp } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTableSmart, type Column, type RowAction } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch, apiOpenInTab } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { folioRelacionado, type Recibo, type ReciboFactura } from "@/lib/cobranza";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import type { Cliente } from "@/lib/types";

import { usePeriodo } from "./periodo";

// El reporte agrega a cada factura abonada su desglose fiscal (null si SAE
// abonó a una factura que aquí no está).
type FacturaAbonada = ReciboFactura & {
  fecha: string | null; subtotal: string | null; descuento: string | null;
  ieps: string | null; iva: string | null; total: string | null;
};
type Comprobante = Omit<Recibo, "facturas"> & { cliente: string; facturas: FacturaAbonada[] };
type Pagos = {
  items: Comprobante[];
  total: string; comprobantes: number; total_cancelado: string; cancelados: number; borradores: number;
};

const ESTADO: Record<Recibo["estado"], { label: string; tone: "success" | "muted" | "danger" }> = {
  BORRADOR: { label: "Borrador", tone: "muted" },
  TIMBRADO: { label: "Timbrado", tone: "success" },
  CANCELADO: { label: "Cancelado", tone: "danger" },
};

// Sin repetir: el espejo puede traer dos renglones de la misma factura.
const foliosRelacionados = (r: Comprobante) => [...new Set(r.facturas.map(folioRelacionado))];
const relacionadas = (r: Comprobante) => foliosRelacionados(r).join(", ");

const sumaDe = (fs: FacturaAbonada[], k: "subtotal" | "ieps" | "iva" | "total" | "importe_pagado") =>
  fs.reduce((t, f) => t + Number(f[k] ?? 0), 0);
const dinero = (v: string | number | null) => (v === null ? "—" : fmtMoney(v));

/** El detalle que se despliega bajo el recibo: cada factura que abona. */
function DetalleFacturas({ r }: { r: Comprobante }) {
  if (r.facturas.length === 0) {
    return <p className="px-4 py-3 text-sm text-muted">El recibo no trae facturas relacionadas.</p>;
  }
  const num = "px-3 py-1.5 text-right tabular-nums";
  return (
    <div className="overflow-x-auto px-4 py-3">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
            <th className="px-3 py-1.5 text-left font-medium">Factura</th>
            <th className="px-3 py-1.5 text-left font-medium">Fecha</th>
            <th className={`${num} font-medium`}>Subtotal</th>
            <th className={`${num} font-medium`}>IEPS</th>
            <th className={`${num} font-medium`}>IVA</th>
            <th className={`${num} font-medium`}>Total</th>
            <th className={`${num} font-medium`}>Pagado</th>
            <th className={`${num} font-medium`}>Saldo</th>
          </tr>
        </thead>
        <tbody>
          {r.facturas.map((f, i) => (
            <tr key={f.factura_id ?? `${f.factura_ref}-${i}`} className="border-b border-border/60">
              <td className="whitespace-nowrap px-3 py-1.5 font-medium">{folioRelacionado(f)}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-muted">{f.fecha ? fmtDate(f.fecha) : "—"}</td>
              <td className={num}>{dinero(f.subtotal)}</td>
              <td className={num}>{dinero(f.ieps)}</td>
              <td className={num}>{dinero(f.iva)}</td>
              <td className={num}>{dinero(f.total)}</td>
              <td className={`${num} font-medium`}>{dinero(f.importe_pagado)}</td>
              <td className={`${num} text-muted`}>{dinero(f.saldo_insoluto)}</td>
            </tr>
          ))}
        </tbody>
        {r.facturas.length > 1 && (
          <tfoot>
            <tr className="font-semibold">
              <td className="px-3 py-1.5" colSpan={2}>{r.facturas.length} facturas</td>
              <td className={num}>{fmtMoney(sumaDe(r.facturas, "subtotal"))}</td>
              <td className={num}>{fmtMoney(sumaDe(r.facturas, "ieps"))}</td>
              <td className={num}>{fmtMoney(sumaDe(r.facturas, "iva"))}</td>
              <td className={num}>{fmtMoney(sumaDe(r.facturas, "total"))}</td>
              <td className={num}>{fmtMoney(sumaDe(r.facturas, "importe_pagado"))}</td>
              <td />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/** `rev` sube cada vez que se registra un pago desde el botón de arriba de la
 *  página: el borrador nuevo aparece sin recargar. */
export function RecibosPago({ clientes, canWrite, rev }: { clientes: Cliente[]; canWrite: boolean; rev: number }) {
  const { me } = useAuth();
  const toast = useToast();
  const { query, filtro } = usePeriodo();

  const res = useResource<Pagos>(`/api/v1/reportes/pagos?incluir_borradores=true&${query}`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (rev) res.reload(); }, [rev]);
  const d = res.data;
  // Lo que queda tras el buscador y los embudos; null = aún sin filtrar.
  const [visibles, setVisibles] = useState<Comprobante[] | null>(null);

  // Correos por cliente (para autollenar el envío): array `correos` o el `email` legado.
  const cliCorreos = useMemo(() => Object.fromEntries(clientes.map((c) => {
    const dom = (c.domicilio_fiscal ?? {}) as Record<string, unknown>;
    const arr = Array.isArray(dom.correos)
      ? (dom.correos as string[])
      : (dom.email ? [String(dom.email)] : []);
    return [c.id, arr.join(", ")];
  })), [clientes]);

  const [timbrando, setTimbrando] = useState<string | null>(null);
  const [aTimbrar, setATimbrar] = useState<Comprobante | null>(null);
  const emisor = (() => {
    const t = me?.tenants.find((x) => x.tenant_id === me.active_tenant.tenant_id);
    return t ? ` Emisor: ${t.name}${t.rfc ? ` — RFC ${t.rfc}` : ""}.` : "";
  })();
  const [enviar, setEnviar] = useState<Comprobante | null>(null);
  const [cancelar, setCancelar] = useState<Comprobante | null>(null);

  async function timbrar(r: Comprobante) {
    setTimbrando(r.id);
    try {
      await apiFetch(`/api/v1/cobranza/recibos-pago/${r.id}/timbrar`, { method: "POST" });
      toast.success(`REP ${r.serie}${r.folio} timbrado`);
      setATimbrar(null);
      res.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo timbrar");
    } finally {
      setTimbrando(null);
    }
  }

  // Estable entre renders: es dependencia de las acciones memoizadas de la tabla.
  const descargar = useCallback((r: Comprobante, tipo: "pdf" | "xml") => {
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

  const cols: Column<Comprobante>[] = useMemo(() => [
    { header: "Fecha pago", className: "whitespace-nowrap", sortable: true,
      sortValue: (r) => r.fecha_pago, exportValue: (r) => fmtDate(r.fecha_pago),
      cell: (r) => fmtDate(r.fecha_pago) },
    { header: "Recibo", className: "whitespace-nowrap", sortable: true,
      sortValue: (r) => `${r.serie}${String(r.folio).padStart(8, "0")}`,
      exportValue: (r) => `${r.serie}${r.folio}`,
      cell: (r) => (
        <span className="font-medium">
          {r.serie}{r.folio}
          {r.origen === "ESPEJO_SAE" && <span className="ml-1.5"><Badge tone="muted">SAE</Badge></span>}
        </span>
      ) },
    { header: "Cliente", truncate: true, sortable: true, sortValue: (r) => r.cliente,
      exportValue: (r) => r.cliente, cell: (r) => <span title={r.cliente}>{r.cliente}</span> },
    { header: "Facturas relacionadas", truncate: true, exportValue: relacionadas,
      filterValues: foliosRelacionados,
      cell: (r) => <span title={relacionadas(r)}>{relacionadas(r) || "—"}</span> },
    { header: "Monto pagado", className: "whitespace-nowrap text-right tabular-nums", sortable: true,
      sortValue: (r) => Number(r.monto), exportValue: (r) => r.monto,
      cell: (r) => (
        <span className={r.estado === "CANCELADO" ? "text-muted line-through" : "font-medium"}>
          {fmtMoney(r.monto)}
        </span>
      ) },
    { header: "Estado", sortable: true, sortValue: (r) => r.estado,
      exportValue: (r) => ESTADO[r.estado].label,
      filterValues: (r) => [ESTADO[r.estado].label],
      cell: (r) => <Badge tone={ESTADO[r.estado].tone}>{ESTADO[r.estado].label}</Badge> },
    { header: "UUID", exportValue: (r) => r.uuid ?? "",
      cell: (r) => r.uuid
        ? <span className="font-mono text-xs text-muted" title={r.uuid}>{r.uuid.slice(0, 8)}…</span>
        : <span className="text-muted">—</span> },
  ], []);

  // Un REP del espejo lo timbró SAE: su PDF, su envío y su cancelación viven
  // allá (el backend los rechaza con 409). Aquí sólo se consulta.
  const esSae = (r: Comprobante) => r.origen === "ESPEJO_SAE";
  const rowActions: RowAction<Comprobante>[] = useMemo(() => [
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

  // El conteo y el total siguen a lo VISIBLE (buscador y embudos). Como el del
  // servidor, solo suman los timbrados: lo cancelado va aparte y los borradores
  // aún no son un pago ante el SAT.
  const suma = useMemo(() => {
    const base = visibles ?? d?.items ?? [];
    const de = (e: Recibo["estado"]) => base.filter((r) => r.estado === e);
    const monto = (xs: Comprobante[]) => xs.reduce((t, r) => t + Number(r.monto), 0);
    const tim = de("TIMBRADO"), can = de("CANCELADO");
    return { timbrados: tim.length, total: monto(tim), cancelados: can.length,
             totalCancelado: monto(can), borradores: de("BORRADOR").length };
  }, [visibles, d]);

  return (
    <div>
      {res.error ? (
        <Alert tone="danger">No se pudieron cargar los recibos de pago.</Alert>
      ) : (
        <DataTableSmart
          rows={d?.items ?? []}
          rowKey={(r) => r.id}
          columns={cols}
          actions={rowActions}
          loading={res.loading}
          renderExpanded={(r) => <DetalleFacturas r={r} />}
          empty="Sin recibos de pago en el periodo."
          storageKey="cobranza-recibos-pago"
          searchPlaceholder="Folio, cliente o factura (p. ej. FEHMOHOS12)…"
          exportFilename="recibos-de-pago"
          defaultPageSize={50}
          onFilteredRowsChange={setVisibles}
          toolbarStart={filtro(d && (
            <>
              {fmtNumber(suma.timbrados, 0)} timbrado{suma.timbrados === 1 ? "" : "s"} ·{" "}
              <span className="font-medium tabular-nums text-foreground">{fmtMoney(suma.total)}</span>
              {suma.borradores > 0 && (
                <> · <span className="text-warning">{suma.borradores} por timbrar</span></>
              )}
            </>
          ))}
        />
      )}
      {suma.cancelados > 0 && (
        <p className="mt-3 text-xs text-muted">
          Fuera del total: {fmtMoney(suma.totalCancelado)} en {fmtNumber(suma.cancelados, 0)}{" "}
          {suma.cancelados === 1 ? "recibo cancelado" : "recibos cancelados"}.
        </p>
      )}
      {enviar && (
        <EnviarRecibo recibo={enviar} defaultTo={cliCorreos[enviar.cliente_id] ?? ""}
          onClose={() => setEnviar(null)} onDone={() => setEnviar(null)} />
      )}
      {cancelar && (
        <CancelarRecibo recibo={cancelar}
          onClose={() => setCancelar(null)}
          onDone={() => { setCancelar(null); res.reload(); }} />
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
  recibo: Comprobante; defaultTo: string; onClose: () => void; onDone: () => void;
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
    <Modal open onClose={onClose} title={`Enviar REP ${recibo.serie}${recibo.folio}`} size="md"
      description="Se adjuntan el PDF y el XML del recibo."
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cerrar</Button>
        <Button onClick={() => void enviar()} disabled={busy}>{busy ? "Enviando…" : "Enviar"}</Button>
      </>}>
      <div className="space-y-3">
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
  recibo: Comprobante; onClose: () => void; onDone: () => void;
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
    <Modal open onClose={onClose} title={`Cancelar REP ${recibo.serie}${recibo.folio}`} size="sm"
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

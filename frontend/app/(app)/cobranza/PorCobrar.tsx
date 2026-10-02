"use client";

// Cobranza → «Por cobrar»: la mesa de trabajo. Las facturas PPD con saldo, de
// todos los clientes; se marcan las que cubre un pago y se registra. El REP que
// sale de ahí se timbra en la pestaña «Recibos de pago».
//
// Una sola barra, como Facturas y Remisiones (#309): buscador · conteo · Excel
// · Columnas. Se descargan TODAS las pendientes (antes cortaba en 200 sin
// avisar), así que el buscador y los embudos —cliente, serie, estado de pago—
// ven todo; ya no hay filtro de cliente encima de la tabla.
import { useEffect, useMemo, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { type Column } from "@/components/ui/DataTable";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { Field, Input, Select } from "@/components/ui/Field";
import { KeyboardCombobox } from "@/components/KeyboardCombobox";
import { LoadingDots } from "@/components/ui/LoadingDots";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtDate, fmtMoney, fmtNumber } from "@/lib/format";
import { useListadoCompleto } from "@/lib/hooks";
import { FORMA_PAGO_SAT, type FacturaPendiente, type FacturaSaldo } from "@/lib/cobranza";
import type { Cliente } from "@/lib/types";

/** `rev` sube cada vez que se registra un pago desde el botón de arriba de la
 *  página: la tabla vuelve a pedir los saldos sin perder sus filtros. */
export function PorCobrar({ clientes, canWrite, rev }: { clientes: Cliente[]; canWrite: boolean; rev: number }) {
  const cliName = useMemo(() => Object.fromEntries(clientes.map((c) => [c.id, c.legal_name])), [clientes]);

  // Lotes de 500 = el tope del endpoint.
  const pendientes = useListadoCompleto<FacturaPendiente>("/api/v1/cobranza/facturas-pendientes", 500);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (rev) pendientes.reload(); }, [rev]);
  const filas = pendientes.data?.items;
  // Lo que queda tras el buscador y los embudos; null = aún sin filtrar.
  const [visibles, setVisibles] = useState<FacturaPendiente[] | null>(null);
  const [pendSel, setPendSel] = useState<FacturaPendiente[]>([]);
  const [selReset, setSelReset] = useState(0);
  // Un pago = un cliente (regla del REP): con selección mixta se avisa.
  const pendClientes = useMemo(() => [...new Set(pendSel.map((f) => f.cliente_id))], [pendSel]);
  const [pagoPre, setPagoPre] = useState<{ clienteId: string; facturaIds: string[] } | null>(null);

  const pendCols: Column<FacturaPendiente>[] = useMemo(() => [
    { header: "Folio", sortable: true, exportValue: (f) => `${f.serie}${f.folio}`,
      sortValue: (f) => `${f.serie}${String(f.folio).padStart(8, "0")}`,
      cell: (f) => <span className="font-medium">{f.serie}{f.folio}</span> },
    { header: "Cliente", truncate: true, sortable: true,
      exportValue: (f) => cliName[f.cliente_id] ?? "",
      sortValue: (f) => cliName[f.cliente_id] ?? "",
      cell: (f) => <span title={cliName[f.cliente_id] ?? ""}>{cliName[f.cliente_id] ?? "—"}</span> },
    { header: "Serie", sortable: true, exportValue: (f) => f.serie, sortValue: (f) => f.serie,
      cell: (f) => f.serie },
    { header: "Fecha", className: "whitespace-nowrap", sortable: true,
      sortValue: (f) => f.fecha, exportValue: (f) => fmtDate(f.fecha), cell: (f) => fmtDate(f.fecha) },
    { header: "Vencimiento", className: "whitespace-nowrap", sortable: true,
      sortValue: (f) => f.vencimiento, exportValue: (f) => fmtDate(f.vencimiento),
      cell: (f) => (
        <span className={f.dias_vencida > 0 ? "font-medium text-danger" : undefined}
          title={f.dias_vencida > 0 ? `Vencida hace ${f.dias_vencida} día(s)` : undefined}>
          {fmtDate(f.vencimiento)}
        </span>
      ) },
    { header: "Total", className: "text-right tabular-nums", sortable: true,
      sortValue: (f) => Number(f.total), exportValue: (f) => f.total, cell: (f) => fmtMoney(f.total) },
    { header: "Saldo pendiente", className: "text-right tabular-nums", sortable: true,
      sortValue: (f) => Number(f.saldo_insoluto), exportValue: (f) => f.saldo_insoluto,
      cell: (f) => <span className="font-medium">{fmtMoney(f.saldo_insoluto)}</span> },
    { header: "Estado de pago", sortable: true, exportValue: (f) => f.estado_pago,
      sortValue: (f) => f.estado_pago,
      cell: (f) => <Badge tone={f.estado_pago === "PARCIAL" ? "warning" : "muted"}>{f.estado_pago}</Badge> },
  ], [cliName]);
  const pendTotalSel = pendSel.reduce((s, f) => s + Number(f.saldo_insoluto), 0);

  // El conteo, el saldo y lo vencido siguen a lo VISIBLE (buscador y embudos).
  const suma = useMemo(() => {
    const base = visibles ?? filas ?? [];
    const saldo = (xs: FacturaPendiente[]) => xs.reduce((t, f) => t + Number(f.saldo_insoluto), 0);
    return { facturas: base.length, saldo: saldo(base), vencido: saldo(base.filter((f) => f.dias_vencida > 0)) };
  }, [visibles, filas]);

  return (
    <div>
      {/* Barra de la selección, como la de acciones en lote de Remisiones. */}
      {pendSel.length > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2 px-4 py-2.5 shadow-sm">
          <span className="text-sm font-medium">
            {pendSel.length} factura{pendSel.length === 1 ? "" : "s"} · saldo{" "}
            <span className="tabular-nums">{fmtMoney(pendTotalSel)}</span>
          </span>
          <span className="text-sm text-muted">·</span>
          {pendClientes.length > 1 ? (
            <span className="text-sm text-warning">Un pago cubre facturas de UN solo cliente — la selección tiene {pendClientes.length}.</span>
          ) : canWrite ? (
            <Button onClick={() => setPagoPre({ clienteId: pendClientes[0], facturaIds: pendSel.map((f) => f.factura_id) })}>
              Registrar pago ({pendSel.length})
            </Button>
          ) : null}
        </div>
      )}
      {pendientes.error ? (
        <Alert tone="danger">No se pudieron cargar las facturas pendientes.</Alert>
      ) : (
        <DataTableSmart
          rows={filas ?? []}
          rowKey={(f) => f.factura_id}
          columns={pendCols}
          loading={pendientes.loading}
          empty="Sin facturas PPD con saldo pendiente."
          storageKey="cobranza-pendientes"
          exportFilename="facturas-por-cobrar"
          defaultPageSize={50}
          selectable
          onSelectionChange={setPendSel}
          selectionResetKey={selReset}
          onFilteredRowsChange={setVisibles}
          searchPlaceholder="Folio o cliente (p. ej. FEHMOHOS12)…"
          toolbarStart={filas && (
            <span className="whitespace-nowrap text-sm text-muted">
              {pendientes.progreso ? (
                <>Cargando {fmtNumber(pendientes.progreso.cargadas, 0)} de {fmtNumber(pendientes.progreso.total, 0)}<LoadingDots /></>
              ) : (
                <>
                  {fmtNumber(suma.facturas, 0)} factura{suma.facturas === 1 ? "" : "s"} · saldo{" "}
                  <span className="font-medium tabular-nums text-foreground">{fmtMoney(suma.saldo)}</span>
                  {suma.vencido > 0 && (
                    <> · <span className="text-danger">vencido <span className="tabular-nums">{fmtMoney(suma.vencido)}</span></span></>
                  )}
                </>
              )}
            </span>
          )}
        />
      )}
      {pagoPre && (
        <RegistrarPago clientes={clientes}
          preClienteId={pagoPre.clienteId}
          preFacturaIds={pagoPre.facturaIds}
          onClose={() => setPagoPre(null)}
          onDone={() => { setPagoPre(null); setPendSel([]); setSelReset((n) => n + 1); pendientes.reload(); }} />
      )}
    </div>
  );
}

export function RegistrarPago({ clientes, preClienteId, preFacturaIds, onClose, onDone }: {
  clientes: Cliente[];
  /** Rediseño 86bbyw5u2: la tabla de pendientes llega aquí con el cliente y
   *  las facturas ya elegidas; el popup solo captura los datos del pago. */
  preClienteId?: string;
  preFacturaIds?: string[];
  onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [clienteId, setClienteId] = useState(preClienteId ?? "");
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
      .then((d) => {
        if (!active) return;
        setSaldos(d.facturas);
        // Las facturas elegidas en la tabla entran con TODO su saldo aplicado
        // (editable): el caso común es el pago completo de lo seleccionado.
        setAplicar(preFacturaIds?.length
          ? Object.fromEntries(
              d.facturas
                .filter((f) => preFacturaIds.includes(f.factura_id))
                .map((f) => [f.factura_id, Number(f.saldo_insoluto).toFixed(2)]),
            )
          : {});
      })
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
      toast.success("Pago registrado — tímbralo en «Recibos de pago»");
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo registrar el pago");
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  return (
    <Modal open onClose={onClose} title="Registrar pago" size="lg"
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

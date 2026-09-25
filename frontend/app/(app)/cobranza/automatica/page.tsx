"use client";

// Cobranza automática: el estado de cuenta que sale solo cada semana.
// Tres pestañas: la COLA (lo que se preparó, se aprueba y se manda, y la
// bitácora de lo que ya salió), los CONTACTOS (a quién se le cobra, por
// cliente o por serie, y quién está en pausa) y los AJUSTES (cuándo sale, qué
// incluye, a quién se copia al escalar, adjuntos y el modo). Todo lo decide el
// negocio aquí; el backend solo lo ejecuta (services/cobranza_auto.py).
import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowLeft, FileText, Pause, Pencil, Play, RefreshCw, Send, Trash2, X } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTableSmart, type Column, type RowAction } from "@/components/ui/DataTableSmart";
import { Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiOpenInTab } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney } from "@/lib/format";
import { useMutation, useResource, type Page as Pagina } from "@/lib/hooks";
import type { Cliente } from "@/lib/types";

type Config = {
  activo: boolean; modo: "REVISION" | "AUTOMATICO"; dia_semana: number; hora: number; zona: string;
  incluir_por_vencer: boolean; saldo_minimo: string | number; escalar_dias: number;
  escalar_cc: string[]; cc_siempre: string[]; adjuntar_pdf: boolean; adjuntar_excel: boolean;
  espejo_max_horas: number; asunto: string | null; mensaje: string | null;
  ultima_generacion: string | null; espejo_ok: boolean; espejo_ultima: string | null;
};
type Contacto = {
  id: string; cliente_id: string; serie: string | null; correos: string[]; cc: string[];
  pausado: boolean; motivo_pausa: string | null;
};
type FilaContactos = {
  cliente_id: string; cliente: string; saldo: string;
  series: { serie: string; facturas: number; saldo: string }[]; contactos: Contacto[];
};
type Envio = {
  id: string; cliente_id: string; cliente: string; serie: string | null; corte: string;
  estado: "PENDIENTE" | "ENVIANDO" | "ENVIADO" | "ERROR" | "DESCARTADO"; origen: string;
  para: string[]; cc: string[]; saldo: string; vencido: string; facturas: number;
  dias_max_vencida: number; escalado: boolean; error: string | null; enviado_at: string | null;
};

type Pestana = "cola" | "contactos" | "ajustes";
const DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const TONO: Record<Envio["estado"], "warning" | "accent" | "success" | "danger" | "muted"> = {
  PENDIENTE: "warning", ENVIANDO: "accent", ENVIADO: "success", ERROR: "danger", DESCARTADO: "muted",
};
const FILTROS = [
  { key: "PENDIENTE,ERROR", label: "Por enviar" },
  { key: "ENVIADO", label: "Enviados" },
  { key: "DESCARTADO", label: "Descartados" },
  { key: "", label: "Todos" },
];

const aLista = (s: string) => s.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
const deLista = (l: string[] | null | undefined) => (l ?? []).join(", ");
const errorDe = (e: unknown, def: string) => (e instanceof ApiError ? e.message : def);

// Los correos que el cliente ya tiene capturados en su ficha (los del envío de
// facturas): se ofrecen como punto de partida al capturar el de cobranza.
function correosDeFicha(c: Cliente | undefined): string[] {
  const dom = (c?.domicilio_fiscal ?? {}) as Record<string, unknown>;
  if (Array.isArray(dom.correos)) return dom.correos as string[];
  return dom.email ? [String(dom.email)] : [];
}

export default function Page() {
  const { me } = useAuth();
  const canWrite = can(me, "factura:gestionar");
  const [pestana, setPestana] = useState<Pestana>("cola");
  const cfgRes = useResource<Config>("/api/v1/cobranza/automatica/config");
  const cfg = cfgRes.data;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Cobranza automática"
        subtitle="El estado de cuenta de las facturas pendientes, por correo y a su hora"
        actions={
          <Link href="/cobranza" className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-surface-2">
            <ArrowLeft size={16} /> Cobranza
          </Link>
        }
      />

      {cfg && <Estado cfg={cfg} />}

      <div role="tablist" className="flex gap-1 border-b border-border">
        {([["cola", "Cola y bitácora"], ["contactos", "Contactos"], ["ajustes", "Ajustes"]] as const).map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={pestana === k} onClick={() => setPestana(k)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm transition ${pestana === k
                    ? "border-accent font-medium text-foreground" : "border-transparent text-muted hover:text-foreground"}`}>
            {l}
          </button>
        ))}
      </div>

      {pestana === "cola" && <Cola canWrite={canWrite} onCambio={cfgRes.reload} />}
      {pestana === "contactos" && <Contactos canWrite={canWrite} />}
      {pestana === "ajustes" && (cfg
        ? <Ajustes cfg={cfg} canWrite={canWrite} onGuardado={(c) => cfgRes.setData(c)} />
        : <div className="flex justify-center py-8"><Spinner /></div>)}
    </div>
  );
}

/** La franja de arriba: si está encendida, cuándo sale, en qué modo y el candado del espejo. */
function Estado({ cfg }: { cfg: Config }) {
  const cuando = `${DIAS[cfg.dia_semana]} a las ${String(cfg.hora).padStart(2, "0")}:00`;
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Card>
        <div className="text-xs text-muted">Estado</div>
        <div className="mt-1 flex items-center gap-2 font-semibold">
          {cfg.activo ? <Badge tone="success">Encendida</Badge> : <Badge tone="muted">Apagada</Badge>}
          <span className="text-sm font-normal text-muted">
            {cfg.modo === "AUTOMATICO" ? "se envía sola" : "espera aprobación"}
          </span>
        </div>
      </Card>
      <Card>
        <div className="text-xs text-muted">Envío semanal</div>
        <div className="mt-1 font-semibold">{cuando}</div>
        <div className="text-xs text-muted">
          {cfg.ultima_generacion ? `Última cola: ${fmtDate(cfg.ultima_generacion)}` : "Aún no se ha armado ninguna cola"}
        </div>
      </Card>
      <Card>
        <div className="text-xs text-muted">Espejo de SAE (pagos)</div>
        <div className="mt-1 font-semibold">
          {cfg.espejo_max_horas === 0
            ? <span className="text-muted">Sin candado</span>
            : cfg.espejo_ok ? <span className="text-success">Al día</span> : <span className="text-danger">Desactualizado</span>}
        </div>
        <div className="text-xs text-muted">
          {cfg.espejo_ultima ? `Última pasada buena: ${fmtDateTime(cfg.espejo_ultima)}` : "Sin pasadas registradas"}
        </div>
      </Card>
    </div>
  );
}

// ─── Cola y bitácora ─────────────────────────────────────────────────────────

function Cola({ canWrite, onCambio }: { canWrite: boolean; onCambio: () => void }) {
  const toast = useToast();
  const { post, loading } = useMutation();
  const [filtro, setFiltro] = useState(FILTROS[0].key);
  const res = useResource<Envio[]>(`/api/v1/cobranza/automatica/envios?dias=120${filtro ? `&estado=${filtro}` : ""}`);
  const [sel, setSel] = useState<Envio[]>([]);
  const [reset, setReset] = useState(0);

  const accion = async (ruta: "enviar" | "descartar", ids: string[]) => {
    try {
      const out = await post<Envio[]>(`/api/v1/cobranza/automatica/envios/${ruta}`, { ids });
      if (ruta === "enviar") {
        const ok = out.filter((e) => e.estado === "ENVIADO").length;
        const mal = out.length - ok;
        if (ok) toast.success(`${ok} ${ok === 1 ? "estado de cuenta enviado" : "estados de cuenta enviados"}.`);
        if (mal) toast.error(`${mal} no ${mal === 1 ? "salió" : "salieron"}: revisa el motivo en la tabla.`);
      } else {
        toast.success(`${out.length} descartado${out.length === 1 ? "" : "s"}.`);
      }
      setReset((n) => n + 1);
      res.reload();
    } catch (e) {
      toast.error(errorDe(e, "No se pudo completar la acción."));
    }
  };

  const generar = async () => {
    try {
      const r = await post<{ creados: number; omitidos: number }>("/api/v1/cobranza/automatica/generar");
      toast.success(r.creados
        ? `Cola armada: ${r.creados} estado${r.creados === 1 ? "" : "s"} de cuenta por revisar.`
        : "No hubo nada nuevo que agregar a la cola de hoy.");
      setFiltro(FILTROS[0].key);
      res.reload();
      onCambio();
    } catch (e) {
      toast.error(errorDe(e, "No se pudo armar la cola."));
    }
  };

  const verPdf = (e: Envio) => {
    const win = window.open("", "_blank");
    const qs = new URLSearchParams();
    if (e.serie) qs.set("serie", e.serie);
    apiOpenInTab(`/api/v1/cobranza/estado-cuenta/${e.cliente_id}/pdf${qs.size ? `?${qs}` : ""}`, win)
      .catch((err) => toast.error(errorDe(err, "No se pudo abrir el PDF.")));
  };

  const cols = useMemo<Column<Envio>[]>(() => [
    { header: "Corte", className: "whitespace-nowrap", sortValue: (e) => e.corte,
      exportValue: (e) => e.corte, cell: (e) => fmtDate(e.corte) },
    { header: "Cliente", truncate: true, sortValue: (e) => e.cliente, exportValue: (e) => e.cliente,
      cell: (e) => (
        <Link href={`/clientes/${e.cliente_id}/estado-cuenta${e.serie ? `?serie=${e.serie}` : ""}`}
              className="font-medium hover:underline" title={e.cliente}>{e.cliente}</Link>
      ) },
    { header: "Serie", sortValue: (e) => e.serie ?? "", exportValue: (e) => e.serie ?? "Todas",
      cell: (e) => e.serie ?? <span className="text-muted">Todas</span> },
    { header: "Para", truncate: true, exportValue: (e) => [...e.para, ...e.cc.map((c) => `cc ${c}`)].join(", "),
      cell: (e) => e.para.length
        ? <span title={[...e.para, ...e.cc.map((c) => `cc: ${c}`)].join("\n")}>
            {e.para.join(", ")}{e.cc.length ? <span className="text-muted"> +{e.cc.length} cc</span> : null}
          </span>
        : <span className="text-danger">sin correo</span> },
    { header: "Saldo", className: "text-right tabular-nums", sortValue: (e) => Number(e.saldo),
      exportValue: (e) => Number(e.saldo), cell: (e) => fmtMoney(e.saldo) },
    { header: "Vencido", className: "text-right tabular-nums", sortValue: (e) => Number(e.vencido),
      exportValue: (e) => Number(e.vencido),
      cell: (e) => Number(e.vencido) > 0 ? <span className="text-danger">{fmtMoney(e.vencido)}</span> : "—" },
    { header: "Días", className: "text-right tabular-nums", sortValue: (e) => e.dias_max_vencida,
      exportValue: (e) => e.dias_max_vencida,
      cell: (e) => (
        <span className="inline-flex items-center gap-1.5">
          {e.escalado && <Badge tone="danger">escalado</Badge>}
          {e.dias_max_vencida > 0 ? e.dias_max_vencida : "—"}
        </span>
      ) },
    { header: "Estado", sortValue: (e) => e.estado, exportValue: (e) => e.estado,
      cell: (e) => <Badge tone={TONO[e.estado]}>{e.estado.toLowerCase()}</Badge> },
    { header: "Detalle", truncate: true, exportValue: (e) => e.error ?? (e.enviado_at ?? ""),
      cell: (e) => e.error
        ? <span className="text-xs text-muted" title={e.error}>{e.error}</span>
        : e.enviado_at ? <span className="text-xs text-muted">{fmtDateTime(e.enviado_at)}</span> : "—" },
  ], []);

  const acciones = useMemo<RowAction<Envio>[]>(() => [
    { id: "pdf", icon: <FileText size={15} />, label: "Ver el PDF que se enviaría", onClick: verPdf },
    ...(canWrite ? [
      { id: "enviar", icon: <Send size={15} />, label: "Aprobar y enviar", tone: "success" as const,
        onClick: (e: Envio) => { if (e.estado === "PENDIENTE" || e.estado === "ERROR") void accion("enviar", [e.id]); } },
      { id: "descartar", icon: <X size={15} />, label: "Descartar", tone: "danger" as const,
        onClick: (e: Envio) => { if (e.estado === "PENDIENTE" || e.estado === "ERROR") void accion("descartar", [e.id]); } },
    ] : []),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [canWrite]);

  const enviables = sel.filter((e) => e.estado === "PENDIENTE" || e.estado === "ERROR");

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1 rounded-lg border border-border p-1">
          {FILTROS.map((f) => (
            <button key={f.label} type="button" onClick={() => { setFiltro(f.key); setReset((n) => n + 1); }}
                    className={`rounded-md px-3 py-1 text-sm ${filtro === f.key ? "bg-surface-2 font-medium" : "text-muted hover:text-foreground"}`}>
              {f.label}
            </button>
          ))}
        </div>
        {canWrite && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={generar} disabled={loading}>
              <RefreshCw size={15} /> Armar la cola de hoy
            </Button>
            <Button variant="secondary" disabled={loading || enviables.length === 0}
                    onClick={() => accion("descartar", enviables.map((e) => e.id))}>
              <Trash2 size={15} /> Descartar ({enviables.length})
            </Button>
            <Button disabled={loading || enviables.length === 0}
                    onClick={() => accion("enviar", enviables.map((e) => e.id))}>
              <Send size={15} /> {loading ? "Enviando…" : `Aprobar y enviar (${enviables.length})`}
            </Button>
          </div>
        )}
      </div>
      {res.error ? (
        <Alert tone="danger">No se pudo cargar la cola.</Alert>
      ) : (
        <DataTableSmart
          rows={res.data ?? []}
          loading={!res.data}
          rowKey={(e) => e.id}
          columns={cols}
          actions={acciones}
          storageKey="cobranza-auto-envios"
          exportFilename="cobranza-automatica"
          selectable={canWrite}
          selectableRow={(e) => e.estado === "PENDIENTE" || e.estado === "ERROR"}
          onSelectionChange={setSel}
          selectionResetKey={reset}
          empty={filtro.startsWith("PENDIENTE")
            ? "Nada por enviar. La cola se arma sola el día programado, o con «Armar la cola de hoy»."
            : "Sin envíos en este filtro."}
        />
      )}
    </Card>
  );
}

// ─── Contactos ───────────────────────────────────────────────────────────────

function Contactos({ canWrite }: { canWrite: boolean }) {
  const res = useResource<FilaContactos[]>("/api/v1/cobranza/automatica/contactos");
  const clientesRes = useResource<Pagina<Cliente>>("/api/v1/clientes?limit=1000");
  const fichas = useMemo(() => Object.fromEntries((clientesRes.data?.items ?? []).map((c) => [c.id, c])),
    [clientesRes.data]);
  const [editar, setEditar] = useState<FilaContactos | null>(null);

  const cols = useMemo<Column<FilaContactos>[]>(() => [
    { header: "Cliente", truncate: true, sortValue: (f) => f.cliente, exportValue: (f) => f.cliente,
      cell: (f) => <span className="font-medium" title={f.cliente}>{f.cliente}</span> },
    { header: "Saldo", className: "text-right tabular-nums", sortValue: (f) => Number(f.saldo),
      exportValue: (f) => Number(f.saldo), cell: (f) => fmtMoney(f.saldo) },
    { header: "Series con saldo", exportValue: (f) => f.series.map((s) => s.serie || "—").join(", "),
      cell: (f) => <span className="text-muted">{f.series.map((s) => s.serie || "—").join(", ") || "—"}</span> },
    { header: "Correos de cobranza", truncate: true,
      exportValue: (f) => f.contactos.map((c) => `${c.serie ?? "Todas"}: ${deLista(c.correos)}`).join(" | "),
      cell: (f) => {
        if (f.contactos.length === 0) return <span className="text-danger">sin capturar</span>;
        return (
          <div className="space-y-0.5">
            {f.contactos.map((c) => (
              <div key={c.id} className="truncate text-sm">
                <span className="text-xs text-muted">{c.serie ?? "Todas"}:</span>{" "}
                {c.correos.length ? deLista(c.correos) : <span className="text-danger">sin correo</span>}
                {c.pausado && <span className="ml-1.5"><Badge tone="warning">en pausa</Badge></span>}
              </div>
            ))}
          </div>
        );
      } },
  ], []);

  const acciones = useMemo<RowAction<FilaContactos>[]>(
    () => canWrite ? [{ id: "editar", icon: <Pencil size={15} />, label: "Editar contactos", onClick: setEditar }] : [],
    [canWrite]);

  return (
    <Card>
      <p className="mb-3 text-sm text-muted">
        A quién se le manda el estado de cuenta. Un contacto para <b>todas las series</b> del cliente
        y, si alguna plaza la paga otra persona, uno <b>por serie</b>. «En pausa» saca al cliente (o a esa
        serie) de la cobranza automática, por ejemplo con un convenio de pago.
      </p>
      {res.error ? <Alert tone="danger">No se pudieron cargar los contactos.</Alert> : (
        <DataTableSmart
          rows={res.data ?? []}
          loading={!res.data}
          rowKey={(f) => f.cliente_id}
          columns={cols}
          actions={acciones}
          onRowClick={canWrite ? setEditar : undefined}
          storageKey="cobranza-auto-contactos"
          exportFilename="contactos-cobranza"
          empty="Ningún cliente tiene saldo pendiente."
        />
      )}
      {editar && (
        <EditarContactos fila={editar} ficha={fichas[editar.cliente_id]}
                         onClose={() => setEditar(null)} onGuardado={() => res.reload()} />
      )}
    </Card>
  );
}

type Borrador = { serie: string; correos: string; cc: string; pausado: boolean; motivo: string; id?: string };

function EditarContactos({ fila, ficha, onClose, onGuardado }: {
  fila: FilaContactos; ficha?: Cliente; onClose: () => void; onGuardado: () => void;
}) {
  const toast = useToast();
  const { put, del, loading } = useMutation();
  const sugeridos = correosDeFicha(ficha);
  const inicial = (): Borrador[] => {
    const general = fila.contactos.find((c) => !c.serie);
    const filas: Borrador[] = [{
      serie: "", id: general?.id, correos: general ? deLista(general.correos) : "",
      cc: deLista(general?.cc), pausado: general?.pausado ?? false, motivo: general?.motivo_pausa ?? "",
    }];
    for (const c of fila.contactos.filter((c) => c.serie)) {
      filas.push({ serie: c.serie!, id: c.id, correos: deLista(c.correos), cc: deLista(c.cc),
                   pausado: c.pausado, motivo: c.motivo_pausa ?? "" });
    }
    return filas;
  };
  const [filas, setFilas] = useState<Borrador[]>(inicial);
  const [quitar, setQuitar] = useState<string[]>([]);
  const libres = fila.series.map((s) => s.serie).filter((s) => s && !filas.some((f) => f.serie === s));
  const cambiar = (i: number, cambio: Partial<Borrador>) =>
    setFilas((fs) => fs.map((f, j) => (j === i ? { ...f, ...cambio } : f)));

  const guardar = async () => {
    try {
      for (const id of quitar) await del(`/api/v1/cobranza/automatica/contactos/${id}`);
      for (const f of filas) {
        // La fila «todas las series» sin nada capturado no se guarda.
        if (!f.serie && !f.id && !f.correos.trim() && !f.cc.trim() && !f.pausado) continue;
        await put("/api/v1/cobranza/automatica/contactos", {
          cliente_id: fila.cliente_id, serie: f.serie || null,
          correos: aLista(f.correos), cc: aLista(f.cc), pausado: f.pausado, motivo_pausa: f.motivo || null,
        });
      }
      toast.success("Contactos guardados.");
      onGuardado();
      onClose();
    } catch (e) {
      toast.error(errorDe(e, "No se pudieron guardar los contactos."));
    }
  };

  return (
    <Modal open onClose={onClose} title={`Contactos de cobranza — ${fila.cliente}`} wide
           footer={
             <div className="flex justify-end gap-2">
               <Button variant="secondary" onClick={onClose}>Cancelar</Button>
               <Button onClick={guardar} disabled={loading}>{loading ? "Guardando…" : "Guardar"}</Button>
             </div>
           }>
      <div className="space-y-4">
        {filas.map((f, i) => (
          <div key={f.serie || "todas"} className="rounded-xl border border-border p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="text-sm font-semibold">
                {f.serie ? `Serie ${f.serie}` : "Todas las series"}
                {!f.serie && filas.length > 1 && (
                  <span className="ml-2 text-xs font-normal text-muted">(las que no tienen contacto propio)</span>
                )}
              </div>
              {f.serie && (
                <button type="button" className="text-xs text-danger hover:underline"
                        onClick={() => { if (f.id) setQuitar((q) => [...q, f.id!]); setFilas((fs) => fs.filter((_, j) => j !== i)); }}>
                  Quitar
                </button>
              )}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Para" hint="Separa varios correos con coma.">
                <Input value={f.correos} onChange={(e) => cambiar(i, { correos: e.target.value })}
                       placeholder="cuentasporpagar@cliente.com" />
              </Field>
              <Field label="Con copia">
                <Input value={f.cc} onChange={(e) => cambiar(i, { cc: e.target.value })} placeholder="opcional" />
              </Field>
            </div>
            {!f.correos.trim() && sugeridos.length > 0 && (
              <button type="button" className="mt-2 text-xs text-accent hover:underline"
                      onClick={() => cambiar(i, { correos: sugeridos.join(", ") })}>
                Usar los correos de la ficha del cliente ({sugeridos.join(", ")})
              </button>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={f.pausado} onChange={(v) => cambiar(i, { pausado: v })} />
                {f.pausado ? <><Pause size={14} /> En pausa</> : <><Play size={14} /> Recibe cobranza</>}
              </label>
              {f.pausado && (
                <Input className="max-w-sm" value={f.motivo} placeholder="Motivo (p. ej. convenio de pago)"
                       onChange={(e) => cambiar(i, { motivo: e.target.value })} />
              )}
            </div>
          </div>
        ))}
        {libres.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted">Agregar un contacto para la serie:</span>
            {libres.map((s) => (
              <button key={s} type="button" className="rounded-full border border-border px-2.5 py-0.5 hover:bg-surface-2"
                      onClick={() => setFilas((fs) => [...fs, { serie: s, correos: "", cc: "", pausado: false, motivo: "" }])}>
                + {s}
              </button>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

// ─── Ajustes ─────────────────────────────────────────────────────────────────

function Ajustes({ cfg, canWrite, onGuardado }: { cfg: Config; canWrite: boolean; onGuardado: (c: Config) => void }) {
  const toast = useToast();
  const { put, loading } = useMutation();
  const [f, setF] = useState(() => ({
    ...cfg, saldo_minimo: String(cfg.saldo_minimo), escalar_cc: deLista(cfg.escalar_cc),
    cc_siempre: deLista(cfg.cc_siempre), asunto: cfg.asunto ?? "", mensaje: cfg.mensaje ?? "",
  }));
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));

  const guardar = async () => {
    try {
      const c = await put<Config>("/api/v1/cobranza/automatica/config", {
        activo: f.activo, modo: f.modo, dia_semana: Number(f.dia_semana), hora: Number(f.hora),
        incluir_por_vencer: f.incluir_por_vencer, saldo_minimo: Number(f.saldo_minimo || 0),
        escalar_dias: Number(f.escalar_dias || 0), escalar_cc: aLista(f.escalar_cc),
        cc_siempre: aLista(f.cc_siempre), adjuntar_pdf: f.adjuntar_pdf, adjuntar_excel: f.adjuntar_excel,
        espejo_max_horas: Number(f.espejo_max_horas || 0), asunto: f.asunto || null, mensaje: f.mensaje || null,
      });
      onGuardado(c);
      toast.success("Ajustes guardados.");
    } catch (e) {
      toast.error(errorDe(e, "No se pudieron guardar los ajustes."));
    }
  };

  const fila = "flex items-start justify-between gap-4 border-b border-border py-3 last:border-0";
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Cuándo y cómo">
        <div className={fila}>
          <div>
            <div className="text-sm font-medium">Cobranza automática encendida</div>
            <div className="text-xs text-muted">Apagada, no se arma la cola ni sale nada.</div>
          </div>
          <Switch checked={f.activo} disabled={!canWrite} onChange={(v) => set("activo", v)} />
        </div>
        <div className={fila}>
          <div>
            <div className="text-sm font-medium">Modo</div>
            <div className="text-xs text-muted">
              En revisión, la cola espera a que alguien apruebe. Automático, sale sola en cuanto se arma.
            </div>
          </div>
          <Select className="w-44" value={f.modo} disabled={!canWrite}
                  onChange={(e) => set("modo", e.target.value as Config["modo"])}>
            <option value="REVISION">En revisión</option>
            <option value="AUTOMATICO">Automático</option>
          </Select>
        </div>
        <div className={fila}>
          <div>
            <div className="text-sm font-medium">Día y hora del envío semanal</div>
            <div className="text-xs text-muted">Hora de la Ciudad de México.</div>
          </div>
          <div className="flex gap-2">
            <Select className="w-36" value={String(f.dia_semana)} disabled={!canWrite}
                    onChange={(e) => set("dia_semana", Number(e.target.value))}>
              {DIAS.map((d, i) => <option key={d} value={String(i)}>{d}</option>)}
            </Select>
            <Select className="w-24" value={String(f.hora)} disabled={!canWrite}
                    onChange={(e) => set("hora", Number(e.target.value))}>
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={String(h)}>{String(h).padStart(2, "0")}:00</option>
              ))}
            </Select>
          </div>
        </div>
        <div className={fila}>
          <div>
            <div className="text-sm font-medium">Candado del espejo de SAE</div>
            <div className="text-xs text-muted">
              No se cobra si los pagos de SAE no se sincronizaron en estas horas. 0 = sin candado.
            </div>
          </div>
          <Input className="w-24 text-right" type="number" min={0} max={168} value={f.espejo_max_horas}
                 disabled={!canWrite} onChange={(e) => set("espejo_max_horas", Number(e.target.value))} />
        </div>
      </Card>

      <Card title="Qué incluye">
        <div className={fila}>
          <div>
            <div className="text-sm font-medium">Incluir facturas por vencer</div>
            <div className="text-xs text-muted">Apagado, solo se cobran las vencidas (y sin vencidas no sale correo).</div>
          </div>
          <Switch checked={f.incluir_por_vencer} disabled={!canWrite} onChange={(v) => set("incluir_por_vencer", v)} />
        </div>
        <div className={fila}>
          <div>
            <div className="text-sm font-medium">Saldo mínimo</div>
            <div className="text-xs text-muted">Por debajo de esto no se manda estado de cuenta.</div>
          </div>
          <Input className="w-32 text-right" type="number" min={0} value={f.saldo_minimo}
                 disabled={!canWrite} onChange={(e) => set("saldo_minimo", e.target.value)} />
        </div>
        <div className={fila}>
          <div className="text-sm font-medium">Adjuntos</div>
          <div className="flex flex-col gap-2 text-sm">
            <label className="flex items-center gap-2">
              <Switch checked={f.adjuntar_pdf} disabled={!canWrite} onChange={(v) => set("adjuntar_pdf", v)} /> PDF
            </label>
            <label className="flex items-center gap-2">
              <Switch checked={f.adjuntar_excel} disabled={!canWrite} onChange={(v) => set("adjuntar_excel", v)} /> Excel (formato SAE)
            </label>
          </div>
        </div>
      </Card>

      <Card title="Copias y escalamiento">
        <div className="space-y-3">
          <Field label="Copia en todos los envíos" hint="Por ejemplo, el correo de cobranza del negocio.">
            <Input value={f.cc_siempre} disabled={!canWrite} onChange={(e) => set("cc_siempre", e.target.value)}
                   placeholder="cobranza@tuempresa.com" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
            <Field label="Escalar a los" hint="días de vencida">
              <Input type="number" min={0} max={365} value={f.escalar_dias} disabled={!canWrite}
                     onChange={(e) => set("escalar_dias", Number(e.target.value))} />
            </Field>
            <Field label="Copiar al escalar" hint="Cuando la factura más vieja pasa esos días. 0 días = nunca.">
              <Input value={f.escalar_cc} disabled={!canWrite} onChange={(e) => set("escalar_cc", e.target.value)}
                     placeholder="direccion@tuempresa.com, vendedor@tuempresa.com" />
            </Field>
          </div>
        </div>
      </Card>

      <Card title="El correo">
        <div className="space-y-3">
          <Field label="Asunto" hint="Vacío = «Estado de cuenta al dd/mm/aaaa».">
            <Input value={f.asunto} disabled={!canWrite} onChange={(e) => set("asunto", e.target.value)} maxLength={200} />
          </Field>
          <Field label="Mensaje" hint="Va antes del resumen de saldo que el sistema agrega solo.">
            <Textarea rows={5} value={f.mensaje} disabled={!canWrite} onChange={(e) => set("mensaje", e.target.value)}
                      placeholder="Buen día, les compartimos su estado de cuenta…" />
          </Field>
        </div>
      </Card>

      {canWrite && (
        <div className="lg:col-span-2 flex justify-end">
          <Button onClick={guardar} disabled={loading}>{loading ? "Guardando…" : "Guardar ajustes"}</Button>
        </div>
      )}
    </div>
  );
}

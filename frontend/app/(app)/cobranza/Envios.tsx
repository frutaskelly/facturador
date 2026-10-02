"use client";

// Cobranza → Envíos: el estado de cuenta, configurado por separado para cada
// envío (dueño, 2-oct-2026). Un envío es una razón social sola o varias juntas
// (EHMO + SUREÑA + MAFAN) con TODO lo suyo: a quién, qué incluye, cómo se ve la
// tabla, y si sale solo (Automático, su día y hora) o con el botón (Manual).
// En la lista: el interruptor Automático/Manual y «Enviar» con un clic. Abajo,
// las razones sociales con saldo a las que ningún envío les cobra. Lo único
// general vive en «Ajustes generales»: el interruptor maestro de los
// automáticos, el candado del espejo de SAE y la copia fija a todos.
// Antes esto eran tres pestañas (cola, Contactos y Ajustes).
import { useMemo, useRef, useState } from "react";
import { History, Mail, Pencil, Plus, Send, Settings, Trash2, X } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTableSmart, type Column, type RowAction } from "@/components/ui/DataTableSmart";
import { Field, Input, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { fmtDate, fmtDateTime, fmtMoney } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";

import {
  AGRUPAR, BASE, EditarEnvio, Opciones, VistaPrevia, borradorDe, errorDe,
  type Bitacora, type Borrador, type Envio, type Modo, type Opcion,
} from "./EditarEnvio";

type Config = {
  activo: boolean; espejo_max_horas: number; cc_siempre: string[]; zona: string;
  espejo_ok: boolean; espejo_ultima: string | null;
};
type SinEnvio = {
  cliente_id: string; nombre: string; legal_name: string; saldo: string; facturas: number;
  parcial: boolean; correos: string[];
};
type Lista = { envios: Envio[]; sin_envio: SinEnvio[] };

const AUTO = "/api/v1/cobranza/automatica";
const AGRUPAR_LABEL = Object.fromEntries(AGRUPAR.map((a) => [a.key, a.label]));
const TONO: Record<Bitacora["estado"], "warning" | "accent" | "success" | "danger" | "muted"> = {
  PENDIENTE: "warning", ENVIANDO: "accent", ENVIADO: "success", ERROR: "danger", DESCARTADO: "muted",
};
const ESTADO_TEXTO: Record<Bitacora["estado"], string> = {
  PENDIENTE: "espera al espejo", ENVIANDO: "enviando", ENVIADO: "enviado", ERROR: "no salió", DESCARTADO: "descartado",
};
const cuandoDe = (b: Bitacora) => b.enviado_at ?? b.created_at;
// El próximo viene con la fecha de la Ciudad de México («2026-10-05T08:00-06:00»):
// se lee su fecha tal cual y la hora del envío, no la del reloj de esta computadora.
const proximoTexto = (e: Envio) =>
  e.proximo ? `${fmtDate(e.proximo.slice(0, 10))} a las ${String(e.hora).padStart(2, "0")}:00` : "";

export function Envios({ canWrite }: { canWrite: boolean }) {
  const toast = useToast();
  const { post, patch, del, loading } = useMutation();
  const cfgRes = useResource<Config>(`${AUTO}/config`);
  const res = useResource<Lista>(BASE);
  const opcRes = useResource<Opcion[]>(`${BASE}/opciones`);
  const cfg = cfgRes.data;
  const envios = useMemo(() => res.data?.envios ?? [], [res.data]);

  const [vista, setVista] = useState<"envios" | "historial">("envios");
  const [deEnvio, setDeEnvio] = useState<Envio | null>(null);
  const [editar, setEditar] = useState<Borrador | null>(null);
  const [ver, setVer] = useState<{ b: Borrador; envio?: Envio } | null>(null);
  const [borrar, setBorrar] = useState<Envio | null>(null);
  const [ajustes, setAjustes] = useState(false);
  // Los que van saliendo: un segundo clic (o la vista previa) no manda dos.
  const enCurso = useRef(new Set<string>());
  const [sel, setSel] = useState<Envio[]>([]);
  const [reset, setReset] = useState(0);

  const recargar = () => { res.reload(); opcRes.reload(); };

  /** Manda un envío ahora. Devuelve si salió (la vista previa se cierra). */
  const enviar = async (e: Envio): Promise<boolean> => {
    if (enCurso.current.has(e.id)) return false;
    enCurso.current.add(e.id);
    try {
      const r = await post<{ envio: Bitacora; grupo: Envio }>(`${BASE}/${e.id}/enviar`);
      res.setData((d) => d && { ...d, envios: d.envios.map((x) => (x.id === e.id ? r.grupo : x)) });
      if (r.envio.estado === "ENVIADO") {
        toast.success(`«${e.nombre}» enviado a ${r.envio.para.join(", ")}.`);
        return true;
      }
      toast.error(`«${e.nombre}» no salió: ${r.envio.error ?? r.envio.estado}`);
      return false;
    } catch (err) {
      toast.error(errorDe(err, `No se pudo enviar «${e.nombre}».`));
      return false;
    } finally {
      enCurso.current.delete(e.id);
    }
  };

  const cambiarModo = async (e: Envio, modo: Modo) => {
    try {
      const g = await patch<Envio>(`${BASE}/${e.id}/modo`, { modo });
      res.setData((d) => d && { ...d, envios: d.envios.map((x) => (x.id === e.id ? g : x)) });
      toast.success(modo === "AUTOMATICO"
        ? `«${e.nombre}» sale solo: ${g.cuando.toLowerCase()}.`
        : `«${e.nombre}» ahora solo sale con el botón Enviar.`);
    } catch (err) {
      toast.error(errorDe(err, "No se pudo cambiar el modo."));
    }
  };

  const enviarSeleccionados = async () => {
    try {
      const out = await post<{ nombre: string; estado: string; error: string | null }[]>(
        `${BASE}/enviar`, { ids: sel.map((e) => e.id) });
      const ok = out.filter((x) => x.estado === "ENVIADO").length;
      const mal = out.filter((x) => x.estado !== "ENVIADO");
      if (ok) toast.success(`${ok} ${ok === 1 ? "estado de cuenta enviado" : "estados de cuenta enviados"}.`);
      if (mal.length) toast.error(`No salieron: ${mal.map((x) => `${x.nombre} (${x.error ?? x.estado})`).join("; ")}`);
      setReset((n) => n + 1);
      res.reload();
    } catch (err) {
      toast.error(errorDe(err, "No se pudieron enviar."));
    }
  };

  const confirmarBorrar = async () => {
    if (!borrar) return;
    try {
      await del(`${BASE}/${borrar.id}`);
      toast.success(`Envío «${borrar.nombre}» borrado.`);
      setBorrar(null);
      recargar();
    } catch (err) {
      toast.error(errorDe(err, "No se pudo borrar el envío."));
    }
  };

  const cols = useMemo<Column<Envio>[]>(() => [
    { header: "Envío", sortValue: (e) => e.nombre, exportValue: (e) => e.nombre,
      cell: (e) => (
        <div className="min-w-[13rem] max-w-[22rem]">
          <div className="truncate font-medium" title={e.nombre}>{e.nombre}</div>
          <div className="truncate text-xs text-muted" title={e.alcance.map((a) => a.cliente).join(", ")}>
            {e.alcance.map((a) => a.cliente + (a.completo ? "" : " (parcial)")).join(", ")}
            {" · por "}{(AGRUPAR_LABEL[e.agrupar_por] ?? "").toLowerCase()}
            {e.nota ? ` · ${e.nota}` : ""}
          </div>
          {e.tambien_en.length > 0 && (
            <div className="truncate text-xs text-amber-700" title="Esas facturas se cobran en los dos correos">
              también va en {e.tambien_en.map((t) => `«${t.nombre}»`).join(", ")}
            </div>
          )}
        </div>
      ) },
    { header: "Para", truncate: true, exportValue: (e) => e.correos.join(", "),
      cell: (e) => e.correos.length
        ? <span title={[...e.correos, ...e.cc.map((c) => `cc: ${c}`)].join("\n")}>
            {e.correos.join(", ")}{e.cc.length ? <span className="text-muted"> +{e.cc.length} cc</span> : null}
          </span>
        : <span className="text-danger">falta correo</span> },
    { header: "Saldo", className: "text-right tabular-nums", sortValue: (e) => Number(e.saldo),
      exportValue: (e) => Number(e.saldo), cell: (e) => fmtMoney(e.saldo) },
    { header: "Vencido", className: "text-right tabular-nums", sortValue: (e) => Number(e.vencido),
      exportValue: (e) => Number(e.vencido),
      cell: (e) => Number(e.vencido) > 0 ? <span className="text-danger">{fmtMoney(e.vencido)}</span> : "—" },
    { header: "Modo", sortValue: (e) => e.modo, exportValue: (e) => e.modo,
      cell: (e) => (
        // El clic en el interruptor no abre el editor de la fila.
        <span className="inline-flex items-center gap-2 whitespace-nowrap text-sm" onClick={(ev) => ev.stopPropagation()}>
          <Switch checked={e.modo === "AUTOMATICO"} disabled={!canWrite}
                  onChange={(v) => cambiarModo(e, v ? "AUTOMATICO" : "MANUAL")} />
          {e.modo === "AUTOMATICO" ? "Automático" : "Manual"}
        </span>
      ) },
    { header: "Cuándo", exportValue: (e) => (e.modo === "AUTOMATICO" ? e.cuando : "Con el botón"),
      sortValue: (e) => e.proximo ?? "",
      cell: (e) => e.modo === "MANUAL" ? <span className="text-muted">Con el botón</span> : (
        <div className="text-sm">
          <div>{e.cuando}</div>
          <div className="text-xs text-muted">
            {e.proximo ? `próximo: ${fmtDate(e.proximo.slice(0, 10))}` : <span className="text-amber-700">automáticos apagados</span>}
          </div>
        </div>
      ) },
    { header: "Último envío", sortValue: (e) => (e.ultimo ? cuandoDe(e.ultimo) : ""),
      exportValue: (e) => (e.ultimo ? `${e.ultimo.estado} ${cuandoDe(e.ultimo)}` : ""),
      cell: (e) => e.ultimo ? (
        <div className="text-sm" title={e.ultimo.error ?? undefined}>
          <Badge tone={TONO[e.ultimo.estado]}>{ESTADO_TEXTO[e.ultimo.estado]}</Badge>
          <div className="text-xs text-muted">
            {fmtDateTime(cuandoDe(e.ultimo))} · {e.ultimo.origen === "PROGRAMADO" ? "automático" : "botón"}
          </div>
        </div>
      ) : <span className="text-muted">nunca</span> },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [canWrite]);

  // «Enviar ahora» va primero y en la columna fija de la derecha: siempre a la
  // vista, con un clic, y el ícono gira mientras sale.
  const acciones = useMemo<RowAction<Envio>[]>(() => [
    ...(canWrite ? [{
      id: "enviar", icon: <Send size={15} />, label: "Enviar ahora", tone: "success" as const,
      onClick: (e: Envio) => enviar(e),
      disabled: (e: Envio) => (e.correos.length === 0 ? "Falta el correo: edita el envío." : false),
    }] : []),
    { id: "ver", icon: <Mail size={15} />, label: "Ver el correo", onClick: (e) => setVer({ b: borradorDe(e), envio: e }) },
    { id: "historial", icon: <History size={15} />, label: "Historial de este envío",
      onClick: (e) => { setDeEnvio(e); setVista("historial"); } },
    ...(canWrite ? [
      { id: "editar", icon: <Pencil size={15} />, label: "Editar", onClick: (e: Envio) => setEditar(borradorDe(e)) },
      { id: "borrar", icon: <Trash2 size={15} />, label: "Borrar", tone: "danger" as const, onClick: setBorrar },
    ] : []),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [canWrite]);

  const proximo = envios.filter((e) => e.proximo).sort((a, b) => (a.proximo! < b.proximo! ? -1 : 1))[0];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <div className="text-xs text-muted">Automáticos</div>
          <div className="mt-1 flex items-center gap-2">
            {cfg && (
              <>
                {cfg.activo ? <Badge tone="success">Encendidos</Badge> : <Badge tone="muted">Apagados</Badge>}
                <span className="text-sm text-muted">{cfg.activo ? "los envíos en Automático salen solos" : "nada sale solo"}</span>
              </>
            )}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">Próximo automático</div>
          {proximo ? (
            <>
              <div className="mt-1 truncate font-semibold" title={proximo.nombre}>{proximo.nombre}</div>
              <div className="text-xs text-muted">{proximoTexto(proximo)}</div>
            </>
          ) : <div className="mt-1 text-sm text-muted">Ninguno programado</div>}
        </Card>
        <Card>
          <div className="text-xs text-muted">Espejo de SAE (pagos)</div>
          {cfg && (
            <>
              <div className="mt-1 font-semibold">
                {cfg.espejo_max_horas === 0
                  ? <span className="text-muted">Sin candado</span>
                  : cfg.espejo_ok ? <span className="text-success">Al día</span> : <span className="text-danger">Desactualizado</span>}
              </div>
              <div className="text-xs text-muted">
                {cfg.espejo_ultima ? `Última pasada buena: ${fmtDateTime(cfg.espejo_ultima)}` : "Sin pasadas registradas"}
              </div>
            </>
          )}
        </Card>
      </div>

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <Opciones chico valor={vista} onChange={(v) => { setVista(v); if (v === "envios") setDeEnvio(null); }}
                    opciones={[{ key: "envios", label: "Envíos" }, { key: "historial", label: "Historial" }]} />
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setAjustes(true)}><Settings size={15} /> Ajustes generales</Button>
            {canWrite && vista === "envios" && sel.length > 0 && (
              <Button variant="secondary" onClick={enviarSeleccionados} disabled={loading}>
                <Send size={15} /> {loading ? "Enviando…" : `Enviar seleccionados (${sel.length})`}
              </Button>
            )}
            {canWrite && (
              <Button onClick={() => setEditar(borradorDe(null))}><Plus size={16} /> Nuevo envío</Button>
            )}
          </div>
        </div>

        {vista === "historial" ? (
          <Historial envio={deEnvio} onQuitarFiltro={() => setDeEnvio(null)} />
        ) : res.error ? <Alert tone="danger">No se pudieron cargar los envíos.</Alert> : (
          <DataTableSmart
            rows={envios}
            loading={!res.data}
            rowKey={(e) => e.id}
            columns={cols}
            actions={acciones}
            onRowClick={canWrite ? (e) => setEditar(borradorDe(e)) : (e) => setVer({ b: borradorDe(e), envio: e })}
            storageKey="cobranza-envios"
            exportFilename="envios-cobranza"
            selectable={canWrite}
            selectableRow={(e) => e.correos.length > 0}
            onSelectionChange={setSel}
            selectionResetKey={reset}
            empty="Todavía no hay envíos. Crea uno con «Nuevo envío» o desde las razones sociales de abajo."
          />
        )}
      </Card>

      {vista === "envios" && (res.data?.sin_envio.length ?? 0) > 0 && (
        <Card title="Razones sociales con saldo sin envío"
              subtitle="Nadie les está mandando estado de cuenta de esto. Crea su envío, solo o junto con otras.">
          <div className="divide-y divide-border">
            {res.data!.sin_envio.map((s) => (
              <div key={s.cliente_id} className="flex flex-wrap items-center gap-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium" title={s.legal_name}>{s.legal_name}</div>
                  <div className="text-xs text-muted">
                    {s.facturas} {s.facturas === 1 ? "factura" : "facturas"}
                    {s.parcial ? " que su envío no incluye" : ""}
                    {s.correos.length ? ` · ficha: ${s.correos.join(", ")}` : " · sin correo en la ficha"}
                  </div>
                </div>
                <span className="text-sm tabular-nums">{fmtMoney(s.saldo)}</span>
                {canWrite && (
                  <Button variant="secondary" className="px-2.5 py-1 text-xs"
                          onClick={() => setEditar(borradorDe(null, s))}>
                    <Plus size={13} /> Crear envío
                  </Button>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {editar && (
        <EditarEnvio
          inicial={editar} opciones={opcRes.data ?? []} cargando={!opcRes.data}
          envios={envios} automaticosEncendidos={cfg?.activo ?? false} canWrite={canWrite}
          onClose={() => setEditar(null)}
          onGuardado={() => { setEditar(null); recargar(); }}
          onPrevio={(b) => setVer({ b })}
        />
      )}
      {ver && (
        <VistaPrevia borrador={ver.b} canWrite={canWrite} onClose={() => setVer(null)}
                     onEnviar={ver.envio ? () => enviar(ver.envio!) : undefined} />
      )}
      {ajustes && cfg && (
        <AjustesGenerales cfg={cfg} canWrite={canWrite} onClose={() => setAjustes(false)}
                          onGuardado={(c) => { cfgRes.setData(c); res.reload(); setAjustes(false); }} />
      )}
      {borrar && (
        <Modal open size="sm" title="Borrar envío" onClose={() => setBorrar(null)}
               footer={
                 <div className="flex justify-end gap-2">
                   <Button variant="secondary" onClick={() => setBorrar(null)}>Cancelar</Button>
                   <Button variant="danger" onClick={confirmarBorrar} disabled={loading}>
                     {loading ? "Borrando…" : "Borrar"}
                   </Button>
                 </div>
               }>
          <p className="text-sm">
            ¿Borrar el envío <b>{borrar.nombre}</b>? Sus razones sociales quedan sin estado de cuenta hasta que les
            crees otro. El historial de lo que ya salió se conserva.
          </p>
        </Modal>
      )}
    </div>
  );
}

// ─── Historial ───────────────────────────────────────────────────────────────

function Historial({ envio, onQuitarFiltro }: { envio: Envio | null; onQuitarFiltro: () => void }) {
  const res = useResource<Bitacora[]>(`${AUTO}/envios?dias=365${envio ? `&grupo_id=${envio.id}` : ""}`);
  const cols = useMemo<Column<Bitacora>[]>(() => [
    { header: "Fecha", className: "whitespace-nowrap", sortValue: (b) => cuandoDe(b), exportValue: (b) => cuandoDe(b),
      cell: (b) => fmtDateTime(cuandoDe(b)) },
    { header: "Envío", truncate: true, sortValue: (b) => b.envio, exportValue: (b) => b.envio,
      cell: (b) => <span className="font-medium" title={b.envio}>{b.envio}</span> },
    { header: "Cómo", sortValue: (b) => b.origen, exportValue: (b) => b.origen,
      cell: (b) => (b.origen === "PROGRAMADO" ? "Automático" : "Botón") },
    { header: "Para", truncate: true, exportValue: (b) => [...b.para, ...b.cc.map((c) => `cc ${c}`)].join(", "),
      cell: (b) => <span title={[...b.para, ...b.cc.map((c) => `cc: ${c}`)].join("\n")}>{b.para.join(", ") || "—"}</span> },
    { header: "Saldo", className: "text-right tabular-nums", sortValue: (b) => Number(b.saldo),
      exportValue: (b) => Number(b.saldo), cell: (b) => fmtMoney(b.saldo) },
    { header: "Vencido", className: "text-right tabular-nums", sortValue: (b) => Number(b.vencido),
      exportValue: (b) => Number(b.vencido),
      cell: (b) => Number(b.vencido) > 0 ? <span className="text-danger">{fmtMoney(b.vencido)}</span> : "—" },
    { header: "Estado", sortValue: (b) => b.estado, exportValue: (b) => b.estado,
      cell: (b) => (
        <span className="inline-flex items-center gap-1.5">
          <Badge tone={TONO[b.estado]}>{ESTADO_TEXTO[b.estado]}</Badge>
          {b.escalado && <Badge tone="danger">escalado</Badge>}
        </span>
      ) },
    { header: "Detalle", truncate: true, exportValue: (b) => b.error ?? "",
      cell: (b) => (b.error ? <span className="text-xs text-muted" title={b.error}>{b.error}</span> : "—") },
  ], []);

  return (
    <div className="space-y-2">
      {envio && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted">Solo del envío</span>
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-0.5">
            {envio.nombre}
            <button type="button" onClick={onQuitarFiltro} aria-label="Ver todos" className="text-muted hover:text-foreground">
              <X size={13} />
            </button>
          </span>
        </div>
      )}
      {res.error ? <Alert tone="danger">No se pudo cargar el historial.</Alert> : (
        <DataTableSmart
          rows={res.data ?? []}
          loading={!res.data}
          rowKey={(b) => b.id}
          columns={cols}
          storageKey="cobranza-historial"
          exportFilename="historial-cobranza"
          empty="Todavía no ha salido ningún estado de cuenta."
        />
      )}
    </div>
  );
}

// ─── Ajustes generales ───────────────────────────────────────────────────────

function AjustesGenerales({ cfg, canWrite, onClose, onGuardado }: {
  cfg: Config; canWrite: boolean; onClose: () => void; onGuardado: (c: Config) => void;
}) {
  const toast = useToast();
  const { put, loading } = useMutation();
  const [activo, setActivo] = useState(cfg.activo);
  const [horas, setHoras] = useState(String(cfg.espejo_max_horas));
  const [copia, setCopia] = useState(cfg.cc_siempre.join(", "));

  const guardar = async () => {
    try {
      const c = await put<Config>(`${AUTO}/config`, {
        activo, espejo_max_horas: Number(horas || 0),
        cc_siempre: copia.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean),
      });
      toast.success("Ajustes generales guardados.");
      onGuardado(c);
    } catch (e) {
      toast.error(errorDe(e, "No se pudieron guardar los ajustes."));
    }
  };

  return (
    <Modal open onClose={onClose} title="Ajustes generales"
           description="Lo que vale para todos los envíos. Lo demás se configura en cada envío."
           footer={
             <div className="flex justify-end gap-2">
               <Button variant="secondary" onClick={onClose}>{canWrite ? "Cancelar" : "Cerrar"}</Button>
               {canWrite && <Button data-modal-primary onClick={guardar} disabled={loading}>{loading ? "Guardando…" : "Guardar"}</Button>}
             </div>
           }>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-medium">Automáticos encendidos</div>
            <div className="text-xs text-muted">Apagado, ningún envío sale solo. Con el botón Enviar siguen saliendo.</div>
          </div>
          <Switch checked={activo} disabled={!canWrite} onChange={setActivo} />
        </div>
        <Field label="Candado del espejo de SAE (horas)"
               hint="No se cobra si los pagos de SAE no se sincronizaron en estas horas. 0 = sin candado.">
          <div className="w-28">
            <Input type="number" min={0} max={168} className="text-right" value={horas}
                   disabled={!canWrite} onChange={(e) => setHoras(e.target.value)} />
          </div>
        </Field>
        <Field label="Copia en todos los envíos" hint="Por ejemplo, el correo de cobranza del negocio.">
          <Input value={copia} disabled={!canWrite} onChange={(e) => setCopia(e.target.value)}
                 placeholder="cobranza@tuempresa.com" />
        </Field>
      </div>
    </Modal>
  );
}

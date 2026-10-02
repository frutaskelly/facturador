"use client";

// Cobranza → Grupos: varias razones sociales en un solo estado de cuenta.
// EHMO quiere ver juntas EHMO, SUREÑA y MAFAN con la tabla por proyecto; otro
// cliente la querrá por serie, por sucursal o por razón social. Cada grupo dice
// qué entra (razón social completa o solo algunos proyectos), cómo se acomoda
// la tabla del correo y a quién se manda. «Vista previa» enseña el correo, el
// Excel y el PDF exactos antes de guardar o mandar nada
// (backend: services/cobranza_grupos.py).
//
// Entrega 1: los grupos se arman y se previsualizan; la cola semanal todavía
// sale de Contactos.
import { useEffect, useMemo, useRef, useState } from "react";
import { FileSpreadsheet, FileText, Mail, Pause, Pencil, Play, Plus, Send, Trash2, X } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTableSmart, type Column, type RowAction } from "@/components/ui/DataTableSmart";
import { Checkbox, Field, Input, Select, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownloadPost } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";

type AgruparPor = "PROYECTO" | "SERIE" | "SUCURSAL" | "CLIENTE";
const AGRUPAR: { key: AgruparPor; label: string }[] = [
  { key: "PROYECTO", label: "Proyecto" },
  { key: "SERIE", label: "Serie" },
  { key: "SUCURSAL", label: "Sucursal" },
  { key: "CLIENTE", label: "Razón social" },
];
const AGRUPAR_LABEL = Object.fromEntries(AGRUPAR.map((a) => [a.key, a.label])) as Record<AgruparPor, string>;

type Nodo = {
  proyecto_id: string | null; proyecto: string | null; serie: string | null; series: string[];
  sucursal: string | null; saldo: string; facturas: number;
};
type Opcion = { cliente_id: string; nombre: string; legal_name: string; codigo: string | null; saldo: string; nodos: Nodo[] };
type AlcanceOut = { cliente_id: string; cliente: string; completo: boolean; proyectos: string[]; series: string[] };
type Grupo = {
  id: string; nombre: string; agrupar_por: AgruparPor; mostrar_antiguedad: boolean;
  correos: string[]; cc: string[]; pausado: boolean; motivo_pausa: string | null;
  alcance: AlcanceOut[]; saldo: string; vencido: string; facturas: number;
  tambien_en: { grupo_id: string; nombre: string; clientes: string[] }[];
};
type Previo = {
  para: string[]; cc: string[]; escalado: boolean; asunto: string; html: string;
  adjuntos: { tipo: "xlsx" | "pdf"; nombre: string; detalle: string }[];
  hojas: string[]; avisos: string[];
};

// Lo que se edita: por razón social, completa o con sus proyectos/series.
type Sel = { completo: boolean; proyectos: string[]; series: string[] };
type Borrador = {
  id?: string; nombre: string; agrupar_por: AgruparPor; mostrar_antiguedad: boolean;
  correos: string; cc: string; pausado: boolean; motivo: string;
  orden: string[]; alcance: Record<string, Sel>;
};

const BASE = "/api/v1/cobranza/automatica/grupos";
const aLista = (s: string) => s.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
const errorDe = (e: unknown, def: string) => (e instanceof ApiError ? e.message : def);
const claveNodo = (n: Nodo) => n.proyecto_id ?? `serie:${n.serie ?? ""}`;

function borradorDe(g: Grupo | null): Borrador {
  if (!g) {
    return { nombre: "", agrupar_por: "PROYECTO", mostrar_antiguedad: false, correos: "", cc: "",
             pausado: false, motivo: "", orden: [], alcance: {} };
  }
  return {
    id: g.id, nombre: g.nombre, agrupar_por: g.agrupar_por, mostrar_antiguedad: g.mostrar_antiguedad,
    correos: g.correos.join(", "), cc: g.cc.join(", "), pausado: g.pausado, motivo: g.motivo_pausa ?? "",
    orden: g.alcance.map((a) => a.cliente_id),
    alcance: Object.fromEntries(g.alcance.map((a) => [a.cliente_id,
      { completo: a.completo, proyectos: a.proyectos, series: a.series }])),
  };
}

function payloadDe(b: Borrador) {
  return {
    id: b.id ?? null, nombre: b.nombre.trim(), agrupar_por: b.agrupar_por,
    mostrar_antiguedad: b.mostrar_antiguedad, correos: aLista(b.correos), cc: aLista(b.cc),
    pausado: b.pausado, motivo_pausa: b.pausado ? b.motivo.trim() || null : null,
    alcance: b.orden.map((id) => ({ cliente_id: id, ...b.alcance[id] })),
  };
}

/** ¿Dos alcances cubren alguna factura en común? (mismo criterio que el backend). */
function chocan(a: Sel, b: Sel) {
  if (a.completo || b.completo) return true;
  return a.proyectos.some((p) => b.proyectos.includes(p)) || a.series.some((s) => b.series.includes(s));
}

export function Grupos({ canWrite }: { canWrite: boolean }) {
  const toast = useToast();
  const { del, loading } = useMutation();
  const res = useResource<Grupo[]>(BASE);
  const opcRes = useResource<Opcion[]>(`${BASE}/opciones`);
  const [editar, setEditar] = useState<Borrador | null>(null);
  const [ver, setVer] = useState<Borrador | null>(null);
  const [borrar, setBorrar] = useState<Grupo | null>(null);

  const cols = useMemo<Column<Grupo>[]>(() => [
    { header: "Grupo", truncate: true, sortValue: (g) => g.nombre, exportValue: (g) => g.nombre,
      cell: (g) => (
        <span className="inline-flex items-center gap-1.5">
          <span className="font-medium" title={g.nombre}>{g.nombre}</span>
          {g.pausado && <Badge tone="warning">en pausa</Badge>}
          {g.tambien_en.length > 0 && (
            <span title={`También va en: ${g.tambien_en.map((t) => t.nombre).join(", ")}`}>
              <Badge tone="accent">en {g.tambien_en.length + 1} grupos</Badge>
            </span>
          )}
        </span>
      ) },
    { header: "Incluye", truncate: true,
      exportValue: (g) => g.alcance.map((a) => a.cliente).join(", "),
      cell: (g) => {
        const parciales = g.alcance.filter((a) => !a.completo).length;
        return (
          <span title={g.alcance.map((a) => `${a.cliente}${a.completo ? "" : " (parcial)"}`).join("\n")}>
            {g.alcance.map((a) => a.cliente).join(", ")}
            {parciales > 0 && <span className="text-muted"> · {parciales} parcial{parciales === 1 ? "" : "es"}</span>}
          </span>
        );
      } },
    { header: "Tabla por", sortValue: (g) => AGRUPAR_LABEL[g.agrupar_por], exportValue: (g) => AGRUPAR_LABEL[g.agrupar_por],
      cell: (g) => AGRUPAR_LABEL[g.agrupar_por] },
    { header: "Para", truncate: true, exportValue: (g) => g.correos.join(", "),
      cell: (g) => g.correos.length
        ? <span title={[...g.correos, ...g.cc.map((c) => `cc: ${c}`)].join("\n")}>
            {g.correos.join(", ")}{g.cc.length ? <span className="text-muted"> +{g.cc.length} cc</span> : null}
          </span>
        : <span className="text-danger">falta correo</span> },
    { header: "Saldo", className: "text-right tabular-nums", sortValue: (g) => Number(g.saldo),
      exportValue: (g) => Number(g.saldo), cell: (g) => fmtMoney(g.saldo) },
    { header: "Vencido", className: "text-right tabular-nums", sortValue: (g) => Number(g.vencido),
      exportValue: (g) => Number(g.vencido),
      cell: (g) => Number(g.vencido) > 0 ? <span className="text-danger">{fmtMoney(g.vencido)}</span> : "—" },
  ], []);

  const acciones = useMemo<RowAction<Grupo>[]>(() => [
    { id: "ver", icon: <Mail size={15} />, label: "Ver el correo", onClick: (g) => setVer(borradorDe(g)) },
    ...(canWrite ? [
      { id: "editar", icon: <Pencil size={15} />, label: "Editar", onClick: (g: Grupo) => setEditar(borradorDe(g)) },
      { id: "borrar", icon: <Trash2 size={15} />, label: "Borrar", tone: "danger" as const, onClick: setBorrar },
    ] : []),
  ], [canWrite]);

  const confirmarBorrar = async () => {
    if (!borrar) return;
    try {
      await del(`${BASE}/${borrar.id}`);
      toast.success(`Grupo «${borrar.nombre}» borrado.`);
      setBorrar(null);
      res.reload();
    } catch (e) {
      toast.error(errorDe(e, "No se pudo borrar el grupo."));
    }
  };

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-muted">
          Un grupo junta varias razones sociales en un solo estado de cuenta, con la tabla del correo por
          proyecto, serie, sucursal o razón social. <b>Vista previa</b> enseña el correo y sus adjuntos tal como
          saldrían. Por ahora la cola semanal de <b>Envíos automáticos</b> sigue saliendo de Contactos; los grupos
          entran a la cola en la siguiente entrega.
        </p>
        {canWrite && (
          <Button onClick={() => setEditar(borradorDe(null))}><Plus size={16} /> Nuevo grupo</Button>
        )}
      </div>
      {res.error ? <Alert tone="danger">No se pudieron cargar los grupos.</Alert> : (
        <DataTableSmart
          rows={res.data ?? []}
          loading={!res.data}
          rowKey={(g) => g.id}
          columns={cols}
          actions={acciones}
          onRowClick={canWrite ? (g) => setEditar(borradorDe(g)) : (g) => setVer(borradorDe(g))}
          storageKey="cobranza-grupos"
          exportFilename="grupos-cobranza"
          empty="Todavía no hay grupos. Crea uno para juntar varias razones sociales en un solo estado de cuenta."
        />
      )}

      {editar && (
        <EditarGrupo
          inicial={editar} opciones={opcRes.data ?? []} cargando={!opcRes.data}
          grupos={res.data ?? []} canWrite={canWrite}
          onClose={() => setEditar(null)}
          onGuardado={() => { setEditar(null); res.reload(); }}
          onPrevio={setVer}
        />
      )}
      {ver && <VistaPrevia borrador={ver} canWrite={canWrite} onClose={() => setVer(null)} />}
      {borrar && (
        <Modal open size="sm" title="Borrar grupo" onClose={() => setBorrar(null)}
               footer={
                 <div className="flex justify-end gap-2">
                   <Button variant="secondary" onClick={() => setBorrar(null)}>Cancelar</Button>
                   <Button variant="danger" onClick={confirmarBorrar} disabled={loading}>
                     {loading ? "Borrando…" : "Borrar"}
                   </Button>
                 </div>
               }>
          <p className="text-sm">
            ¿Borrar el grupo <b>{borrar.nombre}</b>? Sus razones sociales vuelven a cobrarse sueltas. Las facturas no
            cambian.
          </p>
        </Modal>
      )}
    </Card>
  );
}

// ─── Editor ──────────────────────────────────────────────────────────────────

function EditarGrupo({ inicial, opciones, cargando, grupos, canWrite, onClose, onGuardado, onPrevio }: {
  inicial: Borrador; opciones: Opcion[]; cargando: boolean; grupos: Grupo[]; canWrite: boolean;
  onClose: () => void; onGuardado: () => void; onPrevio: (b: Borrador) => void;
}) {
  const toast = useToast();
  const { post, put, loading } = useMutation();
  const [b, setB] = useState<Borrador>(inicial);
  const set = <K extends keyof Borrador>(k: K, v: Borrador[K]) => setB((x) => ({ ...x, [k]: v }));
  const porCliente = useMemo(() => Object.fromEntries(opciones.map((o) => [o.cliente_id, o])), [opciones]);
  const libres = opciones.filter((o) => !b.orden.includes(o.cliente_id));

  const cambiarSel = (clienteId: string, sel: Sel) =>
    setB((x) => ({ ...x, alcance: { ...x.alcance, [clienteId]: sel } }));

  const agregar = (clienteId: string) => {
    if (!clienteId || b.orden.includes(clienteId)) return;
    setB((x) => ({ ...x, orden: [...x.orden, clienteId],
                   alcance: { ...x.alcance, [clienteId]: { completo: true, proyectos: [], series: [] } } }));
  };
  const quitar = (clienteId: string) =>
    setB((x) => {
      const alcance = { ...x.alcance };
      delete alcance[clienteId];
      return { ...x, orden: x.orden.filter((i) => i !== clienteId), alcance };
    });

  // Marcar o desmarcar un proyecto. Desde «completa», desmarcar uno deja
  // marcados todos los demás; marcar el último que faltaba vuelve a «completa».
  const alternarNodo = (clienteId: string, n: Nodo) => {
    const nodos = porCliente[clienteId]?.nodos ?? [];
    const sel = b.alcance[clienteId];
    const marcados = new Set(sel.completo ? nodos.map(claveNodo) : [...sel.proyectos, ...sel.series.map((s) => `serie:${s}`)]);
    const k = claveNodo(n);
    if (marcados.has(k)) marcados.delete(k); else marcados.add(k);
    if (nodos.length > 0 && nodos.every((x) => marcados.has(claveNodo(x)))) {
      cambiarSel(clienteId, { completo: true, proyectos: [], series: [] });
      return;
    }
    const lista = [...marcados];
    cambiarSel(clienteId, {
      completo: false,
      proyectos: lista.filter((x) => !x.startsWith("serie:")),
      series: lista.filter((x) => x.startsWith("serie:")).map((x) => x.slice(6)),
    });
  };

  // Otros grupos que ya cubren algo de lo marcado (aviso, no error).
  const choques = useMemo(() => grupos.filter((g) => g.id !== b.id).flatMap((g) => {
    const nombres = g.alcance.filter((a) => b.alcance[a.cliente_id] && chocan(b.alcance[a.cliente_id], a))
      .map((a) => a.cliente);
    return nombres.length ? [`${nombres.join(", ")} también va en «${g.nombre}»`] : [];
  }), [grupos, b.alcance, b.id]);

  const incompletos = b.orden.filter((id) => {
    const s = b.alcance[id];
    return !s.completo && s.proyectos.length === 0 && s.series.length === 0;
  });
  const listo = b.nombre.trim() !== "" && b.orden.length > 0 && incompletos.length === 0;

  const guardar = async () => {
    try {
      if (b.id) await put(`${BASE}/${b.id}`, payloadDe(b));
      else await post(BASE, payloadDe(b));
      toast.success(`Grupo «${b.nombre.trim()}» guardado.`);
      onGuardado();
    } catch (e) {
      toast.error(errorDe(e, "No se pudo guardar el grupo."));
    }
  };

  return (
    <Modal open onClose={onClose} size="lg"
           title={b.id ? `Editar grupo — ${inicial.nombre}` : "Nuevo grupo de cobranza"}
           footerStart={!listo && (
             <span className="text-xs text-muted">
               {!b.nombre.trim() ? "Ponle nombre al grupo." : b.orden.length === 0 ? "Agrega al menos una razón social."
                 : "Marca al menos un proyecto en cada razón social, o quítala."}
             </span>
           )}
           footer={
             <div className="flex justify-end gap-2">
               <Button variant="secondary" onClick={onClose}>Cancelar</Button>
               <Button variant="secondary" disabled={!listo} onClick={() => onPrevio(b)}>
                 <Mail size={15} /> Vista previa
               </Button>
               {canWrite && (
                 <Button data-modal-primary onClick={guardar} disabled={!listo || loading}>
                   {loading ? "Guardando…" : "Guardar"}
                 </Button>
               )}
             </div>
           }>
      <div className="space-y-5">
        <Field label="Nombre del grupo" required>
          <Input value={b.nombre} maxLength={80} onChange={(e) => set("nombre", e.target.value)} placeholder="EHMO" />
        </Field>

        <div>
          <div className="mb-1 text-sm font-medium">Qué incluye</div>
          <div className="mb-2 text-xs text-muted">
            Marcar la razón social completa trae también sus proyectos nuevos. Si desmarcas un proyecto, ese se queda
            fuera hasta que lo vuelvas a marcar.
          </div>
          {cargando ? <div className="flex justify-center py-6"><Spinner /></div> : (
            <div className="overflow-hidden rounded-xl border border-border">
              {b.orden.length === 0 && (
                <div className="px-4 py-6 text-center text-sm text-muted">Agrega la primera razón social abajo.</div>
              )}
              {b.orden.map((id) => (
                <RazonSocial key={id} opcion={porCliente[id]} sel={b.alcance[id]}
                             onCompleta={(v) => cambiarSel(id, { completo: v, proyectos: [], series: [] })}
                             onNodo={(n) => alternarNodo(id, n)} onQuitar={() => quitar(id)} />
              ))}
              {libres.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 border-t border-dashed border-border bg-surface px-3 py-2">
                  <span className="text-sm text-muted">Agregar razón social:</span>
                  <div className="min-w-[16rem] flex-1">
                    <Select value="" onChange={(e) => agregar(e.target.value)}>
                      <option value="">Escoge…</option>
                      {libres.map((o) => (
                        <option key={o.cliente_id} value={o.cliente_id}>
                          {o.legal_name}{Number(o.saldo) > 0 ? ` · ${fmtMoney(o.saldo)}` : ""}
                        </option>
                      ))}
                    </Select>
                  </div>
                </div>
              )}
            </div>
          )}
          {choques.length > 0 && (
            <div className="mt-2">
              <Alert tone="warning">
                {choques.map((c) => <div key={c}>{c}: esas facturas se cobrarían en los dos correos.</div>)}
              </Alert>
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-sm font-medium">Tabla del correo por</div>
          <div role="group" aria-label="Tabla del correo por" className="grid grid-cols-2 gap-1 rounded-lg bg-surface-2 p-1 sm:grid-cols-4">
            {AGRUPAR.map((a) => (
              <button key={a.key} type="button" aria-pressed={b.agrupar_por === a.key}
                      onClick={() => set("agrupar_por", a.key)}
                      className={`rounded-md px-3 py-1.5 text-sm ${b.agrupar_por === a.key
                        ? "bg-background font-medium text-accent shadow-sm" : "text-muted hover:text-foreground"}`}>
                {a.label}
              </button>
            ))}
          </div>
          <div className="mt-1 text-xs text-muted">También define las hojas del Excel de respaldo: una por fila.</div>
          <label className="mt-3 flex items-center gap-2 text-sm">
            <Switch checked={b.mostrar_antiguedad} onChange={(v) => set("mostrar_antiguedad", v)} />
            Columnas de antigüedad (por vencer, 1 a 30, 31 a 60, 61 a 90 y más de 90 días)
          </label>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Para" hint="Separa varios correos con coma.">
            <Input value={b.correos} onChange={(e) => set("correos", e.target.value)} placeholder="cuentasporpagar@cliente.com" />
          </Field>
          <Field label="Con copia" hint="Además de la copia de Ajustes.">
            <Input value={b.cc} onChange={(e) => set("cc", e.target.value)} placeholder="opcional" />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={b.pausado} onChange={(v) => set("pausado", v)} />
            {b.pausado ? <><Pause size={14} /> En pausa</> : <><Play size={14} /> Recibe cobranza</>}
          </label>
          {b.pausado && (
            <Input className="max-w-sm" value={b.motivo} placeholder="Motivo (p. ej. convenio de pago)"
                   onChange={(e) => set("motivo", e.target.value)} />
          )}
        </div>
      </div>
    </Modal>
  );
}

function RazonSocial({ opcion, sel, onCompleta, onNodo, onQuitar }: {
  opcion?: Opcion; sel: Sel; onCompleta: (v: boolean) => void; onNodo: (n: Nodo) => void; onQuitar: () => void;
}) {
  const padre = useRef<HTMLInputElement>(null);
  const nodos = opcion?.nodos ?? [];
  const marcado = (n: Nodo) => sel.completo
    || (n.proyecto_id ? sel.proyectos.includes(n.proyecto_id) : sel.series.includes(n.serie ?? ""));
  const parcial = !sel.completo && (sel.proyectos.length > 0 || sel.series.length > 0);
  useEffect(() => { if (padre.current) padre.current.indeterminate = parcial; }, [parcial]);

  return (
    <div className="border-b border-border last:border-b-0">
      <div className="flex items-center gap-3 bg-surface px-3 py-2">
        <Checkbox ref={padre} checked={sel.completo} onChange={(e) => onCompleta(e.target.checked)}
                  aria-label={`${opcion?.legal_name ?? "Razón social"} completa`} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" title={opcion?.legal_name}>{opcion?.legal_name ?? "—"}</div>
          <div className="text-xs text-muted">
            {opcion && opcion.nombre !== opcion.legal_name ? `Nombre corto: ${opcion.nombre} · ` : "Sin nombre corto · "}
            {sel.completo ? "completa" : parcial ? "solo lo marcado" : <span className="text-danger">nada marcado</span>}
          </div>
        </div>
        <span className="whitespace-nowrap text-xs tabular-nums text-muted">{opcion ? fmtMoney(opcion.saldo) : ""}</span>
        <button type="button" onClick={onQuitar} title="Quitar del grupo" aria-label="Quitar del grupo"
                className="rounded p-1 text-muted hover:bg-surface-2 hover:text-danger">
          <X size={14} />
        </button>
      </div>
      {nodos.length === 0 && (
        <div className="px-10 py-2 text-xs text-muted">Sin proyectos ni saldo hoy: entra completa.</div>
      )}
      {nodos.map((n) => (
        <label key={claveNodo(n)} className="flex cursor-pointer items-center gap-3 py-1.5 pl-10 pr-3 hover:bg-surface-2">
          <Checkbox checked={marcado(n)} onChange={() => onNodo(n)} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm">{n.proyecto ?? `Sin proyecto · serie ${n.serie || "—"}`}</span>
            <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
              {n.series.map((s) => (
                <span key={s} className="rounded bg-surface-2 px-1.5 font-mono text-[11px] text-accent">{s}</span>
              ))}
              {n.sucursal ?? <span className="text-amber-700">sin sucursal</span>}
            </span>
          </span>
          <span className="whitespace-nowrap text-xs tabular-nums text-muted">
            {Number(n.saldo) > 0 ? fmtMoney(n.saldo) : "sin saldo"}
          </span>
        </label>
      ))}
    </div>
  );
}

// ─── Vista previa ────────────────────────────────────────────────────────────

function VistaPrevia({ borrador, canWrite, onClose }: { borrador: Borrador; canWrite: boolean; onClose: () => void }) {
  const toast = useToast();
  const { post, loading } = useMutation();
  const payload = useMemo(() => payloadDe(borrador), [borrador]);
  const [previo, setPrevio] = useState<Previo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bajando, setBajando] = useState<"xlsx" | "pdf" | null>(null);
  const [alto, setAlto] = useState(420);

  useEffect(() => {
    let vivo = true;
    post<Previo>(`${BASE}/previo`, payload)
      .then((p) => { if (vivo) setPrevio(p); })
      .catch((e) => { if (vivo) setError(errorDe(e, "No se pudo armar la vista previa.")); });
    return () => { vivo = false; };
  }, [post, payload]);

  const bajar = async (tipo: "xlsx" | "pdf") => {
    setBajando(tipo);
    try {
      await apiDownloadPost(`${BASE}/previo/${tipo}`, payload, `estado-cuenta.${tipo}`);
    } catch (e) {
      toast.error(errorDe(e, "No se pudo descargar el archivo."));
    } finally {
      setBajando(null);
    }
  };

  const prueba = async () => {
    try {
      const r = await post<{ to: string }>(`${BASE}/prueba`, payload);
      toast.success(`Prueba enviada a ${r.to}.`);
    } catch (e) {
      toast.error(errorDe(e, "No se pudo mandar la prueba."));
    }
  };

  return (
    <Modal open onClose={onClose} size="xl" title={`Vista previa del correo — ${borrador.nombre.trim() || "grupo"}`}
           description="El correo y los adjuntos tal como saldrían hoy, con los ajustes de la cobranza automática."
           footer={<div className="flex justify-end"><Button variant="secondary" onClick={onClose}>Cerrar</Button></div>}>
      {error ? <Alert tone="danger">{error}</Alert> : !previo ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => bajar("xlsx")} disabled={bajando !== null}>
              <FileSpreadsheet size={15} /> {bajando === "xlsx" ? "Descargando…" : "Descargar Excel"}
            </Button>
            <Button variant="secondary" onClick={() => bajar("pdf")} disabled={bajando !== null}>
              <FileText size={15} /> {bajando === "pdf" ? "Descargando…" : "Descargar PDF"}
            </Button>
            {canWrite && (
              <Button variant="secondary" onClick={prueba} disabled={loading}>
                <Send size={15} /> {loading ? "Enviando…" : "Enviarme una prueba"}
              </Button>
            )}
          </div>

          {previo.avisos.length > 0 && (
            <Alert tone="warning">
              <ul className="list-disc space-y-0.5 pl-4">{previo.avisos.map((a) => <li key={a}>{a}</li>)}</ul>
            </Alert>
          )}

          <div className="overflow-hidden rounded-xl border border-border">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-b border-border px-4 py-3 text-sm">
              <dt className="text-muted">Para</dt>
              <dd className="break-words">{previo.para.length ? previo.para.join(", ") : <span className="text-danger">falta capturar el correo</span>}</dd>
              <dt className="text-muted">CC</dt>
              <dd className="break-words">
                {previo.cc.length ? previo.cc.join(", ") : <span className="text-muted">—</span>}
                {previo.escalado && <span className="ml-2"><Badge tone="danger">escalado</Badge></span>}
              </dd>
              <dt className="text-muted">Asunto</dt>
              <dd className="font-medium">{previo.asunto}</dd>
            </dl>
            <div className="bg-surface p-3">
              {/* Sin scripts y con su propio documento: los estilos del correo
                  no se mezclan con los de la app, y se ve como en la bandeja. */}
              <iframe title="Cuerpo del correo" sandbox="allow-same-origin" srcDoc={previo.html}
                      className="w-full rounded-lg border border-border bg-white" style={{ height: alto }}
                      onLoad={(e) => {
                        // El alto del contenido, no el del documento: ese
                        // nunca baja del alto que ya tiene el iframe.
                        const body = e.currentTarget.contentDocument?.body;
                        if (body) setAlto(Math.min(Math.max(body.scrollHeight + 32, 160), 1400));
                      }} />
            </div>
            {previo.adjuntos.length > 0 && (
              <div className="space-y-3 border-t border-border px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  {previo.adjuntos.map((a) => (
                    <div key={a.nombre} className="flex items-center gap-2 rounded-lg border border-border px-3 py-2">
                      {a.tipo === "xlsx" ? <FileSpreadsheet size={18} className="text-success" /> : <FileText size={18} className="text-danger" />}
                      <div>
                        <div className="text-sm font-medium">{a.nombre}</div>
                        <div className="text-xs text-muted">{a.detalle}</div>
                      </div>
                    </div>
                  ))}
                </div>
                {previo.hojas.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs text-muted">Hojas del Excel</div>
                    <div className="flex gap-0.5 overflow-x-auto border-b border-border">
                      {previo.hojas.map((h, i) => (
                        <span key={h} className={`whitespace-nowrap rounded-t-md border border-b-0 border-border px-2.5 py-1 text-xs ${
                          i === 0 ? "bg-background font-medium" : "bg-surface-2 text-muted"}`}>{h}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

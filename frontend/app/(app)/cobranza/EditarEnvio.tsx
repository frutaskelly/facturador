"use client";

// Cobranza → el editor de un envío y su vista previa. Un envío junta una razón
// social o varias (EHMO + SUREÑA + MAFAN) y trae TODA su configuración: qué
// incluye, cuándo sale (automático o con el botón), a quién, cómo se acomoda la
// tabla del correo, adjuntos, asunto y mensaje. «Vista previa» enseña el correo,
// el Excel y el PDF exactos antes de guardar o mandar nada
// (backend: services/cobranza_grupos.py y cobranza_auto.py).
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { FileSpreadsheet, FileText, Mail, Send, X } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownloadPost } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { useMutation } from "@/lib/hooks";

export type AgruparPor = "PROYECTO" | "SERIE" | "SUCURSAL" | "CLIENTE";
export type Modo = "AUTOMATICO" | "MANUAL";
export const AGRUPAR: { key: AgruparPor; label: string }[] = [
  { key: "PROYECTO", label: "Proyecto" },
  { key: "SERIE", label: "Serie" },
  { key: "SUCURSAL", label: "Sucursal" },
  { key: "CLIENTE", label: "Razón social" },
];
export const DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];

export type Nodo = {
  proyecto_id: string | null; proyecto: string | null; serie: string | null; series: string[];
  sucursal: string | null; saldo: string; facturas: number;
};
export type Opcion = {
  cliente_id: string; nombre: string; legal_name: string; codigo: string | null; correos: string[];
  saldo: string; nodos: Nodo[];
};
export type AlcanceOut = { cliente_id: string; cliente: string; completo: boolean; proyectos: string[]; series: string[] };
export type Bitacora = {
  id: string; grupo_id: string | null; envio: string; corte: string;
  estado: "PENDIENTE" | "ENVIANDO" | "ENVIADO" | "ERROR" | "DESCARTADO"; origen: "MANUAL" | "PROGRAMADO";
  para: string[]; cc: string[]; saldo: string; vencido: string; facturas: number;
  dias_max_vencida: number; escalado: boolean; error: string | null; enviado_at: string | null; created_at: string;
};
export type Envio = {
  id: string; nombre: string; agrupar_por: AgruparPor; mostrar_antiguedad: boolean;
  correos: string[]; cc: string[]; modo: Modo; dia_semana: number; hora: number;
  incluir_por_vencer: boolean; saldo_minimo: string | number; escalar_dias: number; escalar_cc: string[];
  adjuntar_pdf: boolean; adjuntar_excel: boolean; asunto: string | null; mensaje: string | null; nota: string | null;
  alcance: AlcanceOut[]; saldo: string; vencido: string; facturas: number;
  cuando: string; proximo: string | null; ultimo: Bitacora | null;
  tambien_en: { grupo_id: string; nombre: string; clientes: string[] }[];
};
type Previo = {
  para: string[]; cc: string[]; escalado: boolean; asunto: string; html: string;
  adjuntos: { tipo: "xlsx" | "pdf"; nombre: string; detalle: string }[];
  hojas: string[]; avisos: string[];
};

// Lo que se edita. Por razón social: completa o con sus proyectos/series.
type Sel = { completo: boolean; proyectos: string[]; series: string[] };
export type Borrador = {
  id?: string; nombre: string; agrupar_por: AgruparPor; mostrar_antiguedad: boolean;
  correos: string; cc: string; modo: Modo; dia_semana: number; hora: number;
  incluir_por_vencer: boolean; saldo_minimo: string; escalar_dias: string; escalar_cc: string;
  adjuntar_pdf: boolean; adjuntar_excel: boolean; asunto: string; mensaje: string; nota: string;
  orden: string[]; alcance: Record<string, Sel>;
};

export const BASE = "/api/v1/cobranza/automatica/grupos";
const aLista = (s: string) => s.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
export const errorDe = (e: unknown, def: string) => (e instanceof ApiError ? e.message : def);
const claveNodo = (n: Nodo) => n.proyecto_id ?? `serie:${n.serie ?? ""}`;

const VACIO: Borrador = {
  nombre: "", agrupar_por: "PROYECTO", mostrar_antiguedad: false, correos: "", cc: "",
  modo: "MANUAL", dia_semana: 0, hora: 8, incluir_por_vencer: true, saldo_minimo: "100",
  escalar_dias: "30", escalar_cc: "", adjuntar_pdf: true, adjuntar_excel: true,
  asunto: "", mensaje: "", nota: "", orden: [], alcance: {},
};

/** El borrador de un envío guardado, o uno nuevo (vacío o para una razón social). */
export function borradorDe(e: Envio | null, para?: Pick<Opcion, "cliente_id" | "nombre" | "correos">): Borrador {
  if (!e) {
    if (!para) return { ...VACIO };
    return { ...VACIO, nombre: para.nombre, correos: para.correos.join(", "), orden: [para.cliente_id],
             alcance: { [para.cliente_id]: { completo: true, proyectos: [], series: [] } } };
  }
  return {
    id: e.id, nombre: e.nombre, agrupar_por: e.agrupar_por, mostrar_antiguedad: e.mostrar_antiguedad,
    correos: e.correos.join(", "), cc: e.cc.join(", "), modo: e.modo, dia_semana: e.dia_semana, hora: e.hora,
    incluir_por_vencer: e.incluir_por_vencer, saldo_minimo: String(e.saldo_minimo),
    escalar_dias: String(e.escalar_dias), escalar_cc: e.escalar_cc.join(", "),
    adjuntar_pdf: e.adjuntar_pdf, adjuntar_excel: e.adjuntar_excel,
    asunto: e.asunto ?? "", mensaje: e.mensaje ?? "", nota: e.nota ?? "",
    orden: e.alcance.map((a) => a.cliente_id),
    alcance: Object.fromEntries(e.alcance.map((a) => [a.cliente_id,
      { completo: a.completo, proyectos: a.proyectos, series: a.series }])),
  };
}

export function payloadDe(b: Borrador) {
  return {
    id: b.id ?? null, nombre: b.nombre.trim(), agrupar_por: b.agrupar_por,
    mostrar_antiguedad: b.mostrar_antiguedad, correos: aLista(b.correos), cc: aLista(b.cc),
    modo: b.modo, dia_semana: b.dia_semana, hora: b.hora, incluir_por_vencer: b.incluir_por_vencer,
    saldo_minimo: Number(b.saldo_minimo || 0), escalar_dias: Number(b.escalar_dias || 0),
    escalar_cc: aLista(b.escalar_cc), adjuntar_pdf: b.adjuntar_pdf, adjuntar_excel: b.adjuntar_excel,
    asunto: b.asunto.trim() || null, mensaje: b.mensaje.trim() || null, nota: b.nota.trim() || null,
    alcance: b.orden.map((id) => ({ cliente_id: id, ...b.alcance[id] })),
  };
}

/** ¿Dos alcances cubren alguna factura en común? (mismo criterio que el backend). */
function chocan(a: Sel, b: Sel) {
  if (a.completo || b.completo) return true;
  return a.proyectos.some((p) => b.proyectos.includes(p)) || a.series.some((s) => b.series.includes(s));
}

/** Botones de opción (Automático/Manual, Proyecto/Serie…). */
export function Opciones<T extends string>({ valor, opciones, onChange, disabled, chico }: {
  valor: T; opciones: { key: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean; chico?: boolean;
}) {
  return (
    <div role="group" className={`inline-grid gap-1 rounded-lg bg-surface-2 p-1 ${chico ? "" : "w-full"}`}
         style={{ gridTemplateColumns: `repeat(${opciones.length}, minmax(0, 1fr))` }}>
      {opciones.map((o) => (
        <button key={o.key} type="button" aria-pressed={valor === o.key} disabled={disabled}
                onClick={(e) => { e.stopPropagation(); if (valor !== o.key) onChange(o.key); }}
                className={`whitespace-nowrap rounded-md ${chico ? "px-2 py-0.5 text-xs" : "px-3 py-1.5 text-sm"} ${
                  valor === o.key ? "bg-background font-medium text-accent shadow-sm" : "text-muted hover:text-foreground"
                } disabled:cursor-not-allowed disabled:opacity-60`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Seccion({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold">{titulo}</h3>
      {children}
    </section>
  );
}

// ─── Editor ──────────────────────────────────────────────────────────────────

export function EditarEnvio({ inicial, opciones, cargando, envios, automaticosEncendidos, canWrite, onClose, onGuardado, onPrevio }: {
  inicial: Borrador; opciones: Opcion[]; cargando: boolean; envios: Envio[]; automaticosEncendidos: boolean;
  canWrite: boolean; onClose: () => void; onGuardado: () => void; onPrevio: (b: Borrador) => void;
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

  // Otros envíos que ya cubren algo de lo marcado (aviso, no error).
  const choques = useMemo(() => envios.filter((g) => g.id !== b.id).flatMap((g) => {
    const nombres = g.alcance.filter((a) => b.alcance[a.cliente_id] && chocan(b.alcance[a.cliente_id], a))
      .map((a) => a.cliente);
    return nombres.length ? [`${nombres.join(", ")} también va en «${g.nombre}»`] : [];
  }), [envios, b.alcance, b.id]);

  const incompletos = b.orden.filter((id) => {
    const s = b.alcance[id];
    return !s.completo && s.proyectos.length === 0 && s.series.length === 0;
  });
  const listo = b.nombre.trim() !== "" && b.orden.length > 0 && incompletos.length === 0;

  const guardar = async () => {
    try {
      if (b.id) await put(`${BASE}/${b.id}`, payloadDe(b));
      else await post(BASE, payloadDe(b));
      toast.success(`Envío «${b.nombre.trim()}» guardado.`);
      onGuardado();
    } catch (e) {
      toast.error(errorDe(e, "No se pudo guardar el envío."));
    }
  };

  return (
    <Modal open onClose={onClose} size="lg"
           title={b.id ? `Editar envío — ${inicial.nombre}` : "Nuevo envío de cobranza"}
           footerStart={!listo && (
             <span className="text-xs text-muted">
               {!b.nombre.trim() ? "Ponle nombre al envío." : b.orden.length === 0 ? "Agrega al menos una razón social."
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
        <Field label="Nombre del envío" required>
          <Input value={b.nombre} maxLength={80} onChange={(e) => set("nombre", e.target.value)} placeholder="EHMO" />
        </Field>

        <Seccion titulo="Qué incluye">
          <div className="text-xs text-muted">
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
            <Alert tone="warning">
              {choques.map((c) => <div key={c}>{c}: esas facturas se cobrarían en los dos correos.</div>)}
            </Alert>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={b.incluir_por_vencer} onChange={(v) => set("incluir_por_vencer", v)} />
            Incluir facturas por vencer
            <span className="text-xs text-muted">(apagado, solo las vencidas)</span>
          </label>
        </Seccion>

        <Seccion titulo="Cuándo sale">
          <Opciones valor={b.modo} onChange={(v) => set("modo", v)}
                    opciones={[{ key: "MANUAL", label: "Manual · con el botón Enviar" },
                               { key: "AUTOMATICO", label: "Automático · sale solo" }]} />
          {b.modo === "AUTOMATICO" ? (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Día">
                  <Select value={String(b.dia_semana)} onChange={(e) => set("dia_semana", Number(e.target.value))}>
                    {DIAS.map((d, i) => <option key={d} value={String(i)}>{d}</option>)}
                  </Select>
                </Field>
                <Field label="Hora" hint="Ciudad de México">
                  <Select value={String(b.hora)} onChange={(e) => set("hora", Number(e.target.value))}>
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={String(h)}>{String(h).padStart(2, "0")}:00</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Saldo mínimo" hint="Por debajo no sale solo">
                  <Input type="number" min={0} className="text-right" value={b.saldo_minimo}
                         onChange={(e) => set("saldo_minimo", e.target.value)} />
                </Field>
              </div>
              {!automaticosEncendidos && (
                <Alert tone="warning">
                  Los automáticos están apagados en <b>Ajustes generales</b>: este envío no saldrá solo hasta que los
                  enciendas. Con el botón Enviar sí sale.
                </Alert>
              )}
            </>
          ) : (
            <div className="text-xs text-muted">Sale solo cuando presionas Enviar en la lista de envíos.</div>
          )}
        </Seccion>

        <Seccion titulo="A quién">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Para" hint="Separa varios correos con coma.">
              <Input value={b.correos} onChange={(e) => set("correos", e.target.value)} placeholder="cuentasporpagar@cliente.com" />
            </Field>
            <Field label="Con copia" hint="Además de la copia fija de Ajustes generales.">
              <Input value={b.cc} onChange={(e) => set("cc", e.target.value)} placeholder="opcional" />
            </Field>
          </div>
          <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
            <Field label="Escalar a los" hint="días de vencida">
              <Input type="number" min={0} max={365} value={b.escalar_dias} onChange={(e) => set("escalar_dias", e.target.value)} />
            </Field>
            <Field label="Copiar al escalar" hint="Cuando la factura más vieja pasa esos días. 0 días = nunca.">
              <Input value={b.escalar_cc} onChange={(e) => set("escalar_cc", e.target.value)} placeholder="direccion@tuempresa.com" />
            </Field>
          </div>
        </Seccion>

        <Seccion titulo="El correo">
          <div>
            <div className="mb-1 text-sm font-medium">Tabla por</div>
            <Opciones valor={b.agrupar_por} onChange={(v) => set("agrupar_por", v)} opciones={AGRUPAR} />
            <div className="mt-1 text-xs text-muted">También define las hojas del Excel de respaldo: una por fila.</div>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <label className="flex items-center gap-2">
              <Switch checked={b.mostrar_antiguedad} onChange={(v) => set("mostrar_antiguedad", v)} />
              Columnas de antigüedad
            </label>
            <label className="flex items-center gap-2">
              <Switch checked={b.adjuntar_excel} onChange={(v) => set("adjuntar_excel", v)} /> Excel (formato SAE)
            </label>
            <label className="flex items-center gap-2">
              <Switch checked={b.adjuntar_pdf} onChange={(v) => set("adjuntar_pdf", v)} /> PDF
            </label>
          </div>
          <Field label="Asunto" hint="Vacío = «Estado de cuenta <nombre> al dd/mm/aaaa».">
            <Input value={b.asunto} maxLength={200} onChange={(e) => set("asunto", e.target.value)} />
          </Field>
          <Field label="Mensaje" hint="Va antes del resumen de saldo y la tabla, que el sistema agrega solos.">
            <Textarea rows={3} value={b.mensaje} onChange={(e) => set("mensaje", e.target.value)}
                      placeholder="Buen día, les compartimos su estado de cuenta…" />
          </Field>
        </Seccion>

        <Seccion titulo="Nota interna">
          <Input value={b.nota} maxLength={254} onChange={(e) => set("nota", e.target.value)}
                 placeholder="Opcional, no va en el correo (p. ej. convenio de pago)" />
        </Seccion>
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
        <button type="button" onClick={onQuitar} title="Quitar del envío" aria-label="Quitar del envío"
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

export function VistaPrevia({ borrador, canWrite, onClose, onEnviar }: {
  borrador: Borrador; canWrite: boolean; onClose: () => void;
  /** Solo para un envío guardado: «Enviar ahora» desde la vista previa. */
  onEnviar?: () => Promise<boolean>;
}) {
  const toast = useToast();
  const { post, loading } = useMutation();
  const payload = useMemo(() => payloadDe(borrador), [borrador]);
  const [previo, setPrevio] = useState<Previo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bajando, setBajando] = useState<"xlsx" | "pdf" | null>(null);
  const [enviando, setEnviando] = useState(false);
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

  const enviar = async () => {
    if (!onEnviar) return;
    setEnviando(true);
    const ok = await onEnviar();
    setEnviando(false);
    if (ok) onClose();
  };

  return (
    <Modal open onClose={onClose} size="xl" title={`Vista previa del correo — ${borrador.nombre.trim() || "envío"}`}
           description="El correo y los adjuntos tal como saldrían hoy."
           footer={
             <div className="flex justify-end gap-2">
               <Button variant="secondary" onClick={onClose}>Cerrar</Button>
               {onEnviar && canWrite && (
                 <Button onClick={enviar} disabled={enviando || !previo || previo.para.length === 0}>
                   <Send size={15} /> {enviando ? "Enviando…" : "Enviar ahora"}
                 </Button>
               )}
             </div>
           }>
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

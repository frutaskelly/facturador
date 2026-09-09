"use client";

// La franja de «órdenes por resolver»: lo único que quedó de la bandeja de
// órdenes (ticket «Une los menús»). La ingesta convierte sola lo que cruza;
// aquí aparece SOLO lo que necesita una mano — un hospital sin sucursal, un
// posible duplicado, una partida que no cruzó — con su motivo escrito y las
// dos salidas: resolverla y pasarla a remisiones, o descartarla.

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, Trash2, Wand2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";
import type { Cliente, OCRecibida, Sucursal } from "@/lib/types";

const CANAL_TONE: Record<string, "accent" | "muted" | "default"> = {
  WHATSAPP: "accent",
  EMAIL: "default",
  MANUAL: "muted",
  API: "muted",
};

type PageOf<T> = { items: T[]; total: number };

export function OrdenesPorResolver({ onCambio }: { onCambio: () => void }) {
  const { me } = useAuth();
  const canVer = can(me, "menu:oc");
  const canWrite = can(me, "remision:gestionar");
  const toast = useToast();

  const [rows, setRows] = useState<OCRecibida[] | null>(null);
  const [abierta, setAbierta] = useState(true);
  const [ocupada, setOcupada] = useState<string | null>(null); // id con request en vuelo
  const [procesando, setProcesando] = useState(false);

  // Resolver (asignar cliente/sucursal) en un modal chico.
  const [aResolver, setAResolver] = useState<OCRecibida | null>(null);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  const [clienteSel, setClienteSel] = useState("");
  const [sucursalSel, setSucursalSel] = useState("");
  const [aprender, setAprender] = useState(true);
  const [errorModal, setErrorModal] = useState<string | null>(null);

  // Descartar con motivo.
  const [aDescartar, setADescartar] = useState<OCRecibida | null>(null);
  const [motivoDescarte, setMotivoDescarte] = useState("");

  const cargar = useCallback(() => {
    apiFetch<PageOf<OCRecibida>>("/api/v1/oc-recibidas?estado=PENDIENTE&limit=100")
      .then((p) => setRows(p.items))
      .catch(() => setRows(null)); // sin permiso o sin red: la franja no estorba
  }, []);

  useEffect(() => {
    if (canVer) cargar();
  }, [canVer, cargar]);

  useEffect(() => {
    if (!aResolver) return;
    setClienteSel(aResolver.cliente_id ?? "");
    setSucursalSel(aResolver.sucursal_id ?? "");
    setAprender(true);
    setErrorModal(null);
    if (!aResolver.cliente_id) {
      apiFetch<PageOf<Cliente>>("/api/v1/clientes?limit=1000")
        .then((p) => setClientes(p.items))
        .catch(() => setClientes([]));
    }
  }, [aResolver]);

  // Las sucursales dependen del cliente ELEGIDO, no del que traía la orden.
  useEffect(() => {
    if (!clienteSel) { setSucursales([]); return; }
    apiFetch<PageOf<Sucursal>>(`/api/v1/sucursales?cliente_id=${clienteSel}&limit=500`)
      .then((p) => setSucursales(p.items))
      .catch(() => setSucursales([]));
  }, [clienteSel]);

  if (!canVer || !rows || rows.length === 0) return null;

  async function pasar(oc: OCRecibida) {
    setOcupada(oc.id);
    try {
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}/crear-remision-sin-revisar`, { method: "POST" });
      toast.success(`La orden ${oc.folio_externo || ""} ya es una remisión por revisar.`.replace("  ", " "));
      cargar();
      onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo pasar la orden.");
      cargar(); // el motivo del backend quedó escrito en la fila
    } finally {
      setOcupada(null);
    }
  }

  async function resolverYPasar() {
    if (!aResolver) return;
    setOcupada(aResolver.id);
    setErrorModal(null);
    try {
      await apiFetch(`/api/v1/oc-recibidas/${aResolver.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          cliente_id: clienteSel || null,
          sucursal_id: sucursalSel || null,
          aprender,
        }),
      });
      await apiFetch(`/api/v1/oc-recibidas/${aResolver.id}/crear-remision-sin-revisar`, { method: "POST" });
      toast.success("Orden resuelta: ya es una remisión por revisar.");
      setAResolver(null);
      cargar();
      onCambio();
    } catch (e) {
      setErrorModal(e instanceof ApiError ? e.message : "No se pudo resolver la orden.");
      cargar();
    } finally {
      setOcupada(null);
    }
  }

  async function descartar() {
    if (!aDescartar) return;
    setOcupada(aDescartar.id);
    try {
      const qs = motivoDescarte.trim() ? `?motivo=${encodeURIComponent(motivoDescarte.trim())}` : "";
      await apiFetch(`/api/v1/oc-recibidas/${aDescartar.id}/descartar${qs}`, { method: "POST" });
      toast.success("Orden descartada.");
      setADescartar(null);
      setMotivoDescarte("");
      cargar();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descartar.");
    } finally {
      setOcupada(null);
    }
  }

  /** El lote del backend, repetido mientras avance: aprender UNA equivalencia
   *  (un hospital mapeado) destraba de golpe todas las órdenes de ese punto. */
  async function procesarTodo() {
    setProcesando(true);
    let creadas = 0;
    try {
      for (let i = 0; i < 20; i++) {
        const r = await apiFetch<{ creadas: number; fallidas: number; restantes: number }>(
          "/api/v1/oc-recibidas/procesar-pendientes", { method: "POST" },
        );
        creadas += r.creadas;
        if (r.creadas === 0) break; // sin avance: lo que queda necesita una mano
      }
      if (creadas > 0) toast.success(`${creadas} orden${creadas === 1 ? "" : "es"} pasaron a remisiones (por revisar).`);
      else toast.info("Nada que procesar en automático: lo que queda necesita una mano.");
      cargar();
      if (creadas > 0) onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "El proceso en lote falló.");
    } finally {
      setProcesando(false);
    }
  }

  return (
    <div className="mb-4 rounded-xl border border-amber-300/70 bg-amber-50/50 dark:border-amber-700/50 dark:bg-amber-950/20">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <button
          type="button"
          onClick={() => setAbierta((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          {abierta ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <span className="text-sm font-semibold text-amber-800 dark:text-amber-300">
            Órdenes por resolver ({rows.length})
          </span>
          <span className="hidden truncate text-xs text-muted sm:inline">
            — llegaron por WhatsApp o correo y no pudieron volverse remisión solas
          </span>
        </button>
        {canWrite ? (
          <Button variant="secondary" disabled={procesando} onClick={() => void procesarTodo()}>
            <Wand2 size={14} /> {procesando ? "Procesando…" : "Procesar lo que se pueda"}
          </Button>
        ) : null}
      </div>

      {abierta ? (
        <ul className="divide-y divide-amber-200/70 border-t border-amber-200/70 dark:divide-amber-800/40 dark:border-amber-800/40">
          {rows.map((oc) => (
            <li key={oc.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
              <span className="whitespace-nowrap text-xs text-muted">{fmtDate(oc.recibida_at)}</span>
              <Badge tone={CANAL_TONE[oc.canal] ?? "default"}>{oc.canal}</Badge>
              <span className="font-medium">
                {oc.cliente_nombre || oc.remitente || "Sin cliente"}
              </span>
              {oc.folio_externo ? <span className="tabular-nums text-muted">· {oc.folio_externo}</span> : null}
              {oc.punto_entrega ? <span className="text-muted">· {oc.punto_entrega}</span> : null}
              <span className="basis-full text-xs text-amber-800 dark:text-amber-300 sm:basis-auto sm:flex-1">
                {oc.motivo || "Sin motivo registrado"}
              </span>
              <span className="ml-auto flex items-center gap-1.5">
                {oc.archivo_url ? (
                  <a
                    href={oc.archivo_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-md p-1.5 text-muted hover:bg-surface-2"
                    title={oc.archivo_nombre || "Ver el documento original"}
                  >
                    <ExternalLink size={15} />
                  </a>
                ) : null}
                {canWrite ? (
                  <>
                    <Button
                      variant="secondary"
                      disabled={ocupada === oc.id}
                      onClick={() => {
                        // Con cliente y destino resueltos no hay nada que
                        // preguntar; si falta algo, el modal lo pide.
                        if (oc.cliente_id && (oc.sucursal_id || !oc.punto_entrega)) void pasar(oc);
                        else setAResolver(oc);
                      }}
                    >
                      {ocupada === oc.id ? "Pasando…" : "Pasar a remisiones"}
                    </Button>
                    <button
                      type="button"
                      onClick={() => { setMotivoDescarte(""); setADescartar(oc); }}
                      className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                      aria-label="Descartar"
                      title="Descartar"
                    >
                      <Trash2 size={15} />
                    </button>
                  </>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <Modal
        open={aResolver !== null}
        onClose={() => setAResolver(null)}
        title="Resolver la orden"
        footer={
          <>
            <Button variant="secondary" onClick={() => setAResolver(null)}>Cancelar</Button>
            <Button
              onClick={() => void resolverYPasar()}
              disabled={!clienteSel || ocupada === aResolver?.id}
            >
              Guardar y pasar a remisiones
            </Button>
          </>
        }
      >
        {aResolver ? (
          <div className="space-y-3">
            <p className="text-sm text-muted">{aResolver.motivo}</p>
            {errorModal ? <Alert tone="danger">{errorModal}</Alert> : null}
            <Field label="Cliente" required>
              {aResolver.cliente_id ? (
                <Input value={aResolver.cliente_nombre ?? ""} disabled />
              ) : (
                <Select value={clienteSel} onChange={(e) => { setClienteSel(e.target.value); setSucursalSel(""); }}>
                  <option value="">Elegir…</option>
                  {clientes.map((c) => (
                    <option key={c.id} value={c.id}>{c.legal_name}</option>
                  ))}
                </Select>
              )}
            </Field>
            <Field
              label="Sucursal"
              hint={aResolver.punto_entrega
                ? `El punto de entrega es «${aResolver.punto_entrega}» — de la sucursal salen la serie y los precios`
                : "De la sucursal salen la serie y los precios"}
            >
              <Select value={sucursalSel} onChange={(e) => setSucursalSel(e.target.value)} disabled={!clienteSel}>
                <option value="">(matriz / sin sucursal)</option>
                {sucursales.map((s) => (
                  <option key={s.id} value={s.id}>{s.nombre}</option>
                ))}
              </Select>
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={aprender} onChange={(e) => setAprender(e.target.checked)} />
              Aprenderlo para las próximas órdenes iguales
            </label>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={aDescartar !== null}
        onClose={() => setADescartar(null)}
        title="Descartar la orden"
        footer={
          <>
            <Button variant="secondary" onClick={() => setADescartar(null)}>Cancelar</Button>
            <Button variant="danger" onClick={() => void descartar()} disabled={ocupada === aDescartar?.id}>
              Descartar
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          La orden no genera remisión y sale de esta lista. No se borra: queda como
          descartada, con el motivo, por si hay que volver a ella.
        </p>
        <Field label="Motivo">
          <Input
            value={motivoDescarte}
            onChange={(e) => setMotivoDescarte(e.target.value)}
            placeholder="Ej. entró dos veces; es la misma que la remisión 128"
          />
        </Field>
      </Modal>
    </div>
  );
}

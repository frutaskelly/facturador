"use client";

// Las órdenes que llegaron por WhatsApp o correo y no pudieron volverse
// remisión solas. Ya no viven en una franja aparte: son filas de la misma
// tabla de remisiones, con estado REVISAR y el motivo a la vista.
//
// Aquí queda lo que esas filas necesitan —la lista, las acciones y los tres
// modales (resolver, corregir fecha y folio, descartar)— para que la pantalla
// solo las intercale.

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useResource, type Page } from "@/lib/hooks";
import type { Cliente, OCRecibida, Sucursal } from "@/lib/types";

/** Los mismos filtros de la lista de remisiones: la tabla es una sola, así que
 *  un filtro puesto arriba tiene que valer también para estas filas. El rango
 *  de fechas cae sobre la de RECEPCIÓN, que es la única que tienen. */
export type FiltrosOrdenes = {
  desde: string;
  hasta: string;
  clienteId: string;
  q: string;
};

export type OrdenesPorResolver = {
  /** Lo pendiente, ya filtrado. Vacío si no hay permiso o falló la carga: la
   *  tabla de remisiones no se cae por eso. */
  ordenes: OCRecibida[];
  /** Quien puede resolverlas (`remision:gestionar`); si no, solo las ve. */
  puedeResolver: boolean;
  /** Id de la orden con una petición en vuelo (para deshabilitar su botón). */
  ocupada: string | null;
  procesando: boolean;
  /** El lote del backend: convierte todo lo que ya cruce sin preguntar nada. */
  procesarTodo: () => Promise<void>;
  /** Pasarla a remisiones; si le falta cliente o destino, abre el modal. */
  resolver: (oc: OCRecibida) => void;
  /** Corregir la fecha de entrega y el folio que el bot leyó mal. */
  corregir: (oc: OCRecibida) => void;
  descartar: (oc: OCRecibida) => void;
  recargar: () => void;
  /** Los diálogos. La pantalla los monta una vez, fuera de la tabla. */
  modales: ReactNode;
};

export function useOrdenesPorResolver(
  { filtros, onCambio }: { filtros: FiltrosOrdenes; onCambio: () => void },
): OrdenesPorResolver {
  const { me } = useAuth();
  const canVer = can(me, "menu:oc");
  const puedeResolver = can(me, "remision:gestionar");
  const toast = useToast();

  const path = useMemo(() => {
    if (!canVer) return null;
    const p = new URLSearchParams({ estado: "PENDIENTE", limit: "200" });
    if (filtros.desde) p.set("fecha_desde", filtros.desde);
    if (filtros.hasta) p.set("fecha_hasta", filtros.hasta);
    if (filtros.clienteId) p.set("cliente_id", filtros.clienteId);
    if (filtros.q) p.set("q", filtros.q);
    return `/api/v1/oc-recibidas?${p.toString()}`;
  }, [canVer, filtros.desde, filtros.hasta, filtros.clienteId, filtros.q]);

  // Sin permiso o sin red la lista se queda vacía y no estorba: estas filas son
  // un añadido a la tabla, no la tabla.
  const { data, reload: recargar } = useResource<Page<OCRecibida>>(path);
  const ordenes = useMemo(() => data?.items ?? [], [data]);

  const [ocupada, setOcupada] = useState<string | null>(null);
  const [procesando, setProcesando] = useState(false);

  // Resolver (asignar cliente/sucursal) en un modal chico.
  const [aResolver, setAResolver] = useState<OCRecibida | null>(null);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  const [clienteSel, setClienteSel] = useState("");
  const [sucursalSel, setSucursalSel] = useState("");
  const [aprender, setAprender] = useState(true);
  const [errorModal, setErrorModal] = useState<string | null>(null);

  // Corregir la fecha y el folio. CE-38CER (26-sep-2026): el bot fechó en la
  // semana 38 pedidos de la 39, y la única salida era descartarlas.
  const [aCorregir, setACorregir] = useState<OCRecibida | null>(null);
  const [folioEdit, setFolioEdit] = useState("");
  const [fechaEdit, setFechaEdit] = useState("");
  const [errorCorregir, setErrorCorregir] = useState<string | null>(null);

  // Descartar con motivo.
  const [aDescartar, setADescartar] = useState<OCRecibida | null>(null);
  const [motivoDescarte, setMotivoDescarte] = useState("");

  useEffect(() => {
    if (!aResolver) return;
    setClienteSel(aResolver.cliente_id ?? "");
    setSucursalSel(aResolver.sucursal_id ?? "");
    setAprender(true);
    setErrorModal(null);
    if (!aResolver.cliente_id) {
      apiFetch<Page<Cliente>>("/api/v1/clientes?limit=1000")
        .then((p) => setClientes(p.items))
        .catch(() => setClientes([]));
    }
  }, [aResolver]);

  // Las sucursales dependen del cliente ELEGIDO, no del que traía la orden.
  useEffect(() => {
    if (!clienteSel) { setSucursales([]); return; }
    apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${clienteSel}&limit=500`)
      .then((p) => setSucursales(p.items))
      .catch(() => setSucursales([]));
  }, [clienteSel]);

  const pasar = useCallback(async (oc: OCRecibida) => {
    setOcupada(oc.id);
    try {
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}/crear-remision-sin-revisar`, { method: "POST" });
      toast.success(`La orden ${oc.folio_externo || ""} ya es una remisión por revisar.`.replace("  ", " "));
      recargar();
      onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo pasar la orden.");
      recargar(); // el motivo del backend quedó escrito en la fila
    } finally {
      setOcupada(null);
    }
  }, [onCambio, recargar, toast]);

  const resolver = useCallback((oc: OCRecibida) => {
    // Con cliente y destino resueltos no hay nada que preguntar; si falta algo,
    // el modal lo pide.
    if (oc.cliente_id && (oc.sucursal_id || !oc.punto_entrega)) void pasar(oc);
    else setAResolver(oc);
  }, [pasar]);

  const descartar = useCallback((oc: OCRecibida) => {
    setMotivoDescarte("");
    setADescartar(oc);
  }, []);

  const corregir = useCallback((oc: OCRecibida) => {
    setFolioEdit(oc.folio_externo ?? "");
    setFechaEdit(oc.fecha_entrega ?? "");
    setErrorCorregir(null);
    setACorregir(oc);
  }, []);

  // Solo viaja lo que cambió. Folio y fecha se corrigen, no se quitan: sin
  // ellos el candado de gemelas ya no puede comparar.
  const folioNuevo = folioEdit.trim();
  const cambios: { folio_externo?: string; fecha_entrega?: string } = {};
  if (aCorregir && folioNuevo && folioNuevo !== (aCorregir.folio_externo ?? "")) {
    cambios.folio_externo = folioNuevo;
  }
  if (aCorregir && fechaEdit && fechaEdit !== (aCorregir.fecha_entrega ?? "")) {
    cambios.fecha_entrega = fechaEdit;
  }
  const faltaDato = !!aCorregir && (
    (!!aCorregir.folio_externo && !folioNuevo) || (!!aCorregir.fecha_entrega && !fechaEdit)
  );
  const hayCambios = Object.keys(cambios).length > 0;

  async function guardarCorreccion(pasarDespues: boolean) {
    if (!aCorregir || !hayCambios || faltaDato) return;
    setOcupada(aCorregir.id);
    setErrorCorregir(null);
    let actualizada: OCRecibida;
    try {
      // `aprender` en false: una fecha no dice de quién es la orden.
      actualizada = await apiFetch<OCRecibida>(`/api/v1/oc-recibidas/${aCorregir.id}`, {
        method: "PATCH",
        body: JSON.stringify({ ...cambios, aprender: false }),
      });
    } catch (e) {
      setErrorCorregir(e instanceof ApiError ? e.message : "No se pudo corregir la orden.");
      setOcupada(null);
      return;
    }
    setOcupada(null);
    setACorregir(null);
    recargar();
    // Pasarla es el mismo camino del botón de la fila: directo si ya tiene
    // cliente y destino, y si no, el modal que los pide.
    if (pasarDespues) resolver(actualizada);
    else toast.success(actualizada.motivo
      ? "Orden corregida. Sigue sin poder pasar sola: revisa el motivo."
      : "Orden corregida.");
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
      recargar();
      onCambio();
    } catch (e) {
      setErrorModal(e instanceof ApiError ? e.message : "No se pudo resolver la orden.");
      recargar();
    } finally {
      setOcupada(null);
    }
  }

  async function confirmarDescarte() {
    if (!aDescartar) return;
    setOcupada(aDescartar.id);
    try {
      const qs = motivoDescarte.trim() ? `?motivo=${encodeURIComponent(motivoDescarte.trim())}` : "";
      await apiFetch(`/api/v1/oc-recibidas/${aDescartar.id}/descartar${qs}`, { method: "POST" });
      toast.success("Orden descartada.");
      setADescartar(null);
      setMotivoDescarte("");
      recargar();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descartar.");
    } finally {
      setOcupada(null);
    }
  }

  /** El lote del backend, repetido mientras avance: aprender UNA equivalencia
   *  (un hospital mapeado) destraba de golpe todas las órdenes de ese punto. */
  const procesarTodo = useCallback(async () => {
    setProcesando(true);
    let creadas = 0;
    let fallidas = 0;
    try {
      for (let i = 0; i < 20; i++) {
        const r = await apiFetch<{ creadas: number; fallidas: number; restantes: number }>(
          // Lotes CHICOS y timeout holgado: un lote grande tardaba más que el
          // timeout del fetch, el cliente se iba, y la transacción del server
          // quedaba abierta reteniendo candados (los zombis del 10-sep). Con 5
          // por pasada cada request termina en segundos y el bucle avanza igual.
          "/api/v1/oc-recibidas/procesar-pendientes?limite=5",
          { method: "POST" },
          { timeoutMs: 120_000 },
        );
        creadas += r.creadas;
        fallidas += r.fallidas;
        // Una fallida también es avance: queda con su motivo y el lote ya no
        // la vuelve a tomar. Cortar solo con `creadas` detenía el bucle cuando
        // la cabeza de la fila eran cinco que necesitaban una mano, y las
        // convertibles de atrás esperaban otro clic.
        if (r.creadas + r.fallidas === 0) break; // nada más que intentar
      }
      const quedaron = fallidas > 0
        ? ` ${fallidas} quedaron con su motivo para revisarlas a mano.`
        : "";
      if (creadas > 0) toast.success(`${creadas} orden${creadas === 1 ? "" : "es"} pasaron a remisiones (por revisar).${quedaron}`);
      else if (fallidas > 0) toast.info(`Ninguna pasó en automático.${quedaron}`);
      else toast.info("Nada que procesar en automático: lo que queda necesita una mano.");
      recargar();
      if (creadas > 0) onCambio();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "El proceso en lote falló.");
    } finally {
      setProcesando(false);
    }
  }, [onCambio, recargar, toast]);

  const modales = (
    <>
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
        open={aCorregir !== null}
        onClose={() => setACorregir(null)}
        title="Corregir fecha y folio"
        footer={
          <>
            <Button variant="secondary" onClick={() => setACorregir(null)}>Cancelar</Button>
            <Button
              variant="secondary"
              onClick={() => void guardarCorreccion(false)}
              disabled={!hayCambios || faltaDato || ocupada === aCorregir?.id}
            >
              Guardar
            </Button>
            <Button
              onClick={() => void guardarCorreccion(true)}
              disabled={!hayCambios || faltaDato || ocupada === aCorregir?.id}
            >
              Guardar y pasar a remisiones
            </Button>
          </>
        }
      >
        {aCorregir ? (
          <div className="space-y-3">
            {aCorregir.motivo ? <p className="text-sm text-muted">{aCorregir.motivo}</p> : null}
            {errorCorregir ? <Alert tone="danger">{errorCorregir}</Alert> : null}
            <Field
              label="Su pedido"
              required={!!aCorregir.folio_externo}
              hint="El folio lleva la semana y el día: si la fecha estaba mal, casi siempre el folio también."
            >
              <Input value={folioEdit} onChange={(e) => setFolioEdit(e.target.value)} />
            </Field>
            <Field
              label="Fecha de entrega"
              required={!!aCorregir.fecha_entrega}
              hint="Es la que usará la remisión. Un reenvío del bot ya no la vuelve a cambiar."
            >
              <Input type="date" value={fechaEdit} onChange={(e) => setFechaEdit(e.target.value)} />
            </Field>
            {faltaDato ? (
              <p className="text-xs text-danger">El folio y la fecha se corrigen, no se dejan vacíos.</p>
            ) : null}
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
            <Button variant="danger" onClick={() => void confirmarDescarte()} disabled={ocupada === aDescartar?.id}>
              Descartar
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-muted">
          La orden no genera remisión y sale de la tabla. No se borra: queda como
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
    </>
  );

  return {
    ordenes, puedeResolver, ocupada, procesando, procesarTodo, resolver, corregir, descartar,
    recargar, modales,
  };
}

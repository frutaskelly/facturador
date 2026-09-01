"use client";

// "Aprender" los precios tecleados a mano ANTES de guardar el documento.
//
// Cobrar otra cosa siempre es válido y no necesita permiso de nadie: la línea
// lleva su precio y ya. Lo que sí es una decisión aparte es que ese precio se
// QUEDE, porque escribe en otra pantalla — el catálogo de precios — desde una
// remisión. Por eso este diálogo no elige por ti: enseña los dos destinos y
// arranca en "solo en esta remisión".
//
// Los dos destinos no son intercambiables:
//   · precio especial  → `precio_overrides`, toca SOLO a este cliente.
//   · toda la lista    → `precios`, toca a TODOS los que cuelgan de la lista.
// Las listas se comparten entre clientes, así que la segunda opción dice a
// cuántos alcanza antes de que le des clic.

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import type { LineaForm } from "@/lib/lineas";

/** Una línea que se va a cobrar distinto de lo que dice el catálogo. */
export type PrecioDivergente = {
  key: string;
  producto_id: string;
  label: string;
  presentacion: string;
  /** Lo que se va a cobrar (el tecleado). */
  precio: string;
  /** Lo que decía el catálogo, o null si el producto no tenía precio ahí. */
  precioLista: string | null;
  /** Lista de la que salió la referencia (null si no salió de ninguna). */
  precioListaId: string | null;
  /** Tramo del que salió, para no pisar el tramo base con precio de volumen. */
  precioTramo: number | null;
};

const SOLO_DOCUMENTO = "";
const OVERRIDE = "override";

type ListaCandidata = { lista_id: string; nombre: string; alcance: string; clientes: number };

/** Las líneas del documento que valen la pena preguntar.
 *
 *  Solo las de precio TECLEADO: el precio que puso el sistema ya coincide con
 *  el catálogo por definición, y ofrecer "guardarlo" sería ruido. Se incluyen
 *  tanto las que difieren del catálogo como las que no tenían precio ahí —
 *  esas son justo las que el cliente pide "que se agreguen".
 */
export function divergentes(lineas: LineaForm[]): PrecioDivergente[] {
  const out: PrecioDivergente[] = [];
  for (const l of lineas) {
    if (!l.producto_id || !l.precioManual) continue;
    const v = Number(l.precio);
    if (!Number.isFinite(v) || !l.precio.trim() || v <= 0) continue;
    const ref = l.precioLista == null ? null : Number(l.precioLista);
    // Diferencia de un centavo = redondeo, no negociación.
    if (ref != null && Math.abs(v - ref) <= 0.01) continue;
    out.push({
      key: l.key,
      producto_id: l.producto_id,
      label: l.label || l.texto,
      presentacion: l.presentacion,
      precio: l.precio,
      precioLista: l.precioLista ?? null,
      precioListaId: l.precioListaId ?? null,
      precioTramo: l.precioTramo ?? null,
    });
  }
  return out;
}

export function AprenderPreciosDialog({
  open,
  lineas,
  clienteId,
  clienteNombre,
  sucursalId,
  sucursalNombre,
  onCancel,
  onDone,
}: {
  open: boolean;
  lineas: PrecioDivergente[];
  clienteId: string;
  clienteNombre: string;
  /** Cuando viene, el precio especial se guarda para ESA sucursal. */
  sucursalId?: string;
  sucursalNombre?: string;
  onCancel: () => void;
  /** Se llama cuando el usuario decidió: sigue el guardado del documento. */
  onDone: () => void;
}) {
  const toast = useToast();
  const [destino, setDestino] = useState<Record<string, string>>({});
  const [listas, setListas] = useState<ListaCandidata[]>([]);
  const [guardando, setGuardando] = useState(false);

  // Al abrir: las listas que le aplican a este cliente y a cuántos clientes
  // toca cada una. El conteo es el dato que evita el accidente — mover la lista
  // de Balles le cambia el precio a Jubran, porque cuelgan de la misma.
  useEffect(() => {
    if (!open || !clienteId) return;
    let vivo = true;
    setDestino({});
    (async () => {
      try {
        const r = await apiFetch<{ listas: { lista_id: string; nombre: string; alcance: string }[] }>(
          `/api/v1/precios/listas-del-cliente?cliente_id=${clienteId}`,
        );
        const conConteo = await Promise.all(
          (r.listas ?? []).map(async (l) => {
            let clientes = 0;
            try {
              const asg = await apiFetch<{ items: { cliente_id?: string | null }[] }>(
                `/api/v1/asignaciones-precios?lista_id=${l.lista_id}&limit=500`,
              );
              clientes = new Set(
                (asg.items ?? []).map((a) => a.cliente_id).filter(Boolean) as string[],
              ).size;
            } catch {
              /* sin permiso de ver asignaciones: se omite el conteo, no el destino */
            }
            return { ...l, clientes };
          }),
        );
        if (vivo) setListas(conConteo);
      } catch {
        if (vivo) setListas([]);
      }
    })();
    return () => {
      vivo = false;
    };
  }, [open, clienteId]);

  const hayAlgo = useMemo(
    () => Object.values(destino).some((d) => d && d !== SOLO_DOCUMENTO),
    [destino],
  );

  async function aplicar() {
    setGuardando(true);
    // Las de lista se agrupan por lista: un bulk por destino, no uno por línea.
    const porLista = new Map<string, PrecioDivergente[]>();
    const overrides: PrecioDivergente[] = [];
    for (const l of lineas) {
      const d = destino[l.key];
      if (!d || d === SOLO_DOCUMENTO) continue;
      if (d === OVERRIDE) overrides.push(l);
      else porLista.set(d, [...(porLista.get(d) ?? []), l]);
    }
    let ok = 0;
    const fallos: string[] = [];
    try {
      for (const [listaId, items] of porLista) {
        try {
          await apiFetch(`/api/v1/listas-precios/${listaId}/precios/bulk`, {
            method: "POST",
            body: JSON.stringify({
              items: items.map((l) => ({
                producto_id: l.producto_id,
                presentacion: l.presentacion,
                precio_unitario: l.precio,
                // El MISMO tramo del que salió la referencia: escribir el tramo
                // base con un precio de volumen sería un subcobro permanente.
                cantidad_minima: l.precioTramo ?? 1,
              })),
            }),
          });
          ok += items.length;
        } catch (e) {
          fallos.push(e instanceof ApiError ? e.message : "no se pudo escribir la lista");
        }
      }
      for (const l of overrides) {
        try {
          await apiFetch("/api/v1/precios/overrides", {
            method: "POST",
            body: JSON.stringify({
              ...(sucursalId ? { sucursal_id: sucursalId } : { cliente_id: clienteId }),
              producto_id: l.producto_id,
              presentacion: l.presentacion,
              precio_unitario: l.precio,
            }),
          });
          ok += 1;
        } catch (e) {
          fallos.push(e instanceof ApiError ? e.message : `no se pudo guardar ${l.label}`);
        }
      }
      if (ok > 0) toast.success(`${ok} ${ok === 1 ? "precio guardado" : "precios guardados"}`);
      // Un fallo al aprender NO detiene la remisión: el documento es lo que el
      // cliente está esperando, el catálogo se puede corregir después.
      if (fallos.length) toast.error(fallos[0]);
    } finally {
      setGuardando(false);
      onDone();
    }
  }

  return (
    <Modal
      open={open}
      onClose={onCancel}
      wide
      title="Estos precios no son los del catálogo"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={guardando}>
            Volver a la captura
          </Button>
          <Button onClick={() => void aplicar()} disabled={guardando}>
            {guardando ? "Guardando…" : hayAlgo ? "Guardar precios y continuar" : "Continuar sin guardar"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-muted">
          La remisión se va a cobrar con lo que capturaste, elijas lo que elijas. Lo que se
          pregunta aquí es si además <b>se queda guardado</b> — eso cambia el catálogo de
          precios desde esta pantalla, y por eso no se hace solo.
        </p>

        <div className="space-y-3">
          {lineas.map((l) => (
            <div key={l.key} className="rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="text-sm font-medium">
                  {l.label} <span className="text-muted">· {l.presentacion}</span>
                </div>
                <div className="text-sm tabular-nums">
                  {l.precioLista == null ? (
                    <span className="text-muted">sin precio en el catálogo</span>
                  ) : (
                    <span className="text-muted">catálogo {fmtMoney(Number(l.precioLista))}</span>
                  )}
                  {" → "}
                  <b>{fmtMoney(Number(l.precio))}</b>
                </div>
              </div>
              <div className="mt-2">
                <Select
                  value={destino[l.key] ?? SOLO_DOCUMENTO}
                  onChange={(e) => setDestino({ ...destino, [l.key]: e.target.value })}
                >
                  <option value={SOLO_DOCUMENTO}>Solo en esta remisión</option>
                  {/* Nombra el destino REAL: con sucursal el precio especial es
                      de esa plaza, no del cliente entero. */}
                  <option value={OVERRIDE}>
                    {sucursalId
                      ? `Precio especial de ${sucursalNombre || "esta sucursal"} (solo esa sucursal)`
                      : `Precio especial de ${clienteNombre || "este cliente"} (todas sus sucursales)`}
                  </option>
                  {listas.map((li) => (
                    <option key={li.lista_id} value={li.lista_id}>
                      Toda la lista «{li.nombre}»
                      {li.clientes > 1 ? ` — toca a ${li.clientes} clientes` : ""}
                    </option>
                  ))}
                </Select>
              </div>
              {destino[l.key] &&
                destino[l.key] !== SOLO_DOCUMENTO &&
                destino[l.key] !== OVERRIDE &&
                (listas.find((x) => x.lista_id === destino[l.key])?.clientes ?? 0) > 1 && (
                  <p className="mt-2 flex items-start gap-1.5 text-xs text-warning">
                    <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                    Esa lista la comparten varios clientes: a todos les cambia el precio de{" "}
                    {l.label}.
                  </p>
                )}
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}

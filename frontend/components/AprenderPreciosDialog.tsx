"use client";

// "Aprender" los precios tecleados a mano ANTES de guardar el documento.
//
// Cobrar otra cosa siempre es válido y no necesita permiso de nadie: la línea
// lleva su precio y ya. Lo que sí es una decisión aparte es que ese precio se
// QUEDE, porque escribe en otra pantalla — el catálogo de precios — desde una
// remisión. Regla del dueño (1-oct): el precio cambiado en la remisión SÍ va a
// la lista del cliente, así que cada línea llega ya apuntando a la lista de la
// que salió su precio (o, si no tenía, a la lista que cobra este documento).
// Sigue siendo un clic visible —nunca silencioso— y «Respetar precios de la
// OC» lo deja solo en la remisión. La lista BASE nunca se preselecciona.
//
// El único destino es la lista (`precios`), que toca a TODOS los que cuelgan
// de ella; por eso dice a cuántos clientes alcanza antes de que le des clic.
//
// Ya NO ofrece «precio especial» (`precio_overrides`): el dueño los borró el
// 1-oct porque se iban acumulando desde aquí — 25 de EHMO en un mes, cada uno
// ganándole a la lista para siempre. Un precio especial se da de alta a mano
// en Sucursales → Precios especiales, no como efecto de capturar una remisión.

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
/** Solo en el bloque masivo: cada línea a la lista de la que salió su precio. */
const DE_CADA_LINEA = "__de_cada_linea";

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
  listaDocumento,
  onCancel,
  onDone,
}: {
  open: boolean;
  lineas: PrecioDivergente[];
  clienteId: string;
  /** La lista que cobra este documento (sin la base): destino de las líneas
   *  que no traían precio de ninguna lista. */
  listaDocumento?: { lista_id: string; nombre: string } | null;
  onCancel: () => void;
  /** Se llama cuando el usuario decidió: sigue el guardado del documento. */
  onDone: () => void;
}) {
  const toast = useToast();
  const [destino, setDestino] = useState<Record<string, string>>({});
  // El destino del bloque masivo (ticket 86bby2gn2): con 30 líneas divergentes,
  // decidir renglón por renglón eran 30 clics de lo mismo. Se elige UNA vez,
  // se aplica a todas, y las excepciones se corrigen abajo, línea por línea.
  const [masivo, setMasivo] = useState<string>(SOLO_DOCUMENTO);
  const [listas, setListas] = useState<ListaCandidata[]>([]);
  const [guardando, setGuardando] = useState(false);

  /** La lista del cliente que le toca a la línea: de la que salió su precio,
   *  o la del documento si no salió de ninguna. */
  const listaDe = (l: PrecioDivergente) =>
    l.precioListaId ?? listaDocumento?.lista_id ?? SOLO_DOCUMENTO;

  // Al abrir: las listas que le aplican a este cliente y a cuántos clientes
  // toca cada una. El conteo es el dato que evita el accidente — mover la lista
  // de Balles le cambia el precio a Jubran, porque cuelgan de la misma.
  useEffect(() => {
    if (!open || !clienteId) return;
    let vivo = true;
    setDestino(Object.fromEntries(lineas.map((l) => [l.key, listaDe(l)])));
    setMasivo(DE_CADA_LINEA);
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
    // `lineas` y `listaDocumento` se leen al abrir; recalcular a media
    // decisión borraría lo que el usuario ya cambió.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clienteId]);

  // Una lista preseleccionada que no vino en listas-del-cliente (p.ej. sin
  // permiso de verlas) igual necesita su <option>, o el select la pierde.
  const faltantes = useMemo(() => {
    const ya = new Set(listas.map((x) => x.lista_id));
    return [...new Set(lineas.map(listaDe).filter((id) => id && !ya.has(id)))];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listas, lineas, listaDocumento]);

  // Las MISMAS opciones en el bloque masivo y en cada línea: el alcance del
  // cambio (solo este cliente vs toda la lista compartida) es la decisión que
  // este diálogo existe para hacer visible, se tome una vez o treinta.
  const opcionesDestino = (
    <>
      <option value={SOLO_DOCUMENTO}>Solo en esta remisión (respetar precio de la OC)</option>
      {faltantes.map((id) => (
        <option key={id} value={id}>
          {id === listaDocumento?.lista_id
            ? `Toda la lista «${listaDocumento.nombre}»`
            : "La lista de la que salió el precio"}
        </option>
      ))}
      {listas.map((li) => (
        <option key={li.lista_id} value={li.lista_id}>
          Toda la lista «{li.nombre}»
          {li.clientes > 1 ? ` — toca a ${li.clientes} clientes` : ""}
        </option>
      ))}
    </>
  );

  const hayAlgo = useMemo(
    () => Object.values(destino).some((d) => d && d !== SOLO_DOCUMENTO),
    [destino],
  );

  async function aplicar() {
    setGuardando(true);
    // Las de lista se agrupan por lista: un bulk por destino, no uno por línea.
    const porLista = new Map<string, PrecioDivergente[]>();
    for (const l of lineas) {
      const d = destino[l.key];
      if (!d || d === SOLO_DOCUMENTO) continue;
      porLista.set(d, [...(porLista.get(d) ?? []), l]);
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
      size="lg"
      title="Estos precios no son los del catálogo"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={guardando}>
            Volver a la captura
          </Button>
          {/* El camino explícito del ticket 86bbyw35w: los precios vienen
              negociados SOLO para esta orden — se usan en la remisión y no se
              escribe nada en las listas, aunque abajo se
              hubiera elegido algún destino. */}
          <Button
            variant="secondary"
            onClick={() => { setDestino({}); onDone(); }}
            disabled={guardando}
            title="Usa estos precios SOLO en esta remisión: no actualiza ninguna lista"
          >
            Respetar precios de la OC
          </Button>
          <Button onClick={() => void aplicar()} disabled={guardando || !hayAlgo}>
            {guardando ? "Guardando…" : "Guardar precios y continuar"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-muted">
          La remisión se va a cobrar con lo que capturaste, elijas lo que elijas. Cada línea
          ya viene apuntando a <b>la lista de precios del cliente</b> de la que salió su
          precio: «Guardar precios y continuar» la actualiza ahí.
        </p>
        <p className="text-sm text-muted">
          ¿Los precios vienen negociados <b>solo para esta orden de compra</b>?{" "}
          <b>«Respetar precios de la OC»</b> los usa en la remisión y no toca ninguna
          lista.
        </p>

        {lineas.length > 1 && (
          <div className="rounded-lg border border-border bg-surface-2 p-3">
            <div className="mb-2 text-sm font-medium">
              Mismo destino para las {lineas.length} líneas
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-64 flex-1">
                <Select value={masivo} onChange={(e) => setMasivo(e.target.value)}>
                  <option value={DE_CADA_LINEA}>La lista de cada línea (de donde salió su precio)</option>
                  {opcionesDestino}
                </Select>
              </div>
              <Button
                variant="secondary"
                onClick={() => setDestino(Object.fromEntries(lineas.map((l) => [l.key, masivo === DE_CADA_LINEA ? listaDe(l) : masivo])))}
              >
                Aplicar a todas
              </Button>
            </div>
            {masivo !== SOLO_DOCUMENTO &&
              (listas.find((x) => x.lista_id === masivo)?.clientes ?? 0) > 1 && (
                <p className="mt-2 flex items-start gap-1.5 text-xs text-warning">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  Esa lista la comparten varios clientes: a todos les cambian estos{" "}
                  {lineas.length} precios.
                </p>
              )}
            <p className="mt-2 text-xs text-muted">
              Después de aplicar puedes corregir cualquier línea individualmente aquí abajo.
            </p>
          </div>
        )}

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
                  {opcionesDestino}
                </Select>
              </div>
              {destino[l.key] &&
                destino[l.key] !== SOLO_DOCUMENTO &&
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

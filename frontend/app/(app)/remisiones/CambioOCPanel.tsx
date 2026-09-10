"use client";

// «Esta orden cambió después de volverse remisión» — reubicado en /remisiones.
//
// El cliente reenvía el mismo folio corregido y la ingesta, con razón, no pisa
// la captura: una remisión ya hecha no se corrige sola. Lo que faltaba era
// decirlo. Aquí se enseña QUÉ cambió y se cierra con una nota — no con un
// «marcar como leído», porque la acción correcta depende del estado de la
// remisión (BORRADOR se edita, CONFIRMADA ya salió de almacén, FACTURADA
// necesita sustituir el CFDI) y ninguna de las tres la puede hacer el sistema.
//
// Vivía en /oc (PR #114); la bandeja se fusionó en esta pantalla y el panel
// se cuelga del detalle expandido de la remisión, vía su `oc_id`.

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, Check } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import type { CambioLinea, OCRecibidaDetalle } from "@/lib/types";

const ETIQUETA_CABECERA: Record<string, string> = {
  fecha_entrega: "Fecha de entrega",
  observaciones: "Observaciones del documento",
};

function linea(l: CambioLinea) {
  // La clave primero: es por lo que el equipo identifica la partida cuando
  // compara contra el PDF que tiene abierto al lado.
  return [l.clave, l.descripcion, [l.cantidad, l.unidad].filter(Boolean).join(" "),
          l.precio ? `$${l.precio}` : null]
    .filter(Boolean)
    .join(" · ");
}

function Grupo({ titulo, tono, items }: {
  titulo: string;
  tono: string;
  items: CambioLinea[];
}) {
  if (!items.length) return null;
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{titulo}</div>
      <ul className="mt-1 space-y-0.5">
        {items.map((l, i) => (
          <li key={i} className={`text-sm ${tono}`}>{linea(l)}</li>
        ))}
      </ul>
    </div>
  );
}

/** Carga la OC (vistazo: sin cruce, barato) y pinta el diff con su cierre.
 *  Se renderiza solo cuando la fila trae `oc_cambio_abierto`, así que el
 *  fetch ocurre únicamente al expandir una remisión con incidencia. */
export function CambioOCPanel({ ocId, canWrite, onResuelto }: {
  ocId: string;
  canWrite: boolean;
  onResuelto: () => void;
}) {
  const toast = useToast();
  const [oc, setOc] = useState<OCRecibidaDetalle | null>(null);
  const [nota, setNota] = useState("");
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    let vivo = true;
    apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${ocId}?vistazo=true`)
      .then((d) => { if (vivo) setOc(d); })
      .catch(() => { if (vivo) setOc(null); });
    return () => { vivo = false; };
  }, [ocId]);

  if (!oc?.cambio_detectado_at || !oc.cambio_detalle) return null;
  const d = oc.cambio_detalle;
  const abierto = oc.cambio_abierto;
  const cuando = new Date(oc.cambio_detectado_at).toLocaleString("es-MX", {
    dateStyle: "short", timeStyle: "short",
  });

  async function resolver() {
    if (nota.trim().length < 3) {
      toast.error("Escribe qué decidiste: es lo que va a leer quien lo revise después");
      return;
    }
    setGuardando(true);
    try {
      await apiFetch(`/api/v1/oc-recibidas/${ocId}/cambio/resolver`, {
        method: "POST",
        body: JSON.stringify({ nota: nota.trim() }),
      });
      toast.success("Listo, queda registrado");
      setNota("");
      onResuelto();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cerrar");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="mb-3">
      <Alert tone={abierto ? "danger" : "info"}>
        <div className="space-y-3">
          <div className="font-medium">
            <span className="inline-flex items-center gap-1.5">
              <AlertTriangle size={15} />
              {abierto
                ? "El cliente mandó otra versión de esta orden DESPUÉS de que se remisionó"
                : "Esta orden cambió después de remisionarse (ya atendido)"}
            </span>
          </div>
          <div className="text-sm">
            {d.resumen} · detectado el {cuando}
          </div>

          <div className="grid gap-3 rounded-md bg-surface-2 p-3 sm:grid-cols-2">
            <Grupo titulo="Partidas nuevas" tono="text-emerald-700" items={d.lineas.nuevas} />
            <Grupo titulo="Partidas que ya no vienen" tono="text-rose-700" items={d.lineas.quitadas} />
            {d.lineas.cambiadas.length ? (
              <div className="sm:col-span-2">
                <div className="text-xs font-medium uppercase tracking-wide text-muted">
                  Partidas que cambiaron
                </div>
                <ul className="mt-1 space-y-1">
                  {d.lineas.cambiadas.map((c, i) => (
                    <li key={i} className="text-sm">
                      <div className="font-medium">{c.clave || c.descripcion}</div>
                      <div className="flex flex-wrap items-center gap-2 text-muted">
                        <span className="line-through">{c.antes.map(linea).join(" | ")}</span>
                        <ArrowRight size={13} />
                        <span className="text-foreground">{c.ahora.map(linea).join(" | ")}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {Object.entries(d.cabecera).map(([campo, v]) => (
              <div key={campo} className="sm:col-span-2 text-sm">
                <span className="text-muted">{ETIQUETA_CABECERA[campo] ?? campo}: </span>
                <span className="line-through text-muted">{v.antes || "—"}</span>
                <span className="mx-1.5">→</span>
                <strong>{v.ahora || "—"}</strong>
              </div>
            ))}
          </div>

          {abierto ? (
            <>
              <p className="text-sm">
                La remisión <strong>no se tocó</strong>. Si sigue en BORRADOR se corrige ahí; si ya se
                confirmó o se facturó, la corrección es una decisión — escríbela y cierra el aviso.
              </p>
              {canWrite ? (
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[18rem] flex-1">
                    <Field label="¿Qué hiciste?">
                      <Input
                        value={nota}
                        onChange={(e) => setNota(e.target.value)}
                        placeholder="Corregí la remisión a 40 KG / hablé con el cliente, se queda como está…"
                        maxLength={500}
                      />
                    </Field>
                  </div>
                  <Button onClick={resolver} disabled={guardando}>
                    <Check size={15} /> Cerrar el aviso
                  </Button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="text-sm">
              {oc.cambio_resuelto_nota ? <>«{oc.cambio_resuelto_nota}»</> : null}
              {oc.cambio_resuelto_at ? (
                <span className="text-muted">
                  {" "}— {new Date(oc.cambio_resuelto_at).toLocaleString("es-MX", {
                    dateStyle: "short", timeStyle: "short",
                  })}
                </span>
              ) : null}
            </div>
          )}
        </div>
      </Alert>
    </div>
  );
}

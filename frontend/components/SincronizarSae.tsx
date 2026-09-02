"use client";

/**
 * «Sincronizar SAE» — el botón de /facturas y /remisiones.
 *
 * El backend no ve SAE: el botón deja una SOLICITUD y el conector (que corre
 * en la Mac junto a SAE) la recoge en su siguiente vuelta, corre el espejo y
 * reporta. Por eso esto no es un spinner de 2 segundos: mientras la solicitud
 * viva, aquí se sondea cada 5 s y al terminar se recarga la lista de la
 * página. La fecha de «SAE actualizado» sale de la última corrida reportada —
 * incluidas las automáticas de cada 30 min, sin que nadie presione nada.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

type SyncRun = {
  id: string;
  estado: "PENDIENTE" | "EN_CURSO" | "OK" | "ERROR";
  origen: "MANUAL" | "AUTOMATICA";
  terminada_at?: string | null;
  resultado?: {
    enviadas?: number;
    canceladas?: number;
    errores?: string[];
    // La corrida también refresca las listas de precios vinculadas a SAE.
    precios?: { creados?: number; actualizados?: number; sin_cruce?: number } | null;
  } | null;
};
type SyncEstado = { ultima: SyncRun | null; pendiente: SyncRun | null };

const POLL_MS = 5_000;
// El conector recoge solicitudes cada minuto; si en 15 min nadie reportó,
// algo anda mal (bot caído) y seguir sondeando solo gasta — se avisa y se para.
const MAX_POLL_MS = 15 * 60_000;

export function SincronizarSae({ onSynced }: { onSynced?: () => void }) {
  const toast = useToast();
  const [estado, setEstado] = useState<SyncEstado | null>(null);
  const [pidiendo, setPidiendo] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const desde = useRef<number>(0);
  // El callback de recarga cambia en cada render de la página: se lee por ref
  // para que el ciclo de sondeo no se reinicie con cada tecla en un filtro.
  const onSyncedRef = useRef(onSynced);
  onSyncedRef.current = onSynced;

  const detener = () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
  };

  const sondear = useCallback(async (primeraVez: boolean) => {
    let est: SyncEstado;
    try {
      est = await apiFetch<SyncEstado>("/api/v1/facturas/espejo/sync");
    } catch {
      return; // sin red un instante: el siguiente tick lo reintenta
    }
    setEstado(est);
    if (!est.pendiente) {
      detener();
      if (!primeraVez) {
        // Terminó una solicitud que este componente estaba esperando.
        const r = est.ultima?.resultado;
        if (est.ultima?.estado === "OK") {
          const p = r?.precios;
          const precios = p && ((p.creados ?? 0) + (p.actualizados ?? 0)) > 0
            ? ` · precios: ${p.creados ?? 0} nuevo(s), ${p.actualizados ?? 0} actualizado(s)`
            : "";
          toast.success(`SAE sincronizado — ${r?.enviadas ?? 0} factura(s) actualizadas${precios}`);
        } else if (est.ultima?.estado === "ERROR") {
          toast.error(`La sincronización con SAE terminó con errores${r?.errores?.length ? `: ${r.errores[0]}` : ""}`);
        }
        onSyncedRef.current?.();
      }
      return;
    }
    if (Date.now() - desde.current > MAX_POLL_MS) {
      detener();
      toast.error("SAE no ha respondido a la solicitud — revisa que el conector esté corriendo");
      return;
    }
    timer.current = setTimeout(() => void sondear(false), POLL_MS);
  }, [toast]);

  useEffect(() => {
    desde.current = Date.now();
    void sondear(true);
    return detener;
  }, [sondear]);

  const solicitar = async () => {
    setPidiendo(true);
    try {
      const sol = await apiFetch<SyncRun>("/api/v1/facturas/espejo/sync", { method: "POST" });
      setEstado((e) => ({ ultima: e?.ultima ?? null, pendiente: sol }));
      desde.current = Date.now();
      detener();
      timer.current = setTimeout(() => void sondear(false), POLL_MS);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo solicitar la sincronización");
    } finally {
      setPidiendo(false);
    }
  };

  const enCurso = Boolean(estado?.pendiente);
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-xs text-muted" title="Última corrida del espejo SAE (manual o automática)">
        {estado === null
          ? ""
          : estado.ultima
            ? `SAE actualizado: ${fmtDateTime(estado.ultima.terminada_at)}${estado.ultima.estado === "ERROR" ? " (con errores)" : ""}`
            : "SAE: sin sincronizar"}
      </span>
      <Button variant="secondary" onClick={solicitar} disabled={enCurso || pidiendo}>
        <RefreshCw size={16} className={enCurso ? "animate-spin" : undefined} />
        {enCurso ? "Sincronizando…" : "Sincronizar SAE"}
      </Button>
    </span>
  );
}

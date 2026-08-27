"use client";

/**
 * Checklist guiado de "Primeros pasos" para el Dashboard (estilo Google
 * Workspace): barra de progreso + pasos con palomita, el siguiente paso
 * resaltado con su botón de acción. Se alimenta de GET /empresa/checklist
 * (datos vivos: se palomea solo conforme el usuario avanza).
 *
 * - Usuarios sin permiso de empresa (403) o error: la tarjeta no se muestra.
 * - Al completar todo: estado de festejo con botón "Ocultar" (localStorage
 *   por tenant); vuelve a aparecer si algo se descompleta.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, Loader2, PartyPopper } from "lucide-react";
import { apiFetch as api } from "@/lib/api";

import { Card } from "@/components/ui/Card";
import { apiFetch } from "@/lib/api";
import { getActiveTenantId } from "@/lib/tenant";

type Paso = {
  id: string;
  titulo: string;
  detalle: string;
  completo: boolean;
  href: string;
  cta: string;
  /** Paso de revisión que el usuario puede dar por bueno a mano. */
  marcable?: boolean;
  marcado_manual?: boolean;
};
type Checklist = {
  pasos: Paso[];
  completos: number;
  total: number;
  todo_listo: boolean;
  siguiente: string | null;
};

function dismissKey(): string {
  return `ss.primeros-pasos.oculto.${getActiveTenantId() ?? "default"}`;
}

export function PrimerosPasos() {
  const [data, setData] = useState<Checklist | null>(null);
  const [cargando, setCargando] = useState(true);
  const [oculto, setOculto] = useState(false);

  function cacheKey(): string {
    return `ss.primeros-pasos.cache.${getActiveTenantId() ?? "default"}`;
  }

  useEffect(() => {
    // Pinta al instante el último estado conocido (el fetch consulta al PAC y
    // puede tardar segundos); el resultado fresco lo reemplaza al llegar.
    try {
      const cached = localStorage.getItem(cacheKey());
      if (cached) {
        const d = JSON.parse(cached) as Checklist;
        setData(d);
        setOculto(d.todo_listo && localStorage.getItem(dismissKey()) === "1");
        setCargando(false);
      }
    } catch {
      /* caché corrupta: se ignora */
    }

    apiFetch<Checklist>("/api/v1/empresa/checklist")
      .then((d) => {
        setData(d);
        localStorage.setItem(cacheKey(), JSON.stringify(d));
        // Solo se respeta el "ocultar" cuando TODO está completo; si algo se
        // descompletó, la guía reaparece.
        const dismissed = localStorage.getItem(dismissKey()) === "1";
        setOculto(d.todo_listo && dismissed);
        if (!d.todo_listo) localStorage.removeItem(dismissKey());
      })
      .catch(() => {
        /* sin permiso o error: se queda la caché si había, o nada */
      })
      .finally(() => setCargando(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Marca (o desmarca) a mano un paso de revisión y refresca el checklist.
  async function marcar(pasoId: string, completo: boolean) {
    try {
      await api("/api/v1/empresa/checklist/marcar", {
        method: "POST",
        body: JSON.stringify({ paso: pasoId, completo }),
      });
      const d = await api<Checklist>("/api/v1/empresa/checklist");
      setData(d);
      try {
        localStorage.setItem(cacheKey(), JSON.stringify(d));
      } catch {
        /* sin caché no pasa nada */
      }
    } catch {
      /* el widget no es crítico: si falla, se queda como estaba */
    }
  }

  if (oculto) return null;

  // Primera visita (sin caché): indicador de carga en el lugar de la guía.
  if (!data) {
    if (!cargando) return null; // error sin caché: no se muestra
    return (
      <Card>
        <div className="flex items-center gap-3 py-2 text-sm text-muted">
          <Loader2 size={18} className="animate-spin" aria-hidden="true" />
          Cargando tu guía de primeros pasos…
        </div>
      </Card>
    );
  }

  const pct = data.total > 0 ? Math.round((data.completos / data.total) * 100) : 0;

  if (data.todo_listo) {
    return (
      <Card className="border-success/40">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-full bg-emerald-50 text-success">
              <PartyPopper size={20} aria-hidden="true" />
            </span>
            <div>
              <div className="font-semibold tracking-tight">¡Todo listo para trabajar!</div>
              <div className="text-sm text-muted">
                Completaste los {data.total} pasos. Tu empresa ya está operando en Facturador.
              </div>
            </div>
          </div>
          <button
            aria-label="Ocultar guía de primeros pasos"
            className="text-sm font-medium text-muted hover:text-foreground"
            onClick={() => {
              localStorage.setItem(dismissKey(), "1");
              setOculto(true);
            }}
          >
            Ocultar
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold tracking-tight">Primeros pasos</h2>
        <span className="text-sm text-muted">
          {data.completos} de {data.total} completados
        </span>
      </div>
      <p className="text-sm text-muted">
        Te llevamos de la mano: completa estos pasos y tu empresa queda lista para facturar.
      </p>
      <div
        role="progressbar"
        aria-valuenow={data.completos}
        aria-valuemin={0}
        aria-valuemax={data.total}
        aria-label={`${data.completos} de ${data.total} pasos completados`}
        className="mt-3 h-2 w-full overflow-hidden rounded-full bg-surface-2"
      >
        <div
          className="h-full rounded-full bg-success transition-all"
          style={{ width: `${Math.max(pct, 4)}%` }}
        />
      </div>

      <ol className="mt-4 divide-y divide-border">
        {data.pasos.map((p, i) => {
          const esSiguiente = p.id === data.siguiente;
          return (
            <li
              key={p.id}
              className={`flex items-center gap-3 py-3 ${
                esSiguiente ? "-mx-3 rounded-xl border border-accent/30 bg-surface px-3" : ""
              }`}
            >
              {p.completo ? (
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-success text-white">
                  <Check size={15} aria-hidden="true" />
                </span>
              ) : (
                <span
                  className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border text-xs font-semibold ${
                    esSiguiente ? "border-accent text-accent" : "border-border text-muted"
                  }`}
                >
                  {i + 1}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <div
                  className={`text-sm font-medium ${
                    p.completo ? "text-muted line-through decoration-border" : ""
                  }`}
                >
                  {p.titulo}
                  {p.completo && <span className="sr-only"> (completado)</span>}
                </div>
                {!p.completo && (
                  <div className="truncate text-xs text-muted">{p.detalle}</div>
                )}
              </div>
              {!p.completo && p.marcable && (
                <button
                  type="button"
                  onClick={() => marcar(p.id, true)}
                  className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-foreground"
                >
                  Marcar como listo
                </button>
              )}
              {p.completo && p.marcado_manual && (
                <button
                  type="button"
                  onClick={() => marcar(p.id, false)}
                  title="Quitar la marca manual"
                  className="shrink-0 text-xs font-medium text-muted hover:underline"
                >
                  Desmarcar
                </button>
              )}
              {!p.completo && (
                <Link
                  href={p.href}
                  className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                    esSiguiente
                      ? "bg-accent text-white hover:opacity-90"
                      : "border border-border hover:bg-surface-2"
                  }`}
                >
                  {p.cta} <ArrowRight size={14} aria-hidden="true" />
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

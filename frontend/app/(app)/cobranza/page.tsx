"use client";

// Cobranza — una sola pantalla para todo lo que es cobrar. Primero lo que se
// trabaja a mano: las facturas PPD por cobrar (se registra el pago), la
// antigüedad de saldos (la cartera de hoy), los Recibos de pago (REP,
// Complemento de Pago 2.0: se timbran, descargan, envían y cancelan) y las
// notas de crédito. Después, tras la raya, los envíos del estado de cuenta:
// cada uno (una razón social o varias) con su propia configuración, su
// interruptor Automático/Manual y su botón Enviar.
//
// Antes esto vivía en tres lados: /cobranza, /cobranza/automatica (redirige
// aquí, next.config.ts) y las pestañas Comprobantes de pago, Notas de crédito
// y Cuentas por cobrar de Reportes, que se quedó con lo que se vende. Las
// pestañas Contactos y Ajustes (oct-2026) se volvieron parte de cada envío:
// sus ligas viejas abren Envíos.
import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { can, useAuth } from "@/lib/auth";
import { useResource, type Page as Pagina } from "@/lib/hooks";
import type { Cliente } from "@/lib/types";

import { Cartera } from "./Cartera";
import { Envios } from "./Envios";
import { NotasCredito } from "./NotasCredito";
import { PorCobrar, RegistrarPago } from "./PorCobrar";
import { RecibosPago } from "./RecibosPago";

type Pestana = "por-cobrar" | "cartera" | "recibos" | "notas" | "envios";
const PESTANAS: { key: Pestana; label: string; auto?: boolean }[] = [
  { key: "por-cobrar", label: "Por cobrar" },
  { key: "cartera", label: "Antigüedad de saldos" },
  { key: "recibos", label: "Recibos de pago (REP)" },
  { key: "notas", label: "Notas de crédito" },
  { key: "envios", label: "Envíos de estado de cuenta", auto: true },
];
// Las pestañas que se volvieron parte de cada envío: su liga abre Envíos.
const ANTES_ENVIOS = ["contactos", "ajustes", "grupos"];
const DEFAULT: Pestana = "por-cobrar";
const esPestana = (v: string | null): v is Pestana => PESTANAS.some((p) => p.key === v);

export default function Page() {
  const { me } = useAuth();
  const canWrite = can(me, "factura:gestionar");

  // La pestaña vive en la URL (?tab=recibos): sobrevive F5 y se puede mandar
  // la liga. Se lee al montar (client-only, sin forzar Suspense) y no se pinta
  // nada antes: si no, la primera pestaña pediría sus datos de balde.
  const [pestana, setPestana] = useState<Pestana>(DEFAULT);
  const [hidratado, setHidratado] = useState(false);
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (esPestana(t)) setPestana(t);
    else if (t && ANTES_ENVIOS.includes(t)) setPestana("envios");
    setHidratado(true);
  }, []);
  useEffect(() => {
    if (!hidratado) return;
    const p = new URLSearchParams(window.location.search);
    if (pestana === DEFAULT) p.delete("tab"); else p.set("tab", pestana);
    const qs = p.toString();
    window.history.replaceState(window.history.state, "", qs ? `?${qs}` : window.location.pathname);
  }, [hidratado, pestana]);
  // En el celular la barra se desliza: la pestaña abierta (la de la liga, p.
  // ej. ?tab=envios) se centra para que no quede escondida a la derecha.
  const barra = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const b = barra.current;
    const t = b?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (b && t) b.scrollLeft = t.offsetLeft - (b.clientWidth - t.offsetWidth) / 2;
  }, [hidratado, pestana]);

  const clientesRes = useResource<Pagina<Cliente>>("/api/v1/clientes?limit=1000");
  const clientes = clientesRes.data?.items ?? [];

  // «Registrar pago» de arriba: al guardar, sube `rev` y la pestaña abierta
  // (pendientes o recibos) se recarga sola.
  const [nuevo, setNuevo] = useState(false);
  const [rev, setRev] = useState(0);
  const conPago = pestana === "por-cobrar" || pestana === "recibos";

  return (
    <div>
      <PageHeader
        title="Cobranza"
        subtitle="Lo que nos deben, lo que se ha pagado (REP y notas de crédito) y los envíos del estado de cuenta"
        actions={canWrite && conPago && <Button onClick={() => setNuevo(true)}><Plus size={16} /> Registrar pago</Button>}
      />

      {/* En el celular las pestañas no caben: se desliza de lado, sin
          barra de scroll. La raya de abajo es una sombra y no un borde, para
          que el scroll no la recorte. */}
      <div ref={barra} role="tablist" aria-label="Cobranza"
           className="relative mb-4 flex gap-1 overflow-x-auto [scrollbar-width:none] shadow-[inset_0_-1px_0_var(--border)]">
        {PESTANAS.map((t, i) => (
          <div key={t.key} className="flex shrink-0">
            {/* La raya separa lo que se hace a mano de lo que sale solo. */}
            {t.auto && !PESTANAS[i - 1]?.auto && <span aria-hidden className="mx-2 my-2 w-px bg-border" />}
            <button
              type="button"
              role="tab"
              aria-selected={pestana === t.key}
              onClick={() => setPestana(t.key)}
              className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm transition ${
                pestana === t.key
                  ? "border-accent font-medium text-foreground"
                  : "border-transparent text-muted hover:text-foreground"
              }`}
            >
              {t.label}
            </button>
          </div>
        ))}
      </div>

      {hidratado && (
        <>
          {pestana === "por-cobrar" && <PorCobrar clientes={clientes} canWrite={canWrite} rev={rev} />}
          {pestana === "cartera" && <Cartera />}
          {pestana === "recibos" && <RecibosPago clientes={clientes} canWrite={canWrite} rev={rev} />}
          {pestana === "notas" && <NotasCredito />}
          {pestana === "envios" && <Envios canWrite={canWrite} />}
        </>
      )}

      {nuevo && (
        <RegistrarPago clientes={clientes}
          onClose={() => setNuevo(false)}
          onDone={() => { setNuevo(false); setRev((n) => n + 1); }} />
      )}
    </div>
  );
}

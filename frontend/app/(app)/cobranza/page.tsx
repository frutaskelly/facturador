"use client";

// Cobranza — una sola pantalla para todo lo que es cobrar. Primero lo manual:
// las facturas PPD por cobrar (se registra el pago) y los Recibos de pago (REP,
// Complemento de Pago 2.0: se timbran, descargan, envían y cancelan). Después,
// tras la raya, la cobranza automática: los envíos del estado de cuenta, sus
// contactos y sus ajustes. Antes eran dos pantallas (/cobranza y
// /cobranza/automatica); la vieja dirección redirige aquí (next.config.ts).
import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { can, useAuth } from "@/lib/auth";
import { useResource, type Page as Pagina } from "@/lib/hooks";
import type { Cliente } from "@/lib/types";

import { Automatica, type PestanaAuto } from "./Automatica";
import { PorCobrar, RegistrarPago } from "./PorCobrar";
import { RecibosPago } from "./RecibosPago";

type Pestana = "por-cobrar" | "recibos" | PestanaAuto;
const PESTANAS: { key: Pestana; label: string; auto?: boolean }[] = [
  { key: "por-cobrar", label: "Por cobrar" },
  { key: "recibos", label: "Recibos de pago (REP)" },
  { key: "envios", label: "Envíos automáticos", auto: true },
  { key: "contactos", label: "Contactos", auto: true },
  { key: "ajustes", label: "Ajustes", auto: true },
];
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
  // ej. ?tab=ajustes) se centra para que no quede escondida a la derecha.
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
  const manual = pestana === "por-cobrar" || pestana === "recibos";

  return (
    <div>
      <PageHeader
        title="Cobranza"
        subtitle="Facturas PPD por cobrar, sus complementos de pago (REP) y el estado de cuenta automático"
        actions={canWrite && manual && <Button onClick={() => setNuevo(true)}><Plus size={16} /> Registrar pago</Button>}
      />

      {/* En el celular las cinco pestañas no caben: se desliza de lado, sin
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
          {pestana === "recibos" && <RecibosPago clientes={clientes} canWrite={canWrite} rev={rev} />}
          {!manual && <Automatica pestana={pestana} canWrite={canWrite} />}
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

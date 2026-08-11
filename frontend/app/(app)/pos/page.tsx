"use client";

// POS — hub de estaciones (Fase 0). Las tarjetas se dibujan según el flujo
// configurado (orden y etapas propias incluidos) ∩ los permisos del usuario;
// las pantallas de cada estación llegan en las Fases 1-3.
import Link from "next/link";
import { useEffect, useState } from "react";
import { Activity, ClipboardList, Layers, Settings, Store, Truck, Wallet, Warehouse } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { apiFetch } from "@/lib/api";
import { ETAPA_DESC, type EtapaCanonica, type PosConfig, etiquetaDe } from "@/lib/pos";

const ICONO: Record<string, React.ReactNode> = {
  pedido: <ClipboardList size={18} />,
  caja: <Wallet size={18} />,
  almacen: <Warehouse size={18} />,
  salida: <Truck size={18} />,
};

const FASE: Record<string, string> = {
  pedido: "Fase 1",
  caja: "Fase 2",
  almacen: "Fase 3",
  salida: "Fase 3",
};

// Estaciones cuya pantalla ya existe (tarjeta navegable).
const LISTO: Record<string, string> = {
  pedido: "/pos/pedido",
  caja: "/pos/caja",
  almacen: "/pos/almacen",
  salida: "/pos/salida",
};

export default function Page() {
  const [cfg, setCfg] = useState<PosConfig | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    apiFetch<PosConfig>("/api/v1/pos/config")
      .then(setCfg)
      .catch(() => setError(true));
  }, []);

  if (error) return <Alert tone="danger">No se pudo cargar el POS. Recarga la página.</Alert>;
  if (!cfg) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title="Punto de venta"
        subtitle={cfg.activo ? "Elige tu estación de trabajo" : "El POS está desactivado"}
        actions={cfg.puede_configurar ? (
          <Link href="/ajustes/pos" className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm hover:bg-surface-2">
            <Settings size={16} /> Configurar flujo
          </Link>
        ) : undefined}
      />
      {!cfg.activo ? (
        <Card>
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <Store size={32} className="text-muted" />
            <div className="font-medium">El POS está desactivado para este negocio</div>
            <p className="max-w-md text-sm text-muted">
              {cfg.puede_configurar
                ? "Actívalo y arma las etapas del flujo en Ajustes › Punto de venta."
                : "Pide a un administrador activarlo en Ajustes › Punto de venta."}
            </p>
          </div>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {(cfg.etapas_visibles ?? []).map((e) => {
            const href = LISTO[e];   // estación ya construida → tarjeta clickeable
            const inner = (
              <Card key={e} className={href ? "transition hover:border-accent" : undefined}>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <span className="rounded-lg bg-surface-2 p-2">{ICONO[e] ?? <Layers size={18} />}</span>
                    <div>
                      <div className="font-medium">{etiquetaDe(cfg, e)}</div>
                      <div className="text-sm text-muted">
                        {ETAPA_DESC[e as EtapaCanonica] ?? "Etapa propia de este negocio"}
                      </div>
                    </div>
                  </div>
                  {!href && <Badge tone="muted">{FASE[e] ?? "Fase 3"}</Badge>}
                </div>
                <p className="mt-3 text-sm text-muted">
                  {href
                    ? "Abrir esta estación →"
                    : `La pantalla de esta estación se construye en la ${FASE[e] ?? "Fase 3"} del plan del POS.`}
                </p>
              </Card>
            );
            return href ? <Link key={e} href={href}>{inner}</Link> : inner;
          })}
          {/* Operaciones: tablero de supervisión (no es una etapa del flujo) */}
          {(cfg.etapas_visibles ?? []).length > 0 && (
            <Link href="/pos/operaciones">
              <Card className="transition hover:border-accent">
                <div className="flex items-center gap-3">
                  <span className="rounded-lg bg-surface-2 p-2"><Activity size={18} /></span>
                  <div>
                    <div className="font-medium">Operaciones</div>
                    <div className="text-sm text-muted">Tablero del día y pedidos en curso</div>
                  </div>
                </div>
                <p className="mt-3 text-sm text-muted">Abrir tablero →</p>
              </Card>
            </Link>
          )}
          {(cfg.etapas_visibles ?? []).length === 0 && (
            <Alert tone="warning">
              Tu rol no tiene acceso a ninguna estación del flujo activo. Pide a un
              administrador el permiso de la estación que te toca.
            </Alert>
          )}
        </div>
      )}
    </div>
  );
}

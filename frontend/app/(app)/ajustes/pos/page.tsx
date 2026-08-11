"use client";

// Ajustes › Punto de venta — el FLUJO del POS es configuración por cliente:
// aquí se prenden/apagan etapas y se decide cuándo sale el inventario. El
// backend deriva la máquina de estados de esto (services/pos.py).
import { useEffect, useState } from "react";
import { ArrowRight, Store } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Select, Switch } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { useResource, type Page } from "@/lib/hooks";
import { ETAPAS_ORDEN, ETAPA_DESC, ETAPA_LABEL, type Etapa, type PosConfig } from "@/lib/pos";
import type { Serie } from "@/lib/types";

export default function Page() {
  const toast = useToast();
  const [cfg, setCfg] = useState<PosConfig | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);

  const seriesRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=REMISION&activa=true&limit=200");
  const series = seriesRes.data?.items ?? [];

  useEffect(() => {
    apiFetch<PosConfig>("/api/v1/pos/config")
      .then(setCfg)
      .catch(() => setLoadError(true));
  }, []);

  function toggleEtapa(e: Etapa) {
    if (e === "pedido" || !cfg) return;   // pedido siempre activa
    setCfg({
      ...cfg,
      etapas: cfg.etapas.includes(e)
        ? cfg.etapas.filter((x) => x !== e)
        : ETAPAS_ORDEN.filter((x) => cfg.etapas.includes(x) || x === e),
    });
  }

  async function guardar() {
    if (!cfg || saving) return;
    setSaving(true);
    try {
      const saved = await apiFetch<PosConfig>("/api/v1/pos/config", {
        method: "PUT",
        body: JSON.stringify({
          activo: cfg.activo,
          etapas: cfg.etapas,
          credito: cfg.credito,
          inventario_sale_en: cfg.inventario_sale_en,
          serie_id: cfg.serie_id,
          permitir_sobregiro: cfg.permitir_sobregiro,
          ticket: cfg.ticket,
        }),
      });
      setCfg(saved);
      toast.success("Configuración del POS guardada");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    } finally {
      setSaving(false);
    }
  }

  const pipeline: string[] = cfg
    ? [...ETAPAS_ORDEN.filter((e) => cfg.etapas.includes(e)).map((e) => ETAPA_LABEL[e]), "Completado"]
    : [];

  return (
    <div>
      <PageHeader
        title="Punto de venta"
        subtitle="Configura el flujo del POS según la operación de este negocio"
      />
      {loadError && (
        <Alert tone="danger">No se pudo cargar la configuración. Recarga la página.</Alert>
      )}
      {cfg && (
        <div className="grid max-w-3xl gap-4">
          <Card>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">POS activo</div>
                <div className="text-sm text-muted">
                  Apagado, las estaciones no aceptan pedidos nuevos.
                </div>
              </div>
              <Switch checked={cfg.activo} onChange={(v) => setCfg({ ...cfg, activo: v })} />
            </div>
          </Card>

          <Card>
            <div className="mb-2 font-medium">Etapas del flujo</div>
            <p className="mb-3 text-sm text-muted">
              Prende solo lo que este negocio usa: un mostrador vive con Pedido + Caja;
              una bodega usa las cuatro.
            </p>
            <div className="space-y-2">
              {ETAPAS_ORDEN.map((e) => (
                <div key={e} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                  <div>
                    <span className="font-medium">{ETAPA_LABEL[e]}</span>
                    <span className="ml-2 text-sm text-muted">{ETAPA_DESC[e]}</span>
                  </div>
                  <Switch
                    checked={cfg.etapas.includes(e)}
                    disabled={e === "pedido"}
                    onChange={() => toggleEtapa(e)}
                  />
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-1 rounded-lg bg-surface-2 px-3 py-2">
              <Store size={15} className="mr-1 text-muted" />
              {pipeline.map((p, i) => (
                <span key={p} className="flex items-center gap-1">
                  {i > 0 && <ArrowRight size={13} className="text-muted" />}
                  <Badge tone={p === "Completado" ? "muted" : "success"}>{p}</Badge>
                </span>
              ))}
            </div>
          </Card>

          <Card>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field
                label="El inventario sale al…"
                hint="Si esa etapa está apagada, sale al cerrar la última etapa activa"
              >
                <Select
                  value={cfg.inventario_sale_en}
                  onChange={(e) => setCfg({ ...cfg, inventario_sale_en: e.target.value as PosConfig["inventario_sale_en"] })}
                >
                  <option value="cobro">Cobrar (caja)</option>
                  <option value="surtido">Surtir (almacén)</option>
                  <option value="entrega">Entregar (salida)</option>
                </Select>
              </Field>
              <Field label="Serie de folios" hint="En blanco usa la resolución normal de series">
                <Select
                  value={cfg.serie_id ?? ""}
                  onChange={(e) => setCfg({ ...cfg, serie_id: e.target.value || null })}
                >
                  <option value="">(automática)</option>
                  {series.map((s) => (
                    <option key={s.id} value={s.id}>{s.codigo}{s.nombre ? ` · ${s.nombre}` : ""}</option>
                  ))}
                </Select>
              </Field>
            </div>
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">Venta a crédito</div>
                  <div className="text-sm text-muted">Permite cargar al saldo del cliente en caja (Fase 2)</div>
                </div>
                <Switch checked={cfg.credito} onChange={(v) => setCfg({ ...cfg, credito: v })} />
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">Permitir sobregiro</div>
                  <div className="text-sm text-muted">Vender sin existencia deja el inventario en negativo</div>
                </div>
                <Switch checked={cfg.permitir_sobregiro} onChange={(v) => setCfg({ ...cfg, permitir_sobregiro: v })} />
              </div>
            </div>
          </Card>

          <div className="flex justify-end">
            <Button onClick={() => void guardar()} disabled={saving}>
              {saving ? "Guardando…" : "Guardar configuración"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

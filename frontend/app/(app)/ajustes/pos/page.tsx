"use client";

// Ajustes › Punto de venta — el FLUJO del POS es configuración por cliente:
// etapas prendibles, EN EL ORDEN que la operación necesite, con etapas PROPIAS
// ("Empaque", "Verificación"…). El backend deriva la máquina de estados de esto.
import { useEffect, useState } from "react";
import { ArrowDown, ArrowRight, ArrowUp, Plus, Store, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { useResource, type Page } from "@/lib/hooks";
import {
  ETAPAS_CANONICAS, ETAPA_DESC, ETAPA_LABEL, PERMISOS_ETAPA,
  etiquetaDe, slugEtapa, type EtapaCanonica, type PosConfig,
} from "@/lib/pos";
import type { Serie } from "@/lib/types";

export default function Page() {
  const toast = useToast();
  const [cfg, setCfg] = useState<PosConfig | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);
  // Alta de etapa propia
  const [nuevaNombre, setNuevaNombre] = useState("");
  const [nuevaPermiso, setNuevaPermiso] = useState("pedido:surtir");

  const seriesRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=REMISION&activa=true&limit=200");
  const series = seriesRes.data?.items ?? [];

  useEffect(() => {
    apiFetch<PosConfig>("/api/v1/pos/config")
      .then(setCfg)
      .catch(() => setLoadError(true));
  }, []);

  if (loadError) return <Alert tone="danger">No se pudo cargar la configuración. Recarga la página.</Alert>;

  function toggleEtapa(e: string) {
    if (e === "pedido" || !cfg) return;   // pedido siempre activa y primera
    const activa = cfg.etapas.includes(e);
    setCfg({
      ...cfg,
      etapas: activa ? cfg.etapas.filter((x) => x !== e) : [...cfg.etapas, e],
      inventario_sale_en:
        activa && cfg.inventario_sale_en === e ? "crear" : cfg.inventario_sale_en,
    });
  }

  function mover(e: string, dir: -1 | 1) {
    if (!cfg) return;
    const i = cfg.etapas.indexOf(e);
    const j = i + dir;
    if (i <= 0 || j <= 0 || j >= cfg.etapas.length) return;  // pedido fijo al frente
    const etapas = [...cfg.etapas];
    [etapas[i], etapas[j]] = [etapas[j], etapas[i]];
    setCfg({ ...cfg, etapas });
  }

  function agregarCustom() {
    if (!cfg) return;
    const nombre = nuevaNombre.trim();
    const id = slugEtapa(nombre);
    if (!nombre || id.length < 2) { toast.error("Dale un nombre a la etapa (mínimo 2 letras)"); return; }
    if ((ETAPAS_CANONICAS as readonly string[]).includes(id) || cfg.etapas_custom.some((c) => c.id === id)) {
      toast.error("Ya existe una etapa con ese nombre");
      return;
    }
    setCfg({
      ...cfg,
      etapas_custom: [...cfg.etapas_custom, { id, nombre, permiso: nuevaPermiso }],
      etapas: [...cfg.etapas, id],
    });
    setNuevaNombre("");
  }

  function eliminarCustom(id: string) {
    if (!cfg) return;
    setCfg({
      ...cfg,
      etapas_custom: cfg.etapas_custom.filter((c) => c.id !== id),
      etapas: cfg.etapas.filter((e) => e !== id),
      inventario_sale_en: cfg.inventario_sale_en === id ? "crear" : cfg.inventario_sale_en,
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
          etapas_custom: cfg.etapas_custom,
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

  if (!cfg) return null;

  // Flujo ordenado (pedido primero) + canónicas apagadas disponibles para prender.
  const flujo = ["pedido", ...cfg.etapas.filter((e) => e !== "pedido")];
  const apagadas = (ETAPAS_CANONICAS as readonly string[]).filter((e) => !cfg.etapas.includes(e) && e !== "pedido");
  const pipeline = [...flujo.map((e) => etiquetaDe(cfg, e)), "Completado"];

  return (
    <div>
      <PageHeader
        title="Punto de venta"
        subtitle="Configura el flujo del POS según la operación de este negocio"
      />
      <div className="grid max-w-3xl gap-4">
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="font-medium">POS activo</div>
              <div className="text-sm text-muted">Apagado, las estaciones no aceptan pedidos nuevos.</div>
            </div>
            <Switch checked={cfg.activo} onChange={(v) => setCfg({ ...cfg, activo: v })} />
          </div>
        </Card>

        <Card>
          <div className="mb-2 font-medium">Etapas del flujo</div>
          <p className="mb-3 text-sm text-muted">
            Prende lo que este negocio usa, en el orden que trabaja. Las flechas reordenan;
            "Pedido" siempre va primero.
          </p>
          <div className="space-y-2">
            {flujo.map((e, i) => {
              const custom = cfg.etapas_custom.find((c) => c.id === e);
              return (
                <div key={e} className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2">
                  <div className="min-w-0">
                    <span className="font-medium">{etiquetaDe(cfg, e)}</span>
                    <span className="ml-2 text-sm text-muted">
                      {custom
                        ? `Etapa propia · ${PERMISOS_ETAPA.find((p) => p.value === custom.permiso)?.label ?? custom.permiso}`
                        : ETAPA_DESC[e as EtapaCanonica]}
                    </span>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {e !== "pedido" && (
                      <>
                        <button type="button" aria-label={`Subir ${etiquetaDe(cfg, e)}`}
                          onClick={() => mover(e, -1)} disabled={i <= 1}
                          className="rounded p-1 text-muted hover:text-foreground disabled:opacity-30">
                          <ArrowUp size={15} />
                        </button>
                        <button type="button" aria-label={`Bajar ${etiquetaDe(cfg, e)}`}
                          onClick={() => mover(e, 1)} disabled={i === flujo.length - 1}
                          className="rounded p-1 text-muted hover:text-foreground disabled:opacity-30">
                          <ArrowDown size={15} />
                        </button>
                        {custom && (
                          <button type="button" aria-label={`Eliminar ${custom.nombre}`}
                            onClick={() => eliminarCustom(e)}
                            className="rounded p-1 text-muted hover:text-danger">
                            <Trash2 size={15} />
                          </button>
                        )}
                      </>
                    )}
                    <Switch checked disabled={e === "pedido"} onChange={() => toggleEtapa(e)} />
                  </div>
                </div>
              );
            })}
            {apagadas.map((e) => (
              <div key={e} className="flex items-center justify-between gap-2 rounded-lg border border-dashed border-border px-3 py-2 opacity-70">
                <div>
                  <span className="font-medium">{ETAPA_LABEL[e as EtapaCanonica]}</span>
                  <span className="ml-2 text-sm text-muted">{ETAPA_DESC[e as EtapaCanonica]}</span>
                </div>
                <Switch checked={false} onChange={() => toggleEtapa(e)} />
              </div>
            ))}
          </div>

          {/* Agregar etapa propia */}
          <div className="mt-3 grid grid-cols-1 items-end gap-2 rounded-lg bg-surface-2 p-3 sm:grid-cols-[2fr_2fr_auto]">
            <Field label="Nueva etapa">
              <Input value={nuevaNombre} placeholder="Empaque, Verificación…"
                onChange={(e) => setNuevaNombre(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); agregarCustom(); } }} />
            </Field>
            <Field label="Quién la trabaja">
              <Select value={nuevaPermiso} onChange={(e) => setNuevaPermiso(e.target.value)}>
                {PERMISOS_ETAPA.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
              </Select>
            </Field>
            <Button variant="secondary" onClick={agregarCustom}><Plus size={16} /> Agregar</Button>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-1 rounded-lg bg-surface-2 px-3 py-2">
            <Store size={15} className="mr-1 text-muted" />
            {pipeline.map((p, i) => (
              <span key={`${p}-${i}`} className="flex items-center gap-1">
                {i > 0 && <ArrowRight size={13} className="text-muted" />}
                <Badge tone={p === "Completado" ? "muted" : "success"}>{p}</Badge>
              </span>
            ))}
          </div>
        </Card>

        <Card>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="El inventario sale al…"
              hint="Si esa etapa deja el flujo, sale al cerrar la última etapa">
              <Select
                value={cfg.inventario_sale_en}
                onChange={(e) => setCfg({ ...cfg, inventario_sale_en: e.target.value })}
              >
                <option value="crear">Crear el pedido</option>
                {flujo.filter((e) => e !== "pedido").map((e) => (
                  <option key={e} value={e}>Completar {etiquetaDe(cfg, e)}</option>
                ))}
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
    </div>
  );
}

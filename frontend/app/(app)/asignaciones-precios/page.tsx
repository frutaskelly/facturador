"use client";

// Asignación de precios — a QUIÉN se le aplica cada lista.
//
// Un renglón = una negociación. Las cuatro dimensiones (cliente, sucursal,
// serie, proyecto) son opcionales, y dejarlas vacías significa "cualquiera".
// Gana el renglón que coincide en las dimensiones más específicas; el número de
// prioridad lo calcula la base (proyecto 8 · serie 4 · sucursal 2 · cliente 1),
// así que aquí solo se muestra, nunca se recalcula.
import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, Wand2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { type Column } from "@/components/ui/DataTable";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import type {
  Cliente,
  ListaAsignacion,
  ListaPrecios,
  Proyecto,
  Serie,
  Sucursal,
} from "@/lib/types";

const WRITE = "lista_precios:gestionar";

const VACIO = {
  lista_id: "",
  cliente_id: "",
  sucursal_id: "",
  serie_id: "",
  proyecto_id: "",
  vigencia_desde: "",
  vigencia_hasta: "",
  notas: "",
};

/** Cómo se llama en pantalla el alcance de un renglón, por su dimensión más
 *  específica: es como lo nombra quien vende ("es el precio del proyecto"). */
function alcanceDe(a: ListaAsignacion): { texto: string; tono: "success" | "accent" | "warning" | "muted" } {
  if (a.proyecto_id) return { texto: "Proyecto", tono: "success" };
  if (a.serie_id) return { texto: "Serie", tono: "accent" };
  if (a.sucursal_id) return { texto: "Sucursal", tono: "warning" };
  return { texto: "Cliente (global)", tono: "muted" };
}

function guion(v?: string | null) {
  return v && v.trim() !== "" ? v : "—";
}

export default function AsignacionesPreciosPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  const { post, del, loading: saving } = useMutation();

  // ?cliente=<id> — se llega aquí desde la lista de Clientes.
  const clienteParam =
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("cliente")
      : null;

  const asignacionesRes = useResource<Page<ListaAsignacion>>(
    "/api/v1/asignaciones-precios?limit=500",
  );
  const listasRes = useResource<Page<ListaPrecios>>("/api/v1/listas-precios?limit=200");
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=500");
  const sucursalesRes = useResource<Page<Sucursal>>("/api/v1/sucursales?limit=1000");
  const seriesRes = useResource<Page<Serie>>("/api/v1/series?limit=200");
  const proyectosRes = useResource<Page<Proyecto>>("/api/v1/proyectos?limit=500");

  const todas = asignacionesRes.data?.items ?? [];
  const asignaciones = clienteParam ? todas.filter((a) => a.cliente_id === clienteParam) : todas;
  const listas = listasRes.data?.items ?? [];
  const clientes = clientesRes.data?.items ?? [];
  const sucursales = sucursalesRes.data?.items ?? [];
  const series = seriesRes.data?.items ?? [];
  const proyectos = proyectosRes.data?.items ?? [];

  const listaDefault = listas.find((l) => l.es_default);

  // ── alta ──
  const [form, setForm] = useState<typeof VACIO | null>(null);

  // Sucursales y proyectos se acotan al cliente elegido: cruzar los de otro
  // cliente crea un renglón que no puede aplicar nunca (el backend lo rechaza,
  // pero ofrecerlo ya sería mentir).
  const sucursalesDelCliente = useMemo(
    () => (form?.cliente_id ? sucursales.filter((s) => s.cliente_id === form.cliente_id) : []),
    [form?.cliente_id, sucursales],
  );
  const proyectosDelCliente = useMemo(
    () =>
      form?.cliente_id
        ? proyectos.filter((p) => !p.cliente_id || p.cliente_id === form.cliente_id)
        : proyectos.filter((p) => !p.cliente_id),
    [form?.cliente_id, proyectos],
  );

  async function guardar() {
    if (!form) return;
    if (!form.lista_id) {
      toast.error("Elige la lista de precios");
      return;
    }
    if (!form.cliente_id && !form.sucursal_id && !form.serie_id && !form.proyecto_id) {
      toast.error("Elige al menos cliente, sucursal, serie o proyecto");
      return;
    }
    try {
      await post("/api/v1/asignaciones-precios", {
        lista_id: form.lista_id,
        cliente_id: form.cliente_id || null,
        sucursal_id: form.sucursal_id || null,
        serie_id: form.serie_id || null,
        proyecto_id: form.proyecto_id || null,
        vigencia_desde: form.vigencia_desde || null,
        vigencia_hasta: form.vigencia_hasta || null,
        notas: form.notas.trim() || null,
      });
      toast.success("Asignación guardada");
      setForm(null);
      asignacionesRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  const [aBorrar, setABorrar] = useState<ListaAsignacion | null>(null);
  async function confirmarBorrado() {
    if (!aBorrar) return;
    try {
      await del(`/api/v1/asignaciones-precios/${aBorrar.id}`);
      toast.success("Asignación eliminada");
      setABorrar(null);
      asignacionesRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  // ── simulador: qué lista ganaría para una combinación ──
  const [sim, setSim] = useState({ cliente_id: "", sucursal_id: "", serie_id: "", proyecto_id: "" });
  const [simRes, setSimRes] = useState<ListaAsignacion | null>(null);
  const [simCargando, setSimCargando] = useState(false);

  const simSucursales = sim.cliente_id
    ? sucursales.filter((s) => s.cliente_id === sim.cliente_id)
    : [];
  const simProyectos = sim.cliente_id
    ? proyectos.filter((p) => !p.cliente_id || p.cliente_id === sim.cliente_id)
    : proyectos;

  useEffect(() => {
    const dims = Object.entries(sim).filter(([, v]) => v);
    if (dims.length === 0) {
      setSimRes(null);
      return;
    }
    let vivo = true;
    setSimCargando(true);
    const params = new URLSearchParams(dims as [string, string][]);
    apiFetch<ListaAsignacion | null>(`/api/v1/asignaciones-precios/simular?${params}`)
      .then((r) => vivo && setSimRes(r))
      .catch(() => vivo && setSimRes(null))
      .finally(() => vivo && setSimCargando(false));
    return () => {
      vivo = false;
    };
  }, [sim]);

  const columns: Column<ListaAsignacion>[] = [
    {
      header: "Alcance",
      cell: (a) => {
        const { texto, tono } = alcanceDe(a);
        return <Badge tone={tono}>{texto}</Badge>;
      },
    },
    { header: "Lista de precios", cell: (a) => <span className="font-medium">{guion(a.lista_nombre)}</span> },
    { header: "Cliente", cell: (a) => guion(a.cliente_nombre) },
    { header: "Sucursal", cell: (a) => guion(a.sucursal_nombre) },
    { header: "Serie", cell: (a) => guion(a.serie_codigo) },
    { header: "Proyecto", cell: (a) => guion(a.proyecto_nombre) },
    {
      header: "Vigencia",
      cell: (a) =>
        a.vigencia_desde || a.vigencia_hasta
          ? `${a.vigencia_desde ?? "…"} → ${a.vigencia_hasta ?? "…"}`
          : "Siempre",
    },
    {
      header: "Prioridad",
      className: "text-right",
      cell: (a) => <span className="tabular-nums text-muted">{a.especificidad}</span>,
    },
    ...(canWrite
      ? [
          {
            header: "",
            className: "text-right w-1",
            cell: (a: ListaAsignacion) => (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setABorrar(a);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                aria-label="Eliminar asignación"
              >
                <Trash2 size={16} />
              </button>
            ),
          },
        ]
      : []),
  ];

  return (
    <div>
      <PageHeader
        title="Asignación de precios"
        subtitle="Qué lista se le cobra a quién. Gana el renglón que coincide en lo más específico: proyecto, luego serie, luego sucursal, luego cliente."
        actions={
          canWrite ? (
            <Button onClick={() => setForm({ ...VACIO, cliente_id: clienteParam ?? "" })}>
              <Plus size={16} /> Nueva asignación
            </Button>
          ) : undefined
        }
      />

      {/* Simulador — la pregunta que el equipo hace de verdad: "¿qué precio le
          toca a esta orden?", contestada antes de emitir el documento. */}
      <div className="mb-6 rounded-lg border border-border p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium">
          <Wand2 size={16} /> ¿Qué lista le tocaría?
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <Field label="Cliente">
            <Select
              value={sim.cliente_id}
              onChange={(e) =>
                setSim({ cliente_id: e.target.value, sucursal_id: "", serie_id: "", proyecto_id: "" })
              }
            >
              <option value="">— Ninguno —</option>
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sucursal">
            <Select
              value={sim.sucursal_id}
              onChange={(e) => setSim({ ...sim, sucursal_id: e.target.value })}
              disabled={!sim.cliente_id}
            >
              <option value="">— Ninguna —</option>
              {simSucursales.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.nombre}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Serie">
            <Select value={sim.serie_id} onChange={(e) => setSim({ ...sim, serie_id: e.target.value })}>
              <option value="">— Ninguna —</option>
              {series.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.codigo} · {s.tipo_documento}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Proyecto">
            <Select
              value={sim.proyecto_id}
              onChange={(e) => setSim({ ...sim, proyecto_id: e.target.value })}
            >
              <option value="">— Ninguno —</option>
              {simProyectos.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.nombre}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="mt-3 text-sm">
          {simCargando ? (
            <span className="text-muted">Resolviendo…</span>
          ) : simRes ? (
            <>
              Gana <b>{simRes.lista_nombre}</b> — por{" "}
              <b>{alcanceDe(simRes).texto.toLowerCase()}</b> (prioridad {simRes.especificidad}).
            </>
          ) : Object.values(sim).some(Boolean) ? (
            <span className="text-muted">
              Ninguna asignación coincide
              {listaDefault ? (
                <>
                  {" "}
                  → se cobra la lista base del negocio, <b>{listaDefault.nombre}</b>.
                </>
              ) : (
                " y no hay lista base marcada: el documento pediría el precio a mano."
              )}
            </span>
          ) : (
            <span className="text-muted">Elige una combinación para ver qué lista aplicaría.</span>
          )}
        </div>
      </div>

      <DataTableSmart
        columns={columns}
        rows={asignaciones}
        loading={asignacionesRes.loading}
        error={asignacionesRes.error}
        empty="Sin asignaciones: todos los clientes cobran la lista base del negocio."
        storageKey="asignaciones-precios"
      />

      <Modal
        open={form !== null}
        onClose={() => setForm(null)}
        title="Nueva asignación de precios"
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Cancelar
            </Button>
            <Button onClick={guardar} disabled={saving}>
              {saving ? "Guardando…" : "Guardar"}
            </Button>
          </>
        }
      >
        {form && (
          <div className="space-y-4">
            <Field label="Lista de precios" required>
              <Select
                value={form.lista_id}
                onChange={(e) => setForm({ ...form, lista_id: e.target.value })}
              >
                <option value="">— Elige —</option>
                {listas
                  .filter((l) => l.status === "ACTIVO")
                  .map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.codigo} — {l.nombre}
                    </option>
                  ))}
              </Select>
            </Field>

            <p className="text-xs text-muted">
              Deja en blanco lo que no aplique. Solo el cliente = <b>mismos precios en todo el
              país</b>; agregar sucursal, serie o proyecto acota la negociación y le gana.
            </p>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Cliente">
                <Select
                  value={form.cliente_id}
                  onChange={(e) =>
                    setForm({ ...form, cliente_id: e.target.value, sucursal_id: "", proyecto_id: "" })
                  }
                >
                  <option value="">— Cualquiera —</option>
                  {clientes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.legal_name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Sucursal"
                hint={form.cliente_id ? undefined : "Elige primero el cliente."}
              >
                <Select
                  value={form.sucursal_id}
                  onChange={(e) => setForm({ ...form, sucursal_id: e.target.value })}
                  disabled={!form.cliente_id}
                >
                  <option value="">— Cualquiera —</option>
                  {sucursalesDelCliente.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.nombre}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Serie">
                <Select
                  value={form.serie_id}
                  onChange={(e) => setForm({ ...form, serie_id: e.target.value })}
                >
                  <option value="">— Cualquiera —</option>
                  {series.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.codigo} · {s.tipo_documento}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Proyecto">
                <Select
                  value={form.proyecto_id}
                  onChange={(e) => setForm({ ...form, proyecto_id: e.target.value })}
                >
                  <option value="">— Cualquiera —</option>
                  {proyectosDelCliente.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.nombre}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Vigente desde" hint="Vacío = desde siempre.">
                <Input
                  type="date"
                  value={form.vigencia_desde}
                  onChange={(e) => setForm({ ...form, vigencia_desde: e.target.value })}
                />
              </Field>
              <Field label="Vigente hasta" hint="Vacío = sin vencimiento.">
                <Input
                  type="date"
                  value={form.vigencia_hasta}
                  onChange={(e) => setForm({ ...form, vigencia_hasta: e.target.value })}
                />
              </Field>
            </div>

            <Field label="Notas" hint="Con qué se negoció: contrato, licitación, correo.">
              <Textarea
                rows={2}
                value={form.notas}
                onChange={(e) => setForm({ ...form, notas: e.target.value })}
              />
            </Field>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={aBorrar !== null}
        title="Eliminar asignación"
        message={
          aBorrar
            ? `¿Quitar «${aBorrar.lista_nombre}» de ${alcanceDe(aBorrar).texto.toLowerCase()}? Los documentos nuevos pasarán a la asignación menos específica que aplique, o a la lista base.`
            : ""
        }
        onConfirm={confirmarBorrado}
        onClose={() => setABorrar(null)}
        loading={saving}
      />
    </div>
  );
}

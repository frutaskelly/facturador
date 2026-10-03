"use client";

// «¿Qué lista le tocaría?» — la pregunta que el equipo hace de verdad antes de
// emitir un documento. Vivía en Asignación de precios; desde que la lista se
// escoge donde vive la negociación (ficha del proyecto, vínculo cliente×plaza,
// ficha del cliente) se quedó aquí, en Listas de precios.
import { useEffect, useState } from "react";
import { Wand2 } from "lucide-react";

import { Field, Select } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api";
import { useResource, type Page } from "@/lib/hooks";
import type { Cliente, ListaAsignacion, ListaPrecios, Proyecto, Sucursal } from "@/lib/types";

/** Dónde se escogió la lista que ganó: es donde hay que ir a cambiarla. */
function dondeSeEscoge(a: ListaAsignacion): string {
  if (a.proyecto_id) return `la ficha del proyecto ${a.proyecto_nombre ?? ""}`.trim();
  if (a.serie_id) return "una asignación por serie";
  if (a.sucursal_id) return "el vínculo del cliente con la plaza (Clientes → Sucursales)";
  return "la ficha del cliente";
}

export function SimuladorLista({ listas }: { listas: ListaPrecios[] }) {
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=500");
  const sucursalesRes = useResource<Page<Sucursal>>("/api/v1/sucursales?limit=1000");
  const proyectosRes = useResource<Page<Proyecto>>("/api/v1/proyectos?activo=true&limit=500");
  const clientes = clientesRes.data?.items ?? [];
  const sucursales = sucursalesRes.data?.items ?? [];
  const proyectos = proyectosRes.data?.items ?? [];
  const listaDefault = listas.find((l) => l.es_default);

  const [sim, setSim] = useState({ cliente_id: "", sucursal_id: "", proyecto_id: "" });
  const [res, setRes] = useState<ListaAsignacion | null>(null);
  const [cargando, setCargando] = useState(false);

  const sucursalesDelCliente = sim.cliente_id
    ? sucursales.filter((s) => (s.clientes_ids ?? []).includes(sim.cliente_id))
    : [];
  const proyectosDelCliente = sim.cliente_id
    ? proyectos.filter((p) => !p.cliente_id || p.cliente_id === sim.cliente_id)
    : proyectos;

  useEffect(() => {
    const dims = Object.entries(sim).filter(([, v]) => v);
    if (dims.length === 0) {
      setRes(null);
      return;
    }
    let vivo = true;
    setCargando(true);
    const params = new URLSearchParams(dims as [string, string][]);
    apiFetch<ListaAsignacion | null>(`/api/v1/asignaciones-precios/simular?${params}`)
      .then((r) => vivo && setRes(r))
      .catch(() => vivo && setRes(null))
      .finally(() => vivo && setCargando(false));
    return () => {
      vivo = false;
    };
  }, [sim]);

  return (
    <div className="mb-6 rounded-lg border border-border p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <Wand2 size={16} /> ¿Qué lista le tocaría?
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Cliente">
          <Select
            value={sim.cliente_id}
            onChange={(e) => setSim({ cliente_id: e.target.value, sucursal_id: "", proyecto_id: "" })}
          >
            <option value="">— Ninguno —</option>
            {clientes.map((c) => (
              <option key={c.id} value={c.id}>{c.legal_name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Plaza">
          <Select
            value={sim.sucursal_id}
            onChange={(e) => setSim({ ...sim, sucursal_id: e.target.value })}
            disabled={!sim.cliente_id}
          >
            <option value="">— Ninguna —</option>
            {sucursalesDelCliente.map((s) => (
              <option key={s.id} value={s.id}>{s.nombre}</option>
            ))}
          </Select>
        </Field>
        <Field label="Proyecto">
          <Select value={sim.proyecto_id} onChange={(e) => setSim({ ...sim, proyecto_id: e.target.value })}>
            <option value="">— Ninguno —</option>
            {proyectosDelCliente.map((p) => (
              <option key={p.id} value={p.id}>{p.nombre}</option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="mt-3 text-sm">
        {cargando ? (
          <span className="text-muted">Resolviendo…</span>
        ) : res ? (
          <>
            Se cobra con <b>{res.lista_nombre}</b>. Se escoge en {dondeSeEscoge(res)}.
          </>
        ) : Object.values(sim).some(Boolean) ? (
          <span className="text-muted">
            Ni el proyecto, ni la plaza, ni el cliente tienen lista
            {listaDefault ? (
              <> → se cobra la lista base del negocio, <b>{listaDefault.nombre}</b>.</>
            ) : (
              " y no hay lista base marcada: el documento pediría el precio a mano."
            )}
          </span>
        ) : (
          <span className="text-muted">Elige cliente, plaza o proyecto para ver con qué lista se cobraría.</span>
        )}
      </div>
    </div>
  );
}

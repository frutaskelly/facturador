"use client";

// Los productos de una CATEGORÍA o de un ESQUEMA, con edición inline — el
// slidedown de las pantallas de catálogo (28-ago-2026). Editar aquí evita el
// viaje a /productos por cada corrección: nombre, categoría, esquema y clave
// SAT se corrigen donde se está viendo el problema.
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import type { Page } from "@/lib/hooks";
import type { Categoria, EsquemaImpuesto, Producto } from "@/lib/types";

type Filtro = { categoria_id?: string; esquema_impuesto_id?: string };

type Edicion = { nombre: string; categoria_id: string; esquema_impuesto_id: string; clave_sat: string };

export function ProductosDelGrupo({ filtro, canWrite }: { filtro: Filtro; canWrite: boolean }) {
  const toast = useToast();
  const [rows, setRows] = useState<Producto[] | null>(null);
  const [cats, setCats] = useState<Categoria[]>([]);
  const [esquemas, setEsquemas] = useState<EsquemaImpuesto[]>([]);
  const [edits, setEdits] = useState<Record<string, Edicion>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const qs = useMemo(() => {
    const p = new URLSearchParams({ limit: "1000" });
    if (filtro.categoria_id) p.set("categoria_id", filtro.categoria_id);
    if (filtro.esquema_impuesto_id) p.set("esquema_impuesto_id", filtro.esquema_impuesto_id);
    return p.toString();
  }, [filtro.categoria_id, filtro.esquema_impuesto_id]);

  useEffect(() => {
    apiFetch<Page<Producto>>(`/api/v1/productos?${qs}`)
      .then((p) => setRows(p.items))
      .catch(() => setRows([]));
    apiFetch<Page<Categoria>>("/api/v1/categorias?limit=200")
      .then((p) => setCats(p.items))
      .catch(() => undefined);
    apiFetch<Page<EsquemaImpuesto>>("/api/v1/esquemas-impuesto?limit=200")
      .then((p) => setEsquemas(p.items))
      .catch(() => undefined);
  }, [qs]);

  function edicionDe(p: Producto): Edicion {
    return (
      edits[p.id] ?? {
        nombre: p.nombre,
        categoria_id: p.categoria_id ?? "",
        esquema_impuesto_id: p.esquema_impuesto_id ?? "",
        clave_sat: p.clave_sat ?? "",
      }
    );
  }

  function cambiar(p: Producto, patch: Partial<Edicion>) {
    setEdits((prev) => ({ ...prev, [p.id]: { ...edicionDe(p), ...patch } }));
  }

  function tocado(p: Producto): boolean {
    const e = edits[p.id];
    if (!e) return false;
    return (
      e.nombre !== p.nombre ||
      e.categoria_id !== (p.categoria_id ?? "") ||
      e.esquema_impuesto_id !== (p.esquema_impuesto_id ?? "") ||
      e.clave_sat !== (p.clave_sat ?? "")
    );
  }

  async function guardar(p: Producto) {
    const e = edicionDe(p);
    if (!e.nombre.trim()) { toast.error("El nombre no puede quedar vacío"); return; }
    setSaving(p.id);
    try {
      const actualizado = await apiFetch<Producto>(`/api/v1/productos/${p.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          nombre: e.nombre.trim(),
          categoria_id: e.categoria_id || null,
          esquema_impuesto_id: e.esquema_impuesto_id || null,
          clave_sat: e.clave_sat.trim() || null,
        }),
      });
      setRows((prev) => (prev ?? []).map((x) => (x.id === p.id ? actualizado : x)));
      setEdits((prev) => {
        const { [p.id]: _, ...resto } = prev;
        return resto;
      });
      toast.success(`${actualizado.nombre} guardado`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "No se pudo guardar");
    } finally {
      setSaving(null);
    }
  }

  if (rows === null) return <div className="flex justify-center py-6"><Spinner /></div>;
  if (!rows.length) return <p className="py-4 text-sm text-muted">Sin productos aquí.</p>;

  return (
    <div className="overflow-x-auto py-2">
      <table className="w-full text-sm">
        <thead className="text-xs text-muted">
          <tr>
            <th className="px-2 py-1.5 text-left">SKU</th>
            <th className="px-2 py-1.5 text-left">Nombre</th>
            <th className="px-2 py-1.5 text-left">Categoría</th>
            <th className="px-2 py-1.5 text-left">Esquema</th>
            <th className="px-2 py-1.5 text-left">Clave SAT</th>
            {canWrite ? <th className="w-1 px-2 py-1.5" /> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const e = edicionDe(p);
            return (
              <tr key={p.id} className="border-t border-border">
                <td className="px-2 py-1.5 font-medium tabular-nums">{p.sku}</td>
                <td className="min-w-64 px-2 py-1.5">
                  {canWrite ? (
                    <Input value={e.nombre} onChange={(ev) => cambiar(p, { nombre: ev.target.value })} />
                  ) : (
                    p.nombre
                  )}
                </td>
                <td className="min-w-40 px-2 py-1.5">
                  {canWrite ? (
                    <Select value={e.categoria_id} onChange={(ev) => cambiar(p, { categoria_id: ev.target.value })}>
                      <option value="">— Sin categoría —</option>
                      {cats.map((c) => (
                        <option key={c.id} value={c.id}>{c.nombre}</option>
                      ))}
                    </Select>
                  ) : (
                    cats.find((c) => c.id === p.categoria_id)?.nombre ?? "—"
                  )}
                </td>
                <td className="min-w-40 px-2 py-1.5">
                  {canWrite ? (
                    <Select
                      value={e.esquema_impuesto_id}
                      onChange={(ev) => cambiar(p, { esquema_impuesto_id: ev.target.value })}
                    >
                      <option value="">— Sin esquema —</option>
                      {esquemas.map((s) => (
                        <option key={s.id} value={s.id}>{s.nombre}</option>
                      ))}
                    </Select>
                  ) : (
                    esquemas.find((s) => s.id === p.esquema_impuesto_id)?.nombre ?? "—"
                  )}
                </td>
                <td className="w-32 px-2 py-1.5">
                  {canWrite ? (
                    <Input
                      value={e.clave_sat}
                      className="tabular-nums"
                      onChange={(ev) => cambiar(p, { clave_sat: ev.target.value })}
                    />
                  ) : (
                    <span className="tabular-nums">{p.clave_sat ?? "—"}</span>
                  )}
                </td>
                {canWrite ? (
                  <td className="px-2 py-1.5">
                    {tocado(p) ? (
                      <Button onClick={() => { void guardar(p); }} disabled={saving === p.id}>
                        {saving === p.id ? "…" : "Guardar"}
                      </Button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

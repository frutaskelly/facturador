"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";

/** Cómo escriben los clientes un producto, y a dónde manda cada texto. */
export type ProductoAlias = {
  id: string;
  texto: string;
  origen: string;
  cliente_id: string | null;
  cliente_nombre: string | null;
  sucursal_id: string | null;
  sucursal_nombre: string | null;
  ambiguo: boolean;
  tambien_en: string[];
  created_at: string;
};

const WRITE = "producto:gestionar";

const ORIGEN: Record<string, string> = {
  IMPORT: "Importado",
  MANUAL: "Manual",
  IA: "Sugerido por IA",
};

function fecha(iso: string) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("es-MX", { day: "numeric", month: "short" });
}

export function ProductoAliasPanel({ productoId }: { productoId: string }) {
  const { me } = useAuth();
  const toast = useToast();
  const { patch, del, loading: saving } = useMutation();
  const { data, loading, error, reload } = useResource<ProductoAlias[]>(
    `/api/v1/productos/${productoId}/alias`
  );
  // Reapuntar o quitar un texto GLOBAL cambia el cruce de todos los clientes;
  // el de un cliente solo afecta a ese cliente y lo corrige quien captura.
  const puedeGlobal = can(me, WRITE);
  const [reapuntando, setReapuntando] = useState<string | null>(null);
  const [aQuitar, setAQuitar] = useState<ProductoAlias | null>(null);

  async function reapuntar(alias: ProductoAlias, nuevoProductoId: string) {
    try {
      await patch(`/api/v1/productos/alias/${alias.id}`, { producto_id: nuevoProductoId });
      toast.success(`«${alias.texto}» ahora cruza al producto elegido`);
      setReapuntando(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo reapuntar");
    }
  }

  async function quitar() {
    if (!aQuitar) return;
    try {
      await del(`/api/v1/productos/alias/${aQuitar.id}`);
      toast.success(`«${aQuitar.texto}» ya no cruza a este producto`);
      setAQuitar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo quitar");
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }
  if (error) return <p className="py-6 text-sm text-danger">{error}</p>;

  const alias = data ?? [];
  if (alias.length === 0) {
    return (
      <EmptyState
        title="Nadie lo ha escrito distinto"
        hint="Aquí van los textos con los que los clientes piden este producto. Se aprenden solos al cruzar una orden."
      />
    );
  }

  const revisar = alias.filter((a) => a.ambiguo);

  return (
    <div className="space-y-3">
      {revisar.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <b>
            {revisar.length === 1
              ? "1 texto lleva además a otro producto"
              : `${revisar.length} textos llevan además a otro producto`}
          </b>
          . A veces está bien —cada cliente le dice distinto a cosas distintas— y a veces es
          el cruce equivocado. Revísalos abajo.
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-surface-2 text-xs uppercase tracking-wide text-muted">
              <th className="px-3 py-2 text-left font-medium">Texto</th>
              <th className="px-3 py-2 text-left font-medium">Aplica a</th>
              <th className="px-3 py-2 text-left font-medium">Origen</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {alias.map((a) => {
              const editable = a.cliente_id !== null || puedeGlobal;
              return (
                <tr
                  key={a.id}
                  className={`border-t border-surface-2 ${a.ambiguo ? "bg-amber-50/60" : ""}`}
                >
                  <td className="px-3 py-2">
                    <span className="font-medium">{a.texto}</span>
                    {a.ambiguo && (
                      <div className="mt-0.5 text-xs text-muted">
                        También lleva a {a.tambien_en.join(", ")}
                      </div>
                    )}
                    {reapuntando === a.id && (
                      <div className="mt-2 max-w-sm">
                        <ProductoCombobox
                          autoFocus
                          placeholder="¿A qué producto debe ir?"
                          onSelect={(p) => {
                            if (p) reapuntar(a, p.producto_id);
                          }}
                        />
                        <button
                          type="button"
                          className="mt-1 text-xs text-muted hover:text-foreground"
                          onClick={() => setReapuntando(null)}
                        >
                          Cancelar
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {a.cliente_id ? (
                      <Badge tone="warning">
                        {a.cliente_nombre ?? "Cliente"}
                        {a.sucursal_nombre ? ` · ${a.sucursal_nombre}` : ""}
                      </Badge>
                    ) : (
                      <Badge tone="accent">Global</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-xs text-muted">
                    {ORIGEN[a.origen] ?? a.origen} · {fecha(a.created_at)}
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {editable ? (
                      <span className="inline-flex gap-1">
                        <Button
                          variant="secondary"
                          onClick={() => setReapuntando(reapuntando === a.id ? null : a.id)}
                          disabled={saving}
                        >
                          Reapuntar
                        </Button>
                        <Button variant="secondary" onClick={() => setAQuitar(a)} disabled={saving}>
                          Quitar
                        </Button>
                      </span>
                    ) : (
                      <span className="text-xs text-muted">Solo gestión</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted">
        <b>Global</b> aplica a todos los clientes — es ortografía, abreviaturas o la clave del
        SAE, y cambiarlo pide permiso de gestión. <b>Por cliente</b> es su vocabulario: el
        mismo texto puede llevar a otro producto para otro cliente, y eso suele ser correcto.
      </p>

      <ConfirmDialog
        open={aQuitar !== null}
        title="Quitar el texto"
        message={
          aQuitar?.cliente_id
            ? `«${aQuitar?.texto}» dejará de cruzar para ${aQuitar?.cliente_nombre}. Se vuelve a aprender solo la próxima vez que se confirme una orden.`
            : `«${aQuitar?.texto}» dejará de cruzar para TODOS los clientes.`
        }
        onConfirm={quitar}
        onClose={() => setAQuitar(null)}
        loading={saving}
      />
    </div>
  );
}

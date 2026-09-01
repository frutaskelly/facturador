"use client";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { useResource } from "@/lib/hooks";

/** Cómo escriben los clientes un producto. Solo lectura por ahora. */
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
  const { data, loading, error } = useResource<ProductoAlias[]>(
    `/api/v1/productos/${productoId}/alias`
  );

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
            </tr>
          </thead>
          <tbody>
            {alias.map((a) => (
              <tr key={a.id} className={`border-t border-surface-2 ${a.ambiguo ? "bg-amber-50/60" : ""}`}>
                <td className="px-3 py-2">
                  <span className="font-medium">{a.texto}</span>
                  {a.ambiguo && (
                    <div className="mt-0.5 text-xs text-muted">
                      También lleva a {a.tambien_en.join(", ")}
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted">
        <b>Global</b> aplica a todos los clientes — es ortografía, abreviaturas o la clave del
        SAE. <b>Por cliente</b> es su vocabulario: el mismo texto puede llevar a otro producto
        para otro cliente, y eso suele ser correcto.
      </p>
    </div>
  );
}

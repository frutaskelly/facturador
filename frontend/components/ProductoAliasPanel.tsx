"use client";

import { useMemo, useState } from "react";

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

type Grupo = {
  clave: string;
  esGlobal: boolean;
  titulo: string;
  sucursal: string | null;
  alias: ProductoAlias[];
};

/** Un grupo por alcance: primero el global, luego cada cliente. */
function agrupar(alias: ProductoAlias[]): Grupo[] {
  const mapa = new Map<string, Grupo>();
  for (const a of alias) {
    const clave = `${a.cliente_id ?? "global"}|${a.sucursal_id ?? ""}`;
    if (!mapa.has(clave)) {
      mapa.set(clave, {
        clave,
        esGlobal: a.cliente_id === null,
        titulo: a.cliente_id ? a.cliente_nombre ?? "Cliente" : "Todos los clientes",
        sucursal: a.sucursal_nombre,
        alias: [],
      });
    }
    mapa.get(clave)!.alias.push(a);
  }
  return [...mapa.values()].sort((x, y) =>
    x.esGlobal === y.esGlobal ? x.titulo.localeCompare(y.titulo) : x.esGlobal ? -1 : 1
  );
}

export function ProductoAliasPanel({
  productoId,
  productoNombre,
}: {
  productoId: string;
  productoNombre: string;
}) {
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

  const alias = useMemo(() => data ?? [], [data]);
  const grupos = useMemo(() => agrupar(alias), [alias]);
  const revisar = alias.filter((a) => a.ambiguo);

  async function reapuntar(a: ProductoAlias, nuevoProductoId: string) {
    try {
      await patch(`/api/v1/productos/alias/${a.id}`, { producto_id: nuevoProductoId });
      toast.success(`«${a.texto}» ahora cruza al producto elegido`);
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

  if (alias.length === 0) {
    return (
      <EmptyState
        title="Nadie lo ha escrito distinto"
        hint="Aquí van los textos con los que los clientes piden este producto. Se aprenden solos al cruzar una orden."
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Cuando llegue una orden —por WhatsApp, PDF o foto— y una partida diga alguno de
        estos textos, se surtirá{" "}
        <b className="text-foreground">{productoNombre}</b>.
      </p>

      {revisar.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <b>
            {revisar.length === 1
              ? "1 de estos textos también se usa para otro producto"
              : `${revisar.length} de estos textos también se usan para otro producto`}
          </b>
          . A veces está bien —cada cliente le dice distinto a cosas distintas— y a veces es
          el cruce equivocado.
        </div>
      )}

      {grupos.map((g) => (
        <section key={g.clave} className="overflow-hidden rounded-lg border border-border">
          <header className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-2 px-4 py-2">
            {g.esGlobal ? (
              <Badge tone="accent">Todos los clientes</Badge>
            ) : (
              <>
                <span className="text-xs uppercase tracking-wide text-muted">Cliente</span>
                <span className="text-sm font-semibold">{g.titulo}</span>
                {g.sucursal && <Badge tone="warning">{g.sucursal}</Badge>}
              </>
            )}
            <span className="ml-auto text-xs text-muted">
              {g.alias.length === 1 ? "1 forma de escribirlo" : `${g.alias.length} formas de escribirlo`}
            </span>
          </header>

          <ul className="divide-y divide-surface-2">
            {g.alias.map((a) => {
              const editable = a.cliente_id !== null || puedeGlobal;
              return (
                <li
                  key={a.id}
                  className={`px-4 py-3 ${a.ambiguo ? "bg-amber-50/60" : ""}`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium break-words">{a.texto}</p>
                      {a.ambiguo && (
                        <p className="mt-1 text-xs text-amber-700">
                          Para otro alcance este mismo texto lleva a{" "}
                          {a.tambien_en.join(", ")}
                        </p>
                      )}
                      <p className="mt-1 text-xs text-muted">
                        {ORIGEN[a.origen] ?? a.origen} · {fecha(a.created_at)}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      {editable ? (
                        <>
                          <Button
                            variant="secondary"
                            onClick={() => setReapuntando(reapuntando === a.id ? null : a.id)}
                            disabled={saving}
                          >
                            Reapuntar
                          </Button>
                          <Button
                            variant="secondary"
                            onClick={() => setAQuitar(a)}
                            disabled={saving}
                          >
                            Quitar
                          </Button>
                        </>
                      ) : (
                        <span className="self-center text-xs text-muted">Solo gestión</span>
                      )}
                    </div>
                  </div>

                  {reapuntando === a.id && (
                    <div className="mt-3 max-w-md">
                      <ProductoCombobox
                        autoFocus
                        placeholder="¿A qué producto debe ir este texto?"
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
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      <p className="text-xs text-muted">
        <b>Todos los clientes</b> es ortografía, abreviaturas o la clave del SAE, y cambiarlo
        pide permiso de gestión. <b>Por cliente</b> es su vocabulario: el mismo texto puede
        llevar a otro producto para otro cliente, y eso suele ser correcto.
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

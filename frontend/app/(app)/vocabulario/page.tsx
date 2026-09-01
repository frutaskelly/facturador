"use client";

import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { SearchBox } from "@/components/ui/SearchBox";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource, type Page } from "@/lib/hooks";

const WRITE = "producto:gestionar";

type Fila = {
  id: string;
  texto: string;
  producto_id: string;
  producto_sku: string;
  producto_nombre: string;
  cliente_id: string | null;
  cliente_nombre: string | null;
  sucursal_id: string | null;
  sucursal_nombre: string | null;
  origen: string;
  ambiguo: boolean;
};

type Cliente = { id: string; legal_name: string };

const GLOBAL = "__global__";

export default function VocabularioPage() {
  const { me } = useAuth();
  const toast = useToast();
  const { post, patch, del, loading: saving } = useMutation();
  const puedeGlobal = can(me, WRITE);

  const [q, setQ] = useState("");
  const [alcance, setAlcance] = useState("");     // "" = todos
  const [buscado, setBuscado] = useState("");

  // El buscador pega a la API: el vocabulario completo son miles de renglones.
  useEffect(() => {
    const t = setTimeout(() => setBuscado(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=200");
  const clientes = useMemo(() => clientesRes.data?.items ?? [], [clientesRes.data]);

  const filtro =
    alcance === GLOBAL ? "&solo_global=true" : alcance ? `&cliente_id=${alcance}` : "";
  const { data, loading, error, reload } = useResource<Page<Fila>>(
    `/api/v1/productos/vocabulario?limit=300&q=${encodeURIComponent(buscado)}${filtro}`
  );
  const filas = data?.items ?? [];

  // Edición en línea del texto: se guarda al salir del campo o con Enter.
  const [editando, setEditando] = useState<string | null>(null);
  const [borrador, setBorrador] = useState("");
  const [aQuitar, setAQuitar] = useState<Fila | null>(null);

  // Alta: «lo que escriben» = «qué es», para quién.
  const [nuevoTexto, setNuevoTexto] = useState("");
  const [nuevoAlcance, setNuevoAlcance] = useState(GLOBAL);

  function editable(f: Fila) {
    return f.cliente_id !== null || puedeGlobal;
  }

  async function guardarTexto(f: Fila) {
    const texto = borrador.trim();
    setEditando(null);
    if (!texto || texto === f.texto) return;
    try {
      await patch(`/api/v1/productos/alias/${f.id}`, { texto });
      toast.success(`Ahora también se reconoce «${texto}»`);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  async function cambiarProducto(f: Fila, productoId: string) {
    try {
      await patch(`/api/v1/productos/alias/${f.id}`, { producto_id: productoId });
      toast.success(`«${f.texto}» ahora es otro producto`);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cambiar");
    }
  }

  async function agregar(productoId: string) {
    const texto = nuevoTexto.trim();
    if (!texto) {
      toast.error("Escribe primero cómo lo escribe el cliente");
      return;
    }
    try {
      await post("/api/v1/productos/alias", {
        texto,
        producto_id: productoId,
        cliente_id: nuevoAlcance === GLOBAL ? null : nuevoAlcance,
      });
      toast.success(`«${texto}» agregado al vocabulario`);
      setNuevoTexto("");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo agregar");
    }
  }

  async function quitar() {
    if (!aQuitar) return;
    try {
      await del(`/api/v1/productos/alias/${aQuitar.id}`);
      toast.success(`«${aQuitar.texto}» ya no se reconoce`);
      setAQuitar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo quitar");
    }
  }

  return (
    <div>
      <PageHeader
        title="Vocabulario"
        subtitle="Cómo escribe cada cliente los productos. Si una orden dice el texto de la izquierda, se surte el producto de la derecha."
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchBox
          value={q}
          onChange={setQ}
          className="w-72"
          placeholder="Buscar por texto o por producto…"
        />
        <Select value={alcance} onChange={(e) => setAlcance(e.target.value)} className="w-64">
          <option value="">Todos los alcances</option>
          <option value={GLOBAL}>Solo global</option>
          {clientes.map((c) => (
            <option key={c.id} value={c.id}>
              {c.legal_name}
            </option>
          ))}
        </Select>
        {data && (
          <span className="text-sm text-muted">
            {data.total} {data.total === 1 ? "equivalencia" : "equivalencias"}
            {data.total > filas.length && ` · se muestran ${filas.length}`}
          </span>
        )}
      </div>

      {/* Alta: el renglón nuevo vive arriba de la tabla, con la misma forma. */}
      <div className="mb-4 rounded-lg border border-border bg-surface p-3">
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-56">
            <label className="mb-1 block text-xs font-medium text-muted">Para quién</label>
            <Select
              value={nuevoAlcance}
              onChange={(e) => setNuevoAlcance(e.target.value)}
              disabled={!puedeGlobal && nuevoAlcance === GLOBAL}
            >
              {puedeGlobal && <option value={GLOBAL}>Todos los clientes</option>}
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_name}
                </option>
              ))}
            </Select>
          </div>
          <div className="w-72">
            <label className="mb-1 block text-xs font-medium text-muted">
              Si la orden dice…
            </label>
            <Input
              value={nuevoTexto}
              onChange={(e) => setNuevoTexto(e.target.value)}
              placeholder="LIMON REDONDO CON SEMILLA"
            />
          </div>
          <span className="pb-2 text-lg font-medium text-muted">=</span>
          <div className="w-80">
            <label className="mb-1 block text-xs font-medium text-muted">…es este producto</label>
            <ProductoCombobox
              placeholder="Buscar producto…"
              onSelect={(p) => {
                if (p) agregar(p.producto_id);
              }}
            />
          </div>
          <span className="pb-2 text-xs text-muted">
            <Plus size={13} className="inline" /> Elige el producto para guardar
          </span>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : error ? (
        <p className="py-6 text-sm text-danger">{error}</p>
      ) : filas.length === 0 ? (
        <EmptyState
          title="Nada con ese filtro"
          hint="El vocabulario se aprende solo al confirmar órdenes, o se escribe aquí arriba."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-2 text-xs uppercase tracking-wide text-muted">
                <th className="w-64 px-3 py-2 text-left font-medium">Cliente</th>
                <th className="px-3 py-2 text-left font-medium">Producto</th>
                <th className="px-3 py-2 text-left font-medium">Si la orden dice…</th>
                <th className="w-10 px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {filas.map((f) => (
                <tr
                  key={f.id}
                  className={`border-t border-surface-2 ${f.ambiguo ? "bg-amber-50/60" : ""}`}
                >
                  <td className="px-3 py-2 align-top">
                    {f.cliente_id ? (
                      <>
                        <span className="font-medium">{f.cliente_nombre}</span>
                        {f.sucursal_nombre && (
                          <span className="ml-1">
                            <Badge tone="warning">{f.sucursal_nombre}</Badge>
                          </span>
                        )}
                      </>
                    ) : (
                      <Badge tone="accent">Todos los clientes</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <span className="font-medium">{f.producto_nombre}</span>
                    <span className="ml-2 text-xs text-muted">{f.producto_sku}</span>
                    {editable(f) && (
                      <div className="mt-1 max-w-xs">
                        <ProductoCombobox
                          placeholder="Cambiar producto…"
                          onSelect={(p) => {
                            if (p && p.producto_id !== f.producto_id) cambiarProducto(f, p.producto_id);
                          }}
                        />
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 align-top">
                    {editando === f.id ? (
                      <Input
                        autoFocus
                        value={borrador}
                        onChange={(e) => setBorrador(e.target.value)}
                        onBlur={() => guardarTexto(f)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") guardarTexto(f);
                          if (e.key === "Escape") setEditando(null);
                        }}
                      />
                    ) : (
                      <button
                        type="button"
                        className={`text-left ${editable(f) ? "hover:underline" : "cursor-default"}`}
                        onClick={() => {
                          if (!editable(f)) return;
                          setBorrador(f.texto);
                          setEditando(f.id);
                        }}
                      >
                        {f.texto}
                      </button>
                    )}
                    {f.ambiguo && (
                      <p className="mt-1 text-xs text-amber-700">
                        Este mismo texto lleva a otro producto en otro alcance
                      </p>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right align-top">
                    {editable(f) && (
                      <Button variant="secondary" onClick={() => setAQuitar(f)} disabled={saving}>
                        <Trash2 size={14} />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-xs text-muted">
        <b>Todos los clientes</b> aplica a quien no tenga regla propia — ahí caen los clientes
        nuevos, así que cambiarlo pide permiso de gestión. Una regla <b>del cliente</b> siempre
        gana sobre la global.
      </p>

      <ConfirmDialog
        open={aQuitar !== null}
        title="Quitar del vocabulario"
        message={
          aQuitar?.cliente_id
            ? `«${aQuitar?.texto}» dejará de reconocerse para ${aQuitar?.cliente_nombre}.`
            : `«${aQuitar?.texto}» dejará de reconocerse para TODOS los clientes.`
        }
        onConfirm={quitar}
        onClose={() => setAQuitar(null)}
        loading={saving}
      />
    </div>
  );
}

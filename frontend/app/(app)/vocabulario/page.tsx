"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Pencil, Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column, type RowAction } from "@/components/ui/DataTableSmart";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
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
  /** Sólo en las filas globales: a cuántos clientes se les dijo que ese texto
   *  es OTRO producto. No es un error — la cascada lo resuelve — pero un global
   *  contradicho por todos suele estar mal puesto. */
  pisado_por: number;
};

type Cliente = { id: string; legal_name: string };

const GLOBAL = "__global__";
// El vocabulario entero se trae de una vez (hoy ~1.4k renglones) para que la
// tabla busque, ordene y EXPORTE sobre todo, no sobre la página que se ve. El
// tope existe por si un tenant crece de más: la nota lo dice en pantalla.
const PAGINA = 500;
const TOPE = 10000;

export default function VocabularioPage() {
  const { me } = useAuth();
  const toast = useToast();
  const { post, patch, del, loading: saving } = useMutation();
  const puedeGlobal = can(me, WRITE);

  const [filas, setFilas] = useState<Fila[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const acc: Fila[] = [];
      let offset = 0;
      let totalSrv = 0;
      for (;;) {
        const p = await apiFetch<Page<Fila>>(
          `/api/v1/productos/vocabulario?limit=${PAGINA}&offset=${offset}`
        );
        acc.push(...p.items);
        totalSrv = p.total;
        offset += PAGINA;
        if (p.items.length < PAGINA || offset >= Math.min(totalSrv, TOPE)) break;
      }
      setFilas(acc);
      setTotal(totalSrv);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Error al cargar");
      setFilas([]);
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=200");
  const clientes = useMemo(() => clientesRes.data?.items ?? [], [clientesRes.data]);

  // Filtros de la pantalla (el buscador de la tabla afina dentro de esto).
  const [alcance, setAlcance] = useState("");        // "" = todos
  const [soloRevisar, setSoloRevisar] = useState(false);
  // Lo que amerita una mirada: un choque real (dos reglas del mismo alcance) o
  // un global al que sus clientes contradicen.
  const porRevisar = filas.filter((f) => f.ambiguo || f.pisado_por > 0).length;

  const [aQuitar, setAQuitar] = useState<Fila | null>(null);

  // Edición: el renglón se abre en un modal (texto y producto a la vez) en vez
  // de editarse en la celda — así el cambio se confirma con Guardar y no se
  // dispara solo al elegir en un combo.
  const [editar, setEditar] = useState<Fila | null>(null);
  const [edTexto, setEdTexto] = useState("");
  const [edProducto, setEdProducto] = useState<ProductoPick | null>(null);

  // Alta: «lo que escriben» = «qué es», para quién.
  const [alta, setAlta] = useState(false);
  const [nuevoTexto, setNuevoTexto] = useState("");
  const [nuevoAlcance, setNuevoAlcance] = useState(GLOBAL);
  const [nuevoProducto, setNuevoProducto] = useState<ProductoPick | null>(null);

  const editable = useCallback(
    (f: Fila) => f.cliente_id !== null || puedeGlobal,
    [puedeGlobal]
  );

  const abrirEdicion = useCallback((f: Fila) => {
    setEditar(f);
    setEdTexto(f.texto);
    setEdProducto(null);
  }, []);

  async function guardarEdicion() {
    if (!editar) return;
    const texto = edTexto.trim();
    const body: { texto?: string; producto_id?: string } = {};
    if (texto && texto !== editar.texto) body.texto = texto;
    if (edProducto && edProducto.producto_id !== editar.producto_id) {
      body.producto_id = edProducto.producto_id;
    }
    if (Object.keys(body).length === 0) {
      setEditar(null);
      return;
    }
    try {
      await patch(`/api/v1/productos/alias/${editar.id}`, body);
      toast.success(
        body.producto_id
          ? `«${texto || editar.texto}» ahora es otro producto`
          : `Ahora también se reconoce «${texto}»`
      );
      setEditar(null);
      void cargar();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  function abrirAlta() {
    setNuevoTexto("");
    setNuevoProducto(null);
    setNuevoAlcance(puedeGlobal ? GLOBAL : (clientes[0]?.id ?? GLOBAL));
    setAlta(true);
  }

  async function agregar() {
    const texto = nuevoTexto.trim();
    if (!texto) {
      toast.error("Escribe primero cómo lo escribe el cliente");
      return;
    }
    if (!nuevoProducto) {
      toast.error("Elige el producto del catálogo");
      return;
    }
    try {
      await post("/api/v1/productos/alias", {
        texto,
        producto_id: nuevoProducto.producto_id,
        cliente_id: nuevoAlcance === GLOBAL ? null : nuevoAlcance,
      });
      toast.success(`«${texto}» agregado al vocabulario`);
      setAlta(false);
      void cargar();
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
      void cargar();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo quitar");
    }
  }

  const columns = useMemo<Column<Fila>[]>(() => [
    {
      header: "Si la orden dice…",
      key: "texto",
      sortable: true,
      sortValue: (f) => f.texto,
      exportValue: (f) => f.texto,
      cell: (f) => (
        <div>
          <span className="font-medium">{f.texto}</span>
          {f.ambiguo && (
            <p className="mt-0.5 flex items-start gap-1 text-xs text-amber-700">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              Dos reglas del mismo alcance llevan a productos distintos: nadie decide
            </p>
          )}
          {f.pisado_por > 0 && (
            <p className="mt-0.5 text-xs text-muted">
              {f.pisado_por === 1
                ? "1 cliente lo tiene apuntado a otro producto (ese cliente gana)"
                : `${f.pisado_por} clientes lo tienen apuntado a otro producto (ellos ganan)`}
            </p>
          )}
        </div>
      ),
    },
    {
      header: "…es este producto",
      key: "producto",
      sortable: true,
      sortValue: (f) => f.producto_nombre,
      exportValue: (f) => f.producto_nombre,
      truncate: true,
      cell: (f) => <span className="font-medium">{f.producto_nombre}</span>,
    },
    {
      header: "SKU",
      key: "sku",
      sortable: true,
      sortValue: (f) => f.producto_sku,
      exportValue: (f) => f.producto_sku,
      cell: (f) => <span className="text-xs text-muted">{f.producto_sku}</span>,
    },
    {
      header: "Alcance",
      key: "alcance",
      sortable: true,
      sortValue: (f) => f.cliente_nombre ?? "",
      exportValue: (f) => f.cliente_nombre ?? "Todos los clientes",
      cell: (f) =>
        f.cliente_id ? (
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
        ),
    },
    {
      header: "Sucursal",
      key: "sucursal",
      hiddenByDefault: true,
      sortable: true,
      sortValue: (f) => f.sucursal_nombre ?? "",
      exportValue: (f) => f.sucursal_nombre ?? "",
      cell: (f) => <span className="text-muted">{f.sucursal_nombre ?? "—"}</span>,
    },
    {
      header: "Origen",
      key: "origen",
      hiddenByDefault: true,
      sortable: true,
      sortValue: (f) => f.origen,
      exportValue: (f) => f.origen,
      cell: (f) => <Badge tone="muted">{f.origen}</Badge>,
    },
    {
      header: "Por revisar",
      key: "revisar",
      hiddenByDefault: true,
      sortable: true,
      sortValue: (f) => (f.ambiguo ? 2 : f.pisado_por > 0 ? 1 : 0),
      exportValue: (f) =>
        f.ambiguo ? "CONFLICTO" : f.pisado_por > 0 ? `${f.pisado_por} clientes lo pisan` : "",
      cell: (f) =>
        f.ambiguo ? (
          <Badge tone="warning">Conflicto</Badge>
        ) : f.pisado_por > 0 ? (
          <Badge tone="muted">Lo pisan {f.pisado_por}</Badge>
        ) : (
          <span className="text-muted">—</span>
        ),
    },
  ], []);

  const acciones = useMemo<RowAction<Fila>[]>(() => [
    {
      id: "editar",
      label: "Editar equivalencia",
      icon: <Pencil size={15} />,
      onClick: abrirEdicion,
      hidden: (f) => !editable(f),
    },
    {
      id: "borrar",
      label: "Quitar del vocabulario",
      icon: <Trash2 size={15} />,
      tone: "danger",
      onClick: (f) => setAQuitar(f),
      hidden: (f) => !editable(f),
    },
  ], [abrirEdicion, editable]);

  return (
    <div>
      <PageHeader
        title="Vocabulario"
        subtitle="Cómo escribe cada cliente los productos. Si una orden dice el texto de la izquierda, se surte el producto de la derecha."
        actions={
          <Button onClick={abrirAlta}>
            <Plus size={16} /> Agregar equivalencia
          </Button>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select value={alcance} onChange={(e) => setAlcance(e.target.value)} className="w-64">
          <option value="">Todos los alcances</option>
          <option value={GLOBAL}>Solo global</option>
          {clientes.map((c) => (
            <option key={c.id} value={c.id}>
              {c.legal_name}
            </option>
          ))}
        </Select>
        <Button
          variant={soloRevisar ? "primary" : "secondary"}
          onClick={() => setSoloRevisar((v) => !v)}
          disabled={porRevisar === 0}
        >
          <AlertTriangle size={15} /> Solo por revisar ({porRevisar})
        </Button>
        {!cargando && (
          <span className="text-sm text-muted">
            {total} {total === 1 ? "equivalencia" : "equivalencias"}
            {total > filas.length && ` · se cargaron ${filas.length}`}
          </span>
        )}
      </div>

      <DataTableSmart
        columns={columns}
        rows={filas}
        actions={acciones}
        loading={cargando}
        error={error}
        empty="El vocabulario está vacío: se aprende solo al confirmar órdenes, o se agrega aquí."
        rowKey={(f) => f.id}
        storageKey="vocabulario"
        exportFilename="vocabulario"
        searchPlaceholder="Buscar por texto o por producto…"
        rowFilter={(f) =>
          (!soloRevisar || f.ambiguo || f.pisado_por > 0) &&
          (alcance === "" ||
            (alcance === GLOBAL ? f.cliente_id === null : f.cliente_id === alcance))
        }
        rowFilterKey={`${alcance}|${soloRevisar}`}
        rowClassName={(f) => (f.ambiguo ? "bg-amber-50/60" : undefined)}
      />

      <p className="mt-3 text-xs text-muted">
        Manda lo más específico: la regla <b>del cliente en su plaza</b>, luego la <b>del
        cliente</b>, y al final la de <b>todos los clientes</b>. Que un texto lleve a otro
        producto para otro cliente no es un error: por eso sólo se marcan los choques dentro
        de un mismo alcance. La global es la base — aplica a quien no tenga regla propia, ahí
        caen los clientes nuevos, y por eso cambiarla pide permiso de gestión.
      </p>

      {/* Editar: el texto y el producto se confirman juntos con Guardar. */}
      <Modal
        open={editar !== null}
        onClose={() => setEditar(null)}
        title="Editar equivalencia"
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditar(null)} disabled={saving}>
              Cancelar
            </Button>
            <Button onClick={guardarEdicion} disabled={saving}>
              Guardar
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="text-sm text-muted">
            Alcance:{" "}
            {editar?.cliente_id ? (
              <b>
                {editar.cliente_nombre}
                {editar.sucursal_nombre ? ` · ${editar.sucursal_nombre}` : ""}
              </b>
            ) : (
              <b>todos los clientes</b>
            )}
          </div>
          <Field label="Si la orden dice…">
            <Input
              value={edTexto}
              onChange={(e) => setEdTexto(e.target.value)}
              placeholder="LIMON REDONDO CON SEMILLA"
            />
          </Field>
          <Field
            label="…es este producto"
            hint={
              edProducto
                ? `Se cambiará a ${edProducto.nombre}`
                : `Hoy: ${editar?.producto_nombre ?? ""} (${editar?.producto_sku ?? ""})`
            }
          >
            <ProductoCombobox
              placeholder="Buscar otro producto…"
              onSelect={(p) => setEdProducto(p)}
            />
          </Field>
        </div>
      </Modal>

      {/* Alta */}
      <Modal
        open={alta}
        onClose={() => setAlta(false)}
        title="Agregar equivalencia"
        footer={
          <>
            <Button variant="secondary" onClick={() => setAlta(false)} disabled={saving}>
              Cancelar
            </Button>
            <Button onClick={agregar} disabled={saving}>
              Agregar
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Para quién">
            <Select value={nuevoAlcance} onChange={(e) => setNuevoAlcance(e.target.value)}>
              {puedeGlobal && <option value={GLOBAL}>Todos los clientes</option>}
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Si la orden dice…">
            <Input
              value={nuevoTexto}
              onChange={(e) => setNuevoTexto(e.target.value)}
              placeholder="LIMON REDONDO CON SEMILLA"
            />
          </Field>
          <Field
            label="…es este producto"
            hint={nuevoProducto ? nuevoProducto.nombre : "Búscalo en el catálogo"}
          >
            <ProductoCombobox
              placeholder="Buscar producto…"
              onSelect={(p) => setNuevoProducto(p)}
            />
          </Field>
        </div>
      </Modal>

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

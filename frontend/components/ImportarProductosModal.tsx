"use client";

// Importación masiva de productos: plantilla oficial o CUALQUIER lista de
// precios (Excel/CSV/PDF/foto) leída con IA. El preview cruza cada fila contra
// el catálogo (exacto → alias → difuso) para VINCULAR en vez de duplicar; si la
// lista es de un cliente, guarda su código/nombre (van al CFDI como
// NoIdentificacion/Descripcion) y opcionalmente sus precios en su lista.
import { useMemo, useRef, useState } from "react";
import { Download, FileUp, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import { useResource, type Page } from "@/lib/hooks";
import type { Cliente, ImportFilaPreview, ImportPreview, ImportResult } from "@/lib/types";

const ACCEPT = ".xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.webp";

type Accion = "vincular" | "crear" | "omitir";

type Fila = ImportFilaPreview & {
  accion: Accion;
  producto_sel: string;      // producto elegido para vincular
};

export function ImportarProductosModal({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const clientesRes = useResource<Page<Cliente>>(open ? "/api/v1/clientes?limit=1000" : null);
  const clientes = clientesRes.data?.items ?? [];

  const [paso, setPaso] = useState<"subir" | "preview" | "resultado">("subir");
  const [archivo, setArchivo] = useState<File | null>(null);
  const [clienteId, setClienteId] = useState("");
  const [usarIa, setUsarIa] = useState(true);
  const [guardarPrecios, setGuardarPrecios] = useState(false);
  const [cargando, setCargando] = useState(false);
  const [formato, setFormato] = useState<"plantilla" | "ia">("plantilla");
  const [filas, setFilas] = useState<Fila[]>([]);
  const [resultado, setResultado] = useState<ImportResult | null>(null);

  const cliente = clientes.find((c) => c.id === clienteId) ?? null;
  const clienteConLista = Boolean(cliente?.lista_precios_id);

  function reset() {
    setPaso("subir");
    setArchivo(null);
    setClienteId("");
    setUsarIa(true);
    setGuardarPrecios(false);
    setFilas([]);
    setResultado(null);
  }
  function close() {
    reset();
    onClose();
  }

  async function descargarPlantilla() {
    try {
      await apiDownload("/api/v1/productos/plantilla-importacion", "plantilla-productos.xlsx");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descargar la plantilla");
    }
  }

  async function analizar() {
    if (!archivo) {
      toast.error("Elige un archivo primero");
      return;
    }
    setCargando(true);
    try {
      const fd = new FormData();
      fd.append("archivo", archivo);
      fd.append("usar_ia", String(usarIa));
      if (clienteId) fd.append("cliente_id", clienteId);
      const p = await apiFetch<ImportPreview>("/api/v1/productos/importar-preview", {
        method: "POST",
        body: fd,
      });
      setFormato(p.formato);
      setFilas(
        p.filas.map((f) => ({
          ...f,
          accion: f.producto_id ? "vincular" : "crear",
          producto_sel: f.producto_id ?? "",
        }))
      );
      setPaso("preview");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo leer el archivo");
    } finally {
      setCargando(false);
    }
  }

  function setFila(i: number, patch: Partial<Fila>) {
    setFilas((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  const resumen = useMemo(() => {
    const crear = filas.filter((f) => f.accion === "crear").length;
    const vincular = filas.filter((f) => f.accion === "vincular").length;
    const omitir = filas.filter((f) => f.accion === "omitir").length;
    return { crear, vincular, omitir };
  }, [filas]);

  async function importar() {
    const activas = filas.filter((f) => f.accion !== "omitir");
    if (activas.length === 0) {
      toast.error("Todas las filas están omitidas");
      return;
    }
    const sinProducto = filas.find((f) => f.accion === "vincular" && !f.producto_sel);
    if (sinProducto) {
      toast.error(`Fila ${sinProducto.fila}: elige el producto a vincular (o cámbiala a "Crear")`);
      return;
    }
    setCargando(true);
    try {
      const res = await apiFetch<ImportResult>("/api/v1/productos/importar", {
        method: "POST",
        body: JSON.stringify({
          cliente_id: clienteId || null,
          guardar_precios: guardarPrecios && clienteConLista,
          filas: filas.map((f) => ({
            accion: f.accion,
            producto_id: f.accion === "vincular" ? f.producto_sel : null,
            nombre: f.nombre,
            // Sin cliente, el CODIGO capturado es el SKU deseado; con cliente,
            // el SKU interno siempre es automático y su código va como alias.
            sku: !clienteId && f.accion === "crear" && f.codigo ? f.codigo : null,
            descripcion: f.descripcion || null,
            unidad_base: f.unidad || null,
            clave_sat: f.clave_sat || null,
            unidad_sat: f.unidad_sat || null,
            codigo_barras: f.codigo_barras || null,
            codigo_cliente: clienteId ? f.codigo || null : null,
            nombre_cliente: clienteId ? f.nombre || null : null,
            precio: f.precio || null,
          })),
        }),
      });
      setResultado(res);
      setPaso("resultado");
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo importar");
    } finally {
      setCargando(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Importar productos"
      wide
      footer={
        paso === "subir" ? (
          <>
            <Button variant="secondary" onClick={close}>Cancelar</Button>
            <Button onClick={analizar} disabled={!archivo || cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : <FileUp size={16} />}
              {cargando ? "Leyendo archivo…" : "Analizar archivo"}
            </Button>
          </>
        ) : paso === "preview" ? (
          <>
            <Button variant="secondary" onClick={() => setPaso("subir")} disabled={cargando}>
              Regresar
            </Button>
            <Button onClick={importar} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando
                ? "Importando…"
                : `Importar (${resumen.crear} nuevos, ${resumen.vincular} vinculados)`}
            </Button>
          </>
        ) : (
          <Button onClick={close}>Listo</Button>
        )
      }
    >
      {paso === "subir" && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm text-muted">
            Sube la <b>plantilla</b> llena, o cualquier <b>lista de precios</b>{" "}
            (Excel, CSV, PDF o foto) y la IA la convierte por ti. Los productos
            se cruzan contra tu catálogo para <b>no duplicar</b>: si el cliente
            llama &quot;Jitomate Roma&quot; a tu &quot;Jitomate Saladett&quot;, se vincula y se
            guarda su nombre y su código para sus facturas.
          </div>

          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={descargarPlantilla}>
              <Download size={16} /> Descargar plantilla
            </Button>
            <span className="text-xs text-muted">Solo NOMBRE es obligatorio</span>
          </div>

          <Field label="Archivo">
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              onChange={(e) => setArchivo(e.target.files?.[0] ?? null)}
              className="block w-full cursor-pointer rounded-lg border border-border bg-background text-sm file:mr-3 file:cursor-pointer file:rounded-l-lg file:border-0 file:bg-surface-2 file:px-3.5 file:py-2 file:text-sm file:font-medium"
            />
          </Field>

          <Field
            label="¿Es la lista de un cliente? (opcional)"
            hint="Se guardan SU código y SU nombre por producto — salen en sus facturas (NoIdentificacion y Descripción)."
          >
            <Select value={clienteId} onChange={(e) => setClienteId(e.target.value)}>
              <option value="">— No, es mi catálogo —</option>
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_name}
                </option>
              ))}
            </Select>
          </Field>

          {clienteId ? (
            <div className="flex items-center gap-3">
              <Switch
                checked={guardarPrecios && clienteConLista}
                onChange={(v) => setGuardarPrecios(v)}
                disabled={!clienteConLista}
              />
              <span className="text-sm">
                {clienteConLista
                  ? "Guardar los precios del archivo en la lista de precios del cliente"
                  : "El cliente no tiene lista de precios asignada (asígnala en Clientes para guardar precios)"}
              </span>
            </div>
          ) : null}

          <div className="flex items-center gap-3">
            <Switch checked={usarIa} onChange={setUsarIa} />
            <span className="text-sm">
              Leer con IA si el archivo no es la plantilla (Excel libre, PDF o foto)
            </span>
          </div>
        </div>
      )}

      {paso === "preview" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {formato === "ia" ? (
              <Badge tone="accent">
                <Sparkles size={12} className="mr-1" /> Leído con IA
              </Badge>
            ) : (
              <Badge tone="success">Plantilla</Badge>
            )}
            <span className="text-muted">
              {filas.length} filas — {resumen.crear} por crear, {resumen.vincular} vinculadas,{" "}
              {resumen.omitir} omitidas
            </span>
          </div>

          <div className="max-h-[55vh] overflow-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface-2 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-2 py-2">#</th>
                  <th className="px-2 py-2">Producto del archivo</th>
                  <th className="px-2 py-2">Código</th>
                  <th className="px-2 py-2">Precio</th>
                  <th className="px-2 py-2 min-w-[240px]">Acción</th>
                  <th className="px-2 py-2">SAT</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((f, i) => (
                  <tr key={f.fila} className="border-t border-border align-top">
                    <td className="px-2 py-2 text-muted">{f.fila}</td>
                    <td className="px-2 py-2">
                      <div className="font-medium">{f.nombre}</div>
                      {f.descripcion ? <div className="text-xs text-muted">{f.descripcion}</div> : null}
                      {f.unidad ? <div className="text-xs text-muted">{f.unidad}</div> : null}
                      {f.ya_vinculado ? (
                        <Badge tone="accent">Ya vinculado a este cliente</Badge>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 tabular-nums">{f.codigo || "—"}</td>
                    <td className="px-2 py-2 tabular-nums">{f.precio || "—"}</td>
                    <td className="px-2 py-2">
                      <div className="space-y-1.5">
                        <Select
                          value={f.accion}
                          onChange={(e) => setFila(i, { accion: e.target.value as Accion })}
                        >
                          <option value="vincular">Vincular a existente</option>
                          <option value="crear">Crear producto nuevo</option>
                          <option value="omitir">Omitir fila</option>
                        </Select>
                        {f.accion === "vincular" ? (
                          <Select
                            value={f.producto_sel}
                            onChange={(e) => setFila(i, { producto_sel: e.target.value })}
                          >
                            <option value="">— Elige el producto —</option>
                            {f.candidatos.map((c) => (
                              <option key={c.producto_id} value={c.producto_id}>
                                {c.nombre} ({c.sku}) · {c.score}%
                              </option>
                            ))}
                          </Select>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      {f.accion === "crear" ? (
                        <div className="flex gap-1">
                          <Input
                            value={f.clave_sat}
                            onChange={(e) => setFila(i, { clave_sat: e.target.value })}
                            placeholder="01010101"
                            className="w-24"
                          />
                          <Input
                            value={f.unidad_sat}
                            onChange={(e) => setFila(i, { unidad_sat: e.target.value.toUpperCase() })}
                            placeholder="KGM"
                            className="w-16"
                          />
                        </div>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {paso === "resultado" && resultado && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Creados", resultado.creados],
              ["Vinculados", resultado.vinculados],
              ["Códigos de cliente", resultado.alias_guardados],
              ["Precios guardados", resultado.precios_guardados],
            ].map(([label, n]) => (
              <div key={label} className="rounded-lg border border-border p-3 text-center">
                <div className="text-2xl font-semibold tabular-nums">{n}</div>
                <div className="text-xs text-muted">{label}</div>
              </div>
            ))}
          </div>
          {resultado.errores.length > 0 ? (
            <div className="rounded-lg border border-danger/40 bg-danger/5 p-3 text-sm">
              <div className="mb-1 font-medium text-danger">
                {resultado.errores.length} filas con error (el resto sí se importó):
              </div>
              <ul className="list-inside list-disc text-muted">
                {resultado.errores.map((e) => (
                  <li key={e.fila}>
                    Fila {e.fila}: {e.error}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </Modal>
  );
}

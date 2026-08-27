"use client";

// Importación masiva de productos — wizard de 4 pasos:
//   1. Subir (plantilla/SAE determinista, o Excel libre/CSV/PDF/foto con IA)
//   2. Preguntas en LOTE (solo si el archivo las necesita): claves SAT
//      sugeridas-del-catálogo-oficial o genéricas, categorías, esquema, precios
//   3. Preview → Aprobar todo (cruce anti-duplicados, fila por fila editable)
//   4. Resultado + asignar la lista de precios (default / clientes)
// Una subida construye el catálogo, el catálogo del cliente (claves/nombres
// que van al CFDI) y la lista de precios.
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
import type {
  Cliente,
  EsquemaImpuesto,
  ImportFilaPreview,
  ImportPreview,
  ImportResult,
  SugerenciaSat,
} from "@/lib/types";

const ACCEPT = ".xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.webp";

type Accion = "vincular" | "crear" | "omitir";

type Fila = ImportFilaPreview & {
  accion: Accion;
  producto_sel: string;      // producto elegido para vincular
  factor: string;            // variante nueva: 1 UNIDAD = factor × unidad base
};

type Paso = "subir" | "preguntas" | "preview" | "resultado";

function defaultAccion(f: ImportFilaPreview): Accion {
  if (f.duplicada_de || f.baja) return "omitir";
  return f.producto_id ? "vincular" : "crear";
}

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
  const esquemasRes = useResource<Page<EsquemaImpuesto>>(
    open ? "/api/v1/esquemas-impuesto?limit=200" : null
  );
  const esquemas = (esquemasRes.data?.items ?? []).filter((e) => e.activo);

  const [paso, setPaso] = useState<Paso>("subir");
  const [archivo, setArchivo] = useState<File | null>(null);
  const [clienteId, setClienteId] = useState("");
  const [usarIa, setUsarIa] = useState(true);
  const [cargando, setCargando] = useState(false);
  const [formato, setFormato] = useState<"plantilla" | "ia">("plantilla");
  const [meta, setMeta] = useState<ImportPreview | null>(null);
  const [filas, setFilas] = useState<Fila[]>([]);
  const [resultado, setResultado] = useState<ImportResult | null>(null);

  // Preguntas en lote
  const [p1, setP1] = useState<"sugerida" | "generica">("sugerida");
  const [p2, setP2] = useState<"sugerida" | "generica">("sugerida");
  const [crearCategorias, setCrearCategorias] = useState(true);
  const [esquemaDefaultId, setEsquemaDefaultId] = useState("");
  const [guardarPrecios, setGuardarPrecios] = useState(true);
  const [listaNombre, setListaNombre] = useState("");

  // Asignación de la lista (paso resultado)
  const [asignar, setAsignar] = useState<"nada" | "default" | "clientes">("nada");
  const [asignarClientes, setAsignarClientes] = useState<Set<string>>(new Set());
  const [asignando, setAsignando] = useState(false);
  const [asignadoMsg, setAsignadoMsg] = useState("");

  const cliente = clientes.find((c) => c.id === clienteId) ?? null;
  const clienteConLista = Boolean(cliente?.lista_precios_id);

  function reset() {
    setPaso("subir");
    setArchivo(null);
    setClienteId("");
    setUsarIa(true);
    setMeta(null);
    setFilas([]);
    setResultado(null);
    setP1("sugerida");
    setP2("sugerida");
    setCrearCategorias(true);
    setEsquemaDefaultId("");
    setGuardarPrecios(true);
    setListaNombre("");
    setAsignar("nada");
    setAsignarClientes(new Set());
    setAsignadoMsg("");
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
      setMeta(p);
      setFilas(
        p.filas.map((f) => ({
          ...f,
          accion: defaultAccion(f),
          producto_sel: f.producto_id ?? "",
          factor: "1",
        }))
      );
      if (!listaNombre) setListaNombre(archivo.name.replace(/\.[^.]+$/, ""));
      // Con preguntas pendientes → paso 2; si el archivo trae todo → directo al preview.
      const hayPreguntas =
        p.faltan_clave_sat > 0 ||
        p.faltan_unidad_sat > 0 ||
        p.categorias_nuevas.length > 0 ||
        p.filas_sin_esquema > 0 ||
        p.tiene_precios;
      setPaso(hayPreguntas ? "preguntas" : "preview");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo leer el archivo");
    } finally {
      setCargando(false);
    }
  }

  // Aplica las respuestas del lote (P1/P2) y pasa al preview.
  async function aplicarPreguntas() {
    if (!meta) return;
    const faltantes = filas.filter(
      (f) => f.accion !== "omitir" && (!f.clave_sat || !f.unidad_sat)
    );
    setCargando(true);
    try {
      let porNombre: Record<string, SugerenciaSat> = {};
      if ((p1 === "sugerida" || p2 === "sugerida") && faltantes.length > 0) {
        const sugerencias = await apiFetch<SugerenciaSat[]>(
          "/api/v1/productos/sugerir-sat-batch",
          {
            method: "POST",
            body: JSON.stringify({
              productos: faltantes.map((f) => ({ nombre: f.nombre, unidad: f.unidad })),
            }),
          }
        );
        porNombre = Object.fromEntries(sugerencias.map((s) => [s.nombre, s]));
      }
      setFilas((rows) =>
        rows.map((f) => {
          if (f.accion === "omitir") return f;
          const s = porNombre[f.nombre];
          let clave = f.clave_sat;
          let unidad = f.unidad_sat;
          if (!clave) clave = p1 === "sugerida" && s ? s.clave_sat : "01010101";
          if (!unidad) unidad = p2 === "sugerida" && s ? s.unidad_sat : s?.unidad_sat_generica ?? "H87";
          return { ...f, clave_sat: clave, unidad_sat: unidad };
        })
      );
      setPaso("preview");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron sugerir las claves");
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
      const conPrecios = guardarPrecios && Boolean(meta?.tiene_precios);
      const res = await apiFetch<ImportResult>("/api/v1/productos/importar", {
        method: "POST",
        body: JSON.stringify({
          cliente_id: clienteId || null,
          guardar_precios: conPrecios,
          // Cliente con lista → la suya; si no, se crea una con este nombre.
          lista_nombre: conPrecios && !clienteConLista ? listaNombre.trim() || null : null,
          crear_categorias: crearCategorias,
          esquema_default_id: esquemaDefaultId || null,
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
            categoria: f.categoria || null,
            esquema: f.esquema || null,
            activo: !f.baja,
            presentacion_factor:
              f.accion === "vincular" && f.nueva_presentacion ? f.factor || "1" : null,
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

  async function aplicarAsignacion() {
    if (!resultado?.lista_id || asignar === "nada" || asignadoMsg) {
      close();
      return;
    }
    setAsignando(true);
    try {
      const r = await apiFetch<{ default: boolean; clientes_asignados: number }>(
        `/api/v1/listas-precios/${resultado.lista_id}/asignar`,
        {
          method: "POST",
          body: JSON.stringify({
            default: asignar === "default",
            cliente_ids: asignar === "clientes" ? Array.from(asignarClientes) : [],
          }),
        }
      );
      setAsignadoMsg(
        r.default
          ? "Lista marcada como default para todos los clientes"
          : `Lista asignada a ${r.clientes_asignados} cliente(s)`
      );
      toast.success("Lista de precios asignada");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo asignar la lista");
    } finally {
      setAsignando(false);
    }
  }

  const unidadBaseDe = (f: Fila): string => {
    const c = f.candidatos.find((x) => x.producto_id === f.producto_sel);
    return c?.unidad_base ?? "unidad base";
  };

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
        ) : paso === "preguntas" ? (
          <>
            <Button variant="secondary" onClick={() => setPaso("subir")} disabled={cargando}>
              Regresar
            </Button>
            <Button onClick={aplicarPreguntas} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando ? "Aplicando…" : "Continuar al preview"}
            </Button>
          </>
        ) : paso === "preview" ? (
          <>
            <Button
              variant="secondary"
              onClick={() => setPaso(meta && (meta.faltan_clave_sat || meta.faltan_unidad_sat || meta.categorias_nuevas.length || meta.filas_sin_esquema || meta.tiene_precios) ? "preguntas" : "subir")}
              disabled={cargando}
            >
              Regresar
            </Button>
            <Button onClick={importar} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando
                ? "Importando…"
                : `Aprobar todo (${resumen.crear} nuevos, ${resumen.vincular} vinculados)`}
            </Button>
          </>
        ) : (
          <Button onClick={aplicarAsignacion} disabled={asignando}>
            {asignando ? <Spinner className="h-4 w-4" /> : null}
            {resultado?.lista_id && asignar !== "nada" && !asignadoMsg ? "Asignar y terminar" : "Listo"}
          </Button>
        )
      }
    >
      {paso === "subir" && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm text-muted">
            Sube la <b>plantilla</b>, un <b>export de SAE</b> o cualquier{" "}
            <b>lista de precios</b> (Excel, CSV, PDF o foto — la IA la convierte).
            Una sola subida construye el catálogo, los códigos/nombres del
            cliente para su CFDI y la lista de precios.
          </div>

          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={descargarPlantilla}>
              <Download size={16} /> Descargar plantilla
            </Button>
            <span className="text-xs text-muted">Solo DESCRIPCIÓN es obligatoria</span>
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
            hint="Se guardan SU código, SU nombre y SU presentación por producto — salen en sus facturas (NoIdentificacion y Descripción)."
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

          <div className="flex items-center gap-3">
            <Switch checked={usarIa} onChange={setUsarIa} />
            <span className="text-sm">
              Leer con IA si el archivo no es la plantilla (Excel libre, PDF o foto)
            </span>
          </div>
        </div>
      )}

      {paso === "preguntas" && meta && (
        <div className="space-y-4">
          <div className="text-sm text-muted">
            {filas.length} filas leídas. Unas respuestas rápidas para todo el
            lote — en el preview puedes ajustar cualquier fila individual.
          </div>

          {meta.faltan_clave_sat > 0 ? (
            <Field label={`1 · ${meta.faltan_clave_sat} productos sin Clave SAT`}>
              <Select value={p1} onChange={(e) => setP1(e.target.value as "sugerida" | "generica")}>
                <option value="sugerida">Asignar la sugerida (del catálogo SAT oficial)</option>
                <option value="generica">Usar la genérica 01010101</option>
              </Select>
            </Field>
          ) : null}

          {meta.faltan_unidad_sat > 0 ? (
            <Field label={`2 · ${meta.faltan_unidad_sat} productos sin Unidad SAT`}>
              <Select value={p2} onChange={(e) => setP2(e.target.value as "sugerida" | "generica")}>
                <option value="sugerida">Asignar la sugerida según la unidad de venta</option>
                <option value="generica">Usar la genérica H87 (pieza)</option>
              </Select>
            </Field>
          ) : null}

          {meta.categorias_nuevas.length > 0 ? (
            <div className="space-y-1">
              <div className="flex items-center gap-3">
                <Switch checked={crearCategorias} onChange={setCrearCategorias} />
                <span className="text-sm">
                  Crear las {meta.categorias_nuevas.length} categorías nuevas del archivo
                </span>
              </div>
              <div className="text-xs text-muted pl-14">
                {meta.categorias_nuevas.join(" · ")}
              </div>
            </div>
          ) : null}

          {meta.filas_sin_esquema > 0 && esquemas.length > 0 ? (
            <Field
              label={`Esquema de impuesto para las ${meta.filas_sin_esquema} filas que no traen uno`}
              hint="Opcional — sin esquema, el producto se crea sin impuestos asignados."
            >
              <Select value={esquemaDefaultId} onChange={(e) => setEsquemaDefaultId(e.target.value)}>
                <option value="">— Sin esquema —</option>
                {esquemas.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.codigo} — {e.nombre}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}

          {meta.tiene_precios ? (
            <div className="space-y-2 rounded-lg border border-border p-3">
              <div className="flex items-center gap-3">
                <Switch checked={guardarPrecios} onChange={setGuardarPrecios} />
                <span className="text-sm">
                  {clienteConLista
                    ? "Guardar los precios en la lista del cliente"
                    : "Crear una lista de precios con los precios del archivo"}
                </span>
              </div>
              {guardarPrecios && !clienteConLista ? (
                <Field label="Nombre de la lista" hint={clienteId ? "El cliente no tiene lista: se crea esta y se le asigna." : "Al terminar puedes asignarla como default o a clientes."}>
                  <Input value={listaNombre} onChange={(e) => setListaNombre(e.target.value)} />
                </Field>
              ) : null}
            </div>
          ) : null}
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
              <Badge tone="success">Plantilla / SAE</Badge>
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
                      <div className="text-xs text-muted">
                        {[f.unidad, f.categoria].filter(Boolean).join(" · ")}
                      </div>
                      <div className="flex flex-wrap gap-1 pt-0.5">
                        {f.ya_vinculado ? (
                          <Badge tone="accent">Ya vinculado a este cliente</Badge>
                        ) : null}
                        {f.duplicada_de ? (
                          <Badge tone={f.precio_distinto ? "danger" : "warning"}>
                            {f.precio_distinto
                              ? `Repetida con OTRO precio (ver fila ${f.duplicada_de})`
                              : `Repetida (ver fila ${f.duplicada_de})`}
                          </Badge>
                        ) : null}
                        {f.mismo_producto_que ? (
                          <Badge tone="warning">Mismo producto que fila {f.mismo_producto_que}</Badge>
                        ) : null}
                        {f.baja ? <Badge tone="muted">BAJA en el archivo</Badge> : null}
                        {f.clave_sat_valida === false ? (
                          <Badge tone="danger">Clave SAT no existe en el catálogo</Badge>
                        ) : null}
                      </div>
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
                          <>
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
                            {f.nueva_presentacion && f.unidad ? (
                              <div className="flex items-center gap-1.5 text-xs">
                                <span>1 {f.unidad} =</span>
                                <Input
                                  value={f.factor}
                                  onChange={(e) => setFila(i, { factor: e.target.value })}
                                  className="w-16 text-right"
                                />
                                <span>{unidadBaseDe(f)}</span>
                                <Badge tone="accent">Variante nueva</Badge>
                              </div>
                            ) : null}
                          </>
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
        <div className="space-y-4">
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
          {resultado.categorias_creadas || resultado.presentaciones_agregadas ? (
            <div className="text-sm text-muted">
              {resultado.categorias_creadas ? `${resultado.categorias_creadas} categorías creadas. ` : ""}
              {resultado.presentaciones_agregadas
                ? `${resultado.presentaciones_agregadas} presentaciones nuevas agregadas a productos existentes.`
                : ""}
            </div>
          ) : null}
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

          {resultado.lista_id && !asignadoMsg ? (
            <div className="space-y-2 rounded-lg border border-border p-3">
              <div className="text-sm font-medium">
                Lista de precios «{resultado.lista_nombre}» — ¿a quién se la asignamos?
              </div>
              <Select
                value={asignar}
                onChange={(e) => setAsignar(e.target.value as "nada" | "default" | "clientes")}
              >
                <option value="nada">Solo crearla (asignar después)</option>
                <option value="default">Default para TODOS los clientes sin lista propia</option>
                <option value="clientes">Elegir cliente(s)</option>
              </Select>
              {asignar === "clientes" ? (
                <div className="max-h-40 space-y-1 overflow-auto rounded-md border border-border p-2">
                  {clientes.map((c) => (
                    <label key={c.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={asignarClientes.has(c.id)}
                        onChange={(e) => {
                          const next = new Set(asignarClientes);
                          if (e.target.checked) next.add(c.id);
                          else next.delete(c.id);
                          setAsignarClientes(next);
                        }}
                      />
                      {c.legal_name}
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          {asignadoMsg ? (
            <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm">{asignadoMsg}</div>
          ) : null}
        </div>
      )}
    </Modal>
  );
}

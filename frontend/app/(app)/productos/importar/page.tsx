"use client";

// Importar productos — pantalla completa (el preview trae demasiada
// información para un diálogo). Pasos:
//   1. Subir       archivo (plantilla/SAE, Excel libre, CSV, PDF o foto)
//   2. Columnas    qué columna del archivo es qué campo del sistema
//   3. Revisar     decisiones de lote: categorías (match con las existentes),
//                  esquema de impuesto (reglas fiscales + IA), claves SAT, lista
//   4. Preview     tabla editable: unidad, categoría, esquema, SAT, acción
//   5. Resultado   + a quién se asigna la lista de precios
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Download, FileUp, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useResource, type Page } from "@/lib/hooks";
import type {
  Categoria,
  Cliente,
  EsquemaImpuesto,
  ImportColumna,
  ImportFilaPreview,
  ImportPreview,
  ImportResult,
  SugerenciaEsquema,
  SugerenciaSat,
} from "@/lib/types";

const ACCEPT = ".xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.webp";
const POR_PAGINA = 50;   // 508 filas × varios dropdowns no caben de golpe en el DOM

// Unidad de venta → clave de unidad SAT. Espejo de _UNIDAD_A_SAT del backend:
// cambiar la unidad de salida debe cambiar la unidad SAT con la que se timbra.
const UNIDAD_SAT: Record<string, string> = {
  KILO: "KGM", GRAMO: "GRM", LITRO: "LTR", MILILITRO: "MLT", PIEZA: "H87",
  CAJA: "XBX", PAQUETE: "XPK", BOLSA: "XBG", COSTAL: "XSA", BULTO: "XSA",
  DOCENA: "DPC", MANOJO: "H87", MALLA: "XBG", REJA: "XBX", ATADO: "H87", PAR: "PR",
};
const UNIDADES = Object.keys(UNIDAD_SAT);

type Accion = "vincular" | "crear" | "omitir";
type Paso = "subir" | "columnas" | "revisar" | "preview" | "resultado";

type Fila = ImportFilaPreview & {
  accion: Accion;
  producto_sel: string;
  factor: string;
  // Resueltos y editables en el preview (columnas propias):
  cat_id: string;
  esq_id: string;
  esquema_motivo?: string;
};

/** ¿La unidad elegida es una presentación NUEVA del producto vinculado?
 *  Se recalcula en vivo: la marca del backend es una foto contra el producto
 *  sugerido, y el usuario puede cambiar producto o unidad después. */
function esVarianteNueva(f: Fila): boolean {
  if (f.accion !== "vincular" || !f.producto_sel || !f.unidad) return false;
  const cand = f.candidatos.find((c) => c.producto_id === f.producto_sel);
  if (!cand) return false;
  const conocidas = new Set(
    [cand.unidad_base ?? "", ...Object.keys(cand.presentaciones ?? {})]
      .filter(Boolean)
      .map((u) => u.toUpperCase())
  );
  return !conocidas.has(f.unidad.toUpperCase());
}

function defaultAccion(f: ImportFilaPreview): Accion {
  if (f.duplicada_de || f.baja) return "omitir";
  return f.producto_id ? "vincular" : "crear";
}

function aFila(f: ImportFilaPreview): Fila {
  return {
    ...f,
    accion: defaultAccion(f),
    producto_sel: f.producto_id ?? "",
    factor: "1",
    cat_id: f.categoria_id ?? "",
    esq_id: f.esquema_id ?? "",
  };
}

export default function ImportarProductosPage() {
  const router = useRouter();
  const toast = useToast();
  const { me } = useAuth();
  const puedeEscribir = can(me, "producto:gestionar");

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=1000");
  const clientes = clientesRes.data?.items ?? [];
  const esquemasRes = useResource<Page<EsquemaImpuesto>>("/api/v1/esquemas-impuesto?limit=200");
  const esquemas = (esquemasRes.data?.items ?? []).filter((e) => e.activo);
  const categoriasRes = useResource<Page<Categoria>>("/api/v1/categorias?limit=500");
  const [catsExtra, setCatsExtra] = useState<Categoria[]>([]);   // creadas aquí mismo
  const categorias = [...(categoriasRes.data?.items ?? []), ...catsExtra];

  const [paso, setPaso] = useState<Paso>("subir");
  const [archivo, setArchivo] = useState<File | null>(null);
  const [clienteIds, setClienteIds] = useState<string[]>([]);
  const [filtroCliente, setFiltroCliente] = useState("");
  const [usarIa, setUsarIa] = useState(true);
  const [cargando, setCargando] = useState(false);
  const [meta, setMeta] = useState<ImportPreview | null>(null);
  const [columnas, setColumnas] = useState<ImportColumna[]>([]);
  const [campos, setCampos] = useState<{ valor: string; etiqueta: string }[]>([]);
  const [filas, setFilas] = useState<Fila[]>([]);
  const [resultado, setResultado] = useState<ImportResult | null>(null);
  const [pagina, setPagina] = useState(0);

  // Paso 3 — decisiones de lote
  const [p1, setP1] = useState<"sugerida" | "generica">("sugerida");
  const [p2, setP2] = useState<"sugerida" | "generica">("sugerida");
  // Por cada categoría del archivo: "" = crear nueva, o el id de una existente.
  const [catDestino, setCatDestino] = useState<Record<string, string>>({});
  const [guardarPrecios, setGuardarPrecios] = useState(true);
  const [listaNombre, setListaNombre] = useState("");
  const [sugiriendoEsq, setSugiriendoEsq] = useState(false);

  // Paso 5 — asignación de la lista
  const [asignar, setAsignar] = useState<"nada" | "default" | "clientes">("nada");
  const [asignarClientes, setAsignarClientes] = useState<Set<string>>(new Set());
  const [asignando, setAsignando] = useState(false);
  const [asignadoMsg, setAsignadoMsg] = useState("");

  const clientesSel = clientes.filter((c) => clienteIds.includes(c.id));
  const clienteConLista = clientesSel.length === 1 && Boolean(clientesSel[0].lista_precios_id);
  const hayClientes = clienteIds.length > 0;

  const resumen = useMemo(() => {
    const crear = filas.filter((f) => f.accion === "crear").length;
    const vincular = filas.filter((f) => f.accion === "vincular").length;
    const omitir = filas.filter((f) => f.accion === "omitir").length;
    // Solo las que se CREAN reciben esquema: al vincular se conserva el del
    // producto que ya existe (el backend no lo toca).
    const sinEsquema = filas.filter((f) => f.accion === "crear" && !f.esq_id).length;
    const conPrecio = filas.filter((f) => f.accion !== "omitir" && f.precio).length;
    const variantes = filas.filter(esVarianteNueva).length;
    return { crear, vincular, omitir, sinEsquema, conPrecio, variantes };
  }, [filas]);

  // Media hora de decisiones no se pierden por un F5 o un clic al menú.
  const hayTrabajo = filas.length > 0 && paso !== "resultado";
  useEffect(() => {
    if (!hayTrabajo) return;
    const avisar = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", avisar);
    return () => window.removeEventListener("beforeunload", avisar);
  }, [hayTrabajo]);

  async function descargarPlantilla() {
    try {
      await apiDownload("/api/v1/productos/plantilla-importacion", "plantilla-productos.xlsx");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descargar la plantilla");
    }
  }

  const analizar = useCallback(
    async (mapeo?: Record<number, string>) => {
      if (!archivo) {
        toast.error("Elige un archivo primero");
        return;
      }
      setCargando(true);
      try {
        const fd = new FormData();
        fd.append("archivo", archivo);
        fd.append("usar_ia", String(usarIa));
        clienteIds.forEach((id) => fd.append("cliente_ids", id));
        if (mapeo) fd.append("mapeo", JSON.stringify(mapeo));
        const p = await apiFetch<ImportPreview>("/api/v1/productos/importar-preview", {
          method: "POST",
          body: fd,
        });
        setMeta(p);
        setColumnas(p.columnas ?? []);
        setCampos(p.campos_mapeables ?? []);
        setFilas(p.filas.map(aFila));
        setPagina(0);
        // Cada categoría del archivo arranca apuntando a la que le corresponde.
        setCatDestino(
          Object.fromEntries(
            (p.categorias_match ?? []).map((m) => [m.nombre_archivo, m.categoria_id ?? ""])
          )
        );
        if (!listaNombre) {
          setListaNombre(
            archivo.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim()
          );
        }
        if (p.requiere_mapeo || (!mapeo && (p.columnas ?? []).length > 0)) {
          setPaso("columnas");
        } else {
          setPaso("revisar");
        }
      } catch (e) {
        toast.error(
          e instanceof ApiError ? e.message : "No se pudo leer el archivo. Intenta de nuevo."
        );
      } finally {
        setCargando(false);
      }
    },
    [archivo, usarIa, clienteIds, listaNombre, toast]
  );

  async function confirmarColumnas() {
    if (!meta) return;
    if (!columnas.some((c) => c.campo === "nombre")) {
      toast.error("Indica qué columna trae la descripción (nombre) del producto");
      return;
    }
    const cambio = columnas.some((c, i) => c.campo !== (meta.columnas[i]?.campo ?? ""));
    if (!cambio && !meta.requiere_mapeo) {
      setPaso("revisar");
      return;
    }
    if (
      filas.length > 0 &&
      !window.confirm("Releer el archivo descarta los ajustes que hiciste fila por fila. ¿Continuar?")
    ) {
      return;
    }
    const mapeo: Record<number, string> = {};
    columnas.forEach((c) => {
      if (c.campo) mapeo[c.indice] = c.campo;
    });
    await analizar(mapeo);
  }

  /** Claves y unidades SAT faltantes, según lo elegido en el paso 3. */
  async function aplicarSat() {
    const faltantes = filas.filter(
      (f) => f.accion !== "omitir" && (!f.clave_sat || !f.unidad_sat)
    );
    if (faltantes.length === 0) return;
    let porNombre: Record<string, SugerenciaSat> = {};
    if (p1 === "sugerida" || p2 === "sugerida") {
      const sugerencias = await apiFetch<SugerenciaSat[]>("/api/v1/productos/sugerir-sat-batch", {
        method: "POST",
        body: JSON.stringify({
          productos: faltantes.map((f) => ({ nombre: f.nombre, unidad: f.unidad })),
        }),
      });
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
  }

  /** Esquema de impuesto para las filas que no tienen: reglas fiscales + IA. */
  async function sugerirEsquemas() {
    const faltantes = filas.filter((f) => f.accion !== "omitir" && !f.esq_id);
    if (faltantes.length === 0) {
      toast.info("Todas las filas ya tienen esquema");
      return;
    }
    setSugiriendoEsq(true);
    try {
      const sug = await apiFetch<SugerenciaEsquema[]>("/api/v1/productos/sugerir-esquema-batch", {
        method: "POST",
        body: JSON.stringify({
          usar_ia: usarIa,
          productos: faltantes.map((f) => ({
            nombre: f.nombre,
            clave_sat: f.clave_sat,
            categoria: f.categoria,
          })),
        }),
      });
      const porNombre = Object.fromEntries(sug.map((s) => [s.nombre, s]));
      const asignados = faltantes.filter((f) => porNombre[f.nombre]?.esquema_id).length;
      setFilas((rows) =>
        rows.map((f) => {
          if (f.accion === "omitir" || f.esq_id) return f;
          const s = porNombre[f.nombre];
          if (!s?.esquema_id) {
            return s?.motivo ? { ...f, esquema_motivo: s.motivo, esquema_origen: s.origen } : f;
          }
          return { ...f, esq_id: s.esquema_id, esquema_origen: s.origen };
        })
      );
      if (asignados < faltantes.length) {
        toast.info(
          `${asignados} de ${faltantes.length} asignados. Los demás necesitan que los revises.`
        );
      } else {
        toast.success(`${asignados} productos con esquema asignado`);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron sugerir los esquemas");
    } finally {
      setSugiriendoEsq(false);
    }
  }

  async function irAlPreview() {
    setCargando(true);
    try {
      await aplicarSat();
      // Sin esquema no se puede facturar: se asigna solo antes de enseñar la
      // tabla, en vez de dejarlo a que el usuario recuerde apretar un botón.
      if (resumen.sinEsquema > 0 && esquemas.length > 0) {
        await sugerirEsquemas();
      }
      setPaso("preview");
      setPagina(0);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron aplicar las sugerencias");
    } finally {
      setCargando(false);
    }
  }

  function setFila(fila: number, patch: Partial<Fila>) {
    setFilas((rows) => rows.map((r) => (r.fila === fila ? { ...r, ...patch } : r)));
  }

  /** Crea una categoría sin salir de la pantalla y la aplica a la fila. */
  async function crearCategoria(nombre: string, aplicarA?: number) {
    const limpio = nombre.trim();
    if (!limpio) return;
    const norm = (t: string) =>
      t.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const yaExiste = categorias.find((c) => norm(c.nombre) === norm(limpio));
    if (yaExiste) {
      if (aplicarA !== undefined) setFila(aplicarA, { cat_id: yaExiste.id });
      toast.info(`Ya existe «${yaExiste.nombre}»: se usó esa`);
      return yaExiste;
    }
    try {
      const cat = await apiFetch<Categoria>("/api/v1/categorias", {
        method: "POST",
        body: JSON.stringify({ nombre: limpio }),
      });
      setCatsExtra((c) => [...c, cat]);
      if (aplicarA !== undefined) setFila(aplicarA, { cat_id: cat.id });
      toast.success(`Categoría «${cat.nombre}» creada`);
      return cat;
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la categoría");
    }
  }

  async function importar() {
    const activas = filas.filter((f) => f.accion !== "omitir");
    if (activas.length === 0) {
      toast.error("Todas las filas están omitidas");
      return;
    }
    const idx = filas.findIndex((f) => f.accion === "vincular" && !f.producto_sel);
    if (idx >= 0) {
      setPagina(Math.floor(idx / POR_PAGINA));   // llevarlo a la fila, no solo avisar
      toast.error(`Fila ${filas[idx].fila}: elige el producto a vincular`);
      return;
    }
    setCargando(true);
    try {
      const conPrecios = guardarPrecios && Boolean(meta?.tiene_precios);
      const res = await apiFetch<ImportResult>("/api/v1/productos/importar", {
        method: "POST",
        body: JSON.stringify({
          cliente_ids: clienteIds,
          guardar_precios: conPrecios,
          lista_nombre: conPrecios && !clienteConLista ? listaNombre.trim() || null : null,
          // Las categorías nuevas se crean con el nombre del archivo cuando el
          // usuario dejó "crear nueva" para esa categoría.
          crear_categorias: true,
          filas: filas.map((f) => ({
            accion: f.accion,
            producto_id: f.accion === "vincular" ? f.producto_sel : null,
            nombre: f.nombre,
            sku: !hayClientes && f.accion === "crear" && f.codigo ? f.codigo : null,
            descripcion: f.descripcion || null,
            unidad_base: f.unidad || null,
            clave_sat: f.clave_sat || null,
            unidad_sat: f.unidad_sat || null,
            codigo_barras: f.codigo_barras || null,
            categoria_id: f.cat_id || null,
            categoria: f.cat_id ? null : f.categoria || null,
            esquema_impuesto_id: f.esq_id || null,
            activo: !f.baja,
            presentacion_factor: esVarianteNueva(f) ? f.factor || "1" : null,
            codigo_cliente: hayClientes ? f.codigo || null : null,
            nombre_cliente: hayClientes ? f.nombre || null : null,
            precio: f.precio || null,
          })),
        }),
      });
      setResultado(res);
      setPaso("resultado");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo importar");
    } finally {
      setCargando(false);
    }
  }

  async function aplicarAsignacion() {
    if (!resultado?.lista_id || asignar === "nada" || asignadoMsg) {
      router.push("/productos");
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

  const visibles = filas.slice(pagina * POR_PAGINA, (pagina + 1) * POR_PAGINA);
  const paginas = Math.ceil(filas.length / POR_PAGINA);

  if (!puedeEscribir) {
    return <div className="text-sm text-muted">No tienes permiso para importar productos.</div>;
  }

  const PASOS: { id: Paso; label: string }[] = [
    { id: "subir", label: "Archivo" },
    { id: "columnas", label: "Columnas" },
    { id: "revisar", label: "Revisar" },
    { id: "preview", label: "Productos" },
    { id: "resultado", label: "Listo" },
  ];

  return (
    <div className="pb-24">
      <PageHeader
        title="Importar productos"
        subtitle="Una subida construye el catálogo, los códigos del cliente para su CFDI y la lista de precios"
        actions={
          <Link
            href="/productos"
            onClick={(e) => {
              if (
                hayTrabajo &&
                !window.confirm("Se perderán las decisiones de esta importación. ¿Salir?")
              ) {
                e.preventDefault();
              }
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-surface-2"
          >
            <ArrowLeft size={16} /> Productos
          </Link>
        }
      />

      {/* Pasos */}
      <ol className="mb-5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        {PASOS.map((p, i) => {
          const actual = p.id === paso;
          const hecho = PASOS.findIndex((x) => x.id === paso) > i;
          return (
            <li key={p.id} className="flex items-center gap-2">
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${
                  actual
                    ? "bg-accent text-white"
                    : hecho
                      ? "bg-success/15 text-success"
                      : "bg-surface-2 text-muted"
                }`}
              >
                {i + 1}
              </span>
              <span className={actual ? "font-medium" : "text-muted"}>{p.label}</span>
              {i < PASOS.length - 1 ? <span className="text-muted">›</span> : null}
            </li>
          );
        })}
      </ol>

      {/* ── 1. Subir ───────────────────────────────────────────────────── */}
      {paso === "subir" && (
        <div className="max-w-2xl space-y-4">
          <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm text-muted">
            Sube la <b>plantilla</b>, un <b>export de SAE</b> o cualquier{" "}
            <b>lista de precios</b> (Excel, CSV, PDF o foto — la IA la convierte).
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={descargarPlantilla}>
              <Download size={16} /> Descargar plantilla
            </Button>
            <span className="text-xs text-muted">Solo la descripción es obligatoria</span>
          </div>
          <Field label="Archivo">
            <input
              type="file"
              accept={ACCEPT}
              onChange={(e) => setArchivo(e.target.files?.[0] ?? null)}
              className="block w-full cursor-pointer rounded-lg border border-border bg-background text-sm file:mr-3 file:cursor-pointer file:rounded-l-lg file:border-0 file:bg-surface-2 file:px-3.5 file:py-2 file:text-sm file:font-medium"
            />
          </Field>
          <Field
            label={
              clienteIds.length > 0
                ? `¿De qué cliente(s) es la lista? — ${clienteIds.length} seleccionado(s)`
                : "¿Es la lista de uno o varios clientes? (opcional)"
            }
            hint="Se guardan SU código, SU nombre y SU presentación por producto — salen en las facturas de cada cliente. Sin selección = es tu catálogo."
          >
            <div className="rounded-lg border border-border">
              <div className="border-b border-border p-2">
                <Input
                  value={filtroCliente}
                  onChange={(e) => setFiltroCliente(e.target.value)}
                  placeholder="Filtrar clientes…"
                />
              </div>
              <div className="max-h-44 space-y-0.5 overflow-auto p-2">
                {clientes
                  .filter((c) =>
                    c.legal_name.toLowerCase().includes(filtroCliente.trim().toLowerCase())
                  )
                  .map((c) => (
                    <label
                      key={c.id}
                      className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-surface-2"
                    >
                      <input
                        type="checkbox"
                        checked={clienteIds.includes(c.id)}
                        onChange={(e) =>
                          setClienteIds((ids) =>
                            e.target.checked ? [...ids, c.id] : ids.filter((x) => x !== c.id)
                          )
                        }
                      />
                      <span className="truncate">{c.legal_name}</span>
                    </label>
                  ))}
              </div>
            </div>
          </Field>
          <div className="flex items-center gap-3">
            <Switch checked={usarIa} onChange={setUsarIa} />
            <span className="text-sm">
              Usar IA cuando haga falta (leer PDF/foto, sugerir claves SAT y esquemas)
            </span>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => void analizar()} disabled={!archivo || cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : <FileUp size={16} />}
              {cargando ? "Leyendo archivo…" : "Analizar archivo"}
            </Button>
          </div>
        </div>
      )}

      {/* ── 2. Columnas ────────────────────────────────────────────────── */}
      {paso === "columnas" && (
        <div className="max-w-4xl space-y-3">
          {meta?.requiere_mapeo ? (
            <div className="rounded-lg border border-accent/40 bg-accent/5 p-3 text-sm">
              No reconocí los encabezados de este archivo. Indica al menos qué columna
              trae la <b>descripción (nombre)</b> del producto.
            </div>
          ) : (
            <div className="text-sm text-muted">
              Así se está leyendo tu archivo. Cambia el campo de cualquier columna o
              ponla en <b>No importar</b>.
            </div>
          )}
          {meta && meta.filas_sin_nombre > 0 ? (
            <div className="text-sm text-muted">
              {meta.filas_sin_nombre} renglones se descartaron por no traer descripción.
            </div>
          ) : null}
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-3 py-2">Columna del archivo</th>
                  <th className="px-3 py-2">Ejemplos</th>
                  <th className="w-[300px] px-3 py-2">Se importa como</th>
                </tr>
              </thead>
              <tbody>
                {columnas.map((c, i) => {
                  const usados = new Set(
                    columnas.filter((_, j) => j !== i).map((x) => x.campo).filter(Boolean)
                  );
                  return (
                    <tr
                      key={c.indice}
                      className={`border-t border-border ${c.campo ? "" : "opacity-60"}`}
                    >
                      <td className="px-3 py-2 font-medium">{c.encabezado}</td>
                      <td className="px-3 py-2 text-xs text-muted">
                        {c.muestras.length ? c.muestras.join(" · ") : "—"}
                      </td>
                      <td className="px-3 py-2">
                        <Select
                          value={c.campo}
                          aria-label={`Campo para la columna ${c.encabezado}`}
                          onChange={(e) =>
                            setColumnas((cols) =>
                              cols.map((x, j) => (j === i ? { ...x, campo: e.target.value } : x))
                            )
                          }
                        >
                          <option value="">— No importar —</option>
                          {campos
                            .filter((k) => k.valor === c.campo || !usados.has(k.valor))
                            .map((k) => (
                              <option key={k.valor} value={k.valor}>
                                {k.etiqueta}
                              </option>
                            ))}
                        </Select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setPaso("subir")} disabled={cargando}>
              Regresar
            </Button>
            <Button onClick={confirmarColumnas} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando ? "Releyendo…" : "Continuar"}
            </Button>
          </div>
        </div>
      )}

      {/* ── 3. Revisar (decisiones de lote) ────────────────────────────── */}
      {paso === "revisar" && meta && (
        <div className="max-w-3xl space-y-5">
          <div className="text-sm text-muted">
            {filas.length} productos leídos. Estas decisiones aplican a todo el lote; en
            el siguiente paso puedes ajustar cualquier fila.
          </div>

          {/* Categorías: match contra las que ya existen */}
          {(meta.categorias_match ?? []).length > 0 ? (
            <section className="space-y-2">
              <h2 className="text-sm font-medium">Categorías del archivo</h2>
              <div className="overflow-hidden rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs uppercase text-muted">
                    <tr>
                      <th className="px-3 py-2">En el archivo</th>
                      <th className="w-[320px] px-3 py-2">En tu catálogo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {meta.categorias_match.map((m) => (
                      <tr key={m.nombre_archivo} className="border-t border-border">
                        <td className="px-3 py-2 font-medium">{m.nombre_archivo}</td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <Select
                              value={catDestino[m.nombre_archivo] ?? ""}
                              aria-label={`Categoría del sistema para ${m.nombre_archivo}`}
                              onChange={(e) => {
                                const v = e.target.value;
                                setCatDestino((d) => ({ ...d, [m.nombre_archivo]: v }));
                                // Se aplica ya: si se hiciera al entrar al
                                // preview, pisaría las correcciones fila a fila.
                                setFilas((rows) =>
                                  rows.map((f) =>
                                    f.categoria === m.nombre_archivo ? { ...f, cat_id: v } : f
                                  )
                                );
                              }}
                            >
                              <option value="">
                                + Crear «{m.nombre_archivo}»
                              </option>
                              {categorias.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.nombre}
                                </option>
                              ))}
                            </Select>
                            {!m.es_nueva && catDestino[m.nombre_archivo] === m.categoria_id ? (
                              <Badge tone="success">Ya existe</Badge>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          {/* Esquema de impuesto — obligatorio para poder facturar */}
          <section className="space-y-2">
            <h2 className="text-sm font-medium">Esquema de impuesto</h2>
            {esquemasRes.loading ? (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Spinner className="h-4 w-4" /> Cargando esquemas…
              </div>
            ) : esquemas.length === 0 ? (
              <div className="rounded-lg border border-danger/40 bg-danger/5 p-3 text-sm">
                No tienes esquemas de impuesto dados de alta. Sin esquema, las facturas
                de estos productos saldrían sin IVA.{" "}
                <Link href="/esquemas-impuesto" className="text-accent hover:underline">
                  Crear esquemas
                </Link>
              </div>
            ) : (
              <div className="space-y-2 rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span>
                    {resumen.sinEsquema === 0 ? (
                      <>Todos los productos tienen esquema asignado.</>
                    ) : (
                      <>
                        <b>{resumen.sinEsquema}</b> de {filas.length} productos no traen
                        esquema en el archivo.
                      </>
                    )}
                  </span>
                  <Button
                    variant="secondary"
                    onClick={sugerirEsquemas}
                    disabled={sugiriendoEsq || resumen.sinEsquema === 0}
                  >
                    {sugiriendoEsq ? <Spinner className="h-4 w-4" /> : <Sparkles size={16} />}
                    {sugiriendoEsq ? "Analizando…" : "Asignar automáticamente"}
                  </Button>
                </div>
                <p className="text-xs text-muted">
                  Se asigna por producto según las reglas del SAT (alimentos IVA 0%,
                  limpieza y plásticos 16%, refrescos y botanas con IEPS){usarIa ? " y con IA lo que no resuelvan las reglas" : ""}.
                  Sin esquema no se puede facturar correctamente.
                </p>
              </div>
            )}
          </section>

          {/* Claves SAT faltantes */}
          {meta.faltan_clave_sat > 0 || meta.faltan_unidad_sat > 0 ? (
            <section className="space-y-2">
              <h2 className="text-sm font-medium">Claves SAT faltantes</h2>
              {meta.faltan_clave_sat > 0 ? (
                <Field label={`${meta.faltan_clave_sat} productos sin clave SAT`}>
                  <Select value={p1} onChange={(e) => setP1(e.target.value as "sugerida" | "generica")}>
                    <option value="sugerida">Asignar la sugerida (del catálogo SAT oficial)</option>
                    <option value="generica">Usar la genérica 01010101</option>
                  </Select>
                </Field>
              ) : null}
              {meta.faltan_unidad_sat > 0 ? (
                <Field label={`${meta.faltan_unidad_sat} productos sin unidad SAT`}>
                  <Select value={p2} onChange={(e) => setP2(e.target.value as "sugerida" | "generica")}>
                    <option value="sugerida">Asignar la sugerida según la unidad de venta</option>
                    <option value="generica">Usar la genérica H87 (pieza)</option>
                  </Select>
                </Field>
              ) : null}
            </section>
          ) : null}

          {/* Lista de precios */}
          {meta.tiene_precios ? (
            <section className="space-y-2">
              <h2 className="text-sm font-medium">Lista de precios</h2>
              <div className="space-y-2 rounded-lg border border-border p-3">
                <div className="flex items-center gap-3">
                  <Switch checked={guardarPrecios} onChange={setGuardarPrecios} />
                  <span className="text-sm">
                    {clienteConLista
                      ? "Guardar los precios en la lista del cliente"
                      : `Crear una lista con los ${resumen.conPrecio} precios del archivo`}
                  </span>
                </div>
                {guardarPrecios && !clienteConLista ? (
                  <Field label="Nombre de la lista">
                    <Input value={listaNombre} onChange={(e) => setListaNombre(e.target.value)} />
                  </Field>
                ) : null}
              </div>
            </section>
          ) : null}

          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => setPaso(columnas.length > 0 ? "columnas" : "subir")}
              disabled={cargando}
            >
              Regresar
            </Button>
            <Button onClick={irAlPreview} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando ? "Preparando…" : "Ver los productos"}
            </Button>
          </div>
        </div>
      )}

      {/* ── 4. Preview a pantalla completa ─────────────────────────────── */}
      {paso === "preview" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Badge tone="accent">{resumen.crear} nuevos</Badge>
            <Badge tone="success">{resumen.vincular} vinculados</Badge>
            {resumen.variantes > 0 ? (
              <Badge tone="accent">{resumen.variantes} presentaciones nuevas</Badge>
            ) : null}
            {resumen.omitir > 0 ? <Badge tone="muted">{resumen.omitir} omitidos</Badge> : null}
            {resumen.sinEsquema > 0 ? (
              <Badge tone="danger">{resumen.sinEsquema} sin esquema de impuesto</Badge>
            ) : null}
            {resumen.conPrecio > 0 ? (
              <span className="text-muted">{resumen.conPrecio} con precio</span>
            ) : null}
          </div>

          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs uppercase text-muted">
                <tr>
                  <th className="px-2 py-2">#</th>
                  <th className="min-w-[15rem] px-2 py-2">Producto del archivo</th>
                  <th className="min-w-[13rem] px-2 py-2">Acción</th>
                  <th className="min-w-[9rem] px-2 py-2">Unidad de salida</th>
                  <th className="min-w-[11rem] px-2 py-2">Categoría</th>
                  <th className="min-w-[11rem] px-2 py-2">Esquema de impuesto</th>
                  <th className="min-w-[12rem] px-2 py-2">Clave / unidad SAT</th>
                  <th className="px-2 py-2 text-right">Precio</th>
                </tr>
              </thead>
              <tbody>
                {visibles.map((f) => (
                  <tr key={f.fila} className="border-t border-border align-top">
                    <td className="px-2 py-2 text-muted">{f.fila}</td>
                    <td className="px-2 py-2">
                      <div className="font-medium">{f.nombre}</div>
                      {f.codigo ? (
                        <div className="text-xs text-muted tabular-nums">{f.codigo}</div>
                      ) : null}
                      <div className="flex flex-wrap gap-1 pt-0.5">
                        {f.ya_vinculado ? <Badge tone="accent">Ya vinculado</Badge> : null}
                        {f.duplicada_de ? (
                          <Badge tone={f.precio_distinto ? "danger" : "warning"}>
                            {f.precio_distinto
                              ? `Repetida con otro precio (fila ${f.duplicada_de})`
                              : `Repetida (fila ${f.duplicada_de})`}
                          </Badge>
                        ) : null}
                        {f.mismo_producto_que ? (
                          <Badge tone="warning">Mismo producto que fila {f.mismo_producto_que}</Badge>
                        ) : null}
                        {f.baja ? <Badge tone="muted">BAJA</Badge> : null}
                        {f.clave_sat_valida === false ? (
                          <Badge tone="danger">Clave SAT inexistente</Badge>
                        ) : null}
                      </div>
                    </td>

                    {/* Acción */}
                    <td className="px-2 py-2">
                      <div className="space-y-1.5">
                        <Select
                          value={f.accion}
                          aria-label={`Acción para ${f.nombre}`}
                          onChange={(e) => setFila(f.fila, { accion: e.target.value as Accion })}
                        >
                          <option value="vincular">Vincular a existente</option>
                          <option value="crear">Crear producto nuevo</option>
                          <option value="omitir">Omitir</option>
                        </Select>
                        {f.accion === "vincular" ? (
                          <Select
                            value={f.producto_sel}
                            aria-label={`Producto a vincular para ${f.nombre}`}
                            onChange={(e) => setFila(f.fila, { producto_sel: e.target.value })}
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

                    {/* Unidad de salida — un producto puede tener varias */}
                    <td className="px-2 py-2">
                      <Select
                        value={f.unidad || "KILO"}
                        aria-label={`Unidad de salida de ${f.nombre}`}
                        onChange={(e) => {
                          const u = e.target.value;
                          setFila(f.fila, {
                            unidad: u,
                            // La unidad SAT sigue a la de salida (manojo → H87,
                            // kilo → KGM): es con la que se timbra la línea.
                            ...(UNIDAD_SAT[u] ? { unidad_sat: UNIDAD_SAT[u] } : {}),
                          });
                        }}
                      >
                        {(UNIDADES.includes(f.unidad.toUpperCase()) || !f.unidad
                          ? UNIDADES
                          : [f.unidad, ...UNIDADES]
                        ).map((u) => (
                          <option key={u} value={u}>
                            {u}
                          </option>
                        ))}
                      </Select>
                      {esVarianteNueva(f) ? (
                        <div className="mt-1 flex items-center gap-1 text-xs">
                          <span>1 =</span>
                          <div className="w-14">
                            <Input
                              value={f.factor}
                              onChange={(e) => setFila(f.fila, { factor: e.target.value })}
                              aria-label={`Equivalencia de la presentación de ${f.nombre}`}
                            />
                          </div>
                          <span className="text-muted">
                            {f.candidatos.find((c) => c.producto_id === f.producto_sel)?.unidad_base ??
                              "base"}
                          </span>
                        </div>
                      ) : null}
                    </td>

                    {/* Categoría — con alta al vuelo */}
                    <td className="px-2 py-2">
                      <Select
                        value={f.cat_id}
                        disabled={f.accion !== "crear"}
                        aria-label={`Categoría de ${f.nombre}`}
                        onChange={(e) => {
                          if (e.target.value === "__nueva__") {
                            const nombre = window.prompt(
                              "Nombre de la categoría nueva",
                              f.categoria || ""
                            );
                            if (nombre) void crearCategoria(nombre, f.fila);
                            return;
                          }
                          setFila(f.fila, { cat_id: e.target.value });
                        }}
                      >
                        <option value="">
                          {f.categoria ? `+ Crear «${f.categoria}»` : "— Sin categoría —"}
                        </option>
                        {categorias.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.nombre}
                          </option>
                        ))}
                        <option value="__nueva__">+ Nueva categoría…</option>
                      </Select>
                    </td>

                    {/* Esquema de impuesto */}
                    <td className="px-2 py-2">
                      <Select
                        value={f.esq_id}
                        disabled={f.accion !== "crear"}
                        aria-label={`Esquema de impuesto de ${f.nombre}`}
                        onChange={(e) => setFila(f.fila, { esq_id: e.target.value })}
                      >
                        <option value="">— Sin esquema —</option>
                        {esquemas.map((e) => (
                          <option key={e.id} value={e.id}>
                            {e.codigo} · IVA {Math.round(Number(e.iva_tasa) * 100)}%
                          </option>
                        ))}
                      </Select>
                      {f.accion !== "crear" ? (
                        <div className="pt-0.5 text-xs text-muted">del producto existente</div>
                      ) : f.esq_id && f.esquema_origen === "ia" ? (
                        <div className="pt-0.5 text-xs text-muted">sugerido con IA</div>
                      ) : f.esq_id && f.esquema_origen === "regla" ? (
                        <div className="pt-0.5 text-xs text-muted">regla SAT</div>
                      ) : f.esquema_motivo ? (
                        <div className="pt-0.5 text-xs text-danger">{f.esquema_motivo}</div>
                      ) : null}
                    </td>

                    {/* SAT */}
                    <td className="px-2 py-2">
                      {f.accion !== "omitir" ? (
                        <div className="flex gap-1">
                          <div className="w-[7rem]">
                            <Input
                              value={f.clave_sat}
                              onChange={(e) => setFila(f.fila, { clave_sat: e.target.value })}
                              placeholder="01010101"
                              aria-label={`Clave SAT de ${f.nombre}`}
                            />
                          </div>
                          <div className="w-[4.5rem]">
                            <Input
                              value={f.unidad_sat}
                              onChange={(e) =>
                                setFila(f.fila, { unidad_sat: e.target.value.toUpperCase() })
                              }
                              placeholder="KGM"
                              aria-label={`Unidad SAT de ${f.nombre}`}
                            />
                          </div>
                        </div>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>

                    <td className="px-2 py-2 text-right tabular-nums">{f.precio || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {paginas > 1 ? (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted">
                {pagina * POR_PAGINA + 1}–{Math.min((pagina + 1) * POR_PAGINA, filas.length)} de{" "}
                {filas.length}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  onClick={() => setPagina((p) => Math.max(0, p - 1))}
                  disabled={pagina === 0}
                >
                  Anterior
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => setPagina((p) => Math.min(paginas - 1, p + 1))}
                  disabled={pagina >= paginas - 1}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          ) : null}

          <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-background py-3">
            <Button variant="secondary" onClick={() => setPaso("revisar")} disabled={cargando}>
              Regresar
            </Button>
            <Button onClick={importar} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando
                ? "Importando…"
                : `Aprobar todo (${resumen.crear} nuevos, ${resumen.vincular} vinculados)`}
            </Button>
            {resumen.sinEsquema > 0 ? (
              <span className="text-sm text-danger">
                {resumen.sinEsquema} productos quedarían sin esquema de impuesto
              </span>
            ) : null}
          </div>
        </div>
      )}

      {/* ── 5. Resultado ───────────────────────────────────────────────── */}
      {paso === "resultado" && resultado && (
        <div className="max-w-2xl space-y-4">
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
              {resultado.categorias_creadas
                ? `${resultado.categorias_creadas} categorías creadas. `
                : ""}
              {resultado.presentaciones_agregadas
                ? `${resultado.presentaciones_agregadas} presentaciones nuevas agregadas.`
                : ""}
            </div>
          ) : null}
          {resultado.errores.length > 0 ? (
            <div className="rounded-lg border border-danger/40 bg-danger/5 p-3 text-sm">
              <div className="mb-1 font-medium text-danger">
                {resultado.errores.length} filas con error:
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
                Lista «{resultado.lista_nombre}» — ¿a quién se la asignamos?
              </div>
              <Select
                value={asignar}
                onChange={(e) => setAsignar(e.target.value as "nada" | "default" | "clientes")}
              >
                <option value="nada">Solo crearla (asignar después)</option>
                <option value="default">Default para todos los clientes sin lista propia</option>
                <option value="clientes">Elegir cliente(s)</option>
              </Select>
              {asignar === "clientes" ? (
                <div className="max-h-44 space-y-1 overflow-auto rounded-md border border-border p-2">
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
            <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm">
              {asignadoMsg}
            </div>
          ) : null}

          <Button onClick={aplicarAsignacion} disabled={asignando}>
            {asignando ? <Spinner className="h-4 w-4" /> : null}
            {resultado.lista_id && asignar !== "nada" && !asignadoMsg
              ? "Asignar y terminar"
              : "Ir a Productos"}
          </Button>
        </div>
      )}
    </div>
  );
}

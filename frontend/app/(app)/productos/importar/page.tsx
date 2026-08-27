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

import { CategoriaCombobox } from "@/components/CategoriaCombobox";
import { ProductoAccionCombobox } from "@/components/ProductoAccionCombobox";
import { Badge } from "@/components/ui/Badge";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useResource, type Page } from "@/lib/hooks";
import type {
  Candidato,
  Categoria,
  Cliente,
  EsquemaImpuesto,
  ImportColumna,
  ImportFilaPreview,
  ImportPreview,
  ImportResult,
  SugerenciaCategoria,
  SugerenciaEsquema,
  SugerenciaSat,
} from "@/lib/types";

const ACCEPT = ".xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.webp";

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
  /** El que se eligió con el buscador (no venía entre los parecidos). Se guarda
   *  UNO, el vigente: acumularlos llenaba "Parecidos" de productos que el cruce
   *  nunca propuso y que el usuario ya había descartado. */
  elegido?: Candidato;
};

/** Los parecidos del cruce, más el que se haya buscado a mano para esta fila. */
function candidatosDe(f: Fila): Candidato[] {
  const e = f.elegido;
  if (!e || f.candidatos.some((c) => c.producto_id === e.producto_id)) return f.candidatos;
  return [e, ...f.candidatos];
}

/** ¿La unidad elegida es una presentación NUEVA del producto vinculado?
 *  Se recalcula en vivo: la marca del backend es una foto contra el producto
 *  sugerido, y el usuario puede cambiar producto o unidad después. */
function esVarianteNueva(f: Fila): boolean {
  if (f.accion !== "vincular" || !f.producto_sel || !f.unidad) return false;
  const cand = candidatosDe(f).find((c) => c.producto_id === f.producto_sel);
  if (!cand) return false;
  const conocidas = new Set(
    [cand.unidad_base ?? "", ...Object.keys(cand.presentaciones ?? {})]
      .filter(Boolean)
      .map((u) => u.toUpperCase())
  );
  return !conocidas.has(f.unidad.toUpperCase());
}

/** La equivalencia tal como la escribió el usuario, aceptando coma decimal. */
function factorDe(f: Fila): string {
  return (f.factor || "1").trim().replace(",", ".");
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
  // limit=200 es el tope del endpoint (con 500 respondía 422 y la lista salía
  // vacía). Solo las ACTIVAS: son las que el usuario ve en /categorias.
  const categoriasRes = useResource<Page<Categoria>>(
    "/api/v1/categorias?limit=200&activo=true"
  );
  const [catsExtra, setCatsExtra] = useState<Categoria[]>([]);   // creadas aquí mismo
  const categorias = useMemo(
    () => [...(categoriasRes.data?.items ?? []), ...catsExtra],
    [categoriasRes.data, catsExtra]
  );

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
  // La casilla de cada fila significa "se importa". Desmarcarla la omite: es
  // el gesto natural para excluir unas cuantas de un lote grande.
  const [incluidas, setIncluidas] = useState<Set<number>>(new Set());
  // Las filas que arrancaron omitidas (repetida / BAJA) y el usuario volvió a
  // marcar: recuperan su acción natural. Sin esto la caja decía "Crear producto
  // nuevo" pero el alta las descartaba, y el resumen tampoco las contaba.
  const rescatarOmitidas = useCallback((claves: (string | number)[]) => {
    const marcadas = new Set(claves.map(Number));
    setFilas((rows) =>
      rows.map((f) =>
        f.accion === "omitir" && marcadas.has(f.fila)
          ? { ...f, accion: f.producto_sel ? "vincular" : "crear" }
          : f
      )
    );
  }, []);
  const [resetSeleccion, setResetSeleccion] = useState(0);
  // Último fallo al aprobar: se muestra junto al botón para que quede claro que
  // el trabajo sigue en pantalla y basta reintentar (pasa si el servidor se
  // reinicia a media aprobación).
  const [falloImport, setFalloImport] = useState("");

  // Paso 3 — decisiones de lote
  const [p1, setP1] = useState<"sugerida" | "generica">("sugerida");
  const [p2, setP2] = useState<"sugerida" | "generica">("sugerida");
  // Por cada categoría del archivo: "" = crear nueva, o el id de una existente.
  const [catDestino, setCatDestino] = useState<Record<string, string>>({});
  const [listaNombre, setListaNombre] = useState("");
  const [sugiriendoEsq, setSugiriendoEsq] = useState(false);
  const [sugiriendoCat, setSugiriendoCat] = useState(false);

  // Paso 5 — asignación de la lista
  const [asignar, setAsignar] = useState<"nada" | "default" | "clientes" | null>(null);
  const [asignando, setAsignando] = useState(false);
  const [asignadoMsg, setAsignadoMsg] = useState("");

  const hayClientes = clienteIds.length > 0;
  // Si eligió clientes, lo natural es que la lista sea de ellos; puede cambiarlo.
  const asignarA: "nada" | "default" | "clientes" = asignar ?? (hayClientes ? "clientes" : "nada");
  // PDF y fotos los lee la IA: eso tarda ~1 minuto y conviene decirlo.
  const esArchivoIA = /\.(pdf|png|jpe?g|webp)$/i.test(archivo?.name ?? "");

  /** ¿Esta fila se va a importar? (casilla marcada) */
  const seImporta = useCallback((f: Fila) => incluidas.has(f.fila), [incluidas]);

  const resumen = useMemo(() => {
    const activas = filas.filter((f) => incluidas.has(f.fila));
    const crear = activas.filter((f) => f.accion === "crear").length;
    const vincular = activas.filter((f) => f.accion === "vincular").length;
    const omitir = filas.length - activas.length;
    // Solo las que se CREAN reciben esquema: al vincular se conserva el del
    // producto que ya existe (el backend no lo toca).
    const sinEsquema = activas.filter((f) => f.accion === "crear" && !f.esq_id).length;
    // Solo las que se CREAN llevan categoría: al vincular se conserva la del
    // producto que ya existe.
    const sinCategoria = activas.filter((f) => f.accion === "crear" && !f.cat_id).length;
    const conPrecio = activas.filter((f) => f.precio).length;
    const variantes = activas.filter(esVarianteNueva).length;
    return { crear, vincular, omitir, sinEsquema, sinCategoria, conPrecio, variantes };
  }, [filas, incluidas]);

  // Dos filas que terminan en el MISMO producto: la segunda pisa el código y el
  // precio de la primera. El backend lo marca con la foto del preview; en cuanto
  // el usuario vincula a mano (con el buscador) hay que recalcularlo en vivo.
  const mismoProducto = useMemo(() => {
    const primera = new Map<string, number>();
    const avisos = new Map<number, number>();
    for (const f of filas) {
      if (!incluidas.has(f.fila) || f.accion !== "vincular" || !f.producto_sel) continue;
      const ya = primera.get(f.producto_sel);
      if (ya === undefined) primera.set(f.producto_sel, f.fila);
      else avisos.set(f.fila, ya);
    }
    return avisos;
  }, [filas, incluidas]);

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
        if (mapeo) fd.append("mapeo", JSON.stringify(mapeo));
        const p = await apiFetch<ImportPreview>(
          "/api/v1/productos/importar-preview",
          { method: "POST", body: fd },
          { timeoutMs: 5 * 60_000 },   // leer un PDF/foto con IA son ~90 s
        );
        setMeta(p);
        setColumnas(p.columnas ?? []);
        setCampos(p.campos_mapeables ?? []);
        const nuevas = p.filas.map(aFila);
        setFilas(nuevas);
        // Todo entra por default, salvo lo repetido o dado de baja en el archivo.
        setIncluidas(new Set(nuevas.filter((f) => !f.duplicada_de && !f.baja).map((f) => f.fila)));
        setResetSeleccion((n) => n + 1);
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
    [archivo, usarIa, listaNombre, toast]
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
  async function aplicarSat(): Promise<Fila[]> {
    const faltantes = filas.filter(
      (f) => seImporta(f) && (!f.clave_sat || !f.unidad_sat)
    );
    if (faltantes.length === 0) return filas;
    let porNombre: Record<string, SugerenciaSat> = {};
    if (p1 === "sugerida" || p2 === "sugerida") {
      const sugerencias = await apiFetch<SugerenciaSat[]>(
        "/api/v1/productos/sugerir-sat-batch",
        {
          method: "POST",
          body: JSON.stringify({
            productos: faltantes.map((f) => ({ nombre: f.nombre, unidad: f.unidad })),
          }),
        },
        { timeoutMs: 5 * 60_000 },
      );
      porNombre = Object.fromEntries(sugerencias.map((s) => [s.nombre, s]));
    }
    const nuevas = filas.map((f) => {
        if (!incluidas.has(f.fila)) return f;
        const s = porNombre[f.nombre];
        let clave = f.clave_sat;
        let unidad = f.unidad_sat;
        if (!clave) clave = p1 === "sugerida" && s ? s.clave_sat : "01010101";
        if (!unidad) {
          // La unidad SAT sale de la unidad DE ESTA FILA. La sugerencia viene
          // cruzada por nombre, así que "CILANTRO" manojo y "CILANTRO" kilo
          // recibían la misma — y el manojo se timbraba como kilogramo.
          unidad =
            UNIDAD_SAT[(f.unidad || "").toUpperCase()] ??
            (p2 === "sugerida" && s ? s.unidad_sat : s?.unidad_sat_generica ?? "H87");
        }
        return { ...f, clave_sat: clave, unidad_sat: unidad };
    });
    setFilas(nuevas);
    return nuevas;
  }

  /** Categoría para los productos NUEVOS que no traen ninguna: la IA elige
   *  entre las categorías que el negocio ya tiene (las de /categorias). */
  async function sugerirCategorias() {
    const faltantes = filas.filter((f) => seImporta(f) && f.accion === "crear" && !f.cat_id);
    if (faltantes.length === 0) {
      toast.info("Todos los productos nuevos ya tienen categoría");
      return;
    }
    setSugiriendoCat(true);
    try {
      const sug = await apiFetch<SugerenciaCategoria[]>(
        "/api/v1/productos/sugerir-categoria-batch",
        {
          method: "POST",
          body: JSON.stringify({
            usar_ia: usarIa,
            productos: faltantes.map((f) => ({ nombre: f.nombre, clave_sat: f.clave_sat })),
          }),
        },
        { timeoutMs: 5 * 60_000 },
      );
      const porNombre = Object.fromEntries(sug.map((x) => [x.nombre, x]));
      const asignados = faltantes.filter((f) => porNombre[f.nombre]?.categoria_id).length;
      setFilas((rows) =>
        rows.map((f) => {
          if (!incluidas.has(f.fila) || f.accion !== "crear" || f.cat_id) return f;
          const c = porNombre[f.nombre];
          return c?.categoria_id ? { ...f, cat_id: c.categoria_id } : f;
        })
      );
      if (asignados < faltantes.length) {
        toast.info(
          `${asignados} de ${faltantes.length} con categoría. Los demás se quedan sin categoría; puedes ponérsela en el preview.`
        );
      } else {
        toast.success(`${asignados} productos con categoría asignada`);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron sugerir las categorías");
    } finally {
      setSugiriendoCat(false);
    }
  }

  /** Esquema de impuesto para las filas que no tienen: reglas fiscales + IA. */
  async function sugerirEsquemas(base?: Fila[]) {
    const filasBase = base ?? filas;
    const faltantes = filasBase.filter((f) => seImporta(f) && !f.esq_id);
    if (faltantes.length === 0) {
      if (!base) toast.info("Todas las filas ya tienen esquema");
      return;
    }
    setSugiriendoEsq(true);
    try {
      const sug = await apiFetch<SugerenciaEsquema[]>(
        "/api/v1/productos/sugerir-esquema-batch",
        {
          method: "POST",
          body: JSON.stringify({
            usar_ia: usarIa,
            productos: faltantes.map((f) => ({
              nombre: f.nombre,
              clave_sat: f.clave_sat,
              categoria: f.categoria,
            })),
          }),
        },
        { timeoutMs: 5 * 60_000 },
      );
      const porNombre = Object.fromEntries(sug.map((s) => [s.nombre, s]));
      const asignados = faltantes.filter((f) => porNombre[f.nombre]?.esquema_id).length;
      setFilas((rows) =>
        rows.map((f) => {
          if (!incluidas.has(f.fila) || f.esq_id) return f;
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
      const conSat = await aplicarSat();
      // Sin esquema no se puede facturar: se asigna solo antes de enseñar la
      // tabla, en vez de dejarlo a que el usuario recuerde apretar un botón.
      // Con las filas RECIÉN actualizadas: las reglas fiscales se apoyan en la
      // clave SAT que `aplicarSat` acaba de poner.
      if (resumen.sinEsquema > 0 && esquemas.length > 0) {
        await sugerirEsquemas(conSat);
      }
      setPaso("preview");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron aplicar las sugerencias");
    } finally {
      setCargando(false);
    }
  }

  function setFila(fila: number, patch: Partial<Fila>) {
    setFilas((rows) => rows.map((r) => (r.fila === fila ? { ...r, ...patch } : r)));
  }

  /** Vincula la fila a un producto. Si vino del buscador (no estaba entre los
   *  parecidos del preview), se guarda en `candidatos` de esa fila: de ahí
   *  salen la categoría y el esquema que hereda, y el cálculo de si su unidad
   *  es una presentación nueva. */
  function vincularFila(fila: number, c: Candidato) {
    setFilas((rows) =>
      rows.map((r) =>
        r.fila === fila
          ? { ...r, accion: "vincular", producto_sel: c.producto_id, elegido: c }
          : r
      )
    );
  }

  async function importar() {
    if (incluidas.size === 0) {
      toast.error("No hay ninguna fila marcada para importar");
      return;
    }
    // Un "0,5" con coma llegaba al backend como texto y lo rechazaba entero,
    // con un mensaje en inglés y sin decir de qué fila. Se avisa aquí, por fila.
    const malFactor = filas.find(
      (f) => seImporta(f) && esVarianteNueva(f) && !(Number(factorDe(f)) > 0)
    );
    if (malFactor) {
      toast.error(
        `Fila ${malFactor.fila} (${malFactor.nombre}): la equivalencia "${malFactor.factor}" ` +
        "no es un número válido. Escribe cuánto de la unidad base es 1 (por ejemplo 0.5)."
      );
      return;
    }
    const pendiente = filas.find(
      (f) => seImporta(f) && f.accion === "vincular" && !f.producto_sel
    );
    if (pendiente) {
      toast.error(
        `Fila ${pendiente.fila} (${pendiente.nombre}): elige el producto a vincular ` +
        "— búscala con el buscador de la tabla"
      );
      return;
    }
    setCargando(true);
    try {
      const conPrecios = Boolean(meta?.tiene_precios);
      const res = await apiFetch<ImportResult>(
        "/api/v1/productos/importar",
        {
        method: "POST",
        body: JSON.stringify({
          guardar_precios: conPrecios,
          lista_nombre: conPrecios ? listaNombre.trim() || null : null,
          // Las categorías nuevas se crean con el nombre del archivo cuando el
          // usuario dejó "crear nueva" para esa categoría.
          crear_categorias: true,
          filas: filas.map((f) => ({
            accion: seImporta(f) ? f.accion : "omitir",
            producto_id: f.accion === "vincular" ? f.producto_sel : null,
            nombre: f.nombre,
            sku: f.accion === "crear" && f.codigo ? f.codigo : null,
            descripcion: f.descripcion || null,
            unidad_base: f.unidad || null,
            clave_sat: f.clave_sat || null,
            unidad_sat: f.unidad_sat || null,
            codigo_barras: f.codigo_barras || null,
            categoria_id: f.cat_id || null,
            categoria: f.cat_id ? null : f.categoria || null,
            esquema_impuesto_id: f.esq_id || null,
            activo: !f.baja,
            presentacion_factor: esVarianteNueva(f) ? factorDe(f) : null,
            codigo_cliente: f.codigo || null,   // viaja para el paso final
            nombre_cliente: f.nombre || null,
            precio: f.precio || null,
          })),
        }),
        },
        { timeoutMs: 3 * 60_000 },
      );
      setResultado(res);
      setPaso("resultado");
      setFalloImport("");
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "No se pudo importar";
      setFalloImport(msg);
      toast.error(msg);
    } finally {
      setCargando(false);
    }
  }

  async function aplicarAsignacion() {
    if (!resultado || asignadoMsg) {
      router.push("/productos");
      return;
    }
    const sinCambios =
      !hayClientes &&
      (!resultado.lista_id ||
        (asignarA === "nada" && listaNombre.trim() === (resultado.lista_nombre ?? "")));
    if (sinCambios) {
      router.push("/productos");
      return;
    }
    setAsignando(true);
    const hechos: string[] = [];
    try {
      // 1. El catálogo de los clientes elegidos: su código, su nombre y su
      //    presentación por producto — lo que sale en SUS facturas.
      if (hayClientes && resultado.productos.length > 0) {
        const r = await apiFetch<{ clientes: number; guardados: number }>(
          "/api/v1/productos/catalogo-cliente-batch",
          {
            method: "POST",
            body: JSON.stringify({ cliente_ids: clienteIds, items: resultado.productos }),
          }
        );
        hechos.push(`Catálogo guardado para ${r.clientes} cliente(s)`);
      }
      if (!resultado.lista_id) {
        setAsignadoMsg(hechos.join(". ") || "Listo");
        toast.success("Guardado");
        setAsignando(false);
        return;
      }
      // El nombre se decide al final: si lo cambió, se renombra la lista.
      const nombreFinal = listaNombre.trim();
      if (nombreFinal && nombreFinal !== resultado.lista_nombre) {
        await apiFetch(`/api/v1/listas-precios/${resultado.lista_id}`, {
          method: "PATCH",
          body: JSON.stringify({ nombre: nombreFinal }),
        });
      }
      const r = await apiFetch<{ default: boolean; clientes_asignados: number }>(
        `/api/v1/listas-precios/${resultado.lista_id}/asignar`,
        {
          method: "POST",
          body: JSON.stringify({
            default: asignarA === "default",
            // Los MISMOS clientes de arriba: si esta lista es de ellos, es la
            // que deben usar al facturar.
            cliente_ids: asignarA === "clientes" ? clienteIds : [],
          }),
        }
      );
      hechos.push(
        r.default
          ? "Lista marcada como default para todos los clientes"
          : r.clientes_asignados > 0
            ? `Lista asignada a ${r.clientes_asignados} cliente(s)`
            : "Lista guardada"
      );
      setAsignadoMsg(hechos.join(". "));
      toast.success("Guardado");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo asignar la lista");
    } finally {
      setAsignando(false);
    }
  }

  // Columnas de la tabla del preview: todo lo que se va a dar de alta.
  const columnasPreview: Column<Fila>[] = [
    {
      key: "fila",
      header: "#",
      className: "align-top",
      sortable: true,
      sortValue: (f) => f.fila,
      cell: (f) => <span className="text-muted tabular-nums">{f.fila}</span>,
    },
    {
      key: "producto",
      header: "Producto del archivo",
      className: "align-top min-w-[15rem]",
      sortable: true,
      sortValue: (f) => f.nombre,
      // Accessor de BÚSQUEDA (el orden sigue siendo por nombre): con una lista
      // SAE el código es justo por lo que el usuario busca.
      exportValue: (f) => [f.nombre, f.codigo, f.codigo_barras, f.descripcion].filter(Boolean).join(" "),
      cell: (f) => (
        <div className={seImporta(f) ? "" : "opacity-50"}>
          <div className="font-medium">{f.nombre}</div>
          {f.codigo ? <div className="text-xs text-muted tabular-nums">{f.codigo}</div> : null}
          <div className="flex flex-wrap gap-1 pt-0.5">
            {f.ya_vinculado ? <Badge tone="accent">Ya vinculado</Badge> : null}
            {f.duplicada_de ? (
              <Badge tone={f.precio_distinto ? "danger" : "warning"}>
                {f.precio_distinto
                  ? `Repetida con otro precio (fila ${f.duplicada_de})`
                  : `Repetida (fila ${f.duplicada_de})`}
              </Badge>
            ) : null}
            {mismoProducto.get(f.fila) ? (
              <Badge tone="warning">Mismo producto que fila {mismoProducto.get(f.fila)}</Badge>
            ) : null}
            {f.baja ? <Badge tone="muted">BAJA</Badge> : null}
            {f.clave_sat_valida === false ? (
              <Badge tone="danger">Clave SAT inexistente</Badge>
            ) : null}
          </div>
        </div>
      ),
    },
    {
      key: "accion",
      header: "Acción",
      sortValue: (f) => f.accion,
      className: "align-top min-w-[15rem]",
      cell: (f) => (
        // Una sola caja: crear, vincular a uno de los parecidos, o buscar
        // cualquier producto del catálogo. Dos selects encadenados obligaban a
        // dos clics para decir una sola cosa.
        <ProductoAccionCombobox
          valor={f.accion === "vincular" ? f.producto_sel : ""}
          candidatos={candidatosDe(f)}
          ariaLabel={`Qué hacer con ${f.nombre}`}
          onCrear={() => setFila(f.fila, { accion: "crear", producto_sel: "" })}
          onVincular={(c) => vincularFila(f.fila, c)}
        />
      ),
    },
    {
      key: "unidad",
      header: "Unidad de salida",
      sortValue: (f) => f.unidad,
      className: "align-top min-w-[9rem]",
      cell: (f) => (
        <>
          <Select
            value={f.unidad || "KILO"}
            aria-label={`Unidad de salida de ${f.nombre}`}
            onChange={(e) => {
              const u = e.target.value;
              setFila(f.fila, {
                unidad: u,
                // La unidad SAT sigue a la de salida: es con la que se timbra.
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
                {candidatosDe(f).find((c) => c.producto_id === f.producto_sel)?.unidad_base ?? "base"}
              </span>
            </div>
          ) : null}
        </>
      ),
    },
    {
      key: "categoria",
      header: "Categoría",
      sortValue: (f) => categorias.find((c) => c.id === f.cat_id)?.nombre ?? f.categoria,
      className: "align-top min-w-[12rem]",
      cell: (f) => {
        // Al vincular, el producto conserva SU categoría: enseñarla es más
        // honesto que un combo apagado en "Sin categoría".
        if (f.accion === "vincular") {
          const cand = candidatosDe(f).find((c) => c.producto_id === f.producto_sel);
          return (
            <div className="text-sm">
              {cand?.categoria_nombre || <span className="text-muted">Sin categoría</span>}
              <div className="text-xs text-muted">del producto existente</div>
            </div>
          );
        }
        return (
          <CategoriaCombobox
            value={f.cat_id}
            categorias={categorias}
            sugerida={f.categoria || undefined}
            ariaLabel={`Categoría de ${f.nombre}`}
            onCreada={(c) => setCatsExtra((x) => [...x, c])}
            onChange={(v) => setFila(f.fila, { cat_id: v })}
          />
        );
      },
    },
    {
      key: "esquema",
      header: "Esquema de impuesto",
      sortValue: (f) => esquemas.find((e) => e.id === f.esq_id)?.codigo ?? "",
      className: "align-top min-w-[11rem]",
      cell: (f) => (
        <>
          <Select
            value={
              f.accion === "vincular"
                ? candidatosDe(f).find((c) => c.producto_id === f.producto_sel)
                    ?.esquema_impuesto_id ?? ""
                : f.esq_id
            }
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
        </>
      ),
    },
    {
      key: "sat",
      header: "Clave / unidad SAT",
      className: "align-top min-w-[12rem]",
      sortable: true,
      sortValue: (f) => f.clave_sat,
      cell: (f) =>
        seImporta(f) ? (
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
                onChange={(e) => setFila(f.fila, { unidad_sat: e.target.value.toUpperCase() })}
                placeholder="KGM"
                aria-label={`Unidad SAT de ${f.nombre}`}
              />
            </div>
          </div>
        ) : (
          <span className="text-muted">—</span>
        ),
    },
    {
      key: "precio",
      header: "Precio",
      className: "align-top text-right",
      sortable: true,
      sortValue: (f) => f.precio || "",
      cell: (f) => <span className="tabular-nums">{f.precio || "—"}</span>,
    },
  ];

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
          <div className="flex items-center gap-3">
            <Switch checked={usarIa} onChange={setUsarIa} />
            <span className="text-sm">
              Usar IA cuando haga falta (leer PDF/foto, sugerir claves SAT y esquemas)
            </span>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => void analizar()} disabled={!archivo || cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : <FileUp size={16} />}
              {cargando
                ? esArchivoIA
                  ? "Leyendo con IA… (tarda ~1 minuto)"
                  : "Leyendo archivo…"
                : "Analizar archivo"}
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
                            <div className="flex-1">
                              <CategoriaCombobox
                                value={catDestino[m.nombre_archivo] ?? ""}
                                categorias={categorias}
                                sugerida={m.nombre_archivo}
                                ariaLabel={`Categoría del sistema para ${m.nombre_archivo}`}
                                onCreada={(c) => setCatsExtra((x) => [...x, c])}
                                onChange={(v) => {
                                  setCatDestino((d) => ({ ...d, [m.nombre_archivo]: v }));
                                  // Se aplica ya: si se hiciera al entrar al
                                  // preview, pisaría las correcciones fila a fila.
                                  setFilas((rows) =>
                                    rows.map((f) =>
                                      f.categoria === m.nombre_archivo ? { ...f, cat_id: v } : f
                                    )
                                  );
                                }}
                              />
                            </div>
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

          {/* Categoría de los productos nuevos (los vinculados heredan la suya) */}
          <section className="space-y-2">
            <h2 className="text-sm font-medium">Categoría de los productos nuevos</h2>
            <div className="space-y-2 rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  {resumen.sinCategoria === 0 ? (
                    <>Todos los productos nuevos tienen categoría.</>
                  ) : (
                    <>
                      <b>{resumen.sinCategoria}</b> de {resumen.crear} productos nuevos
                      no traen categoría en el archivo.
                    </>
                  )}
                </span>
                <Button
                  variant="secondary"
                  onClick={sugerirCategorias}
                  disabled={
                    sugiriendoCat ||
                    resumen.sinCategoria === 0 ||
                    categorias.length === 0 ||
                    !usarIa
                  }
                >
                  {sugiriendoCat ? <Spinner className="h-4 w-4" /> : <Sparkles size={16} />}
                  {sugiriendoCat ? "Analizando…" : "Agregar categorías sugeridas"}
                </Button>
              </div>
              <p className="text-xs text-muted">
                {categorias.length === 0 ? (
                  <>
                    No tienes categorías dadas de alta.{" "}
                    <Link href="/categorias" className="text-accent hover:underline">
                      Créalas primero
                    </Link>{" "}
                    para poder asignarlas aquí.
                  </>
                ) : !usarIa ? (
                  <>
                    Esta sugerencia la hace la IA, y la dejaste apagada en el paso 1.
                    Puedes poner la categoría a mano en la tabla del siguiente paso.
                  </>
                ) : (
                  <>
                    Se elige entre tus {categorias.length} categorías activas — nunca se
                    inventan nuevas. Los productos que vincules conservan la categoría que
                    ya tienen.
                  </>
                )}
              </p>
            </div>
          </section>

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
                    onClick={() => sugerirEsquemas()}
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
          {/* Acción principal arriba: con cientos de filas, el pie queda lejos.
              Desmarcar una casilla la omite — el conteo lo refleja al vuelo. */}
          <div className="sticky top-0 z-20 -mx-1 flex flex-wrap items-center gap-3 border-b border-border bg-background px-1 py-3">
            <Button onClick={importar} disabled={cargando}>
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando
                ? "Importando…"
                : `Aprobar ${resumen.crear} nuevos, ${resumen.vincular} vinculados, ${resumen.omitir} omitidos`}
            </Button>
            <Button variant="secondary" onClick={() => setPaso("revisar")} disabled={cargando}>
              Regresar
            </Button>
            {resumen.variantes > 0 ? (
              <Badge tone="accent">{resumen.variantes} presentaciones nuevas</Badge>
            ) : null}
            {resumen.sinEsquema > 0 ? (
              <Badge tone="danger">{resumen.sinEsquema} sin esquema de impuesto</Badge>
            ) : null}
            {resumen.conPrecio > 0 ? (
              <span className="text-sm text-muted">{resumen.conPrecio} con precio</span>
            ) : null}
            {falloImport ? (
              <span className="text-sm text-danger">
                {falloImport} — tus decisiones siguen aquí: vuelve a intentarlo.
              </span>
            ) : null}
          </div>

          <DataTable
            columns={columnasPreview}
            rows={filas}
            rowKey={(f) => f.fila}
            selectable
            onSelectionChange={(rows) => {
              setIncluidas(new Set(rows.map((f) => f.fila)));
              rescatarOmitidas(rows.map((f) => f.fila));
            }}
            selectionResetKey={resetSeleccion}
            initialSelectedKeys={filas.filter((f) => !f.duplicada_de && !f.baja).map((f) => f.fila)}
            searchable
            searchPlaceholder="Buscar producto, código, categoría…"
            paginated
            defaultPageSize={50}
            columnsMenu
            storageKey="importar-productos-preview"
            empty="Sin filas"
          />

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

          {!asignadoMsg ? (
            <div className="space-y-2 rounded-lg border border-border p-3">
              <div className="text-sm font-medium">
                ¿Esta lista es de algún cliente? (opcional)
              </div>
              <div className="text-xs text-muted">
                Se guardan SU código, SU nombre y SU presentación por producto — es lo
                que sale en las facturas de cada cliente.
              </div>
              <div className="rounded-lg border border-border">
                <div className="border-b border-border p-2">
                  <Input
                    value={filtroCliente}
                    onChange={(e) => setFiltroCliente(e.target.value)}
                    placeholder="Filtrar clientes…"
                  />
                </div>
                <div className="max-h-40 space-y-0.5 overflow-auto p-2">
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
            </div>
          ) : null}

          {resultado.lista_id && !asignadoMsg ? (
            <div className="space-y-2 rounded-lg border border-border p-3">
              <div className="text-sm font-medium">
                Lista de precios creada con {resultado.precios_guardados} precios
              </div>
              <Field label="Nombre de la lista">
                <Input
                  value={listaNombre}
                  onChange={(e) => setListaNombre(e.target.value)}
                  placeholder="Lista de precios"
                />
              </Field>
              <div className="text-sm font-medium">¿A quién se la asignamos?</div>
              <Select
                value={asignarA}
                onChange={(e) => setAsignar(e.target.value as "nada" | "default" | "clientes")}
              >
                <option value="clientes" disabled={!hayClientes}>
                  {hayClientes
                    ? `A los ${clienteIds.length} cliente(s) elegidos arriba`
                    : "A los clientes elegidos arriba (marca alguno)"}
                </option>
                <option value="default">Default para todos los clientes sin lista propia</option>
                <option value="nada">Solo crearla (asignar después)</option>
              </Select>
              {asignarA === "clientes" && hayClientes ? (
                <p className="text-xs text-muted">
                  {clientes
                    .filter((c) => clienteIds.includes(c.id))
                    .map((c) => c.legal_name)
                    .join(", ")}{" "}
                  facturarán con estos precios.
                </p>
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
            {!asignadoMsg && (resultado.lista_id || hayClientes)
              ? "Guardar y terminar"
              : "Ir a Productos"}
          </Button>
        </div>
      )}
    </div>
  );
}

"use client";

// Importar productos — pantalla completa (el preview trae demasiada
// información para un diálogo). Pasos:
//   1. Subir       archivo (plantilla/SAE, Excel libre, CSV, PDF o foto)
//   2. Columnas    qué columna del archivo es qué campo del sistema
//   3. Revisar     decisiones de lote: categorías (match con las existentes),
//                  esquema de impuesto (reglas fiscales + IA), claves SAT, lista
//   4. Preview     tabla editable: unidad, categoría, esquema, SAT, acción
//   5. Resultado   + a quién se asigna la lista de precios
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

/** Los recuentos del encabezado del preview son también filtros: al pulsarlos
 *  la tabla se acota a esas filas, que es como se revisa qué falta para dar la
 *  lista por completa. */
type FiltroId = "variantes" | "sinClaveSat" | "sinEsquema" | "sinPrecio";

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

/** Espejo de los recuentos de `resumen`: cada filtro muestra exactamente las
 *  filas que su número cuenta. Se definen juntos a propósito — si el recuento y
 *  el filtro se calcularan por separado, el chip diría 10 y la tabla otra cosa. */
const FALTA: Record<FiltroId, (f: Fila) => boolean> = {
  variantes: esVarianteNueva,
  sinClaveSat: (f) => f.accion === "crear" && !f.clave_sat,
  sinEsquema: (f) => f.accion === "crear" && !f.esq_id,
  sinPrecio: (f) => !f.precio,
};

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
  // Filtro activo del encabezado del preview (uno a la vez: son preguntas
  // distintas —"¿qué no tiene precio?"— y cruzarlas escondía filas sin decirlo).
  const [filtro, setFiltro] = useState<FiltroId | null>(null);

  // Paso 3 — decisiones de lote
  const [p1, setP1] = useState<"sugerida" | "generica">("sugerida");
  const [p2, setP2] = useState<"sugerida" | "generica">("sugerida");
  // Por cada categoría del archivo: "" = crear nueva, o el id de una existente.
  const [catDestino, setCatDestino] = useState<Record<string, string>>({});
  const [listaNombre, setListaNombre] = useState("");
  // Lote vigente de filas: analizar() lo avanza al reemplazarlas (e importar()
  // al aprobar, y la salida de la pantalla), y todo trabajo de fondo compara
  // contra él para no aplicarse — ni toastear — sobre filas que ya no existen.
  const loteRef = useRef(0);
  // Lote cuya cadena de fondo corre (null = ninguna): un segundo clic en
  // "Ver los productos" del MISMO lote no la duplica, y un lote nuevo sí
  // arranca la suya aunque la vieja siga muriendo por sus guards.
  const fondoRef = useRef<number | null>(null);
  // Lote de la request de esquemas en vuelo: evita duplicarla dentro del lote
  // ("Asignar automáticamente" + "Ver los productos" casi juntos) sin que una
  // request moribunda de un lote descartado estorbe al lote nuevo.
  const esqEnVuelo = useRef<number | null>(null);
  // Cada análisis registra el LOTE al que pertenece (null = ninguno); los
  // "Analizando…" solo encienden si es el lote vigente, para que una request
  // moribunda de un lote descartado no congele spinners ni selects.
  const [satLote, setSatLote] = useState<number | null>(null);
  const [esqLote, setEsqLote] = useState<number | null>(null);
  const [catLote, setCatLote] = useState<number | null>(null);
  const sugiriendoSat = satLote !== null && satLote === loteRef.current;
  const sugiriendoEsq = esqLote !== null && esqLote === loteRef.current;
  const sugiriendoCat = catLote !== null && catLote === loteRef.current;

  // Al desmontar la pantalla (Sidebar, Atrás del navegador, cualquier link),
  // todo lo que siga en vuelo queda inválido: ni aplica ni toastea sobre la
  // página a la que se fue el usuario.
  useEffect(() => {
    return () => {
      loteRef.current += 1;
    };
  }, []);

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

  // Espejos siempre frescos para el trabajo en segundo plano: los closures del
  // clic se quedan con la foto vieja mientras la IA responde (~1 minuto en el
  // que el usuario ya está editando la tabla).
  const filasRef = useRef<Fila[]>([]);
  useEffect(() => {
    filasRef.current = filas;
  }, [filas]);
  const incluidasRef = useRef(incluidas);
  useEffect(() => {
    incluidasRef.current = incluidas;
  }, [incluidas]);

  const resumen = useMemo(() => {
    const activas = filas.filter((f) => incluidas.has(f.fila));
    const crear = activas.filter((f) => f.accion === "crear").length;
    const vincular = activas.filter((f) => f.accion === "vincular").length;
    const omitir = filas.length - activas.length;
    // Solo las que se CREAN reciben esquema / clave SAT: al vincular se conserva
    // lo del producto que ya existe (el backend no lo toca). Ese matiz vive en
    // `FALTA`, que es también lo que filtra la tabla.
    const sinEsquema = activas.filter(FALTA.sinEsquema).length;
    // Solo las que se CREAN llevan categoría: al vincular se conserva la del
    // producto que ya existe.
    const sinCategoria = activas.filter((f) => f.accion === "crear" && !f.cat_id).length;
    const sinClaveSat = activas.filter(FALTA.sinClaveSat).length;
    const conPrecio = activas.filter((f) => f.precio).length;
    const sinPrecio = activas.filter(FALTA.sinPrecio).length;
    const variantes = activas.filter(FALTA.variantes).length;
    return { crear, vincular, omitir, sinEsquema, sinCategoria, sinClaveSat, conPrecio, sinPrecio, variantes };
  }, [filas, incluidas]);

  // Sugerencias de IA corriendo en segundo plano (la tabla se llena sola).
  const analizandoFondo = sugiriendoSat || sugiriendoEsq || sugiriendoCat;

  /** Los chips del encabezado del preview, en orden. `visible` repite la regla
   *  vieja de los badges: un "sin dato" solo se anuncia cuando su análisis ya
   *  terminó, porque a media asignación sería una alarma falsa de todo el lote.
   *  `sinPrecio` solo aplica si el archivo trae columna de precio; si no, todas
   *  estarían "sin precio" y el chip no diría nada. */
  const chips = useMemo(() => {
    const traePrecios = Boolean(meta?.tiene_precios);
    return ([
      { id: "variantes", n: resumen.variantes, tono: "accent",
        label: `${resumen.variantes} presentaciones nuevas`, visible: resumen.variantes > 0 },
      { id: "sinClaveSat", n: resumen.sinClaveSat, tono: "danger",
        label: `${resumen.sinClaveSat} sin clave SAT`, visible: !sugiriendoSat && resumen.sinClaveSat > 0 },
      { id: "sinEsquema", n: resumen.sinEsquema, tono: "danger",
        label: `${resumen.sinEsquema} sin esquema de impuesto`,
        visible: !sugiriendoSat && !sugiriendoEsq && resumen.sinEsquema > 0 },
      { id: "sinPrecio", n: resumen.sinPrecio, tono: "danger",
        label: `${resumen.sinPrecio} sin precio`, visible: traePrecios && resumen.sinPrecio > 0 },
    ] as const).filter((c) => c.visible);
  }, [resumen, meta, sugiriendoSat, sugiriendoEsq]);

  // Un filtro cuyo chip ya no está (se resolvió, o la IA volvió a correr) dejaría
  // la tabla vacía sin explicar por qué: se suelta solo.
  useEffect(() => {
    if (filtro && !chips.some((c) => c.id === filtro)) setFiltro(null);
  }, [chips, filtro]);

  // Al salir del preview el filtro no debe sobrevivir: se vuelve con la tabla
  // recortada y sin el encabezado que lo explica.
  useEffect(() => {
    if (paso !== "preview") setFiltro(null);
  }, [paso]);

  /** ¿A esta fila le falta algo de lo que se está anunciando? Pinta la partida
   *  en rojo. Solo mira los chips visibles, así que respeta la misma espera a
   *  que la IA termine y nunca pinta por algo que no se le está señalando. */
  const filaIncompleta = useCallback(
    (f: Fila) =>
      incluidas.has(f.fila) &&
      chips.some((c) => c.tono === "danger" && FALTA[c.id](f)),
    [chips, incluidas],
  );
  const tareasFondo = [
    sugiriendoSat && "claves SAT",
    sugiriendoEsq && "esquemas de impuesto",
    sugiriendoCat && "categorías",
  ]
    .filter(Boolean)
    .join(" y ");

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
      const lote = loteRef.current;
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
        loteRef.current += 1;   // filas nuevas: sugerencias en vuelo ya no aplican
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
        // Gateado por lote: si el usuario ya salió de la pantalla, el fallo
        // de una relectura abandonada no toastea sobre la otra página.
        if (loteRef.current === lote) {
          toast.error(
            e instanceof ApiError ? e.message : "No se pudo leer el archivo. Intenta de nuevo."
          );
        }
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

  /** Claves y unidades SAT faltantes, según lo elegido en el paso 3.
   *  Corre en segundo plano mientras el usuario ya ve la tabla. Devuelve las
   *  filas con las claves puestas, o null si NO se aplicó nada (falló la
   *  sugerencia o el lote cambió a media espera) — la cadena que la llama no
   *  debe seguir a los esquemas con claves a medias. */
  async function aplicarSat(lote: number): Promise<Fila[] | null> {
    const snapshot = filasRef.current;
    const incluidasAlLanzar = new Set(incluidasRef.current);
    const faltantes = snapshot.filter(
      (f) => incluidasAlLanzar.has(f.fila) && (!f.clave_sat || !f.unidad_sat)
    );
    if (faltantes.length === 0) return snapshot;
    let porNombre: Record<string, SugerenciaSat> = {};
    // El switch de IA del paso 1 manda: apagado, ninguna llamada — se aplican
    // las genéricas y el mapeo local de unidades.
    if (usarIa && (p1 === "sugerida" || p2 === "sugerida")) {
      setSatLote(lote);
      try {
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
      } catch (e) {
        if (loteRef.current === lote) {
          toast.error(
            e instanceof ApiError
              ? e.message
              : "No se pudieron sugerir las claves SAT — puedes capturarlas en la tabla"
          );
        }
        return null;
      } finally {
        setSatLote((v) => (v === lote ? null : v));
      }
    }
    if (loteRef.current !== lote) return null;
    // Un campo se rellena solo si está vacío AHORA y ya estaba vacío AL LANZAR
    // la request: lo que el usuario vació a propósito durante la espera (venía
    // con valor) no se toca, y una fila re-marcada durante la espera sí recibe
    // sus huecos (por nombre suele haber sugerencia — las repetidas comparten
    // nombre con la fila que sí viajó).
    const claveAlLanzar = new Map(snapshot.map((f) => [f.fila, f.clave_sat]));
    const unidadAlLanzar = new Map(snapshot.map((f) => [f.fila, f.unidad_sat]));
    const rellena = (f: Fila): Fila => {
        if (!incluidasRef.current.has(f.fila)) return f;
        const s = porNombre[f.nombre];
        let clave = f.clave_sat;
        let unidad = f.unidad_sat;
        if (!clave && !claveAlLanzar.get(f.fila)) {
          clave = p1 === "sugerida" && s ? s.clave_sat : "01010101";
        }
        if (!unidad && !unidadAlLanzar.get(f.fila)) {
          // La unidad SAT sale de la unidad DE ESTA FILA. La sugerencia viene
          // cruzada por nombre, así que "CILANTRO" manojo y "CILANTRO" kilo
          // recibían la misma — y el manojo se timbraba como kilogramo.
          // Sin unidad en el archivo, manda el KILO que la tabla ya enseña y
          // el backend guarda como unidad_base — caer a H87 timbraba piezas
          // sobre un producto mostrado y almacenado por kilo.
          unidad =
            UNIDAD_SAT[(f.unidad || "KILO").toUpperCase()] ??
            (p2 === "sugerida" && s ? s.unidad_sat : s?.unidad_sat_generica ?? "H87");
        }
        return { ...f, clave_sat: clave, unidad_sat: unidad };
    };
    // Funcional y solo llenando huecos: lo que el usuario editó en la tabla
    // mientras la IA respondía no se pisa. El retorno parte del espejo fresco
    // para que los esquemas se calculen con las claves manuales incluidas.
    setFilas((rows) => rows.map(rellena));
    return filasRef.current.map(rellena);
  }

  /** Categoría para los productos NUEVOS que no traen ninguna: la IA elige
   *  entre las categorías que el negocio ya tiene (las de /categorias). */
  async function sugerirCategorias() {
    const faltantes = filas.filter((f) => seImporta(f) && f.accion === "crear" && !f.cat_id);
    if (faltantes.length === 0) {
      toast.info("Todos los productos nuevos ya tienen categoría");
      return;
    }
    const lote = loteRef.current;
    setCatLote(lote);
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
      if (loteRef.current !== lote) return;   // ya son filas de otro archivo
      const porNombre = Object.fromEntries(sug.map((x) => [x.nombre, x]));
      const asignados = faltantes.filter((f) => porNombre[f.nombre]?.categoria_id).length;
      setFilas((rows) =>
        rows.map((f) => {
          if (!incluidasRef.current.has(f.fila) || f.accion !== "crear" || f.cat_id) return f;
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
      if (loteRef.current === lote) {
        toast.error(e instanceof ApiError ? e.message : "No se pudieron sugerir las categorías");
      }
    } finally {
      setCatLote((v) => (v === lote ? null : v));
    }
  }

  /** Esquema de impuesto para las filas que no tienen: reglas fiscales + IA. */
  async function sugerirEsquemas(base?: Fila[]) {
    const filasBase = base ?? filas;
    const faltantes = filasBase.filter((f) => incluidasRef.current.has(f.fila) && !f.esq_id);
    if (faltantes.length === 0) {
      if (!base) toast.info("Todas las filas ya tienen esquema");
      return;
    }
    const lote = loteRef.current;
    if (esqEnVuelo.current === lote) return;   // ya corre esta misma request
    esqEnVuelo.current = lote;
    setEsqLote(lote);
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
      if (loteRef.current !== lote) return;   // ya son filas de otro archivo
      const porNombre = Object.fromEntries(sug.map((s) => [s.nombre, s]));
      const asignados = faltantes.filter((f) => porNombre[f.nombre]?.esquema_id).length;
      setFilas((rows) =>
        rows.map((f) => {
          if (!incluidasRef.current.has(f.fila) || f.esq_id) return f;
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
      if (loteRef.current === lote) {
        toast.error(e instanceof ApiError ? e.message : "No se pudieron sugerir los esquemas");
      }
    } finally {
      if (esqEnVuelo.current === lote) esqEnVuelo.current = null;
      setEsqLote((v) => (v === lote ? null : v));
    }
  }

  /** A la tabla YA; las sugerencias corren en segundo plano. Con 160 filas la
   *  IA tarda ~1 minuto y antes este botón esperaba TODO encadenado (claves
   *  SAT y luego esquemas) con "Ver los productos" y "Regresar" congelados.
   *  Ahora la tabla se va llenando sola y todo sigue editable mientras. */
  function irAlPreview() {
    setPaso("preview");
    const lote = loteRef.current;
    if (fondoRef.current === lote) return;   // la cadena de ESTE lote ya corre
    fondoRef.current = lote;
    void (async () => {
      try {
        const conSat = await aplicarSat(lote);
        // null = no se aplicó nada (falló la sugerencia o el lote cambió):
        // seguir a los esquemas con claves a medias los calcularía mal.
        if (conSat === null || loteRef.current !== lote) return;
        // Si el usuario disparó "Asignar automáticamente" a mano mientras
        // corría lo del SAT, se le deja terminar y esta pasada — la que sí
        // trae las claves nuevas — trabaja sobre lo que aquella no resolvió.
        let base = conSat;
        while (esqEnVuelo.current === lote) {
          await new Promise((r) => setTimeout(r, 400));
          if (loteRef.current !== lote) return;
          base = filasRef.current;
        }
        // Sin esquema no se puede facturar: se asigna solo, sin que el usuario
        // recuerde apretar un botón — sobre las filas RECIÉN actualizadas,
        // porque las reglas fiscales se apoyan en la clave SAT nueva.
        if (
          esquemas.length > 0 &&
          base.some((f) => incluidasRef.current.has(f.fila) && f.accion === "crear" && !f.esq_id)
        ) {
          await sugerirEsquemas(base);
        }
      } finally {
        if (fondoRef.current === lote) fondoRef.current = null;
      }
    })();
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
    if (
      analizandoFondo &&
      !window.confirm(
        `Aún se están asignando ${tareasFondo} en segundo plano. Si apruebas ahora, ` +
        "algunos productos pueden quedar sin esos datos. ¿Importar así?"
      )
    ) {
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
    // Desde AQUÍ las sugerencias en vuelo ya no aplican: el payload de abajo
    // viaja sin ellas, y aplicarlas (con su toast de éxito) mientras el import
    // corre afirmaría datos que NO se importaron.
    const loteImport = loteRef.current;
    loteRef.current += 1;
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
      // El import falló: las filas siguen vivas en pantalla, así que se
      // restaura el lote para que las sugerencias vuelvan a operar normal.
      // Si el usuario ya salió (el lote avanzó por otro lado), ni se restaura
      // ni se toastea sobre la otra página.
      if (loteRef.current !== loteImport + 1) return;
      loteRef.current = loteImport;
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
            {f.clave_sat_valida === false && f.accion !== "vincular" ? (
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
      cell: (f) => {
        // Vinculada: el CFDI sale con lo fiscal del producto que ya existe, no
        // con lo que traiga el archivo. Se enseña eso —y sin el aviso de clave
        // inexistente, que hablaba de un dato que no se va a usar.
        if (seImporta(f) && f.accion === "vincular") {
          const cand = candidatosDe(f).find((c) => c.producto_id === f.producto_sel);
          return (
            <div className="text-sm">
              <span className="tabular-nums">{cand?.clave_sat || "—"}</span>
              <span className="text-muted"> · {cand?.unidad_sat || "—"}</span>
              <div className="text-xs text-muted">del producto existente</div>
            </div>
          );
        }
        return seImporta(f) ? (
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
        );
      },
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
              // Con clic modificado (abrir en pestaña nueva) esta página sigue
              // viva: ni confirm ni dar por muerto el trabajo en curso.
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
              const importando = cargando && paso === "preview";
              const aviso = importando
                ? "La aprobación ya se envió y terminará en el servidor aunque salgas — " +
                  "revisa el catálogo después. ¿Salir?"
                : "Se perderán las decisiones de esta importación. ¿Salir?";
              if (hayTrabajo && !window.confirm(aviso)) {
                e.preventDefault();
                return;
              }
              // Al salir, todo lo que siga en vuelo ya no aplica ni toastea.
              loteRef.current += 1;
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
              {sugiriendoSat ? (
                <div className="flex items-center gap-2 text-sm text-muted">
                  <Spinner className="h-4 w-4" />
                  Asignando las claves en segundo plano con lo elegido aquí…
                </div>
              ) : null}
              {!usarIa ? (
                <p className="text-xs text-muted">
                  La IA está apagada (paso 1): se usan las genéricas, y la
                  unidad SAT se deduce de la unidad de venta.
                </p>
              ) : null}
              {meta.faltan_clave_sat > 0 ? (
                <Field label={`${meta.faltan_clave_sat} productos sin clave SAT`}>
                  <Select
                    value={p1}
                    onChange={(e) => setP1(e.target.value as "sugerida" | "generica")}
                    disabled={sugiriendoSat || !usarIa}
                  >
                    <option value="sugerida">Asignar la sugerida (del catálogo SAT oficial)</option>
                    <option value="generica">Usar la genérica 01010101</option>
                  </Select>
                </Field>
              ) : null}
              {meta.faltan_unidad_sat > 0 ? (
                <Field label={`${meta.faltan_unidad_sat} productos sin unidad SAT`}>
                  <Select
                    value={p2}
                    onChange={(e) => setP2(e.target.value as "sugerida" | "generica")}
                    disabled={sugiriendoSat || !usarIa}
                  >
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
            <Button onClick={irAlPreview}>Ver los productos</Button>
          </div>
        </div>
      )}

      {/* ── 4. Preview a pantalla completa ─────────────────────────────── */}
      {paso === "preview" && (
        <div className="space-y-3">
          {/* Acción principal arriba: con cientos de filas, el pie queda lejos.
              Desmarcar una casilla la omite — el conteo lo refleja al vuelo. */}
          <div className="sticky top-0 z-20 -mx-1 flex flex-wrap items-center gap-3 border-b border-border bg-background px-1 py-3">
            {/* Sin esquema el producto nace sin IVA/IEPS y el CFDI sale mal, así
                que el alta no lo permite (el backend lo rechaza igual). Se frena
                aquí para no gastar el viaje y para poder señalar cuáles son. */}
            <Button
              onClick={importar}
              disabled={cargando || resumen.sinEsquema > 0}
              title={
                resumen.sinEsquema > 0
                  ? `${resumen.sinEsquema} productos nuevos no traen esquema de impuesto`
                  : undefined
              }
            >
              {cargando ? <Spinner className="h-4 w-4" /> : null}
              {cargando
                ? "Importando…"
                : `Aprobar ${resumen.crear} nuevos, ${resumen.vincular} vinculados, ${resumen.omitir} omitidos`}
            </Button>
            <Button variant="secondary" onClick={() => setPaso("revisar")} disabled={cargando}>
              Regresar
            </Button>
            {/* Cada recuento es un filtro: pulsarlo deja en la tabla justo esas
                filas, que es como se revisa qué falta para dar la lista por
                completa. Vuelve a pulsarlo (o "Ver todas") para soltarlo.
                Cuáles se muestran y cuándo lo decide `chips`. */}
            {chips.map((c) => {
              const activo = filtro === c.id;
              const base =
                c.tono === "danger"
                  ? "bg-red-50 text-red-700 hover:bg-red-100"
                  : "bg-blue-50 text-blue-700 hover:bg-blue-100";
              const anillo =
                c.tono === "danger" ? "ring-2 ring-red-500" : "ring-2 ring-blue-500";
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setFiltro(activo ? null : c.id)}
                  aria-pressed={activo}
                  title={activo ? "Quitar el filtro" : `Ver solo estas ${c.n} filas`}
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium transition ${base} ${activo ? anillo : ""}`}
                >
                  {c.label}
                </button>
              );
            })}
            {filtro ? (
              <button
                type="button"
                onClick={() => setFiltro(null)}
                className="text-xs font-medium text-muted underline underline-offset-2 hover:text-foreground"
              >
                Ver todas ({filas.length})
              </button>
            ) : null}
            {analizandoFondo ? (
              <span className="flex items-center gap-2 text-sm text-muted">
                <Spinner className="h-4 w-4" />
                Se siguen asignando {tareasFondo} — la tabla se llena sola;
                puedes revisar y editar mientras.
              </span>
            ) : null}
            {resumen.conPrecio > 0 ? (
              <span className="text-sm text-muted">{resumen.conPrecio} con precio</span>
            ) : null}
            {falloImport ? (
              <span className="text-sm text-danger">
                {falloImport} — tus decisiones siguen aquí: vuelve a intentarlo.
              </span>
            ) : null}
            {resumen.sinEsquema > 0 ? (
              <span className="w-full text-sm text-danger">
                {analizandoFondo
                  ? "Espera a que termine de asignar: aún hay productos sin esquema de impuesto."
                  : "No se puede aprobar: un producto sin esquema de impuesto nace sin IVA y su factura saldría mal. "}
                {!analizandoFondo ? (
                  <button
                    type="button"
                    onClick={() => setFiltro("sinEsquema")}
                    className="font-medium underline underline-offset-2"
                  >
                    Ver los {resumen.sinEsquema} que faltan
                  </button>
                ) : null}
                {!analizandoFondo
                  ? " — asígnaselo en la columna «Esquema», o elige uno para todo el lote en Regresar."
                  : null}
              </span>
            ) : null}
          </div>

          {/* Mientras "Importando…" el payload YA viajó: se congela la tabla
              para que una corrección tardía no se pierda en silencio. */}
          <div className={cargando ? "pointer-events-none opacity-60" : undefined}>
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
            // Filtro SOLO de presentación: `rows` sigue completa, así que ocultar
            // filas no las des-selecciona (si menguara `rows`, la tabla avisaría
            // de una selección más chica y esas filas se irían a "omitidas").
            rowFilter={filtro ? (f) => incluidas.has(f.fila) && FALTA[filtro](f) : undefined}
            rowFilterKey={filtro ?? ""}
            rowClassName={(f) => (filaIncompleta(f) ? "bg-red-50/60" : undefined)}
            // Desde `incluidas`, NO el default: al ir a Revisar y volver, la
            // tabla se re-monta y con el default perdía en silencio las
            // casillas que el usuario ya había cambiado.
            initialSelectedKeys={[...incluidas]}
            searchable
            searchPlaceholder="Buscar producto, código, categoría…"
            paginated
            defaultPageSize={50}
            columnsMenu
            storageKey="importar-productos-preview"
            empty="Sin filas"
          />
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

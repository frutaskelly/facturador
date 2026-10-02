"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Sparkles } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select } from "@/components/ui/Field";
import { DescripcionSat } from "@/components/DescripcionSat";
import { SatClaveCombobox } from "@/components/SatClaveCombobox";
import {
  UnidadesClavesSae,
  altasDesdeFilas,
  cambiarUnidadBase,
  cambiosSaeDesdeFilas,
  filaBase,
  normalizarClave,
  problemasClavesSae,
  validarClavesSae,
  type AltaSaeResumen,
  type FilaClave,
} from "@/components/UnidadesClavesSae";
import { useToast } from "@/components/ui/Toast";

// Unidades para el alta rápida de producto (mismas que el buscador de producto).
const UNIDADES_SAT: { code: string; nombre: string }[] = [
  { code: "H87", nombre: "Pieza" }, { code: "KGM", nombre: "Kilogramo" },
  { code: "GRM", nombre: "Gramo" }, { code: "LTR", nombre: "Litro" },
  { code: "MLT", nombre: "Mililitro" }, { code: "XBX", nombre: "Caja" },
  { code: "XPK", nombre: "Paquete" }, { code: "XBG", nombre: "Bolsa" },
  { code: "XSA", nombre: "Saco / Costal" }, { code: "DPC", nombre: "Docena" },
];
const UNIDADES_BASE = ["KILO", "PIEZA", "LITRO", "CAJA", "BULTO", "COSTAL", "MANOJO", "BOLSA"];

// Unidad SAT correspondiente a cada unidad base (default; el usuario puede cambiarla).
const SAT_POR_BASE: Record<string, string> = {
  KILO: "KGM", PIEZA: "H87", LITRO: "LTR", CAJA: "XBX",
  BULTO: "XSA", COSTAL: "XSA", MANOJO: "H87", BOLSA: "XBG",
};
const satPorBase = (base: string) => SAT_POR_BASE[base] ?? "H87";

/** El catálogo oficial del SAT, para que el operador verifique la clave él mismo. */
const SAT_CATALOGO_URL = "http://pys.sat.gob.mx/PyS/catPyS.aspx";

export type ProductoCreado = {
  id: string;
  sku: string;
  nombre: string;
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
  unidad_base?: string | null;
  /** Las altas en SAE que se encolaron con el producto (sólo al crear). */
  altas_sae?: AltaSaeResumen[];
};

/** Un producto del catálogo que se parece al que se está dando de alta. El
 *  backend los manda en el 409 para poder vincular en vez de duplicar. */
export type CandidatoDuplicado = {
  producto_id: string;
  sku: string;
  nombre: string;
  score: number;
  presentaciones?: Record<string, number> | null;
  presentacion_default?: string | null;
  unidad_base?: string | null;
};

/** Una clave candidata del sugeridor por IA. */
type SatOpcion = { clave_sat: string; descripcion: string };

/** Una lista de precios que le aplica al cliente de la orden. */
type ListaDelCliente = { lista_id: string; nombre: string; alcance: string };

/** Los candidatos que vienen dentro del `detail` del 409, si los hay. */
export function candidatosDuplicados(e: unknown): CandidatoDuplicado[] {
  if (!(e instanceof ApiError) || e.status !== 409) return [];
  const d = e.detail;
  if (!d || typeof d !== "object") return [];
  const lista = (d as { candidatos?: unknown }).candidatos;
  return Array.isArray(lista) ? (lista as CandidatoDuplicado[]) : [];
}

/**
 * Modal de alta rápida de producto (lo esencial: nombre, unidad base, unidad y
 * clave SAT). Reutilizable: lo usa el buscador de producto y la columna Match IA
 * del pegado de Excel. Al crear, devuelve el producto por `onCreated` y cierra.
 *
 * La clave SAT **se sugiere sola** al abrir: el alta cae casi siempre en medio
 * de capturar una orden, y quien la captura no se sabe el catálogo del SAT de
 * memoria. Se pide la sugerencia con el nombre ya tecleado, se propone la mejor
 * y se dejan las otras a un clic; el operador puede buscar en el catálogo
 * oficial o abrirlo en el SAT para verificar. Un `01010101` genérico pasa el
 * timbrado pero deja la factura mal clasificada, y eso se paga después.
 *
 * Con `clienteId`, además se puede dejar el precio en la lista de ese cliente
 * sin salir del modal: el producto nuevo nace con precio y la partida deja de
 * quedarse en blanco esperando a que alguien lo capture en otra pantalla.
 *
 * Reglas del dueño (2-oct-2026): la categoría es obligatoria, y en el tenant
 * dueño de SAE el producto no nace sin clave SAE en su unidad base — se liga
 * una que ya existe o se pide el alta, que viaja con el producto. Aquí sólo la
 * base: las demás unidades se agregan después en Productos.
 */
export function CrearProductoModal({
  open,
  onClose,
  nombreInicial = "",
  unidadBaseInicial = "KILO",
  clienteId = null,
  presentacionInicial,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  nombreInicial?: string;
  unidadBaseInicial?: string;
  /** Cliente de la orden: habilita guardar el precio en SU lista. */
  clienteId?: string | null;
  /** Presentación a la que va el precio (por defecto, la unidad base). */
  presentacionInicial?: string;
  onCreated: (p: ProductoCreado) => void;
}) {
  const toast = useToast();
  const { me } = useAuth();
  const saeConectado = !!me?.active_tenant.sae_conectado;
  const [cNombre, setCNombre] = useState(nombreInicial);
  const [cClaveSat, setCClaveSat] = useState("01010101");
  const [cUnidadBase, setCUnidadBase] = useState(unidadBaseInicial);
  const [cUnidadSat, setCUnidadSat] = useState(satPorBase(unidadBaseInicial));
  const [cSaving, setCSaving] = useState(false);
  // Productos parecidos que el backend encontró al intentar crear. Mientras
  // haya candidatos, el alta está detenida: hay que vincular o decir que no.
  const [parecidos, setParecidos] = useState<CandidatoDuplicado[]>([]);

  // Sugerencia de clave SAT por IA.
  const [satOpciones, setSatOpciones] = useState<SatOpcion[]>([]);
  const [satConfianza, setSatConfianza] = useState("");
  const [sugiriendo, setSugiriendo] = useState(false);
  const [satManual, setSatManual] = useState(false);

  // Categoría (obligatoria) y la clave SAE de la unidad base.
  const [categorias, setCategorias] = useState<{ id: string; nombre: string }[]>([]);
  const [categoriaSel, setCategoriaSel] = useState("");
  const [filas, setFilas] = useState<FilaClave[]>(() => [filaBase(unidadBaseInicial || "KILO")]);

  // Precio para la lista del cliente (opcional).
  const [precio, setPrecio] = useState("");
  const [listas, setListas] = useState<ListaDelCliente[]>([]);
  const [listaSel, setListaSel] = useState("");
  // Esquema de impuesto: obligatorio. Sin él el producto nace sin IVA y su
  // CFDI sale mal — el backend lo rechaza, y por aquí se colaron 22 productos
  // cuando este campo no existía.
  const [esquemas, setEsquemas] = useState<{ id: string; codigo: string; nombre: string }[]>([]);
  const [esquemaSel, setEsquemaSel] = useState("");

  /** Pide a la IA las claves candidatas para `nombre`. */
  const sugerirSat = useCallback(
    async (nombre: string) => {
      const n = nombre.trim();
      if (!n) return;
      setSugiriendo(true);
      try {
        const s = await apiFetch<{
          opciones: SatOpcion[];
          unidad_sat: string;
          descripcion_unidad: string;
          confianza: string;
        }>("/api/v1/sat/sugerir", {
          method: "POST",
          body: JSON.stringify({ nombre: n, descripcion: null }),
        });
        setSatOpciones(s.opciones);
        setSatConfianza(s.confianza);
        // La mejor queda puesta: el caso bueno es no tocar nada. Salvo con
        // confianza BAJA (2-oct-2026): ahí la primera es casi un volado y
        // dejarla puesta es invitar a no revisarla — se deja vacía y se escoge.
        if ((s.confianza || "").toLowerCase() === "baja") setCClaveSat("");
        else if (s.opciones[0]?.clave_sat) setCClaveSat(s.opciones[0].clave_sat);
        if (s.unidad_sat) setCUnidadSat(s.unidad_sat);
      } catch {
        // Sin IA (o sin llave) el alta sigue: queda el genérico y el buscador.
        setSatOpciones([]);
        setSatConfianza("");
      } finally {
        setSugiriendo(false);
      }
    },
    []
  );

  // Reinicia los campos con los valores iniciales cada vez que se abre.
  useEffect(() => {
    if (!open) return;
    const base = unidadBaseInicial || "KILO";
    setCNombre(nombreInicial);
    setCClaveSat("01010101");
    setCUnidadBase(base);
    setCUnidadSat(satPorBase(base));   // la unidad SAT sigue a la base por default
    setParecidos([]);
    setSatOpciones([]);
    setSatConfianza("");
    setSatManual(false);
    setPrecio("");
    setListaSel("");
    setCategoriaSel("");
    setFilas([filaBase(base)]);
    // El nombre ya viene tecleado desde el buscador: se sugiere sin pedirlo.
    if (nombreInicial.trim()) void sugerirSat(nombreInicial);
  }, [open, nombreInicial, unidadBaseInicial, sugerirSat]);

  // Los esquemas de impuesto del negocio, para elegir el del producto nuevo.
  useEffect(() => {
    if (!open) return;
    apiFetch<{ items: { id: string; codigo: string; nombre: string; activo: boolean }[] }>(
      "/api/v1/esquemas-impuesto?limit=200"
    )
      .then((r) => {
        const act = r.items.filter((e) => e.activo);
        setEsquemas(act);
        // No se preselecciona ninguno: elegir el esquema es una decisión fiscal
        // y un default invisible es justo cómo se cuela el equivocado.
        setEsquemaSel("");
      })
      .catch(() => setEsquemas([]));
  }, [open]);

  // Las categorías activas: la categoría es obligatoria en el alta.
  useEffect(() => {
    if (!open) return;
    apiFetch<{ items: { id: string; nombre: string; activo: boolean }[] }>("/api/v1/categorias?limit=200")
      .then((r) => setCategorias(r.items.filter((c) => c.activo)))
      .catch(() => setCategorias([]));
  }, [open]);

  // Las listas del cliente, para ofrecer dónde guardar el precio.
  useEffect(() => {
    if (!open || !clienteId) { setListas([]); return; }
    apiFetch<{ listas: ListaDelCliente[] }>(
      `/api/v1/precios/listas-del-cliente?cliente_id=${clienteId}`
    )
      .then((r) => {
        setListas(r.listas);
        // La general es la que casi siempre toca; si no hay, la primera.
        const general = r.listas.find((l) => l.alcance === "General");
        setListaSel((general ?? r.listas[0])?.lista_id ?? "");
      })
      .catch(() => setListas([]));
  }, [open, clienteId]);

  /** Guarda el precio en la lista elegida. No tumba el alta si falla: el
   *  producto ya existe y el precio se puede capturar después. */
  async function guardarPrecio(productoId: string, presentacion: string) {
    const v = precio.trim().replace(/,/g, "");
    if (!v || !listaSel) return;
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) {
      toast.error("El precio no es un número válido — el producto sí se creó");
      return;
    }
    try {
      await apiFetch(`/api/v1/listas-precios/${listaSel}/precios`, {
        method: "POST",
        body: JSON.stringify({
          producto_id: productoId,
          presentacion,
          precio_unitario: v,
        }),
      });
      const nombreLista = listas.find((l) => l.lista_id === listaSel)?.nombre ?? "la lista";
      toast.success(`Precio guardado en ${nombreLista}`);
    } catch (e) {
      toast.error(
        e instanceof Error ? `El producto se creó, pero el precio no: ${e.message}` : "El precio no se guardó"
      );
    }
  }

  /** Alta del producto. Sin `forzar`, el backend responde 409 con los productos
   *  del catálogo que se le parecen: es el candado que evita tener cilantro seis
   *  veces por escribirlo distinto. Con `forzar` se crea a sabiendas. */
  async function crearProducto(forzar = false) {
    if (cSaving) return;
    if (!cNombre.trim()) { toast.error("Escribe el nombre del producto"); return; }
    if (!esquemaSel) { toast.error("Elige el esquema de impuesto"); return; }
    if (faltas.length) { toast.error(faltas[0]); return; }
    const altas = saeConectado ? altasDesdeFilas(filas, cNombre.trim(), cUnidadSat) : [];
    setCSaving(true);
    try {
      const prod = await apiFetch<ProductoCreado>("/api/v1/productos", {
        method: "POST",
        body: JSON.stringify({
          nombre: cNombre.trim(),
          categoria_id: categoriaSel,
          esquema_impuesto_id: esquemaSel,
          clave_sat: cClaveSat.trim(),
          unidad_sat: cUnidadSat,
          unidad_base: cUnidadBase,
          // Sin SAE conectado no hay clave que pedir: nace como siempre.
          ...(saeConectado ? { clave_sae: normalizarClave(filas[0]?.clave) || null } : {}),
          presentaciones: { [cUnidadBase]: 1 },
          presentacion_default: cUnidadBase,
          ...(altas.length ? { altas_sae: altas } : {}),
          forzar,
        }),
      });
      // El producto nace con una sola presentación: la unidad base elegida.
      await guardarPrecio(prod.id, cUnidadBase);
      const avisos = [`Producto "${prod.nombre}" creado`];
      for (const a of prod.altas_sae ?? []) avisos.push(`Alta de ${a.clave} pedida (${a.empresas.join(", ")})`);
      // «Dejar la mía y pedir cambio en SAE» (2-oct-2026): igual que en
      // Productos, va DESPUÉS de crear; si falla, el producto ya existe — se
      // avisa, no se deshace. Sin esto la fila prometía el cambio y nadie lo
      // pedía: producto y SAE quedaban con claves SAT distintas.
      const cambios = saeConectado
        ? cambiosSaeDesdeFilas(filas, cClaveSat, esquemaCodigo, prod.id)
        : [];
      const fallidos: string[] = [];
      for (const c of cambios) {
        try {
          await apiFetch("/api/v1/productos/cambio-sae", { method: "POST", body: JSON.stringify(c) });
          avisos.push(`Cambio de ${c.clave} pedido en SAE`);
        } catch (e) {
          fallidos.push(`${c.clave}: ${e instanceof ApiError ? e.message : "no se pudo"}`);
        }
      }
      onCreated(prod);
      toast.success(avisos.join(" · "));
      if (fallidos.length) {
        toast.error(`El producto sí se creó, pero no pude pedir el cambio en SAE — ${fallidos.join(" · ")}`);
      }
      onClose();
    } catch (e) {
      const dups = candidatosDuplicados(e);
      if (dups.length) {
        setParecidos(dups);
      } else {
        toast.error(e instanceof Error ? e.message : "No se pudo crear el producto");
      }
    } finally {
      setCSaving(false);
    }
  }

  // El número de SAE del esquema elegido (su `codigo` en el Facturador).
  const esquemaCodigo = esquemas.find((e) => e.id === esquemaSel)?.codigo.trim() || null;
  const satBaja = (satConfianza || "").toLowerCase() === "baja";
  const nAltas = saeConectado ? filas.filter((f) => f.estado === "nueva").length : 0;

  // Lo que impide crear; el primero va en rojo al pie y apaga el botón.
  const faltas: string[] = [];
  if (!categoriaSel) faltas.push("Elige la categoría");
  if (!cClaveSat.trim()) faltas.push("Elige la clave SAT");
  const sinClave = validarClavesSae(filas, saeConectado);
  if (sinClave.length) faltas.push(`Falta la clave SAE de ${sinClave.join(", ")}`);
  faltas.push(...problemasClavesSae(filas, cNombre, saeConectado));
  if (nAltas) {
    if (!esquemaCodigo || !/^\d+$/.test(esquemaCodigo)) {
      faltas.push("Para pedir el alta en SAE, elige un esquema de impuesto con número de SAE");
    }
    if (!/^\d{8}$/.test(cClaveSat.trim())) faltas.push("Para pedir el alta en SAE, la clave SAT lleva 8 dígitos");
  }

  /** «Usar la de SAE» para el esquema: el de este lado con ese número. */
  function usarEsquemaDeSae(codigo: number) {
    const esq = esquemas.find((e) => e.codigo.trim() === String(codigo));
    if (!esq) {
      toast.error(`No hay un esquema activo con el número ${codigo} de SAE: créalo en Esquemas de impuesto`);
      return;
    }
    setEsquemaSel(esq.id);
  }

  /** "Es el mismo": no se crea nada — se devuelve el producto que ya existía,
   *  que es justo lo que el catálogo multicliente quiere (un producto, muchos
   *  nombres). Quien llamó al modal lo recibe como si lo acabara de crear. */
  async function usarExistente(c: CandidatoDuplicado) {
    // El precio tecleado era para ESTE producto: se guarda igual, aunque el
    // producto resultara ser uno que ya estaba. La presentación es la del
    // producto EXISTENTE, no la que se iba a crear.
    await guardarPrecio(c.producto_id, presentacionInicial || c.presentacion_default || cUnidadBase);
    onCreated({
      id: c.producto_id,
      sku: c.sku,
      nombre: c.nombre,
      presentaciones: c.presentaciones ?? {},
      presentacion_default: c.presentacion_default ?? null,
      unidad_base: c.unidad_base ?? null,
    });
    toast.success(`Se usó "${c.nombre}", que ya estaba en el catálogo`);
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Nuevo producto"
      // Con SAE la clave se busca aquí mismo, y las candidatas traen su
      // descripción y sus empresas: en el modal angosto no caben.
      size="lg"
      // Lo que falta va a la izquierda del pie: el Modal mete `footer` en un
      // bloque alineado a la derecha.
      footerStart={faltas.length ? (
        <span className="text-sm text-danger">{faltas[0]}</span>
      ) : undefined}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={cSaving}>Cancelar</Button>
          {parecidos.length ? (
            <Button variant="secondary" onClick={() => crearProducto(true)} disabled={cSaving || faltas.length > 0}>
              {cSaving ? "Creando…" : "Es distinto — crearlo igual"}
            </Button>
          ) : (
            <Button onClick={() => crearProducto()} disabled={cSaving || faltas.length > 0}>
              {cSaving ? "Creando…" : nAltas ? "Crear y pedir 1 alta en SAE" : "Crear producto"}
            </Button>
          )}
        </>
      }
    >
      {parecidos.length ? (
        <Alert tone="warning">
          <div className="font-medium">
            Esto ya podría estar en el catálogo. Un mismo producto con dos nombres se
            vuelve dos inventarios y dos precios.
          </div>
          <ul className="mt-2 space-y-1">
            {parecidos.map((c) => (
              <li key={c.producto_id} className="flex items-center justify-between gap-3">
                <span className="text-sm">
                  {c.nombre} <span className="text-xs text-muted">({c.sku})</span>
                </span>
                <Button variant="secondary" onClick={() => void usarExistente(c)} disabled={cSaving}>
                  Es el mismo
                </Button>
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}

      <div className={`grid grid-cols-1 gap-3 sm:grid-cols-2${parecidos.length ? " mt-3" : ""}`}>
        <div className="grid grid-cols-1 gap-3 sm:col-span-2 sm:grid-cols-[2fr_1fr]">
          <Field label="Nombre" required>
            <Input
              value={cNombre}
              onChange={(e) => {
                setCNombre(e.target.value);
                // Otro nombre, otra búsqueda: los parecidos de antes ya no
                // aplican y el botón de crear vuelve a ser el normal.
                setParecidos([]);
              }}
              onBlur={(e) => {
                // Si el nombre cambió respecto al que se sugirió, se vuelve a pedir.
                if (e.target.value.trim() && e.target.value.trim() !== nombreInicial.trim()) {
                  void sugerirSat(e.target.value);
                }
              }}
              autoFocus
            />
          </Field>
          <Field label="Categoría" required>
            <Select value={categoriaSel} onChange={(e) => setCategoriaSel(e.target.value)}>
              <option value="">— Elige —</option>
              {categorias.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Unidad base" hint="Unidad de inventario">
          <Select
            value={cUnidadBase}
            onChange={(e) => {
              const b = e.target.value;
              setCUnidadBase(b);
              setCUnidadSat(satPorBase(b));
              setFilas((fs) => cambiarUnidadBase(fs, b));
            }}
          >
            {UNIDADES_BASE.map((u) => <option key={u} value={u}>{u}</option>)}
          </Select>
        </Field>
        <Field label="Unidad SAT">
          <Select value={cUnidadSat} onChange={(e) => setCUnidadSat(e.target.value)}>
            {UNIDADES_SAT.map((u) => <option key={u.code} value={u.code}>{u.code} — {u.nombre}</option>)}
          </Select>
        </Field>

        <div className="sm:col-span-2">
          <Field
            label="Esquema de impuesto"
            required
            hint={
              esquemas.length === 0
                ? "No hay esquemas dados de alta — créalos en Catálogo › Esquemas de impuesto"
                : "Define el IVA/IEPS del producto. Sin él su factura saldría mal."
            }
          >
            <Select
              value={esquemaSel}
              onChange={(e) => setEsquemaSel(e.target.value)}
              disabled={esquemas.length === 0}
            >
              <option value="">Elige uno…</option>
              {esquemas.map((e) => (
                <option key={e.id} value={e.id}>{e.codigo} — {e.nombre}</option>
              ))}
            </Select>
          </Field>
        </div>

        <div className="sm:col-span-2">
          <Field
            label="Clave SAT"
            hint="Producto/servicio. La mal puesta no rebota al timbrar: clasifica mal la factura."
          >
            {satManual ? (
              <SatClaveCombobox value={cClaveSat} onChange={setCClaveSat} mostrarDescripcion={false} />
            ) : (
              <Input
                value={cClaveSat}
                placeholder={satBaja ? "Elige una de las opciones de abajo" : undefined}
                onChange={(e) => setCClaveSat(e.target.value.trim())}
              />
            )}
          </Field>
          <DescripcionSat clave={cClaveSat} />

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            {sugiriendo ? (
              <span className="inline-flex items-center gap-1 text-muted">
                <Sparkles size={13} /> Buscando la clave que le toca…
              </span>
            ) : (
              <button
                type="button"
                onClick={() => void sugerirSat(cNombre)}
                className="inline-flex items-center gap-1 text-accent hover:underline"
              >
                <Sparkles size={13} /> {satOpciones.length ? "Sugerir de nuevo" : "Sugerir con IA"}
              </button>
            )}
            <button
              type="button"
              onClick={() => setSatManual((v) => !v)}
              className="text-accent hover:underline"
            >
              {satManual ? "Escribirla a mano" : "Buscar en el catálogo"}
            </button>
            <a
              href={SAT_CATALOGO_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-accent hover:underline"
            >
              Verificar en el SAT <ExternalLink size={12} />
            </a>
          </div>

          {satOpciones.length ? (
            <div className="mt-2 rounded-lg border border-border bg-surface-2 p-2">
              <div className={`mb-1 text-xs ${satBaja ? "text-warning" : "text-muted"}`}>
                {satBaja ? (
                  <>
                    Sugerencias de la IA · confianza baja — no estoy segura, así que no dejé
                    ninguna puesta: escoge tú la que le toca (o búscala en el catálogo).
                  </>
                ) : (
                  <>
                    Sugerencias de la IA{satConfianza ? ` · confianza ${satConfianza}` : ""} — la
                    primera ya quedó puesta. Verifícala en el SAT antes de timbrar.
                  </>
                )}
              </div>
              <div className="space-y-1">
                {satOpciones.map((o) => {
                  const activa = o.clave_sat === cClaveSat;
                  return (
                    <button
                      key={o.clave_sat}
                      type="button"
                      onClick={() => setCClaveSat(o.clave_sat)}
                      className={`flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-sm ${
                        activa ? "bg-accent/10 font-medium" : "hover:bg-surface"
                      }`}
                    >
                      <span className="tabular-nums">{o.clave_sat}</span>
                      <span className="text-xs text-muted">{o.descripcion}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>

        {clienteId && listas.length ? (
          <>
            <Field
              label="Precio para este cliente"
              hint={`Por ${presentacionInicial || cUnidadBase}. Opcional — en blanco, se captura después.`}
            >
              <Input
                value={precio}
                inputMode="decimal"
                placeholder="0.00"
                className="text-right tabular-nums"
                onChange={(e) => setPrecio(e.target.value)}
              />
            </Field>
            <Field label="Se guarda en" hint="La lista de precios que le aplica al cliente">
              <Select value={listaSel} onChange={(e) => setListaSel(e.target.value)}>
                {listas.map((l) => (
                  <option key={l.lista_id} value={l.lista_id}>
                    {l.nombre} · {l.alcance}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        ) : null}

        {saeConectado ? (
          <div className="sm:col-span-2">
            <UnidadesClavesSae
              nombre={cNombre}
              filas={filas}
              onChange={setFilas}
              esquemaCodigo={esquemaCodigo}
              esquemaNombre={esquemas.find((e) => e.id === esquemaSel)?.nombre ?? null}
              claveSat={cClaveSat}
              unidadSat={cUnidadSat}
              onUsarSatDeSae={(c) => setCClaveSat(c)}
              onUsarEsquemaDeSae={usarEsquemaDeSae}
              saeConectado
              soloBase
            />
          </div>
        ) : null}

        <p className="text-xs text-muted sm:col-span-2">
          Se crea con lo esencial. Las demás presentaciones se agregan después en Productos.
        </p>
      </div>
    </Modal>
  );
}

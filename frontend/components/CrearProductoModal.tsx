"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Sparkles } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { ApiError, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select } from "@/components/ui/Field";
import { SatClaveCombobox } from "@/components/SatClaveCombobox";
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

  // Precio para la lista del cliente (opcional).
  const [precio, setPrecio] = useState("");
  const [listas, setListas] = useState<ListaDelCliente[]>([]);
  const [listaSel, setListaSel] = useState("");

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
        // La mejor queda puesta: el caso bueno es no tocar nada.
        if (s.opciones[0]?.clave_sat) setCClaveSat(s.opciones[0].clave_sat);
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
    // El nombre ya viene tecleado desde el buscador: se sugiere sin pedirlo.
    if (nombreInicial.trim()) void sugerirSat(nombreInicial);
  }, [open, nombreInicial, unidadBaseInicial, sugerirSat]);

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
    setCSaving(true);
    try {
      const prod = await apiFetch<ProductoCreado>("/api/v1/productos", {
        method: "POST",
        body: JSON.stringify({
          nombre: cNombre.trim(),
          clave_sat: cClaveSat.trim() || "01010101",
          unidad_sat: cUnidadSat,
          unidad_base: cUnidadBase,
          presentaciones: { [cUnidadBase]: 1 },
          presentacion_default: cUnidadBase,
          forzar,
        }),
      });
      // El producto nace con una sola presentación: la unidad base elegida.
      await guardarPrecio(prod.id, cUnidadBase);
      onCreated(prod);
      toast.success(`Producto "${prod.nombre}" creado`);
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
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={cSaving}>Cancelar</Button>
          {parecidos.length ? (
            <Button variant="secondary" onClick={() => crearProducto(true)} disabled={cSaving}>
              {cSaving ? "Creando…" : "Es distinto — crearlo igual"}
            </Button>
          ) : (
            <Button onClick={() => crearProducto()} disabled={cSaving}>
              {cSaving ? "Creando…" : "Crear producto"}
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
        <div className="sm:col-span-2">
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
        </div>
        <Field label="Unidad base" hint="Unidad de inventario">
          <Select value={cUnidadBase} onChange={(e) => { const b = e.target.value; setCUnidadBase(b); setCUnidadSat(satPorBase(b)); }}>
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
            label="Clave SAT"
            hint="Producto/servicio. La mal puesta no rebota al timbrar: clasifica mal la factura."
          >
            {satManual ? (
              <SatClaveCombobox value={cClaveSat} onChange={setCClaveSat} />
            ) : (
              <Input value={cClaveSat} onChange={(e) => setCClaveSat(e.target.value)} />
            )}
          </Field>

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
              <div className="mb-1 text-xs text-muted">
                Sugerencias de la IA{satConfianza ? ` · confianza ${satConfianza}` : ""} — la
                primera ya quedó puesta. Verifícala en el SAT antes de timbrar.
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

        <p className="text-xs text-muted sm:col-span-2">
          Se crea con lo esencial. Puedes completar categoría, presentaciones e impuestos después en Productos.
        </p>
      </div>
    </Modal>
  );
}

"use client";

// El DETALLE de una orden de la bandeja, a PANTALLA COMPLETA (28-ago-2026).
// Era un popup y el dueño lo bajó a tierra: esta es la pantalla de trabajo
// diaria — aquí se decide a quién se le factura, qué producto es cada partida
// y con eso nace la remisión. Merece toda la pantalla y su propia URL
// (compartible, recargable, con botón de atrás).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, ExternalLink, FileText, Inbox, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ProductoCombobox } from "@/components/ProductoCombobox";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type { Page } from "@/lib/hooks";
import type { Almacen, Cliente, OCRecibidaDetalle, Proyecto, Sucursal } from "@/lib/types";
import {
  CANAL_TONE,
  estadoTexto,
  LineaEdit,
  ORIGEN_CRUCE,
  precioNormalizado,
  presentacionDe,
  problemaDe,
  problemaVivo,
  tablaDe,
  unidadBaseDe,
} from "../cruce";

const WRITE = "remision:gestionar";

export default function Page() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [oc, setOc] = useState<OCRecibidaDetalle | null>(null);
  const [noEncontrada, setNoEncontrada] = useState(false);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [proyectos, setProyectos] = useState<Proyecto[]>([]);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);

  const [clienteSel, setClienteSel] = useState("");
  const [sucursalSel, setSucursalSel] = useState("");
  const [almacenSel, setAlmacenSel] = useState("");
  const [puntoEntrega, setPuntoEntrega] = useState("");
  const [proyectoSel, setProyectoSel] = useState("");
  const [lineas, setLineas] = useState<LineaEdit[]>([]);
  const [guardando, setGuardando] = useState(false);
  const [confirmaDescartar, setConfirmaDescartar] = useState(false);
  // Con candidatos, el desplegable arranca acotado a ellos: elegir entre dos es
  // otra cosa que buscar entre todo el padrón.
  const [verTodos, setVerTodos] = useState(false);

  const cargar = useCallback(async () => {
    try {
      const d = await apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${id}`);
      setOc(d);
      setPrecioAceptado([]);
      setClienteSel(d.cliente_id ?? "");
      setSucursalSel(d.sucursal_id ?? "");
      setPuntoEntrega(d.punto_entrega ?? "");
      setProyectoSel(d.proyecto_id ?? "");
      setVerTodos(!d.candidatos?.length);
      setAlmacenSel("");
      setLineas(tablaDe(d));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNoEncontrada(true);
      else toast.error("No se pudo abrir la orden");
    }
  }, [id, toast]);

  useEffect(() => { void cargar(); }, [cargar]);

  useEffect(() => {
    apiFetch<Page<Cliente>>("/api/v1/clientes?limit=1000").then((p) => setClientes(p.items)).catch(() => undefined);
    apiFetch<Page<Almacen>>("/api/v1/almacenes?limit=200").then((p) => setAlmacenes(p.items)).catch(() => undefined);
    apiFetch<Page<Proyecto>>("/api/v1/proyectos?activo=true&limit=500").then((p) => setProyectos(p.items)).catch(() => undefined);
  }, []);

  // Las sucursales dependen del cliente elegido, no del que traía la orden.
  useEffect(() => {
    if (!clienteSel) { setSucursales([]); return; }
    apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${clienteSel}&limit=500`)
      .then((p) => setSucursales(p.items))
      .catch(() => setSucursales([]));
  }, [clienteSel]);

  /** ¿El proyecto elegido NO entrega en la sucursal elegida? (regla de alcance:
   *  un proyecto sin sucursales asignadas aplica en cualquier plaza). Guardar
   *  un par incompatible falla con 422 en el backend; esto avisa antes. */
  const proyectoFueraDePlaza = useMemo(() => {
    if (!proyectoSel) return null;
    const p = proyectos.find((x) => x.id === proyectoSel);
    if (!p?.sucursal_ids?.length) return null;
    return sucursalSel && p.sucursal_ids.includes(sucursalSel) ? null : p;
  }, [proyectos, proyectoSel, sucursalSel]);

  /** ¿El operador cambió cliente/sucursal y todavía no se ha guardado? */
  const sinGuardar = useMemo(
    () =>
      !!oc &&
      (clienteSel !== (oc.cliente_id ?? "") ||
        sucursalSel !== (oc.sucursal_id ?? "") ||
        puntoEntrega.trim() !== (oc.punto_entrega ?? "").trim() ||
        proyectoSel !== (oc.proyecto_id ?? "")),
    [oc, clienteSel, sucursalSel, puntoEntrega, proyectoSel]
  );

  /** Persiste cliente/sucursal. Devuelve la OC guardada, o null si falló.
   *  `aprender` decide si la corrección se guarda como equivalencia (las
   *  próximas órdenes iguales se resuelven solas) o aplica solo a esta. */
  async function guardarAsignacion(aprender = true): Promise<OCRecibidaDetalle | null> {
    if (!oc || !clienteSel) {
      toast.error("Elige el cliente");
      return null;
    }
    const cambioDeCliente =
      clienteSel !== (oc.cliente_id ?? "") || sucursalSel !== (oc.sucursal_id ?? "");
    try {
      const d = await apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${oc.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          cliente_id: clienteSel,
          sucursal_id: sucursalSel || null,
          punto_entrega: puntoEntrega.trim() || null,
          proyecto_id: proyectoSel || null,
          aprender,
        }),
      });
      setOc(d);
      // La evaluación del servidor se renovó: las aceptaciones de conflictos
      // eran sobre la anterior.
      setPrecioAceptado([]);
      // El cruce se recalculó contra el catálogo y el vocabulario del cliente
      // guardado: la tabla se rehace o seguiría mandando productos del anterior.
      if (cambioDeCliente) setLineas(tablaDe(d));
      return d;
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo asignar");
      return null;
    }
  }

  /** Cotiza la partida `numero` contra la lista del cliente y refleja el
   *  resultado: si el campo está vacío (o su valor vino de una cotización
   *  previa), se llena — es lo que se va a cobrar, y verlo evita el "lista" a
   *  ciegas. Un precio TECLEADO jamás se pisa. La respuesta vieja no pisa a la
   *  nueva: se descarta si la línea ya apunta a otro producto/presentación.
   *  Si el rol no puede cotizar (menu:cotizador), simplemente no llena. */
  const cotizaSeq = useRef<Record<number, number>>({});

  // Otra sucursal u otro proyecto = otra negociación: lo cotizado ya no
  // aplica. Se limpia referencia y auto-llenado (lo tecleado se queda); el
  // precio bueno lo resuelve el backend al crear, y volver a cruzar re-cotiza.
  const contextoPrecios = `${sucursalSel}|${proyectoSel}`;
  const contextoPrev = useRef(contextoPrecios);
  useEffect(() => {
    if (contextoPrev.current === contextoPrecios) return;
    contextoPrev.current = contextoPrecios;
    cotizaSeq.current = {};
    setLineas((prev) =>
      prev.map((x) =>
        x.precioLista == null && !x.precioAuto
          ? x
          : {
              ...x,
              precioLista: null,
              precioListaId: null,
              precioTramo: null,
              ...(x.precioAuto ? { precio: "", precioAuto: false } : {}),
            }
      )
    );
  }, [contextoPrecios]);
  async function cotizarLinea(numero: number, productoId: string, presentacion: string, cantidad: string) {
    // Solo la ÚLTIMA cotización pedida para la partida puede escribir: dos
    // respuestas fuera de orden (cambio rápido de producto o de cantidad) no
    // deben dejar el precio ni la referencia de la anterior.
    const seq = (cotizaSeq.current[numero] ?? 0) + 1;
    cotizaSeq.current[numero] = seq;
    const qs = new URLSearchParams({
      producto_id: productoId,
      presentacion,
      cantidad: Number(cantidad) > 0 ? cantidad : "1",
    });
    if (clienteSel) qs.set("cliente_id", clienteSel);
    if (sucursalSel) qs.set("sucursal_id", sucursalSel);
    if (proyectoSel) qs.set("proyecto_id", proyectoSel);
    // La serie pesa más que sucursal+cliente en las asignaciones: sin ella,
    // una lista negociada por serie sería invisible para la cotización.
    if (oc?.serie_prevista_id) qs.set("serie_id", oc.serie_prevista_id);
    try {
      const c = await apiFetch<{
        precio?: string | number | null;
        origen?: string | null;
        lista_id?: string | null;
        cantidad_minima?: number | null;
      }>(`/api/v1/precios/cotizar?${qs}`);
      if (cotizaSeq.current[numero] !== seq) return;
      setLineas((prev) =>
        prev.map((x) => {
          if (x.numero !== numero || x.producto_id !== productoId || x.presentacion !== presentacion) return x;
          const resuelto = c.precio != null && Number.isFinite(Number(c.precio))
            ? Number(c.precio).toFixed(2)
            : null;
          const llenar = resuelto != null && (!x.precio.trim() || x.precioAuto);
          // La lista BASE queda fuera de «actualizar la lista»: es la default
          // de todo el negocio y reescribirla desde una partida es demasiado.
          const origen = c.origen ?? "";
          const actualizable = origen.startsWith("lista") && origen !== "lista_base";
          return {
            ...x,
            precioLista: resuelto,
            precioListaId: actualizable ? (c.lista_id ?? null) : null,
            precioTramo: actualizable ? (c.cantidad_minima ?? null) : null,
            ...(llenar ? { precio: resuelto, precioAuto: true } : {}),
          };
        })
      );
    } catch {
      /* cotización informativa: sin permiso o sin red, el campo queda como estaba */
    }
  }

  /** Escribe el precio tecleado en la lista de donde salió el de referencia.
   *  Es la mitad "y que el sistema lo aprenda" del precio manual: cobrar otra
   *  cosa es válido siempre; actualizar la lista es una DECISIÓN y por eso es
   *  un clic aparte, nunca automático. Actualiza el tramo base (desde 1). */
  async function actualizarLista(l: LineaEdit) {
    const v = precioNormalizado(l.precio);
    if (!v || !l.precioListaId || !l.producto_id || l.precioTramo == null) return;
    try {
      await apiFetch(`/api/v1/listas-precios/${l.precioListaId}/precios/bulk`, {
        method: "POST",
        body: JSON.stringify({
          items: [{
            producto_id: l.producto_id,
            presentacion: l.presentacion,
            precio_unitario: v,
            // El MISMO tramo del que salió la referencia: escribir el tramo
            // base con un precio de volumen sería un subcobro permanente.
            cantidad_minima: l.precioTramo,
          }],
        }),
      });
      const nuevo = Number(v).toFixed(2);
      toast.success(`La lista quedó en ${nuevo} para esta presentación`);
      setLineas((prev) =>
        prev.map((x) => (x.numero === l.numero ? { ...x, precioLista: nuevo } : x))
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo actualizar la lista");
    }
  }

  const sinProducto = useMemo(() => lineas.filter((l) => !l.producto_id).length, [lineas]);
  const bloqueada = !!oc && (!!oc.remision_id || oc.estado === "DESCARTADA");
  const auto = oc?.auto ?? null;

  /** Conflictos de precio que el operador decidió dejar como venían en el
   *  documento: se cobra lo del documento y el rojo se apaga. */
  const [precioAceptado, setPrecioAceptado] = useState<number[]>([]);

  /** Los problemas que el servidor detectó, por número de partida. */
  const problemasSrv = useMemo(() => {
    const m = new Map<number, NonNullable<typeof auto>["problemas"][number]>();
    for (const p of auto?.problemas ?? []) if (!m.has(p.numero)) m.set(p.numero, p);
    return m;
  }, [auto]);

  /** Cuántas partidas están marcadas en rojo ahora mismo: las que no cruzan más
   *  las que el servidor objetó y siguen sin resolverse en la tabla. */
  const partidasEnRojo = useMemo(
    () =>
      lineas.filter(
        (l) =>
          problemaDe(l) ||
          (!precioAceptado.includes(l.numero) && problemaVivo(problemasSrv.get(l.numero), l))
      ).length,
    [lineas, problemasSrv, precioAceptado]
  );

  /** ¿El operador corrigió una partida después de que el servidor evaluó `auto`?
   *  El atajo de un clic rearma las líneas desde su propia evaluación: la
   *  corrección se perdería en silencio y el cruce corregido se reforzaría. */
  const autoDesfasado = useMemo(() => {
    if (!auto?.ok) return false;
    if (auto.lineas.length !== lineas.length) return true;
    return auto.lineas.some((a) => {
      const l = lineas.find((x) => x.numero === a.numero);
      if (!l) return true;
      if (l.producto_id !== a.producto_id) return true;
      if (l.presentacion !== a.presentacion) return true;
      if (l.cantidad.trim() && Number(l.cantidad) !== Number(a.cantidad)) return true;
      const tecleado = precioNormalizado(l.precio);
      return !!tecleado && Number(tecleado) !== Number(a.precio_unitario);
    });
  }, [auto, lineas]);

  /** ¿La orden pasa las validaciones locales para volverse remisión? */
  function listaParaCrear(): boolean {
    if (!oc) return false;
    if (!clienteSel) { toast.error("Asigna primero el cliente"); return false; }
    if (!lineas.length || sinProducto) {
      // Con 63 renglones, «faltan 2» sin decir CUÁLES es una búsqueda a ojo:
      // se nombran y la vista salta a la primera. (El operador borró una sin
      // cruzar, el aviso le reclamó la otra, y no la encontraba.)
      const faltan = lineas.filter((l) => !l.producto_id);
      toast.error(
        faltan.length === 1
          ? `Falta cruzar la partida ${faltan[0].numero}: «${faltan[0].texto.slice(0, 40) || "agregada a mano"}»`
          : `Faltan ${faltan.length} por cruzar: ${faltan.slice(0, 3).map((x) => `la ${x.numero} «${x.texto.slice(0, 25)}»`).join(", ")}${faltan.length > 3 ? "…" : ""}`
      );
      irALinea(faltan[0].numero);
      return false;
    }
    const sinCantidad = lineas.filter((l) => !(Number(l.cantidad) > 0));
    if (sinCantidad.length) {
      toast.error(
        `Sin cantidad válida: ${sinCantidad.slice(0, 3).map((x) => `la ${x.numero} «${x.texto.slice(0, 25)}»`).join(", ")}${sinCantidad.length > 3 ? "…" : ""}`
      );
      irALinea(sinCantidad[0].numero);
      return false;
    }
    return true;
  }

  /** Lleva la vista al renglón de la partida — es la mitad útil del aviso. */
  function irALinea(numero: number) {
    document
      .querySelector(`[data-linea="${numero}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /** El clic en «Crear remisión»: si hay asignación sin guardar, primero se
   *  pregunta si además se APRENDE (equivalencias para las próximas órdenes);
   *  el modal llama a `crearRemision(aprender)`. Sin cambios, directo. */
  const [preguntaGuardar, setPreguntaGuardar] = useState(false);
  function clicCrear() {
    if (!listaParaCrear()) return;
    if (sinGuardar) setPreguntaGuardar(true);
    else void crearRemision(true);
  }

  async function crearRemision(aprender: boolean) {
    if (!oc || !listaParaCrear()) return;
    setPreguntaGuardar(false);
    setGuardando(true);
    try {
      // La remisión se crea con el cliente y la sucursal PERSISTIDOS: si el
      // operador los cambió, se guardan aquí — si no, saldría a nombre del
      // cliente anterior y con la serie equivocada. `aprender` viene de la
      // pregunta del modal.
      const reCruza =
        clienteSel !== (oc.cliente_id ?? "") || sucursalSel !== (oc.sucursal_id ?? "");
      if (sinGuardar && !(await guardarAsignacion(aprender))) return;
      if (reCruza) {
        // Con otro cliente/sucursal el servidor RECRUZÓ las partidas y la tabla
        // se rehizo: crear con la tabla anterior mandaría productos (y
        // aprendería claves) del cliente equivocado. Se guarda y se pide una
        // mirada antes del segundo clic.
        toast.info(
          "Asignación guardada. El cruce se recalculó para el cliente nuevo: revísalo y vuelve a crear."
        );
        return;
      }
      const d = await apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${oc.id}/crear-remision`, {
        method: "POST",
        body: JSON.stringify({
          almacen_id: almacenSel || null,
          lineas: lineas.map((l) => ({
            producto_id: l.producto_id,
            cantidad: l.cantidad,
            presentacion: l.presentacion,
            // El precio AUTO es informativo: se manda vacío y el backend
            // resuelve de la lista al crear (tramos por cantidad incluidos).
            // Solo lo tecleado viaja como precio explícito.
            precio_unitario: l.precioAuto ? null : precioNormalizado(l.precio) || null,
            notas: l.notas,
            texto_original: l.agregada ? null : l.texto || null,
            clave: l.agregada ? null : l.clave,
          })),
        }),
      });
      toast.success(`Remisión ${d.remision_folio ?? ""} creada en borrador`);
      router.push("/oc");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la remisión");
    } finally {
      setGuardando(false);
    }
  }

  /** La orden que cruzó COMPLETA por vías deterministas se vuelve remisión sin
   *  capturar nada. El backend revalida en ese instante y responde 409 con el
   *  motivo si algo dejó de cruzar. */
  async function crearRemisionAuto() {
    if (!oc) return;
    setGuardando(true);
    try {
      const d = await apiFetch<OCRecibidaDetalle>(
        `/api/v1/oc-recibidas/${oc.id}/crear-remision-auto` + (almacenSel ? `?almacen_id=${almacenSel}` : ""),
        { method: "POST" }
      );
      toast.success(`Remisión ${d.remision_folio ?? ""} creada en borrador`);
      router.push("/oc");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "La orden ya no cruza completa; revísala a mano");
      void cargar();
    } finally {
      setGuardando(false);
    }
  }

  async function descartar() {
    if (!oc) return;
    try {
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}/descartar`, { method: "POST" });
      toast.success("Descartada");
      router.push("/oc");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descartar");
    } finally {
      setConfirmaDescartar(false);
    }
  }

  async function reabrir() {
    if (!oc) return;
    try {
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}/reabrir`, { method: "POST" });
      toast.success("Reabierta");
      void cargar();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo reabrir");
    }
  }

  if (noEncontrada) return <Alert tone="danger">Esa orden no existe (¿se borró?). <button className="underline" onClick={() => router.push("/oc")}>Volver a la bandeja</button></Alert>;
  if (!oc) return <div className="flex justify-center py-16"><Spinner /></div>;

  const badge = estadoTexto(oc);
  const editable = canWrite && !bloqueada;

  return (
    <div className="pb-24">
      <PageHeader
        title={`OC ${oc.folio_externo || "sin folio"}`}
        subtitle={oc.remitente ?? undefined}
        actions={
          <div className="flex items-center gap-2">
            <Badge tone={CANAL_TONE[oc.canal] ?? "default"}>{oc.canal}</Badge>
            <Badge tone={badge.tone}>{badge.texto}</Badge>
            {oc.archivo_url ? (
              <a href={oc.archivo_url} target="_blank" rel="noreferrer"
                 className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-sm hover:bg-surface-2">
                <ExternalLink size={15} /> Documento original
              </a>
            ) : null}
            <Button variant="secondary" onClick={() => router.push("/oc")}>
              <ArrowLeft size={15} /> Bandeja
            </Button>
          </div>
        }
      />

      <div className="space-y-5">
        {oc.estado === "DESCARTADA" ? (
          <Alert tone="warning">
            Orden descartada. Reábrela para poder asignarla — asignar desde aquí registraría
            equivalencias de un documento que ya se dio por bueno descartar.
          </Alert>
        ) : null}
        {oc.ambiguo ? (
          <Alert tone="warning">
            <span className="inline-flex items-center gap-1.5"><AlertTriangle size={15} /> {oc.motivo}</span>
          </Alert>
        ) : oc.candidatos?.length && !oc.cliente_id ? (
          <Alert tone="info">{oc.motivo}</Alert>
        ) : null}
        {oc.remision_id ? (
          <Alert tone="success">
            Esta orden ya generó la remisión <strong>{oc.remision_folio}</strong>. Los cambios de
            kilos, líneas y precios se hacen en la remisión.
          </Alert>
        ) : null}

        {auto?.ok && autoDesfasado && !bloqueada ? (
          <p className="text-sm text-muted">
            Corregiste una partida: se crea con «Crear remisión», que respeta lo que acabas de
            cambiar. El atajo de un clic reharía el cruce desde cero.
          </p>
        ) : auto?.ok && !bloqueada ? (
          <Alert tone="success">
            <div className="font-medium">
              Lista para remisión: las {auto.lineas.length} partidas cruzaron por clave o
              vocabulario aprendido, y todas traen precio de la lista del cliente.
            </div>
            {sinGuardar ? (
              <div className="mt-2 text-xs">
                Cambiaste la asignación: esto se calculó con el cliente que ya estaba. Al crear se guarda y recalcula.
              </div>
            ) : null}
          </Alert>
        ) : auto?.motivo && !bloqueada && oc.cliente_id ? (
          <p className="text-sm text-muted">
            Revisión a mano:{" "}
            {auto.problemas?.length > 1
              ? `${auto.problemas.length} partidas necesitan una decisión.`
              : auto.motivo}{" "}
            {partidasEnRojo ? "Están marcadas en rojo abajo." : "Ya las resolviste en la tabla."}
          </p>
        ) : null}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <Field
            label="Cliente"
            hint={oc.candidatos?.length && !verTodos ? "Los que usan este grupo de WhatsApp" : "A quién se le factura"}
          >
            <Select
              value={clienteSel}
              onChange={(e) => {
                if (e.target.value === "__todos__") { setVerTodos(true); return; }
                setClienteSel(e.target.value);
                setSucursalSel("");
              }}
              disabled={!editable}
            >
              <option value="">— Elegir —</option>
              {(oc.candidatos?.length && !verTodos
                ? clientes.filter((c) => oc.candidatos.includes(c.id))
                : clientes
              ).map((c) => (
                <option key={c.id} value={c.id}>{c.legal_name}</option>
              ))}
              {oc.candidatos?.length && !verTodos ? (
                <option value="__todos__">Ver todos los clientes…</option>
              ) : null}
            </Select>
          </Field>
          <Field label="Sucursal" hint="De aquí salen la serie y el almacén">
            <Select value={sucursalSel} onChange={(e) => setSucursalSel(e.target.value)} disabled={!editable || !clienteSel}>
              <option value="">— Sin sucursal —</option>
              {sucursales.map((s) => (
                <option key={s.id} value={s.id}>{s.codigo ? `${s.codigo} · ${s.nombre}` : s.nombre}</option>
              ))}
            </Select>
          </Field>
          <Field label="Punto de entrega" hint="A dónde se descarga. Sale impreso en la remisión y en la factura.">
            <Input value={puntoEntrega} onChange={(e) => setPuntoEntrega(e.target.value)} disabled={!editable} placeholder="HOSPITAL JUAN GRAHAM" />
          </Field>
          <Field label="Proyecto" hint="La negociación bajo la que entra: decide qué lista de precios se cobra.">
            <Select value={proyectoSel} onChange={(e) => setProyectoSel(e.target.value)} disabled={!editable}>
              <option value="">— Sin proyecto —</option>
              {proyectos
                .filter((p) => !p.cliente_id || !clienteSel || p.cliente_id === clienteSel)
                // La negociación es de su plaza: los proyectos con sucursales
                // asignadas solo se ofrecen cuando la sucursal elegida está en
                // su alcance. El ya seleccionado se queda visible aunque no
                // aplique — ocultarlo dejaría al select con un valor fantasma.
                .filter(
                  (p) =>
                    p.id === proyectoSel ||
                    !p.sucursal_ids?.length ||
                    (sucursalSel !== "" && p.sucursal_ids.includes(sucursalSel))
                )
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id === proyectoSel && proyectoFueraDePlaza
                      ? `${p.nombre} (no entrega en esta sucursal)`
                      : p.nombre}
                  </option>
                ))}
            </Select>
          </Field>
          <Field label="Almacén de salida">
            <Select value={almacenSel} onChange={(e) => setAlmacenSel(e.target.value)} disabled={!editable}>
              <option value="">— Sin almacén —</option>
              {almacenes.map((a) => (
                <option key={a.id} value={a.id}>{a.nombre}</option>
              ))}
            </Select>
          </Field>
        </div>

        {proyectoFueraDePlaza ? (
          <Alert tone="warning">
            {sucursalSel
              ? `El proyecto «${proyectoFueraDePlaza.nombre}» no entrega en la sucursal elegida: guardar así va a fallar. Quítalo, cambia la sucursal, o amplía su alcance en Catálogos → Proyectos.`
              : `El proyecto «${proyectoFueraDePlaza.nombre}» solo entrega en ciertas sucursales: elige primero la sucursal de la orden.`}
          </Alert>
        ) : null}

        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Partidas del documento</h3>
            {partidasEnRojo ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-danger">
                <AlertTriangle size={13} />
                {partidasEnRojo === 1 ? "1 partida en rojo" : `${partidasEnRojo} partidas en rojo`}
                {sinProducto ? " — sin cruzar no hay remisión" : " — revísalas antes de crear"}
              </span>
            ) : null}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border bg-surface">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 text-left">Como venía en la orden</th>
                  <th className="px-3 py-2 text-right">Cantidad</th>
                  <th className="px-3 py-2 text-left">Producto del catálogo</th>
                  <th className="px-3 py-2 text-left">Presentación</th>
                  <th className="px-3 py-2 text-right">Precio</th>
                  {editable ? <th className="w-1 px-1 py-2" /> : null}
                </tr>
              </thead>
              <tbody>
                {lineas.map((l, i) => {
                  const problema = problemaDe(l);
                  // Lo que dijo el servidor, ya contrastado con lo que hay
                  // tecleado ahora — y sin lo que el operador dio por bueno.
                  const srv = precioAceptado.includes(l.numero)
                    ? null
                    : problemaVivo(problemasSrv.get(l.numero), l);
                  const conflicto = srv?.tipo === "precio_conflicto" ? srv : null;
                  const enRojo = !!problema || !!srv;
                  return (
                  <tr
                    key={l.numero}
                    data-linea={l.numero}
                    className={`border-t border-border${enRojo ? " bg-danger/5" : ""}`}
                  >
                    <td className={`px-3 py-2${enRojo ? " border-l-2 border-l-danger" : ""}`}>
                      {l.agregada ? (
                        <span className="text-xs italic text-muted">Agregada a mano — no venía en el documento</span>
                      ) : (
                        l.texto
                      )}
                      {l.clave ? (
                        <div className="mt-0.5 text-xs text-muted">
                          Su clave: <span className="tabular-nums">{l.clave}</span>
                        </div>
                      ) : null}
                      {problema ? (
                        <div className="mt-1 flex items-start gap-1.5 text-xs text-danger">
                          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                          <span>
                            <span className="font-medium">{problema.corto}.</span>{" "}
                            {problema.comoResolver}
                          </span>
                        </div>
                      ) : srv ? (
                        <div className="mt-1 flex items-start gap-1.5 text-xs text-danger">
                          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                          {/* El texto va en la columna ANCHA; en la de precio
                              solo caben las dos salidas. Puesto ahí, un renglón
                              con conflicto medía cinco líneas de alto. */}
                          <span>
                            {conflicto ? (
                              <>
                                <span className="font-medium">Precio en conflicto.</span> El
                                documento trae {conflicto.precio_documento} y{" "}
                                {conflicto.fuente_precio ?? "la lista"} dice{" "}
                                {conflicto.precio_lista}.
                              </>
                            ) : (
                              srv.mensaje
                            )}
                          </span>
                        </div>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {editable ? (
                        <div className="flex items-center justify-end gap-1">
                          <div className="w-20">
                            <Input
                              value={l.cantidad}
                              inputMode="decimal"
                              className="text-right tabular-nums"
                              onChange={(e) => {
                                const v = e.target.value;
                                setLineas((prev) => prev.map((x, j) => (j === i ? { ...x, cantidad: v } : x)));
                                // El tramo por volumen depende de la cantidad;
                                // el guard de secuencia descarta las respuestas
                                // intermedias del tecleo.
                                if (l.producto_id) void cotizarLinea(l.numero, l.producto_id, l.presentacion, v);
                              }}
                            />
                          </div>
                          {l.unidad ? <span className="text-xs text-muted">{l.unidad}</span> : null}
                        </div>
                      ) : (
                        <span className="tabular-nums">
                          {l.cantidad}
                          {l.unidad ? <span className="ml-1 text-xs text-muted">{l.unidad}</span> : null}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {l.candidatos.length && !l.buscando ? (
                        <Select
                          value={l.producto_id}
                          disabled={!editable}
                          className={l.producto_id ? "" : "border-danger"}
                          onChange={(e) => {
                            const v = e.target.value;
                            if (v === "__buscar__") {
                              setLineas((prev) =>
                                prev.map((x, j) =>
                                  j === i
                                    ? {
                                        ...x, buscando: true, producto_id: "",
                                        precioLista: null, precioListaId: null, precioTramo: null,
                                        ...(x.precioAuto ? { precio: "", precioAuto: false } : {}),
                                      }
                                    : x
                                )
                              );
                              return;
                            }
                            const cand = l.candidatos.find((c) => c.producto_id === v);
                            const pres = presentacionDe(l.unidad, cand);
                            setLineas((prev) =>
                              prev.map((x, j) => {
                                if (j !== i) return x;
                                // Cambiar de producto invalida el precio de la
                                // cotización anterior YA (no cuando responda la
                                // nueva): si falla, no queda un número ajeno.
                                // El precio TECLEADO se conserva.
                                return {
                                  ...x,
                                  producto_id: v,
                                  presentacion: pres,
                                  presentaciones: Object.keys(cand?.presentaciones ?? {}),
                                  precioLista: null,
                                  precioListaId: null,
                                  precioTramo: null,
                                  ...(x.precioAuto ? { precio: "", precioAuto: false } : {}),
                                };
                              })
                            );
                            if (v) void cotizarLinea(l.numero, v, pres, l.cantidad);
                          }}
                        >
                          <option value="">— Sin cruzar —</option>
                          {l.candidatos.map((c) => (
                            <option key={c.producto_id} value={c.producto_id}>
                              {c.nombre} ({c.sku}) · {ORIGEN_CRUCE[c.origen] ?? `${c.score}%`}
                            </option>
                          ))}
                          <option value="__buscar__">Buscar otro producto…</option>
                        </Select>
                      ) : (
                        <ProductoCombobox
                          label={l.texto}
                          placeholder="Buscar en el catálogo…"
                          // El alta que sale de aquí ya sabe de quién es la
                          // orden: puede dejar el precio en su lista de una vez.
                          clienteId={clienteSel || null}
                          unidadBase={unidadBaseDe(l.unidad)}
                          presentacion={l.presentacion}
                          onSelect={(prod) => {
                            const pres = prod
                              ? presentacionDe(l.unidad, {
                                  producto_id: prod.producto_id,
                                  sku: prod.sku,
                                  nombre: prod.nombre,
                                  score: 100,
                                  origen: "manual",
                                  presentaciones: prod.presentaciones,
                                  presentacion_default: prod.presentacion_default,
                                })
                              : l.presentacion;
                            setLineas((prev) =>
                              prev.map((x, j) => {
                                if (j !== i) return x;
                                return {
                                  ...x,
                                  producto_id: prod ? prod.producto_id : "",
                                  presentacion: pres,
                                  presentaciones: prod ? Object.keys(prod.presentaciones ?? {}) : [],
                                  precioLista: null,
                                  precioListaId: null,
                                  precioTramo: null,
                                  ...(x.precioAuto ? { precio: "", precioAuto: false } : {}),
                                };
                              })
                            );
                            if (prod) void cotizarLinea(l.numero, prod.producto_id, pres, l.cantidad);
                          }}
                        />
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {editable && l.presentaciones.length > 1 ? (
                        <Select
                          value={l.presentacion}
                          onChange={(e) => {
                            const v = e.target.value;
                            setLineas((prev) => prev.map((x, j) => (j === i ? { ...x, presentacion: v } : x)));
                            // Otra presentación, otro precio de lista.
                            if (l.producto_id) void cotizarLinea(l.numero, l.producto_id, v, l.cantidad);
                          }}
                        >
                          {l.presentaciones.map((p) => (
                            <option key={p} value={p}>{p}</option>
                          ))}
                        </Select>
                      ) : (
                        <span className="text-xs text-muted">{l.presentacion}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Input
                        value={l.precio}
                        disabled={!editable}
                        placeholder="lista"
                        className={`text-right${conflicto ? " border-danger" : ""}`}
                        onChange={(e) => {
                          const v = e.target.value;
                          setLineas((prev) => prev.map((x, j) => (j === i ? { ...x, precio: v, precioAuto: false } : x)));
                        }}
                      />
                      {l.precioAuto && !conflicto ? (
                        <div className="mt-1 text-right text-xs text-muted">precio de lista</div>
                      ) : null}
                      {conflicto && editable ? (
                        <div className="mt-1 flex flex-wrap justify-end gap-x-3 gap-y-1 text-xs">
                          <button
                            type="button"
                            className="text-accent hover:underline"
                            onClick={() =>
                              setLineas((prev) =>
                                prev.map((x, j) =>
                                  j === i
                                    ? { ...x, precio: conflicto.precio_lista ?? "", precioAuto: false }
                                    : x
                                )
                              )
                            }
                          >
                            Cobrar {conflicto.precio_lista}
                          </button>
                          <button
                            type="button"
                            className="text-accent hover:underline"
                            onClick={() => setPrecioAceptado((prev) => [...prev, l.numero])}
                          >
                            Dejar {conflicto.precio_documento}
                          </button>
                        </div>
                      ) : null}
                      {(() => {
                        // Precio tecleado distinto del de la lista: se acepta
                        // siempre, y se OFRECE llevarlo a la lista — decisión
                        // aparte, nunca automática. Con el conflicto del
                        // servidor visible no se duplica el aviso.
                        if (conflicto || !editable || l.precioAuto || !l.precioLista) return null;
                        const v = Number(precioNormalizado(l.precio));
                        if (!Number.isFinite(v) || !l.precio.trim()) return null;
                        if (Math.abs(v - Number(l.precioLista)) <= 0.01) return null;
                        const puedeActualizar =
                          l.precioListaId && l.precioTramo != null && can(me, "lista_precios:gestionar");
                        return (
                          <div className="mt-1 text-right text-xs text-muted">
                            La lista dice {l.precioLista}.{" "}
                            {puedeActualizar ? (
                              <button
                                type="button"
                                className="text-accent hover:underline"
                                onClick={() => void actualizarLista(l)}
                              >
                                Actualizar la lista a {v.toFixed(2)}
                                {l.precioTramo && l.precioTramo > 1 ? ` (tramo desde ${l.precioTramo})` : ""}
                              </button>
                            ) : null}
                          </div>
                        );
                      })()}
                    </td>
                    {editable ? (
                      <td className="px-1 py-2">
                        <button
                          type="button"
                          onClick={() => setLineas((prev) => prev.filter((_, j) => j !== i))}
                          className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                          aria-label="Quitar esta partida"
                          title="Quitar esta partida de la remisión (el documento no cambia)"
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    ) : null}
                  </tr>
                  );
                })}
                {!lineas.length ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-sm text-muted">
                      El documento no trae partidas legibles.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex items-start justify-between gap-4">
            <p className="text-xs text-muted">
              El precio vacío lo resuelve la lista del cliente. Quitar o agregar aquí decide qué
              lleva la remisión — el documento original no se toca.
            </p>
            {editable ? (
              <Button
                variant="secondary"
                onClick={() =>
                  setLineas((prev) => [
                    ...prev,
                    {
                      numero: Math.max(0, ...prev.map((x) => x.numero)) + 1000,
                      texto: "", cantidad: "", unidad: "", clave: null,
                      producto_id: "", presentacion: "KILO", presentaciones: [],
                      precio: "", precioLista: null, precioListaId: null, precioTramo: null, precioAuto: false,
                      notas: null, candidatos: [], buscando: true, agregada: true,
                    },
                  ])
                }
              >
                Agregar partida
              </Button>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
          <span className="inline-flex items-center gap-1"><Inbox size={13} /> {oc.origen_externo}</span>
          {oc.archivo_nombre ? (
            <span className="inline-flex items-center gap-1">
              <FileText size={13} />
              {oc.archivo_url ? (
                <a href={oc.archivo_url} target="_blank" rel="noreferrer" className="hover:underline">{oc.archivo_nombre}</a>
              ) : (
                oc.archivo_nombre
              )}
            </span>
          ) : null}
          {oc.resuelto_via ? <span>Resuelto por {oc.resuelto_via}</span> : null}
          <span>
            Recibida {new Date(oc.recibida_at).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" })}
          </span>
        </div>
      </div>

      {/* Barra de acciones fija: siempre a la vista aunque la orden traiga 40 partidas. */}
      <div className="fixed inset-x-0 bottom-0 z-10 border-t border-border bg-surface px-6 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.06)]">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-end gap-2">
          {canWrite && oc.estado === "DESCARTADA" && !oc.remision_id ? (
            <Button onClick={() => { void reabrir(); }}>Reabrir</Button>
          ) : null}
          {canWrite && !bloqueada ? (
            <>
              <Button variant="danger" onClick={() => setConfirmaDescartar(true)} disabled={guardando}>
                Descartar
              </Button>
              {auto?.ok && !autoDesfasado ? (
                <Button
                  variant="secondary"
                  onClick={() => { void crearRemisionAuto(); }}
                  disabled={guardando || sinGuardar}
                  title={sinGuardar ? "Cambiaste la asignación: crea con «Crear remisión», que la guarda" : undefined}
                >
                  Crear remisión de un clic
                </Button>
              ) : null}
              <Button onClick={clicCrear} disabled={guardando}>
                {guardando ? "Creando…" : "Crear remisión"}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={confirmaDescartar}
        title="Descartar la orden"
        message={`¿Descartar la OC ${oc.folio_externo ?? ""}? No se crea ninguna remisión. Se puede reabrir después.`}
        onClose={() => setConfirmaDescartar(false)}
        onConfirm={descartar}
      />

      <Modal
        open={preguntaGuardar}
        onClose={() => setPreguntaGuardar(false)}
        title="¿Guardar la asignación?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPreguntaGuardar(false)} disabled={guardando}>
              Cancelar
            </Button>
            <Button variant="secondary" onClick={() => { void crearRemision(false); }} disabled={guardando}>
              No — solo esta remisión
            </Button>
            <Button onClick={() => { void crearRemision(true); }} disabled={guardando}>
              Sí, guardar y crear
            </Button>
          </>
        }
      >
        <p className="text-sm">
          Cambiaste cliente, sucursal, punto de entrega o proyecto. Con <strong>Sí</strong>, el
          sistema lo aprende y las próximas órdenes iguales se resuelven solas. Con{" "}
          <strong>No</strong>, el cambio aplica a esta remisión sin enseñarle nada al sistema.
          En ambos casos la remisión se crea ahora.
        </p>
      </Modal>
    </div>
  );
}

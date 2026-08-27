"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ClipboardPaste, Plus, Sparkles, Trash2, X } from "lucide-react";

import { KeyboardCombobox, type ComboOption } from "@/components/KeyboardCombobox";
import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { CrearProductoModal, type ProductoCreado } from "@/components/CrearProductoModal";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { LoadingDots } from "@/components/ui/LoadingDots";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import {
  NUEVO_PRODUCTO, fetchFiscalPreview, lineaDesdePegado, matchPresentacion, norm,
  nuevaLinea, pegarLocalFallback, unidadBaseDesde,
  type FiscalPreview, type LineaForm,
} from "@/lib/lineas";
import {
  FORMA_PAGO_FALLBACK, FORMA_PAGO_OPTS, METODO_PAGO_FALLBACK, METODO_PAGO_OPTS,
  USO_CFDI_FALLBACK, USO_CFDI_OPTS,
} from "@/lib/sat";
import type {
  Almacen, Cliente, FacturaDetail, LineaPegada, MatchResult, Producto, Serie,
} from "@/lib/types";

type Props = {
  ambiente: string;
  onClose: () => void;                       // volver al listado
  onSaved: (f: FacturaDetail) => void;       // recargar la lista
};

/**
 * Alta de "factura directa": captura una factura sin remisión previa.
 * Comparte con Remisiones el editor de líneas (pegado de Excel, Match IA y alta
 * de producto al vuelo) vía `lib/lineas`, pero NO sus acciones: aquí se guarda
 * un BORRADOR o se timbra ante el PAC.
 *
 * Diferencias que impone el backend (POST /facturas/directa):
 *  - `almacen_id` es obligatorio: de ahí sale el inventario al timbrar.
 *  - `precio_unitario` es obligatorio por línea (en remisiones el backend lo
 *    resuelve solo si va vacío; aquí no).
 *  - No hay sucursal ni fecha: la factura toma la fecha del timbrado.
 */
export function FacturaDirectaForm({ ambiente, onClose, onSaved }: Props) {
  const toast = useToast();
  const { post, loading: saving } = useMutation();

  // catálogos
  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=200");
  const almacenesRes = useResource<Page<Almacen>>("/api/v1/almacenes?limit=200");
  const productosRes = useResource<Page<Producto>>("/api/v1/productos?limit=1000");
  const seriesRes = useResource<Page<Serie>>("/api/v1/series?tipo_documento=FACTURA&activa=true&limit=200");
  const clientes = clientesRes.data?.items ?? [];
  const almacenes = almacenesRes.data?.items ?? [];
  const productos = productosRes.data?.items ?? [];
  const series = seriesRes.data?.items ?? [];
  const prodById = useMemo(() => Object.fromEntries(productos.map((p) => [p.id, p])), [productos]);

  // cabecera
  const [clienteId, setClienteId] = useState("");
  const [almacenId, setAlmacenId] = useState("");
  const [serieOverride, setSerieOverride] = useState("");
  const [usoCfdi, setUsoCfdi] = useState(USO_CFDI_FALLBACK);
  const [formaPago, setFormaPago] = useState(FORMA_PAGO_FALLBACK);
  const [metodoPago, setMetodoPago] = useState(METODO_PAGO_FALLBACK);
  const [notas, setNotas] = useState("");
  const [lineas, setLineas] = useState<LineaForm[]>([nuevaLinea()]);
  // paso del flujo por teclado: cuál caja se auto-abre/enfoca
  const [step, setStep] = useState<"cliente" | "almacen" | null>("cliente");

  // Un solo almacén → se elige solo (el selector sigue visible y editable).
  useEffect(() => {
    if (almacenes.length === 1 && !almacenId) setAlmacenId(almacenes[0].id);
  }, [almacenes, almacenId]);

  // Catálogo del cliente: producto → SU presentación (con la que compra). Al
  // elegir un producto se preselecciona su unidad ("Cilantro" → MANOJO) y la
  // cotización sale con SU precio de esa presentación.
  const [presCliente, setPresCliente] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!clienteId) { setPresCliente({}); return; }
    let active = true;
    apiFetch<{ producto_id: string; presentacion?: string | null }[]>(
      `/api/v1/clientes/${clienteId}/catalogo`
    )
      .then((rows) => {
        if (!active) return;
        setPresCliente(Object.fromEntries(
          rows.filter((r) => r.presentacion).map((r) => [r.producto_id, r.presentacion as string])
        ));
      })
      .catch(() => { if (active) setPresCliente({}); });
    return () => { active = false; };
  }, [clienteId]);

  // Preview de la serie/folio que aplicaría (override → cliente → default).
  const [serieResuelta, setSerieResuelta] = useState<Serie | null>(null);
  useEffect(() => {
    if (!clienteId) { setSerieResuelta(null); return; }
    let active = true; // evita que una respuesta vieja pise a la del cliente/serie actual
    const p = new URLSearchParams({ tipo_documento: "FACTURA", cliente_id: clienteId });
    if (serieOverride) p.set("serie_id", serieOverride);
    apiFetch<Serie | null>(`/api/v1/series/resolver?${p.toString()}`)
      .then((s) => { if (active) setSerieResuelta(s); })
      .catch(() => { if (active) setSerieResuelta(null); });
    return () => { active = false; };
  }, [clienteId, serieOverride]);

  const folioPreview = serieResuelta ? `${serieResuelta.codigo}${serieResuelta.folio_actual + 1}` : "—";

  // Totales calculados por el SERVIDOR (regla "el backend calcula todo"):
  // mismo cerebro fiscal que guarda la factura; debounce + secuencia anti-race.
  const [fiscalPreview, setFiscalPreview] = useState<FiscalPreview | null>(null);
  const fiscalSeq = useRef(0);
  useEffect(() => {
    const seq = ++fiscalSeq.current;
    const t = setTimeout(() => {
      fetchFiscalPreview(lineas)
        .then((p) => { if (seq === fiscalSeq.current) setFiscalPreview(p); })
        .catch(() => { if (seq === fiscalSeq.current) setFiscalPreview(null); });
    }, 300);
    return () => clearTimeout(t);
  }, [lineas]);

  const subtotalPreview = fiscalPreview?.subtotal ?? lineas.reduce((s, l) => s + (l.importe || 0), 0);
  const iepsPreview = fiscalPreview?.ieps ?? 0;
  const ivaPreview = fiscalPreview?.iva ?? 0;
  const totalPreview = fiscalPreview?.total ?? subtotalPreview;

  const clienteOpts: ComboOption[] = useMemo(() => clientes.map((c) => ({ value: c.id, label: c.legal_name })), [clientes]);
  const almacenOpts: ComboOption[] = useMemo(() => almacenes.map((a) => ({ value: a.id, label: a.nombre })), [almacenes]);
  const serieOpts: ComboOption[] = useMemo(
    () => [
      { value: "", label: `Automática${serieResuelta ? ` · ${serieResuelta.codigo}` : ""}` },
      ...series.map((s) => ({ value: s.id, label: `${s.codigo}${s.nombre ? ` · ${s.nombre}` : ""}` })),
    ],
    [series, serieResuelta],
  );

  // Al elegir cliente se adoptan sus defaults fiscales y su serie; el foco pasa
  // al almacén (o directo a las líneas si solo hay uno).
  function selectCliente(v: string) {
    setClienteId(v);
    const cli = clientes.find((c) => c.id === v);
    setSerieOverride(cli?.serie_factura_id ?? "");
    setUsoCfdi(cli?.uso_cfdi_default ?? USO_CFDI_FALLBACK);
    setFormaPago(cli?.forma_pago_default ?? FORMA_PAGO_FALLBACK);
    setMetodoPago(cli?.metodo_pago_default ?? METODO_PAGO_FALLBACK);
    // Re-cotiza las líneas ya capturadas con la lista del nuevo cliente (si se
    // capturaron sin cliente, o al cambiar de cliente, traían el precio de
    // lista genérico y así se timbraría). Los precios manuales se respetan.
    lineas.forEach((l) => {
      if (l.producto_id && !l.precioManual) cotizar(l.key, l.producto_id, l.presentacion, l.cantidad, v);
    });
    if (almacenes.length > 1) { setStep("almacen"); return; }
    setStep(null);
    setLineFocus({ key: lineas[0]?.key, field: "cantidad" });
  }

  function resetForm() {
    setClienteId(""); setSerieOverride(""); setNotas("");
    setUsoCfdi(USO_CFDI_FALLBACK); setFormaPago(FORMA_PAGO_FALLBACK); setMetodoPago(METODO_PAGO_FALLBACK);
    setAlmacenId(almacenes.length === 1 ? almacenes[0].id : "");
    setLineas([nuevaLinea()]);
    setStep("cliente");
  }

  function setLinea(key: string, patch: Partial<LineaForm>) {
    setLineas((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }

  // ── foco encadenado dentro de las líneas ──
  type LineField = "producto" | "presentacion" | "cantidad" | "precio";
  const [lineFocus, setLineFocus] = useState<{ key: string; field: LineField } | null>(null);
  const cellRefs = useRef<Record<string, HTMLInputElement | null>>({});
  useEffect(() => {
    if (!lineFocus) return;
    if (lineFocus.field === "cantidad" || lineFocus.field === "precio") {
      const el = cellRefs.current[`${lineFocus.key}:${lineFocus.field}`];
      el?.focus();
      el?.select();
    }
  }, [lineFocus]);

  function advanceLine(key: string, from: LineField) {
    // Secuencia: Cantidad → Producto → Presentación → Precio → (siguiente línea).
    if (from === "cantidad") return setLineFocus({ key, field: "producto" });
    if (from === "producto") return setLineFocus({ key, field: "presentacion" });
    if (from === "presentacion") return setLineFocus({ key, field: "precio" });
    const idx = lineas.findIndex((l) => l.key === key);
    if (idx === lineas.length - 1) {
      const nl = nuevaLinea();
      setLineas((ls) => [...ls, nl]);
      return setLineFocus({ key: nl.key, field: "cantidad" });
    }
    return setLineFocus({ key: lineas[idx + 1].key, field: "cantidad" });
  }

  // Secuencia por línea para descartar cotizaciones obsoletas: con precio por
  // volumen, al teclear "15" salen dos requests y la respuesta de "1" puede
  // llegar al final → quedaría (y se timbraría) el precio del tier equivocado.
  const cotizaSeq = useRef<Record<string, number>>({});

  // Cotiza el precio de lista del cliente. Sin sucursal: la factura directa no
  // la tiene, así que el precio sale de la lista del cliente / del producto.
  // `cliente` permite cotizar con un cliente recién elegido antes de que el
  // estado `clienteId` se actualice.
  async function cotizar(key: string, producto_id: string, presentacion: string, cantidad: string, cliente: string = clienteId) {
    if (!producto_id || !Number(cantidad)) return;
    const seq = (cotizaSeq.current[key] ?? 0) + 1;
    cotizaSeq.current[key] = seq;
    try {
      const p = new URLSearchParams({ producto_id, presentacion, cantidad });
      if (cliente) p.set("cliente_id", cliente);
      const r = await apiFetch<{ precio: string | null }>(`/api/v1/precios/cotizar?${p.toString()}`);
      if (cotizaSeq.current[key] !== seq) return; // ya salió una cotización más nueva para esta línea
      setLineas((ls) =>
        ls.map((l) => {
          if (l.key !== key) return l;
          if (l.precioManual) return { ...l, importe: Number(l.precio || 0) * Number(cantidad) };
          const precio = r.precio ?? "";
          return { ...l, precio: precio === "" ? "" : String(precio), importe: Number(precio || 0) * Number(cantidad) };
        }),
      );
    } catch {
      /* sin precio de lista: el usuario lo captura a mano (el backend lo exige) */
    }
  }

  function onPickProducto(key: string, pick: ProductoPick | null, texto: string) {
    if (!pick) {
      // Sin producto no hay precio: se limpia para no inflar los totales con
      // el importe de la selección anterior.
      setLinea(key, { producto_id: "", label: "", texto, precio: "", precioManual: false, importe: 0 });
      return;
    }
    const pres = Object.keys(pick.presentaciones ?? {});
    const def = pick.presentacion_default ?? pick.unidad_base ?? pres[0] ?? "PIEZA";
    // La presentación del CLIENTE (su catálogo) gana sobre la default del producto.
    const presCli = presCliente[pick.producto_id];
    const presentacion = presCli && pres.includes(presCli)
      ? presCli
      : pres.includes(def) ? def : pres[0] ?? def;
    setLinea(key, { producto_id: pick.producto_id, label: pick.nombre, texto, presentaciones: pres, presentacion });
    const ln = lineas.find((l) => l.key === key);
    cotizar(key, pick.producto_id, presentacion, ln?.cantidad ?? "1");
    setLineFocus({ key, field: "presentacion" });
  }

  // ── pegar como Excel ──
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [procesando, setProcesando] = useState(false);
  const [crearParaKey, setCrearParaKey] = useState<string | null>(null);

  function cerrarPaste() {
    setPasteOpen(false);
    setPasteText("");
  }

  async function procesarPaste() {
    const raw = pasteText;
    if (!raw.trim()) return;
    setProcesando(true);
    try {
      let filas: LineaPegada[] = [];
      try {
        filas = await apiFetch("/api/v1/productos/parse-pegado", {
          method: "POST",
          body: JSON.stringify({ texto: raw, usar_ia: true }),
        });
      } catch {
        filas = await pegarLocalFallback(raw); // backend sin el endpoint nuevo
      }
      if (filas.length === 0) { toast.error("No se detectaron líneas en el texto pegado"); return; }
      const nuevas = filas.map(lineaDesdePegado);
      setLineas((ls) => {
        const base = ls.filter((l) => l.producto_id || l.texto);
        return [...base, ...nuevas];
      });
      nuevas.forEach((l) => l.producto_id && cotizar(l.key, l.producto_id, l.presentacion, l.cantidad));
      const porRevisar = nuevas.filter((l) => !l.producto_id).length;
      cerrarPaste();
      toast.success(`${nuevas.length} líneas agregadas` + (porRevisar ? ` · ${porRevisar} por revisar en Match IA` : ""));
    } finally {
      setProcesando(false);
    }
  }

  function resolverMatchIA(key: string, sel: string) {
    const l = lineas.find((x) => x.key === key);
    if (!l) return;
    if (sel === NUEVO_PRODUCTO) {
      setLinea(key, { producto_id: "", label: "", presentaciones: [], presentacion: l.presPegada || l.presentacion, importe: 0 });
      setCrearParaKey(key);
      return;
    }
    const cand = (l.candidatos ?? []).find((c) => c.producto_id === sel);
    if (!cand) return;
    // Aprende el alias cuando el usuario confirma un cruce no exacto.
    if (cand.origen !== "exacto" && norm(l.texto) !== norm(cand.nombre)) {
      apiFetch("/api/v1/productos/alias", {
        method: "POST",
        body: JSON.stringify({ texto: l.texto, producto_id: cand.producto_id }),
      }).catch(() => {});
    }
    const presKeys = Object.keys(cand.presentaciones ?? {});
    const presentacion = matchPresentacion(l.presPegada ?? l.presentacion, cand);
    setLinea(key, { producto_id: cand.producto_id, label: cand.nombre, presentaciones: presKeys, presentacion });
    cotizar(key, cand.producto_id, presentacion, l.cantidad);
  }

  function aplicarProductoCreado(prod: ProductoCreado) {
    const key = crearParaKey;
    if (!key) return;
    const l = lineas.find((x) => x.key === key);
    const presKeys = Object.keys(prod.presentaciones ?? {});
    const presentacion = prod.presentacion_default ?? prod.unidad_base ?? presKeys[0] ?? (l?.presPegada || "PIEZA");
    setLinea(key, { producto_id: prod.id, label: prod.nombre, presentaciones: presKeys, presentacion });
    if (l) cotizar(key, prod.id, presentacion, l.cantidad);
    setCrearParaKey(null);
  }

  const showMatchIA = lineas.some((l) => l.fromPaste);
  const lineaCrear = crearParaKey ? lineas.find((l) => l.key === crearParaKey) : undefined;

  // ── pegar una columna directo en la celda (como Excel) ──
  function pegarColumnaCantidad(key: string, text: string) {
    const valores = text.split("\n").map((v) => v.split("\t")[0].trim()).filter(Boolean);
    if (valores.length === 0) return;
    const idx = lineas.findIndex((l) => l.key === key);
    if (idx < 0) return;
    setLineas((ls) => {
      const next = [...ls];
      valores.forEach((v, i) => {
        const cantidad = v.replace(",", ".");
        const pos = idx + i;
        if (pos < next.length) {
          const l = next[pos];
          next[pos] = { ...l, cantidad, importe: Number(l.precio || 0) * Number(cantidad || 0) };
        } else {
          next.push(nuevaLinea({ cantidad }));
        }
      });
      return next;
    });
    valores.forEach((v, i) => {
      const l = lineas[idx + i];
      if (l?.producto_id) cotizar(l.key, l.producto_id, l.presentacion, v.replace(",", "."));
    });
    toast.success(`${valores.length} cantidades pegadas`);
  }

  async function pegarColumnaProducto(key: string, text: string) {
    const valores = text.split("\n").map((v) => v.split("\t")[0].trim()).filter(Boolean);
    if (valores.length === 0) return;
    const idx = lineas.findIndex((l) => l.key === key);
    if (idx < 0) return;
    let matches: MatchResult[] = [];
    try {
      matches = await apiFetch("/api/v1/productos/match", {
        method: "POST",
        body: JSON.stringify({ textos: valores, usar_ia: false, limit: 1 }),
      });
    } catch {
      matches = [];
    }
    // Calculado FUERA del updater: en StrictMode el updater corre dos veces y
    // duplicaría `paraCotizar` y las líneas creadas con `nuevaLinea()`.
    const paraCotizar: LineaForm[] = [];
    const next = [...lineas];
    valores.forEach((texto, i) => {
      const pos = idx + i;
      const base = pos < next.length ? next[pos] : nuevaLinea();
      const top = matches[i]?.candidatos?.[0];
      const auto = top && (top.origen === "exacto" || top.origen === "alias" || top.score >= 85) ? top : null;
      let linea: LineaForm;
      if (auto) {
        const presKeys = Object.keys(auto.presentaciones ?? {});
        const def = auto.presentacion_default ?? auto.unidad_base ?? presKeys[0] ?? "PIEZA";
        const presentacion = presKeys.includes(def) ? def : presKeys[0] ?? def;
        linea = { ...base, texto, producto_id: auto.producto_id, label: auto.nombre, presentaciones: presKeys, presentacion };
        paraCotizar.push(linea);
      } else {
        linea = { ...base, texto, producto_id: "", label: "" };
      }
      if (pos < next.length) next[pos] = linea;
      else next.push(linea);
    });
    setLineas(next);
    paraCotizar.forEach((l) => cotizar(l.key, l.producto_id, l.presentacion, l.cantidad));
    toast.success(`${valores.length} productos pegados`);
  }

  // Construye el payload de POST /facturas/directa, o null (con toast) si falta algo.
  function construirPayload() {
    if (!clienteId) { toast.error("Elige un cliente"); return null; }
    if (!almacenId) { toast.error("Elige el almacén del que sale la mercancía"); return null; }
    const lns = lineas.filter((l) => l.producto_id && Number(l.cantidad) > 0);
    if (lns.length === 0) { toast.error("Agrega al menos una línea con producto y cantidad"); return null; }
    // El backend exige precio_unitario por línea (a diferencia de la remisión,
    // donde lo resuelve solo). Sin precio de lista hay que capturarlo a mano.
    const sinPrecio = lns.filter((l) => l.precio === "" || Number.isNaN(Number(l.precio)));
    if (sinPrecio.length > 0) {
      toast.error(`Falta el precio en ${sinPrecio.length} línea${sinPrecio.length > 1 ? "s" : ""} (${sinPrecio.map((l) => l.label || l.texto || "sin producto").join(", ")})`);
      return null;
    }
    return {
      cliente_id: clienteId,
      almacen_id: almacenId,
      serie_id: serieOverride || null,
      uso_cfdi: usoCfdi || null,
      forma_pago: formaPago || null,
      metodo_pago: metodoPago || null,
      notas: notas || null,
      lineas: lns.map((l) => ({
        producto_id: l.producto_id,
        cantidad: l.cantidad,
        precio_unitario: l.precio,
        presentacion: l.presentacion || null,
      })),
    };
  }

  const [choiceOpen, setChoiceOpen] = useState(false);
  const [timbrando, setTimbrando] = useState(false);
  const busy = saving || timbrando;
  // Guard SÍNCRONO contra doble clic: `saving`/`timbrando` son estado async y
  // llegan tarde — sin esto un doble clic crea dos facturas.
  const submitRef = useRef(false);

  function abrirGuardar() {
    if (busy) return;
    if (!construirPayload()) return;   // valida y togglea el toast si falta algo
    setChoiceOpen(true);
  }

  // "Borrador": crea la factura sin timbrar y sin tocar el inventario.
  async function guardarBorrador() {
    if (busy || submitRef.current) return;
    const payload = construirPayload();
    if (!payload) return;
    submitRef.current = true;
    setChoiceOpen(false);
    try {
      const f = await post<FacturaDetail>("/api/v1/facturas/directa", payload);
      toast.success(`Factura ${f.serie}${f.folio} guardada (borrador)`);
      onSaved(f);
      onClose();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar la factura");
    } finally {
      submitRef.current = false;
    }
  }

  // "Timbrar": crea el borrador y lo timbra ante el PAC. El timbrado descuenta
  // el inventario del almacén elegido. Si el timbrado falla, el borrador YA
  // existe: salimos al listado para reintentar desde ahí sin duplicarlo.
  async function guardarYTimbrar() {
    if (busy || submitRef.current) return;
    const payload = construirPayload();
    if (!payload) return;
    submitRef.current = true;
    setChoiceOpen(false);
    let creada: FacturaDetail;
    try {
      creada = await post<FacturaDetail>("/api/v1/facturas/directa", payload);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la factura");
      submitRef.current = false;
      return;
    }
    setTimbrando(true);
    try {
      await apiFetch(`/api/v1/facturas/${creada.id}/timbrar`, { method: "POST" });
      toast.success(`Factura ${creada.serie}${creada.folio} timbrada${ambiente === "producción" ? "" : " (sandbox)"}`);
      onSaved(creada);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && /existencia insuficiente/i.test(e.message)) {
        // Política unificada de sobregiro: el backend frena y aquí se pregunta.
        setSobregiroDe(creada);
      } else {
        // El borrador quedó creado: se puede reintentar el timbrado desde la lista.
        toast.error(
          `${e instanceof ApiError ? e.message : "No se pudo timbrar"} — el borrador ${creada.serie}${creada.folio} quedó guardado`,
        );
        onSaved(creada);
        onClose();
      }
    } finally {
      setTimbrando(false);
      submitRef.current = false;
    }
  }

  // Sobregiro: la directa no tiene existencia suficiente; el borrador ya quedó
  // creado y aquí se decide timbrarlo en negativo o dejarlo como borrador.
  const [sobregiroDe, setSobregiroDe] = useState<FacturaDetail | null>(null);

  async function timbrarConSobregiro() {
    if (!sobregiroDe || timbrando) return;
    const f = sobregiroDe;
    setTimbrando(true);
    try {
      await apiFetch(`/api/v1/facturas/${f.id}/timbrar`, {
        method: "POST",
        body: JSON.stringify({ permitir_negativos: true }),
      });
      toast.success(
        `Factura ${f.serie}${f.folio} timbrada con sobregiro${ambiente === "producción" ? "" : " (sandbox)"}`,
      );
    } catch (e) {
      toast.error(
        `${e instanceof ApiError ? e.message : "No se pudo timbrar"} — el borrador ${f.serie}${f.folio} quedó guardado`,
      );
    } finally {
      setTimbrando(false);
      setSobregiroDe(null);
      onSaved(f);
      onClose();
    }
  }

  function declinarSobregiro() {
    const f = sobregiroDe;
    setSobregiroDe(null);
    if (f) {
      toast.success(`El borrador ${f.serie}${f.folio} quedó guardado (sin timbrar)`);
      onSaved(f);
      onClose();
    }
  }

  const almacenNombre = almacenes.find((a) => a.id === almacenId)?.nombre;

  return (
    <div>
      <PageHeader
        title="Nueva factura"
        subtitle={
          ambiente === "producción"
            ? "Captura directa (sin remisión) — al timbrar el CFDI es real ante el SAT y descuenta inventario"
            : "Captura directa (sin remisión) — al timbrar se descuenta inventario (PAC en sandbox)"
        }
        actions={<Button variant="secondary" onClick={onClose}><X size={16} /> Cancelar</Button>}
      />

      <div className="grid grid-cols-1 gap-4 rounded-xl border border-border p-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Cliente" required hint="Escribe y usa ↑/↓ · Enter para seleccionar y avanzar">
          <KeyboardCombobox
            options={clienteOpts}
            value={clienteId}
            onSelect={selectCliente}
            autoOpen={step === "cliente"}
            placeholder="Buscar cliente…"
            emptyText="Sin clientes"
          />
        </Field>
        <Field label="Almacén" required hint="De aquí sale el inventario al timbrar">
          <KeyboardCombobox
            options={almacenOpts}
            value={almacenId}
            onSelect={setAlmacenId}
            onAdvance={() => { setStep(null); setLineFocus({ key: lineas[0]?.key, field: "cantidad" }); }}
            autoOpen={step === "almacen"}
            placeholder="Elige almacén…"
            emptyText="Sin almacenes"
          />
        </Field>
        <Field label="Serie">
          <KeyboardCombobox
            options={serieOpts}
            value={serieOverride}
            onSelect={setSerieOverride}
            placeholder="Serie…"
          />
        </Field>
        <Field label="Consecutivo (informativo)">
          <Input value={folioPreview} readOnly disabled aria-label="Folio consecutivo" />
        </Field>
        <Field label="Uso del CFDI" hint="Predeterminado del cliente">
          <Select value={usoCfdi} onChange={(e) => setUsoCfdi(e.target.value)}>
            {USO_CFDI_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>
        <Field label="Método de pago">
          <Select value={metodoPago} onChange={(e) => setMetodoPago(e.target.value)}>
            {METODO_PAGO_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>
        <Field
          label="Forma de pago"
          hint={metodoPago === "PUE" ? "PUE exige una forma de pago real (no 99)" : undefined}
        >
          <Select value={formaPago} onChange={(e) => setFormaPago(e.target.value)}>
            {FORMA_PAGO_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>
      </div>

      {metodoPago === "PUE" && formaPago === "99" && (
        <div className="mt-3">
          <Alert tone="warning">
            Con método <strong>PUE</strong> el SAT no acepta la forma de pago <strong>99 — Por definir</strong>.
            Elige la forma de pago real o cambia el método a <strong>PPD</strong>, o el PAC rechazará el timbrado.
          </Alert>
        </div>
      )}

      <div className="mt-4 rounded-xl border border-border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Conceptos</h2>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setPasteOpen(true)}><ClipboardPaste size={16} /> Pegar de Excel</Button>
            <Button variant="secondary" onClick={() => setLineas((ls) => [...ls, nuevaLinea()])}><Plus size={16} /> Línea</Button>
          </div>
        </div>

        <div className="space-y-2">
          <div className="hidden grid-cols-12 gap-2 px-1 text-xs text-muted sm:grid">
            <div className={showMatchIA ? "col-span-1" : "col-span-2"}>Cantidad</div>
            <div className="col-span-3">Producto</div>
            {showMatchIA && <div className="col-span-3 inline-flex items-center gap-1"><Sparkles size={12} /> Match IA</div>}
            <div className="col-span-2">Presentación</div>
            <div className="col-span-2">Precio</div>
            {!showMatchIA && <div className="col-span-1 text-right">IEPS</div>}
            {!showMatchIA && <div className="col-span-1 text-right">IVA</div>}
            <div className="col-span-1 text-right">Importe</div>
          </div>
          {lineas.map((l) => {
            // En el desplegable Match IA solo se ofrecen coincidencias ≥80%.
            const cands = (l.candidatos ?? []).filter((c) => c.score >= 80);
            const enCands = cands.some((c) => c.producto_id === l.producto_id);
            return (
              <div key={l.key} className="grid grid-cols-12 items-start gap-2">
                <div className={`col-span-3 ${showMatchIA ? "sm:col-span-1" : "sm:col-span-2"}`}>
                  <Input
                    inputMode="decimal" value={l.cantidad}
                    ref={(el) => { cellRefs.current[`${l.key}:cantidad`] = el; }}
                    onChange={(e) => { const v = e.target.value.replace(",", "."); setLinea(l.key, { cantidad: v, importe: Number(l.precio || 0) * Number(v || 0) }); cotizar(l.key, l.producto_id, l.presentacion, v); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); advanceLine(l.key, "cantidad"); } }}
                    onPaste={(e) => {
                      const text = e.clipboardData.getData("text");
                      if (text.includes("\n")) { e.preventDefault(); pegarColumnaCantidad(l.key, text); }
                    }}
                  />
                </div>
                <div className="col-span-12 sm:col-span-3">
                  <ProductoCombobox
                    label={l.label || l.texto}
                    onSelect={(p, t) => onPickProducto(l.key, p, t)}
                    autoFocus={lineFocus?.key === l.key && lineFocus?.field === "producto"}
                    onPaste={(e) => {
                      const text = e.clipboardData.getData("text");
                      if (text.includes("\n")) { e.preventDefault(); void pegarColumnaProducto(l.key, text); }
                    }}
                  />
                </div>
                {showMatchIA && (
                  <div className="col-span-12 sm:col-span-3">
                    {l.fromPaste ? (
                      <Select
                        value={l.producto_id || NUEVO_PRODUCTO}
                        onChange={(e) => resolverMatchIA(l.key, e.target.value)}
                      >
                        {l.producto_id && !enCands && <option value={l.producto_id}>{l.label || "Producto elegido"}</option>}
                        {cands.map((c) => (
                          <option key={c.producto_id} value={c.producto_id}>{c.nombre} · {c.score}%{c.origen === "ia" ? " (IA)" : ""}</option>
                        ))}
                        <option value={NUEVO_PRODUCTO}>＋ Crear producto nuevo</option>
                      </Select>
                    ) : (
                      <span className="text-xs text-muted">—</span>
                    )}
                  </div>
                )}
                <div className="col-span-4 sm:col-span-2">
                  <KeyboardCombobox
                    options={(l.presentaciones.length ? l.presentaciones : [l.presentacion]).map((p) => ({ value: p, label: p }))}
                    value={l.presentacion}
                    onSelect={(v) => { setLinea(l.key, { presentacion: v }); cotizar(l.key, l.producto_id, v, l.cantidad); }}
                    onAdvance={() => advanceLine(l.key, "presentacion")}
                    autoOpen={lineFocus?.key === l.key && lineFocus?.field === "presentacion"}
                    placeholder="Presentación"
                  />
                </div>
                <div className="col-span-3 sm:col-span-2">
                  <Input
                    inputMode="decimal" placeholder="requerido" value={l.precio}
                    ref={(el) => { cellRefs.current[`${l.key}:precio`] = el; }}
                    onChange={(e) => { const v = e.target.value.replace(",", "."); setLinea(l.key, { precio: v, precioManual: true, importe: Number(v || 0) * Number(l.cantidad || 0) }); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); advanceLine(l.key, "precio"); } }}
                  />
                </div>
                {!showMatchIA && (
                  <div className="col-span-2 flex items-center justify-end sm:col-span-1">
                    <span className="text-sm tabular-nums">{fmtMoney(fiscalPreview?.porKey[l.key]?.ieps ?? 0)}</span>
                  </div>
                )}
                {!showMatchIA && (
                  <div className="col-span-2 flex items-center justify-end sm:col-span-1">
                    <span className="text-sm tabular-nums">{fmtMoney(fiscalPreview?.porKey[l.key]?.iva ?? 0)}</span>
                  </div>
                )}
                <div className="col-span-2 flex items-center justify-end gap-1 sm:col-span-1">
                  <span className="text-sm tabular-nums">{fmtMoney(l.importe)}</span>
                  <button aria-label="Quitar línea" onClick={() => setLineas((ls) => ls.filter((x) => x.key !== l.key))} className="text-muted hover:text-danger"><Trash2 size={15} /></button>
                </div>
              </div>
            );
          })}
        </div>

        <Field label="Notas">
          <Textarea rows={2} value={notas} onChange={(e) => setNotas(e.target.value)} />
        </Field>

        <div className="mt-4 flex items-start justify-between border-t border-border pt-4">
          <div />
          <div className="flex flex-col items-end gap-4">
            <div className="flex flex-col items-end gap-1 text-sm">
              <div className="flex gap-4"><span className="text-muted">Subtotal</span><span className="tabular-nums">{fmtMoney(subtotalPreview)}</span></div>
              <div className="flex gap-4"><span className="text-muted">IEPS</span><span className="tabular-nums">{fmtMoney(iepsPreview)}</span></div>
              <div className="flex gap-4"><span className="text-muted">IVA</span><span className="tabular-nums">{fmtMoney(ivaPreview)}</span></div>
              <div className="flex gap-4 text-base font-semibold"><span className="text-muted">Total</span><span className="tabular-nums">{fmtMoney(totalPreview)}</span></div>
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={resetForm} disabled={busy}>Borrar</Button>
              <Button onClick={abrirGuardar} disabled={busy}>
                {timbrando ? <>Timbrando<LoadingDots /></> : saving ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </div>
        </div>
      </div>

      <Modal open={pasteOpen} onClose={() => { if (!procesando) cerrarPaste(); }} title="Pegar conceptos desde Excel"
        footer={<>
          <Button variant="secondary" onClick={cerrarPaste} disabled={procesando}>Cancelar</Button>
          <Button onClick={procesarPaste} disabled={procesando}>{procesando ? <>Procesando<LoadingDots /></> : "Procesar"}</Button>
        </>}>
        <p className="mb-2 text-sm text-muted">Pega las columnas desde Excel (una fila por línea). La <strong>IA detecta</strong> qué columna es <strong>producto</strong>, <strong>cantidad</strong>, <strong>precio</strong> y <strong>presentación</strong> aunque vengan en cualquier orden, e <strong>ignora el encabezado</strong>. Las líneas entran a la tabla y una columna <strong>Match IA</strong> aparece para elegir el producto del catálogo o crearlo, ahí mismo.</p>
        <Textarea rows={8} value={pasteText} onChange={(e) => setPasteText(e.target.value)} placeholder={"zanahoria\t10\tKILO\njitomate\t5\t12.50"} />
      </Modal>

      <CrearProductoModal
        open={crearParaKey !== null}
        onClose={() => setCrearParaKey(null)}
        nombreInicial={lineaCrear?.texto ?? ""}
        unidadBaseInicial={unidadBaseDesde(lineaCrear?.presPegada)}
        onCreated={aplicarProductoCreado}
      />

      <Modal open={choiceOpen} onClose={() => setChoiceOpen(false)} title="Guardar factura"
        footer={
          <>
            <Button variant="secondary" onClick={() => setChoiceOpen(false)} disabled={busy}>Cancelar</Button>
            <Button variant="secondary" onClick={guardarBorrador} disabled={busy}>Borrador</Button>
            <Button onClick={guardarYTimbrar} disabled={busy}>Timbrar</Button>
          </>
        }>
        <p className="mb-2 text-sm text-muted">Elige cómo guardar la factura:</p>
        <ul className="ml-4 list-disc space-y-1 text-sm text-muted">
          <li><strong>Borrador</strong>: se guarda sin timbrar y <strong>sin afectar el inventario</strong>. Puedes timbrarla después desde la lista.</li>
          <li>
            <strong>Timbrar</strong>: se envía al PAC {ambiente === "producción" ? "en producción (CFDI real ante el SAT)" : "en modo sandbox (prueba)"}
            {" "}y se <strong>descuenta el inventario</strong>{almacenNombre ? ` de ${almacenNombre}` : ""}.
          </li>
        </ul>
      </Modal>

      {/* Sobregiro: sin existencia suficiente para timbrar la directa */}
      <Modal open={sobregiroDe !== null} onClose={declinarSobregiro} title="Existencia insuficiente"
        footer={
          <>
            <Button variant="secondary" onClick={declinarSobregiro} disabled={timbrando}>Dejar como borrador</Button>
            <Button variant="danger" onClick={timbrarConSobregiro} disabled={timbrando}>Timbrar con sobregiro</Button>
          </>
        }>
        <p className="text-sm text-muted">
          No hay existencia suficiente para timbrar {sobregiroDe?.serie}{sobregiroDe?.folio}.
          Si timbras de todas formas, el inventario quedará en negativo (sobregiro).
        </p>
      </Modal>
    </div>
  );
}

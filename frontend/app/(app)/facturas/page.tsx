"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Download, Eye, FileCode2, FileText, Mail, Plus, Replace, Stamp, Trash2, X } from "lucide-react";

import { FacturaDirectaForm } from "@/components/FacturaDirectaForm";
import { SincronizarSae } from "@/components/SincronizarSae";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column, type RowAction } from "@/components/ui/DataTable";
import { DataTableSmart } from "@/components/ui/DataTableSmart";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { SearchBox } from "@/components/ui/SearchBox";
import { useOnboarding } from "@/components/OnboardingChecklist";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiDownload, apiFetch, apiOpenInTab } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney } from "@/lib/format";
import { useResource, type Page } from "@/lib/hooks";
import { FORMA_PAGO_OPTS, METODO_PAGO_OPTS, USO_CFDI_OPTS } from "@/lib/sat";
import type { Cliente, Factura, FacturaDetail, Remision, Serie } from "@/lib/types";

const WRITE = "factura:gestionar";

const ESTADO_TONE: Record<string, "success" | "warning" | "muted" | "danger"> = {
  BORRADOR: "warning",
  TIMBRADA: "success",
  CANCELADA: "danger",
};

export default function FacturasPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  // Permisos destructivos separados de gestionar: cancelar toca al SAT y
  // eliminar borra borradores; no todo rol que captura puede hacerlos.
  const canCancelar = can(me, "factura:cancelar");
  const canEliminar = can(me, "factura:eliminar");

  // Deep-link desde Remisiones (?ver=<factura_id>): abre esa factura con su
  // slide-down. Se lee de window (client-only) para no forzar Suspense.
  const [verId, setVerId] = useState<string | undefined>(undefined);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("ver");
    if (p) setVerId(p);
  }, []);
  const { status: onboardingStatus } = useOnboarding();
  const ambiente = onboardingStatus?.ambiente ?? "sandbox";
  // Con varias empresas abiertas a la vez, un CFDI timbrado desde la equivocada
  // se cancela y se refactura: el diálogo dice con todas sus letras quién emite.
  const emisor = (() => {
    const t = me?.tenants.find((x) => x.tenant_id === me.active_tenant.tenant_id);
    if (!t) return "";
    return ` Emisor: ${t.name}${t.rfc ? ` — RFC ${t.rfc}` : ""}.`;
  })();
  // Sufijo para los toasts de timbrado/cancelación: "(sandbox)" solo cuando el
  // ambiente ya cargó y de verdad es sandbox — en producción (o mientras carga)
  // afirmarlo haría creer que el CFDI no fue real.
  const sufijoAmbiente = onboardingStatus && ambiente !== "producción" ? " (sandbox)" : "";

  const clientesRes = useResource<Page<Cliente>>("/api/v1/clientes?limit=500");
  const clientes = clientesRes.data?.items ?? [];
  const cliName = useMemo(() => Object.fromEntries(clientes.map((c) => [c.id, c.legal_name])), [clientes]);
  // Correos por cliente (para autollenar el envío): array `correos` o el `email` legado.
  const cliCorreos = useMemo(() => Object.fromEntries(clientes.map((c) => {
    const dom = (c.domicilio_fiscal ?? {}) as Record<string, unknown>;
    const arr = Array.isArray(dom.correos)
      ? (dom.correos as string[])
      : (dom.email ? [String(dom.email)] : []);
    return [c.id, arr.join(", ")];
  })), [clientes]);

  // Un usuario de portal (sin menu:series) no puede listar series: pedirlas
  // sería un 403 seguro; null = no pedir y trabajar con lista vacía.
  const seriesFacRes = useResource<Page<Serie>>(
    can(me, "menu:series") ? "/api/v1/series?tipo_documento=FACTURA&activa=true&limit=200" : null,
  );
  const seriesFac = seriesFacRes.data?.items ?? [];

  // Buscar la factura por como la nombra quien pregunta: el folio fiscal
  // ("ZMAFAN 167"), el UUID, o el folio interno de la entrega ("SN-33NER-JUE"),
  // que vive en las observaciones y es con lo que el equipo la busca de verdad.
  const [busca, setBusca] = useState("");
  const [buscaAplicada, setBuscaAplicada] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setBuscaAplicada(busca.trim()), 300);
    return () => clearTimeout(t);
  }, [busca]);
  const { data, loading, error, reload } = useResource<Page<Factura>>(
    `/api/v1/facturas?limit=50${buscaAplicada ? `&q=${encodeURIComponent(buscaAplicada)}` : ""}`,
  );
  const rows = data?.items ?? [];

  // Alta de factura directa (captura a mano, sin remisión): ocupa la pantalla
  // completa como el alta de remisión, en vez de un modal.
  const [mode, setMode] = useState<"list" | "create">("list");

  // ── generar desde remisiones ──
  const [genOpen, setGenOpen] = useState(false);
  const [genCliente, setGenCliente] = useState("");
  const [genSerie, setGenSerie] = useState("");
  const [remisiones, setRemisiones] = useState<Remision[]>([]);
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!genCliente) { setRemisiones([]); setSel({}); return; }
    // Al cambiar rápido de cliente, la respuesta del anterior no debe pisar la
    // lista (se facturaría al cliente equivocado).
    let active = true;
    apiFetch<Page<Remision>>(`/api/v1/remisiones?cliente_id=${genCliente}&limit=200`)
      .then((r) => {
        if (!active) return;
        setRemisiones(r.items.filter(
          (x) => (x.estado === "BORRADOR" || x.estado === "CONFIRMADA") &&
            (!x.factura_id || x.factura_estado === "CANCELADA")));
      })
      .catch((e) => {
        if (!active) return;
        // Un error de red no es "no hay remisiones": se avisa en vez de fingir lista vacía.
        setRemisiones([]);
        toast.error(e instanceof ApiError ? e.message : "No se pudieron cargar las remisiones del cliente");
      });
    setSel({});
    return () => { active = false; };
  }, [genCliente]);

  const selIds = Object.entries(sel).filter(([, v]) => v).map(([k]) => k);
  const selTotal = remisiones.filter((r) => sel[r.id]).reduce((s, r) => s + Number(r.total), 0);

  function openGen() {
    setGenCliente(""); setGenSerie(""); setRemisiones([]); setSel({}); setGenOpen(true);
  }

  async function generar() {
    if (selIds.length === 0) { toast.error("Selecciona al menos una remisión"); return; }
    setBusy(true);
    try {
      const f = await apiFetch<FacturaDetail>("/api/v1/facturas/desde-remisiones", {
        method: "POST",
        body: JSON.stringify({ remision_ids: selIds, serie_id: genSerie || null }),
      });
      toast.success(`Factura ${f.serie}${f.folio} creada (borrador)`);
      setGenOpen(false);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo generar la factura");
    } finally {
      setBusy(false);
    }
  }

  // ── detalle por fila (slide-down): se carga al expandir y se cachea ──
  const [detalles, setDetalles] = useState<Record<string, FacturaDetail>>({});
  const [detalleLoading, setDetalleLoading] = useState<Set<string>>(new Set());
  const [toTimbrar, setToTimbrar] = useState<Factura | null>(null);
  const [toCancel, setToCancel] = useState<Factura | null>(null);
  const [toDescartar, setToDescartar] = useState<Factura | null>(null);
  // Motivo de cancelación SAT (01–04). 01 requiere UUID de la factura que
  // sustituye. Sea cual sea el motivo, al cancelar las remisiones se liberan a
  // BORRADOR y se devuelve su inventario (backend _release_remision_stock).
  const [cancelMotivo, setCancelMotivo] = useState("02");
  const [cancelSustitucion, setCancelSustitucion] = useState("");
  // Qué hacer con el inventario al cancelar: devolución a almacén o pérdida.
  const [cancelInventario, setCancelInventario] = useState<"devolucion" | "perdida">("devolucion");
  const [actBusy, setActBusy] = useState(false);

  // ── enviar por correo ──
  const [toEnviar, setToEnviar] = useState<Factura | null>(null);
  const [enviarTo, setEnviarTo] = useState("");
  const [enviarMensaje, setEnviarMensaje] = useState("");
  const [enviarBusy, setEnviarBusy] = useState(false);

  // ── sustituir (refacturación, relación CFDI 04) ──
  const [toSustituir, setToSustituir] = useState<Factura | null>(null);
  const [sustUso, setSustUso] = useState("");
  const [sustForma, setSustForma] = useState("");
  const [sustMetodo, setSustMetodo] = useState("");
  const [sustBusy, setSustBusy] = useState(false);
  // Mapa id → factura para resolver el folio de la relación de sustitución.
  const facById = useMemo(
    () => Object.fromEntries((data?.items ?? []).map((f) => [f.id, f])),
    [data],
  );

  async function verDetalle(f: Factura) {
    if (detalles[f.id] || detalleLoading.has(f.id)) return;
    setDetalleLoading((s) => new Set(s).add(f.id));
    try {
      const d = await apiFetch<FacturaDetail>(`/api/v1/facturas/${f.id}`);
      setDetalles((m) => ({ ...m, [f.id]: d }));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cargar");
    } finally {
      setDetalleLoading((s) => { const n = new Set(s); n.delete(f.id); return n; });
    }
  }

  // Olvida el detalle cacheado tras una acción que cambia el estado.
  function invalidar(id: string) {
    setDetalles((m) => { const n = { ...m }; delete n[id]; return n; });
  }

  function renderDetalle(f: Factura) {
    const d = detalles[f.id];
    if (!d) return <div className="flex justify-center py-6"><Spinner /></div>;
    const ieps = d.lineas.reduce((s, l) => s + Number(l.ieps_importe || 0), 0);
    return (
      <div className="rounded-xl border border-border bg-background p-4">
        <div className="mb-3 flex flex-wrap gap-4 text-sm">
          <div><span className="text-muted">Cliente:</span> {cliName[d.cliente_id] ?? "—"}</div>
          <div><span className="text-muted">Fecha:</span> {fmtDate(d.fecha)}</div>
          <div><span className="text-muted">Estado:</span> <Badge tone={ESTADO_TONE[d.estado] ?? "muted"}>{d.estado}</Badge></div>
          {d.uuid && <div><span className="text-muted">UUID:</span> <span className="font-mono text-xs">{d.uuid}</span></div>}
          {d.fecha_timbrado && <div><span className="text-muted">Timbrada:</span> {fmtDateTime(d.fecha_timbrado)}</div>}
          {d.sustituye_a_factura_id && (
            <div>
              <span className="text-muted">Sustituye a:</span>{" "}
              <Badge tone="muted">
                {facById[d.sustituye_a_factura_id]
                  ? `${facById[d.sustituye_a_factura_id].serie}${facById[d.sustituye_a_factura_id].folio}`
                  : "factura previa"}
              </Badge>
            </div>
          )}
          {d.uuid_sustitucion && (
            <div>
              <span className="text-muted">Sustituida por UUID:</span>{" "}
              <span className="font-mono text-xs">{d.uuid_sustitucion}</span>
            </div>
          )}
        </div>
        <DataTable
          rows={d.lineas}
          rowKey={(l) => l.numero_linea}
          empty="Sin conceptos"
          columns={[
            { header: "Cant.", className: "text-right tabular-nums", cell: (l) => l.cantidad },
            { header: "Descripción", cell: (l) => l.descripcion },
            { header: "P/U", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.valor_unitario) },
            { header: "IEPS", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.ieps_importe) },
            { header: "IVA", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.iva_importe) },
            { header: "Importe", className: "text-right tabular-nums", cell: (l) => fmtMoney(l.importe) },
          ]}
        />
        <div className="mt-3 flex flex-col items-end gap-1 text-sm">
          <div className="flex gap-4"><span className="text-muted">Subtotal</span><span className="tabular-nums">{fmtMoney(d.subtotal)}</span></div>
          {ieps > 0 && <div className="flex gap-4"><span className="text-muted">IEPS</span><span className="tabular-nums">{fmtMoney(ieps)}</span></div>}
          <div className="flex gap-4"><span className="text-muted">IVA</span><span className="tabular-nums">{fmtMoney(d.iva_trasladado)}</span></div>
          <div className="flex gap-4 text-base font-semibold"><span className="text-muted">Total</span><span className="tabular-nums">{fmtMoney(d.total)}</span></div>
        </div>
      </div>
    );
  }

  // Sobregiro al timbrar una DIRECTA sin existencia (política unificada con
  // remisiones): el backend frena con 422 y aquí se ofrece autorizar.
  const [timbrarSobregiro, setTimbrarSobregiro] = useState<Factura | null>(null);

  async function timbrar(factura?: Factura, permitirNegativos = false) {
    const f = factura ?? toTimbrar;
    if (!f) return;
    setActBusy(true);
    try {
      await apiFetch(`/api/v1/facturas/${f.id}/timbrar`, {
        method: "POST",
        body: JSON.stringify({ permitir_negativos: permitirNegativos }),
      });
      toast.success(`Factura timbrada${sufijoAmbiente}`);
      invalidar(f.id);
      setToTimbrar(null); setTimbrarSobregiro(null); reload();
    } catch (e) {
      if (e instanceof ApiError && /existencia insuficiente/i.test(e.message) && !permitirNegativos) {
        setToTimbrar(null);
        setTimbrarSobregiro(f);
      } else {
        toast.error(e instanceof ApiError ? e.message : "No se pudo timbrar");
      }
    } finally { setActBusy(false); }
  }
  function abrirCancelar(f: Factura) {
    setCancelMotivo("02");
    setCancelSustitucion("");
    // Siempre arranca en devolución: si la cancelación anterior fue merma, un
    // default pegajoso daría de baja inventario sin que el usuario lo note.
    setCancelInventario("devolucion");
    setToCancel(f);
  }
  async function cancelar() {
    if (!toCancel) return;
    setActBusy(true);
    try {
      const body: { motivo: string; uuid_sustitucion?: string; inventario: string } =
        { motivo: cancelMotivo, inventario: cancelInventario };
      // Motivo 01: el UUID es opcional — si no se captura, el backend lo autocompleta
      // con la factura sustituta timbrada (creada con "Sustituir").
      if (cancelMotivo === "01" && cancelSustitucion.trim()) body.uuid_sustitucion = cancelSustitucion.trim();
      await apiFetch(`/api/v1/facturas/${toCancel.id}/cancelar`, {
        method: "POST", body: JSON.stringify(body),
      });
      toast.success(`Factura cancelada${sufijoAmbiente}`);
      invalidar(toCancel.id);
      setToCancel(null); reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cancelar");
    } finally { setActBusy(false); }
  }
  // Descarta una factura en BORRADOR (nunca timbrada): la elimina y regresa sus
  // remisiones a CONFIRMADA para poder refacturarlas.
  async function descartar() {
    if (!toDescartar) return;
    setActBusy(true);
    try {
      await apiFetch(`/api/v1/facturas/${toDescartar.id}`, { method: "DELETE" });
      toast.success(`Borrador ${toDescartar.serie}${toDescartar.folio} descartado (remisiones liberadas)`);
      invalidar(toDescartar.id);
      setToDescartar(null); reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descartar");
    } finally { setActBusy(false); }
  }
  async function descargar(f: Factura, tipo: "pdf" | "xml") {
    try { await apiDownload(`/api/v1/facturas/${f.id}/${tipo}`, `${f.serie}${f.folio}.${tipo}`); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "No se pudo descargar"); }
  }
  function previsualizar(f: Factura) {
    // La pestaña se abre síncrona al click (antes del fetch autenticado) para
    // que el navegador no la trate como pop-up bloqueado.
    const win = window.open("", "_blank");
    apiOpenInTab(`/api/v1/facturas/${f.id}/pdf`, win).catch((e) => {
      toast.error(e instanceof ApiError ? e.message : "No se pudo abrir la factura");
    });
  }
  function abrirEnviar(f: Factura) {
    setEnviarTo(cliCorreos[f.cliente_id] ?? ""); setEnviarMensaje(""); setToEnviar(f);
  }
  async function enviarCorreo() {
    if (!toEnviar) return;
    const destinatarios = enviarTo.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    if (destinatarios.length === 0) {
      toast.error("Indica al menos un destinatario");
      return;
    }
    setEnviarBusy(true);
    try {
      await apiFetch(`/api/v1/facturas/${toEnviar.id}/enviar`, {
        method: "POST",
        body: JSON.stringify({ to: destinatarios, mensaje: enviarMensaje || undefined }),
      });
      toast.success(`Factura ${toEnviar.serie}${toEnviar.folio} enviada`);
      setToEnviar(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo enviar la factura");
    } finally {
      setEnviarBusy(false);
    }
  }

  // ── Sustituir (refacturación): crea la factura sustituta (copia ligada con
  // relación CFDI 04) en borrador; el usuario la timbra y luego cancela la vieja
  // con motivo 01 (el UUID se autocompleta con esta sustituta).
  function abrirSustituir(f: Factura) {
    setSustUso(f.uso_cfdi); setSustForma(f.forma_pago); setSustMetodo(f.metodo_pago);
    setToSustituir(f);
  }
  async function sustituir() {
    if (!toSustituir) return;
    setSustBusy(true);
    try {
      const body: Record<string, string> = {};
      if (sustUso && sustUso !== toSustituir.uso_cfdi) body.uso_cfdi = sustUso;
      if (sustForma && sustForma !== toSustituir.forma_pago) body.forma_pago = sustForma;
      if (sustMetodo && sustMetodo !== toSustituir.metodo_pago) body.metodo_pago = sustMetodo;
      const nueva = await apiFetch<FacturaDetail>(`/api/v1/facturas/${toSustituir.id}/sustituir`, {
        method: "POST", body: JSON.stringify(body),
      });
      toast.success(
        `Sustituta ${nueva.serie}${nueva.folio} creada (borrador). Tímbrala y luego cancela la original con motivo 01.`,
      );
      setToSustituir(null); reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la sustituta");
    } finally {
      setSustBusy(false);
    }
  }

  // ── Acciones en lote (Objetivo 2): selección múltiple estilo remisiones ──
  const [selected, setSelected] = useState<Factura[]>([]);
  const [selectionResetKey, setSelectionResetKey] = useState(0);
  const clearSelection = () => { setSelected([]); setSelectionResetKey((k) => k + 1); };
  const [bulkBusy, setBulkBusy] = useState(false);
  const borradoresSel = selected.filter((f) => f.estado === "BORRADOR");
  const timbradasSel = selected.filter((f) => f.estado === "TIMBRADA");

  // Timbrar N (solo borradores). Las que fallen por existencia insuficiente se
  // reofrecen con sobregiro; el resto de errores se reporta en el toast.
  const [timbrarBulkOpen, setTimbrarBulkOpen] = useState(false);
  const [timbrarBulkSobregiro, setTimbrarBulkSobregiro] = useState<Factura[]>([]);

  async function bulkTimbrar(objetivo: Factura[], permitirNegativos = false) {
    if (objetivo.length === 0) return;
    setBulkBusy(true);
    try {
      const res = await Promise.allSettled(objetivo.map((f) =>
        apiFetch(`/api/v1/facturas/${f.id}/timbrar`, {
          method: "POST",
          body: JSON.stringify({ permitir_negativos: permitirNegativos }),
        })));
      const sinStock: Factura[] = [];
      let ok = 0, otras = 0;
      res.forEach((r, i) => {
        if (r.status === "fulfilled") ok += 1;
        else if (r.reason instanceof ApiError && /existencia insuficiente/i.test(r.reason.message)) sinStock.push(objetivo[i]);
        else otras += 1;
      });
      objetivo.forEach((f) => invalidar(f.id));
      const partes = [`Timbradas: ${ok}${sufijoAmbiente}`];
      if (sinStock.length) partes.push(`${sinStock.length} sin existencia`);
      if (otras) partes.push(`${otras} con error`);
      toast[sinStock.length === 0 && otras === 0 ? "success" : "error"](partes.join(" · "));
      setTimbrarBulkOpen(false);
      setTimbrarBulkSobregiro(permitirNegativos ? [] : sinStock);
      if (sinStock.length === 0 || permitirNegativos) clearSelection();
      reload();
    } finally { setBulkBusy(false); }
  }

  // Cancelar N (solo timbradas). El motivo 01 exige un UUID de sustitución POR
  // factura, así que en lote solo se ofrecen 02/03/04.
  const [cancelBulkOpen, setCancelBulkOpen] = useState(false);
  const [bulkCancelMotivo, setBulkCancelMotivo] = useState("02");
  const [bulkCancelInventario, setBulkCancelInventario] = useState<"devolucion" | "perdida">("devolucion");

  function abrirCancelBulk() {
    setBulkCancelMotivo("02");
    setBulkCancelInventario("devolucion");
    setCancelBulkOpen(true);
  }

  async function bulkCancelar() {
    const objetivo = timbradasSel;
    if (objetivo.length === 0) return;
    setBulkBusy(true);
    try {
      const res = await Promise.allSettled(objetivo.map((f) =>
        apiFetch(`/api/v1/facturas/${f.id}/cancelar`, {
          method: "POST",
          body: JSON.stringify({ motivo: bulkCancelMotivo, inventario: bulkCancelInventario }),
        })));
      const ok = res.filter((r) => r.status === "fulfilled").length;
      const fail = res.length - ok;
      objetivo.forEach((f) => invalidar(f.id));
      const partes = [`Canceladas: ${ok}${sufijoAmbiente}`];
      if (fail) partes.push(`${fail} con error`);
      toast[fail === 0 ? "success" : "error"](partes.join(" · "));
      setCancelBulkOpen(false);
      clearSelection();
      reload();
    } finally { setBulkBusy(false); }
  }

  // Imprimir N: un solo PDF (una factura por página), pestaña síncrona anti-popup.
  function bulkPdf() {
    if (selected.length === 0) return;
    const win = window.open("", "_blank");
    apiOpenInTab(`/api/v1/facturas/pdf?ids=${selected.map((f) => f.id).join(",")}`, win)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "No se pudo abrir el PDF"));
  }

  // XML N: descargas individuales secuenciales (solo timbradas tienen XML).
  async function bulkXml() {
    setBulkBusy(true);
    try {
      let fail = 0;
      for (const f of timbradasSel) {
        try { await apiDownload(`/api/v1/facturas/${f.id}/xml`, `${f.serie}${f.folio}.xml`); }
        catch { fail += 1; }
      }
      if (fail) toast.error(`${fail} XML no se pudieron descargar`);
    } finally { setBulkBusy(false); }
  }

  // Enviar N: un correo POR CLIENTE con todas sus facturas (PDF+XML adjuntos),
  // correos editables por renglón — mismo patrón que remisiones.
  const [bulkSendOpen, setBulkSendOpen] = useState(false);
  const [bulkSendRows, setBulkSendRows] = useState<{ clienteId: string; nombre: string; facIds: string[]; correos: string }[]>([]);
  const [bulkSendMensaje, setBulkSendMensaje] = useState("");

  function bulkEnviar() {
    if (timbradasSel.length === 0) return;
    const byCliente = new Map<string, string[]>();
    for (const f of timbradasSel) {
      const arr = byCliente.get(f.cliente_id) ?? [];
      arr.push(f.id);
      byCliente.set(f.cliente_id, arr);
    }
    setBulkSendRows([...byCliente.entries()].map(([clienteId, facIds]) => ({
      clienteId, nombre: cliName[clienteId] ?? "—", facIds, correos: cliCorreos[clienteId] ?? "",
    })));
    setBulkSendMensaje("");
    setBulkSendOpen(true);
  }

  async function confirmarBulkEnvio() {
    const activos = bulkSendRows.filter((row) => row.correos.trim());
    if (activos.length === 0) { toast.error("No hay correos: agrega al menos uno o cancela"); return; }
    const mensaje = bulkSendMensaje.trim() || undefined;
    setBulkBusy(true);
    let ok = 0, fail = 0;
    try {
      for (const row of activos) {
        try {
          await apiFetch("/api/v1/facturas/enviar-lote", {
            method: "POST",
            body: JSON.stringify({ ids: row.facIds, to: row.correos.trim(), mensaje }),
          });
          ok += 1;
        } catch { fail += 1; }
      }
      const ignorados = bulkSendRows.length - activos.length;
      const partes = [`Correos enviados: ${ok}`];
      if (fail) partes.push(`${fail} fallidos`);
      if (ignorados) partes.push(`${ignorados} cliente(s) ignorado(s)`);
      toast[fail === 0 ? "success" : "error"](partes.join(" · "));
      setBulkSendOpen(false);
      clearSelection();
    } finally { setBulkBusy(false); }
  }

  const columns: Column<Factura>[] = [
    { header: "Folio", cell: (f) => <span className="font-medium">{f.serie}{f.folio}</span> },
    { header: "Cliente", truncate: true, cell: (f) => <span title={cliName[f.cliente_id] ?? ""}>{cliName[f.cliente_id] ?? "—"}</span> },
    { header: "Fecha", className: "whitespace-nowrap", cell: (f) => fmtDate(f.fecha) },
    { header: "Estado", cell: (f) => (
      <div className="flex items-center gap-1.5 whitespace-nowrap">
        <Badge tone={ESTADO_TONE[f.estado] ?? "muted"}>{f.estado}</Badge>
        {f.origen === "ESPEJO_SAE" ? <Badge tone="muted">Espejo SAE</Badge> : null}
      </div>
    ) },
    { header: "Subtotal", className: "text-right tabular-nums", cell: (f) => fmtMoney(f.subtotal) },
    { header: "IVA", className: "text-right tabular-nums", cell: (f) => fmtMoney(f.iva_trasladado) },
    { header: "Total", className: "text-right tabular-nums", cell: (f) => fmtMoney(f.total) },
    { header: "Nota", truncate: true, exportValue: (f) => f.notas ?? "", cell: (f) => <span title={f.notas ?? ""}>{f.notas ?? "—"}</span> },
  ];

  const rowActions: RowAction<Factura>[] = [
    { id: "timbrar", label: "Timbrar", icon: <Stamp size={15} />, tone: "success",
      onClick: (f) => setToTimbrar(f), hidden: (f) => !(canWrite && f.estado === "BORRADOR") },
    { id: "preview", label: "Ver factura", icon: <Eye size={15} />,
      onClick: (f) => previsualizar(f), hidden: (f) => f.estado !== "TIMBRADA" },
    { id: "pdf", label: "Descargar PDF", icon: <Download size={15} />,
      onClick: (f) => { void descargar(f, "pdf"); }, hidden: (f) => f.estado !== "TIMBRADA" },
    // El XML nativo se pide a Facturama; una espejo no tiene facturama_id (su
    // XML vive en SAE — sincronizarlo es v2 del conector).
    { id: "xml", label: "Descargar XML", icon: <FileCode2 size={15} />,
      onClick: (f) => { void descargar(f, "xml"); },
      hidden: (f) => f.estado !== "TIMBRADA" || f.origen === "ESPEJO_SAE" },
    { id: "enviar", label: "Enviar por correo", icon: <Mail size={15} />,
      onClick: (f) => abrirEnviar(f), hidden: (f) => !(canWrite && f.estado === "TIMBRADA") },
    // Las ESPEJO_SAE se cancelan/refacturan EN SAE (el backend lo rechaza con
    // 409); esconder los botones evita ofrecer algo que va a rebotar.
    // Sustituir implica cancelar la original ante el SAT: mismo permiso.
    { id: "sustituir", label: "Sustituir (refacturar)", icon: <Replace size={15} />,
      onClick: (f) => abrirSustituir(f),
      hidden: (f) => !(canCancelar && f.estado === "TIMBRADA" && f.origen !== "ESPEJO_SAE") },
    { id: "cancelar", label: "Cancelar", icon: <X size={15} />, tone: "danger",
      onClick: (f) => abrirCancelar(f),
      hidden: (f) => !(canCancelar && f.estado === "TIMBRADA" && f.origen !== "ESPEJO_SAE") },
    { id: "descartar", label: "Descartar borrador", icon: <Trash2 size={15} />, tone: "danger",
      onClick: (f) => setToDescartar(f), hidden: (f) => !(canEliminar && f.estado === "BORRADOR") },
  ];

  // ───────────────────────── render ─────────────────────────
  if (mode === "create") {
    return (
      <FacturaDirectaForm
        ambiente={ambiente}
        onClose={() => setMode("list")}
        onSaved={reload}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="Facturas"
        subtitle={ambiente === "producción" ? "CFDI 4.0 — timbrado en producción" : "CFDI 4.0 — timbrado en modo sandbox (pruebas)"}
        actions={(
          <>
            <SincronizarSae onSynced={reload} />
            {canWrite && (
              <>
                <Button variant="secondary" onClick={openGen}><FileText size={16} /> Generar desde remisiones</Button>
                <Button onClick={() => setMode("create")}><Plus size={16} /> Nueva factura</Button>
              </>
            )}
          </>
        )}
      />

      <div className="mb-3 flex items-center gap-3">
        <SearchBox
          value={busca}
          onChange={setBusca}
          placeholder="Buscar por folio, UUID u orden (p. ej. SN-33NER-JUE)"
          className="max-w-md"
        />
        {buscaAplicada && !loading && (
          <span className="text-sm text-muted">
            {data?.total ?? 0} resultado{(data?.total ?? 0) === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {/* Barra de acciones en lote */}
      {selected.length > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface-2 px-4 py-2.5 shadow-sm">
          <span className="text-sm font-medium">{selected.length} seleccionada(s)</span>
          <span className="text-sm text-muted">·</span>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={bulkPdf} disabled={bulkBusy}>
              <FileText size={16} /> Imprimir ({selected.length})
            </Button>
            {timbradasSel.length > 0 && (
              <Button variant="secondary" onClick={() => { void bulkXml(); }} disabled={bulkBusy}>
                <FileCode2 size={16} /> XML ({timbradasSel.length})
              </Button>
            )}
            {canWrite && timbradasSel.length > 0 && (
              <Button variant="secondary" onClick={bulkEnviar} disabled={bulkBusy}>
                <Mail size={16} /> Enviar por correo ({timbradasSel.length})
              </Button>
            )}
            {canWrite && borradoresSel.length > 0 && (
              <Button variant="success" onClick={() => setTimbrarBulkOpen(true)} disabled={bulkBusy}>
                <Stamp size={16} /> Timbrar ({borradoresSel.length})
              </Button>
            )}
            {canCancelar && timbradasSel.length > 0 && (
              <Button variant="danger" onClick={abrirCancelBulk} disabled={bulkBusy}>
                <X size={16} /> Cancelar ({timbradasSel.length})
              </Button>
            )}
          </div>
          <button
            type="button"
            onClick={clearSelection}
            className="ml-auto text-sm text-muted hover:text-foreground"
          >
            Limpiar selección
          </button>
        </div>
      )}

      <DataTableSmart
        columns={columns}
        rows={rows}
        loading={loading}
        error={error}
        empty="Sin facturas"
        rowKey={(f) => f.id}
        actions={rowActions}
        onRowExpand={verDetalle}
        renderExpanded={renderDetalle}
        initialExpandedKey={verId}
        storageKey="facturas"
        selectable
        onSelectionChange={setSelected}
        selectionResetKey={selectionResetKey}
      />

      {/* generar */}
      <Modal open={genOpen} onClose={() => setGenOpen(false)} title="Generar factura desde remisiones" wide
        footer={<><Button variant="secondary" onClick={() => setGenOpen(false)}>Cancelar</Button>
          <Button onClick={generar} disabled={busy || selIds.length === 0}>{busy ? "Generando…" : `Generar (${selIds.length})`}</Button></>}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Cliente" required>
            <Select value={genCliente} onChange={(e) => setGenCliente(e.target.value)}>
              <option value="">— Selecciona —</option>
              {clientes.map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
            </Select>
          </Field>
          <Field label="Serie" hint="En blanco usa la del cliente / predeterminada">
            <Select value={genSerie} onChange={(e) => setGenSerie(e.target.value)}>
              <option value="">(automática)</option>
              {seriesFac.map((s) => <option key={s.id} value={s.id}>{s.codigo}{s.nombre ? ` · ${s.nombre}` : ""}</option>)}
            </Select>
          </Field>
        </div>
        <div className="mt-4">
          <div className="mb-2 text-sm font-medium">Remisiones sin facturar (borrador o confirmadas)</div>
          {genCliente && remisiones.length === 0 && <div className="text-sm text-muted">Este cliente no tiene remisiones confirmadas pendientes.</div>}
          <div className="max-h-64 space-y-1 overflow-auto">
            {remisiones.map((r) => (
              <label key={r.id} className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <span className="flex items-center gap-2">
                  <Checkbox checked={!!sel[r.id]} onChange={(e) => setSel((s) => ({ ...s, [r.id]: e.target.checked }))} />
                  <span className="font-medium">{r.folio_interno}</span>
                  <span className="text-muted">{fmtDate(r.fecha_remision)}</span>
                </span>
                <span className="tabular-nums">{fmtMoney(r.total)}</span>
              </label>
            ))}
          </div>
          {selIds.length > 0 && (
            <div className="mt-2 flex justify-end gap-3 text-sm">
              <span className="text-muted">Total seleccionado</span><span className="font-semibold tabular-nums">{fmtMoney(selTotal)}</span>
            </div>
          )}
        </div>
      </Modal>

      <ConfirmDialog open={toTimbrar !== null} title={ambiente === "producción" ? "Timbrar factura" : "Timbrar factura (sandbox)"}
        message={
          ambiente === "producción"
            ? `¿Timbrar ${toTimbrar?.serie}${toTimbrar?.folio}? Se enviará al PAC en producción — el CFDI será real ante el SAT.${emisor}`
            : `¿Timbrar ${toTimbrar?.serie}${toTimbrar?.folio}? Se enviará al PAC en modo sandbox (prueba).${emisor}`
        }
        confirmLabel="Facturar" confirmVariant="success"
        onConfirm={() => void timbrar()} onClose={() => setToTimbrar(null)} loading={actBusy} />

      {/* Sobregiro: la directa no tiene existencia suficiente para timbrar */}
      <ConfirmDialog open={timbrarSobregiro !== null} title="Existencia insuficiente"
        message={`No hay existencia suficiente para timbrar ${timbrarSobregiro?.serie}${timbrarSobregiro?.folio}. ¿Timbrar de todas formas? El inventario quedará en negativo (sobregiro).${emisor}`}
        confirmLabel="Timbrar con sobregiro" confirmVariant="danger"
        onConfirm={() => void timbrar(timbrarSobregiro ?? undefined, true)}
        onClose={() => setTimbrarSobregiro(null)} loading={actBusy} />

      {/* ── Lote: timbrar N ── */}
      <ConfirmDialog open={timbrarBulkOpen}
        title={ambiente === "producción" ? "Timbrar facturas" : "Timbrar facturas (sandbox)"}
        message={
          ambiente === "producción"
            ? `¿Timbrar ${borradoresSel.length} factura(s) en borrador? Se enviarán al PAC en producción — los CFDI serán reales ante el SAT.${emisor}`
            : `¿Timbrar ${borradoresSel.length} factura(s) en borrador? Se enviarán al PAC en modo sandbox (prueba).${emisor}`
        }
        confirmLabel={`Timbrar ${borradoresSel.length}`} confirmVariant="success"
        onConfirm={() => void bulkTimbrar(borradoresSel)}
        onClose={() => setTimbrarBulkOpen(false)} loading={bulkBusy} />

      {/* Lote: sobregiro de las que no tuvieron existencia */}
      <ConfirmDialog open={timbrarBulkSobregiro.length > 0} title="Existencia insuficiente"
        message={`${timbrarBulkSobregiro.length} factura(s) no tienen existencia suficiente (${timbrarBulkSobregiro.map((f) => `${f.serie}${f.folio}`).join(", ")}). ¿Timbrarlas de todas formas? El inventario quedará en negativo (sobregiro).`}
        confirmLabel="Timbrar con sobregiro" confirmVariant="danger"
        onConfirm={() => void bulkTimbrar(timbrarBulkSobregiro, true)}
        onClose={() => { setTimbrarBulkSobregiro([]); clearSelection(); }} loading={bulkBusy} />

      {/* ── Lote: cancelar N ── */}
      <Modal open={cancelBulkOpen} onClose={() => setCancelBulkOpen(false)}
        title={`Cancelar ${timbradasSel.length} factura(s)`}
        footer={<>
          <Button variant="secondary" onClick={() => setCancelBulkOpen(false)} disabled={bulkBusy}>Cerrar</Button>
          <Button variant="danger" onClick={() => void bulkCancelar()} disabled={bulkBusy}>
            {bulkBusy ? "Cancelando…" : `Cancelar ${timbradasSel.length}`}
          </Button>
        </>}>
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Se cancelará ante el {ambiente === "producción" ? "SAT" : "PAC (sandbox)"} cada una de:
            {" "}{timbradasSel.map((f) => `${f.serie}${f.folio}`).join(", ")}.
          </p>
          <Field label="Motivo SAT" hint="El motivo 01 (con sustitución) requiere un UUID por factura; cancélalas una por una desde la fila">
            <Select value={bulkCancelMotivo} onChange={(e) => setBulkCancelMotivo(e.target.value)}>
              <option value="02">02 — Comprobante sin relación</option>
              <option value="03">03 — No se llevó a cabo la operación</option>
              <option value="04">04 — Operación nominativa en factura global</option>
            </Select>
          </Field>
          <Field label="Inventario">
            <Select value={bulkCancelInventario} onChange={(e) => setBulkCancelInventario(e.target.value as "devolucion" | "perdida")}>
              <option value="devolucion">Devolución — la mercancía regresa al almacén</option>
              <option value="perdida">Pérdida (merma) — la mercancía NO regresa</option>
            </Select>
          </Field>
          {bulkCancelInventario === "perdida" && (
            <Alert tone="warning">La mercancía de TODAS las facturas seleccionadas se dará de baja como merma.</Alert>
          )}
        </div>
      </Modal>

      {/* ── Lote: enviar por correo (un correo por cliente) ── */}
      <Modal open={bulkSendOpen} onClose={() => setBulkSendOpen(false)} title="Enviar facturas por correo" wide
        footer={<>
          <Button variant="secondary" onClick={() => setBulkSendOpen(false)} disabled={bulkBusy}>Cancelar</Button>
          <Button onClick={() => void confirmarBulkEnvio()} disabled={bulkBusy}>
            {bulkBusy ? "Enviando…" : "Enviar correos"}
          </Button>
        </>}>
        <p className="mb-3 text-sm text-muted">
          Cada cliente recibe <strong>un solo correo</strong> con todas sus facturas (PDF y XML adjuntos).
          Edita los correos por renglón; un renglón vacío se ignora.
        </p>
        <div className="space-y-2">
          {bulkSendRows.map((row, i) => (
            <div key={row.clienteId} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[1fr_2fr]">
              <div className="text-sm">
                <span className="font-medium">{row.nombre}</span>
                <span className="text-muted"> · {row.facIds.length} factura(s)</span>
              </div>
              <Input
                value={row.correos}
                placeholder="correo@cliente.mx, otro@cliente.mx"
                onChange={(e) => setBulkSendRows((rs) => rs.map((r, j) => (j === i ? { ...r, correos: e.target.value } : r)))}
              />
            </div>
          ))}
        </div>
        <div className="mt-4">
          <Field label="Mensaje (opcional, va en todos los correos)">
            <Textarea rows={2} value={bulkSendMensaje} onChange={(e) => setBulkSendMensaje(e.target.value)} />
          </Field>
        </div>
      </Modal>
      <ConfirmDialog
        open={toDescartar !== null}
        title={`Descartar borrador ${toDescartar?.serie ?? ""}${toDescartar?.folio ?? ""}`}
        message={`¿Descartar el borrador ${toDescartar?.serie ?? ""}${toDescartar?.folio ?? ""}? Se elimina la factura y sus remisiones vuelven a CONFIRMADA para poder refacturarlas. No aplica a facturas timbradas.`}
        confirmLabel="Descartar" confirmVariant="danger"
        onConfirm={descartar} onClose={() => setToDescartar(null)} loading={actBusy} />
      <Modal
        open={toCancel !== null}
        onClose={() => setToCancel(null)}
        title={`Cancelar factura ${toCancel?.serie ?? ""}${toCancel?.folio ?? ""}`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setToCancel(null)} disabled={actBusy}>Cerrar</Button>
            <Button variant="danger" onClick={() => { void cancelar(); }} disabled={actBusy}>
              {actBusy ? "Cancelando…" : "Cancelar factura"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Motivo de cancelación (SAT)">
            <Select value={cancelMotivo} onChange={(e) => setCancelMotivo(e.target.value)}>
              <option value="01">01 — Comprobante emitido con errores con relación</option>
              <option value="02">02 — Comprobante emitido con errores sin relación</option>
              <option value="03">03 — No se llevó a cabo la operación</option>
              <option value="04">04 — Operación nominativa en factura global</option>
            </Select>
          </Field>
          {cancelMotivo === "01" && (
            <Field
              label="UUID de la factura que sustituye (opcional)"
              hint="Déjalo vacío si ya creaste la sustituta con «Sustituir» y la timbraste: se autocompleta sola."
            >
              <Input
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                value={cancelSustitucion}
                onChange={(e) => setCancelSustitucion(e.target.value)}
              />
            </Field>
          )}
          {cancelMotivo === "01" ? (
            <Alert tone="info">
              Sustitución: la mercancía la ampara la factura sustituta, así que el
              inventario <strong>no se mueve</strong> y las remisiones quedan ligadas
              (no se re-facturan).
            </Alert>
          ) : (
            <>
              <Field label="¿Qué hacer con el inventario?">
                <Select value={cancelInventario} onChange={(e) => setCancelInventario(e.target.value as "devolucion" | "perdida")}>
                  <option value="devolucion">Devolución a almacén (regresa a existencias)</option>
                  <option value="perdida">Pérdida por cancelación (se da de baja como merma)</option>
                </Select>
              </Field>
              <Alert tone="warning">
                {cancelInventario === "perdida" ? (
                  <>El inventario reservado se <strong>da de baja como merma</strong> (no regresa a existencias)
                  {" "}y las remisiones quedan <strong>CANCELADAS</strong> (no se pueden volver a facturar).</>
                ) : (
                  <>El inventario reservado <strong>regresa a existencias</strong> y las remisiones se
                  {" "}liberan a <strong>BORRADOR</strong>, para poder <strong>volver a facturarse</strong>.</>
                )}
              </Alert>
            </>
          )}
        </div>
      </Modal>

      <Modal
        open={toSustituir !== null}
        onClose={() => setToSustituir(null)}
        title={`Sustituir factura ${toSustituir?.serie ?? ""}${toSustituir?.folio ?? ""}`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setToSustituir(null)} disabled={sustBusy}>Cerrar</Button>
            <Button onClick={() => { void sustituir(); }} disabled={sustBusy}>
              {sustBusy ? "Creando…" : "Crear sustituta"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Alert tone="info">
            Se creará una <strong>copia</strong> en borrador ligada a esta factura con la
            relación CFDI <strong>«04 Sustitución»</strong>. Corrige los datos si aplica,
            <strong> tímbrala</strong>, y luego <strong>cancela esta con motivo 01</strong>
            {" "}(el UUID se autocompleta). No mueve inventario.
          </Alert>
          <Field label="Uso de CFDI">
            <Select value={sustUso} onChange={(e) => setSustUso(e.target.value)}>
              {USO_CFDI_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Forma de pago">
              <Select value={sustForma} onChange={(e) => setSustForma(e.target.value)}>
                {FORMA_PAGO_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            </Field>
            <Field label="Método de pago">
              <Select value={sustMetodo} onChange={(e) => setSustMetodo(e.target.value)}>
                {METODO_PAGO_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            </Field>
          </div>
        </div>
      </Modal>

      <Modal
        open={toEnviar !== null}
        onClose={() => setToEnviar(null)}
        title={`Enviar factura ${toEnviar?.serie ?? ""}${toEnviar?.folio ?? ""} por correo`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setToEnviar(null)} disabled={enviarBusy}>Cerrar</Button>
            <Button onClick={() => { void enviarCorreo(); }} disabled={enviarBusy}>
              {enviarBusy ? "Enviando…" : "Enviar"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Destinatario(s)" required hint="Separa varios correos con coma o espacio">
            <Input
              placeholder="cliente@empresa.com"
              value={enviarTo}
              onChange={(e) => setEnviarTo(e.target.value)}
            />
          </Field>
          <Field label="Mensaje (opcional)">
            <Textarea
              rows={3}
              placeholder="Se incluirá arriba del cuerpo del correo"
              value={enviarMensaje}
              onChange={(e) => setEnviarMensaje(e.target.value)}
            />
          </Field>
          <p className="text-xs text-muted">
            Se adjuntan el XML y el PDF de la factura, usando la cuenta de correo configurada en{" "}
            <Link href="/ajustes/correo" className="underline">Ajustes › Correo</Link>.
          </p>
        </div>
      </Modal>
    </div>
  );
}

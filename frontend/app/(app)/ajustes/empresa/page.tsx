"use client";

import { useEffect, useState } from "react";
import { Building2, CheckCircle2, Pencil, ShieldCheck, Upload, Check, X } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Field, Input, PasswordInput, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { KeyboardCombobox, type ComboOption } from "@/components/KeyboardCombobox";
import { OnboardingChecklist, useOnboarding } from "@/components/OnboardingChecklist";
import { ApiError, apiBaseUrl, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDate } from "@/lib/format";
import { REGIMENES_FISCALES } from "@/lib/sat";
import { getSupabase } from "@/lib/supabaseClient";
import { tenantHeader } from "@/lib/tenant";

const WRITE = "membership:gestionar";

// apiFetch fuerza Content-Type JSON, así que los endpoints binarios/multipart
// (logo GET/POST, CSD POST) usan este fetch crudo con el mismo Bearer token.
async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const supabase = getSupabase();
  const { data: { session } } = await supabase.auth.getSession();
  const headers = new Headers(init.headers);
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  tenantHeader(headers);   // misma selección de empresa que apiFetch
  return fetch(`${apiBaseUrl()}${path}`, { ...init, headers });
}

// Respuesta no-ok → ApiError con el `detail` del backend (o el statusText).
async function toApiError(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    detail = (await res.json()).detail ?? detail;
  } catch {
    /* cuerpo no-JSON */
  }
  return new ApiError(res.status, detail);
}

// ── Validación local de RFC: formato + dígito verificador del SAT ──────────
// Espejo de backend/app/services/rfc.py: el último carácter del RFC es un
// dígito verificador determinista — atrapa dígitos transpuestos y typos al
// instante, sin consultar al SAT ni gastar folios.
const RFC_RE = /^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$/;
const RFC_TABLA = "0123456789ABCDEFGHIJKLMN&OPQRSTUVWXYZ";
function rfcDigitoVerificador(rfcSinDv: string): string {
  const base = rfcSinDv.padStart(12, " ");
  let suma = 0;
  for (let i = 0; i < 12; i++) {
    const ch = base[i];
    const val = ch === " " ? 37 : ch === "Ñ" ? 38 : RFC_TABLA.indexOf(ch);
    suma += (val < 0 ? 0 : val) * (13 - i);
  }
  const r = 11 - (suma % 11);
  return r === 11 ? "0" : r === 10 ? "A" : String(r);
}
function validarRfcEmisor(rfc: string): { ok: boolean; motivo: string } {
  const r = rfc.trim().toUpperCase();
  if (!r) return { ok: false, motivo: "" };
  if (r === "XAXX010101000" || r === "XEXX010101000")
    return { ok: false, motivo: "Los RFC genéricos del SAT no pueden ser el emisor" };
  if (!RFC_RE.test(r))
    return { ok: false, motivo: "Formato inválido: 3-4 letras + fecha (AAMMDD) + homoclave" };
  if (rfcDigitoVerificador(r.slice(0, -1)) !== r[r.length - 1])
    return { ok: false, motivo: "No pasa el dígito verificador del SAT — revisa dígitos transpuestos o la última letra" };
  return { ok: true, motivo: "" };
}

// Catálogo de entidades federativas (clave de 3 letras del SAT c_Estado).
const MX_ESTADOS: ComboOption[] = [
  ["AGU", "Aguascalientes"], ["BCN", "Baja California"], ["BCS", "Baja California Sur"],
  ["CAM", "Campeche"], ["CHP", "Chiapas"], ["CHH", "Chihuahua"], ["COA", "Coahuila"],
  ["COL", "Colima"], ["CMX", "Ciudad de México"], ["DUR", "Durango"], ["MEX", "Estado de México"],
  ["GUA", "Guanajuato"], ["GRO", "Guerrero"], ["HID", "Hidalgo"], ["JAL", "Jalisco"],
  ["MIC", "Michoacán"], ["MOR", "Morelos"], ["NAY", "Nayarit"], ["NLE", "Nuevo León"],
  ["OAX", "Oaxaca"], ["PUE", "Puebla"], ["QUE", "Querétaro"], ["ROO", "Quintana Roo"],
  ["SLP", "San Luis Potosí"], ["SIN", "Sinaloa"], ["SON", "Sonora"], ["TAB", "Tabasco"],
  ["TAM", "Tamaulipas"], ["TLA", "Tlaxcala"], ["VER", "Veracruz"], ["YUC", "Yucatán"],
  ["ZAC", "Zacatecas"],
].map(([value, label]) => ({ value, label }));

type Empresa = {
  legal_name: string;
  rfc: string;
  regimen_fiscal_sat: string;
  domicilio_fiscal_cp: string;
  domicilio_fiscal: Record<string, unknown>;
  has_logo: boolean;
};

type Csd = {
  Rfc?: string;
  rfc?: string;
  CsdCerExpirationDate?: string;
  ExpirationDate?: string;
  SerialNumber?: string;
  Serial?: string;
  [k: string]: unknown;
};

type FormState = {
  legal_name: string;
  rfc: string;
  regimen_fiscal_sat: string;
  domicilio_fiscal_cp: string;
  calle: string;
  colonia: string;
  ciudad: string;
  estado: string;
  pais: string;
};

const emptyForm = (): FormState => ({
  legal_name: "",
  rfc: "",
  regimen_fiscal_sat: "",
  domicilio_fiscal_cp: "",
  calle: "",
  colonia: "",
  ciudad: "",
  estado: "",
  pais: "",
});

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

// Acepta una clave SAT ("JAL") o un nombre ("Jalisco") y devuelve la clave.
function normalizaEstado(v: string): string {
  const s = v.trim();
  if (!s) return "";
  if (MX_ESTADOS.some((o) => o.value === s)) return s;
  const byLabel = MX_ESTADOS.find((o) => o.label.toLowerCase() === s.toLowerCase());
  return byLabel ? byLabel.value : s;
}

export default function EmpresaPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [form, setForm] = useState<FormState>(emptyForm());
  const [loading, setLoading] = useState(true);
  // Error real (500/red) al cargar los datos del emisor. Mientras esté activo
  // el formulario queda bloqueado: guardar con el form vacío sobrescribiría
  // los datos fiscales reales.
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  // null = sin verificar · true = SAT ok · false = el SAT lo rechazó
  const [verified, setVerified] = useState<boolean | null>(null);
  // Modo bloqueado: tras guardar, los campos quedan de solo lectura hasta
  // confirmar la edición (evita cambios accidentales en datos fiscales).
  const [locked, setLocked] = useState(false);
  const [editConfirmOpen, setEditConfirmOpen] = useState(false);

  // Recarga el checklist de onboarding tras guardar datos o subir CSD.
  const [onboardingKey, setOnboardingKey] = useState(0);
  const { status: onboardingStatus } = useOnboarding(onboardingKey);
  const ambiente = onboardingStatus?.ambiente ?? "sandbox";

  const [csds, setCsds] = useState<Csd[]>([]);

  // Validación LOCAL del CSD (sin subirlo a Facturama): en cuanto están los 3
  // datos, el backend prueba cert/llave/contraseña y la UI pinta ✓/✗ por campo.
  type CsdCheck = {
    cer_ok: boolean; cer_detalle: string;
    key_ok: boolean; key_detalle: string;
    password_ok: boolean; password_detalle: string;
    par_ok: boolean; par_detalle: string;
    rfc_cert: string | null; rfc_coincide: boolean | null;
    vigente: boolean | null; vigencia_fin: string | null;
    es_fiel: boolean | null; valido: boolean;
  };
  const [csdCheck, setCsdCheck] = useState<CsdCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [cerFile, setCerFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [csdPassword, setCsdPassword] = useState("");
  const [uploading, setUploading] = useState(false);

  // Logo del emisor (para el PDF de las facturas). Se previsualiza vía fetch
  // autenticado → object URL (el endpoint requiere Bearer, un <img src> no basta).
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [logoUploading, setLogoUploading] = useState(false);

  async function loadLogo() {
    try {
      const res = await authFetch("/api/v1/empresa/logo");
      if (!res.ok) { setLogoUrl((u) => { if (u) URL.revokeObjectURL(u); return null; }); return; }
      const blob = await res.blob();
      setLogoUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return URL.createObjectURL(blob); });
    } catch {
      setLogoUrl(null);
    }
  }

  function loadCsds() {
    apiFetch<Csd[]>("/api/v1/empresa/csd")
      .then((list) => setCsds(Array.isArray(list) ? list : []))
      .catch(() => setCsds([]));
  }

  useEffect(() => {
    apiFetch<Empresa>("/api/v1/empresa")
      .then((e) => {
        const dom = (e.domicilio_fiscal ?? {}) as Record<string, unknown>;
        setForm({
          legal_name: e.legal_name || "",
          rfc: e.rfc || "",
          regimen_fiscal_sat: e.regimen_fiscal_sat || "",
          domicilio_fiscal_cp: e.domicilio_fiscal_cp || "",
          calle: str(dom.calle),
          colonia: str(dom.colonia),
          ciudad: str(dom.ciudad),
          estado: normalizaEstado(str(dom.estado)),
          // País fijo: siempre México (se ignora cualquier valor guardado distinto).
          pais: "México",
        });
        // Si ya hay datos fiscales guardados, arranca bloqueado.
        if ((e.legal_name || "").trim() || (e.rfc || "").trim()) setLocked(true);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 404) {
          // Sin datos previos: deja el formulario vacío (modo edición).
          return;
        }
        // Error real (500/red): NO tratarlo como "sin datos" — un guardado con
        // el formulario vacío pisaría los datos fiscales reales del emisor.
        setLoadError(true);
      })
      .finally(() => setLoading(false));
    loadCsds();
    loadLogo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function set(patch: Partial<FormState>) {
    setForm((f) => ({ ...f, ...patch }));
  }

  // Veredicto local en vivo (formato + dígito verificador), sin gastar folios.
  const rfcLocal = validarRfcEmisor(form.rfc);
  const rfcEnRojo = (form.rfc.trim() !== "" && !rfcLocal.ok) || verified === false;

  // Si el RFC está en rojo, el formulario se queda en modo edición solo —
  // sin que el usuario tenga que dar clic en "Editar".
  useEffect(() => {
    if (rfcEnRojo) setLocked(false);
  }, [rfcEnRojo]);

  async function verificarRfc() {
    const rfc = form.rfc.trim().toUpperCase();
    if (!rfc) {
      toast.error("Captura primero el RFC");
      return;
    }
    if (!rfcLocal.ok) {
      // Ni siquiera pasa la validación local: no gastar un folio del PAC.
      setVerified(false);
      toast.error(rfcLocal.motivo);
      return;
    }
    setVerifying(true);
    try {
      const r = await apiFetch<{ FormatoCorrecto: boolean; Activo: boolean; Localizado: boolean }>(
        `/api/v1/clientes/validar-rfc?rfc=${encodeURIComponent(rfc)}`,
      );
      const ok = r.FormatoCorrecto && r.Activo && r.Localizado;
      setVerified(ok);
      if (ok) {
        toast.success("RFC verificado: activo y localizado en el SAT");
      } else {
        toast.error(
          `RFC: formato ${r.FormatoCorrecto ? "ok" : "inválido"}, activo ${r.Activo ? "sí" : "no"}, localizado ${r.Localizado ? "sí" : "no"}`,
        );
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo verificar el RFC");
    } finally {
      setVerifying(false);
    }
  }

  async function guardar() {
    if (loadError) {
      toast.error("No se pudieron cargar los datos actuales; recarga la página antes de guardar");
      return;
    }
    if (!form.legal_name.trim()) {
      toast.error("La razón social es obligatoria");
      return;
    }
    if (!form.rfc.trim()) {
      toast.error("El RFC es obligatorio");
      return;
    }
    if (!rfcLocal.ok) {
      toast.error(`RFC inválido: ${rfcLocal.motivo}`);
      return;
    }
    if (!form.domicilio_fiscal_cp.trim()) {
      toast.error("El código postal es obligatorio");
      return;
    }
    const domicilio_fiscal: Record<string, string> = {};
    for (const k of ["calle", "colonia", "ciudad", "estado", "pais"] as const) {
      const val = form[k].trim();
      if (val) domicilio_fiscal[k] = val;
    }
    setSaving(true);
    try {
      await apiFetch<Empresa>("/api/v1/empresa", {
        method: "PUT",
        body: JSON.stringify({
          legal_name: form.legal_name.trim(),
          rfc: form.rfc.trim().toUpperCase(),
          regimen_fiscal_sat: form.regimen_fiscal_sat.trim(),
          domicilio_fiscal_cp: form.domicilio_fiscal_cp.trim(),
          domicilio_fiscal,
        }),
      });
      toast.success("Datos fiscales guardados");
      setLocked(true);
      setOnboardingKey((k) => k + 1);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    } finally {
      setSaving(false);
    }
  }

  // Corre la prueba local cada que cambian archivo/contraseña (debounce para
  // no disparar en cada tecla de la contraseña).
  useEffect(() => {
    if (!cerFile || !keyFile || !csdPassword) {
      setCsdCheck(null);
      return;
    }
    let cancelado = false;
    const t = setTimeout(async () => {
      setChecking(true);
      try {
        const fd = new FormData();
        fd.append("cer", cerFile);
        fd.append("key", keyFile);
        fd.append("password", csdPassword);
        const res = await authFetch("/api/v1/empresa/csd/validar", { method: "POST", body: fd });
        if (!res.ok) throw await toApiError(res);
        const data = (await res.json()) as CsdCheck;
        if (!cancelado) setCsdCheck(data);
      } catch {
        if (!cancelado) setCsdCheck(null);
      } finally {
        if (!cancelado) setChecking(false);
      }
    }, 600);
    return () => { cancelado = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cerFile, keyFile, csdPassword]);

  // Label de campo del CSD con su veredicto: ✓ verde, ✗ rojo o neutro.
  function EtiquetaCsd({ texto, ok }: { texto: string; ok: boolean | null }) {
    return (
      <span className="mb-1 flex items-center gap-1.5 text-sm font-medium">
        {texto}
        {ok === true && <Check size={15} className="text-success" aria-label="válido" />}
        {ok === false && <X size={15} className="text-danger" aria-label="inválido" />}
      </span>
    );
  }

  async function subirCsd() {
    if (!cerFile || !keyFile) {
      toast.error("Selecciona el archivo .cer y el .key");
      return;
    }
    if (!csdPassword) {
      toast.error("Indica la contraseña de la llave privada");
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("cer", cerFile);
      fd.append("key", keyFile);
      fd.append("password", csdPassword);
      const res = await authFetch("/api/v1/empresa/csd", { method: "POST", body: fd });
      if (!res.ok) throw await toApiError(res);
      toast.success("CSD subido correctamente");
      setCerFile(null);
      setKeyFile(null);
      setCsdPassword("");
      loadCsds();
      setOnboardingKey((k) => k + 1);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo subir el CSD");
    } finally {
      setUploading(false);
    }
  }

  async function subirLogo() {
    if (!logoFile) {
      toast.error("Selecciona una imagen (PNG, JPG o WebP)");
      return;
    }
    setLogoUploading(true);
    try {
      const fd = new FormData();
      fd.append("logo", logoFile);
      const res = await authFetch("/api/v1/empresa/logo", { method: "POST", body: fd });
      if (!res.ok) throw await toApiError(res);
      toast.success("Logo actualizado");
      setLogoFile(null);
      loadLogo();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo subir el logo");
    } finally {
      setLogoUploading(false);
    }
  }

  async function quitarLogo() {
    setLogoUploading(true);
    try {
      await apiFetch("/api/v1/empresa/logo", { method: "DELETE" });
      toast.success("Logo eliminado");
      setLogoUrl((u) => { if (u) URL.revokeObjectURL(u); return null; });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar el logo");
    } finally {
      setLogoUploading(false);
    }
  }

  // Solo lectura mientras no se esté editando (loading, sin permiso, bloqueado
  // o con error de carga: sin los datos actuales no es seguro editar/guardar).
  const ro = !canWrite || loading || locked || loadError;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Empresa"
        subtitle="Datos fiscales del emisor y sellos digitales (CSD)"
      />

      <OnboardingChecklist refreshKey={onboardingKey} />

      <Card title="Datos fiscales" subtitle="Información del emisor que aparece en los CFDIs">
        {loadError && (
          <Alert tone="danger" title="No se pudieron cargar los datos fiscales">
            El formulario queda bloqueado para no sobrescribir la información real del emisor.
            Recarga la página e inténtalo de nuevo.
          </Alert>
        )}
        <div className={`grid grid-cols-1 gap-4 sm:grid-cols-2 ${loadError ? "mt-4" : ""}`}>
          <div className="sm:col-span-2">
            <Field label="Razón social" required>
              <Input
                placeholder="Empresa SA de CV"
                value={form.legal_name}
                onChange={(e) => set({ legal_name: e.target.value })}
                disabled={ro}
              />
            </Field>
          </div>
          <Field label="RFC" required>
            <div className="flex items-center gap-2">
              <Input
                placeholder="XAXX010101000"
                value={form.rfc}
                onChange={(e) => {
                  set({ rfc: e.target.value.toUpperCase() });
                  setVerified(null); // cualquier cambio invalida la verificación previa
                }}
                disabled={ro}
              />
              {verified === true ? (
                <Button variant="success" onClick={verificarRfc} disabled={verifying || loadError}>
                  <Check size={16} /> RFC verificado
                </Button>
              ) : rfcEnRojo ? (
                <Button variant="danger" onClick={verificarRfc} disabled={verifying || loadError}>
                  <X size={16} /> {verifying ? "Verificando…" : "RFC inválido"}
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={verificarRfc}
                  disabled={verifying || loading || loadError}
                >
                  <ShieldCheck size={16} /> {verifying ? "Verificando…" : "Verificar RFC"}
                </Button>
              )}
            </div>
            {form.rfc.trim() !== "" && !rfcLocal.ok && rfcLocal.motivo && (
              <span className="mt-1 block text-xs text-danger">{rfcLocal.motivo}</span>
            )}
            {verified === false && rfcLocal.ok && (
              <span className="mt-1 block text-xs text-danger">
                El SAT no valida este RFC (inactivo o no localizado). Corrígelo antes de continuar.
              </span>
            )}
          </Field>
          <Field label="Régimen fiscal SAT">
            <Select
              value={form.regimen_fiscal_sat}
              onChange={(e) => set({ regimen_fiscal_sat: e.target.value })}
              disabled={ro}
            >
              <option value="">— Selecciona —</option>
              {REGIMENES_FISCALES.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Código postal" required>
            <Input
              placeholder="11000"
              value={form.domicilio_fiscal_cp}
              onChange={(e) => set({ domicilio_fiscal_cp: e.target.value })}
              disabled={ro}
            />
          </Field>
          <div className="sm:col-span-2">
            <Field label="Calle y número">
              <Input
                value={form.calle}
                onChange={(e) => set({ calle: e.target.value })}
                disabled={ro}
              />
            </Field>
          </div>
          <Field label="Colonia">
            <Input
              value={form.colonia}
              onChange={(e) => set({ colonia: e.target.value })}
              disabled={ro}
            />
          </Field>
          <Field label="Ciudad/Municipio">
            <Input
              value={form.ciudad}
              onChange={(e) => set({ ciudad: e.target.value })}
              disabled={ro}
            />
          </Field>
          <Field label="Estado">
            <KeyboardCombobox
              options={MX_ESTADOS}
              value={form.estado}
              onSelect={(v) => set({ estado: v })}
              disabled={ro}
              placeholder="Busca tu estado…"
              emptyText="Sin coincidencias"
            />
          </Field>
          <Field label="País">
            {/* Fijo: el emisor de un CFDI siempre es mexicano. */}
            <Input value="México" disabled />
          </Field>
        </div>

        {canWrite && (
          <div className="mt-4 flex items-center gap-2 border-t border-border pt-4">
            {locked ? (
              <Button variant="secondary" onClick={() => setEditConfirmOpen(true)} disabled={loading}>
                <Pencil size={16} /> Editar
              </Button>
            ) : (
              <Button onClick={guardar} disabled={saving || loading || loadError}>
                <Building2 size={16} /> {saving ? "Guardando…" : "Guardar"}
              </Button>
            )}
            {locked && (
              <span className="text-xs text-muted">
                Datos bloqueados. Pulsa Editar para modificarlos.
              </span>
            )}
          </div>
        )}
      </Card>

      <Card title="Sellos digitales (CSD)" subtitle="Certificado de Sello Digital del SAT para timbrar">
        <div className="space-y-4">
          {ambiente === "producción" ? (
            <Alert tone="success">
              Ambiente de <b>producción</b>: los CFDI que timbres aquí son reales ante el SAT.
            </Alert>
          ) : (
            <Alert tone="warning">
              En modo sandbox se usan certificados de prueba; para timbrar con tu CSD
              real se requiere la cuenta de Facturama en producción.
            </Alert>
          )}

          <p className="text-sm text-muted">
            Sube tu .cer y .key del SAT y la contraseña de la llave privada.
            Necesario para timbrar con tu RFC.
          </p>

          {canWrite && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="block">
                <EtiquetaCsd
                  texto="Certificado (.cer)"
                  ok={csdCheck ? csdCheck.cer_ok && csdCheck.vigente !== false && csdCheck.rfc_coincide !== false && !csdCheck.es_fiel : null}
                />
                <Input
                  type="file"
                  accept=".cer"
                  onChange={(e) => setCerFile(e.target.files?.[0] ?? null)}
                  disabled={uploading}
                />
                {csdCheck?.cer_detalle && (
                  <span className="mt-1 block text-xs text-danger">{csdCheck.cer_detalle}</span>
                )}
              </label>
              <label className="block">
                <EtiquetaCsd texto="Llave privada (.key)" ok={csdCheck ? csdCheck.key_ok && (csdCheck.par_ok || !csdCheck.password_ok) : null} />
                <Input
                  type="file"
                  accept=".key"
                  onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)}
                  disabled={uploading}
                />
                {csdCheck?.key_detalle && (
                  <span className="mt-1 block text-xs text-danger">{csdCheck.key_detalle}</span>
                )}
                {csdCheck?.par_detalle && (
                  <span className="mt-1 block text-xs text-danger">{csdCheck.par_detalle}</span>
                )}
              </label>
              <label className="block">
                <EtiquetaCsd texto="Contraseña de la llave privada" ok={csdCheck ? csdCheck.password_ok : null} />
                <PasswordInput
                  value={csdPassword}
                  onChange={(e) => setCsdPassword(e.target.value)}
                  disabled={uploading}
                />
                {csdCheck?.password_detalle && (
                  <span className="mt-1 block text-xs text-danger">{csdCheck.password_detalle}</span>
                )}
              </label>
              <div className="flex items-end">
                <Button onClick={subirCsd} disabled={uploading}>
                  <Upload size={16} /> {uploading ? "Subiendo…" : "Subir CSD"}
                </Button>
              </div>
            </div>
          )}

          {checking && (
            <p className="text-xs text-muted">Probando el certificado y la llave…</p>
          )}
          {csdCheck?.valido && (
            <Alert tone="success">
              ✅ CSD válido: RFC {csdCheck.rfc_cert}, la contraseña abre la llave y corresponde
              al certificado{csdCheck.vigencia_fin ? ` (vigente hasta ${csdCheck.vigencia_fin})` : ""}.
              Ya puedes dar clic en “Subir CSD”.
            </Alert>
          )}

          <div>
            <div className="mb-2 text-sm font-medium">CSD cargados</div>
            {csds.length === 0 ? (
              <p className="text-sm text-muted">No hay sellos digitales cargados.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted">
                    <th className="py-1.5 pr-3 font-medium">RFC</th>
                    <th className="py-1.5 pr-3 font-medium">No. de serie</th>
                    <th className="py-1.5 font-medium">Vigencia</th>
                  </tr>
                </thead>
                <tbody>
                  {csds.map((c, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="py-1.5 pr-3">{c.Rfc ?? c.rfc ?? "—"}</td>
                      <td className="py-1.5 pr-3">{c.SerialNumber ?? c.Serial ?? "—"}</td>
                      <td className="py-1.5">
                        {fmtDate((c.ExpirationDate ?? c.CsdCerExpirationDate) as string | undefined)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </Card>

      <Card title="Logo de la empresa" subtitle="Aparece en la representación impresa de tus facturas (arriba a la derecha)">
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Sube tu logo en PNG, JPG o WebP (máx. 2 MB). Se mostrará en el PDF de las facturas.
          </p>

          {logoUrl && (
            <div className="flex items-center gap-4">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={logoUrl} alt="Logo de la empresa" className="max-h-20 rounded border border-border bg-white p-2" />
              <span className="flex items-center gap-1.5 text-sm font-medium text-success">
                <Check size={15} /> Logo cargado
              </span>
              {canWrite && (
                <Button variant="secondary" onClick={quitarLogo} disabled={logoUploading}>
                  Quitar logo
                </Button>
              )}
            </div>
          )}

          {canWrite && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Imagen del logo">
                <Input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(e) => setLogoFile(e.target.files?.[0] ?? null)}
                  disabled={logoUploading}
                />
              </Field>
              <div className="flex items-end">
                <Button onClick={subirLogo} disabled={logoUploading || !logoFile}>
                  <Upload size={16} /> {logoUploading ? "Subiendo…" : "Subir logo"}
                </Button>
              </div>
            </div>
          )}
        </div>
      </Card>

      <ConfirmDialog
        open={editConfirmOpen}
        title="Editar datos fiscales"
        message="Estás a punto de modificar los datos fiscales del emisor. Estos datos aparecen en los CFDIs timbrados. ¿Deseas continuar?"
        confirmLabel="Sí, editar"
        confirmVariant="primary"
        onConfirm={() => {
          setLocked(false);
          setEditConfirmOpen(false);
        }}
        onClose={() => setEditConfirmOpen(false)}
      />
    </div>
  );
}

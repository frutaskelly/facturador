"use client";

import { useEffect, useState } from "react";
import { Building2, CheckCircle2, Pencil, ShieldCheck, Upload } from "lucide-react";

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
  const [verified, setVerified] = useState(false);
  // Modo bloqueado: tras guardar, los campos quedan de solo lectura hasta
  // confirmar la edición (evita cambios accidentales en datos fiscales).
  const [locked, setLocked] = useState(false);
  const [editConfirmOpen, setEditConfirmOpen] = useState(false);

  // Recarga el checklist de onboarding tras guardar datos o subir CSD.
  const [onboardingKey, setOnboardingKey] = useState(0);
  const { status: onboardingStatus } = useOnboarding(onboardingKey);
  const ambiente = onboardingStatus?.ambiente ?? "sandbox";

  const [csds, setCsds] = useState<Csd[]>([]);
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
          pais: str(dom.pais),
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

  async function verificarRfc() {
    const rfc = form.rfc.trim().toUpperCase();
    if (!rfc) {
      toast.error("Captura primero el RFC");
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
                  setVerified(false); // cualquier cambio invalida la verificación previa
                }}
                disabled={ro}
              />
              {verified ? (
                <Badge tone="success">
                  <CheckCircle2 size={12} className="mr-1" /> Verificado
                </Badge>
              ) : (
                <Button
                  variant="secondary"
                  onClick={verificarRfc}
                  disabled={verifying || loading || locked || loadError}
                >
                  <ShieldCheck size={16} /> {verifying ? "Verificando…" : "Verificar RFC"}
                </Button>
              )}
            </div>
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
            <Input
              value={form.pais}
              onChange={(e) => set({ pais: e.target.value })}
              disabled={ro}
            />
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
              <Field label="Certificado (.cer)">
                <Input
                  type="file"
                  accept=".cer"
                  onChange={(e) => setCerFile(e.target.files?.[0] ?? null)}
                  disabled={uploading}
                />
              </Field>
              <Field label="Llave privada (.key)">
                <Input
                  type="file"
                  accept=".key"
                  onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)}
                  disabled={uploading}
                />
              </Field>
              <Field label="Contraseña de la llave privada">
                <PasswordInput
                  value={csdPassword}
                  onChange={(e) => setCsdPassword(e.target.value)}
                  disabled={uploading}
                />
              </Field>
              <div className="flex items-end">
                <Button onClick={subirCsd} disabled={uploading}>
                  <Upload size={16} /> {uploading ? "Subiendo…" : "Subir CSD"}
                </Button>
              </div>
            </div>
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

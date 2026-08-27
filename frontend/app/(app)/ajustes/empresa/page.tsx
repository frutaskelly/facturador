"use client";

// Ajustes › Empresas — la lista de las empresas del usuario.
//
// Una tarjeta por empresa, con lo que le falta a cada una para poder facturar y
// sus tres verbos: Editar (panel lateral, sin cambiarte de empresa), Entrar
// (switch en esta pestaña) y abrir en pestaña nueva. La configuración pesada de
// una empresa (sello CSD, logo, correo) vive en /ajustes/empresa/configuracion,
// que siempre actúa sobre la empresa de ESA pestaña.
import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  Check,
  ExternalLink,
  LogIn,
  Pencil,
  Plus,
  Settings2,
  ShieldCheck,
  Wand2,
  Upload,
  X,
} from "lucide-react";

import { AgregarEmpresaModal } from "@/components/AgregarEmpresaModal";
import { KeyboardCombobox } from "@/components/KeyboardCombobox";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input, PasswordInput, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { COLORES_EMPRESA, colorEmpresa, inicialesEmpresa } from "@/lib/empresa-color";
import { fmtDate } from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { ESTADOS_MX, REGIMENES_FISCALES, normalizaEstado, validarRfcEmisor } from "@/lib/sat";
import { urlEnEmpresa } from "@/lib/tenant";
import type { EmpresaGrupo, EmpresasGrupo } from "@/lib/types";

const CONFIGURACION = "/ajustes/empresa/configuracion";

function Cuadro({ color, texto, grande }: { color: string; texto: string; grande?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center rounded-lg font-semibold text-white ${
        grande ? "h-9 w-9 text-sm" : "h-5 w-5 rounded-md text-[10px]"
      }`}
      style={{ background: color }}
    >
      {texto}
    </span>
  );
}

function Chip({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${
        ok
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border-amber-200 bg-amber-50 text-amber-700"
      }`}
    >
      {ok ? (
        <Check size={11} strokeWidth={3} />
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      )}
      {children}
    </span>
  );
}

// ── Panel lateral ───────────────────────────────────────────────────────────
// Misma regla que <Modal>: NO se cierra con Escape ni con clic fuera — cerrarlo
// por accidente a media captura tira todo lo escrito. Solo Cancelar y la X.
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function PanelLateral({
  open,
  onClose,
  encabezado,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  encabezado: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Tab") return;
      const root = panelRef.current;
      if (!root) return;
      const focusables = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusables.length === 0) return;
      const primero = focusables[0];
      const ultimo = focusables[focusables.length - 1];
      const activo = document.activeElement;
      if (!(activo instanceof Node) || !root.contains(activo)) {
        e.preventDefault();
        primero.focus();
      } else if (e.shiftKey && (activo === primero || activo === root)) {
        e.preventDefault();
        ultimo.focus();
      } else if (!e.shiftKey && activo === ultimo) {
        e.preventDefault();
        primero.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previo?.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="flex h-full w-full max-w-md flex-col border-l border-border bg-background shadow-xl outline-none"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-5 py-3">
          {encabezado}
          <button
            onClick={onClose}
            aria-label="Cerrar"
            className="rounded-lg p-1.5 text-muted hover:bg-surface-2"
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex shrink-0 items-center gap-2 border-t border-border px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

/** Pass-through de Facturama: cada sello trae su serie y su vigencia. */
type Csd = {
  CsdCerExpirationDate?: string;
  ExpirationDate?: string;
  SerialNumber?: string;
  Serial?: string;
  [k: string]: unknown;
};

// ── Formulario fiscal del panel ─────────────────────────────────────────────
type FormState = {
  legal_name: string;
  rfc: string;
  regimen_fiscal_sat: string;
  domicilio_fiscal_cp: string;
  calle: string;
  colonia: string;
  ciudad: string;
  estado: string;
};

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function formDesde(e: EmpresaGrupo): FormState {
  const dom = e.domicilio_fiscal ?? {};
  return {
    legal_name: e.legal_name,
    rfc: e.rfc,
    regimen_fiscal_sat: e.regimen_fiscal_sat,
    domicilio_fiscal_cp: e.domicilio_fiscal_cp,
    calle: str(dom.calle),
    colonia: str(dom.colonia),
    ciudad: str(dom.ciudad),
    estado: normalizaEstado(str(dom.estado)),
  };
}

export default function EmpresasPage() {
  const { me, switchTenant, refreshMe } = useAuth();
  const toast = useToast();
  const { put, loading: guardando } = useMutation();
  const { data, loading, error, reload, setData } = useResource<EmpresasGrupo>("/api/v1/empresa/grupo");

  const [agregar, setAgregar] = useState(false);
  const [editando, setEditando] = useState<EmpresaGrupo | null>(null);
  const [form, setForm] = useState<FormState | null>(null);

  // ── Sello digital de la empresa que se está editando ──
  const [csds, setCsds] = useState<Csd[] | null>(null);
  const [csdNota, setCsdNota] = useState<string | null>(null);
  const [cerFile, setCerFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [csdPassword, setCsdPassword] = useState("");
  const [subiendo, setSubiendo] = useState(false);
  const [cambiandoColor, setCambiandoColor] = useState(false);

  const editandoId = editando?.tenant_id ?? null;
  useEffect(() => {
    if (!editandoId) return;
    let vivo = true;
    setCsds(null);
    setCsdNota(null);
    apiFetch<Csd[]>(`/api/v1/empresa/${editandoId}/csd`)
      .then((data) => vivo && setCsds(data))
      .catch((e: unknown) => {
        if (!vivo) return;
        setCsds([]);
        // 503 = Facturama sin configurar: no es un error del usuario.
        setCsdNota(e instanceof ApiError ? e.message : "No se pudo consultar el sello");
      });
    return () => {
      vivo = false;
    };
  }, [editandoId]);

  function abrirEdicion(e: EmpresaGrupo) {
    setEditando(e);
    setForm(formDesde(e));
  }

  function cerrarEdicion() {
    setEditando(null);
    setForm(null);
    setCerFile(null);
    setKeyFile(null);
    setCsdPassword("");
  }

  /** `null` devuelve la empresa al color automático. Se aplica al instante. */
  async function elegirColor(color: string | null) {
    if (!editando || cambiandoColor) return;
    setCambiandoColor(true);
    try {
      await apiFetch(`/api/v1/empresa/${editando.tenant_id}/color`, {
        method: "PUT",
        body: JSON.stringify({ color }),
      });
      setEditando({ ...editando, color });
      // Repinta la tarjeta en su lugar: recargar la lista entera cerraría el
      // sentido de "ver el cambio mientras eliges".
      setData((prev) =>
        prev
          ? {
              ...prev,
              empresas: prev.empresas.map((x) =>
                x.tenant_id === editando.tenant_id ? { ...x, color } : x
              ),
            }
          : prev
      );
      if (editando.es_actual) refreshMe(); // el switcher del Topbar
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cambiar el color");
    } finally {
      setCambiandoColor(false);
    }
  }

  async function subirSello() {
    if (!editando || !cerFile || !keyFile || !csdPassword) return;
    const fd = new FormData();
    fd.append("cer", cerFile);
    fd.append("key", keyFile);
    fd.append("password", csdPassword);
    setSubiendo(true);
    try {
      // apiFetch respeta el FormData (no fuerza Content-Type JSON).
      await apiFetch(`/api/v1/empresa/${editando.tenant_id}/csd`, { method: "POST", body: fd });
      toast.success(`Sello cargado en ${editando.legal_name}`);
      setCerFile(null);
      setKeyFile(null);
      setCsdPassword("");
      setCsds(await apiFetch<Csd[]>(`/api/v1/empresa/${editando.tenant_id}/csd`));
      reload(); // el chip "Sello CSD" de la tarjeta
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo subir el sello");
    } finally {
      setSubiendo(false);
    }
  }

  function set(patch: Partial<FormState>) {
    setForm((f) => (f ? { ...f, ...patch } : f));
  }

  const rfcCheck = form ? validarRfcEmisor(form.rfc) : { ok: true, motivo: "" };

  async function guardar() {
    if (!editando || !form) return;
    if (!form.legal_name.trim()) {
      toast.error("La razón social es obligatoria");
      return;
    }
    if (!rfcCheck.ok) {
      toast.error(`RFC inválido: ${rfcCheck.motivo}`);
      return;
    }
    if (!/^\d{5}$/.test(form.domicilio_fiscal_cp.trim())) {
      toast.error("El código postal debe tener 5 dígitos");
      return;
    }
    const domicilio_fiscal: Record<string, string> = {};
    for (const k of ["calle", "colonia", "ciudad", "estado"] as const) {
      const val = form[k].trim();
      if (val) domicilio_fiscal[k] = val;
    }
    try {
      await put(`/api/v1/empresa/${editando.tenant_id}`, {
        legal_name: form.legal_name.trim(),
        rfc: form.rfc.trim().toUpperCase(),
        regimen_fiscal_sat: form.regimen_fiscal_sat.trim(),
        domicilio_fiscal_cp: form.domicilio_fiscal_cp.trim(),
        domicilio_fiscal,
      });
      toast.success(`Datos guardados en ${form.legal_name.trim()}`);
      cerrarEdicion();
      reload();
      // El nombre de la empresa activa se pinta desde /auth/me (Topbar, switcher).
      if (editando.es_actual) refreshMe();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    }
  }

  const empresas = data?.empresas ?? [];
  const puedeAgregar = !!data?.puede_agregar;

  return (
    <>
      <PageHeader
        title="Empresas"
        subtitle={
          data
            ? `${data.grupo_total} de ${data.grupo_max} · todas comparten tu plan y tus usuarios`
            : undefined
        }
        actions={
          puedeAgregar ? (
            <Button onClick={() => setAgregar(true)}>
              <Plus size={16} /> Agregar empresa
            </Button>
          ) : undefined
        }
      />

      {loading && (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      )}

      {error && !loading && (
        <Alert tone="danger" title="No se pudieron cargar tus empresas">
          {error}
        </Alert>
      )}

      {!loading && !error && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {empresas.map((e) => {
            const color = colorEmpresa(e.tenant_id, e.color);
            const nombre = e.legal_name || e.trade_name || e.slug;
            return (
              <article
                key={e.tenant_id}
                className="relative flex flex-col gap-3 overflow-hidden rounded-xl border border-border bg-background p-4"
                style={e.es_actual ? { boxShadow: `0 0 0 1.5px ${color}` } : undefined}
              >
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-0 w-[3px]"
                  style={{ background: color }}
                />

                <div className="flex items-start gap-2.5">
                  <Cuadro color={color} texto={inicialesEmpresa(nombre)} grande />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold" title={nombre}>
                      {nombre}
                    </div>
                    <div className="font-mono text-xs text-muted">{e.rfc || "Sin RFC"}</div>
                  </div>
                  {e.es_actual && (
                    <span
                      className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white"
                      style={{ background: color }}
                    >
                      Aquí estás
                    </span>
                  )}
                </div>

                <div className="flex flex-wrap gap-1.5">
                  <Chip ok={e.datos_fiscales}>Datos fiscales</Chip>
                  <Chip ok={e.csd}>Sello CSD</Chip>
                  <Chip ok={e.logo}>Logo</Chip>
                  <Chip ok={e.series}>Series</Chip>
                  <Chip ok={e.correo}>Correo</Chip>
                </div>

                <div className="mt-auto flex items-center gap-2 border-t border-border pt-3">
                  {e.puede_editar && (
                    <Button variant="secondary" onClick={() => abrirEdicion(e)}>
                      <Pencil size={15} /> Editar
                    </Button>
                  )}
                  {e.es_actual ? (
                    e.puede_editar && (
                    <Link
                      href={CONFIGURACION}
                      className="inline-flex items-center gap-1.5 rounded-lg px-2 py-2 text-sm text-muted hover:bg-surface-2 hover:text-foreground"
                    >
                      <Settings2 size={15} /> Logo y correo
                    </Link>
                    )
                  ) : (
                    <>
                      <Button variant="secondary" onClick={() => switchTenant(e.tenant_id)}>
                        <LogIn size={15} /> Entrar
                      </Button>
                      <a
                        href={urlEnEmpresa("/dashboard", e.slug)}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`Abrir ${nombre} en otra pestaña`}
                        aria-label={`Abrir ${nombre} en otra pestaña`}
                        className="ml-auto inline-flex items-center rounded-lg border border-border px-2.5 py-2 text-muted hover:bg-surface-2 hover:text-foreground"
                      >
                        <ExternalLink size={15} />
                      </a>
                    </>
                  )}
                </div>
              </article>
            );
          })}

          {puedeAgregar && (
            <button
              onClick={() => setAgregar(true)}
              className="flex min-h-[9rem] flex-col items-center justify-center gap-1 rounded-xl border-[1.5px] border-dashed border-border p-5 text-center text-muted transition hover:border-accent hover:text-foreground"
            >
              <Plus size={20} className="text-accent" />
              <span className="text-sm font-semibold text-foreground">Agregar empresa</span>
              <span className="text-xs">Otro RFC del mismo dueño</span>
            </button>
          )}
        </div>
      )}

      {agregar && <AgregarEmpresaModal onClose={() => setAgregar(false)} />}

      <PanelLateral
        open={!!editando && !!form}
        onClose={cerrarEdicion}
        encabezado={
          editando ? (
            <div className="flex min-w-0 items-center gap-2.5">
              <Cuadro
                color={colorEmpresa(editando.tenant_id, editando.color)}
                texto={inicialesEmpresa(editando.legal_name || editando.slug)}
                grande
              />
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">{editando.legal_name}</div>
                <div className="font-mono text-xs text-muted">{editando.rfc}</div>
              </div>
            </div>
          ) : null
        }
        footer={
          editando ? (
            <>
              {!editando.es_actual && (
                <a
                  href={urlEnEmpresa(CONFIGURACION, editando.slug)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
                >
                  Logo y correo <ExternalLink size={13} />
                </a>
              )}
              <div className="ml-auto flex gap-2">
                <Button variant="secondary" onClick={cerrarEdicion} disabled={guardando}>
                  Cancelar
                </Button>
                <Button onClick={guardar} disabled={guardando}>
                  {guardando ? "Guardando…" : "Guardar"}
                </Button>
              </div>
            </>
          ) : null
        }
      >
        {form && editando && (
          <div className="flex flex-col gap-3">
            {!editando.es_actual && (
              <Alert tone="info">
                Estás editando otra empresa. Tu sesión sigue en{" "}
                <strong>
                  {me?.tenants.find((t) => t.tenant_id === me?.active_tenant.tenant_id)?.name ??
                    "la empresa actual"}
                </strong>
                .
              </Alert>
            )}

            <div>
              <span className="mb-1.5 block text-sm font-medium">Color</span>
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => elegirColor(null)}
                  disabled={cambiandoColor}
                  aria-pressed={!editando.color}
                  title="Automático"
                  className={`flex h-7 items-center gap-1 rounded-lg border px-2 text-xs transition disabled:opacity-50 ${
                    !editando.color
                      ? "border-accent text-foreground"
                      : "border-border text-muted hover:bg-surface-2"
                  }`}
                >
                  <Wand2 size={12} /> Automático
                </button>
                {COLORES_EMPRESA.map((c) => {
                  const elegido = editando.color === c;
                  return (
                    <button
                      key={c}
                      type="button"
                      onClick={() => elegirColor(c)}
                      disabled={cambiandoColor}
                      aria-pressed={elegido}
                      aria-label={`Usar el color ${c}`}
                      className="flex h-7 w-7 items-center justify-center rounded-lg transition disabled:opacity-50"
                      style={{
                        background: c,
                        boxShadow: elegido ? "0 0 0 2px var(--background), 0 0 0 4px var(--accent)" : undefined,
                      }}
                    >
                      {elegido && <Check size={13} strokeWidth={3} className="text-white" />}
                    </button>
                  );
                })}
              </div>
            </div>

            <Field label="Razón social" required>
              <Input
                value={form.legal_name}
                onChange={(ev) => set({ legal_name: ev.target.value })}
                placeholder="Como aparece en la constancia de situación fiscal"
              />
            </Field>

            <Field label="RFC" required hint={form.rfc && !rfcCheck.ok ? rfcCheck.motivo : undefined}>
              <Input
                value={form.rfc}
                onChange={(ev) => set({ rfc: ev.target.value.toUpperCase() })}
                className={form.rfc && !rfcCheck.ok ? "border-danger" : ""}
                maxLength={13}
              />
            </Field>

            <Field label="Régimen fiscal" required>
              <Select
                value={form.regimen_fiscal_sat}
                onChange={(ev) => set({ regimen_fiscal_sat: ev.target.value })}
              >
                <option value="">Selecciona…</option>
                {REGIMENES_FISCALES.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Código postal" required>
              <Input
                value={form.domicilio_fiscal_cp}
                onChange={(ev) => set({ domicilio_fiscal_cp: ev.target.value })}
                inputMode="numeric"
                maxLength={5}
              />
            </Field>

            <Field label="Calle y número">
              <Input value={form.calle} onChange={(ev) => set({ calle: ev.target.value })} />
            </Field>

            <Field label="Colonia">
              <Input value={form.colonia} onChange={(ev) => set({ colonia: ev.target.value })} />
            </Field>

            <Field label="Ciudad">
              <Input value={form.ciudad} onChange={(ev) => set({ ciudad: ev.target.value })} />
            </Field>

            <Field label="Estado">
              <KeyboardCombobox
                value={form.estado}
                onSelect={(v) => set({ estado: v })}
                options={ESTADOS_MX}
                placeholder="Busca la entidad…"
                ariaLabel="Estado"
              />
            </Field>

            {/* ── Sello digital ──────────────────────────────────────────── */}
            <div className="mt-2 flex flex-col gap-3 border-t border-border pt-4">
              <div className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-muted" />
                <h3 className="text-sm font-semibold">Sello digital (CSD)</h3>
              </div>

              {csds === null ? (
                <div className="flex items-center gap-2 text-sm text-muted">
                  <Spinner /> Consultando el sello…
                </div>
              ) : csds.length > 0 ? (
                <div className="flex flex-col gap-1">
                  {csds.map((c, i) => (
                    <Chip key={i} ok>
                      Vigente al{" "}
                      {fmtDate((c.ExpirationDate ?? c.CsdCerExpirationDate) as string | undefined)}
                      {" · serie "}
                      {(c.SerialNumber ?? c.Serial ?? "—") as string}
                    </Chip>
                  ))}
                </div>
              ) : (
                <Chip ok={false}>Sin sello cargado — esta empresa no puede timbrar</Chip>
              )}

              {csdNota && <p className="text-xs text-muted">{csdNota}</p>}

              {/* El sello se emite POR RFC: se valida contra el RFC ya guardado,
                  no contra lo que esté escrito arriba sin guardar. */}
              <p className="text-xs text-muted">
                Se valida contra el RFC guardado: <span className="font-mono">{editando.rfc || "—"}</span>
              </p>

              <Field label="Certificado (.cer)">
                <input
                  type="file"
                  accept=".cer"
                  onChange={(ev) => setCerFile(ev.target.files?.[0] ?? null)}
                  className="block w-full text-sm text-muted file:mr-3 file:rounded-lg file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-foreground hover:file:bg-surface-2"
                />
              </Field>

              <Field label="Llave privada (.key)">
                <input
                  type="file"
                  accept=".key"
                  onChange={(ev) => setKeyFile(ev.target.files?.[0] ?? null)}
                  className="block w-full text-sm text-muted file:mr-3 file:rounded-lg file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-foreground hover:file:bg-surface-2"
                />
              </Field>

              <Field label="Contraseña de la llave privada">
                <PasswordInput
                  value={csdPassword}
                  onChange={(ev) => setCsdPassword(ev.target.value)}
                  autoComplete="off"
                />
              </Field>

              <Button
                variant="secondary"
                onClick={subirSello}
                disabled={subiendo || !cerFile || !keyFile || !csdPassword || !editando.rfc}
              >
                <Upload size={15} /> {subiendo ? "Subiendo…" : "Subir sello"}
              </Button>
            </div>
          </div>
        )}
      </PanelLateral>
    </>
  );
}

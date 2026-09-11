"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, Mail, Send, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Field, Input, PasswordInput, Select } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";

const WRITE = "membership:gestionar";

type CorreoConfig = {
  host: string;
  port: number;
  username: string;
  from_email: string;
  from_name: string | null;
  use_ssl: boolean;
  configured: boolean;
  has_password: boolean;
  verificado_at?: string | null;
  prueba_enviada?: boolean;
  aviso?: string | null;
};

type FormState = {
  host: string;
  port: string;
  username: string;
  password: string;
  from_email: string;
  from_name: string;
  use_ssl: "SSL" | "TLS";
};

const emptyForm = (): FormState => ({
  host: "",
  port: "587",
  username: "",
  password: "",
  from_email: "",
  from_name: "",
  use_ssl: "TLS",
});

export default function CorreoPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [form, setForm] = useState<FormState>(emptyForm());
  const [hasPassword, setHasPassword] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testTo, setTestTo] = useState("");
  // Flujo guiado (ticket 86bbxkzz5): sin config previa se pide SOLO el correo
  // y la pregunta ¿es Gmail?; el modo Gmail esconde los campos técnicos que el
  // preset ya sabe. «Configuración avanzada» los destapa cuando hagan falta.
  const [paso, setPaso] = useState<"correo" | "form">("form");
  const [correoInicial, setCorreoInicial] = useState("");
  const [modoGmail, setModoGmail] = useState(false);
  const [avanzado, setAvanzado] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [verificadoAt, setVerificadoAt] = useState<string | null>(null);
  const [quitarOpen, setQuitarOpen] = useState(false);
  const [quitando, setQuitando] = useState(false);

  useEffect(() => {
    apiFetch<CorreoConfig>("/api/v1/correo")
      .then((cfg) => {
        setForm({
          host: cfg.host || "",
          port: String(cfg.port || 587),
          username: cfg.username || "",
          password: "",
          from_email: cfg.from_email || "",
          from_name: cfg.from_name || "",
          use_ssl: cfg.use_ssl ? "SSL" : "TLS",
        });
        setHasPassword(cfg.has_password);
        setConfigured(cfg.configured);
        setVerificadoAt(cfg.verificado_at ?? null);
        if (!cfg.host) setPaso("correo");
        else if ((cfg.host || "").includes("gmail")) setModoGmail(true);
        if (!testTo && cfg.username) setTestTo(cfg.username);
      })
      .catch(() => {
        setPaso("correo"); /* sin config previa: arranca el flujo guiado */
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function set(patch: Partial<FormState>) {
    setForm((f) => ({ ...f, ...patch }));
  }

  function presetGmail() {
    set({ host: "smtp.gmail.com", port: "465", use_ssl: "SSL" });
  }

  /** Paso 1 → 2: aplica lo que el sistema ya sabe y deja solo lo que depende
   *  del usuario (nombre del remitente y contraseña de aplicación). */
  function elegirGmail(esGmail: boolean) {
    const correo = correoInicial.trim();
    if (!correo || !correo.includes("@")) {
      toast.error("Escribe primero tu correo electrónico");
      return;
    }
    if (esGmail) {
      set({ host: "smtp.gmail.com", port: "465", use_ssl: "SSL", username: correo, from_email: correo });
      setModoGmail(true);
      setAvanzado(false);
    } else {
      set({ username: correo, from_email: correo });
      setModoGmail(false);
      setAvanzado(true);
    }
    setPaso("form");
  }

  function buildBody() {
    const body: Record<string, unknown> = {
      host: form.host.trim(),
      port: Number(form.port) || 587,
      username: form.username.trim(),
      from_email: form.from_email.trim(),
      from_name: form.from_name.trim() || null,
      use_ssl: form.use_ssl === "SSL",
    };
    // Sólo enviamos contraseña si el usuario escribió una (vacía conserva la actual).
    if (form.password) body.password = form.password;
    return body;
  }

  async function guardar() {
    if (!form.host.trim() || !form.username.trim() || !form.from_email.trim()) {
      toast.error("Servidor, usuario y remitente son obligatorios");
      return;
    }
    setSaving(true);
    try {
      const cfg = await apiFetch<CorreoConfig>("/api/v1/correo", {
        method: "PUT",
        body: JSON.stringify(buildBody()),
      });
      setHasPassword(cfg.has_password);
      setConfigured(cfg.configured);
      setVerificadoAt(cfg.verificado_at ?? null);
      set({ password: "" });
      toast.success(
        cfg.prueba_enviada
          ? `Guardada ✓ — te enviamos un correo a ${form.from_email.trim() || form.username.trim()}: pulsa «Verificar correo» para terminar`
          : "Configuración guardada (el login SMTP se comprobó) ✓",
      );
      if (cfg.aviso) toast.info(cfg.aviso);
    } catch (e) {
      toast.error(
        e instanceof ApiError ? e.message
          : e instanceof Error && e.message ? e.message
          : "No se pudo guardar la configuración de correo.",
      );
    } finally {
      setSaving(false);
    }
  }

  /** Empezar de cero (feedback 86bbxkzz5): borra la config y regresa al
   *  paso 1 del flujo guiado. Antes no había forma de resetear. */
  async function quitarConfiguracion() {
    setQuitando(true);
    try {
      await apiFetch("/api/v1/correo", { method: "DELETE" });
      setForm(emptyForm());
      setHasPassword(false);
      setConfigured(false);
      setVerificadoAt(null);
      setModoGmail(false);
      setAvanzado(false);
      setCorreoInicial("");
      setQuitarOpen(false);
      setPaso("correo");
      toast.success("Configuración eliminada: puedes empezar de cero");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo quitar la configuración");
    } finally {
      setQuitando(false);
    }
  }

  async function probar() {
    if (!testTo.trim()) {
      toast.error("Indica un correo destinatario de prueba");
      return;
    }
    setTesting(true);
    try {
      await apiFetch("/api/v1/correo/probar", {
        method: "POST",
        body: JSON.stringify({ to: testTo.trim() }),
      });
      toast.success(`Correo de prueba enviado a ${testTo.trim()}`);
    } catch (e) {
      toast.error(
        e instanceof ApiError ? e.message
          : e instanceof Error && e.message ? e.message
          : "No se pudo enviar la prueba.",
      );
    } finally {
      setTesting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Correo"
        subtitle="Conecta una cuenta de correo para enviar remisiones a tus clientes"
        actions={
          canWrite ? (
            <Button variant="secondary" onClick={presetGmail}>
              <Sparkles size={16} /> Preset Gmail
            </Button>
          ) : undefined
        }
      />

      {configured && (
        <div className="mb-3">
          {verificadoAt ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-3 py-1 text-sm text-success">
              <CheckCircle2 size={15} /> Correo verificado
              <span className="text-muted">· {new Date(verificadoAt).toLocaleString("es-MX")}</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-warning/40 bg-surface-2 px-3 py-1 text-sm text-warning">
              Guardada, SIN verificar — abre el correo «Verifica tu configuración» que te
              enviamos y pulsa su botón (o guarda de nuevo para reenviarlo)
            </span>
          )}
        </div>
      )}

      {paso === "correo" ? (
        <div className="max-w-2xl space-y-4 rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Mail size={16} /> Conecta la cuenta desde la que se enviarán tus facturas y remisiones
          </div>
          <Field label="Tu correo electrónico" required>
            <Input
              placeholder="ventas@empresa.com"
              value={correoInicial}
              onChange={(e) => setCorreoInicial(e.target.value)}
              disabled={!canWrite}
            />
          </Field>
          <div className="text-sm">¿Es un correo Gmail?</div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => elegirGmail(true)} disabled={!canWrite}>
              <Sparkles size={16} /> Sí — configurar automáticamente
            </Button>
            <Button variant="secondary" onClick={() => elegirGmail(false)} disabled={!canWrite}>
              No — configurar manualmente
            </Button>
          </div>
          <p className="text-xs text-muted">
            Con Gmail solo te pediremos el nombre del remitente y una contraseña de
            aplicación; el resto lo llenamos nosotros.
          </p>
        </div>
      ) : (
      <div className="max-w-2xl space-y-4 rounded-xl border border-border p-4">
        <div className="rounded-lg bg-surface-2 p-3 text-sm">
          <div className="mb-2 flex items-center gap-2 font-medium">
            <Mail size={16} /> Cómo conectar una cuenta de Gmail
          </div>
          <ol className="ml-4 list-decimal space-y-1.5 text-muted">
            <li>Abre la cuenta de Gmail desde la que se van a enviar los correos.</li>
            <li>
              Activa la verificación en dos pasos (2FA) en{" "}
              <a
                href="https://myaccount.google.com/security"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-accent hover:underline"
              >
                myaccount.google.com/security <ExternalLink size={12} />
              </a>
              .
            </li>
            <li>
              Genera una contraseña de aplicación en{" "}
              <a
                href="https://myaccount.google.com/apppasswords"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-accent hover:underline"
              >
                myaccount.google.com/apppasswords <ExternalLink size={12} />
              </a>
              .
            </li>
            <li>
              Pega esos 16 caracteres en <strong>Contraseña</strong> (abajo). Usa{" "}
              <strong>Preset Gmail</strong> para autocompletar servidor/puerto, y pon
              tu correo completo en <strong>Usuario</strong> y en{" "}
              <strong>Remitente (email)</strong> — Gmail exige que sean el mismo.
            </li>
          </ol>
        </div>

        {modoGmail && !avanzado && (
          <div className="flex items-center justify-between rounded-lg border border-border p-3 text-sm">
            <span className="text-muted">
              Gmail configurado: <b>{form.username || "—"}</b> · smtp.gmail.com · 465 · SSL
            </span>
            <button type="button" className="text-accent hover:underline" onClick={() => setAvanzado(true)}>
              Configuración avanzada
            </button>
          </div>
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {(!modoGmail || avanzado) && (<>
          <Field label="Servidor (host)" required>
            <Input
              placeholder="smtp.gmail.com"
              value={form.host}
              onChange={(e) => set({ host: e.target.value })}
              disabled={!canWrite || loading}
            />
          </Field>
          <Field label="Puerto" required>
            <Input
              type="number"
              value={form.port}
              onChange={(e) => set({ port: e.target.value })}
              disabled={!canWrite || loading}
            />
          </Field>
          </>)}
          {(!modoGmail || avanzado) && (
          <Field label="Usuario" required hint="Tu correo completo (con el que te autenticas)">
            <Input
              placeholder="ventas@empresa.com"
              value={form.username}
              onChange={(e) => set({ username: e.target.value })}
              disabled={!canWrite || loading}
            />
          </Field>
          )}
          <Field label="Contraseña" hint={hasPassword ? "Deja en blanco para conservar la actual" : undefined}>
            <PasswordInput
              placeholder={hasPassword ? "•••• (sin cambios)" : ""}
              value={form.password}
              onChange={(e) => set({ password: e.target.value })}
              disabled={!canWrite || loading}
            />
          </Field>
          <Field label="Remitente (nombre)">
            <Input
              placeholder="Empresa SA de CV"
              value={form.from_name}
              onChange={(e) => set({ from_name: e.target.value })}
              disabled={!canWrite || loading}
            />
          </Field>
          {(!modoGmail || avanzado) && (
          <Field
            label="Remitente (email)"
            required
            hint={
              form.username && form.from_email !== form.username
                ? "En Gmail debe ser el mismo correo que Usuario"
                : undefined
            }
          >
            <div className="flex gap-2">
              <Input
                placeholder="ventas@empresa.com"
                value={form.from_email}
                onChange={(e) => set({ from_email: e.target.value })}
                disabled={!canWrite || loading}
              />
              {form.username && form.from_email !== form.username && (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => set({ from_email: form.username })}
                  disabled={!canWrite || loading}
                >
                  Igual que Usuario
                </Button>
              )}
            </div>
          </Field>
          )}
          {(!modoGmail || avanzado) && (
          <Field label="Conexión segura">
            <Select
              value={form.use_ssl}
              onChange={(e) => set({ use_ssl: e.target.value as "SSL" | "TLS" })}
              disabled={!canWrite || loading}
            >
              <option value="SSL">SSL (puerto 465)</option>
              <option value="TLS">TLS / STARTTLS (puerto 587)</option>
            </Select>
          </Field>
          )}
        </div>

        {canWrite && (
          <div className="flex flex-wrap items-end gap-3 border-t border-border pt-4">
            <Button onClick={guardar} disabled={saving || loading}>
              {saving ? "Guardando…" : "Guardar"}
            </Button>
            {configured && (
              <Button variant="secondary" onClick={() => setQuitarOpen(true)} disabled={saving || loading}>
                Quitar configuración
              </Button>
            )}
            <div className="flex items-end gap-2">
              <Field label="Enviar prueba a">
                <Input
                  placeholder="correo@ejemplo.com"
                  value={testTo}
                  onChange={(e) => setTestTo(e.target.value)}
                  disabled={testing}
                />
              </Field>
              <Button variant="secondary" onClick={probar} disabled={testing || loading}>
                <Send size={16} /> {testing ? "Enviando…" : "Probar"}
              </Button>
            </div>
          </div>
        )}
      </div>
      )}

      <ConfirmDialog
        open={quitarOpen}
        title="Quitar la configuración de correo"
        message="Se borra la cuenta configurada (servidor, usuario, contraseña y verificación) y la pantalla regresa al inicio para configurar desde cero. Mientras no haya otra cuenta guardada, no se podrán enviar facturas ni remisiones por correo."
        confirmLabel="Quitar configuración"
        onConfirm={() => void quitarConfiguracion()}
        onClose={() => setQuitarOpen(false)}
        loading={quitando}
      />
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Mail, Send, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/Button";
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
        if (!testTo && cfg.username) setTestTo(cfg.username);
      })
      .catch(() => {
        /* sin config previa: deja el formulario vacío */
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
      set({ password: "" });
      toast.success("Configuración guardada y verificada ✓");
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

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
          <Field label="Usuario" required hint="Tu correo completo (con el que te autenticas)">
            <Input
              placeholder="ventas@empresa.com"
              value={form.username}
              onChange={(e) => set({ username: e.target.value })}
              disabled={!canWrite || loading}
            />
          </Field>
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
        </div>

        {canWrite && (
          <div className="flex flex-wrap items-end gap-3 border-t border-border pt-4">
            <Button onClick={guardar} disabled={saving || loading}>
              {saving ? "Guardando…" : "Guardar"}
            </Button>
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
    </div>
  );
}

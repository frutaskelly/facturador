"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input, PasswordInput, Select } from "@/components/ui/Field";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { REGIMENES_FISCALES } from "@/lib/sat";
import { getSupabase } from "@/lib/supabaseClient";

const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";

type TurnstileApi = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => void;
};

export default function SignupPage() {
  const router = useRouter();
  const { session } = useAuth();

  const [legalName, setLegalName] = useState("");
  const [rfc, setRfc] = useState("");
  const [regimen, setRegimen] = useState("");
  const [cp, setCp] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [hp, setHp] = useState(""); // honeypot anti-bot (oculto)
  const [captchaToken, setCaptchaToken] = useState("");
  const captchaRef = useRef<HTMLDivElement>(null);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmSent, setConfirmSent] = useState(false);

  useEffect(() => {
    if (session) router.replace("/dashboard");
  }, [session, router]);

  // Carga y renderiza el widget de Turnstile solo si hay site key configurada.
  useEffect(() => {
    if (!TURNSTILE_SITE_KEY) return;
    const SCRIPT_ID = "cf-turnstile-script";
    const render = () => {
      const w = window as unknown as { turnstile?: TurnstileApi };
      if (w.turnstile && captchaRef.current && !captchaRef.current.hasChildNodes()) {
        w.turnstile.render(captchaRef.current, {
          sitekey: TURNSTILE_SITE_KEY,
          callback: (t: string) => setCaptchaToken(t),
          "error-callback": () => setCaptchaToken(""),
          "expired-callback": () => setCaptchaToken(""),
        });
      }
    };
    if (!document.getElementById(SCRIPT_ID)) {
      const s = document.createElement("script");
      s.id = SCRIPT_ID;
      s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      s.async = true;
      s.defer = true;
      s.onload = render;
      document.head.appendChild(s);
    } else {
      render();
    }
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!regimen) {
      setError("Selecciona el régimen fiscal de tu empresa");
      return;
    }
    if (password.length < 8) {
      setError("La contraseña debe tener al menos 8 caracteres");
      return;
    }
    if (TURNSTILE_SITE_KEY && !captchaToken) {
      setError("Completa la verificación anti-bot");
      return;
    }

    const cleanEmail = email.trim().toLowerCase();
    setBusy(true);
    try {
      const res = await apiFetch<{ email_confirmation_required?: boolean }>("/api/v1/registro", {
        method: "POST",
        body: JSON.stringify({
          legal_name: legalName.trim(),
          rfc: rfc.trim().toUpperCase(),
          regimen_fiscal_sat: regimen,
          domicilio_fiscal_cp: cp.trim(),
          owner_email: cleanEmail,
          owner_name: ownerName.trim() || null,
          password,
          website: hp || null,
          turnstile_token: captchaToken || null,
        }),
      });

      // Si se exige confirmar correo: dispara el envío y muestra estado pendiente.
      if (res?.email_confirmation_required) {
        try {
          await getSupabase().auth.resend({ type: "signup", email: cleanEmail });
        } catch {
          /* el correo de confirmación puede reenviarse desde login */
        }
        setConfirmSent(true);
        return;
      }

      // Flujo normal → iniciar sesión y mandar al onboarding fiscal.
      const { error: signInError } = await getSupabase().auth.signInWithPassword({
        email: cleanEmail,
        password,
      });
      if (signInError) {
        // La cuenta se creó pero el auto-login falló: manda a login manual.
        router.replace("/login");
        return;
      }
      router.replace("/onboarding");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo completar el registro");
    } finally {
      setBusy(false);
    }
  }

  if (confirmSent) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-surface px-4">
        <div className="w-full max-w-md rounded-2xl border border-border bg-background p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold tracking-tight">Confirma tu correo</h1>
          <p className="mt-2 text-sm text-muted">
            Te enviamos un correo a <span className="font-medium text-foreground">{email}</span>.
            Ábrelo y confirma tu cuenta para poder iniciar sesión.
          </p>
          <Link href="/login" className="mt-6 inline-block text-sm font-medium hover:underline">
            Ir a iniciar sesión
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <div className="w-full max-w-md rounded-2xl border border-border bg-background p-8 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight">Crea tu cuenta</h1>
        <p className="mt-1 text-sm text-muted">
          Registra tu empresa para empezar a facturar
        </p>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          {/* Honeypot anti-bot: oculto y no enfocable; un humano nunca lo llena. */}
          <input
            type="text"
            name="website"
            tabIndex={-1}
            autoComplete="off"
            aria-hidden="true"
            value={hp}
            onChange={(e) => setHp(e.target.value)}
            style={{ position: "absolute", left: "-9999px", width: 1, height: 1, opacity: 0 }}
          />

          <div className="text-xs font-medium uppercase tracking-wide text-muted">
            Tu empresa
          </div>
          <Field label="Razón social" required>
            <Input
              placeholder="Empresa SA de CV"
              required
              value={legalName}
              onChange={(e) => setLegalName(e.target.value)}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="RFC" required>
              <Input
                placeholder="XAXX010101000"
                required
                value={rfc}
                onChange={(e) => setRfc(e.target.value.toUpperCase())}
              />
            </Field>
            <Field label="Código postal" required>
              <Input
                placeholder="11000"
                inputMode="numeric"
                required
                value={cp}
                onChange={(e) => setCp(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Régimen fiscal SAT" required>
            <Select value={regimen} onChange={(e) => setRegimen(e.target.value)} required>
              <option value="">— Selecciona —</option>
              {REGIMENES_FISCALES.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>

          <div className="pt-2 text-xs font-medium uppercase tracking-wide text-muted">
            Tu cuenta
          </div>
          <Field label="Tu nombre">
            <Input
              autoComplete="name"
              value={ownerName}
              onChange={(e) => setOwnerName(e.target.value)}
            />
          </Field>
          <Field label="Correo" required>
            <Input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Field label="Contraseña" required>
            <PasswordInput
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {TURNSTILE_SITE_KEY && <div ref={captchaRef} className="flex justify-center" />}

          {error && <Alert tone="danger">{error}</Alert>}

          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Creando cuenta…" : "Crear cuenta"}
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-muted">
          ¿Ya tienes cuenta?{" "}
          <Link href="/login" className="font-medium text-foreground hover:underline">
            Inicia sesión
          </Link>
        </p>
      </div>
    </main>
  );
}

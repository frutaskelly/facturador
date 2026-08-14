"use client";

/**
 * Panel de administración de plataforma (admin.smartsupply.mx).
 *
 * Independiente del shell de la app (vive fuera del grupo (app)): maneja su
 * propio login y consume los endpoints /api/v1/platform/*, que el backend
 * gatea contra la lista blanca PLATFORM_OPERATORS. Solo lectura.
 */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { ApiError, apiFetch } from "@/lib/api";
import { getSupabase } from "@/lib/supabaseClient";
import { LogoMark, Wordmark } from "@/components/Logo";

type TenantUser = {
  email: string | null;
  full_name: string | null;
  role: string | null;
  active: boolean;
};
type TenantRow = {
  id: string;
  slug: string;
  legal_name: string;
  trade_name: string | null;
  rfc: string;
  status: string;
  tier: string;
  plan: string;
  seats_limit: number;
  created_at: string | null;
  user_count: number;
  users: TenantUser[];
};
type TenantsResp = { tenants: TenantRow[]; tenant_count: number; user_count: number };

const STATUS_STYLES: Record<string, string> = {
  ACTIVE: "bg-emerald-100 text-emerald-700",
  TRIAL: "bg-amber-100 text-amber-700",
  SUSPENDED: "bg-red-100 text-red-700",
  CHURNED: "bg-gray-200 text-gray-600",
};

// Errores de Supabase Auth (vienen en inglés) → es-MX. Cubre los casos
// comunes; cualquier otro cae en un mensaje genérico.
function loginErrorMessage(error: { code?: string; message: string }): string {
  const code = error.code ?? "";
  const msg = error.message.toLowerCase();
  if (code === "invalid_credentials" || msg.includes("invalid login credentials"))
    return "Correo o contraseña incorrectos.";
  if (code === "email_not_confirmed" || msg.includes("email not confirmed"))
    return "Tu correo aún no está confirmado. Revisa tu bandeja de entrada.";
  if (code === "over_request_rate_limit" || msg.includes("rate limit"))
    return "Demasiados intentos. Espera unos minutos e inténtalo de nuevo.";
  if (msg.includes("fetch") || msg.includes("network"))
    return "No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.";
  return "No se pudo iniciar sesión. Inténtalo de nuevo.";
}

export default function AdminPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const supabase = getSupabase();
    supabase.auth
      .getSession()
      .then((res: { data: { session: Session | null } }) => {
        setSession(res.data.session);
        setReady(true);
      });
    const sub = supabase.auth.onAuthStateChange(
      (_e: AuthChangeEvent, next: Session | null) => setSession(next),
    );
    return () => sub.data.subscription.unsubscribe();
  }, []);

  if (!ready) return <Centered>Cargando…</Centered>;
  if (!session) return <LoginCard />;
  return <Dashboard onSignedOut={() => setSession(null)} />;
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 text-muted">
      {children}
    </main>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2">
      <LogoMark size={30} />
      <Wordmark className="text-[15px]" />
      <span className="text-sm font-medium text-muted">· Admin</span>
    </div>
  );
}

function LoginCard() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await getSupabase().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) setError(loginErrorMessage(error));
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-background p-8 shadow-sm">
        <Brand />
        <h1 className="mt-5 text-xl font-semibold tracking-tight">Panel de administración</h1>
        <p className="mt-1 text-sm text-muted">Acceso solo para operadores de plataforma.</p>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          {error && <Alert tone="danger">{error}</Alert>}
          <Field label="Correo">
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </Field>
          <Field label="Contraseña">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Entrando…" : "Iniciar sesión"}
          </Button>
        </form>
      </div>
    </main>
  );
}

function Dashboard({ onSignedOut }: { onSignedOut: () => void }) {
  const [data, setData] = useState<TenantsResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    apiFetch<TenantsResp>("/api/v1/platform/tenants")
      .then((d) => {
        setData(d);
        setError(null);
        setForbidden(false);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setError(err instanceof ApiError ? err.message : "Error al cargar");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  async function signOut() {
    await getSupabase().auth.signOut();
    onSignedOut();
  }

  return (
    <main className="min-h-screen bg-surface">
      <header className="border-b border-border bg-background">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Brand />
          <Button variant="secondary" onClick={signOut}>
            Cerrar sesión
          </Button>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-8">
        {forbidden ? (
          <Alert tone="danger">
            Tu cuenta no está autorizada como operador de plataforma. Pide que tu correo se
            agregue a <code>PLATFORM_OPERATORS</code>.
          </Alert>
        ) : (
          <>
            <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Stat label="Compañías" value={data?.tenant_count ?? "—"} />
              <Stat label="Usuarios" value={data?.user_count ?? "—"} />
              <Stat
                label="Activas"
                value={
                  data ? data.tenants.filter((t) => t.status === "ACTIVE").length : "—"
                }
              />
            </div>

            {error && <Alert tone="danger">{error}</Alert>}
            {loading && <p className="text-sm text-muted">Cargando compañías…</p>}

            <div className="space-y-4">
              {data?.tenants.map((t) => (
                <TenantCard key={t.id} t={t} />
              ))}
              {data && data.tenants.length === 0 && (
                <p className="text-sm text-muted">No hay compañías registradas todavía.</p>
              )}
            </div>
          </>
        )}
      </div>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <div className="text-2xl font-semibold tracking-tight">{value}</div>
      <div className="mt-1 text-xs uppercase tracking-wide text-muted">{label}</div>
    </div>
  );
}

function TenantCard({ t }: { t: TenantRow }) {
  return (
    <div className="rounded-xl border border-border bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold tracking-tight">{t.trade_name || t.legal_name}</h3>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                STATUS_STYLES[t.status] ?? "bg-gray-100 text-gray-600"
              }`}
            >
              {t.status}
            </span>
          </div>
          <p className="mt-0.5 text-sm text-muted">
            {t.legal_name} · RFC {t.rfc} · {t.slug}
          </p>
        </div>
        <div className="text-right text-sm text-muted">
          <div>
            Plan <span className="font-medium text-foreground">{t.plan}</span> · {t.tier}
          </div>
          <div>
            {t.user_count}/{t.seats_limit} usuarios
          </div>
        </div>
      </div>

      {t.users.length > 0 && (
        <div className="mt-4 overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-surface text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Usuario</th>
                <th className="px-3 py-2 font-medium">Correo</th>
                <th className="px-3 py-2 font-medium">Rol</th>
                <th className="px-3 py-2 font-medium">Estado</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {t.users.map((u, i) => (
                <tr key={i}>
                  <td className="px-3 py-2">{u.full_name || "—"}</td>
                  <td className="px-3 py-2 text-muted">{u.email}</td>
                  <td className="px-3 py-2">{u.role || "—"}</td>
                  <td className="px-3 py-2">
                    {u.active ? (
                      <span className="text-emerald-600">Activo</span>
                    ) : (
                      <span className="text-muted">Inactivo</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";

import { ApiError, apiFetch } from "./api";
import { getSupabase } from "./supabaseClient";

export type Me = {
  auth_user_id: string;
  user_id: string;
  email: string | null;
  active_tenant: { tenant_id: string; role: string; is_owner: boolean };
  tenants: { tenant_id: string; slug: string; name: string; role: string }[];
  permissions: string[];
};

type AuthState = {
  session: Session | null;
  me: Me | null;
  loading: boolean;
  /** Non-null when a session exists but /auth/me failed (e.g. not provisioned). */
  accessError: string | null;
  refreshMe: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessError, setAccessError] = useState<string | null>(null);

  // Track the Supabase session.
  useEffect(() => {
    const supabase = getSupabase();
    supabase.auth
      .getSession()
      .then((res: { data: { session: Session | null } }) => {
        setSession(res.data.session);
        if (!res.data.session) setLoading(false);
      })
      .catch(() => {
        // Refresh token inválido/expirado (sesión vieja en el navegador): tratar
        // como "sin sesión" en vez de dejar la promesa rechazada sin capturar.
        setSession(null);
        setLoading(false);
      });
    const sub = supabase.auth.onAuthStateChange(
      (_event: AuthChangeEvent, next: Session | null) => {
        setSession(next);
        if (!next) {
          setMe(null);
          setAccessError(null);
          setLoading(false);
        }
      }
    );
    return () => sub.data.subscription.unsubscribe();
  }, []);

  // Resolve /auth/me whenever we have a token.
  const token = session?.access_token;
  useEffect(() => {
    if (!token) return;
    let active = true;
    // Spinner global SOLO en la primera resolución (me == null). Supabase rota
    // el access_token en segundo plano (~cada hora / al reenfocar la pestaña),
    // cambiando `token` y re-disparando este efecto; poner loading=true en esos
    // refrescos desmontaría toda la app (el layout muestra Spinner mientras
    // loading) y perdería formularios/modales a medio llenar. Por eso revalidamos
    // en silencio cuando ya tenemos `me`.
    if (!me) setLoading(true);
    apiFetch<Me>("/api/v1/auth/me")
      .then((data) => {
        if (!active) return;
        setMe(data);
        setAccessError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setMe(null);
        setAccessError(err instanceof ApiError ? err.message : "Error de acceso");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // `me` se lee para decidir el spinner inicial; no debe re-disparar el efecto.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const refreshMe = async () => {
    if (!token) return;
    try {
      setMe(await apiFetch<Me>("/api/v1/auth/me"));
      setAccessError(null);
    } catch (err) {
      setAccessError(err instanceof ApiError ? err.message : "Error de acceso");
    }
  };

  const signOut = async () => {
    await getSupabase().auth.signOut();
    setSession(null);
    setMe(null);
    setAccessError(null);
  };

  return (
    <AuthContext.Provider
      value={{ session, me, loading, accessError, refreshMe, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  return ctx;
}

/** Permission check used to gate nav + UI actions. OWNER bypasses everything. */
export function can(me: Me | null, permission?: string): boolean {
  if (!me) return false;
  if (!permission) return true;
  if (me.active_tenant.is_owner) return true;
  return me.permissions.includes(permission);
}

/** True si el usuario tiene AL MENOS UNO de los permisos (o es OWNER). */
export function canAny(me: Me | null, permissions?: string[]): boolean {
  if (!me) return false;
  if (!permissions || permissions.length === 0) return true;
  if (me.active_tenant.is_owner) return true;
  return permissions.some((p) => me.permissions.includes(p));
}

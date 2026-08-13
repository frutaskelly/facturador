"use client";

import { getSupabase } from "./supabaseClient";
import { getActiveTenantId, setActiveTenantId, tenantHeader } from "./tenant";

// Respuestas del backend cuando el selector X-Tenant-Id guardado ya no sirve
// (lo quitaron de esa empresa, o el valor se corrompió). Ver _select_membership
// en backend rbac.py — estos textos son contrato con el frontend.
const TENANT_SELECTOR_ERRORS = new Set([
  "Sin acceso a este tenant",       // 403: ya no es miembro de la empresa elegida
  "Selector de tenant inválido",    // 400: valor guardado corrupto (no es UUID)
]);

/** Selección de empresa obsoleta/corrupta: se limpia y se recarga la app para
 *  reconstruir TODO el estado en la empresa default — jamás se cambia de
 *  empresa en silencio con pantallas montadas (mezclaría datos entre empresas).
 *  Sin loop posible: tras limpiar ya no se manda el header. */
function healStaleTenantSelection(status: number, detail: string): boolean {
  if ((status !== 403 && status !== 400) || !TENANT_SELECTOR_ERRORS.has(detail)) return false;
  if (!getActiveTenantId()) return false;
  setActiveTenantId(null);
  window.location.assign("/dashboard");
  return true;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8011";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** Aplana el `detail` del backend a texto legible: en los 422 de validación,
 *  pydantic devuelve una LISTA de objetos (mostrarla tal cual da "[object Object]"). */
function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        d && typeof d === "object" && typeof (d as { msg?: unknown }).msg === "string"
          ? (d as { msg: string }).msg
          : JSON.stringify(d)
      )
      .join("; ");
  }
  if (detail != null) return JSON.stringify(detail);
  return fallback;
}

/**
 * Authenticated fetch against the backend. Attaches the current Supabase
 * access token as a Bearer JWT. La identidad sale SOLO del token; X-Tenant-Id
 * es un SELECTOR de empresa para usuarios con varias (grupo) que el backend
 * valida contra sus membresías — nunca otorga acceso por sí mismo.
 */
export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const supabase = getSupabase();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const headers = new Headers(init.headers);
  // Con FormData el navegador pone el multipart boundary; forzar JSON lo rompe.
  if (!(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }
  tenantHeader(headers);

  // Timeout por defecto para no dejar un spinner eterno si el backend se
  // cuelga. 60 s: el timbrado con el PAC puede tardar ~30 s. Si el caller pasa
  // su propio `signal`, se respeta tal cual (y su abort se propaga sin traducir).
  const ownTimeout = init.signal == null;
  const signal = init.signal ?? AbortSignal.timeout(60_000);

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers, signal });
  } catch (e) {
    if (ownTimeout && e instanceof DOMException && (e.name === "TimeoutError" || e.name === "AbortError")) {
      throw new ApiError(0, "El servidor tardó demasiado en responder. Revisa tu conexión e intenta de nuevo.");
    }
    throw e;
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = detailToMessage(body.detail, detail);
    } catch {
      /* non-JSON error body */
    }
    healStaleTenantSelection(res.status, detail);
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Descarga autenticada de un archivo binario (XML/PDF) y dispara el guardado. */
export async function apiDownload(path: string, filename: string): Promise<void> {
  const supabase = getSupabase();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers = new Headers();
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  tenantHeader(headers);

  const res = await fetch(`${API_URL}${path}`, { headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = detailToMessage((await res.json()).detail, detail);
    } catch {
      /* binario o vacío */
    }
    healStaleTenantSelection(res.status, detail);
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * Abre un archivo autenticado (p. ej. el PDF de una factura) para vista previa
 * en una pestaña. La pestaña debe abrirse ANTES del fetch (síncrona con el
 * click) para que el navegador no la bloquee como pop-up; aquí solo se le
 * asigna la URL del blob una vez descargado.
 */
export async function apiOpenInTab(path: string, win: Window | null): Promise<void> {
  if (!win) {
    // Pop-up bloqueado: sin pestaña no hay dónde mostrar el archivo. Se lanza
    // como ApiError para que las páginas lo muestren con su toast de error.
    throw new ApiError(
      0,
      "El navegador bloqueó la ventana emergente. Permite las ventanas emergentes para este sitio e intenta de nuevo."
    );
  }
  const supabase = getSupabase();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers = new Headers();
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  tenantHeader(headers);

  const res = await fetch(`${API_URL}${path}`, { headers });
  if (!res.ok) {
    win.close();
    let detail = res.statusText;
    try {
      detail = detailToMessage((await res.json()).detail, detail);
    } catch {
      /* binario o vacío */
    }
    healStaleTenantSelection(res.status, detail);
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  win.location.href = url;
  // Libera el blob cuando la pestaña ya tuvo tiempo de sobra para cargarlo
  // (revocarlo de inmediato rompería la carga del PDF).
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

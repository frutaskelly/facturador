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

/**
 * Base del API derivada del HOST actual: `app.<dominio>` / `admin.<dominio>` →
 * `api.<dominio>`. Así el mismo build sirve varios dominios (facturador.mx y
 * otros dominios) siendo cada uno autocontenido — sin hornear un dominio fijo
 * en build. En localhost/SSR cae a NEXT_PUBLIC_API_URL.
 */
export function apiBaseUrl(): string {
  if (typeof window !== "undefined") {
    const parts = window.location.hostname.split(".");
    if ((parts[0] === "app" || parts[0] === "admin") && parts.length >= 3) {
      return `https://api.${parts.slice(1).join(".")}`;
    }
  }
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8011";
}

export class ApiError extends Error {
  /** El `detail` crudo del backend. Casi siempre basta el mensaje, pero algunos
   *  errores traen datos con los que la pantalla PUEDE hacer algo: el 409 del
   *  alta de productos manda los candidatos duplicados para ofrecer vincular en
   *  vez de crear. */
  constructor(public status: number, message: string, public detail?: unknown) {
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
      .map((d) => {
        if (!d || typeof d !== "object" || typeof (d as { msg?: unknown }).msg !== "string") {
          return JSON.stringify(d);
        }
        // El campo que falló viene en `loc` (["body", "rfc"]); sin él el aviso
        // dice el problema pero no dónde, y no hay nada que ir a corregir.
        const loc = (d as { loc?: unknown }).loc;
        const campo = Array.isArray(loc)
          ? loc.filter((x) => typeof x === "string" && x !== "body").pop()
          : undefined;
        const msg = (d as { msg: string }).msg;
        return campo ? `${campo}: ${msg}` : msg;
      })
      .join("; ");
  }
  // Los errores con datos traen su texto en `mensaje`: sin esto el usuario ve
  // el JSON completo, candidatos incluidos.
  if (detail && typeof detail === "object") {
    const m = (detail as { mensaje?: unknown }).mensaje;
    if (typeof m === "string") return m;
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
  init: RequestInit = {},
  opts: {
    /** Tope de espera en ms (default 60 s). Súbelo en operaciones que
     *  legítimamente tardan más — leer un PDF con IA son ~90 s — o el
     *  navegador aborta una petición que iba bien. */
    timeoutMs?: number;
  } = {},
): Promise<T> {
  // La sesión se lee ANTES de salir a la red; si esta lectura falla (o el
  // refresh del token no pudo completarse), la petición ni siquiera se emite
  // — sin traducirlo, cada pantalla mostraba su mensaje genérico y el fallo
  // no dejaba rastro en el servidor. Se reporta como lo que es: sesión.
  let session: { access_token?: string } | null = null;
  try {
    const supabase = getSupabase();
    const { data, error } = await supabase.auth.getSession();
    session = data.session;
    if (error && !session) {
      throw new ApiError(
        0,
        "No pudimos validar tu sesión. Recarga la página (F5) e intenta de nuevo.",
      );
    }
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(
      0,
      "No pudimos validar tu sesión. Recarga la página (F5) e intenta de nuevo.",
    );
  }

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
  const signal = init.signal ?? AbortSignal.timeout(opts.timeoutMs ?? 60_000);

  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, { ...init, headers, signal });
  } catch (e) {
    if (ownTimeout && e instanceof DOMException && (e.name === "TimeoutError" || e.name === "AbortError")) {
      throw new ApiError(0, "El servidor tardó demasiado en responder. Revisa tu conexión e intenta de nuevo.");
    }
    // Un abort ajeno (el caller canceló) se propaga tal cual.
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    // Falla de RED (sin conexión, o el backend reiniciándose durante un
    // despliegue): fetch lanza TypeError. Sin traducirlo, cada pantalla lo
    // reportaba con su mensaje genérico ("No se pudo leer el archivo"),
    // culpando al dato del usuario en vez de a la conexión.
    throw new ApiError(
      0,
      "No se pudo conectar con el servidor. Puede estar actualizándose; espera unos segundos e intenta de nuevo.",
    );
  }

  if (!res.ok) {
    let detail = res.statusText;
    let crudo: unknown;
    try {
      const body = await res.json();
      crudo = body.detail;
      detail = detailToMessage(body.detail, detail);
    } catch {
      /* non-JSON error body */
    }
    healStaleTenantSelection(res.status, detail);
    throw new ApiError(res.status, detail, crudo);
  }

  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    // Respuesta OK pero el cuerpo llegó truncado o no es JSON (corte del túnel
    // a mitad del stream): también es un fallo de transporte, no del dato que
    // mandó el usuario.
    throw new ApiError(
      0,
      "La respuesta del servidor llegó incompleta. Intenta de nuevo.",
    );
  }
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

  const res = await fetch(`${apiBaseUrl()}${path}`, { headers });
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
 * POST que responde un ARCHIVO (p. ej. el Excel masivo para SAE): manda el
 * body JSON, descarga el blob y respeta el nombre que el servidor puso en
 * Content-Disposition (trae el lote y la fecha; inventarlo aquí lo perdería).
 */
export async function apiDownloadPost(
  path: string,
  body: unknown,
  fallbackFilename: string
): Promise<void> {
  const supabase = getSupabase();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  tenantHeader(headers);

  const res = await fetch(`${apiBaseUrl()}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
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
  const disp = res.headers.get("Content-Disposition") ?? "";
  const m = /filename="?([^";]+)"?/.exec(disp);
  const filename = m ? m[1] : fallbackFilename;
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
  // La pestaña abre EN BLANCO mientras el servidor genera el documento (una
  // impresión de varias facturas tarda varios segundos) y parecía que la
  // acción no funcionó (ticket 86bby39j1): la propia pestaña dice qué pasa.
  // El fetch de abajo la reemplaza con el archivo cuando llega.
  try {
    win.document.write(
      '<!doctype html><title>Generando…</title>' +
      '<body style="margin:0;height:100vh;display:grid;place-items:center;' +
      'font-family:system-ui,sans-serif;color:#555;background:#fafafa">' +
      '<div style="text-align:center"><div style="font-size:15px">Generando el documento…</div>' +
      '<div style="font-size:26px;letter-spacing:6px;animation:pulso 1.1s ease-in-out infinite">•••</div>' +
      "<style>@keyframes pulso{0%,100%{opacity:.25}50%{opacity:1}}</style></div>",
    );
    win.document.close();
  } catch {
    /* pestaña de otra procedencia o ya navegada: el aviso es cortesía, no requisito */
  }
  const supabase = getSupabase();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers = new Headers();
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  tenantHeader(headers);

  const res = await fetch(`${apiBaseUrl()}${path}`, { headers });
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
  // La URL vive mientras viva la app (ticket 86bby3hnp): el botón Descargar
  // del visor de PDF vuelve a PEDIR la URL blob:, y revocada a los 60 s la
  // descarga fallaba con «error de red» aunque el documento se viera en
  // pantalla. El costo es retener el PDF en memoria durante la sesión; el
  // navegador libera todo al cerrar o recargar la app, y `pagehide` recoge
  // lo acumulado antes de eso.
  _urlsAbiertas.push(url);
  win.location.href = url;
}

// Registro de los blobs abiertos en pestañas: se liberan cuando la APP se va
// (recarga o cierre), nunca por temporizador — el visor puede pedirlos de
// vuelta en cualquier momento (su botón de descarga, un F5 en la pestaña).
const _urlsAbiertas: string[] = [];
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    for (const u of _urlsAbiertas) URL.revokeObjectURL(u);
    _urlsAbiertas.length = 0;
  });
}

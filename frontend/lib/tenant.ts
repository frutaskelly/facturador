"use client";

// Empresa (tenant) activa elegida por el usuario — para cuentas con varias
// empresas (grupo). El backend valida SIEMPRE el header X-Tenant-Id contra las
// membresías del usuario (_select_membership): este valor solo SELECCIONA entre
// sus propias empresas; un id ajeno responde 403, nunca da acceso.
//
// La selección vive en la PESTAÑA, no en el navegador: así se pueden tener dos
// empresas abiertas a la vez sin que una le cambie los datos a la otra.
//   sessionStorage → la empresa de ESTA pestaña (manda)
//   localStorage   → la última empresa usada (solo siembra una pestaña nueva)
export const ACTIVE_TENANT_KEY = "ss.active_tenant_id"; // localStorage
const TAB_KEY = "ss.tab_tenant_id"; // sessionStorage
/** Parámetro de URL que abre una empresa concreta en esta pestaña. */
export const EMPRESA_PARAM = "empresa";

export function getActiveTenantId(): string | null {
  if (typeof window === "undefined") return null;
  return (
    window.sessionStorage.getItem(TAB_KEY) ??
    window.localStorage.getItem(ACTIVE_TENANT_KEY)
  );
}

/** Cambia la empresa de esta pestaña Y la recuerda como "la última usada". */
export function setActiveTenantId(tenantId: string | null): void {
  if (typeof window === "undefined") return;
  if (tenantId) {
    window.sessionStorage.setItem(TAB_KEY, tenantId);
    window.localStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
  } else {
    window.sessionStorage.removeItem(TAB_KEY);
    window.localStorage.removeItem(ACTIVE_TENANT_KEY);
  }
}

/** Fija la empresa SOLO de esta pestaña. Abrir la empresa B en una pestaña
 *  nueva no debe cambiar dónde abre la siguiente pestaña en blanco. */
export function setTabTenantId(tenantId: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(TAB_KEY, tenantId);
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function esUuid(valor: string): boolean {
  return UUID_RE.test(valor);
}

/** Lee `?empresa=` y lo BORRA de la barra de direcciones (si no, se arrastraría
 *  a cada enlace que el usuario copie después). Devuelve el valor crudo: puede
 *  ser el slug de la empresa o su id. */
export function tomarEmpresaDeUrl(): string | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const crudo = url.searchParams.get(EMPRESA_PARAM);
  if (!crudo) return null;
  url.searchParams.delete(EMPRESA_PARAM);
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  return crudo.trim() || null;
}

/** Enlace a una pantalla EN OTRA EMPRESA (para abrir en pestaña nueva).
 *  OJO: al abrir un enlace, el navegador COPIA el sessionStorage de la pestaña
 *  origen — por eso la URL manda sobre la memoria de la pestaña, y no al revés. */
export function urlEnEmpresa(path: string, slugOrId: string): string {
  return `${path}?${EMPRESA_PARAM}=${encodeURIComponent(slugOrId)}`;
}

/** Agrega el header de selección de empresa a un fetch autenticado (si hay
 *  selección y el caller no lo puso ya). */
export function tenantHeader(headers: Headers): void {
  const id = getActiveTenantId();
  if (id && !headers.has("X-Tenant-Id")) headers.set("X-Tenant-Id", id);
}

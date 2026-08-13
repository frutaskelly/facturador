"use client";

// Empresa (tenant) activa elegida por el usuario — para cuentas con varias
// empresas (grupo). El backend valida SIEMPRE el header X-Tenant-Id contra las
// membresías del usuario (_select_membership): este valor solo SELECCIONA entre
// sus propias empresas; un id ajeno responde 403, nunca da acceso.
export const ACTIVE_TENANT_KEY = "ss.active_tenant_id";
const KEY = ACTIVE_TENANT_KEY;

export function getActiveTenantId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}

export function setActiveTenantId(tenantId: string | null): void {
  if (typeof window === "undefined") return;
  if (tenantId) window.localStorage.setItem(KEY, tenantId);
  else window.localStorage.removeItem(KEY);
}

/** Agrega el header de selección de empresa a un fetch autenticado (si hay
 *  selección y el caller no lo puso ya). */
export function tenantHeader(headers: Headers): void {
  const id = getActiveTenantId();
  if (id && !headers.has("X-Tenant-Id")) headers.set("X-Tenant-Id", id);
}

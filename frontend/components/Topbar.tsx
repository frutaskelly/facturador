"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown, LogOut, Plus } from "lucide-react";

import { AgregarEmpresaModal } from "@/components/AgregarEmpresaModal";
import { can, useAuth, type Me } from "@/lib/auth";
import { colorEmpresa } from "@/lib/empresa-color";

export function Topbar({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const { switchTenant } = useAuth();
  const tenant = me.tenants.find(
    (t) => t.tenant_id === me.active_tenant.tenant_id
  );
  // El menú aparece con varias empresas O para quien puede agregar la primera
  // hija del grupo desde aquí (dueño y administradores).
  const puedeAgregar = can(me, "membership:gestionar");
  const multi = me.tenants.length > 1 || puedeAgregar;

  const [open, setOpen] = useState(false);
  const [agregar, setAgregar] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Cierra el menú al hacer clic fuera o con Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-background px-6">
      {multi ? (
        <div className="relative" ref={menuRef}>
          {/* Disclosure simple (no listbox ARIA): los botones se navegan con
              Tab/Enter y el menú cierra con Escape o clic fuera. */}
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label="Cambiar de empresa"
            className="-ml-2 flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium text-muted transition hover:bg-surface-2 hover:text-foreground"
          >
            {tenant && (
              <span
                aria-hidden="true"
                className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                style={{ background: colorEmpresa(tenant.tenant_id, tenant.color) }}
              />
            )}
            <span className="max-w-[16rem] truncate">{tenant?.name ?? "—"}</span>
            <ChevronsUpDown size={14} className="shrink-0" />
          </button>
          {open && (
            <div className="absolute left-0 top-full z-50 mt-1 w-64 rounded-xl border border-border bg-background p-1 shadow-lg">
              <div className="px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-muted">
                Empresas
              </div>
              {me.tenants.map((t) => {
                const activa = t.tenant_id === me.active_tenant.tenant_id;
                return (
                  <button
                    key={t.tenant_id}
                    aria-current={activa || undefined}
                    onClick={() => {
                      setOpen(false);
                      switchTenant(t.tenant_id);
                    }}
                    className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left text-sm transition hover:bg-surface-2"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        aria-hidden="true"
                        className="h-5 w-5 shrink-0 rounded-md"
                        style={{ background: colorEmpresa(t.tenant_id, t.color) }}
                      />
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{t.name}</span>
                        <span className="block text-xs text-muted">{t.role}</span>
                      </span>
                    </span>
                    {activa && <Check size={15} className="shrink-0 text-accent" />}
                  </button>
                );
              })}
              {puedeAgregar && (
                <>
                  <div className="my-1 border-t border-border" />
                  <button
                    onClick={() => {
                      setOpen(false);
                      setAgregar(true);
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-muted transition hover:bg-surface-2 hover:text-foreground"
                  >
                    <Plus size={15} className="shrink-0" />
                    Agregar empresa
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="text-sm font-medium text-muted">{tenant?.name ?? "—"}</div>
      )}
      <div className="flex items-center gap-4">
        <div className="text-right leading-tight">
          <div className="text-sm font-medium">{me.email}</div>
          <div className="text-xs text-muted">
            {me.active_tenant.role}
            {me.active_tenant.is_owner ? " · OWNER" : ""}
          </div>
        </div>
        <button
          onClick={onSignOut}
          title="Cerrar sesión"
          aria-label="Cerrar sesión"
          className="rounded-lg p-2 text-muted transition hover:bg-surface-2 hover:text-foreground"
        >
          <LogOut size={18} />
        </button>
      </div>
      {agregar && <AgregarEmpresaModal onClose={() => setAgregar(false)} />}
    </header>
  );
}

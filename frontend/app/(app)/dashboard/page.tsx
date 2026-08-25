"use client";

import { Card } from "@/components/ui/Card";
import { PrimerosPasos } from "@/components/PrimerosPasos";
import { PageHeader } from "@/components/ui/PageHeader";
import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { me } = useAuth();
  if (!me) return null;

  const tenant = me.tenants.find(
    (t) => t.tenant_id === me.active_tenant.tenant_id
  );
  const cards = [
    { label: "Empresa", value: tenant?.name ?? "—" },
    { label: "Tu rol", value: me.active_tenant.is_owner ? "OWNER" : me.active_tenant.role },
    { label: "Permisos activos", value: String(me.permissions.length) },
    { label: "Empresas", value: String(me.tenants.length) },
  ];

  return (
    <div>
      <PageHeader title="Dashboard" subtitle={`Bienvenido, ${me.email}`} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((c) => (
          <Card key={c.label}>
            <div className="text-xs font-medium uppercase tracking-wide text-muted">{c.label}</div>
            <div className="mt-2 text-2xl font-semibold">{c.value}</div>
          </Card>
        ))}
      </div>

      <div className="mt-8">
        <Card title="Módulos">
          <p className="text-sm text-muted">
            El menú lateral muestra solo lo que tu rol permite. Empieza por las
            secciones de Catálogo y Operaciones.
          </p>
        </Card>
      </div>

      <div className="mt-8">
        <PrimerosPasos />
      </div>
    </div>
  );
}

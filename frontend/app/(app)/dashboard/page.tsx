"use client";

// El dashboard abre con el resumen que el negocio llevaba a mano en la hoja
// GRAL de su Excel: una fila por proyecto con su saldo y su vencido, y el
// total abajo. Cada fila enlaza al estado de cuenta del cliente (acotado a su
// serie cuando la fila es de una sola). Solo se pinta para quien puede ver
// cobranza; el resto conserva el dashboard de siempre.
import Link from "next/link";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/Card";
import { PrimerosPasos } from "@/components/PrimerosPasos";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtMoney } from "@/lib/format";

type FilaProyecto = {
  proyecto: string; saldo: string; vencido: string; facturas: number;
  cliente_id: string | null; serie: string | null;
};
type SaldosPorProyecto = {
  corte: string;
  proyectos: FilaProyecto[];
  saldo_total: string; vencido_total: string;
  saldo_en_cancelacion: string;
};

function SaldosPorProyectoCard() {
  const [data, setData] = useState<SaldosPorProyecto | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let vivo = true;
    apiFetch<SaldosPorProyecto>("/api/v1/cobranza/saldos-por-proyecto")
      .then((d) => { if (vivo) setData(d); })
      .catch(() => { if (vivo) setError(true); });
    return () => { vivo = false; };
  }, []);

  if (error) return null;
  if (!data) return <Card title="Saldos por proyecto"><div className="flex justify-center py-6"><Spinner /></div></Card>;

  const destino = (p: FilaProyecto) =>
    p.cliente_id ? `/clientes/${p.cliente_id}/estado-cuenta${p.serie ? `?serie=${p.serie}` : ""}` : null;

  return (
    <Card title={`Saldos por proyecto · corte ${fmtDate(data.corte)}`}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
            <th className="py-1.5">Proyecto</th>
            <th className="py-1.5 text-right">Saldo</th>
            <th className="py-1.5 text-right">Vencido</th>
          </tr>
        </thead>
        <tbody>
          {data.proyectos.map((p) => {
            const href = destino(p);
            const nombre = href
              ? <Link href={href} className="hover:underline">{p.proyecto}</Link>
              : p.proyecto;
            return (
              <tr key={p.proyecto} className="border-b border-border/60">
                <td className="py-1.5 pr-2">{nombre} <span className="text-xs text-muted">· {p.facturas}</span></td>
                <td className="py-1.5 text-right tabular-nums">{fmtMoney(p.saldo)}</td>
                <td className="py-1.5 text-right tabular-nums">
                  {Number(p.vencido) > 0
                    ? <span className="font-medium text-danger">{fmtMoney(p.vencido)}</span>
                    : <span className="text-muted">—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <td className="py-2">Total</td>
            <td className="py-2 text-right tabular-nums">{fmtMoney(data.saldo_total)}</td>
            <td className="py-2 text-right tabular-nums text-danger">{fmtMoney(data.vencido_total)}</td>
          </tr>
        </tfoot>
      </table>
      {Number(data.saldo_en_cancelacion) > 0 && (
        <p className="mt-2 text-xs text-muted">
          Fuera del total: {fmtMoney(data.saldo_en_cancelacion)} en proceso de cancelación ante el SAT.
        </p>
      )}
    </Card>
  );
}

export default function DashboardPage() {
  const { me } = useAuth();
  if (!me) return null;

  const tenant = me.tenants.find(
    (t) => t.tenant_id === me.active_tenant.tenant_id
  );
  const puedeCobranza = me.active_tenant.is_owner || me.permissions.includes("menu:facturas");
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

      {puedeCobranza && (
        <div className="mt-8">
          <SaldosPorProyectoCard />
        </div>
      )}

      <div className="mt-8">
        <PrimerosPasos />
      </div>
    </div>
  );
}

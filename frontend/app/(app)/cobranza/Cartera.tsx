"use client";

// Cobranza → «Antigüedad de saldos»: la cartera, lo que nos deben HOY. Vivía en
// Reportes como «Cuentas por cobrar»; está aquí porque es la vista de arriba de
// la misma cobranza: «Por cobrar» es el renglón de cada factura, esto es el
// resumen por cliente, sucursal o proyecto con sus cajas de antigüedad.
//
// Va SIN periodo: la cartera es lo que se debe al corte de hoy, sin importar
// cuándo se facturó, y recortarla escondía saldos viejos justo donde más
// importan. Cada fila lleva al estado de cuenta del cliente con las cajas que
// estaban marcadas.
import { useState } from "react";

import { BarraSegmentada } from "@/components/ui/Barras";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { fmtDate, fmtMoney } from "@/lib/format";
import { useResource } from "@/lib/hooks";
import { hoyISO } from "@/app/(app)/reportes/rango";
import { SumarioAgrupado, type Agrupar, type FilaSumario } from "@/app/(app)/reportes/SumarioAgrupado";

type CubetaKey = "por_vencer" | "mes_1" | "mes_2" | "mes_3" | "mes_4_mas";
type FilaCartera = {
  etiqueta: string; saldo: string; vencido: string; facturas: number;
  cliente_id: string | null; serie: string | null;
  antiguedad: Record<CubetaKey, string>;
  facturas_por_cubeta: Record<CubetaKey, number>;
};
type CarteraDatos = {
  corte: string; agrupar: Agrupar; filas: FilaCartera[];
  saldo_total: string; vencido_total: string;
  antiguedad: Record<CubetaKey, string>;
  saldo_en_cancelacion: string;
};

// El mismo orden que en Reportes → Ventas: la pregunta primera es «¿quién?».
const AGRUPAR: Agrupar[] = ["cliente", "sucursal", "proyecto"];

// Mismos cortes, con otro nombre, que las cajas del estado de cuenta.
const CUBETA_EN_ESTADO_CUENTA: Record<CubetaKey, string> = {
  por_vencer: "por_vencer", mes_1: "d1_30", mes_2: "d31_60", mes_3: "d61_90", mes_4_mas: "d90_mas",
};

// Mismos cortes que el backend (`_cubeta` en reportes.py).
const CUBETAS: { key: CubetaKey; label: string; detalle: string; clase: string }[] = [
  { key: "por_vencer", label: "Por vencer", detalle: "", clase: "bg-success/70" },
  { key: "mes_1", label: "1 mes", detalle: "1-30 días", clase: "bg-favorite/80" },
  { key: "mes_2", label: "2 meses", detalle: "31-60 días", clase: "bg-favorite" },
  { key: "mes_3", label: "3 meses", detalle: "61-90 días", clase: "bg-danger/70" },
  { key: "mes_4_mas", label: "4+ meses", detalle: "91+ días", clase: "bg-danger" },
];

export function Cartera() {
  const [agrupar, setAgrupar] = useState<Agrupar>("cliente");
  // Cajas de antigüedad marcadas (vacío = todas).
  const [cubetasSel, setCubetasSel] = useState<CubetaKey[]>([]);
  const toggleCubeta = (k: string) => setCubetasSel((prev) =>
    prev.includes(k as CubetaKey) ? prev.filter((x) => x !== k) : [...prev, k as CubetaKey]);

  const carteraRes = useResource<CarteraDatos>(`/api/v1/reportes/cartera?agrupar=${agrupar}`);
  const cartera = carteraRes.data;

  const destino = (f: { cliente_id: string | null; serie: string | null }, cubetas: CubetaKey[] = []) => {
    if (!f.cliente_id) return null;
    const qs = new URLSearchParams();
    if (f.serie) qs.set("serie", f.serie);
    // Las cajas marcadas viajan al estado de cuenta con sus nombres de allá.
    if (cubetas.length) qs.set("antiguedad", cubetas.map((c) => CUBETA_EN_ESTADO_CUENTA[c]).join(","));
    const s = qs.toString();
    return `/clientes/${f.cliente_id}/estado-cuenta${s ? `?${s}` : ""}`;
  };
  // Con cajas marcadas, cada fila suma solo esas cubetas (el vencido, las
  // que no son «por vencer») y se van las que quedan en cero.
  const sumaSel = (a: Record<CubetaKey, string | number>, soloVencido = false) =>
    cubetasSel.reduce((t, k) => (soloVencido && k === "por_vencer" ? t : t + Number(a[k] ?? 0)), 0);
  const filasCartera: FilaSumario[] = cubetasSel.length === 0
    ? (cartera?.filas ?? []).map((f) => ({
        etiqueta: f.etiqueta, monto: f.saldo, facturas: f.facturas, href: destino(f), alerta: f.vencido,
      }))
    : (cartera?.filas ?? [])
        .map((f) => ({
          etiqueta: f.etiqueta, monto: sumaSel(f.antiguedad), facturas: sumaSel(f.facturas_por_cubeta),
          href: destino(f, cubetasSel), alerta: sumaSel(f.antiguedad, true),
        }))
        .filter((f) => f.monto > 0)
        .sort((a, b) => b.monto - a.monto);
  const totalCartera = cubetasSel.length === 0 ? cartera?.saldo_total : cartera && sumaSel(cartera.antiguedad);
  const vencidoCartera = cubetasSel.length === 0 ? cartera?.vencido_total : cartera && sumaSel(cartera.antiguedad, true);

  if (carteraRes.error) {
    return (
      <Card title="Antigüedad de saldos">
        <p className="py-8 text-center text-sm text-muted">No se pudo cargar la cartera.</p>
      </Card>
    );
  }

  return (
    <Card
      title="Antigüedad de saldos"
      subtitle={`Cuentas por cobrar al ${fmtDate(cartera?.corte ?? hoyISO())} de todas las facturas vigentes`}
    >
      {!cartera ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : cartera.filas.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">
          Ninguna factura tiene saldo pendiente.
        </p>
      ) : (
        <>
          <div className="mb-4">
            <BarraSegmentada
              titulo="Antigüedad de la cartera"
              tramos={CUBETAS.map((c) => ({
                key: c.key,
                etiqueta: c.label,
                detalle: c.detalle,
                valor: Number(cartera.antiguedad[c.key]),
                clase: c.clase,
              }))}
              seleccion={cubetasSel}
              onToggle={toggleCubeta}
            />
          </div>

          <div className="border-t border-border pt-4">
            <SumarioAgrupado
              opciones={AGRUPAR}
              agrupar={agrupar}
              onAgrupar={setAgrupar}
              cargando={carteraRes.loading}
              total={
                <>
                  {cubetasSel.length > 0 && (
                    <button type="button" className="mr-3 text-xs text-accent hover:underline"
                            onClick={() => setCubetasSel([])}>
                      Quitar filtro de antigüedad
                    </button>
                  )}
                  Total: <span className="font-semibold tabular-nums">{fmtMoney(totalCartera ?? 0)}</span>
                  <span className="ml-2 text-danger">
                    vencido <span className="font-semibold tabular-nums">{fmtMoney(vencidoCartera ?? 0)}</span>
                  </span>
                </>
              }
              filas={filasCartera}
              columnaMonto="Saldo"
              columnaAlerta="Vencido"
              vacio="Ninguna factura tiene saldo pendiente."
              pie={Number(cartera.saldo_en_cancelacion) > 0 && (
                <>
                  Fuera del total: {fmtMoney(cartera.saldo_en_cancelacion)} en facturas con la
                  cancelación ya pedida al SAT.
                </>
              )}
            />
          </div>
        </>
      )}
    </Card>
  );
}

"use client";

// El periodo de las pestañas de documentos de Cobranza (Recibos de pago, Notas
// de crédito): el mismo selector que Facturas y Remisiones, dentro de la barra
// de la tabla. Por omisión el AÑO en curso y no el mes: son pocos documentos a
// la semana, y el 2 del mes «Este mes» enseñaba una tabla casi vacía.
import { useState, type ReactNode } from "react";

import { PeriodoFiltro, rangoDePeriodo, type Periodo } from "@/components/PeriodoFiltro";

export function usePeriodo(inicial: Periodo = "anio") {
  const [periodo, setPeriodo] = useState<Periodo>(inicial);
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const r = rangoDePeriodo(periodo, desde, hasta);
  const qs = new URLSearchParams();
  // «Todas» = sin rango; el backend, sin fechas, tomaría los últimos 30 días.
  if (periodo === "todas") qs.set("todo", "true");
  else {
    if (r.desde) qs.set("desde", r.desde);
    if (r.hasta) qs.set("hasta", r.hasta);
  }
  const filtro = (conteo?: ReactNode) => (
    <PeriodoFiltro periodo={periodo} onPeriodo={setPeriodo}
      desde={desde} hasta={hasta} onDesde={setDesde} onHasta={setHasta} conteo={conteo} />
  );
  return { query: qs.toString(), filtro };
}

"use client";

import type { ReactNode } from "react";

import { BarraPestanas, useSeccion } from "@/components/SeccionPestanas";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  // Dentro de una sección con pestañas (components/SeccionPestanas.tsx) el
  // título es el de la sección y la barra va debajo, como en Cobranza. Por eso
  // cada pantalla debe pintar UN solo PageHeader: un segundo repetiría la barra.
  const seccion = useSeccion();
  return (
    <>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{seccion?.titulo ?? title}</h1>
          {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {seccion && <BarraPestanas seccion={seccion} />}
    </>
  );
}

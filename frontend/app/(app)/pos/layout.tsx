import type { ReactNode } from "react";

import { SeccionPestanas } from "@/components/SeccionPestanas";

// Estaciones · Configuración: una sola entrada del menú con pestañas
// (lib/nav.tsx). Antes había dos «Punto de venta» en secciones distintas (el
// de trabajar en Extras y el de configurar en Configuraciones); /ajustes/pos
// redirige aquí (next.config.ts). Las estaciones (/pos/pedido, /pos/caja…) no
// son pestañas: se trabajan a pantalla completa con su propio encabezado.
export default function PosLayout({ children }: { children: ReactNode }) {
  return <SeccionPestanas href="/pos">{children}</SeccionPestanas>;
}

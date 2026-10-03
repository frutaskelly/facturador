import type { ReactNode } from "react";

import { SeccionPestanas } from "@/components/SeccionPestanas";

// Productos · Categorías · Impuestos · Vocabulario · Revisión del catálogo:
// una sola entrada del menú con pestañas (lib/nav.tsx). Antes Categorías,
// Esquemas de impuesto y Vocabulario eran entradas sueltas; sus rutas viejas
// redirigen aquí (next.config.ts).
export default function ProductosLayout({ children }: { children: ReactNode }) {
  return <SeccionPestanas href="/productos">{children}</SeccionPestanas>;
}

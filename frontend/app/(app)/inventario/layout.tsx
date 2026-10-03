import type { ReactNode } from "react";

import { SeccionPestanas } from "@/components/SeccionPestanas";

// Existencias · Almacenes: una sola entrada del menú con pestañas (lib/nav.tsx).
// Almacenes estaba en Catálogo y las existencias en Extras; /almacenes redirige
// aquí (next.config.ts).
export default function InventarioLayout({ children }: { children: ReactNode }) {
  return <SeccionPestanas href="/inventario">{children}</SeccionPestanas>;
}

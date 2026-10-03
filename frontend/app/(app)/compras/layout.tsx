import type { ReactNode } from "react";

import { SeccionPestanas } from "@/components/SeccionPestanas";

// Órdenes de compra · Proveedores: una sola entrada del menú con pestañas
// (lib/nav.tsx). /proveedores redirige aquí (next.config.ts).
export default function ComprasLayout({ children }: { children: ReactNode }) {
  return <SeccionPestanas href="/compras">{children}</SeccionPestanas>;
}

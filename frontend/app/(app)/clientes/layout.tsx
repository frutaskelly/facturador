import type { ReactNode } from "react";

import { SeccionPestanas } from "@/components/SeccionPestanas";

// Clientes · Proyectos · Sucursales · Listas de precios: una sola entrada del
// menú con pestañas (lib/nav.tsx). Es todo lo de a quién se le vende y a qué
// precio: la lista que cobra a cada quien se escoge en el proyecto, en el
// cliente dentro de su plaza o en la ficha del cliente, y aquí está junto.
// Las fichas del cliente (/clientes/[id]/…) no son pestañas y se quedan con
// su propio encabezado. Las rutas viejas redirigen aquí (next.config.ts).
export default function ClientesLayout({ children }: { children: ReactNode }) {
  return <SeccionPestanas href="/clientes">{children}</SeccionPestanas>;
}

"use client";

// Pestañas de una entrada del menú (`NavItem.pestanas` en lib/nav.tsx): las
// pantallas que se usan juntas (Productos · Categorías · Impuestos…) viven bajo
// una misma ruta y se ven como una sola, igual que Cobranza.
//
// El layout de la sección (p. ej. app/(app)/productos/layout.tsx) envuelve sus
// páginas con <SeccionPestanas>. El <PageHeader> de cada página lee este
// contexto: si su ruta es una pestaña, el título pasa a ser el de la sección y
// la barra se dibuja debajo. Así cada pantalla conserva su subtítulo y sus
// botones sin saber que vive dentro de una sección. Una sub-ruta que no es
// pestaña (/productos/importar) se queda con su propio encabezado.
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { NAV, puedeVer, type NavPestana } from "@/lib/nav";

export type Seccion = { titulo: string; pestanas: NavPestana[]; activa: NavPestana };

const SeccionCtx = createContext<Seccion | null>(null);

/** La sección con pestañas en la que está la pantalla, o null si no hay barra. */
export function useSeccion(): Seccion | null {
  return useContext(SeccionCtx);
}

export function SeccionPestanas({ href, children }: { href: string; children: ReactNode }) {
  const pathname = usePathname();
  const { me } = useAuth();
  const seccion = useMemo<Seccion | null>(() => {
    const item = NAV.flatMap((s) => s.items).find((i) => i.href === href);
    // Las que este usuario no ve ni siquiera se dibujan.
    const pestanas = (item?.pestanas ?? []).filter((p) => puedeVer(me, p));
    // Con una sola no hay entre qué escoger: la pantalla se queda con su título.
    if (!item || pestanas.length < 2) return null;
    // La raíz (/productos) sólo se marca en sí misma; las demás también en sus
    // sub-rutas (/productos/categorias/…).
    const activa = pestanas.find(
      (p) => pathname === p.href || (p.href !== href && pathname.startsWith(`${p.href}/`)),
    );
    return activa ? { titulo: item.label, pestanas, activa } : null;
  }, [href, me, pathname]);
  return <SeccionCtx.Provider value={seccion}>{children}</SeccionCtx.Provider>;
}

/** La barra, con el mismo trazo que la de Cobranza. Son ligas y no botones:
 *  cada pestaña es su propia ruta, así que F5, «abrir en otra pestaña» y los
 *  marcadores funcionan sin código. */
export function BarraPestanas({ seccion }: { seccion: Seccion }) {
  // En el celular la barra se desliza: la pestaña abierta se centra para que
  // no quede escondida a la derecha.
  const barra = useRef<HTMLElement>(null);
  useEffect(() => {
    const b = barra.current;
    const t = b?.querySelector<HTMLElement>('[aria-current="page"]');
    if (b && t) b.scrollLeft = t.offsetLeft - (b.clientWidth - t.offsetWidth) / 2;
  }, [seccion.activa.href]);

  return (
    // La raya de abajo es una sombra y no un borde, para que el scroll no la recorte.
    <nav ref={barra} aria-label={seccion.titulo}
         className="relative mb-4 flex gap-1 overflow-x-auto [scrollbar-width:none] shadow-[inset_0_-1px_0_var(--border)]">
      {seccion.pestanas.map((p) => {
        const activa = p.href === seccion.activa.href;
        return (
          <Link
            key={p.href}
            href={p.href}
            aria-current={activa ? "page" : undefined}
            className={`shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition ${
              activa
                ? "border-accent font-medium text-foreground"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            {p.label}
          </Link>
        );
      })}
    </nav>
  );
}

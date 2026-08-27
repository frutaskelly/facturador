"use client";

// Identidad visual de la empresa activa: franja de color, ícono de la pestaña y
// título del navegador.
//
// Con dos empresas abiertas a la vez (una por pestaña, ver lib/tenant.ts), esto
// es lo único que distingue una pestaña de otra cuando están de fondo: ahí no se
// ve la app, solo el ícono y el título.
//
// La franja y el ícono aparecen SOLO con varias empresas: a quien tiene una sola
// no le cambiamos la interfaz para resolver un problema que no tiene. El título
// sí va siempre — ayuda igual.
import { useEffect } from "react";
import { usePathname } from "next/navigation";

import type { Me } from "@/lib/auth";
import { colorEmpresa, inicialesEmpresa } from "@/lib/empresa-color";
import { NAV } from "@/lib/nav";

const TITULO_BASE = "Facturador Inteligente";

/** El nombre de la sección actual según el menú: gana el href más específico
 *  (`/ajustes/empresa/configuracion` no debe quedarse en "Dashboard"). */
function seccionDe(pathname: string): string | null {
  let mejor: { label: string; largo: number } | null = null;
  for (const seccion of NAV) {
    for (const item of seccion.items) {
      const calza = pathname === item.href || pathname.startsWith(`${item.href}/`);
      if (calza && (!mejor || item.href.length > mejor.largo)) {
        mejor = { label: item.label, largo: item.href.length };
      }
    }
  }
  return mejor?.label ?? null;
}

/** Cuadro del color de la empresa con sus iniciales, como PNG embebido. */
function faviconDataUrl(color: string, texto: string): string | null {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  ctx.fillStyle = color;
  // roundRect no existe en navegadores viejos: el cuadro recto es un fallback
  // perfectamente digno a 16 px.
  if (typeof ctx.roundRect === "function") {
    ctx.beginPath();
    ctx.roundRect(0, 0, 64, 64, 14);
    ctx.fill();
  } else {
    ctx.fillRect(0, 0, 64, 64);
  }

  ctx.fillStyle = "#ffffff";
  ctx.font = '700 34px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(texto, 32, 35, 52);
  return canvas.toDataURL("image/png");
}

export function EmpresaIdentidad({ me }: { me: Me }) {
  const pathname = usePathname();
  const activa = me.tenants.find((t) => t.tenant_id === me.active_tenant.tenant_id);
  const nombre = activa?.name ?? "";
  const color = activa ? colorEmpresa(activa.tenant_id, activa.color) : null;
  const varias = me.tenants.length > 1;

  // La empresa va PRIMERO: es la mitad que sobrevive cuando la pestaña se
  // encoge, y es justo la que necesitas leer para no equivocarte.
  useEffect(() => {
    const seccion = seccionDe(pathname);
    document.title = [nombre || TITULO_BASE, seccion].filter(Boolean).join(" · ");
  }, [pathname, nombre]);

  useEffect(() => {
    if (!varias || !color || !nombre) return;
    const href = faviconDataUrl(color, inicialesEmpresa(nombre));
    if (!href) return;
    // Se agrega al final del <head>: el navegador se queda con el último
    // `rel="icon"` declarado, así que este gana sobre el ícono de la marca sin
    // tener que quitarlo (y al desmontarse, la marca vuelve sola).
    const link = document.createElement("link");
    link.rel = "icon";
    link.type = "image/png";
    link.href = href;
    document.head.appendChild(link);
    return () => link.remove();
  }, [varias, color, nombre]);

  if (!varias || !color) return null;
  return <div aria-hidden="true" className="h-[3px] shrink-0" style={{ background: color }} />;
}

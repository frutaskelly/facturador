import {
  BarChart3,
  Boxes,
  Building2,
  Calculator,
  FileText,
  Hash,
  Home,
  Library,
  Plug,
  LayoutDashboard,
  Mail,
  Package,
  Palette,
  HandCoins,
  Inbox,
  Receipt,
  Repeat,
  Shield,
  ShoppingBag,
  SlidersHorizontal,
  Store,
  ShoppingCart,
  Truck,
  UserCog,
  Users,
  Settings,
  Warehouse,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { can, canAny, type Me } from "@/lib/auth";

/** Una pestaña de una entrada del menú (ver `NavItem.pestanas`). `antes`: la
 *  ruta que tenía cuando era una entrada suelta del menú; next.config.ts la
 *  redirige aquí y el Sidebar muda a esta entrada los favoritos que la tenían. */
export type NavPestana = { label: string; href: string; perm?: string; anyPerm?: string[]; antes?: string[] };

/** `pestanas`: las pantallas que se usan juntas viven en UNA entrada del menú,
 *  bajo su misma ruta (/productos/categorias), y el encabezado de cada una
 *  dibuja la barra (components/SeccionPestanas.tsx). Cada pestaña conserva su
 *  permiso: la entrada se ve si el usuario puede ver al menos una y lleva a la
 *  primera que puede ver. Así nadie ve más de lo que veía con entradas sueltas. */
export type NavItem = {
  label: string; href: string; perm?: string; anyPerm?: string[]; icon: LucideIcon;
  pestanas?: NavPestana[];
};
/** `corto` e `icon` son lo que el menú COLAPSADO dibuja en el riel (ver
 *  components/Sidebar.tsx). Viven aquí y no en el Sidebar a propósito: si
 *  fueran un mapa aparte, una sección nueva se quedaría sin botón y
 *  desaparecería en silencio para quien tenga el menú contraído.
 *  `corto` cabe en ~9 caracteres a 10 px. */
export type NavSection = { section: string; corto: string; icon: LucideIcon; items: NavItem[] };

/** Sidebar model. Each item is shown only if the user holds `perm` (or is OWNER).
 * `perm` values are the `menu:*` permissions seeded in the backend catalog. */
export const NAV: NavSection[] = [
  {
    // El día a día: lo que se abre todas las mañanas. Cobranza va aquí y no en
    // Configuraciones porque es el complemento de pago de una factura, o sea
    // el último paso del mismo flujo.
    section: "General",
    corto: "Inicio",
    icon: Home,
    items: [
      { label: "Dashboard", href: "/dashboard", perm: "menu:dashboard", icon: LayoutDashboard },
      { label: "Remisiones", href: "/remisiones", perm: "menu:remisiones", icon: FileText },
      // Lo que el bot de WhatsApp no se atrevió a resolver solo («necesito una
      // mano»), con su número. Va junto a Remisiones porque hoy son pedidos atorados.
      { label: "Buzón de tickets", href: "/tickets", perm: "menu:tickets", icon: Inbox },
      { label: "Facturas", href: "/facturas", perm: "menu:facturas", icon: Receipt },
      // Una sola entrada: por cobrar, REP y cobranza automática son pestañas.
      { label: "Cobranza", href: "/cobranza", perm: "menu:facturas", icon: HandCoins },
      // Los cortes del negocio (saldos por proyecto y los que vengan). Mismo
      // permiso que cobranza: ver reportes de saldos ES ver cobranza.
      { label: "Reportes", href: "/reportes", perm: "menu:facturas", icon: BarChart3 },
    ],
  },
  {
    // Las listas que se dan de alta y se mantienen. Almacenes está aquí (es un
    // catálogo) y no en Extras (que es donde se CONSULTA el inventario).
    section: "Catálogo",
    corto: "Catálogo",
    icon: Library,
    items: [
      // Todo lo que describe al producto en una sola entrada (oct-2026): con qué
      // categoría y esquema de impuesto se da de alta, cómo lo escriben los
      // clientes (global o por cliente) y qué productos son el mismo.
      { label: "Productos", href: "/productos", icon: Package, pestanas: [
        { label: "Productos", href: "/productos", perm: "menu:productos" },
        { label: "Categorías", href: "/productos/categorias", perm: "menu:productos.categorias", antes: ["/categorias"] },
        { label: "Impuestos", href: "/productos/impuestos", perm: "menu:esquemas_impuesto", antes: ["/esquemas-impuesto"] },
        { label: "Vocabulario", href: "/productos/vocabulario", perm: "menu:productos", antes: ["/vocabulario"] },
        // Grupos de productos que son el mismo (gemelos, otra unidad, empaques
        // por kilo), calculados en vivo; reemplaza la hoja de Excel del 30-sep.
        { label: "Revisión del catálogo", href: "/productos/revision", perm: "menu:productos" },
      ] },
      // A quién se le vende y a qué precio (oct-2026): la lista que cobra a cada
      // quien se escoge en el proyecto, en el cliente dentro de su plaza o en su
      // ficha, así que las listas viven junto a esas tres.
      { label: "Clientes", href: "/clientes", icon: Users, pestanas: [
        { label: "Clientes", href: "/clientes", perm: "menu:clientes" },
        { label: "Proyectos", href: "/clientes/proyectos", perm: "menu:clientes", antes: ["/proyectos"] },
        { label: "Sucursales", href: "/clientes/sucursales", perm: "menu:clientes", antes: ["/sucursales"] },
        { label: "Listas de precios", href: "/clientes/listas-precios", perm: "menu:listas_precios", antes: ["/listas-precios"] },
      ] },
      { label: "Almacenes", href: "/almacenes", perm: "menu:inventario", icon: Warehouse },
    ],
  },
  {
    section: "Compras",
    corto: "Compras",
    icon: ShoppingBag,
    items: [
      { label: "Compras", href: "/compras", perm: "menu:compras", icon: ShoppingCart },
      { label: "Proveedores", href: "/proveedores", perm: "menu:compras", icon: Truck },
    ],
  },
  {
    // Herramientas que no son el flujo diario de remisionar y facturar.
    section: "Extras",
    corto: "Extras",
    icon: Wrench,
    items: [
      { label: "Punto de venta", href: "/pos",
        anyPerm: ["menu:pos.pedido", "menu:pos.caja", "menu:pos.almacen", "menu:pos.salida"], icon: Store },
      { label: "Inventario", href: "/inventario", perm: "menu:inventario", icon: Boxes },
      { label: "Cotizador", href: "/cotizador", perm: "menu:cotizador", icon: Calculator },
      { label: "Conversiones", href: "/conversiones", perm: "menu:conversiones", icon: Repeat },
    ],
  },
  {
    // Reglas del negocio: CÓMO se cobra y CÓMO se numera. Se tocan de vez en
    // cuando y las toca quien conoce la operación.
    section: "Configuraciones",
    corto: "Config.",
    icon: SlidersHorizontal,
    items: [
      { label: "Series y folios", href: "/ajustes/series", perm: "menu:series", icon: Hash },
      { label: "Punto de venta", href: "/ajustes/pos", perm: "membership:gestionar", icon: Store },
    ],
  },
  {
    // Administración del sistema: quién entra y con qué se conecta.
    section: "Ajustes",
    corto: "Ajustes",
    icon: Settings,
    items: [
      { label: "Empresas", href: "/ajustes/empresa", perm: "membership:gestionar", icon: Building2 },
      { label: "Usuarios", href: "/ajustes/usuarios", perm: "menu:ajustes.usuarios", icon: UserCog },
      { label: "Roles", href: "/ajustes/roles", perm: "menu:ajustes.roles", icon: Shield },
      { label: "Correo", href: "/ajustes/correo", perm: "membership:gestionar", icon: Mail },
      { label: "Conexiones", href: "/ajustes/conexiones", perm: "membership:gestionar", icon: Plug },
      { label: "Sistema de diseño", href: "/ajustes/sistema-diseno", perm: "menu:configuraciones", icon: Palette },
    ],
  },
];

/** ¿Este usuario ve esta entrada o pestaña? Un solo filtro para el Sidebar y
 *  para la barra de pestañas: si cada uno filtrara a su manera, una pestaña
 *  podría aparecer en la barra de alguien que no la tiene en el menú. */
export function puedeVer(me: Me | null, x: { perm?: string; anyPerm?: string[] }): boolean {
  return can(me, x.perm) && canAny(me, x.anyPerm);
}

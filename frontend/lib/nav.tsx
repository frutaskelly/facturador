import {
  Boxes,
  Briefcase,
  Building2,
  Calculator,
  FileText,
  FolderTree,
  Hash,
  Home,
  Inbox,
  Languages,
  Library,
  Plug,
  LayoutDashboard,
  Mail,
  Package,
  Palette,
  Percent,
  HandCoins,
  Receipt,
  Repeat,
  Shield,
  ShoppingBag,
  SlidersHorizontal,
  Store,
  ShoppingCart,
  Tag,
  Target,
  Truck,
  UserCog,
  Users,
  Settings,
  Warehouse,
  Wrench,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { label: string; href: string; perm?: string; anyPerm?: string[]; icon: LucideIcon };
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
      { label: "Bandeja de órdenes", href: "/oc", perm: "menu:oc", icon: Inbox },
      { label: "Remisiones", href: "/remisiones", perm: "menu:remisiones", icon: FileText },
      { label: "Facturas", href: "/facturas", perm: "menu:facturas", icon: Receipt },
      { label: "Cobranza (REP)", href: "/cobranza", perm: "menu:facturas", icon: HandCoins },
    ],
  },
  {
    // Las listas que se dan de alta y se mantienen. Almacenes está aquí (es un
    // catálogo) y no en Extras (que es donde se CONSULTA el inventario).
    section: "Catálogo",
    corto: "Catálogo",
    icon: Library,
    items: [
      { label: "Productos", href: "/productos", perm: "menu:productos", icon: Package },
      { label: "Categorías", href: "/categorias", perm: "menu:productos.categorias", icon: FolderTree },
      { label: "Clientes", href: "/clientes", perm: "menu:clientes", icon: Users },
      { label: "Sucursales y precios", href: "/sucursales", perm: "menu:clientes", icon: Building2 },
      { label: "Proyectos", href: "/proyectos", perm: "menu:clientes", icon: Briefcase },
      { label: "Almacenes", href: "/almacenes", perm: "menu:inventario", icon: Warehouse },
      { label: "Vocabulario", href: "/vocabulario", perm: "menu:productos", icon: Languages },
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
      { label: "Listas de precios", href: "/listas-precios", perm: "menu:listas_precios", icon: Tag },
      { label: "Asignación de precios", href: "/asignaciones-precios", perm: "menu:listas_precios", icon: Target },
      { label: "Esquemas de impuesto", href: "/esquemas-impuesto", perm: "menu:esquemas_impuesto", icon: Percent },
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

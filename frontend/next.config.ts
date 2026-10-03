import type { NextConfig } from "next";

// IP LAN del Mini para acceso desde otros dispositivos en dev; configurable
// sin tocar código cuando cambie el DHCP.
const LAN_ORIGIN = process.env.ALLOWED_DEV_ORIGIN || "192.168.1.65";

const nextConfig: NextConfig = {
  // Standalone output → small runtime image; `next build && next start`
  // is the supported production path (v1 regression we are NOT repeating).
  output: "standalone",

  // v2: the build is a REAL gate. TypeScript errors fail the build (the
  // opposite of v1's `ignoreBuildErrors: true`). Next 16 removed `next lint`
  // and the `eslint` config key, so linting is configured separately later.
  allowedDevOrigins: ["localhost:3012", "127.0.0.1", LAN_ORIGIN],

  async redirects() {
    return [
      // La bandeja de órdenes se fusionó en Remisiones (ticket «Une los
      // menús», sep-2026); los marcadores viejos siguen llegando a un lugar útil.
      { source: "/oc", destination: "/remisiones", permanent: false },
      { source: "/oc/:id", destination: "/remisiones", permanent: false },
      // Cobranza automática se volvió pestañas de /cobranza (oct-2026).
      { source: "/cobranza/automatica", destination: "/cobranza?tab=envios", permanent: false },
      // Categorías, Esquemas de impuesto y Vocabulario se volvieron pestañas de
      // Productos (oct-2026). Las mismas rutas viven en `antes` de lib/nav.tsx,
      // que muda los favoritos; la query (?cliente=…) pasa sola.
      { source: "/categorias", destination: "/productos/categorias", permanent: false },
      { source: "/esquemas-impuesto", destination: "/productos/impuestos", permanent: false },
      { source: "/vocabulario", destination: "/productos/vocabulario", permanent: false },
      // Proyectos, Sucursales y Listas de precios, pestañas de Clientes (oct-2026).
      // «Asignación de precios» se retiró el 1-oct: la lista se escoge donde vive
      // la negociación, y el simulador «¿qué lista le tocaría?» está en Listas.
      { source: "/proyectos", destination: "/clientes/proyectos", permanent: false },
      { source: "/sucursales", destination: "/clientes/sucursales", permanent: false },
      { source: "/listas-precios", destination: "/clientes/listas-precios", permanent: false },
      { source: "/asignaciones-precios", destination: "/clientes/listas-precios", permanent: false },
      // Almacenes, Proveedores y la configuración del POS, pestañas de
      // Inventario, Compras y Punto de venta (oct-2026).
      { source: "/almacenes", destination: "/inventario/almacenes", permanent: false },
      { source: "/proveedores", destination: "/compras/proveedores", permanent: false },
      { source: "/ajustes/pos", destination: "/pos/configuracion", permanent: false },
    ];
  },

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;

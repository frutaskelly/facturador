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

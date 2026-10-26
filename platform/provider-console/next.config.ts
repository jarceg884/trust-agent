import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // App 100% dinámica (toda página lee cookies de sesión / datos del tenant) —
  // sin cache de componentes: los datos de gobernanza deben ser frescos
  // (constitution I). Paridad con platform/desktop-client.
  cacheComponents: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

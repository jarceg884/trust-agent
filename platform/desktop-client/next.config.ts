import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // App 100% dinámica (toda página lee cookies de sesión) — sin cache de
  // componentes: los datos del dashboard deben ser siempre frescos (constitution I).
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

/**
 * Branding publicado — acceso server-side cacheado por request
 * (spec 028 D-028-08, t_42e23b01).
 *
 * `generateMetadata` del layout y el cuerpo del layout necesitan el mismo
 * GET /api/branding (título de página + tema de acento). `cache()` de React
 * lo memoiza por request: una sola llamada al control plane por navegación,
 * compartida por ambos consumidores.
 */
import { cache } from "react";
import { api } from "./control-plane";
import type { TenantBranding } from "./types";

/** Fallback del proveedor cuando no hay branding publicado (REQ-BRD-005). */
export const PROVIDER_FALLBACK_NAME = "Trust Agent";

/**
 * GET /api/branding (publicado) con el token de la cookie httpOnly.
 * Devuelve null si la API no responde: el branding es cosmético y el
 * shell ya muestra el estado real de datos (mismo criterio que el acento).
 */
export const getPublishedBranding = cache(
  async (session: string): Promise<TenantBranding | null> => {
    try {
      return await api<TenantBranding>("/api/branding", { bearer: session });
    } catch {
      return null;
    }
  },
);

/** Título por defecto del shell: "{product_name} — Admin Console". */
export function consoleTitle(name: string): string {
  return `${name} — Admin Console`;
}

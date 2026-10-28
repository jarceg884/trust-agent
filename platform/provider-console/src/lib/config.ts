/**
 * Configuración del Provider Console (spec 014, D-PC-01/02/07).
 *
 * Todo apunta al stack local (constitution VII): control plane en :8000,
 * Admin Console por tenant en :3100. Ninguna URL viaja al navegador: las
 * llamadas al control plane se hacen server-side (route handlers / RSC /
 * proxy BFF) con el provider token en cookie httpOnly.
 *
 * CORRECCIÓN MILLER (t_360a752e, 2026-10-26): eliminado el "Hermes
 * download link" por tenant (antes D-PC-09, target :9090 con ?org=<slug>).
 * El proveedor solo crea/gestiona tenants; el link de descarga lo genera
 * y envía el admin del cliente desde SU Admin Console (platform/console).
 */
export const config = {
  /** Control plane (FastAPI, spec 003). */
  apiBaseUrl: process.env.TRUST_API_BASE_URL ?? "http://127.0.0.1:8000",
  /** Admin Console por tenant (deep-link D-PC-07). */
  adminConsoleUrl:
    process.env.TRUST_ADMIN_CONSOLE_URL ?? "http://127.0.0.1:3100",
} as const;

/** Slug válido de tenant (tenants.slug = realm Keycloak). */
export function isValidSlug(slug: string): boolean {
  return /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(slug);
}

/** Deep-link al login PKCE del realm del tenant en la Admin Console. */
export function adminConsoleLoginUrl(slug: string): string {
  return `${config.adminConsoleUrl}/auth/login?org=${encodeURIComponent(slug)}`;
}

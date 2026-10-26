/**
 * Configuración del Provider Console (spec 014, D-PC-01/02/07/09).
 *
 * Todo apunta al stack local (constitution VII): control plane en :8000,
 * Admin Console por tenant en :3100, desktop white-label en :9090. Ninguna
 * URL viaja al navegador: las llamadas al control plane se hacen server-side
 * (route handlers / RSC / proxy BFF) con el provider token en cookie httpOnly.
 */
export const config = {
  /** Control plane (FastAPI, spec 003). */
  apiBaseUrl: process.env.TRUST_API_BASE_URL ?? "http://127.0.0.1:8000",
  /** Admin Console por tenant (deep-link D-PC-07). */
  adminConsoleUrl:
    process.env.TRUST_ADMIN_CONSOLE_URL ?? "http://127.0.0.1:3100",
  /**
   * Desktop app white-label (D-PC-09): target del "Hermes download link"
   * por tenant (`?org=<slug>`). Cuando el puente Hermes real (spec 022)
   * aterrice el app completa, esta URL se apunta al nuevo target y el
   * contrato de binding no cambia.
   */
  desktopAppUrl: process.env.TRUST_DESKTOP_APP_URL ?? "http://127.0.0.1:9090",
} as const;

/** Slug válido de tenant (tenants.slug = realm Keycloak). */
export function isValidSlug(slug: string): boolean {
  return /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(slug);
}

/** Deep-link al login PKCE del realm del tenant en la Admin Console. */
export function adminConsoleLoginUrl(slug: string): string {
  return `${config.adminConsoleUrl}/auth/login?org=${encodeURIComponent(slug)}`;
}

/** Hermes download link del tenant (org_code embebido, D-PC-09). */
export function hermesDownloadUrl(slug: string): string {
  return `${config.desktopAppUrl}/?org=${encodeURIComponent(slug)}`;
}

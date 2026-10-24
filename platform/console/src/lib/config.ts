/**
 * Configuración del Admin Console (spec 009, D-AC-09).
 *
 * Todo apunta al stack local (constitution VII): control plane en :8000,
 * Keycloak en :8080. Ninguna de estas URLs viaja al navegador: todas las
 * llamadas a la API y al IdP se hacen server-side (route handlers / RSC /
 * proxy BFF — D-AC-01).
 */
export const config = {
  /** Control plane (FastAPI, spec 003). */
  apiBaseUrl: process.env.TRUST_API_BASE_URL ?? "http://127.0.0.1:8000",
  /** IdP OIDC (Keycloak). El realm por org es `{base}/realms/{org_code}`. */
  idpBaseUrl: process.env.TRUST_IDP_BASE_URL ?? "http://127.0.0.1:8080",
  /** Cliente público registrado por el aprovisionamiento en cada realm. */
  clientId: process.env.TRUST_OIDC_CLIENT_ID ?? "trust-agent-web",
  /**
   * Redirect URI del console. Canónica: :3000 (default de
   * `oidc_redirect_uris` en platform/app/config.py). Overrideable porque el
   * host local puede tener el puerto ocupado (D-AC-09): el realm del tenant
   * debe tener registrada la URI que se use.
   */
  redirectUri:
    process.env.TRUST_CONSOLE_REDIRECT_URI ??
    "http://127.0.0.1:3000/auth/callback",
  /** Alcances OIDC del login. */
  scope: "openid",
  /**
   * ACR solicitado al realm (D-MFA-01): Keycloak 26 no emite `amr`; la
   * evidencia MFA del control plane es `acr` >= TRUST_MFA_MIN_ACR (2).
   * Pedir `acr_values=2` activa la condición LoA del realm (OTP → acr "2")
   * también cuando existe una sesión SSO previa sin segundo factor.
   */
  mfaAcrValues: process.env.TRUST_MFA_ACR_VALUES ?? "2",
} as const;

/** Slug válido de organización (realm Keycloak / tenants.slug). */
export function isValidOrgCode(orgCode: string): boolean {
  return /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(orgCode);
}

/** Issuer del realm de una organización. */
export function issuerFor(orgCode: string): string {
  return `${config.idpBaseUrl}/realms/${orgCode}`;
}

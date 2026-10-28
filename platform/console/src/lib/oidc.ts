/**
 * OIDC authorization code + PKCE S256 (spec 009 D-AC-02, constitution IV).
 *
 * Paridad con el resto de las apps web (spec 009 D-AC-02): el baile completo
 * ocurre server-side; el navegador solo recibe el redirect al authorize y
 * vuelve con el `code`. El `code_verifier` jamás cruza al cliente (cookie
 * httpOnly).
 */
import { createHash, randomBytes } from "node:crypto";
import { config, issuerFor } from "./config";

function b64url(input: Buffer): string {
  return input
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export interface PkcePair {
  verifier: string;
  challenge: string;
}

/** PKCE S256: verifier aleatorio de 512 bits + challenge = BASE64URL(SHA256(verifier)). */
export function generatePkce(): PkcePair {
  const verifier = b64url(randomBytes(64));
  const challenge = b64url(createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

export function randomState(): string {
  return b64url(randomBytes(24));
}

/**
 * URL de autorización del realm de la org (cliente público, sin secret).
 *
 * F1 (t_38ce1e82): `redirectUri` se deriva del host por el que entra el
 * navegador (127.0.0.1 ↔ localhost) para que el baile PKCE —cookies incluidas—
 * permanezca en ese host; Keycloak redirige de vuelta exactamente a esta URI.
 *
 * Spec 015 (D-AUTH-06/08): `acrValues` es parametrizable — el login lo elige
 * por org-config (2 si la org exige MFA, 1 si es opcional) y "Set up MFA"
 * fuerza 2 para disparar el enrolamiento TOTP inline sobre la sesión SSO.
 */
export function buildAuthorizeUrl(args: {
  orgCode: string;
  challenge: string;
  state: string;
  redirectUri: string;
  acrValues?: string;
}): string {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    scope: config.scope,
    redirect_uri: args.redirectUri,
    state: args.state,
    code_challenge: args.challenge,
    code_challenge_method: "S256",
    // D-MFA-01: sin acr_values un login SSO previo sin OTP devolvería acr "1"
    // y la sesión mintada quedaría sin MFA. Con el flujo LoA nuevo (D-AUTH-06):
    // acr 1 = password-only; acr 2 = OTP (enrolamiento inline si no hay TOTP).
    acr_values: args.acrValues ?? config.mfaAcrValues,
  });
  return `${issuerFor(args.orgCode)}/protocol/openid-connect/auth?${params}`;
}

export interface TokenSet {
  id_token: string;
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
}

/** Canje code → tokens contra el token endpoint del realm (server-side). */
export async function exchangeCodeForTokens(args: {
  orgCode: string;
  code: string;
  verifier: string;
  redirectUri: string;
}): Promise<TokenSet> {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.clientId,
    redirect_uri: args.redirectUri,
    code: args.code,
    code_verifier: args.verifier,
  });
  const res = await fetch(`${issuerFor(args.orgCode)}/protocol/openid-connect/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "trust-agent-console/0.1.0",
    },
    body,
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`OIDC token exchange failed (HTTP ${res.status}).`);
  }
  return (await res.json()) as TokenSet;
}

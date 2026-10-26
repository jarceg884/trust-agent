/**
 * Custodio local de tokens del desktop (D-DTB-03).
 *
 * El servidor Next.js local (:9090) guarda en cookies httpOnly:
 *   ta_session  — session_token (Bearer hacia el control plane)
 *   ta_refresh  — refresh_token (rotación)
 *   ta_csrf     — csrf_token de la sesión
 *   ta_idtoken  — id_token OIDC (lo exige POST /api/devices/enroll: JWT con MFA)
 *   ta_pkce_*   — verifier/state del baile PKCE en curso (solo durante el login)
 *
 * El navegador JAMÁS lee estos valores (httpOnly). Es el equivalente web del
 * keychain del desktop empaquetado.
 */
import { cookies } from "next/headers";

export const COOKIE = {
  session: "ta_session",
  refresh: "ta_refresh",
  csrf: "ta_csrf",
  idToken: "ta_idtoken",
  pkceVerifier: "ta_pkce_verifier",
  oauthState: "ta_oauth_state",
  loginOrg: "ta_login_org",
  /** Marca de que el login en curso ya es un re-intento de step-up MFA. */
  stepup: "ta_stepup",
} as const;

const baseCookie = {
  httpOnly: true,
  sameSite: "lax" as const,
  // Local http: sin secure. En despliegue HTTPS, activarlo por env.
  secure: false,
  path: "/",
};

export async function setSessionCookies(args: {
  sessionToken: string;
  refreshToken: string;
  csrfToken: string;
  idToken: string;
  maxAgeSeconds: number;
}) {
  const store = await cookies();
  const { maxAgeSeconds, ...values } = args;
  store.set(COOKIE.session, values.sessionToken, { ...baseCookie, maxAge: maxAgeSeconds });
  store.set(COOKIE.refresh, values.refreshToken, { ...baseCookie, maxAge: maxAgeSeconds });
  store.set(COOKIE.csrf, values.csrfToken, { ...baseCookie, maxAge: maxAgeSeconds });
  store.set(COOKIE.idToken, values.idToken, { ...baseCookie, maxAge: maxAgeSeconds });
}

export async function setPkceCookies(args: {
  verifier: string;
  state: string;
  orgCode: string;
}) {
  const store = await cookies();
  // 10 minutos para completar el baile de login.
  store.set(COOKIE.pkceVerifier, args.verifier, { ...baseCookie, maxAge: 600 });
  store.set(COOKIE.oauthState, args.state, { ...baseCookie, maxAge: 600 });
  store.set(COOKIE.loginOrg, args.orgCode, { ...baseCookie, maxAge: 600 });
}

export async function clearPkceCookies() {
  const store = await cookies();
  for (const name of [COOKIE.pkceVerifier, COOKIE.oauthState, COOKIE.loginOrg]) {
    store.set(name, "", { ...baseCookie, maxAge: 0 });
  }
}

/** Marca el baile en curso como re-intento de step-up MFA (D-MFA-01). */
export async function setStepupCookie() {
  const store = await cookies();
  store.set(COOKIE.stepup, "1", { ...baseCookie, maxAge: 600 });
}

export async function clearStepupCookie() {
  const store = await cookies();
  store.set(COOKIE.stepup, "", { ...baseCookie, maxAge: 0 });
}

export async function hasStepupCookie(): Promise<boolean> {
  const store = await cookies();
  return store.get(COOKIE.stepup)?.value === "1";
}

export async function clearSessionCookies() {
  const store = await cookies();
  for (const name of [COOKIE.session, COOKIE.refresh, COOKIE.csrf, COOKIE.idToken]) {
    store.set(name, "", { ...baseCookie, maxAge: 0 });
  }
}

export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(COOKIE.session)?.value || null;
}

export async function getIdToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(COOKIE.idToken)?.value || null;
}

export async function getPkceCookies(): Promise<{
  verifier: string | null;
  state: string | null;
  orgCode: string | null;
}> {
  const store = await cookies();
  return {
    verifier: store.get(COOKIE.pkceVerifier)?.value || null,
    state: store.get(COOKIE.oauthState)?.value || null,
    orgCode: store.get(COOKIE.loginOrg)?.value || null,
  };
}

/**
 * Custodia de tokens del Admin Console (spec 009 D-AC-01/10).
 *
 * El servidor Next.js local guarda en cookies del origen del console
 * (prefijadas `ta_console_*` para no colisionar con otras apps Next.js en
 * el mismo host — las cookies NO distinguen puertos, F2 de qa-console-repro):
 *   ta_console_session  — session_token (httpOnly; el proxy lo reenvía como
 *                 cookie `trust_session` del control plane)
 *   ta_console_refresh  — refresh_token (httpOnly; rotación on-401)
 *   ta_console_csrf     — csrf_token de la sesión admin (legible por JS: el
 *                 navegador debe ponerlo en X-CSRF-Token y el control plane
 *                 valida el hash — REQ-SEC-010; no es un secreto de portador)
 *   ta_console_pkce_*   — verifier/state/org del baile PKCE en curso (10 min)
 *
 * El navegador JAMÁS lee session/refresh (httpOnly). Cero estado de sesión en
 * memoria de proceso (constitution IV): todo vive en cookies + PostgreSQL.
 */
import { cookies } from "next/headers";

export const COOKIE = {
  // Prefijo por app: las apps Next.js comparten host 127.0.0.1 y las
  // cookies no distinguen puertos (F2). Nombres exclusivos = aislamiento total.
  session: "ta_console_session",
  refresh: "ta_console_refresh",
  csrf: "ta_console_csrf",
  pkceVerifier: "ta_console_pkce_verifier",
  oauthState: "ta_console_oauth_state",
  loginOrg: "ta_console_login_org",
  /** Marca de que el login en curso ya es un re-intento de step-up MFA. */
  stepup: "ta_console_stepup",
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
  maxAgeSeconds: number;
}) {
  const store = await cookies();
  store.set(COOKIE.session, args.sessionToken, {
    ...baseCookie,
    maxAge: args.maxAgeSeconds,
  });
  store.set(COOKIE.refresh, args.refreshToken, {
    ...baseCookie,
    maxAge: args.maxAgeSeconds,
  });
  // CSRF legible por JS (sin httpOnly): el header X-CSRF-Token lo transporta
  // en cada mutación y el control plane valida el hash de la sesión.
  store.set(COOKIE.csrf, args.csrfToken, {
    ...baseCookie,
    httpOnly: false,
    maxAge: args.maxAgeSeconds,
  });
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
  for (const name of [COOKIE.session, COOKIE.refresh, COOKIE.csrf]) {
    store.set(name, "", { ...baseCookie, maxAge: 0, httpOnly: name !== COOKIE.csrf });
  }
}

export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(COOKIE.session)?.value || null;
}

export async function getRefreshToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(COOKIE.refresh)?.value || null;
}

export async function getCsrfToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(COOKIE.csrf)?.value || null;
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

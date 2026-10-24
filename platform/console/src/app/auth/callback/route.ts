/**
 * Callback OIDC (spec 009 REQ-AC-001, D-AC-02).
 *
 * El IdP vuelve aquí con `code` + `state`. Este route handler (server-side):
 *   1. valida state contra la cookie del baile,
 *   2. canjea code → id_token con el verifier (cookie httpOnly),
 *   3. minta la sesión ADMIN en el control plane (POST /api/auth/session con
 *      kind="admin"): el body devuelve session_token=null y la sesión llega
 *      como Set-Cookie `trust_session` — se extrae del header,
 *   4. guarda session/refresh/csrf en cookies del console (D-AC-01).
 */
import { NextResponse, type NextRequest } from "next/server";
import { ApiError, rawApi } from "@/lib/control-plane";
import { buildAuthorizeUrl, exchangeCodeForTokens, generatePkce, randomState } from "@/lib/oidc";
import {
  clearPkceCookies,
  clearStepupCookie,
  getPkceCookies,
  hasStepupCookie,
  setPkceCookies,
  setSessionCookies,
  setStepupCookie,
} from "@/lib/session";

interface MintSessionResponse {
  session_token: string | null;
  refresh_token: string;
  csrf_token: string;
  expires_at: string;
  mfa_verified: boolean;
}

function fail(request: NextRequest, message: string) {
  const origin = new URL(request.url).origin;
  return NextResponse.redirect(
    new URL("/login?error=" + encodeURIComponent(message), origin),
  );
}

/** Extrae el valor de `trust_session` de los Set-Cookie de la respuesta. */
function sessionCookieFrom(response: Response): string | null {
  const cookies = response.headers.getSetCookie?.() ?? [];
  for (const raw of cookies) {
    const m = raw.match(/^trust_session=([^;]+)/);
    if (m) return m[1];
  }
  return null;
}

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const errorParam = url.searchParams.get("error");

  if (errorParam) {
    await clearPkceCookies();
    return fail(request, `Sign-in was cancelled or failed (${errorParam}).`);
  }
  if (!code || !state) {
    await clearPkceCookies();
    return fail(request, "Sign-in callback is missing its authorization code.");
  }

  const pkce = await getPkceCookies();
  if (!pkce.verifier || !pkce.state || !pkce.orgCode) {
    await clearPkceCookies();
    return fail(request, "Your sign-in attempt expired. Please try again.");
  }
  if (pkce.state !== state) {
    await clearPkceCookies();
    return fail(request, "Sign-in state mismatch. Please try again.");
  }

  // 1) Canje code → id_token (server-side, con el verifier de la cookie).
  let idToken: string;
  try {
    const tokens = await exchangeCodeForTokens({
      orgCode: pkce.orgCode,
      code,
      verifier: pkce.verifier,
    });
    idToken = tokens.id_token;
  } catch {
    await clearPkceCookies();
    return fail(
      request,
      "Could not complete sign-in with your organization's identity provider.",
    );
  }

  // 2) Mint de sesión admin en el control plane (JWKS + rate limit ahí).
  let minted: MintSessionResponse;
  let sessionCookie: string | null = null;
  try {
    const res = await rawApi("/api/auth/session", {
      method: "POST",
      body: { id_token: idToken, kind: "admin" },
    });
    if (!res.ok) {
      let message = `Control plane rejected the sign-in (HTTP ${res.status}).`;
      try {
        const env = (await res.json()) as { error?: { message?: string } };
        if (env.error?.message) message = env.error.message;
      } catch {
        // no-JSON
      }
      await clearPkceCookies();
      return fail(request, message);
    }
    minted = (await res.json()) as MintSessionResponse;
    sessionCookie = sessionCookieFrom(res);
  } catch (cause) {
    await clearPkceCookies();
    if (cause instanceof ApiError && cause.status === 429) {
      return fail(request, "Too many sign-in attempts. Please wait and try again.");
    }
    return fail(request, "The Trust Agent control plane is unreachable.");
  }

  // 3) La sesión admin llega como Set-Cookie del control plane (el body trae
  //    session_token=null): la custodiamos como cookie httpOnly del console.
  if (!sessionCookie) {
    await clearPkceCookies();
    return fail(request, "Sign-in failed: no admin session was issued.");
  }

  // 2b) Step-up MFA (D-MFA-01): Keycloak 26 no emite `amr`; la evidencia MFA
  // es `acr` >= 2. Una sesión sin MFA (login solo-password, p. ej. el primer
  // login donde CONFIGURE_TOTP enrola el TOTP pero no cuenta como
  // autenticación OTP) no puede usar la consola gobernada. Se re-intenta el
  // authorize UNA vez — la condición LoA del realm fuerza el formulario OTP
  // sobre la sesión SSO existente — y si sigue sin MFA, falla cerrado.
  if (!minted.mfa_verified) {
    await clearPkceCookies();
    // La sesión sin MFA es inservible: revócala (best-effort; /api/auth/logout
    // está exento de la puerta MFA y acepta la cookie del control plane).
    try {
      await rawApi("/api/auth/logout", {
        method: "POST",
        bearer: sessionCookie,
      });
    } catch {
      // ignora: la sesión expira sola y no habilita ninguna ruta
    }
    if (await hasStepupCookie()) {
      await clearStepupCookie();
      return fail(
        request,
        "Your organization requires multi-factor authentication. " +
          "Enroll your authenticator app (TOTP) and sign in again.",
      );
    }
    const { verifier, challenge } = generatePkce();
    const state = randomState();
    await setPkceCookies({ verifier, state, orgCode: pkce.orgCode });
    await setStepupCookie();
    return NextResponse.redirect(
      buildAuthorizeUrl({ orgCode: pkce.orgCode, challenge, state }),
    );
  }
  await clearStepupCookie();

  const maxAge = Math.max(
    60,
    Math.floor((new Date(minted.expires_at).getTime() - Date.now()) / 1000),
  );
  await setSessionCookies({
    sessionToken: sessionCookie,
    refreshToken: minted.refresh_token,
    csrfToken: minted.csrf_token,
    maxAgeSeconds: maxAge,
  });
  await clearPkceCookies();

  return NextResponse.redirect(new URL("/overview", url.origin));
}

export const runtime = "nodejs";

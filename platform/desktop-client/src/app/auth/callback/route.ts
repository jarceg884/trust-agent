/**
 * Callback OIDC (REQ-DTB-001, D-DTB-02/03).
 *
 * El IdP vuelve aquí con `code` + `state`. Este route handler (server-side):
 *   1. valida state contra la cookie del baile,
 *   2. canjea code → id_token con el verifier (cookie httpOnly),
 *   3. minta la sesión desktop en el control plane (POST /api/auth/session),
 *   4. guarda sesión/refresh/csrf/id_token en cookies httpOnly locales.
 */
import { NextResponse, type NextRequest } from "next/server";
import { ApiError, api } from "@/lib/control-plane";
import { buildAuthorizeUrl, exchangeCodeForTokens, generatePkce, randomState } from "@/lib/oidc";
import {
  clearPkceCookies,
  clearStepupCookie,
  getPkceCookies,
  hasStepupCookie,
  setSessionCookies,
  setPkceCookies,
  setStepupCookie,
} from "@/lib/session";

interface MintSessionResponse {
  session_token: string | null;
  refresh_token: string;
  /** Solo sesiones admin (cookie) llevan CSRF; desktop usa Bearer → null. */
  csrf_token: string | null;
  expires_at: string;
  mfa_verified: boolean;
}

function fail(message: string) {
  const url = "/?error=" + encodeURIComponent(message);
  const res = NextResponse.redirect(new URL(url, "http://127.0.0.1:9090"));
  return res;
}

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const errorParam = url.searchParams.get("error");

  if (errorParam) {
    await clearPkceCookies();
    return fail(
      `Your organization's sign-in was cancelled or failed (${errorParam}).`,
    );
  }
  if (!code || !state) {
    await clearPkceCookies();
    return fail("Sign-in callback is missing its authorization code.");
  }

  const pkce = await getPkceCookies();
  if (!pkce.verifier || !pkce.state || !pkce.orgCode) {
    await clearPkceCookies();
    return fail("Your sign-in attempt expired. Please try again.");
  }
  if (pkce.state !== state) {
    await clearPkceCookies();
    return fail("Sign-in state mismatch. Please try again.");
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
    return fail("Could not complete sign-in with your organization's identity provider.");
  }

  // 2) Mint de sesión desktop en el control plane (JWKS + rate limit ahí).
  let minted: MintSessionResponse;
  try {
    minted = await api<MintSessionResponse>("/api/auth/session", {
      method: "POST",
      body: { id_token: idToken, kind: "desktop" },
    });
  } catch (cause) {
    await clearPkceCookies();
    if (cause instanceof ApiError) {
      if (cause.status === 401) {
        return fail("Sign-in rejected: your account is not recognized in this organization.");
      }
      if (cause.status === 429) {
        return fail("Too many sign-in attempts. Please wait and try again.");
      }
      return fail(cause.message);
    }
    return fail("The Trust Agent control plane is unreachable.");
  }

  if (!minted.session_token) {
    await clearPkceCookies();
    return fail("Sign-in failed: no desktop session was issued.");
  }

  // 2b) Step-up MFA (D-MFA-01): Keycloak 26 no emite `amr`; la evidencia MFA
  // es `acr` >= 2. Una sesión sin MFA (login solo-password, p. ej. el primer
  // login donde CONFIGURE_TOTP enrola el TOTP pero no cuenta como
  // autenticación OTP) no puede entrar al dashboard gobernado. Se re-intenta
  // el authorize UNA vez — la condición LoA del realm fuerza el formulario
  // OTP sobre la sesión SSO existente — y si sigue sin MFA, falla cerrado.
  if (!minted.mfa_verified) {
    await clearPkceCookies();
    // La sesión sin MFA es inservible para las rutas gobernadas: revócala
    // (best-effort; /api/auth/logout está exento de la puerta MFA).
    try {
      await api("/api/auth/logout", {
        method: "POST",
        bearer: minted.session_token,
      });
    } catch {
      // ignora: la sesión expira sola y no habilita ninguna ruta
    }
    if (await hasStepupCookie()) {
      await clearStepupCookie();
      return fail(
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

  // 3) Cookies httpOnly locales: custodia de tokens del desktop.
  const maxAge = Math.max(
    60,
    Math.floor((new Date(minted.expires_at).getTime() - Date.now()) / 1000),
  );
  await setSessionCookies({
    sessionToken: minted.session_token,
    refreshToken: minted.refresh_token,
    csrfToken: minted.csrf_token ?? "",
    idToken,
    maxAgeSeconds: maxAge,
  });
  await clearPkceCookies();

  const res = NextResponse.redirect(new URL("/dashboard", url.origin));
  // Las cookies ya se setearon vía cookies(); nada más que hacer.
  return res;
}

export const runtime = "nodejs";

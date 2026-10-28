/**
 * Inicio del baile OIDC PKCE (spec 009 REQ-AC-001, D-AC-02).
 *
 * GET /auth/login?org=<slug>[&acr=2] → valida el org code, guarda
 * verifier/state en cookies httpOnly efímeras (10 min) y redirige al
 * authorize del realm.
 *
 * Spec 015 (D-AUTH-06/08): el `acr_values` del baile se elige por org-config
 * — 2 cuando la org exige MFA (policy require_mfa=true), 1 cuando es
 * opcional (password-only; la app sugiere enrolar vía el banner persistente).
 * `?acr=2` (botón "Set up MFA" del banner) fuerza el nivel 2 para disparar
 * el enrolamiento/verificación TOTP inline sobre la sesión SSO existente.
 * Sin org-config alcanzable se falla cerrado (acr 2).
 */
import { NextResponse, type NextRequest } from "next/server";
import { isValidOrgCode } from "@/lib/config";
import { orgConfig } from "@/lib/control-plane";
import { requestOrigin } from "@/lib/origin";
import { buildAuthorizeUrl, generatePkce, randomState } from "@/lib/oidc";
import { setPkceCookies } from "@/lib/session";

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const org = (url.searchParams.get("org") ?? "").trim().toLowerCase();
  const acrOverride = url.searchParams.get("acr");

  if (!isValidOrgCode(org)) {
    return NextResponse.redirect(
      new URL("/login?error=invalid-org", requestOrigin(request)),
    );
  }

  // Org inexistente → mensaje honesto en el login (el control plane ya
  // rate-limitea la enumeración de orgs; aquí la llamada sale del BFF).
  const cfg = await orgConfig(org);
  if (cfg === null) {
    return NextResponse.redirect(
      new URL("/login?error=unknown-org", requestOrigin(request)),
    );
  }

  const acrValues = acrOverride === "2" || cfg.require_mfa ? "2" : "1";

  const { verifier, challenge } = generatePkce();
  const state = randomState();
  await setPkceCookies({ verifier, state, orgCode: org });

  // F1: el redirect_uri sigue al host por el que entra el navegador para que
  // el baile PKCE (cookies host-only incluidas) no cambie de host.
  return NextResponse.redirect(
    buildAuthorizeUrl({
      orgCode: org,
      challenge,
      state,
      redirectUri: `${requestOrigin(request)}/auth/callback`,
      acrValues,
    }),
  );
}

export const runtime = "nodejs";

/**
 * Inicio del baile OIDC PKCE (spec 009 REQ-AC-001, D-AC-02).
 *
 * GET /auth/login?org=<slug> → valida el org code, guarda verifier/state en
 * cookies httpOnly efímeras (10 min) y redirige al authorize del realm.
 */
import { NextResponse, type NextRequest } from "next/server";
import { isValidOrgCode } from "@/lib/config";
import { buildAuthorizeUrl, generatePkce, randomState } from "@/lib/oidc";
import { setPkceCookies } from "@/lib/session";

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const org = (url.searchParams.get("org") ?? "").trim().toLowerCase();

  if (!isValidOrgCode(org)) {
    return NextResponse.redirect(
      new URL("/login?error=invalid-org", url.origin),
    );
  }

  const { verifier, challenge } = generatePkce();
  const state = randomState();
  await setPkceCookies({ verifier, state, orgCode: org });

  return NextResponse.redirect(buildAuthorizeUrl({ orgCode: org, challenge, state }));
}

export const runtime = "nodejs";

"use server";

/**
 * Server action del login (D-DTB-02): genera PKCE S256 + state, los guarda en
 * cookies httpOnly y redirige el navegador al authorize del realm de la org.
 * El verifier jamás llega al cliente.
 */
import { redirect } from "next/navigation";
import { isValidOrgCode } from "@/lib/config";
import { buildAuthorizeUrl, generatePkce, randomState } from "@/lib/oidc";
import { setPkceCookies } from "@/lib/session";

export async function startLogin(formData: FormData) {
  const orgCode = String(formData.get("orgCode") ?? "")
    .trim()
    .toLowerCase();

  if (!isValidOrgCode(orgCode)) {
    redirect(
      "/?error=" +
        encodeURIComponent(
          "Enter your organization code (e.g. acme-corp).",
        ),
    );
  }

  const { verifier, challenge } = generatePkce();
  const state = randomState();
  await setPkceCookies({ verifier, state, orgCode });

  redirect(buildAuthorizeUrl({ orgCode, challenge, state }));
}

/**
 * Logout (spec 009 REQ-AC-004): revoca la sesión en el control plane
 * (auditada como auth.logout) y limpia las cookies del console.
 * Solo POST (no GET): evita logout por navegación cruzada.
 */
import { NextResponse, type NextRequest } from "next/server";
import { rawApi } from "@/lib/control-plane";
import { requestOrigin } from "@/lib/origin";
import {
  clearSessionCookies,
  getCsrfToken,
  getRefreshToken,
  getSessionToken,
} from "@/lib/session";

export async function POST(request: NextRequest) {
  const origin = requestOrigin(request);
  const session = await getSessionToken();
  const csrf = await getCsrfToken();

  if (session) {
    try {
      // Best-effort: la sesión puede estar ya expirada; las cookies se limpian
      // igual. El logout exitoso queda auditado en el tenant (AC-AC-004).
      await rawApi("/api/auth/logout", {
        method: "POST",
        sessionCookie: session,
        csrfToken: csrf,
      });
    } catch {
      // API caída o sesión inválida: continuar con la limpieza local.
    }
  }
  void (await getRefreshToken()); // el refresh muere con la sesión revocada
  await clearSessionCookies();
  return NextResponse.redirect(new URL("/login", origin));
}

export const runtime = "nodejs";

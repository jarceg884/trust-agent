/**
 * Logout del operador (spec 025, REQ-PS-003): revoca la sesión en el
 * control plane (ops.provider_sessions → status=revoked) y borra la
 * cookie `ta_provider`. La revocación server-side es best-effort: si el
 * control plane no responde, la cookie se borra igual (fail-closed en la
 * UI; la sesión expira por TTL en la BD).
 */
import { NextResponse } from "next/server";
import { api } from "@/lib/control-plane";
import { clearProviderCookie, getProviderToken } from "@/lib/provider-session";

export async function POST() {
  const token = await getProviderToken();
  if (token) {
    try {
      await api("/api/provider/auth/logout", { method: "POST", bearer: token });
    } catch {
      // best-effort: la cookie se borra de todos modos
    }
  }
  await clearProviderCookie();
  return NextResponse.json({ ok: true });
}

export const runtime = "nodejs";

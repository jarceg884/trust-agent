/**
 * Login del operador del proveedor (spec 014 REQ-PC-010, D-PC-02).
 *
 * Valida el token pegado contra el control plane REAL (`GET /api/admin/tenants`
 * con el token como Bearer): 200 ⇒ cookie httpOnly + redirect a la lista;
 * 401/403/503 ⇒ redirect a /login con el reason real (fail-closed).
 */
import { NextResponse } from "next/server";
import { api } from "@/lib/control-plane";
import { setProviderCookie } from "@/lib/provider-session";

export async function POST(request: Request) {
  let token = "";
  try {
    const body = (await request.json()) as { token?: unknown };
    token = typeof body.token === "string" ? body.token.trim() : "";
  } catch {
    // body inválido → token vacío → 401 real abajo
  }
  if (!token) {
    return NextResponse.json(
      { ok: false, reason: "invalid_token", message: "Enter the provider token." },
      { status: 401 },
    );
  }

  try {
    // Validación real: el control plane decide si el token es del proveedor.
    await api("/api/admin/tenants", { bearer: token });
  } catch (cause) {
    const status = cause instanceof Error && "status" in cause
      ? (cause as { status: number }).status
      : 503;
    const reason = cause instanceof Error && "reason" in cause
      ? (cause as { reason: string }).reason
      : "unreachable";
    const message = cause instanceof Error ? cause.message : "Validation failed.";
    return NextResponse.json(
      { ok: false, reason, message },
      { status: status === 503 ? 503 : status },
    );
  }

  await setProviderCookie(token);
  return NextResponse.json({ ok: true });
}

export const runtime = "nodejs";

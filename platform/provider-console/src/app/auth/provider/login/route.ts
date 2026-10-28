/**
 * Login del operador del proveedor (spec 025, REQ-PS-003/004/008).
 *
 * Usuario+contraseña(+TOTP si MFA activa) contra el control plane REAL
 * (`POST /api/provider/auth/login`): 200 ⇒ cookie httpOnly con el token de
 * sesión `pst1…` (D-PS-04 — el token jamás llega a JS del navegador) +
 * estado del operador para que la UI enrute (required action / tenants).
 * 401 `mfa_code_required` ⇒ la UI pide el código TOTP (no es un error
 * terminal); 429 throttle; 503 fail-closed sin operador seedeado.
 */
import { NextResponse } from "next/server";
import { api, ApiError } from "@/lib/control-plane";
import { setProviderCookie } from "@/lib/provider-session";

interface LoginOk {
  operator: {
    username: string;
    must_change_password: boolean;
    mfa_enrolled: boolean;
  };
  session_token: string;
  expires_at: string;
}

export async function POST(request: Request) {
  let body: { username?: unknown; password?: unknown; totp_code?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json(
      { ok: false, reason: "validation_failed", message: "Invalid request body." },
      { status: 422 },
    );
  }
  const username = typeof body.username === "string" ? body.username.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  const totpCode =
    typeof body.totp_code === "string" && body.totp_code.trim()
      ? body.totp_code.trim()
      : undefined;
  if (!username || !password) {
    return NextResponse.json(
      {
        ok: false,
        reason: "validation_failed",
        message: "Enter your username and password.",
      },
      { status: 422 },
    );
  }

  try {
    const result = await api<LoginOk>("/api/provider/auth/login", {
      method: "POST",
      body: { username, password, totp_code: totpCode },
    });
    // El token de sesión vive SOLO en la cookie httpOnly (D-PS-04).
    await setProviderCookie(result.session_token);
    return NextResponse.json({
      ok: true,
      operator: {
        username: result.operator.username,
        must_change_password: result.operator.must_change_password,
        mfa_enrolled: result.operator.mfa_enrolled,
      },
    });
  } catch (cause) {
    if (cause instanceof ApiError) {
      return NextResponse.json(
        { ok: false, reason: cause.reason, message: cause.message },
        { status: cause.status },
      );
    }
    return NextResponse.json(
      { ok: false, reason: "unreachable", message: "Validation failed." },
      { status: 503 },
    );
  }
}

export const runtime = "nodejs";

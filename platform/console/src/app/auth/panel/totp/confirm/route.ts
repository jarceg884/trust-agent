/**
 * Auth panel TOTP confirm (D-AUTH-14): submits the one-time code; the
 * control plane completes Keycloak's CONFIGURE_TOTP required action
 * headlessly, exchanges the authorization code (acr:"2") and mints the
 * MFA-verified admin session — which lands here as cookies.
 */
import { NextResponse } from "next/server";
import { callPanel, sessionCookieFrom } from "@/lib/panel";
import {
  clearPanelDanceCookie,
  clearPanelPendingCookie,
  getPanelDanceCookie,
  setSessionCookies,
} from "@/lib/session";

export async function POST(request: Request) {
  let body: { code?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { status: "error", message: "Invalid request." },
      { status: 400 },
    );
  }
  const code = (body.code ?? "").trim();
  if (!code) {
    return NextResponse.json(
      { status: "error", message: "Enter the code from your authenticator app." },
      { status: 400 },
    );
  }
  const handle = await getPanelDanceCookie();
  if (!handle) {
    return NextResponse.json(
      {
        status: "error",
        reason: "dance_expired",
        message: "Your setup session expired. Please start again.",
      },
      { status: 410 },
    );
  }

  const result = await callPanel("/api/auth/panel/totp/confirm", {
    handle,
    code,
  });
  if (!result.ok) {
    if (result.reason === "dance_expired") {
      await clearPanelDanceCookie();
    }
    return NextResponse.json(
      { status: "error", reason: result.reason, message: result.message },
      { status: result.status },
    );
  }

  const payload = result.body;
  const session =
    payload.session_token || sessionCookieFrom(result.response);
  if (payload.status === "ok" && session && payload.refresh_token && payload.csrf_token) {
    const maxAge = payload.expires_at
      ? Math.max(60, Math.floor((new Date(payload.expires_at).getTime() - Date.now()) / 1000))
      : 8 * 3600;
    await setSessionCookies({
      sessionToken: session,
      refreshToken: payload.refresh_token,
      csrfToken: payload.csrf_token,
      maxAgeSeconds: maxAge,
    });
    await clearPanelDanceCookie();
    await clearPanelPendingCookie();
    return NextResponse.json({ status: "ok", mfa_verified: payload.mfa_verified });
  }
  return NextResponse.json(
    { status: "error", message: "Could not complete the setup. Please try again." },
    { status: 502 },
  );
}

export const runtime = "nodejs";

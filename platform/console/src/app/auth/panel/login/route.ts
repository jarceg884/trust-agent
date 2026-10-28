/**
 * Auth panel login (spec 015 v1.3.0, ADR-019, D-AUTH-12).
 *
 * POST /auth/panel/login {org, email, password, totp?} — the BFF forwards to
 * the control plane's Direct Access Grant and maps the outcome:
 *   - ok            → admin session cookies set here, redirect /overview
 *   - update_password → pending-context cookie, redirect /set-password
 *   - enroll_totp   → dance-handle cookie, redirect /mfa-setup
 *   - errors        → the upstream envelope (generic 401 / mfa hint / 429)
 *
 * The browser never leaves the console origin: zero Keycloak pages, zero
 * Keycloak URLs (AC-AUTH-011).
 */
import { NextResponse } from "next/server";
import { callPanel, sessionCookieFrom } from "@/lib/panel";
import {
  setPanelDanceCookie,
  setPanelPendingCookie,
  setSessionCookies,
} from "@/lib/session";

export async function POST(request: Request) {
  let body: { org?: string; email?: string; password?: string; totp?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { status: "error", message: "Invalid request." },
      { status: 400 },
    );
  }
  const org = (body.org ?? "").trim().toLowerCase();
  const email = (body.email ?? "").trim();
  if (!org || !email || !body.password) {
    return NextResponse.json(
      { status: "error", message: "Enter your organization code, email and password." },
      { status: 400 },
    );
  }

  const result = await callPanel("/api/auth/panel/login", {
    org,
    email,
    password: body.password,
    totp: body.totp || null,
  });
  if (!result.ok) {
    return NextResponse.json(
      {
        status: "error",
        reason: result.reason,
        message: result.message,
      },
      { status: result.status },
    );
  }

  const payload = result.body;
  if (payload.status === "update_password") {
    // First login: the one-time password was PROVEN by the control plane
    // (the grant's "account not fully set up" only happens after credential
    // validation) — the proof handle carries it to the set-password panel.
    await setPanelPendingCookie({ org, email, handle: payload.handle ?? null });
    return NextResponse.json({ status: "update_password" });
  }
  if (payload.status === "enroll_totp" && payload.handle) {
    // CONFIGURE_TOTP required action (or first-login enrollment): the dance
    // already started server-side with the password from THIS request.
    await setPanelPendingCookie({ org, email });
    await setPanelDanceCookie(payload.handle);
    return NextResponse.json({ status: "enroll_totp" });
  }
  if (payload.status === "ok") {
    const session =
      payload.session_token || sessionCookieFrom(result.response);
    if (!session || !payload.refresh_token || !payload.csrf_token) {
      return NextResponse.json(
        { status: "error", message: "Sign-in failed: no admin session was issued." },
        { status: 502 },
      );
    }
    const maxAge = payload.expires_at
      ? Math.max(60, Math.floor((new Date(payload.expires_at).getTime() - Date.now()) / 1000))
      : 8 * 3600;
    await setSessionCookies({
      sessionToken: session,
      refreshToken: payload.refresh_token,
      csrfToken: payload.csrf_token,
      maxAgeSeconds: maxAge,
    });
    return NextResponse.json({ status: "ok", mfa_verified: payload.mfa_verified });
  }
  return NextResponse.json(
    { status: "error", message: "Unexpected sign-in response." },
    { status: 502 },
  );
}

export const runtime = "nodejs";

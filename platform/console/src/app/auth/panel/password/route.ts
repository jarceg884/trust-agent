/**
 * Auth panel password (D-AUTH-15): set (first login, UPDATE_PASSWORD pending
 * — org/email come from the pending cookie) or change (logged-in — org/email
 * are resolved from the session via /api/me server-side).
 */
import { NextResponse } from "next/server";
import { api, ApiError } from "@/lib/control-plane";
import { callPanel, sessionCookieFrom } from "@/lib/panel";
import {
  clearPanelPendingCookie,
  getPanelPendingCookie,
  getSessionToken,
  setSessionCookies,
} from "@/lib/session";
import type { WhoAmI } from "@/lib/types";

export async function POST(request: Request) {
  let body: {
    current_password?: string;
    new_password?: string;
    confirm?: string;
    totp?: string;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { status: "error", message: "Invalid request." },
      { status: 400 },
    );
  }
  const current = body.current_password ?? "";
  const next = body.new_password ?? "";
  if (!next || next !== (body.confirm ?? next)) {
    return NextResponse.json(
      { status: "error", message: "Enter the new password twice, matching." },
      { status: 400 },
    );
  }

  // Resolve org/email: pending cookie (first login, carries the proof
  // handle) or the live session.
  let org = "";
  let email = "";
  let proofHandle: string | null = null;
  const pending = await getPanelPendingCookie();
  if (pending) {
    org = pending.org;
    email = pending.email;
    proofHandle = pending.handle ?? null;
  } else {
    const session = await getSessionToken();
    if (!session) {
      return NextResponse.json(
        { status: "error", message: "Your session expired. Sign in again." },
        { status: 401 },
      );
    }
    try {
      const me = await api<WhoAmI>("/api/me", { sessionCookie: session });
      org = me.tenant_slug;
      email = me.email;
    } catch (cause) {
      const message =
        cause instanceof ApiError
          ? cause.message
          : "Could not verify your session. Sign in again.";
      return NextResponse.json(
        { status: "error", message },
        { status: cause instanceof ApiError ? cause.status : 503 },
      );
    }
  }

  const result = await callPanel("/api/auth/panel/password", {
    org,
    email,
    current_password: current,
    new_password: next,
    totp: body.totp || null,
    proof_handle: proofHandle,
  });
  if (!result.ok) {
    return NextResponse.json(
      { status: "error", reason: result.reason, message: result.message },
      { status: result.status },
    );
  }

  const payload = result.body;
  if (payload.status === "ok" && payload.next !== "login") {
    // First-login path: the control plane re-granted with the NEW password
    // and minted the admin session — land the user in the console.
    const session =
      payload.session_token || sessionCookieFrom(result.response);
    if (session && payload.refresh_token && payload.csrf_token) {
      const maxAge = payload.expires_at
        ? Math.max(60, Math.floor((new Date(payload.expires_at).getTime() - Date.now()) / 1000))
        : 8 * 3600;
      await setSessionCookies({
        sessionToken: session,
        refreshToken: payload.refresh_token,
        csrfToken: payload.csrf_token,
        maxAgeSeconds: maxAge,
      });
      await clearPanelPendingCookie();
      return NextResponse.json({ status: "ok", redirect: "/overview" });
    }
  }
  await clearPanelPendingCookie();
  // Voluntary change (session untouched) or first-login needing a fresh
  // sign-in (e.g. OTP also required).
  return NextResponse.json({ status: "ok", redirect: payload.next === "login" ? "/login" : null });
}

export const runtime = "nodejs";

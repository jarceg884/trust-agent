/**
 * Auth panel TOTP setup (D-AUTH-14, banner flow): the logged-in user
 * re-authenticates with their password; the control plane proves it via a
 * direct grant and starts the headless enrollment dance. The handle is kept
 * in an httpOnly cookie — never in a URL.
 */
import { NextResponse } from "next/server";
import { callPanel } from "@/lib/panel";
import { setPanelDanceCookie } from "@/lib/session";

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
      { status: "error", message: "Enter your email and password." },
      { status: 400 },
    );
  }
  const result = await callPanel("/api/auth/panel/totp/setup", {
    org,
    email,
    password: body.password,
    totp: body.totp || null,
  });
  if (!result.ok) {
    return NextResponse.json(
      { status: "error", reason: result.reason, message: result.message },
      { status: result.status },
    );
  }
  if (!result.body.handle) {
    return NextResponse.json(
      { status: "error", message: "Setup could not be started." },
      { status: 502 },
    );
  }
  await setPanelDanceCookie(result.body.handle);
  return NextResponse.json({ status: "ok" });
}

export const runtime = "nodejs";

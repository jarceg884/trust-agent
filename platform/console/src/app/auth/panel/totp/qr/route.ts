/**
 * Auth panel TOTP QR (D-AUTH-14): returns the enrollment QR (PNG data-URI)
 * for the in-flight dance referenced by the httpOnly handle cookie.
 */
import { NextResponse } from "next/server";
import { rawApi } from "@/lib/control-plane";
import { getPanelDanceCookie } from "@/lib/session";

export async function GET() {
  const handle = await getPanelDanceCookie();
  if (!handle) {
    return NextResponse.json(
      { status: "error", reason: "dance_expired", message: "Your setup session expired. Please start again." },
      { status: 410 },
    );
  }
  let res: Response;
  try {
    res = await rawApi(
      `/api/auth/panel/totp/qr?handle=${encodeURIComponent(handle)}`,
    );
  } catch {
    return NextResponse.json(
      { status: "error", message: "The control plane is unreachable." },
      { status: 503 },
    );
  }
  if (!res.ok) {
    let reason = "error";
    let message = "The QR code is no longer available. Please start again.";
    try {
      const env = (await res.json()) as { error?: { reason?: string; message?: string } };
      reason = env.error?.reason ?? reason;
      message = env.error?.message ?? message;
    } catch {
      // non-JSON
    }
    return NextResponse.json(
      { status: "error", reason, message },
      { status: res.status },
    );
  }
  const body = (await res.json()) as { qr_png?: string };
  return NextResponse.json({ status: "ok", qr_png: body.qr_png ?? null });
}

export const runtime = "nodejs";

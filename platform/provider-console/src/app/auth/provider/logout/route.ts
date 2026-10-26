/**
 * Logout del operador (spec 014 REQ-PC-010): borra la cookie `ta_provider`.
 */
import { NextResponse } from "next/server";
import { clearProviderCookie } from "@/lib/provider-session";

export async function POST() {
  await clearProviderCookie();
  return NextResponse.json({ ok: true });
}

export const runtime = "nodejs";

/**
 * GET /api/desktop/state — estado del usuario y sus dispositivos para la
 * pantalla de enrolamiento (client-side): decide si este dispositivo ya está
 * enrolado/activo comparando el fingerprint del keystore local.
 */
import { NextResponse } from "next/server";
import { ApiError, api, type MyDevice, type WhoAmI } from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";

export async function GET() {
  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json(
      { error: { reason: "no_session", message: "Sign in first." } },
      { status: 401 },
    );
  }
  try {
    const [me, devices] = await Promise.all([
      api<WhoAmI>("/api/me", { bearer: token }),
      api<{ devices: MyDevice[] }>("/api/devices/me", { bearer: token }),
    ]);
    return NextResponse.json({ me, devices: devices.devices });
  } catch (cause) {
    if (cause instanceof ApiError) {
      return NextResponse.json(
        {
          error: {
            reason: cause.reason,
            message: cause.message,
            request_id: cause.requestId,
          },
        },
        { status: cause.status },
      );
    }
    return NextResponse.json(
      { error: { reason: "unreachable", message: "Control plane unreachable." } },
      { status: 503 },
    );
  }
}

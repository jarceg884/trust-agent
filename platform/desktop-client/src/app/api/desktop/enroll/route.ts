/**
 * POST /api/desktop/enroll — proxy server-side del enrolamiento (REQ-DTB-002).
 *
 * El navegador manda solo {public_key, fingerprint, device_name, platform};
 * este handler inyecta el id_token (cookie httpOnly — el JWT con MFA que exige
 * POST /api/devices/enroll) y el org_code (slug del tenant verificado vía
 * /api/me). El navegador jamás manipula el JWT.
 */
import { NextResponse } from "next/server";
import { ApiError, api, type EnrollResponse, type WhoAmI } from "@/lib/control-plane";
import { getIdToken, getSessionToken } from "@/lib/session";

export async function POST(request: Request) {
  const token = await getSessionToken();
  const idToken = await getIdToken();
  if (!token || !idToken) {
    return NextResponse.json(
      { error: { reason: "no_session", message: "Sign in first." } },
      { status: 401 },
    );
  }

  const body = (await request.json()) as {
    public_key?: string;
    fingerprint?: string;
    device_name?: string;
    platform?: string;
  };
  if (!body.public_key || !body.fingerprint) {
    return NextResponse.json(
      {
        error: {
          reason: "validation_failed",
          message: "public_key and fingerprint are required.",
        },
      },
      { status: 422 },
    );
  }

  try {
    // org_code = slug del tenant VERIFICADO de la sesión (D-DEV-02).
    const me = await api<WhoAmI>("/api/me", { bearer: token });
    const result = await api<EnrollResponse>("/api/devices/enroll", {
      method: "POST",
      body: {
        org_code: me.tenant_slug,
        id_token: idToken,
        public_key: body.public_key,
        fingerprint: body.fingerprint,
        device_name: body.device_name ?? null,
        platform: body.platform ?? null,
      },
    });
    return NextResponse.json(result, { status: 202 });
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

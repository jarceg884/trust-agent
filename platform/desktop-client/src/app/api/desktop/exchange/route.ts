/**
 * POST /api/desktop/exchange — proxy del canje/polling del enrollment_token
 * (REQ-DTB-003, D-DEV-04). Pasa el estado real del control plane tal cual:
 *   202 pending (no consume) | 200 device token (consume) | 410 gone (uniforme).
 */
import { NextResponse } from "next/server";
import { ApiError, api, type ExchangeResponse } from "@/lib/control-plane";

export async function POST(request: Request) {
  const body = (await request.json()) as { enrollment_token?: string };
  if (!body.enrollment_token) {
    return NextResponse.json(
      {
        error: {
          reason: "validation_failed",
          message: "enrollment_token is required.",
        },
      },
      { status: 422 },
    );
  }

  try {
    const result = await api<ExchangeResponse>("/api/devices/enroll/exchange", {
      method: "POST",
      body: { enrollment_token: body.enrollment_token },
    });
    // 200 con device token (el pending 202 del control plane llega como 200
    // de api(); distinguimos por la presencia del token).
    const isPending = !("device_token" in result);
    return NextResponse.json(result, { status: isPending ? 202 : 200 });
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

/**
 * Public edge: device API under /api/me/** (governance-bundle sync and heartbeat, device
 * token auth). Only the allowlisted routes pass.
 */
import type { NextRequest } from "next/server";
import { config } from "@/lib/config";
import { edgeNotFound, relay } from "@/lib/edge-proxy";
import { DEVICE_REQUEST_HEADERS, isDeviceApiRoute } from "@/lib/edge-routes";

async function handle(request: NextRequest): Promise<Response> {
  const { pathname, search } = request.nextUrl;
  if (!isDeviceApiRoute(request.method, pathname)) return edgeNotFound();
  return relay(request, `${config.apiBaseUrl}${pathname}${search}`, DEVICE_REQUEST_HEADERS);
}

export const GET = handle;
export const POST = handle;

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

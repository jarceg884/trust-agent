/**
 * Public edge: a tenant's download page, installer (ranged), certificate,
 * desktop-config and CLI installer — forwarded to the control plane on loopback.
 */
import type { NextRequest } from "next/server";
import { config } from "@/lib/config";
import { edgeNotFound, relay } from "@/lib/edge-proxy";
import { DOWNLOAD_REQUEST_HEADERS, isDownloadRoute } from "@/lib/edge-routes";

async function handle(request: NextRequest): Promise<Response> {
  const { pathname, search } = request.nextUrl;
  if (!isDownloadRoute(request.method, pathname)) return edgeNotFound();
  return relay(request, `${config.apiBaseUrl}${pathname}${search}`, DOWNLOAD_REQUEST_HEADERS);
}

export const GET = handle;
export const HEAD = handle;

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

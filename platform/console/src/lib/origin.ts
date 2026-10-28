import type { NextRequest } from "next/server";

/**
 * Canonical origin of the incoming request (F1 fix, t_38ce1e82).
 *
 * Next.js rebuilds `request.url` from the hostname of its startup banner
 * (`localhost` when `next start` runs without `-H`), ignoring the Host header
 * the browser actually used. Redirecting to that origin sends a user who
 * browses http://127.0.0.1:3100 to http://localhost:3100 (or vice versa),
 * where the host-only session cookies do not apply — the user appears logged
 * out even though the session was minted. Rebuild the origin from the
 * proxy/host headers of the request, falling back to `request.url`.
 */
export function originFromHeaders(headers: Headers): string {
  const host = headers.get("x-forwarded-host") ?? headers.get("host");
  if (!host) return "";
  const proto = headers.get("x-forwarded-proto") ?? "http";
  return `${proto}://${host}`;
}

export function requestOrigin(request: NextRequest): string {
  const origin = originFromHeaders(request.headers);
  if (origin) return origin;
  return new URL(request.url).origin;
}

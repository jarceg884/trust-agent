/**
 * Relay for the console's public edge (see edge-routes.ts for WHAT is forwarded).
 *
 * Streams both ways it matters: a 1.8 GB ranged MSIX download and LiteLLM's SSE token
 * stream pass through without buffering. The client's disconnect aborts the upstream
 * request. Cookies never cross the edge: device calls authenticate with their device
 * token, model calls with their virtual key.
 */
import type { NextRequest } from "next/server";
import { clientAddress, RESPONSE_HEADERS } from "./edge-routes";

const NO_BODY_STATUS = new Set([204, 304]);

/**
 * Set by edge-client-address.cjs (preloaded by the image): only then does x-forwarded-for
 * hold the TCP peer instead of whatever the client sent. Without it no client address is
 * forwarded — the API then sees the relay's loopback, never a forgeable value.
 */
const EDGE_MARKER = Symbol.for("trust-agent.edge.client-address");

function verifiedClientAddress(request: NextRequest): string | null {
  if ((globalThis as Record<symbol, unknown>)[EDGE_MARKER] !== true) return null;
  return clientAddress(request.headers.get("x-forwarded-for"));
}

function errorEnvelope(status: number, reason: string, message: string): Response {
  return Response.json(
    { error: { code: status, reason, message, request_id: null } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

/** Anything outside the edge allowlist. */
export function edgeNotFound(): Response {
  return errorEnvelope(404, "not_found", "Not found.");
}

export async function relay(
  request: NextRequest,
  target: string,
  requestHeaders: readonly string[],
): Promise<Response> {
  const headers = new Headers();
  for (const name of requestHeaders) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  // Byte-exact relay: no upstream compression, so content-length / content-range stay true.
  headers.set("accept-encoding", "identity");
  const client = verifiedClientAddress(request);
  if (client) headers.set("x-forwarded-for", client);
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(/:$/, ""));

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return errorEnvelope(
      503,
      "unreachable",
      "The Trust Agent service is unreachable. Check your connection and try again.",
    );
  }

  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) out.set(name, value);
  }
  // fetch decodes an encoded body, so its original length no longer applies.
  if (upstream.headers.get("content-encoding")) out.delete("content-length");
  // no-transform: neither Next's compressor nor an intermediary may buffer an SSE stream
  // or re-encode a ranged download.
  out.set("Cache-Control", `${upstream.headers.get("cache-control") ?? "no-store"}, no-transform`);

  const body =
    request.method === "HEAD" || NO_BODY_STATUS.has(upstream.status) ? null : upstream.body;
  return new Response(body, { status: upstream.status, headers: out });
}

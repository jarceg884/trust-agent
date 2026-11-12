/**
 * Public edge of the control plane (decision Miller, 2026-11-12).
 *
 * Employees and governed desktops outside the LAN reach the platform through the
 * console's public port ONLY: no other port is published. This module is the single
 * allowlist of what that edge forwards — a tenant's public download endpoints, the
 * device API a governed desktop calls with its device token, and LiteLLM's
 * OpenAI-compatible inference routes (virtual-key auth). Anything else answers 404 at
 * the edge, so LiteLLM's admin API/UI and the control plane's other routes never become
 * reachable through the console.
 *
 * Pure (no Next.js imports): unit-tested with `node --test` (scripts/edge-routes.test.mjs).
 */

/** Tenant slug: the API's provisioning rule (app/tenants/provision.py SLUG_RE). */
const SLUG = "[a-z0-9][a-z0-9-]{2,48}";
/** One path segment that is never "." / ".." (dot-only segments are rejected). */
const SEGMENT = "(?!\\.+(?:/|$))[A-Za-z0-9._-]+";
const DOWNLOAD_PATH = new RegExp(`^/download/${SLUG}(?:/${SEGMENT})*$`);

/** `/download/{slug}` and everything under it: page, installer, certificate, desktop-config, CLI. */
export function isDownloadRoute(method: string, pathname: string): boolean {
  return (method === "GET" || method === "HEAD") && DOWNLOAD_PATH.test(pathname);
}

/**
 * Device API of a governed desktop: activation (Electron), enrollment recovery, token
 * refresh, bundle sync, heartbeat and attestation (trust-agent-governance plugin) and the
 * governed update check (Electron updater). Device ids are UUIDs (app/devices/service.py).
 */
const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const DEVICE_API: ReadonlyArray<readonly [string, RegExp]> = [
  ["POST", /^\/api\/devices\/enroll$/],
  ["POST", /^\/api\/devices\/enroll\/exchange$/],
  ["POST", /^\/api\/devices\/token\/refresh$/],
  ["POST", new RegExp(`^/api/devices/${UUID}/attestation$`)],
  ["GET", /^\/api\/me\/governance-bundle$/],
  ["POST", /^\/api\/me\/device-heartbeat$/],
  ["GET", /^\/api\/me\/update-check$/],
];

export function isDeviceApiRoute(method: string, pathname: string): boolean {
  return DEVICE_API.some(([allowed, pattern]) => allowed === method && pattern.test(pathname));
}

/** Model ids may carry provider prefixes and tags (`org/model:tag`). */
const MODEL_ID = "[A-Za-z0-9._:@+-]+(?:/[A-Za-z0-9._:@+-]+)*";
/** OpenAI-compatible inference routes, relative to `/llm/` (with or without `v1/`). */
const LLM_API: ReadonlyArray<readonly [string, RegExp]> = [
  ["POST", /^(?:v1\/)?chat\/completions$/],
  ["POST", /^(?:v1\/)?completions$/],
  ["POST", /^(?:v1\/)?embeddings$/],
  ["GET", /^(?:v1\/)?models$/],
  ["GET", new RegExp(`^(?:v1/)?models/${MODEL_ID}$`)],
];

export function isLlmRoute(method: string, subpath: string): boolean {
  if (subpath.split("/").some((segment) => /^\.+$/.test(segment))) {
    return false;
  }
  return LLM_API.some(([allowed, pattern]) => allowed === method && pattern.test(subpath));
}

/** Request headers relayed upstream per surface; everything else (cookies first) is dropped. */
export const DOWNLOAD_REQUEST_HEADERS = [
  "accept",
  "if-modified-since",
  "if-none-match",
  "if-range",
  "range",
  "user-agent",
] as const;
export const DEVICE_REQUEST_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "user-agent",
  "x-device-token",
  "x-request-id",
] as const;
export const LLM_REQUEST_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "user-agent",
  "x-request-id",
] as const;

/** Upstream response headers relayed to the client. */
export const RESPONSE_HEADERS = [
  "accept-ranges",
  "content-disposition",
  "content-length",
  "content-range",
  "content-type",
  "etag",
  "last-modified",
  "retry-after",
  "x-request-id",
] as const;

/**
 * Client address to forward upstream (the API's per-IP throttles and audit read it via
 * uvicorn's proxy headers, trusted from loopback only). The preloaded
 * edge-client-address.cjs has already replaced `x-forwarded-for` with the verified client
 * (a single entry); anything that is not one valid IP yields null.
 * Without it every edge client would share 127.0.0.1 — one throttle bucket for all.
 */
export function clientAddress(forwardedFor: string | null): string | null {
  let first = (forwardedFor ?? "").split(",")[0]?.trim() ?? "";
  if (first.toLowerCase().startsWith("::ffff:") && first.includes(".")) {
    first = first.slice("::ffff:".length);
  }
  const ipv4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/;
  const ipv6 = /^[0-9a-fA-F:]{2,39}$/;
  return ipv4.test(first) || (first.includes(":") && ipv6.test(first)) ? first : null;
}

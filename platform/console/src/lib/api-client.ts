/**
 * Cliente del navegador (spec 009 D-AC-01): TODO el tráfico pasa por el
 * proxy BFF del console (/api/proxy/*). El navegador jamás conoce las URLs
 * del control plane ni los tokens (httpOnly).
 *
 * Mutaciones: el proxy exige X-CSRF-Token (lo toma de la cookie ta_csrf que
 * el propio navegador envía); el control plane valida el hash (REQ-SEC-010).
 */
export class ApiError extends Error {
  status: number;
  reason: string;
  requestId: string | null;

  constructor(
    status: number,
    reason: string,
    message: string,
    requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.reason = reason;
    this.requestId = requestId;
  }
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    reason?: string;
    message?: string;
    request_id?: string | null;
  };
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

function csrfFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(/(?:^|;\s*)ta_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Llama al proxy del console y devuelve el JSON parseado. */
export async function api<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, signal } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const csrf = csrfFromCookie();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let res: Response;
  try {
    res = await fetch(`/api/proxy${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(
      503,
      "unreachable",
      "The Admin Console server is unreachable. Check your connection and try again.",
      null,
    );
  }

  if (res.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    // respuesta no-JSON
  }
  if (!res.ok) {
    const env = (payload ?? {}) as ErrorEnvelope;
    const err = env.error ?? {};
    throw new ApiError(
      res.status,
      err.reason ?? "error",
      err.message ?? `Request failed (HTTP ${res.status}).`,
      err.request_id ?? null,
    );
  }
  return payload as T;
}

/** Descarga un binario/texto del rango filtrado (export CSV/JSON, REQ-AU-003). */
export async function downloadBlob(path: string, filename: string): Promise<void> {
  const res = await fetch(`/api/proxy${path}`, { cache: "no-store" });
  if (!res.ok) {
    let message = `Export failed (HTTP ${res.status}).`;
    try {
      const env = (await res.json()) as ErrorEnvelope;
      if (env.error?.message) message = env.error.message;
    } catch {
      // no-JSON
    }
    throw new ApiError(res.status, "export_failed", message, null);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

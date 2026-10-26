/**
 * Cliente del navegador (spec 014, D-PC-02): TODO el tráfico interactivo pasa
 * por el proxy BFF (/api/proxy/*). El navegador jamás conoce el provider
 * token (cookie httpOnly); el proxy lo reenvía como Authorization: Bearer.
 *
 * Sin CSRF header: las rutas provider del control plane autentican por header
 * Authorization, no por cookie ambiental — un cross-site no puede dispararlas
 * (D-PC-02), y el proxy solo acepta same-origin.
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

/** Llama al proxy del Provider Console y devuelve el JSON parseado. */
export async function api<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, signal } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";

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
      "The Provider Console server is unreachable. Check your connection and try again.",
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

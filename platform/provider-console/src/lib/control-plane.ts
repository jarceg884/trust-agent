/**
 * Cliente server-side del control plane (spec 014, D-PC-02).
 *
 * Usado desde RSC (gate del shell, páginas de datos) y route handlers de
 * auth. El tráfico interactivo del navegador pasa por el proxy BFF
 * (/api/proxy/*); este cliente inyecta el provider token server-side.
 *
 * Envelope de errores §5.2: { error: { code, reason, message, request_id } }.
 */
import { config } from "./config";

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

export interface ApiOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  /** Provider token (Bearer). */
  bearer?: string | null;
}

/** Llama a la API del control plane y devuelve el JSON; lanza ApiError si falla. */
export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const { method = "GET", body, bearer } = options;
  const headers: Record<string, string> = {
    "User-Agent": "trust-agent-provider-console/0.1.0",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let res: Response;
  try {
    res = await fetch(`${config.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      503,
      "unreachable",
      "The Trust Agent control plane is unreachable. Check your connection and try again.",
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
      err.message ?? `Control plane error (HTTP ${res.status}).`,
      err.request_id ?? null,
    );
  }
  return payload as T;
}

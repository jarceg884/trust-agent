/**
 * Cliente server-side del control plane (spec 009 D-AC-01).
 *
 * Usado desde RSC (gate del shell) y route handlers de auth. El tráfico de
 * las páginas pasa por el proxy BFF (/api/proxy/*); este cliente es para
 * llamadas server-side directas con el token de la cookie httpOnly.
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
  /** Token de sesión (Bearer) o null para rutas sin auth. */
  bearer?: string | null;
  /** Cookie `trust_session` del control plane (flujo admin con CSRF). */
  sessionCookie?: string | null;
  csrfToken?: string | null;
}

/** Llama a la API del control plane y devuelve la Response cruda. */
export async function rawApi(
  path: string,
  options: ApiOptions = {},
): Promise<Response> {
  const { method = "GET", body, bearer, sessionCookie, csrfToken } = options;
  const headers: Record<string, string> = {
    "User-Agent": "trust-agent-console/0.1.0",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  if (sessionCookie) headers.Cookie = `trust_session=${sessionCookie}`;
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  return fetch(`${config.apiBaseUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
}

/** Llama a la API y devuelve el JSON parseado; lanza ApiError si falla. */
export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  let res: Response;
  try {
    res = await rawApi(path, options);
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
    // respuesta no-JSON (p. ej. API caída a mitad de respuesta)
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

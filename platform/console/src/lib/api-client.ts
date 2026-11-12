/**
 * Cliente del navegador (spec 009 D-AC-01): TODO el tráfico pasa por el
 * proxy BFF del console (/api/proxy/*). El navegador jamás conoce las URLs
 * del control plane ni los tokens (httpOnly).
 *
 * Mutaciones: el proxy exige X-CSRF-Token (lo toma de la cookie ta_console_csrf
 * que el propio navegador envía); el control plane valida el hash (REQ-SEC-010).
 */
export class ApiError extends Error {
  status: number;
  reason: string;
  requestId: string | null;
  /** Envelope COMPLETO de la respuesta de error (spec 041): el control
   *  plane fusiona los details al nivel superior (p. ej. `assignments`
   *  de un 409 al borrar una política con asignaciones, o `errors` por
   *  key de un 422). La UI los lee para diálogos con detalle real. */
  payload: Record<string, unknown> | null;

  constructor(
    status: number,
    reason: string,
    message: string,
    requestId: string | null,
    payload: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.reason = reason;
    this.requestId = requestId;
    this.payload = payload;
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
  // ta_console_csrf (prefijo por app — F2: no colisiona con otras apps del host).
  const m = document.cookie.match(/(?:^|;\s*)ta_console_csrf=([^;]+)/);
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
      (payload ?? null) as Record<string, unknown> | null,
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

/** URL del proxy BFF para una ruta API del control plane (p. ej. assets de
 *  branding en <img src> — la cookie httpOnly de sesión viaja sola). */
export function proxyUrl(apiPath: string): string {
  return `/api/proxy${apiPath}`;
}

/** Sube un archivo tal cual como cuerpo (spec 045: POST
 *  /api/marketplace/profiles/import con el .tar.gz de `hermes profile export`).
 *  Sin multipart: el control plane lee el stream con un tope duro antes de
 *  tocar la base. Errores con el envelope completo en `payload` (spec 041). */
export async function apiUploadFile<T = unknown>(
  path: string,
  file: Blob,
  contentType = "application/gzip",
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": contentType };
  const csrf = csrfFromCookie();
  if (csrf) headers["X-CSRF-Token"] = csrf;

  let res: Response;
  try {
    res = await fetch(`/api/proxy${path}`, {
      method: "POST",
      headers,
      body: file,
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
      err.message ?? `Upload failed (HTTP ${res.status}).`,
      err.request_id ?? null,
      (payload ?? null) as Record<string, unknown> | null,
    );
  }
  return payload as T;
}

/** Upload multipart (spec 028: POST /api/branding/assets) vía el proxy BFF.
 *  El navegador fija el Content-Type multipart con el boundary; el proxy
 *  reenvía el cuerpo y el header tal cual. CSRF igual que api(). */
export async function apiUpload<T = unknown>(
  path: string,
  form: FormData,
): Promise<T> {
  const headers: Record<string, string> = {};
  const csrf = csrfFromCookie();
  if (csrf) headers["X-CSRF-Token"] = csrf;

  let res: Response;
  try {
    res = await fetch(`/api/proxy${path}`, {
      method: "POST",
      headers,
      body: form,
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
      err.message ?? `Upload failed (HTTP ${res.status}).`,
      err.request_id ?? null,
    );
  }
  return payload as T;
}

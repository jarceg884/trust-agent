/**
 * Cliente server-side del control plane (spec 003/007).
 *
 * - Siempre con `Authorization: Bearer <session_token>` (la sesión desktop
 *   llega en el body del mint, no en cookies del control plane).
 * - Envelope de errores §5.2: { error: { code, reason, message, request_id } }.
 * - Cero `verify=False` equivalente: fetch normal con TLS del entorno local.
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
  bearer?: string | null;
  /** Permite pasar un JWT en el body (p. ej. id_token en /api/devices/enroll). */
  raw?: boolean;
}

/** Llama a la API del control plane y devuelve el JSON parseado. */
export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const { method = "GET", body, bearer } = options;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "User-Agent": "trust-agent-desktop/0.1.0",
  };
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let res: Response;
  try {
    res = await fetch(`${config.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (cause) {
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
    // respuesta no-JSON (p. ej. proxy caído)
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

// --- Tipos de las respuestas que consume el desktop (contrato 003/007) ---

export interface WhoAmI {
  user_id: string;
  email: string;
  display_name: string | null;
  department_id: string | null;
  tenant_id: string;
  tenant_slug: string;
  roles: { role_key: string; department_id: string | null }[];
  permissions: string[];
  mfa_verified: boolean;
  via: string;
}

export interface BudgetLevel {
  level: "user" | "dept" | "org";
  budget_cents: number | null;
  spent_cents: number;
  remaining_cents: number | null;
  period: string;
  cutoff: boolean;
}

export interface BudgetStatus {
  user: BudgetLevel;
  department: BudgetLevel;
  organization: BudgetLevel;
  cutoff: boolean;
  cutoff_level: string | null;
}

export type EffectivePolicy = Record<string, unknown>;

export interface MyDevice {
  id: string;
  fingerprint: string;
  device_name: string | null;
  platform: string | null;
  status: "pending" | "active" | "rejected" | "revoked";
  enrolled_at: string | null;
  approved_at: string | null;
  last_seen_at: string | null;
}

export interface GovernanceNotification {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  created_at: string;
  read_at: string | null;
}

/** GET /api/branding (spec 012 REQ-ORG-110/111) — branding activo del
 *  tenant, leído en runtime tras el login (REQ-BRD-002/003, spec 013). */
export interface Branding {
  product_name: string;
  tagline: string | null;
  accent_color: string | null;
  logo_url: string | null;
  source: "provider" | "tenant";
}

export interface EnrollResponse {
  device_id: string;
  status: string;
  enrollment_token: string;
}

export type ExchangeResponse =
  | { status: "pending"; device_id?: string }
  | { device_token: string; expires_in: number };

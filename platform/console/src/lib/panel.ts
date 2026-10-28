/**
 * Auth panel BFF helpers (spec 015 v1.3.0, ADR-019).
 *
 * The browser ONLY talks to these route handlers; the control plane does the
 * Direct Access Grant / the headless TOTP dance against Keycloak. Passwords
 * transit browser → BFF → control plane and are never logged or persisted
 * here (D-AUTH-16).
 */
import { rawApi } from "./control-plane";

export interface PanelErrorEnvelope {
  error: {
    code: number;
    reason: string;
    message: string;
    request_id: string | null;
  };
}

export interface PanelSessionBody {
  status: string;
  mfa_verified?: boolean;
  session_token?: string | null;
  refresh_token?: string;
  csrf_token?: string | null;
  expires_at?: string;
  handle?: string;
  expires_in?: number;
  next?: string;
}

/** Extracts the control-plane `trust_session` Set-Cookie (admin mints carry
 * the session as a cookie, session_token=null in the body — same contract as
 * the PKCE callback). */
export function sessionCookieFrom(response: Response): string | null {
  const cookies = response.headers.getSetCookie?.() ?? [];
  for (const raw of cookies) {
    const m = raw.match(/^trust_session=([^;]+)/);
    if (m) return m[1];
  }
  return null;
}

/** Calls a panel endpoint and normalizes the outcome for the client form:
 * -> { ok: true, body } | { ok: false, status, reason, message } */
export async function callPanel(
  path: string,
  body: unknown,
): Promise<
  | { ok: true; body: PanelSessionBody; response: Response }
  | { ok: false; status: number; reason: string; message: string }
> {
  let res: Response;
  try {
    res = await rawApi(path, { method: "POST", body });
  } catch {
    return {
      ok: false,
      status: 503,
      reason: "unreachable",
      message:
        "The Trust Agent control plane is unreachable. Check your connection and try again.",
    };
  }
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    // non-JSON
  }
  if (!res.ok) {
    const env = (payload ?? {}) as PanelErrorEnvelope;
    return {
      ok: false,
      status: res.status,
      reason: env.error?.reason ?? "error",
      message:
        env.error?.message ??
        `Sign-in failed (HTTP ${res.status}). Please try again.`,
    };
  }
  return { ok: true, body: (payload ?? {}) as PanelSessionBody, response: res };
}

/** Human copy for the panel error reasons (product voice, no Keycloak
 * jargon — the user never learns there is an IdP behind). */
export function panelErrorMessage(reason: string, fallback: string): string {
  switch (reason) {
    case "invalid_credentials":
      return "Invalid email or password.";
    case "mfa_code_required":
      return "Invalid credentials. If you have multi-factor authentication "
        + "configured, enter your one-time code as well.";
    case "invalid_totp_code":
      return "That code did not match. Check your authenticator app and try again.";
    case "dance_expired":
      return "Your setup session expired. Please start again.";
    case "already_enrolled":
      return "This account already has multi-factor authentication configured.";
    case "rate_limited":
      return "Too many attempts. Please wait a moment and try again.";
    case "account_not_ready":
      return "Your account needs attention from your administrator before you "
        + "can sign in.";
    default:
      return fallback;
  }
}

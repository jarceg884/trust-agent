"""Auth panel — Keycloak never visible (spec 015 v1.3.0, ADR-019).

Product decision (Miller, 2026-10-28): every authentication screen (login,
TOTP enrollment, password set/change, errors) is OUR product. Keycloak
remains the invisible identity engine: it validates credentials, enforces
OTP and issues the tokens — but the browser NEVER talks to it and never
sees its pages.

Three mechanisms, all probe-verified against the live KC 26.2.5:

1. LOGIN — Direct Access Grant (D-AUTH-12): ``grant_type=password`` (+ the
   ``totp`` form parameter when the user has MFA) against the tenant realm
   with the public ``trust-agent-web`` client. The id_token is verified via
   JWKS and mints a PostgreSQL session through the SAME transaction as
   POST /api/auth/session (``service.mint_transaction``).

2. MFA EVIDENCE (D-AUTH-13): the realm's direct grant flow is wired as
   ``trust-direct-grant-loa`` (see keycloak_admin.ensure_direct_grant_flow)
   so a password+OTP grant carries the signed ``acr:"2"`` that
   ``mfa_evidence()`` already accepts. The gate is NOT relaxed.

3. TOTP ENROLLMENT — headless required-action dance (D-AUTH-14): KC 26 has
   NO REST to generate a TOTP secret or create the OTP credential, so the
   control plane drives the CONFIGURE_TOTP required action server-side
   (authorize with ``acr_values=2`` + PKCE → login form → config-totp page
   → QR + totpSecret extracted by stable ids → the panel shows OUR QR →
   the user confirms the code → the dance completes → the authorization
   code is exchanged → id_token ``acr:"2"`` → mint). The dance state lives
   in ``ops.auth_dances`` (handle stored hashed, TTL 10 min). Passwords are
   NEVER persisted — they exist only inside a single request's memory.

SECURITY (D-AUTH-16, tradeoff accepted by product):
- Passwords are never logged, stored, or echoed in errors/audit. Audit
  events (auth.login, auth.totp_enrolled, auth.password_changed,
  auth.login_failed) carry NO credential material.
- Every endpoint is rate-limited via ops.login_throttle (REQ-SEC-006):
  per-IP pre-check plus a per-account counter keyed by the *email* hash
  (pre-verification there are no trusted claims — an attacker can at most
  burn the counter of an account whose email they know, which is exactly
  the brute-force this counter exists for).
- Wrong password → 401 generic ("Invalid email or password"), no oracle.
  The OTP hint (401 ``mfa_code_required``) is only returned when the
  account actually has an OTP credential (Admin REST lookup post-401).
- "Account is not fully set up" (400) is returned by Keycloak ONLY after
  the credentials validated (wrong password → 401 "Invalid user
  credentials", a different error — live-verified): it is the proof of
  knowledge of the current password that authorizes the UPDATE_PASSWORD /
  CONFIGURE_TOTP panels.
"""
import base64
import hashlib
import html as html_mod
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

from ..config import get_settings
from ..db import get_engine, get_migrator_engine, set_tenant
from ..errors import (
    ApiError,
    invalid_token,
    mfa_code_required,
    not_found,
    service_unavailable,
    validation_failed,
)
from . import service
from . import throttle
from .jwks import verify_id_token
from .service import mint_transaction

router = APIRouter(prefix="/api/auth/panel")

DANCE_TTL_SECONDS = 600
#: Max intra-dance hops when completing the enrollment (config-totp →
#: possibly the OTP form re-asking the same code → redirect with code).
_MAX_DANCE_HOPS = 3
_MIN_PASSWORD_LENGTH = 12  # parity with register.MIN_PASSWORD_LENGTH


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------

class PanelLoginBody(BaseModel):
    org: str
    email: str
    password: str
    totp: str | None = None


class TotpSetupBody(BaseModel):
    org: str
    email: str
    password: str
    totp: str | None = None


class TotpConfirmBody(BaseModel):
    handle: str
    code: str


class PanelPasswordBody(BaseModel):
    org: str
    email: str
    current_password: str = ""
    new_password: str
    totp: str | None = None
    # First-login flow (UPDATE_PASSWORD): the proof handle issued by the
    # login endpoint AFTER the direct grant validated the one-time password.
    # When present it replaces the current-password proof (the panel no
    # longer has the one-time password in memory).
    proof_handle: str | None = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str | None:
    import ipaddress

    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _kc():
    from ..tenants.keycloak_admin import KeycloakAdminClient

    return KeycloakAdminClient()


def _resolve_org(org: str) -> dict:
    """Org slug → tenant row (migrator engine: tenants is provider-side)."""
    from ..tenants.provision import find_tenant_by_slug

    org = (org or "").strip().lower()
    tenant = find_tenant_by_slug(get_migrator_engine(), org)
    if tenant is None:
        raise not_found("Unknown organization code.")
    return dict(tenant)


def _panel_acct_key(tenant_id: str, email: str) -> str:
    """Pre-verification account throttle key (email-hash family of
    throttle.account_key — no trusted claims exist before the grant)."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"acct:{tenant_id}:{digest}"


def _burn(keys: list[str | None]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        throttle.register_failure(conn, keys)


def _audit_login_failed(tenant_id: str, reason: str) -> None:
    """Tenant-chain audit for panel failures after the org resolved
    (D-AU-03): the org is real, the attempt is real. No credential
    material in the payload (D-AUTH-16). Committed in its own tx so it
    survives the error response."""
    from ..audit import service as audit_service

    engine = get_engine()
    try:
        with engine.begin() as conn:
            set_tenant(conn, tenant_id)
            audit_service.emit(
                conn,
                tenant_id=tenant_id,
                actor_role="system",
                action="auth.login_failed",
                payload={"reason": reason, "via": "panel"},
            )
    except Exception:  # noqa: BLE001 - audit is best-effort here
        pass


def _set_admin_cookie(response: Response, session_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_minutes * 60,
        path="/",
    )


def _mint_admin(
    request: Request, response: Response, *, id_token: str,
    acct_key: str | None,
) -> dict:
    """Verify a freshly obtained id_token and mint an admin session through
    the shared mint transaction (via="panel"). Returns the response body."""
    settings = get_settings()
    engine = get_engine()
    ip_k = throttle.ip_key(_client_ip(request))
    with engine.begin() as conn:
        throttle.check(conn, [ip_k])
    # The panel's email-keyed account counter must gate the mint too: a
    # locked account (repeated wrong passwords) stays locked even when a
    # request finally carries the RIGHT password (REQ-SEC-006 — the lock
    # must not be bypassable by succeeding after the failures).
    if acct_key:
        with engine.begin() as conn:
            throttle.check(conn, [acct_key])
    try:
        claims = verify_id_token(
            id_token,
            idp_base_urls=settings.idp_base_urls,
            audience=settings.oidc_audience,
            leeway=settings.jwt_leeway_seconds,
        )
    except ApiError:
        _burn([ip_k])
        raise
    claims_acct = throttle.account_key(claims["tenant_id"], claims["sub"])
    with engine.begin() as conn:
        throttle.check(conn, [claims_acct])
    try:
        with engine.begin() as conn:
            set_tenant(conn, claims["tenant_id"])
            result = mint_transaction(
                conn,
                claims=claims,
                acct_key=claims_acct,
                kind="admin",
                via="panel",
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
    except ApiError:
        _burn([claims_acct, ip_k, acct_key])
        raise
    tokens = result["tokens"]
    _set_admin_cookie(response, tokens.session_token)
    return {
        "status": "ok",
        "mfa_verified": result["mfa_verified"],
        # The consumer is the console BFF (server-side fetch), which stores
        # the token in its own httpOnly cookie — equivalent to the mint's
        # Set-Cookie, but usable without scraping headers.
        "session_token": tokens.session_token,
        "refresh_token": tokens.refresh_token,
        "csrf_token": tokens.csrf_token,
        "expires_at": tokens.expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Direct grant
# ---------------------------------------------------------------------------

def _direct_grant(realm: str, email: str, password: str,
                  totp: str | None) -> httpx.Response:
    settings = get_settings()
    base = settings.keycloak_admin_url.rstrip("/")
    data = {
        "grant_type": "password",
        "client_id": settings.oidc_audience,
        "username": email.strip(),
        "password": password,
        "scope": "openid",
    }
    if totp:
        data["totp"] = totp.strip()
    try:
        return httpx.post(
            f"{base}/realms/{realm}/protocol/openid-connect/token",
            data=data,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise service_unavailable(
            "The identity service is unreachable. Try again in a moment."
        ) from exc


def _grant_outcome(resp: httpx.Response) -> tuple[str, dict | None]:
    """Classify a direct grant response.

    -> ("ok", token_set) | ("invalid_credentials", None)
       | ("account_not_setup", None) | ("error", None)
    """
    if resp.status_code == 200:
        return "ok", resp.json()
    try:
        body = resp.json()
    except ValueError:
        return "error", None
    desc = body.get("error_description") or ""
    if resp.status_code == 401 and "credentials" in desc.lower():
        return "invalid_credentials", None
    if resp.status_code == 400 and "not fully set up" in desc.lower():
        # Keycloak validated the credentials BEFORE checking required
        # actions (live-verified: a wrong password answers 401 "Invalid
        # user credentials" instead) — this outcome PROVES the password.
        return "account_not_setup", None
    return "error", None


def _user_state(kc, realm: str, email: str) -> dict:
    """Admin REST lookup for the panel decisioning (never raises on
    missing user; fail-closed callers treat None as unknown)."""
    user = kc.find_user_by_email(realm, email.strip())
    if user is None:
        return {"exists": False}
    return {
        "exists": True,
        "id": user["id"],
        "required_actions": user.get("requiredActions") or [],
        "has_otp": kc.user_has_otp_credential(realm, user["id"]),
    }


# ---------------------------------------------------------------------------
# Headless CONFIGURE_TOTP dance (D-AUTH-14)
# ---------------------------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _dance_redirect_uri() -> str:
    uris = [u.strip() for u in get_settings().oidc_redirect_uris.split(",")
            if u.strip()]
    return uris[0]


def _dance_request(cookies: dict, method: str, url: str,
                   **kw) -> httpx.Response:
    """Manual cookie jar: Keycloak sets `Secure` cookies over http (browsers
    accept them on 127.0.0.1 — a secure context — but httpx refuses); the
    dance echoes them by hand exactly like a browser would."""
    headers = kw.pop("headers", {})
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        resp = httpx.request(method, url, headers=headers, timeout=20,
                             follow_redirects=False, **kw)
    except httpx.HTTPError as exc:
        raise service_unavailable(
            "The identity service is unreachable. Try again in a moment."
        ) from exc
    for key, value in resp.headers.multi_items():
        if key.lower() == "set-cookie":
            kv = value.split(";", 1)[0]
            name, _, val = kv.partition("=")
            cookies[name.strip()] = val.strip()
    return resp


def _parse_form(page: str) -> tuple[str | None, dict]:
    """(action, fields) of the first form in the page; the action is
    HTML-unescaped (KC templates emit &amp; entities — live-verified).
    Inputs without a value attribute (e.g. the login form's credentialId)
    are captured with an empty string."""
    m = re.search(r'<form[^>]*action="([^"]+)"', page)
    action = html_mod.unescape(m.group(1)) if m else None
    fields: dict = {}
    for im in re.finditer(r"<input[^>]*>", page):
        tag = im.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        fields[name.group(1)] = value.group(1) if value else ""
    return action, fields


def _extract_qr(page: str) -> str | None:
    """The config-totp page embeds the QR as an inline PNG data-URI
    (id=kc-totp-secret-qr-code). KC emits a space after the comma —
    normalize it so <img src=...> renders."""
    m = re.search(
        r'id="kc-totp-secret-qr-code"[^>]*src="data:image/png;base64,\s*'
        r'([A-Za-z0-9+/=]+)"', page)
    if m is None:
        return None
    return "data:image/png;base64," + m.group(1)


def _page_kind(page: str, fields: dict) -> str:
    if "totpSecret" in fields:
        return "config_totp"
    if re.search(r'name="totp"', page):
        return "otp_form"
    return "unknown"


def _start_dance(realm: str, email: str, password: str,
                 totp: str | None) -> dict:
    """Drive Keycloak's browser flow headless up to the config-totp page.

    authorize (acr_values=2, PKCE) → login form → POST credentials →
    config-totp page (inline CONFIGURE_TOTP for users without an OTP
    credential — D-AUTH-06 semantics). Returns the dance state; raises
    ApiError on wrong credentials or unexpected pages. The password is
    used in-memory only and NEVER copied into the returned state."""
    settings = get_settings()
    base = settings.keycloak_admin_url.rstrip("/")
    cookies: dict = {}
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    redirect_uri = _dance_redirect_uri()
    params = (
        "response_type=code"
        f"&client_id={settings.oidc_audience}"
        "&scope=openid"
        f"&redirect_uri={redirect_uri}"
        f"&state={_b64url(secrets.token_bytes(24))}"
        f"&code_challenge={challenge}"
        "&code_challenge_method=S256"
        "&acr_values=2"
    )
    resp = _dance_request(cookies, "GET",
                          f"{base}/realms/{realm}/protocol/openid-connect/auth?{params}")
    if resp.status_code != 200:
        raise service_unavailable(
            "The identity service did not start the sign-in flow."
        )
    action, fields = _parse_form(resp.text)
    if action is None or "username" not in fields and "username" not in resp.text:
        raise service_unavailable("Unexpected identity service response.")
    login = {"username": email.strip(), "password": password, "credentialId": ""}
    if totp:
        login["totp"] = totp.strip()
    resp = _dance_request(cookies, "POST", action, data=login)
    if resp.status_code == 200:
        # 200 after the credentials POST = the login form re-rendered with
        # an error (wrong credentials) — no oracle beyond "invalid".
        raise ApiError(
            status_code=401, code="unauthorized", reason="invalid_credentials",
            message="Invalid email or password.",
        )
    if resp.status_code != 302:
        raise service_unavailable("Unexpected identity service response.")
    resp = _dance_request(cookies, "GET", resp.headers["location"])
    if resp.status_code != 200:
        raise service_unavailable("Unexpected identity service response.")
    action, fields = _parse_form(resp.text)
    kind = _page_kind(resp.text, fields)
    if kind == "otp_form":
        # The user ALREADY has an OTP credential: the flow asks for the
        # code instead of the enrollment page.
        raise ApiError(
            status_code=409, code="conflict", reason="already_enrolled",
            message="This account already has multi-factor authentication "
                    "configured.",
        )
    if kind != "config_totp" or action is None:
        raise service_unavailable("Unexpected identity service response.")
    qr = _extract_qr(resp.text)
    if qr is None:
        raise service_unavailable(
            "The identity service did not provide a setup QR code."
        )
    return {
        "cookies": cookies,
        "form_action": action,
        "form_fields": fields,
        "qr_png": qr,
        "pkce_verifier": verifier,
        "redirect_uri": redirect_uri,
        "stage": "config_totp",
    }


def _exchange_dance_code(state: dict, code: str) -> dict:
    settings = get_settings()
    base = settings.keycloak_admin_url.rstrip("/")
    try:
        resp = httpx.post(
            f"{base}/realms/{state['realm']}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.oidc_audience,
                "redirect_uri": state["redirect_uri"],
                "code": code,
                "code_verifier": state["pkce_verifier"],
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise service_unavailable(
            "The identity service is unreachable."
        ) from exc
    if resp.status_code != 200:
        raise service_unavailable("Could not complete the sign-in flow.")
    return resp.json()


# -- dance persistence (ops.auth_dances) --------------------------------------

def _dance_save(kind: str, realm: str, tenant_id: str, email: str,
                state: dict) -> str:
    handle = secrets.token_urlsafe(24)
    handle_hash = hashlib.sha256(handle.encode()).hexdigest()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ops.auth_dances WHERE expires_at < now()"
            )
        )
        conn.execute(
            text(
                "INSERT INTO ops.auth_dances "
                "(handle_hash, kind, realm, tenant_id, email, state, "
                " expires_at) VALUES (:h, :kind, :realm, "
                "CAST(:tenant_id AS uuid), :email, CAST(:state AS jsonb), "
                "now() + make_interval(secs => :ttl))"
            ),
            {
                "h": handle_hash,
                "kind": kind,
                "realm": realm,
                "tenant_id": tenant_id,
                "email": email,
                "state": json.dumps(state),
                "ttl": DANCE_TTL_SECONDS,
            },
        )
    return handle


def _dance_load(handle: str) -> dict | None:
    handle_hash = hashlib.sha256(handle.encode()).hexdigest()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ops.auth_dances WHERE expires_at < now()"))
        row = conn.execute(
            text(
                "SELECT kind, realm, tenant_id, email, state FROM "
                "ops.auth_dances WHERE handle_hash = :h AND expires_at > now()"
            ),
            {"h": handle_hash},
        ).mappings().first()
    if row is None:
        return None
    out = dict(row)
    # psycopg decodes jsonb to dict natively; only decode when the driver
    # returned a raw string (defensive — both shapes are valid).
    state = out["state"]
    if isinstance(state, str):
        state = json.loads(state)
    out["state"] = state
    return out


def _dance_update(handle: str, state: dict) -> None:
    handle_hash = hashlib.sha256(handle.encode()).hexdigest()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.auth_dances SET state = CAST(:state AS jsonb), "
                "updated_at = now() WHERE handle_hash = :h"
            ),
            {"state": json.dumps(state), "h": handle_hash},
        )


def _dance_delete(handle: str) -> None:
    handle_hash = hashlib.sha256(handle.encode()).hexdigest()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ops.auth_dances WHERE handle_hash = :h"),
            {"h": handle_hash},
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _precheck(request: Request, org: str) -> tuple[dict, str, str | None]:
    """Shared pre-amble: IP throttle + org resolution.

    -> (tenant, realm, ip_key). Raises 404 (burns the IP counter) for an
    unknown org — same anti-enumeration behaviour as org-config."""
    engine = get_engine()
    ip_k = throttle.ip_key(_client_ip(request))
    with engine.begin() as conn:
        throttle.check(conn, [ip_k])
    try:
        tenant = _resolve_org(org)
    except ApiError:
        _burn([ip_k])
        raise
    realm = tenant.get("oidc_realm") or tenant["slug"]
    return tenant, realm, ip_k


@router.post("/login")
def panel_login(body: PanelLoginBody, request: Request, response: Response):
    """D-AUTH-12: email + password (+ OTP when enrolled) → admin session."""
    tenant, realm, ip_k = _precheck(request, body.org)
    tenant_id = str(tenant["id"])
    acct_k = _panel_acct_key(tenant_id, body.email)

    resp = _direct_grant(realm, body.email, body.password, body.totp)
    outcome, tokens = _grant_outcome(resp)

    if outcome == "ok" and tokens:
        return _mint_admin(request, response,
                           id_token=tokens["id_token"], acct_key=acct_k)

    if outcome == "invalid_credentials":
        _burn([acct_k, ip_k])
        hint = False
        try:
            state = _user_state(_kc(), realm, body.email)
            hint = state.get("has_otp") is True
        except Exception:  # noqa: BLE001 - hint is best-effort, fail-closed
            hint = False
        _audit_login_failed(tenant_id, "invalid_credentials")
        if hint:
            # D-AUTH-16: the OTP hint only for accounts that have MFA.
            raise mfa_code_required()
        raise ApiError(
            status_code=401, code="unauthorized", reason="invalid_credentials",
            message="Invalid email or password.",
        )

    if outcome == "account_not_setup":
        # Credentials PROVEN (see _grant_outcome). Route to the right panel.
        try:
            state = _user_state(_kc(), realm, body.email)
        except Exception:  # noqa: BLE001 - Admin REST down → fail closed
            raise service_unavailable(
                "The identity service is unreachable. Try again in a moment."
            )
        if not state.get("exists"):
            # Credentials valid in the IdP but no readable user (e.g. a
            # brokered-only identity): fail closed, generic message.
            _burn([acct_k, ip_k])
            _audit_login_failed(tenant_id, "unknown_user")
            raise ApiError(
                status_code=401, code="unauthorized",
                reason="invalid_credentials",
                message="Invalid email or password.",
            )
        actions = state.get("required_actions") or []
        if "UPDATE_PASSWORD" in actions:
            # The one-time password was PROVEN by this grant outcome; carry
            # the proof forward as a short-lived handle (hashed at rest,
            # same mechanics as the TOTP dance) so the set-password panel
            # does not ask for it again — only the holder of this handle
            # (the BFF's httpOnly cookie) may complete the change.
            handle = _dance_save("update_password", realm, tenant_id,
                                 body.email.strip(), {})
            return {"status": "update_password", "handle": handle,
                    "expires_in": DANCE_TTL_SECONDS}
        if "CONFIGURE_TOTP" in actions:
            # Start the enrollment dance NOW — the password is in this
            # request and the user should not re-type it.
            dance = _start_dance(realm, body.email, body.password, body.totp)
            handle = _dance_save("totp_enroll", realm, tenant_id,
                                 body.email.strip(), dance)
            return {"status": "enroll_totp", "handle": handle,
                    "expires_in": DANCE_TTL_SECONDS}
        _burn([acct_k, ip_k])
        _audit_login_failed(tenant_id, "account_not_ready")
        raise ApiError(
            status_code=403, code="forbidden", reason="account_not_ready",
            message="Your account needs attention from your administrator "
                    "before you can sign in.",
        )

    # Unknown grant error (disabled user, etc.) — generic, burns counters.
    _burn([acct_k, ip_k])
    _audit_login_failed(tenant_id, "grant_error")
    raise ApiError(
        status_code=401, code="unauthorized", reason="invalid_credentials",
        message="Invalid email or password.",
    )


@router.post("/totp/setup")
def totp_setup(body: TotpSetupBody, request: Request):
    """D-AUTH-14 (banner flow): prove the password via direct grant, then
    start the enrollment dance and return the handle (the QR is fetched
    separately via /totp/qr to keep it out of cookies/URLs)."""
    tenant, realm, ip_k = _precheck(request, body.org)
    tenant_id = str(tenant["id"])
    acct_k = _panel_acct_key(tenant_id, body.email)

    resp = _direct_grant(realm, body.email, body.password, body.totp)
    outcome, _ = _grant_outcome(resp)
    if outcome == "invalid_credentials":
        # An OTP-enrolled account cannot pass the grant without the code —
        # that is not a setup precondition problem: answer the honest 409
        # (the has-OTP hint is the accepted D-AUTH-16 tradeoff, same as the
        # login hint) instead of a misleading "invalid credentials".
        try:
            state = _user_state(_kc(), realm, body.email)
            if state.get("has_otp"):
                raise ApiError(
                    status_code=409, code="conflict", reason="already_enrolled",
                    message="This account already has multi-factor "
                            "authentication configured.",
                )
        except ApiError:
            raise
        except Exception:  # noqa: BLE001 - Admin REST down → generic path
            pass
        _burn([acct_k, ip_k])
        _audit_login_failed(tenant_id, "invalid_credentials")
        raise ApiError(
            status_code=401, code="unauthorized", reason="invalid_credentials",
            message="Invalid email or password.",
        )
    if outcome == "error":
        _burn([acct_k, ip_k])
        raise ApiError(
            status_code=401, code="unauthorized", reason="invalid_credentials",
            message="Invalid email or password.",
        )
    if outcome == "ok":
        try:
            state = _user_state(_kc(), realm, body.email)
        except Exception:  # noqa: BLE001
            raise service_unavailable(
                "The identity service is unreachable."
            )
        if state.get("has_otp"):
            raise ApiError(
                status_code=409, code="conflict", reason="already_enrolled",
                message="This account already has multi-factor "
                        "authentication configured.",
            )
        if not state.get("exists"):
            raise ApiError(
                status_code=401, code="unauthorized",
                reason="invalid_credentials",
                message="Invalid email or password.",
            )
    # outcome ok (without OTP) or account_not_setup: start the dance.
    try:
        dance = _start_dance(realm, body.email, body.password, body.totp)
    except ApiError as exc:
        if exc.reason == "already_enrolled":
            raise
        _burn([acct_k, ip_k])
        raise
    handle = _dance_save("totp_enroll", realm, tenant_id,
                         body.email.strip(), dance)
    return {"status": "ok", "handle": handle,
            "expires_in": DANCE_TTL_SECONDS}


@router.get("/totp/qr")
def totp_qr(handle: str):
    """The QR PNG (data-URI) of an in-flight enrollment dance. The handle
    is the capability (hashed at rest, TTL 10 min) — the BFF keeps it in an
    httpOnly cookie, never in a URL."""
    row = _dance_load(handle)
    if row is None or row["kind"] != "totp_enroll":
        raise ApiError(
            status_code=410, code="gone", reason="dance_expired",
            message="The setup session expired. Start again.",
        )
    return {"qr_png": row["state"].get("qr_png")}


@router.post("/totp/confirm")
def totp_confirm(body: TotpConfirmBody, request: Request,
                 response: Response):
    """D-AUTH-14: submit the code, complete the required action, exchange
    the authorization code (acr:"2") and mint the MFA-verified session."""
    row = _dance_load(body.handle)
    if row is None or row["kind"] != "totp_enroll":
        raise ApiError(
            status_code=410, code="gone", reason="dance_expired",
            message="The setup session expired. Start again.",
        )
    state = dict(row["state"])
    state["realm"] = row["realm"]
    code = body.code.strip()
    cookies = state["cookies"]

    for _ in range(_MAX_DANCE_HOPS):
        fields = dict(state["form_fields"])
        fields["totp"] = code
        fields.setdefault("userLabel", "")
        fields.setdefault("logout-sessions", "on")
        resp = _dance_request(cookies, "POST", state["form_action"],
                              data=fields)
        state["cookies"] = cookies
        if resp.status_code == 302:
            location = resp.headers.get("location", "")
            parsed = urlparse(location)
            codes = parse_qs(parsed.query).get("code")
            if codes:
                token_set = _exchange_dance_code(state, codes[0])
                _dance_delete(body.handle)
                result = _mint_admin(
                    request, response,
                    id_token=token_set["id_token"], acct_key=None,
                )
                # auth.totp_enrolled — same tenant transaction as the mint
                # would be ideal; the mint already committed, so emit in a
                # dedicated committed tx (no credential material).
                from ..audit import service as audit_service

                engine = get_engine()
                with engine.begin() as conn:
                    set_tenant(conn, row["tenant_id"])
                    audit_service.emit(
                        conn,
                        tenant_id=str(row["tenant_id"]),
                        actor_role="system",
                        action="auth.totp_enrolled",
                        payload={"via": "panel"},
                    )
                result["status"] = "ok"
                return result
            # Intermediate redirect (e.g. back to the flow): follow it.
            resp = _dance_request(cookies, "GET", location)
            state["cookies"] = cookies
            if resp.status_code != 200:
                break
        if resp.status_code != 200:
            break
        action, fields = _parse_form(resp.text)
        kind = _page_kind(resp.text, fields)
        if kind in ("config_totp", "otp_form") and action is not None:
            # config_totp re-rendered = wrong code (retry allowed, the
            # auth session is alive); otp_form = post-enrollment second
            # challenge — the SAME code is submitted again on the next
            # hop (live-verified flow shape).
            state["form_action"] = action
            state["form_fields"] = fields
            state["stage"] = kind
            _dance_update(body.handle, state)
            if kind == "config_totp":
                raise ApiError(
                    status_code=401, code="unauthorized",
                    reason="invalid_totp_code",
                    message="That code did not match. Check your "
                            "authenticator app and try again.",
                )
            continue
        break

    _dance_delete(body.handle)
    raise service_unavailable(
        "Could not complete the multi-factor setup. Start again."
    )


@router.post("/password")
def panel_password(body: PanelPasswordBody, request: Request,
                   response: Response):
    """D-AUTH-15: set/change the password. Two proof paths:

    - proof_handle (first login, UPDATE_PASSWORD pending): the login
      endpoint issued this handle AFTER the direct grant validated the
      one-time password ("Account is not fully set up" only occurs
      post-credential-validation — live-verified). The pending action is
      re-verified against the IdP before the reset; org/email come from
      the handle's row, never from the request.
    - current_password (voluntary change): a direct grant must SUCCEED
      with it before Admin REST resets anything (generic 401 otherwise).
    """
    if len(body.new_password) < _MIN_PASSWORD_LENGTH:
        raise validation_failed(
            f"password must be at least {_MIN_PASSWORD_LENGTH} characters."
        )

    from ..tenants.keycloak_admin import KeycloakAdminError

    first_login = False
    if body.proof_handle:
        row = _dance_load(body.proof_handle)
        if row is None or row["kind"] != "update_password":
            raise ApiError(
                status_code=410, code="gone", reason="dance_expired",
                message="Your session expired. Sign in again.",
            )
        # Trust the SERVER-side row, not the client payload.
        realm = row["realm"]
        email = row["email"]
        tenant_id = str(row["tenant_id"])
        first_login = True
        try:
            state = _user_state(_kc(), realm, email)
        except Exception:  # noqa: BLE001
            raise service_unavailable(
                "The identity service is unreachable."
            )
        if "UPDATE_PASSWORD" not in (state.get("required_actions") or []):
            # The action was already completed (or the handle is stale):
            # fail closed — no free password reset.
            _dance_delete(body.proof_handle)
            raise ApiError(
                status_code=410, code="gone", reason="dance_expired",
                message="Your session expired. Sign in again.",
            )
    else:
        if not body.current_password:
            raise validation_failed("current_password is required.")
        tenant, realm, ip_k = _precheck(request, body.org)
        tenant_id = str(tenant["id"])
        email = body.email.strip()
        acct_k = _panel_acct_key(tenant_id, email)
        resp = _direct_grant(realm, email, body.current_password, body.totp)
        outcome, _ = _grant_outcome(resp)
        if outcome == "invalid_credentials":
            # Consistency with the login endpoint (D-AUTH-16): an
            # OTP-enrolled account cannot pass the grant without the code —
            # answer the mfa hint instead of a misleading "invalid
            # credentials" when the password was actually correct.
            try:
                state = _user_state(_kc(), realm, email)
                if state.get("has_otp"):
                    _burn([acct_k, ip_k])
                    raise mfa_code_required()
            except ApiError:
                raise
            except Exception:  # noqa: BLE001 - Admin REST down → generic path
                pass
            _burn([acct_k, ip_k])
            _audit_login_failed(tenant_id, "invalid_credentials")
            raise ApiError(
                status_code=401, code="unauthorized",
                reason="invalid_credentials",
                message="Invalid email or password.",
            )
        if outcome == "error":
            _burn([acct_k, ip_k])
            raise ApiError(
                status_code=401, code="unauthorized",
                reason="invalid_credentials",
                message="Invalid email or password.",
            )

    try:
        kc = _kc()
        user = kc.find_user_by_email(realm, email)
        if user is None:
            raise ApiError(
                status_code=401, code="unauthorized",
                reason="invalid_credentials",
                message="Invalid email or password.",
            )
        kc.set_user_password(
            realm, user["id"], body.new_password,
            clear_action="UPDATE_PASSWORD" if first_login else None,
        )
    except KeycloakAdminError:
        raise service_unavailable(
            "The identity service is unreachable. Try again in a moment."
        )

    if first_login:
        _dance_delete(body.proof_handle)

    # Audit WITHOUT credential material (D-AUTH-16), tenant chain.
    from ..audit import service as audit_service

    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        audit_service.emit(
            conn,
            tenant_id=tenant_id,
            actor_role="system",
            actor_id=None,
            action="auth.password_changed",
            payload={"via": "panel", "first_login": first_login},
        )

    if not first_login:
        return {"status": "ok"}

    # First login: land the user straight in the console. The re-grant
    # uses the NEW password (in-memory only). If the account also has an
    # OTP credential the grant needs the code — route back to login then.
    resp = _direct_grant(realm, email, body.new_password, body.totp)
    outcome, tokens = _grant_outcome(resp)
    if outcome == "ok" and tokens:
        return _mint_admin(request, response,
                           id_token=tokens["id_token"], acct_key=None)
    return {"status": "ok", "next": "login"}

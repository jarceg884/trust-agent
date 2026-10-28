"""Sessions in PostgreSQL + request authentication (ADR-007, REQ-SEC-003).

Zero in-process state (lesson C2): sessions live in the `sessions` table.
Tokens are opaque and random (never derivable from the username):

    session token:  sts1.<tenant_id>.<session_id>.<secret>
    refresh token:  rt1.<tenant_id>.<session_id>.<secret>

Only sha256 hashes are stored (sessions.jti / refresh_token_hash /
csrf_token_hash). The tenant_id embedded in the token is a SCOPE HINT used
to open the RLS context before looking the session up — a forged hint simply
finds no row (D-CP-03/04).
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings
from ..db import get_engine, set_tenant
from ..errors import (
    csrf_required,
    invalid_token,
    password_change_required,
    service_unavailable,
    tenant_pending_activation,
    tenant_suspended,
)
from .jwks import verify_id_token
from .mfa import mfa_evidence

SESSION_PREFIX = "sts1."
REFRESH_PREFIX = "rt1."
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class RoleAssignment:
    role_key: str
    department_id: str | None = None


def load_roles(conn: Connection, user_id: str) -> list[RoleAssignment]:
    rows = conn.execute(
        text("SELECT role_key, department_id FROM user_roles WHERE user_id = :uid"),
        {"uid": user_id},
    ).mappings()
    return [RoleAssignment(r["role_key"], r["department_id"]) for r in rows]


def load_permissions(conn: Connection, role_keys: list[str]) -> set[str]:
    """Permissions from the catalog matrix (data-driven, I10 — never constants)."""
    if not role_keys:
        return set()
    rows = conn.execute(
        text(
            "SELECT DISTINCT permission_key FROM catalog.role_permissions "
            "WHERE role_key = ANY(:roles)"
        ),
        {"roles": list(role_keys)},
    ).mappings()
    return {r["permission_key"] for r in rows}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Principal:
    tenant_id: str
    tenant_slug: str
    # Human-facing organization name (tenants.name). Additive field exposed
    # by whoami (t_b14cbefb): the console topbar shows the org NAME and
    # degrades the slug to tooltip/secondary. Same source of truth as the
    # slug (tenants table), resolved at principal-build time.
    tenant_name: str
    user_id: str
    email: str
    display_name: str
    department_id: str | None
    roles: list[RoleAssignment] = field(default_factory=list)
    permissions: set[str] = field(default_factory=set)
    session_id: str | None = None
    session_kind: str | None = None
    mfa_verified: bool = False
    device_id: str | None = None
    via: str = "session"  # "session" | "jwt"
    auth_via_cookie: bool = False

    @property
    def role_keys(self) -> list[str]:
        return [ra.role_key for ra in self.roles]


@dataclass
class SessionTokens:
    session_id: str
    session_token: str
    refresh_token: str
    csrf_token: str | None
    expires_at: datetime


# ---------------------------------------------------------------------------
# Session lifecycle (all DB operations take a tenant-scoped connection)
# ---------------------------------------------------------------------------

def mint_session(
    conn: Connection,
    *,
    tenant_id: str,
    user_id: str,
    kind: str,
    mfa_verified: bool,
    device_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SessionTokens:
    settings = get_settings()
    session_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(32)
    refresh_secret = secrets.token_urlsafe(32)
    csrf_secret = secrets.token_urlsafe(24) if kind == "admin" else None
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.session_ttl_minutes
    )

    conn.execute(
        text(
            "INSERT INTO sessions (id, tenant_id, user_id, device_id, kind, jti, "
            " refresh_token_hash, csrf_token_hash, status, mfa_verified, ip, "
            " user_agent, expires_at) "
            "VALUES (:id, :tenant_id, :user_id, :device_id, :kind, :jti, "
            " :refresh_hash, :csrf_hash, 'active', :mfa, CAST(:ip AS inet), "
            " :user_agent, :expires_at)"
        ),
        {
            "id": session_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "device_id": device_id,
            "kind": kind,
            "jti": _hash(secret),
            "refresh_hash": _hash(refresh_secret),
            "csrf_hash": _hash(csrf_secret) if csrf_secret else None,
            "mfa": mfa_verified,
            "ip": ip,
            "user_agent": user_agent,
            "expires_at": expires_at,
        },
    )
    session_token = f"{SESSION_PREFIX}{tenant_id}.{session_id}.{secret}"
    refresh_token = f"{REFRESH_PREFIX}{tenant_id}.{session_id}.{refresh_secret}"
    return SessionTokens(
        session_id=session_id,
        session_token=session_token,
        refresh_token=refresh_token,
        csrf_token=csrf_secret,
        expires_at=expires_at,
    )


def _parse_scoped_token(token: str, prefix: str) -> tuple[str, str, str]:
    """-> (tenant_hint, session_id, secret) or raise invalid_token."""
    if not token.startswith(prefix):
        raise invalid_token()
    body = token[len(prefix):]
    parts = body.split(".")
    if len(parts) != 3:
        raise invalid_token()
    tenant_hint, session_id, secret = parts
    try:
        uuid.UUID(tenant_hint)
        uuid.UUID(session_id)
    except ValueError:
        raise invalid_token()
    return tenant_hint, session_id, secret


def lookup_session(conn: Connection, session_id: str, secret_hash: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT s.id, s.tenant_id, s.user_id, s.device_id, s.kind, s.jti, "
            "       s.refresh_token_hash, s.csrf_token_hash, s.status, "
            "       s.mfa_verified, s.expires_at, "
            "       u.email, u.display_name, u.department_id, u.status AS user_status, "
            "       t.status AS tenant_status, t.slug AS tenant_slug, "
            "       t.name AS tenant_name, t.oidc_issuer_url "
            "FROM sessions s "
            "JOIN users u ON u.tenant_id = s.tenant_id AND u.id = s.user_id "
            "JOIN tenants t ON t.id = s.tenant_id "
            "WHERE s.id = CAST(:sid AS uuid) AND s.jti = :hash "
            "  AND s.status = 'active' AND s.expires_at > now()"
        ),
        {"sid": session_id, "hash": secret_hash},
    ).mappings().first()
    return dict(row) if row else None


def sync_mfa_enrolled(conn: Connection, user_id: str) -> bool:
    """D-AUTH-11 (spec 015 v1.1.0): lazy sync of ``users.mfa_enrolled``.

    Called only from auth funnels that have ALREADY verified an id_token
    whose claims prove a second factor (``mfa_evidence`` — D-MFA-01) and run
    inside a tenant-scoped transaction (RLS isolates the UPDATE, constitution
    III). The guard makes it a no-op once the column is true, so per-request
    callers (stateless JWT path) do not write on every call. Monotonic by
    design: a password-only login never clears the column — not proving a
    second factor does not prove the user un-enrolled. Returns True when the
    column actually flipped (false -> true).
    """
    result = conn.execute(
        text(
            "UPDATE users SET mfa_enrolled = true, updated_at = now() "
            "WHERE id = CAST(:uid AS uuid) AND NOT mfa_enrolled"
        ),
        {"uid": user_id},
    )
    return (result.rowcount or 0) > 0


def revoke_session(conn: Connection, session_id: str) -> None:
    conn.execute(
        text(
            "UPDATE sessions SET status = 'revoked', updated_at = now() "
            "WHERE id = CAST(:sid AS uuid)"
        ),
        {"sid": session_id},
    )


def rotate_refresh(conn: Connection, refresh_token: str) -> tuple[str, str]:
    """Rotate a refresh token (one-time). Reuse of an already-rotated refresh
    revokes the session (reuse detection) and fails.

    Returns (session_token, new_refresh_token).
    """
    tenant_hint, session_id, secret = _parse_scoped_token(refresh_token, REFRESH_PREFIX)
    row = conn.execute(
        text(
            "SELECT id, tenant_id, refresh_token_hash, status FROM sessions "
            "WHERE id = CAST(:sid AS uuid)"
        ),
        {"sid": session_id},
    ).mappings().first()
    if row is None:
        raise invalid_token()
    if row["refresh_token_hash"] != _hash(secret):
        # Replay of a consumed refresh token: kill the session. The revoke
        # MUST survive the rollback triggered by the 401 we raise next, so
        # commit it explicitly before raising. The replay is audited as
        # auth.login_failed in the same transaction (D-AU-04, REQ-AU-022).
        from ..audit import service as audit_service

        revoke_session(conn, session_id)
        audit_service.emit(
            conn,
            tenant_id=str(row["tenant_id"]),
            actor_role="system",
            action="auth.login_failed",
            target_type="session",
            target_id=session_id,
            payload={"reason": "refresh_reuse"},
        )
        conn.commit()
        raise invalid_token()
    if row["status"] != "active":
        raise invalid_token()

    new_secret = secrets.token_urlsafe(32)
    conn.execute(
        text(
            "UPDATE sessions SET refresh_token_hash = :h, updated_at = now() "
            "WHERE id = CAST(:sid AS uuid)"
        ),
        {"h": _hash(new_secret), "sid": session_id},
    )
    session_token = f"{SESSION_PREFIX}{tenant_hint}.{session_id}.{_session_secret(conn, session_id)}"
    return session_token, f"{REFRESH_PREFIX}{tenant_hint}.{session_id}.{new_secret}"


def _session_secret(conn: Connection, session_id: str) -> str:
    """The raw session secret is never stored — re-mint the session token by
    rotating its secret too (refresh implies a fresh bearer)."""
    new_secret = secrets.token_urlsafe(32)
    conn.execute(
        text(
            "UPDATE sessions SET jti = :h, updated_at = now() "
            "WHERE id = CAST(:sid AS uuid)"
        ),
        {"h": _hash(new_secret), "sid": session_id},
    )
    return new_secret


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------

def _extract_token(request: Request) -> tuple[str, bool] | None:
    """-> (token, via_cookie) or None when unauthenticated."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip(), False
    cookie = request.cookies.get(get_settings().cookie_name)
    if cookie:
        return cookie, True
    return None


def _build_principal(
    *,
    row: dict,
    roles: list[RoleAssignment],
    permissions: set[str],
    session_id: str | None,
    session_kind: str | None,
    device_id: str | None,
    mfa_verified: bool,
    via: str,
    auth_via_cookie: bool,
) -> Principal:
    return Principal(
        tenant_id=str(row["tenant_id"]),
        tenant_slug=row["tenant_slug"],
        tenant_name=row["tenant_name"],
        user_id=str(row["user_id"]),
        email=row["email"],
        display_name=row["display_name"],
        department_id=str(row["department_id"]) if row["department_id"] else None,
        roles=roles,
        permissions=permissions,
        session_id=session_id,
        session_kind=session_kind,
        mfa_verified=mfa_verified,
        device_id=str(device_id) if device_id else None,
        via=via,
        auth_via_cookie=auth_via_cookie,
    )


def authenticate(
    request: Request, *, allow_pending: bool = False
) -> Principal | None:
    """Resolve the request principal, or None for anonymous requests.

    Raises 401 invalid_token / 403 tenant_suspended / 423
    tenant_pending_activation on failure. ``allow_pending`` (spec 026,
    D-26-03) lets a pending_activation tenant through — reserved for
    session-lifecycle endpoints (logout); the cached principal then carries
    no gate, so it must never be used on business surface.
    Result is cached on request.state (FastAPI dependency cache).
    """
    cached = getattr(request.state, "principal", "__unset__")
    if cached != "__unset__":
        return cached  # type: ignore[return-value]

    extracted = _extract_token(request)
    if extracted is None:
        request.state.principal = None
        return None
    token, via_cookie = extracted
    settings = get_settings()
    engine = get_engine()

    if token.startswith(SESSION_PREFIX):
        tenant_hint, session_id, secret = _parse_scoped_token(token, SESSION_PREFIX)
        with engine.begin() as conn:
            set_tenant(conn, tenant_hint)
            row = lookup_session(conn, session_id, _hash(secret))
            if row is None:
                raise invalid_token()
            roles = load_roles(conn, str(row["user_id"]))
            permissions = load_permissions(conn, [r.role_key for r in roles])
        principal = _build_principal(
            row=row,
            roles=roles,
            permissions=permissions,
            session_id=str(row["id"]),
            session_kind=row["kind"],
            device_id=row["device_id"],
            mfa_verified=row["mfa_verified"],
            via="session",
            auth_via_cookie=via_cookie,
        )
        _check_csrf(request, row, via_cookie)
    else:
        # Raw JWT from the realm of the tenant (desktop/plugin path).
        claims = verify_id_token(
            token,
            idp_base_urls=settings.idp_base_urls,
            audience=settings.oidc_audience,
            leeway=settings.jwt_leeway_seconds,
        )
        tenant_id = claims["tenant_id"]
        # MFA evidence: `amr` (test double / standard-IdP) or `acr` >=
        # TRUST_MFA_MIN_ACR (Keycloak 26, which never emits `amr` — D-MFA-01).
        mfa_verified = mfa_evidence(claims)
        with engine.begin() as conn:
            set_tenant(conn, tenant_id)
            trow = conn.execute(
                text("SELECT id, status, slug, name, oidc_issuer_url FROM tenants")
            ).mappings().first()
            if trow is None:
                raise invalid_token()
            if trow["oidc_issuer_url"] and trow["oidc_issuer_url"] != claims["iss"]:
                raise invalid_token()
            urow = conn.execute(
                text(
                    "SELECT id, email, display_name, department_id, status "
                    "FROM users WHERE oidc_subject = :sub"
                ),
                {"sub": claims["sub"]},
            ).mappings().first()
            if urow is None or urow["status"] != "active":
                raise invalid_token()
            if mfa_verified:
                # D-AUTH-11 (spec 015 v1.1.0): the desktop/plugin path uses
                # the raw id_token as Bearer and never mints a DB session —
                # sync the directory column here. Guarded UPDATE: no write
                # once the column is true.
                sync_mfa_enrolled(conn, str(urow["id"]))
            roles = load_roles(conn, str(urow["id"]))
            permissions = load_permissions(conn, [r.role_key for r in roles])
        row = {
            "tenant_id": trow["id"],
            "tenant_slug": trow["slug"],
            "tenant_name": trow["name"],
            "tenant_status": trow["status"],
            "user_id": urow["id"],
            "email": urow["email"],
            "display_name": urow["display_name"],
            "department_id": urow["department_id"],
        }
        principal = _build_principal(
            row=row,
            roles=roles,
            permissions=permissions,
            session_id=None,
            session_kind="api",
            device_id=None,
            mfa_verified=mfa_verified,
            via="jwt",
            auth_via_cookie=False,
        )

    if principal and row.get("tenant_status") == "pending_activation":
        # Spec 026 (REQ-SS-002, D-26-02): the tenant is LOCKED. The user
        # authenticated (they may SEE their status) but nothing functional
        # answers — 423 on every tenant endpoint.
        if not allow_pending:
            raise tenant_pending_activation()
    elif principal and row.get("tenant_status") != "active":
        raise tenant_suspended()
    request.state.principal = principal
    return principal


def _check_csrf(request: Request, session_row: dict, via_cookie: bool) -> None:
    """Cookie-authenticated mutations must carry the CSRF token (REQ-SEC-010)."""
    if not via_cookie or request.method not in MUTATING_METHODS:
        return
    if session_row["kind"] != "admin":
        return
    provided = request.headers.get("x-csrf-token", "")
    if not provided or not session_row["csrf_token_hash"] or \
            _hash(provided) != session_row["csrf_token_hash"]:
        raise csrf_required()


def get_principal(request: Request) -> Principal:
    """Dependency: the authenticated principal or 401."""
    principal = authenticate(request)
    if principal is None:
        raise invalid_token("Authentication required.")
    return principal


def get_principal_allow_pending(request: Request) -> Principal:
    """Spec 026 (D-26-03): like get_principal, but a pending_activation
    tenant passes. Reserved for session-lifecycle endpoints the locked user
    still needs (logout) — never for business surface."""
    principal = authenticate(request, allow_pending=True)
    if principal is None:
        raise invalid_token("Authentication required.")
    return principal


def get_provider_principal(request: Request) -> Principal:
    """Provider operator principal (spec 025 D-PS-06 — supercede el token
    estático del spec 004 D-ONB-02).

    Dos caminos, AMBOS fail-closed si no hay operador seedeado en
    ``ops.provider_operators`` (REQ-PS-010 — la recuperación es reiniciar el
    API, que re-seedea):

    1. PRIMARIO — sesión de operador ``pst1…`` (``ops.provider_sessions``,
       mintada por ``POST /api/provider/auth/login``). Sujeto a la required
       action del primer login: 403 ``password_change_required`` hasta
       completar el cambio (D-PS-08).
    2. BREAK-GLASS (deprecado, documentado en platform/README.md) — el
       bearer estático de entorno, SOLO con ``TRUST_PROVIDER_TOKEN``
       configurado explícitamente. Vía de escape para CLI/scripts de
       emergencia; NO está sujeto al flag del primer login.

    platform_admin NO es un rol de tenant (el CHECK de user_roles lo excluye
    y sessions.tenant_id es NOT NULL), así que ninguna sesión de usuario
    puede cargarlo. Los permisos se leen data-driven de
    catalog.role_permissions — jamás constantes de código.
    """
    # Import local: provider.operator importa ..auth.throttle (mismo paquete).
    from ..provider import operator as provider_op

    engine = get_engine()
    try:
        operator_seeded = provider_op.operator_exists(engine)
    except Exception:
        operator_seeded = False
    if not operator_seeded:
        raise service_unavailable(
            "Provider operations are disabled: no provider operator is "
            "seeded. Restart the API to bootstrap one (fail-closed)."
        )

    auth = request.headers.get("authorization", "")
    provided = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    if not provided:
        raise invalid_token("Provider authentication required.")

    if provided.startswith(provider_op.SESSION_PREFIX):
        with engine.begin() as conn:
            session = provider_op.lookup_operator_session(conn, provided)
        if session is None:
            raise invalid_token("Invalid or expired provider session.")
        if session["must_change_password"]:
            # Required action del primer login (D-PS-08): el control plane
            # niega TODA ruta provider de negocio hasta completar el cambio.
            raise password_change_required()
        display_name = f"Provider Operator ({session['username']})"
    else:
        settings = get_settings()
        if not settings.provider_token:
            raise service_unavailable(
                "Provider operations are disabled: TRUST_PROVIDER_TOKEN is "
                "not configured (fail-closed)."
            )
        if not secrets.compare_digest(provided, settings.provider_token):
            raise invalid_token("Invalid provider token.")
        display_name = "Provider Operator (break-glass)"

    with engine.connect() as conn:
        permissions = load_permissions(conn, ["platform_admin"])
    principal = Principal(
        tenant_id="",
        tenant_slug="platform",
        tenant_name="Platform",
        user_id="",
        email="",
        display_name=display_name,
        department_id=None,
        roles=[RoleAssignment("platform_admin")],
        permissions=permissions,
        via="provider",
    )
    request.state.principal = principal
    return principal


def require_mfa(principal: Principal = Depends(get_principal)) -> Principal:
    """MfaGate (REQ-ORG-007): 403 mfa_required without verified MFA.

    Spec 015 (D-AUTH-07): the gate is POLICY-AWARE — it only fires when the
    organization's effective `require_mfa` policy is true. With
    require_mfa=false the session without MFA passes (the app always
    suggests enrolling via the persistent banner instead of blocking).

    Exempt routes simply do not declare this dependency (/api/auth/*, /healthz).
    """
    if principal.mfa_verified:
        return principal
    from ..db import get_engine, set_tenant
    from ..errors import mfa_required
    from ..governance.service import org_policy_value

    engine = get_engine()
    with engine.connect() as conn:
        set_tenant(conn, principal.tenant_id)
        required = org_policy_value(conn, "require_mfa")
    if required is None or required is True or required == "true":
        raise mfa_required()
    return principal


def get_tx(request: Request) -> Iterator[Connection]:
    """Business transaction scoped to the principal's tenant (D-CP-02).

    Depends on authentication: no tenant scope without a verified principal.
    """
    principal = get_principal(request)
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, principal.tenant_id)
        yield conn

"""Auth + identity endpoints (/api/auth/*, /api/me*)."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit_service
from ..config import get_settings
from ..db import get_engine, set_tenant
from ..errors import ApiError, invalid_token
from . import service
from . import throttle
from .jwks import verify_id_token
from .mfa import mfa_evidence
from .rbac import top_role_key
from .service import Principal, get_principal, get_tx, require_mfa

router = APIRouter(prefix="/api/auth")


class MintSessionBody(BaseModel):
    id_token: str
    kind: str = "desktop"  # "desktop" | "admin"


class RefreshBody(BaseModel):
    refresh_token: str


def _client_ip(request: Request) -> str | None:
    """Only store parseable IPs (the sessions.ip column is inet)."""
    import ipaddress

    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


@router.post("/session", status_code=201)
def mint_session(body: MintSessionBody, request: Request, response: Response):
    """Verify an OIDC id_token (JWKS) and mint a PostgreSQL session.

    The client (desktop / Admin Console) completes authorization-code + PKCE
    S256 against the tenant realm on its own; this endpoint only ever sees the
    resulting id_token. No passwords, no ROPC (REQ-SEC-001).

    Login rate limiting (REQ-SEC-006): counters live in ops.login_throttle
    (cross-tenant, pre-auth — D-DB-14). The IP lock is checked BEFORE any
    verification (flood control); the account counter uses only claims that
    verified cryptographically, so a forged token cannot steer someone else's
    counter. Failures inside the tenant-scoped transaction are recorded in a
    separate transaction after the rollback (same pattern as refresh-reuse
    detection in service.rotate_refresh).
    """
    if body.kind not in ("desktop", "admin"):
        raise invalid_token("kind must be 'desktop' or 'admin'.")
    settings = get_settings()
    engine = get_engine()
    ip_k = throttle.ip_key(_client_ip(request))

    # 1) IP lock: pre-verification, blocks even valid logins from a flooding IP.
    with engine.begin() as conn:
        throttle.check(conn, [ip_k])

    # 2) Verify the token; a failure burns the IP counter (no trusted account).
    try:
        claims = verify_id_token(
            body.id_token,
            idp_base_urls=settings.idp_base_urls,
            audience=settings.oidc_audience,
            leeway=settings.jwt_leeway_seconds,
        )
    except ApiError:
        with engine.begin() as conn:
            throttle.register_failure(conn, [ip_k])
        raise

    tenant_id = claims["tenant_id"]
    acct_k = throttle.account_key(tenant_id, claims["sub"])

    # 3) Account lock (claims are verified — the counter key is trustworthy).
    with engine.begin() as conn:
        throttle.check(conn, [acct_k])

    # 4) Tenant-scoped business transaction; failures count for account + IP.
    try:
        with engine.begin() as conn:
            set_tenant(conn, tenant_id)
            trow = conn.execute(
                text("SELECT id, status, oidc_issuer_url FROM tenants")
            ).mappings().first()
            if trow is None:
                raise invalid_token()
            if trow["oidc_issuer_url"] and trow["oidc_issuer_url"] != claims["iss"]:
                raise invalid_token()
            urow = conn.execute(
                text("SELECT id, status FROM users WHERE oidc_subject = :sub"),
                {"sub": claims["sub"]},
            ).mappings().first()
            if urow is None or urow["status"] != "active":
                # auth.login_failed — the tenant is already verified (valid
                # signature/claims + existing tenant + matching iss), so the
                # event belongs to THIS tenant's chain (D-AU-03). Pre-tenant
                # failures (bad signature/aud/iss/tenant) are never audited: a
                # forged token must not pollute any chain. The event must
                # survive the 401 rollback -> explicit commit before raising.
                audit_service.emit(
                    conn,
                    tenant_id=tenant_id,
                    actor_role="system",
                    actor_id=str(urow["id"]) if urow is not None else None,
                    action="auth.login_failed",
                    payload={
                        "reason": "user_disabled" if urow is not None else "unknown_user"
                    },
                )
                conn.commit()
                raise invalid_token()
            tokens = service.mint_session(
                conn,
                tenant_id=tenant_id,
                user_id=str(urow["id"]),
                kind=body.kind,
                mfa_verified=mfa_evidence(claims),
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            # auth.login — same business transaction as the session INSERT
            # (D-AU-07: the audit event is part of the flow, not best-effort).
            role_keys = [
                r.role_key for r in service.load_roles(conn, str(urow["id"]))
            ]
            audit_service.emit(
                conn,
                tenant_id=tenant_id,
                actor_role=top_role_key(role_keys),
                actor_id=str(urow["id"]),
                action="auth.login",
                target_type="session",
                target_id=tokens.session_id,
                payload={
                    "kind": body.kind,
                    "mfa_verified": mfa_evidence(claims),
                    "via": "oidc",
                },
            )
            # Atomic with the session insert: a successful login clears the
            # account counter (the IP counter is NOT reset — see throttle.py).
            throttle.register_success(conn, acct_k)
    except ApiError:
        with engine.begin() as conn:
            throttle.register_failure(conn, [acct_k, ip_k])
        raise

    if body.kind == "admin":
        response.set_cookie(
            key=settings.cookie_name,
            value=tokens.session_token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=settings.session_ttl_minutes * 60,
            path="/",
        )
    return {
        "session_token": tokens.session_token if body.kind != "admin" else None,
        "refresh_token": tokens.refresh_token,
        "csrf_token": tokens.csrf_token,
        "expires_at": tokens.expires_at.isoformat(),
        "mfa_verified": mfa_evidence(claims),
    }


@router.post("/refresh")
def refresh(body: RefreshBody):
    engine = get_engine()
    tenant_hint, _, _ = service._parse_scoped_token(
        body.refresh_token, service.REFRESH_PREFIX
    )
    with engine.begin() as conn:
        set_tenant(conn, tenant_hint)
        session_token, refresh_token = service.rotate_refresh(
            conn, body.refresh_token
        )
    return {"session_token": session_token, "refresh_token": refresh_token}


@router.post("/logout", status_code=204)
def logout(principal: Principal = Depends(get_principal)):
    if principal.session_id is None:
        # Stateless JWT: nothing to revoke server-side.
        return None
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, principal.tenant_id)
        service.revoke_session(conn, principal.session_id)
        audit_service.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=top_role_key(principal.role_keys),
            actor_id=principal.user_id,
            action="auth.logout",
            target_type="session",
            target_id=principal.session_id,
        )
    return None


# --- /api/me -----------------------------------------------------------------

me_router = APIRouter(prefix="/api/me", dependencies=[Depends(require_mfa)])


@me_router.get("")
def whoami(principal: Principal = Depends(get_principal)):
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "display_name": principal.display_name,
        "department_id": principal.department_id,
        "tenant_id": principal.tenant_id,
        "tenant_slug": principal.tenant_slug,
        "roles": [
            {"role_key": r.role_key, "department_id": r.department_id}
            for r in principal.roles
        ],
        "permissions": sorted(principal.permissions),
        "mfa_verified": principal.mfa_verified,
        "via": principal.via,
    }


@me_router.get("/policies")
def my_policies(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    from ..governance.service import effective_policy_for_user

    return effective_policy_for_user(conn, principal)


@me_router.get("/budget")
def my_budget(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Own budget status per level + cutoff flag (REQ-GOV-012)."""
    from ..governance import budgets as budgets_service

    return budgets_service.status_for_user(
        conn, principal.tenant_id, principal.user_id,
        principal.department_id,
    )


@me_router.get("/notifications")
def my_notifications(
    unread_only: bool = False,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Own governance inbox (cutoffs, thresholds) — REQ-GOV-050."""
    from ..governance import notifications as inbox

    return {"notifications": inbox.list_for_user(
        conn, user_id=principal.user_id, unread_only=unread_only
    )}


@me_router.post("/notifications/{notification_id}/read", status_code=204)
def mark_notification_read(
    notification_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    from ..governance import notifications as inbox

    if not inbox.mark_read(
        conn, user_id=principal.user_id, notification_id=notification_id
    ):
        from ..errors import not_found

        raise not_found("Notification not found.")
    return None

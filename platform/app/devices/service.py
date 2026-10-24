"""Device enrollment — first-class flow (spec 007, ADR-013).

The desktop generates a local keypair and requests enrollment with a verified
id_token + the org code; an admin approves from the console; the control
plane binds device<->user<->org and issues the signed device token. NO
scraping anywhere (lesson C5): the device identity comes from the device
itself (public key + fingerprint).

Secrets handling (ADR-006): the enrollment token is opaque
    enr1.<tenant_id>.<device_id>.<secret>
and only sha256(secret) is stored (devices.enrollment_token_hash). It is
delivered EXACTLY ONCE (the 202 response) and redeemed exactly once — a
second redemption answers 410 gone, uniformly for unknown/consumed/invalid
(no oracle).
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..audit import service as audit_service
from ..auth.rbac import dept_scope
from ..auth.service import Principal
from ..config import get_settings
from ..errors import conflict, gone, not_found, validation_failed
from .tokens import issue_device_token

ENROLLMENT_PREFIX = "enr1."

_AUDITABLE_STATUSES = ("pending", "active", "revoked", "rejected")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def validate_public_key(public_key: str) -> None:
    """The device public key must parse as a PEM SubjectPublicKeyInfo."""
    from cryptography.hazmat.primitives import serialization

    try:
        serialization.load_pem_public_key(public_key.encode())
    except Exception:
        raise validation_failed(
            "public_key must be a PEM SubjectPublicKeyInfo key."
        )


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _enrolling_role(conn: Connection, user_id: str) -> str:
    """Audit actor_role = the enrolling user's own role (D-DEV-10)."""
    from ..auth.service import load_roles

    roles = load_roles(conn, user_id)
    return roles[0].role_key if roles else "user"


# ---------------------------------------------------------------------------
# Enrollment (tenant-scoped connection; tenant comes from the VERIFIED claim)
# ---------------------------------------------------------------------------
def enroll(
    conn: Connection,
    *,
    tenant_id: str,
    user_id: str,
    public_key: str,
    fingerprint: str,
    device_name: str | None,
    platform: str | None,
) -> dict:
    """Create/re-enroll a pending device and mint a one-time enrollment token.

    Upsert key: (tenant_id, fingerprint) — retries never create extra rows.
    """
    validate_public_key(public_key)
    if not fingerprint or len(fingerprint) > 256:
        raise validation_failed("fingerprint is required (max 256 chars).")

    existing = conn.execute(
        text("SELECT id, user_id, status FROM devices WHERE fingerprint = :fp"),
        {"fp": fingerprint},
    ).mappings().first()

    secret = secrets.token_urlsafe(32)
    token_hash = _hash(secret)

    if existing is None:
        device_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO devices (id, tenant_id, user_id, fingerprint, "
                " device_name, platform, status, public_key, "
                " enrollment_token_hash) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), "
                " CAST(:user_id AS uuid), :fp, :name, :platform, 'pending', "
                " :public_key, :enrollment_hash)"
            ),
            {
                "id": device_id,
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "fp": fingerprint,
                "name": device_name,
                "platform": platform,
                "public_key": public_key,
                "enrollment_hash": token_hash,
            },
        )
    else:
        if str(existing["user_id"]) != str(user_id):
            # Fingerprint already taken by another user of this tenant.
            raise conflict("This device fingerprint is already enrolled.")
        if existing["status"] == "active":
            raise conflict("This device is already enrolled and active.")
        # pending (retry) or revoked/rejected (re-enrollment, ADR-013):
        # reset to pending with the new key and a fresh enrollment token.
        device_id = str(existing["id"])
        conn.execute(
            text(
                "UPDATE devices SET status = 'pending', public_key = :public_key, "
                " device_name = :name, platform = :platform, "
                " enrollment_token_hash = :enrollment_hash, "
                " approved_at = NULL, approved_by = NULL, "
                " enrolled_at = now(), updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {
                "id": device_id,
                "public_key": public_key,
                "name": device_name,
                "platform": platform,
                "enrollment_hash": token_hash,
            },
        )

    audit_service.emit(
        conn,
        tenant_id=str(tenant_id),
        actor_role=_enrolling_role(conn, user_id),
        actor_id=str(user_id),
        action="device.enrolled",
        target_type="device",
        target_id=device_id,
        device_id=device_id,
        payload={"fingerprint": fingerprint, "user_id": str(user_id)},
    )
    return {
        "device_id": device_id,
        "status": "pending",
        "enrollment_token": f"{ENROLLMENT_PREFIX}{tenant_id}.{device_id}.{secret}",
    }


# ---------------------------------------------------------------------------
# One-time exchange (no principal: the enrollment token IS the credential)
# ---------------------------------------------------------------------------
def _parse_enrollment_token(token: str) -> tuple[str, str, str]:
    """-> (tenant_hint, device_id, secret) or raise gone (uniform, no oracle)."""
    if not token.startswith(ENROLLMENT_PREFIX):
        raise gone("The enrollment token is invalid or already used.")
    parts = token[len(ENROLLMENT_PREFIX):].split(".")
    if len(parts) != 3:
        raise gone("The enrollment token is invalid or already used.")
    tenant_hint, device_id, secret = parts
    try:
        uuid.UUID(tenant_hint)
        uuid.UUID(device_id)
    except ValueError:
        raise gone("The enrollment token is invalid or already used.")
    return tenant_hint, device_id, secret


def exchange(engine: Engine, enrollment_token: str) -> dict:
    """Redeem the enrollment token for the signed device token (one-time).

    pending -> {"status": "pending"} (router answers 202, nothing consumed)
    active  -> device token, hash consumed (second redemption -> 410)
    anything else (unknown/consumed/rejected/revoked) -> 410 gone.
    """
    tenant_hint, device_id, secret = _parse_enrollment_token(enrollment_token)
    with engine.begin() as conn:
        from ..db import set_tenant

        set_tenant(conn, tenant_hint)
        row = conn.execute(
            text(
                "SELECT id, user_id, status, enrollment_token_hash "
                "FROM devices WHERE id = CAST(:did AS uuid)"
            ),
            {"did": device_id},
        ).mappings().first()
        if (
            row is None
            or row["enrollment_token_hash"] is None
            or row["enrollment_token_hash"] != _hash(secret)
        ):
            raise gone("The enrollment token is invalid or already used.")
        if row["status"] == "pending":
            return {"device_id": device_id, "status": "pending"}
        if row["status"] != "active":
            # rejected/revoked: the enrollment token died with the device.
            raise gone("The enrollment token is invalid or already used.")

        # Active: consume the hash (one-time redemption) and issue the token.
        conn.execute(
            text(
                "UPDATE devices SET enrollment_token_hash = NULL, "
                "updated_at = now() WHERE id = CAST(:did AS uuid)"
            ),
            {"did": device_id},
        )
    settings = get_settings()
    device_token = issue_device_token(
        settings,
        device_id=device_id,
        user_id=str(row["user_id"]),
        tenant_id=tenant_hint,
    )
    return {
        "device_token": device_token,
        "device_id": device_id,
        "status": "active",
        "expires_in": settings.device_token_ttl_minutes * 60,
    }


# ---------------------------------------------------------------------------
# Renewal (session + MFA + ownership; nothing one-time is consumed)
# ---------------------------------------------------------------------------
def renew_token(conn: Connection, *, principal: Principal, device_id: str) -> dict:
    row = conn.execute(
        text("SELECT id, user_id, status FROM devices WHERE id = CAST(:did AS uuid)"),
        {"did": device_id},
    ).mappings().first()
    if row is None or str(row["user_id"]) != principal.user_id:
        raise not_found("Device not found.")
    if row["status"] != "active":
        raise conflict("The device is not active.")
    settings = get_settings()
    device_token = issue_device_token(
        settings,
        device_id=device_id,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    return {
        "device_token": device_token,
        "device_id": device_id,
        "status": "active",
        "expires_in": settings.device_token_ttl_minutes * 60,
    }


# ---------------------------------------------------------------------------
# User view (own devices only)
# ---------------------------------------------------------------------------
def my_devices(conn: Connection, principal: Principal) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT id, fingerprint, device_name, platform, status, "
            "enrolled_at, approved_at, last_seen_at "
            "FROM devices WHERE user_id = CAST(:uid AS uuid) "
            "ORDER BY created_at"
        ),
        {"uid": principal.user_id},
    ).mappings()
    return [
        {
            "id": str(r["id"]),
            "fingerprint": r["fingerprint"],
            "device_name": r["device_name"],
            "platform": r["platform"],
            "status": r["status"],
            "enrolled_at": _iso(r["enrolled_at"]),
            "approved_at": _iso(r["approved_at"]),
            "last_seen_at": _iso(r["last_seen_at"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Admin console (permission devices.manage; dept_admin scoped to their depts)
# ---------------------------------------------------------------------------
_DEVICE_SELECT = text(
    "SELECT d.id, d.fingerprint, d.device_name, d.platform, d.status, "
    "d.enrolled_at, d.approved_at, d.approved_by, d.last_seen_at, "
    "d.user_id, u.email, u.display_name, u.department_id, "
    "dept.name AS department_name, "
    "(SELECT count(*) FROM sessions s "
    " WHERE s.device_id = d.id AND s.status = 'active') AS active_sessions "
    "FROM devices d "
    "JOIN users u ON u.tenant_id = d.tenant_id AND u.id = d.user_id "
    "LEFT JOIN departments dept "
    " ON dept.tenant_id = d.tenant_id AND dept.id = u.department_id"
)


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "department_id": str(row["department_id"]) if row["department_id"] else None,
        "department_name": row["department_name"],
        "fingerprint": row["fingerprint"],
        "device_name": row["device_name"],
        "platform": row["platform"],
        "status": row["status"],
        "enrolled_at": _iso(row["enrolled_at"]),
        "approved_at": _iso(row["approved_at"]),
        "approved_by": str(row["approved_by"]) if row["approved_by"] else None,
        "last_seen_at": _iso(row["last_seen_at"]),
        "active_sessions": row["active_sessions"],
    }


def list_devices(
    conn: Connection,
    *,
    principal: Principal,
    status: str | None = None,
    department_id: str | None = None,
) -> list[dict]:
    if status is not None and status not in _AUDITABLE_STATUSES:
        raise validation_failed(f"status must be one of {list(_AUDITABLE_STATUSES)}.")
    clauses, params = [], {}
    scope = dept_scope(principal)
    if scope is not None:  # dept_admin (or plain user): only their departments
        clauses.append("u.department_id = ANY(:depts)")
        params["depts"] = scope
    if status is not None:
        clauses.append("d.status = :status")
        params["status"] = status
    if department_id is not None:
        clauses.append("u.department_id = CAST(:dept AS uuid)")
        params["dept"] = department_id
    sql = _DEVICE_SELECT
    if clauses:
        sql = text(str(_DEVICE_SELECT) + " WHERE " + " AND ".join(clauses))
    rows = conn.execute(sql, params).mappings()
    return [_serialize(r) for r in rows]


def _get_scoped_device(
    conn: Connection, principal: Principal, device_id: str
):
    """Fetch a device within the principal's admin scope or 404 (D-CP-09)."""
    row = conn.execute(
        text(str(_DEVICE_SELECT) + " WHERE d.id = CAST(:did AS uuid)"),
        {"did": device_id},
    ).mappings().first()
    if row is not None:
        scope = dept_scope(principal)
        if scope is not None:
            # normalize to str: psycopg returns UUID objects for uuid columns
            scope = [str(s) for s in scope]
            if (
                row["department_id"] is None
                or str(row["department_id"]) not in scope
            ):
                row = None  # out of the dept_admin's scope: same as non-existent
    if row is None:
        raise not_found("Device not found.")
    return row


def get_device(conn: Connection, *, principal: Principal, device_id: str) -> dict:
    return _serialize(_get_scoped_device(conn, principal, device_id))


def approve(conn: Connection, *, principal: Principal, device_id: str) -> dict:
    row = _get_scoped_device(conn, principal, device_id)
    if row["status"] != "pending":
        raise conflict("Only pending devices can be approved.")
    conn.execute(
        text(
            "UPDATE devices SET status = 'active', approved_at = now(), "
            "approved_by = CAST(:actor AS uuid), updated_at = now() "
            "WHERE id = CAST(:did AS uuid)"
        ),
        {"did": device_id, "actor": principal.user_id},
    )
    audit_service.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="device.approved",
        target_type="device",
        target_id=device_id,
        device_id=device_id,
        payload={
            "fingerprint": row["fingerprint"],
            "user_id": str(row["user_id"]),
        },
    )
    return {"device_id": device_id, "status": "active"}


def reject(conn: Connection, *, principal: Principal, device_id: str) -> dict:
    row = _get_scoped_device(conn, principal, device_id)
    if row["status"] != "pending":
        raise conflict("Only pending devices can be rejected.")
    conn.execute(
        text(
            "UPDATE devices SET status = 'rejected', "
            "enrollment_token_hash = NULL, updated_at = now() "
            "WHERE id = CAST(:did AS uuid)"
        ),
        {"did": device_id},
    )
    audit_service.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="device.revoked",
        target_type="device",
        target_id=device_id,
        device_id=device_id,
        payload={
            "outcome": "rejected",
            "fingerprint": row["fingerprint"],
            "user_id": str(row["user_id"]),
        },
    )
    return {"device_id": device_id, "status": "rejected"}


def revoke(conn: Connection, *, principal: Principal, device_id: str) -> dict:
    """Revoke a device AND its sessions in the SAME transaction (REQ-DEV-004:
    effective immediately — the next request is 401, no cache involved)."""
    row = _get_scoped_device(conn, principal, device_id)
    if row["status"] not in ("pending", "active"):
        raise conflict("The device is already revoked or rejected.")
    conn.execute(
        text(
            "UPDATE devices SET status = 'revoked', "
            "enrollment_token_hash = NULL, updated_at = now() "
            "WHERE id = CAST(:did AS uuid)"
        ),
        {"did": device_id},
    )
    conn.execute(
        text(
            "UPDATE sessions SET status = 'revoked', updated_at = now() "
            "WHERE device_id = CAST(:did AS uuid)"
        ),
        {"did": device_id},
    )
    audit_service.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="device.revoked",
        target_type="device",
        target_id=device_id,
        device_id=device_id,
        payload={
            "outcome": "revoked",
            "sessions_revoked": True,
            "fingerprint": row["fingerprint"],
            "user_id": str(row["user_id"]),
        },
    )
    return {"device_id": device_id, "status": "revoked"}

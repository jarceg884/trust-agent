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
    device_type: str = "desktop",
) -> dict:
    """Create/re-enroll a pending device and mint a one-time enrollment token.

    Upsert key: (tenant_id, fingerprint) — retries never create extra rows.
    `device_type` (spec 034, D-034-04): 'cli' | 'desktop' — validated by the
    router; stored so admins can tell a CLI install from a desktop app.
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
                " enrollment_token_hash, device_type) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), "
                " CAST(:user_id AS uuid), :fp, :name, :platform, 'pending', "
                " :public_key, :enrollment_hash, :device_type)"
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
                "device_type": device_type,
            },
        )
    else:
        if str(existing["user_id"]) != str(user_id):
            # Fingerprint already taken by another user of this tenant.
            raise conflict("This device fingerprint is already enrolled.")
        if existing["status"] == "active":
            # D-HRB-03 amend (spec 022 v1.1.0, REQ-HRB-010b, gap t_0b6e8e2d):
            # the device is ALREADY approved and the caller just re-proved
            # identity with a fresh MFA id_token (verified by the router) —
            # re-issue the device token DIRECTLY, no admin re-approval and
            # no one-time enrollment token. This is the documented recovery
            # for an expired device token (agent off > TTL): re-run the
            # login flow and the next enroll yields a fresh token.
            device_id = str(existing["id"])
            settings = get_settings()
            audit_service.emit(
                conn,
                tenant_id=str(tenant_id),
                actor_role=_enrolling_role(conn, user_id),
                actor_id=str(user_id),
                action="device.token_reissued",
                target_type="device",
                target_id=device_id,
                device_id=device_id,
                payload={"fingerprint": fingerprint, "user_id": str(user_id)},
            )
            return {
                "device_id": device_id,
                "status": "active",
                "device_token": issue_device_token(
                    settings,
                    device_id=device_id,
                    user_id=str(user_id),
                    tenant_id=str(tenant_id),
                ),
                "expires_in": settings.device_token_ttl_minutes * 60,
            }
        # pending (retry) or revoked/rejected (re-enrollment, ADR-013):
        # reset to pending with the new key and a fresh enrollment token.
        device_id = str(existing["id"])
        conn.execute(
            text(
                "UPDATE devices SET status = 'pending', public_key = :public_key, "
                " device_name = :name, platform = :platform, "
                " device_type = :device_type, "
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
                "device_type": device_type,
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


def preauthorize(
    conn: Connection,
    *,
    tenant_id: str,
    user_id: str,
    actor: Principal,
    device_name: str | None = None,
    platform: str | None = None,
) -> dict:
    """Admin-issued enrollment token (Helios-style, spec 044).

    Unlike :func:`enroll`, this flow is STARTED BY THE ADMIN: they pick the
    user in the console and the control plane creates an ALREADY-AUTHORIZED
    device slot plus its one-time token. The employee then only enters
    URL + organization + token in the app — no OIDC id_token file, no
    trust-login, no second approval step.

    The token is minted here and returned EXACTLY ONCE (ADR-006); only
    sha256(secret) is stored. The slot starts ``active`` because the admin's
    choice IS the authorization (approved_by records who granted it).
    """
    row = conn.execute(
        text("SELECT id, email, status FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        raise not_found("User not found.")
    if row["status"] != "active":
        raise validation_failed("The user must be active before issuing a token.")

    secret = secrets.token_urlsafe(32)
    device_id = str(uuid.uuid4())
    # Placeholder fingerprint: unique per slot and replaced with the real
    # machine fingerprint on redemption (exchange binds it).
    placeholder = f"unclaimed:{device_id}"
    conn.execute(
        text(
            "INSERT INTO devices (id, tenant_id, user_id, fingerprint, device_name,"
            " platform, status, enrollment_token_hash, approved_at, approved_by)"
            " VALUES (CAST(:did AS uuid), CAST(:t AS uuid), CAST(:u AS uuid),"
            " :fp, :name, :platform, 'active', :hash, now(), CAST(:by AS uuid))"
        ),
        {
            "did": device_id,
            "t": tenant_id,
            "u": user_id,
            "fp": placeholder,
            "name": device_name,
            "platform": platform,
            "hash": _hash(secret),
            "by": actor.user_id,
        },
    )
    audit_service.emit(
        conn,
        tenant_id=tenant_id,
        actor_role=_actor_role(actor),
        action="device.enrollment_token_issued",
        actor_id=actor.user_id,
        target_type="user",
        target_id=user_id,
        device_id=device_id,
        payload={"device_id": device_id, "issued_to": row["email"]},
    )
    return {
        "device_id": device_id,
        "user_id": user_id,
        "user_email": row["email"],
        "status": "active",
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


def exchange(
    engine: Engine,
    enrollment_token: str,
    *,
    fingerprint: str | None = None,
    public_key: str | None = None,
    device_name: str | None = None,
    platform: str | None = None,
    issue_model_key: bool = True,
) -> dict:
    """Redeem the enrollment token for the signed device token (one-time).

    pending -> {"status": "pending"} (router answers 202, nothing consumed)
    active  -> device token, hash consumed (second redemption -> 410)

    Spec 044 (Helios-style admin tokens): when the caller supplies the real
    machine identity (fingerprint/public_key/device_name/platform) the device
    slot created by :func:`preauthorize` is bound to this machine before the
    token is consumed. The same call also provisions the LiteLLM virtual key
    so the app is usable immediately — no separate trust-login step.

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

        # Bind the real machine identity over the placeholder, if supplied.
        if fingerprint:
            # Re-enrolling the same machine must not trip the unique
            # (tenant, fingerprint) constraint: retire any OTHER live row
            # that already holds this fingerprint.
            conn.execute(
                text(
                    "UPDATE devices SET status = 'revoked', "
                    "fingerprint = 'retired:' || id::text, updated_at = now() "
                    "WHERE fingerprint = :fp AND id <> CAST(:did AS uuid) "
                    "AND status <> 'revoked'"
                ),
                {"fp": fingerprint, "did": device_id},
            )
            conn.execute(
                text(
                    "UPDATE devices SET fingerprint = :fp, "
                    "public_key = COALESCE(:pk, public_key), "
                    "device_name = COALESCE(:name, device_name), "
                    "platform = COALESCE(:plat, platform), updated_at = now() "
                    "WHERE id = CAST(:did AS uuid)"
                ),
                {
                    "fp": fingerprint,
                    "pk": public_key,
                    "name": device_name,
                    "plat": platform,
                    "did": device_id,
                },
            )

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
    result = {
        "device_token": device_token,
        "device_id": device_id,
        "status": "active",
        "expires_in": settings.device_token_ttl_minutes * 60,
    }

    # Spec 044: provision the model key in the same activation so the app is
    # usable immediately. Fail-soft: a key outage must not void a valid
    # device token (the admin can re-issue from the console).
    if issue_model_key:
        try:
            from ..models.keys import _target_principal, issue_key

            with engine.begin() as conn2:
                from ..db import set_tenant as _set_tenant

                _set_tenant(conn2, tenant_hint)
                target = _target_principal(conn2, tenant_hint, str(row["user_id"]))
                issued = issue_key(conn2, target, user_id=str(row["user_id"]))
            result["model_key"] = issued.get("key")
            result["model_key_alias"] = issued.get("alias")
            result["allowed_models"] = issued.get("allowed_models") or []
        except Exception:  # health: activation must survive a key outage
            result["model_key"] = None

    return result


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
            "SELECT id, fingerprint, device_name, platform, device_type, status, "
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
            "device_type": r["device_type"],
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
    "SELECT d.id, d.fingerprint, d.device_name, d.platform, d.device_type, "
    "d.status, "
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
        "device_type": row["device_type"],
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
        action="device.rejected",
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


# ---------------------------------------------------------------------------
# Presence (spec 027, REQ-027-04): who is connected RIGHT NOW
# ---------------------------------------------------------------------------
_PRESENCE_SELECT = text(
    "SELECT d.id, d.fingerprint, d.device_name, d.platform, d.device_type, "
    "d.status, "
    "d.last_heartbeat_at, d.applied_policy_version, d.agent_uptime_seconds, "
    "d.user_id, u.email, u.display_name, u.department_id, "
    "dept.name AS department_name "
    "FROM devices d "
    "JOIN users u ON u.tenant_id = d.tenant_id AND u.id = d.user_id "
    "LEFT JOIN departments dept "
    " ON dept.tenant_id = d.tenant_id AND dept.id = u.department_id"
)


def presence(
    conn: Connection, *, principal: Principal, department_id: str | None = None
) -> dict:
    """Live presence: online count + per-device detail (D-027-04).

    `online` is computed ONLY over active devices: a heartbeat younger than
    the online window means the enrolled agent is alive. The current policy
    version per device comes from the same function that signs the bundle,
    so `policy_up_to_date` is the live-propagation signal the admin sees.
    """
    from ..config import get_settings
    from ..governance.bundle import _policy_version

    settings = get_settings()
    window = max(60, settings.presence_online_window_seconds)

    clauses, params = [], {}
    scope = dept_scope(principal)
    if scope is not None:  # dept_admin (or plain user): only their departments
        clauses.append("u.department_id = ANY(:depts)")
        params["depts"] = scope
    if department_id is not None:
        clauses.append("u.department_id = CAST(:dept AS uuid)")
        params["dept"] = department_id
    sql = _PRESENCE_SELECT
    if clauses:
        sql = text(str(_PRESENCE_SELECT) + " WHERE " + " AND ".join(clauses))
    rows = conn.execute(sql, params).mappings()

    devices = []
    online_count = 0
    active_count = 0
    for r in rows:
        current_version = _policy_version(
            conn, department_id=r["department_id"] and str(r["department_id"]),
            user_id=str(r["user_id"]),
        )
        heartbeat = r["last_heartbeat_at"]
        online = (
            r["status"] == "active"
            and heartbeat is not None
            and (datetime.now(timezone.utc) - heartbeat).total_seconds() <= window
        )
        if r["status"] == "active":
            active_count += 1
        if online:
            online_count += 1
        applied = r["applied_policy_version"]
        devices.append({
            "id": str(r["id"]),
            "user_id": str(r["user_id"]),
            "email": r["email"],
            "display_name": r["display_name"],
            "department_id": str(r["department_id"]) if r["department_id"] else None,
            "department_name": r["department_name"],
            "fingerprint": r["fingerprint"],
            "device_name": r["device_name"],
            "platform": r["platform"],
            "device_type": r["device_type"],
            "status": r["status"],
            "online": online,
            "last_heartbeat_at": _iso(heartbeat),
            "applied_policy_version": applied,
            "current_policy_version": current_version,
            "policy_up_to_date": applied is not None and applied == current_version,
            "agent_uptime_seconds": r["agent_uptime_seconds"],
        })
    return {
        "online_count": online_count,
        "active_count": active_count,
        "online_window_seconds": window,
        "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
        "devices": devices,
    }


# ---------------------------------------------------------------------------
# Integral revocation sweep (spec 019 Incremento 4, REQ-GOH-011 / D-GOH-05)
# ---------------------------------------------------------------------------
def _block_user_virtual_keys(
    conn: Connection, principal: Principal, *, user_id: str, device_id: str
) -> list[dict]:
    """Block the user's ACTIVE LiteLLM virtual keys (REQ-GOH-011a).

    Decision against the real model (platform/app/models/): virtual keys
    are USER-scoped — ``api_keys.device_id`` exists in the schema but no
    issuance path populates it, and D-GOH-05/REQ-GOH-011 say "la virtual
    key del usuario" dies on revocation. We therefore block every ACTIVE
    key of the device's owner via the real proxy ``/key/block`` (by hash,
    D-GOV-02) and mark it ``blocked`` in the same transaction.

    Fail-closed: a LiteLLM failure raises service_unavailable, which
    rolls back the WHOLE revocation (the device stays active). A 404
    from the proxy means the key no longer exists at the gateway — it is
    already dead, so we proceed (same rule as keys.revoke_key).
    """
    from ..models.litellm import get_litellm

    litellm = get_litellm()
    rows = conn.execute(
        text(
            "SELECT id, alias, litellm_key_hash FROM api_keys "
            "WHERE user_id = CAST(:uid AS uuid) AND status = 'active'"
        ),
        {"uid": user_id},
    ).mappings().all()
    blocked: list[dict] = []
    for r in rows:
        litellm.block_key(r["litellm_key_hash"])
        conn.execute(
            text(
                "UPDATE api_keys SET status = 'blocked', updated_at = now() "
                "WHERE id = CAST(:kid AS uuid)"
            ),
            {"kid": str(r["id"])},
        )
        audit_service.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            actor_id=principal.user_id,
            action="model.key_blocked",
            target_type="api_key",
            target_id=str(r["id"]),
            device_id=device_id,
            payload={
                "reason": "device_revoked",
                "device_id": device_id,
                "alias": r["alias"],
            },
        )
        blocked.append({"key_id": str(r["id"]), "alias": r["alias"]})
    return blocked


def _sweep_device_connector_grants(
    conn: Connection, principal: Principal, *, user_id: str, device_id: str
) -> list[dict]:
    """Disconnect the user's ACTIVE connector instances (REQ-GOH-011b).

    Uses the shared cutoff primitive (REQ-CON-005) so catalog connectors
    die exactly like the policy sweep does; each cut is audited as
    ``connector.disconnected`` (reason device_revoked) and the owner gets
    an inbox notification. Custom connections (spec 024) have no
    per-device grant row: their runtime injection channel is session-
    gated and the device's sessions are revoked in this same transaction,
    which cuts that channel too.
    """
    from ..connectors import service as connectors_service
    from ..governance import notifications as inbox

    cut = connectors_service.disconnect_connector_instances(
        conn, user_id=user_id
    )
    swept: list[dict] = []
    for inst in cut:
        audit_service.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            actor_id=principal.user_id,
            action="connector.disconnected",
            target_type="connector_instance",
            target_id=str(inst["id"]),
            device_id=device_id,
            payload={
                "connector_slug": inst["connector_slug"],
                "reason": "device_revoked",
                "device_id": device_id,
            },
        )
        inbox.notify(
            conn,
            tenant_id=principal.tenant_id,
            user_id=user_id,
            type="connector_disconnected",
            payload={
                "connector_slug": inst["connector_slug"],
                "reason": "device_revoked",
                "device_id": device_id,
            },
        )
        swept.append(
            {
                "instance_id": str(inst["id"]),
                "connector_slug": inst["connector_slug"],
            }
        )
    return swept


def revoke(conn: Connection, *, principal: Principal, device_id: str) -> dict:
    """Revoke a device AND its sessions AND its control-plane access in
    the SAME transaction (REQ-DEV-004 + REQ-GOH-011): the next request is
    401, no cache involved, and the revoked device can neither invoke
    models (virtual key blocked at the gateway) nor use connectors whose
    credentials live in the server-side vault.

    The sweep runs only when the device was ACTIVE: a pending device
    never exchanged a device token nor held sessions, so it has no
    control-plane access to cut — and killing the user's key because an
    unapproved enrollment was revoked would cut access that belongs to
    the user's OTHER active devices, beyond what the spec promises
    ("la revocación corta el acceso real" DEL dispositivo revocado).

    Fail-closed (spec 019 §9 Incremento 4): if the LiteLLM block or the
    connector sweep fails, the exception propagates and the transaction
    rolls back — the revocation is NOT confirmed.
    """
    row = _get_scoped_device(conn, principal, device_id)
    if row["status"] not in ("pending", "active"):
        raise conflict("The device is already revoked or rejected.")
    was_active = row["status"] == "active"
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
    sweep = {"keys_blocked": [], "connectors_disconnected": []}
    if was_active:
        sweep["keys_blocked"] = _block_user_virtual_keys(
            conn, principal, user_id=str(row["user_id"]), device_id=device_id
        )
        sweep["connectors_disconnected"] = _sweep_device_connector_grants(
            conn, principal, user_id=str(row["user_id"]), device_id=device_id
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
            # REQ-GOH-011: the audit carries the sweep summary (keys
            # blocked + connectors swept) so the admin can verify the
            # hard remote guarantee from the audit trail alone.
            "sweep": sweep,
        },
    )
    return {
        "device_id": device_id,
        "status": "revoked",
        "sweep": sweep,
    }


def delete(conn: Connection, *, principal: Principal, device_id: str) -> None:
    """Hard-delete an enrolled device and its dependent rows.

    Admin action from the console. Sessions and API keys are removed first so
    the device row can be deleted without dangling references. Audit keeps the
    fact (and the device id) after the row is gone.
    """
    row = _get_scoped_device(conn, principal, device_id)
    audit_service.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        action="device.deleted",
        actor_id=principal.user_id,
        target_type="device",
        target_id=device_id,
        device_id=device_id,
        payload={
            "device_name": row["device_name"],
            "user_id": str(row["user_id"]),
            "previous_status": row["status"],
        },
    )
    conn.execute(
        text("DELETE FROM sessions WHERE device_id = CAST(:did AS uuid)"),
        {"did": device_id},
    )
    conn.execute(
        text("DELETE FROM api_keys WHERE device_id = CAST(:did AS uuid)"),
        {"did": device_id},
    )
    conn.execute(
        text("DELETE FROM devices WHERE id = CAST(:did AS uuid)"),
        {"did": device_id},
    )

"""Connector instances: governance + cutoff primitive (spec 006, REQ-VC-010…015).

`disconnect_connector_instances` is the shared cutoff primitive (REQ-CON-005):
the credential revocation path (vault) and the policy-update path (spec 005)
both call it so active instances are cut in the same transaction (≤ 60 s).
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..audit import service as audit
from ..auth.rbac import dept_scope
from ..auth.service import Principal
from ..errors import (
    conflict,
    connector_not_allowed,
    messaging_mode_not_allowed,
    not_found,
    validation_failed,
)
from ..governance.combinator import MODE_RANKS
from ..governance.service import effective_policy_for_user

# Secret-looking config keys are rejected: connector config is NON-secret by
# schema (002 §5.11); secrets live in the vault (C4, REQ-SEC-005).
_FORBIDDEN_CONFIG_KEYS = ("password", "secret", "token", "api_key", "credential")


def _to_uuid(value: str) -> str:
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise not_found("Resource not found.")
    return value


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _instance_meta(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "connector_slug": row["connector_slug"],
        "status": row["status"],
        "mode": row["mode"],
        "credential_id": str(row["credential_id"]) if row.get("credential_id") else None,
        "last_used_at": _iso(row.get("last_used_at")),
        "created_at": _iso(row.get("created_at")),
    }


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _visibility_sql(principal: Principal) -> tuple[str, dict]:
    """Same visibility model as the vault (D-VC-04)."""
    if "org_admin" in principal.role_keys:
        return "TRUE", {}
    params: dict = {"uid": principal.user_id}
    if "dept_admin" in principal.role_keys:
        depts = dept_scope(principal) or []
        if depts:
            return (
                "(i.user_id = CAST(:uid AS uuid) "
                " OR owner.department_id = ANY(CAST(:depts AS uuid[])))",
                {**params, "depts": [_uuid.UUID(d) for d in depts]},
            )
    return "i.user_id = CAST(:uid AS uuid)", params


def disconnect_connector_instances(
    conn: Connection,
    *,
    connector_slug: str | None = None,
    credential_id: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    """Cut ACTIVE instances matching the filters; caller keeps the transaction
    and emits the audit events (REQ-CON-005 / REQ-VLT-005).

    -> list of disconnected rows [{id, connector_slug, user_id}].
    """
    clauses = ["i.status = 'active'"]
    params: dict = {}
    if connector_slug is not None:
        clauses.append("i.connector_slug = :slug")
        params["slug"] = connector_slug
    if credential_id is not None:
        clauses.append("i.credential_id = CAST(:cred AS uuid)")
        params["cred"] = _to_uuid(credential_id)
    if user_id is not None:
        clauses.append("i.user_id = CAST(:uid AS uuid)")
        params["uid"] = user_id
    rows = conn.execute(
        text(
            "UPDATE connector_instances i SET status = 'disconnected', "
            "updated_at = now() WHERE " + " AND ".join(clauses) +
            " RETURNING i.id, i.connector_slug, i.user_id"
        ),
        params,
    ).mappings()
    return [dict(r) for r in rows]


def sweep_after_policy_change(
    conn: Connection,
    principal: Principal,
    *,
    level: str,
    department_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """REQ-CON-005 (spec 013 D-013-02): proactive disconnect ≤ 60 s.

    Called from policies.upsert/deactivate IN THE SAME transaction when
    `allowed_connectors` or `messaging_permissions` change at org/dept/
    user level. Re-evaluates the NEW effective policy of every affected
    user and cuts their ACTIVE instances that it no longer allows, using
    the shared cutoff primitive. Each cut is audited as
    `connector.disconnected` (reason policy_updated) and the owner gets
    an inbox notification. Same transaction → no 60 s race; the at-use
    enforcement (catalog filter + _check_instance_governance) remains as
    defense in depth.

    -> {"disconnected": n, "notified": m}
    """
    from ..governance import notifications as inbox
    from ..governance.service import effective_policy

    if level == "user":
        rows = conn.execute(
            text("SELECT id, department_id FROM users "
                 "WHERE id = CAST(:u AS uuid) AND status = 'active'"),
            {"u": user_id},
        ).mappings()
    elif level == "dept":
        rows = conn.execute(
            text("SELECT id, department_id FROM users "
                 "WHERE department_id = CAST(:d AS uuid) AND status = 'active'"),
            {"d": department_id},
        ).mappings()
    else:  # org: every active user of the tenant
        rows = conn.execute(
            text("SELECT id, department_id FROM users WHERE status = 'active'")
        ).mappings()

    disconnected = 0
    notified = 0
    for r in rows:
        owner_id = str(r["id"])
        owner_dept = (
            str(r["department_id"]) if r["department_id"] else None
        )
        policy = effective_policy(
            conn, user_id=owner_id, department_id=owner_dept
        )
        allowed = set(policy.get("allowed_connectors") or [])
        messaging = policy.get("messaging_permissions") or {}
        instances = conn.execute(
            text("SELECT id, connector_slug FROM connector_instances "
                 "WHERE user_id = CAST(:u AS uuid) AND status = 'active'"),
            {"u": owner_id},
        ).mappings()
        for inst in instances:
            slug = inst["connector_slug"]
            cat = conn.execute(
                text("SELECT category FROM catalog.connector_catalog "
                     "WHERE slug = :slug"),
                {"slug": slug},
            ).mappings().first()
            # Same rule as _check_instance_governance (D-VC-03): the
            # allow-list for everything, the messaging map on top for
            # messaging platforms.
            if cat and cat["category"] == "messaging":
                still_allowed = bool(
                    (messaging.get(slug) or {}).get("allowed", False)
                )
            else:
                still_allowed = slug in allowed
            if still_allowed:
                continue
            cut = disconnect_connector_instances(
                conn, connector_slug=slug, user_id=owner_id
            )
            for row in cut:
                audit.emit(
                    conn,
                    tenant_id=principal.tenant_id,
                    actor_role=_actor_role(principal),
                    actor_id=principal.user_id,
                    action="connector.disconnected",
                    target_type="connector_instance",
                    target_id=str(row["id"]),
                    payload={
                        "connector_slug": row["connector_slug"],
                        "reason": "policy_updated",
                        "policy_level": level,
                        **({"department_id": department_id}
                           if department_id else {}),
                    },
                )
                inbox.notify(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=owner_id,
                    type="connector_disconnected",
                    payload={
                        "connector_slug": row["connector_slug"],
                        "reason": "policy_updated",
                        "policy_level": level,
                    },
                )
                disconnected += 1
                notified += 1
    return {"disconnected": disconnected, "notified": notified}


def list_available(conn: Connection, principal: Principal) -> list[dict]:
    """Catalog filtered by the principal's effective allowed_connectors
    (REQ-CON-003: the desktop shows only what policy allows)."""
    policy = effective_policy_for_user(conn, principal)
    allowed = set(policy.get("allowed_connectors") or [])
    messaging = policy.get("messaging_permissions") or {}
    rows = conn.execute(
        text(
            "SELECT slug, name, category, capabilities, required_scopes, "
            "status FROM catalog.connector_catalog "
            "WHERE status = 'available' ORDER BY slug"
        )
    ).mappings()
    out = []
    for r in rows:
        if r["slug"] not in allowed:
            continue
        item = dict(r)
        if r["category"] == "messaging":
            perm = messaging.get(r["slug"]) or {}
            item["messaging"] = {
                "allowed": bool(perm.get("allowed", False)),
                "mode": perm.get("mode", "read_only"),
            }
        out.append(item)
    return out


def _validate_config(config: dict) -> None:
    for key in (config or {}):
        lowered = str(key).lower()
        if any(f in lowered for f in _FORBIDDEN_CONFIG_KEYS):
            raise validation_failed(
                f"Config key '{key}' looks like a secret; connector config is "
                "non-secret — store credentials in the vault."
            )


def _check_instance_governance(
    conn: Connection,
    principal: Principal,
    *,
    connector_slug: str,
    mode: str | None,
) -> str:
    """Connector allow-list + messaging map gates (D-VC-03). -> final mode."""
    policy = effective_policy_for_user(conn, principal)
    if connector_slug not in (policy.get("allowed_connectors") or []):
        raise connector_not_allowed(
            f"Connector '{connector_slug}' is not allowed by policy."
        )
    cat = conn.execute(
        text(
            "SELECT category FROM catalog.connector_catalog "
            "WHERE slug = :slug AND status = 'available'"
        ),
        {"slug": connector_slug},
    ).mappings().first()
    if cat is None:
        raise not_found("Connector not found.")

    if mode is None:
        mode = "read_only"  # most restrictive default
    if mode not in MODE_RANKS:
        raise validation_failed(
            "Mode must be one of: read_only, reply, full."
        )
    if cat["category"] == "messaging":
        perms = policy.get("messaging_permissions") or {}
        perm = perms.get(connector_slug) or {}
        if not perm.get("allowed", False):
            raise connector_not_allowed(
                f"Messaging platform '{connector_slug}' is not allowed by "
                "policy."
            )
        effective_mode = perm.get("mode", "read_only")
        if MODE_RANKS[mode] < MODE_RANKS.get(effective_mode, 3):
            raise messaging_mode_not_allowed(
                f"Mode '{mode}' exceeds the policy mode '{effective_mode}' "
                f"for '{connector_slug}'."
            )
    return mode


def create_instance(
    conn: Connection,
    principal: Principal,
    *,
    connector_slug: str,
    credential_id: str | None = None,
    mode: str | None = None,
    config: dict | None = None,
) -> tuple[dict, bool]:
    """Create (or reactivate) a connector instance for the principal.

    -> (metadata, reactivated). Governance per D-VC-03/D-VC-07; the linked
    credential must belong to the principal, be active and match the slug.
    """
    config = config or {}
    _validate_config(config)
    final_mode = _check_instance_governance(
        conn, principal, connector_slug=connector_slug, mode=mode
    )

    if credential_id is not None:
        cred = conn.execute(
            text(
                "SELECT id, status, connector_slug FROM vault_credentials "
                "WHERE id = CAST(:cid AS uuid) "
                "  AND user_id = CAST(:uid AS uuid)"
            ),
            {"cid": _to_uuid(credential_id), "uid": principal.user_id},
        ).mappings().first()
        if cred is None:
            raise not_found("Credential not found.")
        if cred["status"] != "active":
            raise validation_failed("Credential is revoked.")
        if cred["connector_slug"] != connector_slug:
            raise validation_failed(
                "Credential does not belong to this connector."
            )

    existing = conn.execute(
        text(
            "SELECT * FROM connector_instances "
            "WHERE user_id = CAST(:uid AS uuid) AND connector_slug = :slug"
        ),
        {"uid": principal.user_id, "slug": connector_slug},
    ).mappings().first()

    if existing is not None:
        if existing["status"] == "active":
            raise conflict("Connector already connected.")
        if existing["status"] == "revoked":
            raise conflict(
                "Instance was revoked by an administrator; a new one cannot "
                "be created."
            )
        row = conn.execute(
            text(
                "UPDATE connector_instances SET status = 'active', "
                "mode = :mode, credential_id = CAST(:cred AS uuid), "
                "config = CAST(:config AS jsonb), updated_at = now() "
                "WHERE id = CAST(:iid AS uuid) RETURNING *"
            ),
            {"mode": final_mode, "cred": credential_id,
             "config": _dump_config(config), "iid": str(existing["id"])},
        ).mappings().first()
        reactivated = True
    else:
        try:
            row = conn.execute(
                text(
                    "INSERT INTO connector_instances "
                    "(tenant_id, user_id, connector_slug, status, mode, "
                    " credential_id, config) "
                    "VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), :slug, "
                    "        'active', :mode, CAST(:cred AS uuid), "
                    "        CAST(:config AS jsonb)) RETURNING *"
                ),
                {
                    "tid": principal.tenant_id, "uid": principal.user_id,
                    "slug": connector_slug, "mode": final_mode,
                    "cred": credential_id, "config": _dump_config(config),
                },
            ).mappings().first()
        except IntegrityError:
            raise conflict("Connector already connected.")
        reactivated = False

    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="connector.connected",
        target_type="connector_instance",
        target_id=str(row["id"]),
        payload={"connector_slug": connector_slug, "mode": final_mode,
                 "reactivated": reactivated},
    )
    return _instance_meta(dict(row)), reactivated


def list_instances(conn: Connection, principal: Principal) -> list[dict]:
    frag, params = _visibility_sql(principal)
    rows = conn.execute(
        text(
            "SELECT i.* FROM connector_instances i "
            "JOIN users owner ON owner.tenant_id = i.tenant_id "
            "               AND owner.id = i.user_id "
            f"WHERE {frag} ORDER BY i.created_at DESC, i.id"
        ),
        params,
    ).mappings()
    return [_instance_meta(dict(r)) for r in rows]


def get_instance(
    conn: Connection, principal: Principal, instance_id: str
) -> dict:
    frag, params = _visibility_sql(principal)
    row = conn.execute(
        text(
            "SELECT i.* FROM connector_instances i "
            "JOIN users owner ON owner.tenant_id = i.tenant_id "
            "               AND owner.id = i.user_id "
            f"WHERE i.id = CAST(:iid AS uuid) AND {frag}"
        ),
        {"iid": _to_uuid(instance_id), **params},
    ).mappings().first()
    if row is None:
        raise not_found("Connector instance not found.")
    return _instance_meta(dict(row))


def disconnect_instance(
    conn: Connection, principal: Principal, instance_id: str
) -> dict:
    """User-driven disconnect (idempotent on already-disconnected)."""
    meta = get_instance(conn, principal, instance_id)
    if meta["status"] == "revoked":
        raise conflict("Instance was revoked and cannot be modified.")
    if meta["status"] == "active":
        conn.execute(
            text(
                "UPDATE connector_instances SET status = 'disconnected', "
                "updated_at = now() WHERE id = CAST(:iid AS uuid)"
            ),
            {"iid": meta["id"]},
        )
        audit.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            actor_id=principal.user_id,
            action="connector.disconnected",
            target_type="connector_instance",
            target_id=meta["id"],
            payload={"connector_slug": meta["connector_slug"],
                     "reason": "user_disconnect"},
        )
        meta["status"] = "disconnected"
    return meta


def _dump_config(config: dict) -> str:
    import json

    return json.dumps(config or {})

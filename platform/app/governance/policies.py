"""Policy upsert at three levels (REQ-POL-001…007, D-GOV-09).

Typed catalog keys only; invariable provider floor keys are rejected at
any level (422); a dept/user value that RELAXES the superior level's
current value is rejected (422) using the same restrictiveness semantics
as the deny-overrides combinator: a value is acceptable iff
combine([superior, new]) == new (i.e. the new value is the most
restrictive of the two).
"""
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import not_found, validation_failed
from .combinator import STRING_RANKS, combine


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _catalog_entry(conn: Connection, key: str) -> dict:
    row = conn.execute(
        text(
            "SELECT key, value_type, invariable, default_value "
            "FROM catalog.policy_keys WHERE key = :k"
        ),
        {"k": key},
    ).mappings().first()
    if row is None:
        raise validation_failed(f"Unknown policy key: '{key}' (typed catalog "
                                "only, REQ-POL-003).")
    return dict(row)


def _validate_value(entry: dict, value) -> None:
    vt = entry["value_type"]
    if vt == "boolean" and not isinstance(value, bool):
        raise validation_failed(f"Policy '{entry['key']}' expects a boolean.")
    if vt == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise validation_failed(f"Policy '{entry['key']}' expects an integer.")
    if vt == "list":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise validation_failed(f"Policy '{entry['key']}' expects a list of strings.")
    if vt == "string" and not isinstance(value, str):
        raise validation_failed(f"Policy '{entry['key']}' expects a string.")
    if vt == "map" and not isinstance(value, dict):
        raise validation_failed(f"Policy '{entry['key']}' expects a map.")
    if vt == "string":
        ranks = STRING_RANKS.get(entry["key"], {})
        if ranks and value not in ranks:
            raise validation_failed(
                f"Policy '{entry['key']}' accepts one of: {sorted(ranks)}."
            )


def _current_value(
    conn: Connection, *, level: str,
    department_id: str | None, user_id: str | None, key: str,
) -> object | None:
    """Latest active value for a key at one level (max version)."""
    row = conn.execute(
        text(
            "SELECT value FROM policies WHERE key = :k AND level = :level "
            "AND is_active "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid)) "
            "ORDER BY version DESC LIMIT 1"
        ),
        {
            "k": key, "level": level, "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings().first()
    return row["value"] if row else None


def _relaxes(superior, new, key: str, value_type: str) -> bool:
    """True when `new` is LESS restrictive than `superior` (REQ-POL-007)."""
    if superior is None:
        return False
    catalog = {key: {"value_type": value_type, "invariable": False,
                     "default_value": None}}
    combined = combine({key: superior}, {key: new}, {}, catalog)[key]
    return combined != new


def _sweep_connectors(
    conn: Connection, principal: Principal, *, level: str,
    department_id: str | None, user_id: str | None, key: str,
) -> None:
    """REQ-CON-005 (spec 013 D-013-02): after a policy change that can
    remove connectors, cut the active instances the NEW effective policy
    no longer allows — same transaction, notified and audited."""
    if key not in ("allowed_connectors", "messaging_permissions"):
        return
    from ..connectors import service as connectors_service

    connectors_service.sweep_after_policy_change(
        conn, principal, level=level,
        department_id=department_id, user_id=user_id,
    )


def upsert(
    conn: Connection,
    principal: Principal,
    *,
    level: str,
    key: str,
    value,
    department_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    if level not in ("org", "dept", "user"):
        raise validation_failed("level must be org, dept or user.")
    entry = _catalog_entry(conn, key)
    if entry["invariable"]:
        raise validation_failed(
            f"Policy '{key}' is a provider invariable (floor) and cannot be "
            "set at any level (REQ-POL-004)."
        )
    _validate_value(entry, value)

    # REQ-POL-007: dept/user values may not relax the superior levels.
    if level == "dept":
        org_value = _current_value(conn, level="org", department_id=None,
                                   user_id=None, key=key)
        if _relaxes(org_value, value, key, entry["value_type"]):
            raise validation_failed(
                f"Department policy would relax the organization policy for "
                f"'{key}' (REQ-POL-007)."
            )
    if level == "user":
        org_value = _current_value(conn, level="org", department_id=None,
                                   user_id=None, key=key)
        dept_value = (
            _current_value(conn, level="dept", department_id=department_id,
                           user_id=None, key=key)
            if department_id else None
        )
        for superior in (org_value, dept_value):
            if _relaxes(superior, value, key, entry["value_type"]):
                raise validation_failed(
                    f"User policy would relax a superior policy for '{key}' "
                    "(REQ-POL-007)."
                )

    # One row per (key, level, scope): the unique index policies_scope_uidx
    # has no is_active part — after deactivate() the SAME row must be
    # reactivated, never re-INSERTed (latent 500 exposed by spec 013 tests).
    existing = conn.execute(
        text(
            "SELECT id, version FROM policies WHERE key = :k AND level = :level "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid)) "
            "ORDER BY version DESC LIMIT 1"
        ),
        {
            "k": key, "level": level, "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings().first()

    if existing is not None:
        conn.execute(
            text(
                "UPDATE policies SET value = CAST(:v AS jsonb), "
                "version = version + 1, is_active = true, updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"v": json.dumps(value), "id": str(existing["id"])},
        )
        version = existing["version"] + 1
        policy_id = str(existing["id"])
    else:
        inserted = conn.execute(
            text(
                "INSERT INTO policies (tenant_id, level, department_id, "
                "user_id, key, value, version, created_by) "
                "VALUES (CAST(:t AS uuid), :level, CAST(:dept AS uuid), "
                "CAST(:usr AS uuid), :k, CAST(:v AS jsonb), 1, "
                "CAST(:actor AS uuid)) RETURNING id"
            ),
            {
                "t": principal.tenant_id, "level": level, "dept": department_id,
                "usr": user_id, "k": key, "v": json.dumps(value),
                "actor": principal.user_id,
            },
        ).mappings().first()
        version = 1
        policy_id = str(inserted["id"])

    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="policy.updated", actor_id=principal.user_id,
        target_type="policy", target_id=key,
        payload={"key": key, "level": level, "version": version,
                 "department_id": department_id, "user_id": user_id},
    )
    _sweep_connectors(conn, principal, level=level,
                      department_id=department_id, user_id=user_id, key=key)
    return {"id": policy_id, "key": key, "level": level, "version": version,
            "value": value}


def deactivate(
    conn: Connection, principal: Principal, *,
    level: str, key: str,
    department_id: str | None = None, user_id: str | None = None,
) -> None:
    """Deactivate EVERY version of the (key, level, scope) policy so the
    effective value falls back to the catalog default (or the superior
    level) — not to a stale previous version."""
    result = conn.execute(
        text(
            "UPDATE policies SET is_active = false, updated_at = now() "
            "WHERE key = :k AND level = :level AND is_active "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid))"
        ),
        {
            "k": key, "level": level, "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    )
    if result.rowcount == 0:
        raise not_found("Policy not found at this level.")
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="policy.updated", actor_id=principal.user_id,
        target_type="policy", target_id=key,
        payload={"level": level, "deactivated": True,
                 "department_id": department_id, "user_id": user_id},
    )
    _sweep_connectors(conn, principal, level=level,
                      department_id=department_id, user_id=user_id, key=key)


def list_level(
    conn: Connection, *, level: str,
    department_id: str | None = None, user_id: str | None = None,
) -> list[dict]:
    """Latest active value per key at one level (admin view)."""
    rows = conn.execute(
        text(
            "SELECT DISTINCT ON (key) key, value, version, updated_at "
            "FROM policies WHERE level = :level AND is_active "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid)) "
            "ORDER BY key, version DESC"
        ),
        {
            "level": level, "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings()
    return [
        {"key": r["key"], "value": r["value"], "version": r["version"],
         "updated_at": r["updated_at"].isoformat()}
        for r in rows
    ]

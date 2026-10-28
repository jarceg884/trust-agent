"""Policy upsert at three levels (REQ-POL-001…007, D-GOV-09).

Typed catalog keys only; invariable provider floor keys are rejected at
any level (422); a dept/user value that RELAXES the superior level's
current value is rejected (422) using the same restrictiveness semantics
as the deny-overrides combinator: a value is acceptable iff
combine([superior, new]) == new (i.e. the new value is the most
restrictive of the two).
"""
import json
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import not_found, validation_failed
from .combinator import ORG_ONLY_KEYS, STRING_RANKS, WEB_DOMAIN_MODES, combine

# Spec 019 (REQ-GOH-014): provider floors for integer policy keys.
# Spec 029 (REQ-029-004): floors for the capability-matrix integer keys —
# delegation depth cannot be negative and retention cannot be zero days.
INTEGER_FLOORS: dict[str, int] = {
    "offline_grace_minutes": 15,
    "attestation_overdue_hours": 1,
    "max_delegation_depth": 0,
    "session_retention_days": 1,
}


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
        # Spec 019 (REQ-GOH-014): provider floors for the agent governance
        # keys — a grace shorter than 15 min or an overdue window < 1 h would
        # make the fail-closed machinery (ADR-017) meaningless.
        floor = INTEGER_FLOORS.get(entry["key"])
        if floor is not None and value < floor:
            raise validation_failed(
                f"Policy '{entry['key']}' must be >= {floor}."
            )
    if vt == "list":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise validation_failed(f"Policy '{entry['key']}' expects a list of strings.")
    if vt == "string" and not isinstance(value, str):
        raise validation_failed(f"Policy '{entry['key']}' expects a string.")
    if vt == "map" and not isinstance(value, dict):
        raise validation_failed(f"Policy '{entry['key']}' expects a map.")
    # Spec 029 (REQ-029-005): structural validation for web_domain_policy —
    # the generic map check is not enough. Extra keys are tolerated
    # (forward-compatible schema), but mode/domains are strictly typed.
    if entry["key"] == "web_domain_policy" and isinstance(value, dict):
        mode = value.get("mode", "open")
        if mode not in WEB_DOMAIN_MODES:
            raise validation_failed(
                f"Policy 'web_domain_policy.mode' accepts one of: "
                f"{list(WEB_DOMAIN_MODES)}."
            )
        domains = value.get("domains", [])
        if not isinstance(domains, list) or not all(
            isinstance(d, str) for d in domains
        ):
            raise validation_failed(
                "Policy 'web_domain_policy.domains' expects a list of strings."
            )
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


def _canonical(value):
    """Order-insensitive form for relax comparison: the combinator returns
    lists/domains in sorted order, but the upsert value may list them in any
    order — a pure ordering difference is NOT a relaxation (spec 029)."""
    if isinstance(value, list):
        return sorted(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    return value


def _relaxes(superior, new, key: str, value_type: str) -> bool:
    """True when `new` is LESS restrictive than `superior` (REQ-POL-007)."""
    if superior is None:
        return False
    catalog = {key: {"value_type": value_type, "invariable": False,
                     "default_value": None}}
    combined = combine({key: superior}, {key: new}, {}, catalog)[key]
    return _canonical(combined) != _canonical(new)


def _validate_connection_tokens(
    conn: Connection, principal: Principal, value: list[str],
) -> None:
    """Spec 024 (REQ-024-005, D-24-07): typo-protection for the tokens of
    ``allowed_connections`` — each token must be the wildcard ``*``, a
    catalog connector slug, or the id of an ACTIVE custom connection of
    the tenant. Anything else answers 422 (coherent with REQ-POL-003)."""
    for token in value:
        if token == "*":
            continue
        slug = conn.execute(
            text("SELECT 1 FROM catalog.connector_catalog WHERE slug = :s"),
            {"s": token},
        ).first()
        if slug is not None:
            continue
        try:
            _uuid.UUID(token)
        except (ValueError, AttributeError, TypeError):
            raise validation_failed(
                f"Unknown allowed_connections token: '{token}' (expected "
                "'*', a catalog slug, or a custom connection id)."
            )
        active = conn.execute(
            text(
                "SELECT 1 FROM vault_connections "
                "WHERE id = CAST(:cid AS uuid) AND status = 'active'"
            ),
            {"cid": token},
        ).first()
        if active is None:
            raise validation_failed(
                f"allowed_connections token '{token}' is not an active "
                "custom connection of this organization."
            )


def _sweep_connectors(
    conn: Connection, principal: Principal, *, level: str,
    department_id: str | None, user_id: str | None, key: str,
) -> None:
    """REQ-CON-005 (spec 013 D-013-02): after a policy change that can
    remove connectors, cut the active instances the NEW effective policy
    no longer allows — same transaction, notified and audited.

    Spec 024 (REQ-024-008): ``allowed_connections`` also triggers the
    sweep — its tokens narrow the catalog on top of allowed_connectors."""
    if key not in (
        "allowed_connectors", "messaging_permissions", "allowed_connections",
    ):
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
    # Spec 015 (D-AUTH-07/08): org-only keys gate organization-wide behavior
    # that happens BEFORE any department context exists (login UX, self
    # registration) — the enforcement gate reads ONLY the org level, so a
    # dept/user override would be stored, shown in the matrix, and never
    # honored (hallazgo t_8f716f9f). Reject it instead of lying.
    if level in ("dept", "user") and key in ORG_ONLY_KEYS:
        raise validation_failed(
            f"Policy '{key}' is org-only (spec 015 D-AUTH-07/08): it gates "
            "organization-wide behavior and cannot be overridden at the "
            f"{level} level."
        )
    _validate_value(entry, value)

    # Spec 024 (D-24-07): token validation for allowed_connections.
    if key == "allowed_connections":
        _validate_connection_tokens(conn, principal, value)

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


def model_approvals(
    conn: Connection, *, level: str,
    department_id: str | None = None, user_id: str | None = None,
) -> list[dict]:
    """Approved models at one level/scope (spec 018, D-018-02).

    The approval matrix is exposed as part of the level's governance
    policy — not as a separate menu: (model_name, mode, status).
    """
    rows = conn.execute(
        text(
            "SELECT mc.model_name, ma.mode, ma.status FROM model_access ma "
            "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
            "WHERE ma.level = :level AND ma.status = 'approved' "
            "AND (CAST(:dept AS uuid) IS NULL OR ma.department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR ma.user_id = CAST(:usr AS uuid)) "
            "ORDER BY mc.model_name"
        ),
        {"level": level, "dept": department_id, "usr": user_id},
    ).mappings()
    return [
        {"model_name": r["model_name"], "mode": r["mode"],
         "status": r["status"]}
        for r in rows
    ]

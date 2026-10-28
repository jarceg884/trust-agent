"""Effective policy resolution for a principal (REQ-POL-006)."""
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.service import Principal
from .combinator import combine


def load_policy_catalog(conn: Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT key, value_type, invariable, default_value "
            "FROM catalog.policy_keys"
        )
    ).mappings()
    return {
        r["key"]: {
            "value_type": r["value_type"],
            "invariable": r["invariable"],
            "default_value": r["default_value"],
        }
        for r in rows
    }


def _load_level(
    conn: Connection,
    *,
    level: str,
    department_id: str | None,
    user_id: str | None,
) -> dict[str, Any]:
    """Latest active policy values for one level (max version per key)."""
    rows = conn.execute(
        text(
            "SELECT DISTINCT ON (key) key, value "
            "FROM policies "
            "WHERE level = :level AND is_active "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid)) "
            "ORDER BY key, version DESC"
        ),
        {
            "level": level,
            "dept": department_id,
            "usr": user_id,
            "dept_null": department_id is None,
            "user_null": user_id is None,
        },
    ).mappings()
    return {r["key"]: r["value"] for r in rows}


def org_policy_value(conn: Connection, key: str) -> Any:
    """Effective ORG-level value for a policy key (spec 015 D-AUTH-07/08).

    Org row if present, else the catalog default. Department/user overrides
    do NOT apply: `require_mfa` / `allow_self_registration` gate
    organization-wide behavior (login UX, registration) that happens before
    any department context exists.
    """
    catalog = load_policy_catalog(conn)
    entry = catalog.get(key)
    if entry is None:
        return None
    org = _load_level(conn, level="org", department_id=None, user_id=None)
    return org.get(key, entry["default_value"])


def effective_policy(
    conn: Connection, *, user_id: str, department_id: str | None,
) -> dict[str, Any]:
    """Deny-overrides combination org → dept → user for one user.

    User-id based (not Principal based) so governance flows can resolve
    the effective policy of ANY user — e.g. the REQ-CON-005 proactive
    sweep when a policy change disables a connector.
    """
    catalog = load_policy_catalog(conn)
    org = _load_level(conn, level="org", department_id=None, user_id=None)
    dept = (
        _load_level(
            conn, level="dept",
            department_id=department_id, user_id=None,
        )
        if department_id
        else {}
    )
    user = _load_level(
        conn, level="user", department_id=department_id, user_id=user_id,
    )
    return combine(org, dept, user, catalog)


def effective_policy_for_user(
    conn: Connection, principal: Principal
) -> dict[str, Any]:
    return effective_policy(
        conn, user_id=principal.user_id,
        department_id=principal.department_id,
    )

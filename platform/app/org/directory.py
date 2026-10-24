"""User directory + edit/disable (spec 012, REQ-ORG-100/103/104).

Scoping mirrors audit D-AU-02 (D-ORG-01): RLS isolates the tenant; the role
scope is an application filter in the SAME tenant-scoped transaction:

  - org_admin  -> whole tenant
  - dept_admin -> users of their departments
  - user       -> only themselves

No secrets in responses (D-ORG-08): oidc_subject never leaves the DB.
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import dept_scope, top_role_key
from ..auth.service import Principal
from ..errors import not_found, permission_denied, validation_failed

EDITABLE_FIELDS = ("display_name", "department_id")


def _roles_by_user(conn: Connection) -> dict[str, list[dict]]:
    rows = conn.execute(
        text(
            "SELECT user_id, role_key, department_id FROM user_roles "
            "ORDER BY created_at"
        )
    ).mappings()
    roles: dict[str, list[dict]] = {}
    for r in rows:
        roles.setdefault(str(r["user_id"]), []).append({
            "role_key": r["role_key"],
            "department_id": str(r["department_id"])
            if r["department_id"] else None,
        })
    return roles


def list_users(conn: Connection, principal: Principal) -> dict:
    """GET /api/users — the directory within the caller's role scope."""
    if "org_admin" in principal.role_keys:
        condition, params = "TRUE", {}
    elif "dept_admin" in principal.role_keys:
        scoped = dept_scope(principal) or []
        condition = "u.department_id = ANY(CAST(:scope AS uuid[]))"
        params = {"scope": scoped}
    else:
        condition = "u.id = CAST(:uid AS uuid)"
        params = {"uid": principal.user_id}
    rows = conn.execute(
        text(
            "SELECT u.id, u.email, u.display_name, u.department_id, u.status "
            f"FROM users u WHERE {condition} ORDER BY u.email"
        ),
        params,
    ).mappings()
    roles = _roles_by_user(conn)
    users = []
    for r in rows:
        uid = str(r["id"])
        users.append({
            "user_id": uid,
            "email": r["email"],
            "display_name": r["display_name"],
            "department_id": str(r["department_id"])
            if r["department_id"] else None,
            "status": r["status"],
            "roles": roles.get(uid, []),
        })
    return {"users": users}


def _get_user(conn: Connection, user_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT id, email, display_name, department_id, status "
            "FROM users WHERE id = CAST(:u AS uuid)"
        ),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        raise not_found("User not found.")
    return dict(row)


def _require_in_scope(
    conn: Connection, principal: Principal, user: dict,
) -> None:
    """users.manage holders only operate within their department scope."""
    if "org_admin" in principal.role_keys:
        return
    dept = str(user["department_id"]) if user["department_id"] else None
    if dept not in (dept_scope(principal) or []):
        # No existence oracle across scopes (D-CP-09).
        raise not_found("User not found.")


def update_user(
    conn: Connection, principal: Principal, *, user_id: str,
    display_name: str | None, department_id: str | None,
) -> dict:
    """PUT /api/users/{id} — edit display name / move department."""
    user = _get_user(conn, user_id)
    _require_in_scope(conn, principal, user)

    changes: dict = {}
    if display_name is not None:
        name = display_name.strip()
        if not name or len(name) > 120:
            raise validation_failed("display_name must be 1-120 characters.")
        if name != user["display_name"]:
            changes["display_name"] = name
    if department_id is not None:
        if department_id != (
            str(user["department_id"]) if user["department_id"] else None
        ):
            dept = conn.execute(
                text("SELECT id FROM departments WHERE id = CAST(:d AS uuid)"),
                {"d": department_id},
            ).mappings().first()
            if dept is None:
                raise validation_failed("Unknown department.")
            if "org_admin" not in principal.role_keys and \
                    department_id not in (dept_scope(principal) or []):
                raise permission_denied(
                    "Outside your department scope."
                )
            changes["department_id"] = department_id

    if changes:
        conn.execute(
            text(
                "UPDATE users SET display_name = :display, "
                "department_id = CAST(:dept AS uuid), updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {
                "display": changes.get("display_name", user["display_name"]),
                "dept": changes.get("department_id", user["department_id"]),
                "id": user_id,
            },
        )
        payload: dict = {"email": user["email"]}
        if "department_id" in changes:
            payload["department_id"] = changes["department_id"]
        audit.emit(
            conn, tenant_id=principal.tenant_id,
            actor_role=top_role_key(principal.role_keys),
            action="user.updated", actor_id=principal.user_id,
            target_type="user", target_id=user_id, payload=payload,
        )

    fresh = _get_user(conn, user_id)
    return {
        "user_id": str(fresh["id"]),
        "email": fresh["email"],
        "display_name": fresh["display_name"],
        "department_id": str(fresh["department_id"])
        if fresh["department_id"] else None,
        "status": fresh["status"],
    }


def disable_user(
    conn: Connection, principal: Principal, *, user_id: str,
) -> dict:
    """POST /api/users/{id}/disable — status + sessions + devices revoked
    in the SAME transaction (REQ-ORG-005, immediate effect)."""
    if user_id == principal.user_id:
        raise validation_failed("You cannot disable your own account.")
    user = _get_user(conn, user_id)
    _require_in_scope(conn, principal, user)

    if user["status"] != "disabled":
        conn.execute(
            text(
                "UPDATE users SET status = 'disabled', updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "UPDATE sessions SET status = 'revoked', updated_at = now() "
                "WHERE user_id = CAST(:id AS uuid) AND status = 'active'"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "UPDATE devices SET status = 'revoked', updated_at = now() "
                "WHERE user_id = CAST(:id AS uuid) "
                "AND status IN ('pending', 'active')"
            ),
            {"id": user_id},
        )
        audit.emit(
            conn, tenant_id=principal.tenant_id,
            actor_role=top_role_key(principal.role_keys),
            action="user.disabled", actor_id=principal.user_id,
            target_type="user", target_id=user_id,
            payload={"email": user["email"]},
        )

    return {"user_id": user_id, "status": "disabled"}

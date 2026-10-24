"""Shared agents with explicit grants (REQ-AGT-001…006, D-GOV-13).

Publishing creates NO grants. Grants are explicit `invoke`/`view` rows;
`manage` (creating/revoking grants) is role-based: owner, dept_admin of
the agent's department, or org_admin. Users without a grant get 404 (no
existence oracle); view-without-invoke gets 403 on invoke.
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import not_found, permission_denied, validation_failed


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _get_agent(conn: Connection, agent_id: str) -> dict:
    row = conn.execute(
        text("SELECT * FROM agents WHERE id = CAST(:a AS uuid) AND status = 'active'"),
        {"a": agent_id},
    ).mappings().first()
    if row is None:
        raise not_found("Agent not found.")
    return dict(row)


def _can_manage(conn: Connection, principal: Principal, agent: dict) -> bool:
    if str(agent["owner_user_id"]) == principal.user_id:
        return True
    if "org_admin" in principal.role_keys:
        return True
    if "dept_admin" in principal.role_keys:
        scoped = [ra.department_id for ra in principal.roles
                  if ra.role_key == "dept_admin"]
        return agent["department_id"] and str(agent["department_id"]) in scoped
    return False


def _grant_permission(conn: Connection, principal: Principal, agent: dict) -> str | None:
    """Active grant permission for the principal on the agent (or 'own')."""
    if str(agent["owner_user_id"]) == principal.user_id:
        return "own"
    row = conn.execute(
        text(
            "SELECT permission FROM agent_grants "
            "WHERE agent_id = CAST(:a AS uuid) AND grantee_user_id = CAST(:u AS uuid) "
            "AND revoked_at IS NULL"
        ),
        {"a": str(agent["id"]), "u": principal.user_id},
    ).mappings().first()
    return row["permission"] if row else None


def publish(
    conn: Connection, principal: Principal, *,
    name: str, description: str | None, scope: str,
) -> dict:
    if scope not in ("dept", "org"):
        raise validation_failed("scope must be 'dept' or 'org'.")
    if not principal.department_id:
        raise validation_failed("Publishing a shared agent requires a department.")
    agent_id = str(_uuid.uuid4())
    try:
        conn.execute(
            text(
                "INSERT INTO agents (id, tenant_id, owner_user_id, "
                "department_id, name, description, scope) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:o AS uuid), "
                "CAST(:d AS uuid), :name, :desc, :scope)"
            ),
            {
                "id": agent_id, "t": principal.tenant_id,
                "o": principal.user_id, "d": principal.department_id,
                "name": name, "desc": description, "scope": scope,
            },
        )
    except Exception as exc:
        if "agents_owner_user_id_name_key" in str(exc) or "duplicate key" in str(exc):
            raise validation_failed("You already have a shared agent with that name.")
        raise
    # REQ-AGT-001: published WITHOUT any grant.
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="agent.shared", actor_id=principal.user_id,
        target_type="agent", target_id=agent_id,
        payload={"name": name, "scope": scope},
    )
    return {"id": agent_id, "name": name, "scope": scope,
            "owner_user_id": principal.user_id,
            "department_id": principal.department_id}


def grant(
    conn: Connection, principal: Principal, *,
    agent_id: str, grantee_user_id: str, permission: str,
) -> dict:
    agent = _get_agent(conn, agent_id)
    if not _can_manage(conn, principal, agent):
        raise permission_denied("Only the owner, the department admin or an "
                                "organization admin manages grants.")
    if permission not in ("invoke", "view"):
        raise validation_failed("Grant permission must be 'invoke' or 'view' "
                                "(manage is role-based, REQ-AGT-002).")
    if grantee_user_id == str(agent["owner_user_id"]):
        raise validation_failed("The owner does not need a grant.")
    grantee = conn.execute(
        text("SELECT id, status FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": grantee_user_id},
    ).mappings().first()
    if grantee is None:
        raise not_found("Grantee user not found.")
    if grantee["status"] != "active":
        raise validation_failed("Grantee user is not active.")

    existing = conn.execute(
        text(
            "SELECT id, revoked_at FROM agent_grants "
            "WHERE agent_id = CAST(:a AS uuid) AND grantee_user_id = CAST(:u AS uuid) "
            "AND permission = :p"
        ),
        {"a": agent_id, "u": grantee_user_id, "p": permission},
    ).mappings().first()
    if existing is not None and existing["revoked_at"] is None:
        raise validation_failed("The grant already exists.")
    if existing is not None:
        conn.execute(
            text("UPDATE agent_grants SET revoked_at = NULL, granted_at = now(), "
                 "granted_by = CAST(:g AS uuid) WHERE id = CAST(:id AS uuid)"),
            {"g": principal.user_id, "id": str(existing["id"])},
        )
        grant_id = str(existing["id"])
    else:
        inserted = conn.execute(
            text(
                "INSERT INTO agent_grants (id, tenant_id, agent_id, "
                "grantee_user_id, permission, granted_by) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:a AS uuid), "
                "CAST(:u AS uuid), :p, CAST(:g AS uuid)) RETURNING id"
            ),
            {
                "id": str(_uuid.uuid4()), "t": principal.tenant_id,
                "a": agent_id, "u": grantee_user_id, "p": permission,
                "g": principal.user_id,
            },
        ).mappings().first()
        grant_id = str(inserted["id"])

    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="agent.granted", actor_id=principal.user_id,
        target_type="agent", target_id=agent_id,
        payload={"grantee_user_id": grantee_user_id, "permission": permission},
    )
    return {"id": grant_id, "agent_id": agent_id,
            "grantee_user_id": grantee_user_id, "permission": permission}


def list_grants(
    conn: Connection, principal: Principal, *, agent_id: str,
) -> list[dict]:
    """GET /api/agents/{id}/grants (spec 012, REQ-ORG-120): active grants
    for agents the principal can MANAGE (same gate as grant/revoke —
    anyone else gets 404, no existence oracle). Enables revocation without
    knowing the grant_id a priori."""
    agent = _get_agent(conn, agent_id)
    if not _can_manage(conn, principal, agent):
        raise not_found("Agent not found.")
    rows = conn.execute(
        text(
            "SELECT id, grantee_user_id, permission, granted_at, granted_by "
            "FROM agent_grants WHERE agent_id = CAST(:a AS uuid) "
            "AND revoked_at IS NULL ORDER BY granted_at"
        ),
        {"a": agent_id},
    ).mappings()
    return [{
        "id": str(r["id"]),
        "grantee_user_id": str(r["grantee_user_id"]),
        "permission": r["permission"],
        "granted_at": r["granted_at"].isoformat() if r["granted_at"] else None,
        "granted_by": str(r["granted_by"]) if r["granted_by"] else None,
    } for r in rows]


def revoke_grant(
    conn: Connection, principal: Principal, *,
    agent_id: str, grant_id: str,
) -> None:
    agent = _get_agent(conn, agent_id)
    if not _can_manage(conn, principal, agent):
        raise permission_denied("Only the owner, the department admin or an "
                                "organization admin manages grants.")
    row = conn.execute(
        text(
            "SELECT id, grantee_user_id, permission, revoked_at "
            "FROM agent_grants WHERE id = CAST(:g AS uuid) "
            "AND agent_id = CAST(:a AS uuid)"
        ),
        {"g": grant_id, "a": agent_id},
    ).mappings().first()
    if row is None:
        raise not_found("Grant not found.")
    if row["revoked_at"] is not None:
        return
    conn.execute(
        text("UPDATE agent_grants SET revoked_at = now() "
             "WHERE id = CAST(:g AS uuid)"),
        {"g": grant_id},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="agent.revoked", actor_id=principal.user_id,
        target_type="agent", target_id=agent_id,
        payload={"grant_id": grant_id,
                 "grantee_user_id": str(row["grantee_user_id"]),
                 "permission": row["permission"]},
    )


def visible_agent(
    conn: Connection, principal: Principal, agent_id: str
) -> tuple[dict, str]:
    """(agent, access) for a principal or 404 (REQ-AGT-003)."""
    try:
        agent = _get_agent(conn, agent_id)
    except Exception:
        raise not_found("Agent not found.")
    perm = _grant_permission(conn, principal, agent)
    if perm is not None:
        return agent, perm
    if _can_manage(conn, principal, agent):
        return agent, "manage"
    # No grant, not admin of scope: existence is not revealed.
    raise not_found("Agent not found.")


def list_for_user(conn: Connection, principal: Principal) -> list[dict]:
    """Desktop list (REQ-AGT-006): own agents + agents with a live grant."""
    rows = conn.execute(
        text(
            "SELECT a.*, "
            "CASE WHEN a.owner_user_id = CAST(:u AS uuid) THEN 'own' "
            "     ELSE (SELECT permission FROM agent_grants g "
            "           WHERE g.agent_id = a.id AND g.grantee_user_id = CAST(:u AS uuid) "
            "           AND g.revoked_at IS NULL LIMIT 1) END AS access "
            "FROM agents a WHERE a.status = 'active' "
            "AND (a.owner_user_id = CAST(:u AS uuid) OR EXISTS ("
            "  SELECT 1 FROM agent_grants g WHERE g.agent_id = a.id "
            "  AND g.grantee_user_id = CAST(:u AS uuid) AND g.revoked_at IS NULL)) "
            "ORDER BY a.created_at DESC"
        ),
        {"u": principal.user_id},
    ).mappings()
    out = []
    for r in rows:
        out.append({
            "id": str(r["id"]), "name": r["name"],
            "description": r["description"], "scope": r["scope"],
            "owner_user_id": str(r["owner_user_id"]),
            "department_id": str(r["department_id"]) if r["department_id"] else None,
            "access": r["access"],
        })
    return out


def invoke_gate(
    conn: Connection, principal: Principal, agent_id: str
) -> dict:
    """POST /api/agents/{id}/invoke: authorize + audit (REQ-AGT-004/005).

    Model consumption is billed to the INVOKER because the desktop calls
    the gateway with the invoker's virtual key.
    """
    agent, access = visible_agent(conn, principal, agent_id)
    if access not in ("own", "invoke"):
        audit.emit(
            conn, tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            action="agent.invoke_denied", actor_id=principal.user_id,
            target_type="agent", target_id=agent_id,
            payload={"access": access},
        )
        # Commit the denial audit before raising: the business transaction
        # rolls back on the error response (same pattern as D-CP-04).
        conn.commit()
        raise permission_denied(
            "You have a view grant but not an invoke grant for this agent."
            if access == "view"
            else "You cannot invoke this agent."
        )
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="agent.invoked", actor_id=principal.user_id,
        target_type="agent", target_id=agent_id,
        payload={"billed_to_user_id": principal.user_id},
    )
    return {
        "agent_id": agent_id,
        "billed_to_user_id": principal.user_id,
        "access": access,
    }

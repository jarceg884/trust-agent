"""Governance notification inbox (D-GOV-08, REQ-GOV-050).

Cutoffs and threshold crossings write real inbox rows; users list and
mark their own notifications only (RLS + explicit user filter).
"""
import json
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection


def notify(
    conn: Connection, *, tenant_id: str, user_id: str,
    type: str, payload: dict | None = None,
) -> str:
    """Insert one inbox notification (idempotency is the caller's job —
    see spend.enforce_tenant for the threshold dedupe)."""
    nid = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO notifications (id, tenant_id, user_id, type, payload) "
            "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:u AS uuid), "
            ":type, CAST(:p AS jsonb))"
        ),
        {"id": nid, "t": tenant_id, "u": user_id, "type": type,
         "p": json.dumps(payload or {})},
    )
    return nid


def notify_role(
    conn: Connection, *, tenant_id: str, role_key: str,
    type: str, payload: dict | None = None,
) -> int:
    """Notify every user holding `role_key` in the tenant (org_admin, or
    dept_admin scoped to a department)."""
    rows = conn.execute(
        text(
            "SELECT DISTINCT user_id FROM user_roles "
            "WHERE role_key = :role "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid))"
        ),
        {"role": role_key, "dept": (payload or {}).get("department_id")},
    ).mappings()
    count = 0
    for r in rows:
        notify(conn, tenant_id=tenant_id, user_id=str(r["user_id"]),
               type=type, payload=payload)
        count += 1
    return count


def already_notified(
    conn: Connection, *, user_id: str, type: str, scope: str,
    period_start: str,
) -> bool:
    """Threshold dedupe: same (user, type, scope, window) notified once."""
    row = conn.execute(
        text(
            "SELECT 1 FROM notifications WHERE user_id = CAST(:u AS uuid) "
            "AND type = :type AND payload->>'scope' = :scope "
            "AND payload->>'period_start' = :ps LIMIT 1"
        ),
        {"u": user_id, "type": type, "scope": scope, "ps": period_start},
    ).mappings().first()
    return row is not None


def list_for_user(
    conn: Connection, *, user_id: str, unread_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT id, type, payload, read_at, created_at "
            "FROM notifications WHERE user_id = CAST(:u AS uuid) "
            "AND (:unread = false OR read_at IS NULL) "
            "ORDER BY created_at DESC LIMIT :lim"
        ),
        {"u": user_id, "unread": unread_only, "lim": limit},
    ).mappings()
    return [
        {
            "id": str(r["id"]), "type": r["type"], "payload": r["payload"],
            "read_at": r["read_at"].isoformat() if r["read_at"] else None,
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


def mark_read(conn: Connection, *, user_id: str, notification_id: str) -> bool:
    result = conn.execute(
        text(
            "UPDATE notifications SET read_at = now() "
            "WHERE id = CAST(:n AS uuid) AND user_id = CAST(:u AS uuid) "
            "AND read_at IS NULL"
        ),
        {"n": notification_id, "u": user_id},
    )
    return result.rowcount > 0

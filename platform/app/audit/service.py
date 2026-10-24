"""Audit: event emission + query API + chain verification (spec 008).

The hash chain (seq, hash_prev, hash) is computed by the schema's BEFORE
INSERT trigger under an advisory lock (spec 002, D-DB-07) — the app only
INSERTs. Verification delegates to audit_verify_chain(tenant).

Query scoping (D-AU-02): RLS already isolates the tenant; the role scope is
an additional application filter applied in the SAME tenant-scoped
transaction:
  - org_admin  -> whole tenant
  - dept_admin -> events whose actor belongs to their departments
  - user       -> only their own events (actor_id = principal)
Events emitted by system/provider (actor_id NULL) are org_admin-only.
"""
import csv
import io
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

EXPORT_ROW_CAP = 10_000
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def emit(
    conn: Connection,
    *,
    tenant_id: str,
    actor_role: str,
    action: str,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    device_id: str | None = None,
    cost_cents: int | None = None,
    payload: dict | None = None,
) -> None:
    """INSERT one audit event; the DB trigger assigns seq + hash chain."""
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_role, actor_id, action, "
            " target_type, target_id, device_id, cost_cents, payload) "
            "VALUES (CAST(:tenant_id AS uuid), :actor_role, CAST(:actor_id AS uuid), "
            " :action, :target_type, :target_id, CAST(:device_id AS uuid), "
            " :cost_cents, CAST(:payload AS jsonb))"
        ),
        {
            "tenant_id": str(tenant_id),
            "actor_role": actor_role,
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "device_id": device_id,
            "cost_cents": cost_cents,
            "payload": json.dumps(payload or {}),
        },
    )


def verify_chain(conn: Connection, tenant_id: str) -> dict:
    """-> {"status": "ok"} or {"status": "broken", "seq": N} (REQ-AUD-007)."""
    row = conn.execute(
        text("SELECT status, seq FROM audit_verify_chain(CAST(:t AS uuid))"),
        {"t": str(tenant_id)},
    ).mappings().first()
    if row is None:  # pragma: no cover - function always returns a row
        return {"status": "ok"}
    result = {"status": row["status"]}
    if row["seq"] is not None:
        result["seq"] = row["seq"]
    return result


def emit_verification(
    conn: Connection,
    *,
    tenant_id: str,
    result: dict,
    source: str,
) -> None:
    """Every verification run is itself audited (D-AU-05, REQ-AU-011)."""
    payload: dict[str, Any] = {"result": result["status"], "source": source}
    if result.get("seq") is not None:
        payload["broken_seq"] = result["seq"]
    emit(
        conn,
        tenant_id=tenant_id,
        actor_role="system",
        action="audit.verified",
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Query API (REQ-AU-001/002)
# ---------------------------------------------------------------------------

def _escape_like(value: str) -> str:
    """Treat user wildcards literally (D-AU-11)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def role_scope_sql(role_keys: list[str], departments: list[str] | None,
                   user_id: str) -> tuple[str, dict]:
    """WHERE fragment implementing the role scope (D-AU-02).

    `departments` follows rbac.dept_scope semantics: None = whole tenant
    (org_admin); a list = restrict to events whose actor belongs to those
    departments (dept_admin) or to the principal's own events (user).
    """
    if "org_admin" in role_keys:
        return "TRUE", {}
    if departments is None:  # pragma: no cover - rbac always returns a list
        return "TRUE", {}
    if "dept_admin" in role_keys:
        return "u.department_id = ANY(:scope_depts)", {"scope_depts": departments}
    return "a.actor_id = CAST(:scope_uid AS uuid)", {"scope_uid": str(user_id)}


def query_events(
    conn: Connection,
    *,
    role_keys: list[str],
    departments: list[str] | None,
    user_id: str,
    actor_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    device_id: str | None = None,
    department_id: str | None = None,
    since=None,
    until=None,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    """Filtered, paginated audit query within the caller's role scope.

    Returns {"items": [...], "total": N, "limit": limit, "offset": offset}.
    Runs inside the caller's tenant-scoped transaction (RLS isolates the
    tenant; the role scope is an extra filter — D-AU-02).
    """
    conditions, params = role_scope_sql(role_keys, departments, user_id)

    if actor_id is not None:
        conditions += " AND a.actor_id = CAST(:actor_id AS uuid)"
        params["actor_id"] = str(actor_id)
    if action is not None:
        conditions += " AND a.action = :action"
        params["action"] = action
    if target_type is not None:
        conditions += " AND a.target_type = :target_type"
        params["target_type"] = target_type
    if target_id is not None:
        conditions += " AND a.target_id = :target_id"
        params["target_id"] = target_id
    if device_id is not None:
        conditions += " AND a.device_id = CAST(:device_id AS uuid)"
        params["device_id"] = str(device_id)
    if department_id is not None:
        conditions += " AND u.department_id = CAST(:department_id AS uuid)"
        params["department_id"] = str(department_id)
    if since is not None:
        conditions += " AND a.ts >= CAST(:since AS timestamptz)"
        params["since"] = str(since)
    if until is not None:
        conditions += " AND a.ts < CAST(:until AS timestamptz)"
        params["until"] = str(until)
    if q:
        conditions += (
            " AND (a.action ILIKE :q OR a.target_type ILIKE :q "
            " OR a.target_id ILIKE :q OR a.actor_role ILIKE :q "
            " OR a.payload::text ILIKE :q)"
        )
        params["q"] = f"%{_escape_like(q)}%"

    base = (
        "FROM audit_log a "
        "LEFT JOIN users u ON u.tenant_id = a.tenant_id AND u.id = a.actor_id "
        f"WHERE {conditions}"
    )
    total = conn.execute(
        text(f"SELECT count(*) {base}"), params
    ).scalar_one()

    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    rows = conn.execute(
        text(
            "SELECT a.id, a.seq, a.ts, a.actor_role, a.actor_id, a.action, "
            " a.target_type, a.target_id, a.device_id, a.cost_cents, "
            " a.payload, a.hash_prev, a.hash "
            f"{base} ORDER BY a.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()

    items = [_row_to_item(r) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _row_to_item(row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "ts": row["ts"].isoformat() if row["ts"] is not None else None,
        "actor_role": row["actor_role"],
        "actor_id": str(row["actor_id"]) if row["actor_id"] is not None else None,
        "action": row["action"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "device_id": str(row["device_id"]) if row["device_id"] is not None else None,
        "cost_cents": row["cost_cents"],
        "payload": row["payload"],
        "hash_prev": row["hash_prev"],
        "hash": row["hash"],
    }


EXPORT_COLUMNS = [
    "id", "seq", "ts", "actor_role", "actor_id", "action", "target_type",
    "target_id", "device_id", "cost_cents", "payload", "hash_prev", "hash",
]


def export_rows(conn: Connection, **filters) -> list[dict]:
    """Unpaginated query (capped at EXPORT_ROW_CAP) for CSV/JSON export."""
    result = query_events(
        conn, limit=EXPORT_ROW_CAP, offset=0, **filters
    )
    return result["items"]


def rows_to_csv(items: list[dict]) -> str:
    """CSV with fixed columns, hashes included (D-AU-09, REQ-AU-005)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        row = dict(item)
        if isinstance(row.get("payload"), dict):
            row["payload"] = json.dumps(row["payload"], sort_keys=True)
        writer.writerow(row)
    return buffer.getvalue()

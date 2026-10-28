"""Audit del plano proveedor (spec 025 D-PS-07): ``ops.provider_audit``.

Append-only con hash chain GLOBAL — el trigger ``provider_audit_chain_trigger``
(migración 0016) asigna ``seq``/``hash_prev``/``hash`` bajo advisory lock,
igual que ``audit_log`` pero con una sola secuencia (no hay partición por
tenant). El trigger de inmutabilidad rechaza UPDATE/DELETE a nivel BD.

Los eventos del operador no pueden vivir en ``audit_log``: esa tabla exige
``tenant_id NOT NULL`` + FK a ``tenants`` y su cadena es por tenant. Los
eventos SOBRE tenants (p.ej. ``tenant.suspended``) siguen en el audit_log
del tenant afectado, como siempre.
"""
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection


def emit(
    conn: Connection,
    *,
    action: str,
    operator_id: str | None = None,
    username: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    payload: dict | None = None,
) -> None:
    """INSERT un evento del operador; el trigger asigna seq + cadena."""
    conn.execute(
        text(
            "INSERT INTO ops.provider_audit "
            " (action, operator_id, username, ip, user_agent, payload) "
            "VALUES (:action, CAST(:operator_id AS uuid), :username, "
            " CAST(:ip AS inet), :user_agent, CAST(:payload AS jsonb))"
        ),
        {
            "action": action,
            "operator_id": operator_id,
            "username": username,
            "ip": ip,
            "user_agent": user_agent,
            "payload": json.dumps(payload or {}),
        },
    )


def verify_chain(conn: Connection) -> dict:
    """-> {"status": "ok"} | {"status": "broken", "seq": N} (REQ-PS-011)."""
    row = conn.execute(
        text("SELECT status, seq FROM ops.provider_audit_verify_chain()")
    ).mappings().first()
    if row is None:  # pragma: no cover - la función siempre devuelve fila
        return {"status": "ok"}
    result = {"status": row["status"]}
    if row["seq"] is not None:
        result["seq"] = row["seq"]
    return result

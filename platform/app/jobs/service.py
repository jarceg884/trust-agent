"""Async jobs in PostgreSQL (C2: zero in-process state, REQ-ORG-002).

Enqueue is tenant-scoped (trust_app + set_config). The CLAIM is cross-tenant:
RLS hides the queue from trust_app, so the worker claims through a dedicated
trust_migrator connection used ONLY for the claim statement (D-CP-10); the
job itself always executes as trust_app inside the tenant's scope.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from ..db import tenant_tx
from ..errors import not_found

CLAIM_SQL = text(
    "UPDATE jobs SET status = 'running', started_at = now(), "
    "attempts = attempts + 1, updated_at = now() "
    "WHERE id = (SELECT id FROM jobs WHERE status = 'pending' "
    "            AND run_after <= now() ORDER BY created_at "
    "            LIMIT 1 FOR UPDATE SKIP LOCKED) "
    "RETURNING id, tenant_id, job_type, payload, attempts, max_attempts"
)


def enqueue(
    engine: Engine | None,
    *,
    tenant_id: str,
    job_type: str,
    payload: dict | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    run_after: datetime | None = None,
    actor_id: str | None = None,
    conn: Connection | None = None,
) -> dict:
    """Enqueue a job; idempotent per (tenant, job_type, idempotency_key).

    A NEW job emits audit event job.created (spec 008, REQ-AU-023); an
    idempotent hit returns the existing row without re-emitting.

    With `conn` the insert rides the CALLER's tenant-scoped transaction
    (spec 012 D-ORG-02): the audit chain's advisory lock is held until
    that transaction commits, so opening a second connection here would
    self-deadlock on job.created — and sharing the transaction makes the
    invite atomic (no dangling job if the request rolls back).
    """
    from ..audit import service as audit_service

    def _run(c: Connection) -> dict:
        if idempotency_key is not None:
            existing = c.execute(
                text(
                    "SELECT * FROM jobs WHERE job_type = :t "
                    "AND idempotency_key = :k"
                ),
                {"t": job_type, "k": idempotency_key},
            ).mappings().first()
            if existing is not None:
                return dict(existing)
        row = c.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, job_type, payload, "
                " max_attempts, idempotency_key, run_after) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), :type, "
                " CAST(:payload AS jsonb), :max_attempts, :idem, :run_after) "
                "RETURNING *"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "type": job_type,
                "payload": json.dumps(payload or {}),
                "max_attempts": max_attempts,
                "idem": idempotency_key,
                "run_after": run_after or datetime.now(timezone.utc),
            },
        ).mappings().first()
        audit_service.emit(
            c,
            tenant_id=str(tenant_id),
            actor_role="system",
            actor_id=actor_id,
            action="job.created",
            target_type="job",
            target_id=str(row["id"]),
            payload={
                "job_type": job_type,
                **({"idempotency_key": idempotency_key}
                   if idempotency_key is not None else {}),
            },
        )
        return dict(row)

    if conn is not None:
        return _run(conn)
    with tenant_tx(engine, tenant_id) as c:
        return _run(c)


def claim_next(migrator_engine: Engine) -> dict | None:
    """Claim the next pending job (cross-tenant, SKIP LOCKED, no double claim)."""
    with migrator_engine.begin() as conn:
        row = conn.execute(CLAIM_SQL).mappings().first()
        return dict(row) if row else None


def complete(
    engine: Engine, *, tenant_id: str, job_id: str, result: dict | None
) -> None:
    from ..audit import service as audit_service

    with tenant_tx(engine, tenant_id) as conn:
        conn.execute(
            text(
                "UPDATE jobs SET status = 'succeeded', "
                "result = CAST(:result AS jsonb), finished_at = now(), "
                "updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"result": json.dumps(result or {}), "id": job_id},
        )
        audit_service.emit(
            conn,
            tenant_id=str(tenant_id),
            actor_role="system",
            action="job.completed",
            target_type="job",
            target_id=job_id,
            payload={"status": "succeeded"},
        )


def fail(
    engine: Engine, *, tenant_id: str, job_id: str, error: str,
    max_attempts: int, retry_delay_seconds: int = 5,
) -> None:
    """Mark failed for good, or re-queue while attempts remain."""
    with tenant_tx(engine, tenant_id) as conn:
        row = conn.execute(
            text("SELECT attempts FROM jobs WHERE id = CAST(:id AS uuid)"),
            {"id": job_id},
        ).mappings().first()
        if row is None:
            raise not_found("Job not found.")
        retry = row["attempts"] < max_attempts
        conn.execute(
            text(
                "UPDATE jobs SET status = :status, error = :error, "
                "run_after = now() + make_interval(secs => :delay), "
                "finished_at = "
                "CASE WHEN :status = 'failed' THEN now() ELSE finished_at END, "
                "updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {
                "status": "pending" if retry else "failed",
                "error": error[:2000],
                "delay": retry_delay_seconds if retry else 0,
                "id": job_id,
            },
        )


def get_job(conn: Connection, job_id: str) -> dict:
    """Fetch a job within the current tenant scope (cross-tenant -> 404)."""
    row = conn.execute(
        text("SELECT * FROM jobs WHERE id = CAST(:id AS uuid)"),
        {"id": job_id},
    ).mappings().first()
    if row is None:
        raise not_found("Job not found.")
    return dict(row)


Handler = Callable[[Connection, dict], dict]
_HANDLERS: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    _HANDLERS[job_type] = handler


def _echo_handler(conn: Connection, job: dict) -> dict:
    """Built-in smoke handler for the core (real handlers arrive with 004/005)."""
    return {"echo": job["payload"], "job_type": job["job_type"]}


register_handler("echo", _echo_handler)


def build_migrator_engine(migrator_url: str) -> Engine:
    """Dedicated claim connection (trust_migrator, BYPASSRLS — claim only)."""
    return create_engine(
        migrator_url,
        pool_pre_ping=True,
        connect_args={"application_name": "trust-job-worker-claim"},
    )

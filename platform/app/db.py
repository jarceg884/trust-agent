"""Database access — engine as trust_app and tenant-scoped transactions.

The golden rule (spec 002 / skill postgresql-rls-multitenant): every business
query runs inside a transaction whose FIRST statement is

    SELECT set_config('app.tenant_id', '<uuid>', true)   -- == SET LOCAL

`is_local=true` makes the setting die with the transaction, so a pooled
connection can never leak one tenant's scope into another request. Without
the setting, RLS is fail-closed: 0 rows.
"""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .config import Settings, get_settings


def build_engine(settings: Settings | None = None) -> Engine:
    """App engine (trust_app, subject to RLS).

    Every pooled connection carries `lock_timeout` (QA H3, t_82b2ffaa):
    a statement stuck waiting on a lock — e.g. the per-tenant
    pg_advisory_xact_lock of the audit_log chain trigger held by a stuck
    writer — aborts with SQLSTATE 55P03 after the timeout instead of
    hanging forever. The transaction rolls back, the request fails
    loudly (503) and the audit chain stays consistent (the INSERT never
    happened). See Settings.database_lock_timeout.
    """
    settings = settings or get_settings()
    connect_args = {"application_name": "trust-control-plane"}
    if settings.database_lock_timeout:
        connect_args["options"] = (
            f"-c lock_timeout={settings.database_lock_timeout}"
        )
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args=connect_args,
    )


_engine: Engine | None = None
_migrator_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_migrator_engine() -> Engine:
    """Cached trust_migrator engine for provider-side cross-tenant lookups
    (spec 004, D-ONB-10 — same pattern as the job claim connection)."""
    global _migrator_engine
    if _migrator_engine is None:
        from .errors import service_unavailable

        settings = get_settings()
        if not settings.database_migrator_url:
            raise service_unavailable(
                "TRUST_DATABASE_MIGRATOR_URL is not configured; provider "
                "operations need the migrator connection (fail-closed)."
            )
        _migrator_engine = create_engine(
            settings.database_migrator_url,
            pool_pre_ping=True,
            connect_args={"application_name": "trust-control-plane-migrator"},
        )
    return _migrator_engine


def reset_engine() -> None:
    """Dispose the cached engines (used by tests to simulate a restart)."""
    global _engine, _migrator_engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    if _migrator_engine is not None:
        _migrator_engine.dispose()
    _migrator_engine = None


def set_tenant(conn: Connection, tenant_id: str) -> None:
    conn.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


@contextmanager
def tenant_tx(engine: Engine, tenant_id: str) -> Iterator[Connection]:
    """One transaction scoped to a tenant: BEGIN; SET LOCAL app.tenant_id; ...; COMMIT."""
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        yield conn


@contextmanager
def plain_tx(engine: Engine) -> Iterator[Connection]:
    """Transaction without tenant scope — RLS fail-closed (0 rows on business tables).

    Only for cross-tenant operational reads that live OUTSIDE RLS
    (e.g. ops.spend_cursor) — never for business tables.
    """
    with engine.begin() as conn:
        yield conn

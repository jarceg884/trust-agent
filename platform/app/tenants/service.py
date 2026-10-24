"""Tenant resolution helpers (provisioning itself is spec 004)."""
from sqlalchemy import text
from sqlalchemy.engine import Connection


def tenant_status(conn: Connection) -> dict | None:
    """The tenant row within the current scope (RLS: only ours)."""
    row = conn.execute(
        text("SELECT id, slug, name, status, oidc_realm, oidc_issuer_url "
             "FROM tenants")
    ).mappings().first()
    return dict(row) if row else None

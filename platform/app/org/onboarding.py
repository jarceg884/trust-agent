"""Organization structure onboarding (spec 038, D-38-02/06).

A per-tenant flag `tenants.structure_onboarding_pending` starts PENDING for
every tenant (new and pre-existing — the migration backfills it with
DEFAULT true), so each org_admin is offered the "Organization structure"
wizard exactly once on their first login with the tenant active. The wizard
itself performs NO new mutations: it travels the existing department CRUD
(spec 012/016) and this module only (1) exposes the flag and (2) closes the
onboarding, auditing `onboarding.structure_completed` with the counts of
what the wizard did — the audit fires ONLY on the pending -> completed
transition (a repeated PUT is an idempotent no-op).
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import top_role_key
from ..auth.service import Principal


def get_state(conn: Connection) -> dict:
    """GET /api/org/onboarding — {structure_onboarding_pending: bool}.

    Read inside the caller's tenant scope (RLS isolates; the tenants row is
    visible to every principal of the tenant). The ROUTE enforces
    `departments.manage` (REQ-DO-040).
    """
    row = conn.execute(
        text("SELECT structure_onboarding_pending FROM tenants")
    ).mappings().first()
    return {
        "structure_onboarding_pending": bool(row["structure_onboarding_pending"])
        if row else True
    }


def complete(
    conn: Connection,
    principal: Principal,
    *,
    source: str,
    created: int = 0,
    renamed: int = 0,
    deleted: int = 0,
) -> dict:
    """PUT /api/org/onboarding — close the structure onboarding.

    `source` is "wizard" (changes confirmed) or "skip" (closed without
    mutating anything). The flag flip and the audit event ride the caller's
    transaction; the audit is emitted ONLY when the flag actually flips
    (pending -> completed) so a double-submit cannot duplicate the event.
    """
    flipped = conn.execute(
        text(
            "UPDATE tenants SET structure_onboarding_pending = false, "
            " updated_at = now() "
            "WHERE structure_onboarding_pending "
            "RETURNING id"
        )
    ).first() is not None
    if flipped:
        audit.emit(
            conn, tenant_id=principal.tenant_id,
            actor_role=top_role_key(principal.role_keys),
            actor_id=principal.user_id,
            action="onboarding.structure_completed",
            target_type="tenant", target_id=principal.tenant_id,
            payload={
                "source": source,
                "created": created,
                "renamed": renamed,
                "deleted": deleted,
            },
        )
    return {
        "structure_onboarding_pending": False,
        "completed_now": flipped,
        "source": source,
    }

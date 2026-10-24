"""Model access governance (REQ-MDL-001/005/006, D-GOV-11).

Approvals live in `model_access` at three levels (org/dept/user). The
EFFECTIVE set for a user is the intersection of the levels that define
rows for her, further narrowed by the effective policy `allowed_models`
when non-empty. Subset rule: a dept/user approval requires the org
approval of the same model (422 otherwise).
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import model_not_approved, not_found, validation_failed


def _model_id(conn: Connection, model_name: str) -> str | None:
    row = conn.execute(
        text("SELECT id FROM catalog.model_catalog WHERE model_name = :m"),
        {"m": model_name},
    ).mappings().first()
    return str(row["id"]) if row else None


def approve(
    conn: Connection,
    principal: Principal,
    *,
    model_name: str,
    level: str,
    department_id: str | None,
    user_id: str | None,
    mode: str,
) -> dict:
    """Approve a model for a level (subset rule enforced)."""
    if level not in ("org", "dept", "user"):
        raise validation_failed("level must be org, dept or user.")
    if mode not in ("central", "byok"):
        raise validation_failed("mode must be 'central' or 'byok'.")
    model_id = _model_id(conn, model_name)
    if model_id is None:
        raise not_found("Model not found in the global catalog.")

    if level in ("dept", "user"):
        org_row = conn.execute(
            text(
                "SELECT id FROM model_access WHERE level = 'org' "
                "AND model_id = CAST(:m AS uuid) AND status = 'approved'"
            ),
            {"m": model_id},
        ).mappings().first()
        if org_row is None:
            raise validation_failed(
                f"Subset rule violated: '{model_name}' is not approved at "
                "organization level (REQ-MDL-005)."
            )

    existing = conn.execute(
        text(
            "SELECT id FROM model_access WHERE model_id = CAST(:m AS uuid) "
            "AND level = :level "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid))"
        ),
        {
            "m": model_id, "level": level,
            "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings().first()

    if existing is not None:
        conn.execute(
            text(
                "UPDATE model_access SET status = 'approved', mode = :mode, "
                "updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"mode": mode, "id": str(existing["id"])},
        )
        row_id = str(existing["id"])
    else:
        inserted = conn.execute(
            text(
                "INSERT INTO model_access (tenant_id, model_id, level, "
                "department_id, user_id, mode, status) "
                "VALUES (CAST(:t AS uuid), CAST(:m AS uuid), :level, "
                "CAST(:dept AS uuid), CAST(:usr AS uuid), :mode, 'approved') "
                "RETURNING id"
            ),
            {
                "t": principal.tenant_id, "m": model_id, "level": level,
                "dept": department_id, "usr": user_id, "mode": mode,
            },
        ).mappings().first()
        row_id = str(inserted["id"])

    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="model.approved", actor_id=principal.user_id,
        target_type="model", target_id=model_name,
        payload={"level": level, "mode": mode,
                 "department_id": department_id, "user_id": user_id},
    )
    return {"id": row_id, "model_name": model_name, "level": level,
            "mode": mode, "status": "approved"}


def revoke(
    conn: Connection,
    principal: Principal,
    *,
    model_name: str,
    level: str,
    department_id: str | None,
    user_id: str | None,
) -> dict:
    """Revoke an approval (denied). The effective set is an intersection, so
    revoking at org makes the model ineffective for everyone below."""
    model_id = _model_id(conn, model_name)
    if model_id is None:
        raise not_found("Model not found in the global catalog.")
    row = conn.execute(
        text(
            "SELECT id FROM model_access WHERE model_id = CAST(:m AS uuid) "
            "AND level = :level AND status = 'approved' "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid))"
        ),
        {
            "m": model_id, "level": level,
            "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings().first()
    if row is None:
        raise not_found("Approval not found at this level.")
    conn.execute(
        text("UPDATE model_access SET status = 'denied', updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": str(row["id"])},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="model.revoked", actor_id=principal.user_id,
        target_type="model", target_id=model_name,
        payload={"level": level, "department_id": department_id,
                 "user_id": user_id},
    )
    return {"model_name": model_name, "level": level, "status": "denied"}


def _approved_model_names(
    conn: Connection, *, level: str,
    department_id: str | None = None,
    user_id: str | None = None,
) -> set[str]:
    """Approved model names at one level for one scope (empty = no rows)."""
    rows = conn.execute(
        text(
            "SELECT mc.model_name FROM model_access ma "
            "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
            "WHERE ma.level = :level AND ma.status = 'approved' "
            "AND (CAST(:dept AS uuid) IS NULL OR ma.department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR ma.user_id = CAST(:usr AS uuid))"
        ),
        {"level": level, "dept": department_id, "usr": user_id},
    ).mappings()
    return {r["model_name"] for r in rows}


def effective_models_for_user(
    conn: Connection, principal: Principal
) -> list[str]:
    """Org ∩ (dept rows? dept) ∩ (user rows? user) ∩ policy allowed_models."""
    org = _approved_model_names(conn, level="org")
    effective = org
    if principal.department_id:
        dept = _approved_model_names(
            conn, level="dept", department_id=principal.department_id
        )
        if dept:
            effective &= dept
    user = _approved_model_names(
        conn, level="user",
        department_id=principal.department_id, user_id=principal.user_id,
    )
    if user:
        effective &= user

    from ..governance.service import effective_policy_for_user

    policy = effective_policy_for_user(conn, principal)
    allowed_by_policy = policy.get("allowed_models") or []
    if allowed_by_policy:
        effective &= set(allowed_by_policy)
    return sorted(effective)


def assert_model_allowed(
    conn: Connection, principal: Principal, model_name: str
) -> None:
    """Governance gate: 403 model_not_approved + audit when not effective.

    The audit event MUST survive the error response — the business
    transaction rolls back on the raise, so the INSERT is committed
    explicitly first (same pattern as refresh-reuse detection, D-CP-04).
    """
    if model_name in effective_models_for_user(conn, principal):
        return
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        action="model.access_denied", actor_id=principal.user_id,
        target_type="model", target_id=model_name,
    )
    conn.commit()
    raise model_not_approved(
        f"Model '{model_name}' is not approved for your scope."
    )


def resolve_mode(
    conn: Connection, *, tenant_id: str, model_name: str,
    user_id: str | None, department_id: str | None,
) -> str:
    """Mode for a (user, model): user → dept → org; default central.

    Used by the spend ingest to stamp spend_ledger.mode (D-GOV-06).
    """
    for level, dept, usr in (
        ("user", department_id, user_id),
        ("dept", department_id, None),
        ("org", None, None),
    ):
        row = conn.execute(
            text(
                "SELECT ma.mode FROM model_access ma "
                "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
                "WHERE ma.level = :level AND ma.status = 'approved' "
                "AND mc.model_name = :model "
                "AND (CAST(:dept AS uuid) IS NULL OR ma.department_id = CAST(:dept AS uuid)) "
                "AND (CAST(:usr AS uuid) IS NULL OR ma.user_id = CAST(:usr AS uuid)) "
                "LIMIT 1"
            ),
            {"level": level, "model": model_name, "dept": dept, "usr": usr},
        ).mappings().first()
        if row is not None:
            return row["mode"]
    return "central"


def access_overview(
    conn: Connection, *, department_id: str | None = None
) -> dict:
    """Catalog with per-level approval status for the tenant (REQ-MDL-006)."""
    rows = conn.execute(
        text(
            "SELECT mc.model_name, mc.display_name, mc.provider, "
            "mc.input_price_cents_per_m, mc.output_price_cents_per_m, "
            "mc.status AS catalog_status "
            "FROM catalog.model_catalog mc ORDER BY mc.model_name"
        )
    ).mappings()
    approvals = conn.execute(
        text(
            "SELECT ma.level, ma.department_id, ma.user_id, ma.mode, "
            "mc.model_name FROM model_access ma "
            "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
            "WHERE ma.status = 'approved' "
            "AND (CAST(:dept AS uuid) IS NULL OR ma.department_id = CAST(:dept AS uuid) "
            "     OR ma.department_id IS NULL) "
            "ORDER BY mc.model_name, ma.level"
        ),
        {"dept": department_id},
    ).mappings()

    by_model: dict[str, list[dict]] = {}
    for r in approvals:
        by_model.setdefault(r["model_name"], []).append({
            "level": r["level"],
            "department_id": str(r["department_id"]) if r["department_id"] else None,
            "user_id": str(r["user_id"]) if r["user_id"] else None,
            "mode": r["mode"],
        })
    models = []
    for r in rows:
        models.append({
            "model_name": r["model_name"],
            "display_name": r["display_name"],
            "provider": r["provider"],
            "input_price_cents_per_m": r["input_price_cents_per_m"],
            "output_price_cents_per_m": r["output_price_cents_per_m"],
            "catalog_status": r["catalog_status"],
            "approvals": by_model.get(r["model_name"], []),
        })
    return {"models": models}


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"

"""Model access governance (REQ-MDL-001/005/006, D-GOV-11).

Approvals live in `model_access` at three levels (org/dept/user). The
EFFECTIVE set for a user is the intersection of the levels that define
rows for her (a level with rows but zero APPROVED rows denies everything
at that level — a revoke must never relax), further narrowed by the
effective policy `allowed_models` when DEFINED at any level (an explicit
empty list is a deny-all; a null/absent key adds no restriction —
t_f0d3122d, spec delta 003/005/018). Subset rule: a dept/user approval
requires the org approval of the same model (422 otherwise).
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


def _resolve_scope(
    conn: Connection,
    tenant_id: str,
    *,
    level: str,
    department_id: str | None,
    user_id: str | None,
) -> tuple[str | None, str | None]:
    """Validate the (level, department_id, user_id) triple against the
    model_access_scope_check contract BEFORE touching the table — a raw
    violation surfaced as an opaque 500 (GAP M-1, D-018-06).

    level='user': the department is the user's REAL department, resolved
    from users (tenant-scoped). The body's department_id is optional and
    only cross-checked: an approval anchored to a wrong department would
    be silently ineffective (the effective-set intersection queries by
    the principal's department). A user without a department cannot have
    a user-level row (the DB constraint requires one) → 422; constrain
    her via policy allowed_models instead.
    """
    if level == "org":
        if department_id is not None or user_id is not None:
            raise validation_failed(
                "Org level is tenant-wide: department_id and user_id "
                "must be null."
            )
        return None, None
    if level == "dept":
        if user_id is not None:
            raise validation_failed(
                "Department level cannot carry a user_id."
            )
        if department_id is None:
            raise validation_failed(
                "department_id is required at department level."
            )
        return department_id, None
    # level == "user"
    if user_id is None:
        raise validation_failed("user_id is required at user level.")
    row = conn.execute(
        text(
            "SELECT department_id FROM users "
            "WHERE id = CAST(:u AS uuid) AND tenant_id = CAST(:t AS uuid)"
        ),
        {"u": user_id, "t": tenant_id},
    ).mappings().first()
    if row is None:
        # Cross-tenant forged IDs simply do not exist (D-CP-09).
        raise not_found("User not found.")
    real_dept = str(row["department_id"]) if row["department_id"] else None
    if real_dept is None:
        raise validation_failed(
            "User has no department: user-level model access requires one "
            "(constrain via policy allowed_models instead)."
        )
    if department_id is not None and department_id != real_dept:
        raise validation_failed(
            "department_id does not match the user's department."
        )
    return real_dept, user_id


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
    # D-018-06: the DB scope is resolved/validated server-side — the
    # body never reaches the INSERT unvalidated (GAP M-1: level=user
    # with a NULL department used to die as CheckViolation → 500).
    department_id, user_id = _resolve_scope(
        conn, principal.tenant_id, level=level,
        department_id=department_id, user_id=user_id,
    )
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
    if level not in ("org", "dept", "user"):
        raise validation_failed("level must be org, dept or user.")
    # D-018-06: same server-side scope resolution as approve() — the
    # revoke by user_id alone (what the UI sends) must find the row.
    department_id, user_id = _resolve_scope(
        conn, principal.tenant_id, level=level,
        department_id=department_id, user_id=user_id,
    )
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


def _level_defines_rows(
    conn: Connection, *, level: str,
    department_id: str | None = None,
    user_id: str | None = None,
) -> bool:
    """True when the level has ANY model_access row for the scope —
    approved OR denied (t_f0d3122d).

    D-GOV-11: "si el depto tiene filas: aprobados en depto". A level that
    defines rows participates in the intersection with its APPROVED set;
    a level whose rows are all denied therefore denies everything (an
    explicit denial can never be relaxed — revoking the last approval of
    a level must not fall back to the wider org set).
    """
    row = conn.execute(
        text(
            "SELECT 1 FROM model_access ma "
            "WHERE ma.level = :level "
            "AND (CAST(:dept AS uuid) IS NULL OR ma.department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR ma.user_id = CAST(:usr AS uuid)) "
            "LIMIT 1"
        ),
        {"level": level, "dept": department_id, "usr": user_id},
    ).first()
    return row is not None


def effective_models_for_user(
    conn: Connection, principal: Principal
) -> list[str]:
    """Org ∩ (dept rows? dept approved) ∩ (user rows? user approved)
    ∩ policy allowed_models (defined? even empty = deny-all)
    ∩ byok gate (spec 032 D-032-04).

    t_f0d3122d: the policy key is intersected when DEFINED at any level
    (deny-overrides, D-CP-15 — a level defining [] allows nothing). A
    null/absent key (catalog default since migration 0019) adds no
    restriction: the effective set is just the approval intersection.

    Spec 032: a byok model is effective ONLY if its connection is active,
    allowed by the user's effective ``allowed_connections`` and its catalog
    status is 'available' — composite deny-overrides: a denial on either
    history (model approvals OR connection policy) wins.
    """
    org = _approved_model_names(conn, level="org")
    effective = org
    if principal.department_id and _level_defines_rows(
        conn, level="dept", department_id=principal.department_id
    ):
        effective &= _approved_model_names(
            conn, level="dept", department_id=principal.department_id
        )
    if _level_defines_rows(
        conn, level="user",
        department_id=principal.department_id, user_id=principal.user_id,
    ):
        effective &= _approved_model_names(
            conn, level="user",
            department_id=principal.department_id, user_id=principal.user_id,
        )

    from ..governance.service import effective_policy_for_user

    policy = effective_policy_for_user(conn, principal)
    allowed_by_policy = policy.get("allowed_models")
    if isinstance(allowed_by_policy, list):
        # Defined at some level: [] is an explicit deny-all (the most
        # restrictive value the upsert accepts) — never "no restriction".
        effective &= set(allowed_by_policy)

    # Spec 032 (D-032-04): composite connection ∧ approval gate for byok.
    if effective:
        tokens = set(policy.get("allowed_connections") or [])
        byok_rows = conn.execute(
            text(
                "SELECT mc.model_name, mc.status, vc.status AS conn_status, "
                "vc.id AS conn_id FROM catalog.model_catalog mc "
                "JOIN vault_connections vc ON vc.id = mc.connection_id "
                "WHERE mc.source = 'byok' AND mc.model_name = ANY(:names)"
            ),
            {"names": sorted(effective)},
        ).mappings()
        for r in byok_rows:
            if (
                r["conn_status"] != "active"
                or str(r["conn_id"]) not in tokens
                or r["status"] != "available"
            ):
                effective.discard(r["model_name"])
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
    """Catalog with per-level approval status for the tenant (REQ-MDL-006).

    Spec 018 (D-018-02): model approvals ARE governance policy — the
    response carries a `policy` section with the live org-level
    `allowed_models` (null = no additional restriction) and the
    invariable floor `approved_models_only` from catalog.policy_keys.
    """
    rows = conn.execute(
        text(
            "SELECT mc.model_name, mc.display_name, mc.provider, "
            "mc.context_tokens, mc.max_output_tokens, "
            "mc.input_price_cents_per_m, mc.output_price_cents_per_m, "
            "mc.status AS catalog_status, mc.source, mc.connection_id, "
            "mc.last_seen_at, vc.name AS connection_name, "
            "vc.status AS connection_status "
            "FROM catalog.model_catalog mc "
            "LEFT JOIN vault_connections vc ON vc.id = mc.connection_id "
            "ORDER BY mc.source DESC, vc.name, mc.model_name"
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
            "context_tokens": r["context_tokens"],
            "max_output_tokens": r["max_output_tokens"],
            "input_price_cents_per_m": r["input_price_cents_per_m"],
            "output_price_cents_per_m": r["output_price_cents_per_m"],
            "catalog_status": r["catalog_status"],
            "source": r["source"],
            "connection_id": (
                str(r["connection_id"]) if r["connection_id"] else None
            ),
            "connection_name": r["connection_name"],
            "connection_status": r["connection_status"],
            "last_seen_at": (
                r["last_seen_at"].isoformat() if r["last_seen_at"] else None
            ),
            "approvals": by_model.get(r["model_name"], []),
        })

    # Spec 018 (D-018-02): the approval matrix is part of the governance
    # policy — expose the live org-level allowed_models (null = no extra
    # restriction) and the invariable floor approved_models_only.
    org_allowed = conn.execute(
        text(
            "SELECT value FROM policies WHERE level = 'org' "
            "AND key = 'allowed_models' AND is_active "
            "ORDER BY version DESC LIMIT 1"
        )
    ).mappings().first()
    floor = conn.execute(
        text(
            "SELECT default_value FROM catalog.policy_keys "
            "WHERE key = 'approved_models_only'"
        )
    ).mappings().first()
    policy = {
        "allowed_models": (
            list(org_allowed["value"]) if org_allowed is not None else None
        ),
        "approved_models_only": (
            bool(floor["default_value"]) if floor is not None else True
        ),
    }
    return {"models": models, "policy": policy}


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"

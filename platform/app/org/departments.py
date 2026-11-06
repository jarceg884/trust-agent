"""Department lifecycle (spec 012 §1 + spec 016 CRUD evolution).

Create (spec 012, REQ-ORG-121, D-ORG-06) with optional initial budget
(spec 016 D-16-01), rename and delete (spec 016 D-16-02/03). All mutations
run inside the caller's tenant-scoped transaction (get_tx + RLS): a forged
cross-tenant id is simply not visible (404, D-CP-09). `profile_template`
is validated against the registry (spec 003 profiles, REQ-PRF-002).
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import top_role_key
from ..auth.service import Principal
from ..errors import conflict, not_found, validation_failed
from ..governance import budgets
from ..profiles.templates import get_template


def _valid_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise validation_failed("name must be 1-80 characters.")
    return name


def _dept_row(conn: Connection, department_id: str) -> dict:
    """Fetch a department inside the tenant scope; 404 for anything else
    (unknown or belonging to another tenant — D-CP-09). Malformed uuids
    answer 422 at the ROUTE layer since t_378503a1; the check below is
    defense in depth for internal callers."""
    try:
        _uuid.UUID(department_id)
    except (ValueError, AttributeError, TypeError):
        raise not_found("Department not found.") from None
    row = conn.execute(
        text("SELECT id, name, profile_template FROM departments "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": department_id},
    ).mappings().first()
    if row is None:
        raise not_found("Department not found.")
    return dict(row)


def _count(conn: Connection, sql: str, department_id: str) -> int:
    return conn.execute(text(sql), {"id": department_id}).first()[0]


def create_department(
    conn: Connection, principal: Principal, *,
    name: str, profile_template: str | None,
    initial_budget_cents: int | None = None,
) -> dict:
    """POST /api/departments — 201 {department_id, name[, budget_cents]}.

    D-16-01: with `initial_budget_cents` the department AND its monthly
    budget are created in ONE transaction — `budgets.set_dept_budget`
    reuses the canonical Σ dept ≤ org validation; on failure (422) the
    whole transaction rolls back and the department does NOT exist.
    """
    name = _valid_name(name)
    if profile_template is not None:
        try:
            get_template(profile_template)
        except ValueError as exc:
            raise validation_failed(str(exc)) from None

    duplicate = conn.execute(
        text("SELECT id FROM departments WHERE lower(name) = lower(:n)"),
        {"n": name},
    ).mappings().first()
    if duplicate is not None:
        raise validation_failed(
            "A department with that name already exists in this organization."
        )

    department_id = str(_uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO departments (id, tenant_id, name, profile_template) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :tpl)"
        ),
        {"id": department_id, "tid": principal.tenant_id,
         "name": name, "tpl": profile_template},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="department.created", actor_id=principal.user_id,
        target_type="department", target_id=department_id,
        payload={"name": name,
                 **({"profile_template": profile_template}
                    if profile_template else {})},
    )
    result: dict = {"department_id": department_id, "name": name}
    if initial_budget_cents is not None:
        # Same transaction: emits its own budget.updated audit event;
        # any validation failure (no org budget, Σ > org) rolls back
        # the department insert above.
        budgets.set_dept_budget(
            conn, principal, department_id=department_id,
            amount_cents=initial_budget_cents,
        )
        result["budget_cents"] = initial_budget_cents
    return result


def rename_department(
    conn: Connection, principal: Principal, *,
    department_id: str, name: str,
) -> dict:
    """PUT /api/departments/{id} — 200 {department_id, name} (D-16-03).

    Uniqueness is case-insensitive per tenant (same criterion as create);
    renaming to its own current name is a no-op success.
    """
    name = _valid_name(name)
    row = _dept_row(conn, department_id)
    old_name = row["name"]
    if old_name.lower() != name.lower():
        duplicate = conn.execute(
            text("SELECT id FROM departments "
                 "WHERE lower(name) = lower(:n) AND id <> CAST(:id AS uuid)"),
            {"n": name, "id": department_id},
        ).mappings().first()
        if duplicate is not None:
            raise validation_failed(
                "A department with that name already exists in this "
                "organization."
            )
    conn.execute(
        text("UPDATE departments SET name = :name, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"name": name, "id": department_id},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="department.renamed", actor_id=principal.user_id,
        target_type="department", target_id=department_id,
        payload={"old_name": old_name, "new_name": name},
    )
    return {"department_id": department_id, "name": name}


def delete_department(
    conn: Connection, principal: Principal, *, department_id: str,
) -> dict:
    """DELETE /api/departments/{id} — 200 {deleted, department_id, name}.

    D-16-02: explicit business rules, never implicit cascades. A
    department is deletable ONLY when empty; each blocker answers 409
    with its count and the existing way out. Orphaned level-dept config
    (budgets, policies, model_access) is removed in the SAME transaction;
    `budget_counters` has no FK and only exists when spend occurred —
    and spend blocks the delete, so it stays untouched as history.
    """
    row = _dept_row(conn, department_id)
    name = row["name"]

    blockers: list[str] = []
    users_n = _count(
        conn, "SELECT count(*) FROM users WHERE department_id = "
        "CAST(:id AS uuid)", department_id)
    if users_n:
        blockers.append(
            f"{users_n} assigned user(s) — move them first via "
            f"PUT /api/users/{{user_id}}"
        )
    roles_n = _count(
        conn, "SELECT count(*) FROM user_roles WHERE department_id = "
        "CAST(:id AS uuid)", department_id)
    if roles_n:
        blockers.append(f"{roles_n} department-scoped role assignment(s)")
    spend_n = _count(
        conn, "SELECT count(*) FROM spend_ledger WHERE department_id = "
        "CAST(:id AS uuid)", department_id)
    if spend_n:
        blockers.append(
            f"{spend_n} spend ledger entries (spend attribution is "
            "immutable governance data)"
        )
    agents_n = _count(
        conn, "SELECT count(*) FROM agents WHERE department_id = "
        "CAST(:id AS uuid)", department_id)
    if agents_n:
        blockers.append(f"{agents_n} agent(s) reference this department")
    if blockers:
        raise conflict(
            f"Department \"{name}\" is not empty: " + "; ".join(blockers)
            + ". Resolve them and try again."
        )

    removed_budgets = conn.execute(
        text("DELETE FROM budgets WHERE department_id = CAST(:id AS uuid)"),
        {"id": department_id},
    ).rowcount
    removed_policies = conn.execute(
        text("DELETE FROM policies WHERE department_id = CAST(:id AS uuid)"),
        {"id": department_id},
    ).rowcount
    removed_model_access = conn.execute(
        text("DELETE FROM model_access WHERE department_id = "
             "CAST(:id AS uuid)"),
        {"id": department_id},
    ).rowcount
    conn.execute(
        text("DELETE FROM departments WHERE id = CAST(:id AS uuid)"),
        {"id": department_id},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="department.deleted", actor_id=principal.user_id,
        target_type="department", target_id=department_id,
        payload={
            "name": name,
            **({"profile_template": row["profile_template"]}
               if row["profile_template"] else {}),
            "removed": {
                "budgets": removed_budgets,
                "policies": removed_policies,
                "model_access": removed_model_access,
            },
        },
    )
    return {"deleted": True, "department_id": department_id, "name": name}


def list_departments(conn: Connection) -> dict:
    """GET /api/departments (spec 017 REQ-RA-006): minimal list for the
    console selectors — every department of the caller's tenant (RLS does
    the scoping). Same population as budgets/overview, which also lists
    budgetless departments (LEFT JOIN, spec 013 D-013-07) — the stale claim
    that overview hid them was corrected then; both surfaces agree."""
    rows = conn.execute(
        text("SELECT id, name FROM departments ORDER BY name")
    ).mappings()
    return {
        "departments": [
            {"department_id": str(r["id"]), "name": r["name"]}
            for r in rows
        ]
    }

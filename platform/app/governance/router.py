"""Governance endpoints: budgets, policies, shared agents (spec 005)."""
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import dept_scope
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import (
    not_found, permission_denied, optional_uuid, require_uuid,
    validation_failed,
)
from . import agent_profiles, agents, budgets, policies, policies_matrix
from . import named_policies, usage

router = APIRouter(prefix="/api", dependencies=[Depends(require_mfa)])


# --- Budgets (REQ-GOV-001…004, 012) ---------------------------------------------

class BudgetBody(BaseModel):
    amount_cents: int
    period: str = "monthly"
    soft_threshold_pct: int = 80
    force: bool = False


@router.get("/budgets/overview")
def budget_overview(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Org overview for org_admin; department overview for dept_admin."""
    if "org_admin" in principal.role_keys:
        return budgets.overview(conn, principal.tenant_id)
    if "dept_admin" in principal.role_keys:
        full = budgets.overview(conn, principal.tenant_id)
        scoped = dept_scope(principal) or []
        full["departments"] = [
            d for d in full["departments"]
            if d["department_id"] in scoped
        ]
        return full
    raise permission_denied("Only admins view the budget overview.")


@router.put("/budgets/org")
def put_org_budget(
    body: BudgetBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    if "budget.org.manage" not in principal.permissions:
        raise permission_denied("Missing permission: budget.org.manage")
    return budgets.set_org_budget(
        conn, principal, amount_cents=body.amount_cents,
        period=body.period, soft_threshold_pct=body.soft_threshold_pct,
        force=body.force,
    )


@router.put("/budgets/dept/{department_id}")
def put_dept_budget(
    department_id: str,
    body: BudgetBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    # t_378503a1: a malformed UUID is a client format error (422), never a
    # SQL CAST failure (500). Canonicalized so scope compares stay correct.
    department_id = require_uuid(department_id, "department_id")
    if "budget.dept.manage" not in principal.permissions:
        raise permission_denied("Missing permission: budget.dept.manage")
    if "org_admin" not in principal.role_keys:
        if department_id not in (dept_scope(principal) or []):
            raise not_found("Department not found.")  # no scope oracle
    return budgets.set_dept_budget(
        conn, principal, department_id=department_id,
        amount_cents=body.amount_cents, period=body.period,
        soft_threshold_pct=body.soft_threshold_pct, force=body.force,
    )


@router.put("/budgets/user/{user_id}")
def put_user_budget(
    user_id: str,
    body: BudgetBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    user_id = require_uuid(user_id, "user_id")  # t_378503a1: 422, not 500
    if "budget.user.manage" not in principal.permissions:
        raise permission_denied("Missing permission: budget.user.manage")
    if "org_admin" not in principal.role_keys:
        row = conn.execute(
            text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
            {"u": user_id},
        ).mappings().first()
        if row is None:
            raise not_found("User not found.")
        dept = str(row["department_id"]) if row["department_id"] else None
        if dept not in (dept_scope(principal) or []):
            raise permission_denied("Outside your department scope.")
    return budgets.set_user_budget(
        conn, principal, user_id=user_id, amount_cents=body.amount_cents,
        period=body.period, soft_threshold_pct=body.soft_threshold_pct,
        force=body.force,
    )


@router.get("/budgets/history")
def budget_history(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
    limit: int = 12,
):
    """Closed-period budget vs spent per level + per-model breakdown
    (REQ-BUD-012, spec 013 D-013-03). org_admin: whole tenant;
    dept_admin: scoped to their departments."""
    if "org_admin" in principal.role_keys:
        return budgets.history(conn, limit=limit)
    if "dept_admin" in principal.role_keys:
        return budgets.history(
            conn, scope_depts=dept_scope(principal) or [], limit=limit
        )
    raise permission_denied("Only admins view the budget history.")


@router.get("/budgets/me")
def my_budget(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return budgets.status_for_user(
        conn, principal.tenant_id, principal.user_id,
        principal.department_id,
    )


# --- Usage analytics (spec 035, mandato Miller) ---------------------------------
# El admin ve los requests y consumos POR USUARIO sobre el ledger real
# (spend_ledger). Rutas estáticas registradas antes de cualquier ruta con
# path param para evitar capturas.

def _usage_scope(principal: Principal) -> tuple[str, list[str] | None]:
    """("org"|"dept", scope_depts) o lanza 403 si no es admin."""
    if "org_admin" in principal.role_keys:
        return "org", None
    if "dept_admin" in principal.role_keys:
        return "dept", dept_scope(principal) or []
    raise permission_denied("Only admins view usage analytics.")


def _usage_filters(
    period: str, from_: str | None, to: str | None,
    user_id: str | None, model: str | None, mode: str | None,
) -> tuple[dict, str | None, str | None, str | None]:
    window = usage.resolve_window(period, from_, to)
    if user_id is not None:
        user_id = require_uuid(user_id, "user_id")  # 422, no 500
    usage.validate_filters(model, mode)
    return window, user_id, model, mode


@router.get("/usage")
def usage_summary(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
    period: str = "30d",
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    mode: str | None = None,
):
    """GET /api/usage (REQ-USG-001): agregado por usuario del período."""
    _, scope_depts = _usage_scope(principal)
    window, user_id, model, mode = _usage_filters(
        period, from_, to, user_id, model, mode
    )
    return usage.aggregate(
        conn, scope_depts=scope_depts, window=window,
        user_id=user_id, model=model, mode=mode,
    )


@router.get("/usage/requests")
def usage_requests(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
    period: str = "30d",
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """GET /api/usage/requests (REQ-USG-002): drill-down paginado."""
    _, scope_depts = _usage_scope(principal)
    window, user_id, model, mode = _usage_filters(
        period, from_, to, user_id, model, mode
    )
    return usage.requests_page(
        conn, scope_depts=scope_depts, window=window,
        user_id=user_id, model=model, mode=mode,
        limit=limit, offset=offset,
    )


@router.get("/usage/export")
def usage_export(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
    level: str = "users",
    period: str = "30d",
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    mode: str | None = None,
):
    """GET /api/usage/export (REQ-USG-003): CSV auditado."""
    _, scope_depts = _usage_scope(principal)
    window, user_id, model, mode = _usage_filters(
        period, from_, to, user_id, model, mode
    )
    rows = usage.export_rows(
        conn, scope_depts=scope_depts, window=window, level=level,
        user_id=user_id, model=model, mode=mode,
    )
    # Audit en la MISMA transacción: get_tx commitea al retornar.
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=(
            principal.role_keys[0] if principal.role_keys else "user"
        ),
        action="usage.exported", actor_id=principal.user_id,
        target_type="usage", target_id=level,
        payload={"level": level,
                 "from": window["from"].isoformat(),
                 "to": window["to"].isoformat(),
                 "user_id": user_id, "model": model, "mode": mode,
                 "rows": len(rows)},
    )
    csv_text = usage.rows_to_csv(rows)
    filename = f"usage-{level}-{window['from'].isoformat()}_{window['to'].isoformat()}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Policies (REQ-GOV-020…024) --------------------------------------------------

class PolicyBody(BaseModel):
    key: str
    value: object


def _policy_scope(
    conn: Connection, principal: Principal, level: str,
    department_id: str | None, user_id: str | None,
) -> None:
    """org: policies.org.manage (org_admin). dept: policies.dept.manage,
    dept_admin limited to own departments. user: org_admin anywhere,
    dept_admin within their departments.

    Spec 041 (D-41-09): única implementación de la regla — delega en
    named_policies.require_recipient_scope para que el panel por key
    (spec 036) y las políticas nombradas compartan EXACTAMENTE el mismo
    gate (cero superficie de bypass, cero drift)."""
    named_policies.require_recipient_scope(
        conn, principal, level, department_id, user_id,
    )


@router.get("/policies")
def list_policies(
    level: str,
    department_id: str | None = None,
    user_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    # t_378503a1: optional UUID filters are format-checked (422, not 500).
    department_id = optional_uuid(department_id, "department_id")
    user_id = optional_uuid(user_id, "user_id")
    if not ({"policies.org.manage", "policies.dept.manage", "audit.view"}
            & principal.permissions):
        raise permission_denied("Only admins view raw policies.")
    if level == "org" and "org_admin" not in principal.role_keys:
        raise permission_denied("Only an organization admin views org policies.")
    if level in ("dept", "user") and "org_admin" not in principal.role_keys:
        if level == "dept" and department_id not in (dept_scope(principal) or []):
            raise not_found("Department not found.")
        if level == "user":
            row = conn.execute(
                text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
                {"u": user_id},
            ).mappings().first()
            if row is None:
                raise not_found("User not found.")
            dept = str(row["department_id"]) if row["department_id"] else None
            if dept not in (dept_scope(principal) or []):
                raise permission_denied("Outside your department scope.")
    return {
        "policies": policies.list_level(
            conn, level=level, department_id=department_id, user_id=user_id
        ),
        # Spec 018 (D-018-02): the model approval matrix is part of the
        # level's governance policy.
        "model_approvals": policies.model_approvals(
            conn, level=level, department_id=department_id, user_id=user_id
        ),
    }


@router.put("/policies/org")
def put_org_policy(
    body: PolicyBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _policy_scope(conn, principal, "org", None, None)
    return policies.upsert(conn, principal, level="org",
                           key=body.key, value=body.value)


@router.put("/policies/dept/{department_id}")
def put_dept_policy(
    department_id: str,
    body: PolicyBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    department_id = require_uuid(department_id, "department_id")  # t_378503a1
    _policy_scope(conn, principal, "dept", department_id, None)
    return policies.upsert(conn, principal, level="dept",
                           key=body.key, value=body.value,
                           department_id=department_id)


@router.put("/policies/user/{user_id}")
def put_user_policy(
    user_id: str,
    body: PolicyBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    user_id = require_uuid(user_id, "user_id")  # t_378503a1: 422, not 500
    _policy_scope(conn, principal, "user", None, user_id)
    row = conn.execute(
        text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        raise not_found("User not found.")
    dept = str(row["department_id"]) if row["department_id"] else None
    return policies.upsert(conn, principal, level="user",
                           key=body.key, value=body.value,
                           department_id=dept, user_id=user_id)


@router.get("/policies/matrix")
def policies_matrix_view(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Spec 033 (REQ-033-001): catálogo tipado + org + overrides por
    depto y por usuario en UNA respuesta — la tabla maestra de la
    página dedicada Policies. Read-only; la edición usa los PUT/DELETE
    de arriba (validación y audit intactos)."""
    return policies_matrix.matrix(conn, principal)


@router.get("/policies/effective")
def policies_effective_view(
    user_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Spec 033 (REQ-033-002): efectiva deny-overrides de un usuario
    arbitrario + provenance por key (source/tightened/denied) — el diff
    "qué hereda, qué estrecha, qué niega" de la página Policies."""
    return policies_matrix.effective_diff(conn, principal, user_id)


@router.delete("/policies/{level}/{key}", status_code=204)
def delete_policy(
    level: str,
    key: str,
    department_id: str | None = None,
    user_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    # t_378503a1: optional UUID filters are format-checked (422, not 500).
    department_id = optional_uuid(department_id, "department_id")
    user_id = optional_uuid(user_id, "user_id")
    # REQ-GOV-025 (gap B-3, t_fdc728d2): PUT /policies/user/{user_id}
    # resolves the user's department from `users` and stamps it on the
    # row; DELETE must apply the SAME resolution or the row written by
    # the PUT is unreachable without repeating department_id — the
    # PUT/DELETE asymmetry that answered 404 for an existing policy.
    # An explicit department_id keeps working (backward compatibility).
    if level == "user" and user_id and department_id is None:
        row = conn.execute(
            text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
            {"u": user_id},
        ).mappings().first()
        if row is None:
            raise not_found("User not found.")
        department_id = str(row["department_id"]) if row["department_id"] else None
    _policy_scope(conn, principal, level, department_id, user_id)
    policies.deactivate(
        conn, principal, level=level, key=key,
        department_id=department_id, user_id=user_id,
    )
    return None


# --- Named policies (spec 041, REQ-041-001…011) -----------------------------------
# Una política nombrada = preset de policy keys; la asignación MATERIALIZA
# vía policies.upsert existente (D-41-01, engine intacto). El orden de
# registro importa: /recipients y /assignments/... van ANTES de las rutas
# con {id} para no ser capturados por el path param.

class NamedPolicyBody(BaseModel):
    name: str
    description: str | None = None
    definition: dict | None = None
    template_slug: str | None = None


class NamedPolicyUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    definition: dict | None = None


class AssignBody(BaseModel):
    level: str
    department_id: str | None = None
    user_id: str | None = None


@router.get("/named-policies")
def list_named_policies(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Lista de políticas nombradas + bloque ``builder`` (contrato canónico
    del sidebar, D-41-02) — la UI JAMÁS hardcodea grupos/capacidades."""
    return named_policies.list_policies(conn, principal)


@router.get("/named-policies/recipients")
def named_policy_recipients(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Qué políticas nombradas aplican a cada destinatario del alcance
    (D-41-12) — leído de named_policy_assignments, sin re-derivar la
    efectiva (eso sigue viniendo de matrix/effective)."""
    return named_policies.recipients(conn, principal)


@router.post("/named-policies", status_code=201)
def create_named_policy(
    body: NamedPolicyBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return named_policies.create(
        conn, principal, name=body.name, description=body.description,
        definition=body.definition, template_slug=body.template_slug,
    )


@router.put("/named-policies/{policy_id}")
def update_named_policy(
    policy_id: str,
    body: NamedPolicyUpdateBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    policy_id = require_uuid(policy_id, "policy_id")  # 422, no 500
    return named_policies.update(
        conn, principal, policy_id, name=body.name,
        description=body.description, definition=body.definition,
    )


@router.delete("/named-policies/{policy_id}", status_code=204)
def delete_named_policy(
    policy_id: str,
    cascade: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """409 con la lista de asignaciones activas mientras tenga (D-41-07);
    ``?cascade=unassign`` desasigna todas y luego borra."""
    policy_id = require_uuid(policy_id, "policy_id")  # 422, no 500
    if cascade is not None and cascade != "unassign":
        raise validation_failed("cascade must be 'unassign'.")
    named_policies.delete(
        conn, principal, policy_id, cascade=(cascade == "unassign"),
    )
    return None


@router.get("/named-policies/{policy_id}/preview")
def preview_named_policy(
    policy_id: str,
    level: str,
    department_id: str | None = None,
    user_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Dry-run SIN escribir (REQ-041-004): keys a escribir (con valor
    actual → overwrites), skipped_org_only, would_relax."""
    policy_id = require_uuid(policy_id, "policy_id")  # 422, no 500
    department_id = optional_uuid(department_id, "department_id")
    user_id = optional_uuid(user_id, "user_id")
    return named_policies.preview(
        conn, principal, policy_id, level=level,
        department_id=department_id, user_id=user_id,
    )


@router.post("/named-policies/{policy_id}/assign", status_code=201)
def assign_named_policy(
    policy_id: str,
    body: AssignBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Materialización atómica (REQ-041-005): todos los upserts en la
    transacción del request; un fallo → 422 por key y CERO filas nuevas."""
    policy_id = require_uuid(policy_id, "policy_id")  # 422, no 500
    department_id = optional_uuid(body.department_id, "department_id")
    user_id = optional_uuid(body.user_id, "user_id")
    return named_policies.assign(
        conn, principal, policy_id, level=body.level,
        department_id=department_id, user_id=user_id,
    )


@router.delete("/named-policies/assignments/{assignment_id}")
def unassign_named_policy(
    assignment_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Unassign segura (D-41-05): solo keys del snapshot; las editadas a
    mano se conservan y se reportan en kept_modified."""
    assignment_id = require_uuid(assignment_id, "assignment_id")
    return named_policies.unassign(conn, principal, assignment_id)


@router.post("/named-policies/{policy_id}/reapply")
def reapply_named_policy(
    policy_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Re-materialización best-effort por destinatario (REQ-041-006)."""
    policy_id = require_uuid(policy_id, "policy_id")  # 422, no 500
    return named_policies.reapply(conn, principal, policy_id)


# --- Shared agents (REQ-GOV-040…044) ----------------------------------------------

class AgentBody(BaseModel):
    name: str
    description: str | None = None
    scope: str = "dept"


class GrantBody(BaseModel):
    user_id: str
    permission: str


class InvokeBody(BaseModel):
    """Spec 020 (REQ-020-003): optional model the caller plans to use —
    the gate applies the policy engine (403 model_not_approved)."""
    model_name: str | None = None


# --- Default agent profiles (spec 020, REQ-020-001/002) ----------------------------

@router.get("/agent-profiles")
def list_agent_profiles(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Product-curated default profiles + the caller's effective policy
    (deny-overrides) — the Shared Agents 'Default profiles' section."""
    return agent_profiles.list_profiles(conn, principal)


@router.post("/agent-profiles/{key}/adopt", status_code=201)
def adopt_agent_profile(
    key: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Adopt a default profile: a tenant-scoped agent instance owned by
    the adopter, with explicit grants like any published agent."""
    return agent_profiles.adopt(conn, principal, key)


@router.post("/agents", status_code=201)
def publish_agent(
    body: AgentBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return agents.publish(
        conn, principal, name=body.name, description=body.description,
        scope=body.scope,
    )


@router.get("/agents")
def list_agents(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return {"agents": agents.list_for_user(conn, principal)}


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    agent_id = require_uuid(agent_id, "agent_id")  # t_378503a1: 422, not 500
    agent, access = agents.visible_agent(conn, principal, agent_id)
    return {
        "id": str(agent["id"]), "name": agent["name"],
        "description": agent["description"], "scope": agent["scope"],
        "owner_user_id": str(agent["owner_user_id"]),
        "department_id": str(agent["department_id"])
        if agent["department_id"] else None,
        "access": access,
    }


@router.get("/agents/{agent_id}/grants")
def list_grants(
    agent_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Active grants of an agent (spec 012, REQ-ORG-120) — same manage
    gate as POST/DELETE grants; 404 hides existence from anyone else."""
    agent_id = require_uuid(agent_id, "agent_id")  # t_378503a1: 422, not 500
    return {"grants": agents.list_grants(conn, principal, agent_id=agent_id)}


@router.post("/agents/{agent_id}/grants", status_code=201)
def create_grant(
    agent_id: str,
    body: GrantBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    agent_id = require_uuid(agent_id, "agent_id")  # t_378503a1: 422, not 500
    grantee_user_id = require_uuid(body.user_id, "user_id")
    return agents.grant(
        conn, principal, agent_id=agent_id,
        grantee_user_id=grantee_user_id, permission=body.permission,
    )


@router.delete("/agents/{agent_id}/grants/{grant_id}", status_code=204)
def revoke_grant(
    agent_id: str,
    grant_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    agent_id = require_uuid(agent_id, "agent_id")  # t_378503a1: 422, not 500
    grant_id = require_uuid(grant_id, "grant_id")
    agents.revoke_grant(conn, principal, agent_id=agent_id, grant_id=grant_id)
    return None


@router.post("/agents/{agent_id}/invoke")
def invoke_agent(
    agent_id: str,
    body: InvokeBody | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    agent_id = require_uuid(agent_id, "agent_id")  # t_378503a1: 422, not 500
    return agents.invoke_gate(
        conn, principal, agent_id,
        model_name=(body.model_name if body else None),
    )

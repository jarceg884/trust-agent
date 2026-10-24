"""Governance endpoints: budgets, policies, shared agents (spec 005)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.rbac import dept_scope
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import not_found, permission_denied
from . import agents, budgets, policies

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
    dept_admin within their departments."""
    if level == "org":
        if "policies.org.manage" not in principal.permissions:
            raise permission_denied("Missing permission: policies.org.manage")
        return
    if level == "dept":
        if not ({"policies.dept.manage", "policies.org.manage"}
                & principal.permissions):
            raise permission_denied("Missing permission: policies.dept.manage")
        if "org_admin" not in principal.role_keys:
            if department_id not in (dept_scope(principal) or []):
                raise not_found("Department not found.")
        return
    if level == "user":
        if not ({"policies.dept.manage", "policies.org.manage"}
                & principal.permissions):
            raise permission_denied("Missing permission: policies.dept.manage")
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
        return
    raise permission_denied("Invalid level.")


@router.get("/policies")
def list_policies(
    level: str,
    department_id: str | None = None,
    user_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
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
    return {"policies": policies.list_level(
        conn, level=level, department_id=department_id, user_id=user_id
    )}


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


@router.delete("/policies/{level}/{key}", status_code=204)
def delete_policy(
    level: str,
    key: str,
    department_id: str | None = None,
    user_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _policy_scope(conn, principal, level, department_id, user_id)
    policies.deactivate(
        conn, principal, level=level, key=key,
        department_id=department_id, user_id=user_id,
    )
    return None


# --- Shared agents (REQ-GOV-040…044) ----------------------------------------------

class AgentBody(BaseModel):
    name: str
    description: str | None = None
    scope: str = "dept"


class GrantBody(BaseModel):
    user_id: str
    permission: str


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
    return {"grants": agents.list_grants(conn, principal, agent_id=agent_id)}


@router.post("/agents/{agent_id}/grants", status_code=201)
def create_grant(
    agent_id: str,
    body: GrantBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return agents.grant(
        conn, principal, agent_id=agent_id,
        grantee_user_id=body.user_id, permission=body.permission,
    )


@router.delete("/agents/{agent_id}/grants/{grant_id}", status_code=204)
def revoke_grant(
    agent_id: str,
    grant_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    agents.revoke_grant(conn, principal, agent_id=agent_id, grant_id=grant_id)
    return None


@router.post("/agents/{agent_id}/invoke")
def invoke_agent(
    agent_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return agents.invoke_gate(conn, principal, agent_id)

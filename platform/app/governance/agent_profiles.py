"""Default agent profiles of the product, governed by tenant policy
(spec 020, feedback Miller #7).

Curated product templates (analyst/coder/ops) the tenant sees in Shared
Agents and can ADOPT. Adoption creates a tenant-scoped instance in
`agents` (profile_key column, one live instance per tenant+profile) —
a normal agent row: explicit grants, role-based manage, immediate
revocation, 404 without a grant. The templates' suggested models,
connectors and base policies are SUGGESTIONS ONLY: they never relax or
auto-approve anything — the tenant's own policy (deny-overrides)
governs invocation exactly like any other agent (D-020-01/03).
"""
import uuid as _uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import not_found, validation_failed


@dataclass(frozen=True)
class AgentProfileTemplate:
    key: str
    name: str
    description: str
    scope: str
    skills: list[str]
    suggested_connectors: list[str]
    suggested_models: list[str]
    base_policies: dict = field(default_factory=dict)


TEMPLATES: dict[str, AgentProfileTemplate] = {
    t.key: t
    for t in [
        AgentProfileTemplate(
            key="analyst",
            name="Analyst",
            description=(
                "Research and data analysis: summarization, report "
                "drafting, spreadsheet reasoning and cited findings."
            ),
            scope="org",
            skills=[
                "web-research",
                "data-analysis",
                "report-writing",
                "citations",
            ],
            suggested_connectors=["email"],
            suggested_models=["glm-5.2"],
            base_policies={"allowed_connectors": ["email"]},
        ),
        AgentProfileTemplate(
            key="coder",
            name="Coder",
            description=(
                "Software development: code review, refactoring, tests "
                "and technical documentation inside the repo."
            ),
            scope="org",
            skills=[
                "code-review",
                "refactoring",
                "test-driven-development",
                "debugging",
            ],
            suggested_connectors=[],
            suggested_models=["glm-5.2"],
            base_policies={},
        ),
        AgentProfileTemplate(
            key="ops",
            name="Ops",
            description=(
                "IT operations: incident triage, infrastructure health "
                "checks, log analysis and operational reporting."
            ),
            scope="org",
            skills=[
                "incident-triage",
                "log-analysis",
                "system-administration",
                "patch-management",
            ],
            suggested_connectors=["vmware", "email", "slack"],
            suggested_models=["glm-5.2"],
            base_policies={"allowed_connectors": ["vmware", "email", "slack"]},
        ),
        AgentProfileTemplate(
            key="support",
            name="Support",
            description=(
                "Customer operations: reply drafting, ticket summaries, "
                "CRM hygiene and escalation management."
            ),
            scope="org",
            skills=[
                "customer-support",
                "crm-hygiene",
                "response-templates",
                "escalation-management",
            ],
            suggested_connectors=["email", "slack", "teams"],
            suggested_models=["glm-5.2"],
            base_policies={"allowed_connectors": ["email", "slack", "teams"]},
        ),
    ]
}


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _adopted_instance(
    conn: Connection, tenant_id: str, profile_key: str
) -> dict | None:
    """Live adopted instance of a profile in the tenant (RLS-scoped)."""
    row = conn.execute(
        text(
            "SELECT id, owner_user_id FROM agents "
            "WHERE profile_key = :k AND status = 'active' LIMIT 1"
        ),
        {"k": profile_key},
    ).mappings().first()
    if row is None:
        return None
    return {"id": str(row["id"]),
            "owner_user_id": str(row["owner_user_id"])}


def effective_policy_for_caller(conn: Connection, principal: Principal) -> dict:
    """Effective governance context of the CALLER (D-020-04): the same
    deny-overrides engine that governs any other agent invocation."""
    from ..governance.service import effective_policy_for_user
    from ..models import access

    policy = effective_policy_for_user(conn, principal)
    from . import budgets

    status = budgets.status_for_user(
        conn, principal.tenant_id, principal.user_id,
        principal.department_id,
    )
    return {
        "allowed_models": access.effective_models_for_user(conn, principal),
        "allowed_connectors": policy.get("allowed_connectors") or [],
        "budget_cutoff": status["cutoff"],
    }


def list_profiles(conn: Connection, principal: Principal) -> dict:
    """GET /api/agent-profiles (REQ-020-001): catalog + tenant adoption
    state + the caller's effective policy."""
    profiles = []
    for key in sorted(TEMPLATES):
        t = TEMPLATES[key]
        adopted = _adopted_instance(conn, principal.tenant_id, key)
        profiles.append({
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "scope": t.scope,
            "skills": list(t.skills),
            "suggested_connectors": list(t.suggested_connectors),
            "suggested_models": list(t.suggested_models),
            "adopted_agent_id": adopted["id"] if adopted else None,
            "adopted_by": adopted["owner_user_id"] if adopted else None,
        })
    return {
        "profiles": profiles,
        "effective_policy": effective_policy_for_caller(conn, principal),
    }


def adopt(conn: Connection, principal: Principal, key: str) -> dict:
    """POST /api/agent-profiles/{key}/adopt (REQ-020-002): create the
    tenant instance of a default profile. One live instance per tenant
    and profile (agents_tenant_profile_uidx); the adopter becomes the
    owner, so grants/manage work exactly like a published agent."""
    template = TEMPLATES.get(key)
    if template is None:
        raise not_found("Agent profile not found.")
    if not principal.department_id:
        raise validation_failed(
            "Adopting a default profile requires a department."
        )
    if _adopted_instance(conn, principal.tenant_id, key) is not None:
        raise validation_failed(
            "This default profile is already adopted by your organization."
        )
    agent_id = str(_uuid.uuid4())
    try:
        conn.execute(
            text(
                "INSERT INTO agents (id, tenant_id, owner_user_id, "
                "department_id, name, description, scope, profile_key) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:o AS uuid), "
                "CAST(:d AS uuid), :name, :desc, :scope, :pk)"
            ),
            {
                "id": agent_id, "t": principal.tenant_id,
                "o": principal.user_id, "d": principal.department_id,
                "name": template.name, "desc": template.description,
                "scope": template.scope, "pk": key,
            },
        )
    except Exception as exc:
        message = str(exc)
        if "agents_tenant_profile_uidx" in message:
            raise validation_failed(
                "This default profile is already adopted by your organization."
            )
        if ("agents_owner_user_id_name_key" in message
                or "duplicate key" in message):
            raise validation_failed(
                "You already have a shared agent with that name."
            )
        raise
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="agent.profile_adopted", actor_id=principal.user_id,
        target_type="agent", target_id=agent_id,
        payload={"profile_key": key, "name": template.name},
    )
    return {
        "agent_id": agent_id,
        "profile_key": key,
        "name": template.name,
        "scope": template.scope,
        "owner_user_id": principal.user_id,
        "department_id": principal.department_id,
    }

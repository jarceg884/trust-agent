"""Pre-designed Hermes profile templates per department (REQ-PRF-001, D-ONB-06).

Versioned data (not a DB table — a catalog.profile_templates table is a
deliberated schema evolution owned by spec 002). Each template declares the
skills the department's Hermes profiles ship with, suggested connectors,
desktop quick actions, base policy suggestions and a suggested monthly budget
in USD. Departments reference a template by key (departments.profile_template).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProfileTemplate:
    key: str
    name: str
    description: str
    skills: list[str]
    suggested_connectors: list[str]
    quick_actions: list[str]
    base_policies: dict = field(default_factory=dict)
    suggested_monthly_budget_usd: float = 100.0


TEMPLATES: dict[str, ProfileTemplate] = {
    t.key: t
    for t in [
        ProfileTemplate(
            key="it_operations",
            name="IT Operations",
            description=(
                "Service desk, infrastructure health, security operations and "
                "operational reporting for the IT department."
            ),
            skills=[
                "system-administration",
                "network-diagnostics",
                "incident-triage",
                "security-hardening",
                "log-analysis",
                "patch-management",
            ],
            suggested_connectors=["vmware", "email", "slack"],
            quick_actions=[
                "Check host health",
                "Open incident ticket",
                "Review pending patches",
                "Summarize overnight alerts",
            ],
            base_policies={"allowed_connectors": ["vmware", "email", "slack"]},
            suggested_monthly_budget_usd=150.0,
        ),
        ProfileTemplate(
            key="cloud_operations",
            name="Cloud Operations",
            description=(
                "Cloud provisioning, cost review and incident management for "
                "the cloud/infrastructure team."
            ),
            skills=[
                "cloud-provisioning",
                "cost-optimization",
                "infrastructure-as-code",
                "incident-management",
                "capacity-planning",
                "backup-and-recovery",
            ],
            suggested_connectors=["vmware", "email", "teams"],
            quick_actions=[
                "Review cloud spend",
                "Provision staging VM",
                "Run capacity report",
                "List unattached volumes",
            ],
            base_policies={"allowed_connectors": ["vmware", "email", "teams"]},
            suggested_monthly_budget_usd=200.0,
        ),
        ProfileTemplate(
            key="finance",
            name="Finance",
            description=(
                "Financial reporting, reconciliation and spend analysis for "
                "the finance department."
            ),
            skills=[
                "financial-reporting",
                "reconciliation",
                "spend-analysis",
                "invoice-processing",
                "forecasting",
            ],
            suggested_connectors=["email"],
            quick_actions=[
                "Monthly spend report",
                "Reconcile accounts",
                "Forecast next quarter",
            ],
            base_policies={"allowed_connectors": ["email"]},
            suggested_monthly_budget_usd=100.0,
        ),
        ProfileTemplate(
            key="customer_operations",
            name="Customer Operations",
            description=(
                "Customer support, CRM hygiene and response templates for the "
                "customer-facing team."
            ),
            skills=[
                "customer-support",
                "crm-hygiene",
                "response-templates",
                "sentiment-analysis",
                "escalation-management",
            ],
            suggested_connectors=["email", "whatsapp", "telegram", "slack", "teams"],
            quick_actions=[
                "Draft customer reply",
                "Summarize open tickets",
                "Escalate critical case",
            ],
            base_policies={
                "allowed_connectors": ["email", "whatsapp", "telegram", "slack", "teams"]
            },
            suggested_monthly_budget_usd=120.0,
        ),
    ]
}

DEFAULT_TEMPLATE_KEYS: list[str] = [
    "it_operations",
    "cloud_operations",
    "finance",
    "customer_operations",
]


def get_template(key: str) -> ProfileTemplate:
    try:
        return TEMPLATES[key]
    except KeyError:
        valid = ", ".join(sorted(TEMPLATES))
        raise ValueError(f"Unknown profile template '{key}'. Valid: {valid}") from None


def resolve_template_keys(keys: list[str] | None) -> list[str]:
    """Validate a department selection against the registry (422 upstream)."""
    resolved = list(keys) if keys else list(DEFAULT_TEMPLATE_KEYS)
    for key in resolved:
        get_template(key)  # raises ValueError on unknown keys
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate profile template keys in department selection.")
    return resolved

"""Pre-designed Hermes profile templates per department (REQ-PRF-001, D-ONB-06).

Versioned data (not a DB table — a catalog.profile_templates table is a
deliberated schema evolution owned by spec 002). Each template declares the
skills the department's Hermes profiles ship with, suggested connectors,
desktop quick actions, base policy suggestions and a suggested monthly budget
in USD. Departments reference a template by key (departments.profile_template).

Spec 038 (D-38-03): the catalog grows 4 -> 12 templates with localized
display names (name_es/description_es, same es+en precedent as spec 036).
The template key is metadata only — the department NAME is customer data
(D-38-04): the wizard pre-fills the localized display name but the admin
can edit it ("Finanzas" with template `finance`).

Spec 038 (D-38-01): this registry no longer seeds anything implicitly.
`resolve_template_keys(None)` resolves to an EMPTY list — an organization is
born with the departments it DECLARES or none at all.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProfileTemplate:
    key: str
    name: str
    description: str
    name_es: str
    description_es: str
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
            name_es="Operaciones de TI",
            description_es=(
                "Mesa de servicio, salud de la infraestructura, operaciones de "
                "seguridad y reportes operativos para el área de TI."
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
            name_es="Operaciones de Nube",
            description_es=(
                "Aprovisionamiento en la nube, revisión de costos y gestión de "
                "incidentes para el equipo de infraestructura cloud."
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
            key="customer_operations",
            name="Customer Operations",
            description=(
                "Customer support, CRM hygiene and response templates for the "
                "customer-facing team."
            ),
            name_es="Operaciones de Clientes",
            description_es=(
                "Soporte al cliente, higiene del CRM y plantillas de respuesta "
                "para el equipo de cara al cliente."
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
        ProfileTemplate(
            key="data_analytics",
            name="Data & Analytics",
            description=(
                "Data exploration, dashboarding and reporting for the "
                "analytics team."
            ),
            name_es="Datos y Analítica",
            description_es=(
                "Exploración de datos, tableros y reportes para el equipo de "
                "analítica."
            ),
            skills=[
                "data-exploration",
                "sql-analysis",
                "dashboarding",
                "reporting",
                "forecasting",
            ],
            suggested_connectors=["email", "slack"],
            quick_actions=[
                "Run monthly report",
                "Summarize dataset",
                "Draft KPI review",
            ],
            base_policies={"allowed_connectors": ["email", "slack"]},
            suggested_monthly_budget_usd=120.0,
        ),
        ProfileTemplate(
            key="engineering",
            name="Engineering",
            description=(
                "Code review, debugging, CI diagnostics and technical "
                "documentation for the software engineering team."
            ),
            name_es="Ingeniería",
            description_es=(
                "Revisión de código, depuración, diagnóstico de CI y "
                "documentación técnica para el equipo de desarrollo."
            ),
            skills=[
                "code-review",
                "debugging",
                "ci-diagnostics",
                "refactoring",
                "technical-writing",
            ],
            suggested_connectors=["email", "slack"],
            quick_actions=[
                "Review pull request",
                "Triage build failure",
                "Draft design doc",
            ],
            base_policies={"allowed_connectors": ["email", "slack"]},
            suggested_monthly_budget_usd=200.0,
        ),
        ProfileTemplate(
            key="finance",
            name="Finance",
            description=(
                "Financial reporting, reconciliation and spend analysis for "
                "the finance department."
            ),
            name_es="Finanzas",
            description_es=(
                "Reportes financieros, conciliaciones y análisis de gasto para "
                "el área de finanzas."
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
            key="human_resources",
            name="Human Resources",
            description=(
                "Recruiting pipeline, onboarding guides and HR communications "
                "for the people team."
            ),
            name_es="Recursos Humanos",
            description_es=(
                "Pipeline de reclutamiento, guías de onboarding y "
                "comunicaciones de RRHH para el equipo de personas."
            ),
            skills=[
                "recruiting-support",
                "onboarding-guides",
                "policy-communication",
                "meeting-notes",
                "survey-analysis",
            ],
            suggested_connectors=["email", "teams"],
            quick_actions=[
                "Draft job description",
                "Summarize candidate feedback",
                "Draft onboarding plan",
            ],
            base_policies={"allowed_connectors": ["email", "teams"]},
            suggested_monthly_budget_usd=80.0,
        ),
        ProfileTemplate(
            key="legal",
            name="Legal",
            description=(
                "Contract review, compliance research and policy drafting for "
                "the legal team."
            ),
            name_es="Legal",
            description_es=(
                "Revisión de contratos, investigación normativa y redacción de "
                "políticas para el equipo legal."
            ),
            skills=[
                "contract-review",
                "compliance-research",
                "policy-drafting",
                "summarization",
            ],
            suggested_connectors=["email"],
            quick_actions=[
                "Summarize contract",
                "Draft NDA clause",
                "Compliance checklist",
            ],
            base_policies={"allowed_connectors": ["email"]},
            suggested_monthly_budget_usd=80.0,
        ),
        ProfileTemplate(
            key="marketing",
            name="Marketing",
            description=(
                "Campaign drafting, content calendars and communication "
                "review for the marketing team."
            ),
            name_es="Marketing",
            description_es=(
                "Redacción de campañas, calendarios de contenido y revisión de "
                "comunicaciones para el equipo de marketing."
            ),
            skills=[
                "campaign-drafting",
                "content-calendar",
                "copywriting",
                "brand-review",
                "seo-basics",
            ],
            suggested_connectors=["email", "slack", "teams"],
            quick_actions=[
                "Draft campaign brief",
                "Review landing copy",
                "Plan content calendar",
            ],
            base_policies={"allowed_connectors": ["email", "slack", "teams"]},
            suggested_monthly_budget_usd=100.0,
        ),
        ProfileTemplate(
            key="product",
            name="Product",
            description=(
                "Product specs, roadmap grooming and user-feedback synthesis "
                "for the product team."
            ),
            name_es="Producto",
            description_es=(
                "Especificaciones de producto, grooming del roadmap y síntesis "
                "de feedback de usuarios para el equipo de producto."
            ),
            skills=[
                "spec-writing",
                "roadmap-grooming",
                "user-feedback-synthesis",
                "competitive-research",
            ],
            suggested_connectors=["email", "slack"],
            quick_actions=[
                "Draft feature spec",
                "Summarize user feedback",
                "Update roadmap notes",
            ],
            base_policies={"allowed_connectors": ["email", "slack"]},
            suggested_monthly_budget_usd=100.0,
        ),
        ProfileTemplate(
            key="sales",
            name="Sales",
            description=(
                "Lead research, outreach drafting and pipeline reporting for "
                "the sales team."
            ),
            name_es="Ventas",
            description_es=(
                "Investigación de prospectos, redacción de outreach y reportes "
                "de pipeline para el equipo comercial."
            ),
            skills=[
                "lead-research",
                "outreach-drafting",
                "pipeline-reporting",
                "meeting-preparation",
            ],
            suggested_connectors=["email", "whatsapp", "telegram"],
            quick_actions=[
                "Draft follow-up email",
                "Research prospect",
                "Weekly pipeline summary",
            ],
            base_policies={"allowed_connectors": ["email", "whatsapp", "telegram"]},
            suggested_monthly_budget_usd=100.0,
        ),
        ProfileTemplate(
            key="security_compliance",
            name="Security & Compliance",
            description=(
                "Security reviews, audit evidence collection and policy "
                "maintenance for the security team."
            ),
            name_es="Seguridad y Cumplimiento",
            description_es=(
                "Revisiones de seguridad, recolección de evidencia de auditoría "
                "y mantenimiento de políticas para el equipo de seguridad."
            ),
            skills=[
                "security-review",
                "audit-evidence",
                "policy-maintenance",
                "incident-response",
                "log-analysis",
            ],
            suggested_connectors=["email", "slack"],
            quick_actions=[
                "Draft security review",
                "Collect audit evidence",
                "Summarize incident timeline",
            ],
            base_policies={"allowed_connectors": ["email", "slack"]},
            suggested_monthly_budget_usd=120.0,
        ),
    ]
}

# Suggested catalog keys for UI defaults (spec 038 §6: this list LOST its
# former implicit-seed role — an organization is never born from it).
DEFAULT_TEMPLATE_KEYS: list[str] = sorted(TEMPLATES)


def get_template(key: str) -> ProfileTemplate:
    try:
        return TEMPLATES[key]
    except KeyError:
        valid = ", ".join(sorted(TEMPLATES))
        raise ValueError(f"Unknown profile template '{key}'. Valid: {valid}") from None


def resolve_template_keys(keys: list[str] | None) -> list[str]:
    """Validate a department selection against the registry (422 upstream).

    Spec 038 (D-38-01): no selection resolves to an EMPTY list — never to a
    default set. The caller decides what "no departments" means (a tenant
    born empty + the structure wizard).
    """
    resolved = list(keys) if keys else []
    for key in resolved:
        get_template(key)  # raises ValueError on unknown keys
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate profile template keys in department selection.")
    return resolved


def catalog_payload() -> list[dict]:
    """Read-only catalog listing for GET /api/org/profile-templates
    (REQ-DO-001): every template with its es+en display fields, ordered
    by key. No tenant data."""
    return [
        {
            "key": t.key,
            "name": t.name,
            "name_es": t.name_es,
            "description": t.description,
            "description_es": t.description_es,
            "suggested_monthly_budget_usd": t.suggested_monthly_budget_usd,
        }
        for t in sorted(TEMPLATES.values(), key=lambda t: t.key)
    ]

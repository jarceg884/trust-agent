"""Tenant provisioning orchestrator (spec 004, REQ-ONB-001…009).

One orchestrated operation creates the whole tenant:

    Keycloak (compensable, runs FIRST — D-ONB-03):
        realm -> user profile (unmanaged attrs) -> web client -> tenant_id
        mapper -> MFA ACR/LoA wiring (D-MFA-01) -> initial org_admin
        (temporary one-time password + TOTP required action)
    PostgreSQL (ONE atomic transaction as trust_app, runs LAST):
        tenants -> departments (profile templates) -> users -> user_roles
        -> budgets -> policies (invariable defaults from the catalog)
        -> model_access (model_plan resolved INSIDE the tx) -> audit events

Failure at any step rolls the DB transaction back entirely (zero orphan
rows) and compensates by deleting the realm. The error names the exact step
that failed and the compensations performed (D-ONB-09: a per-tenant audit
of the rollback is impossible — audit_log is append-only with an FK to
tenants, and zero-orphans means no tenant row survives).
"""
import json
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..audit import service as audit_service
from ..db import set_tenant
from ..errors import ApiError, validation_failed
from ..profiles.templates import get_template, resolve_template_keys
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,48}$")

# Provider default branding (REQ-ONB-007): Trust Agent white-label base.
DEFAULT_BRANDING = {
    "product_name": "Trust Agent",
    "primary_color": "#4F46E5",
    "logo_url": None,
}

STANDARD_PLAN = "standard"


@dataclass
class ProvisionRequest:
    organization_name: str
    admin_email: str
    initial_budget_usd: float
    model_plan: str | list[str] = STANDARD_PLAN  # "standard" | [model_name, ...]
    branding: dict | None = None
    departments: list[str] | None = None  # profile template keys; None = all 4
    slug: str | None = None  # explicit idempotency key (D-ONB-04)
    admin_display_name: str | None = None


@dataclass
class _Provisioned:
    departments: list[dict] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    budget_cents: int = 0


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug


def _provisioning_failed(message: str, status_code: int = 503) -> ApiError:
    return ApiError(
        status_code=status_code, code="provisioning_failed",
        reason="provisioning_failed", message=message,
    )


# ---------------------------------------------------------------------------
# Cross-tenant lookups (trust_migrator ONLY for the slug lookup, D-ONB-10)
# ---------------------------------------------------------------------------

def find_tenant_by_slug(migrator_engine: Engine, slug: str) -> dict | None:
    with migrator_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, name, slug, status FROM tenants WHERE slug = :slug"),
            {"slug": slug},
        ).mappings().first()
        return dict(row) if row else None


def tenant_state(engine: Engine, tenant_id: str) -> dict:
    """Canonical tenant state, read as trust_app inside the tenant scope."""
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        row = conn.execute(
            text(
                "SELECT id, name, slug, status, oidc_realm, oidc_issuer_url, "
                "oidc_client_id, branding, created_at FROM tenants"
            )
        ).mappings().first()
        if row is None:
            return {}
        state = dict(row)
        state["id"] = str(state["id"])
        state["created_at"] = state["created_at"].isoformat()
        state["branding"] = state["branding"] if state["branding"] else {}
        counts = conn.execute(
            text(
                "SELECT "
                " (SELECT count(*) FROM departments) AS departments, "
                " (SELECT count(*) FROM users) AS users, "
                " (SELECT count(*) FROM budgets WHERE level = 'org') AS org_budgets, "
                " (SELECT count(*) FROM policies WHERE level = 'org' AND is_active) "
                "   AS active_org_policies, "
                " (SELECT count(*) FROM model_access WHERE status = 'approved') "
                "   AS approved_models"
            )
        ).mappings().first()
        state["counts"] = {k: int(v) for k, v in dict(counts).items()}
        return state


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def provision(
    engine: Engine,
    migrator_engine: Engine,
    kc: KeycloakAdminClient,
    request: ProvisionRequest,
) -> dict:
    slug = request.slug or slugify(request.organization_name)
    if not SLUG_RE.match(slug):
        raise validation_failed(
            f"Invalid tenant slug '{slug}': must match {SLUG_RE.pattern}."
        )
    if request.initial_budget_usd <= 0:
        raise validation_failed("initial_budget_usd must be positive.")
    if not request.admin_email or "@" not in request.admin_email:
        raise validation_failed("admin_email must be a valid email address.")
    try:
        template_keys = resolve_template_keys(request.departments)
    except ValueError as exc:
        raise validation_failed(str(exc)) from None

    # --- Idempotency (D-ONB-04): same slug -> existing state, no duplicates.
    existing = find_tenant_by_slug(migrator_engine, slug)
    if existing is not None:
        from .healthcheck import healthcheck

        return {
            "created": False,
            "tenant": tenant_state(engine, str(existing["id"])),
            "one_time_activation": None,  # never re-delivered (not recoverable)
            "healthcheck": healthcheck(engine, migrator_engine, kc, slug),
        }

    tenant_id = str(uuid.uuid4())
    issuer = kc.issuer_for(slug)

    # --- Keycloak first (compensable) ----------------------------------------
    try:
        kc.ensure_realm(slug)
        kc.enable_unmanaged_attributes(slug)
        kc.ensure_tenant_id_mapper(slug)
        # MFA evidence via acr (Keycloak 26 never emits `amr` — D-MFA-01).
        kc.ensure_mfa_acr(slug)
        kc_user_uuid, one_time_password = kc.ensure_admin_user(
            slug,
            username=request.admin_email,
            email=request.admin_email,
            tenant_id=tenant_id,
            display_name=request.admin_display_name or request.admin_email,
        )
    except KeycloakAdminError as exc:
        compensations = _compensate(kc, slug)
        raise _provisioning_failed(
            f"Provisioning failed at step '{exc.step}': {exc.detail}. "
            f"Rollback: {compensations or 'nothing to compensate'}."
        ) from None

    # --- PostgreSQL: ONE transaction as trust_app (atomic, D-ONB-03) ---------
    try:
        provisioned = _provision_in_db(
            engine, request=request, slug=slug, tenant_id=tenant_id,
            issuer=issuer, kc_user_uuid=kc_user_uuid,
            template_keys=template_keys,
        )
    except ApiError as exc:
        compensations = _compensate(kc, slug)
        raise _provisioning_failed(
            f"Provisioning failed at step 'db.{exc.reason}': {exc.message} "
            f"Rollback: {compensations or 'nothing to compensate'}.",
            status_code=exc.status_code,
        ) from None
    except Exception as exc:  # unexpected DB failure -> rollback + compensate
        compensations = _compensate(kc, slug)
        raise _provisioning_failed(
            f"Provisioning failed at step 'db.transaction': {exc}. "
            f"Rollback: {compensations or 'nothing to compensate'}."
        ) from None

    # --- Post-commit healthcheck (read-only, D-ONB-11) ------------------------
    from .healthcheck import healthcheck

    check = healthcheck(engine, migrator_engine, kc, slug)
    return {
        "created": True,
        "tenant": tenant_state(engine, tenant_id),
        "admin": {
            "email": request.admin_email,
            "display_name": request.admin_display_name or request.admin_email,
            "department": template_keys[0],
        },
        "one_time_activation": {
            "issuer_url": issuer,
            "client_id": "trust-agent-web",
            "username": request.admin_email,
            "one_time_password": one_time_password,
            "instructions": (
                "Delivered exactly once. The first login forces a new "
                "password and TOTP enrollment; this password is invalid "
                "afterwards and is never stored by the platform."
            ),
        },
        "provisioned": {
            "departments": provisioned.departments,
            "models": provisioned.models,
            "policies": provisioned.policies,
            "budget_cents": provisioned.budget_cents,
        },
        "healthcheck": check,
    }


def _compensate(kc: KeycloakAdminClient, slug: str) -> str:
    """Compensating action for the Keycloak leg: delete the realm."""
    try:
        kc.delete_realm(slug)
        return f"realm '{slug}' deleted"
    except KeycloakAdminError as exc:
        # Compensation failed: surface it loudly — an orphan realm remains.
        return f"REALM CLEANUP FAILED for '{slug}' ({exc.detail}) — manual review"


def _provision_in_db(
    engine: Engine,
    *,
    request: ProvisionRequest,
    slug: str,
    tenant_id: str,
    issuer: str,
    kc_user_uuid: str,
    template_keys: list[str],
) -> _Provisioned:
    """The whole tenant in ONE trust_app transaction. Any failure rolls
    everything back (the caller then compensates the realm)."""
    result = _Provisioned()
    branding = dict(DEFAULT_BRANDING)
    branding.update(request.branding or {})
    amount_cents = int(round(request.initial_budget_usd * 100))

    with engine.begin() as conn:
        set_tenant(conn, tenant_id)  # first statement (D-CP-02)

        conn.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, oidc_realm, "
                " oidc_issuer_url, oidc_client_id, branding) "
                "VALUES (CAST(:id AS uuid), :name, :slug, 'active', :realm, "
                " :issuer, :client, CAST(:branding AS jsonb))"
            ),
            {
                "id": tenant_id, "name": request.organization_name, "slug": slug,
                "realm": slug, "issuer": issuer, "client": "trust-agent-web",
                "branding": json.dumps(branding),
            },
        )

        # Departments seeded from the profile templates (D-ONB-06).
        first_dept_id = None
        for key in template_keys:
            template = get_template(key)
            row = conn.execute(
                text(
                    "INSERT INTO departments (id, tenant_id, name, profile_template) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :pt) "
                    "RETURNING id"
                ),
                {
                    "id": str(uuid.uuid4()), "tid": tenant_id,
                    "name": template.name, "pt": key,
                },
            ).mappings().first()
            dept_id = str(row["id"])
            first_dept_id = first_dept_id or dept_id
            result.departments.append({"id": dept_id, "profile_template": key,
                                       "name": template.name})
            audit_service.emit(
                conn, tenant_id=tenant_id, actor_role="provider",
                action="department.created", target_type="department",
                target_id=dept_id, payload={"profile_template": key},
            )

        # Initial org_admin (Keycloak subject linked, MFA pending enrollment).
        user_row = conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, department_id, email, "
                " display_name, status, mfa_enrolled, oidc_subject) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
                " CAST(:dept AS uuid), :email, :display, 'active', false, :sub) "
                "RETURNING id"
            ),
            {
                "id": str(uuid.uuid4()), "tid": tenant_id, "dept": first_dept_id,
                "email": request.admin_email,
                "display": request.admin_display_name or request.admin_email,
                "sub": kc_user_uuid,
            },
        ).mappings().first()
        admin_user_id = str(user_row["id"])
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="user.created", target_type="user", target_id=admin_user_id,
            payload={"email": request.admin_email, "initial_role": "org_admin"},
        )
        conn.execute(
            text(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
                " CAST(:uid AS uuid), 'org_admin')"
            ),
            {"id": str(uuid.uuid4()), "tid": tenant_id, "uid": admin_user_id},
        )
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="role.assigned", target_type="user", target_id=admin_user_id,
            payload={"role_key": "org_admin"},
        )

        # Organization budget (USD cents, REQ-ONB-006).
        conn.execute(
            text(
                "INSERT INTO budgets (id, tenant_id, level, amount_cents, period) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), 'org', "
                " :amount, 'monthly')"
            ),
            {"id": str(uuid.uuid4()), "tid": tenant_id, "amount": amount_cents},
        )
        result.budget_cents = amount_cents
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="budget.updated", target_type="budget",
            payload={"level": "org", "amount_cents": amount_cents,
                     "period": "monthly"},
        )

        # Default policies: the invariable provider floor, read from the
        # catalog (data-driven, D-ONB-07 — no policy constants here).
        policy_rows = conn.execute(
            text("SELECT key, default_value FROM catalog.policy_keys "
                 "WHERE invariable ORDER BY key")
        ).mappings()
        for prow in policy_rows:
            conn.execute(
                text(
                    "INSERT INTO policies (id, tenant_id, level, key, value) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), 'org', "
                    " :key, CAST(:value AS jsonb))"
                ),
                {
                    "id": str(uuid.uuid4()), "tid": tenant_id,
                    "key": prow["key"],
                    "value": json.dumps(prow["default_value"]),
                },
            )
            result.policies.append(prow["key"])
            audit_service.emit(
                conn, tenant_id=tenant_id, actor_role="provider",
                action="policy.updated", target_type="policy",
                target_id=prow["key"],
                payload={"level": "org", "value": prow["default_value"],
                         "default": True},
            )

        # Initial approved model catalog (model_plan, resolved INSIDE the
        # transaction: an unknown name fails mid-tx -> total rollback,
        # exercising the compensation path for real — D-ONB-08).
        if request.model_plan == STANDARD_PLAN:
            model_rows = conn.execute(
                text("SELECT id, model_name FROM catalog.model_catalog "
                     "WHERE status = 'available' ORDER BY model_name")
            ).mappings()
            model_names = None
        else:
            if not isinstance(request.model_plan, list) or not request.model_plan:
                raise validation_failed(
                    "model_plan must be 'standard' or a non-empty list of "
                    "model names."
                )
            model_names = list(request.model_plan)
            model_rows = conn.execute(
                text("SELECT id, model_name FROM catalog.model_catalog "
                     "WHERE status = 'available' AND model_name = ANY(:names) "
                     "ORDER BY model_name"),
                {"names": model_names},
            ).mappings()
            found = {r["model_name"] for r in model_rows}
            missing = sorted(set(model_names) - found)
            if missing:
                raise validation_failed(
                    "Unknown or unavailable models in model_plan: "
                    + ", ".join(missing)
                )
        for mrow in model_rows:
            conn.execute(
                text(
                    "INSERT INTO model_access (id, tenant_id, model_id, level, "
                    " mode, status) VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
                    " CAST(:mid AS uuid), 'org', 'central', 'approved')"
                ),
                {"id": str(uuid.uuid4()), "tid": tenant_id, "mid": str(mrow["id"])},
            )
            result.models.append(mrow["model_name"])
            audit_service.emit(
                conn, tenant_id=tenant_id, actor_role="provider",
                action="model.approved", target_type="model",
                target_id=mrow["model_name"],
                payload={"level": "org", "mode": "central"},
            )
        if not result.models:
            raise validation_failed(
                "model_plan resolved to zero models (empty global catalog?)."
            )

        # Summary event (REQ-ONB-009) — last, so the payload can carry the
        # full picture of what was created.
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="tenant.provisioned", target_type="tenant", target_id=slug,
            payload={
                "organization_name": request.organization_name,
                "admin_email": request.admin_email,
                "departments": template_keys,
                "models": result.models,
                "policies": result.policies,
                "budget_cents": result.budget_cents,
                "oidc_issuer_url": issuer,
            },
        )

    return result

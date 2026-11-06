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
from ..governance import named_policies as named_policies_service
from ..profiles.templates import get_template
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,48}$")

# Department name rules (spec 012 / D-16-03 — same as the CRUD surface).
DEPT_NAME_MAX = 80


@dataclass(frozen=True)
class DepartmentSpec:
    """A department to create at birth (spec 038 D-38-04/05).

    `name` is customer data (free text, unique case-insensitive per tenant);
    `profile_template` is optional metadata validated against the registry.
    """

    name: str
    profile_template: str | None = None


def resolve_department_specs(
    departments: list[str | dict] | None,
) -> list[DepartmentSpec]:
    """Normalize a declared department list (REQ-DO-011).

    string  -> template key (backward compatible; name = template.name)
    dict    -> custom department {name, profile_template?}
    None/[] -> EMPTY list (D-38-01: the org is born empty + wizard)

    Raises ValueError (mapped to 422 upstream) on unknown template keys,
    invalid names or case-insensitive duplicates.
    """
    specs: list[DepartmentSpec] = []
    seen: set[str] = set()
    for entry in departments or []:
        if isinstance(entry, str):
            template = get_template(entry)  # ValueError on unknown key
            name, key = template.name, entry
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not 1 <= len(name) <= DEPT_NAME_MAX:
                raise ValueError(
                    f"Department name must be 1-{DEPT_NAME_MAX} characters."
                )
            key = entry.get("profile_template")
            if key is not None:
                if not isinstance(key, str):
                    raise ValueError("profile_template must be a string key.")
                get_template(key)  # ValueError on unknown key
        else:
            raise ValueError(
                "Each department must be a template key (string) or an "
                "object {name, profile_template?}."
            )
        if name.lower() in seen:
            raise ValueError(
                f"Duplicate department name '{name}' in the declaration."
            )
        seen.add(name.lower())
        specs.append(DepartmentSpec(name=name, profile_template=key))
    return specs

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
    # Spec 038 (D-38-05): template keys (strings, backward compatible) or
    # custom objects {name, profile_template?}. None/[] = born EMPTY (the
    # org_admin sets the real structure on first login via the wizard).
    departments: list[str | dict] | None = None
    slug: str | None = None  # explicit idempotency key (D-ONB-04)
    admin_display_name: str | None = None


@dataclass
class _Provisioned:
    departments: list[dict] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    # Spec 041 (REQ-041-007): slugs de las plantillas de políticas nombradas
    # sembradas al nacer el tenant (idempotente por slug).
    templates: list[str] = field(default_factory=list)
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
        # Spec 038 (D-38-01/05): declared departments or NONE — never a seed.
        dept_specs = resolve_department_specs(request.departments)
    except ValueError as exc:
        raise validation_failed(str(exc)) from None

    # --- Idempotency (D-ONB-04): same slug -> existing state, no duplicates.
    existing = find_tenant_by_slug(migrator_engine, slug)
    if existing is not None:
        from .healthcheck import healthcheck

        # F1 (t_38ce1e82): re-running provisioning is the repair path for
        # pre-existing tenants — realms provisioned before both loopback
        # names (127.0.0.1 + localhost) were registered only carry the
        # 127.0.0.1 callbacks, which breaks login from localhost. Best
        # effort: a heal failure must not break the idempotent re-run.
        try:
            kc.ensure_web_client_redirect_uris(slug)
            # Spec 015 v1.3.0 (ADR-019): auth panel retro-fit — direct
            # grant flag + trust-direct-grant-loa flow (same heal path).
            kc.ensure_direct_grant_enabled(slug)
            kc.ensure_direct_grant_flow(slug)
        except KeycloakAdminError:
            pass
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
        # Spec 015 v1.3.0 (ADR-019): auth panel — direct grant enabled on
        # the web client + trust-direct-grant-loa flow so password+OTP
        # grants carry the signed acr:"2" (D-AUTH-12/13).
        kc.ensure_direct_grant_enabled(slug)
        kc.ensure_direct_grant_flow(slug)
        # Spec 015: white-label surface + Google brokering + tenant_id for
        # brokered users (D-AUTH-01/02/03). Google is fail-closed: without
        # TRUST_GOOGLE_CLIENT_ID/SECRET no IdP is added.
        branding = request.branding or {}
        kc.ensure_login_theme(slug, product_name=branding.get("product_name"))
        kc.ensure_google_idp(slug)
        kc.ensure_tenant_id_hardcode_mapper(slug, tenant_id)
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
            dept_specs=dept_specs,
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
            # Spec 038 (D-38-01): the admin is born with NO department when
            # none is declared (department_id NULL) — or in the first
            # declared one otherwise.
            "department": dept_specs[0].name if dept_specs else None,
        },
        "one_time_activation": {
            "issuer_url": issuer,
            "client_id": "trust-agent-web",
            "username": request.admin_email,
            "one_time_password": one_time_password,
            "instructions": (
                # Copy corregido (QA Pilar 1, t_ccf901f4): el primer login
                # fuerza UPDATE_PASSWORD, pero el TOTP SOLO es forzado cuando
                # la policy de la org require_mfa=true (spec 015 D-AUTH-06/07);
                # con require_mfa=false queda como banner opcional "Set up MFA".
                "Delivered exactly once. The first login forces a new "
                "password; TOTP (MFA) enrollment is enforced only if the "
                "organization's policy require_mfa is enabled — otherwise "
                "the Admin Console suggests it. This password is invalid "
                "afterwards and is never stored by the platform."
            ),
        },
        "provisioned": {
            "departments": provisioned.departments,
            "models": provisioned.models,
            "policies": provisioned.policies,
            "templates": provisioned.templates,
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
    dept_specs: list[DepartmentSpec],
    status: str = "active",
    amount_cents: int | None = None,
    summary_action: str = "tenant.provisioned",
    actor_role: str = "provider",
    actor_id: str | None = None,
) -> _Provisioned:
    """The whole tenant in ONE trust_app transaction. Any failure rolls
    everything back (the caller then compensates the realm).

    Spec 026 (D-26-04): parameterized for the public self-signup —
    status='pending_activation', amount_cents=0, summary_action
    'tenant.self_registered' and the actor being the self-registered admin.
    Defaults preserve the provider provisioning behavior exactly."""
    result = _Provisioned()
    branding = dict(DEFAULT_BRANDING)
    branding.update(request.branding or {})
    if amount_cents is None:
        amount_cents = int(round(request.initial_budget_usd * 100))

    with engine.begin() as conn:
        set_tenant(conn, tenant_id)  # first statement (D-CP-02)

        conn.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, oidc_realm, "
                " oidc_issuer_url, oidc_client_id, branding) "
                "VALUES (CAST(:id AS uuid), :name, :slug, :status, :realm, "
                " :issuer, :client, CAST(:branding AS jsonb))"
            ),
            {
                "id": tenant_id, "name": request.organization_name, "slug": slug,
                "status": status,
                "realm": slug, "issuer": issuer, "client": "trust-agent-web",
                "branding": json.dumps(branding),
            },
        )

        # Departments DECLARED at birth (spec 038 D-38-01/05 — no implicit
        # seed): template keys or custom names, in the declared order. An
        # empty declaration leaves the org with ZERO departments (the
        # structure wizard picks up from there on first login).
        first_dept_id = None
        for spec in dept_specs:
            row = conn.execute(
                text(
                    "INSERT INTO departments (id, tenant_id, name, profile_template) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :pt) "
                    "RETURNING id"
                ),
                {
                    "id": str(uuid.uuid4()), "tid": tenant_id,
                    "name": spec.name, "pt": spec.profile_template,
                },
            ).mappings().first()
            dept_id = str(row["id"])
            first_dept_id = first_dept_id or dept_id
            result.departments.append(
                {"id": dept_id, "profile_template": spec.profile_template,
                 "name": spec.name}
            )
            audit_service.emit(
                conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
                action="department.created", target_type="department",
                target_id=dept_id,
                payload={"name": spec.name,
                         **({"profile_template": spec.profile_template}
                            if spec.profile_template else {})},
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
        # Spec 026: for the self-signup the actor IS the admin just created
        # (actor_role != 'provider'); the provider path keeps actor_id NULL
        # exactly as before.
        if actor_id is None and actor_role != "provider":
            actor_id = admin_user_id
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
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
            conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
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
            conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
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
                conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
                action="policy.updated", target_type="policy",
                target_id=prow["key"],
                payload={"level": "org", "value": prow["default_value"],
                         "default": True},
            )

        # Spec 041 (REQ-041-007, D-41-08): las 3 plantillas del contrato del
        # builder se siembran como named_policies comunes (editable/
        # asignable/borrable), idempotente por slug. El backfill de los
        # tenants existentes lo hace la migración 0034 con las MISMAS
        # definitions (verificado por tests/test_named_policies.py).
        result.templates = named_policies_service.seed_builtin_templates(
            conn, tenant_id, actor_role=actor_role, actor_id=actor_id,
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
            # Materializar ANTES de leer `found`: un Result de SQLAlchemy no
            # admite re-iteración — la comprensión de `found` consumía el
            # cursor y el loop de INSERT veía cero filas ("model_plan
            # resolved to zero models"). Encontrado por la re-validación
            # contra LiteLLM real (t_c4766c4d): model_plan=['glm-5.2'] era
            # el primer uso REAL del camino de lista explícita.
            model_rows = list(conn.execute(
                text("SELECT id, model_name FROM catalog.model_catalog "
                     "WHERE status = 'available' AND model_name = ANY(:names) "
                     "ORDER BY model_name"),
                {"names": model_names},
            ).mappings())
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
                conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
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
            conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
            action=summary_action, target_type="tenant", target_id=slug,
            payload={
                "organization_name": request.organization_name,
                "admin_email": request.admin_email,
                "status": status,
                "departments": [
                    {"name": s.name,
                     **({"profile_template": s.profile_template}
                        if s.profile_template else {})}
                    for s in dept_specs
                ],
                "models": result.models,
                "policies": result.policies,
                "budget_cents": result.budget_cents,
                "oidc_issuer_url": issuer,
            },
        )

    return result

"""Public organization self-signup (spec 026, mandato Miller).

POST /api/auth/signup-org is PUBLIC (rate-limited per IP): a NEW customer
creates their own ORGANIZATION at the SaaS entrance. The tenant is born
COMPLETE (realm KC real, departments, invariable policies, approved models)
but **pending_activation** and with **zero budget** — the PROVIDER activates
it and assigns the initial budget (see provider_service.activate_tenant).

Seam with spec 015 (D-AUTH-04): that spec's ``/api/auth/register`` creates a
USER inside an EXISTING organization; this module creates the ORGANIZATION
itself. The public entrance distinguishes "Create your organization"
(/signup) from "Join your organization" (/register?org=).

The admin identity is created with a PERMANENT password chosen by the user
(``create_user_with_password``, temporary=false, no required actions — MFA
enrolls inline via the OTP form, same criterion as spec 015). The password
transits once towards Admin REST and is never stored or logged.
"""
import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from ..errors import ApiError, conflict, service_unavailable, validation_failed
from .provision import (
    SLUG_RE,
    ProvisionRequest,
    _compensate,
    _provision_in_db,
    find_tenant_by_slug,
    slugify,
    tenant_state,
)
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError

# Same password policy as user self-registration (spec 015 D-AUTH-04).
from ..auth.register import MIN_PASSWORD_LENGTH

STANDARD_PLAN = "standard"


@dataclass
class SelfSignupRequest:
    organization_name: str
    admin_email: str
    admin_password: str
    admin_display_name: str
    slug: str | None = None  # optional; default = slugify(organization_name)


def _keycloak():
    """Factory indirection so tests can substitute the Admin REST client
    with a declared double (same pattern as auth/register.py)."""
    return KeycloakAdminClient()


def _validate(request: SelfSignupRequest, slug: str) -> None:
    if not request.organization_name.strip():
        raise validation_failed("organization_name must not be empty.")
    if not SLUG_RE.match(slug):
        raise validation_failed(
            f"Invalid organization code '{slug}': must match {SLUG_RE.pattern}."
        )
    if "@" not in request.admin_email or \
            request.admin_email.startswith("@") or request.admin_email.endswith("@"):
        raise validation_failed("admin_email must be a valid email address.")
    # Literal preservation: the password is stored verbatim by Keycloak.
    if len(request.admin_password) < MIN_PASSWORD_LENGTH:
        raise validation_failed(
            f"admin_password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if not request.admin_display_name.strip():
        raise validation_failed("admin_display_name must not be empty.")


def self_signup(
    engine: Engine,
    migrator_engine: Engine,
    kc: KeycloakAdminClient,
    request: SelfSignupRequest,
) -> dict:
    """Full organization self-signup flow.

    Returns ``{"org", "status", "tenant", "message"}``.
    Raises ApiError: 409 slug taken, 422 validation, 503 provisioning failed.
    """
    slug = request.slug or slugify(request.organization_name)
    slug = slug.strip().lower()
    _validate(request, slug)
    admin_email = request.admin_email.strip().lower()

    # Slug availability (anti-squatting: a taken code answers 409 without
    # revealing anything about the existing tenant).
    if find_tenant_by_slug(migrator_engine, slug) is not None:
        raise conflict(
            "That organization code is already taken. Choose another one."
        )

    tenant_id = str(_uuid.uuid4())
    issuer = kc.issuer_for(slug)

    # --- Keycloak first (compensable) — same realm shape as provisioning       -
    try:
        kc.ensure_realm(slug)
        kc.enable_unmanaged_attributes(slug)
        kc.ensure_tenant_id_mapper(slug)
        kc.ensure_mfa_acr(slug)
        # White-label + Google brokering (fail-closed without credentials) —
        # identical surface to a provider-provisioned tenant.
        kc.ensure_login_theme(slug)
        kc.ensure_google_idp(slug)
        kc.ensure_tenant_id_hardcode_mapper(slug, tenant_id)
        kc_sub = kc.create_user_with_password(
            slug,
            username=admin_email,
            email=admin_email,
            tenant_id=tenant_id,
            display_name=request.admin_display_name.strip(),
            password=request.admin_password,
        )
    except KeycloakAdminError as exc:
        compensations = _compensate(kc, slug)
        raise service_unavailable(
            f"Self-signup failed at step '{exc.step}': {exc.detail}. "
            f"Rollback: {compensations or 'nothing to compensate'}."
        ) from None

    # --- PostgreSQL: ONE transaction (reuses the provisioning leg, D-26-04) ---
    provision_request = ProvisionRequest(
        organization_name=request.organization_name.strip(),
        admin_email=admin_email,
        initial_budget_usd=0.0,  # placeholder — amount_cents is explicit below
        model_plan=STANDARD_PLAN,
        # Spec 038 (D-38-01, enmienda AC-26-001): the self-signup org is born
        # with ZERO departments — its real structure is declared post-
        # activation through the "Organization structure" wizard.
        departments=[],
        slug=slug,
        admin_display_name=request.admin_display_name.strip(),
    )
    try:
        _provision_in_db(
            engine,
            request=provision_request,
            slug=slug,
            tenant_id=tenant_id,
            issuer=issuer,
            kc_user_uuid=kc_sub,
            dept_specs=[],
            status="pending_activation",
            amount_cents=0,
            summary_action="tenant.self_registered",
            actor_role="user",
        )
    except ApiError as exc:
        compensations = _compensate(kc, slug)
        raise service_unavailable(
            f"Self-signup failed at step 'db.{exc.reason}': {exc.message} "
            f"Rollback: {compensations or 'nothing to compensate'}."
        ) from None
    except Exception as exc:
        compensations = _compensate(kc, slug)
        raise service_unavailable(
            f"Self-signup failed at step 'db.transaction': {exc}. "
            f"Rollback: {compensations or 'nothing to compensate'}."
        ) from None

    return {
        "org": slug,
        "status": "pending_activation",
        "tenant": tenant_state(engine, tenant_id),
        "message": (
            "Your organization has been created and is pending activation "
            "by the provider. You can sign in to check its status; nothing "
            "is functional until it is activated."
        ),
    }

"""Self-registration with password (spec 015 D-AUTH-04).

POST /api/auth/register is PUBLIC (rate-limited): the end customer creates
their own user with a password from the app — no ROPC (constitution IV),
no admin involvement. The flow:

  1. resolve the org by slug (trust_migrator lookup, D-ONB-10),
  2. gate on the org policy ``allow_self_registration`` (D-AUTH-07),
  3. reject duplicated emails (409, tenant-scoped check),
  4. create the identity in Keycloak via Admin REST with a PERMANENT
     password (``temporary=false``) and NO required actions — MFA is
     optional and enrolls inline via the OTP form on acr_values=2,
  5. insert ``users`` + ``user_roles`` (minimum role ``user``) and audit
     ``user.self_registered`` in ONE tenant transaction.

The password transits once through the control plane towards Admin REST
(TLS in production) and is never stored or logged.
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..audit import service as audit
from ..db import set_tenant
from ..errors import ApiError, conflict, not_found, validation_failed
from ..governance.service import org_policy_value

MIN_PASSWORD_LENGTH = 12


def _keycloak():
    """Factory indirection so tests can substitute the Admin REST client
    with a declared double (same pattern as org/invite.py)."""
    from ..tenants.keycloak_admin import KeycloakAdminClient

    return KeycloakAdminClient()


def _validate(email: str, password: str, display_name: str) -> None:
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise validation_failed("email must be a valid email address.")
    # Literal preservation: the password is stored verbatim by Keycloak —
    # no trimming/normalization (constitution, literal_preservation).
    if len(password) < MIN_PASSWORD_LENGTH:
        raise validation_failed(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if not display_name.strip():
        raise validation_failed("display_name must not be empty.")


def register_user(
    engine: Engine,
    migrator_engine: Engine,
    *,
    org: str,
    email: str,
    password: str,
    display_name: str,
) -> dict:
    """Full self-registration flow. Returns ``{"user_id", "org"}``.

    Raises ApiError: 404 unknown org, 403 self_registration_disabled,
    409 email_exists, 422 validation, 503 Keycloak unreachable.
    """
    from ..tenants.provision import find_tenant_by_slug

    _validate(email, password, display_name)
    email = email.strip().lower()

    tenant = find_tenant_by_slug(migrator_engine, org)
    if tenant is None:
        raise not_found("Unknown organization code.")
    tenant_id = str(tenant["id"])

    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        allowed = org_policy_value(conn, "allow_self_registration")
        if allowed is not True and allowed != "true":
            raise ApiError(
                status_code=403,
                code="forbidden",
                reason="self_registration_disabled",
                message=(
                    "This organization does not allow self-registration. "
                    "Ask an administrator to invite you."
                ),
            )
        existing = conn.execute(
            text("SELECT id FROM users WHERE lower(email) = :email"),
            {"email": email},
        ).mappings().first()
        if existing is not None:
            raise conflict(
                "A user with that email already exists in this organization."
            )
        realm = tenant["slug"]

    # Keycloak identity FIRST (compensable: no DB row can reference a
    # missing subject). Outside the DB transaction — Admin REST is not
    # transactional; a duplicate username in KC maps to 409.
    try:
        kc_sub = _keycloak().create_user_with_password(
            realm,
            username=email,
            email=email,
            tenant_id=tenant_id,
            display_name=display_name.strip(),
            password=password,
        )
    except ApiError:
        raise
    except Exception as exc:
        step = getattr(exc, "step", "")
        if step == "keycloak.self_register.exists":
            raise conflict(
                "A user with that email already exists in this organization."
            ) from None
        from ..errors import service_unavailable

        raise service_unavailable(
            "The identity provider rejected the registration. Try again."
        ) from None

    # DB rows + audit in ONE tenant transaction.
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        row = conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, department_id, email, "
                " display_name, status, mfa_enrolled, oidc_subject) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
                " (SELECT id FROM departments WHERE tenant_id = CAST(:tid AS uuid) "
                "  ORDER BY (name = 'General') DESC, created_at LIMIT 1), "
                " :email, :display, 'active', false, :sub) "
                "ON CONFLICT (tenant_id, email) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": str(_uuid.uuid4()), "tid": tenant_id,
                "email": email, "display": display_name.strip(), "sub": kc_sub,
            },
        ).mappings().first()
        if row is None:
            # Lost a race against a concurrent registration for the same
            # email: the KC identity above is orphaned but harmless (it can
            # never mint a session without the users row — fail closed).
            raise conflict(
                "A user with that email already exists in this organization."
            )
        user_id = str(row["id"])
        conn.execute(
            text(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
                " CAST(:uid AS uuid), 'user')"
            ),
            {"id": str(_uuid.uuid4()), "tid": tenant_id, "uid": user_id},
        )
        audit.emit(
            conn, tenant_id=tenant_id,
            actor_role="user", actor_id=user_id,
            action="user.self_registered",
            target_type="user", target_id=user_id,
            payload={"email": email, "initial_role": "user",
                     "mfa": "optional"},
        )
    return {"user_id": user_id, "org": tenant["slug"]}


def auto_provision_brokered(
    conn: Connection,
    *,
    tenant_id: str,
    sub: str,
    email: str,
    display_name: str,
    via: str,
) -> str:
    """First-brokered-login auto-provision (spec 015 D-AUTH-05).

    Runs INSIDE the mint transaction (the id_token already verified
    cryptographically — signature/iss/tenant). Creates ``users`` +
    ``user_roles`` (minimum role) and audits ``user.auto_provisioned``.
    Returns the new user row id. Caller has already checked the
    ``allow_self_registration`` policy.
    """
    row = conn.execute(
        text(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            " display_name, status, mfa_enrolled, oidc_subject) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
            " (SELECT id FROM departments WHERE tenant_id = CAST(:tid AS uuid) "
            "  ORDER BY (name = 'General') DESC, created_at LIMIT 1), "
            " :email, :display, 'active', false, :sub) "
            "RETURNING id"
        ),
        {
            "id": str(_uuid.uuid4()), "tid": tenant_id,
            "email": email, "display": display_name, "sub": sub,
        },
    ).mappings().first()
    user_id = str(row["id"])
    conn.execute(
        text(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
            " CAST(:uid AS uuid), 'user')"
        ),
        {"id": str(_uuid.uuid4()), "tid": tenant_id, "uid": user_id},
    )
    audit.emit(
        conn, tenant_id=tenant_id,
        actor_role="system", actor_id=user_id,
        action="user.auto_provisioned",
        target_type="user", target_id=user_id,
        payload={"email": email, "initial_role": "user", "via": via},
    )
    return user_id

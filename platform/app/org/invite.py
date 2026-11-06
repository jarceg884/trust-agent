"""User invitation: async job `user.invite` (spec 012, REQ-ORG-101/102).

POST /api/users validates, emits `user.invited` inside the request
transaction and enqueues the job as the LAST operation (if enqueue fails,
the rollback takes the audit event with it — no dangling artifacts).

The worker handler creates the identity in Keycloak via Admin REST (one-time
temporary password + CONFIGURE_TOTP + tenant_id attribute, D-ONB-12) and the
users/user_roles rows in ONE tenant_tx, with audit user.created +
role.assigned. The one-time password travels in jobs.result (D-ORG-02):
tenant-scoped via RLS, MFA-gated, invalidated by Keycloak on first login.
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import dept_scope, top_role_key
from ..auth.service import Principal
from ..errors import validation_failed
from ..jobs import service as jobs_service
from ..profiles.templates import get_template  # noqa: F401  (validación centralizada)

TENANT_ROLES = ("org_admin", "dept_admin", "user")
JOB_TYPE = "user.invite"


def _keycloak():
    """Factory indirection so tests can substitute the Admin REST client
    with a declared double (plan §4); production builds the real client."""
    from ..tenants.keycloak_admin import KeycloakAdminClient

    return KeycloakAdminClient()


def validate_invite(
    conn: Connection, principal: Principal, *, email: str,
    department_id: str | None, role_key: str,
) -> None:
    """Shared by the router: email/departamento/rol dentro del alcance."""
    email = (email or "").strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise validation_failed("email must be a valid email address.")
    if role_key not in TENANT_ROLES:
        raise validation_failed(
            f"role_key must be one of: {', '.join(TENANT_ROLES)}."
        )
    if "org_admin" not in principal.role_keys:
        # dept_admin: solo su scope y sin escalar a org_admin (REQ-ORG-102).
        if role_key == "org_admin":
            raise validation_failed(
                "Only an organization admin can invite an org_admin."
            )
        scoped = dept_scope(principal) or []
        if department_id is None:
            raise validation_failed(
                "A department admin must invite into one of their "
                "departments."
            )
        if department_id not in scoped:
            # Sin oráculo de scope (D-CP-09).
            from ..errors import not_found

            raise not_found("Department not found.")
    if department_id is None:
        raise validation_failed(
            "A department is required for every user."
        )
    dept = conn.execute(
        text("SELECT id FROM departments WHERE id = CAST(:d AS uuid)"),
        {"d": department_id},
    ).mappings().first()
    if dept is None:
        raise validation_failed("Unknown department.")
    if role_key == "dept_admin" and department_id is None:
        raise validation_failed(
            "dept_admin requires a department scope."
        )
    existing = conn.execute(
        text("SELECT id FROM users WHERE lower(email) = :email"),
        {"email": email},
    ).mappings().first()
    if existing is not None:
        raise validation_failed(
            "A user with that email already exists in this organization."
        )


def enqueue_invite(
    conn: Connection, principal: Principal, *,
    email: str, department_id: str | None, role_key: str,
    display_name: str | None,
) -> dict:
    """Audit + enqueue, both inside the REQUEST transaction (D-ORG-02).

    Sharing the connection is not optional: the audit chain takes a
    per-tenant advisory lock held until commit, so a second connection
    emitting job.created would self-deadlock against this transaction.
    One transaction also makes the invite atomic — no dangling job if
    the request rolls back.
    """
    email = email.strip().lower()
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="user.invited", actor_id=principal.user_id,
        target_type="user", target_id=email,
        payload={
            "email": email,
            **({"department_id": department_id}
               if department_id else {}),
            "role_key": role_key,
        },
    )
    job = jobs_service.enqueue(
        None,
        tenant_id=principal.tenant_id,
        job_type=JOB_TYPE,
        payload={
            "email": email,
            "department_id": department_id,
            "role_key": role_key,
            "display_name": display_name,
            "invited_by": principal.user_id,
        },
        idempotency_key=f"invite:{email}",
        actor_id=principal.user_id,
        conn=conn,
    )
    return {"job_id": str(job["id"]), "status": job["status"]}


# ---------------------------------------------------------------------------
# Worker handler (runs as trust_app inside the tenant scope, D-CP-10)
# ---------------------------------------------------------------------------

def handle_invite(conn: Connection, job: dict) -> dict:
    payload = job["payload"] or {}
    email = payload["email"]
    department_id = payload.get("department_id")
    role_key = payload.get("role_key") or "user"
    display_name = payload.get("display_name") or email
    invited_by = payload.get("invited_by")

    tenant = conn.execute(
        text("SELECT slug, oidc_realm FROM tenants")
    ).mappings().first()
    if tenant is None:
        raise RuntimeError("invite: tenant row missing in scope")
    realm = tenant["oidc_realm"] or tenant["slug"]

    # Idempotent retry: the user row may already exist from a partial run.
    existing = conn.execute(
        text("SELECT id FROM users WHERE lower(email) = :email"),
        {"email": email.lower()},
    ).mappings().first()

    if existing is None:
        kc_user_uuid, one_time_password = _keycloak() \
            .ensure_user_with_one_time_password(
                realm,
                username=email,
                email=email,
                tenant_id=str(job["tenant_id"]),
                display_name=display_name,
            )
        row = conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, department_id, email, "
                " display_name, status, mfa_enrolled, oidc_subject) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), "
                " CAST(:dept AS uuid), :email, :display, 'active', false, :sub) "
                "RETURNING id"
            ),
            {
                "id": str(_uuid.uuid4()), "tid": str(job["tenant_id"]),
                "dept": department_id, "email": email.lower(),
                "display": display_name, "sub": kc_user_uuid,
            },
        ).mappings().first()
        user_id = str(row["id"])
        audit.emit(
            conn, tenant_id=str(job["tenant_id"]), actor_role="system",
            actor_id=invited_by, action="user.created",
            target_type="user", target_id=user_id,
            payload={"email": email.lower(),
                     **({"department_id": department_id}
                        if department_id else {})},
        )
        conn.execute(
            text(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
                " department_id) VALUES (CAST(:id AS uuid), "
                " CAST(:tid AS uuid), CAST(:uid AS uuid), :rk, "
                " CAST(:dept AS uuid))"
            ),
            {
                "id": str(_uuid.uuid4()), "tid": str(job["tenant_id"]),
                "uid": user_id, "rk": role_key, "dept": department_id,
            },
        )
        audit.emit(
            conn, tenant_id=str(job["tenant_id"]), actor_role="system",
            actor_id=invited_by, action="role.assigned",
            target_type="user", target_id=user_id,
            payload={"role_key": role_key},
        )
    else:
        user_id = str(existing["id"])
        # Re-assert the one-time password so a retried job still delivers
        # a usable activation (the previous one stays invalid after use).
        _, one_time_password = _keycloak() \
            .ensure_user_with_one_time_password(
                realm,
                username=email,
                email=email,
                tenant_id=str(job["tenant_id"]),
                display_name=display_name,
            )

    return {
        "user_id": user_id,
        "email": email.lower(),
        "one_time_password": one_time_password,
        "instructions": (
            # Copy corregido (QA Pilar 1, t_ccf901f4): mismo contrato que el
            # provisioning — password forzado en el primer login; TOTP solo
            # forzado si la policy de la org require_mfa=true (spec 015).
            "Delivered to the inviting admin (tenant-scoped job result). "
            "Temporary: Keycloak forces a new password on first login; "
            "TOTP (MFA) enrollment is enforced only if the organization's "
            "policy require_mfa is enabled. It is invalid afterwards."
        ),
    }


def register() -> None:
    from ..jobs.service import register_handler

    register_handler(JOB_TYPE, handle_invite)

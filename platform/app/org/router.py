"""Organization endpoints (spec 012): users directory/invite/edit/disable,
branding, departments. Scoping mirrors audit D-AU-02 (D-ORG-01).
Spec 017 (roles & access): role assign/revoke + departments list."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import conflict, not_found, optional_uuid, permission_denied, require_uuid
from ..profiles import templates as profile_templates
from . import branding, departments, directory, invite, onboarding, roles

router = APIRouter(prefix="/api", dependencies=[Depends(require_mfa)])


# --- Users (REQ-ORG-100…104) --------------------------------------------------

class InviteBody(BaseModel):
    email: str
    # Regla de producto: todo usuario pertenece a un departamento.
    department_id: str
    role_key: str = "user"
    display_name: str | None = None


class UserUpdateBody(BaseModel):
    display_name: str | None = None
    department_id: str | None = None
    # Spec 017 D-17-05: optional status transition.
    status: Literal["active", "disabled"] | None = None


class RoleAssignBody(BaseModel):
    role_key: str
    department_id: str | None = None


@router.get("/users")
def list_users(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Directory within the caller's role scope (no entry permission:
    the scope IS the authorization, D-ORG-01)."""
    return directory.list_users(conn, principal)


@router.post("/users", status_code=202)
def invite_user(
    body: InviteBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    department_id = require_uuid(body.department_id, "department_id")
    if "users.invite" not in principal.permissions:
        raise permission_denied("Missing permission: users.invite")
    invite.validate_invite(
        conn, principal, email=body.email,
        department_id=department_id, role_key=body.role_key,
    )
    # D-ORG-02: audit + enqueue ride the request transaction atomically
    # (shared connection — the audit chain lock forbids a second tx here).
    return invite.enqueue_invite(
        conn, principal, email=body.email,
        department_id=department_id, role_key=body.role_key,
        display_name=body.display_name,
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Hard-delete a user and everything that depends on them.

    Order matters: dependent rows first (sessions, keys, devices, grants…),
    the user row last. Self-deletion and deleting the last org_admin are
    refused — the organization must never end up without an administrator.
    """
    user_id = require_uuid(user_id, "user_id")
    if "users.manage" not in principal.permissions:
        raise permission_denied("Missing permission: users.manage")
    if user_id == principal.user_id:
        raise permission_denied("You cannot delete your own account.")

    target = conn.execute(
        text(
            "SELECT u.id, u.email, (SELECT count(*) FROM user_roles ur"
            " JOIN catalog.roles r ON r.key = ur.role_key"
            " WHERE ur.user_id = u.id AND r.key = 'org_admin') AS admin_roles"
            " FROM users u WHERE u.id = CAST(:u AS uuid)"
        ),
        {"u": user_id},
    ).mappings().first()
    if target is None:
        raise not_found("User not found.")

    if target["admin_roles"]:
        remaining = conn.execute(
            text(
                "SELECT count(DISTINCT ur.user_id) FROM user_roles ur"
                " JOIN catalog.roles r ON r.key = ur.role_key"
                " WHERE r.key = 'org_admin' AND ur.user_id <> CAST(:u AS uuid)"
            ),
            {"u": user_id},
        ).scalar()
        if not remaining:
            raise conflict("Cannot delete the last organization administrator.")

    cleanup = [
        "DELETE FROM spend_ledger WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM budgets WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM policies WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM model_access WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM vault_credentials WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM connector_instances WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM api_keys WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM sessions WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM named_policy_assignments WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM notifications WHERE user_id = CAST(:u AS uuid)",
        "UPDATE vault_connections SET created_by = NULL WHERE created_by = CAST(:u AS uuid)",
        "DELETE FROM agent_grants WHERE grantee_user_id = CAST(:u AS uuid) OR granted_by = CAST(:u AS uuid)",
        "DELETE FROM agents WHERE owner_user_id = CAST(:u AS uuid)",
        "DELETE FROM user_roles WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM devices WHERE user_id = CAST(:u AS uuid)",
        "DELETE FROM users WHERE id = CAST(:u AS uuid)",
    ]
    for statement in cleanup:
        conn.execute(text(statement), {"u": user_id})

    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=principal.role_keys[0] if principal.role_keys else "org_admin",
        action="user.deleted",
        actor_id=principal.user_id,
        target_type="user",
        target_id=user_id,
        payload={"email": target["email"]},
    )
    return None


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdateBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    user_id = require_uuid(user_id, "user_id")  # t_378503a1: 422, not 500
    department_id = optional_uuid(body.department_id, "department_id")
    if "users.manage" not in principal.permissions:
        raise permission_denied("Missing permission: users.manage")
    return directory.update_user(
        conn, principal, user_id=user_id,
        display_name=body.display_name, department_id=department_id,
        status=body.status,
    )


# --- Roles (spec 017, feedback Miller #4) --------------------------------------
# Data-driven gate (D-17-01): the `roles.assign` permission is granted ONLY
# to org_admin in catalog.role_permissions (migration 0010). dept_admin keeps
# users.manage but can never touch roles — no escalation path.

@router.post("/users/{user_id}/roles", status_code=201)
def assign_role(
    user_id: str,
    body: RoleAssignBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    user_id = require_uuid(user_id, "user_id")  # t_378503a1: 422, not 500
    department_id = optional_uuid(body.department_id, "department_id")
    if "roles.assign" not in principal.permissions:
        raise permission_denied("Missing permission: roles.assign")
    return roles.assign_role(
        conn, principal, user_id=user_id, role_key=body.role_key,
        department_id=department_id,
    )


@router.delete("/users/{user_id}/roles/{role_key}", status_code=204)
def revoke_role(
    user_id: str,
    role_key: str,
    department_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    user_id = require_uuid(user_id, "user_id")  # t_378503a1: 422, not 500
    department_id = optional_uuid(department_id, "department_id")
    if "roles.assign" not in principal.permissions:
        raise permission_denied("Missing permission: roles.assign")
    roles.revoke_role(
        conn, principal, user_id=user_id, role_key=role_key,
        department_id=department_id,
    )


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    user_id = require_uuid(user_id, "user_id")  # t_378503a1: 422, not 500
    if "users.manage" not in principal.permissions:
        raise permission_denied("Missing permission: users.manage")
    return directory.disable_user(conn, principal, user_id=user_id)


# --- Branding (REQ-ORG-110/111 + spec 028 REQ-BRD-004) --------------------------

@router.get("/branding")
def get_branding(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Published effective branding (the desktop consumes it at runtime,
    REQ-BRD-002/003); any authenticated principal reads it. The DRAFT is
    never exposed here (REQ-BRD-004: unpublished changes are invisible)."""
    return branding.get_branding(conn)


@router.put("/branding")
def put_branding(
    body: dict,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Edit the branding DRAFT (spec 028 D-028-04 — used to publish in
    place; REQ-BRD-004 requires draft/publish). Publish with
    POST /api/branding/publish."""
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    return branding.put_draft(conn, principal, body=body)


@router.get("/branding/draft")
def get_branding_draft(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Draft editor state (fields + asset metadata + has_changes)."""
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    return branding.get_draft(conn)


@router.post("/branding/publish")
def publish_branding(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Atomic draft → published as version N+1 (audit branding.published)."""
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    return branding.publish(conn, principal)


@router.get("/branding/versions")
def list_branding_versions(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    return branding.list_versions(conn)


class RollbackBody(BaseModel):
    version: int = Field(gt=0)


@router.post("/branding/rollback")
def rollback_branding(
    body: RollbackBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Re-publish a previous version's exact data as a NEW version."""
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    return branding.rollback(conn, principal, version=body.version)


@router.post("/branding/assets")
async def upload_branding_asset(
    kind: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Upload logo_light/logo_dark/favicon (PNG/SVG ≤ 2 MB, magic-byte
    validation) into branding_assets and reference it from the draft."""
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    content = await file.read()
    return branding.upload_asset(
        conn, principal, kind=kind, content=content,
    )


@router.get("/branding/assets/{asset_id}")
def get_branding_asset(
    asset_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Serve asset bytes to any authenticated principal of the tenant (RLS
    isolates; the logo is tenant-scoped, not secret)."""
    asset_id = require_uuid(asset_id, "asset_id")
    asset = branding.get_asset(conn, asset_id)
    return Response(
        content=asset["content"],
        media_type=asset["mime"],
        headers={
            "ETag": f'"{asset["sha256"]}"',
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


# --- Departments (REQ-ORG-121 + spec 016 CRUD) -----------------------------------

class DepartmentBody(BaseModel):
    name: str
    profile_template: str | None = None
    # D-16-01: optional initial monthly budget — department + budget are
    # created atomically; invalid budget rolls back the whole create.
    initial_budget_cents: int | None = Field(default=None, gt=0)


class DepartmentRenameBody(BaseModel):
    name: str


@router.post("/departments", status_code=201)
def create_department(
    body: DepartmentBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    if "departments.manage" not in principal.permissions:
        raise permission_denied("Missing permission: departments.manage")
    return departments.create_department(
        conn, principal, name=body.name,
        profile_template=body.profile_template,
        initial_budget_cents=body.initial_budget_cents,
    )


@router.put("/departments/{department_id}")
def rename_department(
    department_id: str,
    body: DepartmentRenameBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Rename (spec 016 D-16-03): unique per tenant case-insensitive."""
    department_id = require_uuid(department_id, "department_id")  # t_378503a1
    if "departments.manage" not in principal.permissions:
        raise permission_denied("Missing permission: departments.manage")
    return departments.rename_department(
        conn, principal, department_id=department_id, name=body.name,
    )


@router.delete("/departments/{department_id}")
def delete_department(
    department_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Delete an EMPTY department (spec 016 D-16-02): blockers answer 409
    with counts; level-dept config cascades in the same transaction."""
    department_id = require_uuid(department_id, "department_id")  # t_378503a1
    if "departments.manage" not in principal.permissions:
        raise permission_denied("Missing permission: departments.manage")
    return departments.delete_department(
        conn, principal, department_id=department_id,
    )


@router.get("/departments")
def list_departments(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Minimal department list for the console selectors (spec 017
    REQ-RA-006). Any authenticated principal: RLS scopes the tenant (the
    directory already exposes department ids; names are not sensitive
    within the tenant)."""
    return departments.list_departments(conn)


# --- Organization structure onboarding (spec 038) ------------------------------

@router.get("/org/profile-templates")
def list_profile_templates(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """GET /api/org/profile-templates (REQ-DO-001): the 12-template catalog
    with es+en display fields, ordered by key. Read-only, no tenant data —
    any authenticated principal of the tenant (the wizard needs it and the
    CRUD already accepts template keys). `conn` is not used: the route
    exists to enforce a valid tenant session (MFA per the router gate)."""
    _ = principal, conn
    return {"templates": profile_templates.catalog_payload()}


@router.get("/org/onboarding")
def get_onboarding_state(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """GET /api/org/onboarding (REQ-DO-040): structure-onboarding flag for
    the caller's tenant. `departments.manage` only (org_admin) — the wizard
    gate is an org_admin surface; other roles get 403."""
    if "departments.manage" not in principal.permissions:
        raise permission_denied("Missing permission: departments.manage")
    return onboarding.get_state(conn)


class OnboardingCompleteBody(BaseModel):
    # "wizard" = changes confirmed; "skip" = closed without mutating.
    source: Literal["wizard", "skip"]
    created: int = Field(default=0, ge=0)
    renamed: int = Field(default=0, ge=0)
    deleted: int = Field(default=0, ge=0)


@router.put("/org/onboarding")
def complete_onboarding(
    body: OnboardingCompleteBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """PUT /api/org/onboarding (REQ-DO-021): close the structure onboarding.

    The wizard's mutations already traveled the department CRUD (each with
    its own audit event); this only flips the flag and emits
    `onboarding.structure_completed` {source, created, renamed, deleted} —
    on the pending -> completed transition only (idempotent re-PUT).
    """
    if "departments.manage" not in principal.permissions:
        raise permission_denied("Missing permission: departments.manage")
    return onboarding.complete(
        conn, principal,
        source=body.source, created=body.created,
        renamed=body.renamed, deleted=body.deleted,
    )

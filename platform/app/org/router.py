"""Organization endpoints (spec 012): users directory/invite/edit/disable,
branding, departments. Scoping mirrors audit D-AU-02 (D-ORG-01).
Spec 017 (roles & access): role assign/revoke + departments list."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import optional_uuid, permission_denied, require_uuid
from . import branding, departments, directory, invite, roles

router = APIRouter(prefix="/api", dependencies=[Depends(require_mfa)])


# --- Users (REQ-ORG-100…104) --------------------------------------------------

class InviteBody(BaseModel):
    email: str
    department_id: str | None = None
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
    department_id = optional_uuid(body.department_id, "department_id")
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

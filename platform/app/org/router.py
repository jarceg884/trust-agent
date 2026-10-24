"""Organization endpoints (spec 012): users directory/invite/edit/disable,
branding, departments. Scoping mirrors audit D-AU-02 (D-ORG-01)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import permission_denied
from . import branding, departments, directory, invite

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
    if "users.invite" not in principal.permissions:
        raise permission_denied("Missing permission: users.invite")
    invite.validate_invite(
        conn, principal, email=body.email,
        department_id=body.department_id, role_key=body.role_key,
    )
    # D-ORG-02: audit + enqueue ride the request transaction atomically
    # (shared connection — the audit chain lock forbids a second tx here).
    return invite.enqueue_invite(
        conn, principal, email=body.email,
        department_id=body.department_id, role_key=body.role_key,
        display_name=body.display_name,
    )


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdateBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    if "users.manage" not in principal.permissions:
        raise permission_denied("Missing permission: users.manage")
    return directory.update_user(
        conn, principal, user_id=user_id,
        display_name=body.display_name, department_id=body.department_id,
    )


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    if "users.manage" not in principal.permissions:
        raise permission_denied("Missing permission: users.manage")
    return directory.disable_user(conn, principal, user_id=user_id)


# --- Branding (REQ-ORG-110/111) ------------------------------------------------

@router.get("/branding")
def get_branding(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Any authenticated principal reads branding (the desktop consumes it
    at runtime, REQ-BRD-002/003); only org_admin can PUT."""
    return branding.get_branding(conn)


@router.put("/branding")
def put_branding(
    body: dict,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    if "branding.manage" not in principal.permissions:
        raise permission_denied("Missing permission: branding.manage")
    return branding.put_branding(conn, principal, body=body)


# --- Departments (REQ-ORG-121) ---------------------------------------------------

class DepartmentBody(BaseModel):
    name: str
    profile_template: str | None = None


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
    )

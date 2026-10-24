"""Model domain: catalog, access governance and LiteLLM virtual keys (spec 005)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.rbac import dept_scope
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import not_found, permission_denied
from . import access, keys

router = APIRouter(prefix="/api/models", dependencies=[Depends(require_mfa)])


@router.get("")
def list_models(conn: Connection = Depends(get_tx)):
    """Global model catalog (provider reference; pricing in USD cents/million)."""
    rows = conn.execute(
        text(
            "SELECT model_name, display_name, provider, "
            "input_price_cents_per_m, output_price_cents_per_m, status "
            "FROM catalog.model_catalog ORDER BY model_name"
        )
    ).mappings()
    return {"models": [dict(r) for r in rows]}


# --- Model access (REQ-MDL-001/005/006) ---------------------------------------

class AccessBody(BaseModel):
    model_name: str
    level: str  # org | dept | user
    department_id: str | None = None
    user_id: str | None = None
    mode: str = "central"


def _require_manage_models(principal: Principal) -> None:
    if "models.approve" not in principal.permissions:
        raise permission_denied("Missing permission: models.approve")


def _user_department(conn: Connection, user_id: str) -> str | None:
    row = conn.execute(
        text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        # Cross-tenant forged IDs simply do not exist (D-CP-09).
        raise not_found("User not found.")
    return str(row["department_id"]) if row["department_id"] else None


def _check_scope(
    conn: Connection,
    principal: Principal,
    level: str,
    department_id: str | None,
    user_id: str | None,
) -> None:
    """org level: org_admin only. dept/user levels: org_admin anywhere,
    dept_admin only within their own departments (D-CP-08)."""
    if "org_admin" in principal.role_keys:
        return
    scoped = dept_scope(principal) or []
    if level == "org":
        raise permission_denied(
            "Only an organization admin manages org-level approvals."
        )
    if level == "dept":
        if department_id not in scoped:
            raise permission_denied("Outside your department scope.")
        return
    if level == "user":
        if not user_id:
            raise permission_denied("user_id is required for user level.")
        if _user_department(conn, user_id) not in scoped:
            raise permission_denied("Outside your department scope.")
        return
    raise permission_denied("Invalid level.")


@router.get("/access")
def get_access(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Catalog with per-level approval status (REQ-MDL-006)."""
    if not ({"org_admin", "dept_admin"} & set(principal.role_keys)):
        raise permission_denied("Only admins view the Model Access matrix.")
    return access.access_overview(conn)


@router.put("/access", status_code=201)
def put_access(
    body: AccessBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_manage_models(principal)
    _check_scope(conn, principal, body.level,
                 body.department_id, body.user_id)
    return access.approve(
        conn, principal,
        model_name=body.model_name, level=body.level,
        department_id=body.department_id, user_id=body.user_id,
        mode=body.mode,
    )


@router.post("/access/revoke")
def revoke_access(
    body: AccessBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_manage_models(principal)
    _check_scope(conn, principal, body.level,
                 body.department_id, body.user_id)
    return access.revoke(
        conn, principal,
        model_name=body.model_name, level=body.level,
        department_id=body.department_id, user_id=body.user_id,
    )


@router.get("/allowed-for-me")
def allowed_for_me(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Effective approved models for the caller (intersection + policy)."""
    return {"models": access.effective_models_for_user(conn, principal)}


class VerifyBody(BaseModel):
    model_name: str


@router.post("/verify")
def verify_model(
    body: VerifyBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Governance gate for the desktop: 200, or 403 model_not_approved."""
    access.assert_model_allowed(conn, principal, body.model_name)
    return {"model_name": body.model_name, "allowed": True}


# --- Virtual keys (REQ-MDL-002) -----------------------------------------------

class IssueKeyBody(BaseModel):
    user_id: str | None = None  # None = self


@router.post("/keys", status_code=201)
def issue_key(
    body: IssueKeyBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    target = body.user_id or principal.user_id
    if target != principal.user_id:
        # Admins may issue for users in their scope (budget.user.manage).
        if "budget.user.manage" not in principal.permissions:
            raise permission_denied("Missing permission: budget.user.manage")
        _check_scope(conn, principal, "user", None, target)
    return keys.issue_key(conn, principal, user_id=target)


@router.get("/keys/me")
def my_keys(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Key metadata only — the key material was returned exactly once at
    issuance and is never exposed again (constitution IV)."""
    return {"keys": keys.list_keys_for_user(conn, principal.user_id)}


@router.post("/keys/{key_id}/revoke", status_code=204)
def revoke_key(
    key_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    row = conn.execute(
        text("SELECT user_id FROM api_keys WHERE id = CAST(:k AS uuid)"),
        {"k": key_id},
    ).mappings().first()
    if row is None:
        raise not_found("Key not found.")
    if str(row["user_id"]) != principal.user_id:
        if "budget.user.manage" not in principal.permissions:
            raise permission_denied("Missing permission: budget.user.manage")
        _check_scope(conn, principal, "user", None, str(row["user_id"]))
    keys.revoke_key(conn, principal, key_id=key_id)
    return None

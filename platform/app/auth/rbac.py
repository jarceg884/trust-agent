"""RBAC — data-driven from catalog.role_permissions (spec 001 §2.1, I10).

The permission matrix lives in the database (catalog schema), never as code
constants. Roles come from user_roles within the tenant scope.
"""
from fastapi import Depends

from .service import Principal, get_principal


def dept_scope(principal: Principal) -> list[str] | None:
    """Departments a principal may administer (normalized to str — the DB
    driver returns UUID objects for uuid columns).

    - org_admin: None (whole tenant)
    - dept_admin: the departments of their dept_admin assignments
    - user: their own department — callers decide read/write semantics.
    """
    if "org_admin" in principal.role_keys:
        return None
    scoped = [str(ra.department_id) for ra in principal.roles
              if ra.role_key == "dept_admin" and ra.department_id]
    if scoped:
        return scoped
    return [principal.department_id] if principal.department_id else []


def require_permission(permission: str):
    """Route dependency: 403 permission_denied unless the principal holds it."""

    def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if permission not in principal.permissions:
            from ..errors import permission_denied

            raise permission_denied(f"Missing permission: {permission}")
        return principal

    return _check


def require_role(*role_keys: str):
    """Route dependency: 403 unless the principal holds one of the roles."""

    def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if not set(role_keys) & set(principal.role_keys):
            from ..errors import permission_denied

            raise permission_denied(
                f"Requires one of roles: {', '.join(role_keys)}"
            )
        return principal

    return _check


_ROLE_PRECEDENCE = ("org_admin", "dept_admin", "user")


def top_role_key(role_keys: list[str]) -> str:
    """Highest-precedence role for audit actor_role (org_admin > dept_admin > user)."""
    for role in _ROLE_PRECEDENCE:
        if role in role_keys:
            return role
    return "user"

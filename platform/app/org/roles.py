"""Role assignment/revocation (spec 017, feedback Miller #4).

Solo org_admin: la puerta es el permiso data-driven `roles.assign`
(catalog.role_permissions, migración 0010) — verificado en el router con el
patrón rbac existente. Sin auto-escalada (D-17-02): un principal jamás
modifica sus propios roles. Audit `role.assigned`/`role.revoked` — el mismo
contrato que ya emiten provision e invite (spec 008 §5).
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import top_role_key
from ..auth.service import Principal
from ..errors import not_found, validation_failed
from .directory import _get_user, _roles_by_user
from .invite import TENANT_ROLES


def _self_guard(principal: Principal, user_id: str) -> None:
    """D-17-02: sin auto-escalada ni auto-privación de roles."""
    if user_id == principal.user_id:
        raise validation_failed("You cannot modify your own roles.")


def _fresh_roles(conn: Connection, user_id: str) -> list[dict]:
    return _roles_by_user(conn).get(user_id, [])


def assign_role(
    conn: Connection, principal: Principal, *, user_id: str,
    role_key: str, department_id: str | None,
) -> dict:
    """POST /api/users/{id}/roles — asignar rol con scope de depto (D-17-03)."""
    _self_guard(principal, user_id)
    if role_key not in TENANT_ROLES:
        raise validation_failed(
            f"role_key must be one of: {', '.join(TENANT_ROLES)}."
        )
    if role_key == "dept_admin" and department_id is None:
        raise validation_failed("dept_admin requires a department scope.")
    # 404 sin oráculo cross-tenant/scope (D-CP-09) — RLS aisla el tenant.
    _get_user(conn, user_id)
    if department_id is not None:
        dept = conn.execute(
            text("SELECT id FROM departments WHERE id = CAST(:d AS uuid)"),
            {"d": department_id},
        ).mappings().first()
        if dept is None:
            raise validation_failed("Unknown department.")

    duplicate = conn.execute(
        text(
            "SELECT 1 FROM user_roles WHERE user_id = CAST(:u AS uuid) "
            "AND role_key = :rk AND department_id IS NOT DISTINCT FROM "
            "CAST(:d AS uuid)"
        ),
        {"u": user_id, "rk": role_key, "d": department_id},
    ).first()
    if duplicate is not None:
        raise validation_failed(
            "User already has that role in that scope."
        )

    conn.execute(
        text(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
            " department_id, granted_by) VALUES (CAST(:id AS uuid), "
            " CAST(:tid AS uuid), CAST(:uid AS uuid), :rk, "
            " CAST(:dept AS uuid), CAST(:by AS uuid))"
        ),
        {
            "id": str(_uuid.uuid4()), "tid": principal.tenant_id,
            "uid": user_id, "rk": role_key, "dept": department_id,
            "by": principal.user_id,
        },
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="role.assigned", actor_id=principal.user_id,
        target_type="user", target_id=user_id,
        payload={
            "role_key": role_key,
            **({"department_id": department_id} if department_id else {}),
        },
    )
    return {"user_id": user_id, "roles": _fresh_roles(conn, user_id)}


def revoke_role(
    conn: Connection, principal: Principal, *, user_id: str,
    role_key: str, department_id: str | None,
) -> None:
    """DELETE /api/users/{id}/roles/{role_key} — quitar rol (D-17-04).

    `department_id` (query param) desambigua scopes múltiples de dept_admin;
    sin él y con más de una fila del mismo rol → 422.
    """
    _self_guard(principal, user_id)
    if role_key not in TENANT_ROLES:
        raise validation_failed(
            f"role_key must be one of: {', '.join(TENANT_ROLES)}."
        )
    _get_user(conn, user_id)
    rows = conn.execute(
        text(
            "SELECT id, department_id FROM user_roles "
            "WHERE user_id = CAST(:u AS uuid) AND role_key = :rk "
            "ORDER BY created_at"
        ),
        {"u": user_id, "rk": role_key},
    ).mappings().all()
    if not rows:
        raise not_found("Role not assigned.")
    if len(rows) > 1 and department_id is None:
        raise validation_failed(
            "User has that role in several scopes: department_id is "
            "required to disambiguate."
        )
    target = next(
        (
            r for r in rows
            if (str(r["department_id"]) if r["department_id"] else None)
            == department_id
        ),
        None,
    )
    if target is None:
        raise not_found("Role not assigned in that scope.")

    conn.execute(
        text("DELETE FROM user_roles WHERE id = CAST(:id AS uuid)"),
        {"id": str(target["id"])},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="role.revoked", actor_id=principal.user_id,
        target_type="user", target_id=user_id,
        payload={
            "role_key": role_key,
            **({"department_id": department_id} if department_id else {}),
        },
    )

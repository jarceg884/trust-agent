"""Department creation (spec 012, REQ-ORG-121, D-ORG-06).

Only the create action exists today (what the console consumes); full
CRUD arrives with its own evolution. `profile_template` is validated
against the registry (spec 003 profiles, REQ-PRF-002).
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import top_role_key
from ..auth.service import Principal
from ..errors import validation_failed
from ..profiles.templates import get_template


def create_department(
    conn: Connection, principal: Principal, *,
    name: str, profile_template: str | None,
) -> dict:
    """POST /api/departments — 201 {department_id, name}."""
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise validation_failed("name must be 1-80 characters.")
    if profile_template is not None:
        try:
            get_template(profile_template)
        except ValueError as exc:
            raise validation_failed(str(exc)) from None

    duplicate = conn.execute(
        text("SELECT id FROM departments WHERE lower(name) = lower(:n)"),
        {"n": name},
    ).mappings().first()
    if duplicate is not None:
        raise validation_failed(
            "A department with that name already exists in this organization."
        )

    department_id = str(_uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO departments (id, tenant_id, name, profile_template) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :tpl)"
        ),
        {"id": department_id, "tid": principal.tenant_id,
         "name": name, "tpl": profile_template},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="department.created", actor_id=principal.user_id,
        target_type="department", target_id=department_id,
        payload={"name": name,
                 **({"profile_template": profile_template}
                    if profile_template else {})},
    )
    return {"department_id": department_id, "name": name}

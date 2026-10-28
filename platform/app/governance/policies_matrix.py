"""Vista unificada de policies (spec 033): matriz por niveles + diff efectivo.

Dos lecturas READ-ONLY para la página dedicada "Policies" del Admin Console:

- ``matrix``: catálogo tipado completo + valores org + overrides por
  departamento + overrides por usuario (solo quienes TIENEN overrides),
  con nombres legibles para render.
- ``effective_diff``: la policy efectiva (deny-overrides real via
  ``combinator.combine``) de un usuario arbitrario + provenance por key
  (default/org/dept/user, tightened_by_dept/user, denied).

Cero mutaciones: la edición de la página usa los PUT/DELETE existentes
(governance.router / policies.upsert / policies.deactivate) con su
validación 422, no-relajación REQ-POL-007 y audit intactos.
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.rbac import dept_scope
from ..auth.service import Principal
from ..errors import not_found, permission_denied, require_uuid
from .combinator import (
    ORG_ONLY_KEYS,
    STRING_RANKS,
    WEB_DOMAIN_MODES,
    combine,
)
from .policies import _canonical, list_level
from .service import _load_level, load_policy_catalog

# Espejo de permisos de GET /api/policies (list_policies): quien puede
# VER policies crudas puede ver la matriz.
_VIEW_PERMISSIONS = ("policies.org.manage", "policies.dept.manage",
                     "audit.view")


def _require_view(principal: Principal) -> None:
    if not (set(_VIEW_PERMISSIONS) & principal.permissions):
        raise permission_denied("Only admins view the policy matrix.")


def _scope_depts(principal: Principal) -> list[str] | None:
    """None = sin restricción (org_admin); lista = scope de dept_admin."""
    if "org_admin" in principal.role_keys:
        return None
    if "dept_admin" in principal.role_keys:
        return dept_scope(principal) or []
    # Otros roles con permiso de vista (p. ej. audit.view): solo lectura
    # de la baseline org, sin departamentos ni usuarios.
    return []


def _choices_for(key: str) -> list[str] | None:
    """Enums reales del contrato (D-33-05): la UI renderiza selects."""
    if key == "web_domain_policy":
        return list(WEB_DOMAIN_MODES)
    ranks = STRING_RANKS.get(key)
    if ranks:
        # Ordenados de más a menos restrictivo (como se muestran en UI).
        return [k for k, _ in sorted(ranks.items(), key=lambda kv: -kv[1])]
    return None


def _catalog(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT key, value_type, invariable, default_value "
            "FROM catalog.policy_keys ORDER BY key"
        )
    ).mappings()
    return [
        {
            "key": r["key"],
            "value_type": r["value_type"],
            "invariable": r["invariable"],
            "org_only": r["key"] in ORG_ONLY_KEYS,
            "default_value": r["default_value"],
            "choices": _choices_for(r["key"]),
        }
        for r in rows
    ]


def matrix(conn: Connection, principal: Principal) -> dict[str, Any]:
    """GET /api/policies/matrix — catálogo + org + deptos + usuarios."""
    _require_view(principal)
    scope = _scope_depts(principal)

    org = list_level(conn, level="org", department_id=None, user_id=None)

    departments: list[dict[str, Any]] = []
    users: list[dict[str, Any]] = []
    if scope is None or scope:
        dept_rows = conn.execute(
            text(
                "SELECT d.id, d.name FROM departments d "
                "WHERE (CAST(:scope AS uuid[]) IS NULL "
                "OR d.id = ANY(CAST(:scope AS uuid[]))) "
                "ORDER BY d.name"
            ),
            {"scope": scope},
        ).mappings()
        departments = [
            {
                "department_id": str(r["id"]),
                "name": r["name"],
                "policies": list_level(
                    conn, level="dept", department_id=str(r["id"]),
                    user_id=None,
                ),
            }
            for r in dept_rows
        ]

        # Solo usuarios CON overrides activos a nivel usuario (la tabla
        # maestra no necesita a todo el directorio: el picker de la página
        # usa GET /api/users).
        user_rows = conn.execute(
            text(
                "SELECT DISTINCT u.id, u.email, u.display_name, "
                "u.department_id, d.name AS department_name "
                "FROM users u "
                "JOIN policies p ON p.user_id = u.id "
                "LEFT JOIN departments d ON d.id = u.department_id "
                "WHERE p.is_active "
                "AND (CAST(:scope AS uuid[]) IS NULL "
                "OR u.department_id = ANY(CAST(:scope AS uuid[]))) "
                "ORDER BY u.email"
            ),
            {"scope": scope},
        ).mappings()
        for r in user_rows:
            users.append({
                "user_id": str(r["id"]),
                "email": r["email"],
                "display_name": r["display_name"],
                "department_id": str(r["department_id"])
                if r["department_id"] else None,
                "department_name": r["department_name"],
                "policies": list_level(
                    conn, level="user", department_id=str(r["department_id"])
                    if r["department_id"] else None,
                    user_id=str(r["id"]),
                ),
            })

    return {
        "catalog": _catalog(conn),
        "org": org,
        "departments": departments,
        "users": users,
        "scope": "org" if scope is None else "dept",
    }


def _is_denied(key: str, effective: Any) -> bool:
    """D-33-03: 'negado' es un hecho del valor efectivo, no de la UI.

    - boolean ``allow_*`` en false → la capacidad está vetada;
    - lista ``allowed_*`` vacía → nada de esa categoría pasa;
    - ``web_domain_policy`` allowlist sin dominios → sin web.
    Los ``require_*`` en true son pisos ACTIVOS, no denegaciones.
    """
    if key.startswith("allow_") and effective is False:
        return True
    if key.startswith("allowed_") and effective == []:
        return True
    if key == "web_domain_policy" and isinstance(effective, dict):
        if effective.get("mode") == "allowlist" and not effective.get("domains"):
            return True
    return False


def effective_diff(
    conn: Connection, principal: Principal, user_id: str,
) -> dict[str, Any]:
    """GET /api/policies/effective?user_id=… — efectiva + provenance."""
    _require_view(principal)
    user_id = require_uuid(user_id, "user_id")
    scope = _scope_depts(principal)

    row = conn.execute(
        text(
            "SELECT u.id, u.email, u.display_name, u.department_id, "
            "u.status, d.name AS department_name "
            "FROM users u LEFT JOIN departments d ON d.id = u.department_id "
            "WHERE u.id = CAST(:u AS uuid)"
        ),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        raise not_found("User not found.")
    department_id = str(row["department_id"]) if row["department_id"] else None
    if scope is not None and department_id not in scope:
        # Sin oráculo de existencia fuera del scope (patrón del router).
        raise not_found("User not found.")

    catalog = load_policy_catalog(conn)
    org = _load_level(conn, level="org", department_id=None, user_id=None)
    dept = (
        _load_level(conn, level="dept", department_id=department_id,
                    user_id=None)
        if department_id else {}
    )
    user = _load_level(conn, level="user", department_id=department_id,
                       user_id=user_id)

    v_org = combine(org, {}, {}, catalog)
    v_org_dept = combine(org, dept, {}, catalog)
    v_full = combine(org, dept, user, catalog)

    keys: list[dict[str, Any]] = []
    for key, meta in catalog.items():
        effective = v_full[key]
        org_only = key in ORG_ONLY_KEYS
        keys.append({
            "key": key,
            "value_type": meta["value_type"],
            "invariable": meta["invariable"],
            "org_only": org_only,
            "default_value": meta["default_value"],
            "org_value": org.get(key),
            "dept_value": dept.get(key),
            "user_value": user.get(key),
            "org_effective": v_org[key],
            "dept_effective": v_org_dept[key],
            "effective": effective,
            # Org-only keys resolve as the gate does (org row or default);
            # a legacy dept/user row must not claim provenance it cannot have.
            "source": (
                "org" if org_only and key in org
                else "default" if org_only
                else "user" if key in user
                else "dept" if key in dept
                else "org" if key in org
                else "default"
            ),
            "tightened_by_dept": (
                key in dept
                and _canonical(v_org_dept[key]) != _canonical(v_org[key])
            ),
            "tightened_by_user": (
                key in user
                and _canonical(v_full[key]) != _canonical(v_org_dept[key])
            ),
            "denied": _is_denied(key, effective),
        })

    return {
        "user": {
            "user_id": str(row["id"]),
            "email": row["email"],
            "display_name": row["display_name"],
            "department_id": department_id,
            "department_name": row["department_name"],
            "status": row["status"],
        },
        "keys": keys,
    }

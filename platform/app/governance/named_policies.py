"""Políticas nombradas (spec 041, visión Miller) — preset de policy keys.

Una política nombrada = ``name`` + ``description`` + ``definition`` (mapa
``key → value`` sobre las 44 keys no invariables del catálogo tipado).
Asignarla MATERIALIZA el mapa vía ``policies.upsert`` existente (D-41-01):
la tabla ``policies`` sigue siendo la ÚNICA verdad para combinator, matrix,
effective y bundle JWS. El engine NO se reescribe — se mapea:

- deny-overrides, 422 de no-relajación (REQ-POL-007), audit por key y el
  sweep de conectores quedan intactos GRATIS porque el camino de escritura
  es el mismo (``policies.upsert`` / ``policies.deactivate``).
- Asignación atómica (D-41-04): todos los upserts corren en la MISMA
  transacción del request (``get_tx``); si cualquier key falla la
  validación, nada se escribe y el 422 lleva el detalle por key.
- Ownership por snapshot (D-41-05): cada asignación guarda ``applied_keys``
  (lo efectivamente escrito); desasignar solo desactiva las keys cuyo
  valor actual siga siendo el del snapshot — el trabajo manual explícito
  y la propiedad de OTRA política se conservan y se reportan.
- Editar NO auto-propaga (D-41-06): las asignaciones quedan ``stale``
  (``policy.updated_at > assignment.applied_at``) hasta un reapply
  explícito, best-effort por destinatario (savepoint por asignación).
- Permisos espejo de los PUT por nivel (D-41-09): ``require_recipient_scope``
  es la MISMA regla que ``_policy_scope`` de governance/router.py (única
  implementación: el router delega aquí).
- Audit ``named_policy.*`` vía ``audit.emit`` existente (D-41-11).

REQ-041-010: ``combinator.py`` y ``policies.py`` solo se IMPORTAN — cero
cambios de semántica. Las funciones privadas de policies (``_catalog_entry``,
``_validate_value``, ``_current_value``, ``_relaxes``, ``_canonical``) se
reusan read-only para que la validación del preset sea EXACTAMENTE la del
upsert por key.
"""
import json
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..audit import service as audit
from ..auth.rbac import dept_scope
from ..auth.service import Principal
from ..errors import conflict, not_found, permission_denied, validation_failed
from . import named_policy_builder as builder
from . import policies
from .combinator import ORG_ONLY_KEYS

NAME_MAX = 120


def _api_message(exc: Exception) -> str:
    """Mensaje estable de un ApiError (o repr de cualquier otra excepción)."""
    return str(getattr(exc, "message", None) or exc)


# ---------------------------------------------------------------------------
# Permisos (D-41-09): espejo EXACTO de los PUT por nivel
# ---------------------------------------------------------------------------

def require_recipient_scope(
    conn: Connection, principal: Principal, level: str,
    department_id: str | None, user_id: str | None,
) -> None:
    """org: policies.org.manage (org_admin). dept: policies.dept.manage,
    dept_admin limited to own departments. user: org_admin anywhere,
    dept_admin within their departments.

    Única implementación de la regla: governance/router.py::_policy_scope
    delega aquí (D-41-09 — cero superficie de bypass entre el panel por
    key de spec 036 y las políticas nombradas).
    """
    if level == "org":
        if "policies.org.manage" not in principal.permissions:
            raise permission_denied("Missing permission: policies.org.manage")
        return
    if level == "dept":
        if not ({"policies.dept.manage", "policies.org.manage"}
                & principal.permissions):
            raise permission_denied("Missing permission: policies.dept.manage")
        if "org_admin" not in principal.role_keys:
            if department_id not in (dept_scope(principal) or []):
                raise not_found("Department not found.")  # no scope oracle
        return
    if level == "user":
        if not ({"policies.dept.manage", "policies.org.manage"}
                & principal.permissions):
            raise permission_denied("Missing permission: policies.dept.manage")
        if "org_admin" not in principal.role_keys:
            row = conn.execute(
                text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
                {"u": user_id},
            ).mappings().first()
            if row is None:
                raise not_found("User not found.")
            dept = str(row["department_id"]) if row["department_id"] else None
            if dept not in (dept_scope(principal) or []):
                # Espejo EXACTO del PUT por nivel (mismo mensaje/contrato).
                raise permission_denied("Outside your department scope.")
        return
    # Espejo EXACTO del PUT/DELETE por nivel (mismo mensaje/contrato).
    raise permission_denied("Invalid level.")


def require_manage_policies(principal: Principal) -> None:
    """Ver el builder / crear / editar / borrar políticas nombradas exige
    cualquiera de los dos permisos de gestión de policies (D-41-09)."""
    if not ({"policies.org.manage", "policies.dept.manage"}
            & principal.permissions):
        raise permission_denied(
            "Missing permission: policies.org.manage or policies.dept.manage"
        )


# ---------------------------------------------------------------------------
# Validación de la definition (REQ-041-001: MISMA validación que el upsert)
# ---------------------------------------------------------------------------

def validate_definition(conn: Connection, definition: dict) -> None:
    """Cada key debe existir en catalog.policy_keys, NO ser invariable y
    pasar la MISMA validación de value_type que el upsert por key. Los
    errores se reportan TODOS de una vez (detalle por key)."""
    if not isinstance(definition, dict) or not definition:
        raise validation_failed(
            "definition must be a non-empty map of policy key -> value."
        )
    errors: list[dict] = []
    for key, value in definition.items():
        try:
            entry = policies._catalog_entry(conn, key)
            if entry["invariable"]:
                raise validation_failed(
                    f"Policy '{key}' is a provider invariable (floor) and "
                    "cannot be set at any level (REQ-POL-004)."
                )
            policies._validate_value(entry, value)
        except Exception as exc:  # ApiError de policies: mensaje por key
            errors.append({"key": key, "message": _api_message(exc)})
    if errors:
        raise validation_failed(
            "Invalid definition: " + "; ".join(
                f"'{e['key']}': {e['message']}" for e in errors
            ),
            details={"errors": errors},
        )


def _normalize_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise validation_failed(
            f"Policy name must be 1-{NAME_MAX} characters."
        )
    return name


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def _policy_row(conn: Connection, principal: Principal, policy_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT id, name, description, definition, source_template, "
            "created_by, created_at, updated_at FROM named_policies "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": policy_id},
    ).mappings().first()  # RLS: solo el tenant del actor
    if row is None:
        raise not_found("Named policy not found.")
    return dict(row)


def _assignment_rows(conn: Connection, principal: Principal,
                     policy_id: str | None = None) -> list[dict]:
    """Asignaciones del tenant (o de una política) con el badge stale.

    Para un dept_admin se filtran a su alcance (misma regla que
    ``recipients``): asignaciones a sus deptos y a usuarios de sus deptos.
    """
    sql = (
        "SELECT a.id, a.named_policy_id, a.level, a.department_id, a.user_id, "
        "a.applied_keys, a.applied_at, p.name, p.source_template, "
        "(p.updated_at > a.applied_at) AS stale "
        "FROM named_policy_assignments a JOIN named_policies p "
        "  ON p.id = a.named_policy_id"
    )
    params: dict = {}
    if policy_id is not None:
        sql += " WHERE a.named_policy_id = CAST(:pid AS uuid)"
        params["pid"] = policy_id
    sql += " ORDER BY a.created_at"
    rows = [dict(r) for r in conn.execute(text(sql), params).mappings()]
    if "org_admin" in principal.role_keys:
        return rows
    scoped = set(dept_scope(principal) or [])
    visible = []
    for r in rows:
        if r["level"] == "dept" and str(r["department_id"]) in scoped:
            visible.append(r)
        elif r["level"] == "user":
            dept = conn.execute(
                text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
                {"u": str(r["user_id"])},
            ).mappings().first()
            if dept is not None and dept["department_id"] \
                    and str(dept["department_id"]) in scoped:
                visible.append(r)
    return visible


def _assignment_out(r: dict) -> dict:
    return {
        "assignment_id": str(r["id"]),
        "named_policy_id": str(r["named_policy_id"]),
        "level": r["level"],
        "department_id": str(r["department_id"]) if r["department_id"] else None,
        "user_id": str(r["user_id"]) if r["user_id"] else None,
        "applied_keys": r["applied_keys"],
        "applied_at": r["applied_at"].isoformat(),
        "stale": bool(r["stale"]),
    }


def _policy_out(row: dict, assignments: list[dict]) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "definition": row["definition"],
        "source_template": row["source_template"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "assignments": assignments,
        "assignments_count": len(assignments),
        "stale_assignments": any(a["stale"] for a in assignments),
    }


def list_policies(conn: Connection, principal: Principal) -> dict:
    """GET /api/named-policies: lista + bloque ``builder`` (D-41-02) +
    plantillas del contrato. La UI JAMÁS hardcodea el builder."""
    require_manage_policies(principal)
    rows = [dict(r) for r in conn.execute(
        text(
            "SELECT id, name, description, definition, source_template, "
            "created_at, updated_at FROM named_policies "
            "ORDER BY source_template NULLS LAST, created_at"
        )
    ).mappings()]
    by_policy: dict[str, list[dict]] = {}
    for a in _assignment_rows(conn, principal):
        by_policy.setdefault(str(a["named_policy_id"]), []).append(a)
    return {
        "policies": [
            _policy_out(r, [
                _assignment_out(a)
                for a in by_policy.get(str(r["id"]), [])
            ])
            for r in rows
        ],
        "builder": builder.builder_block(),
    }


def recipients(conn: Connection, principal: Principal) -> dict:
    """GET /api/named-policies/recipients (D-41-12): qué políticas nombradas
    aplican a cada destinatario del alcance — leído de
    named_policy_assignments, SIN re-derivar valores efectivos (la efectiva
    por key sigue viniendo SOLO de matrix/effective, D-36-04)."""
    require_manage_policies(principal)
    assignments = _assignment_rows(conn, principal)

    # Destinatarios del alcance: org (solo org_admin), deptos y usuarios.
    recipients_out: list[dict] = []

    def _recipient(level: str, department_id: str | None,
                   user_id: str | None) -> dict:
        return {
            "level": level,
            "department_id": department_id,
            "user_id": user_id,
            "policies": [],
        }

    if "org_admin" in principal.role_keys:
        org = _recipient("org", None, None)
        dept_rows = conn.execute(
            text("SELECT id, name FROM departments ORDER BY name")
        ).mappings()
        user_rows = conn.execute(
            text("SELECT id, display_name, email, department_id "
                 "FROM users WHERE status = 'active' ORDER BY display_name")
        ).mappings()
        depts = [dict(r) for r in dept_rows]
        users = [dict(r) for r in user_rows]
    else:
        org = None
        scoped = set(dept_scope(principal) or [])
        depts = [dict(r) for r in conn.execute(
            text("SELECT id, name FROM departments ORDER BY name")
        ).mappings() if str(r["id"]) in scoped]
        users = [dict(r) for r in conn.execute(
            text("SELECT id, display_name, email, department_id FROM users "
                 "WHERE status = 'active' ORDER BY display_name")
        ).mappings() if r["department_id"]
            and str(r["department_id"]) in scoped]

    index: dict[tuple, dict] = {}
    if org is not None:
        recipients_out.append(org)
        index[("org", None, None)] = org
    for d in depts:
        rec = _recipient("dept", str(d["id"]), None)
        rec["name"] = d["name"]
        recipients_out.append(rec)
        index[("dept", str(d["id"]), None)] = rec
    for u in users:
        rec = _recipient("user", str(u["department_id"]), str(u["id"]))
        rec["name"] = u["display_name"] or u["email"]
        recipients_out.append(rec)
        index[("user", str(u["department_id"]), str(u["id"]))] = rec

    for a in assignments:
        key = (
            a["level"],
            str(a["department_id"]) if a["department_id"] else None,
            str(a["user_id"]) if a["user_id"] else None,
        )
        rec = index.get(key)
        if rec is None:
            continue  # fuera de alcance (defensivo: ya filtrado arriba)
        rec["policies"].append({
            "assignment_id": str(a["id"]),
            "named_policy_id": str(a["named_policy_id"]),
            "name": a["name"],
            "source_template": a["source_template"],
            "stale": bool(a["stale"]),
            "applied_at": a["applied_at"].isoformat(),
        })
    return {"recipients": recipients_out}


# ---------------------------------------------------------------------------
# CRUD (REQ-041-001/002)
# ---------------------------------------------------------------------------

def _template_definition_for(
    conn: Connection, principal: Principal, template_slug: str,
) -> dict:
    """Definition de partida de una plantilla (REQ-041-001): la fila
    sembrada del tenant (editable, D-41-08) si existe; si el admin la
    borró, el contrato canónico como fallback honesto."""
    row = conn.execute(
        text(
            "SELECT definition FROM named_policies "
            "WHERE source_template = :st"
        ),
        {"st": f"builtin:{template_slug}"},
    ).mappings().first()
    if row is not None:
        return dict(row["definition"])
    contract = builder.template_definition(template_slug)
    if contract is None:
        raise validation_failed(f"Unknown template_slug: '{template_slug}'.")
    return contract


def create(
    conn: Connection, principal: Principal, *,
    name: str, description: str | None = None,
    definition: dict | None = None, template_slug: str | None = None,
) -> dict:
    require_manage_policies(principal)
    name = _normalize_name(name)
    if definition is None:
        if template_slug is None:
            raise validation_failed(
                "definition is required (or template_slug to start from a "
                "template)."
            )
        definition = _template_definition_for(conn, principal, template_slug)
    validate_definition(conn, definition)
    try:
        row = conn.execute(
            text(
                "INSERT INTO named_policies "
                "  (tenant_id, name, description, definition, source_template, "
                "   created_by) "
                "VALUES (CAST(:t AS uuid), :name, :description, "
                "        CAST(:definition AS jsonb), NULL, CAST(:actor AS uuid)) "
                "RETURNING id, created_at, updated_at"
            ),
            {
                "t": principal.tenant_id, "name": name,
                "description": description,
                "definition": json.dumps(definition),
                "actor": principal.user_id,
            },
        ).mappings().first()
    except IntegrityError:
        raise validation_failed(
            f"A named policy '{name}' already exists in this organization."
        ) from None
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=policies._actor_role(principal),
        action="named_policy.created", actor_id=principal.user_id,
        target_type="named_policy", target_id=str(row["id"]),
        payload={"name": name, "keys": sorted(definition.keys()),
                 **({"template_slug": template_slug}
                    if template_slug else {})},
    )
    return {
        "id": str(row["id"]), "name": name, "description": description,
        "definition": definition, "source_template": None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "assignments": [], "assignments_count": 0, "stale_assignments": False,
    }


def update(
    conn: Connection, principal: Principal, policy_id: str, *,
    name: str | None = None, description: str | None = None,
    definition: dict | None = None,
) -> dict:
    """Editar NO auto-propaga (D-41-06): las asignaciones existentes quedan
    stale (policy.updated_at > assignment.applied_at) hasta un reapply
    explícito."""
    require_manage_policies(principal)
    row = _policy_row(conn, principal, policy_id)
    if name is not None:
        name = _normalize_name(name)
    if definition is not None:
        validate_definition(conn, definition)
    new_name = name if name is not None else row["name"]
    new_description = description if description is not None else row["description"]
    new_definition = definition if definition is not None else row["definition"]
    try:
        conn.execute(
            text(
                "UPDATE named_policies SET name = :name, "
                "description = :description, "
                "definition = CAST(:definition AS jsonb) "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {
                "id": policy_id, "name": new_name,
                "description": new_description,
                "definition": json.dumps(new_definition),
            },
        )
    except IntegrityError:
        raise validation_failed(
            f"A named policy '{new_name}' already exists in this organization."
        ) from None
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=policies._actor_role(principal),
        action="named_policy.updated", actor_id=principal.user_id,
        target_type="named_policy", target_id=policy_id,
        payload={"name": new_name, "keys": sorted(new_definition.keys())},
    )
    # Re-leer: el trigger set_updated_at estampa el updated_at REAL (la
    # respuesta debe mostrar el marcador de stale fresco, D-41-06).
    row = _policy_row(conn, principal, policy_id)
    assignments = [
        _assignment_out(a)
        for a in _assignment_rows(conn, principal, policy_id=policy_id)
    ]
    return _policy_out(
        {
            **row, "name": new_name, "description": new_description,
            "definition": new_definition,
        },
        assignments,
    )


def delete(
    conn: Connection, principal: Principal, policy_id: str, *,
    cascade: bool = False,
) -> None:
    """Borrado con asignaciones activas → 409 con la lista (D-41-07);
    ``cascade=unassign`` desasigna todas (REQ-041-005) y luego borra."""
    require_manage_policies(principal)
    row = _policy_row(conn, principal, policy_id)
    assignments = _assignment_rows(conn, principal, policy_id=policy_id)
    if assignments and not cascade:
        raise conflict(
            "Named policy has active assignments; unassign them first or "
            "repeat with cascade=unassign.",
            details={
                "assignments": [
                    {
                        "assignment_id": str(a["id"]), "level": a["level"],
                        "department_id": str(a["department_id"])
                        if a["department_id"] else None,
                        "user_id": str(a["user_id"]) if a["user_id"] else None,
                    }
                    for a in assignments
                ]
            },
        )
    # Cascade: cada unassign respeta el permiso del actor sobre SU
    # destinatario (D-41-09) — un dept_admin no puede desasignar una
    # asignación de org o de un depto ajeno vía cascade. Si cualquier
    # destinatario está fuera de alcance → 403 y NADA se borra (la
    # transacción del request revierte entera).
    for a in assignments:
        require_recipient_scope(
            conn, principal, a["level"],
            str(a["department_id"]) if a["department_id"] else None,
            str(a["user_id"]) if a["user_id"] else None,
        )
    for a in assignments:
        _unassign_one(conn, principal, a, policy_name=row["name"])
    conn.execute(
        text("DELETE FROM named_policies WHERE id = CAST(:id AS uuid)"),
        {"id": policy_id},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=policies._actor_role(principal),
        action="named_policy.deleted", actor_id=principal.user_id,
        target_type="named_policy", target_id=policy_id,
        payload={"name": row["name"],
                 "keys": sorted(row["definition"].keys()),
                 "cascade": bool(assignments)},
    )


# ---------------------------------------------------------------------------
# Preview / assign / unassign / reapply (REQ-041-004/005/006)
# ---------------------------------------------------------------------------

def _resolve_recipient(
    conn: Connection, principal: Principal, level: str,
    department_id: str | None, user_id: str | None,
) -> tuple[str | None, str | None]:
    """Valida el destinatario y devuelve (department_id, user_id) que se
    estampa en las filas de policies (espejo del PUT por nivel: el nivel
    user resuelve el depto del usuario)."""
    require_recipient_scope(conn, principal, level, department_id, user_id)
    if level == "org":
        if department_id or user_id:
            raise validation_failed(
                "level 'org' takes no department_id/user_id."
            )
        return None, None
    if level == "dept":
        if not department_id or user_id:
            raise validation_failed(
                "level 'dept' requires department_id (and no user_id)."
            )
        exists = conn.execute(
            text("SELECT 1 FROM departments WHERE id = CAST(:d AS uuid)"),
            {"d": department_id},
        ).first()
        if exists is None:
            raise not_found("Department not found.")
        return department_id, None
    # level == "user"
    if not user_id or department_id:
        raise validation_failed(
            "level 'user' requires user_id (department_id is resolved from "
            "the user)."
        )
    row = conn.execute(
        text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        raise not_found("User not found.")
    dept = str(row["department_id"]) if row["department_id"] else None
    if dept is None:
        raise validation_failed(
            "User has no department; user-level policies require one."
        )
    return dept, user_id


def _analyze(
    conn: Connection, definition: dict, level: str,
    department_id: str | None, user_id: str | None,
) -> dict:
    """El análisis compartido de preview y assign (SIN escribir):

    - ``skipped_org_only``: keys org_only cuando el destino no es org
      (D-41-04: se omiten con aviso — el engine las rechazaría).
    - ``would_relax``: keys cuyo valor relajaría un nivel superior
      (REQ-POL-007 — la asignación fallará).
    - ``invalid``: keys desconocidas/invariables o con valor de tipo
      incorrecto (la MISMA validación del upsert por key).
    - ``keys``: por cada key a escribir, valor nuevo, valor actual del
      nivel (si existe) y marca ``overwrites``.
    """
    keys: list[dict] = []
    skipped_org_only: list[str] = []
    would_relax: list[dict] = []
    invalid: list[dict] = []
    for key, value in definition.items():
        if level in ("dept", "user") and key in ORG_ONLY_KEYS:
            skipped_org_only.append(key)
            continue
        try:
            entry = policies._catalog_entry(conn, key)
            if entry["invariable"]:
                raise validation_failed(
                    f"Policy '{key}' is a provider invariable (floor) and "
                    "cannot be set at any level (REQ-POL-004)."
                )
            policies._validate_value(entry, value)
        except Exception as exc:
            invalid.append({"key": key, "message": _api_message(exc)})
            continue
        current = policies._current_value(
            conn, level=level, department_id=department_id,
            user_id=user_id, key=key,
        )
        if level in ("dept", "user"):
            org_value = policies._current_value(
                conn, level="org", department_id=None, user_id=None, key=key,
            )
            superiors = [org_value]
            if level == "user" and department_id:
                superiors.append(policies._current_value(
                    conn, level="dept", department_id=department_id,
                    user_id=None, key=key,
                ))
            for superior in superiors:
                if policies._relaxes(superior, value, key,
                                     entry["value_type"]):
                    would_relax.append({
                        "key": key,
                        "message": (
                            f"Policy '{key}' would relax a superior policy "
                            "(REQ-POL-007)."
                        ),
                    })
                    break
        keys.append({
            "key": key, "new_value": value, "current_value": current,
            "overwrites": current is not None,
        })
    return {
        "keys": keys, "skipped_org_only": skipped_org_only,
        "would_relax": would_relax, "invalid": invalid,
    }


def preview(
    conn: Connection, principal: Principal, policy_id: str, *,
    level: str, department_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """GET /{id}/preview (REQ-041-04): dry-run SIN escribir nada."""
    row = _policy_row(conn, principal, policy_id)
    dept, usr = _resolve_recipient(
        conn, principal, level, department_id, user_id,
    )
    analysis = _analyze(conn, row["definition"], level, dept, usr)
    return {
        "named_policy_id": policy_id, "name": row["name"],
        "level": level, "department_id": dept, "user_id": usr,
        **analysis,
    }


def _materialize(
    conn: Connection, principal: Principal, definition: dict,
    level: str, department_id: str | None, user_id: str | None,
) -> dict:
    """Ejecuta los upserts de las keys aplicables (todo-o-nada: corre en la
    transacción del request — o del savepoint del reapply). Devuelve el
    mapa efectivamente escrito (sin las org_only omitidas)."""
    analysis = _analyze(conn, definition, level, department_id, user_id)
    if analysis["invalid"] or analysis["would_relax"]:
        errors = analysis["invalid"] + analysis["would_relax"]
        raise validation_failed(
            "Assignment rejected: " + "; ".join(
                f"'{e['key']}': {e['message']}" for e in errors
            ),
            details={"errors": errors},
        )
    applied: dict = {}
    for entry in analysis["keys"]:
        policies.upsert(
            conn, principal, level=level, key=entry["key"],
            value=entry["new_value"],
            department_id=department_id, user_id=user_id,
        )
        applied[entry["key"]] = entry["new_value"]
    return {"applied": applied, "skipped": analysis["skipped_org_only"]}


def assign(
    conn: Connection, principal: Principal, policy_id: str, *,
    level: str, department_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """POST /{id}/assign (REQ-041-005): materialización atómica + snapshot.

    La atomicidad la da la transacción del request (get_tx): si cualquier
    upsert falla, NADA se escribe y el 422 lleva el detalle por key.
    """
    row = _policy_row(conn, principal, policy_id)
    dept, usr = _resolve_recipient(
        conn, principal, level, department_id, user_id,
    )
    result = _materialize(
        conn, principal, row["definition"], level, dept, usr,
    )
    try:
        assignment = conn.execute(
            text(
                "INSERT INTO named_policy_assignments "
                "  (tenant_id, named_policy_id, level, department_id, "
                "   user_id, applied_keys, created_by) "
                "VALUES (CAST(:t AS uuid), CAST(:pid AS uuid), :level, "
                "        CAST(:dept AS uuid), CAST(:usr AS uuid), "
                "        CAST(:keys AS jsonb), CAST(:actor AS uuid)) "
                "RETURNING id, applied_at"
            ),
            {
                "t": principal.tenant_id, "pid": policy_id, "level": level,
                "dept": dept, "usr": usr,
                "keys": json.dumps(result["applied"]),
                "actor": principal.user_id,
            },
        ).mappings().first()
    except IntegrityError:
        raise conflict(
            "This named policy is already assigned to that recipient."
        ) from None
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=policies._actor_role(principal),
        action="named_policy.assigned", actor_id=principal.user_id,
        target_type="named_policy", target_id=policy_id,
        payload={
            "name": row["name"], "level": level,
            "department_id": dept, "user_id": usr,
            "keys": sorted(result["applied"].keys()),
            "skipped": result["skipped"],
        },
    )
    return {
        "assignment_id": str(assignment["id"]),
        "named_policy_id": policy_id,
        "level": level, "department_id": dept, "user_id": usr,
        "applied_keys": result["applied"],
        "skipped": result["skipped"],
        "applied_at": assignment["applied_at"].isoformat(),
    }


def _unassign_one(
    conn: Connection, principal: Principal, assignment: dict,
    *, policy_name: str,
) -> dict:
    """Unassign segura (D-41-05): desactiva SOLO las keys cuyo valor actual
    en el nivel sigue siendo el del snapshot; las editadas a mano (o
    sobreescritas por otra política, o ya desactivadas) se CONSERVAN y se
    reportan en ``kept_modified``."""
    level = assignment["level"]
    dept = str(assignment["department_id"]) if assignment["department_id"] else None
    usr = str(assignment["user_id"]) if assignment["user_id"] else None
    deactivated: list[str] = []
    kept_modified: list[dict] = []
    snapshot: dict = assignment["applied_keys"] or {}
    for key, value in snapshot.items():
        current = policies._current_value(
            conn, level=level, department_id=dept, user_id=usr, key=key,
        )
        if current is None:
            kept_modified.append({
                "key": key, "current_value": None,
                "reason": "already_inactive",
            })
        elif policies._canonical(current) == policies._canonical(value):
            policies.deactivate(
                conn, principal, level=level, key=key,
                department_id=dept, user_id=usr,
            )
            deactivated.append(key)
        else:
            kept_modified.append({
                "key": key, "current_value": current,
                "reason": "modified_after_assignment",
            })
    conn.execute(
        text("DELETE FROM named_policy_assignments WHERE id = CAST(:id AS uuid)"),
        {"id": str(assignment["id"])},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=policies._actor_role(principal),
        action="named_policy.unassigned", actor_id=principal.user_id,
        target_type="named_policy",
        target_id=str(assignment["named_policy_id"]),
        payload={
            "name": policy_name, "level": level,
            "department_id": dept, "user_id": usr,
            "keys": sorted(snapshot.keys()),
            "deactivated": deactivated,
            "kept_modified": [k["key"] for k in kept_modified],
        },
    )
    return {"deactivated": deactivated, "kept_modified": kept_modified}


def unassign(
    conn: Connection, principal: Principal, assignment_id: str,
) -> dict:
    """DELETE /assignments/{assignment_id} (REQ-041-005)."""
    row = conn.execute(
        text(
            "SELECT a.id, a.named_policy_id, a.level, a.department_id, "
            "a.user_id, a.applied_keys, a.applied_at, p.name "
            "FROM named_policy_assignments a JOIN named_policies p "
            "  ON p.id = a.named_policy_id "
            "WHERE a.id = CAST(:id AS uuid)"
        ),
        {"id": assignment_id},
    ).mappings().first()  # RLS: solo el tenant del actor
    if row is None:
        raise not_found("Assignment not found.")
    require_recipient_scope(
        conn, principal, row["level"],
        str(row["department_id"]) if row["department_id"] else None,
        str(row["user_id"]) if row["user_id"] else None,
    )
    return _unassign_one(conn, principal, dict(row), policy_name=row["name"])


def reapply(conn: Connection, principal: Principal, policy_id: str) -> dict:
    """POST /{id}/reapply (REQ-041-006): re-materialización best-effort POR
    destinatario. Cada asignación corre bajo su PROPIO savepoint: un
    destinatario que falla (p. ej. la edición ahora relajaría un nivel
    superior) no arrastra a los demás, y sus keys no cambian; las exitosas
    refrescan snapshot (applied_keys/applied_at) y dejan de ser stale."""
    row = _policy_row(conn, principal, policy_id)
    require_manage_policies(principal)
    assignments = _assignment_rows(conn, principal, policy_id=policy_id)
    results: list[dict] = []
    for a in assignments:
        recipient = {
            "assignment_id": str(a["id"]), "level": a["level"],
            "department_id": str(a["department_id"])
            if a["department_id"] else None,
            "user_id": str(a["user_id"]) if a["user_id"] else None,
            "stale": bool(a["stale"]),
        }
        try:
            with conn.begin_nested():
                require_recipient_scope(
                    conn, principal, a["level"],
                    recipient["department_id"], recipient["user_id"],
                )
                materialized = _materialize(
                    conn, principal, row["definition"], a["level"],
                    recipient["department_id"], recipient["user_id"],
                )
                conn.execute(
                    text(
                        "UPDATE named_policy_assignments "
                        "SET applied_keys = CAST(:keys AS jsonb), "
                        "    applied_at = now() "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {
                        "keys": json.dumps(materialized["applied"]),
                        "id": str(a["id"]),
                    },
                )
                audit.emit(
                    conn, tenant_id=principal.tenant_id,
                    actor_role=policies._actor_role(principal),
                    action="named_policy.reapplied",
                    actor_id=principal.user_id,
                    target_type="named_policy", target_id=policy_id,
                    payload={
                        "name": row["name"], "level": a["level"],
                        "department_id": recipient["department_id"],
                        "user_id": recipient["user_id"],
                        "keys": sorted(materialized["applied"].keys()),
                        "skipped": materialized["skipped"],
                    },
                )
            results.append({
                **recipient, "status": "applied",
                "applied_keys": materialized["applied"],
                "skipped": materialized["skipped"],
            })
        except Exception as exc:
            # El savepoint ya revirtió SOLO esta asignación (audit de keys
            # incluido); el mensaje real (422/403) se reporta tal cual.
            results.append({
                **recipient, "status": "failed",
                "errors": [str(getattr(exc, "message", exc))],
            })
    return {"results": results}


# ---------------------------------------------------------------------------
# Seed de plantillas (REQ-041-007, D-41-08)
# ---------------------------------------------------------------------------

def seed_builtin_templates(
    conn: Connection, tenant_id: str, *,
    actor_role: str = "provider", actor_id: str | None = None,
) -> list[str]:
    """Siembra las 3 plantillas del contrato como named_policies comunes
    (``source_template = 'builtin:<slug>'``), idempotente por slug.

    Usado por el provisioning (platform/app/tenants/provision.py) y por los
    tests; la migración 0034 hace el backfill de los tenants existentes con
    las MISMAS definitions (verificadas por test_named_policies). Si el
    tenant ya tiene una política con el mismo NOMBRE (creada por un admin),
    la plantilla se salta — el seed jamás rompe datos del cliente.
    """
    seeded: list[str] = []
    for tpl in builder.builder_templates():
        source_template = f"builtin:{tpl['slug']}"
        exists = conn.execute(
            text(
                "SELECT 1 FROM named_policies "
                "WHERE tenant_id = CAST(:t AS uuid) AND "
                "      (source_template = :st OR name = :name)"
            ),
            {"t": tenant_id, "st": source_template, "name": tpl["name"]["es"]},
        ).first()
        if exists is not None:
            continue
        row = conn.execute(
            text(
                "INSERT INTO named_policies "
                "  (tenant_id, name, description, definition, source_template) "
                "VALUES (CAST(:t AS uuid), :name, :description, "
                "        CAST(:definition AS jsonb), :st) "
                "RETURNING id"
            ),
            {
                "t": tenant_id, "name": tpl["name"]["es"],
                "description": tpl["description"]["es"],
                "definition": json.dumps(tpl["definition"]),
                "st": source_template,
            },
        ).mappings().first()
        audit.emit(
            conn, tenant_id=tenant_id, actor_role=actor_role,
            actor_id=actor_id, action="named_policy.created",
            target_type="named_policy", target_id=str(row["id"]),
            payload={
                "name": tpl["name"]["es"],
                "source_template": source_template,
                "seed": True,
                "keys": sorted(tpl["definition"].keys()),
            },
        )
        seeded.append(tpl["slug"])
    return seeded

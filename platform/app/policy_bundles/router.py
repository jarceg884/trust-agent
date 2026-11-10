"""Policies — una sola política por departamento o por persona (spec 044 v2).

Cada política contiene:
  • ``desktop``      — réplica de Settings de Hermes Desktop (opciones que el
                       usuario puede o no usar).
  • ``credentials``  — accesos del vault permitidos.
  • ``global_prompt``— instrucciones de la organización; se inyectan desde el
                       bundle firmado y prevalecen sobre SOUL.md/perfiles.
  • ``models``       — modelos a los que la política da acceso.
  • ``default_model``— modelo por defecto de la política.
  • ``agents``       — agentes compartidos incluidos en la política.
  • ``department_id``/``user_id`` — asignación única (departamento o persona).
"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import not_found, optional_uuid, permission_denied, require_uuid, validation_failed

router = APIRouter(prefix="/api/policy-bundles", dependencies=[Depends(require_mfa)])


class BundleBody(BaseModel):
    name: str
    description: str = ""
    desktop: dict = {}
    credentials: dict = {}
    global_prompt: str = ""
    profiles: list = []
    contracts: list = []
    models: list[str] = []
    default_model: str | None = None
    agents: list[str] = []
    department_id: str | None = None
    user_id: str | None = None


def _require_admin(principal: Principal) -> None:
    if "org_admin" not in principal.role_keys and "dept_admin" not in principal.role_keys:
        raise permission_denied("Only organization administrators manage policies.")


def _validate_profiles(body: BundleBody) -> None:
    """Spec 044: los perfiles de la política son ``{name, model}`` y el modelo
    debe estar entre los que la propia política concede.

    Un perfil que nombre un modelo fuera de ``models`` es un hueco de
    gobernanza: el empleado entraría por el perfil a un modelo que la política
    no le da. La validación vive en el servidor porque el bundle viaja firmado
    — cualquier cliente que escriba la política pasa por aquí."""
    seen: set[str] = set()
    for entry in body.profiles:
        if not isinstance(entry, dict):
            raise validation_failed("Cada perfil debe ser un objeto {name, model}.")
        name = str(entry.get("name") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not name or not model:
            raise validation_failed("Cada perfil necesita nombre y modelo.")
        if name.lower() in seen:
            raise validation_failed(f"Perfil duplicado: '{name}'.")
        seen.add(name.lower())
        if body.models and model not in body.models:
            raise validation_failed(
                f"El perfil '{name}' usa el modelo '{model}', que no está entre "
                "los modelos de esta política. Márcalo en la sección Modelos y "
                "vuelve a guardar."
            )


def _row(row) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "desktop": row["desktop"],
        "credentials": row["credentials"],
        "global_prompt": row["global_prompt"],
        "profiles": row["profiles"],
        "contracts": row["contracts"] or [],
        "models": row["models"] or [],
        "default_model": row["default_model"],
        "agents": row["agents"] or [],
        "department_id": str(row["department_id"]) if row["department_id"] else None,
        "user_id": str(row["user_id"]) if row["user_id"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _scope(body: BundleBody, principal: Principal) -> tuple[str | None, str | None]:
    dep = optional_uuid(body.department_id, "department_id") if body.department_id else None
    usr = optional_uuid(body.user_id, "user_id") if body.user_id else None
    if dep and usr:
        raise validation_failed("Assign the policy to a department OR a person, not both.")
    if not dep and not usr:
        raise validation_failed("Assign the policy to a department or a person.")
    return dep, usr


_COLUMNS = (
    "id, name, description, desktop, credentials, global_prompt, profiles, "
    "models, default_model, agents, contracts, department_id, user_id, created_at, updated_at"
)


@router.get("")
def list_bundles(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    rows = conn.execute(
        text(f"SELECT {_COLUMNS} FROM policy_bundles ORDER BY created_at")
    ).mappings()
    return {"bundles": [_row(r) for r in rows]}


@router.post("", status_code=201)
def create_bundle(
    body: BundleBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    _validate_profiles(body)
    dep, usr = _scope(body, principal)
    row = conn.execute(
        text(
            "INSERT INTO policy_bundles"
            f" (tenant_id, name, description, desktop, credentials, global_prompt,"
            f"  profiles, contracts, models, default_model, agents, department_id, user_id, created_by)"
            f" VALUES (CAST(:t AS uuid), :name, :desc, CAST(:desktop AS jsonb),"
            f"  CAST(:creds AS jsonb), :prompt, CAST(:profiles AS jsonb),"
            f"  CAST(:contracts AS jsonb), CAST(:models AS jsonb), :default_model, CAST(:agents AS jsonb),"
            f"  CAST(:dep AS uuid), CAST(:usr AS uuid), CAST(:by AS uuid))"
            f" RETURNING {_COLUMNS}"
        ),
        {
            "t": principal.tenant_id,
            "name": body.name,
            "desc": body.description,
            "desktop": json.dumps(body.desktop),
            "creds": json.dumps(body.credentials),
            "prompt": body.global_prompt,
            "profiles": json.dumps(body.profiles),
            "contracts": json.dumps(body.contracts),
            "models": json.dumps(body.models),
            "default_model": body.default_model,
            "agents": json.dumps(body.agents),
            "dep": dep,
            "usr": usr,
            "by": principal.user_id,
        },
    ).mappings().first()
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=principal.role_keys[0] if principal.role_keys else "org_admin",
        action="policy_bundle.created",
        actor_id=principal.user_id,
        target_type="policy_bundle",
        target_id=str(row["id"]),
        payload={"name": body.name, "department_id": dep, "user_id": usr},
    )
    return _row(row)


@router.put("/{bundle_id}")
def update_bundle(
    bundle_id: str,
    body: BundleBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    bundle_id = require_uuid(bundle_id, "bundle_id")
    _validate_profiles(body)
    dep, usr = _scope(body, principal)
    row = conn.execute(
        text(
            "UPDATE policy_bundles SET name=:name, description=:desc,"
            " desktop=CAST(:desktop AS jsonb), credentials=CAST(:creds AS jsonb),"
            " global_prompt=:prompt, profiles=CAST(:profiles AS jsonb),"
            " contracts=CAST(:contracts AS jsonb),"
            " models=CAST(:models AS jsonb), default_model=:default_model,"
            " agents=CAST(:agents AS jsonb), department_id=CAST(:dep AS uuid),"
            " user_id=CAST(:usr AS uuid), updated_at=now()"
            " WHERE id=CAST(:id AS uuid)"
            f" RETURNING {_COLUMNS}"
        ),
        {
            "id": bundle_id,
            "name": body.name,
            "desc": body.description,
            "desktop": json.dumps(body.desktop),
            "creds": json.dumps(body.credentials),
            "prompt": body.global_prompt,
            "profiles": json.dumps(body.profiles),
            "contracts": json.dumps(body.contracts),
            "models": json.dumps(body.models),
            "default_model": body.default_model,
            "agents": json.dumps(body.agents),
            "dep": dep,
            "usr": usr,
        },
    ).mappings().first()
    if row is None:
        raise not_found("Policy not found.")
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=principal.role_keys[0] if principal.role_keys else "org_admin",
        action="policy_bundle.updated",
        actor_id=principal.user_id,
        target_type="policy_bundle",
        target_id=bundle_id,
        payload={"name": body.name},
    )
    return _row(row)


@router.delete("/{bundle_id}", status_code=204)
def delete_bundle(
    bundle_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    bundle_id = require_uuid(bundle_id, "bundle_id")
    conn.execute(
        text("DELETE FROM policy_bundles WHERE id=CAST(:id AS uuid)"),
        {"id": bundle_id},
    )
    return None


@router.post("/{bundle_id}/assign")
def assign_bundle(
    bundle_id: str,
    department_id: str | None = None,
    user_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    bundle_id = require_uuid(bundle_id, "bundle_id")
    dep = optional_uuid(department_id, "department_id") if department_id else None
    usr = optional_uuid(user_id, "user_id") if user_id else None
    if dep and usr:
        raise validation_failed("Assign the policy to a department OR a person, not both.")
    if not dep and not usr:
        raise validation_failed("Assign the policy to a department or a person.")
    conn.execute(
        text(
            "UPDATE policy_bundles SET department_id=CAST(:dep AS uuid),"
            " user_id=CAST(:usr AS uuid), updated_at=now() WHERE id=CAST(:id AS uuid)"
        ),
        {"id": bundle_id, "dep": dep, "usr": usr},
    )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=principal.role_keys[0] if principal.role_keys else "org_admin",
        action="policy_bundle.assigned",
        actor_id=principal.user_id,
        target_type="policy_bundle",
        target_id=bundle_id,
        payload={"department_id": dep, "user_id": usr},
    )
    return {"ok": True, "department_id": dep, "user_id": usr}


def effective_bundle_for_user(conn: Connection, user_id: str) -> dict | None:
    """Política del usuario: primero la personal, si no la de su departamento."""
    row = conn.execute(
        text(
            f"SELECT {_COLUMNS} FROM policy_bundles"
            " WHERE user_id = CAST(:u AS uuid)"
            " ORDER BY updated_at DESC LIMIT 1"
        ),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        row = conn.execute(
            text(
                f"SELECT b.id, b.name, b.description, b.desktop, b.credentials, b.global_prompt,"
                f" b.profiles, b.contracts, b.models, b.default_model, b.agents, b.department_id, b.user_id,"
                f" b.created_at, b.updated_at FROM policy_bundles b"
                f" JOIN users u ON u.department_id = b.department_id"
                f" WHERE u.id = CAST(:u AS uuid)"
                f" ORDER BY b.updated_at DESC LIMIT 1"
            ),
            {"u": user_id},
        ).mappings().first()
    return dict(row) if row else None

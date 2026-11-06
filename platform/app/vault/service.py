"""Vault credential lifecycle (spec 006, REQ-VC-001…008).

Governance-gated (deny-overrides via spec 003's effective policy), encrypted
at rest with the envelope primitive of app/vault/crypto.py (D-VC-10), one-time
secret delivery (D-VC-01), role-scoped metadata visibility (D-VC-04) and an
audited runtime injection channel (D-VC-02).
"""
import secrets
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..audit import service as audit
from ..auth.rbac import dept_scope
from ..auth.service import Principal
from ..config import get_settings
from ..errors import (
    conflict,
    connector_not_allowed,
    credential_type_not_allowed,
    gone,
    not_found,
    validation_failed,
)
from ..governance.service import effective_policy_for_user
from . import crypto

# Metadata keys returned by reads — NEVER secret values nor ciphertext
# (REQ-VLT-007). This is the stable read contract of the vault.
_META_KEYS = (
    "id", "user_id", "name", "credential_type", "connector_slug", "scope",
    "status", "last_rotated_at", "last_used_at", "created_at",
)


def _to_uuid(value: str) -> str:
    """Validate UUID format; malformed ids answer 404 (D-CP-09).

    Defense in depth only: since t_378503a1 the ROUTE layer rejects
    malformed UUID params with 422 validation_failed before the service
    runs, so this 404 is unreachable via the API and protects internal
    callers instead.
    """
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise not_found("Resource not found.")
    return value


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _meta(row: dict) -> dict:
    """Stable metadata projection (REQ-VLT-007): ids/dates as strings."""
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "name": row["name"],
        "credential_type": row["credential_type"],
        "connector_slug": row["connector_slug"],
        "scope": row["scope"],
        "folder_id": str(row["folder_id"]) if row.get("folder_id") else None,
        "status": row["status"],
        "last_rotated_at": _iso(row.get("last_rotated_at")),
        "last_used_at": _iso(row.get("last_used_at")),
        "created_at": _iso(row.get("created_at")),
    }


def _visibility_sql(principal: Principal) -> tuple[str, dict]:
    """WHERE fragment for metadata visibility (D-VC-04).

    org_admin -> whole tenant; dept_admin -> own + their departments' users;
    everyone else -> own credentials only. RLS already scopes the tenant.
    """
    if "org_admin" in principal.role_keys:
        return "TRUE", {}
    params: dict = {"uid": principal.user_id}
    if "dept_admin" in principal.role_keys:
        depts = dept_scope(principal) or []
        if depts:
            return (
                "(v.user_id = CAST(:uid AS uuid) "
                " OR owner.department_id = ANY(CAST(:depts AS uuid[])))",
                {**params, "depts": [_uuid.UUID(d) for d in depts]},
            )
    return "v.user_id = CAST(:uid AS uuid)", params


def _load_credential(
    conn: Connection,
    principal: Principal,
    credential_id: str,
    *,
    owner_only: bool = False,
) -> dict:
    """Fetch one credential row (with ciphertext) within visibility scope."""
    cred_id = _to_uuid(credential_id)
    if owner_only:
        frag, params = "v.user_id = CAST(:uid AS uuid)", {"uid": principal.user_id}
    else:
        frag, params = _visibility_sql(principal)
    row = conn.execute(
        text(
            "SELECT v.*, owner.department_id AS owner_department_id "
            "FROM vault_credentials v "
            "JOIN users owner ON owner.tenant_id = v.tenant_id "
            "               AND owner.id = v.user_id "
            f"WHERE v.id = CAST(:cid AS uuid) AND {frag}"
        ),
        {"cid": cred_id, **params},
    ).mappings().first()
    if row is None:
        raise not_found("Credential not found.")
    return dict(row)


def get_catalog_connector(conn: Connection, slug: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT slug, name, category, capabilities, required_scopes, status "
            "FROM catalog.connector_catalog WHERE slug = :slug"
        ),
        {"slug": slug},
    ).mappings().first()
    return dict(row) if row else None


def _check_governance(
    conn: Connection,
    principal: Principal,
    *,
    credential_type: str,
    connector_slug: str | None,
    scope: str | None,
) -> None:
    """Deny-overrides gates (D-VC-03): types, connectors, scopes.

    Spec 024 (REQ-024-008): catalog connectors are ALSO gated by the
    effective ``allowed_connections`` tokens (wildcard default)."""
    policy = effective_policy_for_user(conn, principal)

    allowed_types = policy.get("vault_allowed_credential_types") or []
    if credential_type not in allowed_types:
        raise credential_type_not_allowed(
            f"Credential type '{credential_type}' is not allowed by policy."
        )

    if not connector_slug:
        return  # e.g. BYOK model keys: no connector governance (spec 005)

    catalog_row = get_catalog_connector(conn, connector_slug)
    if catalog_row is None or catalog_row["status"] != "available":
        raise not_found("Connector not found.")

    allowed_connectors = policy.get("allowed_connectors") or []
    if connector_slug not in allowed_connectors:
        raise connector_not_allowed(
            f"Connector '{connector_slug}' is not allowed by policy."
        )

    from ..connectors.service import connection_tokens_allow

    if not connection_tokens_allow(
        set(policy.get("allowed_connections") or []), connector_slug
    ):
        raise connector_not_allowed(
            f"Connector '{connector_slug}' is not covered by the "
            "allowed_connections policy."
        )

    # scoped_credentials is a provider-invariable floor (REQ-CON-006).
    if policy.get("scoped_credentials"):
        if not scope or not scope.strip():
            raise validation_failed(
                "A scope is required for connector credentials."
            )
        tokens = {t.strip() for t in scope.split(",") if t.strip()}
        required = set(catalog_row["required_scopes"] or [])
        if not tokens or not tokens <= required:
            raise validation_failed(
                f"Scope must be a non-empty subset of {sorted(required)}."
            )


def create_credential(
    conn: Connection,
    principal: Principal,
    *,
    name: str,
    credential_type: str,
    secret_value: str,
    connector_slug: str | None = None,
    scope: str | None = None,
    folder_id: str | None = None,
) -> dict:
    """Register an encrypted credential; the value is returned ONE time only."""
    _check_governance(
        conn, principal,
        credential_type=credential_type,
        connector_slug=connector_slug, scope=scope,
    )
    folder_id = _validate_folder(conn, principal, folder_id)
    settings = get_settings()
    kek = crypto.load_kek(settings.vault_kek_b64)
    ct_secret, ct_dek, nonce, key_id = crypto.encrypt_secret(
        secret_value.encode("utf-8"), kek, settings.vault_key_id
    )
    try:
        row = conn.execute(
            text(
                "INSERT INTO vault_credentials "
                "(tenant_id, user_id, name, credential_type, connector_slug, "
                " scope, folder_id, ciphertext_secret, ciphertext_dek, nonce, key_id) "
                "VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), :name, :ctype, "
                "        :cslug, :scope, CAST(:folder_id AS uuid), :cts, :ctd, :nonce, :key_id) "
                "RETURNING id, created_at"
            ),
            {
                "tid": principal.tenant_id, "uid": principal.user_id,
                "name": name, "ctype": credential_type,
                "cslug": connector_slug, "scope": scope,
                "folder_id": folder_id,
                "cts": ct_secret, "ctd": ct_dek, "nonce": nonce,
                "key_id": key_id,
            },
        ).mappings().first()
    except IntegrityError:
        raise conflict("A credential with this name already exists.")
    credential_id = str(row["id"])
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="vault.credential_created",
        target_type="vault_credential",
        target_id=credential_id,
        payload={  # NEVER the secret (REQ-VLT-006, AC-VC-002)
            "name": name,
            "credential_type": credential_type,
            "connector_slug": connector_slug,
            "scope": scope,
            "folder_id": folder_id,
        },
    )
    return {
        "credential_id": credential_id,
        "secret_value": secret_value,  # one-time (D-VC-01)
        "metadata": {
            "id": credential_id,
            "user_id": principal.user_id,
            "name": name,
            "credential_type": credential_type,
            "connector_slug": connector_slug,
            "scope": scope,
            "folder_id": folder_id,
            "status": "active",
            "last_rotated_at": None,
            "last_used_at": None,
            "created_at": _iso(row["created_at"]),
        },
    }


def list_credentials(conn: Connection, principal: Principal) -> list[dict]:
    frag, params = _visibility_sql(principal)
    rows = conn.execute(
        text(
            "SELECT v.* FROM vault_credentials v "
            "JOIN users owner ON owner.tenant_id = v.tenant_id "
            "               AND owner.id = v.user_id "
            f"WHERE {frag} ORDER BY v.created_at DESC, v.id"
        ),
        params,
    ).mappings()
    return [_meta(dict(r)) for r in rows]


def get_credential(
    conn: Connection, principal: Principal, credential_id: str
) -> dict:
    """Metadata only — the value never leaves the vault on reads."""
    row = _load_credential(conn, principal, credential_id)
    return _meta(row)


# --- Folders (Infisical-style tree, owner-scoped like credentials) --------

def _folder_meta(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "parent_folder_id": str(row["parent_folder_id"]) if row.get("parent_folder_id") else None,
        "name": row["name"],
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _validate_folder(
    conn: Connection, principal: Principal, folder_id: str | None
) -> str | None:
    """None stays None (root); otherwise the folder must exist and be
    visible to the caller (same visibility rule as credentials) or 404."""
    if not folder_id:
        return None
    frag, params = _visibility_sql_folder(principal)
    row = conn.execute(
        text(
            "SELECT f.id FROM vault_folders f "
            "JOIN users owner ON owner.tenant_id = f.tenant_id "
            "               AND owner.id = f.user_id "
            f"WHERE f.id = CAST(:fid AS uuid) AND {frag}"
        ),
        {"fid": _to_uuid(folder_id), **params},
    ).first()
    if row is None:
        raise not_found("Folder not found.")
    return str(row[0])


def _visibility_sql_folder(principal: Principal) -> tuple[str, dict]:
    """Same owner/dept_admin/org_admin visibility rule as credentials,
    applied to vault_folders (aliased ``f``)."""
    if "org_admin" in principal.role_keys:
        return "TRUE", {}
    params: dict = {"uid": principal.user_id}
    if "dept_admin" in principal.role_keys:
        depts = dept_scope(principal) or []
        if depts:
            return (
                "(f.user_id = CAST(:uid AS uuid) "
                " OR owner.department_id = ANY(CAST(:depts AS uuid[])))",
                {**params, "depts": [_uuid.UUID(d) for d in depts]},
            )
    return "f.user_id = CAST(:uid AS uuid)", params


def create_folder(
    conn: Connection,
    principal: Principal,
    *,
    name: str,
    parent_folder_id: str | None = None,
) -> dict:
    """Create a folder (owned by the caller) optionally nested under
    another folder the caller can see."""
    name = (name or "").strip()
    if not name:
        raise validation_failed("Folder name must not be empty.")
    parent_folder_id = _validate_folder(conn, principal, parent_folder_id)
    row = conn.execute(
        text(
            "INSERT INTO vault_folders (tenant_id, user_id, parent_folder_id, name) "
            "VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), "
            "        CAST(:parent AS uuid), :name) "
            "RETURNING id, created_at, updated_at"
        ),
        {
            "tid": principal.tenant_id, "uid": principal.user_id,
            "parent": parent_folder_id, "name": name,
        },
    ).mappings().first()
    folder_id = str(row["id"])
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="vault.folder_created",
        target_type="vault_folder",
        target_id=folder_id,
        payload={"name": name, "parent_folder_id": parent_folder_id},
    )
    return _folder_meta({
        "id": folder_id, "user_id": principal.user_id,
        "parent_folder_id": parent_folder_id, "name": name,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    })


def list_folders(conn: Connection, principal: Principal) -> list[dict]:
    frag, params = _visibility_sql_folder(principal)
    rows = conn.execute(
        text(
            "SELECT f.* FROM vault_folders f "
            "JOIN users owner ON owner.tenant_id = f.tenant_id "
            "               AND owner.id = f.user_id "
            f"WHERE {frag} ORDER BY f.name"
        ),
        params,
    ).mappings()
    return [_folder_meta(dict(r)) for r in rows]


def _load_folder_owned(
    conn: Connection, principal: Principal, folder_id: str
) -> dict:
    """Owner-only load (rename/delete/move are owner-only, same contract
    as credential rotate: a folder belongs to one person)."""
    row = conn.execute(
        text(
            "SELECT * FROM vault_folders "
            "WHERE id = CAST(:fid AS uuid) AND user_id = CAST(:uid AS uuid)"
        ),
        {"fid": _to_uuid(folder_id), "uid": principal.user_id},
    ).mappings().first()
    if row is None:
        raise not_found("Folder not found.")
    return dict(row)


def rename_folder(
    conn: Connection, principal: Principal, folder_id: str, *, name: str
) -> dict:
    row = _load_folder_owned(conn, principal, folder_id)
    name = (name or "").strip()
    if not name:
        raise validation_failed("Folder name must not be empty.")
    conn.execute(
        text(
            "UPDATE vault_folders SET name = :name, updated_at = now() "
            "WHERE id = CAST(:fid AS uuid)"
        ),
        {"name": name, "fid": row["id"]},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        actor_id=principal.user_id, action="vault.folder_renamed",
        target_type="vault_folder", target_id=str(row["id"]),
        payload={"name": name},
    )
    return _folder_meta({**row, "name": name})


def delete_folder(
    conn: Connection, principal: Principal, folder_id: str
) -> dict:
    """Delete an EMPTY folder (no sub-folders, no credentials) — moving
    contents out first keeps deletion non-destructive by default."""
    row = _load_folder_owned(conn, principal, folder_id)
    children = conn.execute(
        text("SELECT count(*) FROM vault_folders WHERE parent_folder_id = CAST(:fid AS uuid)"),
        {"fid": row["id"]},
    ).scalar()
    secrets_inside = conn.execute(
        text("SELECT count(*) FROM vault_credentials WHERE folder_id = CAST(:fid AS uuid)"),
        {"fid": row["id"]},
    ).scalar()
    if children or secrets_inside:
        raise conflict(
            "Folder is not empty. Move its secrets and sub-folders out "
            "before deleting it."
        )
    conn.execute(
        text("DELETE FROM vault_folders WHERE id = CAST(:fid AS uuid)"),
        {"fid": row["id"]},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        actor_id=principal.user_id, action="vault.folder_deleted",
        target_type="vault_folder", target_id=str(row["id"]),
        payload={"name": row["name"]},
    )
    return {"folder_id": str(row["id"]), "deleted": True}


def move_credential(
    conn: Connection, principal: Principal, credential_id: str,
    *, folder_id: str | None,
) -> dict:
    """Move one secret into a folder (or back to root with folder_id=None).

    Owner-only, same as rotate: moving between folders is a vault-tree
    edit, not a governance action."""
    row = _load_credential(conn, principal, credential_id, owner_only=True)
    folder_id = _validate_folder(conn, principal, folder_id)
    conn.execute(
        text(
            "UPDATE vault_credentials SET folder_id = CAST(:fid AS uuid), "
            "updated_at = now() WHERE id = CAST(:cid AS uuid)"
        ),
        {"fid": folder_id, "cid": str(row["id"])},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        actor_id=principal.user_id, action="vault.credential_moved",
        target_type="vault_credential", target_id=str(row["id"]),
        payload={"folder_id": folder_id},
    )
    return _meta({**row, "folder_id": folder_id})


def rotate_credential(
    conn: Connection,
    principal: Principal,
    credential_id: str,
    new_secret_value: str | None = None,
) -> dict:
    """Rotate: fresh DEK + nonce; the OLD value dies with the old ciphertext.

    The NEW value is returned one time (D-VC-05). Owner-only: the response
    delivers a secret, so nobody else may rotate.
    """
    row = _load_credential(conn, principal, credential_id, owner_only=True)
    if row["status"] != "active":
        raise gone("Credential is revoked.")
    new_value = new_secret_value or secrets.token_urlsafe(32)
    settings = get_settings()
    kek = crypto.load_kek(settings.vault_kek_b64)
    ct_secret, ct_dek, nonce, key_id = crypto.encrypt_secret(
        new_value.encode("utf-8"), kek, settings.vault_key_id
    )
    conn.execute(
        text(
            "UPDATE vault_credentials SET ciphertext_secret = :cts, "
            "ciphertext_dek = :ctd, nonce = :nonce, key_id = :key_id, "
            "last_rotated_at = now(), updated_at = now() "
            "WHERE id = CAST(:cid AS uuid)"
        ),
        {"cts": ct_secret, "ctd": ct_dek, "nonce": nonce, "key_id": key_id,
         "cid": str(row["id"])},
    )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="vault.credential_rotated",
        target_type="vault_credential",
        target_id=str(row["id"]),
        payload={"connector_slug": row["connector_slug"]},
    )
    meta = _meta({**row, "last_rotated_at": _now_iso(conn)})
    return {"credential_id": str(row["id"]), "secret_value": new_value,
            "metadata": meta}


def revoke_credential(
    conn: Connection, principal: Principal, credential_id: str
) -> dict:
    """Revoke + immediate cutoff of dependent instances (REQ-VLT-005, D-VC-06).

    Same transaction: instances using the credential are disconnected at once
    (well under the 60 s budget). Idempotent on already-revoked credentials.
    """
    row = _load_credential(conn, principal, credential_id)
    if row["status"] == "revoked":
        return _meta(row)  # idempotent
    conn.execute(
        text(
            "UPDATE vault_credentials SET status = 'revoked', "
            "updated_at = now() WHERE id = CAST(:cid AS uuid)"
        ),
        {"cid": str(row["id"])},
    )
    # Cutoff: disconnect every active instance that used this credential.
    from ..connectors import service as connectors_service

    disconnected = connectors_service.disconnect_connector_instances(
        conn, credential_id=str(row["id"])
    )
    for inst in disconnected:
        audit.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            actor_id=principal.user_id,
            action="connector.disconnected",
            target_type="connector_instance",
            target_id=str(inst["id"]),
            payload={"reason": "credential_revoked",
                     "connector_slug": inst["connector_slug"]},
        )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="vault.credential_revoked",
        target_type="vault_credential",
        target_id=str(row["id"]),
        payload={"connector_slug": row["connector_slug"]},
    )
    return _meta({**row, "status": "revoked"})


def use_credential(
    conn: Connection, principal: Principal, credential_id: str
) -> dict:
    """Runtime injection channel (REQ-VLT-003, D-VC-02).

    Owner-only: the decrypted value is delivered to the caller's connector
    runtime IN MEMORY. Marks last_used_at and audits vault.credential_used
    WITHOUT the secret. Revoked -> 410 gone.
    """
    row = _load_credential(conn, principal, credential_id, owner_only=True)
    if row["status"] != "active":
        raise gone("Credential is revoked.")
    connector_slug = row["connector_slug"]
    if connector_slug:
        inst = conn.execute(
            text(
                "SELECT id FROM connector_instances "
                "WHERE user_id = CAST(:uid AS uuid) AND connector_slug = :slug "
                "  AND status = 'active'"
            ),
            {"uid": principal.user_id, "slug": connector_slug},
        ).mappings().first()
        if inst is None:
            raise conflict(
                "No active connector instance for this credential."
            )
        conn.execute(
            text(
                "UPDATE connector_instances SET last_used_at = now(), "
                "updated_at = now() WHERE id = CAST(:iid AS uuid)"
            ),
            {"iid": str(inst["id"])},
        )
    settings = get_settings()
    kek = crypto.load_kek(settings.vault_kek_b64)
    secret = crypto.decrypt_secret(
        row["ciphertext_secret"], row["ciphertext_dek"], row["nonce"], kek
    )
    conn.execute(
        text(
            "UPDATE vault_credentials SET last_used_at = now(), "
            "updated_at = now() WHERE id = CAST(:cid AS uuid)"
        ),
        {"cid": str(row["id"])},
    )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="vault.credential_used",
        target_type="vault_credential",
        target_id=str(row["id"]),
        device_id=principal.device_id,
        payload={"connector_slug": connector_slug},  # NEVER the secret
    )
    return {
        "credential_id": str(row["id"]),
        "connector_slug": connector_slug,
        "secret_value": secret.decode("utf-8"),
    }


def _now_iso(conn: Connection) -> str:
    row = conn.execute(text("SELECT now() AS ts")).mappings().first()
    return _iso(row["ts"])

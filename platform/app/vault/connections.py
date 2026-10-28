"""Conexiones personalizadas del vault (spec 024, mandato Miller).

Conexiones definidas por el ADMIN de la organización más allá del catálogo
fijo de conectores — bases de datos, API keys genéricas u otra metadata
libre. El secreto se cifra con el MISMO envelope crypto del vault (KEK/DEK
AES-256-GCM, D-24-01) y se muestra EXACTAMENTE una vez (create/update);
las lecturas devuelven metadata únicamente (REQ-024-002).

Governance (REQ-024-005/007): la policy key ``allowed_connections``
(deny-overrides, 3 niveles) gobierna el USO runtime — la inyección del
secreto exige que el id de la conexión esté en la policy efectiva del
usuario (403 si no; 410 si está revocada). La inyección es por-uso y sin
estado persistente (D-24-05): un cambio de policy aplica en el uso
siguiente y el bundle excluye la conexión en el sync siguiente.
"""
import uuid as _uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..audit import service as audit
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..config import get_settings
from ..errors import (
    conflict,
    connector_not_allowed,
    gone,
    not_found,
    permission_denied,
    require_uuid,
    validation_failed,
)
from ..governance.service import effective_policy_for_user
from ..models.litellm import LiteLLMClient
from . import crypto

router = APIRouter(
    prefix="/api/vault/connections", dependencies=[Depends(require_mfa)]
)

KINDS = ("database", "api_key", "custom", "llm")

# Metadata is NON-secret by schema (same rule as connector config, C4):
# secrets live encrypted in their own columns.
_FORBIDDEN_META_KEYS = ("password", "secret", "token", "api_key", "credential")

# Wildcard token of the allowed_connections policy key (D-24-03): "every
# catalog connector (still gated by allowed_connectors)". Custom connections
# ALWAYS require their explicit id.
WILDCARD = "*"


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _meta(row: dict) -> dict:
    """Stable metadata projection (REQ-024-002): NEVER secret nor ciphertext."""
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "kind": row["kind"],
        "metadata": row["metadata"] or {},
        "status": row["status"],
        "created_by": str(row["created_by"]),
        "last_rotated_at": _iso(row.get("last_rotated_at")),
        "last_used_at": _iso(row.get("last_used_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _reject_secret_keys(metadata: dict) -> None:
    for key in (metadata or {}):
        lowered = str(key).lower()
        if any(f in lowered for f in _FORBIDDEN_META_KEYS):
            raise validation_failed(
                f"Metadata key '{key}' looks like a secret; metadata is "
                "non-secret — the secret lives encrypted in its own column."
            )


def _validate_metadata(kind: str, metadata: dict) -> dict:
    """Per-kind validation (REQ-024-011). -> normalized metadata dict."""
    if not isinstance(metadata, dict):
        raise validation_failed("Metadata must be a JSON object.")
    _reject_secret_keys(metadata)
    if kind == "database":
        required = ("type", "host", "port", "database", "username")
        for key in required:
            if not metadata.get(key):
                raise validation_failed(
                    f"Database connections require metadata '{key}'."
                )
        port = metadata["port"]
        if not isinstance(port, int) or isinstance(port, bool) \
                or not (1 <= port <= 65535):
            raise validation_failed("Metadata 'port' must be an integer 1-65535.")
        for key in ("type", "host", "database", "username"):
            if not isinstance(metadata[key], str):
                raise validation_failed(f"Metadata '{key}' must be a string.")
    elif kind == "api_key":
        if not metadata.get("service"):
            raise validation_failed("API key connections require metadata 'service'.")
        auth = metadata.get("auth")
        if auth not in ("header", "bearer"):
            raise validation_failed("Metadata 'auth' must be 'header' or 'bearer'.")
        if auth == "header" and not metadata.get("header_name"):
            raise validation_failed(
                "Header auth requires metadata 'header_name'."
            )
    elif kind == "llm":
        # Spec 032 (dynamic discovery): OpenAI-compatible endpoint whose
        # models are discovered via GET {base_url}/models with this key.
        base_url = metadata.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise validation_failed(
                "LLM connections require metadata 'base_url' "
                "(http/https OpenAI-compatible endpoint)."
            )
        # Admin-declared BYOK pricing (¢/M USD); 0 = unknown cost.
        for key in ("input_price_cents_per_m", "output_price_cents_per_m"):
            value = metadata.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or value < 0:
                raise validation_failed(
                    f"Metadata '{key}' must be a number >= 0 (USD cents per "
                    "million tokens)."
                )
    # 'custom': free-form non-secret metadata.
    return metadata


def _require_manage(principal: Principal) -> None:
    """CRUD is org_admin-only (D-24-04, data-driven RBAC)."""
    if "connections.manage" not in principal.permissions:
        raise permission_denied("Missing permission: connections.manage")


def allowed_connection_tokens(conn: Connection, principal: Principal) -> set[str]:
    """Effective allowed_connections tokens (deny-overrides, REQ-024-005)."""
    policy = effective_policy_for_user(conn, principal)
    return set(policy.get("allowed_connections") or [])


def _load(
    conn: Connection, connection_id: str, *, active_only: bool = False
) -> dict:
    row = conn.execute(
        text(
            "SELECT * FROM vault_connections WHERE id = CAST(:cid AS uuid)"
        ),
        {"cid": connection_id},
    ).mappings().first()
    if row is None:
        raise not_found("Connection not found.")
    if active_only and row["status"] != "active":
        raise gone("Connection is revoked.")
    return dict(row)


def _visible(conn: Connection, principal: Principal, row: dict) -> bool:
    """org_admin sees the whole tenant; everyone else only what their
    effective policy allows (they are the consumable set)."""
    if "org_admin" in principal.role_keys:
        return True
    return str(row["id"]) in allowed_connection_tokens(conn, principal)


def effective_connections_for_user(conn: Connection, principal: Principal) -> dict:
    """Effective connections for the governance bundle (REQ-024-010).

    -> {"catalog": [slugs usable under the combined rule],
        "custom": [{id, kind, name, metadata} of allowed active connections]}
    Secrets NEVER leave this projection.
    """
    from ..connectors import service as connectors_service

    policy = effective_policy_for_user(conn, principal)
    allowed = set(policy.get("allowed_connectors") or [])
    tokens = set(policy.get("allowed_connections") or [])
    catalog = [
        slug for slug in sorted(allowed)
        if connectors_service.connection_tokens_allow(tokens, slug)
    ]
    custom = []
    if tokens:
        rows = conn.execute(
            text(
                "SELECT id, name, kind, metadata FROM vault_connections "
                "WHERE status = 'active' ORDER BY created_at, id"
            )
        ).mappings()
        for r in rows:
            if str(r["id"]) in tokens:
                custom.append({
                    "id": str(r["id"]),
                    "kind": r["kind"],
                    "name": r["name"],
                    "metadata": r["metadata"] or {},
                })
    return {"catalog": catalog, "custom": custom}


def create_connection(
    conn: Connection,
    principal: Principal,
    *,
    name: str,
    kind: str,
    metadata: dict,
    secret_value: str,
) -> dict:
    _require_manage(principal)
    if kind not in KINDS:
        raise validation_failed(f"Kind must be one of: {list(KINDS)}.")
    metadata = _validate_metadata(kind, metadata)
    settings = get_settings()
    kek = crypto.load_kek(settings.vault_kek_b64)
    ct_secret, ct_dek, nonce, key_id = crypto.encrypt_secret(
        secret_value.encode("utf-8"), kek, settings.vault_key_id
    )
    try:
        row = conn.execute(
            text(
                "INSERT INTO vault_connections "
                "(tenant_id, created_by, name, kind, metadata, "
                " ciphertext_secret, ciphertext_dek, nonce, key_id) "
                "VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), :name, :kind, "
                "        CAST(:meta AS jsonb), :cts, :ctd, :nonce, :key_id) "
                "RETURNING id, created_at"
            ),
            {
                "tid": principal.tenant_id, "uid": principal.user_id,
                "name": name, "kind": kind,
                "meta": _dump(metadata),
                "cts": ct_secret, "ctd": ct_dek, "nonce": nonce,
                "key_id": key_id,
            },
        ).mappings().first()
    except IntegrityError:
        raise conflict("A connection with this name already exists.")
    connection_id = str(row["id"])
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="connection.created",
        target_type="vault_connection",
        target_id=connection_id,
        payload={"name": name, "kind": kind, "metadata": metadata},
    )
    return {
        "connection_id": connection_id,
        "secret_value": secret_value,  # one-time (REQ-024-002)
        "metadata": {
            "id": connection_id,
            "name": name,
            "kind": kind,
            "metadata": metadata,
            "status": "active",
            "created_by": principal.user_id,
            "last_rotated_at": None,
            "last_used_at": None,
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["created_at"]),
        },
    }


def list_connections(conn: Connection, principal: Principal) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT * FROM vault_connections ORDER BY created_at DESC, id"
        )
    ).mappings()
    return [
        _meta(dict(r)) for r in rows
        if _visible(conn, principal, dict(r))
    ]


def get_connection(
    conn: Connection, principal: Principal, connection_id: str
) -> dict:
    connection_id = require_uuid(connection_id, "connection_id")
    row = _load(conn, connection_id)
    if not _visible(conn, principal, row):
        raise not_found("Connection not found.")  # no existence oracle
    return _meta(row)


def update_connection(
    conn: Connection,
    principal: Principal,
    connection_id: str,
    *,
    name: str | None = None,
    kind: str | None = None,
    metadata: dict | None = None,
    new_secret_value: str | None = None,
) -> dict:
    """Update metadata/name (and optionally rotate the secret).

    The NEW secret value is returned ONE time (REQ-024-002); the old
    ciphertext dies with the UPDATE.
    """
    _require_manage(principal)
    connection_id = require_uuid(connection_id, "connection_id")
    row = _load(conn, connection_id)
    if row["status"] != "active":
        raise gone("Connection is revoked.")

    final_name = name or row["name"]
    final_kind = kind or row["kind"]
    if final_kind not in KINDS:
        raise validation_failed(f"Kind must be one of: {list(KINDS)}.")
    final_metadata = (
        _validate_metadata(final_kind, metadata)
        if metadata is not None else row["metadata"]
    )
    if name and name != row["name"]:
        dup = conn.execute(
            text(
                "SELECT 1 FROM vault_connections "
                "WHERE name = :name AND id <> CAST(:cid AS uuid)"
            ),
            {"name": name, "cid": connection_id},
        ).first()
        if dup is not None:
            raise conflict("A connection with this name already exists.")

    secret_value = new_secret_value
    if secret_value is not None:
        settings = get_settings()
        kek = crypto.load_kek(settings.vault_kek_b64)
        ct_secret, ct_dek, nonce, key_id = crypto.encrypt_secret(
            secret_value.encode("utf-8"), kek, settings.vault_key_id
        )
        conn.execute(
            text(
                "UPDATE vault_connections SET name = :name, kind = :kind, "
                "metadata = CAST(:meta AS jsonb), ciphertext_secret = :cts, "
                "ciphertext_dek = :ctd, nonce = :nonce, key_id = :key_id, "
                "last_rotated_at = now() WHERE id = CAST(:cid AS uuid)"
            ),
            {
                "name": final_name, "kind": final_kind,
                "meta": _dump(final_metadata),
                "cts": ct_secret, "ctd": ct_dek, "nonce": nonce,
                "key_id": key_id, "cid": connection_id,
            },
        )
    else:
        conn.execute(
            text(
                "UPDATE vault_connections SET name = :name, kind = :kind, "
                "metadata = CAST(:meta AS jsonb) "
                "WHERE id = CAST(:cid AS uuid)"
            ),
            {
                "name": final_name, "kind": final_kind,
                "meta": _dump(final_metadata), "cid": connection_id,
            },
        )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="connection.updated",
        target_type="vault_connection",
        target_id=connection_id,
        payload={
            "name": final_name, "kind": final_kind,
            "metadata": final_metadata,
            "secret_rotated": secret_value is not None,
        },
    )
    updated = _load(conn, connection_id)
    result = {
        "connection_id": connection_id,
        "metadata": _meta(updated),
    }
    if secret_value is not None:
        result["secret_value"] = secret_value  # one-time
    return result


def revoke_connection(
    conn: Connection,
    principal: Principal,
    connection_id: str,
    *,
    litellm: LiteLLMClient | None = None,
) -> dict:
    """Irreversible revoke, idempotent on already-revoked connections.

    Spec 032: an llm connection carries catalog models — revoking marks
    them stale AND removes them from the gateway (fail-closed: if the
    gateway is unreachable the revoke does not complete, same contract as
    keys.revoke_key — old vkeys must never keep routing a dead upstream).
    """
    _require_manage(principal)
    connection_id = require_uuid(connection_id, "connection_id")
    row = _load(conn, connection_id)
    if row["status"] == "revoked":
        return _meta(row)  # idempotent
    if row["kind"] == "llm":
        from ..models import discovery

        discovery.mark_connection_stale(
            conn, principal.tenant_id, connection_id, litellm=litellm
        )
    conn.execute(
        text(
            "UPDATE vault_connections SET status = 'revoked' "
            "WHERE id = CAST(:cid AS uuid)"
        ),
        {"cid": connection_id},
    )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="connection.revoked",
        target_type="vault_connection",
        target_id=connection_id,
        payload={"name": row["name"], "kind": row["kind"]},
    )
    return _meta({**row, "status": "revoked"})


def use_connection(
    conn: Connection,
    principal: Principal,
    connection_id: str,
) -> dict:
    """Runtime injection channel (REQ-024-007): decrypted secret delivered
    to the caller IN MEMORY, per-use and stateless (D-24-05).

    Policy gate: the connection id must be in the EFFECTIVE
    allowed_connections (deny-overrides) → 403 otherwise; revoked → 410.
    Audited as connection.used WITHOUT the secret.
    """
    connection_id = require_uuid(connection_id, "connection_id")
    row = _load(conn, connection_id)
    if row["status"] != "active":
        raise gone("Connection is revoked.")
    tokens = allowed_connection_tokens(conn, principal)
    if connection_id not in tokens:
        raise connector_not_allowed(
            "Connection is not allowed by policy (allowed_connections)."
        )
    settings = get_settings()
    kek = crypto.load_kek(settings.vault_kek_b64)
    secret = crypto.decrypt_secret(
        row["ciphertext_secret"], row["ciphertext_dek"], row["nonce"], kek
    )
    conn.execute(
        text(
            "UPDATE vault_connections SET last_used_at = now() "
            "WHERE id = CAST(:cid AS uuid)"
        ),
        {"cid": connection_id},
    )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="connection.used",
        target_type="vault_connection",
        target_id=connection_id,
        device_id=principal.device_id,
        payload={"name": row["name"], "kind": row["kind"]},  # NEVER the secret
    )
    return {
        "connection_id": connection_id,
        "kind": row["kind"],
        "name": row["name"],
        "metadata": row["metadata"] or {},
        "secret_value": secret.decode("utf-8"),
    }


def _dump(metadata: dict) -> str:
    import json

    return json.dumps(metadata or {})


# --- Router -----------------------------------------------------------------

class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=40)
    metadata: dict = Field(default_factory=dict)
    secret_value: str = Field(min_length=1, max_length=65536)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    metadata: dict | None = None
    new_secret_value: str | None = Field(
        default=None, min_length=1, max_length=65536
    )


@router.post("", status_code=201)
def post_connection(
    payload: ConnectionCreate,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Create a custom connection -> 201 with the secret ONE time."""
    return create_connection(
        conn, principal,
        name=payload.name, kind=payload.kind,
        metadata=payload.metadata, secret_value=payload.secret_value,
    )


@router.get("")
def get_connections(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Metadata only; org_admin sees the tenant, others their allowed set."""
    return {"connections": list_connections(conn, principal)}


@router.get("/{connection_id}")
def get_connection_route(
    connection_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return get_connection(conn, principal, connection_id)


@router.put("/{connection_id}")
def put_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Update metadata/name (optional secret rotation, one-time display)."""
    return update_connection(
        conn, principal, connection_id,
        name=payload.name, kind=payload.kind,
        metadata=payload.metadata,
        new_secret_value=payload.new_secret_value,
    )


@router.post("/{connection_id}/revoke")
def post_revoke(
    connection_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return revoke_connection(conn, principal, connection_id)


@router.post("/{connection_id}/use")
def post_use(
    connection_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Runtime injection (REQ-024-007): policy-gated, audited, in memory."""
    return use_connection(conn, principal, connection_id)

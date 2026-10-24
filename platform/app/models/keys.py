"""Virtual key issuance against LiteLLM (REQ-MDL-002, D-GOV-12).

The plaintext key exists exactly once — in the HTTP response of the
issuance call. The database keeps only the LiteLLM token hash (sha256 of
the key), which is also the handle for /key/block and /key/unblock.
"""
import json
import secrets

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal, load_permissions, load_roles
from ..errors import not_found, validation_failed
from . import access
from .litellm import LiteLLMClient, budget_duration_for, get_litellm


def _target_principal(conn: Connection, tenant_id: str, user_id: str) -> Principal:
    """Build a Principal for the target user (same tenant scope — RLS)."""
    row = conn.execute(
        text(
            "SELECT id, email, display_name, department_id, status "
            "FROM users WHERE id = CAST(:u AS uuid)"
        ),
        {"u": user_id},
    ).mappings().first()
    if row is None:
        raise not_found("User not found.")
    roles = load_roles(conn, str(row["id"]))
    permissions = load_permissions(conn, [r.role_key for r in roles])
    return Principal(
        tenant_id=tenant_id,
        tenant_slug="",
        user_id=str(row["id"]),
        email=row["email"],
        display_name=row["display_name"],
        department_id=str(row["department_id"]) if row["department_id"] else None,
        roles=roles,
        permissions=permissions,
    )


def _user_budget(conn: Connection, user_id: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT amount_cents, period FROM budgets "
            "WHERE level = 'user' AND user_id = CAST(:u AS uuid) "
            "AND status = 'active'"
        ),
        {"u": user_id},
    ).mappings().first()
    return dict(row) if row else None


def issue_key(
    conn: Connection,
    principal: Principal,
    *,
    user_id: str,
    litellm: LiteLLMClient | None = None,
) -> dict:
    """Issue a LiteLLM virtual key for `user_id` (one-time plaintext return)."""
    litellm = litellm or get_litellm()
    target = _target_principal(conn, principal.tenant_id, user_id)
    models = access.effective_models_for_user(conn, target)
    if not models:
        # Fail closed: a LiteLLM key with models=[] would allow EVERYTHING.
        raise validation_failed(
            "The user has no effective approved models; refusing to issue "
            "an unrestricted key."
        )
    budget = _user_budget(conn, user_id)
    max_budget_usd = (budget["amount_cents"] / 100.0) if budget else None
    budget_duration = (
        budget_duration_for(budget["period"]) if budget else None
    )
    alias = f"trust-{user_id[:8]}-{secrets.token_hex(4)}"

    created = litellm.generate_key(
        alias=alias,
        user_id=user_id,
        models=models,
        max_budget_usd=max_budget_usd,
        budget_duration=budget_duration,
        metadata={"tenant_id": principal.tenant_id, "user_id": user_id},
    )
    key_hash = created.get("token") or ""
    plaintext = created.get("key") or ""
    if not key_hash or not plaintext:
        raise validation_failed("LiteLLM returned no key material.")

    inserted = conn.execute(
        text(
            "INSERT INTO api_keys (tenant_id, user_id, litellm_key_hash, "
            "alias, allowed_models, max_budget_cents, budget_duration) "
            "VALUES (CAST(:t AS uuid), CAST(:u AS uuid), :hash, :alias, "
            "CAST(:models AS jsonb), :max_cents, :duration) RETURNING id"
        ),
        {
            "t": principal.tenant_id, "u": user_id, "hash": key_hash,
            "alias": alias, "models": json.dumps(models),
            "max_cents": budget["amount_cents"] if budget else None,
            "duration": budget_duration,
        },
    ).mappings().first()

    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=principal.role_keys[0] if principal.role_keys else "user",
        action="model.key_issued", actor_id=principal.user_id,
        target_type="user", target_id=user_id,
        payload={"alias": alias, "allowed_models": models,
                 "max_budget_cents": budget["amount_cents"] if budget else None},
    )
    return {
        "key": plaintext,  # ONE-TIME: never persisted, never returned again
        "key_id": str(inserted["id"]),
        "alias": alias,
        "allowed_models": models,
        "max_budget_usd": max_budget_usd,
        "budget_duration": budget_duration,
    }


def revoke_key(
    conn: Connection,
    principal: Principal,
    *,
    key_id: str,
    litellm: LiteLLMClient | None = None,
) -> None:
    litellm = litellm or get_litellm()
    row = conn.execute(
        text("SELECT id, user_id, litellm_key_hash, status FROM api_keys "
             "WHERE id = CAST(:k AS uuid)"),
        {"k": key_id},
    ).mappings().first()
    if row is None:
        raise not_found("Key not found.")
    if row["status"] == "revoked":
        return
    litellm.block_key(row["litellm_key_hash"])
    conn.execute(
        text("UPDATE api_keys SET status = 'revoked', revoked_at = now(), "
             "updated_at = now() WHERE id = CAST(:k AS uuid)"),
        {"k": key_id},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=principal.role_keys[0] if principal.role_keys else "user",
        action="model.key_blocked", actor_id=principal.user_id,
        target_type="api_key", target_id=key_id,
        payload={"reason": "revoked"},
    )


def list_keys_for_user(conn: Connection, user_id: str) -> list[dict]:
    """Metadata only — never the hash, never the key (REQ-GOV-035)."""
    rows = conn.execute(
        text(
            "SELECT id, alias, status, allowed_models, max_budget_cents, "
            "budget_duration, created_at, revoked_at FROM api_keys "
            "WHERE user_id = CAST(:u AS uuid) ORDER BY created_at DESC"
        ),
        {"u": user_id},
    ).mappings()
    return [
        {
            "id": str(r["id"]), "alias": r["alias"], "status": r["status"],
            "allowed_models": r["allowed_models"],
            "max_budget_cents": r["max_budget_cents"],
            "budget_duration": r["budget_duration"],
            "created_at": r["created_at"].isoformat(),
            "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
        }
        for r in rows
    ]

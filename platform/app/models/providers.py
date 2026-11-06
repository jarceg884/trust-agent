"""Provider-operated model providers (spec 036, repro Miller t_64e645c1).

The PLATFORM PROVIDER registers model sources (any OpenAI-compatible
endpoint + key, envelope-encrypted in ``ops.model_providers``) and decides
per tenant which ones are enabled — "qué accesos le doy a este cliente".
Discovered models are materialized per enabled tenant in
``catalog.model_catalog`` as ``source='provider'`` with the shared
namespaced name ``prov:{provider_id}:{upstream_id}`` and are approved at
org level on enablement (same sense as the provisioning ``model_plan``);
from there the existing level governance (org → dept → user, subset rule,
revoke, ``allowed_models``) applies unchanged.

This is NOT the client BYOK flow (spec 032): the key here belongs to the
provider, the client never sees it, and the enablement is provider policy.
"""
import time
import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..audit import service as audit
from ..db import get_engine, get_migrator_engine, tenant_tx
from ..errors import conflict, gone, not_found, require_uuid, service_unavailable, validation_failed
from ..provider import audit as provider_audit
from .discovery import fetch_upstream_models, models_url
from .litellm import LiteLLMClient, get_litellm

# ---------------------------------------------------------------- providers


def _provider_row(conn: Connection, provider_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT id, name, display_name, base_url, status, "
            "input_price_cents_per_m, output_price_cents_per_m, "
            "created_at, updated_at FROM ops.model_providers "
            "WHERE id = CAST(:pid AS uuid)"
        ),
        {"pid": provider_id},
    ).mappings().first()
    if row is None:
        raise not_found("Model provider not found.")
    return dict(row)


def _load_provider_with_key(conn: Connection, provider_id: str) -> dict:
    """Provider row INCLUDING the envelope columns — internal use only;
    the returned dict must never be serialized into a response."""
    row = conn.execute(
        text(
            "SELECT id, name, display_name, base_url, status, "
            "input_price_cents_per_m, output_price_cents_per_m, "
            "ciphertext_secret, ciphertext_dek, nonce "
            "FROM ops.model_providers WHERE id = CAST(:pid AS uuid)"
        ),
        {"pid": provider_id},
    ).mappings().first()
    if row is None:
        raise not_found("Model provider not found.")
    return dict(row)


def _decrypt_key(row: dict) -> str:
    """Envelope decrypt IN MEMORY, per use (same contract as BYOK,
    REQ-024-007/D-24-05): the key never reaches responses, logs nor audit."""
    from ..config import get_settings
    from ..vault import crypto

    kek = crypto.load_kek(get_settings().vault_kek_b64)
    return crypto.decrypt_secret(
        row["ciphertext_secret"], row["ciphertext_dek"], row["nonce"], kek
    ).decode("utf-8")


def _validate_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise validation_failed(
            "base_url must be an http(s) OpenAI-compatible endpoint."
        )
    return url


def _validate_prices(input_cents, output_cents) -> tuple:
    try:
        cin = float(input_cents) if input_cents is not None else None
        cout = float(output_cents) if output_cents is not None else None
    except (TypeError, ValueError) as exc:
        raise validation_failed(
            "Pricing must be numeric (input/output cents per million tokens)."
        ) from exc
    if (cin is not None and cin < 0) or (cout is not None and cout < 0):
        raise validation_failed("Declared pricing must be >= 0.")
    return cin, cout


def create_provider(
    engine: Engine,
    *,
    name: str,
    base_url: str,
    api_key: str,
    display_name: str | None = None,
    input_price_cents_per_m=None,
    output_price_cents_per_m=None,
    operator: dict | None = None,
) -> dict:
    """Register a model provider. The key is stored ONLY as an envelope
    ciphertext (vault crypto); it is never returned, logged nor audited."""
    from ..config import get_settings
    from ..vault import crypto

    name = (name or "").strip()
    if not name:
        raise validation_failed("Provider name is required.")
    if not api_key or not api_key.strip():
        raise validation_failed("Provider API key is required.")
    base_url = _validate_base_url(base_url)
    cin, cout = _validate_prices(
        input_price_cents_per_m, output_price_cents_per_m
    )

    kek = crypto.load_kek(get_settings().vault_kek_b64)
    secret, dek, nonce, key_id = crypto.encrypt_secret(
        api_key.strip().encode(), kek, get_settings().vault_key_id
    )

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM ops.model_providers WHERE name = :n"),
            {"n": name},
        ).first()
        if existing is not None:
            raise conflict(f"A model provider named '{name}' already exists.")
        inserted = conn.execute(
            text(
                "INSERT INTO ops.model_providers "
                "  (name, display_name, base_url, ciphertext_secret, "
                "   ciphertext_dek, nonce, key_id, input_price_cents_per_m, "
                "   output_price_cents_per_m) "
                "VALUES (:name, :display, :url, :sec, :dek, :nonce, :key_id, "
                "        :cin, :cout) RETURNING id, created_at"
            ),
            {
                "name": name,
                "display": (display_name or "").strip() or None,
                "url": base_url,
                "sec": secret,
                "dek": dek,
                "nonce": nonce,
                "key_id": key_id,
                "cin": cin,
                "cout": cout,
            },
        ).mappings().first()
        provider_id = str(inserted["id"])
        provider_audit.emit(
            conn,
            action="provider.created",
            operator_id=(operator or {}).get("operator_id"),
            username=(operator or {}).get("username"),
            payload={
                "provider_id": provider_id,
                "name": name,
                "base_url": base_url,
            },  # never the key
        )
    return {
        "id": provider_id,
        "name": name,
        "display_name": display_name or None,
        "base_url": base_url,
        "status": "active",
        "input_price_cents_per_m": cin,
        "output_price_cents_per_m": cout,
    }


def list_providers(engine: Engine, migrator_engine: Engine | None = None) -> list[dict]:
    """All providers with model/tenant counts — NO key material.

    ``catalog.model_catalog`` vive bajo RLS por tenant (AC-032-07): el plano
    provider NO tiene contexto de tenant, así que el conteo de modelos
    disponibles es una lectura cross-tenant y debe correr en la conexión
    migrator (BYPASSRLS, D-CP-10/D-ONB-10 — mismo patrón que el slug de
    tenants). Sin eso la columna "models" de la UI siempre muestra 0.
    """
    if migrator_engine is not None:
        with migrator_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT mp.id, mp.name, mp.display_name, mp.base_url, "
                    "mp.status, mp.input_price_cents_per_m, "
                    "mp.output_price_cents_per_m, mp.created_at, "
                    "(SELECT count(*) FROM ops.model_provider_tenants mpt "
                    "  WHERE mpt.provider_id = mp.id AND mpt.enabled) "
                    "  AS enabled_tenants, "
                    "(SELECT count(*) FROM catalog.model_catalog mc "
                    "  WHERE mc.provider_id = mp.id AND mc.status = 'available') "
                    "  AS available_models "
                    "FROM ops.model_providers mp ORDER BY mp.created_at"
                )
            ).mappings()
    else:  # pragma: no cover - engine-only path kept for backwards compat
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT mp.id, mp.name, mp.display_name, mp.base_url, "
                    "mp.status, mp.input_price_cents_per_m, "
                    "mp.output_price_cents_per_m, mp.created_at, "
                    "(SELECT count(*) FROM ops.model_provider_tenants mpt "
                    "  WHERE mpt.provider_id = mp.id AND mpt.enabled) "
                    "  AS enabled_tenants, "
                    "(SELECT count(*) FROM catalog.model_catalog mc "
                    "  WHERE mc.provider_id = mp.id AND mc.status = 'available') "
                    "  AS available_models "
                    "FROM ops.model_providers mp ORDER BY mp.created_at"
                )
            ).mappings()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "display_name": r["display_name"],
            "base_url": r["base_url"],
            "status": r["status"],
            "input_price_cents_per_m": r["input_price_cents_per_m"],
            "output_price_cents_per_m": r["output_price_cents_per_m"],
            "enabled_tenants": int(r["enabled_tenants"]),
            "available_models": int(r["available_models"]),
        }
        for r in rows
    ]


def get_provider(engine: Engine, provider_id: str,
                 migrator_engine: Engine | None = None) -> dict:
    """Detail: provider (no key) + discovered models per tenant + enabled
    tenants (slug resolved cross-tenant via the migrator by the caller).

    Los modelos viven en ``catalog.model_catalog`` bajo RLS por tenant: la
    lectura es cross-tenant (todas las filas del proveedor, de todos los
    clientes) y corre en la conexión migrator (BYPASSRLS) cuando el router
    la provee — sin eso el detalle siempre llega sin modelos.
    """
    with engine.begin() as conn:
        row = _provider_row(conn, provider_id)
        tenants = conn.execute(
            text(
                "SELECT tenant_id, enabled, enabled_at, disabled_at "
                "FROM ops.model_provider_tenants "
                "WHERE provider_id = CAST(:pid AS uuid) ORDER BY enabled_at"
            ),
            {"pid": provider_id},
        ).mappings()
    models: list = []
    if migrator_engine is not None:
        with migrator_engine.connect() as conn:
            models = conn.execute(
                text(
                    "SELECT mc.model_name, mc.display_name, mc.status, "
                    "mc.tenant_id, mc.last_seen_at, mc.gateway_model_id "
                    "FROM catalog.model_catalog mc "
                    "WHERE mc.provider_id = CAST(:pid AS uuid) "
                    "ORDER BY mc.model_name, mc.tenant_id"
                ),
                {"pid": provider_id},
            ).mappings().all()
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "display_name": row["display_name"],
        "base_url": row["base_url"],
        "status": row["status"],
        "input_price_cents_per_m": row["input_price_cents_per_m"],
        "output_price_cents_per_m": row["output_price_cents_per_m"],
        "models": [
            {
                "model_name": m["model_name"],
                "display_name": m["display_name"],
                "status": m["status"],
                "tenant_id": str(m["tenant_id"]) if m["tenant_id"] else None,
                "last_seen_at": (
                    m["last_seen_at"].isoformat() if m["last_seen_at"] else None
                ),
            }
            for m in models
        ],
        "tenants": [
            {
                "tenant_id": str(t["tenant_id"]),
                "enabled": bool(t["enabled"]),
                "enabled_at": t["enabled_at"].isoformat(),
                "disabled_at": (
                    t["disabled_at"].isoformat() if t["disabled_at"] else None
                ),
            }
            for t in tenants
        ],
    }


# ------------------------------------------------------------- enablement


def _resolve_tenant(migrator_engine: Engine, tenant_ref: str) -> dict:
    """Cross-tenant slug/id lookup (trust_migrator ONLY, D-ONB-10)."""
    with migrator_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, slug, name, status FROM tenants "
                "WHERE slug = :ref OR id::text = :ref"
            ),
            {"ref": (tenant_ref or "").strip()},
        ).mappings().first()
    if row is None:
        raise not_found("Tenant not found.")
    if row["status"] != "active":
        raise validation_failed(
            f"Tenant '{row['slug']}' is not active (status: {row['status']})."
        )
    return {
        "id": str(row["id"]), "slug": row["slug"], "name": row["name"],
    }


def enabled_tenant_ids(conn: Connection, provider_id: str) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT tenant_id FROM ops.model_provider_tenants "
            "WHERE provider_id = CAST(:pid AS uuid) AND enabled "
            "ORDER BY enabled_at"
        ),
        {"pid": provider_id},
    ).mappings()
    return sorted(str(r["tenant_id"]) for r in rows)


def _upsert_provider_row(
    conn: Connection,
    *,
    tenant_id: str,
    provider_id: str,
    provider_name: str,
    upstream_id: str,
    input_cents,
    output_cents,
) -> str:
    """Insert/restore one provider catalog row (tenant-scoped tx) → name."""
    model_name = f"prov:{provider_id}:{upstream_id}"
    conn.execute(
        text(
            "INSERT INTO catalog.model_catalog "
            "  (model_name, display_name, provider, "
            "   input_price_cents_per_m, output_price_cents_per_m, status, "
            "   tenant_id, provider_id, source, last_seen_at) "
            "VALUES (:name, :display, :provider, :cin, :cout, 'available', "
            "        CAST(:tid AS uuid), CAST(:pid AS uuid), 'provider', now()) "
            "ON CONFLICT (tenant_id, model_name) WHERE source = 'provider' "
            "DO UPDATE SET status = 'available', last_seen_at = now(), "
            "  display_name = EXCLUDED.display_name, "
            "  provider = EXCLUDED.provider, "
            "  input_price_cents_per_m = EXCLUDED.input_price_cents_per_m, "
            "  output_price_cents_per_m = EXCLUDED.output_price_cents_per_m, "
            "  updated_at = now()"
        ),
        {
            "name": model_name,
            "display": upstream_id,
            "provider": provider_name,
            "cin": input_cents,
            "cout": output_cents,
            "tid": tenant_id,
            "pid": provider_id,
        },
    )
    return model_name


def _provider_rows_for_tenant(
    conn: Connection, tenant_id: str, provider_id: str
) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT id, model_name, status, gateway_model_id "
                "FROM catalog.model_catalog "
                "WHERE source = 'provider' AND tenant_id = CAST(:tid AS uuid) "
                "AND provider_id = CAST(:pid AS uuid)"
            ),
            {"tid": tenant_id, "pid": provider_id},
        ).mappings()
    ]


def _approve_missing_org_rows(
    conn: Connection, tenant_id: str, model_names: list[str]
) -> int:
    """Org-level approval (mode='provider') for the discovered models that
    have NO model_access row at org level — a previous admin revocation is
    NEVER re-approved (a revoke must not relax, D-GOV-11)."""
    approved = 0
    for name in model_names:
        row = conn.execute(
            text(
                "SELECT mc.id FROM catalog.model_catalog mc "
                "WHERE mc.model_name = :name AND mc.tenant_id "
                "  = CAST(:tid AS uuid) AND mc.source = 'provider'"
            ),
            {"name": name, "tid": tenant_id},
        ).mappings().first()
        if row is None:
            continue
        existing = conn.execute(
            text(
                "SELECT id FROM model_access WHERE level = 'org' "
                "AND model_id = CAST(:mid AS uuid)"
            ),
            {"mid": str(row["id"])},
        ).mappings().first()
        if existing is not None:
            continue
        conn.execute(
            text(
                "INSERT INTO model_access (tenant_id, model_id, level, "
                "mode, status) VALUES (CAST(:tid AS uuid), "
                "CAST(:mid AS uuid), 'org', 'provider', 'approved')"
            ),
            {"tid": tenant_id, "mid": str(row["id"])},
        )
        audit.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="model.approved", target_type="model", target_id=name,
            payload={"level": "org", "mode": "provider",
                     "via": "provider_enablement"},
        )
        approved += 1
    return approved


def _register_gateway_models(
    litellm: LiteLLMClient, row: dict, api_key: str,
    upstream_ids: list[str], registered: dict[str, str],
) -> dict[str, str]:
    """Idempotent gateway registration shared across tenants →
    {model_name: gateway_model_id}."""
    for upstream_id in upstream_ids:
        model_name = f"prov:{row['id']}:{upstream_id}"
        if model_name in registered:
            continue
        created = litellm.add_model(
            model_name=model_name,
            upstream_model=f"openai/{upstream_id}",
            api_base=row["base_url"],
            api_key=api_key,
            input_cost_per_token=(
                float(row["input_price_cents_per_m"]) / 100.0 / 1_000_000.0
                if row["input_price_cents_per_m"] is not None else None
            ),
            output_cost_per_token=(
                float(row["output_price_cents_per_m"]) / 100.0 / 1_000_000.0
                if row["output_price_cents_per_m"] is not None else None
            ),
        )
        gateway_model_id = str(created.get("model_id") or "")
        if gateway_model_id:
            registered[model_name] = gateway_model_id
    return registered


def _sync_tenant_rows(
    conn: Connection,
    *,
    tenant_id: str,
    provider_row: dict,
    upstream_ids: list[str],
    registered: dict[str, str],
    actor_role: str = "provider",
    approve_missing: bool = False,
) -> dict:
    """Sync ONE tenant's provider rows on the GIVEN connection (the caller
    owns the transaction — provider context wraps it in tenant_tx; the
    per-tenant refresh leg passes the job's tenant-scoped conn): upsert
    discovered rows, stale the vanished, persist gateway ids, (optionally)
    approve new org rows, audit."""
    discovered: list[str] = []
    for upstream_id in upstream_ids:
        discovered.append(
            _upsert_provider_row(
                conn,
                tenant_id=tenant_id,
                provider_id=str(provider_row["id"]),
                provider_name=provider_row["name"],
                upstream_id=upstream_id,
                input_cents=provider_row["input_price_cents_per_m"],
                output_cents=provider_row["output_price_cents_per_m"],
            )
        )
    for name, gmid in registered.items():
        conn.execute(
            text(
                "UPDATE catalog.model_catalog SET gateway_model_id = :gmid "
                "WHERE model_name = :name AND tenant_id = CAST(:tid AS uuid)"
            ),
            {"gmid": gmid, "name": name, "tid": tenant_id},
        )
    stale = 0
    seen = set(discovered)
    for existing in _provider_rows_for_tenant(
        conn, tenant_id, str(provider_row["id"])
    ):
        if existing["model_name"] in seen or existing["status"] in (
            "stale", "removed",
        ):
            continue
        conn.execute(
            text(
                "UPDATE catalog.model_catalog SET status = 'stale', "
                "updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": str(existing["id"])},
        )
        stale += 1
    approved = 0
    if approve_missing:
        approved = _approve_missing_org_rows(conn, tenant_id, discovered)
    audit.emit(
        conn, tenant_id=tenant_id, actor_role=actor_role,
        action="model.catalog_refreshed", target_type="model_provider",
        target_id=str(provider_row["id"]),
        payload={
            "scope": "provider", "provider_id": str(provider_row["id"]),
            "provider": provider_row["name"],
            "discovered": len(discovered), "stale": stale,
            "approved": approved,
        },
    )
    return {"discovered": len(discovered), "stale": stale,
            "approved": approved, "models": discovered}


def refresh_provider(
    engine: Engine,
    provider_id: str,
    *,
    litellm: LiteLLMClient | None = None,
    operator: dict | None = None,
) -> dict:
    """REAL fetch GET {base_url}/models; sync the rows of EVERY enabled
    tenant + the shared gateway registrations. Fail-closed: an unreachable
    upstream is a 503 and nothing is invented (constitution I)."""
    litellm = litellm or get_litellm()
    with engine.begin() as conn:
        row = _load_provider_with_key(conn, provider_id)
    if row["status"] != "active":
        raise validation_failed(
            f"Model provider '{row['name']}' is disabled — reactivate it "
            "before refreshing."
        )
    api_key = _decrypt_key(row)  # in memory, per use
    upstream_ids = fetch_upstream_models(row["base_url"], api_key)

    registered: dict[str, str] = {
        name: gmid for name, gmid in litellm.db_model_ids().items()
        if name.startswith(f"prov:{provider_id}:")
    }
    _register_gateway_models(
        litellm, row, api_key, upstream_ids, registered
    )

    tenants = []
    with engine.begin() as conn:
        enabled = enabled_tenant_ids(conn, provider_id)
    for tenant_id in enabled:
        with tenant_tx(engine, tenant_id) as conn:
            tenants.append(
                _sync_tenant_rows(
                    conn, tenant_id=tenant_id, provider_row=row,
                    upstream_ids=upstream_ids, registered=registered,
                )
            )

    with engine.begin() as conn:
        provider_audit.emit(
            conn,
            action="provider.refreshed",
            operator_id=(operator or {}).get("operator_id"),
            username=(operator or {}).get("username"),
            payload={
                "provider_id": provider_id, "name": row["name"],
                "upstream_models": len(upstream_ids),
                "tenants": len(tenants),
            },  # never the key
        )
    return {
        "provider_id": provider_id, "name": row["name"],
        "upstream_models": len(upstream_ids), "tenants": tenants,
        "models": sorted(upstream_ids),
    }


def enable_for_tenant(
    engine: Engine,
    migrator_engine: Engine,
    provider_id: str,
    tenant_ref: str,
    *,
    litellm: LiteLLMClient | None = None,
    operator: dict | None = None,
) -> dict:
    """Enable the provider for one client: enablement row + REAL discovery
    for that tenant + org approvals of the discovered models (D-036-03)."""
    litellm = litellm or get_litellm()
    tenant = _resolve_tenant(migrator_engine, tenant_ref)
    with engine.begin() as conn:
        row = _load_provider_with_key(conn, provider_id)
    if row["status"] != "active":
        raise validation_failed(
            f"Model provider '{row['name']}' is disabled."
        )
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT enabled FROM ops.model_provider_tenants "
                "WHERE provider_id = CAST(:pid AS uuid) "
                "AND tenant_id = CAST(:tid AS uuid)"
            ),
            {"pid": provider_id, "tid": tenant["id"]},
        ).mappings().first()
        if existing is not None and existing["enabled"]:
            raise conflict(
                f"Provider '{row['name']}' is already enabled for tenant "
                f"'{tenant['slug']}'."
            )
        conn.execute(
            text(
                "INSERT INTO ops.model_provider_tenants "
                "  (provider_id, tenant_id, enabled, enabled_at, disabled_at) "
                "VALUES (CAST(:pid AS uuid), CAST(:tid AS uuid), true, now(), "
                "        NULL) "
                "ON CONFLICT (provider_id, tenant_id) DO UPDATE SET "
                "  enabled = true, enabled_at = now(), disabled_at = NULL"
            ),
            {"pid": provider_id, "tid": tenant["id"]},
        )
        provider_audit.emit(
            conn,
            action="provider.tenant_enabled",
            operator_id=(operator or {}).get("operator_id"),
            username=(operator or {}).get("username"),
            payload={"provider_id": provider_id,
                     "tenant_id": tenant["id"], "slug": tenant["slug"]},
        )

    api_key = _decrypt_key(row)
    upstream_ids = fetch_upstream_models(row["base_url"], api_key)
    registered: dict[str, str] = {
        name: gmid for name, gmid in litellm.db_model_ids().items()
        if name.startswith(f"prov:{provider_id}:")
    }
    _register_gateway_models(litellm, row, api_key, upstream_ids, registered)
    with tenant_tx(engine, tenant["id"]) as conn:
        sync = _sync_tenant_rows(
            conn, tenant_id=tenant["id"], provider_row=row,
            upstream_ids=upstream_ids, registered=registered,
            approve_missing=True,
        )
        audit.emit(
            conn, tenant_id=tenant["id"], actor_role="provider",
            action="model.provider_enabled", target_type="model_provider",
            target_id=provider_id,
            payload={"provider": row["name"],
                     "discovered": sync["discovered"],
                     "approved": sync["approved"]},
        )
    return {"tenant": tenant, "provider_id": provider_id,
            "provider": row["name"], **sync}


def _other_tenants_keep_model(
    migrator_engine: Engine, provider_id: str, tenant_id: str, model_name: str
) -> bool:
    """Cross-tenant read (BYPASSRLS migrator): does any OTHER enabled tenant
    still hold this provider model available? Shared gateway routes may be
    deleted only when the last consumer is gone (D-036-02)."""
    with migrator_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM catalog.model_catalog mc "
                "JOIN ops.model_provider_tenants mpt "
                "  ON mpt.tenant_id = mc.tenant_id "
                " AND mpt.provider_id = mc.provider_id AND mpt.enabled "
                "WHERE mc.source = 'provider' AND mc.model_name = :name "
                "AND mc.status = 'available' "
                "AND mc.tenant_id <> CAST(:tid AS uuid) LIMIT 1"
            ),
            {"name": model_name, "tid": tenant_id},
        ).first()
    return row is not None


def disable_for_tenant(
    engine: Engine,
    migrator_engine: Engine,
    provider_id: str,
    tenant_id: str,
    *,
    litellm: LiteLLMClient | None = None,
    operator: dict | None = None,
) -> dict:
    """Disable for one client: the tenant's rows go stale (fail-closed:
    out of the effective set immediately); shared gateway routes are
    deleted only when this was the last consumer. Idempotent."""
    litellm = litellm or get_litellm()
    with engine.begin() as conn:
        row = _provider_row(conn, provider_id)
        existing = conn.execute(
            text(
                "SELECT enabled FROM ops.model_provider_tenants "
                "WHERE provider_id = CAST(:pid AS uuid) "
                "AND tenant_id = CAST(:tid AS uuid)"
            ),
            {"pid": provider_id, "tid": tenant_id},
        ).mappings().first()
        if existing is None:
            raise not_found(
                "This provider is not enabled for that tenant."
            )

    stale = 0
    freed: list[str] = []
    with tenant_tx(engine, tenant_id) as conn:
        for r in _provider_rows_for_tenant(conn, tenant_id, provider_id):
            if r["status"] in ("stale", "removed"):
                continue
            conn.execute(
                text(
                    "UPDATE catalog.model_catalog SET status = 'stale', "
                    "updated_at = now() WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(r["id"])},
            )
            stale += 1
        audit.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="model.provider_disabled", target_type="model_provider",
            target_id=provider_id,
            payload={"provider": row["name"], "stale": stale},
        )

    # Shared gateway cleanup: only models no other enabled tenant uses.
    with migrator_engine.connect() as conn:
        names = [
            r["model_name"]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT mc.model_name, mc.gateway_model_id "
                    "FROM catalog.model_catalog mc "
                    "WHERE mc.source = 'provider' "
                    "AND mc.provider_id = CAST(:pid AS uuid) "
                    "AND mc.tenant_id = CAST(:tid AS uuid) "
                    "AND mc.gateway_model_id IS NOT NULL"
                ),
                {"pid": provider_id, "tid": tenant_id},
            ).mappings()
        ]
    for name in names:
        if not _other_tenants_keep_model(
            migrator_engine, provider_id, tenant_id, name
        ):
            gmid = None
            with migrator_engine.connect() as conn:
                r = conn.execute(
                    text(
                        "SELECT gateway_model_id FROM catalog.model_catalog "
                        "WHERE model_name = :name AND tenant_id "
                        "  = CAST(:tid AS uuid)",
                    ),
                    {"name": name, "tid": tenant_id},
                ).mappings().first()
                gmid = r["gateway_model_id"] if r else None
            if gmid:
                litellm.delete_model(gmid)
                freed.append(name)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.model_provider_tenants SET enabled = false, "
                "disabled_at = now() WHERE provider_id = CAST(:pid AS uuid) "
                "AND tenant_id = CAST(:tid AS uuid)"
            ),
            {"pid": provider_id, "tid": tenant_id},
        )
        provider_audit.emit(
            conn,
            action="provider.tenant_disabled",
            operator_id=(operator or {}).get("operator_id"),
            username=(operator or {}).get("username"),
            payload={"provider_id": provider_id, "tenant_id": tenant_id,
                     "stale": stale, "gateway_freed": freed},
        )
    return {"provider_id": provider_id, "tenant_id": tenant_id,
            "stale": stale, "gateway_freed": freed}


def set_provider_status(
    engine: Engine,
    migrator_engine: Engine,
    provider_id: str,
    status: str,
    *,
    litellm: LiteLLMClient | None = None,
    operator: dict | None = None,
) -> dict:
    """Suspend/reactivate the provider GLOBALLY. Suspend = every enabled
    tenant's rows stale + shared gateway routes deleted (fail-closed).
    Reactivate = status back to active; the next refresh (or enablement)
    re-materializes the rows."""
    if status not in ("active", "disabled"):
        raise validation_failed("status must be 'active' or 'disabled'.")
    litellm = litellm or get_litellm()
    with engine.begin() as conn:
        row = _provider_row(conn, provider_id)
        if row["status"] == status:
            return {"id": provider_id, "status": status, "changed": False}
        conn.execute(
            text(
                "UPDATE ops.model_providers SET status = :s, "
                "updated_at = now() WHERE id = CAST(:pid AS uuid)"
            ),
            {"s": status, "pid": provider_id},
        )
        tenants = enabled_tenant_ids(conn, provider_id)
        provider_audit.emit(
            conn,
            action=(
                "provider.suspended" if status == "disabled"
                else "provider.reactivated"
            ),
            operator_id=(operator or {}).get("operator_id"),
            username=(operator or {}).get("username"),
            payload={"provider_id": provider_id, "name": row["name"]},
        )

    if status != "disabled":
        return {"id": provider_id, "status": status, "changed": True}

    stale_total = 0
    freed: list[str] = []
    for tenant_id in tenants:
        with tenant_tx(engine, tenant_id) as conn:
            n = 0
            for r in _provider_rows_for_tenant(conn, tenant_id, provider_id):
                if r["status"] in ("stale", "removed"):
                    continue
                conn.execute(
                    text(
                        "UPDATE catalog.model_catalog SET status = 'stale', "
                        "updated_at = now() WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(r["id"])},
                )
                n += 1
            stale_total += n
            audit.emit(
                conn, tenant_id=tenant_id, actor_role="provider",
                action="model.provider_disabled",
                target_type="model_provider", target_id=provider_id,
                payload={"provider": row["name"], "stale": n,
                         "reason": "provider_suspended"},
            )
    # Global suspend: no consumer remains — drop every shared route.
    with migrator_engine.connect() as conn:
        gmids = [
            (r["model_name"], r["gateway_model_id"])
            for r in conn.execute(
                text(
                    "SELECT DISTINCT model_name, gateway_model_id "
                    "FROM catalog.model_catalog WHERE source = 'provider' "
                    "AND provider_id = CAST(:pid AS uuid) "
                    "AND gateway_model_id IS NOT NULL"
                ),
                {"pid": provider_id},
            ).mappings()
        ]
    for name, gmid in gmids:
        litellm.delete_model(gmid)
        freed.append(name)
    return {"id": provider_id, "status": status, "changed": True,
            "stale": stale_total, "gateway_freed": freed}


# ------------------------------------------------- per-tenant refresh leg


def enabled_providers_for_tenant(
    conn: Connection, tenant_id: str
) -> list[dict]:
    """Providers enabled for this tenant (readable via the ops tables —
    no RLS on the provider plane; the enablement row IS the policy)."""
    rows = conn.execute(
        text(
            "SELECT mp.id, mp.name, mp.display_name, mp.base_url, mp.status, "
            "mp.input_price_cents_per_m, mp.output_price_cents_per_m, "
            "mp.ciphertext_secret, mp.ciphertext_dek, mp.nonce "
            "FROM ops.model_providers mp "
            "JOIN ops.model_provider_tenants mpt "
            "  ON mpt.provider_id = mp.id "
            "WHERE mpt.tenant_id = CAST(:tid AS uuid) AND mpt.enabled "
            "AND mp.status = 'active' ORDER BY mp.name"
        ),
        {"tid": tenant_id},
    ).mappings()
    return [dict(r) for r in rows]


def tenant_provider_leg(
    conn: Connection,
    tenant_id: str,
    *,
    litellm: LiteLLMClient | None = None,
    actor_role: str = "system",
) -> dict:
    """The per-tenant leg of the global catalog refresh (D-036-05): sync
    THIS tenant's rows for every enabled provider, on the caller's
    tenant-scoped connection (the job's conn — same contract as the byok
    leg). Real fetches; a broken upstream is reported per provider and
    never blocks the rest (same containment as refresh_tenant). Gateway
    routes are NOT deleted here — they are shared across tenants; the
    provider-side refresh/disable owns that lifecycle (spec 036 §2)."""
    litellm = litellm or get_litellm()
    result: dict = {}
    for row in enabled_providers_for_tenant(conn, tenant_id):
        provider_id = str(row["id"])
        try:
            api_key = _decrypt_key(row)
            upstream_ids = fetch_upstream_models(row["base_url"], api_key)
            registered: dict[str, str] = {
                name: gmid for name, gmid in litellm.db_model_ids().items()
                if name.startswith(f"prov:{provider_id}:")
            }
            _register_gateway_models(
                litellm, row, api_key, upstream_ids, registered
            )
            result[row["name"]] = _sync_tenant_rows(
                conn, tenant_id=tenant_id, provider_row=row,
                upstream_ids=upstream_ids, registered=registered,
                actor_role=actor_role,
            )
        except Exception as exc:  # noqa: BLE001 - containment per provider
            result[row["name"]] = {
                "provider_id": provider_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result


# ----------------------------------------------------- test connection (043)

def test_provider_connection(
    engine: Engine,
    provider_id: str,
) -> dict:
    """REAL GET {base_url}/models with timer — never raises on upstream
    errors (returns ``{ok: false, error: ...}`` so the UI can show the
    message without a 5xx toast). The key is decrypted in memory, per use,
    and NEVER appears in the response (D-043-01)."""
    with engine.begin() as conn:
        row = _load_provider_with_key(conn, provider_id)
    api_key = _decrypt_key(row)
    url = models_url(row["base_url"])
    t0 = time.monotonic()
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        latency = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "error": f"Connection failed: {exc}", "latency_ms": latency}
    latency = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            "latency_ms": latency,
        }
    try:
        data = resp.json()
        model_ids = sorted(
            m.get("id", "") for m in data.get("data", [])
            if m.get("id")
        )
    except Exception:
        model_ids = []
    return {
        "ok": True,
        "models": model_ids,
        "count": len(model_ids),
        "latency_ms": latency,
    }


# ------------------------------------------------- BYOK gating per tenant (043)

def list_byok_policy(migrator_engine: Engine) -> list[dict]:
    """All tenants with their BYOK flag (cross-tenant migrator read).
    Tenants without a policy row default to byok_allowed=true."""
    with migrator_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.id, t.slug, t.name, t.status, "
                "COALESCE(p.byok_allowed, true) AS byok_allowed, "
                "p.set_at, p.set_by "
                "FROM tenants t "
                "LEFT JOIN ops.tenant_byok_policy p ON p.tenant_id = t.id "
                "ORDER BY t.name"
            )
        ).mappings()
    return [
        {
            "tenant_id": str(r["id"]),
            "slug": r["slug"],
            "name": r["name"],
            "status": r["status"],
            "byok_allowed": bool(r["byok_allowed"]),
            "set_at": r["set_at"].isoformat() if r["set_at"] else None,
            "set_by": r["set_by"],
        }
        for r in rows
    ]


def set_byok_policy(
    engine: Engine,
    migrator_engine: Engine,
    tenant_id: str,
    byok_allowed: bool,
    *,
    operator: dict | None = None,
) -> dict:
    """Upsert the BYOK policy for a tenant + audit in ops.provider_audit."""
    tenant_id = require_uuid(tenant_id, "tenant_id")
    # Verify tenant exists (cross-tenant migrator read)
    with migrator_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, slug, name FROM tenants WHERE id = CAST(:tid AS uuid)"),
            {"tid": tenant_id},
        ).mappings().first()
    if row is None:
        raise not_found("Tenant not found.")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.tenant_byok_policy "
                "  (tenant_id, byok_allowed, set_at, set_by) "
                "VALUES (CAST(:tid AS uuid), :allowed, now(), :by) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "  byok_allowed = EXCLUDED.byok_allowed, "
                "  set_at = now(), set_by = EXCLUDED.set_by"
            ),
            {"tid": tenant_id, "allowed": byok_allowed,
             "by": (operator or {}).get("username")},
        )
        provider_audit.emit(
            conn,
            action="provider.byok_policy_set",
            operator_id=(operator or {}).get("operator_id"),
            username=(operator or {}).get("username"),
            payload={
                "tenant_id": tenant_id,
                "slug": row["slug"],
                "byok_allowed": byok_allowed,
            },
        )
    return {
        "tenant_id": tenant_id,
        "slug": row["slug"],
        "byok_allowed": byok_allowed,
    }


def tenant_byok_allowed(conn: Connection, tenant_id: str) -> bool:
    """Read the BYOK policy for a tenant on a tenant-scoped connection.
    No row = default true (backwards-compatible, D-043-07)."""
    row = conn.execute(
        text(
            "SELECT COALESCE("
            "  (SELECT byok_allowed FROM ops.tenant_byok_policy "
            "   WHERE tenant_id = CAST(:tid AS uuid)), true)"
        ),
        {"tid": tenant_id},
    ).scalar()
    return bool(row)

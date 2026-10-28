"""Dynamic model discovery per connection (spec 032, mandato Miller).

Every ``llm`` (OpenAI-compatible) vault connection contributes its models
to the catalog: the control plane calls ``GET {base_url}/models`` with the
connection key (decrypted from the vault IN MEMORY, per use — never in
responses, logs nor audit; same contract as REQ-024-007) and upserts each
model into ``catalog.model_catalog`` with ``source='byok'`` and the
namespaced name ``byok:{connection_id}:{upstream_id}`` (collision-proof:
the connection UUID is globally unique).
"""
import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..config import get_settings
from ..errors import gone, not_found, service_unavailable, validation_failed
from .litellm import LiteLLMClient, get_litellm

UPSTREAM_TIMEOUT = 20.0


def models_url(base_url: str) -> str:
    """Normalize an OpenAI-compatible base_url to its GET /models URL.

    ``https://host/v1`` → ``https://host/v1/models``;
    ``https://host/openai/v1`` (MaaS) → same, already ends in /v1;
    ``https://host`` → ``https://host/v1/models``.
    """
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def fetch_upstream_models(base_url: str, api_key: str) -> list[str]:
    """GET {base}/models with the connection key (bearer). Real HTTP.

    -> sorted upstream model ids. Any transport/HTTP error is a 503: the
    catalog never invents models (constitution I).
    """
    url = models_url(base_url)
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=UPSTREAM_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise service_unavailable(f"Upstream {url} unreachable: {exc}") from exc
    if resp.status_code in (401, 403):
        raise service_unavailable(
            f"Upstream rejected the connection key ({resp.status_code}) "
            "— rotate the secret on the connection."
        )
    if resp.status_code != 200:
        raise service_unavailable(
            f"Upstream {url} error {resp.status_code}: {resp.text[:200]}"
        )
    try:
        payload = resp.json()
        ids = [str(m["id"]) for m in payload.get("data", []) if m.get("id")]
    except (ValueError, KeyError, TypeError) as exc:
        raise service_unavailable(
            f"Upstream {url} returned a non-OpenAI model list: {exc}"
        ) from exc
    return sorted(set(ids))


def _load_llm_connection(
    conn: Connection, tenant_id: str, connection_id: str
) -> dict:
    row = conn.execute(
        text(
            "SELECT id, name, kind, metadata, status, ciphertext_secret, "
            "ciphertext_dek, nonce FROM vault_connections "
            "WHERE id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)"
        ),
        {"cid": connection_id, "tid": tenant_id},
    ).mappings().first()
    if row is None:
        raise not_found("Connection not found.")
    if row["status"] != "active":
        raise gone("Connection is revoked.")
    if row["kind"] != "llm":
        raise validation_failed(
            "Only connections of kind 'llm' contribute models to the catalog."
        )
    return dict(row)


def _decrypt_key(row: dict) -> str:
    from ..vault import crypto

    settings = get_settings()
    kek = crypto.load_kek(settings.vault_kek_b64)
    return crypto.decrypt_secret(
        row["ciphertext_secret"], row["ciphertext_dek"], row["nonce"], kek
    ).decode("utf-8")


def _declared_prices(metadata: dict) -> tuple[float, float]:
    """Admin-declared BYOK pricing (¢/M USD); 0 = unknown cost."""
    meta = metadata or {}
    try:
        cin = float(meta.get("input_price_cents_per_m") or 0.0)
        cout = float(meta.get("output_price_cents_per_m") or 0.0)
    except (TypeError, ValueError) as exc:
        raise validation_failed(
            "Connection metadata pricing must be numeric "
            "(input_price_cents_per_m / output_price_cents_per_m)."
        ) from exc
    if cin < 0 or cout < 0:
        raise validation_failed("Declared pricing must be >= 0.")
    return cin, cout


def _upsert_byok_row(
    conn: Connection,
    *,
    tenant_id: str,
    connection_id: str,
    connection_name: str,
    upstream_id: str,
    input_cents: float,
    output_cents: float,
) -> str:
    """Insert/restore one byok catalog row -> its model_name."""
    model_name = f"byok:{connection_id}:{upstream_id}"
    conn.execute(
        text(
            "INSERT INTO catalog.model_catalog "
            "  (model_name, display_name, provider, input_price_cents_per_m, "
            "   output_price_cents_per_m, status, tenant_id, connection_id, "
            "   source, last_seen_at) "
            "VALUES (:name, :display, :provider, :cin, :cout, 'available', "
            "        CAST(:tid AS uuid), CAST(:cid AS uuid), 'byok', now()) "
            "ON CONFLICT (connection_id, model_name) WHERE source = 'byok' "
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
            "provider": connection_name,
            "cin": input_cents,
            "cout": output_cents,
            "tid": tenant_id,
            "cid": connection_id,
        },
    )
    return model_name


def _connection_rows(conn: Connection, connection_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT id, model_name, status, gateway_model_id "
                "FROM catalog.model_catalog "
                "WHERE source = 'byok' AND connection_id = CAST(:cid AS uuid)"
            ),
            {"cid": connection_id},
        ).mappings()
    ]


def refresh_connection(
    conn: Connection,
    tenant_id: str,
    connection_id: str,
    *,
    litellm: LiteLLMClient | None = None,
    actor_role: str = "system",
    actor_id: str | None = None,
) -> dict:
    """Discover + sync one llm connection into the catalog and the gateway.

    -> {"discovered": n, "updated": n, "stale": n, "models": [...]}.
    Audits model.catalog_refreshed (never the key).
    """
    litellm = litellm or get_litellm()
    row = _load_llm_connection(conn, tenant_id, connection_id)
    base_url = (row["metadata"] or {}).get("base_url")
    if not base_url:
        raise validation_failed(
            "LLM connections require metadata 'base_url'."
        )
    api_key = _decrypt_key(row)  # in memory, per use (D-24-05)
    input_cents, output_cents = _declared_prices(row["metadata"])
    upstream_ids = fetch_upstream_models(base_url, api_key)

    # Idempotent gateway registration (skip already-registered names).
    registered = litellm.db_model_ids()
    discovered_names: list[str] = []
    for upstream_id in upstream_ids:
        model_name = _upsert_byok_row(
            conn,
            tenant_id=tenant_id,
            connection_id=connection_id,
            connection_name=row["name"],
            upstream_id=upstream_id,
            input_cents=input_cents,
            output_cents=output_cents,
        )
        discovered_names.append(model_name)
        if model_name not in registered:
            created = litellm.add_model(
                model_name=model_name,
                upstream_model=f"openai/{upstream_id}",
                api_base=base_url,
                api_key=api_key,
                input_cost_per_token=input_cents / 100.0 / 1_000_000.0,
                output_cost_per_token=output_cents / 100.0 / 1_000_000.0,
            )
            gateway_model_id = str(created.get("model_id") or "")
            if gateway_model_id:
                conn.execute(
                    text(
                        "UPDATE catalog.model_catalog "
                        "SET gateway_model_id = :gmid WHERE model_name = :name"
                    ),
                    {"gmid": gateway_model_id, "name": model_name},
                )
        elif not _has_gateway_id(conn, model_name):
            # Already registered but the row lost its gateway id (legacy):
            # backfill from /model/info.
            conn.execute(
                text(
                    "UPDATE catalog.model_catalog SET gateway_model_id = :gmid "
                    "WHERE model_name = :name"
                ),
                {"gmid": registered[model_name], "name": model_name},
            )

    # Models that vanished from the API -> stale + gateway delete.
    seen = set(discovered_names)
    stale = 0
    for existing in _connection_rows(conn, connection_id):
        if existing["model_name"] in seen or existing["status"] in (
            "stale", "removed",
        ):
            continue
        _stale_row(conn, litellm, existing)
        stale += 1

    summary = {
        "scope": "connection",
        "connection_id": connection_id,
        "discovered": len(discovered_names),
        "stale": stale,
    }
    audit.emit(
        conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
        action="model.catalog_refreshed",
        target_type="vault_connection", target_id=connection_id,
        payload=summary,
    )
    return summary | {"models": discovered_names}


def _has_gateway_id(conn: Connection, model_name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT gateway_model_id FROM catalog.model_catalog "
            "WHERE model_name = :name"
        ),
        {"name": model_name},
    ).mappings().first()
    return bool(row and row["gateway_model_id"])


def _stale_row(
    conn: Connection, litellm: LiteLLMClient, existing: dict
) -> None:
    """Mark one byok row stale and remove it from the gateway."""
    conn.execute(
        text(
            "UPDATE catalog.model_catalog SET status = 'stale', "
            "updated_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(existing["id"])},
    )
    gateway_model_id = existing.get("gateway_model_id")
    if not gateway_model_id:
        # Legacy row without id: resolve by name (best effort, still real).
        try:
            gateway_model_id = litellm.db_model_ids().get(
                existing["model_name"]
            )
        except Exception:
            gateway_model_id = None
    if gateway_model_id:
        litellm.delete_model(gateway_model_id)


def mark_connection_stale(
    conn: Connection,
    tenant_id: str,
    connection_id: str,
    *,
    litellm: LiteLLMClient | None = None,
) -> int:
    """Revoked connection: all its models stale + removed from the gateway.

    Fail-closed (D-032-02): if the gateway is unreachable the caller's
    transaction must NOT complete — old vkeys would still route. Returns
    the number of models marked stale.
    """
    litellm = litellm or get_litellm()
    count = 0
    for existing in _connection_rows(conn, connection_id):
        if existing["status"] in ("stale", "removed"):
            continue
        _stale_row(conn, litellm, existing)
        count += 1
    if count:
        audit.emit(
            conn, tenant_id=tenant_id, actor_role="system",
            action="model.catalog_refreshed",
            target_type="vault_connection", target_id=connection_id,
            payload={
                "scope": "connection_revoked", "connection_id": connection_id,
                "stale": count,
            },
        )
    return count


def refresh_central(
    conn: Connection,
    tenant_id: str,
    *,
    litellm: LiteLLMClient | None = None,
    actor_role: str = "system",
    actor_id: str | None = None,
) -> dict:
    """Central source: reconcile catalog ↔ LIVE upstream /models (spec 018
    §5, D-018-07 — the authoritative source for the SET of central models)
    and catalog ↔ gateway routing (spec 032, the operative access path).

    - central ``available`` absent from the live upstream → ``stale``
      (fail-closed: out of allowed-for-me, verify 403, plan ``standard``
      no longer approves it). Existing ``model_access`` rows are KEPT —
      the status gate makes them ineffective, preserving approval history.
    - central ``stale`` that reappears upstream AND is routed → restored
      to ``available``.
    - upstream deployment missing from the catalog → INSERTED as central
      ``available`` with NULL pricing/limits (unknown until a doc-stamped
      migration — same contract as BYOK declared-0 pricing) and registered
      in the gateway so it routes from the first refresh. The audit
      payload's ``added`` list is the operational trigger for that
      migration.

    Without ``TRUST_MAAS_ENDPOINT``/``TRUST_MAAS_API_KEY`` the upstream leg
    is skipped and the refresh degrades to the gateway-routing check only —
    it never marks ``available`` anything it has not seen (no fail-open).
    The provider key lives in memory per call; it never reaches responses,
    logs nor the audit payload.
    """
    litellm = litellm or get_litellm()
    settings = get_settings()
    gateway_models = set(litellm.list_gateway_models())
    upstream_ids: set[str] | None = None
    if settings.maas_endpoint and settings.maas_api_key:
        upstream_ids = set(
            fetch_upstream_models(settings.maas_endpoint, settings.maas_api_key)
        )

    rows = conn.execute(
        text(
            "SELECT id, model_name, status FROM catalog.model_catalog "
            "WHERE source = 'central'"
        )
    ).mappings()
    stale = restored = 0
    known: set[str] = set()
    for r in rows:
        name = r["model_name"]
        known.add(name)
        routed = name in gateway_models
        # Upstream leg (authoritative when configured): a model the live
        # /models no longer lists is NOT usable even if the gateway still
        # routes it (the gateway would forward straight into an upstream
        # 404 — the exact GAP M-2 failure mode).
        in_upstream = (name in upstream_ids) if upstream_ids is not None else None
        usable = routed and in_upstream is not False
        if r["status"] == "available" and not usable:
            conn.execute(
                text(
                    "UPDATE catalog.model_catalog SET status = 'stale', "
                    "updated_at = now() WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(r["id"])},
            )
            stale += 1
        elif r["status"] == "stale" and usable:
            conn.execute(
                text(
                    "UPDATE catalog.model_catalog SET status = 'available', "
                    "updated_at = now() WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(r["id"])},
            )
            restored += 1

    # New upstream deployments absent from the catalog (D-018-07 "alta"):
    # central row with NULL pricing/limits + immediate gateway registration.
    added: list[str] = []
    if upstream_ids is not None:
        for name in sorted(upstream_ids - known):
            conn.execute(
                text(
                    "INSERT INTO catalog.model_catalog "
                    "  (model_name, display_name, provider, status, source, "
                    "   last_seen_at) "
                    "VALUES (:name, :name, 'huawei_maas', 'available', "
                    "        'central', now()) "
                    "ON CONFLICT (model_name) WHERE source = 'central' "
                    "DO UPDATE SET status = 'available', last_seen_at = now(), "
                    "  updated_at = now()"
                ),
                {"name": name},
            )
            if name not in gateway_models:
                created = litellm.add_model(
                    model_name=name,
                    upstream_model=f"openai/{name}",
                    api_base=settings.maas_endpoint,
                    api_key=settings.maas_api_key,
                )
                gateway_model_id = str(created.get("model_id") or "")
                if gateway_model_id:
                    conn.execute(
                        text(
                            "UPDATE catalog.model_catalog "
                            "SET gateway_model_id = :gmid WHERE model_name = :name"
                        ),
                        {"gmid": gateway_model_id, "name": name},
                    )
            added.append(name)

    summary = {
        "scope": "central", "stale": stale, "restored": restored,
        "added": added,
        "gateway_models": len(gateway_models),
        "upstream_models": len(upstream_ids) if upstream_ids is not None else None,
    }
    audit.emit(
        conn, tenant_id=tenant_id, actor_role=actor_role, actor_id=actor_id,
        action="model.catalog_refreshed", target_type="model_catalog",
        payload=summary,
    )
    return summary


def refresh_tenant(
    conn: Connection,
    tenant_id: str,
    *,
    litellm: LiteLLMClient | None = None,
    actor_role: str = "system",
    actor_id: str | None = None,
) -> dict:
    """Global refresh: central + every active llm connection of the tenant."""
    litellm = litellm or get_litellm()
    result: dict = {
        "central": refresh_central(
            conn, tenant_id, litellm=litellm,
            actor_role=actor_role, actor_id=actor_id,
        ),
        "connections": {},
    }
    rows = conn.execute(
        text(
            "SELECT id FROM vault_connections "
            "WHERE tenant_id = CAST(:tid AS uuid) AND status = 'active' "
            "AND kind = 'llm' ORDER BY created_at"
        ),
        {"tid": tenant_id},
    ).mappings()
    for r in rows:
        connection_id = str(r["id"])
        try:
            result["connections"][connection_id] = refresh_connection(
                conn, tenant_id, connection_id,
                litellm=litellm, actor_role=actor_role, actor_id=actor_id,
            )
        except Exception as exc:
            # One unreachable upstream must never block the refresh of the
            # remaining connections (nor the central sync, already done):
            # the error is reported per connection — no models are invented
            # for it and its rows simply keep their previous status
            # (constitution I: the catalog never fabricates).
            result["connections"][connection_id] = {
                "scope": "connection",
                "connection_id": connection_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result


def active_llm_tenants(migrator_engine) -> list[str]:
    """Cross-tenant read-only listing for the jobs worker (D-CP-10 pattern)."""
    from sqlalchemy import text as _text

    with migrator_engine.connect() as c:
        rows = c.execute(
            _text(
                "SELECT DISTINCT tenant_id FROM vault_connections "
                "WHERE status = 'active' AND kind = 'llm'"
            )
        ).mappings().all()
    return sorted(str(r["tenant_id"]) for r in rows)


def active_tenants(migrator_engine) -> list[str]:
    """All active tenants (cross-tenant read via the BYPASSRLS migrator
    connection, D-CP-10 pattern).

    Spec 018 §5 (D-018-07): the periodic catalog refresh runs for EVERY
    active tenant, not only those with llm connections — central-catalog
    drift (a model retired or deployed upstream) affects all of them, and
    refresh_tenant's central leg is the sync mechanism.
    """
    from sqlalchemy import text as _text

    with migrator_engine.connect() as c:
        rows = c.execute(
            _text("SELECT id FROM tenants WHERE status = 'active' ORDER BY id")
        ).mappings().all()
    return sorted(str(r["id"]) for r in rows)

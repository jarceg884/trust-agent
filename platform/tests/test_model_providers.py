"""Spec 036 — Model providers operados por el provider (repro Miller t_64e645c1).

Dobles DECLARADOS (D-GOV-15, mismo patrón que spec 032):
  - ``FakeOpenAIUpstream`` (reutilizado de test_dynamic_discovery): servidor
    HTTP real con el contrato OpenAI-compatible ``GET /v1/models`` — el
    discovery del proveedor hace HTTP real contra él; la evidencia live
    (AC-036-08) usa MaaS real (scripts/evidence_model_providers.py).
  - ``litellm_fake`` (conftest): gateway con /model/new, /model/delete,
    /model/info — las rutas compartidas por tenant se registran/bajan ahí.

Criterios de aceptación (DB efímera canónica, seeds A/B del conftest):
  - AC-036-01: alta de proveedor con key en envelope (ninguna respuesta ni
    audit lleva la key); 409 duplicado; 422 base_url/precios; listado sin
    material de key.
  - AC-036-02: refresh = fetch REAL → filas source='provider' SOLO para los
    tenants habilitados; registro compartido en el gateway.
  - AC-036-03: habilitar = incluido por defecto (aprobación org mode=
    'provider' sin tocar revocaciones previas); deshabilitar → stale +
    verify 403; una revocación org NO se re-aprueba al re-habilitar.
  - AC-036-04: 401/403 data-driven (providers.manage); audit del operador
    (ops.provider_audit) + audit del tenant, sin keys.
  - AC-036-05: el refresh global del tenant incluye la pata providers.
  - AC-036-07: RLS — el tenant B no ve los modelos-provider del tenant A.
"""
import json
import uuid

import psycopg
import pytest

from conftest import (
    ADMIN_DSN,
    DEPT_A1,
    SUBS,
    TENANT_A,
    TENANT_B,
    TEST_DB,
    USER_CAROL,
    bearer_headers,
    mint,
)
from test_dynamic_discovery import FakeOpenAIUpstream, UPSTREAM_SECRET

PROVIDER = {"Authorization": "Bearer test-provider-token"}
BAD = {"Authorization": "Bearer wrong-token"}

APP_DSN = (
    "host=127.0.0.1 port=55432 user=trust_app password=trust_app_dev "
    f"dbname={TEST_DB}"
)


@pytest.fixture()
def upstream():
    server = FakeOpenAIUpstream(["prov-chat-8k", "prov-reason-32k"])
    yield server
    server.shutdown()


def _create_provider(client, upstream, *, name=None, base_url=None,
                     api_key=UPSTREAM_SECRET, prices=(100, 200)):
    return client.post(
        "/api/admin/model-providers",
        json={
            "name": name or f"acme-{uuid.uuid4().hex[:8]}",
            "base_url": base_url or upstream.base_url,
            "api_key": api_key,
            "input_price_cents_per_m": prices[0],
            "output_price_cents_per_m": prices[1],
        },
        headers=PROVIDER,
    )


def _enable(client, provider_id, tenant="tenant-a"):
    return client.post(
        f"/api/admin/model-providers/{provider_id}/tenants",
        json={"tenant": tenant},
        headers=PROVIDER,
    )


def _disable(client, provider_id, tenant_id=TENANT_A):
    return client.delete(
        f"/api/admin/model-providers/{provider_id}/tenants/{tenant_id}",
        headers=PROVIDER,
    )


def _refresh_provider(client, provider_id):
    return client.post(
        f"/api/admin/model-providers/{provider_id}/refresh",
        headers=PROVIDER,
    )


def _models(client, tokens):
    resp = client.get("/api/models", headers=bearer_headers(tokens))
    assert resp.status_code == 200, resp.text
    return {m["model_name"]: m for m in resp.json()["models"]}


def _verify(client, tokens, model_name):
    return client.post("/api/models/verify", json={"model_name": model_name},
                       headers=bearer_headers(tokens))


def _approve(client, tokens, model_name, level="org", mode="provider",
             department_id=None, user_id=None):
    return client.put(
        "/api/models/access",
        json={"model_name": model_name, "level": level, "mode": mode,
              "department_id": department_id, "user_id": user_id},
        headers=bearer_headers(tokens),
    )


def _revoke_org(client, tokens, model_name):
    return client.post("/api/models/access/revoke",
                       json={"model_name": model_name, "level": "org"},
                       headers=bearer_headers(tokens))


def _tenant_audit(action, target_id=None):
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            cur.execute(
                "SELECT payload FROM audit_log WHERE action = %s "
                "AND (%s::text IS NULL OR target_id = %s) ORDER BY ts",
                (action, target_id, target_id),
            )
            return [row[0] for row in cur.fetchall()]


def _provider_audit(action):
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM ops.provider_audit WHERE action = %s "
                "ORDER BY ts", (action,),
            )
            return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# AC-036-01 — alta + higiene de la key (envelope, jamás en respuestas)
# ---------------------------------------------------------------------------

def test_ac036_01_create_validation_and_key_hygiene(client, upstream):
    # Auth matrix: no token / bad token → 401
    for headers in (None, BAD):
        resp = client.post(
            "/api/admin/model-providers",
            json={"name": "x", "base_url": upstream.base_url,
                  "api_key": "k"},
            headers=headers,
        )
        assert resp.status_code == 401, resp.text

    resp = _create_provider(client, upstream, name="hygiene-prov")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    pid = body["id"]
    assert body["status"] == "active"
    # NO key material anywhere in the response (plaintext NOR ciphertext)
    dumped = json.dumps(body)
    assert UPSTREAM_SECRET not in dumped
    assert "ciphertext" not in dumped and "nonce" not in dumped.lower()

    # Duplicate name → 409
    resp = _create_provider(client, upstream, name="hygiene-prov")
    assert resp.status_code == 409, resp.text

    # Invalid base_url / negative pricing / empty key → 422
    assert _create_provider(
        client, upstream, name="bad-url", base_url="ftp://nope"
    ).status_code == 422
    assert _create_provider(
        client, upstream, name="bad-price", prices=(-1, 200)
    ).status_code == 422
    resp = client.post(
        "/api/admin/model-providers",
        json={"name": "no-key", "base_url": upstream.base_url,
              "api_key": "  "},
        headers=PROVIDER,
    )
    assert resp.status_code == 422, resp.text

    # Listing carries NO key material either
    resp = client.get("/api/admin/model-providers", headers=PROVIDER)
    assert resp.status_code == 200, resp.text
    dumped = json.dumps(resp.json())
    assert UPSTREAM_SECRET not in dumped
    assert "ciphertext" not in dumped
    row = next(p for p in resp.json()["providers"]
               if p["name"] == "hygiene-prov")
    assert row["id"] == pid and row["enabled_tenants"] == 0

    # The key lives ONLY as an envelope ciphertext in ops.model_providers
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ciphertext_secret, ciphertext_dek, nonce, key_id "
                "FROM ops.model_providers WHERE id = %s", (pid,),
            )
            sec, dek, nonce, key_id = cur.fetchone()
    assert sec and dek and nonce and key_id
    assert UPSTREAM_SECRET.encode() not in sec  # encrypted, not plaintext

    # Operator audit exists and never carries the key
    rows = _provider_audit("provider.created")
    assert any(p.get("provider_id") == pid for p in rows)
    assert UPSTREAM_SECRET not in json.dumps(rows)


# ---------------------------------------------------------------------------
# AC-036-02/03 — habilitar por tenant: fetch real, incluido por defecto
# ---------------------------------------------------------------------------

def test_ac036_02_03_enable_discovery_and_default_approval(
    client, idp, upstream, litellm_fake
):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    pid = _create_provider(client, upstream, name="default-prov").json()["id"]

    # Refresh with NO enabled tenants: real fetch, nothing materialized
    resp = _refresh_provider(client, pid)
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["upstream_models"] == 2, summary
    assert summary["tenants"] == []
    assert upstream.seen_auth, "refresh never called the upstream"
    assert upstream.seen_auth[-1] == f"Bearer {UPSTREAM_SECRET}"

    # Enable for tenant A → immediate discovery + org approvals
    resp = _enable(client, pid, "tenant-a")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant"]["slug"] == "tenant-a"
    assert body["discovered"] == 2 and body["approved"] == 2, body

    chat = f"prov:{pid}:prov-chat-8k"
    reason = f"prov:{pid}:prov-reason-32k"

    # Tenant A sees them as platform-included provider models
    models = _models(client, alice)
    assert models[chat]["source"] == "provider"
    assert models[chat]["provider"] == "default-prov"
    assert models[chat]["status"] == "available"
    assert float(models[chat]["input_price_cents_per_m"]) == 100.0
    # Central models remain untouched
    assert models["glm-5.2"]["source"] == "central"

    # Access overview marks them by provider (Admin Console contract) with
    # the org approval stamped by the enablement (mode='provider').
    resp = client.get("/api/models/access", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    rows = {m["model_name"]: m for m in resp.json()["models"]}
    assert rows[chat]["provider_source_name"] == "default-prov"
    assert rows[chat]["provider_id"] == pid
    def _org_provider(a):
        return (a["level"] == "org" and a["mode"] == "provider"
                and a["department_id"] is None and a["user_id"] is None)

    assert any(_org_provider(a) for a in rows[chat]["approvals"])
    assert any(_org_provider(a) for a in rows[reason]["approvals"])

    # Effective set + verify for a plain user of the org. The seed narrows
    # dept IT (A1) to glm-5.2 only — the subset rule (existing governance,
    # unchanged) requires the dept to approve the new model too, exactly
    # like the byok cascade in spec 032.
    assert _approve(client, alice, chat, "dept",
                    department_id=DEPT_A1).status_code == 201
    assert _approve(client, alice, chat, "user",
                    department_id=DEPT_A1, user_id=USER_CAROL).status_code == 201
    resp = client.get("/api/models/allowed-for-me",
                      headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text
    assert chat in resp.json()["models"]
    assert _verify(client, carol, chat).status_code == 200

    # Shared gateway registration (one route, key of the PROVIDER)
    assert chat in litellm_fake.models
    registered = litellm_fake.models[chat]
    assert registered["litellm_params"]["api_key"] == UPSTREAM_SECRET
    assert registered["litellm_params"]["api_base"] == upstream.base_url

    # Double enable → 409; unknown tenant → 404
    assert _enable(client, pid, "tenant-a").status_code == 409
    assert _enable(client, pid, "no-such-tenant").status_code == 404

    # Tenant audit: enabled + refreshed, never the key
    enabled = _tenant_audit("model.provider_enabled", pid)
    assert enabled and UPSTREAM_SECRET not in json.dumps(enabled)


def test_ac036_03_disable_gates_and_revoke_never_reapproved(
    client, idp, upstream, litellm_fake
):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    pid = _create_provider(client, upstream, name="gate-prov").json()["id"]
    assert _enable(client, pid).status_code == 201

    chat = f"prov:{pid}:prov-chat-8k"
    reason = f"prov:{pid}:prov-reason-32k"
    # Dept cascade (the seed narrows dept IT to glm-5.2 — subset rule)
    for name in (chat, reason):
        assert _approve(client, alice, name, "dept",
                        department_id=DEPT_A1).status_code == 201
        assert _approve(client, alice, name, "user",
                        department_id=DEPT_A1,
                        user_id=USER_CAROL).status_code == 201
    assert _verify(client, carol, chat).status_code == 200

    # The admin revokes ONE model at org — a revoke must not relax (D-GOV-11)
    assert _revoke_org(client, alice, chat).status_code == 200
    assert _verify(client, carol, chat).status_code == 403

    # Disable the provider for the tenant → every row stale, fail-closed
    litellm_fake.model_delete_calls.clear()
    resp = _disable(client, pid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["stale"] == 2, resp.text
    models = _models(client, alice)
    assert models[chat]["status"] == "stale"
    assert models[reason]["status"] == "stale"
    # Still-approved model is gated too (composite gate, deny-overrides)
    assert _verify(client, carol, reason).status_code == 403
    denied = _tenant_audit("model.access_denied", reason)
    assert denied
    # Shared gateway routes freed (this was the only consumer)
    assert litellm_fake.model_delete_calls, "gateway delete never happened"
    assert chat not in litellm_fake.models
    # Disablement audited in the tenant chain
    assert _tenant_audit("model.provider_disabled", pid)

    # Re-enable: the REVOKED model is NOT re-approved (its denied org row
    # persists — a revoke never relaxes, D-GOV-11); the other one keeps its
    # approval and becomes effective again once the row is back.
    resp = _enable(client, pid)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["discovered"] == 2
    assert body["approved"] == 0, body  # both already HAD org rows
    assert _verify(client, carol, chat).status_code == 403   # still denied
    assert _verify(client, carol, reason).status_code == 200  # restored


def test_ac036_03_global_suspend_reactivates(client, idp, upstream,
                                             litellm_fake):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    pid = _create_provider(client, upstream, name="suspend-prov").json()["id"]
    assert _enable(client, pid).status_code == 201
    chat = f"prov:{pid}:prov-chat-8k"
    assert _approve(client, alice, chat, "dept",
                    department_id=DEPT_A1).status_code == 201
    assert _approve(client, alice, chat, "user",
                    department_id=DEPT_A1, user_id=USER_CAROL).status_code == 201
    assert _verify(client, carol, chat).status_code == 200

    litellm_fake.model_delete_calls.clear()
    resp = client.post(f"/api/admin/model-providers/{pid}/status",
                       json={"status": "disabled"}, headers=PROVIDER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is True and body["stale"] == 2
    assert _models(client, alice)[chat]["status"] == "stale"
    assert _verify(client, carol, chat).status_code == 403
    assert chat not in litellm_fake.models  # every shared route dropped

    # Refresh while disabled → 422 (fail-closed, nothing invented)
    assert _refresh_provider(client, pid).status_code == 422

    # Reactivate + refresh → available again
    resp = client.post(f"/api/admin/model-providers/{pid}/status",
                       json={"status": "active"}, headers=PROVIDER)
    assert resp.status_code == 200 and resp.json()["changed"] is True
    resp = _refresh_provider(client, pid)
    assert resp.status_code == 200, resp.text
    assert _models(client, alice)[chat]["status"] == "available"
    assert _verify(client, carol, chat).status_code == 200


# ---------------------------------------------------------------------------
# AC-036-04 — permiso data-driven + audit del operador
# ---------------------------------------------------------------------------

def test_ac036_04_permission_is_data_driven(client, admin_conn):
    """Borrar la fila platform_admin→providers.manage del catálogo debe
    negar TODAS las rutas (D-ONB-02, paridad AC-ONB-030)."""
    routes = [
        ("GET", "/api/admin/model-providers", None),
        ("POST", "/api/admin/model-providers",
         {"name": "x", "base_url": "http://x/v1", "api_key": "k"}),
        ("GET", "/api/admin/model-providers/"
         "00000000-0000-0000-0000-000000000001", None),
        ("POST", "/api/admin/model-providers/"
         "00000000-0000-0000-0000-000000000001/refresh", None),
        ("POST", "/api/admin/model-providers/"
         "00000000-0000-0000-0000-000000000001/tenants", {"tenant": "t"}),
        ("DELETE", "/api/admin/model-providers/"
         "00000000-0000-0000-0000-000000000001/tenants/"
         "00000000-0000-0000-0000-000000000002", None),
        ("POST", "/api/admin/model-providers/"
         "00000000-0000-0000-0000-000000000001/status",
         {"status": "disabled"}),
    ]
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM catalog.role_permissions "
            "WHERE role_key = 'platform_admin' "
            "AND permission_key = 'providers.manage'"
        )
    try:
        for method, path, payload in routes:
            if method == "GET":
                resp = client.get(path, headers=PROVIDER)
            elif method == "DELETE":
                resp = client.delete(path, headers=PROVIDER)
            else:
                resp = client.post(path, json=payload, headers=PROVIDER)
            assert resp.status_code == 403, (path, resp.text)
            assert resp.json()["error"]["reason"] == "permission_denied"
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO catalog.role_permissions "
                "(role_key, permission_key) VALUES "
                "('platform_admin', 'providers.manage') ON CONFLICT DO NOTHING"
            )


def test_ac036_04_operator_audit_chain(client, upstream):
    pid = _create_provider(client, upstream, name="audit-prov").json()["id"]
    assert _refresh_provider(client, pid).status_code == 200
    rows = _provider_audit("provider.refreshed")
    match = [p for p in rows if p.get("provider_id") == pid]
    assert match and match[0]["upstream_models"] == 2
    assert UPSTREAM_SECRET not in json.dumps(rows)


# ---------------------------------------------------------------------------
# AC-036-05 — la pata providers del refresh global del tenant
# ---------------------------------------------------------------------------

def test_ac036_05_tenant_refresh_includes_providers(client, idp, upstream):
    alice = mint(client, idp, "alice")
    pid = _create_provider(client, upstream, name="leg-prov").json()["id"]
    assert _enable(client, pid).status_code == 201
    chat = f"prov:{pid}:prov-chat-8k"
    reason = f"prov:{pid}:prov-reason-32k"

    # The upstream drops a model → the GLOBAL tenant refresh stales it
    upstream.models = ["prov-chat-8k"]
    resp = client.post("/api/models/catalog/refresh", json={},
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    leg = summary["providers"]["leg-prov"]
    assert leg["discovered"] == 1 and leg["stale"] == 1, leg
    models = _models(client, alice)
    assert models[reason]["status"] == "stale"
    assert models[chat]["status"] == "available"


# ---------------------------------------------------------------------------
# AC-036-07 — RLS: los modelos-provider son filas del tenant habilitado
# ---------------------------------------------------------------------------

def test_ac036_07_rls_isolation(client, idp, upstream):
    alice = mint(client, idp, "alice")
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    pid = _create_provider(client, upstream, name="rls-prov").json()["id"]
    assert _enable(client, pid, "tenant-a").status_code == 201
    chat = f"prov:{pid}:prov-chat-8k"

    # Tenant B (not enabled) sees NOTHING — API…
    assert chat not in _models(client, dave)
    # …and SQL under RLS as trust_app
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_B,))
            cur.execute(
                "SELECT count(*) FROM catalog.model_catalog "
                "WHERE source = 'provider' AND provider_id = %s", (pid,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            cur.execute(
                "SELECT count(*) FROM catalog.model_catalog "
                "WHERE source = 'provider' AND provider_id = %s", (pid,),
            )
            assert cur.fetchone()[0] == 2

    # Enabling tenant B materializes ITS OWN rows (same shared model_name)
    resp = _enable(client, pid, "tenant-b")
    assert resp.status_code == 201, resp.text
    assert resp.json()["approved"] == 2
    assert _models(client, dave)[chat]["source"] == "provider"
    assert _models(client, alice)[chat]["status"] == "available"


# ---------------------------------------------------------------------------
# Espejo D-GOV-06 — spend_ledger.mode acepta 'provider' (regresión del run
# de evidencia AC-036-08: sin el espejo del CHECK, el ingest del
# spend-worker moría con CheckViolation y congelaba el cursor global).
# ---------------------------------------------------------------------------

def test_ac036_spend_ledger_accepts_provider_mode(client, idp, upstream):
    alice = mint(client, idp, "alice")
    pid = _create_provider(client, upstream, name="spend-prov").json()["id"]
    assert _enable(client, pid).status_code == 201
    chat = f"prov:{pid}:prov-chat-8k"

    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            # La aprobación org lleva mode='provider' (D-036-03)…
            cur.execute(
                "SELECT ma.mode FROM public.model_access ma "
                "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
                "WHERE mc.model_name = %s AND ma.status = 'approved'",
                (chat,),
            )
            assert cur.fetchone()[0] == "provider"
            user_id = USER_CAROL  # UUID del seed del conftest (dept A1)
            # …y el ledger ACEPTA esa mode (CHECK con 'provider', D-GOV-06).
            cur.execute(
                "INSERT INTO public.spend_ledger (tenant_id, request_id, user_id, "
                "model_name, mode, cost_base_cents, cost_billed_cents, "
                "tokens_in, tokens_out, occurred_at) VALUES "
                "(%s, %s, %s, %s, 'provider', 1, 1, 10, 10, now())",
                (TENANT_A, f"spend-prov-{pid[:8]}", user_id, chat),
            )
            cur.execute(
                "SELECT mode FROM public.spend_ledger "
                "WHERE request_id = %s", (f"spend-prov-{pid[:8]}",),
            )
            assert cur.fetchone()[0] == "provider"
        # Only the CHECK is under test: the row must NOT stay in the shared
        # ledger. Committed, it counted as a real request of Carol today and
        # broke the exact counts of test_spend_cutoff / test_usage_analytics
        # whenever the whole suite ran (they pass in isolation).
        conn.rollback()


# ---------------------------------------------------------------------------
# Regresión RLS del run UI AC-036-06: los conteos del listado/detalle del
# provider son lecturas cross-tenant de catalog.model_catalog (BYPASSRLS
# migrator) — con el engine del app (sin contexto de tenant) RLS devolvía
# SIEMPRE 0 modelos y el detalle llegaba vacío.
# ---------------------------------------------------------------------------

def test_ac036_provider_listing_counts_cross_tenant(client, idp, upstream):
    pid = _create_provider(client, upstream, name="count-prov").json()["id"]
    resp = client.get("/api/admin/model-providers", headers=PROVIDER)
    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.json()["providers"]}
    assert by_id[pid]["available_models"] == 0  # nothing enabled yet

    assert _enable(client, pid).status_code == 201
    resp = client.get("/api/admin/model-providers", headers=PROVIDER)
    by_id = {p["id"]: p for p in resp.json()["providers"]}
    assert by_id[pid]["available_models"] == 2, by_id[pid]
    assert by_id[pid]["enabled_tenants"] == 1

    resp = client.get(f"/api/admin/model-providers/{pid}", headers=PROVIDER)
    assert resp.status_code == 200
    detail = resp.json()
    assert len(detail["models"]) == 2, detail["models"]
    assert {m["status"] for m in detail["models"]} == {"available"}

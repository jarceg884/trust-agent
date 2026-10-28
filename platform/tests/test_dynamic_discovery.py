"""Spec 032 — Dynamic model discovery per connected API (mandato Miller).

Dobles DECLARADOS (D-GOV-15, mismo patrón que litellm_fake):
  - ``FakeOpenAIUpstream``: servidor HTTP real que implementa el contrato
    OpenAI-compatible ``GET /v1/models`` (bearer) — el discovery hace HTTP
    real contra él; la evidencia live (AC-032-05/08) usa MaaS real.
  - ``litellm_fake`` extendido con /model/new, /model/delete (por model_id),
    /model/info y /v1/models (contrato verificado contra el proxy real
    v1.106.3).

Criterios de aceptación (DB efímera canónica):
  - AC-032-01: conexión llm → refresh → modelos byok en catálogo con source,
    provider = nombre de conexión; key jamás en respuestas.
  - AC-032-02: modelo que desaparece del upstream → stale → no efectivo;
    reaparece → available; revocar conexión → stale + baja del gateway.
  - AC-032-03: refresh global (central + conexiones) audita
    model.catalog_refreshed; job periódico idempotente por ventana.
  - AC-032-04: gate compuesto — aprobado en org/depto/usuario PERO la
    conexión fuera de la policy efectiva → verify 403 + audit.
  - AC-032-06: access overview expone source/connection_name/status.
  - AC-032-07: RLS — tenant B no ve los modelos byok de tenant A.
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import psycopg
import pytest

from conftest import (
    DEPT_A1,
    SUBS,
    TENANT_A,
    TENANT_B,
    TEST_DB,
    USER_ALICE,
    USER_CAROL,
    USER_DAVE,
    bearer_headers,
    mint,
)

APP_DSN = (
    "host=127.0.0.1 port=55432 user=trust_app password=trust_app_dev "
    f"dbname={TEST_DB}"
)

UPSTREAM_SECRET = "sk-upstream-test-key-3f2a"


class FakeOpenAIUpstream:
    """DECLARED TEST DOUBLE: real HTTP server implementing the documented
    OpenAI-compatible ``GET /v1/models`` contract (bearer auth)."""

    def __init__(self, models: list[str]):
        self.models = list(models)
        self.seen_auth: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                auth = self.headers.get("Authorization", "")
                outer.seen_auth.append(auth)
                if auth != f"Bearer {UPSTREAM_SECRET}":
                    self._json(401, {"error": {"message": "Invalid key"}})
                    return
                if self.path.rstrip("/").endswith("/models"):
                    self._json(200, {"data": [{"id": m, "object": "model"}
                                              for m in outer.models]})
                    return
                self._json(404, {"error": "not found"})

            def _json(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def shutdown(self) -> None:
        self._server.shutdown()


@pytest.fixture()
def upstream():
    server = FakeOpenAIUpstream(["mini-chat-8k", "mini-reason-32k"])
    yield server
    server.shutdown()


def _put_policy(client, tokens, path, key, value):
    return client.put(path, json={"key": key, "value": value},
                      headers=bearer_headers(tokens))


def _create_llm_connection(client, tokens, upstream, *, name=None):
    # Unique name per test: vault_connections has UNIQUE (tenant_id, name)
    # and every test mints sessions in the same seeded tenant A.
    name = name or f"acme-llm-{uuid.uuid4().hex[:8]}"
    return client.post(
        "/api/vault/connections",
        json={
            "name": name,
            "kind": "llm",
            "metadata": {
                "base_url": upstream.base_url,
                "input_price_cents_per_m": 100,
                "output_price_cents_per_m": 200,
            },
            "secret_value": UPSTREAM_SECRET,
        },
        headers=bearer_headers(tokens),
    )


def _refresh(client, tokens, connection_id=None):
    return client.post(
        "/api/models/catalog/refresh",
        json={"connection_id": connection_id},
        headers=bearer_headers(tokens),
    )


def _approve(client, tokens, model_name, level="org", mode="byok",
             department_id=None, user_id=None):
    return client.put(
        "/api/models/access",
        json={"model_name": model_name, "level": level, "mode": mode,
              "department_id": department_id, "user_id": user_id},
        headers=bearer_headers(tokens),
    )


def _verify(client, tokens, model_name):
    return client.post("/api/models/verify", json={"model_name": model_name},
                       headers=bearer_headers(tokens))


# ---------------------------------------------------------------------------
# AC-032-01 — discovery por conexión
# ---------------------------------------------------------------------------

def test_connection_kind_llm_validation(client, idp):
    alice = mint(client, idp, "alice")
    # base_url is required for llm connections
    resp = client.post(
        "/api/vault/connections",
        json={"name": "bad-llm", "kind": "llm", "metadata": {},
              "secret_value": "k"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422, resp.text
    # negative pricing is rejected
    resp = client.post(
        "/api/vault/connections",
        json={"name": "bad-llm2", "kind": "llm",
              "metadata": {"base_url": "http://x/v1",
                           "input_price_cents_per_m": -5},
              "secret_value": "k"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422, resp.text


def test_discovery_populates_catalog(client, idp, upstream, litellm_fake):
    alice = mint(client, idp, "alice")
    resp = _create_llm_connection(client, alice, upstream)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    cid = body["connection_id"]
    conn_name = body["metadata"]["name"]
    # One-time secret only; metadata never carries it
    assert body["secret_value"] == UPSTREAM_SECRET
    assert "secret" not in json.dumps(body["metadata"]).lower()

    # Policy allows the connection at org (spec 024 D-24-03)
    resp = _put_policy(client, alice, "/api/policies/org",
                       "allowed_connections", ["*", cid])
    assert resp.status_code in (200, 201), resp.text

    # Refresh (admin action) — real HTTP fetch against the double upstream
    resp = _refresh(client, alice, cid)
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["discovered"] == 2, summary
    assert summary["stale"] == 0

    # The upstream saw the DECRYPTED key over the wire (bearer)
    assert upstream.seen_auth, "discovery never called the upstream"
    assert upstream.seen_auth[-1] == f"Bearer {UPSTREAM_SECRET}"

    model_name = f"byok:{cid}:mini-chat-8k"
    resp = client.get("/api/models", headers=bearer_headers(alice))
    assert resp.status_code == 200
    models = {m["model_name"]: m for m in resp.json()["models"]}
    assert model_name in models, sorted(models)
    row = models[model_name]
    assert row["source"] == "byok"
    assert row["provider"] == conn_name
    assert row["status"] == "available"
    assert float(row["input_price_cents_per_m"]) == 100.0
    # Central models remain
    assert "glm-5.2" in models and models["glm-5.2"]["source"] == "central"

    # Gateway registration (idempotent by name)
    assert model_name in litellm_fake.models
    registered = litellm_fake.models[model_name]
    assert registered["litellm_params"]["api_key"] == UPSTREAM_SECRET
    assert registered["model_info"]["input_cost_per_token"] == pytest.approx(
        100 / 100 / 1_000_000)

    # Second refresh does not duplicate gateway registrations
    resp = _refresh(client, alice, cid)
    assert resp.status_code == 200
    assert sorted(litellm_fake.models) == sorted(
        [f"byok:{cid}:mini-chat-8k", f"byok:{cid}:mini-reason-32k"])

    # Audit exists and never carries the key (RLS: set the tenant scope)
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            cur.execute("SELECT count(*) FROM audit_log "
                        "WHERE action = 'model.catalog_refreshed'")
            assert cur.fetchone()[0] >= 2
            cur.execute("SELECT payload FROM audit_log "
                        "WHERE action = 'model.catalog_refreshed'")
            for (payload,) in cur.fetchall():
                assert UPSTREAM_SECRET not in json.dumps(payload)


# ---------------------------------------------------------------------------
# AC-032-02 — stale lifecycle
# ---------------------------------------------------------------------------

def test_stale_lifecycle_and_revoke(client, idp, upstream, litellm_fake):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    cid = _create_llm_connection(client, alice, upstream).json()["connection_id"]
    _put_policy(client, alice, "/api/policies/org",
                "allowed_connections", ["*", cid])
    assert _refresh(client, alice, cid).status_code == 200
    model_name = f"byok:{cid}:mini-chat-8k"

    # Approve the full cascade (org → dept → user; subset rule)
    assert _approve(client, alice, model_name, "org").status_code == 201
    assert _approve(client, alice, model_name, "dept",
                    department_id=DEPT_A1).status_code == 201
    assert _approve(client, alice, model_name, "user",
                    department_id=DEPT_A1, user_id=USER_CAROL).status_code == 201
    assert _verify(client, carol, model_name).status_code == 200

    # The upstream drops a model -> refresh marks it stale
    upstream.models = ["mini-chat-8k"]
    dropped = f"byok:{cid}:mini-reason-32k"
    resp = _refresh(client, alice, cid)
    assert resp.json()["stale"] == 1
    resp = client.get("/api/models", headers=bearer_headers(alice))
    statuses = {m["model_name"]: m["status"] for m in resp.json()["models"]}
    assert statuses[dropped] == "stale"
    assert statuses[model_name] == "available"
    # Stale model removed from the gateway
    assert dropped not in litellm_fake.models

    # It reappears -> restored
    upstream.models = ["mini-chat-8k", "mini-reason-32k"]
    _refresh(client, alice, cid)
    resp = client.get("/api/models", headers=bearer_headers(alice))
    statuses = {m["model_name"]: m["status"] for m in resp.json()["models"]}
    assert statuses[dropped] == "available"

    # Revoke the connection -> every byok model stale + gateway delete,
    # and the approved model stops being invocable (composite gate).
    resp = client.post(f"/api/vault/connections/{cid}/revoke",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    resp = client.get("/api/models", headers=bearer_headers(alice))
    for m in resp.json()["models"]:
        # Scope: THIS connection's models (the session DB accumulates byok
        # rows from earlier tests — each one owns its own connection).
        if m["source"] == "byok" and m["model_name"].startswith(f"byok:{cid}:"):
            assert m["status"] == "stale", m
    assert litellm_fake.model_delete_calls, "gateway delete never happened"
    assert model_name not in litellm_fake.models

    resp = _verify(client, carol, model_name)
    assert resp.status_code == 403, resp.text
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            cur.execute(
                "SELECT count(*) FROM audit_log "
                "WHERE action = 'model.access_denied' "
                "AND target_id = %s", (model_name,))
            assert cur.fetchone()[0] >= 1


# ---------------------------------------------------------------------------
# AC-032-04 — composite deny-overrides (connection policy ∧ approval)
# ---------------------------------------------------------------------------

def test_composite_gate_connection_policy(client, idp, upstream, litellm_fake):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    cid = _create_llm_connection(client, alice, upstream).json()["connection_id"]
    _put_policy(client, alice, "/api/policies/org",
                "allowed_connections", ["*", cid])
    _refresh(client, alice, cid)
    model_name = f"byok:{cid}:mini-chat-8k"
    _approve(client, alice, model_name, "org")
    _approve(client, alice, model_name, "dept", department_id=DEPT_A1)
    _approve(client, alice, model_name, "user",
             department_id=DEPT_A1, user_id=USER_CAROL)
    assert _verify(client, carol, model_name).status_code == 200

    # User-level allowed_connections = [] (deny-all, deny-overrides):
    # the approval chain stays intact but the CONNECTION side denies.
    resp = _put_policy(client, alice, f"/api/policies/user/{USER_CAROL}",
                       "allowed_connections", [])
    assert resp.status_code in (200, 201), resp.text
    resp = _verify(client, carol, model_name)
    assert resp.status_code == 403, resp.text
    resp = client.get("/api/models/allowed-for-me",
                      headers=bearer_headers(carol))
    assert model_name not in resp.json()["models"]

    # Removing the user-level denial restores access (org policy applies).
    # DELETE route shape: /policies/{level}/{key} with scope query params.
    resp = client.delete(
        "/api/policies/user/allowed_connections"
        f"?department_id={DEPT_A1}&user_id={USER_CAROL}",
        headers=bearer_headers(alice))
    assert resp.status_code == 204, resp.text
    assert _verify(client, carol, model_name).status_code == 200


# ---------------------------------------------------------------------------
# AC-032-03 — refresh global + central sync + job periódico
# ---------------------------------------------------------------------------

def test_global_refresh_and_central_sync(client, idp, upstream, litellm_fake):
    alice = mint(client, idp, "alice")
    cid = _create_llm_connection(client, alice, upstream).json()["connection_id"]
    _put_policy(client, alice, "/api/policies/org",
                "allowed_connections", ["*", cid])

    # Central drift: the gateway stops routing glm-5.1. The double's
    # /v1/models lists prices ∪ registered models, so seed all live
    # central deployments first (spec 018 §5 catalog: qwen3-32b is
    # retired-stale in the seed and the gateway does NOT route it) and
    # then drop one.
    litellm_fake.prices = {
        "glm-5.3": (140, 440), "glm-5.2": (140, 440), "glm-5.1": (107.8, 377.4),
        "deepseek-v4.1-flash-1017": (30, 120),
        "deepseek-v4.1-flash": (30, 120), "deepseek-v4-flash": (13.5, 27),
    }
    litellm_fake.prices.pop("glm-5.1")
    resp = _refresh(client, alice)  # global
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["central"]["stale"] == 1, result
    assert cid in result["connections"], result
    resp = client.get("/api/models", headers=bearer_headers(alice))
    statuses = {m["model_name"]: m["status"] for m in resp.json()["models"]}
    assert statuses["glm-5.1"] == "stale"
    assert statuses[f"byok:{cid}:mini-chat-8k"] == "available"

    # Non-admin cannot refresh
    carol = mint(client, idp, "carol")
    assert _refresh(client, carol).status_code == 403


def test_periodic_refresh_job_idempotent(client, idp, upstream):
    from app.config import get_settings
    from app.db import build_engine
    from app.jobs import service as jobs_service
    from app.jobs import worker as jobs_worker
    from app.jobs.service import build_migrator_engine

    alice = mint(client, idp, "alice")
    cid = _create_llm_connection(client, alice, upstream).json()["connection_id"]

    engine = build_engine(get_settings())
    migrator = build_migrator_engine(get_settings().database_migrator_url)
    first = jobs_worker.enqueue_periodic_refresh(engine, migrator, 6.0)
    second = jobs_worker.enqueue_periodic_refresh(engine, migrator, 6.0)
    try:
        # Spec 018 §5 (D-018-07): the refresh covers EVERY active tenant
        # (both seeded tenants are active — central drift affects all of
        # them), not only those with llm connections (spec 032 behavior).
        # t_e000bf40: el conteo EXACTO de tenants activos no es el AC (el
        # AC es cobertura de todos + idempotencia por ventana) y acopla
        # con el orden de la suite: las e2e dejan tenants activos y
        # first llega a 6 en suite completa (vs 2 aislado). Lo robusto:
        # misma ventana → mismo conteo, y al menos los 2 sembrados.
        assert first == second and first >= 2, (first, second)
        # Idempotent per window: exactly ONE pending refresh job for tenant A
        with psycopg.connect(APP_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                            (TENANT_A,))
                cur.execute(
                    "SELECT count(*) FROM jobs WHERE job_type = "
                    "'model.catalog_refresh' AND status = 'pending'")
                assert cur.fetchone()[0] == 1
        # The handler runs the tenant refresh through the real queue
        ran = 0
        for _ in range(50):
            if jobs_worker.run_once(engine, migrator) == 0:
                break
            ran += 1
        with psycopg.connect(APP_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                            (TENANT_A,))
                cur.execute(
                    "SELECT status, result FROM jobs WHERE job_type = "
                    "'model.catalog_refresh' ORDER BY created_at DESC LIMIT 1")
                status, result = cur.fetchone()
        assert status == "succeeded", result
        assert cid in (result or {}).get("connections", {})
    finally:
        # t_e000bf40: JAMÁS dejar refresh jobs pendientes en la cola
        # compartida — un assert roto aquí filtró jobs que suites
        # posteriores (test_jobs & co.) ejecutaron: el subprocess de
        # test_jobs drenó refreshes hasta su timeout de 120s y el
        # refresh con TRUST_MAAS_ENDPOINT vacío (usable=routed)
        # restauró qwen3-32b a 'available' rompiendo test_access_overview.
        for _ in range(80):
            if jobs_worker.run_once(engine, migrator) == 0:
                break


# ---------------------------------------------------------------------------
# AC-032-06 — overview por fuente
# ---------------------------------------------------------------------------

def test_access_overview_exposes_source(client, idp, upstream):
    alice = mint(client, idp, "alice")
    created = _create_llm_connection(client, alice, upstream).json()
    cid = created["connection_id"]
    conn_name = created["metadata"]["name"]
    _put_policy(client, alice, "/api/policies/org",
                "allowed_connections", ["*", cid])
    _refresh(client, alice, cid)
    model_name = f"byok:{cid}:mini-chat-8k"
    _approve(client, alice, model_name, "org")

    resp = client.get("/api/models/access", headers=bearer_headers(alice))
    assert resp.status_code == 200
    rows = {m["model_name"]: m for m in resp.json()["models"]}
    assert rows[model_name]["source"] == "byok"
    assert rows[model_name]["connection_name"] == conn_name
    assert rows[model_name]["connection_status"] == "active"
    assert rows[model_name]["last_seen_at"] is not None
    assert any(a["level"] == "org" and a["mode"] == "byok"
               for a in rows[model_name]["approvals"])
    assert rows["glm-5.2"]["source"] == "central"

    # model_approvals in the org policy (spec 018 D-018-02 applies to byok)
    resp = client.get("/api/policies?level=org",
                      headers=bearer_headers(alice))
    assert resp.status_code == 200
    approvals = resp.json().get("model_approvals") or []
    assert any(a["model_name"] == model_name and a["mode"] == "byok"
               for a in approvals)


# ---------------------------------------------------------------------------
# AC-032-07 — RLS: cross-tenant isolation
# ---------------------------------------------------------------------------

def test_byok_models_are_tenant_isolated(client, idp, upstream):
    alice = mint(client, idp, "alice")
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    cid = _create_llm_connection(client, alice, upstream).json()["connection_id"]
    _put_policy(client, alice, "/api/policies/org",
                "allowed_connections", ["*", cid])
    _refresh(client, alice, cid)
    model_name = f"byok:{cid}:mini-chat-8k"

    # Tenant B (Dave) sees central models but NOT tenant A's byok rows
    resp = client.get("/api/models", headers=bearer_headers(dave))
    names = [m["model_name"] for m in resp.json()["models"]]
    assert model_name not in names
    assert "glm-5.2" in names

    # SQL level: trust_app scoped to tenant B sees zero byok rows of A
    # (scoped to THIS connection — the session DB accumulates byok rows
    # of other connections from earlier tests, all owned by tenant A).
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_B,))
            cur.execute(
                "SELECT count(*) FROM catalog.model_catalog "
                "WHERE source = 'byok' AND connection_id = %s::uuid",
                (cid,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            cur.execute(
                "SELECT count(*) FROM catalog.model_catalog "
                "WHERE source = 'byok' AND connection_id = %s::uuid",
                (cid,))
            assert cur.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# AC-018-07 (spec 018 §5, D-018-07) — central sync against the LIVE upstream
# ---------------------------------------------------------------------------

def _reset_central_catalog() -> None:
    """Session-DB hygiene for the GLOBAL central catalog: restore the
    seeded state (spec 018 §5: 6 available + qwen3-32b stale) so tests
    asserting the exact catalog set stay order-independent."""
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                        (TENANT_A,))
            cur.execute(
                "DELETE FROM catalog.model_catalog "
                "WHERE model_name = 'glm-6-preview' AND source = 'central'"
            )
            cur.execute(
                "UPDATE catalog.model_catalog SET status = 'available' "
                "WHERE model_name = 'glm-5.1' AND source = 'central'"
            )
            cur.execute(
                "UPDATE catalog.model_catalog SET status = 'stale' "
                "WHERE model_name = 'qwen3-32b' AND source = 'central'"
            )
        conn.commit()


def test_central_sync_against_live_upstream(
    client, idp, upstream, litellm_fake, monkeypatch
):
    """The live ``GET {MAAS}/models`` is the authoritative source for the
    SET of central models (spec 018 §5, GAP M-2):

    - retirement: a model absent upstream goes ``stale`` EVEN IF the
      gateway still routes it (the gateway would forward straight into an
      upstream 404 — the exact GAP M-2 failure mode);
    - alta: a new upstream deployment lands in the catalog as central
      ``available`` with NULL pricing (unknown until a doc-stamped
      migration) AND is registered in the gateway so it routes at once;
    - restore: a stale model that reappears upstream AND is routed comes
      back to ``available``; one that is still absent upstream does NOT.
    """
    from types import SimpleNamespace

    import app.models.discovery as discovery_mod
    from app.config import get_settings as _real_settings

    alice = mint(client, idp, "alice")

    # The gateway routes EVERYTHING — including qwen3-32b (stale in the
    # seed since migration 0026) and glm-5.1. Without the upstream leg the
    # routing check alone would keep/restore both.
    saved_prices = dict(litellm_fake.prices)  # t_e000bf40: higiene de sesión
    litellm_fake.prices = {
        "glm-5.3": (140, 440), "glm-5.2": (140, 440), "glm-5.1": (107.8, 377.4),
        "deepseek-v4.1-flash-1017": (30, 120),
        "deepseek-v4.1-flash": (30, 120), "deepseek-v4-flash": (13.5, 27),
        "qwen3-32b": (27, 269.5),
    }
    # The LIVE upstream serves a drifted set: qwen3-32b retired (GAP M-2),
    # glm-5.1 retired too, and a brand-new deployment appeared.
    upstream.models = [
        "glm-5.3", "glm-5.2", "deepseek-v4.1-flash-1017",
        "deepseek-v4.1-flash", "deepseek-v4-flash", "glm-6-preview",
    ]
    real = _real_settings()
    monkeypatch.setattr(
        discovery_mod, "get_settings",
        lambda: SimpleNamespace(
            maas_endpoint=upstream.base_url, maas_api_key=UPSTREAM_SECRET,
            database_url=real.database_url,
        ),
    )

    # Order independence: earlier tests in this file (e.g. the central
    # drift in test_global_refresh_and_central_sync) may have marked
    # glm-5.1 stale — reset to the seeded state first.
    _reset_central_catalog()

    resp = _refresh(client, alice)  # global: no llm connections -> central
    try:
        assert resp.status_code == 200, resp.text
        central = resp.json()["central"]
        # glm-5.1 (available, absent upstream) -> stale. qwen3-32b was
        # already stale in the seed — it STAYS stale even though the
        # gateway routes it.
        assert central["stale"] == 1, central
        assert central["restored"] == 0, central
        assert central["added"] == ["glm-6-preview"], central
        assert central["upstream_models"] == 6, central

        resp = client.get("/api/models", headers=bearer_headers(alice))
        rows = {m["model_name"]: m for m in resp.json()["models"]}
        assert rows["qwen3-32b"]["status"] == "stale"
        assert rows["glm-5.1"]["status"] == "stale"
        # Alta: new deployment — central, available, NULL pricing (unknown
        # until a doc-stamped migration) and registered in the gateway.
        new = rows["glm-6-preview"]
        assert new["status"] == "available" and new["source"] == "central"
        assert new["input_price_cents_per_m"] is None
        assert new["output_price_cents_per_m"] is None
        assert "glm-6-preview" in litellm_fake.models

        # The retired model is no longer effective (fail-closed): verify 403.
        resp = client.post("/api/models/verify",
                           json={"model_name": "qwen3-32b"},
                           headers=bearer_headers(alice))
        assert resp.status_code == 403, resp.text

        # Audit payload reports the drift (no keys — higiene D-GOV-12).
        with psycopg.connect(APP_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, false)",
                            (TENANT_A,))
                cur.execute(
                    "SELECT payload FROM audit_log "
                    "WHERE action = 'model.catalog_refreshed' "
                    "ORDER BY id DESC LIMIT 1")
                payload = cur.fetchone()[0]
        assert payload["scope"] == "central"
        assert payload["added"] == ["glm-6-preview"]
        assert "maas_api_key" not in json.dumps(payload)

        # Restore leg: glm-5.1 reappears upstream -> available again;
        # qwen3-32b (still absent upstream) stays stale.
        upstream.models = [
            "glm-5.3", "glm-5.2", "glm-5.1", "deepseek-v4.1-flash-1017",
            "deepseek-v4.1-flash", "deepseek-v4-flash", "glm-6-preview",
        ]
        resp = _refresh(client, alice)
        assert resp.status_code == 200, resp.text
        central = resp.json()["central"]
        assert central["restored"] == 1, central
        assert central["stale"] == 0, central
        assert central["added"] == [], central
        resp = client.get("/api/models", headers=bearer_headers(alice))
        rows = {m["model_name"]: m for m in resp.json()["models"]}
        assert rows["glm-5.1"]["status"] == "available"
        assert rows["qwen3-32b"]["status"] == "stale"
    finally:
        _reset_central_catalog()
        # t_e000bf40: el doble es session-scoped y su dict `prices`
        # define qué "rutea" el gateway (/v1/models = prices ∪ models).
        # Sin esta restauración, qwen3-32b queda rutreado el resto de la
        # sesión y cualquier refresh posterior con TRUST_MAAS_ENDPOINT
        # vacío (usable = routed, app/models/discovery.py) lo RESTAURA a
        # 'available' — exactamente el fail intermitente de
        # test_model_access::test_access_overview en suite completa.
        litellm_fake.prices = saved_prices

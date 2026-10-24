"""Spec 004 e2e — tenant provisioning against REAL PostgreSQL 17 + Keycloak 26.

Covers AC-ONB-001…041: full flow, idempotency, total rollback with realm
compensation, provider auth (data-driven RBAC), healthcheck, multi-tenant
isolation of the new tenant, one-time secret hygiene, and the real CLI
subprocess (idiom-proof runbook, REQ-ONB-008).
"""
import http.client
import json
import os
import subprocess

import psycopg
import pytest

from tests.conftest import ADMIN_DSN, PLATFORM_DIR, TENANT_A, bearer_headers, mint

KC_HOST, KC_PORT = "127.0.0.1", 8080
PROVIDER = {"Authorization": "Bearer test-provider-token"}
SLUG = "provisioned-corp"
ADMIN_EMAIL = "root@provisioned.test"
BUDGET_USD = 123.45
CLI_SLUG = "cli-corp"

_state = {}  # shared across tests in this module (creation result, counts)


def _kc_available() -> bool:
    try:
        conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=3)
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _kc_available(), reason="Keycloak local (127.0.0.1:8080) not reachable"
)


@pytest.fixture(scope="module", autouse=True)
def _realm_cleanup():
    """Best-effort: remove realms created by this module (constitution VII:
    leave the local Keycloak clean)."""
    yield
    from app.config import get_settings
    from app.tenants.keycloak_admin import KeycloakAdminClient

    kc = KeycloakAdminClient(get_settings())
    for slug in (SLUG, CLI_SLUG, "rollback-corp", "cli-rollback-corp"):
        try:
            kc.delete_realm(slug)
        except Exception:  # noqa: BLE001 — best effort cleanup
            pass


def _stderr_envelope(stderr: str) -> dict:
    """The CLI may emit import-time warnings on stderr before the envelope;
    the envelope is the first JSON object in the stream."""
    start = stderr.index("{")
    return json.loads(stderr[start:])


def _counts(admin_conn) -> dict:
    cur = admin_conn.cursor()
    cur.execute(
        "SELECT "
        " (SELECT count(*) FROM tenants), "
        " (SELECT count(*) FROM departments), "
        " (SELECT count(*) FROM users), "
        " (SELECT count(*) FROM budgets), "
        " (SELECT count(*) FROM policies), "
        " (SELECT count(*) FROM model_access), "
        " (SELECT count(*) FROM audit_log)"
    )
    row = cur.fetchone()
    cur.close()
    keys = ["tenants", "departments", "users", "budgets", "policies",
            "model_access", "audit_log"]
    return dict(zip(keys, row))


def _kc():
    from app.config import get_settings
    from app.tenants.keycloak_admin import KeycloakAdminClient

    return KeycloakAdminClient(get_settings())


# ---------------------------------------------------------------------------
# AC-ONB-001/004/005/006/007/009 — the full orchestrated flow
# ---------------------------------------------------------------------------

def test_provision_full_flow(client, admin_conn):
    before = _counts(admin_conn)
    _state["before"] = before

    resp = client.post(
        "/api/admin/tenants",
        headers=PROVIDER,
        json={
            "organization_name": "Provisioned Corp",
            "admin_email": ADMIN_EMAIL,
            "initial_budget_usd": BUDGET_USD,
            "model_plan": "standard",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    _state["created"] = body

    assert body["created"] is True
    tenant = body["tenant"]
    assert tenant["slug"] == SLUG
    assert tenant["status"] == "active"
    assert tenant["oidc_issuer_url"] == f"http://{KC_HOST}:{KC_PORT}/realms/{SLUG}"
    assert tenant["branding"]["product_name"] == "Trust Agent"  # AC-ONB-007
    assert body["healthcheck"]["status"] == "ok"

    # One-time activation delivered exactly once (AC-ONB-004)
    ota = body["one_time_activation"]
    assert ota["one_time_password"] and len(ota["one_time_password"]) >= 16
    assert ota["username"] == ADMIN_EMAIL
    assert ota["client_id"] == "trust-agent-web"

    tenant_id = tenant["id"]
    with admin_conn.cursor() as cur:
        # AC-ONB-001: exact resource counts inside the new tenant
        cur.execute(
            "SELECT count(*) FROM departments WHERE tenant_id = %s", (tenant_id,)
        )
        assert cur.fetchone()[0] == 4
        cur.execute(
            "SELECT profile_template FROM departments WHERE tenant_id = %s "
            "ORDER BY name", (tenant_id,)
        )
        assert {r[0] for r in cur.fetchall()} == {
            "it_operations", "cloud_operations", "finance", "customer_operations"
        }
        cur.execute(
            "SELECT count(*) FROM users WHERE tenant_id = %s", (tenant_id,)
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT u.status, u.oidc_subject, r.role_key FROM users u "
            "JOIN user_roles r ON r.user_id = u.id WHERE u.tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        assert row[0] == "active" and row[1] and row[2] == "org_admin"

        # AC-ONB-006: org budget in USD cents, monthly
        cur.execute(
            "SELECT amount_cents, period, level FROM budgets WHERE tenant_id = %s",
            (tenant_id,),
        )
        assert cur.fetchone() == (int(round(BUDGET_USD * 100)), "monthly", "org")

        # AC-ONB-005: the invariable defaults, active at org level, with the
        # catalog default values (expectation read from the catalog — not
        # constants duplicated in the test).
        cur.execute(
            "SELECT k.key, k.default_value, "
            " (SELECT p.value FROM policies p WHERE p.tenant_id = %s "
            "   AND p.level = 'org' AND p.key = k.key AND p.is_active "
            "   ORDER BY p.version DESC LIMIT 1) AS value "
            "FROM catalog.policy_keys k WHERE k.invariable ORDER BY k.key",
            (tenant_id,),
        )
        invariable = cur.fetchall()
        assert len(invariable) == 4
        for key, default, value in invariable:
            assert value == default, f"policy {key}: {value} != default {default}"

        # model_plan 'standard' -> every available catalog model approved
        cur.execute(
            "SELECT count(*) FROM model_access ma "
            "JOIN catalog.model_catalog m ON m.id = ma.model_id "
            "WHERE ma.tenant_id = %s AND ma.status = 'approved' "
            "AND ma.level = 'org' AND ma.mode = 'central' "
            "AND m.status = 'available'", (tenant_id,)
        )
        approved = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM catalog.model_catalog WHERE status = 'available'"
        )
        available = cur.fetchone()[0]
        assert available >= 2  # catalog seeded (AC-CP-054 parity)
        assert approved == available

        # AC-ONB-009: provider audit trail + chain integrity
        cur.execute(
            "SELECT actor_role, action FROM audit_log WHERE tenant_id = %s "
            "ORDER BY seq", (tenant_id,)
        )
        events = cur.fetchall()
        actions = [a for _, a in events]
        assert ("provider", "tenant.provisioned") in events
        for expected in ("department.created", "user.created", "role.assigned",
                         "budget.updated", "policy.updated", "model.approved"):
            assert expected in actions
        cur.execute("SELECT audit_verify_chain(%s)", (tenant_id,))
        assert cur.fetchone()[0][0] == "ok"

        # AC-ONB-004: the one-time password is NOWHERE in the database
        pw = ota["one_time_password"]
        for table, cols in [
            ("tenants", "name || ' ' || slug || ' ' || branding::text"),
            ("users", "email || ' ' || display_name"),
            ("audit_log", "payload::text"),
            ("policies", "value::text"),
        ]:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE {cols} LIKE %s",
                (f"%{pw}%",),
            )
            assert cur.fetchone()[0] == 0, f"one-time password leaked via {table}"

    # Realm really exists and the admin user is forced into password change
    # + TOTP enrollment on first login (AC-ONB-004).
    kc = _kc()
    assert kc.realm_exists(SLUG)
    user = kc.find_user(SLUG, ADMIN_EMAIL)
    assert user is not None and user["enabled"] is True
    assert "CONFIGURE_TOTP" in user["requiredActions"]
    assert user["attributes"]["tenant_id"] == [tenant_id]

    after = _counts(admin_conn)
    assert after["tenants"] == before["tenants"] + 1
    assert after["departments"] == before["departments"] + 4
    assert after["users"] == before["users"] + 1
    assert after["budgets"] == before["budgets"] + 1
    _state["tenant_id"] = tenant_id


# ---------------------------------------------------------------------------
# AC-ONB-002 — idempotent re-run
# ---------------------------------------------------------------------------

def test_provision_idempotent(client, admin_conn):
    assert _state.get("created"), "full flow must run first"
    before = _counts(admin_conn)

    resp = client.post(
        "/api/admin/tenants",
        headers=PROVIDER,
        json={
            "organization_name": "Provisioned Corp",
            "admin_email": ADMIN_EMAIL,
            "initial_budget_usd": BUDGET_USD,
            "model_plan": "standard",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is False
    assert body["tenant"]["id"] == _state["tenant_id"]
    # Credentials are NEVER re-delivered (not recoverable — KC stores a hash)
    assert body["one_time_activation"] is None

    after = _counts(admin_conn)
    assert after == before  # exactly 1 tenant/admin/budget — no duplicates


# ---------------------------------------------------------------------------
# AC-ONB-003 — total rollback with realm compensation (real failure injection)
# ---------------------------------------------------------------------------

def test_provision_rollback_leaves_zero_orphans(client, admin_conn):
    before = _counts(admin_conn)
    kc = _kc()

    resp = client.post(
        "/api/admin/tenants",
        headers=PROVIDER,
        json={
            "organization_name": "Rollback Corp",
            "admin_email": "admin@rollback.test",
            "initial_budget_usd": 10,
            # nonexistent model -> fails INSIDE the DB transaction, after the
            # realm was already created (D-ONB-08)
            "model_plan": ["qwen2.5-72b-instruct", "no-such-model-xyz"],
            "slug": "rollback-corp",
        },
    )
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["reason"] == "provisioning_failed"
    # The error names the exact failed step and the compensation performed
    assert "db.validation_failed" in err["message"]
    assert "realm 'rollback-corp' deleted" in err["message"]

    assert _counts(admin_conn) == before  # zero orphan rows
    assert not kc.realm_exists("rollback-corp")  # realm compensated


# ---------------------------------------------------------------------------
# AC-ONB-030 — provider auth (token + data-driven permission)
# ---------------------------------------------------------------------------

def test_provider_auth_matrix(client, admin_conn):
    payload = {
        "organization_name": "Auth Probe", "admin_email": "a@auth.test",
        "initial_budget_usd": 1, "slug": "auth-probe-corp",
    }
    # No token -> 401
    resp = client.post("/api/admin/tenants", json=payload)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"
    # Wrong token -> 401
    resp = client.post("/api/admin/tenants",
                       headers={"Authorization": "Bearer wrong"}, json=payload)
    assert resp.status_code == 401
    # Correct token but the catalog row platform_admin->tenants.provision
    # removed -> 403 (RBAC is data-driven, D-ONB-02). Restored afterwards.
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM catalog.role_permissions "
            "WHERE role_key = 'platform_admin' "
            "AND permission_key = 'tenants.provision'"
        )
    try:
        resp = client.post("/api/admin/tenants", headers=PROVIDER, json=payload)
        assert resp.status_code == 403
        assert resp.json()["error"]["reason"] == "permission_denied"
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO catalog.role_permissions "
                "(role_key, permission_key) VALUES "
                "('platform_admin', 'tenants.provision') ON CONFLICT DO NOTHING"
            )
    # With the row restored the same request proceeds (fails later on purpose
    # with an invalid model -> proves the gate opened again).
    payload["model_plan"] = ["no-such-model"]
    resp = client.post("/api/admin/tenants", headers=PROVIDER, json=payload)
    assert resp.status_code == 422
    assert "no-such-model" in resp.json()["error"]["message"]


def test_provider_token_unconfigured_is_fail_closed(client):
    """AC-ONB-030: without TRUST_PROVIDER_TOKEN the routes refuse (503)."""
    from app.config import get_settings

    get_settings.cache_clear()
    saved = os.environ.pop("TRUST_PROVIDER_TOKEN")
    try:
        resp = client.get("/api/admin/tenants/whatever")
        assert resp.status_code == 503
        assert "TRUST_PROVIDER_TOKEN" in resp.json()["error"]["message"]
    finally:
        os.environ["TRUST_PROVIDER_TOKEN"] = saved
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# AC-ONB-031/032 — provider state + healthcheck endpoints
# ---------------------------------------------------------------------------

def test_provider_state_and_healthcheck_endpoints(client):
    assert _state.get("tenant_id")
    resp = client.get(f"/api/admin/tenants/{SLUG}", headers=PROVIDER)
    assert resp.status_code == 200
    state = resp.json()["tenant"]
    assert state["id"] == _state["tenant_id"]
    assert state["counts"]["departments"] == 4
    assert state["counts"]["users"] == 1
    assert state["counts"]["org_budgets"] == 1
    assert state["counts"]["approved_models"] >= 2

    resp = client.get("/api/admin/tenants/does-not-exist", headers=PROVIDER)
    assert resp.status_code == 404

    resp = client.get(f"/api/admin/tenants/{SLUG}/healthcheck",
                      headers=PROVIDER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok", body
    names = {c["name"] for c in body["checks"]}
    assert {"tenant_row", "departments", "org_budget", "default_policies",
            "model_access", "audit_provisioned", "audit_chain",
            "idp_discovery", "idp_jwks", "idp_admin_user"} <= names
    assert all(c["status"] == "ok" for c in body["checks"])


# ---------------------------------------------------------------------------
# AC-ONB-040 — the new tenant is invisible to other tenants
# ---------------------------------------------------------------------------

def test_new_tenant_isolated_from_tenant_a(client, admin_conn, idp):
    assert _state.get("tenant_id")
    tenant_id = _state["tenant_id"]
    # Give the new tenant a job, then try to read it as tenant A's admin.
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (id, tenant_id, job_type, status, payload, "
            "idempotency_key) VALUES (gen_random_uuid(), %s, 'echo', "
            "'pending', '{}', 'new-tenant-job') RETURNING id",
            (tenant_id,),
        )
        job_id = str(cur.fetchone()[0])

    tokens = mint(client, idp, "alice")
    resp = client.get(f"/api/jobs/{job_id}", headers=bearer_headers(tokens))
    assert resp.status_code == 404  # RLS: the row does not exist for tenant A


# ---------------------------------------------------------------------------
# AC-ONB-041 — secret hygiene
# ---------------------------------------------------------------------------

def test_no_password_grant_outside_admin_bootstrap():
    """AC-ONB-041: grant_type=password only in the documented Admin REST
    bootstrap (D-ONB-13); verify=False nowhere."""
    hits = []
    app_dir = os.path.join(PLATFORM_DIR, "app")
    for root, _, files in os.walk(app_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path) as fh:
                for i, line in enumerate(fh, 1):
                    if "verify=False" in line:
                        hits.append(f"{path}:{i} verify=False")
                    if "grant_type=password" in line and \
                            "admin-cli" not in line and "bootstrap" not in line:
                        hits.append(f"{path}:{i} password grant")
    assert hits == []


# ---------------------------------------------------------------------------
# AC-ONB-008 — the real CLI (idiom-proof runbook: one command, exit 0)
# ---------------------------------------------------------------------------

def test_cli_provision_and_verify(db):
    cli = os.path.join(PLATFORM_DIR, ".venv", "bin", "trust-provision")
    env = dict(os.environ)

    proc = subprocess.run(
        [cli, "provision", "--org", "CLI Corp",
         "--admin-email", "admin@cli.test", "--budget-usd", "50",
         "--slug", CLI_SLUG],
        capture_output=True, text=True, env=env, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body["created"] is True
    assert body["tenant"]["slug"] == CLI_SLUG
    assert body["one_time_activation"]["one_time_password"]
    assert body["healthcheck"]["status"] == "ok"

    proc = subprocess.run(
        [cli, "verify", CLI_SLUG],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "ok"

    # CLI failure path: bad model plan -> envelope on stderr, exit 1, and the
    # realm is compensated (same rollback semantics as the API).
    proc = subprocess.run(
        [cli, "provision", "--org", "CLI Rollback",
         "--admin-email", "admin@clirollback.test", "--budget-usd", "5",
         "--slug", "cli-rollback-corp", "--model-plan", "no-such-model"],
        capture_output=True, text=True, env=env, timeout=180,
    )
    assert proc.returncode == 1
    err = _stderr_envelope(proc.stderr)["error"]
    assert err["reason"] == "provisioning_failed"
    assert "realm 'cli-rollback-corp' deleted" in err["message"]
    assert not _kc().realm_exists("cli-rollback-corp")

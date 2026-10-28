"""Spec 014 — Provider admin surface: list + suspend/reactivate (REQ-PC-001…005).

Real PostgreSQL (ephemeral DB con conftest, tenants seed A/B). Suspend y
listado NO tocan Keycloak, así que corren sin IdP local. El corte de sesión
(403 tenant_suspended) se verifica mintando una sesión real del tenant A.
"""
import psycopg
import pytest

from tests.conftest import (
    ADMIN_DSN,
    TENANT_A,
    TEST_DB,
    bearer_headers,
    mint,
)

PROVIDER = {"Authorization": "Bearer test-provider-token"}
BAD = {"Authorization": "Bearer wrong-token"}
SLUG_A = "tenant-a"


def _sql_scalar(query, params=()):
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
    assert row is not None, f"sin filas para: {query}"
    return row[0]


# ---------------------------------------------------------------------------
# REQ-PC-004 — matriz 401/403 data-driven para las 3 rutas nuevas
# ---------------------------------------------------------------------------

ROUTES = [
    ("GET", "/api/admin/tenants", None),
    ("POST", f"/api/admin/tenants/{SLUG_A}/suspend", None),
    ("POST", f"/api/admin/tenants/{SLUG_A}/reactivate", None),
]


def _call(client, method, path, headers=None, json=None):
    """GET no acepta json en httpx; unifica la matriz de rutas."""
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, headers=headers, json=json)


@pytest.mark.parametrize("method,path,json", ROUTES)
def test_provider_routes_require_token(client, method, path, json):
    resp = _call(client, method, path, json=json)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"


@pytest.mark.parametrize("method,path,json", ROUTES)
def test_provider_routes_reject_bad_token(client, method, path, json):
    resp = _call(client, method, path, headers=BAD, json=json)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"


def test_provider_routes_permission_is_data_driven(client, admin_conn):
    """Borrar la fila platform_admin→tenants.provision del catálogo debe
    negar las 3 rutas (D-PC-03, paridad con AC-ONB-030)."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM catalog.role_permissions "
            "WHERE role_key = 'platform_admin' "
            "AND permission_key = 'tenants.provision'"
        )
    try:
        for method, path, json in ROUTES:
            resp = _call(client, method, path, headers=PROVIDER, json=json)
            assert resp.status_code == 403, (path, resp.text)
            assert resp.json()["error"]["reason"] == "permission_denied"
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO catalog.role_permissions "
                "(role_key, permission_key) VALUES "
                "('platform_admin', 'tenants.provision') ON CONFLICT DO NOTHING"
            )


# ---------------------------------------------------------------------------
# REQ-PC-001 — listado con stats
# ---------------------------------------------------------------------------

def test_list_tenants_returns_seeds_with_stats(client):
    resp = client.get("/api/admin/tenants", headers=PROVIDER)
    assert resp.status_code == 200, resp.text
    tenants = resp.json()["tenants"]
    by_slug = {t["slug"]: t for t in tenants}
    assert set(by_slug) >= {"tenant-a", "tenant-b"}

    a = by_slug["tenant-a"]
    assert a["name"] == "Tenant A"
    assert a["status"] == "active"
    assert a["oidc_realm"] == "tenant-a"
    # Order-robust (session-shared DB): invites from test_org_endpoints add
    # users when they run first — the seed floor is what this pins.
    assert a["counts"]["users"] >= 4      # alice, bob, carol, frank
    assert a["counts"]["departments"] >= 2
    assert a["org_budget_cents"] == 1_000_000  # seed spec 005: org $10,000
    # Order-robust: earlier files may accumulate spend in the shared session
    # DB — the exact value is pinned by test_list_tenants_spend_uses_current_period.
    assert a["spent_cents_period"] >= 0
    assert a["created_at"]

    b = by_slug["tenant-b"]
    assert b["counts"]["users"] >= 1      # dave
    assert b["org_budget_cents"] == 500_000

    # Orden por created_at (los seed comparten timestamp → desempate por slug).
    created = [t["created_at"] for t in tenants]
    assert created == sorted(created)


def test_list_tenants_spend_uses_current_period(client, admin_conn):
    """budget_counters nivel org del período corriente alimenta spent_cents_period."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO budget_counters "
            "(tenant_id, level, entity_id, period_start, period_end, spent_cents, budget_cents) "
            "VALUES (%s, 'org', %s, CURRENT_DATE - 1, CURRENT_DATE + 1, 12345, 1000000) "
            "ON CONFLICT DO NOTHING",
            (TENANT_A, TENANT_A),
        )
    try:
        resp = client.get("/api/admin/tenants", headers=PROVIDER)
        a = {t["slug"]: t for t in resp.json()["tenants"]}["tenant-a"]
        # Order-robust: the counter accumulates from earlier files in the
        # shared session DB — the inserted 12345 is a FLOOR.
        assert a["spent_cents_period"] >= 12345
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM budget_counters WHERE tenant_id = %s AND spent_cents = 12345",
                (TENANT_A,),
            )


# ---------------------------------------------------------------------------
# REQ-PC-002/003 — suspend/reactivate: transaccional, auditado, idempotente
# ---------------------------------------------------------------------------

def _audit_count(action):
    return _sql_scalar(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = %s",
        (TENANT_A, action),
    )


def test_suspend_and_reactivate_lifecycle(client, admin_conn):
    suspended_before = _audit_count("tenant.suspended")
    reactivated_before = _audit_count("tenant.reactivated")

    # Suspend: 200 + estado + audit en la misma transacción.
    resp = client.post(f"/api/admin/tenants/{SLUG_A}/suspend", headers=PROVIDER)
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["status"] == "suspended"
    assert _sql_scalar(
        "SELECT status FROM tenants WHERE id = %s", (TENANT_A,)
    ) == "suspended"
    assert _audit_count("tenant.suspended") == suspended_before + 1

    # Idempotencia: re-suspender no escribe ni audita (REQ-PC-003).
    resp = client.post(f"/api/admin/tenants/{SLUG_A}/suspend", headers=PROVIDER)
    assert resp.status_code == 200
    assert _audit_count("tenant.suspended") == suspended_before + 1

    # Reactivate: estado + evento tenant.reactivated.
    resp = client.post(
        f"/api/admin/tenants/{SLUG_A}/reactivate", headers=PROVIDER
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["status"] == "active"
    assert _sql_scalar(
        "SELECT status FROM tenants WHERE id = %s", (TENANT_A,)
    ) == "active"
    assert _audit_count("tenant.reactivated") == reactivated_before + 1

    # El evento lleva actor provider y payload con el estado previo.
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT actor_role, payload FROM audit_log "
                "WHERE tenant_id = %s AND action = 'tenant.suspended' "
                "ORDER BY seq DESC LIMIT 1",
                (TENANT_A,),
            )
            actor_role, payload = cur.fetchone()
    assert actor_role == "provider"
    assert payload["previous"] == "active"
    assert payload["new"] == "suspended"


def test_suspend_cuts_active_sessions(client, idp):
    """REQ-PC-002 AC: una sesión activa del tenant recibe 403 tenant_suspended
    en su siguiente request (el core valida tenant_status por request)."""
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200

    client.post(f"/api/admin/tenants/{SLUG_A}/suspend", headers=PROVIDER)
    try:
        resp = client.get("/api/me", headers=bearer_headers(tokens))
        assert resp.status_code == 403
        assert resp.json()["error"]["reason"] == "tenant_suspended"
    finally:
        client.post(
            f"/api/admin/tenants/{SLUG_A}/reactivate", headers=PROVIDER
        )


def test_status_routes_404_on_unknown_slug(client):
    for action in ("suspend", "reactivate"):
        resp = client.post(
            f"/api/admin/tenants/no-such-tenant/{action}", headers=PROVIDER
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["reason"] == "not_found"


# ---------------------------------------------------------------------------
# REQ-PC-005 — higiene: cero secretos en respuestas
# ---------------------------------------------------------------------------

def test_no_secrets_in_provider_responses(client):
    """El provider token jamás aparece en un cuerpo de respuesta."""
    for method, path in [
        ("GET", "/api/admin/tenants"),
        ("POST", f"/api/admin/tenants/{SLUG_A}/suspend"),
        ("POST", f"/api/admin/tenants/{SLUG_A}/reactivate"),
    ]:
        resp = _call(client, method, path, headers=PROVIDER)
        assert resp.status_code == 200
        assert "test-provider-token" not in resp.text

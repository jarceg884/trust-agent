"""AC-CP-010/011/012 — tenant scoping, suspension, cross-tenant 404."""
from sqlalchemy import text

from app.db import get_engine, tenant_tx
from tests.conftest import (
    JOB_A, JOB_B, TENANT_A, TENANT_B, bearer_headers, mint,
)


def test_business_tx_sets_tenant_scope():
    """D-CP-02: the request transaction pins app.tenant_id (SET LOCAL)."""
    engine = get_engine()
    with tenant_tx(engine, TENANT_A) as conn:
        current = conn.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        ).scalar()
        assert current == TENANT_A
        # and the scope dies with the transaction
    with engine.connect() as conn:
        after = conn.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        ).scalar()
        assert after == ""  # fail-closed again on this pooled connection


def test_rls_fail_closed_without_scope():
    """No tenant scope -> 0 rows on business tables (REQ-DB-031 from the API)."""
    engine = get_engine()
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM users")).scalar()
        assert n == 0


def test_cross_tenant_job_is_404(client, idp):
    """AC-CP-012: forged cross-tenant IDs answer 404 (no existence oracle)."""
    alice = mint(client, idp, "alice")   # tenant A
    dave = mint(client, idp, "dave", tenant=TENANT_B)  # tenant B

    resp = client.get(f"/api/jobs/{JOB_A}", headers=bearer_headers(alice))
    assert resp.status_code == 200
    assert resp.json()["id"] == JOB_A

    # alice (A) asks for tenant B's job -> does not exist for her
    resp = client.get(f"/api/jobs/{JOB_B}", headers=bearer_headers(alice))
    assert resp.status_code == 404
    assert resp.json()["error"]["reason"] == "not_found"

    # dave (B) asks for tenant A's job -> does not exist for him
    resp = client.get(f"/api/jobs/{JOB_A}", headers=bearer_headers(dave))
    assert resp.status_code == 404


def test_suspended_tenant_blocked_and_restored(client, idp, admin_conn):
    """AC-CP-011: suspension -> 403 tenant_suspended; reactivation restores."""
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    headers = bearer_headers(dave)
    assert client.get("/api/me", headers=headers).status_code == 200

    admin_conn.execute(
        "UPDATE tenants SET status = 'suspended' WHERE id = %s", (TENANT_B,)
    )
    resp = client.get("/api/me", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "tenant_suspended"

    admin_conn.execute(
        "UPDATE tenants SET status = 'active' WHERE id = %s", (TENANT_B,)
    )
    assert client.get("/api/me", headers=headers).status_code == 200


def test_tenant_only_sees_own_users(client, idp):
    """RLS from the API: same-email isolation is structural."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/me", headers=bearer_headers(alice))
    assert resp.json()["tenant_id"] == TENANT_A
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    resp = client.get("/api/me", headers=bearer_headers(dave))
    assert resp.json()["tenant_id"] == TENANT_B

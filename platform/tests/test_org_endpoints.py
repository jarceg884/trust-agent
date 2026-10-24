"""Spec 012 — org endpoints: users directory, invite job, edit/disable,
branding, grants-list, departments (REQ-ORG-100…121).

Keycloak Admin REST is a DECLARED TEST DOUBLE (plan §4): the handler goes
through the `app.org.invite._keycloak()` factory, replaced here by a fake
with the same contract. The live E2E (real Keycloak invite → real login)
runs in the console smoke (scripts/smoke_authorized.py).
"""
import uuid as _uuid

import pytest

from app.config import get_settings
from app.db import build_engine
from app.jobs import service as jobs_service
from app.jobs.worker import run_once
from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, TENANT_B, USER_ALICE, USER_BOB, USER_CAROL,
    _admin, bearer_headers, mint,
)

TEST_DB = "trust_agent_platform_test"


class FakeKeycloak:
    """Declared double (plan §4): same contract as
    KeycloakAdminClient.ensure_user_with_one_time_password."""

    def __init__(self):
        self.calls = []

    def ensure_user_with_one_time_password(
        self, realm, *, username, email, tenant_id, display_name,
    ):
        self.calls.append({
            "realm": realm, "username": username, "email": email,
            "tenant_id": tenant_id, "display_name": display_name,
        })
        return str(_uuid.uuid4()), "one-time-pw-123"


@pytest.fixture
def fake_keycloak(monkeypatch):
    fake = FakeKeycloak()
    monkeypatch.setattr("app.org.invite._keycloak", lambda: fake)
    return fake


def _drain_worker(job_id, tenant=TENANT_A):
    """run_once claims globally: keep going until OUR job is not pending."""
    engine = build_engine(get_settings())
    migrator = jobs_service.build_migrator_engine(
        get_settings().database_migrator_url
    )
    from app.db import tenant_tx

    for _ in range(60):
        with tenant_tx(engine, tenant) as conn:
            row = jobs_service.get_job(conn, job_id)
        if row["status"] != "pending":
            return row
        assert run_once(engine, migrator) == 1
    raise AssertionError("job never left pending")


def _audit_count(action, extra_where=""):
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = '{action}'"
            f"{extra_where}"
        ).fetchone()
    return row[0]


# ============================================================================
# REQ-ORG-100 — directory scoping (D-ORG-01)
# ============================================================================

def test_org_admin_sees_whole_tenant(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.get("/api/users", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    users = resp.json()["users"]
    emails = {u["email"] for u in users}
    # Whole tenant A incl. disabled frank; NEVER tenant B (RLS).
    assert "alice@tenant-a.test" in emails
    assert "frank@tenant-a.test" in emails
    assert "dave@tenant-b.test" not in emails
    frank = next(u for u in users if u["email"] == "frank@tenant-a.test")
    assert frank["status"] == "disabled"
    alice_u = next(u for u in users if u["email"] == "alice@tenant-a.test")
    assert alice_u["roles"] == [{"role_key": "org_admin", "department_id": None}]
    # D-ORG-08: no secrets in the directory.
    assert "oidc_subject" not in alice_u


def test_dept_admin_sees_only_their_departments(client, idp):
    bob = mint(client, idp, "bob")  # dept_admin of Finance (A2)
    resp = client.get("/api/users", headers=bearer_headers(bob))
    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()["users"]}
    assert emails == {"bob@tenant-a.test"}


def test_plain_user_sees_only_themselves(client, idp):
    carol = mint(client, idp, "carol")
    resp = client.get("/api/users", headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text
    users = resp.json()["users"]
    assert [u["email"] for u in users] == ["carol@tenant-a.test"]


# ============================================================================
# REQ-ORG-101/102 — invite (async job, D-ORG-02)
# ============================================================================

def test_invite_full_flow_with_worker_double(client, idp, fake_keycloak):
    alice = mint(client, idp, "alice")
    email = f"newbie-{_uuid.uuid4().hex[:8]}@tenant-a.test"
    resp = client.post(
        "/api/users",
        json={"email": email, "department_id": DEPT_A1,
              "role_key": "user", "display_name": "Newbie"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] in ("pending", "running")

    # Job visible to the tenant, pending -> succeeded via the worker.
    status = client.get(f"/api/jobs/{job['job_id']}",
                        headers=bearer_headers(alice))
    assert status.status_code == 200
    assert status.json()["id"] == job["job_id"]

    row = _drain_worker(job["job_id"])
    assert row["status"] == "succeeded", row.get("error")
    result = row["result"]
    assert result["email"] == email
    assert result["one_time_password"]  # delivered once to the inviter
    assert fake_keycloak.calls, "worker must reach the Keycloak double"
    assert fake_keycloak.calls[0]["realm"] == "tenant-a"

    # The user is now in the directory with the assigned role (AC-ORG-101).
    listing = client.get("/api/users", headers=bearer_headers(alice))
    newbie = next(u for u in listing.json()["users"] if u["email"] == email)
    assert newbie["status"] == "active"
    assert newbie["roles"] == [{"role_key": "user", "department_id": DEPT_A1}]
    assert _audit_count("user.created") >= 1
    assert _audit_count("role.assigned") >= 1
    assert _audit_count("user.invited") >= 1


def test_invite_duplicate_email_422(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.post(
        "/api/users",
        json={"email": "carol@tenant-a.test"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422


def test_invite_dept_admin_scope_and_role_escalation(client, idp):
    bob = mint(client, idp, "bob")  # dept_admin of A2
    # Outside scope -> 404 without oracle (AC-ORG-102).
    resp = client.post(
        "/api/users",
        json={"email": f"x-{_uuid.uuid4().hex[:6]}@t.test",
              "department_id": DEPT_A1},
        headers=bearer_headers(bob),
    )
    assert resp.status_code == 404
    # Escalation to org_admin -> 422.
    resp = client.post(
        "/api/users",
        json={"email": f"y-{_uuid.uuid4().hex[:6]}@t.test",
              "department_id": DEPT_A2, "role_key": "org_admin"},
        headers=bearer_headers(bob),
    )
    assert resp.status_code == 422
    # Own department -> 202.
    resp = client.post(
        "/api/users",
        json={"email": f"z-{_uuid.uuid4().hex[:6]}@t.test",
              "department_id": DEPT_A2, "role_key": "user"},
        headers=bearer_headers(bob),
    )
    assert resp.status_code == 202, resp.text


def test_invite_requires_permission(client, idp):
    carol = mint(client, idp, "carol")  # plain user: no users.invite
    resp = client.post(
        "/api/users", json={"email": "nobody@t.test"},
        headers=bearer_headers(carol),
    )
    assert resp.status_code == 403


def test_invite_unknown_role_and_department_422(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.post(
        "/api/users",
        json={"email": "a@t.test", "role_key": "superuser"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/users",
        json={"email": "b@t.test",
              "department_id": "99999999-9999-9999-9999-999999999999"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422


# ============================================================================
# REQ-ORG-103/104 — edit + disable (D-ORG-03)
# ============================================================================

def test_update_user_display_name_and_audit(client, idp):
    alice = mint(client, idp, "alice")
    before = _audit_count("user.updated")
    resp = client.put(
        f"/api/users/{USER_CAROL}",
        json={"display_name": "Carol T."},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Carol T."
    assert _audit_count("user.updated") == before + 1


def test_update_user_out_of_scope_404_and_move_403(client, idp):
    bob = mint(client, idp, "bob")  # dept_admin A2
    # Carol lives in A1 (outside scope): no existence oracle.
    resp = client.put(
        f"/api/users/{USER_CAROL}",
        json={"display_name": "Nope"},
        headers=bearer_headers(bob),
    )
    assert resp.status_code == 404
    # Bob (in scope) moving HIMSELF to A1 -> 403 (AC-ORG-104).
    resp = client.put(
        f"/api/users/{USER_BOB}",
        json={"department_id": DEPT_A1},
        headers=bearer_headers(bob),
    )
    assert resp.status_code == 403


def test_update_user_unknown_404(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.put(
        "/api/users/99999999-9999-9999-9999-999999999999",
        json={"display_name": "X"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 404


def test_disable_revokes_sessions_devices_and_is_idempotent(
    client, idp, fake_keycloak,
):
    alice = mint(client, idp, "alice")
    # Invite a fresh user so the shared seed stays untouched.
    email = f"temp-{_uuid.uuid4().hex[:8]}@tenant-a.test"
    resp = client.post(
        "/api/users", json={"email": email, "department_id": DEPT_A1},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 202, resp.text
    row = _drain_worker(resp.json()["job_id"])
    victim = row["result"]["user_id"]

    # Self-disable is refused (AC-ORG-103).
    resp = client.post(f"/api/users/{USER_ALICE}/disable",
                       headers=bearer_headers(alice))
    assert resp.status_code == 422

    disabled_before = _audit_count("user.disabled")
    resp = client.post(f"/api/users/{victim}/disable",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "disabled"
    assert _audit_count("user.disabled") == disabled_before + 1

    # Idempotent re-disable: 200, NO new audit event.
    resp = client.post(f"/api/users/{victim}/disable",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200
    assert _audit_count("user.disabled") == disabled_before + 1

    # Sessions and devices were revoked in the same transaction.
    with _admin(TEST_DB) as c:
        sessions = c.execute(
            "SELECT count(*) FROM sessions "
            f"WHERE user_id = '{victim}' AND status = 'active'"
        ).fetchone()[0]
        devices = c.execute(
            "SELECT count(*) FROM devices "
            f"WHERE user_id = '{victim}' AND status IN ('pending','active')"
        ).fetchone()[0]
    assert sessions == 0
    assert devices == 0


def test_disable_out_of_scope_404(client, idp):
    bob = mint(client, idp, "bob")  # dept_admin A2; carol is in A1
    resp = client.post(f"/api/users/{USER_CAROL}/disable",
                       headers=bearer_headers(bob))
    assert resp.status_code == 404


# ============================================================================
# REQ-ORG-110/111 — branding (D-ORG-04)
# ============================================================================

def test_branding_get_provider_default(client, idp, admin_conn):
    carol = mint(client, idp, "carol")
    resp = client.get("/api/branding", headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Seeded tenant has no branding -> provider default (AC-ORG-110).
    assert body["source"] == "provider"
    assert body["product_name"] == "Trust Agent"

    # A tenant stamped with the provisioning default ALSO reads as provider
    # (AC-ONB-007: fresh provisioning is not a customization).
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE tenants SET branding = "
            "'{\"product_name\": \"Trust Agent\", "
            "\"primary_color\": \"#4F46E5\", \"logo_url\": null}'::jsonb "
            f"WHERE id = '{TENANT_A}'"
        )
        admin_conn.commit()
    resp = client.get("/api/branding", headers=bearer_headers(carol))
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "provider"
    assert body["accent_color"] == "#4F46E5"  # legacy primary_color mapping
    with admin_conn.cursor() as cur:
        cur.execute(f"UPDATE tenants SET branding = '{{}}'::jsonb "
                    f"WHERE id = '{TENANT_A}'")
        admin_conn.commit()


def test_branding_put_requires_manage_and_validates(client, idp):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    # Plain user: no branding.manage -> 403 (AC-ORG-111).
    resp = client.put("/api/branding", json={"product_name": "X"},
                      headers=bearer_headers(carol))
    assert resp.status_code == 403
    # Invalid color / empty body -> 422.
    assert client.put("/api/branding", json={"accent_color": "red"},
                      headers=bearer_headers(alice)).status_code == 422
    assert client.put("/api/branding", json={},
                      headers=bearer_headers(alice)).status_code == 422

    before = _audit_count("branding.updated")
    resp = client.put(
        "/api/branding",
        json={"product_name": "Acme Suite", "tagline": "Run with us",
              "accent_color": "#c90104"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "tenant"
    assert body["product_name"] == "Acme Suite"
    assert _audit_count("branding.updated") == before + 1

    # GET reflects the tenant branding for every principal.
    resp = client.get("/api/branding", headers=bearer_headers(carol))
    assert resp.json()["source"] == "tenant"
    assert resp.json()["product_name"] == "Acme Suite"

    # Restore the seed so other suites see the pristine tenant.
    with _admin(TEST_DB) as c:
        c.execute(f"UPDATE tenants SET branding = '{{}}'::jsonb "
                  f"WHERE id = '{TENANT_A}'")


# ============================================================================
# REQ-ORG-120 — grants-list (D-ORG-05)
# ============================================================================

def _publish(client, tokens, name):
    resp = client.post(
        "/api/agents", json={"name": name, "scope": "dept"},
        headers=bearer_headers(tokens),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_grants_list_gated_and_complete(client, idp):
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol, f"grants-list-{_uuid.uuid4().hex[:6]}")
    created = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_ALICE, "permission": "invoke"},
        headers=bearer_headers(carol),
    ).json()

    resp = client.get(f"/api/agents/{agent_id}/grants",
                      headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text
    grants = resp.json()["grants"]
    assert [g["id"] for g in grants] == [created["id"]]
    g = grants[0]
    assert g["grantee_user_id"] == USER_ALICE
    assert g["permission"] == "invoke"
    assert g["granted_by"] == USER_CAROL
    assert g["granted_at"]

    # Revoked grants disappear from the listing.
    resp = client.delete(f"/api/agents/{agent_id}/grants/{created['id']}",
                         headers=bearer_headers(carol))
    assert resp.status_code == 204
    resp = client.get(f"/api/agents/{agent_id}/grants",
                      headers=bearer_headers(carol))
    assert resp.json()["grants"] == []


def test_grants_list_hides_existence(client, idp):
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol, f"hidden-{_uuid.uuid4().hex[:6]}")
    # dept_admin of ANOTHER department and tenant-B user -> 404 (AC-ORG-120).
    bob = mint(client, idp, "bob")
    assert client.get(f"/api/agents/{agent_id}/grants",
                      headers=bearer_headers(bob)).status_code == 404
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    assert client.get(f"/api/agents/{agent_id}/grants",
                      headers=bearer_headers(dave)).status_code == 404
    # Unknown agent -> 404 for everyone.
    alice = mint(client, idp, "alice")
    assert client.get(
        "/api/agents/99999999-9999-9999-9999-999999999999/grants",
        headers=bearer_headers(alice)).status_code == 404


# ============================================================================
# REQ-ORG-121 — departments (D-ORG-06)
# ============================================================================

def test_create_department_happy_path_and_audit(client, idp):
    alice = mint(client, idp, "alice")
    name = f"Legal-{_uuid.uuid4().hex[:6]}"
    before = _audit_count("department.created")
    resp = client.post(
        "/api/departments",
        json={"name": name, "profile_template": "it_operations"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == name
    assert body["department_id"]
    assert _audit_count("department.created") == before + 1

    # Duplicate name (case-insensitive) -> 422.
    resp = client.post(
        "/api/departments", json={"name": name.upper()},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422


def test_create_department_validations(client, idp):
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    # dept_admin lacks departments.manage -> 403 (AC-ORG-121).
    resp = client.post("/api/departments", json={"name": "Nope"},
                       headers=bearer_headers(bob))
    assert resp.status_code == 403
    # Unknown profile template -> 422.
    resp = client.post(
        "/api/departments",
        json={"name": f"X-{_uuid.uuid4().hex[:6]}",
              "profile_template": "does_not_exist"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 422
    # Empty name -> 422.
    resp = client.post("/api/departments", json={"name": "  "},
                       headers=bearer_headers(alice))
    assert resp.status_code == 422

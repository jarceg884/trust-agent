"""Spec 017 — roles & access: assign/revoke endpoints, status via PUT,
departments list (feedback Miller #4, REQ-RA-001…006).

Real infrastructure (constitution I): ephemeral PostgreSQL via the canonical
Alembic project (which now includes 0010_roles_access — the data-driven
`roles.assign` permission is exercised for real, not mocked).
"""
import uuid as _uuid

import pytest

from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, USER_ALICE, USER_BOB, USER_CAROL, USER_DAVE,
    _admin, bearer_headers, mint,
)

import os  # noqa: E402 (honor TRUST_TEST_DB como tests/conftest.py)
TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")


class FakeKeycloak:
    """Declared double (same contract as test_org_endpoints): the invite
    worker must not reach a real IdP from the unit suite."""

    def ensure_user_with_one_time_password(
        self, realm, *, username, email, tenant_id, display_name,
    ):
        return str(_uuid.uuid4()), "one-time-pw-123"


@pytest.fixture
def fake_keycloak(monkeypatch):
    fake = FakeKeycloak()
    monkeypatch.setattr("app.org.invite._keycloak", lambda: fake)
    return fake


def _audit_count(action, extra_where=""):
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = '{action}'"
            f"{extra_where}"
        ).fetchone()
    return row[0]


def _roles_of(client, tokens, user_id):
    resp = client.get("/api/users", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    return next(
        u["roles"] for u in resp.json()["users"] if u["user_id"] == user_id
    )


# ============================================================================
# REQ-RA-001 — assign (AC-RA-001)
# ============================================================================

def test_assign_dept_admin_with_scope_and_audit(client, idp):
    alice = mint(client, idp, "alice")
    before = _audit_count("role.assigned")
    resp = client.post(
        f"/api/users/{USER_CAROL}/roles",
        json={"role_key": "dept_admin", "department_id": DEPT_A1},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert {"role_key": "dept_admin", "department_id": DEPT_A1} \
        in body["roles"]
    assert _audit_count("role.assigned") == before + 1
    # The directory reflects the assignment.
    assert {"role_key": "dept_admin", "department_id": DEPT_A1} \
        in _roles_of(client, alice, USER_CAROL)
    # Cleanup: keep the shared seed pristine for the rest of the suite.
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/dept_admin",
        params={"department_id": DEPT_A1},
        headers=bearer_headers(alice),
    ).status_code == 204


def test_assign_org_admin_without_scope(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.post(
        f"/api/users/{USER_CAROL}/roles",
        json={"role_key": "org_admin"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 201, resp.text
    assert {"role_key": "org_admin", "department_id": None} \
        in resp.json()["roles"]
    # Cleanup (otherwise carol stays org_admin for the whole session DB).
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/org_admin",
        headers=bearer_headers(alice),
    ).status_code == 204


def test_assign_validations(client, idp):
    alice = mint(client, idp, "alice")
    # Unknown role -> 422.
    assert client.post(
        f"/api/users/{USER_CAROL}/roles", json={"role_key": "superuser"},
        headers=bearer_headers(alice),
    ).status_code == 422
    # dept_admin without scope -> 422 (CHECK mirror).
    assert client.post(
        f"/api/users/{USER_CAROL}/roles", json={"role_key": "dept_admin"},
        headers=bearer_headers(alice),
    ).status_code == 422
    # Unknown department -> 422.
    assert client.post(
        f"/api/users/{USER_CAROL}/roles",
        json={"role_key": "dept_admin",
              "department_id": "99999999-9999-9999-9999-999999999999"},
        headers=bearer_headers(alice),
    ).status_code == 422
    # Unknown user -> 404 without oracle.
    assert client.post(
        "/api/users/99999999-9999-9999-9999-999999999999/roles",
        json={"role_key": "user"},
        headers=bearer_headers(alice),
    ).status_code == 404
    # Exact duplicate -> 422.
    assert client.post(
        f"/api/users/{USER_CAROL}/roles", json={"role_key": "user"},
        headers=bearer_headers(alice),
    ).status_code == 422


def test_assign_cross_tenant_404(client, idp):
    alice = mint(client, idp, "alice")
    # Dave lives in tenant B: RLS hides him (D-CP-09).
    assert client.post(
        f"/api/users/{USER_DAVE}/roles", json={"role_key": "org_admin"},
        headers=bearer_headers(alice),
    ).status_code == 404


# ============================================================================
# REQ-RA-002 — escalation -> 403 (AC-RA-003)
# ============================================================================

def test_dept_admin_cannot_assign_roles_403(client, idp):
    bob = mint(client, idp, "bob")  # dept_admin: users.manage, NO roles.assign
    assert client.post(
        f"/api/users/{USER_BOB}/roles",
        json={"role_key": "org_admin"},  # escalation attempt
        headers=bearer_headers(bob),
    ).status_code == 403
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/user",
        headers=bearer_headers(bob),
    ).status_code == 403


def test_plain_user_cannot_assign_roles_403(client, idp):
    carol = mint(client, idp, "carol")
    assert client.post(
        f"/api/users/{USER_ALICE}/roles", json={"role_key": "org_admin"},
        headers=bearer_headers(carol),
    ).status_code == 403
    assert client.delete(
        f"/api/users/{USER_ALICE}/roles/org_admin",
        headers=bearer_headers(carol),
    ).status_code == 403


def test_roles_assign_permission_only_org_admin(client, idp):
    """Data-driven check (I10): the catalog grants roles.assign ONLY to
    org_admin — the gate is DB data, not code."""
    with _admin(TEST_DB) as c:
        rows = c.execute(
            "SELECT role_key FROM catalog.role_permissions "
            "WHERE permission_key = 'roles.assign' ORDER BY role_key"
        ).fetchall()
    assert [r[0] for r in rows] == ["org_admin"]


# ============================================================================
# REQ-RA-003 — self-modification -> 422 (AC-RA-004)
# ============================================================================

def test_self_assign_and_self_revoke_422(client, idp):
    alice = mint(client, idp, "alice")
    assert client.post(
        f"/api/users/{USER_ALICE}/roles", json={"role_key": "dept_admin",
                                                "department_id": DEPT_A1},
        headers=bearer_headers(alice),
    ).status_code == 422
    assert client.delete(
        f"/api/users/{USER_ALICE}/roles/org_admin",
        headers=bearer_headers(alice),
    ).status_code == 422


# ============================================================================
# REQ-RA-004 — revoke (AC-RA-002)
# ============================================================================

def test_revoke_role_and_audit(client, idp):
    alice = mint(client, idp, "alice")
    assert client.post(
        f"/api/users/{USER_CAROL}/roles",
        json={"role_key": "dept_admin", "department_id": DEPT_A1},
        headers=bearer_headers(alice),
    ).status_code == 201

    before = _audit_count("role.revoked")
    resp = client.delete(
        f"/api/users/{USER_CAROL}/roles/dept_admin",
        params={"department_id": DEPT_A1},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 204, resp.text
    assert _audit_count("role.revoked") == before + 1
    roles = _roles_of(client, alice, USER_CAROL)
    assert all(r["role_key"] != "dept_admin" for r in roles)

    # Already revoked -> 404.
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/dept_admin",
        params={"department_id": DEPT_A1},
        headers=bearer_headers(alice),
    ).status_code == 404


def test_revoke_ambiguous_scope_requires_department(client, idp):
    alice = mint(client, idp, "alice")
    for dept in (DEPT_A1, DEPT_A2):
        assert client.post(
            f"/api/users/{USER_CAROL}/roles",
            json={"role_key": "dept_admin", "department_id": dept},
            headers=bearer_headers(alice),
        ).status_code == 201
    # Two scopes, no department_id -> 422.
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/dept_admin",
        headers=bearer_headers(alice),
    ).status_code == 422
    # Wrong scope -> 404.
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/dept_admin",
        params={"department_id": "99999999-9999-9999-9999-999999999999"},
        headers=bearer_headers(alice),
    ).status_code == 404
    # One scope at a time -> 204 twice.
    for dept in (DEPT_A1, DEPT_A2):
        assert client.delete(
            f"/api/users/{USER_CAROL}/roles/dept_admin",
            params={"department_id": dept},
            headers=bearer_headers(alice),
        ).status_code == 204


def test_revoke_unscoped_role_without_department(client, idp):
    alice = mint(client, idp, "alice")
    # carol's plain `user` role has no scope: DELETE without params works.
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/user",
        headers=bearer_headers(alice),
    ).status_code == 204
    # Restore the seed for other suites.
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
            f"VALUES (gen_random_uuid(), '{TENANT_A}', '{USER_CAROL}', "
            "'user')"
        )


def test_revoke_unknown_role_422(client, idp):
    alice = mint(client, idp, "alice")
    assert client.delete(
        f"/api/users/{USER_CAROL}/roles/superuser",
        headers=bearer_headers(alice),
    ).status_code == 422


# ============================================================================
# REQ-RA-005 — status via PUT (AC-RA-005)
# ============================================================================

@pytest.fixture
def invited_users():
    """Throwaway users invited by a test: teardown removes them so the
    shared seed stays pristine (provider-admin counts users per tenant —
    residue there breaks test_provider_admin, orden de suite)."""
    ids: list[str] = []
    yield ids
    with _admin(TEST_DB) as c, c.cursor() as cur:
        for uid in ids:
            cur.execute("DELETE FROM user_roles WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def _fresh_user(client, alice, tracker, dept=DEPT_A1):
    """Invite a throwaway user (tracked for teardown) so the shared seed
    stays untouched."""
    from app.jobs import service as jobs_service
    from app.jobs.worker import run_once
    from app.db import build_engine, tenant_tx
    from app.config import get_settings

    email = f"ra-{_uuid.uuid4().hex[:8]}@tenant-a.test"
    resp = client.post(
        "/api/users", json={"email": email, "department_id": dept},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    engine = build_engine(get_settings())
    migrator = jobs_service.build_migrator_engine(
        get_settings().database_migrator_url
    )
    for _ in range(60):
        with tenant_tx(engine, TENANT_A) as conn:
            row = jobs_service.get_job(conn, job_id)
        if row["status"] != "pending":
            tracker.append(row["result"]["user_id"])
            return row["result"]["user_id"]
        assert run_once(engine, migrator) == 1
    raise AssertionError("invite job never left pending")


def test_put_status_disable_and_reactivate(
    client, idp, fake_keycloak, invited_users,
):
    alice = mint(client, idp, "alice")
    victim = _fresh_user(client, alice, invited_users)

    disabled_before = _audit_count("user.disabled")
    resp = client.put(
        f"/api/users/{victim}", json={"status": "disabled"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "disabled"
    assert _audit_count("user.disabled") == disabled_before + 1
    # Same revocation semantics as POST /disable (D-17-05).
    with _admin(TEST_DB) as c:
        sessions = c.execute(
            "SELECT count(*) FROM sessions "
            f"WHERE user_id = '{victim}' AND status = 'active'"
        ).fetchone()[0]
    assert sessions == 0

    updated_before = _audit_count("user.updated")
    resp = client.put(
        f"/api/users/{victim}", json={"status": "active"},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert _audit_count("user.updated") == updated_before + 1


def test_put_status_validations(client, idp):
    alice = mint(client, idp, "alice")
    # Invalid value -> 422 (Literal rejects it at the schema layer).
    assert client.put(
        f"/api/users/{USER_CAROL}", json={"status": "banned"},
        headers=bearer_headers(alice),
    ).status_code == 422
    # Self-disable via PUT -> 422 (guard shared with POST /disable).
    assert client.put(
        f"/api/users/{USER_ALICE}", json={"status": "disabled"},
        headers=bearer_headers(alice),
    ).status_code == 422


# ============================================================================
# REQ-RA-006 — departments list (AC-RA-006)
# ============================================================================

def test_departments_list_scoped_to_tenant(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.get("/api/departments", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    ids = {d["department_id"] for d in resp.json()["departments"]}
    assert DEPT_A1 in ids and DEPT_A2 in ids
    # Tenant B's department is invisible (RLS).
    assert "30000000-0000-0000-0000-000000000003" not in ids
    # A plain user also reads the list (selectors need it).
    carol = mint(client, idp, "carol")
    assert client.get(
        "/api/departments", headers=bearer_headers(carol)
    ).status_code == 200

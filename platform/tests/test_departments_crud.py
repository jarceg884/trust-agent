"""Spec 016 — Departments CRUD: create with initial budget (D-16-01),
rename (D-16-03), delete with business rules (D-16-02), tenant isolation
(D-16-04). REQ-16-100…104.

Everything runs against the ephemeral PostgreSQL DB built by the canonical
Alembic project with RLS active and two seeded tenants (conftest). Seeded
context used here: tenant A org budget = $10,000 (1,000,000 cents), dept
budgets IT $4,000 + Finance $3,000 -> $3,000 unallocated; DEPT_A1 (IT)
holds alice + carol; DEPT_A2 (Finance) holds bob (dept_admin scoped).
"""
import uuid as _uuid

from httpx import Response

from conftest import (
    DEPT_A1, DEPT_B1, TENANT_A, TEST_DB, USER_ALICE, USER_BOB,
    _admin, bearer_headers, mint,
)


def _audit_count(action: str) -> int:
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = '{action}'"
        ).fetchone()
    return row[0]


def _audit_latest(action: str) -> dict:
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT payload FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = '{action}' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    # psycopg parses jsonb columns into dicts natively.
    return row[0]


def _overview_ids(client, token) -> dict[str, str]:
    resp = client.get("/api/budgets/overview", headers=bearer_headers(token))
    assert resp.status_code == 200, resp.text
    return {d["name"]: d["department_id"]
            for d in resp.json()["departments"]}


def _create(client, token, *, name=None, **extra) -> Response:
    return client.post(
        "/api/departments",
        json={"name": name or f"Dept-{_uuid.uuid4().hex[:8]}", **extra},
        headers=bearer_headers(token),
    )


def _cleanup(client, token, *dept_ids: str) -> None:
    """Best-effort DELETE so this file leaves NO departments behind.

    test_provider_admin asserts exact per-tenant counts and runs after this
    file in collection order — leaked departments would break it (the suite
    DB is session-scoped, no per-test rollback).
    """
    for dept_id in dept_ids:
        client.delete(f"/api/departments/{dept_id}",
                      headers=bearer_headers(token))


# ============================================================================
# REQ-16-100 — POST with optional initial budget (D-16-01)
# ============================================================================

def test_create_with_initial_budget_atomic(client, idp):
    alice = mint(client, idp, "alice")
    name = f"Research-{_uuid.uuid4().hex[:6]}"
    created = _audit_count("department.created")
    budgeted = _audit_count("budget.updated")

    resp = _create(client, alice, name=name, initial_budget_cents=50_000)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == name
    assert body["budget_cents"] == 50_000

    # Two real audit events: department.created + budget.updated.
    assert _audit_count("department.created") == created + 1
    assert _audit_count("budget.updated") == budgeted + 1

    # The department lists in the overview WITH its budget.
    resp = client.get("/api/budgets/overview", headers=bearer_headers(alice))
    dept = next(d for d in resp.json()["departments"] if d["name"] == name)
    assert dept["budget_cents"] == 50_000
    _cleanup(client, alice, body["department_id"])


def test_create_without_budget_matches_spec_012(client, idp):
    alice = mint(client, idp, "alice")
    resp = _create(client, alice)
    assert resp.status_code == 201, resp.text
    assert "budget_cents" not in resp.json()
    dept_id = resp.json()["department_id"]
    resp = client.get("/api/budgets/overview", headers=bearer_headers(alice))
    dept = next(d for d in resp.json()["departments"]
                if d["department_id"] == dept_id)
    assert dept["budget_cents"] is None  # listed, budgetless (D-013-07)
    _cleanup(client, alice, dept_id)


def test_create_budget_above_unallocated_rolls_back_everything(client, idp):
    """Σ dept ≤ org: $3,000 unallocated -> $5,000 initial budget -> 422
    AND the department does not exist (atomic rollback, D-16-01)."""
    alice = mint(client, idp, "alice")
    name = f"Overflow-{_uuid.uuid4().hex[:6]}"
    created = _audit_count("department.created")

    resp = _create(client, alice, name=name, initial_budget_cents=500_000)
    assert resp.status_code == 422, resp.text
    assert "exceed the organization budget" in resp.json()["error"]["message"]

    # Rollback: no department row, no department.created audit event.
    assert _audit_count("department.created") == created
    resp = client.get("/api/budgets/overview", headers=bearer_headers(alice))
    assert all(d["name"] != name for d in resp.json()["departments"])


def test_create_initial_budget_must_be_positive(client, idp):
    alice = mint(client, idp, "alice")
    for bad in (0, -100):
        resp = _create(client, alice, initial_budget_cents=bad)
        assert resp.status_code == 422, resp.text


# ============================================================================
# REQ-16-101 — PUT rename (D-16-03)
# ============================================================================

def test_rename_happy_path_and_audit(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    new_name = f"Renamed-{_uuid.uuid4().hex[:6]}"
    before = _audit_count("department.renamed")

    resp = client.put(
        f"/api/departments/{dept_id}", json={"name": new_name},
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"department_id": dept_id, "name": new_name}
    assert _audit_count("department.renamed") == before + 1
    payload = _audit_latest("department.renamed")
    assert payload["new_name"] == new_name

    ids = _overview_ids(client, alice)
    assert new_name in ids and ids[new_name] == dept_id
    _cleanup(client, alice, dept_id)


def test_rename_duplicate_case_insensitive_422(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    try:
        resp = client.put(
            f"/api/departments/{dept_id}", json={"name": "finance"},
            headers=bearer_headers(alice),
        )
        assert resp.status_code == 422, resp.text
        # Renaming to its own current name is a no-op success.
        resp = client.put(
            f"/api/departments/{dept_id}", json={"name": "  Finance  "},
            headers=bearer_headers(alice),
        )
        assert resp.status_code == 422  # still collides with DEPT_A2 "Finance"
    finally:
        _cleanup(client, alice, dept_id)


def test_rename_validations_and_scope(client, idp):
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    dept_id = _create(client, alice).json()["department_id"]
    try:
        # Empty name -> 422.
        resp = client.put(f"/api/departments/{dept_id}", json={"name": "  "},
                          headers=bearer_headers(alice))
        assert resp.status_code == 422
        # dept_admin lacks departments.manage -> 403.
        resp = client.put(f"/api/departments/{dept_id}", json={"name": "X"},
                          headers=bearer_headers(bob))
        assert resp.status_code == 403
        # Cross-tenant id -> 404 (never reveals existence, D-CP-09).
        resp = client.put(f"/api/departments/{DEPT_B1}", json={"name": "X"},
                          headers=bearer_headers(alice))
        assert resp.status_code == 404
        # Malformed uuid -> 422 validation_failed (t_378503a1: uniform contract
        # for every UUID param of the control plane; it was 404 before).
        resp = client.put("/api/departments/not-a-uuid", json={"name": "X"},
                          headers=bearer_headers(alice))
        assert resp.status_code == 422
        assert resp.json()["error"]["reason"] == "validation_failed"
    finally:
        _cleanup(client, alice, dept_id)


# ============================================================================
# REQ-16-102 — DELETE empty department + config cascade (D-16-02)
# ============================================================================

def _seed_dept_config(dept_id: str) -> None:
    """Level-dept config rows via admin SQL (RLS-free planter)."""
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO budgets (id, tenant_id, level, department_id, "
            "user_id, amount_cents) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{dept_id}', NULL, "
            "10000)"
        )
        c.execute(
            "INSERT INTO policies (id, tenant_id, level, department_id, "
            "user_id, key, value) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{dept_id}', NULL, "
            "'allowed_connectors', '[\"email\"]')"
        )
        c.execute(
            "INSERT INTO model_access (id, tenant_id, model_id, level, "
            f"department_id, user_id) VALUES (gen_random_uuid(), '{TENANT_A}', "
            f"(SELECT id FROM catalog.model_catalog ORDER BY model_name "
            f"LIMIT 1), 'dept', '{dept_id}', NULL)"
        )


def test_delete_empty_department_cascades_config(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    _seed_dept_config(dept_id)
    deleted = _audit_count("department.deleted")

    resp = client.delete(f"/api/departments/{dept_id}",
                         headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "department_id": dept_id,
                           "name": resp.json()["name"]}
    assert _audit_count("department.deleted") == deleted + 1

    # Payload is self-sufficient (D-16-05): name + removed config counts.
    payload = _audit_latest("department.deleted")
    assert payload["removed"] == {"budgets": 1, "policies": 1,
                                  "model_access": 1}
    assert payload["name"]

    # Config rows are gone in the same transaction; dept leaves the overview.
    with _admin(TEST_DB) as c:
        for table in ("budgets", "policies", "model_access"):
            n = c.execute(
                f"SELECT count(*) FROM {table} WHERE department_id = "
                f"'{dept_id}'"
            ).fetchone()[0]
            assert n == 0, table
        n = c.execute(
            f"SELECT count(*) FROM departments WHERE id = '{dept_id}'"
        ).fetchone()[0]
        assert n == 0
    resp = client.get("/api/budgets/overview", headers=bearer_headers(alice))
    assert all(d["department_id"] != dept_id
               for d in resp.json()["departments"])


# ============================================================================
# REQ-16-103 — DELETE blockers answer 409 with counts (D-16-02)
# ============================================================================
# Blocker rows are planted via admin SQL on a FRESH department with
# UNIQUE users (never the seeded alice/bob/carol: the session DB is
# shared and a stray role/spend row would pollute other suites), and
# removed again so nothing leaks.

def _plant_user(dept_id: str) -> str:
    uid = str(_uuid.uuid4())
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            "display_name, status, mfa_enrolled) VALUES "
            f"('{uid}', '{TENANT_A}', '{dept_id}', "
            f"'blocker-{uid[:8]}@tenant-a.test', 'Blocker', 'active', true)"
        )
    return uid


def _unplant(*sqls: str) -> None:
    with _admin(TEST_DB) as c:
        for sql in sqls:
            c.execute(sql)


def test_delete_with_users_409(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    user_id = _plant_user(dept_id)
    try:
        resp = client.delete(f"/api/departments/{dept_id}",
                             headers=bearer_headers(alice))
        assert resp.status_code == 409, resp.text
        message = resp.json()["error"]["message"]
        assert "1 assigned user(s)" in message
        assert "PUT /api/users/" in message  # the existing way out
    finally:
        _unplant(f"DELETE FROM users WHERE id = '{user_id}'")
        _cleanup(client, alice, dept_id)


def test_delete_with_dept_scoped_roles_409(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    user_id = _plant_user(dept_id)
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
            "department_id) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', '{user_id}', "
            f"'dept_admin', '{dept_id}')"
        )
    try:
        resp = client.delete(f"/api/departments/{dept_id}",
                             headers=bearer_headers(alice))
        assert resp.status_code == 409, resp.text
        assert "department-scoped role" in resp.json()["error"]["message"]
    finally:
        _unplant(
            f"DELETE FROM user_roles WHERE user_id = '{user_id}'",
            f"DELETE FROM users WHERE id = '{user_id}'",
        )
        _cleanup(client, alice, dept_id)


def test_delete_with_spend_409(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    request_id = f"req-{_uuid.uuid4().hex[:12]}"
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO spend_ledger (tenant_id, user_id, department_id, "
            "request_id, model_name, mode, cost_base_cents, "
            "cost_billed_cents, occurred_at) VALUES "
            f"('{TENANT_A}', NULL, '{dept_id}', "
            f"'{request_id}', 'glm-5.2', 'central', 10, 10, now())"
        )
    try:
        resp = client.delete(f"/api/departments/{dept_id}",
                             headers=bearer_headers(alice))
        assert resp.status_code == 409, resp.text
        assert "spend ledger" in resp.json()["error"]["message"]
    finally:
        _unplant(f"DELETE FROM spend_ledger WHERE request_id = "
                 f"'{request_id}'")
        _cleanup(client, alice, dept_id)


def test_delete_with_agents_409(client, idp):
    alice = mint(client, idp, "alice")
    dept_id = _create(client, alice).json()["department_id"]
    agent_id = str(_uuid.uuid4())
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO agents (id, tenant_id, owner_user_id, "
            "department_id, name, scope) VALUES "
            f"('{agent_id}', '{TENANT_A}', '{USER_ALICE}', "
            f"'{dept_id}', 'Blocker agent', 'dept')"
        )
    try:
        resp = client.delete(f"/api/departments/{dept_id}",
                             headers=bearer_headers(alice))
        assert resp.status_code == 409, resp.text
        assert "agent(s)" in resp.json()["error"]["message"]
    finally:
        _unplant(f"DELETE FROM agents WHERE id = '{agent_id}'")
        _cleanup(client, alice, dept_id)


def test_delete_validations_and_scope(client, idp):
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    # dept_admin lacks departments.manage -> 403.
    resp = client.delete(f"/api/departments/{DEPT_A1}",
                         headers=bearer_headers(bob))
    assert resp.status_code == 403
    # Cross-tenant id -> 404 (REQ-16-104, D-CP-09).
    resp = client.delete(f"/api/departments/{DEPT_B1}",
                         headers=bearer_headers(alice))
    assert resp.status_code == 404
    # Unknown-but-valid id -> 404; malformed id -> 422 (contrato global de
    # validación UUID del router, fix t_378503a1); nunca 500.
    resp = client.delete(f"/api/departments/{_uuid.uuid4()}",
                         headers=bearer_headers(alice))
    assert resp.status_code == 404
    resp = client.delete("/api/departments/not-a-uuid",
                         headers=bearer_headers(alice))
    assert resp.status_code == 422


# ============================================================================
# REQ-16-104 — multi-tenant isolation end-to-end
# ============================================================================

def test_forged_cross_tenant_ids_never_reveal_existence(client, idp):
    """Every mutation on tenant B's department from tenant A answers 404,
    and tenant B's audit/data is untouched."""
    alice = mint(client, idp, "alice")
    for method, kwargs in (
        ("put", {"json": {"name": "Hijacked"}}),
        ("delete", {}),
    ):
        resp = getattr(client, method)(
            f"/api/departments/{DEPT_B1}",
            headers=bearer_headers(alice), **kwargs,
        )
        assert resp.status_code == 404, (method, resp.text)
    with _admin(TEST_DB) as c:
        name = c.execute(
            f"SELECT name FROM departments WHERE id = '{DEPT_B1}'"
        ).fetchone()[0]
    assert name == "Operations"  # untouched

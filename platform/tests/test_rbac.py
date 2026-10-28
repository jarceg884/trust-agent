"""AC-CP-030/031 — RBAC matrix verified against catalog data (I10)."""
import os

import pytest

from tests.conftest import TENANT_A, TEST_DB, bearer_headers, mint

ROLE_USERS = {
    "org_admin": "alice",
    "dept_admin": "bob",
    "user": "carol",
}


@pytest.fixture(scope="module")
def role_tokens(client, idp):
    return {role: mint(client, idp, user) for role, user in ROLE_USERS.items()}


def test_rbac_matrix_from_catalog(client, role_tokens, admin_conn):
    """For every (role, permission) in catalog.role_permissions the probe
    endpoint answers exactly what the matrix says — the matrix is DATA."""
    matrix = dict(
        (f"{r}|{p}", True)
        for r, p in admin_conn.execute(
            "SELECT role_key, permission_key FROM catalog.role_permissions"
        ).fetchall()
    )
    roles = [r[0] for r in admin_conn.execute(
        "SELECT key FROM catalog.roles WHERE is_tenant_role").fetchall()]
    perms = [p[0] for p in admin_conn.execute(
        "SELECT key FROM catalog.permissions").fetchall()]

    checked = 0
    for role in roles:
        headers = bearer_headers(role_tokens[role])
        for perm in perms:
            resp = client.get(f"/api/_probe/perm/{perm}", headers=headers)
            expected = matrix.get(f"{role}|{perm}", False)
            assert resp.status_code == (200 if expected else 403), \
                f"role={role} perm={perm}: expected {'200' if expected else '403'}, " \
                f"got {resp.status_code} {resp.text}"
            checked += 1
    assert checked == len(roles) * len(perms)
    assert checked >= 3 * 18  # 3 tenant roles x >=18 permissions


def test_audit_verify_requires_org_admin(client, idp):
    """The chain verifier is org_admin only (spec §3.6)."""
    for user, expected in (("alice", 200), ("bob", 403), ("carol", 403)):
        tokens = mint(client, idp, user)
        resp = client.post("/api/admin/audit/verify",
                           headers=bearer_headers(tokens))
        assert resp.status_code == expected, f"user={user}: {resp.text}"


def test_dept_admin_scope_limited_to_own_department(client, idp, admin_conn):
    """AC-CP-031: bob (dept_admin of Finance/A2) cannot administer IT/A1.

    The dept scope is enforced by scoping: resources of other departments
    fall outside his filter or do not exist for him.
    """
    from app.auth.rbac import dept_scope
    from app.auth.service import Principal, RoleAssignment

    bob = Principal(
        tenant_id=TENANT_A, tenant_slug="tenant-a", tenant_name="Tenant A",
        user_id="bob",
        email="", display_name="", department_id=None,
        roles=[RoleAssignment("dept_admin", "30000000-0000-0000-0000-000000000002")],
    )
    assert dept_scope(bob) == ["30000000-0000-0000-0000-000000000002"]

    alice = Principal(
        tenant_id=TENANT_A, tenant_slug="tenant-a", tenant_name="Tenant A",
        user_id="alice",
        email="", display_name="", department_id=None,
        roles=[RoleAssignment("org_admin", None)],
    )
    assert dept_scope(alice) is None  # whole tenant


def test_platform_admin_is_not_assignable_in_tenants(admin_conn):
    """user_roles only accepts tenant roles (schema CHECK, REQ-ORG-003)."""
    import psycopg

    with psycopg.connect(
        f"host=127.0.0.1 port=55432 user=trust_app password=trust_app_dev "
        f"dbname={TEST_DB}"  # t_e000bf40: conftest.TEST_DB (import-time) —
        # os.environ['TRUST_TEST_DB'] ya fue purgado por el fixture env (H6)
    ) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO user_roles (tenant_id, user_id, role_key) "
                    "VALUES (%s, %s, 'platform_admin')",
                    (TENANT_A, "40000000-0000-0000-0000-000000000001"),
                )

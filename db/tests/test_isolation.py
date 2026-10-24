"""AC-DB-032 — Aislamiento multi-tenant (constitution III, bloqueante).

Suite parametrizada sobre las 16 tablas de negocio, ejecutada con el rol de
aplicación real (trust_app) y scope transaccional de tenant:
  1. SELECT con scope ve SOLO las filas de su tenant (y las ve todas).
  2. INSERT cross-tenant → rechazado por RLS (WITH CHECK).
  3. INSERT en su tenant → OK.
  4. UPDATE que reasigna tenant_id → rechazado por RLS.
  5. UPDATE de filas del otro tenant → 0 filas afectadas.
  6. DELETE de filas del otro tenant → 0 filas afectadas.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import psycopg.errors

from conftest import (
    BUSINESS_TABLES, TENANT_A, TENANT_B, DEPT_A1, DEPT_A2, DEPT_B1, USER_A1, USER_B1,
    VAULT_A1, VAULT_B1, tenant_tx,
)

_CT = bytes(range(32))


def _insert_for(table: str, tenant_id: str, n: int):
    """(sql, params) de una fila válida de `table` para `tenant_id`."""
    u = uuid.uuid4()
    dept = DEPT_A1 if tenant_id == TENANT_A else DEPT_B1
    user = USER_A1 if tenant_id == TENANT_A else USER_B1
    vault = VAULT_A1 if tenant_id == TENANT_A else VAULT_B1
    model = "deepseek-v3" if tenant_id == TENANT_A else "qwen2.5-72b-instruct"
    # depto sin presupuesto sembrado (evita chocar con el unique de scope)
    free_dept = DEPT_A2 if tenant_id == TENANT_A else DEPT_B1
    rows = {
        "tenants": (
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (u, f"Tenant {n}", f"tenant-{n}-{u.hex[:8]}"),
        ),
        "departments": (
            "INSERT INTO departments (id, tenant_id, name) VALUES (%s, %s, %s)",
            (u, tenant_id, f"Dept-{n}-{u.hex[:8]}"),
        ),
        "users": (
            "INSERT INTO users (id, tenant_id, department_id, email, display_name) "
            "VALUES (%s, %s, %s, %s, %s)",
            (u, tenant_id, dept, f"u{n}-{u.hex[:8]}@x.test", f"User {n}"),
        ),
        "user_roles": (
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key) VALUES (%s, %s, %s, 'user')",
            (u, tenant_id, user),
        ),
        "budgets": (
            "INSERT INTO budgets (id, tenant_id, level, department_id, amount_cents) "
            "VALUES (%s, %s, 'dept', %s, 1000)",
            (u, tenant_id, free_dept),
        ),
        "budget_counters": (
            "INSERT INTO budget_counters (tenant_id, level, entity_id, period_start, period_end) "
            "VALUES (%s, 'org', %s, %s, %s)",
            (tenant_id, tenant_id, date(2026, 11, 1), date(2026, 11, 30)),
        ),
        "spend_ledger": (
            "INSERT INTO spend_ledger (tenant_id, request_id, model_name, mode, "
            "cost_base_cents, cost_billed_cents, occurred_at) VALUES (%s, %s, %s, 'central', 10, 10, %s)",
            (tenant_id, f"req-{n}-{u.hex[:12]}", "deepseek-v3", datetime.now(timezone.utc)),
        ),
        "policies": (
            "INSERT INTO policies (id, tenant_id, level, key, value) "
            "VALUES (%s, %s, 'org', 'max_daily_sessions', '5'::jsonb)",
            (u, tenant_id),
        ),
        "devices": (
            "INSERT INTO devices (id, tenant_id, user_id, fingerprint) VALUES (%s, %s, %s, %s)",
            (u, tenant_id, user, f"fp-{u.hex[:16]}"),
        ),
        "vault_credentials": (
            "INSERT INTO vault_credentials (id, tenant_id, user_id, name, credential_type, "
            "ciphertext_secret, ciphertext_dek, nonce, key_id) VALUES (%s, %s, %s, %s, 'api_key', %s, %s, %s, 'kek-v1')",
            (u, tenant_id, user, f"cred-{n}-{u.hex[:8]}", _CT, _CT, _CT[:12]),
        ),
        "connector_instances": (
            "INSERT INTO connector_instances (id, tenant_id, user_id, connector_slug, credential_id) "
            "VALUES (%s, %s, %s, 'telegram', %s)",
            (u, tenant_id, user, vault),
        ),
        "audit_log": (
            "INSERT INTO audit_log (tenant_id, actor_role, action, payload) "
            "VALUES (%s, 'system', 'job.created', '{}')",
            (tenant_id,),
        ),
        "model_access": (
            "INSERT INTO model_access (id, tenant_id, model_id, level, mode) "
            "VALUES (%s, %s, (SELECT id FROM catalog.model_catalog WHERE model_name = %s), 'org', 'central')",
            (u, tenant_id, model),
        ),
        "api_keys": (
            "INSERT INTO api_keys (id, tenant_id, user_id, litellm_key_hash) VALUES (%s, %s, %s, %s)",
            (u, tenant_id, user, f"hash-{u.hex[:16]}"),
        ),
        "sessions": (
            "INSERT INTO sessions (id, tenant_id, user_id, kind, jti, expires_at) "
            "VALUES (%s, %s, %s, 'api', %s, %s)",
            (u, tenant_id, user, f"jti-{u.hex[:12]}", datetime.now(timezone.utc) + timedelta(hours=1)),
        ),
        "jobs": (
            "INSERT INTO jobs (id, tenant_id, job_type, idempotency_key) VALUES (%s, %s, 'test_job', %s)",
            (u, tenant_id, f"ik-{u.hex[:12]}"),
        ),
        # spec 005 (governance)
        "agents": (
            "INSERT INTO agents (id, tenant_id, owner_user_id, department_id, name) "
            "VALUES (%s, %s, %s, %s, %s)",
            (u, tenant_id, user, dept, f"Agent-{n}-{u.hex[:8]}"),
        ),
        "agent_grants": (
            # agent del propio tenant por subselect (RLS del SELECT ya filtra);
            # en el caso cross-tenant el subselect devuelve NULL y el WITH CHECK
            # de RLS se evalúa ANTES que la FK → InsufficientPrivilege.
            "INSERT INTO agent_grants (id, tenant_id, agent_id, grantee_user_id, permission) "
            "VALUES (%s, %s, (SELECT id FROM agents WHERE tenant_id = %s LIMIT 1), %s, 'invoke')",
            (u, tenant_id, tenant_id, user),
        ),
        "notifications": (
            "INSERT INTO notifications (id, tenant_id, user_id, type) "
            "VALUES (%s, %s, %s, 'budget_cutoff')",
            (u, tenant_id, user),
        ),
        # spec 013 (REQ-AUD-006): checkpoint de cadena (solo lectura para
        # la app; las escrituras son del job de purga, rol trust_migrator).
        "audit_chain_checkpoint": (
            "INSERT INTO audit_chain_checkpoint "
            "(tenant_id, last_seq, last_hash, purged_rows) "
            "VALUES (%s, 1, %s, 1)",
            (tenant_id, "c" * 64),
        ),
    }
    return rows[table]


NON_TENANT_TABLES = [t for t in BUSINESS_TABLES if t != "tenants"]
# La app es SELECT-only en el checkpoint (spec 013 D-013-04): los tests de
# INSERT lo excluyen y uno propio verifica el rechazo fail-closed.
READ_ONLY_TABLES = ("audit_chain_checkpoint",)
WRITABLE_NON_TENANT_TABLES = [
    t for t in NON_TENANT_TABLES if t not in READ_ONLY_TABLES
]


@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_select_scoped_sees_only_own_tenant(app_conn, admin_conn, table):
    admin_cur = admin_conn.cursor()
    admin_cur.execute(f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (TENANT_A,))
    expected_a = admin_cur.fetchone()[0]
    admin_cur.execute(f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (TENANT_B,))
    expected_b = admin_cur.fetchone()[0]
    assert expected_a > 0, f"seed incompleto: {table} sin filas del tenant A"

    with tenant_tx(app_conn, TENANT_A) as conn:
        seen = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    assert seen == expected_a, f"{table}: el tenant A ve {seen} filas, debería ver {expected_a}"


@pytest.mark.parametrize("table", WRITABLE_NON_TENANT_TABLES)
def test_insert_cross_tenant_rejected(app_conn, table):
    """Scoped al tenant A, insertar filas del tenant B → violación de RLS."""
    sql, params = _insert_for(table, TENANT_B, n=100)
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(sql, params)


@pytest.mark.parametrize("table", WRITABLE_NON_TENANT_TABLES)
def test_insert_own_tenant_ok(app_conn, table):
    sql, params = _insert_for(table, TENANT_A, n=200)
    with tenant_tx(app_conn, TENANT_A) as conn:
        conn.execute(sql, params)
        assert conn.execute("SELECT count(*) FROM " + table).fetchone()[0] >= 1


@pytest.mark.parametrize("table", READ_ONLY_TABLES)
def test_read_only_tables_reject_app_writes(app_conn, table):
    """spec 013 D-013-04: la app es SELECT-only en el checkpoint de cadena
    (las escrituras son del job de purga, rol trust_migrator). INSERT,
    UPDATE y DELETE se rechazan por privilegio — fail-closed doble
    (grant + trigger de inmutabilidad); el SELECT scoped sí funciona
    (lo consume audit_verify_chain)."""
    sql, params = _insert_for(table, TENANT_A, n=300)
    # Cada intento en su propia transacción: el rechazo anterior aborta
    # la transacción actual (psycopg) y no se puede encadenar.
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql, params)
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"UPDATE {table} SET last_seq = 99")
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"DELETE FROM {table}")
    with tenant_tx(app_conn, TENANT_A) as conn:
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 1


def test_tenants_insert_requires_matching_scope(app_conn):
    """tenants.tenant_id es GENERATED (= id): insertar otro tenant estando
    scoped a A lo rechaza RLS; con su propio scope, prospera (flujo de
    aprovisionamiento)."""
    new_id = uuid.uuid4()
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                "INSERT INTO tenants (id, name, slug) VALUES (%s, 'X', %s)",
                (new_id, f"slug-{new_id.hex[:8]}"),
            )
    with tenant_tx(app_conn, str(new_id)) as conn:
        conn.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, 'X', %s)",
            (new_id, f"slug-{new_id.hex[:8]}"),
        )
        assert conn.execute("SELECT count(*) FROM tenants").fetchone()[0] == 1


@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_update_reassigning_tenant_rejected(app_conn, table):
    """Reasignar filas del tenant A al tenant B → rechazado (WITH CHECK /
    columna generada / sin grant de UPDATE en audit_log)."""
    if table == "audit_log":
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE audit_log SET payload = '{}' WHERE tenant_id = %s", (TENANT_A,))
        return
    if table == "audit_chain_checkpoint":
        # SELECT-only para la app (spec 013 D-013-04): sin grant de UPDATE.
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE audit_chain_checkpoint SET last_seq = 99 "
                    "WHERE tenant_id = %s", (TENANT_A,))
        return
    if table == "tenants":
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.Error, match="generated column"):
                conn.execute("UPDATE tenants SET tenant_id = %s WHERE tenant_id = %s", (TENANT_B, TENANT_A))
        return
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                f"UPDATE {table} SET tenant_id = %s WHERE tenant_id = %s",
                (TENANT_B, TENANT_A),
            )


@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_update_other_tenant_rows_invisible(app_conn, table):
    """UPDATE dirigido a filas del tenant B estando scoped a A → 0 filas."""
    if table == "audit_log":
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE audit_log SET payload = '{}' WHERE tenant_id = %s", (TENANT_B,))
        return
    if table == "audit_chain_checkpoint":
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE audit_chain_checkpoint SET last_seq = 99 "
                    "WHERE tenant_id = %s", (TENANT_B,))
        return
    if table == "tenants":
        with tenant_tx(app_conn, TENANT_A) as conn:
            cur = conn.execute("UPDATE tenants SET name = name WHERE tenant_id = %s", (TENANT_B,))
        assert cur.rowcount == 0
        return
    with tenant_tx(app_conn, TENANT_A) as conn:
        cur = conn.execute(f"UPDATE {table} SET tenant_id = %s WHERE tenant_id = %s", (TENANT_B, TENANT_B))
    assert cur.rowcount == 0, f"{table}: el UPDATE cross-tenant afectó filas"


@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_delete_other_tenant_rows_invisible(app_conn, table):
    if table == "audit_log":
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM audit_log WHERE tenant_id = %s", (TENANT_B,))
        return
    if table == "audit_chain_checkpoint":
        with tenant_tx(app_conn, TENANT_A) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "DELETE FROM audit_chain_checkpoint WHERE tenant_id = %s",
                    (TENANT_B,))
        return
    with tenant_tx(app_conn, TENANT_A) as conn:
        cur = conn.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (TENANT_B,))
    assert cur.rowcount == 0, f"{table}: el DELETE cross-tenant borró filas"


def test_tenant_b_is_symmetrically_isolated(app_conn, admin_conn):
    """Simetría: el tenant B tampoco ve al A (spot-check sobre users/audit)."""
    admin_cur = admin_conn.cursor()
    admin_cur.execute("SELECT count(*) FROM users WHERE tenant_id = %s", (TENANT_B,))
    expected = admin_cur.fetchone()[0]
    with tenant_tx(app_conn, TENANT_B) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == expected
        cur = conn.execute("DELETE FROM users WHERE tenant_id = %s", (TENANT_A,))
    assert cur.rowcount == 0

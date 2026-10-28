"""AC-DB-010…025 — Constraints de negocio: scopes de nivel, dinero en centavos,
uniques por tenant, FKs compuestos cross-tenant, vault sin plaintext."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import psycopg.errors

from conftest import (
    TENANT_A, TENANT_B, DEPT_A1, DEPT_A2, DEPT_B1, USER_A1, USER_A2, USER_B1,
    VAULT_A1, tenant_tx,
)

CT = bytes(range(32))
CANARY = "SUPERSECRET-CANARY-4f2a"


# --- tenants (AC-DB-010) -----------------------------------------------------

def test_tenant_retention_below_365_rejected(app_conn):
    new_id = uuid.uuid4()
    with tenant_tx(app_conn, str(new_id)) as conn:  # scope del tenant nuevo (flujo de provisioning)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO tenants (id, name, slug, retention_days) "
                "VALUES (%s, 'R', %s, 100)",
                (new_id, f"r-{new_id.hex[:8]}"),
            )


def test_tenant_duplicate_slug_rejected(app_conn):
    new_id = uuid.uuid4()
    with tenant_tx(app_conn, str(new_id)) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO tenants (id, name, slug) VALUES (%s, 'Dup', 'tenant-a')",
                (new_id,),
            )


def test_tenant_tenant_id_is_generated_self_reference(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        row = conn.execute(
            "SELECT id, tenant_id FROM tenants WHERE slug = 'tenant-a'"
        ).fetchone()
    assert row[0] == row[1]


# --- departments / users (AC-DB-011) -----------------------------------------

def test_department_duplicate_name_within_tenant(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO departments (id, tenant_id, name) VALUES (%s, %s, 'IT')",
                (uuid.uuid4(), TENANT_A),
            )


def test_same_department_name_allowed_across_tenants(app_conn):
    with tenant_tx(app_conn, TENANT_B) as conn:
        conn.execute(
            "INSERT INTO departments (id, tenant_id, name) VALUES (%s, %s, 'IT')",
            (uuid.uuid4(), TENANT_B),
        )


def test_same_email_allowed_across_tenants(app_conn):
    with tenant_tx(app_conn, TENANT_B) as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, display_name) "
            "VALUES (%s, %s, %s, 'alice@tenant-a.test', 'Alice B')",
            (uuid.uuid4(), TENANT_B, DEPT_B1),
        )


def test_user_cannot_reference_other_tenant_department(app_conn):
    """FK compuesto (tenant_id, department_id): imposible apuntar al depto de
    otro tenant incluso saltando la validación de aplicación."""
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO users (id, tenant_id, department_id, email, display_name) "
                "VALUES (%s, %s, %s, 'x@x.test', 'X')",
                (uuid.uuid4(), TENANT_A, DEPT_B1),
            )


# --- user_roles (AC-DB-012) ---------------------------------------------------

def test_platform_admin_not_assignable_within_tenant(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                "VALUES (%s, %s, %s, 'platform_admin')",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


def test_unknown_role_rejected(app_conn):
    """Un rol fuera de la matriz se rechaza: el CHECK (matriz §2.1) es la
    guarda efectiva — PostgreSQL evalúa CHECK antes que la FK a catalog.roles."""
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                "VALUES (%s, %s, %s, 'superuser')",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


def test_dept_admin_requires_department_scope(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                "VALUES (%s, %s, %s, 'dept_admin')",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


# --- budgets (AC-DB-013) -------------------------------------------------------

def test_budget_org_with_department_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO budgets (id, tenant_id, level, department_id, amount_cents) "
                "VALUES (%s, %s, 'org', %s, 100)",
                (uuid.uuid4(), TENANT_A, DEPT_A1),
            )


def test_budget_user_level_requires_dept_and_user(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO budgets (id, tenant_id, level, user_id, amount_cents) "
                "VALUES (%s, %s, 'user', %s, 100)",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


def test_budget_negative_amount_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO budgets (id, tenant_id, level, amount_cents) "
                "VALUES (%s, %s, 'org', -1)",
                (uuid.uuid4(), TENANT_A),
            )


def test_duplicate_org_budget_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO budgets (id, tenant_id, level, amount_cents) "
                "VALUES (%s, %s, 'org', 500)",
                (uuid.uuid4(), TENANT_A),
            )


def test_budget_counters_accepts_period_row(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        conn.execute(
            "INSERT INTO budget_counters (tenant_id, level, entity_id, period_start, period_end, "
            "spent_cents, budget_cents) VALUES (%s, 'dept', %s, '2026-11-01', '2026-11-30', 0, 400000)",
            (TENANT_A, DEPT_A2),
        )


# --- spend_ledger (AC-DB-014) ---------------------------------------------------

def test_spend_ledger_duplicate_request_id_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO spend_ledger (tenant_id, request_id, model_name, mode, "
                "cost_base_cents, cost_billed_cents, occurred_at) "
                "VALUES (%s, 'req-a-1', 'deepseek-v4-flash', 'central', 1, 1, now())",
                (TENANT_A,),
            )


def test_spend_ledger_byok_billed_equals_base(app_conn):
    """Modo BYOK: sin margen — cost_billed_cents == cost_base_cents (arq. §5.3)."""
    with tenant_tx(app_conn, TENANT_A) as conn:
        row = conn.execute(
            "INSERT INTO spend_ledger (tenant_id, request_id, model_name, mode, "
            "cost_base_cents, cost_billed_cents, occurred_at) "
            "VALUES (%s, %s, 'deepseek-v4-flash', 'byok', 42, 42, now()) RETURNING cost_billed_cents",
            (TENANT_A, f"req-byok-{uuid.uuid4().hex[:8]}"),
        ).fetchone()
        assert row[0] == 42


def test_spend_cursor_updatable_by_app(app_conn):
    with app_conn.transaction():
        app_conn.execute(
            "UPDATE ops.spend_cursor SET last_request_id = %s, last_occurred_at = now() WHERE id = 1",
            ("req-cursor-1",),
        )
        value = app_conn.execute("SELECT last_request_id FROM ops.spend_cursor").fetchone()[0]
    assert value == "req-cursor-1"


# --- policies (AC-DB-015) --------------------------------------------------------

def test_policy_unknown_key_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO policies (id, tenant_id, level, key, value) "
                "VALUES (%s, %s, 'org', 'clave_inventada', '1'::jsonb)",
                (uuid.uuid4(), TENANT_A),
            )


def test_policy_user_level_requires_user(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO policies (id, tenant_id, level, department_id, key, value) "
                "VALUES (%s, %s, 'user', %s, 'max_daily_sessions', '5'::jsonb)",
                (uuid.uuid4(), TENANT_A, DEPT_A1),
            )


def test_policy_duplicate_scope_key_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO policies (id, tenant_id, level, key, value) "
                "VALUES (%s, %s, 'org', 'require_mfa', 'true'::jsonb)",
                (uuid.uuid4(), TENANT_A),
            )


def test_invariable_policy_keys_are_catalog_data(admin_conn):
    row = admin_conn.execute(
        "SELECT key FROM catalog.policy_keys WHERE invariable ORDER BY key"
    ).fetchall()
    assert [r[0] for r in row] == [
        # require_mfa dejó de ser invariable (migración 0014, spec 015
        # D-AUTH-07): es org-tunable, default false.
        "approved_models_only", "explicit_shared_agent_grants",
        "scoped_credentials",
    ]


# --- devices (AC-DB-016) -----------------------------------------------------------

def test_device_duplicate_fingerprint_within_tenant(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO devices (id, tenant_id, user_id, fingerprint) "
                "VALUES (%s, %s, %s, 'fp-alice-device')",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


def test_same_fingerprint_allowed_across_tenants(app_conn):
    with tenant_tx(app_conn, TENANT_B) as conn:
        conn.execute(
            "INSERT INTO devices (id, tenant_id, user_id, fingerprint) "
            "VALUES (%s, %s, %s, 'fp-alice-device')",
            (uuid.uuid4(), TENANT_B, USER_B1),
        )


# --- vault_credentials (AC-DB-018) ---------------------------------------------------

def test_vault_requires_ciphertext_columns(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO vault_credentials (id, tenant_id, user_id, name, credential_type, "
                "ciphertext_dek, nonce, key_id) VALUES (%s, %s, %s, 'x', 'api_key', %s, %s, 'kek-v1')",
                (uuid.uuid4(), TENANT_A, USER_A1, CT, CT[:12]),
            )


def test_vault_never_stores_plaintext(app_conn, admin_conn):
    """Constitución IV / REQ-VLT-001: el canario jamás aparece en la tabla."""
    with tenant_tx(app_conn, TENANT_A) as conn:
        conn.execute(
            "INSERT INTO vault_credentials (id, tenant_id, user_id, name, credential_type, "
            "ciphertext_secret, ciphertext_dek, nonce, key_id) "
            "VALUES (%s, %s, %s, 'canary-cred', 'api_key', %s, %s, %s, 'kek-v1')",
            (uuid.uuid4(), TENANT_A, USER_A1, CT, CT, CT[:12]),
        )
    dump = admin_conn.execute(
        "SELECT * FROM vault_credentials"
    ).fetchall()
    blob = repr(dump)
    assert CANARY not in blob, "el secreto canario no debe existir en ninguna columna"
    # y el canario tampoco en el dump textual de toda la tabla
    text = admin_conn.execute(
        "SELECT string_agg(t::text, ',') FROM vault_credentials t"
    ).fetchone()[0]
    assert CANARY not in text


# --- connector_instances (AC-DB-017) ---------------------------------------------------

def test_connector_unknown_slug_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO connector_instances (id, tenant_id, user_id, connector_slug) "
                "VALUES (%s, %s, %s, 'pinterest')",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


def test_connector_cannot_use_other_tenant_credential(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO connector_instances (id, tenant_id, user_id, connector_slug, credential_id) "
                "VALUES (%s, %s, %s, 'slack', %s)",
                (uuid.uuid4(), TENANT_A, USER_A1, "60000000-0000-0000-0000-000000000002"),
            )


# --- model_access (AC-DB-022) -----------------------------------------------------------

def test_model_access_duplicate_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO model_access (id, tenant_id, model_id, level, mode) "
                "VALUES (%s, %s, "
                "(SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), "
                "'org', 'central')",
                (uuid.uuid4(), TENANT_A),
            )


def test_model_access_org_and_user_levels_coexist(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        conn.execute(
            "INSERT INTO model_access (id, tenant_id, model_id, level, department_id, user_id, mode) "
            "VALUES (%s, %s, "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = 'deepseek-v4-flash'), "
            "'user', %s, %s, 'byok')",
            (uuid.uuid4(), TENANT_A, DEPT_A1, USER_A1),
        )


# --- api_keys (AC-DB-023) -----------------------------------------------------------------

def test_api_key_requires_hash(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "INSERT INTO api_keys (id, tenant_id, user_id) VALUES (%s, %s, %s)",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


def test_api_key_hash_is_unique(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO api_keys (id, tenant_id, user_id, litellm_key_hash) "
                "VALUES (%s, %s, %s, 'hash-alice-key')",
                (uuid.uuid4(), TENANT_A, USER_A1),
            )


# --- sessions (AC-DB-024) -------------------------------------------------------------------

def test_session_duplicate_jti_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO sessions (id, tenant_id, user_id, kind, jti, expires_at) "
                "VALUES (%s, %s, %s, 'desktop', 'jti-alice', %s)",
                (uuid.uuid4(), TENANT_A, USER_A1, datetime.now(timezone.utc) + timedelta(hours=1)),
            )


# --- jobs (AC-DB-025) -------------------------------------------------------------------------

def test_job_duplicate_idempotency_key_rejected(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO jobs (id, tenant_id, job_type, idempotency_key) "
                "VALUES (%s, %s, 'user_provisioning', 'onboard-alice')",
                (uuid.uuid4(), TENANT_A),
            )


def test_job_same_idempotency_key_other_tenant_ok(app_conn):
    with tenant_tx(app_conn, TENANT_B) as conn:
        conn.execute(
            "INSERT INTO jobs (id, tenant_id, job_type, idempotency_key) "
            "VALUES (%s, %s, 'user_provisioning', 'onboard-alice')",
            (uuid.uuid4(), TENANT_B),
        )


def test_job_requires_tenant(admin_conn):
    """Con trust_app, RLS rechaza el INSERT sin tenant antes que el NOT NULL;
    la restricción NOT NULL se verifica vía admin (superusuario)."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        admin_conn.execute(
            "INSERT INTO jobs (id, job_type) VALUES (%s, 'orphan_job')",
            (uuid.uuid4(),),
        )

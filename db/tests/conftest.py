"""Fixtures para la suite de la capa de datos Trust Agent.

Levanta una base de datos efímera REAL (trust_agent_test) aplicando la vía
canónica (`alembic upgrade head`), siembra dos tenants con datos en las 16
tablas de negocio y expone conexiones como `trust_app` (rol de aplicación,
sujeto a RLS) y como `postgres` (admin, para verificación cross-tenant).

Requisitos: contenedor PostgreSQL 17 en 127.0.0.1:55432 (ver db/README.md).
"""
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

DB_HOST = os.environ.get("TRUST_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("TRUST_DB_PORT", "55432")
ADMIN_PASSWORD = os.environ.get("TRUST_DB_ADMIN_PASSWORD", "trustlocal")
TEST_DB = "trust_agent_test"

ADMIN_DSN = (
    f"host={DB_HOST} port={DB_PORT} user=postgres password={ADMIN_PASSWORD} "
    f"dbname={{db}}"
)
APP_DSN = (
    f"host={DB_HOST} port={DB_PORT} user=trust_app password=trust_app_dev "
    f"dbname={TEST_DB}"
)

DB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALEMBIC = os.path.join(DB_DIR, ".venv", "bin", "alembic")
SCHEMA_SQL = os.path.join(DB_DIR, "schema.sql")

# --- Identificadores fijos del seed -----------------------------------------
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
DEPT_A1 = "30000000-0000-0000-0000-000000000001"
DEPT_A2 = "30000000-0000-0000-0000-000000000002"
DEPT_B1 = "30000000-0000-0000-0000-000000000003"
USER_A1 = "40000000-0000-0000-0000-000000000001"  # alice (dept A1)
USER_A2 = "40000000-0000-0000-0000-000000000002"  # bob   (dept A2)
USER_B1 = "40000000-0000-0000-0000-000000000003"  # carol (dept B1)
DEVICE_A1 = "50000000-0000-0000-0000-000000000001"
DEVICE_B1 = "50000000-0000-0000-0000-000000000002"
VAULT_A1 = "60000000-0000-0000-0000-000000000001"  # credencial vault tenant A
VAULT_B1 = "60000000-0000-0000-0000-000000000002"  # credencial vault tenant B
# spec 024: conexiones personalizadas del tenant (seed para RLS/isolation)
CONN_A1 = "60000000-0000-0000-0000-000000000003"
CONN_B1 = "60000000-0000-0000-0000-000000000004"
# spec 041: política nombrada + asignación por tenant (seed de aislamiento)
NAMED_POLICY_A1 = "80000000-0000-0000-0000-000000000001"
NAMED_ASSIGNMENT_A1 = "80000000-0000-0000-0000-000000000002"
NAMED_POLICY_B1 = "80000000-0000-0000-0000-000000000003"
NAMED_ASSIGNMENT_B1 = "80000000-0000-0000-0000-000000000004"

# Las 16 tablas de negocio (todas con tenant_id + RLS).
BUSINESS_TABLES = [
    "tenants",
    "departments",
    "users",
    "user_roles",
    "budgets",
    "budget_counters",
    "spend_ledger",
    "policies",
    "devices",
    "vault_credentials",
    "vault_connections",
    "connector_instances",
    "audit_log",
    "model_access",
    "api_keys",
    "sessions",
    "jobs",
    # spec 005 (governance): migración 0004_governance
    "agents",
    "agent_grants",
    "notifications",
    # spec 013 (REQ-AUD-006): migración 0006_audit_retention
    "audit_chain_checkpoint",
    # spec 028 (branding REQ-BRD-004): migración 0018_branding_versioning
    "branding_assets",
    "branding_versions",
    # spec 041 (políticas nombradas): migración 0034_named_policies
    "named_policies",
    "named_policy_assignments",
]

_FAKE_CT = bytes(range(32))  # ciphertext opaco de prueba (nunca plaintext)


def _admin(db: str = "postgres") -> psycopg.Connection:
    return psycopg.connect(ADMIN_DSN.format(db=db), autocommit=True)


@contextmanager
def tenant_tx(conn: psycopg.Connection, tenant_id: str):
    """Transacción con scope de tenant — exactamente como hará la app:
    `set_config(..., is_local=true)` == SET LOCAL, muere con la transacción."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield conn


def _seed() -> None:
    with _admin(TEST_DB) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (id, name, slug, oidc_realm, maas_margin_bps) "
                "VALUES (%s,'Tenant A','tenant-a','tenant-a',500), "
                "(%s,'Tenant B','tenant-b','tenant-b',0)",
                (TENANT_A, TENANT_B),
            )
            cur.execute(
                "INSERT INTO departments (id, tenant_id, name) VALUES "
                f"(%s, '{TENANT_A}', 'IT'), (%s, '{TENANT_A}', 'Finance'), "
                f"(%s, '{TENANT_B}', 'Operations')",
                (DEPT_A1, DEPT_A2, DEPT_B1),
            )
            cur.execute(
                "INSERT INTO users (id, tenant_id, department_id, email, display_name, status, mfa_enrolled) VALUES "
                f"(%s, '{TENANT_A}', '{DEPT_A1}', 'alice@tenant-a.test', 'Alice', 'active', true), "
                f"(%s, '{TENANT_A}', '{DEPT_A2}', 'bob@tenant-a.test', 'Bob', 'active', true), "
                f"(%s, '{TENANT_B}', '{DEPT_B1}', 'carol@tenant-b.test', 'Carol', 'active', true)",
                (USER_A1, USER_A2, USER_B1),
            )
            cur.execute(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key, department_id) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A1}', 'org_admin', NULL), "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A2}', 'dept_admin', '{DEPT_A2}'), "
                f"(gen_random_uuid(), '{TENANT_B}', '{USER_B1}', 'user', NULL)"
            )
            cur.execute(
                "INSERT INTO budgets (id, tenant_id, level, department_id, user_id, amount_cents) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', 'org',  NULL, NULL, 1000000), "
                f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{DEPT_A1}', NULL, 400000), "
                f"(gen_random_uuid(), '{TENANT_A}', 'user', '{DEPT_A1}', '{USER_A1}', 100000), "
                f"(gen_random_uuid(), '{TENANT_B}', 'org',  NULL, NULL, 500000)"
            )
            cur.execute(
                "INSERT INTO budget_counters (tenant_id, level, entity_id, period_start, period_end, spent_cents, budget_cents) VALUES "
                f"('{TENANT_A}', 'org',  '{TENANT_A}', '2026-10-01', '2026-10-31', 120000, 1000000), "
                f"('{TENANT_A}', 'dept', '{DEPT_A1}',  '2026-10-01', '2026-10-31', 30000, 400000), "
                f"('{TENANT_B}', 'org',  '{TENANT_B}', '2026-10-01', '2026-10-31', 0, 500000)"
            )
            cur.execute(
                "INSERT INTO spend_ledger (tenant_id, request_id, user_id, department_id, model_name, mode, "
                "cost_base_cents, cost_billed_cents, tokens_in, tokens_out, occurred_at) VALUES "
                f"('{TENANT_A}', 'req-a-1', '{USER_A1}', '{DEPT_A1}', 'glm-5.2', 'central', 100, 105, 1000, 200, now()), "
                f"('{TENANT_A}', 'req-a-2', '{USER_A1}', '{DEPT_A1}', 'deepseek-v4-flash', 'byok', 50, 50, 500, 100, now()), "
                f"('{TENANT_B}', 'req-b-1', '{USER_B1}', '{DEPT_B1}', 'glm-5.2', 'central', 80, 80, 800, 150, now())"
            )
            cur.execute(
                "INSERT INTO policies (id, tenant_id, level, department_id, user_id, key, value) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', 'org',  NULL, NULL, 'require_mfa', 'true'), "
                f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{DEPT_A1}', NULL, 'allowed_connectors', '[\"vmware\",\"email\"]'), "
                f"(gen_random_uuid(), '{TENANT_A}', 'user', '{DEPT_A1}', '{USER_A1}', 'session_timeout_minutes', '30'), "
                f"(gen_random_uuid(), '{TENANT_B}', 'org',  NULL, NULL, 'require_mfa', 'true')"
            )
            cur.execute(
                "INSERT INTO devices (id, tenant_id, user_id, fingerprint, status) VALUES "
                f"(%s, '{TENANT_A}', '{USER_A1}', 'fp-alice-device', 'active'), "
                f"(%s, '{TENANT_B}', '{USER_B1}', 'fp-carol-device', 'pending')",
                (DEVICE_A1, DEVICE_B1),
            )
            cur.execute(
                "INSERT INTO vault_credentials (id, tenant_id, user_id, name, credential_type, connector_slug, "
                "ciphertext_secret, ciphertext_dek, nonce, key_id) VALUES "
                f"(%s, '{TENANT_A}', '{USER_A1}', 'vmware-ro', 'api_key', 'vmware', %s, %s, %s, 'kek-v1'), "
                f"(%s, '{TENANT_B}', '{USER_B1}', 'email-imap', 'password', 'email', %s, %s, %s, 'kek-v1')",
                (VAULT_A1, _FAKE_CT, _FAKE_CT, _FAKE_CT[:12], VAULT_B1, _FAKE_CT, _FAKE_CT, _FAKE_CT[:12]),
            )
            # spec 024: conexiones personalizadas (definidas por el org_admin,
            # secreto cifrado con el mismo envelope crypto — aquí ciphertext opaco)
            cur.execute(
                "INSERT INTO vault_connections (id, tenant_id, created_by, name, kind, metadata, status, "
                "ciphertext_secret, ciphertext_dek, nonce, key_id) VALUES "
                "(%s, '" + TENANT_A + "', '" + USER_A1 + "', 'analytics-db', 'database', "
                "'{\"type\":\"postgres\",\"host\":\"db.internal\",\"port\":5432,\"database\":\"analytics\",\"username\":\"reader\"}', "
                "'active', %s, %s, %s, 'kek-v1'), "
                "(%s, '" + TENANT_B + "', '" + USER_B1 + "', 'billing-api', 'api_key', "
                "'{\"service\":\"billing\",\"auth\":\"bearer\"}', "
                "'active', %s, %s, %s, 'kek-v1')",
                (CONN_A1, _FAKE_CT, _FAKE_CT, _FAKE_CT[:12],
                 CONN_B1, _FAKE_CT, _FAKE_CT, _FAKE_CT[:12]),
            )
            cur.execute(
                "INSERT INTO connector_instances (id, tenant_id, user_id, connector_slug, mode, credential_id) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A1}', 'vmware', 'read_only', '{VAULT_A1}'), "
                f"(gen_random_uuid(), '{TENANT_B}', '{USER_B1}', 'email', NULL, '{VAULT_B1}')"
            )
            cur.execute(
                "INSERT INTO audit_log (tenant_id, actor_role, action, target_type, target_id, payload) VALUES "
                f"('{TENANT_A}', 'system', 'tenant.provisioned', 'tenant', 'tenant-a', '{{\"steps\": 3}}'), "
                f"('{TENANT_A}', 'system', 'job.created', 'job', 'j-1', '{{\"type\": \"user_provisioning\"}}'), "
                f"('{TENANT_A}', 'org_admin', 'policy.updated', 'policy', 'require_mfa', '{{\"level\": \"org\"}}'), "
                f"('{TENANT_B}', 'system', 'tenant.provisioned', 'tenant', 'tenant-b', '{{\"steps\": 2}}'), "
                f"('{TENANT_B}', 'system', 'job.created', 'job', 'j-9', '{{\"type\": \"user_provisioning\"}}')"
            )
            cur.execute(
                "INSERT INTO model_access (id, tenant_id, model_id, level, department_id, user_id, mode, status) VALUES "
                "(gen_random_uuid(), %s, (SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), 'org', NULL, NULL, 'central', 'approved'), "
                "(gen_random_uuid(), %s, (SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), 'user', %s, %s, 'byok', 'approved'), "
                "(gen_random_uuid(), %s, (SELECT id FROM catalog.model_catalog WHERE model_name = 'deepseek-v4-flash'), 'org', NULL, NULL, 'central', 'approved')",
                (TENANT_A, TENANT_A, DEPT_A1, USER_A1, TENANT_B),
            )
            cur.execute(
                "INSERT INTO api_keys (id, tenant_id, user_id, device_id, litellm_key_hash, litellm_team_id, max_budget_cents, budget_duration) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A1}', '{DEVICE_A1}', 'hash-alice-key', 'team-a1', 100000, '30d'), "
                f"(gen_random_uuid(), '{TENANT_B}', '{USER_B1}', NULL, 'hash-carol-key', 'team-b1', 50000, '30d')"
            )
            cur.execute(
                "INSERT INTO sessions (id, tenant_id, user_id, device_id, kind, jti, expires_at, mfa_verified) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A1}', '{DEVICE_A1}', 'desktop', 'jti-alice', %s, true), "
                f"(gen_random_uuid(), '{TENANT_B}', '{USER_B1}', NULL, 'admin', 'jti-carol', %s, true)",
                (datetime.now(timezone.utc) + timedelta(days=1),) * 2,
            )
            cur.execute(
                "INSERT INTO jobs (id, tenant_id, job_type, status, idempotency_key) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', 'user_provisioning', 'succeeded', 'onboard-alice'), "
                f"(gen_random_uuid(), '{TENANT_B}', 'user_provisioning', 'pending', 'onboard-carol')"
            )
            # spec 005 (governance): agentes compartidos + grants + notificaciones
            cur.execute(
                "INSERT INTO agents (id, tenant_id, owner_user_id, department_id, name, description, scope) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A1}', '{DEPT_A1}', 'ops-runbook-agent', 'Runbook assistant', 'dept'), "
                f"(gen_random_uuid(), '{TENANT_B}', '{USER_B1}', '{DEPT_B1}', 'hr-agent', 'HR helper', 'dept')"
            )
            cur.execute(
                "INSERT INTO agent_grants (id, tenant_id, agent_id, grantee_user_id, permission, granted_by) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', "
                f"(SELECT id FROM agents WHERE tenant_id = '{TENANT_A}' AND name = 'ops-runbook-agent'), "
                f"'{USER_A2}', 'invoke', '{USER_A1}'), "
                f"(gen_random_uuid(), '{TENANT_B}', "
                f"(SELECT id FROM agents WHERE tenant_id = '{TENANT_B}' AND name = 'hr-agent'), "
                f"'{USER_B1}', 'view', '{USER_B1}')"
            )
            cur.execute(
                "INSERT INTO notifications (id, tenant_id, user_id, type, payload) VALUES "
                f"(gen_random_uuid(), '{TENANT_A}', '{USER_A1}', 'budget_threshold_80', "
                "'{\"level\": \"user\", \"period_start\": \"2026-10-01\"}'), "
                f"(gen_random_uuid(), '{TENANT_B}', '{USER_B1}', 'budget_cutoff', "
                "'{\"level\": \"org\"}')"
            )
            # spec 013 (REQ-AUD-006): checkpoint de cadena por tenant.
            # Génesis (seq 0 / hash de 64 ceros): verificación equivalente
            # a sin checkpoint — el seed de audit_log arranca en seq 1.
            cur.execute(
                "INSERT INTO audit_chain_checkpoint "
                "(tenant_id, last_seq, last_hash, purged_rows) VALUES "
                f"('{TENANT_A}', 0, repeat('0', 64), 0), "
                f"('{TENANT_B}', 0, repeat('0', 64), 0)"
            )
            # spec 028 (branding REQ-BRD-004): asset + versión publicada
            # por tenant (aislamiento verificable en test_isolation).
            for tenant in (TENANT_A, TENANT_B):
                cur.execute(
                    "INSERT INTO branding_assets "
                    "(tenant_id, kind, mime, size_bytes, sha256, content) "
                    "VALUES (%s, 'logo_light', 'image/png', 8, %s, %s)",
                    (tenant, "b" * 64, b"\x89PNG\r\n\x1a\n"),
                )
                cur.execute(
                    "INSERT INTO branding_versions (tenant_id, version, data) "
                    "VALUES (%s, 1, %s)",
                    (tenant, '{"product_name": "Trust Agent"}'),
                )
            # spec 041 (políticas nombradas): una política nombrada por
            # tenant + una asignación (depto A1 para el tenant A, org para
            # el tenant B) — aislamiento verificable en test_isolation.
            for tenant, np_id, asg_id, level, dept in (
                (TENANT_A, NAMED_POLICY_A1, NAMED_ASSIGNMENT_A1, "dept",
                 DEPT_A1),
                (TENANT_B, NAMED_POLICY_B1, NAMED_ASSIGNMENT_B1, "org", None),
            ):
                cur.execute(
                    "INSERT INTO named_policies "
                    "  (id, tenant_id, name, description, definition) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (np_id, tenant, f"Política {tenant[:8]}",
                     "seed de aislamiento",
                     '{"allow_voice_mode": false}'),
                )
                cur.execute(
                    "INSERT INTO named_policy_assignments "
                    "  (id, tenant_id, named_policy_id, level, "
                    "   department_id, applied_keys) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (asg_id, tenant, np_id, level, dept,
                     '{"allow_voice_mode": false}'),
                )


@pytest.fixture(scope="session")
def testdb():
    """DB efímera vía `alembic upgrade head` + seed de dos tenants."""
    with _admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    env = dict(
        os.environ,
        DATABASE_URL=f"postgresql+psycopg://postgres:{ADMIN_PASSWORD}@{DB_HOST}:{DB_PORT}/{TEST_DB}",
    )
    proc = subprocess.run(
        [ALEMBIC, "upgrade", "head"], cwd=DB_DIR, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, f"alembic upgrade head falló:\n{proc.stderr}"
    _seed()
    yield TEST_DB
    with _admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")


@pytest.fixture()
def admin_conn(testdb):
    """Conexión como postgres (superusuario): verificación cross-tenant."""
    with _admin(TEST_DB) as conn:
        yield conn


@pytest.fixture()
def app_conn(testdb):
    """Conexión como trust_app — el rol de aplicación, sujeto a RLS."""
    conn = psycopg.connect(APP_DSN)
    yield conn
    conn.close()

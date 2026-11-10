"""Fixtures for the control plane suite.

Real infrastructure, no simulation (constitution I):
- ephemeral PostgreSQL database `trust_agent_platform_test` created via the
  CANONICAL Alembic project (platform/alembic.ini -> db/migrations, D-CP-11);
- a mock OIDC IdP serving a real JWKS over HTTP (ephemeral port) so JWT
  verification exercises the actual PyJWKClient fetch + signature check;
- two seeded tenants (A/B) with users in every RBAC role, devices, policies
  and jobs;
- a probe router generated from catalog.role_permissions (data-driven RBAC
  matrix, I10).
"""
import base64
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

PLATFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_HOST = os.environ.get("TRUST_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("TRUST_DB_PORT", "55432")
ADMIN_PASSWORD = os.environ.get("TRUST_DB_ADMIN_PASSWORD", "trustlocal")
TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")

ADMIN_DSN = f"host={DB_HOST} port={DB_PORT} user=postgres password={ADMIN_PASSWORD} dbname={{db}}"
APP_DB_URL = f"postgresql+psycopg://trust_app:trust_app_dev@{DB_HOST}:{DB_PORT}/{TEST_DB}"
MIGRATOR_DB_URL = f"postgresql+psycopg://trust_migrator:trust_migrator_dev@{DB_HOST}:{DB_PORT}/{TEST_DB}"

# Module-level env defaults: `app.main` runs `create_app()` at import time
# and create_app() is fail-closed on boot settings (DATABASE_URL +
# DEVICE_TOKEN_SECRET), but test modules are imported at collection time,
# BEFORE the session-scoped `env` fixture runs. The `env` fixture re-sets
# these and clears the settings cache, so these defaults only need to make
# collection-time imports succeed.
os.environ.setdefault("TRUST_DATABASE_URL", APP_DB_URL)
os.environ.setdefault("TRUST_DEVICE_TOKEN_SECRET", "test-device-secret")
# Same pattern for the spec 019 fail-closed bundle signing key: collection-time
# imports of app.main must succeed; the session `env` fixture sets the real
# per-session key afterwards (minimal unblock fix, t_0ed22c94).
os.environ.setdefault(
    "TRUST_POLICY_JWS_PRIVATE_KEY_B64",
    base64.b64encode(
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode(),
)

# --- Fixed seed identifiers ---------------------------------------------------
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
DEPT_A1 = "30000000-0000-0000-0000-000000000001"  # IT
DEPT_A2 = "30000000-0000-0000-0000-000000000002"  # Finance
DEPT_B1 = "30000000-0000-0000-0000-000000000003"  # Operations
USER_ALICE = "40000000-0000-0000-0000-000000000001"  # org_admin (A, dept A1)
USER_BOB = "40000000-0000-0000-0000-000000000002"    # dept_admin (A, dept A2)
USER_CAROL = "40000000-0000-0000-0000-000000000003"  # user (A, dept A1)
USER_DAVE = "40000000-0000-0000-0000-000000000004"   # user (B, dept B1)
USER_FRANK = "40000000-0000-0000-0000-000000000005"  # disabled user (A)
DEVICE_ALICE = "50000000-0000-0000-0000-000000000001"  # active
DEVICE_CAROL = "50000000-0000-0000-0000-000000000002"  # pending
JOB_A = "70000000-0000-0000-0000-000000000001"
JOB_B = "70000000-0000-0000-0000-000000000002"

SUBS = {
    "alice": "sub-alice-orgadmin",
    "bob": "sub-bob-deptadmin",
    "carol": "sub-carol-user",
    "dave": "sub-dave-user",
    "frank": "sub-frank-disabled",
}

KID = "test-rsa-1"
TEST_KEK = base64.b64encode(b"\x01" * 32).decode()

# Governance bundle signing keypair (spec 019, D-GOH-02): generated once per
# session — create_app() is fail-closed without it (validate_policy_jws_key).
# The matching public PEM is what the plugin verifier pins.
_POLICY_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
POLICY_JWS_PRIVATE_KEY_B64 = base64.b64encode(
    _POLICY_KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
).decode()
POLICY_JWS_PUBLIC_PEM = _POLICY_KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
POLICY_JWS_ISSUER = "https://trust-agent.control-plane.test/"


# =============================================================================
# Mock OIDC IdP (real HTTP + real RSA signatures)
# =============================================================================
class _IdP:
    def __init__(self) -> None:
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._private_key.public_key()))
        jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
        self.jwks = {"keys": [jwk]}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.endswith("/protocol/openid-connect/certs"):
                    body = json.dumps(outer.jwks).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def issuer(self, tenant_slug: str) -> str:
        return f"{self.base_url}/realms/{tenant_slug}"

    def make_jwt(
        self,
        *,
        sub: str,
        tenant_id: str,
        issuer: str,
        audience: str = "trust-agent-web",
        expires_in: int = 300,
        amr: list[str] | None = None,
        extra_claims: dict | None = None,
        omit_tenant_id: bool = False,
        sign_with_other_key: bool = False,
        algorithm: str = "RS256",
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": audience,
            "sub": sub,
            "exp": now + expires_in,
            "iat": now,
            "email": f"{sub}@test.local",
        }
        if not omit_tenant_id:
            claims["tenant_id"] = tenant_id
        if amr is not None:
            claims["amr"] = amr
        if extra_claims:
            claims.update(extra_claims)
        key = self._other_key if sign_with_other_key else self._private_key
        if algorithm == "HS256":
            return jwt.encode(claims, "symmetric-secret", algorithm="HS256")
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(scope="session")
def idp():
    server = _IdP()
    yield server
    server._server.shutdown()


@pytest.fixture(scope="session")
def litellm_fake():
    """DECLARED TEST DOUBLE (D-GOV-15): real HTTP server implementing the
    documented LiteLLM Admin API contract — see tests/litellm_fake.py."""
    from litellm_fake import LiteLLMFake

    server = LiteLLMFake()
    yield server
    server.shutdown()


@pytest.fixture(scope="session")
def env(idp, litellm_fake):
    """Configure TRUST_* env for the whole suite (before any settings use).

    H6 (QA t_82b2ffaa): the suite PINS its environment instead of
    inheriting it. Every TRUST_* variable from the launching shell is
    scrubbed first, then the deterministic test values below are set —
    otherwise `TRUST_COOKIE_SECURE=false` exported for a local service
    breaks test_admin_cookie_flags, and any other leaked TRUST_* silently
    changes what the suite exercises. (TRUST_DB_HOST/PORT/ADMIN_PASSWORD/
    TEST_DB are consumed at conftest import time, before this scrub, so
    they still parameterize the ephemeral database connection.)
    """
    for key in [k for k in os.environ if k.startswith("TRUST_")]:
        del os.environ[key]
    os.environ["TRUST_DATABASE_URL"] = APP_DB_URL
    os.environ["TRUST_DATABASE_MIGRATOR_URL"] = MIGRATOR_DB_URL
    os.environ["TRUST_IDP_BASE_URLS"] = f"{idp.base_url},http://127.0.0.1:8080"
    os.environ["TRUST_OIDC_AUDIENCE"] = "trust-agent-web"
    os.environ["TRUST_JWT_LEEWAY_SECONDS"] = "60"
    os.environ["TRUST_MFA_MIN_ACR"] = "2"
    os.environ["TRUST_SESSION_TTL_MINUTES"] = "60"
    os.environ["TRUST_COOKIE_NAME"] = "trust_session"
    os.environ["TRUST_COOKIE_SECURE"] = "true"
    os.environ["TRUST_DEVICE_TOKEN_SECRET"] = "test-device-secret"
    os.environ["TRUST_DEVICE_TOKEN_TTL_MINUTES"] = "15"
    os.environ["TRUST_VAULT_KEK_B64"] = TEST_KEK
    os.environ["TRUST_VAULT_KEY_ID"] = "kek-v1"
    os.environ["TRUST_LOGIN_MAX_FAILURES"] = "5"
    os.environ["TRUST_LOGIN_WINDOW_SECONDS"] = "900"
    os.environ["TRUST_LOGIN_LOCKOUT_BASE_SECONDS"] = "30"
    os.environ["TRUST_LOGIN_LOCKOUT_MAX_SECONDS"] = "86400"
    os.environ["TRUST_LITELLM_BASE_URL"] = litellm_fake.base_url
    os.environ["TRUST_LITELLM_MASTER_KEY"] = "sk-master-test"
    # Spec 023 (download bundle): the API packages the plugin from this tree
    # (in the container the Dockerfile copies it to /opt/trust-agent/...).
    os.environ["TRUST_HERMES_PLUGIN_DIR"] = os.path.join(
        PLATFORM_DIR, "hermes", "plugins",
        "trust-agent-governance",
    )
    # Governance bundle signing key (spec 019 lane, t_f99e12ab): consolidated
    # with the minimal unblock fix from t_0ed22c94 — the env fixture now sets
    # the MODULE-level per-session keypair so tests can also verify bundle
    # signatures with the matching public PEM (POLICY_JWS_PUBLIC_PEM).
    os.environ["TRUST_POLICY_JWS_PRIVATE_KEY_B64"] = POLICY_JWS_PRIVATE_KEY_B64
    os.environ["TRUST_POLICY_JWS_ISSUER"] = POLICY_JWS_ISSUER
    # Provider operations (spec 004): provider token + Keycloak Admin REST.
    os.environ["TRUST_PROVIDER_TOKEN"] = "test-provider-token"
    # Provider super-user (spec 025, mandato Miller): the client fixture's
    # create_app() bootstraps the operator with the PINNED initial password
    # (documented CI/test path of D-PS-03) so the whole suite — including
    # spec 014's break-glass tests, which fail-close without a seeded
    # operator (REQ-PS-010) — runs against a real seeded identity.
    os.environ["TRUST_PROVIDER_OPERATOR_USERNAME"] = "provider-admin"
    os.environ["TRUST_PROVIDER_OPERATOR_INITIAL_PASSWORD"] = "ProviderInitial!2026x"
    os.environ["TRUST_KEYCLOAK_ADMIN_URL"] = "http://127.0.0.1:8080"
    os.environ["TRUST_KEYCLOAK_ADMIN_USER"] = "admin"
    os.environ["TRUST_KEYCLOAK_ADMIN_PASSWORD"] = "KeycloakDemo2026"
    from app.config import get_settings
    from app.db import reset_engine

    get_settings.cache_clear()
    reset_engine()
    yield os.environ
    get_settings.cache_clear()
    reset_engine()


def _admin(db: str = "postgres"):
    return psycopg.connect(ADMIN_DSN.format(db=db), autocommit=True)


def _seed(issuer_a: str) -> None:
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_realm, oidc_issuer_url) "
            "VALUES (%s,'Tenant A','tenant-a','active','tenant-a', %s), "
            "(%s,'Tenant B','tenant-b','active','tenant-b', NULL)",
            (TENANT_A, issuer_a, TENANT_B),
        )
        cur.execute(
            "INSERT INTO departments (id, tenant_id, name) VALUES "
            f"(%s, '{TENANT_A}', 'IT'), (%s, '{TENANT_A}', 'Finance'), "
            f"(%s, '{TENANT_B}', 'Operations')",
            (DEPT_A1, DEPT_A2, DEPT_B1),
        )
        cur.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, display_name, "
            "status, mfa_enrolled, oidc_subject) VALUES "
            f"(%s, '{TENANT_A}', '{DEPT_A1}', 'alice@tenant-a.test', 'Alice', 'active', true, %s), "
            f"(%s, '{TENANT_A}', '{DEPT_A2}', 'bob@tenant-a.test', 'Bob', 'active', true, %s), "
            f"(%s, '{TENANT_A}', '{DEPT_A1}', 'carol@tenant-a.test', 'Carol', 'active', true, %s), "
            f"(%s, '{TENANT_B}', '{DEPT_B1}', 'dave@tenant-b.test', 'Dave', 'active', true, %s), "
            f"(%s, '{TENANT_A}', '{DEPT_A1}', 'frank@tenant-a.test', 'Frank', 'disabled', false, %s)",
            (USER_ALICE, SUBS["alice"], USER_BOB, SUBS["bob"], USER_CAROL,
             SUBS["carol"], USER_DAVE, SUBS["dave"], USER_FRANK, SUBS["frank"]),
        )
        cur.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key, department_id) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', '{USER_ALICE}', 'org_admin', NULL), "
            f"(gen_random_uuid(), '{TENANT_A}', '{USER_BOB}', 'dept_admin', '{DEPT_A2}'), "
            f"(gen_random_uuid(), '{TENANT_A}', '{USER_CAROL}', 'user', NULL), "
            f"(gen_random_uuid(), '{TENANT_B}', '{USER_DAVE}', 'user', NULL)"
        )
        cur.execute(
            "INSERT INTO devices (id, tenant_id, user_id, fingerprint, status) VALUES "
            f"(%s, '{TENANT_A}', '{USER_ALICE}', 'fp-alice-laptop', 'active'), "
            f"(%s, '{TENANT_A}', '{USER_CAROL}', 'fp-carol-pending', 'pending')",
            (DEVICE_ALICE, DEVICE_CAROL),
        )
        # Policies for the deny-overrides tests (AC-CP-051)
        cur.execute(
            "INSERT INTO policies (id, tenant_id, level, department_id, user_id, key, value) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', 'org', NULL, NULL, 'allowed_connectors', "
            "'[\"vmware\", \"email\"]'), "
            f"(gen_random_uuid(), '{TENANT_A}', 'org', NULL, NULL, 'require_mfa', "
            "'true'::jsonb), "
            f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{DEPT_A1}', NULL, 'allowed_connectors', "
            "'[\"vmware\"]'), "
            f"(gen_random_uuid(), '{TENANT_A}', 'user', '{DEPT_A1}', '{USER_CAROL}', "
            "'session_timeout_minutes', '30')"
        )
        cur.execute(
            "INSERT INTO jobs (id, tenant_id, job_type, status, payload, idempotency_key) VALUES "
            f"(%s, '{TENANT_A}', 'echo', 'pending', '{{\"seed\": true}}', 'seed-job-a'), "
            f"(%s, '{TENANT_B}', 'echo', 'pending', '{{\"seed\": true}}', 'seed-job-b')",
            (JOB_A, JOB_B),
        )
        # spec 005 (governance): hierarchical budgets + model access seeds.
        # Tenant A: org $10,000 / IT $4,000 / Finance $3,000;
        #           Carol (IT) $1,000; Alice (IT) $2,000.
        cur.execute(
            "INSERT INTO budgets (id, tenant_id, level, department_id, user_id, amount_cents) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', 'org',  NULL, NULL, 1000000), "
            f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{DEPT_A1}', NULL, 400000), "
            f"(gen_random_uuid(), '{TENANT_A}', 'dept', '{DEPT_A2}', NULL, 300000), "
            f"(gen_random_uuid(), '{TENANT_A}', 'user', '{DEPT_A1}', '{USER_CAROL}', 100000), "
            f"(gen_random_uuid(), '{TENANT_A}', 'user', '{DEPT_A1}', '{USER_ALICE}', 200000), "
            f"(gen_random_uuid(), '{TENANT_B}', 'org',  NULL, NULL, 500000)"
        )
        # Model access: org A approves two catalog models (central);
        # dept IT narrows to glm-5.2 only; Carol has a user-level byok row.
        # (Nombres del catálogo MaaS real — spec 018 / 0009_model_catalog_maas;
        # fix mínimo del lane spec 020 porque los nombres genéricos fueron
        # retirados del catálogo y el seed reventaba con model_id NULL.)
        cur.execute(
            "INSERT INTO model_access (id, tenant_id, model_id, level, department_id, user_id, mode, status) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), "
            "'org', NULL, NULL, 'central', 'approved'), "
            f"(gen_random_uuid(), '{TENANT_A}', "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = 'deepseek-v4-flash'), "
            "'org', NULL, NULL, 'central', 'approved'), "
            f"(gen_random_uuid(), '{TENANT_A}', "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), "
            f"'dept', '{DEPT_A1}', NULL, 'central', 'approved'), "
            f"(gen_random_uuid(), '{TENANT_A}', "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), "
            f"'user', '{DEPT_A1}', '{USER_CAROL}', 'byok', 'approved'), "
            f"(gen_random_uuid(), '{TENANT_B}', "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = 'glm-5.2'), "
            "'org', NULL, NULL, 'central', 'approved')"
        )


@pytest.fixture(scope="session")
def db(env, idp):
    """Ephemeral DB via the canonical Alembic project + two-tenant seed."""
    with _admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    proc = subprocess.run(
        [os.path.join(PLATFORM_DIR, ".venv", "bin", "alembic"), "-c", "alembic.ini",
         "upgrade", "head"],
        cwd=PLATFORM_DIR,
        env=dict(os.environ,
                 DATABASE_URL=f"postgresql+psycopg://postgres:{ADMIN_PASSWORD}@{DB_HOST}:{DB_PORT}/{TEST_DB}"),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"alembic upgrade head failed:\n{proc.stderr}"
    _seed(idp.issuer("tenant-a"))
    yield TEST_DB
    # Dispose the app engine and terminate leftover test connections so the
    # ephemeral database can be dropped cleanly.
    from app.db import reset_engine

    reset_engine()
    with _admin() as c:
        c.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (TEST_DB,)
        )
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")


@pytest.fixture(scope="session")
def catalog_permissions(db):
    """Permission keys from the catalog (data-driven RBAC matrix, I10)."""
    with _admin(TEST_DB) as c:
        rows = c.execute("SELECT key FROM catalog.permissions ORDER BY key").fetchall()
    return [r[0] for r in rows]


@pytest.fixture(scope="session")
def client(db, catalog_permissions):
    """TestClient with the probe router generated from the catalog matrix."""
    from app.auth.rbac import require_permission, require_role
    from app.auth.service import get_principal, require_mfa
    from app.devices.gate import require_device
    from app.main import create_app

    app = create_app()
    probe = APIRouter()

    @probe.get("/api/_probe/whoami", dependencies=[Depends(require_mfa)])
    def probe_whoami(principal=Depends(get_principal)):
        return {"user_id": principal.user_id}

    @probe.get("/api/_probe/device",
               dependencies=[Depends(require_mfa), Depends(require_device)])
    def probe_device(principal=Depends(get_principal)):
        return {"device_ok": True, "user_id": principal.user_id}

    @probe.post("/api/_probe/csrf", dependencies=[Depends(require_mfa)])
    def probe_csrf(principal=Depends(get_principal)):
        return {"mutated": True}

    for permission in catalog_permissions:
        def _make(perm):
            @probe.get(f"/api/_probe/perm/{perm}",
                       dependencies=[Depends(require_mfa),
                                     Depends(require_permission(perm))])
            def _endpoint(principal=Depends(get_principal)):
                return {"permission": perm}
            return _endpoint
        _make(permission)

    app.include_router(probe)
    with TestClient(app) as c:
        yield c


# =============================================================================
# Helpers
# =============================================================================
def mint(client, idp, user, *, tenant=TENANT_A, kind="desktop", amr=("mfa",),
         audience="trust-agent-web", extra_claims=None):
    """Mint a session through the real API with a mock-IdP JWT."""
    issuer = idp.issuer("tenant-a" if tenant == TENANT_A else "tenant-b")
    token = idp.make_jwt(
        sub=SUBS[user], tenant_id=tenant, issuer=issuer,
        audience=audience, amr=list(amr) if amr is not None else None,
        extra_claims=extra_claims,
    )
    resp = client.post("/api/auth/session", json={"id_token": token, "kind": kind})
    assert resp.status_code == 201, resp.text
    return resp.json()


def bearer_headers(tokens):
    return {"Authorization": f"Bearer {tokens['session_token']}"}


@pytest.fixture(scope="session")
def admin_conn(db):
    """Superuser connection for cross-tenant verification."""
    conn = _admin(TEST_DB)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def ledger_delta_sandbox(admin_conn, litellm_fake):
    """Sandbox de módulo para suites que escriben en el ledger compartido
    (t_e000bf40). La DB de sesión la comparte toda la suite: un módulo
    que ingesta filas de HOY en spend_ledger (vía el worker o ingest
    directo) contamina los conteos EXACTOS sobre la ventana 'today'/30d
    del tenant que hacen otras suites (p.ej. test_usage_analytics:
    carol.requests == 2, total == 5, ids == {CAROL, BOB}...).

    Uso (opt-in, cubre todo el módulo):
        pytestmark = pytest.mark.usefixtures("ledger_delta_sandbox")

    Al entrar: snapshot de los request_id presentes (tenants A/B), de
    budget_counters y de los logs del doble (session-scoped). Al salir
    del módulo: borrar SOLO el delta de filas creadas aquí — el doble
    genera request_id aleatorio (uuid4) por chat, sin prefijo, así que
    las filas propias se identifican por DIFERENCIA contra el snapshot,
    no por patrón — restaurar los counters al snapshot exacto (los
    counters son derivados del ledger) y REMOVER los logs que el módulo
    añadió al doble. Sin ese último paso cualquier worker run de una
    suite posterior (p.ej. test_tenant_self_signup, que corre antes que
    test_usage_analytics) RE-INGESTA las filas borradas: el cursor del
    worker es de granularidad diaria y vuelve a escanear los logs de
    hoy, y el ledger es idempotente por request_id solo mientras la
    fila existe. Las filas/logs preexistentes de otros módulos no se
    tocan.
    """
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT request_id FROM spend_ledger "
            f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}')"
        )
        before = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT tenant_id, level, entity_id, period_start, period_end, "
            "spent_cents, budget_cents FROM budget_counters "
            f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}')"
        )
        counters = cur.fetchall()
    logs_snapshot = list(litellm_fake.logs)
    yield
    litellm_fake.logs[:] = logs_snapshot
    with admin_conn.cursor() as cur:
        if before:
            # psycopg 3 no adapta una tupla para `IN %s` (idioma de
            # psycopg2): la lista se pasa como array y se compara con ALL.
            cur.execute(
                "DELETE FROM spend_ledger "
                f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}') "
                "AND request_id <> ALL(%s)",
                (list(before),),
            )
        else:
            cur.execute(
                "DELETE FROM spend_ledger "
                f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}')"
            )
        cur.execute(
            "DELETE FROM budget_counters "
            f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}')"
        )
        if counters:
            cur.executemany(
                "INSERT INTO budget_counters (tenant_id, level, entity_id, "
                "period_start, period_end, spent_cents, budget_cents) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                counters,
            )
    admin_conn.commit()

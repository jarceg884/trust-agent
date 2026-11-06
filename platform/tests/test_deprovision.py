"""Deprovisioning de tenants (t_4f4b0eb1, repro Miller) — spec coverage.

Dos niveles (constitution I — nada simulado sin declararlo):

  1. Servicio contra PostgreSQL real (DB efímera de la suite): se crea un
     tenant COMPLETO vía _provision_in_db (la misma pieza que usa el
     provisioner, sin Keycloak) y se borra vía deprovision() con un stub
     de KC declarado. Cubre: cascade por tenant_id en TODAS las tablas,
     fail-closed del confirm, 404, audit tenant.deleted en
     ops.provider_audit, y el warning de realm huérfano.
  2. API end-to-end contra Keycloak 26 real (skip si no está en
     127.0.0.1:8080): provision REAL → DELETE REAL → realm borrado,
     permiso data-driven (quitar platform_admin -> tenants.delete de
     catalog.role_permissions debe negar el endpoint, D-ONB-02).
"""
import http.client
import json
import uuid

import pytest

PROVIDER = {"Authorization": "Bearer test-provider-token"}
SLUG = "del-corp"
ADMIN_EMAIL = "root@del-corp.test"

_state = {}  # compartido entre los pasos ordenados del módulo


def _kc_available() -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=3)
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return True
    except OSError:
        return False


KC_UP = _kc_available()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StubKc:
    """Doble declarado del cliente Admin REST: registra los deletes."""

    def __init__(self, fail: bool = False):
        self.deleted: list[str] = []
        self.fail = fail

    def delete_realm(self, realm: str) -> None:
        from app.tenants.keycloak_admin import KeycloakAdminError

        if self.fail:
            raise KeycloakAdminError(
                "keycloak.rollback", "stub: realm delete failed"
            )
        self.deleted.append(realm)


def _make_tenant(slug: str = SLUG) -> str:
    """Crea un tenant completo vía la MISMA pieza del provisioner (sin KC)."""
    from app.db import get_engine
    from app.tenants.provision import ProvisionRequest, _provision_in_db

    tenant_id = str(uuid.uuid4())
    _provision_in_db(
        get_engine(),
        request=ProvisionRequest(
            organization_name="Delete Corp",
            admin_email=ADMIN_EMAIL,
            initial_budget_usd=50.0,
        ),
        slug=slug,
        tenant_id=tenant_id,
        issuer=f"http://idp/realms/{slug}",
        kc_user_uuid=str(uuid.uuid4()),
        # spec 038 (D-38-01): born with the DECLARED departments or empty.
        dept_specs=[],
    )
    return tenant_id


_TENANT_TABLES = [
    "departments", "users", "user_roles", "budgets", "budget_counters",
    "spend_ledger", "policies", "devices", "vault_credentials",
    "connector_instances", "audit_log", "model_access", "api_keys",
    "sessions", "jobs", "agents", "agent_grants", "notifications",
    "audit_chain_checkpoint", "vault_connections", "branding_assets",
    "branding_versions",
]


def _tenant_rows(admin_conn, tenant_id: str) -> dict:
    cur = admin_conn.cursor()
    total = 0
    per_table = {}
    for table in _TENANT_TABLES:
        cur.execute(f"SELECT count(*) FROM {table} WHERE tenant_id = %s",
                    (tenant_id,))
        n = cur.fetchone()[0]
        per_table[table] = n
        total += n
    cur.close()
    per_table["_total"] = total
    return per_table


def _slug_exists(admin_conn, slug: str) -> bool:
    with admin_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM tenants WHERE slug = %s", (slug,))
        return cur.fetchone() is not None


def _last_provider_audit(admin_conn, action: str):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT action, payload FROM ops.provider_audit "
            "WHERE action = %s ORDER BY id DESC LIMIT 1", (action,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        # psycopg ya decodifica jsonb a dict.
        payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        return (row[0], payload)


# ---------------------------------------------------------------------------
# 1. Servicio (PostgreSQL real, sin Keycloak)
# ---------------------------------------------------------------------------

def test_deprovision_removes_every_row_and_audits(client, admin_conn):
    from app.db import get_migrator_engine
    from app.tenants.deprovision import deprovision

    tenant_id = _make_tenant()
    before = _tenant_rows(admin_conn, tenant_id)
    assert before["_total"] > 0, "fixture: the tenant must have rows"
    # El provisioner emitió eventos de audit del tenant (chain real).
    assert before["audit_log"] > 0

    kc = StubKc()
    result = deprovision(get_migrator_engine(), kc, SLUG, SLUG)

    assert result["deleted"] is True
    assert result["realm_deleted"] is True
    assert kc.deleted == [SLUG]
    # TODAS las filas del tenant se fueron (cascade por tenant_id).
    assert _tenant_rows(admin_conn, tenant_id)["_total"] == 0
    assert not _slug_exists(admin_conn, SLUG)
    # El evento durable vive en el plano proveedor (audit_log se borra
    # CON el tenant — no puede vivir ahí).
    event = _last_provider_audit(admin_conn, "tenant.deleted")
    assert event is not None
    assert event[1]["slug"] == SLUG
    assert event[1]["removed"]["tenants"] == 1
    # Los tenants seed (A/B) quedan intactos.
    assert _slug_exists(admin_conn, "tenant-a")
    assert _slug_exists(admin_conn, "tenant-b")


def test_deprovision_fail_closed_on_confirm_mismatch(client, admin_conn):
    from app.db import get_migrator_engine
    from app.errors import ApiError
    from app.tenants.deprovision import deprovision

    tenant_id = _make_tenant("del-confirm")
    before = _tenant_rows(admin_conn, tenant_id)

    with pytest.raises(ApiError) as excinfo:
        deprovision(get_migrator_engine(), StubKc(), "del-confirm", "del-conf")
    assert excinfo.value.status_code == 422
    # Fail-closed: nada se tocó.
    assert _tenant_rows(admin_conn, tenant_id) == before
    assert _slug_exists(admin_conn, "del-confirm")

    # Limpieza del fixture (usa el camino real bajo prueba).
    from app.tenants.deprovision import deprovision as dep
    dep(get_migrator_engine(), StubKc(), "del-confirm", "del-confirm")
    assert not _slug_exists(admin_conn, "del-confirm")


def test_deprovision_tenant_with_byok_catalog_rows(client, admin_conn):
    """Regresión del barrido real: las filas BYOK del tenant viven en el
    catálogo GLOBAL catalog.model_catalog y su FK connection_id →
    vault_connections es ON DELETE SET NULL — el SET NULL viola
    model_catalog_source_scope_check (byok exige connection). Sin el
    borrado del catálogo del tenant ANTES de vault_connections, el
    deprovisioning revienta con CheckViolation (encontrado con los
    dyn-disc-* de la DB real)."""
    from app.db import get_migrator_engine
    from app.tenants.deprovision import deprovision

    tenant_id = _make_tenant("del-byok")
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (tenant_id,)
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO vault_connections (id, tenant_id, created_by, name, "
            "kind, ciphertext_secret, ciphertext_dek, nonce, key_id) "
            "VALUES (%s, %s, %s, 'llm-byok', 'llm', %s, %s, %s, 'test-kek')",
            (str(uuid.uuid4()), tenant_id, user_id, b"s", b"k", b"n"),
        )
        cur.execute("SELECT id FROM vault_connections WHERE tenant_id = %s",
                    (tenant_id,))
        conn_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO catalog.model_catalog (id, model_name, display_name, "
            "provider, source, status, tenant_id, connection_id) "
            "VALUES (%s, %s, 'GLM-5.2', 'llm-byok', 'byok', 'available', %s, %s)",
            (str(uuid.uuid4()), f"byok:{conn_id}:glm-5.2", tenant_id, conn_id),
        )

    result = deprovision(get_migrator_engine(), StubKc(), "del-byok",
                         "del-byok")
    assert result["deleted"] is True
    # La fila byok del catálogo global murió con el tenant (no queda
    # huérfana apuntando a una conexión inexistente).
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM catalog.model_catalog WHERE tenant_id = %s",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM vault_connections WHERE tenant_id = %s",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 0
    assert result["removed"]["catalog.model_catalog"] == 1


def test_deprovision_unknown_slug_is_404(client):
    from app.db import get_migrator_engine
    from app.errors import ApiError
    from app.tenants.deprovision import deprovision

    with pytest.raises(ApiError) as excinfo:
        deprovision(get_migrator_engine(), StubKc(), "no-such-tenant",
                    "no-such-tenant")
    assert excinfo.value.status_code == 404


def test_deprovision_realm_failure_leaves_warning(client, admin_conn):
    """El borrado de DB SUCEDIÓ; el realm huérfano se reporta honestamente
    (nada simulado): deleted=true, realm_deleted=false, warning explícito."""
    from app.db import get_migrator_engine
    from app.tenants.deprovision import deprovision

    _make_tenant("del-realmfail")
    result = deprovision(
        get_migrator_engine(), StubKc(fail=True), "del-realmfail",
        "del-realmfail",
    )
    assert result["deleted"] is True
    assert result["realm_deleted"] is False
    assert "could NOT be removed" in result["warning"]
    assert not _slug_exists(admin_conn, "del-realmfail")


# ---------------------------------------------------------------------------
# 2. API end-to-end (Keycloak 26 real) — permiso data-driven + confirm
# ---------------------------------------------------------------------------

pytestmark_api = pytest.mark.skipif(
    not KC_UP, reason="Keycloak local (127.0.0.1:8080) not reachable"
)


@pytestmark_api
def test_delete_api_full_flow(client, admin_conn):
    # --- permiso data-driven (D-ONB-02): sin la fila, el endpoint niega.
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM catalog.role_permissions "
            "WHERE role_key = 'platform_admin' AND permission_key = 'tenants.delete'"
        )
    resp = client.request(
        "DELETE", f"/api/admin/tenants/{SLUG}",
        headers=PROVIDER, json={"confirm": SLUG},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["reason"] == "permission_denied"
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO catalog.role_permissions (role_key, permission_key) "
            "VALUES ('platform_admin', 'tenants.delete') "
            "ON CONFLICT DO NOTHING"
        )

    # --- fixture: tenant REAL (Keycloak + PostgreSQL). Slug explícito:
    # sin él, provision() usaría slugify("Delete Corp") = "delete-corp".
    resp = client.post(
        "/api/admin/tenants",
        headers=PROVIDER,
        json={
            "organization_name": "Delete Corp",
            "admin_email": ADMIN_EMAIL,
            "initial_budget_usd": 10.0,
            "slug": SLUG,
        },
    )
    assert resp.status_code == 201, resp.text
    _state["tenant_id"] = resp.json()["tenant"]["id"]

    # --- fail-closed: confirm erróneo no toca nada.
    resp = client.request(
        "DELETE", f"/api/admin/tenants/{SLUG}", headers=PROVIDER,
        json={"confirm": "wrong-slug"},
    )
    assert resp.status_code == 422, resp.text
    assert _slug_exists(admin_conn, SLUG)

    # --- sin auth: 401.
    resp = client.request(
        "DELETE", f"/api/admin/tenants/{SLUG}", json={"confirm": SLUG}
    )
    assert resp.status_code == 401, resp.text

    # --- DELETE real: 200, filas + realm fuera.
    resp = client.request(
        "DELETE", f"/api/admin/tenants/{SLUG}", headers=PROVIDER,
        json={"confirm": SLUG},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["realm_deleted"] is True
    assert body["warning"] is None
    assert not _slug_exists(admin_conn, SLUG)
    assert _tenant_rows(admin_conn, _state["tenant_id"])["_total"] == 0

    # El realm de Keycloak desapareció de verdad.
    from app.config import get_settings
    from app.tenants.keycloak_admin import KeycloakAdminClient

    kc = KeycloakAdminClient(get_settings())
    assert kc.realm_exists(SLUG) is False

    # El evento durable quedó en ops.provider_audit (vía break-glass).
    event = _last_provider_audit(admin_conn, "tenant.deleted")
    assert event is not None and event[1]["slug"] == SLUG
    assert event[1]["via"] == "break-glass"

    # Idempotencia de ausencia: borrar de nuevo es 404, no 500.
    resp = client.request(
        "DELETE", f"/api/admin/tenants/{SLUG}", headers=PROVIDER,
        json={"confirm": SLUG},
    )
    assert resp.status_code == 404, resp.text


@pytestmark_api
def test_delete_api_requires_provider_auth(client, idp):
    """Un token de SESSION de tenant (no provider) no puede borrar nada."""
    from tests.conftest import mint

    tokens = mint(client, idp, "alice")
    resp = client.request(
        "DELETE", "/api/admin/tenants/tenant-a",
        headers={"Authorization": f"Bearer {tokens['session_token']}"},
        json={"confirm": "tenant-a"},
    )
    assert resp.status_code in (401, 403), resp.text

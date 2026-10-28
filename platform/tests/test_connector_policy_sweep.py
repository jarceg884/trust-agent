"""AC-013-005 (REQ-CON-005, spec 013 D-013-02) — desconexión proactiva.

Cambiar allowed_connectors / messaging_permissions a nivel org, depto o
usuario corta EN LA MISMA transacción las instancias activas que la nueva
política efectiva ya no permite: instancia → disconnected, notificación de
inbox al dueño, `connector.disconnected` auditado (reason policy_updated).
El depto vecino no se afecta.
"""
from conftest import (
    DEPT_A2, TENANT_A, USER_BOB, USER_CAROL, _admin, bearer_headers, mint,
)

import os  # noqa: E402 (honor TRUST_TEST_DB como tests/conftest.py)
import pytest  # noqa: E402

TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")


@pytest.fixture(scope="module", autouse=True)
def _restore_seed_connector_instances():
    """Hermeticidad (t_710f0acc): los tests de este archivo dejan instancias
    de conector para Carol/Bob que el seed no tiene (la de vmware de Carol
    queda ACTIVA). Sin limpieza, la revocación posterior de un device de
    Carol (test_devices_flow) dispara el sweep de conectores de la
    revocación integral (REQ-GOH-011) y deja una notificación
    connector_disconnected en su inbox, que rompe test_spend_cutoff::
    test_notifications_endpoint_scoped (inbox solo de tipos budget)."""
    yield
    with _admin(TEST_DB) as c:
        c.execute(
            "DELETE FROM connector_instances "
            "WHERE tenant_id = %s AND user_id IN (%s, %s)",
            (TENANT_A, USER_CAROL, USER_BOB),
        )


def _put_policy(client, path, key, value, tokens):
    return client.put(path, json={"key": key, "value": value},
                      headers=bearer_headers(tokens))


def _create_instance(client, tokens, slug):
    return client.post("/api/connectors/instances",
                       json={"connector_slug": slug},
                       headers=bearer_headers(tokens))


def _ensure_instance(client, tokens, slug):
    """200 (reactivada) / 201 (nueva) / 409 (ya activa de un test anterior)."""
    resp = _create_instance(client, tokens, slug)
    assert resp.status_code in (200, 201, 409), resp.text
    return resp


def _instance_status(admin_conn, user_id, slug):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM connector_instances "
            f"WHERE tenant_id = '{TENANT_A}' AND user_id = %s "
            "AND connector_slug = %s",
            (user_id, slug),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _notifications(admin_conn, user_id, type_):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM notifications "
            f"WHERE tenant_id = '{TENANT_A}' AND user_id = %s AND type = %s "
            "ORDER BY created_at",
            (user_id, type_),
        )
        return cur.fetchall()


def _sweep_audit(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT target_id, payload FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'connector.disconnected' "
            "AND payload->>'reason' = 'policy_updated'"
        )
        return cur.fetchall()


def test_org_policy_change_sweeps_instances(client, idp, admin_conn):
    """AC-CON-005 a nivel org: quitar email del allow-list org corta la
    instancia de email de Bob (depto A2) y NO toca la de vmware de Carol
    (depto A1, depto vecino)."""
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")

    # Seed: org allowed_connectors = [vmware, email]; depto A1 = [vmware].
    # Bob (A2, sin política de depto) puede usar email; Carol solo vmware.
    resp = _create_instance(client, bob, "email")
    assert resp.status_code == 201, resp.text
    resp = _create_instance(client, carol, "vmware")
    assert resp.status_code == 201, resp.text

    # Org quita email → barrido síncrono en la misma transacción.
    resp = _put_policy(client, "/api/policies/org",
                       "allowed_connectors", ["vmware"], alice)
    assert resp.status_code == 200, resp.text

    assert _instance_status(admin_conn, USER_BOB, "email") == "disconnected"
    assert _instance_status(admin_conn, USER_CAROL, "vmware") == "active"

    # Notificación al dueño afectado (el vecino no necesita notificación).
    notes = _notifications(admin_conn, USER_BOB, "connector_disconnected")
    assert notes and notes[-1][0]["connector_slug"] == "email"
    assert notes[-1][0]["policy_level"] == "org"

    # Audit del corte con reason=policy_updated (contrato spec 008 §5).
    events = _sweep_audit(admin_conn)
    assert events, "connector.disconnected(policy_updated) debe quedar auditado"

    # Limpieza: restaurar el allow-list org del seed.
    resp = _put_policy(client, "/api/policies/org",
                       "allowed_connectors", ["vmware", "email"], alice)
    assert resp.status_code == 200, resp.text


def test_dept_policy_change_scopes_to_department(client, idp, admin_conn):
    """AC-CON-005 a nivel depto: A2 restringe a [vmware] → solo Bob (A2)
    se corta; Carol (A1) no se afecta."""
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")

    # Reactivada tras el test org (200 reactivate / 201 nueva / 409 activa).
    _ensure_instance(client, bob, "email")
    _ensure_instance(client, carol, "vmware")

    resp = _put_policy(client, f"/api/policies/dept/{DEPT_A2}",
                       "allowed_connectors", ["vmware"], alice)
    assert resp.status_code == 200, resp.text

    assert _instance_status(admin_conn, USER_BOB, "email") == "disconnected"
    assert _instance_status(admin_conn, USER_CAROL, "vmware") == "active"
    notes = _notifications(admin_conn, USER_BOB, "connector_disconnected")
    assert notes and notes[-1][0]["policy_level"] == "dept"

    # Limpieza: desactivar la política de depto creada por el test.
    resp = client.delete(f"/api/policies/dept/allowed_connectors"
                         f"?department_id={DEPT_A2}",
                         headers=bearer_headers(alice))
    assert resp.status_code == 204, resp.text


def test_user_policy_change_only_affects_that_user(client, idp, admin_conn):
    """Nivel usuario: la política de Bob no toca instancias de Carol."""
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")

    _ensure_instance(client, bob, "email")
    _ensure_instance(client, carol, "vmware")

    resp = _put_policy(client, f"/api/policies/user/{USER_BOB}",
                       "allowed_connectors", [], alice)
    assert resp.status_code == 200, resp.text

    assert _instance_status(admin_conn, USER_BOB, "email") == "disconnected"
    assert _instance_status(admin_conn, USER_CAROL, "vmware") == "active"

    # Limpieza (la política user vive con department_id resuelto).
    resp = client.delete(f"/api/policies/user/allowed_connectors"
                         f"?user_id={USER_BOB}&department_id={DEPT_A2}",
                         headers=bearer_headers(alice))
    assert resp.status_code == 204, resp.text


def test_messaging_permission_sweep(client, idp, admin_conn):
    """Rama messaging: whatsapp permitido → instancia activa; allowed=false
    → el barrido la corta (misma regla que _check_instance_governance)."""
    bob = mint(client, idp, "bob")
    alice = mint(client, idp, "alice")

    resp = _put_policy(client, "/api/policies/org", "allowed_connectors",
                       ["vmware", "email", "whatsapp"], alice)
    assert resp.status_code == 200, resp.text
    resp = _put_policy(client, "/api/policies/org", "messaging_permissions",
                       {"whatsapp": {"allowed": True, "mode": "read_only"}},
                       alice)
    assert resp.status_code == 200, resp.text

    _ensure_instance(client, bob, "whatsapp")
    assert _instance_status(admin_conn, USER_BOB, "whatsapp") == "active"

    resp = _put_policy(client, "/api/policies/org", "messaging_permissions",
                       {"whatsapp": {"allowed": False}}, alice)
    assert resp.status_code == 200, resp.text
    assert _instance_status(admin_conn, USER_BOB, "whatsapp") == "disconnected"
    notes = _notifications(admin_conn, USER_BOB, "connector_disconnected")
    assert notes and notes[-1][0]["connector_slug"] == "whatsapp"

    # Limpieza: restaurar el seed (allow-list org sin whatsapp, sin mapa).
    resp = client.delete("/api/policies/org/messaging_permissions",
                         headers=bearer_headers(alice))
    assert resp.status_code == 204, resp.text
    resp = _put_policy(client, "/api/policies/org",
                       "allowed_connectors", ["vmware", "email"], alice)
    assert resp.status_code == 200, resp.text

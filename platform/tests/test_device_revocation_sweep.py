"""Spec 019 Incremento 4 — revocación integral (REQ-GOH-011, AC-GOH-011).

Al revocar un dispositivo ACTIVO, en la MISMA transacción mueren además
del device token y las sesiones:
  (a) la virtual key de LiteLLM del usuario (/key/block por hash → la
      invocación con la key retenida → 401 del gateway), y
  (b) las instancias de conectores activas del usuario (primitive de
      corte compartida → disconnected, audit connector.disconnected,
      notificación de inbox).

Fail-closed: si el gateway no puede bloquear la key, la revocación NO se
confirma (503 + rollback — el dispositivo sigue active). Un dispositivo
pending revocado NO dispara el sweep (nunca tuvo acceso real). El
re-enrolamiento por fingerprint sigue funcionando después de la
revocación integral (spec 007 upsert).
"""
import uuid

import psycopg
import pytest
from tests.conftest import (
    TENANT_A, TEST_DB, USER_CAROL, _admin, bearer_headers, mint,
)
from tests.test_devices_flow import _enroll, _session_id

MODEL = "glm-5.2"  # aprobado para el depto A1 (seed de conftest)

# Sandbox de módulo (t_e000bf40): este módulo chatea como Carol (tenant A)
# contra el doble; esos logs, ingesta cualquier worker posterior como filas
# de HOY del tenant A y rompen los conteos EXACTOS de la ventana 'today'
# en test_usage_analytics. El fixture (conftest) remueve los logs del
# doble y borra el delta de filas del ledger al salir del módulo.
pytestmark = pytest.mark.usefixtures("ledger_delta_sandbox")


@pytest.fixture(scope="module", autouse=True)
def _clean_carol_sweep_notifications():
    """Hermeticidad (t_710f0acc): la revocación integral de devices de
    Carol deja en su inbox notificaciones connector_disconnected (reason
    device_revoked) que este archivo afirma (L~197) pero no limpia.
    test_spend_cutoff::test_notifications_endpoint_scoped exige un inbox
    solo de tipos budget → borrarlas al salir del módulo."""
    yield
    with _admin(TEST_DB) as c:
        c.execute(
            "DELETE FROM notifications "
            "WHERE tenant_id = %s AND user_id = %s "
            "AND type = 'connector_disconnected'",
            (TENANT_A, USER_CAROL),
        )


def _q(admin_conn, sql, params=None):
    with admin_conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _issue_key(client, tokens):
    resp = client.post("/api/models/keys", json={}, headers=bearer_headers(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _key_row(admin_conn, key_id):
    return _q(
        admin_conn,
        "SELECT id, alias, status FROM api_keys WHERE id = %s", (key_id,),
    )[0]


def _instance_status(admin_conn, user_id, slug):
    rows = _q(
        admin_conn,
        "SELECT status FROM connector_instances "
        "WHERE tenant_id = %s AND user_id = %s AND connector_slug = %s",
        (TENANT_A, user_id, slug),
    )
    return rows[0]["status"] if rows else None


def _audit(admin_conn, action, device_id):
    return _q(
        admin_conn,
        "SELECT target_type, target_id, payload FROM audit_log "
        "WHERE tenant_id = %s AND action = %s AND device_id = %s "
        "ORDER BY seq",
        (TENANT_A, action, device_id),
    )


def _full_device(client, idp, fingerprint, *, user="carol"):
    """enroll → approve → exchange -> (device_id, device_token)."""
    enrolled = _enroll(client, idp, fingerprint, user=user).json()
    device_id = enrolled["device_id"]
    alice = mint(client, idp, "alice")
    resp = client.post(f"/api/devices/{device_id}/approve",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token": enrolled["enrollment_token"]})
    assert resp.status_code == 200, resp.text
    return device_id, resp.json()["device_token"]


def _reset_carol_control_plane_state(admin_conn):
    """Hermetic setup: the suite shares ONE ephemeral database, so earlier
    test files may leave Carol with ACTIVE keys / vmware instances. The
    sweep assertions below are exact (== [key_id] / == [instance_id]), so
    any pre-existing ACTIVE grant of hers would both break them and get
    killed by the revoke under test. Deactivate that leftover state first.
    """
    admin_conn.execute(
        "UPDATE api_keys SET status = 'revoked', updated_at = now() "
        "WHERE user_id = %s AND status = 'active'",
        (USER_CAROL,),
    )
    admin_conn.execute(
        "UPDATE connector_instances SET status = 'disconnected', "
        "updated_at = now() "
        "WHERE user_id = %s AND connector_slug = 'vmware' "
        "AND status = 'active'",
        (USER_CAROL,),
    )


def test_revoke_sweeps_virtual_key_and_connectors(
    client, idp, admin_conn, litellm_fake
):
    """AC-GOH-011: revocar un device activo con key + conector activo
    corta AMBOS accesos del lado del control plane, en la misma
    transacción, con audit del resumen y cadena íntegra."""
    fp = f"fp-sweep-{uuid.uuid4().hex[:8]}"
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")
    _reset_carol_control_plane_state(admin_conn)

    # 1. key virtual real (plaintext UNA vez) → la data plane la acepta.
    key = _issue_key(client, carol)
    status, _body = litellm_fake.chat(key["key"], MODEL)
    assert status == 200, "pre-condición: la key recién emitida invoca"

    # 2. credencial vault + instancia de conector activas (vmware, A1).
    resp = client.post(
        "/api/vault/credentials",
        json={"name": f"sweep-cred-{fp}", "credential_type": "api_key",
              "secret_value": "vcred-secret-1", "connector_slug": "vmware",
              "scope": "read_only"},
        headers=bearer_headers(carol),
    )
    assert resp.status_code == 201, resp.text
    credential_id = resp.json()["credential_id"]
    resp = client.post(
        "/api/connectors/instances",
        json={"connector_slug": "vmware", "credential_id": credential_id},
        headers=bearer_headers(carol),
    )
    # 201 = new instance; 200 = upsert re-activated the (unique) existing
    # disconnected row for (user, connector) — either way the response IS
    # the single ACTIVE vmware instance the sweep must disconnect.
    assert resp.status_code in (200, 201), resp.text
    instance_id = resp.json()["id"]
    # el canal de inyección runtime funciona antes de la revocación
    resp = client.post(f"/api/vault/credentials/{credential_id}/use",
                       headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text

    # 3. dispositivo enrolado, aprobado y canjeado; sesión ligada al device.
    device_id, device_token = _full_device(client, idp, fp)
    session_id = _session_id(carol)
    admin_conn.execute(
        "UPDATE sessions SET device_id = %s WHERE id = %s",
        (device_id, session_id),
    )

    # 4. revocación integral por el admin.
    resp = client.post(f"/api/devices/{device_id}/revoke",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "revoked"
    sweep = body["sweep"]
    assert [k["key_id"] for k in sweep["keys_blocked"]] == [key["key_id"]]
    assert [c["instance_id"] for c in sweep["connectors_disconnected"]] == \
        [instance_id]

    # 5. (a) la virtual key murió en el gateway: invocar con la key
    #       retenida → 401 (aunque el usuario local la conserve).
    assert _key_row(admin_conn, key["key_id"])["status"] == "blocked"
    status, body401 = litellm_fake.chat(key["key"], MODEL)
    assert status == 401, f"la key bloqueada debe rebotar: {status} {body401}"

    # 6. (b) el conector murió: instancia disconnected; el uso con la
    #       sesión del dispositivo → 401 (sesión revocada); con una
    #       sesión fresca → 409 (sin instancia activa).
    assert _instance_status(admin_conn, USER_CAROL, "vmware") == "disconnected"
    resp = client.post(f"/api/vault/credentials/{credential_id}/use",
                       headers=bearer_headers(carol))
    assert resp.status_code == 401, "la sesión ligada al device murió"
    carol_fresh = mint(client, idp, "carol")
    resp = client.post(f"/api/vault/credentials/{credential_id}/use",
                       headers=bearer_headers(carol_fresh))
    assert resp.status_code == 409, "sin instancia activa no hay inyección"

    # 7. audit: device.revoked porta el resumen del sweep + eventos
    #    model.key_blocked / connector.disconnected (reason device_revoked)
    #    + notificación de inbox + cadena íntegra.
    revoked_ev = _audit(admin_conn, "device.revoked", device_id)
    assert len(revoked_ev) == 1
    payload = revoked_ev[0]["payload"]
    assert payload["sweep"]["keys_blocked"][0]["alias"] == key["alias"]
    assert payload["sweep"]["connectors_disconnected"][0][
        "connector_slug"] == "vmware"
    key_ev = _audit(admin_conn, "model.key_blocked", device_id)
    assert len(key_ev) == 1
    assert key_ev[0]["payload"]["reason"] == "device_revoked"
    assert key_ev[0]["target_id"] == key["key_id"]
    conn_ev = _audit(admin_conn, "connector.disconnected", device_id)
    assert len(conn_ev) == 1
    assert conn_ev[0]["payload"]["reason"] == "device_revoked"
    assert conn_ev[0]["target_id"] == instance_id
    notes = _q(
        admin_conn,
        "SELECT payload FROM notifications WHERE tenant_id = %s "
        "AND user_id = %s AND type = 'connector_disconnected' "
        "AND payload->>'reason' = 'device_revoked'",
        (TENANT_A, USER_CAROL),
    )
    assert notes, "el dueño debe recibir notificación de inbox del corte"
    chain = _q(admin_conn, "SELECT status FROM audit_verify_chain(%s)",
               (TENANT_A,))[0]
    assert chain["status"] == "ok"

    # 8. re-enrolamiento por fingerprint (spec 007 upsert): el MISMO
    #    device id vuelve a pending → approve → exchange → gate OK.
    re_enrolled = _enroll(client, idp, fp).json()
    assert re_enrolled["device_id"] == device_id
    resp = client.post(f"/api/devices/{device_id}/approve",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/devices/enroll/exchange",
        json={"enrollment_token": re_enrolled["enrollment_token"]},
    )
    assert resp.status_code == 200, resp.text
    headers = {**bearer_headers(mint(client, idp, "carol")),
               "X-Device-Token": resp.json()["device_token"]}
    assert client.get("/api/_probe/device", headers=headers).status_code == 200


def test_revoke_fail_closed_when_gateway_down(
    client, idp, admin_conn, litellm_fake, monkeypatch
):
    """Fail-closed (spec 019 §9 Inc. 4): si el sweep no puede bloquear la
    key en LiteLLM, la revocación NO se confirma — 503 + rollback total
    (device sigue active, sesión sigue activa, sin audit device.revoked)."""
    from app.models.litellm import LiteLLMClient

    fp = f"fp-failclosed-{uuid.uuid4().hex[:8]}"
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")
    key = _issue_key(client, carol)
    device_id, _token = _full_device(client, idp, fp)
    session_id = _session_id(carol)
    admin_conn.execute(
        "UPDATE sessions SET device_id = %s WHERE id = %s",
        (device_id, session_id),
    )

    dead = LiteLLMClient("http://127.0.0.1:1", "sk-dead")  # puerto cerrado
    monkeypatch.setattr(
        "app.models.litellm.get_litellm", lambda: dead
    )
    resp = client.post(f"/api/devices/{device_id}/revoke",
                       headers=bearer_headers(alice))
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["reason"] == "service_unavailable"
    # nada se confirmó: device active, sesión activa, key activa, sin audit
    assert _q(admin_conn, "SELECT status FROM devices WHERE id = %s",
              (device_id,))[0]["status"] == "active"
    assert _q(admin_conn, "SELECT status FROM sessions WHERE id = %s",
              (session_id,))[0]["status"] == "active"
    assert _key_row(admin_conn, key["key_id"])["status"] == "active"
    assert _audit(admin_conn, "device.revoked", device_id) == []
    status, _ = litellm_fake.chat(key["key"], MODEL)
    assert status == 200, "la key sigue viva porque nada se confirmó"

    # con el gateway de vuelta, la MISMA revocación completa el sweep.
    monkeypatch.undo()
    resp = client.post(f"/api/devices/{device_id}/revoke",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert _key_row(admin_conn, key["key_id"])["status"] == "blocked"
    status, _ = litellm_fake.chat(key["key"], MODEL)
    assert status == 401


def test_revoke_pending_device_does_not_sweep(
    client, idp, admin_conn, litellm_fake
):
    """Un dispositivo pending nunca canjeó token ni tuvo sesiones: no hay
    acceso real que cortar, y la key del usuario (que pertenece a sus
    OTROS dispositivos activos) NO debe morir por revocar un pending."""
    fp = f"fp-pending-{uuid.uuid4().hex[:8]}"
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")
    key = _issue_key(client, carol)

    enrolled = _enroll(client, idp, fp).json()  # queda pending
    resp = client.post(f"/api/devices/{enrolled['device_id']}/revoke",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["sweep"] == {
        "keys_blocked": [], "connectors_disconnected": [],
    }
    assert _key_row(admin_conn, key["key_id"])["status"] == "active"
    status, _ = litellm_fake.chat(key["key"], MODEL)
    assert status == 200, "la key del usuario sigue operativa"

"""Spec 027 — Policy en vivo + presencia (mandato Miller).

Criterios de aceptación (evidencia real, nada simulado):
  - AC-027-01: POST /api/me/device-heartbeat (camino plugin: Bearer device
    token) → 200 y persiste last_heartbeat_at/applied_policy_version/
    agent_uptime_seconds; revocado → 401 device_not_authorized; body inválido
    → 422.
  - AC-027-02: admin actualiza policy → heartbeat con versión vieja →
    policy_update_available=true + current_policy_version nueva.
  - AC-027-03: propagación EN VIVO con el BundleSyncer REAL del plugin
    contra el control plane REAL sobre HTTP: sync → v_base; admin cambia
    policy; sync → bundle con la nueva versión aplicado, SIN reiniciar ni
    re-enrolar; heartbeat confirma → policy_update_available=false.
  - AC-027-04: GET /api/devices/presence — online/versión/uptime tras
    heartbeat; dept_admin solo ve sus deptos; user sin devices.manage → 403.
  - AC-027-05: audit sin flooding — device.heartbeat muestreado (2
    heartbeats → 1 fila) y policy.propagated solo en la transición
    confirmada ({from_version, to_version}).
  - AC-027-07: revoke → heartbeat 401 y presence lo saca de conectados.

El control plane se levanta REAL (uvicorn en hilo, puerto efímero) contra la
PostgreSQL efímera canónica de conftest (patrón test_governance_bundle.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import (
    DEVICE_ALICE,
    POLICY_JWS_PUBLIC_PEM,
    TENANT_A,
    USER_ALICE,
    bearer_headers,
    mint,
)

PLATFORM_DIR = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM_DIR / "hermes" / "plugins" / "trust-agent-governance"

TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")


# =============================================================================
# Carga del paquete del plugin (nombre sintético — el directorio lleva guiones)
# =============================================================================
@pytest.fixture(scope="session")
def tag():
    spec = importlib.util.spec_from_file_location(
        "trust_agent_governance_plugin_lpp",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["trust_agent_governance_plugin_lpp"] = mod
    spec.loader.exec_module(mod)
    return mod


# =============================================================================
# Control plane REAL sobre HTTP (uvicorn en hilo, puerto efímero)
# =============================================================================
@pytest.fixture(scope="session")
def real_api(db, env):
    import uvicorn

    from app.main import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn (control plane real) no arrancó"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _device_token() -> str:
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    return issue_device_token(
        get_settings(), device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A
    )


def _heartbeat(client, *, applied=None, uptime=42, mode="normal", token=None):
    """POST /api/me/device-heartbeat por el camino plugin (Bearer device token)."""
    dt = token or _device_token()
    return client.post(
        "/api/me/device-heartbeat",
        json={
            "applied_policy_version": applied,
            "uptime_seconds": uptime,
            "mode": mode,
        },
        headers={"Authorization": f"Bearer {dt}", "X-Device-Token": dt},
    )


def _heartbeat_http(api: str, *, applied=None, uptime=42, token: str | None = None):
    """Heartbeat contra el control plane REAL sobre HTTP (urllib)."""
    dt = token or _device_token()
    req = urllib.request.Request(
        f"{api}/api/me/device-heartbeat",
        data=json.dumps({
            "applied_policy_version": applied,
            "uptime_seconds": uptime,
            "mode": "normal",
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {dt}",
            "X-Device-Token": dt,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:  # noqa: F401 (imported below at module use)
        return exc.code, json.loads(exc.read() or b"{}")


def _audit_count(admin_conn, action: str, device_id: str = DEVICE_ALICE) -> int:
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = %s AND device_id = %s",
            (action, device_id),
        )
        return cur.fetchone()[0]


def _bump_org_policy(client, idp, baseline: int, value: int = 24) -> int:
    """Admin (alice, org_admin) bumps the org policy ABOVE the baseline.

    ``_policy_version`` is the MAX active version across keys, and a NEW key
    is born at v1 — so a single PUT may not raise the max. We PUT the same
    key until its version exceeds the baseline (idempotent-safe, capped).

    Key choice: ``attestation_overdue_hours`` (integer, variable) — no other
    suite PUTs it, so this file does not perturb the order-sensitive
    ``version == 1`` assertions of test_policies_api (shared session DB).
    Values stay >= 1: the bundle contract tests only floor this claim.
    """
    alice = mint(client, idp, "alice")
    version = baseline
    for _ in range(20):
        resp = client.put(
            "/api/policies/org",
            json={"key": "attestation_overdue_hours", "value": value},
            headers=bearer_headers(alice),
        )
        assert resp.status_code == 200, resp.text
        version = resp.json()["version"]
        if version > baseline:
            return version
        value += 1
    raise AssertionError("20 PUTs no elevaron la versión por encima de la base")


# =============================================================================
# AC-027-01 — heartbeat: 200 + persistencia; 401 revocado; 422 inválido
# =============================================================================
def test_ac_027_01_heartbeat_persists_presence(client, admin_conn):
    resp = _heartbeat(client, applied=1, uptime=42)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_id"] == DEVICE_ALICE
    assert isinstance(body["current_policy_version"], int)
    assert body["heartbeat_interval_seconds"] == 300
    assert body["online_window_seconds"] == 600
    assert body["server_time"]
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT last_heartbeat_at IS NOT NULL, applied_policy_version, "
            "agent_uptime_seconds FROM devices WHERE id = %s",
            (DEVICE_ALICE,),
        )
        has_hb, applied, uptime = cur.fetchone()
    assert has_hb and applied == 1 and uptime == 42


def test_ac_027_01_revoked_device_401(client, admin_conn):
    _heartbeat(client, applied=1)  # sanity: funciona activo
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE devices SET status = 'revoked' WHERE id = %s", (DEVICE_ALICE,))
    try:
        resp = _heartbeat(client, applied=1)
        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["reason"] == "device_not_authorized"
    finally:
        with admin_conn.cursor() as cur:
            cur.execute("UPDATE devices SET status = 'active' WHERE id = %s", (DEVICE_ALICE,))


@pytest.mark.parametrize("payload", [
    {"applied_policy_version": 1, "uptime_seconds": -1, "mode": "normal"},
    {"applied_policy_version": 1, "uptime_seconds": 5, "mode": "yolo"},
])
def test_ac_027_01_invalid_body_422(client, payload):
    dt = _device_token()
    resp = client.post(
        "/api/me/device-heartbeat",
        json=payload,
        headers={"Authorization": f"Bearer {dt}", "X-Device-Token": dt},
    )
    assert resp.status_code == 422, resp.text


def test_ac_027_01_requires_device_token(client):
    resp = client.post(
        "/api/me/device-heartbeat",
        json={"applied_policy_version": 1, "uptime_seconds": 1, "mode": "normal"},
    )
    assert resp.status_code == 401, resp.text


# =============================================================================
# AC-HRB-010a (spec 022 v1.1.0) — sliding renewal: el camino plugin renueva
# =============================================================================
def test_ac_hrb_010a_heartbeat_renews_device_token_plugin_path(client):
    """El heartbeat con Bearer=device token recibe un token FRESCO que a su
    vez autentica el siguiente ciclo (sesión > TTL sostenida por heartbeat)."""
    from app.config import get_settings

    resp = _heartbeat(client, applied=1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_token"], "plugin path must receive a renewed token"
    assert body["expires_in"] == get_settings().device_token_ttl_minutes * 60
    # El token renovado ES una credencial válida: el ciclo siguiente con él
    # también renueva (sliding window sostenida).
    resp2 = _heartbeat(client, applied=1, token=body["device_token"])
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["device_token"]


def test_ac_hrb_010a_heartbeat_session_path_does_not_renew(client, idp):
    """Camino sesión+MFA: sin device_token en la respuesta (el desktop tiene
    POST /api/devices/{id}/token; la renovación por heartbeat es del plugin)."""
    alice = mint(client, idp, "alice")
    dt = _device_token()
    resp = client.post(
        "/api/me/device-heartbeat",
        json={"applied_policy_version": 1, "uptime_seconds": 7, "mode": "normal"},
        headers={**bearer_headers(alice), "X-Device-Token": dt},
    )
    assert resp.status_code == 200, resp.text
    assert "device_token" not in resp.json()
    assert "expires_in" not in resp.json()


# =============================================================================
# AC-027-02 — señal de actualización tras cambio de policy del admin
# =============================================================================
def test_ac_027_02_update_available_after_policy_change(client, idp, admin_conn):
    # Línea base: el device reporta la versión vigente → sin update.
    base = _heartbeat(client, applied=None).json()
    v0 = base["current_policy_version"]
    ok = _heartbeat(client, applied=v0).json()
    assert ok["policy_update_available"] is False

    # El admin cambia la policy org → la versión vigente sube.
    v1 = _bump_org_policy(client, idp, v0)

    # El device sigue reportando la versión vieja → señal de actualización.
    stale = _heartbeat(client, applied=v0).json()
    assert stale["policy_update_available"] is True
    assert stale["current_policy_version"] == v1
    assert v1 > v0


# =============================================================================
# AC-027-05 — audit: heartbeat muestreado + policy.propagated en transición
# =============================================================================
def test_ac_027_05_audit_sampled_and_propagated(client, idp, admin_conn):
    # Order-independence: heartbeats de tests anteriores en la misma DB ya
    # fijaron last_heartbeat_audit_at — se resetea para observar el muestreo
    # desde cero en ESTE test.
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE devices SET last_heartbeat_audit_at = NULL WHERE id = %s",
            (DEVICE_ALICE,),
        )
    hb_before = _audit_count(admin_conn, "device.heartbeat")
    prop_before = _audit_count(admin_conn, "policy.propagated")

    # Línea base y primer heartbeat (audit muestreado: primera fila).
    v0 = _heartbeat(client, applied=None).json()["current_policy_version"]
    _heartbeat(client, applied=v0)
    # Segundo heartbeat inmediato: MUESTREADO — no debe crear otra fila.
    _heartbeat(client, applied=v0)
    assert _audit_count(admin_conn, "device.heartbeat") == hb_before + 1

    # El admin sube la versión; el device confirma la nueva → propagated.
    v1 = _bump_org_policy(client, idp, v0)
    assert _heartbeat(client, applied=v0).json()["policy_update_available"] is True
    confirmed = _heartbeat(client, applied=v1).json()
    assert confirmed["policy_update_available"] is False
    assert _audit_count(admin_conn, "policy.propagated") == prop_before + 1

    # La fila de propagated lleva la transición {from_version, to_version}.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'policy.propagated' "
            "AND device_id = %s ORDER BY seq DESC LIMIT 1",
            (DEVICE_ALICE,),
        )
        payload = cur.fetchone()[0]
    assert payload["from_version"] == v0
    assert payload["to_version"] == v1

    # Heartbeats repetidos con la misma versión: sin más propagated.
    _heartbeat(client, applied=v1)
    assert _audit_count(admin_conn, "policy.propagated") == prop_before + 1


# =============================================================================
# AC-027-04 — presence: online/versión/uptime + scopes
# =============================================================================
def test_ac_027_04_presence_and_scopes(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    v = _heartbeat(client, applied=None).json()["current_policy_version"]
    _heartbeat(client, applied=v, uptime=1234)

    resp = client.get("/api/devices/presence", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["online_window_seconds"] == 600
    assert body["heartbeat_interval_seconds"] == 300
    mine = [d for d in body["devices"] if d["id"] == DEVICE_ALICE]
    assert mine, "el device de alice debe aparecer en presencia"
    row = mine[0]
    assert row["online"] is True
    assert row["status"] == "active"
    assert row["applied_policy_version"] == v
    assert row["current_policy_version"] == v
    assert row["policy_up_to_date"] is True
    assert row["agent_uptime_seconds"] == 1234
    assert row["last_heartbeat_at"]
    assert row["email"] == "alice@tenant-a.test"
    assert row["department_name"] == "IT"
    assert body["online_count"] >= 1
    assert body["active_count"] >= 1

    # dept_admin (bob, Finance) NO ve el device de alice (IT): dept-scope.
    bob = mint(client, idp, "bob")
    resp_bob = client.get("/api/devices/presence", headers=bearer_headers(bob))
    assert resp_bob.status_code == 200
    body_bob = resp_bob.json()
    assert all(d["id"] != DEVICE_ALICE for d in body_bob["devices"])
    assert all(d["department_name"] == "Finance" for d in body_bob["devices"])

    # user sin devices.manage → 403.
    carol = mint(client, idp, "carol")
    resp_carol = client.get("/api/devices/presence", headers=bearer_headers(carol))
    assert resp_carol.status_code == 403


# =============================================================================
# AC-027-07 — revoke: el device desaparece de conectados
# =============================================================================
def test_ac_027_07_revoke_removes_from_online(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    v = _heartbeat(client, applied=None).json()["current_policy_version"]
    _heartbeat(client, applied=v)
    before = client.get("/api/devices/presence", headers=bearer_headers(alice)).json()
    assert any(d["id"] == DEVICE_ALICE and d["online"] for d in before["devices"])

    # El admin revoca el device desde la API real (audit + sesiones cortadas).
    resp = client.post(f"/api/devices/{DEVICE_ALICE}/revoke", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text

    try:
        # Heartbeat → 401 inmediato (fail-closed, REQ-GOH-010).
        hb = _heartbeat(client, applied=v)
        assert hb.status_code == 401
        assert hb.json()["error"]["reason"] == "device_not_authorized"

        # Presence: revocado → offline y fuera del recuento de conectados.
        after = client.get("/api/devices/presence", headers=bearer_headers(alice)).json()
        row = next(d for d in after["devices"] if d["id"] == DEVICE_ALICE)
        assert row["online"] is False
        assert row["status"] == "revoked"
        assert all(
            not (d["online"] and d["id"] == DEVICE_ALICE) for d in after["devices"]
        )
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "UPDATE devices SET status = 'active' WHERE id = %s", (DEVICE_ALICE,)
            )


# =============================================================================
# AC-027-03 — propagación EN VIVO con el BundleSyncer real (HTTP real)
# =============================================================================
@pytest.fixture(scope="session")
def pinned_pubkey_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("pinned-lpp") / "trust_agent_policy_pubkey.pem"
    path.write_text(POLICY_JWS_PUBLIC_PEM, encoding="utf-8")
    return path


def test_ac_027_03_live_propagation_real_syncer(
    tag, real_api, client, idp, tmp_path, monkeypatch, pinned_pubkey_file, admin_conn
):
    """El desktop enrolado aplica el cambio de policy SIN reiniciar ni
    re-enrolar: sync → v0; admin cambia policy; heartbeat señala update;
    sync → bundle v1 aplicado; heartbeat confirma → sin update."""
    import urllib.error  # noqa: F401  (usado por _heartbeat_http)

    home = tmp_path
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TRUST_AGENT_API", real_api)
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey_file))
    monkeypatch.setenv("TRUST_SYNC_INTERVAL", "300")
    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": DEVICE_ALICE, "token": _device_token()}),
        encoding="utf-8",
    )

    tag.state.reset_state()
    syncer = tag.bundle_client.BundleSyncer()
    snapshot = syncer.startup()
    assert snapshot["mode"] == "normal", snapshot
    v0 = snapshot["bundle"]["policy_version"]

    # Heartbeat con la versión aplicada → sin señal de actualización.
    status, body = _heartbeat_http(real_api, applied=v0)
    assert status == 200, body
    assert body["policy_update_available"] is False

    # El admin cambia la policy (mismo DB, control plane real).
    v1 = _bump_org_policy(client, idp, v0)
    assert v1 > v0

    # El próximo heartbeat señala la actualización (ventana ≤ intervalo).
    status, body = _heartbeat_http(real_api, applied=v0)
    assert status == 200
    assert body["policy_update_available"] is True
    assert body["current_policy_version"] == v1

    # El plugin re-baja el bundle en el mismo ciclo (tick) y lo aplica:
    # SIN reiniciar el proceso ni re-enrolar el dispositivo.
    snapshot2 = syncer.tick()
    assert snapshot2["mode"] == "normal", snapshot2
    assert snapshot2["bundle"]["policy_version"] == v1, (
        "la nueva policy debe quedar aplicada tras el sync"
    )

    # El heartbeat de confirmación cierra el ciclo de propagación.
    status, body = _heartbeat_http(real_api, applied=v1)
    assert status == 200
    assert body["policy_update_available"] is False

    # La presencia del admin refleja la versión aplicada al día.
    alice = mint(client, idp, "alice")
    presence = client.get("/api/devices/presence", headers=bearer_headers(alice)).json()
    row = next(d for d in presence["devices"] if d["id"] == DEVICE_ALICE)
    assert row["online"] is True
    assert row["applied_policy_version"] == v1
    assert row["policy_up_to_date"] is True

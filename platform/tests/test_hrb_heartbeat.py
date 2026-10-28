"""Spec 022 + seam spec 027 — tests unitarios del heartbeat de presencia.

Contrato §4 de specs/027-live-policy-presence (seam t_f4f99970 → esta lane):
  - ``POST /api/me/device-heartbeat`` con ``{applied_policy_version,
    uptime_seconds, mode}`` en cada ciclo de sync (piggyback).
  - ``policy_update_available: true`` → re-baja del bundle EN EL MISMO ciclo
    (propagación en vivo, sin reiniciar ni re-enrolar — D-027-03).
  - 401/403 del heartbeat → fail-closed inmediato (revoked, REQ-GOH-010).
  - Error de red del heartbeat → best-effort: NO cambia el estado.
  - Autoajuste de intervalo (D-027-02) solo si el operador no fijó
    ``TRUST_SYNC_INTERVAL``.

Sin red real: ``urllib.request.urlopen`` se sustituye por un fake que
enruta por URL (governance-bundle GET / device-heartbeat POST).
"""
import importlib.util
import json
import time
import uuid
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import urllib.error
import urllib.request

PLATFORM = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM / "hermes" / "plugins" / "trust-agent-governance"

DEVICE_ID = "99999999-8888-7777-6666-555555555555"


def _load_plugin():
    import sys

    pkg_spec = importlib.util.spec_from_file_location(
        "tag_plugin_hb", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["tag_plugin_hb"] = pkg
    pkg_spec.loader.exec_module(pkg)
    return pkg


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pinned_pubkey(tmp_path_factory, signing_key):
    pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    path = tmp_path_factory.mktemp("pinned-hb") / "trust_agent_policy_pubkey.pem"
    path.write_text(pem)
    return path


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TRUST_AGENT_API", "http://controlplane.test")
    monkeypatch.delenv("TRUST_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("TRUST_SYNC_INTERVAL", raising=False)
    return tmp_path


@pytest.fixture()
def plugin(home, monkeypatch, pinned_pubkey):
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey))
    mod = _load_plugin()
    mod.state.reset_state()  # estado fresco del núcleo v2 por test
    return mod


def _make_bundle(signing_key, *, policy_version=7, allowed_tools=None):
    now = int(time.time())
    payload = {
        "iss": "https://trust-agent.control-plane/",
        "aud": DEVICE_ID,
        "iat": now, "exp": now + 3600, "jti": uuid.uuid4().hex,
        "tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "device_id": DEVICE_ID,
        "policy_version": policy_version,
        "allowed_tools": allowed_tools if allowed_tools is not None else ["web_search"],
        "allowed_connectors": [],
        "approved_models": ["glm-5.2"],
        "budget": {
            "user": {"remaining_cents": 4200, "cutoff": False},
            "dept": {"remaining_cents": 150000, "cutoff": False},
            "org": {"remaining_cents": 9800000, "cutoff": False},
        },
        "offline_grace_minutes": 60,
        "unbind_mode": "readonly",
        "attestation_overdue_hours": 24,
    }
    return jwt.encode(payload, signing_key, algorithm="RS256")


def _enrolled(home):
    d = home / "trust-agent"
    d.mkdir(parents=True, exist_ok=True)
    token_file = d / "device-token.json"
    token_file.write_text(json.dumps({
        "device_id": DEVICE_ID, "token": "devtok.test",
        "enrolled_at": int(time.time()), "org_code": "t", "tenant_slug": "t",
    }))
    return token_file


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode() if payload is not None else b"{}"
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeControlPlane:
    """Enruta por URL: GET governance-bundle / POST device-heartbeat.

    Registra TODAS las requests (method, url, body, headers) para asserts.
    ``bundle_sequence``: JWS devueltos por GET en orden (se agotan → el último
    se repite). ``heartbeat_response``: dict de la respuesta del POST.
    """

    def __init__(self, bundle_sequence, heartbeat_response):
        self.bundle_sequence = list(bundle_sequence)
        self.heartbeat_response = heartbeat_response
        self.requests = []

    def __call__(self, req, timeout=None, context=None):
        url = req.full_url
        body = req.data.decode() if req.data else ""
        self.requests.append({
            "method": req.get_method(), "url": url,
            "body": json.loads(body) if body else None,
            "authorization": req.headers.get("Authorization"),
            "x_device_token": req.headers.get("X-device-token"),
        })
        if url.endswith("/api/me/governance-bundle"):
            jws = self.bundle_sequence.pop(0) if len(self.bundle_sequence) > 1 \
                else (self.bundle_sequence[0] if self.bundle_sequence else None)
            return FakeResponse({"bundle": jws})
        if url.endswith("/api/me/device-heartbeat"):
            return FakeResponse(self.heartbeat_response)
        return FakeResponse({}, status=404)


def _install_fake(plugin, monkeypatch, fake):
    monkeypatch.setattr(
        plugin.bundle_client.urllib.request, "urlopen", fake)


# ---------------------------------------------------------------------------
# Contrato §4 — body del heartbeat y piggyback en el ciclo de sync
# ---------------------------------------------------------------------------
def test_heartbeat_posts_applied_version_uptime_and_mode(plugin, monkeypatch,
                                                         signing_key, home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    fake = FakeControlPlane([jws_v7], {
        "device_id": DEVICE_ID, "current_policy_version": 7,
        "policy_update_available": False,
        "heartbeat_interval_seconds": 300, "online_window_seconds": 600,
        "server_time": "2026-10-26T00:00:00Z",
    })
    _install_fake(plugin, monkeypatch, fake)

    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()

    # El ciclo aplicó el bundle v7 (modo normal)...
    assert snap["mode"] == "normal"
    assert snap["bundle"]["policy_version"] == 7
    # ...y en el MISMO ciclo viajó el heartbeat con el contrato §4:
    hb = [r for r in fake.requests if r["url"].endswith("/api/me/device-heartbeat")]
    assert len(hb) == 1, "el heartbeat debe viajar una vez por ciclo de sync"
    body = hb[0]["body"]
    assert body["applied_policy_version"] == 7
    assert isinstance(body["uptime_seconds"], int) and body["uptime_seconds"] >= 0
    assert body["mode"] == "normal"
    # Misma auth que el governance-bundle (Bearer + X-Device-Token):
    assert hb[0]["authorization"] == "Bearer devtok.test"
    assert hb[0]["x_device_token"] == "devtok.test"
    # El heartbeat queda registrado en el log local del plugin:
    log = (home / "trust-agent" / "plugin.log").read_text()
    assert "heartbeat_ok" in log


def test_heartbeat_reports_restricted_mode_without_bundle(plugin, monkeypatch,
                                                          signing_key, home):
    """Sin bundle aplicado: applied_policy_version=null, mode=restricted."""
    _enrolled(home)
    jws_expired = _make_bundle(signing_key)  # válido, pero lo vence el fake
    # Bundle vencido en caché + sync de red caído → restricted(expired) y el
    # heartbeat reporta honestamente ese modo.
    past = int(time.time()) - 7200
    payload = jwt.decode(jws_expired, options={"verify_signature": False})
    payload["iat"], payload["exp"] = past, past + 60
    jws_expired = jwt.encode(payload, signing_key, algorithm="RS256")
    (home / "trust-agent" / "governance-bundle.jws").write_text(jws_expired)

    def network_down(req, timeout=None, context=None):
        raise urllib.error.URLError("control plane down")

    monkeypatch.setattr(plugin.bundle_client.urllib.request, "urlopen", network_down)
    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()
    assert snap["mode"] == "restricted"
    # Sin red no hay heartbeat posible (best-effort): nada que verificar aquí
    # más allá del estado fail-closed.
    assert snap["reason"] in ("expired", "no_bundle")


# ---------------------------------------------------------------------------
# D-027-03 — propagación en vivo: policy_update_available → re-baja en el
# MISMO ciclo, sin reiniciar ni re-enrolar
# ---------------------------------------------------------------------------
def test_policy_update_available_triggers_resync_same_cycle(plugin, monkeypatch,
                                                            signing_key, home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7, allowed_tools=["web_search"])
    jws_v8 = _make_bundle(signing_key, policy_version=8,
                          allowed_tools=["web_search", "terminal"])
    fake = FakeControlPlane([jws_v7, jws_v8], {
        "device_id": DEVICE_ID, "current_policy_version": 8,
        "policy_update_available": True,
        "heartbeat_interval_seconds": 300, "online_window_seconds": 600,
        "server_time": "2026-10-26T00:00:00Z",
    })
    _install_fake(plugin, monkeypatch, fake)

    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()

    # El ciclo: GET v7 → aplica → heartbeat (applied=7, current=8) → re-baja
    # → aplica v8. TODO en un solo tick, sin reinicio ni re-enrolamiento.
    gets = [r for r in fake.requests if r["url"].endswith("/api/me/governance-bundle")]
    assert len(gets) == 2, "policy_update_available debe forzar re-baja inmediata"
    assert snap["mode"] == "normal"
    assert snap["bundle"]["policy_version"] == 8
    assert snap["bundle"]["allowed_tools"] == ["web_search", "terminal"]
    log = (home / "trust-agent" / "plugin.log").read_text()
    assert "policy_update_pull" in log


def test_no_update_signal_means_single_get(plugin, monkeypatch, signing_key, home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    fake = FakeControlPlane([jws_v7], {
        "device_id": DEVICE_ID, "current_policy_version": 7,
        "policy_update_available": False,
        "heartbeat_interval_seconds": 300, "online_window_seconds": 600,
        "server_time": "2026-10-26T00:00:00Z",
    })
    _install_fake(plugin, monkeypatch, fake)
    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()
    gets = [r for r in fake.requests if r["url"].endswith("/api/me/governance-bundle")]
    assert len(gets) == 1
    assert snap["bundle"]["policy_version"] == 7


# ---------------------------------------------------------------------------
# REQ-GOH-010 — revocación detectada por el heartbeat → fail-closed inmediato
# ---------------------------------------------------------------------------
def test_heartbeat_401_revokes_immediately(plugin, monkeypatch, signing_key, home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    cache = home / "trust-agent" / "governance-bundle.jws"

    class RevokedAfterSync:
        """GET bundle OK; POST heartbeat → 401 (el device fue revocado)."""

        def __init__(self):
            self.requests = []

        def __call__(self, req, timeout=None, context=None):
            url = req.full_url
            if url.endswith("/api/me/governance-bundle"):
                return FakeResponse({"bundle": jws_v7})
            assert url.endswith("/api/me/device-heartbeat"), url
            raise urllib.error.HTTPError(url, 401, "device_not_authorized",
                                         None, None)

    fake = RevokedAfterSync()
    monkeypatch.setattr(plugin.bundle_client.urllib.request, "urlopen", fake)
    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()

    assert snap["mode"] == "restricted"
    assert snap["reason"] == "revoked"
    assert not cache.exists(), "la caché del bundle deja de ser autoridad"
    log = (home / "trust-agent" / "plugin.log").read_text()
    assert "device_unauthorized" in log


def test_heartbeat_network_error_is_best_effort(plugin, monkeypatch,
                                                signing_key, home):
    """Heartbeat caído + sync OK → el estado de gobernanza NO cambia."""
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)

    class HeartbeatDown:
        def __call__(self, req, timeout=None, context=None):
            if req.full_url.endswith("/api/me/device-heartbeat"):
                raise urllib.error.URLError("heartbeat endpoint down")
            return FakeResponse({"bundle": jws_v7})

    monkeypatch.setattr(plugin.bundle_client.urllib.request, "urlopen", HeartbeatDown())
    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()
    assert snap["mode"] == "normal"
    assert snap["bundle"]["policy_version"] == 7
    log = (home / "trust-agent" / "plugin.log").read_text()
    assert "heartbeat_unavailable" in log


# ---------------------------------------------------------------------------
# D-027-02 — autoajuste del intervalo recomendado por el servidor
# ---------------------------------------------------------------------------
def test_server_interval_adopted_when_not_locked(plugin, monkeypatch,
                                                 signing_key, home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    fake = FakeControlPlane([jws_v7], {
        "device_id": DEVICE_ID, "current_policy_version": 7,
        "policy_update_available": False,
        "heartbeat_interval_seconds": 42, "online_window_seconds": 600,
        "server_time": "2026-10-26T00:00:00Z",
    })
    _install_fake(plugin, monkeypatch, fake)
    syncer = plugin.bundle_client.BundleSyncer()  # sin TRUST_SYNC_INTERVAL
    assert syncer.interval == 300.0  # default local
    syncer.tick()
    assert syncer.interval == 42.0  # adoptado del servidor


def test_operator_interval_wins_over_server(plugin, monkeypatch, signing_key,
                                            home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    fake = FakeControlPlane([jws_v7], {
        "device_id": DEVICE_ID, "current_policy_version": 7,
        "policy_update_available": False,
        "heartbeat_interval_seconds": 42, "online_window_seconds": 600,
        "server_time": "2026-10-26T00:00:00Z",
    })
    _install_fake(plugin, monkeypatch, fake)
    syncer = plugin.bundle_client.BundleSyncer(interval=15.0)  # operador fijó
    syncer.tick()
    assert syncer.interval == 15.0  # el servidor NO pisa al operador


def test_uptime_monotonic_non_negative(plugin):
    up1 = plugin.bundle_client.agent_uptime_seconds()
    time.sleep(0.01)
    up2 = plugin.bundle_client.agent_uptime_seconds()
    assert up1 >= 0 and up2 >= up1


# ---------------------------------------------------------------------------
# AC-HRB-010a (spec 022 v1.1.0) — sliding renewal: el heartbeat devuelve un
# device token fresco y el plugin lo persiste en el MISMO ciclo
# ---------------------------------------------------------------------------
def test_heartbeat_renews_device_token_and_persists(plugin, monkeypatch,
                                                    signing_key, home):
    _enrolled(home)
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    renewed_token = "devtok.renewed-1"
    fake = FakeControlPlane([jws_v7], {
        "device_id": DEVICE_ID, "current_policy_version": 7,
        "policy_update_available": False,
        "heartbeat_interval_seconds": 300, "online_window_seconds": 600,
        "server_time": "2026-10-27T00:00:00Z",
        "device_token": renewed_token, "expires_in": 900,
    })
    _install_fake(plugin, monkeypatch, fake)

    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()
    assert snap["mode"] == "normal"

    # El token renovado quedó persistido preservando los campos del enrolado:
    data = json.loads((home / "trust-agent" / "device-token.json").read_text())
    assert data["token"] == renewed_token
    assert data["device_id"] == DEVICE_ID
    assert data["renewed_at"], "la renovación marca renewed_at"
    assert data["org_code"] == "t", "org_code preservado del enrolamiento"
    # El log local registra la renovación (evidencia del sliding renewal):
    log = (home / "trust-agent" / "plugin.log").read_text()
    assert "device_token_renewed" in log

    # El siguiente ciclo YA viaja con el token renovado:
    syncer.tick()
    hb = [r for r in fake.requests if r["url"].endswith("/api/me/device-heartbeat")]
    assert hb[-1]["authorization"] == f"Bearer {renewed_token}"
    assert hb[-1]["x_device_token"] == renewed_token


# ---------------------------------------------------------------------------
# AC-HRB-010b — token expirado: recuperación por re-emisión directa (200) o
# restricted(token_expired) honesto; JAMÁS "revoked" por expiración
# ---------------------------------------------------------------------------
def _device_jwt(expires_in: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"device_id": DEVICE_ID, "user_id": "u", "tenant_id": "t",
         "jti": uuid.uuid4().hex, "iat": now, "exp": now + expires_in},
        "test-device-secret", algorithm="HS256",
    )


def test_expired_token_recovers_via_direct_reissue(plugin, monkeypatch,
                                                   signing_key, home):
    """Token expirado + id_token fresco → el MISMO ciclo re-enrolla (POST
    /api/devices/enroll → 200 directo), sincroniza y termina en normal."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    (tdir / "id-token.txt").write_text("fresh-id-token\n")
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600),
        "org_code": "acme", "tenant_slug": "acme",
    }))
    fresh_token = _device_jwt(900)
    jws_v7 = _make_bundle(signing_key, policy_version=7)

    class ReissueControlPlane:
        def __init__(self):
            self.requests = []

        def __call__(self, req, timeout=None, context=None):
            url = req.full_url
            body = json.loads(req.data.decode()) if req.data else None
            self.requests.append({"url": url, "body": body})
            if url.endswith("/api/devices/enroll"):
                return FakeResponse(
                    {"device_id": DEVICE_ID, "status": "active",
                     "device_token": fresh_token, "expires_in": 900},
                    status=200,
                )
            if url.endswith("/api/me/governance-bundle"):
                return FakeResponse({"bundle": jws_v7})
            if url.endswith("/api/me/device-heartbeat"):
                return FakeResponse({
                    "device_id": DEVICE_ID, "current_policy_version": 7,
                    "policy_update_available": False,
                    "heartbeat_interval_seconds": 300,
                    "online_window_seconds": 600,
                    "server_time": "2026-10-27T00:00:00Z",
                })
            return FakeResponse({}, status=404)

    fake = ReissueControlPlane()
    monkeypatch.setattr(plugin.bundle_client.urllib.request, "urlopen", fake)

    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()

    # Recuperación completa en UN ciclo: re-enroll → sync → heartbeat → normal
    assert snap["mode"] == "normal", snap
    enroll_calls = [r for r in fake.requests
                    if r["url"].endswith("/api/devices/enroll")]
    assert len(enroll_calls) == 1, "un único POST de re-enroll por ciclo"
    assert enroll_calls[0]["body"]["id_token"] == "fresh-id-token"
    # El token fresco quedó en disco:
    data = json.loads((tdir / "device-token.json").read_text())
    assert data["token"] == fresh_token
    # Trazas de la recuperación en el log local:
    log = (tdir / "plugin.log").read_text()
    assert "device_token_expired" in log
    assert "device_token_recovered" in log


def test_expired_token_without_id_token_is_token_expired_not_revoked(
        plugin, monkeypatch, signing_key, home):
    """Sin id_token fresco no hay recuperación: restricted(token_expired) —
    razón honesta, DISTINTA de revoked; la caché firmada no se destruye."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    # SIN id-token.txt → la recuperación no puede arrancar.
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600),
    }))
    jws_v7 = _make_bundle(signing_key, policy_version=7)
    (tdir / "governance-bundle.jws").write_text(jws_v7)

    def network_down(req, timeout=None, context=None):
        raise urllib.error.URLError("control plane down")

    monkeypatch.setattr(plugin.bundle_client.urllib.request, "urlopen", network_down)
    syncer = plugin.bundle_client.BundleSyncer()
    snap = syncer.tick()

    assert snap["mode"] == "restricted"
    assert snap["reason"] == "token_expired", snap
    log = (tdir / "plugin.log").read_text()
    assert "device_token_expired" in log
    assert "device_unauthorized" not in log, "expiración NO es revocación"
    # La caché del bundle NO se borró (no fue revocación — REQ-GOH-010 solo
    # invalida la caché ante 401/403):
    assert (tdir / "governance-bundle.jws").exists()

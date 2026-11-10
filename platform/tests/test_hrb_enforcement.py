"""Spec 022 (hermes-real-bridge) — tests unitarios del enforcement.

AC-HRB-003 (veto por allowed_tools con mensaje exacto), AC-HRB-004
(fail-closed: sin bundle / expirado sin sync / unbind_mode) y AC-HRB-005
(verificador: firma manipulada, aud incorrecto, exp vencido, jti replay,
HS256 rechazado). Sin red real: el sync fallido se prueba contra un puerto
local cerrado (127.0.0.1:1 — conexión rechazada de verdad).

El paquete del plugin se carga con importlib (el directorio lleva guion,
no es un nombre de módulo importable directamente — igual que hace el
loader de plugins de Hermes).
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PLATFORM = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM / "hermes" / "plugins" / "trust-agent-governance"

DEVICE_ID = "11111111-2222-3333-4444-555555555555"


def _load_plugin():
    """Carga el paquete del plugin + sus submódulos (el __init__ v1 aún no
    los cablea — el cableado final es de esta spec, T10)."""
    import sys

    pkg_spec = importlib.util.spec_from_file_location(
        "tag_plugin_under_test", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["tag_plugin_under_test"] = pkg
    pkg_spec.loader.exec_module(pkg)
    for sub in ("enroll", "enforcement"):
        sub_spec = importlib.util.spec_from_file_location(
            f"tag_plugin_under_test.{sub}", PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(sub_spec)
        sys.modules[f"tag_plugin_under_test.{sub}"] = mod
        sub_spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Home Hermes aislado + env del plugin apuntando al tmp."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TRUST_AGENT_API", "http://127.0.0.1:1")  # puerto cerrado
    monkeypatch.delenv("TRUST_DEVICE_TOKEN", raising=False)
    # Hermético: un host enrolado exporta su binding (TRUST_ORG_CODE…) y
    # haría pasar por "vinculado" a un home que en el test no lo está.
    monkeypatch.delenv("TRUST_ORG_CODE", raising=False)
    monkeypatch.delenv("TRUST_DEVICE_TYPE", raising=False)
    return tmp_path


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pinned_pubkey(tmp_path_factory, signing_key):
    """Clave pública pineada en un archivo (como la profile distribution)."""
    pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    path = tmp_path_factory.mktemp("pinned") / "trust_agent_policy_pubkey.pem"
    path.write_text(pem)
    return path


@pytest.fixture()
def plugin(home, monkeypatch, pinned_pubkey):
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey))
    mod = _load_plugin()
    # Estado fresco por test (el singleton vive en el módulo cargado).
    mod.enforcement.STATE = mod.enforcement.GovernanceState()
    return mod


def _make_bundle(signing_key, *, device_id=DEVICE_ID, allowed_tools=None,
                 exp_delta=3600, unbind_mode="readonly", jti=None,
                 aud=None, alg="RS256", mutate_payload=None):
    now = int(time.time())
    payload = {
        "iss": "https://trust-agent.control-plane/",
        "aud": aud if aud is not None else device_id,
        "iat": now, "exp": now + exp_delta, "jti": jti or uuid.uuid4().hex,
        "tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "device_id": device_id,
        "policy_version": 7,
        "allowed_tools": allowed_tools if allowed_tools is not None else ["web_search"],
        "allowed_connectors": [],
        "approved_models": ["glm-5.2"],  # deployment real MaaS (spec 018)
        "budget": {
            "user": {"remaining_cents": 4200, "cutoff": False},
            "dept": {"remaining_cents": 150000, "cutoff": False},
            "org": {"remaining_cents": 9800000, "cutoff": False},
        },
        "offline_grace_minutes": 60,
        "unbind_mode": unbind_mode,
        "attestation_overdue_hours": 24,
    }
    if mutate_payload:
        mutate_payload(payload)
    key = signing_key
    if alg == "HS256":  # prohibido — el verificador debe rechazarlo
        return jwt.encode(payload, "attacker-secret", algorithm="HS256")
    return jwt.encode(payload, key, algorithm="RS256")


def _enrolled(home, device_id=DEVICE_ID):
    """Simula el estado tras /trust-enroll: device-token.json 0600."""
    d = home / "trust-agent"
    d.mkdir(parents=True, exist_ok=True)
    token_file = d / "device-token.json"
    token_file.write_text(json.dumps({
        "device_id": device_id, "token": "devtok.test",
        "enrolled_at": int(time.time()), "org_code": "t", "tenant_slug": "t",
    }))
    os.chmod(token_file, 0o600)
    return token_file


def _cache(home, jws):
    (home / "trust-agent").mkdir(parents=True, exist_ok=True)
    (home / "trust-agent" / "governance-bundle.jws").write_text(jws)


# ---------------------------------------------------------------------------
# AC-HRB-005 — verificador del bundle (REQ-GOH-003)
# ---------------------------------------------------------------------------
def test_verify_valid_bundle(plugin, signing_key):
    jws = _make_bundle(signing_key)
    payload = plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)
    assert payload["allowed_tools"] == ["web_search"]
    assert payload["device_id"] == DEVICE_ID


def test_verify_rejects_tampered_payload(plugin, signing_key):
    jws = _make_bundle(signing_key)
    # Alterar el payload tras la firma (payload.b64 modificado):
    head, body, sig = jws.split(".")
    claims = json.loads(__import__("base64").urlsafe_b64decode(body + "=="))
    claims["allowed_tools"] = ["terminal", "browser"]  # escalada de privilegios
    new_body = __import__("base64").urlsafe_b64encode(
        json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(plugin.enforcement.BundleRejected):
        plugin.enforcement.verify_bundle(f"{head}.{new_body}.{sig}", device_id=DEVICE_ID)


def test_verify_rejects_wrong_audience(plugin, signing_key):
    jws = _make_bundle(signing_key, aud="otro-dispositivo")
    with pytest.raises(plugin.enforcement.BundleRejected):
        plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)


def test_verify_rejects_expired(plugin, signing_key):
    jws = _make_bundle(signing_key, exp_delta=-10)
    with pytest.raises(plugin.enforcement.BundleRejected):
        plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)


def test_verify_rejects_replayed_jti(plugin, signing_key):
    jti = uuid.uuid4().hex
    jws = _make_bundle(signing_key, jti=jti)
    plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)  # 1ª vez OK
    with pytest.raises(plugin.enforcement.BundleRejected):
        plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)  # replay


def test_verify_rejects_hs256(plugin, signing_key):
    jws = _make_bundle(signing_key, alg="HS256")
    with pytest.raises(plugin.enforcement.BundleRejected):
        plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)


def test_verify_rejects_missing_claims(plugin, signing_key):
    jws = _make_bundle(signing_key, mutate_payload=lambda p: p.pop("budget"))
    with pytest.raises(plugin.enforcement.BundleRejected):
        plugin.enforcement.verify_bundle(jws, device_id=DEVICE_ID)


# ---------------------------------------------------------------------------
# AC-HRB-003 — veto pre_tool_call en modo normal (REQ-GOH-005)
# ---------------------------------------------------------------------------
def test_veto_blocks_tool_not_allowed(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(
        signing_key, allowed_tools=["web_search", "governance_status"]))
    state = plugin.enforcement.STATE
    directive = state.on_pre_tool_call(tool_name="terminal", args={})
    assert directive == {
        "action": "block",
        "message": (
            "BLOCKED by organization policy: tool 'terminal' is not allowed. "
            "Allowed tools: web_search, governance_status."
        ),
    }


def test_veto_allows_approved_tool(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, allowed_tools=["web_search"]))
    assert plugin.enforcement.STATE.on_pre_tool_call(tool_name="web_search", args={}) is None


def test_veto_counts_persist(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, allowed_tools=[]))
    plugin.enforcement.STATE.on_pre_tool_call(tool_name="terminal", args={})
    plugin.enforcement.STATE.on_pre_tool_call(tool_name="terminal", args={})
    counts = json.loads((home / "trust-agent" / "veto-counts.json").read_text())
    assert counts["terminal"] == 2


def test_empty_allowed_tools_blocks_all_except_internal(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, allowed_tools=[]))
    state = plugin.enforcement.STATE
    assert state.on_pre_tool_call(tool_name="web_search", args={})["action"] == "block"
    assert state.on_pre_tool_call(tool_name="terminal", args={})["action"] == "block"
    # governance_status SIEMPRE permitida (REQ-GOH-005/008):
    assert state.on_pre_tool_call(tool_name="governance_status", args={}) is None


# ---------------------------------------------------------------------------
# AC-HRB-004 — fail-closed (REQ-GOH-009/010)
# ---------------------------------------------------------------------------
def test_restricted_without_any_bundle(plugin, home):
    _enrolled(home)
    state = plugin.enforcement.STATE
    directive = state.on_pre_tool_call(tool_name="web_search", args={})
    assert directive["action"] == "block"
    assert "no valid governance bundle" in directive["message"]


def test_restricted_without_device_token(plugin, home):
    state = plugin.enforcement.STATE
    directive = state.on_pre_tool_call(tool_name="web_search", args={})
    assert directive["action"] == "block"
    assert "device not enrolled" in directive["message"]


def test_restricted_expired_cache_sync_unavailable(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, exp_delta=-30))
    state = plugin.enforcement.STATE
    directive = state.on_pre_tool_call(tool_name="web_search", args={})
    assert directive["action"] == "block"
    assert "expired, network sync unavailable" in directive["message"]


def test_restricted_pre_llm_call_notice(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, exp_delta=-30, unbind_mode="readonly"))
    result = plugin.enforcement.STATE.on_pre_llm_call()
    assert result is not None
    assert "RESTRICTED MODE" in result["context"]


def test_lock_mode_context(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, exp_delta=-30, unbind_mode="lock"))
    result = plugin.enforcement.STATE.on_pre_llm_call()
    assert "LOCKED" in result["context"]
    assert "re-enroll" in result["context"]


def test_normal_mode_no_pre_llm_context(plugin, signing_key, home):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key))
    # En modo normal el contexto lo inyecta el núcleo v2 — aquí: None.
    assert plugin.enforcement.STATE.on_pre_llm_call() is None


def test_recovery_to_normal_after_restricted(plugin, signing_key, home):
    _enrolled(home)
    state = plugin.enforcement.STATE
    assert state.on_pre_tool_call(tool_name="web_search", args={})["action"] == "block"
    _cache(home, _make_bundle(signing_key, allowed_tools=["web_search"]))
    assert state.on_pre_tool_call(tool_name="web_search", args={}) is None


def test_offline_grace_survives_repeated_evaluations(plugin, signing_key, home):
    """Regresión del bug de replay falso: el bundle CACHEADO se re-verifica
    en cada evaluación; sin remember=False la 2ª evaluación lo rechazaba
    como replay y la gracia offline (REQ-GOH-009/018) se rompía."""
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, allowed_tools=["web_search"]))
    state = plugin.enforcement.STATE
    for i in range(3):  # evaluaciones repetidas del MISMO caché, sin sync
        assert state.on_pre_tool_call(tool_name="web_search", args={}) is None, f"eval #{i+1}"
    # y el veto sigue operativo en la 4ª evaluación:
    assert state.on_pre_tool_call(tool_name="terminal", args={})["action"] == "block"


# ---------------------------------------------------------------------------
# Enroll — piezas unitarias (AC-HRB-001/002 parciales)
# ---------------------------------------------------------------------------
def test_keypair_generation_and_reuse(plugin, home):
    priv1, pub1, fp1 = plugin.enroll.load_or_create_keypair()
    priv2, pub2, fp2 = plugin.enroll.load_or_create_keypair()  # reutiliza
    assert fp1 == fp2 and priv1 == priv2
    mode = stat.S_IMODE(os.stat(home / "trust-agent" / "device_key.pem").st_mode)
    assert mode == 0o600
    serialization.load_pem_private_key(priv1.encode(), password=None)  # PKCS#8 válido


def test_device_token_store_permissions(plugin, home):
    path = plugin.enroll.save_device_token(
        device_id=DEVICE_ID, token="tok", org_code="acme", tenant_slug="acme")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    loaded = plugin.enroll.load_device_token()
    assert loaded["device_id"] == DEVICE_ID
    assert loaded["token"] == "tok"
    assert loaded["org_code"] == "acme"
    assert loaded["model_key"] == ""


def test_enroll_command_usage_without_args(plugin):
    out = plugin.enroll.cmd_trust_enroll("")
    assert out.startswith("Usage: /trust-enroll")
    # Sin credenciales jamás (D-HRB-01):
    assert "password" in out.lower()


def test_enroll_command_missing_id_token_file(plugin, home):
    out = plugin.enroll.cmd_trust_enroll("acme")
    assert "id_token file not found" in out
    assert "Usage" in out


# ---------------------------------------------------------------------------
# Auto-enroll ZERO-UI (mandato Miller) — precondiciones y ciclo, sin red
# (TRUST_AGENT_API apunta al puerto cerrado 127.0.0.1:1 del fixture home).
# ---------------------------------------------------------------------------
def test_auto_enroll_no_binding_does_nothing(plugin, home):
    """Sin binding de org el watcher NO intenta nada (cero side effects)."""
    assert plugin.enroll.auto_enroll_once() == "no_binding"
    assert not (home / "trust-agent" / "device_key.pem").exists()
    assert not (home / "trust-agent" / "device-token.json").exists()


def test_auto_enroll_binding_without_id_token(plugin, home):
    """Binding presente pero sin id_token de primera ejecución → espera."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("hermes-bridge\n")
    assert plugin.enroll.auto_enroll_once() == "no_id_token"
    # El keypair NO se genera sin id_token (no hay intento real).
    assert not (tdir / "device_key.pem").exists()


def test_auto_enroll_attempts_when_preconditions_met(plugin, home):
    """Binding + id_token → intenta el flujo real (red cerrada → network)."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("hermes-bridge\n")
    (tdir / "id-token.txt").write_text("fake-id-token\n")
    outcome = plugin.enroll.auto_enroll_once(poll_timeout_s=1.0)
    assert outcome == "network"  # puerto cerrado: intento real, fallo honesto
    # El keypair del dispositivo SÍ se genera (el flujo arrancó de verdad).
    assert (tdir / "device_key.pem").exists()
    # El intento queda registrado en el log local del plugin.
    log = (tdir / "plugin.log").read_text()
    assert "auto_enroll_attempt" in log and "network" in log


def test_auto_enroll_skips_when_already_enrolled(plugin, home):
    """Con device token vigente el watcher no reintenta (ya enrolado)."""
    plugin.enroll.save_device_token(
        device_id=DEVICE_ID, token="tok", org_code="acme", tenant_slug="acme")
    tdir = home / "trust-agent"
    (tdir / "org-code.txt").write_text("hermes-bridge\n")
    (tdir / "id-token.txt").write_text("fake-id-token\n")
    assert plugin.enroll.auto_enroll_once() == "already_enrolled"


def test_org_binding_env_overrides_file(plugin, home, monkeypatch):
    """El binding del instalador (env) gana sobre el archivo horneado."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("from-file\n")
    monkeypatch.setenv("TRUST_ORG_CODE", "from-env")
    assert plugin.enroll.org_binding() == "from-env"
    monkeypatch.delenv("TRUST_ORG_CODE")
    assert plugin.enroll.org_binding() == "from-file"


# ---------------------------------------------------------------------------
# Fix t_dd2f5f65 — race multi-proceso del auto-enroll (410s mutuos)
# ---------------------------------------------------------------------------
class _FakeControlPlane:
    """HTTP local que emula la semántica EXACTA de devices/service.py:

    cada ``POST /api/devices/enroll`` RESETEA ``enrollment_token_hash`` del
    device pending (upsert por fingerprint) y emite un token NUEVO; el
    exchange con un token != hash vigente responde 410 (one-time). Con la
    versión pre-fix del plugin, dos procesos enrollando a la vez sobre el
    mismo home se pisan el token mutuamente con este mismo servidor.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.enroll_posts = 0
        self.exchanges = 0
        self.gones = 0
        self.approved = False
        self.approve_after = 0  # aprobar tras N exchanges (0 = nunca)
        self.current_token: str | None = None  # hash vigente (None = consumido)
        self.last_issued: str | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/devices/enroll":
                    with outer._lock:
                        outer.enroll_posts += 1
                        outer.current_token = f"ten.{DEVICE_ID}.{uuid.uuid4().hex}"
                        outer.last_issued = outer.current_token
                    self._json(202, {
                        "device_id": DEVICE_ID, "status": "pending",
                        "enrollment_token": outer.current_token,
                    })
                    return
                if self.path == "/api/devices/enroll/exchange":
                    with outer._lock:
                        outer.exchanges += 1
                        if outer.approve_after and outer.exchanges >= outer.approve_after:
                            outer.approved = True
                        if req.get("enrollment_token") != outer.current_token:
                            outer.gones += 1
                            self._json(410, {"error": {
                                "code": "gone",
                                "message": "The enrollment token is invalid "
                                           "or already used."}})
                            return
                        if not outer.approved:
                            self._json(202, {"status": "pending"})
                            return
                        outer.current_token = None  # consumido (one-time)
                    self._json(200, {
                        "device_token": "device-token-live",
                        "device_id": DEVICE_ID, "status": "active",
                    })
                    return
                self._json(404, {"error": {"message": "not found"}})

            def log_message(self, format, *args):  # silenciar stderr del test
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "_FakeControlPlane":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture()
def fake_cp(home, monkeypatch):
    cp = _FakeControlPlane().start()
    monkeypatch.setenv("TRUST_AGENT_API", cp.url)
    yield cp
    cp.stop()


def _preconditions(home, org="acme"):
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text(org + "\n")
    (tdir / "id-token.txt").write_text("fake-id-token\n")
    return tdir


def test_auto_enroll_reuses_saved_token_without_repost(plugin, home, fake_cp):
    """REUSE-FIRST: con un enrollment token guardado para ESTA org, el
    intento canjea SIN re-POSTear /enroll (cada POST resetea el hash del
    token del device pending — la causa de los 410s mutuos)."""
    tdir = _preconditions(home)
    saved = f"ten.{DEVICE_ID}.saved-secret"
    plugin.enroll._save_enrollment_token(saved, DEVICE_ID, "acme")
    fake_cp.current_token = saved  # el hash vigente del server es este
    assert plugin.enroll.auto_enroll_once(poll_timeout_s=0.5) == "pending"
    assert fake_cp.enroll_posts == 0  # cero POST: reutilizó el guardado
    assert fake_cp.exchanges >= 1
    assert fake_cp.gones == 0


def test_auto_enroll_gone_discards_saved_and_reposts(plugin, home, fake_cp):
    """410 del token guardado (consumido/caducado/rechazado) → se descarta y
    se re-POSTea (recovery); el token recién emitido queda persistido."""
    _preconditions(home)
    stale = f"ten.{DEVICE_ID}.stale-secret"
    plugin.enroll._save_enrollment_token(stale, DEVICE_ID, "acme")
    # el hash vigente del server es OTRO (p. ej. lo reseteó un proceso con
    # la versión pre-fix): canjear el guardado → 410.
    fake_cp.current_token = f"ten.{DEVICE_ID}.other-secret"
    assert plugin.enroll.auto_enroll_once(poll_timeout_s=0.5) == "pending"
    assert fake_cp.gones == 1
    assert fake_cp.enroll_posts == 1  # re-POSTeó tras descartar el 410
    # el token guardado ahora es el recién emitido por ese POST:
    saved = plugin.enroll._load_enrollment_token()
    assert saved["enrollment_token"] == fake_cp.last_issued


def test_auto_enroll_busy_when_lock_held(plugin, home, monkeypatch):
    """El flock por home serializa: con el lock tomado por OTRO proceso, el
    intento degrada a "busy" (el ciclo reintenta) en vez de pisar el token."""
    import fcntl

    tdir = _preconditions(home)
    monkeypatch.setattr(plugin.enroll, "AUTOENROLL_LOCK_WAIT_S", 0.2)
    with (tdir / ".autoenroll.lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert plugin.enroll.auto_enroll_once(poll_timeout_s=1.0) == "busy"
    # liberado → el intento procede (puerto cerrado del fixture → network):
    assert plugin.enroll.auto_enroll_once(poll_timeout_s=1.0) == "network"


def test_auto_enroll_multiprocess_no_mutual_410(plugin, home, fake_cp):
    """Regresión del race REAL (t_dd2f5f65): 3 procesos con el plugin sobre
    el MISMO home (gateway standalone + TUI + gateway explícito), como en el
    E2E de la spec 023. Invariantes del fix: exactamente UN POST /enroll
    (nadie resetea el hash ajeno), CERO 410s servidos, y los 3 convergen
    (uno canjea, los demás ya_enrolled vía device-token.json compartido)."""
    tdir = _preconditions(home)
    fake_cp.approve_after = 4  # el admin aprueba tras 4 exchanges
    child = (
        "import importlib.util, sys, json, time\n"
        "spec = importlib.util.spec_from_file_location('enroll_ut', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "outcomes = []\n"
        "for _ in range(8):\n"
        "    out = m.auto_enroll_once(poll_timeout_s=2.0)\n"
        "    outcomes.append(out)\n"
        "    if out in ('active', 'already_enrolled'):\n"
        "        break\n"
        "    time.sleep(0.5)\n"
        "print(json.dumps(outcomes))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("TRUST_DEVICE_TOKEN", "TRUST_DEVICE_ID")}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(PLUGIN_DIR / "enroll.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(3)
    ]
    results = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=120)
        assert p.returncode == 0, stderr
        results.append(json.loads(stdout.strip().splitlines()[-1]))
    assert fake_cp.enroll_posts == 1, "solo UN proceso POSTea /enroll"
    assert fake_cp.gones == 0, "cero 410s mutuos"
    assert all(r and r[-1] in ("active", "already_enrolled") for r in results)
    assert any(r[-1] == "active" for r in results)
    # device token persistido UNA vez, visible para los 3 procesos:
    data = json.loads((tdir / "device-token.json").read_text())
    assert data["device_id"] == DEVICE_ID
    assert data["token"] == "device-token-live"


# ---------------------------------------------------------------------------
# D-HRB-03 amend (spec 022 v1.1.0, REQ-HRB-010) — expiración del device token
# y re-emisión directa (200) en el flujo de enroll
# ---------------------------------------------------------------------------
def _device_jwt(expires_in: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"device_id": DEVICE_ID, "user_id": "u", "tenant_id": "t",
         "jti": uuid.uuid4().hex, "iat": now, "exp": now + expires_in},
        "test-device-secret", algorithm="HS256",
    )


def test_device_token_expired_helper(plugin):
    """La lectura local del exp (sin verificar firma) enruta la recuperación."""
    assert not plugin.enroll.device_token_expired({"token": _device_jwt(900)})
    assert plugin.enroll.device_token_expired({"token": _device_jwt(-900)})
    # Token ilegible (env v1 / garbage): False — el server decide (401).
    assert not plugin.enroll.device_token_expired({"token": "not-a-jwt"})


def test_auto_enroll_retries_when_token_expired(plugin, home):
    """D-HRB-03 amend: token expirado en disco → el watcher YA NO corta en
    already_enrolled por presencia del archivo; re-intenta el flujo."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600),
    }))
    # Spec 022 v1.2.0: primero intenta el refresh con su propio token (red
    # cerrada del fixture → network, honesto); antes ni siquiera re-intentaba.
    assert plugin.enroll.auto_enroll_once() == "network"
    log = (tdir / "plugin.log").read_text()
    assert "device_token_refresh_failed" in log and "network" in log


def _fake_refresh(status, token=None):
    def fake_request(method, path, body=None, headers=None, timeout=15.0):
        assert method == "POST" and path == "/api/devices/token/refresh", path
        if status == 200:
            return 200, {"device_id": DEVICE_ID, "status": "active",
                         "device_token": token, "expires_in": 900}
        return status, {"error": {"code": "unauthorized",
                                  "message": "Invalid device token."}}
    return fake_request


def test_auto_enroll_refreshes_expired_token_without_browser(plugin, home,
                                                             monkeypatch):
    """Escritorio activado por token one-time del admin (spec 044): token
    expirado → refresh con su propio token; NUNCA abre el navegador."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    (tdir / "enrollment.json").write_text(json.dumps({
        "org": "acme", "portal_url": "http://portal.test",
        "activated_at": "2026-11-06T00:00:00Z"}))
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600),
        "model_base_url": "http://gw.test/litellm"}))
    opened = []
    monkeypatch.setattr(plugin.enroll, "attempt_browser_login",
                        lambda *a, **k: opened.append(1) or False)
    monkeypatch.setattr(plugin.enroll, "_request",
                        _fake_refresh(200, _device_jwt(900)))

    assert plugin.enroll.auto_enroll_once() == "already_enrolled"
    data = json.loads((tdir / "device-token.json").read_text())
    assert not plugin.enroll.device_token_expired({"token": data["token"]})
    assert data["model_base_url"] == "http://gw.test/litellm"
    assert opened == []


def test_refresh_concurrent_threads_single_post(plugin, home, monkeypatch):
    """Dos hilos del MISMO proceso (BundleSyncer + watcher) con el token
    expirado: el lock por home los serializa → exactamente UN POST de refresh;
    el segundo ve el token fresco y no vuelve a llamar al server."""
    import threading

    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600)}))
    monkeypatch.setattr(plugin.enroll, "AUTOENROLL_LOCK_WAIT_S", 10.0)
    posts = []
    in_post = threading.Event()
    release = threading.Event()
    inner = _fake_refresh(200, _device_jwt(900))

    def slow_request(*args, **kwargs):
        posts.append(1)
        in_post.set()
        assert release.wait(5), "the test never released the first POST"
        return inner(*args, **kwargs)

    monkeypatch.setattr(plugin.enroll, "_request", slow_request)
    results = []
    first = threading.Thread(
        target=lambda: results.append(plugin.enroll.refresh_expired_device_token()))
    second = threading.Thread(
        target=lambda: results.append(plugin.enroll.refresh_expired_device_token()))
    first.start()
    assert in_post.wait(5), "first refresh never reached the server"
    second.start()  # contends for the home lock while the first POST is in flight
    release.set()
    first.join(10)
    second.join(10)

    assert results == ["active", "active"]
    assert posts == [1], "only one refresh may reach the server"
    log = (tdir / "plugin.log").read_text()
    assert log.count("device_token_refreshed") == 1


def test_auto_enroll_admin_activated_rejected_needs_activation(plugin, home,
                                                               monkeypatch):
    """Refresh rechazado (revocado / muy viejo) en un escritorio activado por
    el admin → needs_activation; sin pestañas de login en cada ciclo."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    (tdir / "enrollment.json").write_text(json.dumps({
        "org": "acme", "portal_url": "http://portal.test"}))
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600)}))
    opened = []
    monkeypatch.setattr(plugin.enroll, "attempt_browser_login",
                        lambda *a, **k: opened.append(1) or False)
    monkeypatch.setattr(plugin.enroll, "_request", _fake_refresh(401))

    assert plugin.enroll.auto_enroll_once() == "needs_activation"
    assert opened == []
    log = (tdir / "plugin.log").read_text()
    assert "device_token_refresh_failed" in log and "rejected" in log


def test_auto_enroll_oidc_home_rejected_falls_back_to_browser(plugin, home,
                                                              monkeypatch):
    """Home enrolado por OIDC (sin enrollment.json de activación): si el
    refresh se rechaza, conserva el camino de login del navegador."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600)}))
    opened = []
    monkeypatch.setattr(plugin.enroll, "attempt_browser_login",
                        lambda *a, **k: opened.append(1) or False)
    monkeypatch.setattr(plugin.enroll, "_browser_login_on_cooldown", lambda: False)
    monkeypatch.setattr(plugin.enroll, "_request", _fake_refresh(401))

    assert plugin.enroll.auto_enroll_once() == "no_id_token"
    assert opened == [1]


def test_enroll_flow_direct_reissue_200(plugin, home, monkeypatch):
    """POST /api/devices/enroll → 200 con device_token directo (device active
    del mismo usuario) se persiste y retorna active (REQ-HRB-010b)."""
    seen = {}

    def fake_request(method, path, body=None, headers=None, timeout=15.0):
        seen["method"], seen["path"], seen["body"] = method, path, body
        return 200, {"device_id": DEVICE_ID, "status": "active",
                     "device_token": "device-token-reissued", "expires_in": 900}

    monkeypatch.setattr(plugin.enroll, "_request", fake_request)
    outcome, info = plugin.enroll._enroll_flow("acme", "fresh-id-token", 1.0)

    assert outcome == "active"
    assert info["reissued"] is True
    assert seen["method"] == "POST" and seen["path"] == "/api/devices/enroll"
    assert seen["body"]["id_token"] == "fresh-id-token"
    data = json.loads((home / "trust-agent" / "device-token.json").read_text())
    assert data["token"] == "device-token-reissued"
    assert data["device_id"] == DEVICE_ID
    assert data["org_code"] == "acme"


def test_recover_expired_token_full_flow(plugin, home, monkeypatch):
    """recover_expired_token: binding + id_token fresco → active con el token
    re-emitido en disco (camino que usa el BundleSyncer)."""
    tdir = home / "trust-agent"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "org-code.txt").write_text("acme\n")
    (tdir / "id-token.txt").write_text("fresh-id-token\n")
    (tdir / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": _device_jwt(-3600),
    }))

    def fake_request(method, path, body=None, headers=None, timeout=15.0):
        return 200, {"device_id": DEVICE_ID, "status": "active",
                     "device_token": _device_jwt(900), "expires_in": 900}

    monkeypatch.setattr(plugin.enroll, "_request", fake_request)
    assert plugin.enroll.recover_expired_token() == "active"
    data = json.loads((tdir / "device-token.json").read_text())
    assert not plugin.enroll.device_token_expired(
        {"token": data["token"]}), "el token en disco ya no está expirado"
    log = (tdir / "plugin.log").read_text()
    assert "token_recovered" in log

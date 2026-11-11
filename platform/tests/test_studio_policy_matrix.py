"""Matriz Policy Studio → capacidad REAL del agente (servidor + plugin).

Responde: "si el admin activa un switch en Policy Studio, ¿el agente del
escritorio puede de verdad usar esa capacidad? ¿y si lo apaga, queda
bloqueada?"

Para cada switch que promete una capacidad del agente:
  1. el admin guarda una política personal con ese switch (API real de la
     consola, ``POST /api/policy-bundles``) y el dispositivo baja su bundle
     (``GET /api/me/governance-bundle`` → compilación real del servidor);
  2. ese payload REAL se carga en el plugin de gobernanza real (verificación
     JWS RS256, modo normal) dentro de un HERMES_HOME de escritorio típico:
     sin ``terminal.backend`` en config.yaml (Hermes usa ``local``) y con la
     raíz de escritura en el workspace del usuario;
  3. cada tool call representativa pasa por los TRES hooks ``pre_tool_call``
     del plugin: allowlist (batch 1), capability_enforcement y
     config_enforcement.

ON → ninguna call vetada. OFF → todas vetadas.

Incidente que la motivó (2026-11-10, e2e-lab): "Terminal / shell" ON llegaba
al dispositivo y el agente no podía ejecutar nada — primero allowed_tools (el
compilador solo restaba), luego P-2 (``allowed_terminal_backends=["docker"]``
vetaba el backend local de un escritorio Windows sin Docker).

El payload se re-firma con una clave de test pineada en el plugin: lo que se
prueba es el CONTENIDO que compila el servidor; la firma del servidor ya la
cubre test_governance_bundle.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import USER_ALICE, bearer_headers, mint

PLATFORM = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM / "hermes" / "plugins" / "trust-agent-governance"
PUBLIC_URL = "https://93.184.216.34/"  # IP pública literal: el test no resuelve DNS

# Política base: todo apagado salvo el maestro de herramientas del agente.
BASE_UI = {
    "cli": True, "cli_terminal": False, "cli_files_read": False,
    "cli_files_write": False, "cli_web": False, "cli_code": False,
    "memory": False, "agents": False, "capabilities": False, "cron": False,
    "voice": False, "vault": False, "browser": False,
}

# switch → tool calls representativas ({ws} = workspace del usuario).
MATRIX: dict[str, list[tuple[str, dict]]] = {
    "cli_terminal": [
        ("terminal", {"command": "hostname"}),
        ("process_manage", {"action": "list"}),
    ],
    "cli_files_read": [
        ("read_file", {"path": "{ws}/notas.txt"}),
        ("search_files", {"pattern": "trust", "path": "{ws}"}),
    ],
    "cli_files_write": [
        ("write_file", {"path": "{ws}/salida.txt", "content": "ok"}),
        ("patch", {"path": "{ws}/salida.txt", "old_string": "ok", "new_string": "ok2"}),
    ],
    "cli_web": [
        ("web_search", {"query": "trust agent"}),
        ("web_extract", {"urls": [PUBLIC_URL]}),
    ],
    "cli_code": [("execute_code", {"code": "print(1)"})],
    "memory": [("memory", {"action": "add", "target": "memory", "content": "prefiere español"})],
    "agents": [("delegate_task", {"goal": "resume este texto"})],
    "capabilities": [("skills_list", {}), ("skill_view", {"name": "demo"})],
    "cron": [("cronjob_manage", {"action": "list"})],
    "voice": [("text_to_speech", {"text": "hola"})],
    "vault": [("browser_vault", {"action": "list"})],
    "browser": [
        ("browser_exec", {"url": PUBLIC_URL}),
        ("browser_cdp", {"method": "Page.navigate", "params": {"url": PUBLIC_URL}}),
    ],
}

_PKG = "tag_plugin_matrix"


def _load_plugin():
    """Plugin real, mismo orden de carga y re-vinculado que sus propios tests."""
    pkg_spec = importlib.util.spec_from_file_location(
        _PKG, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules[_PKG] = pkg
    pkg_spec.loader.exec_module(pkg)
    for sub in ("policy_contract", "state", "bundle_client", "enroll", "enforcement",
                "attestation", "capability_enforcement", "config_enforcement"):
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{sub}", PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    pkg.enforcement.enroll = pkg.enroll
    pkg.enforcement.policy_contract = pkg.policy_contract
    pkg.bundle_client.policy_contract = pkg.policy_contract
    pkg.bundle_client.state = pkg.state
    pkg.attestation.bundle_client = pkg.bundle_client
    pkg.attestation.state = pkg.state
    for mod in (pkg.capability_enforcement, pkg.config_enforcement):
        mod.enroll = pkg.enroll
        mod.policy_contract = pkg.policy_contract
        mod.enforcement = pkg.enforcement
    return pkg


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def desktop_home(tmp_path, monkeypatch, signing_key):
    """HERMES_HOME de un escritorio gobernado típico (Windows sin Docker)."""
    home, ws = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    ws.mkdir()
    (ws / "notas.txt").write_text("trust agent\n", encoding="utf-8")
    pub = tmp_path / "policy_pubkey.pem"
    pub.write_text(signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode())
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TRUST_AGENT_API", "http://127.0.0.1:1")  # sin red: usa el caché
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pub))
    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(ws))
    for var in ("TRUST_DEVICE_TOKEN", "HERMES_YOLO_MODE", "HERMES_MANAGED_DIR",
                "TRUST_ORG_CODE", "TRUST_DEVICE_TYPE"):
        monkeypatch.delenv(var, raising=False)
    # Sin terminal.backend → Hermes (y la regla P-2) usan "local".
    (home / "config.yaml").write_text("model:\n  provider: custom\n", encoding="utf-8")
    return home, ws


def _studio_bundle_payload(client, idp, desktop: dict) -> dict:
    """Payload que el servidor compila para alice con esta política de Studio."""
    from test_governance_bundle import _get_bundle

    admin = bearer_headers(mint(client, idp, "alice"))
    resp = client.post(
        "/api/policy-bundles",
        json={"name": "studio-matrix", "desktop": desktop, "user_id": USER_ALICE},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    bundle_id = resp.json()["id"]
    try:
        got = _get_bundle(client, idp)
        assert got.status_code == 200, got.text
        return pyjwt.decode(got.json()["bundle"], options={"verify_signature": False})
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)


def _plugin_with(home, signing_key, payload):
    """Plugin real en modo normal con ESE payload en caché."""
    trust_dir = home / "trust-agent"
    trust_dir.mkdir(parents=True, exist_ok=True)
    token = trust_dir / "device-token.json"
    token.write_text(json.dumps({
        "device_id": payload["device_id"], "token": "devtok.test",
        "enrolled_at": int(time.time()),
    }))
    os.chmod(token, 0o600)
    (trust_dir / "governance-bundle.jws").write_text(
        pyjwt.encode(payload, signing_key, algorithm="RS256"))
    plugin = _load_plugin()
    plugin.enforcement.STATE = plugin.enforcement.GovernanceState()
    cap = plugin.capability_enforcement
    cap._CONFIG_CACHE = (0.0, {})
    cap._PLUGIN_TOOLS = {}
    cap._VAULT_KINDS = {}
    cap._DEPTH = 0
    cap._CTX = None
    cap._BATCH1_ACTIVE = False
    plugin.config_enforcement._CONFIG_CACHE.update(mtime=-1.0, path="", data={})
    snap = plugin.enforcement.STATE.evaluate()
    assert snap["mode"] == "normal", snap
    return plugin


def _veto(plugin, tool: str, args: dict) -> str | None:
    """Mensaje del primer hook que veta la call, o None si pasa los tres."""
    for hook in (plugin.enforcement.STATE.on_pre_tool_call,
                 plugin.capability_enforcement.on_pre_tool_call,
                 plugin.config_enforcement.on_pre_tool_call):
        result = hook(tool_name=tool, args=json.loads(json.dumps(args)))
        if result and result.get("action") == "block":
            return str(result.get("message") or "blocked")
    return None


def _calls(switch: str, ws: Path) -> list[tuple[str, dict]]:
    root = str(ws).replace("\\", "/")
    return [(tool, json.loads(json.dumps(args).replace("{ws}", root)))
            for tool, args in MATRIX[switch]]


@pytest.mark.parametrize("switch", sorted(MATRIX))
def test_switch_on_makes_the_capability_usable(switch, client, idp, desktop_home, signing_key):
    home, ws = desktop_home
    payload = _studio_bundle_payload(client, idp, {**BASE_UI, switch: True})
    plugin = _plugin_with(home, signing_key, payload)
    vetoes = {}
    for tool, args in _calls(switch, ws):
        message = _veto(plugin, tool, args)
        if message:
            vetoes[tool] = message
    assert not vetoes, f"{switch} ON pero el agente sigue bloqueado: {vetoes}"


@pytest.mark.parametrize("switch", sorted(MATRIX))
def test_switch_off_blocks_the_capability(switch, client, idp, desktop_home, signing_key):
    home, ws = desktop_home
    payload = _studio_bundle_payload(client, idp, dict(BASE_UI))
    plugin = _plugin_with(home, signing_key, payload)
    allowed = [tool for tool, args in _calls(switch, ws) if _veto(plugin, tool, args) is None]
    assert not allowed, f"{switch} OFF pero el agente puede usar: {allowed}"

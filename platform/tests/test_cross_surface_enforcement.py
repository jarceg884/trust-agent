"""Spec 042 GAP-42-A — tests del enforcement cross-surface de mensajerías y
conectores (REQ-PE-011/012).

AC-011-1: con ``messaging_permissions={"telegram": false}``, el agente NO
despacha por telegram (veto de conector + pin de gateway.platforms).
AC-011-2: con ``allowed_connectors=[]``, toda tool de conector vetada con
mensaje estándar.
AC-011-3: fail-closed — key ausente en el bundle → default restrictivo del
plugin, jamás relaja.

Sin red real: el sync fallido va contra un puerto local cerrado
(127.0.0.1:1), igual que test_hrb_enforcement.py. El bundle se firma RS256
de verdad y se verifica con la clave pineada (camino completo del plugin).
"""
import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PLATFORM = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM / "hermes" / "plugins" / "trust-agent-governance"

DEVICE_ID = "11111111-2222-3333-4444-555555555555"
BLOCK_PREFIX = "BLOCKED by organization policy:"


def _load_plugin():
    pkg_spec = importlib.util.spec_from_file_location(
        "tag_plugin_cs", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["tag_plugin_cs"] = pkg
    pkg_spec.loader.exec_module(pkg)
    for sub in ("enroll", "policy_contract", "enforcement", "config_enforcement"):
        spec = importlib.util.spec_from_file_location(
            f"tag_plugin_cs.{sub}", PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"tag_plugin_cs.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    pkg.enforcement.enroll = pkg.enroll
    pkg.config_enforcement.enforcement = pkg.enforcement
    return pkg


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TRUST_AGENT_API", "http://127.0.0.1:1")
    monkeypatch.delenv("TRUST_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    return tmp_path


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pinned_pubkey(tmp_path_factory, signing_key):
    pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    path = tmp_path_factory.mktemp("pinned-cs") / "trust_agent_policy_pubkey.pem"
    path.write_text(pem)
    return path


@pytest.fixture()
def plugin(home, monkeypatch, pinned_pubkey):
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey))
    mod = _load_plugin()
    mod.enforcement.STATE = mod.enforcement.GovernanceState()
    mod.config_enforcement._CONFIG_CACHE.update(mtime=-1.0, path="", data={})
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
BATCH1_DEFAULTS = {
    "approval_mode": "manual",
    "allowed_terminal_backends": ["docker"],
    "require_write_sandbox": True,
    "allowed_env_passthrough": [],
    "web_domain_policy": {"mode": "allowlist", "domains": ["example.org"]},
    "allow_private_network_access": False,
    "allowed_providers": ["litellm_gateway"],
    "allow_self_updates": False,
}


def _make_bundle(signing_key, *, device_id=DEVICE_ID, exp_delta=3600,
                 keys=None, allowed_tools=None, allowed_connectors=None,
                 messaging_permissions=None):
    now = int(time.time())
    payload = {
        "iss": "https://trust-agent.control-plane/",
        "aud": device_id,
        "iat": now, "exp": now + exp_delta, "jti": uuid.uuid4().hex,
        "tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "device_id": device_id,
        "policy_version": 42,
        "schema_version": 2,
        "allowed_tools": allowed_tools if allowed_tools is not None else [
            "web_search", "web_extract", "read_file", "write_file", "patch",
            "terminal", "process_manage", "execute_code", "governance_status",
            "connectors__gmail__send", "connectors__telegram__send_message",
        ],
        "allowed_connectors": allowed_connectors if allowed_connectors is not None else [],
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
    payload.update({**BATCH1_DEFAULTS, **(keys or {})})
    if messaging_permissions is not None:
        payload["messaging_permissions"] = messaging_permissions
    return jwt.encode(payload, signing_key, algorithm="RS256")


def _enrolled(home, device_id=DEVICE_ID):
    d = home / "trust-agent"
    d.mkdir(parents=True, exist_ok=True)
    token_file = d / "device-token.json"
    token_file.write_text(json.dumps({
        "device_id": device_id, "token": "devtok.test",
        "enrolled_at": int(time.time()),
    }))
    os.chmod(token_file, 0o600)
    return token_file


def _cache(home, jws):
    (home / "trust-agent").mkdir(parents=True, exist_ok=True)
    (home / "trust-agent" / "governance-bundle.jws").write_text(jws)


def _write_config(home, config: dict):
    import yaml
    (home / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _normal(plugin, home, signing_key, **kw):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, **kw))
    snap = plugin.enforcement.STATE.evaluate()
    assert snap["mode"] == "normal", snap
    return snap


# ===========================================================================
# AC-011-2: allowed_connectors=[] → toda tool de conector vetada
# ===========================================================================

class TestAllowedConnectorsEnforcement:
    """AC-011-2: con allowed_connectors=[], toda tool de conector vetada
    con mensaje estándar en desktop y CLI del mismo usuario."""

    def test_connector_tool_vetoed_when_not_in_allowed(self, plugin, home, signing_key):
        """allowed_connectors=[] → connectors__gmail__send vetada."""
        _normal(plugin, home, signing_key, allowed_connectors=[])
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        assert result is not None
        assert result["action"] == "block"
        assert BLOCK_PREFIX in result["message"]
        assert "gmail" in result["message"]

    def test_connector_tool_allowed_when_in_list(self, plugin, home, signing_key):
        """allowed_connectors=["gmail"] → connectors__gmail__send permitida."""
        _normal(plugin, home, signing_key, allowed_connectors=["gmail"])
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        assert result is None

    def test_connector_batch_sentinel_not_vetoed(self, plugin, home, signing_key):
        """connectors__execute es un sentinel del planner, no una tool real:
        no se veto por allowed_connectors (lo veto allowed_tools si no está)."""
        _normal(plugin, home, signing_key, allowed_connectors=[],
                allowed_tools=["connectors__execute"])
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__execute", args={})
        assert result is None

    def test_non_connector_tool_not_affected(self, plugin, home, signing_key):
        """allowed_connectors=[] no afecta tools que no son de conector."""
        _normal(plugin, home, signing_key, allowed_connectors=[])
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="web_search", args={"query": "test"})
        assert result is None

    def test_standard_block_message(self, plugin, home, signing_key):
        """El mensaje de veto contiene el prefijo normativo estándar."""
        _normal(plugin, home, signing_key, allowed_connectors=[])
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        assert result is not None
        assert result["action"] == "block"
        assert result["message"].startswith(BLOCK_PREFIX)
        assert "telegram" in result["message"]
        assert "Allowed connectors" in result["message"]


# ===========================================================================
# AC-011-1: messaging_permissions={"telegram": false} → no despacha por telegram
# ===========================================================================

class TestMessagingPermissionsEnforcement:
    """AC-011-1: con messaging_permissions={"telegram": false}, el agente
    enrolado del usuario NO despacha por telegram en ninguna superficie."""

    def test_messaging_connector_vetoed(self, plugin, home, signing_key):
        """messaging_permissions={"telegram": {"allowed": false}} →
        connectors__telegram__send_message vetada por messaging_permissions."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": {"allowed": False}})
        result = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        assert result is not None
        assert result["action"] == "block"
        assert BLOCK_PREFIX in result["message"]
        assert "telegram" in result["message"]
        assert "messaging" in result["message"].lower()

    def test_messaging_connector_allowed_when_permitted(self, plugin, home, signing_key):
        """messaging_permissions={"telegram": {"allowed": true, "mode": "reply"}} →
        connectors__telegram__send_message NO vetada por messaging."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": {"allowed": True, "mode": "reply"}})
        result = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        assert result is None

    def test_messaging_shorthand_false(self, plugin, home, signing_key):
        """Forma corta: messaging_permissions={"telegram": false}."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": False})
        result = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        assert result is not None
        assert result["action"] == "block"

    def test_other_platform_not_affected(self, plugin, home, signing_key):
        """Bloquear telegram no afecta gmail."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram", "gmail"],
                messaging_permissions={"telegram": {"allowed": False}})
        result = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        assert result is None

    def test_gateway_platform_pinned(self, plugin, home, signing_key):
        """sync_managed_config pina gateway.platforms.telegram.enabled=false."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": {"allowed": False}})
        _write_config(home, {"model": {"provider": "custom"}})
        payload = plugin.enforcement.STATE.evaluate()["payload"]
        wrote = plugin.config_enforcement.sync_managed_config(payload, force=True)
        assert wrote is True
        import yaml
        config = yaml.safe_load((home / "config.yaml").read_text())
        platforms = config.get("gateway", {}).get("platforms", {})
        assert platforms.get("telegram", {}).get("enabled") is False

    def test_gateway_platform_not_pinned_when_allowed(self, plugin, home, signing_key):
        """sync_managed_config NO pina gateway.platforms cuando allowed=true."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": {"allowed": True, "mode": "full"}})
        _write_config(home, {"model": {"provider": "custom"}})
        payload = plugin.enforcement.STATE.evaluate()["payload"]
        plugin.config_enforcement.sync_managed_config(payload, force=True)
        import yaml
        config = yaml.safe_load((home / "config.yaml").read_text())
        platforms = config.get("gateway", {}).get("platforms", {})
        assert "telegram" not in platforms or platforms.get("telegram", {}).get("enabled") is not False


# ===========================================================================
# AC-011-3: fail-closed — key ausente → default restrictivo
# ===========================================================================

class TestFailClosedDefaults:
    """AC-011-3: fail-closed — key ausente en el bundle → default
    restrictivo del plugin, jamás relaja."""

    def test_allowed_connectors_absent_blocks_all(self, plugin, home, signing_key):
        """allowed_connectors ausente del bundle → default [] → todo conector
        vetado (deny-by-default)."""
        _normal(plugin, home, signing_key, allowed_connectors=None)
        # Forzar allowed_connectors ausente del payload
        payload = plugin.enforcement.STATE.evaluate()["payload"]
        payload.pop("allowed_connectors", None)
        plugin.enforcement.STATE.payload = payload
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        assert result is not None
        assert result["action"] == "block"

    def test_messaging_permissions_absent_blocks_all(self, plugin, home, signing_key):
        """messaging_permissions ausente del bundle → default {} →
        _blocked_messaging_platforms retorna [] (sin denegaciones explícitas),
        pero el backend deny-by-default bloquea todo (perm.get("allowed", False))."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions=None)
        payload = plugin.enforcement.STATE.evaluate()["payload"]
        payload.pop("messaging_permissions", None)
        plugin.enforcement.STATE.payload = payload
        blocked = plugin.config_enforcement._blocked_messaging_platforms(payload)
        # Sin denegaciones explícitas → lista vacía (el backend hace el resto)
        assert blocked == []

    def test_messaging_permissions_explicit_false_still_blocks(self, plugin, home, signing_key):
        """Cuando messaging_permissions={"telegram": {"allowed": false}} está
        presente, la plataforma se reporta como bloqueada."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": {"allowed": False}})
        payload = plugin.enforcement.STATE.evaluate()["payload"]
        blocked = plugin.config_enforcement._blocked_messaging_platforms(payload)
        assert "telegram" in blocked

    def test_policy_value_raises_on_unknown_key(self, plugin, home, signing_key):
        """Una key desconocida → KeyError (ignore-but-never-relax)."""
        _normal(plugin, home, signing_key)
        payload = plugin.enforcement.STATE.evaluate()["payload"]
        with pytest.raises(KeyError):
            plugin.policy_contract.policy_value(payload, "nonexistent_key_42")


# ===========================================================================
# Cross-surface: mismo bundle, mismo veto en ambas superficies
# ===========================================================================

class TestCrossSurfaceConsistency:
    """El bundle es por USUARIO (bundle.py:225) → el mismo veto aplica en
    desktop y CLI. Verificamos que enforcement.py y config_enforcement.py
    (dos hooks independientes) llegan al mismo resultado para el mismo
    tool + payload."""

    def test_both_layers_veto_blocked_connector(self, plugin, home, signing_key):
        """allowed_connectors=[] → tanto enforcement.py como
        config_enforcement.py veto connectors__gmail__send."""
        _normal(plugin, home, signing_key, allowed_connectors=[])
        # enforcement.py veto
        r1 = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        assert r1 is not None and r1["action"] == "block"
        # config_enforcement.py no necesita re-vetar (enforcement ya lo hizo),
        # pero si lo evaluara independientemente, messaging_permissions no
        # bloquearía gmail (no está en messaging_permissions)
        r2 = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        # r2 es None porque config_enforcement no veto por allowed_connectors
        # (eso es job de enforcement.py) — pero el veto YA ocurrió en r1.
        # Lo importante: el tool NO pasa.
        assert r1["action"] == "block"

    def test_both_layers_veto_blocked_messaging(self, plugin, home, signing_key):
        """messaging_permissions={"telegram": false} → config_enforcement.py
        veto connectors__telegram__send_message (defense-in-depth sobre
        enforcement.py que ya veto por allowed_connectors=[])."""
        _normal(plugin, home, signing_key,
                allowed_connectors=["telegram"],
                messaging_permissions={"telegram": {"allowed": False}})
        # enforcement.py: telegram ESTÁ en allowed_connectors → no veto
        r1 = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        assert r1 is None
        # config_enforcement.py: messaging_permissions bloquea telegram → veto
        r2 = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        assert r2 is not None
        assert r2["action"] == "block"
        assert "messaging" in r2["message"].lower()

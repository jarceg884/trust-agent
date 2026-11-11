"""Spec 030 (t_7e2852f8) — tests del enforcement batch 1: keys de
CONFIGURACIÓN del agente (P-1..P-5, P-18..P-20, P-26, P-29).

Sin red real: el sync fallido va contra un puerto local cerrado
(127.0.0.1:1), igual que test_hrb_enforcement.py. El bundle se firma RS256
de verdad y se verifica con la clave pineada (camino completo del plugin).

El paquete del plugin se carga con importlib (directorio con guion — mismo
patrón que el loader de plugins de Hermes).
"""
import importlib.util
import json
import os
import sys
import time
import types
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
        "tag_plugin_b1", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["tag_plugin_b1"] = pkg
    pkg_spec.loader.exec_module(pkg)
    subs = {}
    for sub in ("enroll", "policy_contract", "enforcement", "config_enforcement"):
        spec = importlib.util.spec_from_file_location(
            f"tag_plugin_b1.{sub}", PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"tag_plugin_b1.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
        subs[sub] = mod
    # enforcement importa enroll/policy_contract con el nombre del paquete:
    # re-vincular para que el paquete cargado sea UNA sola verdad.
    pkg.enforcement.enroll = pkg.enroll
    pkg.config_enforcement.enforcement = pkg.enforcement
    return pkg


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TRUST_AGENT_API", "http://127.0.0.1:1")  # puerto cerrado
    monkeypatch.delenv("TRUST_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
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
    path = tmp_path_factory.mktemp("pinned-b1") / "trust_agent_policy_pubkey.pem"
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
# Helpers: bundle schema 2 con las keys del batch 1 + config.yaml real
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
                 keys=None, drop_batch1_keys=False, allowed_tools=None):
    now = int(time.time())
    payload = {
        "iss": "https://trust-agent.control-plane/",
        "aud": device_id,
        "iat": now, "exp": now + exp_delta, "jti": uuid.uuid4().hex,
        "tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "device_id": device_id,
        "policy_version": 11,
        "schema_version": 2,
        "allowed_tools": allowed_tools if allowed_tools is not None else [
            "web_search", "web_extract", "read_file", "write_file", "patch",
            "terminal", "process_manage", "execute_code", "governance_status",
        ],
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
    if not drop_batch1_keys:
        payload.update({**BATCH1_DEFAULTS, **(keys or {})})
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
    """Estado normal: dispositivo enrolado + bundle válido en caché."""
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, **kw))
    snap = plugin.enforcement.STATE.evaluate()
    assert snap["mode"] == "normal", snap
    return snap


def _call(plugin, tool, args=None, session_id="sess-1"):
    return plugin.config_enforcement.on_pre_tool_call(
        tool_name=tool, args=args or {}, session_id=session_id)


def _veto_counts(home):
    path = home / "trust-agent" / "veto-counts.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ---------------------------------------------------------------------------
# AC-030-001 — approval_mode (P-1)
# ---------------------------------------------------------------------------
def test_approval_mode_config_off_below_floor(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"approval_mode": "manual"})
    _write_config(home, {"approvals": {"mode": "off"}})
    decision = _call(plugin, "terminal", {"command": "echo hi"})
    assert decision and decision["action"] == "block"
    assert decision["message"].startswith(BLOCK_PREFIX)
    assert "approval mode 'off'" in decision["message"]
    assert "manual" in decision["message"]


def test_approval_mode_env_yolo(plugin, home, signing_key, monkeypatch):
    _normal(plugin, home, signing_key, keys={"approval_mode": "smart"})
    _write_config(home, {"approvals": {"mode": "smart"}})
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    decision = _call(plugin, "terminal", {"command": "echo hi"})
    assert decision and decision["action"] == "block"
    assert "HERMES_YOLO_MODE" in decision["message"]


def test_approval_mode_session_yolo_reevaluated_per_call(plugin, home, signing_key, monkeypatch):
    """/yolo persistido: se re-evalúa POR LLAMADA (D-030-02)."""
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "manual", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"]})
    _write_config(home, {"approvals": {"mode": "manual"},
                         "terminal": {"backend": "local"}})

    yolo_state = {"sess-1": True}
    fake_tools = types.ModuleType("tools")
    fake_approval = types.ModuleType("tools.approval")
    fake_approval._YOLO_MODE_FROZEN = False
    fake_approval.is_session_yolo_enabled = lambda sid: yolo_state.get(sid, False)
    fake_tools.approval = fake_approval
    monkeypatch.setitem(sys.modules, "tools", fake_tools)
    monkeypatch.setitem(sys.modules, "tools.approval", fake_approval)

    decision = _call(plugin, "terminal", {"command": "echo hi"})
    assert decision and decision["action"] == "block"
    assert "session /yolo" in decision["message"]

    yolo_state["sess-1"] = False  # /yolo off → restaura al vuelo
    assert _call(plugin, "terminal", {"command": "echo hi"}) is None


def test_approval_mode_floor_off_allows_smart(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off",
        "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"],
    })
    _write_config(home, {"approvals": {"mode": "smart"}})
    assert _call(plugin, "terminal", {"command": "echo hi"}) is None


def test_approval_mode_only_exec_tools(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"approval_mode": "manual"})
    _write_config(home, {"approvals": {"mode": "off"}})
    # read_file no es tool de ejecución: el piso de approval no la veta.
    assert _call(plugin, "read_file", {"path": "/tmp/x"}) is None


# ---------------------------------------------------------------------------
# AC-030-002 — allowed_terminal_backends (P-2)
# ---------------------------------------------------------------------------
def test_terminal_backend_not_allowed(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"approval_mode": "off"})  # backends: ["docker"]
    _write_config(home, {"terminal": {"backend": "local"}})
    for tool in ("terminal", "process_manage"):
        decision = _call(plugin, tool, {"command": "echo hi"})
        assert decision and decision["action"] == "block"
        assert "terminal backend 'local' is not allowed" in decision["message"]


def test_terminal_backend_wildcard_and_default(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "allowed_terminal_backends": ["*"], "approval_mode": "off"})
    # sin terminal.backend en config → default local; con "*" pasa.
    _write_config(home, {})
    assert _call(plugin, "terminal", {"command": "echo hi"}) is None


# ---------------------------------------------------------------------------
# AC-030-003 — require_write_sandbox (P-5)
# ---------------------------------------------------------------------------
def test_write_sandbox_outside_root(plugin, home, signing_key, tmp_path, monkeypatch):
    root = tmp_path / "ws-root"
    root.mkdir()
    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(root))
    _normal(plugin, home, signing_key, keys={"approval_mode": "off"})
    decision = _call(plugin, "write_file", {"path": "/etc/batch1-test.txt", "content": "x"})
    assert decision and decision["action"] == "block"
    assert "outside the sandbox" in decision["message"]
    # dentro de la raíz pasa
    assert _call(plugin, "write_file", {"path": str(root / "a.txt"), "content": "x"}) is None


def test_write_sandbox_cwd_when_env_unset(plugin, home, signing_key, tmp_path, monkeypatch):
    work = tmp_path / "session-ws"
    work.mkdir()
    monkeypatch.chdir(work)
    _normal(plugin, home, signing_key, keys={"approval_mode": "off"})
    decision = _call(plugin, "patch", {"path": "/tmp/batch1-escape.txt"})
    assert decision and decision["action"] == "block"
    assert _call(plugin, "write_file", {"path": "relative-note.md"}) is None


def test_write_sandbox_disabled(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"require_write_sandbox": False})
    decision = _call(plugin, "write_file", {"path": "/etc/batch1-test.txt"})
    assert decision is None


# ---------------------------------------------------------------------------
# AC-030-004 — allowed_env_passthrough (P-18)
# ---------------------------------------------------------------------------
def test_env_passthrough_explicit_assignment(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"]})
    _write_config(home, {})
    decision = _call(plugin, "terminal", {"command": "SECRET_TOKEN=x echo hi"})
    assert decision and decision["action"] == "block"
    assert "SECRET_TOKEN" in decision["message"]
    assert "env passthrough" in decision["message"]


def test_env_passthrough_arg_position_not_flagged(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"]})
    _write_config(home, {})
    # foo=bar es ARGUMENTO de grep, no asignación de env del child.
    assert _call(plugin, "terminal", {"command": "grep foo=bar file.txt"}) is None


def test_env_passthrough_config_passthrough_and_wildcard(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": []})
    _write_config(home, {"terminal": {"backend": "local",
                                      "env_passthrough": ["SECRET_X"]}})
    decision = _call(plugin, "terminal", {"command": "echo hi"})
    assert decision and "SECRET_X" in decision["message"]

    # con "*" en la allowlist todo pasa
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"]})
    assert _call(plugin, "terminal", {"command": "SECRET_X=1 echo hi"}) is None


def test_env_passthrough_export_and_execute_code(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"]})
    _write_config(home, {})
    decision = _call(plugin, "execute_code", {"code": "os.system('export TRUST_SECRET=1')"})
    assert decision and "TRUST_SECRET" in decision["message"]


# ---------------------------------------------------------------------------
# AC-030-005 — web_domain_policy (P-19)
# ---------------------------------------------------------------------------
def test_web_allowlist_blocks_unlisted_domain(plugin, home, signing_key):
    _normal(plugin, home, signing_key)  # allowlist ["example.org"]
    decision = _call(plugin, "web_extract", {"urls": ["https://example.org/a",
                                                      "https://evil.com/b"]})
    assert decision and decision["action"] == "block"
    assert "evil.com" in decision["message"]
    assert "allowlist" in decision["message"]


def test_web_allowlist_subdomain_passes(plugin, home, signing_key):
    _normal(plugin, home, signing_key)
    assert _call(plugin, "web_extract", {"urls": ["https://sub.example.org/a"]}) is None


def test_web_search_requires_site_filter(plugin, home, signing_key):
    _normal(plugin, home, signing_key)
    decision = _call(plugin, "web_search", {"query": "random query"})
    assert decision and "site:" in decision["message"]
    assert _call(plugin, "web_search", {"query": "site:example.org docs"}) is None


def test_web_blocklist_vetoes_and_syncs_config(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "web_domain_policy": {"mode": "blocklist", "domains": ["evil.com"]},
        "allow_private_network_access": True})
    _write_config(home, {"model": {"default": "glm-5.2"}})
    decision = _call(plugin, "web_extract", {"urls": ["https://evil.com/b"]})
    assert decision and "blocked" in decision["message"]
    assert _call(plugin, "web_extract", {"urls": ["https://example.org/a"]}) is None
    # managed sync: security.website_blocklist impuesto en config.yaml
    plugin.config_enforcement.sync_managed_config(
        plugin.enforcement.STATE.payload)
    import yaml

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["security"]["website_blocklist"] == {
        "enabled": True, "domains": ["evil.com"], "shared_files": []}
    assert cfg["model"]["default"] == "glm-5.2"  # el resto se preserva
    # idempotente: segunda pasada no escribe
    assert plugin.config_enforcement.sync_managed_config(
        plugin.enforcement.STATE.payload) is False


def test_web_policy_terminal_command_url(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allow_private_network_access": True})
    _write_config(home, {})
    decision = _call(plugin, "terminal", {"command": "curl https://evil.com/x"})
    assert decision and "evil.com" in decision["message"]


# ---------------------------------------------------------------------------
# AC-030-006 — allow_private_network_access (P-20)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "http://10.0.0.1/x", "http://127.0.0.1:8000/health",
    "http://192.168.1.5/x", "http://localhost/x", "http://[::1]/x",
])
def test_private_network_blocked(plugin, home, signing_key, url):
    _normal(plugin, home, signing_key, keys={
        "web_domain_policy": {"mode": "open", "domains": []},
        "allow_private_network_access": False})
    decision = _call(plugin, "web_extract", {"urls": [url]})
    assert decision and decision["action"] == "block"
    assert "private networks" in decision["message"]


def test_private_network_public_host_passes(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "web_domain_policy": {"mode": "open", "domains": []},
        "allow_private_network_access": False})
    assert _call(plugin, "web_extract", {"urls": ["http://8.8.8.8/x"]}) is None


def test_private_network_sync_forces_allow_private_urls_false(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "web_domain_policy": {"mode": "open", "domains": []}})
    _write_config(home, {"security": {"allow_private_urls": True}})
    plugin.config_enforcement.sync_managed_config(
        plugin.enforcement.STATE.payload)
    import yaml

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["security"]["allow_private_urls"] is False


# ---------------------------------------------------------------------------
# AC-030-007 — allowed_providers (P-26, pre_llm_call — contexto + conteo)
# ---------------------------------------------------------------------------
def test_provider_offender_context_and_count(plugin, home, signing_key):
    _normal(plugin, home, signing_key)  # allowed_providers ["litellm_gateway"]
    _write_config(home, {
        "model": {"default": "glm-5.2", "provider": "custom",
                  "base_url": "http://127.0.0.1:4000"},
        "fallback_providers": ["anthropic"],
    })
    result = plugin.config_enforcement.on_pre_llm_call(session_id="sess-1")
    assert result and "context" in result
    assert "anthropic" in result["context"]
    assert result["context"].startswith("[organization governance]")
    assert "BLOCKED by organization policy" in result["context"]
    # el veto queda contado para la próxima attestation
    assert _veto_counts(home).get("pre_llm_call", 0) >= 1


def test_provider_gateway_profile_no_false_positive(plugin, home, signing_key):
    """El perfil gobernado real (custom + base_url = gateway) se clasifica
    litellm_gateway: SIN mensaje de provider (control negativo)."""
    _normal(plugin, home, signing_key)
    _write_config(home, {
        "model": {"default": "glm-5.2", "provider": "custom",
                  "base_url": "http://127.0.0.1:4000"},
    })
    result = plugin.config_enforcement.on_pre_llm_call(session_id="sess-1")
    assert result is None or "provider" not in result.get("context", "")


def test_provider_routing_and_auxiliary_collected(plugin, home, signing_key):
    _normal(plugin, home, signing_key)
    _write_config(home, {
        "model": {"provider": "custom", "base_url": "http://127.0.0.1:4000"},
        "provider_routing": {"only": ["openrouter"]},
        "auxiliary": {"title_generation": {"provider": "groq"}},
    })
    offenders = plugin.config_enforcement.provider_offenders(
        plugin.enforcement.STATE.payload)
    assert "openrouter" in offenders and "groq" in offenders


# ---------------------------------------------------------------------------
# AC-030-008 — allow_self_updates (P-29)
# ---------------------------------------------------------------------------
def test_self_update_hermes_update_blocked(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"]})
    _write_config(home, {})
    decision = _call(plugin, "terminal", {"command": "hermes update"})
    assert decision and decision["action"] == "block"
    assert "self-updates are not permitted" in decision["message"]


def test_self_update_plugin_install_blocked(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"]})
    _write_config(home, {})
    for cmd in ("hermes plugin install foo", "hermes plugins add foo",
                "uv pip install hermes-agent"):
        decision = _call(plugin, "terminal", {"command": cmd})
        assert decision and "self-updates" in decision["message"], cmd


def test_self_update_benign_command_passes(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"]})
    _write_config(home, {})
    assert _call(plugin, "terminal", {"command": "ls -la"}) is None


def test_self_update_allowed_when_policy_true(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allow_self_updates": True})
    _write_config(home, {})
    assert _call(plugin, "terminal", {"command": "hermes update"}) is None


def test_self_update_sync_forces_allow_lazy_installs_false(plugin, home, signing_key):
    _normal(plugin, home, signing_key)
    _write_config(home, {"security": {"allow_lazy_installs": True}})
    plugin.config_enforcement.sync_managed_config(
        plugin.enforcement.STATE.payload)
    import yaml

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["security"]["allow_lazy_installs"] is False


# ---------------------------------------------------------------------------
# AC-030-009 — auditoría de vetos (log + conteo attestation)
# ---------------------------------------------------------------------------
def test_vetoes_audited_in_log_and_counts(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"approval_mode": "off"})
    _write_config(home, {"terminal": {"backend": "local"}})
    _call(plugin, "terminal", {"command": "echo hi"})
    _call(plugin, "terminal", {"command": "echo hi"})
    counts = _veto_counts(home)
    assert counts.get("terminal") == 2
    log_lines = (home / "trust-agent" / "plugin.log").read_text(encoding="utf-8")
    assert log_lines.count('"layer": "config_batch1"') >= 2
    assert "terminal_backend_not_allowed" in log_lines


# ---------------------------------------------------------------------------
# AC-030-010 — deny-by-default (schema 1) + cero parches + restricted
# ---------------------------------------------------------------------------
def test_deny_by_default_schema1_bundle(plugin, home, signing_key):
    """Bundle VIEJO (schema 1, sin keys del batch): defaults restrictivos del
    PLUGIN (REQ-GOH-023): backend [] → terminal vetado; approval manual."""
    _normal(plugin, home, signing_key, drop_batch1_keys=True)
    _write_config(home, {"approvals": {"mode": "smart"},
                         "terminal": {"backend": "docker"}})
    decision = _call(plugin, "terminal", {"command": "echo hi"})
    assert decision and decision["action"] == "block"
    # approval floor manual (default restrictivo) < smart efectivo → veto
    assert "approval mode 'smart'" in decision["message"]


def test_restricted_mode_batch1_returns_none(plugin, home, signing_key):
    """Sin bundle válido (restricted): enforcement ya bloquea TODO; el batch 1
    no duplica vetos (fail-closed heredado de spec 019 §5.4)."""
    _enrolled(home)  # sin caché de bundle → restricted
    assert _call(plugin, "terminal", {"command": "echo hi"}) is None
    assert plugin.config_enforcement.on_pre_llm_call(session_id="s") is None


def test_no_hermes_cli_imports_in_batch1_module():
    """AC-GOH-016 extendido al módulo nuevo: cero imports de hermes_cli."""
    src = (PLUGIN_DIR / "config_enforcement.py").read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in src.splitlines()
        if line.strip().startswith("import hermes_cli")
        or line.strip().startswith("from hermes_cli")
    ]
    assert not offenders, f"imports de hermes_cli: {offenders}"


def test_internal_tool_never_vetoed(plugin, home, signing_key):
    _normal(plugin, home, signing_key)
    _write_config(home, {"approvals": {"mode": "off"}})
    assert _call(plugin, "governance_status", {}) is None


# ---------------------------------------------------------------------------
# P-26 — provider/model/credentials are organization-owned and pinned
# ---------------------------------------------------------------------------
def test_sync_pins_org_model_provider_and_credentials(plugin, home):
    """A hand-edited config cannot keep another provider, model or key:
    every managed sync restores the signed policy + device-issued credential."""
    import yaml

    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID,
        "token": "devtok.test",
        "model_base_url": "https://litellm.org.example/v1",
        "model_key": "sk-org-issued",
    }), encoding="utf-8")
    _write_config(home, {
        "model": {
            "default": "my-own-model",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-user-own",
        },
        "fallback_providers": ["anthropic"],
        "provider_routing": {"only": ["openai"]},
        "auxiliary": {"compression": {"provider": "openai"}},
        "moa": {"provider": "openai"},
        "providers": {"openai": {"api_key": "sk-user"}},
    })
    payload = {
        "jti": "pin-test",
        "allowed_providers": ["litellm_gateway"],
        "policy_models": ["deepseek-v4.1-flash-1017"],
        "policy_default_model": "deepseek-v4.1-flash-1017",
    }
    assert plugin.config_enforcement.sync_managed_config(payload, force=True) is True

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["model"]["default"] == "deepseek-v4.1-flash-1017"
    assert cfg["model"]["provider"] == "custom"
    assert cfg["model"]["base_url"] == "https://litellm.org.example/v1"
    assert cfg["model"]["api_key"] == "sk-org-issued"
    assert cfg["fallback_providers"] == []
    assert cfg["provider_routing"] == {}
    assert cfg["auxiliary"] == {}
    assert cfg["moa"] == {}
    assert cfg["providers"] == {}


# ---------------------------------------------------------------------------
# Perfiles gobernados — P-26 por perfil + strip del opt-out del multiplexor
# (incidente en vivo 2026-11-10: perfiles clonados con gateway.standalone
# heredado nacían sin servicio → profile_mismatch en cada sesión)
# ---------------------------------------------------------------------------
def _write_profile(home, name: str, config: dict):
    import yaml

    d = home / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


_PROFILE_PAYLOAD = {
    "jti": "profiles-test",
    "allowed_providers": ["litellm_gateway"],
    "approved_models": ["glm-5.2", "deepseek-v4-flash"],
    "policy_models": ["glm-5.2", "deepseek-v4-flash"],
    "policy_default_model": "glm-5.2",
}


def test_profile_standalone_opt_out_stripped_and_idempotent(plugin, home):
    import yaml

    _write_profile(home, "vmware", {
        "gateway": {"standalone": True, "voice": {"enabled_voice_only": False}},
        "model": {"default": "glm-5.2", "provider": "custom",
                  "base_url": "https://litellm.org.example/v1"},
    })
    assert plugin.config_enforcement.sync_managed_config(
        dict(_PROFILE_PAYLOAD), force=True) is True
    cfg = yaml.safe_load(
        (home / "profiles" / "vmware" / "config.yaml").read_text(encoding="utf-8"))
    assert "standalone" not in cfg["gateway"]
    assert cfg["gateway"]["voice"] == {"enabled_voice_only": False}
    assert cfg["model"]["default"] == "glm-5.2"  # aprobado: se conserva
    # Idempotente: ni el config principal ni los perfiles cambian → False.
    assert plugin.config_enforcement.sync_managed_config(
        dict(_PROFILE_PAYLOAD), force=True) is False


def test_profile_rogue_config_pinned_and_model_clamped(plugin, home):
    """Un perfil con config editado a mano no escapa del gateway de la org:
    provider/base_url/api_key pineados (P-26), puertas alternativas cerradas
    y el modelo clampeado a los aprobados. Un perfil con modelo aprobado lo
    conserva; un cascarón sin marcadores jamás se toca."""
    import yaml

    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID,
        "token": "devtok.test",
        "model_base_url": "https://litellm.org.example/v1",
        "model_key": "sk-org-issued",
    }), encoding="utf-8")
    _write_profile(home, "ops", {
        "model": {"default": "gpt-4o", "provider": "openai",
                  "base_url": "https://api.openai.com/v1",
                  "api_key": "sk-rogue"},
        "providers": {"openai": {"api_key": "sk-rogue"}},
        "gateway": {"standalone": True},
    })
    _write_profile(home, "finanzas", {
        "model": {"default": "deepseek-v4-flash", "provider": "custom"},
    })
    shell = home / "profiles" / "cascaron"
    shell.mkdir(parents=True)
    (shell / "basura.txt").write_text("x", encoding="utf-8")

    assert plugin.config_enforcement.sync_managed_config(
        dict(_PROFILE_PAYLOAD), force=True) is True

    ops = yaml.safe_load(
        (home / "profiles" / "ops" / "config.yaml").read_text(encoding="utf-8"))
    assert ops["model"]["default"] == "glm-5.2"  # gpt-4o no aprobado → default de la policy
    assert ops["model"]["provider"] == "custom"
    assert ops["model"]["base_url"] == "https://litellm.org.example/v1"
    assert ops["model"]["api_key"] == "sk-org-issued"
    assert ops["providers"] == {}
    assert "standalone" not in ops["gateway"]

    fin = yaml.safe_load(
        (home / "profiles" / "finanzas" / "config.yaml").read_text(encoding="utf-8"))
    assert fin["model"]["default"] == "deepseek-v4-flash"  # aprobado: se conserva
    assert fin["model"]["base_url"] == "https://litellm.org.example/v1"

    # Cascarón sin marcadores de identidad: el plugin no crea su config.
    assert not (shell / "config.yaml").exists()


# ---------------------------------------------------------------------------
# Perfiles gobernados — la sesión de un perfil usa el ENROLAMIENTO del
# dispositivo (incidente en vivo 2026-11-10: el gateway multiplexado ya servía
# vmware/huawei, pero cada turno del perfil evaluaba profiles/<p>/trust-agent
# — sin device token — y quedaba en modo restringido "device not enrolled":
# todas las tools bloqueadas y sin pin P-26 del config del perfil)
# ---------------------------------------------------------------------------
OTHER_DEVICE_ID = "99999999-8888-7777-6666-555555555555"

_CORE_SRC = Path(os.environ.get("HERMES_AGENT_SRC", "/usr/local/lib/hermes-agent"))


def _profile_context(monkeypatch, home, name: str) -> Path:
    """Contexto de sesión de un perfil vía env (proceso dedicado
    ``--profile <name>``): HERMES_HOME → ``<root>/profiles/<name>``."""
    profile = home / "profiles" / name
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(profile))
    return profile


@pytest.fixture()
def core_constants(monkeypatch):
    """``hermes_constants`` REAL del core: el override context-local es la
    vía del gateway multiplexado (env HERMES_HOME = raíz, sesión = perfil)."""
    path = _CORE_SRC / "hermes_constants.py"
    if not path.is_file():
        pytest.skip("hermes-agent core source not available")
    spec = importlib.util.spec_from_file_location("hermes_constants", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setitem(sys.modules, "hermes_constants", mod)
    return mod


def test_profile_session_is_governed_by_the_device_enrollment(
        plugin, home, signing_key, monkeypatch):
    _enrolled(home)
    _cache(home, _make_bundle(signing_key))
    profile = _profile_context(monkeypatch, home, "vmware")

    assert plugin.enroll.trust_dir() == home / "trust-agent"
    assert plugin.bundle_client.trust_dir() == home / "trust-agent"
    assert plugin.enroll.load_device_token()["device_id"] == DEVICE_ID
    assert plugin.bundle_client.load_device_token()["device_id"] == DEVICE_ID
    snap = plugin.enforcement.STATE.evaluate()
    assert snap["mode"] == "normal", snap
    assert snap["payload"]["policy_version"] == 11
    # El perfil no recibe una copia del token: una sola identidad por dispositivo.
    assert not (profile / "trust-agent" / "device-token.json").exists()


def test_multiplexed_profile_session_uses_device_enrollment(
        plugin, home, signing_key, core_constants):
    (home / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    _enrolled(home)
    _cache(home, _make_bundle(signing_key))
    profile = home / "profiles" / "vmware"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")

    token = core_constants.set_hermes_home_override(profile)
    try:
        assert plugin.enroll.hermes_home() == profile  # la sesión ES el perfil
        assert plugin.enroll.trust_dir() == home / "trust-agent"
        assert plugin.bundle_client.trust_dir() == home / "trust-agent"
        snap = plugin.enforcement.GovernanceState().evaluate()
        assert snap["mode"] == "normal", snap
    finally:
        core_constants.reset_hermes_home_override(token)


def test_profile_with_own_enrollment_keeps_its_identity(plugin, home, monkeypatch):
    """D-030-09: un perfil enrolado APARTE (workers del servidor) conserva
    su propia identidad — nunca hereda la del host."""
    _enrolled(home, device_id=DEVICE_ID)
    profile = _profile_context(monkeypatch, home, "worker")
    _enrolled(profile, device_id=OTHER_DEVICE_ID)

    assert plugin.enroll.trust_dir() == profile / "trust-agent"
    assert plugin.enroll.load_device_token()["device_id"] == OTHER_DEVICE_ID
    assert plugin.bundle_client.load_device_token()["device_id"] == OTHER_DEVICE_ID


def test_profile_of_unenrolled_device_stays_restricted(plugin, home, monkeypatch):
    profile = _profile_context(monkeypatch, home, "vmware")
    snap = plugin.enforcement.STATE.evaluate()
    assert snap["mode"] == "restricted"
    assert "not enrolled" in snap["cause"]
    assert plugin.enroll.trust_dir() == profile / "trust-agent"


def test_profile_session_turn_pins_its_own_config(plugin, home, signing_key, monkeypatch):
    """Con el perfil en modo normal, el pre_llm_call del turno pinea SU
    config.yaml (P-26) — antes el modo restringido retornaba sin sync."""
    import yaml

    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID,
        "token": "devtok.test",
        "model_base_url": "https://litellm.org.example/v1",
        "model_key": "sk-org-issued",
    }), encoding="utf-8")
    _cache(home, _make_bundle(signing_key, keys={
        "policy_models": ["glm-5.2"], "policy_default_model": "glm-5.2"}))
    profile = _profile_context(monkeypatch, home, "ops")
    (profile / "config.yaml").write_text(yaml.safe_dump({
        "model": {"default": "gpt-4o", "provider": "openai",
                  "base_url": "https://api.openai.com/v1", "api_key": "sk-rogue"},
        "providers": {"openai": {"api_key": "sk-rogue"}},
    }), encoding="utf-8")

    plugin.config_enforcement.on_pre_llm_call(session_id="sess-ops")

    cfg = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["model"]["provider"] == "custom"
    assert cfg["model"]["base_url"] == "https://litellm.org.example/v1"
    assert cfg["model"]["api_key"] == "sk-org-issued"
    assert cfg["model"]["default"] == "glm-5.2"
    assert cfg["providers"] == {}


_MANAGED_WRITE_REASON = "organization-managed files"


def test_profile_session_cannot_write_device_credentials(
        plugin, home, signing_key, monkeypatch):
    (home / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    _enrolled(home)
    # Sin sandbox de escritura: el bloqueo debe venir de la protección de
    # archivos gestionados, no de otra regla.
    _cache(home, _make_bundle(signing_key, keys={"require_write_sandbox": False}))
    _profile_context(monkeypatch, home, "vmware")
    assert plugin.enforcement.STATE.evaluate()["mode"] == "normal"

    for target in (home / "trust-agent" / "device-token.json",
                   home / "trust-agent" / "governance-bundle.jws",
                   home / "config.yaml"):
        decision = _call(plugin, "write_file", {"path": str(target), "content": "{}"})
        assert decision and decision["action"] == "block", target
        assert _MANAGED_WRITE_REASON in decision["message"], decision


def test_root_session_cannot_write_a_profile_config(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"require_write_sandbox": False})
    _write_profile(home, "vmware", {"model": {"default": "glm-5.2"}})
    target = home / "profiles" / "vmware" / "config.yaml"
    decision = _call(plugin, "write_file", {"path": str(target), "content": "x: 1\n"})
    assert decision and decision["action"] == "block"
    assert _MANAGED_WRITE_REASON in decision["message"], decision


def test_governance_plugin_stays_enabled_in_every_home(plugin, home):
    """El manager de plugins de cada home solo carga la gobernanza si su
    config la habilita (y ``plugins.disabled`` gana): el sync la re-pinea en
    el home propio y en cada perfil vivo."""
    import yaml

    gov = plugin.config_enforcement.GOVERNANCE_PLUGIN
    _write_config(home, {"plugins": {"enabled": ["memory-x"], "disabled": [gov]}})
    _write_profile(home, "vmware", {"model": {"default": "glm-5.2"},
                                    "plugins": {"disabled": [gov, "other"]}})
    _write_profile(home, "huawei", {"model": {"default": "glm-5.2"}})

    assert plugin.config_enforcement.sync_managed_config(
        dict(_PROFILE_PAYLOAD), force=True) is True

    root = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert root["plugins"] == {"enabled": ["memory-x", gov], "disabled": []}
    vmware = yaml.safe_load(
        (home / "profiles" / "vmware" / "config.yaml").read_text(encoding="utf-8"))
    assert vmware["plugins"] == {"disabled": ["other"], "enabled": [gov]}
    huawei = yaml.safe_load(
        (home / "profiles" / "huawei" / "config.yaml").read_text(encoding="utf-8"))
    assert huawei["plugins"] == {"enabled": [gov]}
    # Idempotente.
    assert plugin.config_enforcement.sync_managed_config(
        dict(_PROFILE_PAYLOAD), force=True) is False


@pytest.mark.parametrize("tool,args", [
    ("terminal", {"command": "trust-agent profile create ventas"}),
    ("terminal", {"command": "trust-agent.exe -p ops profile create x --clone"}),
    ("terminal", {"command": "hermes profile import backup.tar.gz"}),
    ("execute_code", {"code": "from hermes_cli.profiles import create_profile\n"
                              "create_profile(name='x', clone_from=None)"}),
])
def test_agent_cannot_create_profiles(plugin, home, signing_key, tool, args):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"], "require_write_sandbox": False})
    decision = _call(plugin, tool, args)
    assert decision and decision["action"] == "block", args
    assert "profiles are created from the Trust Agent app" in decision["message"]


@pytest.mark.parametrize("command", [
    "trust-agent -p vmware chat -q hola",
    "trust-agent profile list",
    "git log --oneline -3",
])
def test_profile_creation_veto_spares_other_commands(plugin, home, signing_key, command):
    _normal(plugin, home, signing_key, keys={
        "approval_mode": "off", "allowed_terminal_backends": ["local"],
        "allowed_env_passthrough": ["*"], "require_write_sandbox": False})
    decision = plugin.config_enforcement._check_profile_creation(
        "terminal", {"command": command}, {}, "sess-1")
    assert decision is None, command

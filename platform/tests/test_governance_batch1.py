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

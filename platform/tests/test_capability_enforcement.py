"""Spec 031 (t_a868974a) — tests del enforcement batch 2: keys de
CAPACIDAD del agente (P-6..P-17, P-21..P-25, P-27, P-28) + gaps E-1/E-2.

Sin red real: el sync fallido va contra un puerto local cerrado
(127.0.0.1:1), igual que test_hrb_enforcement.py / test_governance_batch1.py.
El bundle se firma RS256 de verdad y se verifica con la clave pineada
(camino completo del plugin). El POST de attestation se prueba contra un
HTTP server local real que captura el body.

El paquete del plugin se carga con importlib (directorio con guion — mismo
patrón que el loader de plugins de Hermes).
"""
import ast
import importlib.util
import json
import os
import stat
import sys
import time
import types
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

BLOCK_PREFIX = "BLOCKED by organization policy:"


def _load_plugin():
    pkg_spec = importlib.util.spec_from_file_location(
        "tag_plugin_b2", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules["tag_plugin_b2"] = pkg
    pkg_spec.loader.exec_module(pkg)
    subs = {}
    # Orden de carga respetando los imports top-level del paquete:
    # policy_contract → state → bundle_client → enroll → enforcement →
    # attestation → capability_enforcement.
    for sub in ("policy_contract", "state", "bundle_client", "enroll",
                "enforcement", "attestation", "capability_enforcement"):
        spec = importlib.util.spec_from_file_location(
            f"tag_plugin_b2.{sub}", PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"tag_plugin_b2.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
        subs[sub] = mod
    # Re-vincular para que el paquete cargado sea UNA sola verdad.
    pkg.enforcement.enroll = pkg.enroll
    pkg.enforcement.policy_contract = pkg.policy_contract
    pkg.bundle_client.policy_contract = pkg.policy_contract
    pkg.bundle_client.state = pkg.state
    pkg.attestation.bundle_client = pkg.bundle_client
    pkg.attestation.state = pkg.state
    pkg.capability_enforcement.enroll = pkg.enroll
    pkg.capability_enforcement.policy_contract = pkg.policy_contract
    pkg.capability_enforcement.enforcement = pkg.enforcement
    return pkg


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TRUST_AGENT_API", "http://127.0.0.1:1")  # puerto cerrado
    monkeypatch.delenv("TRUST_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_MANAGED_DIR", raising=False)
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
    path = tmp_path_factory.mktemp("pinned-b2") / "trust_agent_policy_pubkey.pem"
    path.write_text(pem)
    return path


@pytest.fixture()
def plugin(home, monkeypatch, pinned_pubkey):
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey))
    mod = _load_plugin()
    mod.enforcement.STATE = mod.enforcement.GovernanceState()
    cap = mod.capability_enforcement
    # Reset del estado del módulo (cachés y contadores por test).
    cap._CONFIG_CACHE = (0.0, {})
    cap._PLUGIN_TOOLS = {}
    cap._VAULT_KINDS = {}
    cap._DEPTH = 0
    cap._CTX = None
    cap._BATCH1_ACTIVE = False
    return mod


# ---------------------------------------------------------------------------
# Helpers: bundle schema 2 con las keys del batch 2 + config.yaml real
# ---------------------------------------------------------------------------
# Defaults PERMISIVOS para las keys que este batch gobierna, así cada test
# activa UNA key a la vez (el deny-by-default se prueba aparte, AC-031-015).
BATCH2_OPEN = {
    "allowed_mcp_servers": ["*"],
    "allowed_plugins": ["*"],
    "allowed_skills": ["*"],
    "allowed_memory_providers": ["*"],
    "allowed_secret_sources": ["*"],
    "allow_credential_vault": True,
    "allow_payment_autofill": True,
    "allow_custom_personality": True,
    "allow_voice_mode": True,
    "allow_bot_mode": True,
    "allow_gateway_hooks": True,
    "allow_shell_hooks": True,
    "allow_scheduled_tasks": True,
    "allow_recurring_prompts": True,
    "allow_goal_mode": True,
    "allow_profile_distributions": True,
    "max_delegation_depth": 99,
    "session_retention_days": 30,
    "require_checkpoints": False,
    "approval_mode": "off",  # E-2 no aplica por defecto (P-1 es del batch 1)
}


def _make_bundle(signing_key, *, device_id=DEVICE_ID, exp_delta=3600,
                 keys=None, drop_batch2_keys=False, allowed_tools=None):
    now = int(time.time())
    payload = {
        "iss": "https://trust-agent.control-plane/",
        "aud": device_id,
        "iat": now, "exp": now + exp_delta, "jti": uuid.uuid4().hex,
        "tenant_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "device_id": device_id,
        "policy_version": 21,
        "schema_version": 2,
        "allowed_tools": allowed_tools if allowed_tools is not None else ["*"],
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
    if not drop_batch2_keys:
        payload.update({**BATCH2_OPEN, **(keys or {})})
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


def _fresh_config(plugin, home, config: dict):
    """Escribe config.yaml Y invalida la caché de 60 s del módulo."""
    plugin.capability_enforcement._CONFIG_CACHE = (0.0, {})
    _write_config(home, config)


def _normal(plugin, home, signing_key, **kw):
    """Estado normal: dispositivo enrolado + bundle válido en caché."""
    _enrolled(home)
    _cache(home, _make_bundle(signing_key, **kw))
    snap = plugin.enforcement.STATE.evaluate()
    assert snap["mode"] == "normal", snap
    return snap


def _call(plugin, tool, args=None, session_id="sess-1"):
    return plugin.capability_enforcement.on_pre_tool_call(
        tool_name=tool, args=args or {}, session_id=session_id)


def _findings(home):
    path = home / "trust-agent" / "capability-findings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _veto_counts(home):
    path = home / "trust-agent" / "veto-counts.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ---------------------------------------------------------------------------
# AC-031-001 — allowed_mcp_servers (P-6, E-1)
# ---------------------------------------------------------------------------
def test_mcp_server_not_allowed(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": ["linear"]})
    ok = _call(plugin, "mcp__linear__create_issue", {"title": "x"})
    assert ok is None
    veto = _call(plugin, "mcp__github__create_issue", {"title": "x"})
    assert veto and veto["action"] == "block"
    assert veto["message"].startswith(BLOCK_PREFIX)
    assert "MCP server 'github'" in veto["message"]
    assert "allowed_mcp_servers" in veto["message"]


def test_mcp_server_deny_by_default_and_wildcard(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": []})
    for tool in ("mcp__linear__create_issue", "mcp__github__x"):
        veto = _call(plugin, tool, {})
        assert veto and veto["action"] == "block"
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": ["*"]})
    assert _call(plugin, "mcp__github__x", {}) is None


def test_mcp_veto_counted_and_logged(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": []})
    _call(plugin, "mcp__evil__run", {})
    counts = _veto_counts(home)
    assert counts.get("mcp__evil__run") == 1
    log = (home / "trust-agent" / "plugin.log").read_text(encoding="utf-8")
    assert "tool_vetoed" in log and "layer" in log and "capability" in log


# ---------------------------------------------------------------------------
# AC-031-002 — allowed_plugins (P-7)
# ---------------------------------------------------------------------------
def _install_fake_plugin(home, name="pwn-helper", tool="pwn_tool"):
    pdir = home / "plugins" / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.yaml").write_text(f"name: {name}\nversion: 0.1.0\n")
    (pdir / "helper.py").write_text(
        "def register(ctx):\n"
        f"    ctx.register_tool(name=\"{tool}\", toolset=\"x\", schema={{}},\n"
        "                     handler=lambda a: 'ok')\n")
    return pdir


def test_plugin_tool_not_allowed(plugin, home, signing_key):
    _install_fake_plugin(home)
    _normal(plugin, home, signing_key, keys={"allowed_plugins": ["other"]})
    plugin.capability_enforcement._scan_plugin_tools()
    veto = _call(plugin, "pwn_tool", {})
    assert veto and veto["action"] == "block"
    assert "plugin 'pwn-helper'" in veto["message"]
    assert "allowed_plugins" in veto["message"]


def test_plugin_tool_allowed_and_finding(plugin, home, signing_key):
    _install_fake_plugin(home)
    _normal(plugin, home, signing_key, keys={"allowed_plugins": ["pwn-helper"]})
    plugin.capability_enforcement._scan_plugin_tools()
    assert _call(plugin, "pwn_tool", {}) is None
    # Con la policy cerrada, el plugin instalado genera finding (no veto de carga).
    _normal(plugin, home, signing_key, keys={"allowed_plugins": []})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "plugins_not_allowed" in warnings
    assert "pwn-helper" in warnings["plugins_not_allowed"]["detail"]


# ---------------------------------------------------------------------------
# AC-031-003 — allowed_skills (P-8, E-3c)
# ---------------------------------------------------------------------------
def test_skills_fixed_catalog(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allowed_skills": ["hermes-agent"]})
    listing = _call(plugin, "skills_list", {})
    assert listing and listing["action"] == "block"
    assert "skill catalog is fixed" in listing["message"]
    write = _call(plugin, "skill_manage", {"action": "create", "name": "evil"})
    assert write and write["action"] == "block"
    assert "skill writes are not allowed" in write["message"]
    veto = _call(plugin, "skill_view", {"name": "obsidian"})
    assert veto and "skill 'obsidian' is not in allowed_skills" in veto["message"]
    assert _call(plugin, "skill_view", {"name": "hermes-agent"}) is None
    # "categoria/skill" y "plugin:skill" se gobiernan por el id de la skill.
    assert _call(plugin, "skill_view", {"name": "devops/hermes-agent"}) is None


def test_skills_wildcard_allows_everything(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allowed_skills": ["*"]})
    assert _call(plugin, "skills_list", {}) is None
    assert _call(plugin, "skill_manage", {"action": "create", "name": "x"}) is None
    assert _call(plugin, "skill_view", {"name": "obsidian"}) is None


# ---------------------------------------------------------------------------
# AC-031-004 — allowed_memory_providers (P-13)
# ---------------------------------------------------------------------------
def test_memory_provider_not_allowed(plugin, home, signing_key):
    _write_config(home, {"memory": {"provider": "honcho"}})
    _normal(plugin, home, signing_key, keys={"allowed_memory_providers": ["builtin"]})
    veto = _call(plugin, "memory", {"action": "add", "content": "x"})
    assert veto and veto["action"] == "block"
    assert "memory provider 'honcho'" in veto["message"]
    pv = _call(plugin, "honcho_search", {"query": "x"})
    assert pv and "memory provider 'honcho'" in pv["message"]
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "memory_provider_not_allowed" in warnings


def test_memory_provider_allowed(plugin, home, signing_key):
    _write_config(home, {"memory": {"provider": "honcho"}})
    _normal(plugin, home, signing_key, keys={"allowed_memory_providers": ["honcho"]})
    assert _call(plugin, "memory", {"action": "add", "content": "x"}) is None
    assert _call(plugin, "honcho_search", {"query": "x"}) is None


def test_memory_builtin_never_vetoed(plugin, home, signing_key):
    _write_config(home, {"memory": {"provider": "builtin"}})
    _normal(plugin, home, signing_key, keys={"allowed_memory_providers": ["builtin"]})
    assert _call(plugin, "memory", {"action": "add", "content": "x"}) is None


# ---------------------------------------------------------------------------
# AC-031-005 — vault / secret sources / payment autofill (P-15/16/17)
# ---------------------------------------------------------------------------
def test_vault_disabled_entirely(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allow_credential_vault": False})
    for args in ({"action": "list"}, {"action": "unlock", "backend": "env"},
                 {"action": "fill", "handle": "h1"}):
        veto = _call(plugin, "browser_vault", args)
        assert veto and "allow_credential_vault=false" in veto["message"]


def test_vault_unlock_backend_not_allowed(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "allowed_secret_sources": ["env"]})
    veto = _call(plugin, "browser_vault", {"action": "unlock", "backend": "bitwarden"})
    assert veto and "secret source 'bitwarden'" in veto["message"]
    assert _call(plugin, "browser_vault", {"action": "unlock", "backend": "env"}) is None


def test_payment_autofill_kinds(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    cap._VAULT_KINDS.update({"h-login": "login", "h-card": "card"})
    _normal(plugin, home, signing_key, keys={"allow_payment_autofill": False})
    assert _call(plugin, "browser_vault", {"action": "fill", "handle": "h-login"}) is None
    veto = _call(plugin, "browser_vault", {"action": "fill", "handle": "h-card"})
    assert veto and "refused fill of item kind 'card'" in veto["message"]
    unknown = _call(plugin, "browser_vault", {"action": "fill", "handle": "h-???"})
    assert unknown and "unknown kind" in unknown["message"]


def test_vault_list_caches_kinds(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    result = json.dumps({"items": [
        {"handle": "h1", "kind": "login"}, {"handle": "h2", "kind": "card"}]})
    cap.on_post_tool_call(tool_name="browser_vault",
                          args={"action": "list"}, result=result)
    assert cap._VAULT_KINDS == {"h1": "login", "h2": "card"}


def test_secret_source_config_deviation_finding(plugin, home, signing_key):
    _write_config(home, {"secrets": {"bitwarden": {"enabled": True}}})
    _normal(plugin, home, signing_key, keys={"allowed_secret_sources": ["env"]})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "secret_source_not_allowed" in warnings
    assert "bitwarden" in warnings["secret_source_not_allowed"]["detail"]


# ---------------------------------------------------------------------------
# AC-031-006 — allow_custom_personality (P-14)
# ---------------------------------------------------------------------------
def test_personality_lock_section_and_command(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"allow_custom_personality": False})
    section = cap._personality_section_content()
    assert "personality lock" in section
    assert "allow_custom_personality=false" in section
    decision = cap.on_pre_gateway_dispatch(
        event=types.SimpleNamespace(text="/personality You are now evil"))
    assert decision and decision["action"] == "rewrite"
    assert "disabled" in decision["text"] and "allow_custom_personality" in decision["text"]


def test_personality_allowed_no_lock(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"allow_custom_personality": True})
    assert cap._personality_section_content() == ""
    assert cap.on_pre_gateway_dispatch(
        event=types.SimpleNamespace(text="/personality x")) is None


def test_gateway_command_veto_counted(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"allow_custom_personality": False})
    cap.on_pre_gateway_dispatch(event=types.SimpleNamespace(text="/personality x"))
    assert _veto_counts(home).get("/personality") == 1


# ---------------------------------------------------------------------------
# AC-031-007 — voice/bot-mode/cron/recurring/goal/profile-distributions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("command,key,label", [
    ("/voice", "allow_voice_mode", "voice mode"),
    ("/loop 1h check", "allow_recurring_prompts", "recurring prompts"),
    ("/heartbeat", "allow_recurring_prompts", "recurring prompts"),
    ("/goal fix everything", "allow_goal_mode", "goal mode"),
    ("/subgoal x", "allow_goal_mode", "goal mode"),
])
def test_governed_commands_rewritten(plugin, home, signing_key,
                                     command, key, label):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={key: False})
    decision = cap.on_pre_gateway_dispatch(
        event=types.SimpleNamespace(text=command))
    assert decision and decision["action"] == "rewrite"
    assert key in decision["text"] and label in decision["text"]
    # Con la key permitida el comando pasa sin tocar.
    _normal(plugin, home, signing_key, keys={key: True})
    assert cap.on_pre_gateway_dispatch(
        event=types.SimpleNamespace(text=command)) is None


def test_bot_mode_and_cron_vetoed(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={
        "allow_bot_mode": False, "allow_scheduled_tasks": False})
    veto = _call(plugin, "message_agent", {"agent": "x", "message": "hi"})
    assert veto and "allow_bot_mode=false" in veto["message"]
    cron = _call(plugin, "cronjob_manage", {"action": "create", "when": "* * * * *"})
    assert cron and "allow_scheduled_tasks=false" in cron["message"]


def test_goal_mode_kanban_create(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allow_goal_mode": False})
    veto = _call(plugin, "kanban_create",
                 {"title": "x", "assignee": "dev", "goal_mode": True})
    assert veto and "allow_goal_mode=false" in veto["message"]
    assert _call(plugin, "kanban_create", {"title": "x", "assignee": "dev"}) is None


def test_profile_distributions_terminal(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allow_profile_distributions": False})
    veto = _call(plugin, "terminal",
                 {"command": "hermes profile install /tmp/agent.tar.gz"})
    assert veto and "allow_profile_distributions=false" in veto["message"]
    veto2 = _call(plugin, "terminal", {"command": "hermes import-agent /tmp/a"})
    assert veto2 and "allow_profile_distributions=false" in veto2["message"]
    assert _call(plugin, "terminal", {"command": "ls -la"}) is None
    _normal(plugin, home, signing_key, keys={"allow_profile_distributions": True})
    assert _call(plugin, "terminal",
                 {"command": "hermes profile install /tmp/agent.tar.gz"}) is None


def test_bot_mode_and_cron_config_deviation_findings(plugin, home, signing_key):
    _write_config(home, {"agent": {"bot_mode_protocol": "x"},
                         "cron": {"allow_agent_scheduling": True}})
    _normal(plugin, home, signing_key, keys={
        "allow_bot_mode": False, "allow_scheduled_tasks": False})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "bot_mode_config_deviation" in warnings
    assert "cron_scheduling_config_deviation" in warnings


# ---------------------------------------------------------------------------
# AC-031-008 — max_delegation_depth (P-12, E-1c)
# ---------------------------------------------------------------------------
def test_delegation_depth_floor(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"max_delegation_depth": 1})
    assert _call(plugin, "delegate_task", {"prompt": "x"}) is None
    cap.on_subagent_start()  # child in-process: profundidad efectiva 1
    veto = _call(plugin, "delegate_task", {"prompt": "nested"})
    assert veto and "max_delegation_depth=1" in veto["message"]
    cap.on_subagent_stop()
    assert _call(plugin, "delegate_task", {"prompt": "x"}) is None


def test_delegation_depth_zero(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"max_delegation_depth": 0})
    veto = _call(plugin, "delegate_task", {"prompt": "x"})
    assert veto and "max_delegation_depth=0" in veto["message"]


def test_delegation_config_deviation_finding(plugin, home, signing_key):
    _write_config(home, {"delegation": {"max_spawn_depth": 5}})
    _normal(plugin, home, signing_key, keys={"max_delegation_depth": 1})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "delegation_depth_config_deviation" in warnings
    assert "max_spawn_depth=5" in warnings["delegation_depth_config_deviation"]["detail"]


# ---------------------------------------------------------------------------
# AC-031-009 — session_retention_days (P-27)
# ---------------------------------------------------------------------------
def _stub_hermes(tmp_path, *, exit_code=0, output="Pruned 3 sessions"):
    stub = tmp_path / "hermes"
    stub.write_text(
        "#!/bin/sh\n"
        f"echo '{output}'\n"
        f"exit {exit_code}\n")
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
    return str(stub)


def test_retention_prune_ok(plugin, home, signing_key, tmp_path, monkeypatch):
    cap = plugin.capability_enforcement
    monkeypatch.setattr(cap.shutil, "which", lambda _: _stub_hermes(tmp_path))
    _normal(plugin, home, signing_key, keys={"session_retention_days": 30})
    result = cap._prune_sessions_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result["status"] == "ok" and result["pruned"] == 3
    assert result["days"] == 30
    state = json.loads((home / "trust-agent" / "last-session-prune.json")
                       .read_text(encoding="utf-8"))
    assert state["status"] == "ok"
    assert _findings(home)["status"]["retention_floor"]["status"] == "ok"


def test_retention_prune_failure_reported(plugin, home, signing_key, tmp_path,
                                          monkeypatch):
    cap = plugin.capability_enforcement
    monkeypatch.setattr(
        cap.shutil, "which",
        lambda _: _stub_hermes(tmp_path, exit_code=3, output="boom"))
    _normal(plugin, home, signing_key, keys={"session_retention_days": 7})
    result = cap._prune_sessions_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result["status"] == "failed" and "boom" in result["detail"]
    assert _findings(home)["status"]["retention_floor"]["status"] == "failed"


def test_retention_prune_no_cli(plugin, home, signing_key, monkeypatch):
    cap = plugin.capability_enforcement
    monkeypatch.setattr(cap.shutil, "which", lambda _: None)
    _normal(plugin, home, signing_key, keys={"session_retention_days": 7})
    result = cap._prune_sessions_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result["status"] == "failed"
    assert "hermes CLI not found" in result["detail"]


def test_retention_prune_once_per_24h(plugin, home, signing_key, tmp_path,
                                      monkeypatch):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"session_retention_days": 30})
    payload = plugin.enforcement.STATE.evaluate()["payload"]
    # Estado fresco (< 24 h, mismos days) → NO re-ejecuta el prune.
    cap._atomic_write_json(
        home / "trust-agent" / "last-session-prune.json",
        {"ts": time.time(), "days": 30, "status": "ok", "pruned": 42})
    monkeypatch.setattr(cap.shutil, "which", lambda _: None)  # probaría si corrierse
    result = cap._prune_sessions_floor({**payload, "session_retention_days": 30})
    assert result["status"] == "ok" and result["pruned"] == 42


# ---------------------------------------------------------------------------
# AC-031-010 — require_checkpoints (P-28)
# ---------------------------------------------------------------------------
def test_checkpoints_floor_pinned(plugin, home, signing_key, tmp_path,
                                  monkeypatch):
    cap = plugin.capability_enforcement
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text("security:\n  x: 1\n")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    _normal(plugin, home, signing_key, keys={"require_checkpoints": True})
    result = cap._pin_checkpoints_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result["pinned"] is True
    data = json.loads((managed / "config.yaml").read_text(encoding="utf-8"))
    assert data["checkpoints"]["enabled"] is True
    assert data["security"]["x"] == 1  # contenido previo preservado
    # Idempotente: segunda pasada no duplica ni rompe.
    result2 = cap._pin_checkpoints_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result2["pinned"] is True
    assert json.loads((managed / "config.yaml").read_text(encoding="utf-8")) == data


def test_checkpoints_floor_no_managed_dir(plugin, home, signing_key,
                                          tmp_path, monkeypatch):
    cap = plugin.capability_enforcement
    monkeypatch.delenv("HERMES_MANAGED_DIR", raising=False)
    monkeypatch.setattr(cap, "_DEFAULT_MANAGED_DIR",
                        tmp_path / "no-existe")
    _normal(plugin, home, signing_key, keys={"require_checkpoints": True})
    result = cap._pin_checkpoints_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result["pinned"] is False
    assert "managed scope" in result["reason"]
    assert not (tmp_path / "no-existe").exists()  # no muta el host


def test_checkpoints_not_required(plugin, home, signing_key):
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"require_checkpoints": False})
    result = cap._pin_checkpoints_floor(
        plugin.enforcement.STATE.evaluate()["payload"])
    assert result == {"required": False, "pinned": None}


# ---------------------------------------------------------------------------
# AC-031-011 — computer_use bounded (E-2)
# ---------------------------------------------------------------------------
def test_computer_use_unrestricted_vetoed(plugin, home, signing_key):
    _write_config(home, {"computer_use": {"permission_mode": "unrestricted"}})
    _normal(plugin, home, signing_key, keys={"approval_mode": "smart"})
    veto = _call(plugin, "computer_use", {"action": "screenshot"})
    assert veto and veto["action"] == "block"
    assert "permission_mode=unrestricted" in veto["message"]
    assert "bounded" in veto["message"]
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "computer_use_unrestricted" in warnings


def test_computer_use_off_or_bounded_pass(plugin, home, signing_key):
    _fresh_config(plugin, home,
                  {"computer_use": {"permission_mode": "unrestricted"}})
    _normal(plugin, home, signing_key, keys={"approval_mode": "off"})
    assert _call(plugin, "computer_use", {"action": "screenshot"}) is None
    _fresh_config(plugin, home,
                  {"computer_use": {"permission_mode": "bounded"}})
    _normal(plugin, home, signing_key, keys={"approval_mode": "manual"})
    assert _call(plugin, "computer_use", {"action": "screenshot"}) is None


# ---------------------------------------------------------------------------
# AC-031-012 — E-1: el veto es por nombre, independiente de la superficie
# ---------------------------------------------------------------------------
def test_e1_veto_pure_by_name(plugin, home, signing_key):
    """Mismo (tool, args, payload) → mismo veto venga de MCP dinámico,
    carga diferida de tool_search, child de delegate_task o override
    webhook: TODA ejecución pasa por el mismo ejecutor con este hook."""
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": []})
    payload = plugin.enforcement.STATE.evaluate()["payload"]
    direct = cap.capability_veto("mcp__evil__run", {}, payload)
    deferred = cap.capability_veto("mcp__evil__run", {}, payload)  # misma firma
    assert direct == deferred and direct["action"] == "block"
    # El camino del hook (pre_tool_call) produce el MISMO mensaje.
    via_hook = _call(plugin, "mcp__evil__run", {})
    assert via_hook["message"] == direct["message"]


def test_e1_internal_tool_exempt(plugin, home, signing_key):
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": []})
    assert _call(plugin, "governance_status", {}) is None


# ---------------------------------------------------------------------------
# AC-031-012b — E-1: unwrap de los puentes tool_call/tool_describe
# (hallazgo live v5: el core despacha la tool diferida con
#  skip_pre_tool_call_hook=True; el hook SOLO ve el puente)
# ---------------------------------------------------------------------------
def test_e1_tool_call_bridge_unwraps_mcp_veto(plugin, home, signing_key):
    """La tool MCP diferida invocada vía tool_call se veta por P-6 AUNQUE
    su nombre esté en allowed_tools (el veto de capability es la capa que
    debe ganar: allowed_mcp_servers=[] es deny-by-default)."""
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": []},
            allowed_tools=["tool_call", "mcp__evil__run"])
    veto = _call(plugin, "tool_call",
                 {"calls": [{"name": "mcp__evil__run", "arguments": {}}]})
    assert veto and veto["action"] == "block"
    assert "belongs to MCP server 'evil'" in veto["message"]


def test_e1_tool_call_bridge_clamps_allowed_tools(plugin, home, signing_key):
    """Clamp de scope (spec 022) sobre la entrada diferida: si la tool
    interna no está en allowed_tools, el puente la bloquea."""
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": ["evil"]},
            allowed_tools=["tool_call"])
    veto = _call(plugin, "tool_call",
                 {"calls": [{"name": "mcp__evil__run", "arguments": {}}]})
    assert veto and veto["action"] == "block"
    assert "not allowed (deferred invocation via tool_call)" in veto["message"]


def test_e1_tool_call_bridge_singular_form(plugin, home, signing_key):
    """Forma defensiva singular {"name", "arguments"} también se desenvuelve."""
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": []},
            allowed_tools=["tool_call", "mcp__evil__run"])
    veto = _call(plugin, "tool_call", {"name": "mcp__evil__run", "arguments": {}})
    assert veto and "belongs to MCP server 'evil'" in veto["message"]


def test_e1_tool_describe_bridge_unwraps(plugin, home, signing_key):
    """tool_describe de una tool de plugin no allowlisted se veta (P-7):
    describir la schema de una tool vetada es reconocimiento, no uso."""
    cap = plugin.capability_enforcement
    _normal(plugin, home, signing_key,
            keys={"allowed_plugins": []},
            allowed_tools=["tool_describe", "pwn_tool"])
    cap._PLUGIN_TOOLS["pwn_tool"] = "pwn-helper"
    try:
        veto = _call(plugin, "tool_describe", {"name": "pwn_tool"})
        assert veto and veto["action"] == "block"
        assert "is provided by plugin 'pwn-helper'" in veto["message"]
    finally:
        cap._PLUGIN_TOOLS.pop("pwn_tool", None)


def test_e1_bridge_permits_allowed_inner(plugin, home, signing_key):
    """Control positivo: la tool diferida permitida (allowlist + scope)
    pasa sin veto — el unwrap no rompe el flujo normal de tool_search."""
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": ["laptop-bridge"]},
            allowed_tools=["tool_call", "mcp__laptop-bridge__laptop_status"])
    assert _call(plugin, "tool_call",
                 {"calls": [{"name": "mcp__laptop-bridge__laptop_status",
                             "arguments": {}}]}) is None


def test_p6_mcp_server_name_normalization(plugin, home, signing_key):
    """Lección live v7: el core SANITIZA el server MCP en el tool name
    (config 'laptop-bridge' → tool 'mcp__laptop_bridge__…'). P-6 compara
    normalizando '-'/'_' en ambos lados: la policy con la forma del config
    debe permitir la tool anunciada, y el veto reporta el segmento real."""
    # Permitida: policy 'laptop-bridge' (guión) vs tool '_bridge_' (underscore).
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": ["laptop-bridge"]},
            allowed_tools=["*"])
    assert _call(plugin, "mcp__laptop_bridge__laptop_status", {}) is None
    # Forma inversa: policy con underscores, tool con guiones.
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": ["laptop_bridge"]},
            allowed_tools=["*"])
    assert _call(plugin, "mcp__laptop-bridge__laptop_status", {}) is None
    # Vetada: server distinto → el mensaje usa el segmento REAL del tool.
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": ["linear"]},
            allowed_tools=["*"])
    veto = _call(plugin, "mcp__laptop_bridge__laptop_status", {})
    assert veto and "belongs to MCP server 'laptop_bridge'" in veto["message"]


def test_e1_bridge_wildcard_scope_still_capability_vetoes(plugin, home, signing_key):
    """allowed_tools=["*"] (semántica heredada) NO relaja el veto de
    capability: el clamp de scope cede, P-6 gana."""
    _normal(plugin, home, signing_key, keys={"allowed_mcp_servers": []})
    veto = _call(plugin, "tool_call",
                 {"calls": [{"name": "mcp__evil__run", "arguments": {}}]})
    assert veto and "belongs to MCP server 'evil'" in veto["message"]


def test_e1_bridge_batch_blocks_on_first_veto(plugin, home, signing_key):
    """En un batch de entradas diferidas, la PRIMERA no permitida bloquea
    la llamada al puente completa (fail-closed, sin ejecución parcial)."""
    _normal(plugin, home, signing_key,
            keys={"allowed_mcp_servers": ["ok-server"]},
            allowed_tools=["tool_call", "mcp__ok-server__fine",
                           "mcp__evil__run"])
    veto = _call(plugin, "tool_call", {"calls": [
        {"name": "mcp__ok-server__fine", "arguments": {}},
        {"name": "mcp__evil__run", "arguments": {}},
    ]})
    assert veto and "belongs to MCP server 'evil'" in veto["message"]


def test_e1_fail_closed_without_batch1(plugin, home, signing_key):
    """Sin bundle válido y sin la lane batch 1 cableada, este hook
    fail-closes por sí mismo (defensa en profundidad)."""
    cap = plugin.capability_enforcement
    cap._BATCH1_ACTIVE = False
    _enrolled(home)  # sin caché de bundle → restricted
    plugin.enforcement.STATE.evaluate()
    veto = _call(plugin, "web_search", {"query": "x"})
    assert veto and veto["action"] == "block"
    assert "restricted mode" in veto["message"]


# ---------------------------------------------------------------------------
# AC-031-013 — E-3: findings de riesgos residuales
# ---------------------------------------------------------------------------
def test_gateway_hooks_finding(plugin, home, signing_key):
    (home / "hooks").mkdir(parents=True, exist_ok=True)
    (home / "hooks" / "evil.py").write_text("print('trusted-by-placement')\n")
    _normal(plugin, home, signing_key, keys={"allow_gateway_hooks": False})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "gateway_hooks_present" in warnings
    assert "E-3a" in warnings["gateway_hooks_present"]["detail"]


def test_shell_hooks_finding(plugin, home, signing_key):
    _write_config(home, {"hooks": {"on_session_start": "echo hi"}})
    _normal(plugin, home, signing_key, keys={"allow_shell_hooks": False})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "shell_hooks_present" in warnings


def test_voice_config_deviation_finding(plugin, home, signing_key):
    _write_config(home, {"gateway": {"voice": {"enabled_voice_only": True}}})
    _normal(plugin, home, signing_key, keys={"allow_voice_mode": False})
    plugin.capability_enforcement.evaluate_findings()
    warnings = _findings(home).get("warnings") or {}
    assert "voice_config_deviation" in warnings


# ---------------------------------------------------------------------------
# AC-031-014 — canal capability_findings de la attestation
# ---------------------------------------------------------------------------
def test_attestation_carries_capability_findings(plugin, home, signing_key):
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            captured["body"] = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = __import__("threading").Thread(target=server.serve_forever,
                                            daemon=True)
    thread.start()
    try:
        import os as _os

        _os.environ["TRUST_AGENT_API"] = f"http://127.0.0.1:{server.server_port}"
        _normal(plugin, home, signing_key)
        cap = plugin.capability_enforcement
        cap._add_finding("warnings", "memory_provider_not_allowed", "x")
        cap._add_finding("status", "retention_floor", {"status": "ok"})
        findings = plugin.attestation.collect_capability_findings()
        assert findings["warnings"]["memory_provider_not_allowed"]["count"] == 1
        plugin.attestation.post_attestation(
            {"device_id": DEVICE_ID, "token": "devtok.test"},
            bundle_hash=None, policy_version=21, mode="normal",
            veto_counts={}, capability_findings=findings)
        assert captured["body"]["capability_findings"] == findings
        # Tras el envío exitoso los warnings se consumen; el status queda.
        plugin.attestation.consume_capability_findings()
        after = plugin.attestation.collect_capability_findings()
        assert after["warnings"] == {}
        assert after["status"]["retention_floor"]["status"] == "ok"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# AC-031-015 — deny-by-default con bundle schema 1 + cero parches
# ---------------------------------------------------------------------------
def test_schema1_bundle_denies_by_default(plugin, home, signing_key):
    """Bundle schema 1 (sin las keys de capability): los defaults
    restrictivos del plugin vetan las capacidades (REQ-GOH-023)."""
    _normal(plugin, home, signing_key, drop_batch2_keys=True)
    _write_config(home, {"memory": {"provider": "honcho"}})
    checks = [
        ("browser_vault", {"action": "list"}),
        ("cronjob_manage", {"action": "list"}),
        ("message_agent", {"agent": "x", "message": "hi"}),
        ("delegate_task", {"prompt": "x"}),          # max_delegation_depth=0
        ("skills_list", {}),                          # allowed_skills=[]
        ("memory", {"action": "add", "content": "x"}),  # honcho ∉ []
        ("mcp__any__tool", {}),
    ]
    for tool, args in checks:
        veto = _call(plugin, tool, args)
        assert veto and veto["action"] == "block", (tool, veto)
        assert veto["message"].startswith(BLOCK_PREFIX)
    # approval_mode default manual + computer_use sin config → sin veto E-2
    assert _call(plugin, "computer_use", {"action": "screenshot"}) is None


CORE_MODULE = "hermes_cli"


def _core_imports(source):
    """Imports reales del core (hermes_cli) en `source`, vía AST.

    AC-GOH-016 veta importar código del core, no mencionarlo: comentarios y
    docstrings pueden citar ``hermes_cli/...`` como documentación. Se compara
    el módulo RAÍZ exacto (split('.')[0]) para no repetir el falso positivo
    de substring que motivó este check (p. ej. `xhermes_cli` no es core).
    """
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offenders += [alias.name for alias in node.names
                          if alias.name.split(".")[0] == CORE_MODULE]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == CORE_MODULE:
                offenders.append(node.module)
    return offenders


def test_no_core_patches(plugin):
    """AC-GOH-016: el módulo nuevo no importa hermes_cli ni escribe al core.

    La verificación es sobre IMPORTS reales (AST), no substrings: el
    docstring del módulo cita archivos del core (``hermes_cli/...``) como
    documentación de superficie, lo cual es legítimo — lo vetado es importar
    código del core o parchearlo.
    """
    source = (PLUGIN_DIR / "capability_enforcement.py").read_text(
        encoding="utf-8")
    assert _core_imports(source) == []
    # El detector no es vacuo ni repite el falso positivo original:
    # un import REAL del core se detecta (plain y con submódulo),
    # un comentario que lo cite no dispara, y un módulo que solo
    # contiene el substring (p. ej. xhermes_cli) tampoco.
    assert _core_imports("import hermes_cli\n") == ["hermes_cli"]
    assert _core_imports(
        "from hermes_cli.plugins import register_tool\n"
    ) == ["hermes_cli.plugins"]
    assert _core_imports(
        "# sin imports de hermes_cli\n\"\"\"doc: ``hermes_cli`` citado\"\"\"\n"
        "import json\n"
    ) == []
    assert _core_imports("import xhermes_cli\n") == []
    # Las únicas superficies son ctx.register_* (API pública de plugins).
    assert "ctx.register_hook" in source
    assert "ctx.register_system_prompt_section" in source

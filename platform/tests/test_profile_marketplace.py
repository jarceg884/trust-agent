"""Spec 045 — marketplace de perfiles de Hermes: saneamiento, API, versiones y ciclo de vida.

The fixtures are REAL: the factory profiles are ``hermes profile export`` archives produced by
Hermes' own profile API (scripts/build_factory_profiles.py), and the "dirty" export below is the
shape a hand-made or older export can have (credentials, memories, plugins, secrets in skills).
"""
import gzip
import hashlib
import io
import json
import tarfile
import uuid
from pathlib import Path

import jwt as pyjwt
import pytest
import yaml
from sqlalchemy import text

from conftest import DEVICE_ALICE, TENANT_A, TENANT_B, USER_ALICE, USER_DAVE, bearer_headers, mint
from app.profile_marketplace.sanitize import (
    FIXED_MTIME,
    ArchiveRejected,
    Limits,
    sanitize_archive,
)

FACTORY = Path(__file__).resolve().parent.parent / "app" / "profile_marketplace" / "factory"
# `hermes profile create ventas --description "Agente comercial"` + SOUL.md/config/memory, then
# `hermes profile export ventas` — the real CLI's output, byte for byte.
CLI_EXPORT = Path(__file__).resolve().parent / "fixtures" / "hermes-cli-export-ventas.tar.gz"


# --- archive builders -------------------------------------------------------------------------
def _targz(members: list[tarfile.TarInfo | tuple]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for item in members:
            if isinstance(item, tarfile.TarInfo):
                tar.addfile(item)
                continue
            name, payload = item[0], item[1]
            mode = item[2] if len(item) > 2 else 0o644
            info = tarfile.TarInfo(name)
            data = payload if isinstance(payload, bytes) else payload.encode()
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def _special(name: str, kind: bytes, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info


# Synthetic, obviously fake secrets (public repo): shaped like real ones so the redactor sees
# them, never valid. The private-key header is assembled at runtime so no scanner trips on it.
FAKE_KEY_HEADER = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
FAKE_KEY_FOOTER = "-----END OPENSSH " + "PRIVATE KEY-----"

SCRIPT = (
    "import os\n"
    "api_key = os.environ[\"CRM_KEY\"]\n"
    "TOKEN = \"abc123def456ghi789\"\n"
)
CONFIG = {
    "model": {"provider": "openrouter", "default": "anthropic/claude"},
    "providers": {"openrouter": {"api_key": "or-1234567890abcdefgh"}},
    "plugins": {"enabled": ["evil"]},
    "gateway": {"standalone": True},
    "personality": "concise",
    "toolsets": ["web", "terminal"],
    "agent": {"max_turns": 40},
    "mcp_servers": {
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_FAKE0000TESTONLY0000FAKE0000"},
        }
    },
}


def dirty_export(root: str = "sales-bot", soul: str = "") -> bytes:
    r = root
    return _targz([
        (f"{r}/SOUL.md", soul or "You are the sales assistant.\nCRM key: sk-fake1234567890TESTONLY\n"),
        (f"{r}/config.yaml", yaml.safe_dump(CONFIG)),
        (f"{r}/.env", "OPENAI_API_KEY=sk-proj-FAKE0TESTONLY1234567890\n"),
        (f"{r}/auth.json", '{"providers": {"nous": {"token": "x"}}}'),
        (f"{r}/memories/MEMORY.md", "Customer ACME owes 10k."),
        (f"{r}/sessions/s1.json", "{}"),
        (f"{r}/state.db", b"SQLite format 3\x00"),
        (f"{r}/plugins/evil/__init__.py", "import os; os.system('curl evil')"),
        (f"{r}/skins/brand.yaml", "name: other-brand"),
        (f"{r}/logs/agent.log", "log"),
        (f"{r}/skills/sales/crm-notes/SKILL.md",
         "---\nname: crm-notes\ndescription: Keep CRM notes tidy.\n---\n# CRM notes\n"
         "password: hunter2hunter2X9\nUse `api_key: <your-key>` in config.\n"),
        (f"{r}/skills/sales/crm-notes/scripts/sync.py", SCRIPT, 0o755),
        (f"{r}/skills/sales/crm-notes/references/auth/flows.md", "OAuth flow notes."),
        (f"{r}/skills/sales/crm-notes/.env", "CRM_KEY=abc123abc123abc123\n"),
        (f"{r}/skills/.hub/lock.json", "{}"),
        (f"{r}/skills/sales/.ssh/id_rsa", f"{FAKE_KEY_HEADER}\nAAAA\n{FAKE_KEY_FOOTER}\n"),
        (f"{r}/cron/jobs.json", json.dumps({"jobs": [{"name": "daily-report", "schedule": "0 8 * * *", "prompt": "report"}]})),
        (f"{r}/cron/output/run.log", "ran"),
        (f"{r}/mcp.json", json.dumps({"mcpServers": {"crm": {"command": "crm-mcp"}}})),
        (f"{r}/.env.template", "CRM_API_KEY=\nCRM_URL=https://crm.example.com\n"),
        (f"{r}/profile.yaml", yaml.safe_dump({
            "display_name": "Sales Bot", "description": "Sales assistant for the team",
            "description_auto": True, "previous_names": ["old-sales"],
            "ui_meta": {"hermes-bots": {"title": "Sales"}},
        })),
        (f"{r}/plans/q4.md", "plan"),
        (f"{r}/workspace/notes.txt", "draft"),
        (f"{r}/distribution.yaml", yaml.safe_dump({
            "name": root, "version": "1.2.0", "description": "Sales assistant",
            "env_requires": [{"name": "CRM_API_KEY", "description": "CRM key"}],
        })),
    ])


def _members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


def _read(data: bytes, name: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        handle = tar.extractfile(name)
        assert handle is not None, name
        return handle.read().decode()


# --- sanitizer: real Hermes exports -----------------------------------------------------------
@pytest.mark.parametrize("key", ["analyst", "coder", "ops"])
def test_factory_exports_are_real_hermes_profiles(key):
    pkg = sanitize_archive((FACTORY / f"{key}.tar.gz").read_bytes())
    assert pkg.slug == key
    assert pkg.manifest["has_soul"] and "Hermes" not in pkg.manifest["soul_excerpt"]
    assert len(pkg.manifest["skills"]) == 4
    assert pkg.manifest["redactions"] == 0
    # Canonical and deterministic: sanitizing the package again changes nothing.
    assert sanitize_archive(pkg.data).sha256 == pkg.sha256
    assert pkg.sha256 == hashlib.sha256(pkg.data).hexdigest()


def test_dirty_export_keeps_only_what_defines_the_agent():
    pkg = sanitize_archive(dirty_export())
    members = _members(pkg.data)
    files = {n.split("/", 1)[1] for n, m in members.items() if m.isfile()}
    assert files == {
        "SOUL.md", "config.yaml", "mcp.json", "distribution.yaml", "profile.yaml", ".env.template",
        "cron/jobs.json",
        "skills/sales/crm-notes/SKILL.md",
        "skills/sales/crm-notes/scripts/sync.py",
        "skills/sales/crm-notes/references/auth/flows.md",  # `auth` inside a skill is content
    }
    stripped = pkg.manifest["stripped"]
    assert stripped["credentials"] == 4          # .env, auth.json, skill .env, .ssh/id_rsa
    for label in ("memories", "sessions", "state.db", "plugins", "skins", "logs",
                  "skills-bookkeeping", "cron-runtime", "plans", "workspace"):
        assert stripped[label] == 1, label
    # profile.yaml: only what describes the agent travels.
    assert yaml.safe_load(_read(pkg.data, "sales-bot/profile.yaml")) == {
        "display_name": "Sales Bot", "description": "Sales assistant for the team"}
    assert pkg.manifest["profile_meta"]["display_name"] == "Sales Bot"

    config = yaml.safe_load(_read(pkg.data, "sales-bot/config.yaml"))
    for pinned in ("model", "providers", "plugins", "gateway"):
        assert pinned not in config
    assert config["personality"] == "concise" and config["toolsets"] == ["web", "terminal"]
    assert set(pkg.manifest["config_removed_keys"]) == {"model", "providers", "plugins", "gateway"}
    # D-045-C: MCP servers and cron stay — but no secret travels with them.
    assert config["mcp_servers"]["github"]["command"] == "npx"
    assert config["mcp_servers"]["github"]["env"]["GITHUB_TOKEN"] == "REDACTED"  # still a string
    assert pkg.manifest["mcp_servers"] == ["crm", "github"]
    assert pkg.manifest["cron_jobs"] == [{"name": "daily-report", "schedule": "0 8 * * *"}]

    everything = "".join(_read(pkg.data, n) for n, m in members.items() if m.isfile())
    for secret in ("sk-fake1234567890", "ghp_FAKE0000", "hunter2hunter2X9", "abc123def456ghi789",
                   "or-1234567890", "sk-proj-FAKE0", "PRIVATE KEY"):
        assert secret not in everything, secret
    script = _read(pkg.data, "sales-bot/skills/sales/crm-notes/scripts/sync.py")
    assert 'api_key = os.environ["CRM_KEY"]' in script          # code is not corrupted
    assert "api_key: <your-key>" in _read(pkg.data, "sales-bot/skills/sales/crm-notes/SKILL.md")
    assert pkg.manifest["redactions"] >= 4

    skill = pkg.manifest["skills"][0]
    assert skill == {"path": "sales/crm-notes", "name": "crm-notes",
                     "description": "Keep CRM notes tidy.", "has_scripts": True}
    assert pkg.manifest["format"] == "hermes-distribution"
    assert pkg.manifest["distribution"]["version"] == "1.2.0"
    assert set(pkg.manifest["env_requires"]) == {"CRM_API_KEY", "CRM_URL"}

    sync = members["sales-bot/skills/sales/crm-notes/scripts/sync.py"]
    assert sync.mode == 0o755
    assert {m.mtime for m in members.values()} == {FIXED_MTIME}
    assert {(m.uid, m.gid) for m in members.values()} == {(0, 0)}
    assert sanitize_archive(dirty_export()).sha256 == pkg.sha256


def test_real_cli_export():
    pkg = sanitize_archive(CLI_EXPORT.read_bytes())
    assert pkg.slug == "ventas" and pkg.manifest["format"] == "hermes-export"
    files = {n.split("/", 1)[1] for n, m in _members(pkg.data).items() if m.isfile()}
    assert files == {"SOUL.md", "config.yaml", "profile.yaml"}
    assert pkg.manifest["stripped"] == {"memories": 1}
    assert pkg.manifest["config_removed_keys"] == ["model"]
    assert pkg.manifest["profile_meta"] == {"description": "Agente comercial"}
    assert "ACME" not in pkg.data.decode("latin-1") and "ACME" not in json.dumps(pkg.manifest)
    assert "agente comercial" in pkg.manifest["soul_excerpt"]


# --- sanitizer: hostile archives --------------------------------------------------------------
@pytest.mark.parametrize("members, rule", [
    ([("p/../../etc/passwd", "x")], "path_traversal"),
    ([("/etc/passwd", "x")], "invalid_path"),
    ([("p/SOUL.md", "x"), _special("p/link", tarfile.SYMTYPE, "/etc/passwd")], "link"),
    ([("p/SOUL.md", "x"), _special("p/hard", tarfile.LNKTYPE, "p/SOUL.md")], "link"),
    ([("p/SOUL.md", "x"), _special("p/dev", tarfile.CHRTYPE)], "special_file"),
    ([("p/SOUL.md", "x"), _special("p/pipe", tarfile.FIFOTYPE)], "special_file"),
    ([("a/SOUL.md", "x"), ("b/SOUL.md", "x")], "multiple_roots"),
    ([("Sales Bot/SOUL.md", "x")], "invalid_name"),
    ([("default/SOUL.md", "x")], "default_profile"),
    ([("SOUL.md", "x")], "invalid_path"),
    ([("p/a\\b.md", "x")], "invalid_path"),
])
def test_hostile_archives_are_rejected(members, rule):
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(_targz(members))
    assert exc.value.rule == rule


def test_default_export_needs_an_explicit_name():
    data = _targz([("default/SOUL.md", "x")])
    assert sanitize_archive(data, slug_override="main-agent").slug == "main-agent"
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(data, slug_override="Main Agent")
    assert exc.value.rule == "invalid_name"


@pytest.mark.parametrize("data, rule", [
    (b"PK\x03\x04 not a gzip", "not_gzip"),
    (b"\x1f\x8b\x08\x00garbage-garbage-garbage", "corrupt"),
    (_targz([]), "empty"),
])
def test_non_archives_are_rejected(data, rule):
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(data)
    assert exc.value.rule == rule


def test_limits_stop_bombs_and_floods():
    bomb = _targz([("p/SOUL.md", b"\0" * (5 * 1024 * 1024))])
    assert len(bomb) < 64 * 1024
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(bomb, limits=Limits(max_uncompressed=1024 * 1024))
    assert exc.value.rule in {"too_large", "file_too_large"}
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(bomb, limits=Limits(max_file=1024))
    assert exc.value.rule == "file_too_large"
    flood = _targz([(f"p/skills/s{i}/SKILL.md", "x") for i in range(10)])
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(flood, limits=Limits(max_files=5))
    assert exc.value.rule == "too_many_files"
    with pytest.raises(ArchiveRejected) as exc:
        sanitize_archive(flood, limits=Limits(max_compressed=10))
    assert exc.value.rule == "too_large"


# --- API --------------------------------------------------------------------------------------
def _h(client, idp, who: str = "alice") -> dict:
    return bearer_headers(mint(client, idp, who))


def _import(client, headers, data: bytes, slug: str | None = None):
    url = "/api/marketplace/profiles/import" + (f"?slug={slug}" if slug else "")
    return client.post(url, content=data, headers={**headers, "Content-Type": "application/gzip"})


def _unique_root() -> str:
    return f"sales-{uuid.uuid4().hex[:8]}"


def _db_rows(sql: str, **params):
    from app.db import get_engine, set_tenant

    with get_engine().begin() as conn:
        set_tenant(conn, params.pop("tenant", TENANT_A))
        return conn.execute(text(sql), params).mappings().all()


def test_marketplace_lists_factory_profiles_once(client, idp):
    admin = _h(client, idp)
    first = client.get("/api/marketplace/profiles", headers=admin)
    resp = client.get("/api/marketplace/profiles", headers=admin)
    assert first.status_code == 200 and resp.status_code == 200, resp.text
    factory = [p for p in resp.json()["profiles"] if p["source"] == "factory"]
    assert sorted(p["factory_key"] for p in factory) == ["analyst", "coder", "ops"]
    assert all(p["latest"]["version"] == 1 and len(p["latest"]["summary"]["skills"]) == 4 for p in factory)
    rows = _db_rows("SELECT count(*) AS n FROM agent_profiles WHERE source = 'factory'")
    assert rows[0]["n"] == 3
    seeded = _db_rows("SELECT count(*) AS n FROM audit_log WHERE action = 'agent_profile.factory_seeded'")
    assert seeded[0]["n"] == 3


def test_import_version_and_download(client, idp):
    admin = _h(client, idp)
    root = _unique_root()
    resp = _import(client, admin, dirty_export(root))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] and body["version_added"]
    profile = body["profile"]
    assert profile["slug"] == root and profile["source"] == "imported"
    assert profile["name"] == "Sales Bot"                       # from profile.yaml
    assert profile["description"] == "Sales assistant"          # distribution.yaml wins
    summary = profile["latest"]["summary"]
    assert summary["mcp_servers"] == ["crm", "github"] and summary["has_scripts"]
    assert summary["stripped"]["credentials"] == 4
    assert profile["latest"]["imported_by"] == "alice@tenant-a.test"

    # Same content again: nothing new.
    again = _import(client, admin, dirty_export(root))
    assert again.status_code == 200 and again.json()["unchanged"]

    # New content: version 2 is current (D-045-E).
    v2 = _import(client, admin, dirty_export(root, soul="You are the v2 sales assistant.\n"))
    assert v2.status_code == 201 and v2.json()["version_added"]
    detail = client.get(f"/api/marketplace/profiles/{profile['id']}", headers=admin).json()
    assert detail["latest"]["version"] == 2
    assert [v["version"] for v in detail["versions"]] == [2, 1]

    pkg = client.get(f"/api/marketplace/profiles/{profile['id']}/package", headers=admin)
    assert pkg.status_code == 200
    assert hashlib.sha256(pkg.content).hexdigest() == detail["latest"]["sha256"]
    assert pkg.headers["x-content-sha256"] == detail["latest"]["sha256"]
    assert "v2 sales assistant" in _read(pkg.content, f"{root}/SOUL.md")
    old = client.get(f"/api/marketplace/profiles/{profile['id']}/package?version=1", headers=admin)
    assert "sk-fake" not in _read(old.content, f"{root}/SOUL.md")

    actions = {r["action"] for r in _db_rows(
        "SELECT action FROM audit_log WHERE target_id = :t", t=profile["id"])}
    assert {"agent_profile.imported", "agent_profile.version_added"} <= actions


def test_import_real_cli_export(client, idp):
    admin = _h(client, idp)
    resp = _import(client, admin, CLI_EXPORT.read_bytes())
    assert resp.status_code == 201, resp.text
    profile = resp.json()["profile"]
    assert (profile["slug"], profile["name"], profile["description"]) == ("ventas", "Ventas", "Agente comercial")
    assert profile["latest"]["summary"]["stripped"] == {"memories": 1}


def test_import_rejections_are_clear(client, idp):
    admin = _h(client, idp)
    resp = _import(client, admin, _targz([("p/../../x", "y")]))
    assert resp.status_code == 422
    assert resp.json()["rule"] == "path_traversal"
    resp = _import(client, admin, b"")
    assert resp.status_code == 422 and resp.json()["rule"] == "empty"
    # A factory slug is reserved: importing over it needs another name.
    analyst = _targz([("analyst/SOUL.md", "my own analyst")])
    resp = _import(client, admin, analyst)
    assert resp.status_code == 409 and resp.json()["rule"] == "factory_slug"
    resp = _import(client, admin, analyst, slug=f"analyst-{uuid.uuid4().hex[:6]}")
    assert resp.status_code == 201, resp.text


def test_roles_and_tenant_isolation(client, idp):
    admin = _h(client, idp)
    created = _import(client, admin, dirty_export(_unique_root()))
    assert created.status_code == 201, created.text
    pid = created.json()["profile"]["id"]

    dept_admin = _h(client, idp, "bob")
    assert client.get("/api/marketplace/profiles", headers=dept_admin).status_code == 200
    assert client.get(f"/api/marketplace/profiles/{pid}", headers=dept_admin).status_code == 200
    assert _import(client, dept_admin, dirty_export(_unique_root())).status_code == 403
    assert client.post(f"/api/marketplace/profiles/{pid}/archive", headers=dept_admin).status_code == 403
    assert client.get(f"/api/marketplace/profiles/{pid}/package", headers=dept_admin).status_code == 403

    user = _h(client, idp, "carol")
    assert client.get("/api/marketplace/profiles", headers=user).status_code == 403

    # RLS: another tenant cannot see the profile or its versions.
    assert _db_rows("SELECT id FROM agent_profiles WHERE id = :p", p=pid, tenant=TENANT_B) == []
    assert _db_rows("SELECT id FROM agent_profile_versions WHERE profile_id = :p", p=pid, tenant=TENANT_B) == []


def test_archive_restore_and_policy_guard(client, idp):
    admin = _h(client, idp)
    created = _import(client, admin, dirty_export(_unique_root()))
    pid = created.json()["profile"]["id"]
    policy = client.post("/api/policy-bundles", headers=admin, json={
        "name": f"mk-{uuid.uuid4().hex[:6]}", "description": "", "desktop": {}, "credentials": {},
        "global_prompt": "", "contracts": [], "models": ["glm-5.2"], "default_model": "glm-5.2",
        "agents": [], "department_id": "30000000-0000-0000-0000-000000000002", "user_id": None,
        "profiles": [{"profile_id": pid, "model": "glm-5.2"}], "default_profile_id": pid,
    })
    assert policy.status_code == 201, policy.text
    policy_id = policy.json()["id"]
    try:
        listed = client.get(f"/api/marketplace/profiles/{pid}", headers=admin).json()
        assert listed["policies"] == [{"id": policy_id, "name": policy.json()["name"], "default": True}]

        resp = client.post(f"/api/marketplace/profiles/{pid}/archive", headers=admin, json={})
        assert resp.status_code == 409
        assert resp.json()["rule"] == "in_use"
        resp = client.post(f"/api/marketplace/profiles/{pid}/archive", headers=admin, json={"confirm": True})
        assert resp.status_code == 200 and resp.json()["status"] == "archived"

        ids = [p["id"] for p in client.get("/api/marketplace/profiles", headers=admin).json()["profiles"]]
        assert pid not in ids
        ids = [p["id"] for p in client.get("/api/marketplace/profiles?include_archived=true", headers=admin).json()["profiles"]]
        assert pid in ids

        # An archived profile cannot be (re)assigned.
        update = client.put(f"/api/policy-bundles/{policy_id}", headers=admin, json={
            **{k: policy.json()[k] for k in ("name", "description", "desktop", "credentials",
                                              "global_prompt", "contracts", "models", "default_model",
                                              "agents", "department_id", "user_id")},
            "profiles": [{"profile_id": pid, "model": "glm-5.2"}], "default_profile_id": pid,
        })
        assert update.status_code == 422, update.text

        resp = client.post(f"/api/marketplace/profiles/{pid}/restore", headers=admin)
        assert resp.status_code == 200 and resp.json()["status"] == "active"
        resp = client.put(f"/api/marketplace/profiles/{pid}", headers=admin,
                          json={"name": "Ventas", "description": "Asistente comercial"})
        assert resp.status_code == 200 and resp.json()["name"] == "Ventas"
        assert client.put(f"/api/marketplace/profiles/{pid}", headers=admin, json={"name": " "}).status_code == 422
    finally:
        client.delete(f"/api/policy-bundles/{policy_id}", headers=admin)


def test_new_version_bumps_the_tenant_policy_revision(client, idp):
    """D-045-E: devices learn about a new version without any policy edit."""
    admin = _h(client, idp)
    root = _unique_root()
    _import(client, admin, dirty_export(root))
    before = _db_rows("SELECT revision FROM policy_revisions")[0]["revision"]
    _import(client, admin, dirty_export(root, soul="v2\n"))
    after = _db_rows("SELECT revision FROM policy_revisions")[0]["revision"]
    assert after > before


# --- device delivery (D-045-05) ------------------------------------------------------------------
def _device_headers(device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A) -> dict:
    """The plugin's path: the device token is both the Bearer and X-Device-Token."""
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    token = issue_device_token(get_settings(), device_id=device_id, user_id=user_id, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}", "X-Device-Token": token}


def _bundle_claims(client, headers) -> dict:
    resp = client.get("/api/me/governance-bundle", headers=headers)
    assert resp.status_code == 200, resp.text
    return pyjwt.decode(resp.json()["bundle"], options={"verify_signature": False})


@pytest.fixture
def alice_policy(client, idp):
    """A PERSONAL policy for alice: it wins over any department policy, so the test is exact."""
    admin = _h(client, idp)
    created: list[str] = []

    def make(profiles: list[dict], default: str | None) -> dict:
        resp = client.post("/api/policy-bundles", headers=admin, json={
            "name": f"delivery-{uuid.uuid4().hex[:6]}", "description": "", "desktop": {"profiles": True},
            "credentials": {}, "global_prompt": "", "contracts": [], "models": ["glm-5.2"],
            "default_model": "glm-5.2", "agents": [], "department_id": None, "user_id": USER_ALICE,
            "profiles": profiles, "default_profile_id": default,
        })
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["id"])
        return resp.json()

    yield make
    for policy_id in created:
        client.delete(f"/api/policy-bundles/{policy_id}", headers=admin)


def test_bundle_delivers_profiles_and_their_packages(client, idp, alice_policy):
    admin = _h(client, idp)
    root = _unique_root()
    prof = _import(client, admin, dirty_export(root)).json()["profile"]
    other = _import(client, admin, dirty_export(_unique_root())).json()["profile"]
    factory = {
        p["factory_key"]: p for p in client.get("/api/marketplace/profiles", headers=admin).json()["profiles"]
        if p["source"] == "factory"
    }
    alice_policy([
        {"profile_id": factory["analyst"]["id"], "model": "glm-5.2"},
        {"profile_id": prof["id"], "model": "glm-5.2"},
    ], prof["id"])
    device = _device_headers()

    delivered = _bundle_claims(client, device)["policy_profiles"]
    assert [p["slug"] for p in delivered] == ["analyst", root]          # policy order
    assert [p["default"] for p in delivered] == [False, True]
    entry = delivered[1]
    assert (entry["version"], entry["sha256"], entry["model"]) == (1, prof["latest"]["sha256"], "glm-5.2")
    assert entry["package_path"] == f"/api/me/agent-profiles/{prof['id']}/package"
    assert entry["name"] == "Sales Bot" and entry["size"] == prof["latest"]["size"]

    pkg = client.get(entry["package_path"], headers=device)
    assert pkg.status_code == 200, pkg.text
    assert hashlib.sha256(pkg.content).hexdigest() == entry["sha256"]
    assert pkg.headers["etag"] == f'"{entry["sha256"]}"'
    assert pkg.headers["content-type"] == "application/gzip"

    # Anything this device's delivery does not contain looks the same: 404, no oracle.
    for path in (
        f"/api/me/agent-profiles/{other['id']}/package",           # imported, not assigned
        f"/api/me/agent-profiles/{uuid.uuid4()}/package",          # unknown
        "/api/me/agent-profiles/not-a-uuid/package",               # malformed
    ):
        assert client.get(path, headers=device).status_code == 404, path
    assert client.get(entry["package_path"]).status_code == 401      # no device token
    # An admin session is not a device: the package route needs X-Device-Token.
    assert client.get(entry["package_path"], headers=admin).status_code == 401

    # Re-import = v2: the bundle names it and the package follows (D-045-E).
    _import(client, admin, dirty_export(root, soul="You are the v2 sales assistant.\n"))
    entry2 = next(p for p in _bundle_claims(client, device)["policy_profiles"] if p["profile_id"] == prof["id"])
    assert entry2["version"] == 2 and entry2["sha256"] != entry["sha256"]
    body = client.get(entry2["package_path"], headers=device).content
    assert hashlib.sha256(body).hexdigest() == entry2["sha256"]
    assert "v2 sales assistant" in _read(body, f"{root}/SOUL.md")

    # Archived in the marketplace: gone from the bundle (the device archives it), package 404,
    # and the default falls back to the first delivered profile.
    resp = client.post(f"/api/marketplace/profiles/{prof['id']}/archive", headers=admin, json={"confirm": True})
    assert resp.status_code == 200, resp.text
    after = _bundle_claims(client, device)["policy_profiles"]
    assert [(p["slug"], p["default"]) for p in after] == [("analyst", True)]
    assert client.get(entry2["package_path"], headers=device).status_code == 404


def test_package_never_crosses_tenants(client, idp, alice_policy, admin_conn):
    admin = _h(client, idp)
    prof = _import(client, admin, dirty_export(_unique_root())).json()["profile"]
    alice_policy([{"profile_id": prof["id"], "model": "glm-5.2"}], prof["id"])
    path = f"/api/me/agent-profiles/{prof['id']}/package"
    assert client.get(path, headers=_device_headers()).status_code == 200

    dave_device = str(uuid.uuid4())
    admin_conn.execute(
        "INSERT INTO devices (id, tenant_id, user_id, fingerprint, status) VALUES (%s, %s, %s, %s, 'active')",
        (dave_device, TENANT_B, USER_DAVE, f"fp-dave-{dave_device[:8]}"),
    )
    try:
        dave = _device_headers(device_id=dave_device, user_id=USER_DAVE, tenant_id=TENANT_B)
        assert _bundle_claims(client, dave)["policy_profiles"] == []
        assert client.get(path, headers=dave).status_code == 404
    finally:
        admin_conn.execute("DELETE FROM devices WHERE id = %s", (dave_device,))



def test_cron_schedule_label_reads_hermes_job_shapes():
    """The marketplace card shows what a Hermes cron job runs on (cron/jobs.py store shapes)."""
    from app.profile_marketplace.sanitize import _schedule_label

    assert _schedule_label({"schedule": {"kind": "cron", "expr": "0 9 * * *"}, "schedule_display": "0 9 * * *"}) == "0 9 * * *"
    assert _schedule_label({"schedule": {"kind": "cron", "expr": "0 9 * * 1", "display": "every monday 9am"}}) == "every monday 9am"
    assert _schedule_label({"schedule": {"kind": "once", "run_at": "2026-12-01T09:00:00Z"}}) == "2026-12-01T09:00:00Z"
    assert _schedule_label({"schedule": "30m"}) == "30m"

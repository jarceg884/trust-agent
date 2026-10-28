"""Spec 029 — Catálogo de policies v2: 29 keys tipadas (matriz P-1..P-29).

AC-029-1 upsert válido por tipo · AC-029-2 no-relajación (REQ-POL-007) ·
AC-029-3 key/enum/piso inválidos → 422 · AC-029-4 deny-overrides 3 niveles
(intersección, unión de denies, AND/OR booleanos, mínimo, STRING_RANKS,
deny-overrides por modo de web_domain_policy) + unit tests del combinator.
"""
import os

import pytest

from conftest import (
    DEPT_A1, TEST_DB, USER_CAROL, _admin, bearer_headers, mint,
)

# (value_type, default del proveedor) — paridad con la migración 0022 y
# docs/hermes-capability-matrix.md §3.
NEW_KEYS = {
    "approval_mode": ("string", "smart"),
    "allowed_terminal_backends": ("list", ["docker"]),
    "denied_command_patterns": ("list", []),
    "allowed_command_patterns": ("list", []),
    "require_write_sandbox": ("boolean", True),
    "allowed_mcp_servers": ("list", []),
    "allowed_plugins": ("list", []),
    "allowed_skills": ("list", ["*"]),
    "allow_scheduled_tasks": ("boolean", False),
    "allow_recurring_prompts": ("boolean", False),
    "allow_goal_mode": ("boolean", False),
    "max_delegation_depth": ("integer", 1),
    "allowed_memory_providers": ("list", ["builtin"]),
    "allow_custom_personality": ("boolean", False),
    "allowed_secret_sources": ("list", ["env"]),
    "allow_credential_vault": ("boolean", False),
    "allow_payment_autofill": ("boolean", False),
    "allowed_env_passthrough": ("list", []),
    "web_domain_policy": ("map", {"mode": "open", "domains": []}),
    "allow_private_network_access": ("boolean", False),
    "allow_voice_mode": ("boolean", False),
    "allow_bot_mode": ("boolean", False),
    "allow_gateway_hooks": ("boolean", False),
    "allow_shell_hooks": ("boolean", False),
    "allow_profile_distributions": ("boolean", False),
    "allowed_providers": ("list", ["litellm_gateway"]),
    "session_retention_days": ("integer", 30),
    "require_checkpoints": ("boolean", True),
    "allow_self_updates": ("boolean", False),
}


def _put(client, path, key, value, tokens):
    return client.put(path, json={"key": key, "value": value},
                      headers=bearer_headers(tokens))


def _delete(client, level, key, tokens, **params):
    return client.delete(f"/api/policies/{level}/{key}", params=params,
                         headers=bearer_headers(tokens))


@pytest.fixture()
def cleanup(client, idp):
    """Borra TODAS las policies nuevas fijadas por un test (los 3 niveles),
    para no contaminar al resto de la suite que comparte el tenant."""
    alice = mint(client, idp, "alice")
    done: list[tuple[str, str, dict]] = []

    def _remember(level, key, **params):
        done.append((level, key, params))

    yield _remember
    for level, key, params in reversed(done):
        _delete(client, level, key, alice, **params)


# ---------------------------------------------------------------- AC-029-0

def test_catalog_contains_the_29_new_keys(db):
    """La migración 0022 dejó las 29 keys en catalog.policy_keys con tipo y
    default exactos (paridad schema.sql ↔ Alembic)."""
    with _admin(TEST_DB) as c:
        rows = c.execute(
            "SELECT key, value_type, default_value, invariable "
            "FROM catalog.policy_keys WHERE key = ANY(%s)",
            (list(NEW_KEYS),),
        ).fetchall()
    assert len(rows) == 29
    seen = {r[0]: (r[1], r[2]) for r in rows}
    for key, (vt, default) in NEW_KEYS.items():
        assert key in seen, key
        assert seen[key][0] == vt, key
        assert seen[key][1] == default, key
    # Ninguna es invariable: los 3 niveles pueden fijarlas (REQ-029-001).
    assert all(r[3] is False for r in rows)


# ---------------------------------------------------------------- AC-029-1

def test_valid_upsert_per_value_type(client, idp):
    """AC-029-1: un upsert válido de cada tipo (string enum, list, boolean
    require/allow, integer, map) responde 200 y versiona; tras el DELETE la
    efectiva vuelve al default del catálogo."""
    alice = mint(client, idp, "alice")
    cases = [
        ("approval_mode", "manual"),
        ("allowed_terminal_backends", ["docker", "ssh"]),
        ("denied_command_patterns", ["rm -rf *"]),
        ("require_write_sandbox", True),
        ("allow_voice_mode", True),
        ("max_delegation_depth", 2),
        ("allowed_providers", ["litellm_gateway", "openrouter"]),
        ("web_domain_policy", {"mode": "blocklist", "domains": ["evil.com"]}),
    ]
    for key, value in cases:
        resp = _put(client, "/api/policies/org", key, value, alice)
        assert resp.status_code == 200, (key, resp.text)
        assert resp.json()["version"] >= 1
        assert _delete(client, "org", key, alice).status_code == 204
    # La efectiva refleja el default del catálogo una vez desactivadas.
    carol = mint(client, idp, "carol")
    eff = client.get("/api/me/policies", headers=bearer_headers(carol)).json()
    assert eff["approval_mode"] == "smart"
    assert eff["allowed_skills"] == ["*"]
    assert eff["web_domain_policy"] == {"mode": "open", "domains": []}
    assert eff["session_retention_days"] == 30
    assert eff["require_write_sandbox"] is True
    assert eff["allowed_providers"] == ["litellm_gateway"]
    assert eff["allow_bot_mode"] is False


# ---------------------------------------------------------------- AC-029-2

def test_no_relaxation_between_levels(client, idp, cleanup):
    """AC-029-2 (REQ-POL-007): un valor de depto/usuario que relaja el nivel
    superior → 422; uno más restrictivo → 200."""
    alice = mint(client, idp, "alice")
    dept_path = f"/api/policies/dept/{DEPT_A1}"
    user_path = f"/api/policies/user/{USER_CAROL}"

    # approval_mode: bajar el rank (smart→off) relaja → 422; subir → 200.
    assert _put(client, "/api/policies/org", "approval_mode", "smart",
                alice).status_code == 200
    cleanup("org", "approval_mode")
    assert _put(client, dept_path, "approval_mode", "off",
                alice).status_code == 422
    assert _put(client, dept_path, "approval_mode", "manual",
                alice).status_code == 200
    cleanup("dept", "approval_mode", department_id=DEPT_A1)

    # denied_command_patterns (UNIÓN): quitar denies relaja → 422.
    assert _put(client, "/api/policies/org", "denied_command_patterns",
                ["rm -rf *"], alice).status_code == 200
    cleanup("org", "denied_command_patterns")
    assert _put(client, dept_path, "denied_command_patterns", [],
                alice).status_code == 422
    assert _put(client, dept_path, "denied_command_patterns",
                ["rm -rf *", "dd *"], alice).status_code == 200
    cleanup("dept", "denied_command_patterns", department_id=DEPT_A1)

    # require_write_sandbox (OR, true gana): apagar lo que el org exige → 422.
    assert _put(client, "/api/policies/org", "require_write_sandbox", True,
                alice).status_code == 200
    cleanup("org", "require_write_sandbox")
    assert _put(client, dept_path, "require_write_sandbox", False,
                alice).status_code == 422

    # allow_voice_mode (AND): el depto puede restringir más (false)...
    assert _put(client, "/api/policies/org", "allow_voice_mode", True,
                alice).status_code == 200
    cleanup("org", "allow_voice_mode")
    assert _put(client, dept_path, "allow_voice_mode", False,
                alice).status_code == 200
    cleanup("dept", "allow_voice_mode", department_id=DEPT_A1)
    # ...y el usuario no puede volver a relajar por encima del depto.
    assert _put(client, user_path, "allow_voice_mode", True,
                alice).status_code == 422

    # max_delegation_depth (mínimo): subir el techo del org relaja → 422.
    assert _put(client, "/api/policies/org", "max_delegation_depth", 2,
                alice).status_code == 200
    cleanup("org", "max_delegation_depth")
    assert _put(client, dept_path, "max_delegation_depth", 5,
                alice).status_code == 422
    assert _put(client, dept_path, "max_delegation_depth", 1,
                alice).status_code == 200
    cleanup("dept", "max_delegation_depth", department_id=DEPT_A1)

    # allowed_skills (intersección): el usuario no puede ensanchar el depto.
    assert _put(client, dept_path, "allowed_skills", ["math"],
                alice).status_code == 200
    cleanup("dept", "allowed_skills", department_id=DEPT_A1)
    assert _put(client, user_path, "allowed_skills", ["*"],
                alice).status_code == 422

    # web_domain_policy: volver a open cuando el org fijó allowlist relaja.
    assert _put(client, "/api/policies/org", "web_domain_policy",
                {"mode": "allowlist", "domains": ["a.com", "b.com"]},
                alice).status_code == 200
    cleanup("org", "web_domain_policy")
    assert _put(client, dept_path, "web_domain_policy", {"mode": "open"},
                alice).status_code == 422
    assert _put(client, dept_path, "web_domain_policy",
                {"mode": "allowlist", "domains": ["a.com"]},
                alice).status_code == 200
    cleanup("dept", "web_domain_policy", department_id=DEPT_A1)


# ---------------------------------------------------------------- AC-029-3

def test_unknown_key_enum_and_floors(client, idp):
    """AC-029-3: key fuera de catálogo → 422; enum inválido → 422;
    entero bajo el piso del proveedor → 422."""
    alice = mint(client, idp, "alice")
    # Key desconocida (typo de approval_mode).
    assert _put(client, "/api/policies/org", "approval_modes", "manual",
                alice).status_code == 422
    # Enum inválido (approval_mode fuera de off|smart|manual).
    assert _put(client, "/api/policies/org", "approval_mode", "yolo",
                alice).status_code == 422
    # web_domain_policy: modo fuera de open|allowlist|blocklist.
    assert _put(client, "/api/policies/org", "web_domain_policy",
                {"mode": "custom", "domains": []},
                alice).status_code == 422
    # web_domain_policy: domains con tipo incorrecto.
    assert _put(client, "/api/policies/org", "web_domain_policy",
                {"mode": "open", "domains": "all"},
                alice).status_code == 422
    # Pisos enteros (REQ-029-004).
    assert _put(client, "/api/policies/org", "session_retention_days", 0,
                alice).status_code == 422
    assert _put(client, "/api/policies/org", "max_delegation_depth", -1,
                alice).status_code == 422
    # Tipos incorrectos.
    assert _put(client, "/api/policies/org", "allow_bot_mode", "yes",
                alice).status_code == 422
    assert _put(client, "/api/policies/org", "allowed_plugins", "mcp",
                alice).status_code == 422


# ---------------------------------------------------------------- AC-029-4

def test_three_level_deny_overrides(client, idp, cleanup):
    """AC-029-4: la efectiva de Carol = deny-overrides de los 3 niveles con
    la semántica key-aware de la spec 029."""
    alice = mint(client, idp, "alice")
    dept_path = f"/api/policies/dept/{DEPT_A1}"
    user_path = f"/api/policies/user/{USER_CAROL}"

    org_values = {
        "allowed_terminal_backends": ["docker", "ssh", "local"],
        "denied_command_patterns": ["a*"],
        "approval_mode": "smart",
        "max_delegation_depth": 3,
        "allow_voice_mode": True,
        "require_write_sandbox": False,
        "require_checkpoints": False,
        "web_domain_policy": {"mode": "allowlist",
                              "domains": ["a.com", "b.com", "c.com"]},
    }
    dept_values = {
        "allowed_terminal_backends": ["docker", "ssh"],
        # UNIÓN: el nivel inferior solo puede AÑADIR denies (debe incluir
        # los del superior para no relajar).
        "denied_command_patterns": ["a*", "b*"],
        "approval_mode": "manual",
        "max_delegation_depth": 2,
        "allow_voice_mode": True,
        "require_write_sandbox": True,
        "require_checkpoints": True,
        "web_domain_policy": {"mode": "allowlist",
                              "domains": ["a.com", "b.com"]},
    }
    user_values = {
        "allowed_terminal_backends": ["docker"],
        "denied_command_patterns": ["a*", "b*", "c*"],
        "approval_mode": "manual",
        "max_delegation_depth": 1,
        "allow_voice_mode": False,
        "require_write_sandbox": True,
        "require_checkpoints": True,
        "web_domain_policy": {"mode": "allowlist", "domains": ["a.com"]},
    }
    for key, value in org_values.items():
        resp = _put(client, "/api/policies/org", key, value, alice)
        assert resp.status_code == 200, (key, resp.text)
        cleanup("org", key)
    for key, value in dept_values.items():
        resp = _put(client, dept_path, key, value, alice)
        assert resp.status_code == 200, (key, resp.text)
        cleanup("dept", key, department_id=DEPT_A1)
    for key, value in user_values.items():
        resp = _put(client, user_path, key, value, alice)
        assert resp.status_code == 200, (key, resp.text)
        cleanup("user", key, user_id=USER_CAROL)

    carol = mint(client, idp, "carol")
    eff = client.get("/api/me/policies", headers=bearer_headers(carol)).json()
    # listas: intersección de allowlists.
    assert eff["allowed_terminal_backends"] == ["docker"]
    # denies: UNIÓN (deny-overrides).
    assert eff["denied_command_patterns"] == ["a*", "b*", "c*"]
    # string: rank máximo (manual > smart > off).
    assert eff["approval_mode"] == "manual"
    # integer: mínimo.
    assert eff["max_delegation_depth"] == 1
    # boolean AND (false gana) para allow_*.
    assert eff["allow_voice_mode"] is False
    # boolean OR (true gana) para require_*.
    assert eff["require_write_sandbox"] is True
    assert eff["require_checkpoints"] is True
    # map: deny-overrides por modo (allowlist = intersección).
    assert eff["web_domain_policy"] == {"mode": "allowlist",
                                        "domains": ["a.com"]}


# ------------------------------------------------- combinator (unit, sin DB)

def _catalog_for(keys):
    return {
        k: {"value_type": NEW_KEYS[k][0], "invariable": False,
            "default_value": NEW_KEYS[k][1]}
        for k in keys
    }


def test_combinator_string_ranks_and_booleans():
    from app.governance.combinator import combine

    catalog = _catalog_for(["approval_mode", "allow_voice_mode",
                            "require_write_sandbox", "require_checkpoints"])
    eff = combine(
        {"approval_mode": "smart", "allow_voice_mode": True,
         "require_write_sandbox": False, "require_checkpoints": False},
        {"approval_mode": "off", "allow_voice_mode": True,
         "require_write_sandbox": True, "require_checkpoints": True},
        {"approval_mode": "smart", "allow_voice_mode": False,
         "require_write_sandbox": True, "require_checkpoints": True},
        catalog,
    )
    assert eff["approval_mode"] == "smart"  # rank máximo pese al depto off
    assert eff["allow_voice_mode"] is False  # AND
    assert eff["require_write_sandbox"] is True  # OR (true gana)
    assert eff["require_checkpoints"] is True  # OR (true gana)


def test_combinator_deny_union_and_integer_min():
    from app.governance.combinator import combine

    catalog = _catalog_for(["denied_command_patterns", "allowed_skills",
                            "session_retention_days", "max_delegation_depth"])
    eff = combine(
        {"denied_command_patterns": ["a*"], "allowed_skills": ["math", "web"],
         "session_retention_days": 90, "max_delegation_depth": 3},
        {"denied_command_patterns": ["b*"], "allowed_skills": ["math", "web",
                                                              "pdf"],
         "session_retention_days": 30, "max_delegation_depth": 2},
        {"denied_command_patterns": ["c*"], "allowed_skills": ["math"],
         "session_retention_days": 7, "max_delegation_depth": 5},
        catalog,
    )
    assert eff["denied_command_patterns"] == ["a*", "b*", "c*"]  # unión
    assert eff["allowed_skills"] == ["math"]  # intersección
    assert eff["session_retention_days"] == 7  # mínimo
    assert eff["max_delegation_depth"] == 2  # mínimo


def test_combinator_web_domain_policy_modes():
    from app.governance.combinator import combine

    catalog = _catalog_for(["web_domain_policy"])
    # Todos open → open.
    eff = combine({"web_domain_policy": {"mode": "open", "domains": []}},
                  {"web_domain_policy": {"mode": "open", "domains": []}},
                  {"web_domain_policy": {"mode": "open", "domains": []}},
                  catalog)
    assert eff["web_domain_policy"] == {"mode": "open", "domains": []}
    # Blocklists de cualquier nivel se aplican (unión); open no relaja.
    eff = combine({"web_domain_policy": {"mode": "blocklist",
                                         "domains": ["x.com"]}},
                  {"web_domain_policy": {"mode": "blocklist",
                                         "domains": ["y.com"]}},
                  {"web_domain_policy": {"mode": "open", "domains": []}},
                  catalog)
    assert eff["web_domain_policy"] == {"mode": "blocklist",
                                        "domains": ["x.com", "y.com"]}
    # Allowlist = intersección, MENOS los dominios bloqueados por cualquier
    # nivel (deny-overrides).
    eff = combine({"web_domain_policy": {"mode": "allowlist",
                                         "domains": ["a.com", "b.com",
                                                     "c.com"]}},
                  {"web_domain_policy": {"mode": "blocklist",
                                         "domains": ["c.com"]}},
                  {"web_domain_policy": {"mode": "open", "domains": []}},
                  catalog)
    assert eff["web_domain_policy"] == {"mode": "allowlist",
                                        "domains": ["a.com", "b.com"]}

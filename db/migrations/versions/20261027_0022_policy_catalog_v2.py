"""Migración 0022: policy_catalog_v2

Revision ID: 0022_policy_catalog_v2
Revises: 0021_hermes_download
Create Date: 20261027

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).

Spec 029 (catálogo de policies v2, t_fb62560f): las 29 policy keys NUEVAS
que la matriz de capacidades (docs/hermes-capability-matrix.md §3, P-1..P-29,
t_d8ed9362) identificó como gap — deny-by-default: toda capacidad de Hermes
sin policy key es un gap. Misma maquinaria que REQ-GOV-020..024 (migración
0011): catálogo tipado + combinator deny-overrides + no-relajación
(REQ-POL-007). Todas ``invariable = false``.

Semántica de combinación (combinator.py, key-aware desde esta spec):
- approval_mode: STRING_RANKS manual(2) > smart(1) > off(0)
- denied_command_patterns: UNIÓN (deny-overrides); resto de listas: intersección
- require_write_sandbox / require_checkpoints: OR (true gana); allow_*: AND
- max_delegation_depth (piso 0) / session_retention_days (piso 1): mínimo,
  pisos enforceados en policies.py::INTEGER_FLOORS
- web_domain_policy: map {mode: open|allowlist|blocklist, domains: []}
  combinado por deny-overrides por modo
"""
from alembic import op
import sqlalchemy as sa


revision = "0022_policy_catalog_v2"
down_revision = "0021_hermes_download"
branch_labels = None
depends_on = None

# (key, value_type, default_json, description) — paridad exacta con
# db/schema.sql §3.2 (la exige db/tests/test_schema_parity.py).
_NEW_KEYS = [
    (
        "approval_mode",
        "string",
        '"smart"',
        "Command approval mode floor: off|smart|manual (STRING_RANKS manual>smart>off; "
        "vetoes approvals.mode:off, --yolo, /yolo, HERMES_YOLO_MODE).",
    ),
    (
        "allowed_terminal_backends",
        "list",
        '["docker"]',
        "Terminal backends the level allows (local,ssh,docker,modal,daytona,"
        "vercel_sandbox,singularity,*); intersection across levels.",
    ),
    (
        "denied_command_patterns",
        "list",
        "[]",
        "fnmatch globs of commands denied unconditionally; UNION across levels "
        "(deny-overrides: more denials = more restrictive).",
    ),
    (
        "allowed_command_patterns",
        "list",
        "[]",
        "Ceiling for the agent's permanent command_allowlist; intersection across "
        "levels.",
    ),
    (
        "require_write_sandbox",
        "boolean",
        "true",
        "File writes confined to the write sandbox (HERMES_WRITE_SAFE_ROOT); OR "
        "across levels (true wins).",
    ),
    (
        "allowed_mcp_servers",
        "list",
        "[]",
        "MCP server names the level allows (*); intersection across levels.",
    ),
    (
        "allowed_plugins",
        "list",
        "[]",
        "Plugin names the level allows (*); intersection across levels.",
    ),
    (
        "allowed_skills",
        "list",
        '["*"]',
        "Skill ids the level allows (*); intersection across levels (spec 001 "
        "REQ-PRF-005 — was referenced but missing from the catalog).",
    ),
    (
        "allow_scheduled_tasks",
        "boolean",
        "false",
        "Agent may create cron jobs / scheduled tasks; AND across levels.",
    ),
    (
        "allow_recurring_prompts",
        "boolean",
        "false",
        "Recurring prompts in-session (/loop, session heartbeat); AND across levels.",
    ),
    (
        "allow_goal_mode",
        "boolean",
        "false",
        "Goal mode (LLM-judged goal loop); AND across levels.",
    ),
    (
        "max_delegation_depth",
        "integer",
        "1",
        "Max subagent spawn depth (provider floor 0); minimum across levels.",
    ),
    (
        "allowed_memory_providers",
        "list",
        '["builtin"]',
        "Memory providers the level allows (builtin + names, *); intersection.",
    ),
    (
        "allow_custom_personality",
        "boolean",
        "false",
        "User may set a custom personality (SOUL.md / agent.personalities); AND.",
    ),
    (
        "allowed_secret_sources",
        "list",
        '["env"]',
        "Secret sources the level allows (env + sources, *); intersection.",
    ),
    (
        "allow_credential_vault",
        "boolean",
        "false",
        "Browser credential vault (logins/cards) and external login adoption; AND.",
    ),
    (
        "allow_payment_autofill",
        "boolean",
        "false",
        "Payment/card autofill prompts; AND across levels.",
    ),
    (
        "allowed_env_passthrough",
        "list",
        "[]",
        "Environment variable names that may pass through to sandboxes (*); "
        "intersection across levels.",
    ),
    (
        "web_domain_policy",
        "map",
        '{"mode": "open", "domains": []}',
        "Web domain policy {mode: open|allowlist|blocklist, domains: []}; "
        "deny-overrides per mode (any blocklist applies; allowlist = intersection).",
    ),
    (
        "allow_private_network_access",
        "boolean",
        "false",
        "Agent may reach private networks/metadata endpoints (SSRF opt-out); AND.",
    ),
    (
        "allow_voice_mode",
        "boolean",
        "false",
        "Voice mode (STT+TTS / full-duplex) and wake word; AND across levels.",
    ),
    (
        "allow_bot_mode",
        "boolean",
        "false",
        "Bot mode (bot roster, message_agent, bot_peers); AND across levels.",
    ),
    (
        "allow_gateway_hooks",
        "boolean",
        "false",
        "Gateway event hooks (~/.hermes/hooks/, trusted-by-placement); AND.",
    ),
    (
        "allow_shell_hooks",
        "boolean",
        "false",
        "Shell hooks (hooks: in config.yaml); AND across levels.",
    ),
    (
        "allow_profile_distributions",
        "boolean",
        "false",
        "Profile distributions (hermes profile install) and agent imports; AND.",
    ),
    (
        "allowed_providers",
        "list",
        '["litellm_gateway"]',
        "Model providers the level allows (litellm_gateway + providers, *); "
        "intersection across levels (routing/fallback cannot escape the list).",
    ),
    (
        "session_retention_days",
        "integer",
        "30",
        "Session retention window in days (provider floor 1); minimum across levels.",
    ),
    (
        "require_checkpoints",
        "boolean",
        "true",
        "Checkpoints (shadow git store / rollback) must be enabled; OR across "
        "levels (true wins).",
    ),
    (
        "allow_self_updates",
        "boolean",
        "false",
        "Self-updates (hermes update, lazy installs, catalog installs); AND.",
    ),
]


def upgrade() -> None:
    for key, value_type, default_json, description in _NEW_KEYS:
        op.execute(
            sa.text(
                "INSERT INTO catalog.policy_keys "
                "(key, value_type, invariable, default_value, description) "
                "VALUES (:k, :vt, false, CAST(:dv AS jsonb), :d) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, vt=value_type, dv=default_json, d=description)
        )


def downgrade() -> None:
    for key, _, _, _ in _NEW_KEYS:
        op.execute(
            sa.text("DELETE FROM catalog.policy_keys WHERE key = :k").bindparams(k=key)
        )

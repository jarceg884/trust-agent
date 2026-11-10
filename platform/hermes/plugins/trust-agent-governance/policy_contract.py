"""policy_contract.py — contrato de schema versionado del bundle (spec 019
D-GOH-11, REQ-GOH-022/023, t_150e8931).

Módulo compartido por TODOS los consumidores del bundle dentro del plugin
(``bundle_client``, ``enforcement``, y los batches de enforcement de las
lanes t_7e2852f8/t_a868974a): UNA sola verdad sobre qué keys existen, qué
hacer con las desconocidas y qué valor toma una key ausente.

Reglas del contrato (normativas, spec 019 §5.2):

1. **Schema versionado**: el payload lleva ``schema_version`` (entero).
   Ausente = schema 1 (bundle viejo emitido antes del requisito) — verifica
   igual (compatibilidad retro, REQ-GOH-022).

2. **Unknown-key policy = ignore-but-never-relax**: una key que este plugin
   no conoce, o un ``schema_version`` mayor al soportado, NO rompe la
   verificación ni el parseo: se ignora con warning ``unknown_schema_keys``
   en el log local y JAMÁS relaja nada. Una key desconocida nunca
   desbloquea una capacidad: el plugin SOLO lee keys de ``KNOWN_POLICY_KEYS``
   vía :func:`policy_value` — no existe código que consulte una key
   desconocida.

3. **Deny-by-default por key ausente** (REQ-GOH-023): una key conocida que
   NO está en el bundle (bundle viejo) toma el default RESTRINGIDO del
   plugin (``RESTRICTIVE_DEFAULTS``), NO el default del catálogo del
   proveedor. Un tenant que nunca configuró ``allow_voice_mode`` obtiene
   ``false`` en el agente hasta que la org lo configure. Excepción: las
   tools internas del plugin (``governance_status``), siempre permitidas
   (REQ-GOH-005).

Cero parches al core: módulo puro, sin imports de ``hermes_cli``.
"""
from __future__ import annotations

from typing import Any

# Máximo schema que este plugin entiende. El control plane puede emitir
# bundles con schema mayor (keys nuevas que aún no enforcementamos): se
# verifican y aplican las keys conocidas; las nuevas se ignoran con warning.
SUPPORTED_SCHEMA_VERSION = 2

# Claims estructurales del payload (spec 019 §5.2): NO son policy keys.
STRUCTURAL_CLAIMS = frozenset({
    "iss", "aud", "exp", "iat", "jti",
    "tenant_id", "user_id", "device_id",
    "policy_version", "schema_version",
    "budget", "approved_models", "allowed_connections",
    # Instrucciones de la organización (policy bundles spec 044): viajan en
    # el bundle firmado y se INYECTAN en el system prompt como sección
    # gobernada. No son una policy key con enforcement booleano — son texto.
    "global_prompt",
    # Superficies del Policy Bundle (spec 044) que el bundle firmado
    # transporta para el desktop/gateway (modelos por defecto, perfiles,
    # agentes, contratos, credenciales y flags de UI). Son datos
    # estructurales del payload — no policy keys del catálogo — y el plugin
    # los ignora deliberadamente (los consume la capa desktop), así que no
    # deben contar como "unknown policy keys".
    "ui_features", "policy_credentials", "policy_profiles", "policy_models",
    "policy_default_model", "policy_agents", "policy_contracts",
    # White-label identity (2026-11-05): the desktop renders the product and
    # organization name from the signed bundle — data, not policy keys.
    "product_name", "tenant_name",
})

# Keys del catálogo que este plugin conoce (18 existentes + 29 nuevas de la
# matriz de capacidades P-1..P-29). Una key del bundle fuera de este conjunto
# (y fuera de STRUCTURAL_CLAIMS) es "desconocida": se ignora con warning.
KNOWN_POLICY_KEYS = frozenset({
    # --- 18 keys existentes del catálogo (migración 0011 y anteriores) ---
    "require_mfa", "allow_self_registration", "approved_models_only",
    "explicit_shared_agent_grants", "scoped_credentials",
    "allowed_models", "allowed_connectors", "shared_agent_grants_mode",
    "vault_allowed_credential_types", "session_timeout_minutes",
    "artifact_sharing", "messaging_permissions", "max_daily_sessions",
    "allowed_tools", "offline_grace_minutes", "unbind_mode",
    "attestation_overdue_hours", "policy_models", "policy_default_model",
    # ``allowed_connections`` es STRUCTURAL (se compila aparte, spec 024).
    # --- 29 keys nuevas (matriz de capacidades §3, P-1..P-29) ---
    "approval_mode", "allowed_terminal_backends", "denied_command_patterns",
    "allowed_command_patterns", "require_write_sandbox",
    "allowed_mcp_servers", "allowed_plugins", "allowed_skills",
    "allow_scheduled_tasks", "allow_recurring_prompts", "allow_goal_mode",
    "max_delegation_depth", "allowed_memory_providers",
    "allow_custom_personality", "allowed_secret_sources",
    "allow_credential_vault", "allow_payment_autofill",
    "allowed_env_passthrough", "web_domain_policy",
    "allow_private_network_access", "allow_voice_mode", "allow_bot_mode",
    "allow_gateway_hooks", "allow_shell_hooks",
    "allow_profile_distributions", "allowed_providers",
    "session_retention_days", "require_checkpoints", "allow_self_updates",
})

# Default RESTRINGIDO por key ausente (REQ-GOH-023): el valor más
# restrictivo posible de cada tipo — NO el default del catálogo del
# proveedor. Los batches de enforcement (t_7e2852f8/t_a868974a) DEBEN
# resolver toda key vía policy_value(), nunca con payload.get() crudo.
RESTRICTIVE_DEFAULTS: dict[str, Any] = {
    # booleans de capacidad: ausente = prohibido
    "allow_scheduled_tasks": False,
    "allow_recurring_prompts": False,
    "allow_goal_mode": False,
    "allow_custom_personality": False,
    "allow_credential_vault": False,
    "allow_payment_autofill": False,
    "allow_private_network_access": False,
    "allow_voice_mode": False,
    "allow_bot_mode": False,
    "allow_gateway_hooks": False,
    "allow_shell_hooks": False,
    "allow_profile_distributions": False,
    "allow_self_updates": False,
    # booleans de requisito: ausente = exigido (restrictivo)
    "require_write_sandbox": True,
    "require_checkpoints": True,
    # listas allow: ausente = nada permitido
    "allowed_terminal_backends": [],
    "allowed_command_patterns": [],
    "allowed_mcp_servers": [],
    "allowed_plugins": [],
    "allowed_skills": [],
    "allowed_memory_providers": [],
    "allowed_secret_sources": [],
    "allowed_env_passthrough": [],
    "allowed_providers": [],
    "allowed_models": [],
    "policy_models": [],
    "policy_default_model": "",
    "allowed_tools": [],
    "allowed_connectors": [],
    # deny-lists: ausente = sin denegaciones EXTRA (no desbloquean nada:
    # las allow-lists companion ya están vacías por defecto)
    "denied_command_patterns": [],
    # enteros: ausente = valor más restrictivo (mínimo piso)
    "max_delegation_depth": 0,
    "session_retention_days": 1,
    # enums rankeados: ausente = rank más restrictivo
    "approval_mode": "manual",
    "unbind_mode": "lock",
    # maps: ausente = nada permitido
    "web_domain_policy": {"mode": "allowlist", "domains": []},
    "messaging_permissions": {},
    # informativas (no se enforce localmente; valor conservador)
    "session_timeout_minutes": 1,
    "max_daily_sessions": 0,
    "offline_grace_minutes": 15,  # piso del proveedor (D-GOH-08)
    "attestation_overdue_hours": 1,
    "vault_allowed_credential_types": [],
    "shared_agent_grants_mode": "explicit",
    "artifact_sharing": "private",
    "require_mfa": True,
    "approved_models_only": True,
    "explicit_shared_agent_grants": True,
    "scoped_credentials": True,
    "allow_self_registration": False,
}


def schema_version_of(payload: dict[str, Any]) -> int:
    """``schema_version`` del bundle; ausente/garbage = 1 (schema viejo)."""
    try:
        return int(payload.get("schema_version", 1))
    except (TypeError, ValueError):
        return 1


def unknown_policy_keys(payload: dict[str, Any]) -> list[str]:
    """Keys del payload que este plugin NO conoce (ordenadas).

    Excluye los claims estructurales (no son policy keys). Una key
    desconocida es tolerada (ignore-but-never-relax) pero se reporta para
    el warning del log local.
    """
    return sorted(
        str(k) for k in payload
        if k not in STRUCTURAL_CLAIMS and k not in KNOWN_POLICY_KEYS
    )


def note_schema(payload: dict[str, Any], log_event) -> int:
    """Warning del contrato al aplicar un bundle NUEVO (REQ-GOH-022).

    Registra ``unknown_schema_keys`` en el log local cuando el bundle trae
    un schema mayor al soportado o keys desconocidas. NUNCA rechaza el
    bundle ni relaja nada: solo deja evidencia para el operador y para la
    próxima attestation. Retorna el schema_version del bundle.
    """
    version = schema_version_of(payload)
    unknown = unknown_policy_keys(payload)
    if version > SUPPORTED_SCHEMA_VERSION or unknown:
        log_event(
            "unknown_schema_keys",
            schema_version=version,
            supported=SUPPORTED_SCHEMA_VERSION,
            unknown_keys=unknown,
        )
    return version


def policy_value(payload: dict[str, Any] | None, key: str) -> Any:
    """Valor de una policy key del bundle con deny-by-default (REQ-GOH-023).

    - Key presente en el bundle → valor firmado del control plane.
    - Key ausente (bundle viejo) o sin bundle → ``RESTRICTIVE_DEFAULTS``
      del PLUGIN (no el default del catálogo).
    - Key desconocida para el plugin → KeyError: no existe enforcement que
      la consulte (ignore-but-never-relax — una key desconocida jamás
      desbloquea una capacidad).
    """
    if key not in KNOWN_POLICY_KEYS:
        raise KeyError(
            f"policy key '{key}' is not known to this plugin version "
            f"(ignore-but-never-relax: unknown keys are never read)"
        )
    if payload is not None and key in payload:
        return payload[key]
    return RESTRICTIVE_DEFAULTS[key]


def capability_allowed(payload: dict[str, Any] | None, key: str) -> bool:
    """¿La capability gobernada por ``key`` está permitida? (REQ-GOH-023).

    Deny-by-default: key ausente → default restrictivo (False para las
    allow_* / True para las require_* interpretado como "exigido" — para
    esas keys la capability se considera NO satisfecha a menos que el
    requisito se cumpla, decisión del batch de enforcement).
    """
    return bool(policy_value(payload, key))

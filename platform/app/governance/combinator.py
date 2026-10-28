"""Deny-overrides policy combinator (REQ-POL-002/004, arq. §5.7) — pure functions.

Semantics (D-CP-15): the effective policy is the MOST RESTRICTIVE
combination of the three levels (org / dept / user). A denial at any level
cannot be relaxed by another level. Provider-invariable keys (catalog.
policy_keys.invariable = true) ALWAYS resolve to their default value — the
provider floor (REQ-POL-004).

Per value type:
- boolean  : logical AND (every level must allow); EXCEPT the spec 029
             "require_*" keys where true is the restrictive value → logical
             OR (true wins, TRUE_WINS_KEYS)
- integer  : minimum (most restrictive)
- list     : intersection of allow-lists (a level defining [] allows nothing);
             EXCEPT deny-lists (DENY_UNION_KEYS, e.g. denied_command_patterns)
             which combine by UNION — more denials = more restrictive
- string   : rank by restrictiveness (see STRING_RANKS); unknown values rank
             as most restrictive (conservative)
- map      : key-specific: messaging_permissions → per-platform merge
             (allowed=false wins; otherwise the most restrictive mode);
             web_domain_policy → deny-overrides per mode (spec 029 REQ-029-005)
"""
from typing import Any

# Higher rank = more restrictive.
STRING_RANKS: dict[str, dict[str, int]] = {
    "shared_agent_grants_mode": {"explicit": 2, "open": 1},
    "artifact_sharing": {"private": 2, "shared": 1},
    # Spec 019 (D-GOH-08): unbind_mode decide el grado del modo restringido
    # al desligar/revocar — lock (sin operación) es más restrictivo que
    # readonly (solo conversación). También define el enum válido.
    "unbind_mode": {"lock": 2, "readonly": 1},
    # Spec 029 (REQ-029-003, matriz P-1): piso del modo de aprobación de
    # comandos. manual (aprobar todo) > smart > off (YOLO). Un nivel inferior
    # no puede bajar el rank — veta approvals.mode:off, --yolo, /yolo y
    # HERMES_YOLO_MODE. También define el enum válido.
    "approval_mode": {"manual": 2, "smart": 1, "off": 0},
}

# Spec 029 (REQ-029-006): booleanos direccionales — para estas keys TRUE es
# el valor restrictivo, así que combina OR (true gana). El resto de los
# booleanos (allow_*, require_mfa...) combina AND (false gana).
TRUE_WINS_KEYS: frozenset[str] = frozenset(
    {"require_write_sandbox", "require_checkpoints"}
)

# Spec 015 (D-AUTH-07/08): keys que gatillan comportamiento de la org ANTES
# de que exista contexto de departamento (login UX, auto-registro) — el gate
# lee SOLO el nivel org (governance.service.org_policy_value). Un override
# dept/user sería aceptado por el upsert pero jamás honrado: inoperante y
# visible en la matriz (hallazgo t_8f716f9f). Se rechazan en el upsert
# (policies.upsert → 422) y combine() los resuelve como el gate: solo org.
ORG_ONLY_KEYS: frozenset[str] = frozenset(
    {"require_mfa", "allow_self_registration"}
)

# Spec 029 (REQ-029-007): listas de DENEGACIÓN — combinan por UNIÓN
# (deny-overrides: más patrones denegados = más restrictivo). El resto de
# las listas (allowlists) sigue combinando por intersección.
DENY_UNION_KEYS: frozenset[str] = frozenset({"denied_command_patterns"})

# Spec 029 (REQ-029-005): modos válidos de web_domain_policy.
WEB_DOMAIN_MODES = ("open", "allowlist", "blocklist")
MODE_RANKS: dict[str, int] = {"read_only": 3, "reply": 2, "full": 1}
_UNKNOWN_RANK = 10**6


def _combine_boolean(key: str, values: list[Any]) -> Any:
    if key in TRUE_WINS_KEYS:
        return any(bool(v) for v in values)
    return all(bool(v) for v in values)


def _combine_integer(values: list[Any]) -> Any:
    return min(int(v) for v in values)


def _combine_list(key: str, values: list[list[Any]]) -> list[Any]:
    sets = [set(v) for v in values]
    if key in DENY_UNION_KEYS:
        out: set[Any] = set()
        for s in sets:
            out |= s
    else:
        out = sets[0]
        for s in sets[1:]:
            out &= s
    return sorted(out)


def _combine_string(key: str, values: list[str]) -> str:
    ranks = STRING_RANKS.get(key, {})
    return max(values, key=lambda v: ranks.get(v, _UNKNOWN_RANK))


def _combine_web_domain_policy(values: list[dict[str, Any]]) -> dict[str, Any]:
    """Spec 029 (REQ-029-005): deny-overrides por modo.

    - Algún nivel en allowlist → modo efectivo allowlist con la INTERSECCIÓN
      de las allowlists, menos los dominios bloqueados por cualquier nivel
      (un deny siempre aplica).
    - Sin allowlists pero algún blocklist → modo efectivo blocklist con la
      UNIÓN de las blocklists.
    - Todos open → open.
    """
    allowlists: list[set[str]] = []
    blocklists: list[set[str]] = []
    for level in values:
        spec = level or {}
        mode = spec.get("mode", "open")
        domains = spec.get("domains") or []
        if mode == "allowlist":
            allowlists.append(set(domains))
        elif mode == "blocklist":
            blocklists.append(set(domains))
    if allowlists:
        out = set(allowlists[0])
        for s in allowlists[1:]:
            out &= s
        for s in blocklists:
            out -= s  # deny-overrides: un bloqueo de cualquier nivel aplica
        return {"mode": "allowlist", "domains": sorted(out)}
    if blocklists:
        out = set()
        for s in blocklists:
            out |= s
        return {"mode": "blocklist", "domains": sorted(out)}
    return {"mode": "open", "domains": []}


def _combine_map(key: str, values: list[dict[str, Any]]) -> dict[str, Any]:
    if key == "web_domain_policy":
        return _combine_web_domain_policy(values)
    platforms: dict[str, list[dict[str, Any]]] = {}
    for level in values:
        for platform, spec in (level or {}).items():
            platforms.setdefault(platform, []).append(spec)
    effective: dict[str, Any] = {}
    for platform, specs in platforms.items():
        if any(not spec.get("allowed", False) for spec in specs):
            effective[platform] = {"allowed": False}
            continue
        modes = [spec.get("mode", "read_only") for spec in specs]
        mode = max(modes, key=lambda m: MODE_RANKS.get(m, _UNKNOWN_RANK))
        effective[platform] = {"allowed": True, "mode": mode}
    return effective


def combine(
    org: dict[str, Any],
    dept: dict[str, Any],
    user: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Combine the three policy levels into the effective policy.

    `catalog` maps key -> {"value_type": ..., "invariable": bool,
    "default_value": ...} (from catalog.policy_keys).
    """
    effective: dict[str, Any] = {}
    for key, meta in catalog.items():
        if meta.get("invariable"):
            # Provider floor: never relaxable at any level (REQ-POL-004).
            effective[key] = meta["default_value"]
            continue
        if key in ORG_ONLY_KEYS:
            # Spec 015 (D-AUTH-07/08): the gate reads ONLY the org level —
            # mirror it here so the effective policy equals actual behavior
            # even if a legacy dept/user row exists.
            effective[key] = org.get(key, meta["default_value"])
            continue
        values = [lvl[key] for lvl in (org, dept, user) if key in lvl]
        if not values:
            effective[key] = meta["default_value"]
            continue
        value_type = meta["value_type"]
        if value_type == "boolean":
            effective[key] = _combine_boolean(key, values)
        elif value_type == "integer":
            effective[key] = _combine_integer(values)
        elif value_type == "list":
            effective[key] = _combine_list(key, values)
        elif value_type == "string":
            effective[key] = _combine_string(key, values)
        elif value_type == "map":
            effective[key] = _combine_map(key, values)
        else:  # pragma: no cover - catalog constrains value_type
            effective[key] = meta["default_value"]
    return effective

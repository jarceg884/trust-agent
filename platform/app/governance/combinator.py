"""Deny-overrides policy combinator (REQ-POL-002/004, arq. §5.7) — pure functions.

Semantics (D-CP-15): the effective policy is the MOST RESTRICTIVE
combination of the three levels (org / dept / user). A denial at any level
cannot be relaxed by another level. Provider-invariable keys (catalog.
policy_keys.invariable = true) ALWAYS resolve to their default value — the
provider floor (REQ-POL-004).

Per value type:
- boolean  : logical AND (every level must allow)
- integer  : minimum (most restrictive)
- list     : intersection of allow-lists (a level defining [] allows nothing)
- string   : rank by restrictiveness (see STRING_RANKS); unknown values rank
             as most restrictive (conservative)
- map      : per-platform merge (messaging_permissions): allowed=false wins;
             otherwise the most restrictive mode (MODE_RANKS)
"""
from typing import Any

# Higher rank = more restrictive.
STRING_RANKS: dict[str, dict[str, int]] = {
    "shared_agent_grants_mode": {"explicit": 2, "open": 1},
    "artifact_sharing": {"private": 2, "shared": 1},
}
MODE_RANKS: dict[str, int] = {"read_only": 3, "reply": 2, "full": 1}
_UNKNOWN_RANK = 10**6


def _combine_boolean(values: list[Any]) -> Any:
    return all(bool(v) for v in values)


def _combine_integer(values: list[Any]) -> Any:
    return min(int(v) for v in values)


def _combine_list(values: list[list[Any]]) -> list[Any]:
    sets = [set(v) for v in values]
    out = sets[0]
    for s in sets[1:]:
        out &= s
    return sorted(out)


def _combine_string(key: str, values: list[str]) -> str:
    ranks = STRING_RANKS.get(key, {})
    return max(values, key=lambda v: ranks.get(v, _UNKNOWN_RANK))


def _combine_map(values: list[dict[str, Any]]) -> dict[str, Any]:
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
        values = [lvl[key] for lvl in (org, dept, user) if key in lvl]
        if not values:
            effective[key] = meta["default_value"]
            continue
        value_type = meta["value_type"]
        if value_type == "boolean":
            effective[key] = _combine_boolean(values)
        elif value_type == "integer":
            effective[key] = _combine_integer(values)
        elif value_type == "list":
            effective[key] = _combine_list(values)
        elif value_type == "string":
            effective[key] = _combine_string(key, values)
        elif value_type == "map":
            effective[key] = _combine_map(values)
        else:  # pragma: no cover - catalog constrains value_type
            effective[key] = meta["default_value"]
    return effective

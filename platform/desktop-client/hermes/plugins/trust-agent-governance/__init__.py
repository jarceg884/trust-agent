"""trust-agent-governance — puente oficial de gobernanza para Hermes Agent.

El desktop es Hermes CONFIGURADO, no un fork parcheado (constitution IV,
lección C6): este plugin usa exclusivamente la API pública de plugins
(`plugin.yaml` + ``register(ctx)``, verificada en ``hermes_cli/plugins.py``).

Qué registra:
  - tool ``governance_status``: presupuesto, políticas efectivas y modelos
    aprobados del usuario, desde la API real del control plane (spec 003/005)
    autenticada con el device token del enrolamiento (spec 007).
  - hook ``pre_llm_call``: inyecta el contexto de gobernanza vigente en cada
    turno (verificado: ``agent/turn_context.py::_collect_pre_llm_call_context``
    — es un hook de CONTEXTO, no de veto).
  - hook ``pre_tool_call``: veto de defensa en profundidad cuando una política
    de la organización deniega el tool (verificado:
    ``_dispatch_pre_tool_call_hooks`` consume ``block_message``).

El GATE REAL de modelos/presupuesto es LiteLLM: la virtual key del usuario
(profile config.yaml) solo acepta los modelos aprobados y aplica max_budget
(D-GOV-15). Este plugin aporta visibilidad y defensa en profundidad.

Config por entorno (perfil del usuario):
  TRUST_AGENT_API   — base URL del control plane (p. ej. http://127.0.0.1:8000)
  TRUST_DEVICE_TOKEN — device token del enrolamiento (spec 007; TTL 15 min,
                       renovable vía POST /api/devices/{id}/token)
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

TOOLSET = "trust_agent"


def _api_base() -> str:
    return os.environ.get("TRUST_AGENT_API", "http://127.0.0.1:8000").rstrip("/")


def _device_token() -> str | None:
    return os.environ.get("TRUST_DEVICE_TOKEN") or None


def _get(path: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET autenticado con el device token. Falla cerrado (levanta)."""
    token = _device_token()
    if not token:
        raise RuntimeError(
            "TRUST_DEVICE_TOKEN is not set — enroll this device first (spec 007)."
        )
    req = urllib.request.Request(
        f"{_api_base()}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - base URL por config
        return json.loads(resp.read().decode())


def _fetch_governed_state() -> dict[str, Any]:
    """Estado gobernado del usuario: presupuesto + políticas + modelos."""
    budget = _get("/api/me/budget")
    policies = _get("/api/me/policies")
    models = _get("/api/models/allowed-for-me").get("models", [])
    return {
        "budget": {
            "user_remaining_cents": budget["user"]["remaining_cents"],
            "user_spent_cents": budget["user"]["spent_cents"],
            "cutoff": budget.get("cutoff", False),
            "cutoff_level": budget.get("cutoff_level"),
        },
        "policies": policies,
        "approved_models": models,
    }


# --- tool: governance_status -------------------------------------------------

_STATUS_SCHEMA = {
    "name": "governance_status",
    "description": (
      "Show the organization's governed state for this user: remaining budget, "
      "active policies and approved models. Read-only, sourced live from the "
      "Trust Agent control plane."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def _handle_governance_status(**_: Any) -> str:
    try:
        state = _fetch_governed_state()
    except Exception as exc:  # noqa: BLE001 - el tool reporta, no tumba el turno
        return f"Governance status unavailable: {exc}"
    budget = state["budget"]
    lines = [
        "Trust Agent — governed session state",
        f"  Budget remaining (you): ${budget['user_remaining_cents'] / 100:,.2f} USD",
        f"  Spent this period:      ${budget['user_spent_cents'] / 100:,.2f} USD",
        f"  Cutoff:                 {'YES (' + str(budget['cutoff_level']) + ')' if budget['cutoff'] else 'no'}",
        f"  Approved models:        {', '.join(state['approved_models']) or 'none'}",
        "  Active policies:",
    ]
    for key, value in state["policies"].items():
        lines.append(f"    - {key} = {json.dumps(value)}")
    return "\n".join(lines)


# --- hook: pre_llm_call (contexto de gobernanza por turno) -------------------

def _on_pre_llm_call(model: str | None = None, **_: Any) -> dict[str, str]:
    """Contexto breve inyectado al mensaje del usuario cada turno.

    Si el control plane no responde, devolvemos contexto vacío (fail-open en
    VISIBILIDAD — el gate duro sigue siendo LiteLLM + DeviceGate).
    """
    try:
        state = _fetch_governed_state()
    except Exception:  # noqa: BLE001
        return {"context": ""}
    budget = state["budget"]
    cutoff = (
        "ATTENTION: spending is CUT OFF at the "
        f"{budget['cutoff_level']} level. "
        if budget.get("cutoff")
        else ""
    )
    context = (
        f"[organization governance] Approved models: "
        f"{', '.join(state['approved_models']) or 'none'}. "
        f"{cutoff}"
        "All activity is audited by your organization."
    )
    return {"context": context}


# --- hook: pre_tool_call (veto de defensa en profundidad) --------------------

def _on_pre_tool_call(tool: str = "", args: dict | None = None, **_: Any):
    """Bloquea tools que la política organizacional deniega.

    v1: cuando el corte de presupuesto está activo, se deniegan los tools de
    ejecución costosa (agent spawn / delegación). Las políticas de tools
    (messaging_permissions, artifact_sharing) se cablean aquí cuando el
    catálogo las active. Return: (block_message | None, modified_args | None).
    """
    args = args or {}
    try:
        budget = _get("/api/me/budget")
        cutoff = bool(budget.get("cutoff"))
    except Exception:  # noqa: BLE001 - sin señal, no vetar (el gate es LiteLLM)
        return None, None
    if cutoff and tool in {"delegate_task", "spawn_agent"}:
        return (
            "Blocked by organization policy: the budget is in cutoff. "
            "Ask your admin to raise the budget or wait for the next period.",
            None,
        )
    return None, None


def register(ctx) -> None:
    """Punto de entrada oficial del plugin (plugin.yaml → register(ctx))."""
    ctx.register_tool(
        name="governance_status",
        toolset=TOOLSET,
        schema=_STATUS_SCHEMA,
        handler=_handle_governance_status,
        description=(
            "Trust Agent governed state: budget, policies, approved models."
        ),
        emoji="🛡️",
    )
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)

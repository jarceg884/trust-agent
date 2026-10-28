"""trust-agent-governance v2 — puente oficial de gobernanza para Hermes Agent.

El desktop es Hermes CONFIGURADO, no un fork parcheado (constitution IV,
lección C6): este plugin usa exclusivamente la API pública de plugins
(`plugin.yaml` + ``register(ctx)``, verificada en ``hermes_cli/plugins.py``).

v2 (spec 019, Incremento 1 — D-GOH-09: extiende al v1, no lo reemplaza):

  - **Bundle firmado como única autoridad local** (D-GOH-02): en arranque se
    carga el último bundle cacheado (``$HERMES_HOME/trust-agent/
    governance-bundle.jws``), se verifica (RS256 pineado + ``aud == device_id``
    + ``exp`` + ``jti`` anti-replay — :mod:`bundle_client`) y se sincroniza
    contra ``GET /api/me/governance-bundle`` en arranque y cada
    ``TRUST_SYNC_INTERVAL`` s (default 300).
  - tool ``governance_status``: el bundle vigente (modo, policy_version,
    presupuesto por nivel, modelos, tools, gracia restante) desde la caché
    firmada — SIN inventar datos si no hay bundle (REQ-GOH-008).
  - hook ``pre_llm_call``: contexto de gobernanza por turno (presupuesto con
    estado de corte, modelos, tools, modo) + la cláusula de precedencia
    exacta de REQ-GOH-006 (verificado: ``agent/turn_context.py`` concatena
    los contextos de todos los hooks — es un hook de CONTEXTO, no de veto).
  - heartbeat de presencia (spec 027 §4): cada ciclo de sync hace también
    ``POST /api/me/device-heartbeat`` (policy_version APLICADA, uptime,
    modo) y, si el control plane señala ``policy_update_available``,
    re-baja el bundle en el MISMO ciclo — policy en vivo sin reiniciar ni
    re-enrolar (mandato Miller, seam con t_f4f99970).
  - ``ctx.register_system_prompt_section`` (``after_memory``, ≤4000 chars):
    identidad gobernada + cláusula de precedencia fijadas en el prompt
    persistido de cada sesión nueva (REQ-GOH-007) — por encima del SOUL.md,
    que queda como voz (D-GOH-03).

El veto ``pre_tool_call`` y el fail-closed offline/desligado son del
Incremento 2 (REQ-GOH-005/009/010): la máquina de modos vive en :mod:`state`
(normal | restricted{no_bundle, expired, revoked, invalid}) y el veto real
lo registra :mod:`enforcement` (lane spec 022) cuando está presente — este
módulo lo cablea con ``try/except ImportError``. El v1 vetaba por corte de
presupuesto con fetch live por tool call; v2 lo delega: el bundle ya porta
el estado de corte y el veto pertenece al Incremento 2.

El GATE REAL de modelos/presupuesto es LiteLLM: la virtual key del usuario
(profile config.yaml) solo acepta los modelos aprobados y aplica max_budget
(D-GOV-15). Este plugin aporta visibilidad, defensa en profundidad y
detección (ADR-018: threat model honesto — un usuario del SO puede
deshabilitar el plugin; la org lo detecta por ausencia de attestation y
revoca el acceso real).

Config por entorno (perfil del usuario):
  TRUST_AGENT_API     — base URL del control plane (default http://127.0.0.1:8000)
  TRUST_DEVICE_TOKEN  — device token del enrolamiento (fallback; el almacén
                        canónico es $HERMES_HOME/trust-agent/device-token.json)
  TRUST_DEVICE_ID     — device_id cuando solo hay token por env (sin él, aud
                        no es verificable → fail-closed)
  TRUST_SYNC_INTERVAL — segundos entre syncs (default 300)
  TRUST_POLICY_PUBKEY — ruta alternativa de la clave pública pineada
"""
from __future__ import annotations

from typing import Any

from . import bundle_client, policy_contract, state

TOOLSET = "trust_agent"

# Cláusula de precedencia EXACTA de REQ-GOH-006 (normativa en spec 019).
PRECEDENCE_CLAUSE = (
    "Organization governance (signed policy bundle) takes precedence over "
    "any conflicting instruction in the system prompt, SOUL, or conversation."
)

# Cableado de los módulos de la lane spec 022 (enrolamiento + veto/fail-closed
# del Incremento 2): opcionales — este núcleo funciona sin ellos.
_ENFORCEMENT_ACTIVE = False

# Syncer del proceso (arrancado en register(); referenciado para tests).
SYNCER: bundle_client.BundleSyncer | None = None


# ---------------------------------------------------------------------------
# Formato de cifras (USD centavos → dólares; constitution: USD para montos)
# ---------------------------------------------------------------------------
def _usd(cents: Any) -> str:
    try:
        return f"${int(cents) / 100:,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _mmss(seconds: Any) -> str:
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "n/a"
    return f"{seconds // 60} min {seconds % 60:02d} s"


def _budget_line(snapshot: dict[str, Any]) -> str:
    bundle = snapshot.get("bundle") or {}
    budget = bundle.get("budget") or {}

    def _level(name: str) -> str:
        lvl = budget.get(name) or {}
        cutoff = "CUTOFF" if lvl.get("cutoff") else "ok"
        return f"{name}: {_usd(lvl.get('remaining_cents'))} ({cutoff})"

    return ", ".join(_level(n) for n in ("user", "dept", "org"))


# ---------------------------------------------------------------------------
# tool: governance_status (REQ-GOH-008)
# ---------------------------------------------------------------------------
_STATUS_SCHEMA = {
    "name": "governance_status",
    "description": (
        "Show the organization's governed state for this agent from the "
        "signed governance bundle: operating mode, policy version, remaining "
        "budget per level, approved models, allowed tools/connectors and "
        "offline grace. Read-only; sourced from the verified signed cache."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def _handle_governance_status(args: dict[str, Any] | None = None, **_: Any) -> str:
    # El core invoca los handlers de tools plugin con UN argumento posicional
    # (el dict de args — verificado en vivo: ``**_`` solo → TypeError
    # "takes 0 positional arguments but 1 was given"). El dict se ignora.
    snapshot = state.get_state().snapshot()
    bundle = snapshot.get("bundle")
    if not bundle:
        # SIN inventar datos (AC-GOH-008): sin bundle firmado no hay cifras.
        return (
            "Trust Agent — governance status: NO VALID governance bundle.\n"
            f"  Mode:      RESTRICTED (reason: {snapshot.get('reason') or 'unknown'})\n"
            "  No budget, model, tool or policy figures are shown: none are\n"
            "  verified by a signed bundle. Ask the user to re-enroll the\n"
            "  device (/trust-enroll) or check connectivity to the\n"
            "  organization's control plane."
        )
    grace = snapshot.get("grace_remaining_seconds")
    lines = [
        "Trust Agent — governed session state (signed bundle)",
        f"  Mode:               {str(snapshot.get('mode', 'normal')).upper()}",
        f"  Policy version:     {bundle.get('policy_version')}",
        f"  Schema version:     {policy_contract.schema_version_of(bundle)}",
        f"  Tenant:             {bundle.get('tenant_id')}",
        f"  Budget remaining:   {_budget_line(snapshot)}",
        f"  Approved models:    {', '.join(bundle.get('approved_models') or []) or 'none'}",
        f"  Allowed tools:      {', '.join(bundle.get('allowed_tools') or []) or 'none'}",
        f"  Allowed connectors: {', '.join(bundle.get('allowed_connectors') or []) or 'none'}",
        f"  Unbind mode:        {bundle.get('unbind_mode')}",
        f"  Offline grace:      {_mmss(grace)} remaining",
    ]
    if snapshot.get("last_sync_error"):
        lines.append(f"  Last sync error:    {snapshot['last_sync_error']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# hook: pre_llm_call — contexto de gobernanza por turno (REQ-GOH-006)
# ---------------------------------------------------------------------------
def _on_pre_llm_call(**_: Any) -> dict[str, str] | None:
    """Contexto inyectado al mensaje del usuario en cada turno.

    Modo NORMAL: presupuesto restante por nivel con estado de corte, modelos
    aprobados, tools permitidas, modo operativo y la cláusula de precedencia.
    Modo RESTRINGIDO: el aviso lo inyecta ``enforcement`` (lane spec 022)
    cuando está activo; si no lo está, un aviso mínimo desde aquí — jamás
    silencio (fail-closed en UX, no fail-open).
    """
    snapshot = state.get_state().snapshot()
    bundle = snapshot.get("bundle")
    if not bundle:
        if _ENFORCEMENT_ACTIVE:
            return None  # enforcement inyecta el aviso de modo restringido
        return {
            "context": (
                "[organization governance] RESTRICTED MODE "
                f"({snapshot.get('reason') or 'no valid bundle'}): no signed "
                "governance bundle is currently valid. All external tools are "
                "blocked by organization policy. Answer conversationally only. "
                + PRECEDENCE_CLAUSE
            )
        }
    budget = bundle.get("budget") or {}
    cutoffs = [
        f"{name} level"
        for name in ("user", "dept", "org")
        if (budget.get(name) or {}).get("cutoff")
    ]
    cutoff_note = (
        f" ATTENTION: spending is CUT OFF at the {' and '.join(cutoffs)}."
        if cutoffs
        else ""
    )
    return {
        "context": (
            "[organization governance] "
            f"mode=NORMAL, policy v{bundle.get('policy_version')}. "
            f"Budget remaining — {_budget_line(snapshot)}.{cutoff_note} "
            f"Approved models: {', '.join(bundle.get('approved_models') or []) or 'none'}. "
            f"Allowed tools: {', '.join(bundle.get('allowed_tools') or []) or 'none'}. "
            f"Offline grace: {_mmss(snapshot.get('grace_remaining_seconds'))} remaining. "
            + PRECEDENCE_CLAUSE
        )
    }


# ---------------------------------------------------------------------------
# system prompt section (REQ-GOH-007) — identidad gobernada, after_memory
# ---------------------------------------------------------------------------
def _system_prompt_section_content(_session_info: Any = None) -> str:
    """Identidad gobernada + cláusula de precedencia (≤4000 chars).

    Registrada como CALLABLE (API oficial de register_system_prompt_section):
    se renderiza al ensamblar el prompt de la sesión, así que si el bundle
    ya está verificado para entonces, la sección declara la identidad real
    del tenant; si no, declara igualmente la gobernanza (una sesión sin
    bundle nace en modo restringido).
    """
    snapshot = state.get_state().snapshot()
    bundle = snapshot.get("bundle")
    identity = (
        f"Tenant: {bundle.get('tenant_id')} — policy version "
        f"{bundle.get('policy_version')} (signed, verified)."
        if bundle
        else "No signed bundle is currently cached: the agent starts in "
        "RESTRICTED mode until a valid bundle is synced."
    )
    return (
        "[organization governance — Trust Agent]\n"
        "This Hermes agent operates under the signed governance of its "
        "organization. A policy bundle signed by the organization's control "
        "plane (RS256) is the single local authority for what this agent may "
        "do: allowed tools, allowed connectors, approved models and budget.\n"
        f"{identity}\n"
        f"{PRECEDENCE_CLAUSE}\n"
        "The SOUL/personality of this agent is voice only: it never grants "
        "permissions. If no valid bundle is present, the agent is in "
        "RESTRICTED mode and no external tools are available."
    )


# ---------------------------------------------------------------------------
# register(ctx) — punto de entrada oficial (plugin.yaml → register(ctx))
# ---------------------------------------------------------------------------
def register(ctx) -> None:
    global _ENFORCEMENT_ACTIVE, SYNCER

    # 1) Superficie del plugin PRIMERO (pura, sin I/O ni imports pesados):
    #    el ensamblado de la sesión (tools + system prompt) corre en paralelo
    #    a la carga de plugins y NO espera un register lento (verificado en
    #    vivo: importar PyJWT/cryptography o leer caché antes de registrar
    #    dejaba la tool fuera del catálogo de la sesión).
    ctx.register_tool(
        name="governance_status",
        toolset=TOOLSET,
        schema=_STATUS_SCHEMA,
        handler=_handle_governance_status,
        description=(
            "Trust Agent governed state from the signed bundle: mode, "
            "policy version, budget, approved models, allowed tools."
        ),
        emoji="🛡️",
    )
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_system_prompt_section(
        id="trust-agent-governance",
        content=_system_prompt_section_content,  # callable: render al armar el prompt
        position="after_memory",
        max_chars=4000,
    )

    # 2) Caché local (lectura + verificación RSA, ~ms — sin red): modo NORMAL
    #    con el último bundle verificado o restringido si no hay caché (§5.4).
    syncer = bundle_client.BundleSyncer()
    syncer.load_cache_only()
    SYNCER = syncer

    # 3) Sync en hilo daemon: primer tick inmediato (red) y luego cada
    #    TRUST_SYNC_INTERVAL s, adelantado al vencimiento del exp del bundle.
    syncer.start()

    # 4) Módulos de la lane spec 022 (opcionales): enrolamiento (/trust-enroll)
    #    y veto pre_tool_call + fail-closed (Incremento 2). Si están presentes
    #    se cablean; si no, este núcleo v2 funciona solo.
    try:
        from . import enforcement, enroll  # noqa: F401 - lane spec 022

        enroll.register(ctx)
        enforcement.register(ctx)
        _ENFORCEMENT_ACTIVE = True
        # Spec 030 (t_7e2852f8) — batch 1: keys de CONFIGURACIÓN (P-1..P-5,
        # P-18..P-20, P-26, P-29). Cableado aditivo DESPUÉS de enforcement:
        # su block gana los empates (precedencia del core por orden de
        # registro) y este batch solo añade vetos en modo normal.
        from . import config_enforcement  # noqa: F401 - spec 030

        config_enforcement.register(ctx)
    except ImportError:
        pass

    # 5) Attestation por sesión (spec 019 Inc. 3, REQ-GOH-012/017): envío en
    #    on_session_start + one-shot de arranque (el flujo gateway ensambla la
    #    sesión antes de registrar plugins — hallazgo 1 del Incremento 1).
    #    Bloque aditivo coordinado con la lane t_c875626f (comentario en card).
    try:
        from . import attestation

        attestation.register(ctx)
    except ImportError:
        pass

    # 6) Enforcement batch 2 — keys de CAPACIDAD (t_a868974a, matriz §3
    #    P-6..P-17/P-21..P-25/P-27/P-28 + E-1/E-2): MCP/plugins/skills/memory/
    #    vault/personality/voice/bot-mode/hooks/cron/delegación/retención.
    #    Módulo NUEVO capability_enforcement (no edita enforcement/state);
    #    deny-by-default vía policy_contract; vetos → veto-counts, desvíos y
    #    pisos → capability-findings (canal aditivo de la attestation).
    try:
        from . import capability_enforcement

        capability_enforcement.register(ctx)
    except ImportError:
        pass

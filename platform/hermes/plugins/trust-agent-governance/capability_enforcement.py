"""capability_enforcement.py — enforcement batch 2: keys de CAPACIDAD (t_a868974a).

Matriz de capacidades §3 P-6..P-17, P-21..P-25, P-27, P-28 + gaps E-1/E-2
(docs/hermes-capability-matrix.md). TODO el enforcement de este batch vive en
este módulo NUEVO (estrategia anti-colisión de la lane: no edita
``enforcement.py``/``state.py`` de la lane batch 1 — los REUSA por import).

Deny-by-default (REQ-GOH-023): toda key se resuelve vía
``policy_contract.policy_value`` — key ausente (bundle schema 1) = default
RESTRINGIDO del plugin, nunca el default del catálogo. Un bundle viejo deja
las capacidades de este batch vetadas hasta que la org las configure.

Mapa key → superficie → mecanismo (verificado contra el core, sin parchearlo):

  P-6  allowed_mcp_servers      tools ``mcp__<server>__*`` → veto por nombre
                                en ``pre_tool_call``. Las tools MCP son
                                toolsets dinámicos, pero TODA ejecución pasa
                                por ``agent/tool_executor.py::_pre_tool_block``
                                (L671-677) → el veto por nombre alcanza la
                                carga diferida de tool_search (E-1b: la tool
                                diferida se invoca por su nombre REAL —
                                ``agent/prompt_builder.py`` L626) y los
                                children de delegate_task (E-1c: in-process,
                                ``agent/subagent_lifecycle.py`` L270 — los
                                hooks son del proceso y los children heredan
                                el ejecutor).
  P-7  allowed_plugins          scan de ``$HERMES_HOME/plugins/*/`` (manifest
                                + ``register_tool`` literales) → mapa
                                tool→plugin; tools de plugins no allowlisted
                                → veto. Detección de plugins cargados no
                                allowlisted → finding (``ctx.has_plugin``).
  P-8  allowed_skills           ``skill_view``/``skill_manage`` vetoado por
                                skill fuera de lista; ``skills_list`` vetoado
                                cuando la policy FIJA el catálogo (sin `*`):
                                el resultado de un listado no se puede
                                filtrar desde un hook — fail-closed. El piso
                                pisa ``skills.write_approval`` local (E-3c).
  P-13 allowed_memory_providers ``memory`` vetoado cuando el provider activo
                                (config ``memory.provider`` o plugin de
                                provider cargado) no está en la lista; tools
                                de provider (``honcho_*``, ``hindsight_*``…)
                                ídem. Advertencia → attestation.
  P-15 allowed_secret_sources   ``browser_vault action=unlock backend=…`` del
                                backend no allowlisted → veto; sources
                                externos habilitados en config (bitwarden/
                                onepassword/command) no allowlisted →
                                finding (la resolución de ``${secret:…}`` es
                                de proceso — no vetable por tool, E-3).
  P-16 allow_credential_vault   ``browser_vault`` vetoado entero cuando false.
  P-17 allow_payment_autofill   ``browser_vault action=fill`` de item no-login
                                (card/address) → veto. El kind del handle se
                                cachea del ``action=list`` (``post_tool_call``);
                                handle desconocido → fail-closed (el flujo
                                documentado es list-first).
  P-14 allow_custom_personality sección de prompt ``trust-agent-personality-lock``
                                (``after_memory``) cuando false + ``/personality``
                                interceptado en ``pre_gateway_dispatch``
                                (rewrite: el comando nunca ejecuta). El
                                SOUL.md queda POR DEBAJO: la sección de
                                gobernanza del núcleo v2 + la cláusula de
                                precedencia por turno ya lo subordinan.
  P-21 allow_voice_mode         ``/voice`` rewrite en pre_gateway_dispatch.
  P-22 allow_bot_mode           tool ``message_agent`` vetoada; desvío de
                                config ``agent.bot_mode_protocol`` → finding.
  P-23 allow_gateway_hooks      ``$HERMES_HOME/hooks/`` con entradas y false →
                                finding (RIESGO RESIDUAL E-3a: trusted-by-
                                placement, el plugin NO puede vetar la carga).
  P-24 allow_shell_hooks        config ``hooks:`` presente y false → finding
                                (los shell hooks corren fuera del pipeline de
                                plugins; la aprobación first-use es nativa).
  P-9  allow_scheduled_tasks    tool ``cronjob_manage`` vetoada; desvío de
                                ``cron.allow_agent_scheduling`` → finding.
  P-10 allow_recurring_prompts  ``/loop`` y ``/heartbeat`` rewrite.
  P-11 allow_goal_mode          ``/goal`` y ``/subgoal`` rewrite;
                                ``kanban_create(goal_mode=true)`` vetoado por
                                args.
  P-25 allow_profile_distrib.   ``terminal`` con comandos ``hermes profile
                                install`` / ``hermes import-agent`` vetoado
                                (el entry point IN-PROCESO; el CLI directo
                                del usuario es E-3: control local).
  P-12 max_delegation_depth     ``delegate_task`` vetoado cuando la
                                profundidad efectiva (contador vía hooks
                                ``subagent_start``/``subagent_stop``) alcanza
                                el máximo de la policy. Default restrictivo 0
                                = sin delegación. Desvío de
                                ``delegation.max_spawn_depth`` → finding.
  P-27 session_retention_days   piso APLICADO: ``hermes sessions prune
                                --older-than <N>d --yes --force`` (primitiva
                                oficial; 1 vez/24 h en hilo daemon; --force
                                porque el plugin corre DENTRO del proceso que
                                retiene state.db — el holder scan nos vería a
                                nosotros; SQLite WAL serializa los writers y
                                prune solo toca sesiones TERMINADAS). Estado
                                → attestation.
  P-28 require_checkpoints      piso pineado en el managed scope (overlay
                                admin-pinned ``checkpoints.enabled: true`` —
                                el overlay GANA al config del usuario, M16).
                                Si el directorio managed no existe (lo crea
                                el instalador/distribución) → finding, sin
                                efectos colaterales al host. Estado →
                                attestation.
  E-2  computer_use bounded     ``computer_use`` vetoada cuando
                                ``computer_use.permission_mode=unrestricted``
                                y ``approval_mode`` >= smart (rank
                                manual>smart>off): YOLO/unrestricted apaga los
                                prompts de Cua y la policy no lo permite.

  E-1  veto en carga diferida    el veto por nombre cubre (a) tools MCP
                                dinámicas y (b) cargas diferidas de
                                tool_search SOLO vía unwrap de los puentes
                                tool_call/tool_describe: el core despacha la
                                tool diferida con skip_pre_tool_call_hook=True
                                y el hook solo ve el puente (hallazgo live
                                v5 — sin unwrap, la tool no permitida
                                EJECUTA). El unwrap clampea cada entrada
                                contra allowed_tools (spec 022) y luego
                                aplica capability_veto. Cubre también (c)
                                children de delegate_task (in-process) y (d)
                                el override ``toolsets:`` por ruta webhook
                                (el override solo cambia qué tools se
                                ANUNCIAN; la ejecución de cualquiera pasa
                                por el mismo ejecutor con el mismo veto).
                                Tests + evidencia live en la sesión
                                enrolada.

Findings → attestation: desvíos y pisos se persisten en
``$HERMES_HOME/trust-agent/capability-findings.json`` y viajan en el POST de
attestation (campo ``capability_findings`` — canal aditivo coordinado con la
lane Inc. 3). Los vetos cuentan en el contrato existente
``veto-counts.json`` (misma semántica que ``enforcement._count_veto``).

Cero parches al core (REQ-GOH-016): SOLO ``ctx.register_*`` + lectura de
config.yaml del perfil (I/O local, sin imports de ``hermes_cli``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import enroll, policy_contract

# ---------------------------------------------------------------------------
# Contratos de archivo (D-HRB-03: cada módulo declara los suyos, sin acoplar
# imports de lanes hermanas — mismo patrón que attestation.py)
# ---------------------------------------------------------------------------

def _trust_dir() -> Path:
    return enroll.trust_dir()


def _findings_path() -> Path:
    return _trust_dir() / "capability-findings.json"


def _veto_counts_path() -> Path:
    # Mismo contrato que enforcement._veto_counts_path / attestation.py
    return _trust_dir() / "veto-counts.json"


def _last_prune_path() -> Path:
    return _trust_dir() / "last-session-prune.json"


def _log_event(event: str, **fields: Any) -> None:
    """Log local append-only (mismo formato que enforcement.log_event)."""
    try:
        entry = {"ts": int(time.time()), "pid": os.getpid(), "event": event, **fields}
        _trust_dir().mkdir(parents=True, exist_ok=True)
        with (_trust_dir() / "plugin.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # el log JAMÁS tumba el turno


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _count_veto(tool: str, reason: str) -> None:
    """Conteo por tool para la attestation (REQ-GOH-012/017).

    Mismo contrato de archivo que ``enforcement._count_veto``: la attestation
    fusiona ambas fuentes — los vetos de CAPACIDAD cuentan igual que los de
    allowed_tools.
    """
    _log_event("tool_vetoed", tool=tool, reason=reason, layer="capability")
    try:
        path = _veto_counts_path()
        counts: dict[str, int] = {}
        if path.exists():
            counts = json.loads(path.read_text(encoding="utf-8"))
        counts[tool] = int(counts.get(tool, 0)) + 1
        _atomic_write_json(path, counts)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Findings (desvíos + pisos) — canal capability-findings.json → attestation
# ---------------------------------------------------------------------------

def _add_finding(kind: str, code: str, detail: str | dict[str, Any]) -> None:
    """Acumula/actualiza un finding. ``kind``: "warnings" (acumula count) o
    "status" (última palabra — el piso/desvío vigente)."""
    try:
        path = _findings_path()
        data: dict[str, Any] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        bucket = data.setdefault(kind, {})
        if kind == "warnings":
            prev = bucket.get(code) or {}
            bucket[code] = {
                "count": int(prev.get("count", 0)) + 1,
                "detail": detail,
            }
        else:
            bucket[code] = detail if not isinstance(detail, dict) else detail
        _atomic_write_json(path, data)
    except (OSError, ValueError):
        pass


def collect_findings() -> dict[str, Any]:
    """Snapshot de findings para la attestation (sin consumir)."""
    try:
        data = json.loads(_findings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def consume_findings() -> None:
    """Tras una attestation EXITOSA los warnings parten de cero; el status
    se recalcula en la próxima evaluación (evaluate_findings lo reescribe)."""
    try:
        data = collect_findings()
        data["warnings"] = {}
        _atomic_write_json(_findings_path(), data)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Estado del módulo
# ---------------------------------------------------------------------------

# Tools internas del paquete: siempre permitidas (REQ-GOH-005).
INTERNAL_TOOLS = frozenset({"governance_status"})

_BATCH1_ACTIVE = False  # ¿la lane batch 1 (enforcement.py) está cableada?
_CTX: Any = None        # PluginContext (para has_plugin)
_PLUGIN_TOOLS: dict[str, str] = {}   # tool → plugin (scan de installs)
_VAULT_KINDS: dict[str, str] = {}    # handle → kind (cache de action=list)
_DEPTH = 0                          # profundidad de delegación efectiva
_DEPTH_LOCK = threading.Lock()
_CONFIG_CACHE: tuple[float, dict] = (0.0, {})  # (ts, config.yaml del perfil)

# Rank de approval_mode (matriz P-1): manual(2) > smart(1) > off(0).
_APPROVAL_RANKS = {"off": 0, "smart": 1, "manual": 2}

# Tools de MCP: mcp__<server>__<tool> (toolsets dinámicos, toolsets.py).
_MCP_NAME_RE = re.compile(r"^mcp__([A-Za-z0-9_-]+?)__")


def _norm_mcp_server(name: str) -> str:
    """P-6: el core SANITIZA el nombre del server MCP al armar el nombre de
    tool (config ``laptop-bridge`` → tool ``mcp__laptop_bridge__…``,
    verificado live v7): comparar server contra allowed_mcp_servers
    normalizando ``-``/``_`` en ambos lados."""
    return name.replace("_", "-")


def _mcp_server_allows(allowed: set[str], server: str) -> bool:
    if "*" in allowed:
        return True
    target = _norm_mcp_server(server)
    return any(_norm_mcp_server(str(a)) == target for a in allowed)

# E-1 (hallazgo live v5): puentes de tools diferidas
# (tools/tool_search_catalog.py::BRIDGE_TOOL_NAMES). El core despacha la
# tool diferida con skip_pre_tool_call_hook=True (agent/tool_executor.py)
# — el hook pre_tool_call SOLO ve el puente ('tool_call'/'tool_describe')
# con el nombre real embebido en args. Sin unwrap, una tool MCP/plugin
# diferida no permitida EJECUTARÍA de verdad: el veto por nombre del
# ejecutor no la alcanza. Se desenvuelve CADA entrada del batch y se
# evalúa contra allowed_tools (clamp de scope, spec 022) + capability_veto.
_BRIDGE_TOOLS = frozenset({"tool_call", "tool_describe"})


def _bridge_entries(tool: str, args: Any) -> list[tuple[str, dict[str, Any]]]:
    """Entradas ``(name, arguments)`` embebidas en una llamada al puente.

    ``tool_call`` acepta ``{"calls": [{"name", "arguments"}, ...]}`` (batch,
    forma documentada del schema) y —defensivamente— la forma singular
    ``{"name", "arguments"}``. ``tool_describe`` acepta ``{"name"}``.
    """
    if not isinstance(args, dict):
        return []
    if tool == "tool_call":
        calls = args.get("calls")
        if isinstance(calls, list):
            entries = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                inner_args = call.get("arguments")
                entries.append((
                    str(call.get("name") or "").strip(),
                    inner_args if isinstance(inner_args, dict) else {},
                ))
            return entries
        name = str(args.get("name") or "").strip()
        if name:
            inner_args = args.get("arguments")
            return [(name, inner_args if isinstance(inner_args, dict) else {})]
        return []
    name = str(args.get("name") or "").strip()
    return [(name, {})] if name else []

# Comandos de gateway gobernados por capability keys (P-14/P-21/P-10/P-11).
# /trust-enroll NUNCA se gobierna aquí: es la vía de re-enrolamiento.
_GOVERNED_COMMANDS: dict[str, tuple[str, str]] = {
    "/personality": ("allow_custom_personality", "custom personality changes"),
    "/voice": ("allow_voice_mode", "voice mode"),
    "/loop": ("allow_recurring_prompts", "recurring prompts"),
    "/heartbeat": ("allow_recurring_prompts", "recurring prompts"),
    "/goal": ("allow_goal_mode", "goal mode"),
    "/subgoal": ("allow_goal_mode", "goal mode"),
}

# Plugins de memory provider conocidos del catálogo (C-047) → prefijos de sus
# tools (el memory manager las sirve fuera del registry: tool_executor.py).
_MEMORY_PROVIDER_PREFIXES: dict[str, tuple[str, ...]] = {
    "honcho": ("honcho_",),
    "hindsight": ("hindsight_",),
    "mem0": ("mem0_",),
    "openviking": ("openviking_",),
    "supermemory": ("supermemory_",),
    "retaindb": ("retaindb_",),
    "byterover": ("byterover_",),
}

# Entry points de distribución de perfiles / import de otros agentes (P-25).
_PROFILE_DISTRIBUTION_RE = re.compile(
    r"\bhermes\s+(?:profile\s+install|import-agent)\b"
)

# Patrones de register_tool en fuente de plugins (P-7): forma keyword y
# posicional del API pública (hermes_cli/plugins.py::register_tool).
_PLUGIN_TOOL_PATTERNS = (
    re.compile(r"register_tool\s*\(\s*name\s*=\s*[\"']([A-Za-z0-9_]+)[\"']"),
    re.compile(r"register_tool\s*\(\s*[\"']([A-Za-z0-9_]+)[\"']\s*,"),
)


def _block(detail: str) -> dict[str, str]:
    """Directiva de veto con el mensaje normativo de la spec 019."""
    return {"action": "block", "message": f"BLOCKED by organization policy: {detail}"}


def _policy(payload: dict[str, Any], key: str) -> Any:
    """Key de policy con deny-by-default (REQ-GOH-023) — NUNCA payload.get."""
    return policy_contract.policy_value(payload, key)


def _allowed_set(payload: dict[str, Any], key: str) -> set[str]:
    value = _policy(payload, key)
    if isinstance(value, (list, tuple, set)):
        return {str(x) for x in value}
    return set()


def _list_allows(values: set[str], item: str) -> bool:
    return "*" in values or item in values


def _bool(payload: dict[str, Any], key: str) -> bool:
    return bool(_policy(payload, key))


# ---------------------------------------------------------------------------
# Bundle vigente — REUSA la máquina de estados de la lane batch 1 (sin editarla)
# ---------------------------------------------------------------------------

def _current_payload() -> tuple[str, dict[str, Any] | None]:
    """``(mode, payload)`` del bundle vigente.

    Orden: ``enforcement.STATE.evaluate()`` (lane batch 1 — verifica caché y
    sincroniza con backoff) → núcleo v2 (``state.get_state``) → restricted.
    Ante cualquier fallo: restricted sin bundle (fail-closed).
    """
    try:
        from . import enforcement

        snap = enforcement.STATE.evaluate()
        return str(snap.get("mode") or "restricted"), snap.get("payload")
    except Exception:  # noqa: BLE001 - lane ausente o en refactor → núcleo v2
        try:
            from . import state as _core

            snap = _core.get_state().snapshot()
            bundle = snap.get("bundle")
            if bundle:
                return "normal", bundle
        except Exception:  # noqa: BLE001
            pass
    return "restricted", None


# ---------------------------------------------------------------------------
# Config del perfil (lectura local — sin imports de hermes_cli)
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    return enroll.hermes_home() / "config.yaml"


def _cached_config(*, max_age: float = 60.0) -> dict[str, Any]:
    """config.yaml del perfil con caché de 60 s (se lee por tool call)."""
    global _CONFIG_CACHE
    ts, cached = _CONFIG_CACHE
    now = time.monotonic()
    if now - ts > max_age:
        try:
            import yaml

            cached = yaml.safe_load(_config_path().read_text(encoding="utf-8"))
            cached = cached if isinstance(cached, dict) else {}
        except (OSError, ValueError):
            cached = {}
        _CONFIG_CACHE = (now, cached)
    return cached


# Default POSIX del managed scope (hermes_cli/managed_scope.py::get_managed_dir).
# Constante para que los tests la monkeypatcheen sin tocar el host real.
_DEFAULT_MANAGED_DIR = Path("/etc/hermes")


def _managed_dir_for_writing() -> Path | None:
    """Directorio managed scope donde pinear pisos (M16).

    ``$HERMES_MANAGED_DIR`` explícito → se usa (creándolo). Si no,
    ``/etc/hermes`` SOLO cuando ya existe (lo crea el instalador/distribución
    de la org — el plugin NO crea managed scope host-wide por su cuenta: sin
    él reporta el desvío, no muta el host).
    """
    env = os.environ.get("HERMES_MANAGED_DIR", "").strip()
    if env:
        path = Path(env)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            return None
    return _DEFAULT_MANAGED_DIR if _DEFAULT_MANAGED_DIR.is_dir() else None


def _deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------
# P-7: scan de plugins instalados → mapa tool→plugin
# ---------------------------------------------------------------------------

def _scan_plugin_tools() -> None:
    """Best-effort: tools registradas por plugins instalados en
    ``$HERMES_HOME/plugins/*/`` (user-installed — M14). Las tools con nombre
    dinámico no se atrapan aquí, pero ya caen en el deny-by-default de
    ``allowed_tools`` (batch 1): este mapa cubre el caso inverso — una tool
    allowlisted en ``allowed_tools`` que venga de un plugin no allowlisted.
    """
    global _PLUGIN_TOOLS
    mapping: dict[str, str] = {}
    plugins_dir = enroll.hermes_home() / "plugins"
    try:
        entries = sorted(plugins_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_dir():
            continue
        manifest = entry / "plugin.yaml"
        if not manifest.exists():
            continue
        name = entry.name
        try:
            import yaml

            data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name"):
                name = str(data["name"])
        except (OSError, ValueError):
            pass
        for py in entry.glob("*.py"):
            try:
                source = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern in _PLUGIN_TOOL_PATTERNS:
                for match in pattern.findall(source):
                    mapping[str(match)] = name
    _PLUGIN_TOOLS = mapping
    if mapping:
        _log_event("plugin_tool_scan", tools=len(mapping))


def _installed_plugin_names() -> list[str]:
    names = set(_PLUGIN_TOOLS.values())
    plugins_dir = enroll.hermes_home() / "plugins"
    try:
        names.update(
            e.name for e in plugins_dir.iterdir()
            if e.is_dir() and (e / "plugin.yaml").exists()
        )
    except OSError:
        pass
    return sorted(names)


# ---------------------------------------------------------------------------
# P-13: provider de memoria activo
# ---------------------------------------------------------------------------

def _active_memory_provider() -> str | None:
    """Provider externo activo: config ``memory.provider`` o plugin de
    provider cargado (``ctx.has_plugin`` — probe público del PluginContext)."""
    provider = str(_cached_config().get("memory", {}).get("provider") or "").strip()
    if provider and provider != "builtin":
        return provider
    if _CTX is not None:
        for plugin in _MEMORY_PROVIDER_PREFIXES:
            try:
                if _CTX.has_plugin(plugin):
                    return plugin
            except Exception:  # noqa: BLE001 - probe best-effort
                continue
    return None


# ---------------------------------------------------------------------------
# Veto por capacidad (pre_tool_call) — puro sobre (tool, args, payload)
# ---------------------------------------------------------------------------

def _skill_allowed(allowed: set[str], name: str) -> bool:
    if _list_allows(allowed, "*"):
        return True
    if name in allowed:
        return True
    # "categoria/skill" y "plugin:skill" → se gobierna por el id de la skill.
    bare = name.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return bare in allowed


def _skills_veto(tool: str, args: dict[str, Any], payload: dict[str, Any]) -> dict | None:
    allowed = _allowed_set(payload, "allowed_skills")
    catalog_fixed = not _list_allows(allowed, "*")
    name = str(args.get("name") or "").strip()
    if tool == "skills_list" and catalog_fixed:
        return _block(
            "the skill catalog is fixed by organization policy (allowed_skills); "
            f"listing is disabled. Allowed skills: {', '.join(sorted(allowed)) or 'none'}."
        )
    if tool == "skill_manage":
        # Piso sobre skills.write_approval local (E-3c): con el catálogo
        # fijado, NINGUNA escritura de skills procede, apruebe o no el gate
        # local; sin fijar, la skill objetivo debe estar en la lista.
        if catalog_fixed:
            return _block(
                "skill writes are not allowed: the organization fixes the skill "
                "catalog (allowed_skills)."
            )
        if name and not _skill_allowed(allowed, name):
            return _block(f"skill '{name}' is not in allowed_skills.")
        return None
    if tool == "skill_view" and catalog_fixed and name and not _skill_allowed(allowed, name):
        return _block(f"skill '{name}' is not in allowed_skills.")
    return None


def _memory_veto(tool: str, payload: dict[str, Any]) -> dict | None:
    allowed = _allowed_set(payload, "allowed_memory_providers")
    provider = _active_memory_provider()
    if provider and not _list_allows(allowed, provider):
        if tool == "memory":
            return _block(
                f"the memory tool is disabled: external memory provider "
                f"'{provider}' is not in allowed_memory_providers."
            )
    for name, prefixes in _MEMORY_PROVIDER_PREFIXES.items():
        if tool.startswith(prefixes) and not _list_allows(allowed, name):
            return _block(
                f"tool '{tool}' belongs to memory provider '{name}', which is "
                "not in allowed_memory_providers."
            )
    return None


def _vault_veto(args: dict[str, Any], payload: dict[str, Any]) -> dict | None:
    if not _bool(payload, "allow_credential_vault"):
        return _block(
            "the browser credential vault is not allowed (allow_credential_vault=false)."
        )
    action = str(args.get("action") or "").strip()
    if action == "unlock":
        backend = str(args.get("backend") or "").strip()
        if backend and not _list_allows(_allowed_set(payload, "allowed_secret_sources"), backend):
            return _block(
                f"secret source '{backend}' is not in allowed_secret_sources."
            )
    if action == "fill" and not _bool(payload, "allow_payment_autofill"):
        handle = str(args.get("handle") or "").strip()
        kind = _VAULT_KINDS.get(handle)
        if kind is None:
            return _block(
                "payment autofill is not allowed (allow_payment_autofill=false); "
                f"item '{handle}' has unknown kind (call browser_vault "
                "action=list first) — refused fail-closed."
            )
        if kind != "login":
            return _block(
                f"payment autofill is not allowed (allow_payment_autofill=false); "
                f"refused fill of item kind '{kind}'."
            )
    return None


def _computer_use_veto(payload: dict[str, Any]) -> dict | None:
    """E-2: con approval_mode >= smart, permission_mode=unrestricted se veta."""
    mode = str(_policy(payload, "approval_mode") or "manual").lower()
    if _APPROVAL_RANKS.get(mode, 2) < 1:
        return None  # approval_mode=off: el piso bounded no aplica (P-1 batch 1)
    cfg = _cached_config().get("computer_use") or {}
    if str(cfg.get("permission_mode") or "").strip().lower() == "unrestricted":
        return _block(
            "computer_use is configured with permission_mode=unrestricted, "
            "which is not allowed while approval_mode >= smart (capability "
            "matrix E-2). Ask the organization to set computer_use."
            "permission_mode=bounded."
        )
    return None


def _current_depth() -> int:
    with _DEPTH_LOCK:
        return _DEPTH


def capability_veto(tool: str, args: dict[str, Any], payload: dict[str, Any]) -> dict | None:
    """Evalúa UNA llamada de tool contra las keys de CAPACIDAD del bundle.

    Puro salvo las cachés documentadas del módulo (config 60 s, kinds de
    vault, profundidad de delegación). Retorna la directiva de veto o None.
    """
    # P-6: tools de MCP (nombre real — cubre carga diferida y children, E-1).
    match = _MCP_NAME_RE.match(tool)
    if match:
        server = match.group(1)
        if not _mcp_server_allows(
                _allowed_set(payload, "allowed_mcp_servers"), server):
            return _block(
                f"tool '{tool}' belongs to MCP server '{server}', which is not "
                "in allowed_mcp_servers."
            )
    # P-7: tools registradas por plugins no allowlisted.
    plugin = _PLUGIN_TOOLS.get(tool)
    if plugin and not _list_allows(_allowed_set(payload, "allowed_plugins"), plugin):
        return _block(
            f"tool '{tool}' is provided by plugin '{plugin}', which is not in "
            "allowed_plugins."
        )
    # P-8: skills.
    if tool in ("skill_view", "skills_list", "skill_manage"):
        veto = _skills_veto(tool, args, payload)
        if veto:
            return veto
    # P-13: memory providers externos.
    veto = _memory_veto(tool, payload)
    if veto:
        return veto
    # P-16/P-17/P-15: vault de credenciales del browser.
    if tool == "browser_vault":
        veto = _vault_veto(args, payload)
        if veto:
            return veto
    # E-2: computer_use bounded.
    if tool == "computer_use":
        veto = _computer_use_veto(payload)
        if veto:
            return veto
    # P-9: tareas programadas.
    if tool == "cronjob_manage" and not _bool(payload, "allow_scheduled_tasks"):
        return _block(
            "scheduled tasks (cronjob_manage) are not allowed "
            "(allow_scheduled_tasks=false)."
        )
    # P-22: bot mode.
    if tool == "message_agent" and not _bool(payload, "allow_bot_mode"):
        return _block("bot mode (message_agent) is not allowed (allow_bot_mode=false).")
    # P-12: profundidad de delegación efectiva.
    if tool == "delegate_task":
        try:
            max_depth = int(_policy(payload, "max_delegation_depth") or 0)
        except (TypeError, ValueError):
            max_depth = 0
        if _current_depth() >= max_depth:
            return _block(
                f"delegation is not allowed at effective depth "
                f"{_current_depth()} (max_delegation_depth={max_depth})."
            )
    # P-11: goal mode vía kanban_create(goal_mode=true).
    if tool == "kanban_create" and args.get("goal_mode") and not _bool(payload, "allow_goal_mode"):
        return _block("goal mode is not allowed (allow_goal_mode=false).")
    # P-25: distribución de perfiles / import de otros agentes vía terminal.
    if tool == "terminal" and not _bool(payload, "allow_profile_distributions"):
        command = str(args.get("command") or "")
        if _PROFILE_DISTRIBUTION_RE.search(command):
            return _block(
                "profile distributions and agent imports are not allowed "
                "(allow_profile_distributions=false)."
            )
    return None


# ---------------------------------------------------------------------------
# Hooks (API pública de plugins — ctx.register_hook)
# ---------------------------------------------------------------------------

def on_pre_tool_call(tool_name: str = "", args: dict | None = None, **_: Any):
    """Veto de capacidades (batch 2). Complementa —no duplica— al veto de
    allowed_tools/restricted de la lane batch 1: en modo restringido esta
    lane se retira (la batch 1 bloquea todo) SALVO si la batch 1 no está
    cableada ( entonces este hook fail-closes por sí mismo)."""
    tool = (tool_name or "").strip()
    if not tool or tool in INTERNAL_TOOLS:
        return None
    mode, payload = _current_payload()
    if payload is None:
        if _BATCH1_ACTIVE:
            return None  # la lane batch 1 ya bloquea toda tool externa
        _count_veto(tool, "restricted mode (no valid governance bundle)")
        return _block(
            "restricted mode (no valid governance bundle): no external tools "
            "are available until a valid governance bundle is synced."
        )
    veto = capability_veto(tool, args if isinstance(args, dict) else {}, payload)
    if veto:
        _count_veto(tool, veto["message"])
        _log_event("capability_veto", tool=tool, message=veto["message"])
        return veto
    # E-1(a)/(b): puentes de tools diferidas. La tool interna despacha con
    # skip_pre_tool_call_hook=True — este hook es el ÚNICO punto de veto y
    # solo ve el puente. Desenvolver cada entrada y evaluarla contra
    # allowed_tools (clamp de scope, spec 022) y capability_veto (P-6/P-7/
    # P-8/...). Hallazgo live v5: sin esto, mcp__laptop-bridge__laptop_status
    # (servidor NO allowlisted) habría ejecutado de verdad vía tool_call.
    if tool in _BRIDGE_TOOLS:
        for inner_name, inner_args in _bridge_entries(tool, args):
            if not inner_name or inner_name in INTERNAL_TOOLS:
                continue
            # Clamp de scope (spec 022) con la misma semántica de "*" que
            # el veto de allowed_tools de la lane batch 1.
            allowed = {str(x) for x in (payload.get("allowed_tools") or [])}
            if allowed and not _list_allows(allowed, inner_name):
                message = (
                    f"tool '{inner_name}' is not allowed (deferred invocation "
                    f"via {tool}). Allowed tools: {', '.join(sorted(allowed)) or 'none'}."
                )
                _count_veto(inner_name, f"not in allowed_tools (via {tool})")
                _log_event("capability_veto", tool=inner_name, message=message)
                return _block(message)
            inner_veto = capability_veto(inner_name, inner_args, payload)
            if inner_veto:
                _count_veto(inner_name, inner_veto["message"])
                _log_event(
                    "capability_veto", tool=inner_name,
                    message=inner_veto["message"])
                return inner_veto
    return None


def on_post_tool_call(tool_name: str = "", args: dict | None = None, result: Any = None, **_: Any):
    """Cache handle→kind de ``browser_vault action=list`` (P-17)."""
    if tool_name != "browser_vault":
        return
    if str((args or {}).get("action") or "") != "list":
        return
    try:
        data = json.loads(result) if isinstance(result, str) else result
        for item in (data or {}).get("items") or []:
            handle, kind = item.get("handle"), item.get("kind")
            if handle and kind:
                _VAULT_KINDS[str(handle)] = str(kind)
    except (ValueError, TypeError, AttributeError):
        pass  # observador: jamás rompe la tool


def on_subagent_start(**_: Any) -> None:
    """P-12: los children corren in-process (E-1c) — el contador de
    profundidad vive en el proceso y ve toda la cadena de delegación."""
    global _DEPTH
    with _DEPTH_LOCK:
        _DEPTH += 1
    _log_event("delegation_depth", depth=_current_depth())


def on_subagent_stop(**_: Any) -> None:
    global _DEPTH
    with _DEPTH_LOCK:
        _DEPTH = max(0, _DEPTH - 1)


def on_pre_gateway_dispatch(event: Any = None, **_: Any):
    """P-14/P-21/P-10/P-11: los comandos de gateway gobernados se REESCRIBEN
    (``{"action": "rewrite", "text": …}`` — API de pre_gateway_dispatch): el
    comando NUNCA ejecuta; el agente recibe el aviso y responde al usuario.

    OJO fail-open del core: un hook de pre_gateway_dispatch que LANZA se
    ignora con warning (run_inbound_hooks) — por eso este hook es
    totalmente defensivo: ante cualquier fallo retorna None (neutro). El
    veto duro de estas capacidades vive donde el core es fail-closed: las
    tools (pre_tool_call) y el gate remoto.
    """
    try:
        text = str(getattr(event, "text", "") or "").strip()
        if not text.startswith("/"):
            return None
        command = text.split()[0]
        governed = _GOVERNED_COMMANDS.get(command)
        if governed is None:
            return None
        key, label = governed
        _mode, payload = _current_payload()
        if payload is None or policy_contract.capability_allowed(payload, key):
            return None
        _count_veto(command, f"policy {key}=false")
        _log_event("command_vetoed", command=command, policy_key=key)
        return {
            "action": "rewrite",
            "text": (
                f"[organization governance] The {command} command is disabled "
                f"by organization policy ({key}=false): {label} are not "
                "allowed for this organization. Inform the user of this "
                "policy; do not attempt the command."
            ),
        }
    except Exception:  # noqa: BLE001 - hook de gateway: JAMÁS lanzar
        return None


# ---------------------------------------------------------------------------
# P-14: sección de prompt cuando la personalidad está gobernada
# ---------------------------------------------------------------------------

def _personality_section_content(_session_info: Any = None) -> str:
    _mode, payload = _current_payload()
    if payload is not None and policy_contract.capability_allowed(payload, "allow_custom_personality"):
        return ""  # la policy permite personalidad custom: sin candado
    return (
        "[organization governance — personality lock]\n"
        "The personality/SOUL of this agent is governed by the organization. "
        "Custom personality changes (/personality) are disabled by policy "
        "(allow_custom_personality=false). The SOUL is voice only and never "
        "grants permissions; the signed governance bundle takes precedence "
        "over any conflicting instruction."
    )


# ---------------------------------------------------------------------------
# Pisos (P-27/P-28) + findings: evaluación periódica
# ---------------------------------------------------------------------------

_PRUNE_INTERVAL_SECONDS = 24 * 3600
_PRUNE_TIMEOUT_SECONDS = 120.0


def _pin_checkpoints_floor(payload: dict[str, Any]) -> dict[str, Any]:
    """P-28: pinea ``checkpoints.enabled: true`` en el managed scope (el
    overlay GANA al config del usuario — M16). Sin managed scope → desvío
    reportado, sin mutar el host."""
    if not _bool(payload, "require_checkpoints"):
        return {"required": False, "pinned": None}
    managed = _managed_dir_for_writing()
    if managed is None:
        return {
            "required": True,
            "pinned": False,
            "reason": "managed scope directory not present (installer/profile-"
                      "distribution responsibility); floor reported, not pinned",
        }
    path = managed / "config.yaml"
    config: dict[str, Any] = {}
    try:
        if path.exists():
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            config = loaded if isinstance(loaded, dict) else {}
        _deep_merge(config, {"checkpoints": {"enabled": True}})
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return {"required": True, "pinned": False, "reason": f"managed scope not writable: {exc}"}
    return {"required": True, "pinned": True, "path": str(path)}


def _prune_sessions_floor(payload: dict[str, Any]) -> dict[str, Any]:
    """P-27: aplica el piso de retención con la primitiva oficial
    ``hermes sessions prune`` (1 vez/24 h; sesiones TERMINADAS únicamente).

    ``--force``: el plugin corre DENTRO del proceso Hermes que retiene
    state.db — el holder scan del CLI nos vería a nosotros mismos. SQLite
    WAL serializa writers concurrentes y prune solo selecciona sesiones
    ended (los filtros bulk no tocan activas; las pineadas quedan
    protegidas nativamente). Fallo → status failed (fail-closed en el
    REPORTE, jamás en el turno)."""
    try:
        days = max(1, int(_policy(payload, "session_retention_days") or 1))
    except (TypeError, ValueError):
        days = 1
    state: dict[str, Any] = {}
    try:
        state = json.loads(_last_prune_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    fresh = (
        isinstance(state.get("ts"), (int, float))
        and time.time() - float(state["ts"]) < _PRUNE_INTERVAL_SECONDS
        and int(state.get("days") or 0) == days
    )
    if fresh:
        return {k: v for k, v in state.items() if k != "ts"}
    binary = shutil.which("hermes")
    if binary is None:
        result = {"days": days, "status": "failed", "detail": "hermes CLI not found on PATH"}
        _add_finding("status", "retention_floor", result)
        return result
    try:
        proc = subprocess.run(  # noqa: S603 - binario fijo, args fijos
            [binary, "sessions", "prune", "--older-than", f"{days}d", "--yes", "--force"],
            capture_output=True,
            text=True,
            timeout=_PRUNE_TIMEOUT_SECONDS,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        match = re.search(r"[Pp]runed\s+(\d+)", output)
        if proc.returncode == 0:
            result = {
                "days": days,
                "status": "ok",
                "pruned": int(match.group(1)) if match else None,
            }
        else:
            result = {
                "days": days,
                "status": "failed",
                "detail": output.strip()[-300:] or f"exit {proc.returncode}",
            }
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {"days": days, "status": "failed", "detail": str(exc)[:300]}
    try:
        _atomic_write_json(_last_prune_path(), {"ts": time.time(), **result})
    except OSError:
        pass
    _add_finding("status", "retention_floor", result)
    _log_event("retention_prune", **result)
    return result


def evaluate_findings() -> dict[str, Any]:
    """Evalúa desvíos config-vs-policy + pisos, los persiste y retorna el
    snapshot (canal → attestation). Corre en hilo daemon (register/arranque
    y on_session_start): nada de esto puede bloquear un turno."""
    mode, payload = _current_payload()
    if payload is None:
        snapshot = {"status": {"mode": mode or "restricted"}, "warnings": {}}
        try:
            _atomic_write_json(_findings_path(), snapshot)
        except OSError:
            pass
        return snapshot
    config = _cached_config(max_age=0.0)  # lectura fresca para la evaluación

    # P-13: provider de memoria externo no allowlisted.
    provider = _active_memory_provider()
    if provider and not _list_allows(_allowed_set(payload, "allowed_memory_providers"), provider):
        _add_finding(
            "warnings",
            "memory_provider_not_allowed",
            f"external memory provider '{provider}' is active but not in "
            "allowed_memory_providers; the memory tool is vetoed",
        )
    # P-15: secret sources externos habilitados en config no allowlisted
    # (la resolución ${secret:…} es de proceso — no vetable por tool, E-3).
    allowed_sources = _allowed_set(payload, "allowed_secret_sources")
    secrets = config.get("secrets") or {}
    for source in ("bitwarden", "onepassword", "command"):
        if (secrets.get(source) or {}).get("enabled") and not _list_allows(allowed_sources, source):
            _add_finding(
                "warnings",
                "secret_source_not_allowed",
                f"secret source '{source}' is enabled in config but not in "
                "allowed_secret_sources",
            )
    # P-23: gateway hooks trusted-by-placement (E-3a — NO vetable).
    hooks_dir = enroll.hermes_home() / "hooks"
    if not _bool(payload, "allow_gateway_hooks"):
        try:
            entries = [e.name for e in hooks_dir.iterdir() if e.name != "__pycache__"]
        except OSError:
            entries = []
        if entries:
            _add_finding(
                "warnings",
                "gateway_hooks_present",
                f"~/.hermes/hooks/ contains {entries} and allow_gateway_hooks=false "
                "(residual risk E-3a: trusted-by-placement, not vetable by plugin)",
            )
    # P-24: shell hooks en config (aprobación first-use nativa; no vetable).
    if config.get("hooks") and not _bool(payload, "allow_shell_hooks"):
        _add_finding(
            "warnings",
            "shell_hooks_present",
            "config.yaml declares shell hooks and allow_shell_hooks=false",
        )
    # P-9: cron.allow_agent_scheduling por encima de la policy.
    if (config.get("cron") or {}).get("allow_agent_scheduling") and not _bool(payload, "allow_scheduled_tasks"):
        _add_finding(
            "warnings",
            "cron_scheduling_config_deviation",
            "cron.allow_agent_scheduling=true in config but policy "
            "allow_scheduled_tasks=false; cronjob_manage stays vetoed",
        )
    # P-12: delegation.max_spawn_depth por encima de la policy.
    try:
        cfg_depth = int((config.get("delegation") or {}).get("max_spawn_depth") or 0)
    except (TypeError, ValueError):
        cfg_depth = 0
    try:
        pol_depth = int(_policy(payload, "max_delegation_depth") or 0)
    except (TypeError, ValueError):
        pol_depth = 0
    if cfg_depth > pol_depth:
        _add_finding(
            "warnings",
            "delegation_depth_config_deviation",
            f"delegation.max_spawn_depth={cfg_depth} in config exceeds policy "
            f"max_delegation_depth={pol_depth}; the policy floor is enforced "
            "at every delegate_task call",
        )
    # E-2: computer_use unrestricted con approval_mode >= smart.
    if str((config.get("computer_use") or {}).get("permission_mode") or "").strip().lower() == "unrestricted":
        approval = str(_policy(payload, "approval_mode") or "manual").lower()
        if _APPROVAL_RANKS.get(approval, 2) >= 1:
            _add_finding(
                "warnings",
                "computer_use_unrestricted",
                "computer_use.permission_mode=unrestricted with approval_mode>="
                f"{approval}; computer_use is vetoed until bounded (E-2)",
            )
    # P-14: personalities configuradas con la personalidad gobernada.
    if (config.get("agent") or {}).get("personalities") and not _bool(payload, "allow_custom_personality"):
        _add_finding(
            "warnings",
            "personality_config_deviation",
            "agent.personalities is set in config but allow_custom_personality=false",
        )
    # P-21: voice habilitado en config con voice mode vetado.
    if (config.get("gateway") or {}).get("voice", {}).get("enabled_voice_only") and not _bool(payload, "allow_voice_mode"):
        _add_finding(
            "warnings",
            "voice_config_deviation",
            "gateway.voice.enabled_voice_only=true in config but "
            "allow_voice_mode=false",
        )
    # P-22: bot mode protocol en config con bot mode vetado.
    if (config.get("agent") or {}).get("bot_mode_protocol") and not _bool(payload, "allow_bot_mode"):
        _add_finding(
            "warnings",
            "bot_mode_config_deviation",
            "agent.bot_mode_protocol is set in config but allow_bot_mode=false",
        )
    # P-7: plugins instalados/cargados no allowlisted.
    allowed_plugins = _allowed_set(payload, "allowed_plugins")
    not_allowed = [
        name for name in _installed_plugin_names()
        if not _list_allows(allowed_plugins, name)
    ]
    if not_allowed:
        _add_finding(
            "warnings",
            "plugins_not_allowed",
            f"installed plugins not in allowed_plugins: {not_allowed}",
        )
    # P-28 + P-27: pisos (status — última palabra).
    _add_finding("status", "checkpoints_floor", _pin_checkpoints_floor(payload))
    _add_finding("status", "retention_floor", _prune_sessions_floor(payload))
    snapshot = collect_findings()
    _log_event("capability_findings", warnings=snapshot.get("warnings") or {})
    return snapshot


def _startup_floors() -> None:
    """Arranque: margen para el primer sync del bundle (como attestation)."""
    time.sleep(1.5)
    try:
        evaluate_findings()
    except Exception as exc:  # noqa: BLE001 - el piso JAMÁS tumba el arranque
        _log_event("capability_findings_error", detail=str(exc))


# ---------------------------------------------------------------------------
# register(ctx) — cableado aditivo desde __init__.py (API pública de plugins)
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Registra el enforcement batch 2 (capability-level).

    Registro RÁPIDO primero (hallazgo 3 del Incremento 1: el ensamblado de
    sesión no espera un register lento): hooks + sección; los pisos y la
    evaluación de findings corren en hilo daemon.
    """
    global _CTX, _BATCH1_ACTIVE
    _CTX = ctx
    try:
        from . import enforcement  # noqa: F401 - probe de la lane batch 1

        _BATCH1_ACTIVE = True
    except ImportError:
        _BATCH1_ACTIVE = False
    _scan_plugin_tools()

    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("pre_gateway_dispatch", on_pre_gateway_dispatch)

    def _on_session_start(**_: Any) -> None:
        threading.Thread(
            target=evaluate_findings,
            name="trust-capability-findings",
            daemon=True,
        ).start()

    ctx.register_hook("on_session_start", _on_session_start)

    # P-14: candado de personalidad en el prompt persistido (after_memory,
    # debajo de la sección de gobernanza del núcleo v2). Deny-by-default: sin
    # bundle aún, allow_custom_personality ausente = false → se registra.
    _mode, payload = _current_payload()
    if payload is None or not policy_contract.capability_allowed(
        payload, "allow_custom_personality"
    ):
        ctx.register_system_prompt_section(
            id="trust-agent-personality-lock",
            content=_personality_section_content,
            position="after_memory",
            max_chars=2000,
        )

    threading.Thread(
        target=_startup_floors,
        name="trust-capability-floors",
        daemon=True,
    ).start()

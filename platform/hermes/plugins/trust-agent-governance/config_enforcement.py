"""config_enforcement.py — batch 1 (spec 030, t_7e2852f8): enforcement de las
keys de CONFIGURACIÓN del agente sobre el bundle firmado.

Matriz de capacidades §3 (docs/hermes-capability-matrix.md), keys cubiertas:

  P-1  approval_mode              vetar approvals.mode:off / --yolo / /yolo /
                                  HERMES_YOLO_MODE=1 cuando la policy exige
                                  smart/manual (rank manual>smart>off).
  P-2  allowed_terminal_backends  terminal.backend fuera de lista → veto de
                                  terminal/process_manage.
  P-5  require_write_sandbox      write_file/patch fuera del workspace → veto.
  P-18 allowed_env_passthrough    veto de terminal/execute_code con vars no
                                  allowlisted (asignaciones del comando +
                                  passthrough configurado).
  P-19 web_domain_policy          allowlist/blocklist de dominios para tools
                                  con URL + sync administrado de
                                  security.website_blocklist (managed scope).
  P-20 allow_private_network_access  veto de URLs a redes privadas + sync
                                  administrado de security.allow_private_urls.
  P-26 allowed_providers          pre_llm_call: mensaje de policy cuando el
                                  provider efectivo cae fuera de la lista
                                  (el gate duro es LiteLLM — D-HRB-08).
  P-29 allow_self_updates         veto de hermes update / installs de plugin
                                  catalog + sync de security.allow_lazy_installs.

Cero parches al core (REQ-GOH-016): solo ``ctx.register_hook``; la config
local se LEE de ``$HERMES_HOME/config.yaml`` y las únicas escrituras fuera
del plugin son los syncs administrados de ``security.*`` (vehículo managed
scope, ADR-016 — SOLO imponen restricciones, jamás las resetan). Las keys se
resuelven EXCLUSIVAMENTE vía :func:`policy_contract.policy_value`
(deny-by-default por key ausente, REQ-GOH-023).

El modo operativo lo evalúa ``enforcement.STATE`` (spec 022 / spec 019 §5.4):
en modo restringido este módulo no añade nada — el fail-closed de spec 019
ya bloquea toda tool externa. Los vetos de este batch se auditan igual
(``plugin.log`` + ``veto-counts.json`` → próxima attestation, REQ-GOH-012/017).
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from . import enroll, enforcement, policy_contract
from .enforcement import INTERNAL_TOOLS, log_event

# PyYAML is not part of the minimal base interpreter used by some desktop
# installs; the governed plugin carries a private pure-Python copy so the
# managed-config pin never depends on the host's site-packages.
import sys as _sys
_VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in _sys.path:
    _sys.path.insert(0, str(_VENDOR_DIR))

# ---------------------------------------------------------------------------
# Superficies de tools (matriz C-003/C-004/C-011/C-001/C-005/C-045)
# ---------------------------------------------------------------------------
# Tools cuyo bypass de aprobaciones es el riesgo real de YOLO (D-030-01).
EXEC_TOOLS = frozenset({"terminal", "process_manage", "execute_code", "computer_use"})
# Tools atadas al backend de terminal (P-2).
TERMINAL_TOOLS = frozenset({"terminal", "process_manage"})
# Tools de escritura de archivos (P-5).
WRITE_TOOLS = frozenset({"write_file", "patch"})
# Tools que EJECUTAN comandos/código (P-18/P-29 y escaneo de URLs en comandos).
COMMAND_TOOLS = frozenset({"terminal", "process_manage", "execute_code"})
# Tools cuyos args SON acceso web por definición (P-19/P-20). Las browser_*
# se matchean por prefijo (el toolset browser registra varias).
WEB_TOOLS = frozenset({"web_extract", "web_search", "vision_analyze"})

# Mensaje de veto: SIEMPRE con el prefijo normativo de REQ-GOH-005.
_BLOCK_PREFIX = "BLOCKED by organization policy:"

# ---------------------------------------------------------------------------
# Ranks y defaults locales (espejo del combinator, spec 029 — solo lectura)
# ---------------------------------------------------------------------------
_APPROVAL_RANKS = {"off": 0, "smart": 1, "manual": 2}
_DEFAULT_APPROVAL_MODE = "smart"  # default del catálogo (matrix P-1)
_DEFAULT_TERMINAL_BACKEND = "local"  # default de Hermes (terminal.backend)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Config local del perfil ($HERMES_HOME/config.yaml) — lectura con caché mtime
# ---------------------------------------------------------------------------
_CONFIG_CACHE: dict[str, Any] = {"mtime": -1.0, "path": "", "data": {}}


def hermes_config() -> dict[str, Any]:
    """Config YAML del perfil activo (lectura; sin excepciones jamás)."""
    path = enroll.hermes_home() / "config.yaml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _CONFIG_CACHE["path"] == str(path) and _CONFIG_CACHE["mtime"] == mtime:
        return _CONFIG_CACHE["data"]  # type: ignore[return-value]
    try:
        import yaml  # disponible en el venv de Hermes (core lo usa para config)

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return {}
    data = data if isinstance(data, dict) else {}
    _CONFIG_CACHE.update(mtime=mtime, path=str(path), data=data)
    return data


def _config_section(name: str) -> dict[str, Any]:
    section = hermes_config().get(name)
    return section if isinstance(section, dict) else {}


# ---------------------------------------------------------------------------
# Veto: auditoría local (plugin.log + veto-counts.json → attestation)
# ---------------------------------------------------------------------------
def _bump_veto(tool: str, reason: str) -> None:
    """Cuenta el veto para la próxima attestation (mismo esquema que
    ``enforcement._count_veto``: ``veto-counts.json`` {tool: count})."""
    log_event("tool_vetoed", tool=tool, reason=reason, layer="config_batch1")
    try:
        path = enroll.trust_dir() / "veto-counts.json"
        counts: dict[str, int] = {}
        if path.exists():
            counts = json.loads(path.read_text(encoding="utf-8"))
        counts[tool] = int(counts.get(tool, 0)) + 1
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(counts, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError):
        pass  # la auditoría jamás tumba el turno


def _block(tool: str, reason: str, message: str) -> dict[str, str]:
    _bump_veto(tool, reason)
    return {"action": "block", "message": message}


# ---------------------------------------------------------------------------
# P-1 approval_mode — modo efectivo (D-030-02), re-evaluado POR LLAMADA
# ---------------------------------------------------------------------------
def effective_approval_mode(session_id: str = "") -> tuple[str, list[str]]:
    """(modo efectivo, evidencia). Cuatro superficies del bypass (P-1):

    1. ``HERMES_YOLO_MODE`` truthy (env del proceso).
    2. ``--yolo`` congelado a nivel proceso: ``tools.approval._YOLO_MODE_FROZEN``
       (import lazy del core, SOLO lectura — D-030-09).
    3. ``/yolo`` persistido de sesión: ``tools.approval.is_session_yolo_enabled``
       (el hook recibe ``session_id`` del core).
    4. ``approvals.mode`` de config.yaml (default ``smart``).
    """
    if _truthy(os.environ.get("HERMES_YOLO_MODE")):
        return "off", ["HERMES_YOLO_MODE=1"]
    try:
        # Import PUNTUAL del core (presente solo dentro de un Hermes real;
        # en tests standalone simplemente no está — fail-safe, no fail-open:
        # las demás superficies se siguen evaluando).
        from tools import approval as _core_approval

        if getattr(_core_approval, "_YOLO_MODE_FROZEN", False):
            return "off", ["process --yolo (tools.approval._YOLO_MODE_FROZEN)"]
        if session_id:
            try:
                if _core_approval.is_session_yolo_enabled(session_id):
                    return "off", [f"session /yolo enabled (session {session_id})"]
            except Exception:  # noqa: BLE001 - lectura best-effort del core
                pass
    except ImportError:
        pass
    mode = str(_config_section("approvals").get("mode") or _DEFAULT_APPROVAL_MODE)
    mode = mode.strip().lower()
    if mode not in _APPROVAL_RANKS:
        mode = "manual"  # valor desconocido → conservador
    return mode, [f"approvals.mode={mode} (config.yaml)"]


def _check_approval_mode(tool: str, args: dict[str, Any],
                         payload: dict[str, Any], session_id: str):
    if tool not in EXEC_TOOLS:
        return None
    floor = str(policy_contract.policy_value(payload, "approval_mode")).lower()
    effective, evidence = effective_approval_mode(session_id)
    if _APPROVAL_RANKS.get(effective, 0) >= _APPROVAL_RANKS.get(floor, 2):
        return None
    return _block(
        tool,
        f"approval_mode_below_floor (effective={effective}, floor={floor}, "
        f"via={'; '.join(evidence)})",
        f"{_BLOCK_PREFIX} approval mode '{effective}' is below the organization "
        f"floor '{floor}' (YOLO / approvals off is not permitted here; "
        f"detected via {', '.join(evidence)}). Restore the approval mode "
        f"(/yolo off or approvals.mode in config) and retry.",
    )


# ---------------------------------------------------------------------------
# P-2 allowed_terminal_backends
# ---------------------------------------------------------------------------
def effective_terminal_backend() -> str:
    backend = str(_config_section("terminal").get("backend") or _DEFAULT_TERMINAL_BACKEND)
    return backend.strip().lower()


def _check_terminal_backend(tool: str, _args: dict[str, Any],
                            payload: dict[str, Any], _session_id: str):
    if tool not in TERMINAL_TOOLS:
        return None
    allowed = [str(x).lower() for x in policy_contract.policy_value(
        payload, "allowed_terminal_backends") or []]
    if "*" in allowed:
        return None
    backend = effective_terminal_backend()
    if backend in allowed:
        return None
    return _block(
        tool,
        f"terminal_backend_not_allowed (backend={backend}, allowed={allowed})",
        f"{_BLOCK_PREFIX} terminal backend '{backend}' is not allowed. "
        f"Allowed backends: {', '.join(allowed) or 'none'}. The organization "
        f"policy governs which terminal backend this agent may use.",
    )


# ---------------------------------------------------------------------------
# P-5 require_write_sandbox (D-030-03)
# ---------------------------------------------------------------------------
def _sandbox_roots() -> list[str]:
    """Raíces permitidas: HERMES_WRITE_SAFE_ROOT (vehículo administrado) o el
    cwd de la sesión (workspace del dispositivo). $HERMES_HOME NO es raíz."""
    roots: list[str] = []
    for raw in filter(None, os.environ.get("HERMES_WRITE_SAFE_ROOT", "").split(os.pathsep)):
        try:
            roots.append(os.path.realpath(os.path.expanduser(raw)))
        except (OSError, ValueError):
            continue
    if not roots:
        try:
            roots.append(os.path.realpath(os.getcwd()))
        except (OSError, ValueError):
            roots.append("/")
    return roots


def _path_within(path: str, roots: list[str]) -> bool:
    try:
        real = os.path.realpath(os.path.expanduser(str(path)))
    except (OSError, ValueError):
        return False
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def _check_write_sandbox(tool: str, args: dict[str, Any],
                         payload: dict[str, Any], _session_id: str):
    if tool not in WRITE_TOOLS:
        return None
    if not policy_contract.capability_allowed(payload, "require_write_sandbox"):
        return None
    target = args.get("path") or args.get("file_path") or args.get("filename")
    if not target:
        return None
    roots = _sandbox_roots()
    if _path_within(str(target), roots):
        return None
    return _block(
        tool,
        f"write_outside_sandbox (path={target}, roots={roots})",
        f"{_BLOCK_PREFIX} writes are sandboxed to the device workspace "
        f"({os.pathsep.join(roots)}). '{target}' is outside the sandbox.",
    )


# ---------------------------------------------------------------------------
# P-18 allowed_env_passthrough (D-030-04)
# ---------------------------------------------------------------------------
# Asignaciones env en posición de comando: inicio de segmento (con prefijos
# sudo/env) o tras ; && || | (. Un `foo=bar` en posición de ARGUMENTO
# (tras el nombre del comando) NO es asignación del child.
_SEG_SPLIT_RE = re.compile(r"[;|&\n]+|\(|&&|\|\|")
_LEAD_ASSIGN_RE = re.compile(
    r"^(?:sudo\s+|env\s+|-u\s+\S+\s+)*([A-Za-z_][A-Za-z0-9_]*)=\S*\s*")
_EXPORT_RE = re.compile(r"\bexport\s+([A-Za-z_][A-Za-z0-9_]*)=")


def _assigned_env_names(command: str) -> set[str]:
    names: set[str] = set()
    for segment in _SEG_SPLIT_RE.split(command or ""):
        segment = segment.strip()
        if not segment:
            continue
        rest = segment
        # Pares NAME=value iniciales (pueden ser varios: A=1 B=2 cmd ...)
        while rest:
            m = _LEAD_ASSIGN_RE.match(rest)
            if not m:
                break
            names.add(m.group(1))
            rest = rest[m.end():]
    names.update(_EXPORT_RE.findall(command or ""))
    return names


def _configured_env_passthrough() -> set[str]:
    terminal = _config_section("terminal")
    names: set[str] = set()
    for key in ("env_passthrough", "docker_forward_env"):
        value = terminal.get(key)
        if isinstance(value, list):
            names.update(str(x) for x in value)
    return names


def _check_env_passthrough(tool: str, args: dict[str, Any],
                           payload: dict[str, Any], _session_id: str):
    if tool not in COMMAND_TOOLS:
        return None
    text = " ".join(
        str(args.get(k) or "") for k in ("command", "code", "script") if args.get(k)
    )
    candidates = _assigned_env_names(text) | _configured_env_passthrough()
    if not candidates:
        return None
    allowed = {str(x) for x in policy_contract.policy_value(
        payload, "allowed_env_passthrough") or []}
    if "*" in allowed:
        return None
    offending = sorted(candidates - allowed)
    if not offending:
        return None
    return _block(
        tool,
        f"env_passthrough_not_allowed (vars={offending})",
        f"{_BLOCK_PREFIX} environment variable(s) {', '.join(offending)} are not "
        f"in the allowed env passthrough list. Allowed: {', '.join(sorted(allowed)) or 'none'}.",
    )


# ---------------------------------------------------------------------------
# P-19/P-20 dominios y redes privadas (D-030-05/06)
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_URL_TRAILING = ").,;:!?]}\u201d\u2019"


def _find_urls(text: str) -> list[str]:
    """URLs en un texto, sin puntuación trailing (paréntesis/corchetes de
    prosodia). Soporta literales IPv6 (http://[::1]/x)."""
    return [m.rstrip(_URL_TRAILING) for m in _URL_RE.findall(text)]


def _host_matches_domain(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    return domain == "*" or host == domain or host.endswith("." + domain)


def _is_private_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") \
            or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast
    )


def _urls_in_args(args: dict[str, Any]) -> list[str]:
    """URLs presentes en los args (walk recursivo de valores string)."""
    found: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, str):
            found.extend(_find_urls(value))
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                _walk(item)

    _walk(args)
    return found


def _hosts_of(urls: list[str]) -> list[str]:
    hosts: list[str] = []
    for url in urls:
        try:
            host = urllib.parse.urlparse(url).hostname
        except ValueError:
            continue
        if host:
            hosts.append(host)
    return hosts


def _is_web_tool(tool: str) -> bool:
    return tool in WEB_TOOLS or tool.startswith("browser")


def _check_web_policy(tool: str, args: dict[str, Any],
                      payload: dict[str, Any], _session_id: str):
    """P-19 + P-20 para tools con URL (web/browser/vision) y comandos."""
    policy = policy_contract.policy_value(payload, "web_domain_policy") or {}
    if not isinstance(policy, dict):
        policy = {}
    mode = str(policy.get("mode") or "allowlist").strip().lower()
    domains = [str(d) for d in policy.get("domains") or []]

    # --- web_search: el query no es una URL — allowlist exige site: (D-030-05)
    if tool == "web_search":
        if mode != "allowlist":
            return None
        query = " ".join(
            str(args.get(k) or "") for k in ("query", "q", "search") if args.get(k)
        ).lower()
        if any(_host_matches_domain(d, d) and d.lower() in query for d in domains):
            return None
        site = re.search(r"site[=:]\s*([\w.*-]+)", query)
        if site and any(
            _host_matches_domain(site.group(1), d) for d in domains
        ):
            return None
        return _block(
            tool,
            f"web_search_unrestricted (mode=allowlist, domains={domains})",
            f"{_BLOCK_PREFIX} web search is restricted to the organization's "
            f"domain allowlist ({', '.join(domains) or 'none'}). Restrict the "
            f"query with site:<allowed-domain> and retry.",
        )

    # --- tools con URL en args (web/browser/vision) o URLs en comandos
    if _is_web_tool(tool):
        urls = _urls_in_args(args)
    elif tool in COMMAND_TOOLS:
        text = " ".join(
            str(args.get(k) or "") for k in ("command", "code", "script") if args.get(k)
        )
        urls = _find_urls(text)
    else:
        return None
    if not urls:
        return None

    # P-20 primero (red privada): apply SIEMPRE que la policy la niegue.
    if not policy_contract.capability_allowed(payload, "allow_private_network_access"):
        private = sorted({h for h in _hosts_of(urls) if _is_private_host(h)})
        if private:
            return _block(
                tool,
                f"private_network_access (hosts={private})",
                f"{_BLOCK_PREFIX} access to private networks is not permitted "
                f"by organization policy (host(s): {', '.join(private)}).",
            )

    # P-19 dominios.
    if mode == "allowlist":
        hosts = _hosts_of(urls)
        bad = sorted({
            h for h in hosts
            if not any(_host_matches_domain(h, d) for d in domains)
        })
        if bad:
            return _block(
                tool,
                f"domain_not_in_allowlist (hosts={bad}, allowlist={domains})",
                f"{_BLOCK_PREFIX} domain(s) {', '.join(bad)} are not in the "
                f"organization's web domain allowlist "
                f"({', '.join(domains) or 'none'}).",
            )
    elif mode == "blocklist":
        hosts = _hosts_of(urls)
        bad = sorted({
            h for h in hosts
            if any(_host_matches_domain(h, d) for d in domains)
        })
        if bad:
            return _block(
                tool,
                f"domain_blocklisted (hosts={bad}, blocklist={domains})",
                f"{_BLOCK_PREFIX} domain(s) {', '.join(bad)} are blocked by "
                f"the organization's web domain policy.",
            )
    return None


# ---------------------------------------------------------------------------
# P-29 allow_self_updates (D-030-08)
# ---------------------------------------------------------------------------
_SELF_UPDATE_RES = (
    re.compile(r"\bhermes\s+update\b"),
    re.compile(r"\bhermes\s+plugins?\s+(?:install|add|enable)\b"),
    re.compile(r"\b(?:pip|uv|uvx|pipx)\s+(?:pip\s+)?(?:install|add)\b[^|;&\n]*\bhermes"),
)


def _check_self_updates(tool: str, args: dict[str, Any],
                        payload: dict[str, Any], _session_id: str):
    if tool not in COMMAND_TOOLS:
        return None
    if policy_contract.capability_allowed(payload, "allow_self_updates"):
        return None
    text = " ".join(
        str(args.get(k) or "") for k in ("command", "code", "script") if args.get(k)
    )
    if not any(rx.search(text) for rx in _SELF_UPDATE_RES):
        return None
    return _block(
        tool,
        "self_update_not_allowed (hermes update / plugin catalog install)",
        f"{_BLOCK_PREFIX} self-updates are not permitted. This agent may not "
        f"run 'hermes update', install plugins from the catalog, or install "
        f"hermes packages from agent sessions.",
    )


# ---------------------------------------------------------------------------
# P-26 allowed_providers (D-030-07) — pre_llm_call (contexto, no veto)
# ---------------------------------------------------------------------------
_CUSTOM_PROVIDERS = frozenset({"custom", "openai-compatible", "openai_compatible"})


def _classify_provider(provider: str, model_cfg: dict[str, Any]) -> str:
    """Provider de policy. `custom`+base_url = gateway del tenant (contrato
    del profile template). Residual D-GOH-07: repuntar base_url a un proxy
    personal no es distinguible localmente — el gate duro es LiteLLM."""
    provider = (provider or "").strip().lower()
    if provider in _CUSTOM_PROVIDERS and model_cfg.get("base_url"):
        return "litellm_gateway"
    return provider


def effective_providers() -> list[str]:
    """Providers efectivos declarados en la config local (C-028/C-029/C-030):
    model.provider, fallback_providers, provider_routing, auxiliary,
    local_runtime y presets MoA."""
    cfg = hermes_config()
    names: set[str] = set()

    model = cfg.get("model") or {}
    if isinstance(model, dict):
        provider = str(model.get("provider") or "").strip()
        if provider:
            names.add(_classify_provider(provider, model))

    fallback = cfg.get("fallback_providers")
    if isinstance(fallback, list):
        names.update(str(x).strip().lower() for x in fallback if x)

    routing = cfg.get("provider_routing") or {}
    if isinstance(routing, dict):
        for key in ("only", "sort", "order"):
            value = routing.get(key)
            if isinstance(value, list):
                names.update(str(x).strip().lower() for x in value if x)

    def _collect_providers(node: Any) -> None:
        if isinstance(node, dict):
            provider = node.get("provider")
            if isinstance(provider, str) and provider.strip():
                names.add(_classify_provider(provider.strip(), node))
            for value in node.values():
                _collect_providers(value)
        elif isinstance(node, list):
            for item in node:
                _collect_providers(item)

    for section in ("auxiliary", "local_runtime", "moa"):
        _collect_providers(cfg.get(section))

    return sorted(x for x in names if x)


def provider_offenders(payload: dict[str, Any]) -> list[str]:
    allowed = {str(x).strip().lower() for x in
               policy_contract.policy_value(payload, "allowed_providers") or []}
    if "*" in allowed:
        return []
    return [p for p in effective_providers() if p not in allowed]


# ---------------------------------------------------------------------------
# Managed scope: sync administrado de security.* en config.yaml (D-030-05/06/08)
# ---------------------------------------------------------------------------
_MANAGED_SYNC_MARKER = "managed-config-sync.json"


def _managed_sync_marker_path() -> Path:
    return enroll.trust_dir() / _MANAGED_SYNC_MARKER


def _blocked_messaging_platforms(payload: dict[str, Any]) -> list[str]:
    """Plataformas de mensajería bloqueadas por ``messaging_permissions``
    (REQ-PE-011, spec 042 GAP-42-A).

    ``messaging_permissions`` es un map ``platform→{allowed: bool, mode: str}``
    (o ``platform→false`` forma corta). Una plataforma con ``allowed=false``
    (o ausente del map — deny-by-default) está bloqueada para despacho.

    Como el catálogo de plataformas no se conoce en el plugin, solo se
    reportan las EXPLÍCITAMENTE denegadas (presentes en el map con
    ``allowed=false``). Las ausentes ya están bloqueadas por deny-by-default
    del backend (``perm.get("allowed", False)`` → False).
    """
    perms = policy_contract.policy_value(payload, "messaging_permissions") or {}
    if not isinstance(perms, dict):
        return []
    blocked: list[str] = []
    for platform, perm in perms.items():
        if isinstance(perm, dict):
            allowed = bool(perm.get("allowed", False))
        else:
            allowed = bool(perm)
        if not allowed:
            blocked.append(str(platform))
    return sorted(blocked)


_PROFILE_IDENTITY_MARKERS = (
    "config.yaml", ".env", "SOUL.md", "profile.yaml", "auth.json", "state.db",
)

# Nombre del plugin tal como lo listan ``plugins.enabled`` / ``plugins.disabled``.
GOVERNANCE_PLUGIN = "trust-agent-governance"


def _pin_governance_enabled(cfg: dict[str, Any]) -> bool:
    """Deja este plugin habilitado en *cfg* (mutado in place): presente en
    ``plugins.enabled`` y ausente de ``plugins.disabled`` — en el core el
    disable gana sobre el enable, y un home que no lo habilita arranca su
    próximo manager de plugins SIN gobernanza. Retorna True si cambió."""
    plugins = cfg.get("plugins")
    plugins = dict(plugins) if isinstance(plugins, dict) else {}
    enabled = [str(x) for x in plugins.get("enabled") or []]
    disabled = [str(x) for x in plugins.get("disabled") or []]
    changed = False
    if GOVERNANCE_PLUGIN not in enabled:
        plugins["enabled"] = enabled + [GOVERNANCE_PLUGIN]
        changed = True
    if GOVERNANCE_PLUGIN in disabled:
        plugins["disabled"] = [x for x in disabled if x != GOVERNANCE_PLUGIN]
        changed = True
    if changed:
        cfg["plugins"] = plugins
    return changed


def _live_profile_dirs(home: Path) -> list[Path]:
    """Named profiles under ``home/profiles`` that carry identity markers.

    Same predicate family as the core (``hermes_constants``): a marker-less
    dir is runtime side-effect shell — never listed, never served, and never
    normalized here (creating a config.yaml would make it servable, which is
    the host's decision, not the plugin's)."""
    root = home / "profiles"
    if not root.is_dir():
        return []
    try:
        from hermes_constants import named_profile_has_identity as _has_identity
    except Exception:  # noqa: BLE001 - standalone tests: fall back to the marker set
        def _has_identity(profile_home: Path) -> bool:  # type: ignore[misc]
            return any((profile_home / m).is_file() for m in _PROFILE_IDENTITY_MARKERS)
    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and not entry.name.startswith(".") and _has_identity(entry):
            out.append(entry)
    return out


def approved_models(payload: dict[str, Any]) -> list[str]:
    """Modelos que la policy aprueba (``approved_models`` o, en bundles viejos, ``policy_models``)."""
    approved = payload.get("approved_models") if isinstance(payload, dict) else None
    if not isinstance(approved, list) or not approved:
        approved = policy_contract.policy_value(payload, "policy_models") or []
    return [str(m) for m in approved]


def normalize_profile_config(cfg: dict[str, Any], model_pin: dict[str, Any], approved: list[str]) -> bool:
    """P-26 sobre UN config de perfil (dict mutado in place); True si cambió.

    - ``gateway.standalone`` fuera: el host multiplexa los perfiles.
    - provider/base_url/api_key como el config principal; puertas alternativas cerradas.
    - el modelo del perfil se conserva solo si está aprobado; si no, el default de la policy.
    - el plugin de gobernanza habilitado (``plugins.disabled`` gana sobre enabled en el core).
    Lo usan el sync de cada turno y la materialización de perfiles de la organización
    (spec 045 §6), que lo aplica ANTES de que el perfil exista para Hermes."""
    changed = False
    gateway = cfg.get("gateway")
    gateway = dict(gateway) if isinstance(gateway, dict) else {}
    if gateway.get("standalone") is not None:
        gateway.pop("standalone", None)
        cfg["gateway"] = gateway
        changed = True
    if model_pin:
        model = cfg.get("model")
        model = dict(model) if isinstance(model, dict) else {}
        for key, value in model_pin.items():
            if key == "default":
                continue  # el perfil puede usar OTRO modelo aprobado
            if model.get(key) != value:
                model[key] = value
                changed = True
        current = str(model.get("default") or "").strip()
        if current and approved and current not in approved:
            model["default"] = model_pin.get("default")
            changed = True
        elif not current and model_pin.get("default"):
            model["default"] = model_pin["default"]
            changed = True
        cfg["model"] = model
        for key, empty in (
            ("fallback_providers", []), ("provider_routing", {}),
            ("auxiliary", {}), ("local_runtime", {}), ("moa", {}),
            ("providers", {}),
        ):
            if cfg.get(key) not in (None, empty):
                cfg[key] = empty
                changed = True
    # La gobernanza del perfil ES este plugin: el manager de plugins de
    # cada perfil (uno por home en el gateway multiplexado) solo lo carga
    # si su config lo habilita, y ``plugins.disabled`` gana sobre enabled.
    if _pin_governance_enabled(cfg):
        changed = True
    return changed


def _normalize_profile_configs(payload: dict[str, Any], model_pin: dict[str, Any]) -> list[str]:
    """P-26 per profile: every LIVE named profile runs on the org's gateway.

    Crear perfiles está PERMITIDO bajo gobernanza (decisión de producto,
    2026-11-10) — pero cada perfil es una isla con su PROPIO config.yaml y
    nada lo normalizaba: un perfil clonado heredaba ``gateway.standalone:
    true`` del default gobernado y el multiplexor del host jamás lo servía
    (``profile_mismatch`` en cada sesión — incidente en vivo), y un config
    editado a mano podía nombrar otro proveedor/endpoint/modelo. En cada sync:

    - ``gateway.standalone`` se elimina: el host multiplexa los perfiles.
    - provider/base_url/api_key se pinean como el config principal (P-26) y
      las puertas alternativas (fallback_providers, providers, …) se cierran.
    - el modelo del perfil se CONSERVA solo si está entre los aprobados
      (``approved_models``); si no, se clampa al default de la policy.

    Idempotente: escribe solo si el contenido cambia. Retorna los perfiles
    sincronizados para la línea de audit del sync."""
    if not isinstance(payload, dict):
        return []
    approved = approved_models(payload)
    home = enroll.hermes_home()
    synced: list[str] = []
    for profile_dir in _live_profile_dirs(home):
        path = profile_dir / "config.yaml"
        if not path.is_file():
            continue  # isla viva sin config: nada que pinear (y no lo creamos)
        try:
            import yaml  # copia vendor del plugin (ver arriba)

            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cfg = loaded if isinstance(loaded, dict) else {}
        if not normalize_profile_config(cfg, model_pin, approved):
            continue
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            continue
        synced.append(f"profile:{profile_dir.name}")
    return synced


def managed_model_pin(payload: dict[str, Any]) -> dict[str, Any]:
    """P-26: lo que el config local debe fijar del modelo — default de la policy, proveedor del
    gateway de la organización y endpoint/clave del dispositivo (device-token.json)."""
    allowed_providers = {
        str(p).strip().lower()
        for p in (policy_contract.policy_value(payload, "allowed_providers") or [])
    }
    policy_models = policy_contract.policy_value(payload, "policy_models") or []
    default_model = payload.get("policy_default_model") or (
        policy_models[0] if isinstance(policy_models, list) and policy_models else None
    )
    device = enroll.load_device_token() or {}
    model_pin: dict[str, Any] = {}
    if isinstance(default_model, str) and default_model.strip():
        model_pin["default"] = default_model.strip()
    if allowed_providers and "*" not in allowed_providers and "litellm_gateway" in allowed_providers:
        model_pin["provider"] = "custom"
        model_pin["fallback_providers"] = []
    if isinstance(device.get("model_base_url"), str) and device["model_base_url"]:
        model_pin["base_url"] = device["model_base_url"]
    if isinstance(device.get("model_key"), str) and device["model_key"]:
        model_pin["api_key"] = device["model_key"]
    return model_pin


def sync_managed_config(payload: dict[str, Any], *, force: bool = False) -> bool:
    """Impone en ``config.yaml`` las restricciones que la policy gobierna
    (managed scope como vehículo, ADR-016):

    - ``web_domain_policy.mode=blocklist`` con dominios →
      ``security.website_blocklist = {enabled: true, domains: [...]}``
      (el mecanismo nativo M11 lo aplica con su TTL de recarga).
    - ``allow_private_network_access=false`` → ``security.allow_private_urls: false``.
    - ``allow_self_updates=false`` → ``security.allow_lazy_installs: false``.
    - ``messaging_permissions[<plat>].allowed=false`` →
      ``gateway.platforms.<plat>.enabled=false`` (REQ-PE-011, spec 042
      GAP-42-A: el overlay GANA al config del usuario, M16 — el gateway
      no despacha por la plataforma bloqueada en NINGUNA superficie).

    SOLO impone restricciones: jamás resetea una sección existente (una
    blocklist stale o un platform.enabled=false stale son MÁS restrictivos
    — dirección fail-closed). Idempotente: escribe solo si el contenido
    cambia; marca el jti sincronizado para no repetir el trabajo por bundle.
    Retorna True si escribió.
    """
    marker_path = _managed_sync_marker_path()
    jti = str(payload.get("jti") or "")
    # NOTE: deliberately no early return on jti: the config file is
    # user-writable on disk, so every turn re-checks and restores the
    # organization-owned model/provider/credential fields even when the
    # signed bundle itself did not change.

    policy = policy_contract.policy_value(payload, "web_domain_policy") or {}
    if not isinstance(policy, dict):
        policy = {}
    mode = str(policy.get("mode") or "").strip().lower()
    domains = [str(d) for d in policy.get("domains") or []]

    security_updates: dict[str, Any] = {}
    if mode == "blocklist" and domains:
        security_updates["website_blocklist"] = {
            "enabled": True, "domains": list(domains), "shared_files": [],
        }
    if not policy_contract.capability_allowed(payload, "allow_private_network_access"):
        security_updates["allow_private_urls"] = False
    if not policy_contract.capability_allowed(payload, "allow_self_updates"):
        security_updates["allow_lazy_installs"] = False

    # P-26: the organization owns provider, model and credentials. Pin the
    # local config to the signed policy every sync so editing config.yaml or
    # a stale profile cannot select another provider/model/endpoint.
    model_pin = managed_model_pin(payload)
    ui_features = payload.get("ui_features") or {}
    webapp_pin = isinstance(ui_features, dict) and ui_features.get("web_access") is False
    voice_pin = isinstance(ui_features, dict) and ui_features.get("voice") is False

    # REQ-PE-011: plataformas de mensajería bloqueadas → gateway.platforms.<plat>.enabled=false
    blocked_platforms = _blocked_messaging_platforms(payload)

    if not security_updates and not blocked_platforms and not model_pin and not webapp_pin and not voice_pin:
        return False

    try:
        import yaml  # venv de Hermes

        path = enroll.hermes_home() / "config.yaml"
        existing: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        changed = False

        # security.* updates
        if security_updates:
            security = existing.get("security")
            security = dict(security) if isinstance(security, dict) else {}
            for key, value in security_updates.items():
                if security.get(key) != value:
                    security[key] = value
                    changed = True
            existing["security"] = security

        # P-26: model/provider/api_key pin + removal of alternate providers.
        if model_pin:
            model = existing.get("model")
            model = dict(model) if isinstance(model, dict) else {}
            for key, value in model_pin.items():
                if model.get(key) != value:
                    model[key] = value
                    changed = True
            model.pop("fallback", None)
            existing["model"] = model
            if model_pin.get("provider") == "custom":
                # Alternate provider doors: each one could route around the
                # organization's LiteLLM gateway/billing.
                for key, empty in (
                    ("fallback_providers", []),
                    ("provider_routing", {}),
                    ("auxiliary", {}),
                    ("local_runtime", {}),
                    ("moa", {}),
                    ("providers", {}),
                ):
                    if existing.get(key) not in (None, empty):
                        existing[key] = empty
                        changed = True

        # UI policy → backend function off: serving the web app and voice-only
        # mode are not just hidden buttons; their gateway features are pinned off.
        if webapp_pin:
            webapp = existing.get("webapp")
            webapp = dict(webapp) if isinstance(webapp, dict) else {}
            if webapp.get("enabled") is not False:
                webapp["enabled"] = False
                existing["webapp"] = webapp
                changed = True
        if voice_pin:
            gateway = existing.get("gateway")
            gateway = dict(gateway) if isinstance(gateway, dict) else {}
            voice = gateway.get("voice")
            voice = dict(voice) if isinstance(voice, dict) else {}
            if voice.get("enabled_voice_only") is not False:
                voice["enabled_voice_only"] = False
                gateway["voice"] = voice
                existing["gateway"] = gateway
                changed = True

        # gateway.platforms.<plat>.enabled=false (messaging_permissions)
        if blocked_platforms:
            gateway = existing.get("gateway")
            gateway = dict(gateway) if isinstance(gateway, dict) else {}
            platforms = gateway.get("platforms")
            platforms = dict(platforms) if isinstance(platforms, dict) else {}
            for plat in blocked_platforms:
                plat_cfg = platforms.get(plat)
                plat_cfg = dict(plat_cfg) if isinstance(plat_cfg, dict) else {}
                if plat_cfg.get("enabled") is not False:
                    plat_cfg["enabled"] = False
                    platforms[plat] = plat_cfg
                    changed = True
            gateway["platforms"] = platforms
            existing["gateway"] = gateway

        # Este plugin ES la gobernanza de este home: su habilitación también
        # la pinea la organización (el próximo arranque del gateway no debe
        # cargar este home sin plugin).
        if _pin_governance_enabled(existing):
            changed = True

        # P-26 per profile (perfiles gobernados): corre en CADA sync — los
        # perfiles se crean entre bundles y su config es tan editable como el
        # principal. Véase _normalize_profile_configs.
        profile_synced = _normalize_profile_configs(payload, model_pin)
        if not changed:
            if profile_synced:
                log_event("managed_config_synced", keys=profile_synced)
            try:
                marker_path.write_text(json.dumps({"jti": jti, "at": int(time.time())}),
                                       encoding="utf-8")
            except OSError:
                pass
            return bool(profile_synced)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        synced_keys = sorted(security_updates) + [
            f"gateway.platforms.{p}.enabled" for p in blocked_platforms
        ]
        if model_pin:
            synced_keys += [f"model.{k}" for k in sorted(model_pin)]
        if webapp_pin:
            synced_keys.append("webapp.enabled")
        if voice_pin:
            synced_keys.append("gateway.voice.enabled_voice_only")
        synced_keys += profile_synced
        log_event("managed_config_synced", keys=synced_keys)
        try:
            marker_path.write_text(json.dumps({"jti": jti, "at": int(time.time())}),
                                   encoding="utf-8")
        except OSError:
            pass
        return True
    except (OSError, ValueError, ImportError) as exc:
        log_event("managed_config_sync_failed", error=str(exc))
        return False


# ---------------------------------------------------------------------------
# Managed-file protection: a governed agent may never edit its own config,
# device token or governance status via tools (the platform UI is the only
# supported path; the organization owns provider/model/credentials).
# ---------------------------------------------------------------------------
_MANAGED_CONFIG_WRITE_RE = re.compile(
    r"(hermes\s+config\s+(set|unset|edit)|config\.ya?ml)"
    r"|(device-token\.json|governance_status\.json|governance-bundle\.jws)",
    re.IGNORECASE,
)


def _is_managed_config_path(path: str) -> bool:
    try:
        candidate = Path(path).expanduser().resolve()
        home = enroll.hermes_home().resolve()
        # El home ENROLADO puede ser otro (sesión de un perfil del escritorio:
        # identidad/bundle del dispositivo viven en la raíz) — sus credenciales
        # y su config son igual de organization-managed.
        device = enroll.device_home().resolve()
        protected = {(home / "config.yaml").resolve(), (device / "config.yaml").resolve()}
        for base in {home, device}:
            for name in ("device-token.json", "governance_status.json",
                         "governance-bundle.jws"):
                protected.add((base / "trust-agent" / name).resolve())
        # P-26 por perfil: el config de cada perfil vivo lo pinea el sync;
        # el agente tampoco lo edita (desde ninguna sesión).
        for profile_dir in _live_profile_dirs(device):
            protected.add((profile_dir / "config.yaml").resolve())
        return candidate in protected
    except OSError:
        return False


def _check_managed_config_edits(tool: str, args: dict[str, Any],
                                payload: dict[str, Any], _session_id: str):
    allowed = {str(x).strip().lower() for x in
               policy_contract.policy_value(payload, "allowed_providers") or []}
    if "*" in allowed:
        return None

    if tool in WRITE_TOOLS:
        candidate = str(args.get("path") or args.get("file_path") or "")
        if candidate and _is_managed_config_path(candidate):
            return _block(
                tool,
                "managed_config_write",
                f"{_BLOCK_PREFIX} config.yaml / device-token.json / "
                "governance_status.json are organization-managed files and "
                "cannot be edited by the agent. Providers, models, gateways "
                "and credentials are configured by your organization.",
            )

    if tool in COMMAND_TOOLS:
        command = str(args.get("command") or args.get("code") or "")
        if command and _MANAGED_CONFIG_WRITE_RE.search(command):
            return _block(
                tool,
                "managed_config_command",
                f"{_BLOCK_PREFIX} editing config.yaml, trust-agent credentials "
                "or the governance bundle is forbidden; provider/model/gateway "
                "configuration belongs to your organization.",
            )
    return None


# Un perfil creado FUERA de la app (CLI o código del agente, sin clonar) nace
# sin el plugin de gobernanza y el gateway multiplexado lo sirve en caliente:
# un agente gobernado no fabrica perfiles. La app los crea clonando uno vivo,
# así heredan la gobernanza de la organización.
_PROFILE_CREATE_RE = re.compile(
    r"\b(hermes|trust-agent)(\.exe)?\b[^\n|;&]*?\bprofiles?\s+(create|import|clone)\b"
    r"|\bcreate_profile\s*\(|\bimport_profile\s*\(",
    re.IGNORECASE,
)


def _check_profile_creation(tool: str, args: dict[str, Any],
                            _payload: dict[str, Any], _session_id: str):
    if tool not in COMMAND_TOOLS:
        return None
    command = str(args.get("command") or args.get("code") or "")
    if command and _PROFILE_CREATE_RE.search(command):
        return _block(
            tool,
            "profile_create_command",
            f"{_BLOCK_PREFIX} profiles are created from the Trust Agent app "
            "(Profiles → New profile) so they inherit your organization's "
            "governance; the agent cannot create or import profiles.",
        )
    return None


# ---------------------------------------------------------------------------
# REQ-PE-011 (spec 042 GAP-42-A): messaging_permissions — veto de conector
# ---------------------------------------------------------------------------
def _check_messaging_permissions(tool: str, _args: dict[str, Any],
                                 payload: dict[str, Any], _session_id: str):
    """Defense-in-depth: veto de tools de conector ``connectors__<slug>__*``
    cuando ``messaging_permissions[<slug>].allowed == false``.

    El pin de ``gateway.platforms.<slug>.enabled=false`` vía
    :func:`sync_managed_config` es el mecanismo primario (bloquea el despacho
    del gateway); este veto cubre el camino de tools de conector directas
    (p. ej. ``connectors__telegram__send_message``) que no pasan por el
    gateway. ``allowed_connectors`` (en enforcement.py) ya veto por slug
    ausente de la lista; este check cubre el caso donde el conector ESTÁ
    allowlisted pero la mensajería por esa plataforma está bloqueada.
    """
    if not tool.startswith("connectors__") or tool == "connectors__execute":
        return None
    parts = tool.split("__", 2)
    if len(parts) != 3 or not parts[1]:
        return None
    slug = parts[1]
    blocked = _blocked_messaging_platforms(payload)
    if slug not in blocked:
        return None
    return _block(
        tool,
        f"messaging_platform_blocked (platform={slug})",
        f"{_BLOCK_PREFIX} messaging platform '{slug}' is blocked by "
        f"organization policy (messaging_permissions). The agent may not "
        f"dispatch or interact via this platform on any surface.",
    )


# ---------------------------------------------------------------------------
# Hooks (API pública: ctx.register_hook — cero parches al core)
# ---------------------------------------------------------------------------
def on_pre_tool_call(tool_name: str = "", args: dict[str, Any] | None = None,
                     session_id: str = "", **_: Any) -> dict[str, str] | None:
    """Veto del batch 1 (spec 030). En modo restringido retorna None: el
    fail-closed de enforcement (spec 019 §5.4) ya bloqueó la llamada."""
    tool = (tool_name or "").strip()
    if not tool or tool in INTERNAL_TOOLS:
        return None
    snapshot = enforcement.STATE.evaluate()
    if snapshot["mode"] != "normal" or not snapshot.get("payload"):
        return None  # restricted: enforcement ya bloquea TODO (REQ-GOH-009/010)
    payload = snapshot["payload"]
    args = args if isinstance(args, dict) else {}

    checkers = (
        _check_self_updates,      # P-29 (antes de nada: auto-modificación)
        _check_managed_config_edits,  # P-26 (config/credenciales org)
        _check_profile_creation,  # perfiles gobernados: solo la app los crea
        _check_approval_mode,     # P-1
        _check_terminal_backend,  # P-2
        _check_env_passthrough,   # P-18
        _check_write_sandbox,     # P-5
        _check_web_policy,        # P-19 + P-20
        _check_messaging_permissions,  # REQ-PE-011 (spec 042 GAP-42-A)
    )
    for checker in checkers:
        try:
            decision = checker(tool, args, payload, session_id)
            if decision:
                return decision
        except Exception as exc:  # noqa: BLE001 - un check roto NUNCA abre:
            # fail-closed de dirección: ante error interno se bloquea.
            log_event("batch1_check_error", checker=checker.__name__, error=str(exc))
            return _block(
                tool,
                f"batch1_internal_error ({checker.__name__})",
                f"{_BLOCK_PREFIX} the governance plugin could not verify this "
                f"tool call against organization policy ({checker.__name__}); "
                f"it is blocked as a precaution.",
            )
    return None


def on_pre_llm_call(session_id: str = "", **_: Any) -> dict[str, str] | None:
    """Contexto por turno del batch 1: aviso de approval_mode bajo el piso,
    mensaje de policy de providers (P-26 — el hook NO veta la llamada,
    D-HRB-08) y sync administrado de security.* (una vez por bundle)."""
    snapshot = enforcement.STATE.evaluate()
    if snapshot["mode"] != "normal" or not snapshot.get("payload"):
        return None  # restricted: enforcement inyecta el aviso de modo
    payload = snapshot["payload"]

    # Detect offenders BEFORE the managed sync restores the config, so the
    # model still receives an explicit policy note about what was blocked.
    try:
        offenders = provider_offenders(payload)
    except Exception:  # noqa: BLE001
        offenders = []

    try:
        sync_managed_config(payload)
    except Exception as exc:  # noqa: BLE001 - el sync jamás tumba el turno
        log_event("managed_config_sync_error", error=repr(exc))

    notes: list[str] = []

    floor = str(policy_contract.policy_value(payload, "approval_mode")).lower()
    effective, evidence = effective_approval_mode(session_id)
    if _APPROVAL_RANKS.get(effective, 0) < _APPROVAL_RANKS.get(floor, 2):
        notes.append(
            f"approval mode '{effective}' is BELOW the organization floor "
            f"'{floor}' (detected via {', '.join(evidence)}): execution tools "
            f"(terminal, process_manage, execute_code, computer_use) are "
            f"BLOCKED by organization policy until the approval mode is restored."
        )

    if offenders:
        allowed = policy_contract.policy_value(payload, "allowed_providers") or []
        notes.append(
            f"BLOCKED by organization policy: provider(s) {', '.join(offenders)} "
            f"are not in the allowed providers list "
            f"({', '.join(str(x) for x in allowed) or 'none'}). Model calls must "
            f"go through the organization's approved providers (the LiteLLM "
            f"gateway is the hard gate); do not rely on these providers."
        )
        log_event("tool_vetoed", tool="pre_llm_call",
                  reason=f"provider_not_allowed ({', '.join(offenders)})",
                  layer="config_batch1")
        _bump_veto("pre_llm_call", f"provider_not_allowed ({', '.join(offenders)})")

    if not notes:
        return None
    return {"context": "[organization governance] " + " ".join(notes)}


def register(ctx) -> None:
    """Registra los hooks del batch 1 (cableado aditivo desde __init__.py,
    DESPUÉS de enforcement.register — su block gana los empates)."""
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    log_event("batch1_active",
              keys=["approval_mode", "allowed_terminal_backends",
                    "require_write_sandbox", "allowed_env_passthrough",
                    "web_domain_policy", "allow_private_network_access",
                    "allowed_providers", "allow_self_updates"])

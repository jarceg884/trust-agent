"""enforcement.py — enforcement real de la policy de la organización.

Spec 022 (hermes-real-bridge) = Incremento 2 de la spec 019: REQ-GOH-005
(veto ``pre_tool_call`` por ``allowed_tools``), REQ-GOH-009/010 (fail-closed
offline/desligado) y REQ-GOH-003 (verificación del bundle). Cero parches al
core: solo ``ctx.register_hook`` (API pública verificada en
``hermes_cli/plugins.py`` — ``_get_pre_tool_call_directive_details`` consume
``{"action": "block", "message"}``; ``agent/turn_context.py`` concatena los
contextos de todos los ``pre_llm_call``).

Máquina de estados (spec 019 §5.4, D-HRB-05):

  NORMAL     bundle vigente en caché (firma RS256 + aud + exp + jti OK)
             → tools según allowed_tools; governance_status siempre permitida.
  RESTRICTED sin caché / caché vencida sin sync / sync 401-403
             → TODA tool externa bloqueada + aviso por pre_llm_call
             (con instrucción de no operar si unbind_mode=lock).

El caché ``$HERMES_HOME/trust-agent/governance-bundle.jws`` es el contrato
compartido con el núcleo v2 (spec 019 Inc. 1): quien sincronice escribe el
JWS compacto; aquí lo verificamos POR NOSOTROS MISMOS (defensa en
profundidad, D-HRB-04) y, si falta o expiró, intentamos UN sync contra
``GET /api/me/governance-bundle`` (camino plugin: Bearer = device token,
``X-Device-Token`` = device token — ``bundle.py::require_bundle_auth``).

Threat model honesto (ADR-018): este enforcement local es defensa en
profundidad + UX + detección; las garantías DURAS son remotas (LiteLLM
modelos/presupuesto, vault server-side, device token, audit). Un usuario del
SO puede deshabilitar el plugin — la org lo detecta por ausencia de
attestation/traffic y revoca el acceso real.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import enroll
from . import policy_contract

# Tools internas de este paquete: siempre permitidas (REQ-GOH-005/008).
INTERNAL_TOOLS = frozenset({"governance_status"})

# Mensaje exacto de REQ-GOH-005 (normativo en spec 019).
_BLOCK_MSG = (
    "BLOCKED by organization policy: tool '{tool}' is not allowed. "
    "Allowed tools: {allowed}."
)
_RESTRICTED_MSG = (
    "BLOCKED by organization policy: restricted mode ({cause}). "
    "No external tools are available until a valid governance bundle "
    "is synced. Ask the user to re-enroll (/trust-enroll) or check "
    "network connectivity to the organization's control plane."
)
_LOCK_CONTEXT = (
    "[organization governance] LOCKED: this device has been unbound or "
    "revoked by the organization (unbind_mode=lock). Do not attempt any "
    "operation or tool use; inform the user they must re-enroll with their "
    "organization or contact their admin."
)
_RESTRICTED_CONTEXT = (
    "[organization governance] RESTRICTED MODE ({cause}): the signed "
    "governance bundle is not currently valid. All external tools are "
    "blocked by organization policy. Answer conversationally only."
)

# Claims obligatorios del payload (spec 019 §5.2 — el endpoint los garantiza;
# el verificador los EXIGE: un bundle sin ellos no es confiable).
REQUIRED_CLAIMS = (
    "exp", "iat", "jti", "aud", "tenant_id", "user_id", "device_id",
    "policy_version", "allowed_tools", "allowed_connectors",
    "approved_models", "budget", "offline_grace_minutes", "unbind_mode",
    "attestation_overdue_hours",
)

_JTI_CACHE_LIMIT = 256


class _Unauthorized(Exception):
    """El control plane respondió 401/403 en el sync (desligado/revocado)."""


class BundleRejected(Exception):
    """Bundle inválido/manipulado/replay — NUNCA se aplica (REQ-GOH-003)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Estado en disco
# ---------------------------------------------------------------------------
def _bundle_cache_path() -> Path:
    return enroll.trust_dir() / "governance-bundle.jws"


def _jti_cache_path() -> Path:
    return enroll.trust_dir() / "jti-cache.json"


def _veto_counts_path() -> Path:
    return enroll.trust_dir() / "veto-counts.json"


def _plugin_log_path() -> Path:
    return enroll.trust_dir() / "plugin.log"


def log_event(event: str, **fields: Any) -> None:
    """Log local append-only del plugin (evidencia + debugging).

    Siempre incluye ``pid``: en el modelo un-gateway-por-host el plugin puede
    estar instanciado en varios procesos a la vez (gateway host, chat/TUI
    embebido, managed workers) y el pid es la única forma de atribuir eventos.
    """
    try:
        entry = {"ts": int(time.time()), "pid": os.getpid(), "event": event, **fields}
        with _plugin_log_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # el log JAMÁS tumba el turno


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Verificación del bundle (D-HRB-04, REQ-GOH-003)
# ---------------------------------------------------------------------------
def pinned_public_key_pem() -> str:
    """Clave pública pineada junto al plugin (dato público, D-GOH-09)."""
    env = os.environ.get("TRUST_POLICY_PUBKEY", "").strip()
    if env:
        return Path(env).expanduser().read_text(encoding="utf-8")
    return (Path(__file__).resolve().parent / "trust_agent_policy_pubkey.pem").read_text(
        encoding="utf-8"
    )


def _load_jti_cache() -> set[str]:
    try:
        data = json.loads(_jti_cache_path().read_text(encoding="utf-8"))
        return {str(x) for x in data[-_JTI_CACHE_LIMIT:]}
    except (OSError, ValueError):
        return set()


def _remember_jti(jti: str) -> None:
    try:
        cache = sorted(_load_jti_cache() | {jti})
        _atomic_write_json(_jti_cache_path(), cache[-_JTI_CACHE_LIMIT:])
    except OSError:
        pass


def verify_bundle(jws: str, *, device_id: str, remember: bool = True) -> dict[str, Any]:
    """Verifica el JWS contra la clave pineada. Lanza ``BundleRejected``.

    Controles (REQ-GOH-003): algoritmo RS256 pinneado (HS256 rechazado),
    firma, ``aud == device_id`` local, ``exp`` no vencido, claims completos
    y ``jti`` no repetido (anti-replay con caché local).

    ``remember=False`` para re-verificar el bundle YA cacheado (su jti es el
    "current", no un replay — el cacheado se re-verifica en cada evaluación y
    una verificación repetida del mismo artefacto NO es un ataque de replay;
    el anti-replay estricto aplica al bundle PRESENTADO COMO NUEVO por el
    sync). Sin esto, la segunda evaluación del caché lo rechazaría como
    replay y la gracia offline (REQ-GOH-009/018) se rompería.
    """
    import jwt  # PyJWT 2.13 — disponible en el venv de Hermes (verificado)

    if not isinstance(jws, str) or jws.count(".") != 2:
        raise BundleRejected("malformed JWS")
    try:
        header = jwt.get_unverified_header(jws)
    except jwt.PyJWTError as exc:
        raise BundleRejected(f"unreadable header: {exc}")
    if header.get("alg") != "RS256":
        # HS256 prohibido (constitution IV); cualquier otro algoritmo también.
        raise BundleRejected(f"algorithm {header.get('alg')!r} not allowed (RS256 only)")

    pubkey = pinned_public_key_pem()
    try:
        payload = jwt.decode(
            jws,
            pubkey,
            algorithms=["RS256"],  # pinneado: nunca aceptamos alg del header
            audience=device_id,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError:
        raise BundleRejected("bundle expired (offline grace consumed)")
    except jwt.InvalidAudienceError:
        raise BundleRejected("bundle audience does not match this device")
    except jwt.MissingRequiredClaimError as exc:
        raise BundleRejected(f"missing claim: {exc.claim}")
    except jwt.PyJWTError as exc:
        raise BundleRejected(f"invalid signature or claims: {exc}")

    jti = str(payload.get("jti", ""))
    if remember:
        if jti in _load_jti_cache():
            raise BundleRejected("replayed bundle (jti already seen)")
        _remember_jti(jti)
        # Spec 019 D-GOH-11 (REQ-GOH-022): warning del contrato de schema al
        # aplicar un bundle NUEVO (el caché se re-verifica con remember=False
        # en cada evaluación). Keys desconocidas / schema mayor al soportado:
        # se ignoran con warning, JAMÁS relajan nada.
        policy_contract.note_schema(payload, log_event)
    return payload


# ---------------------------------------------------------------------------
# Sync (UN intento acotado; el núcleo v2 programa el periódico)
# ---------------------------------------------------------------------------
def _sync_bundle(device: dict[str, str]) -> str | None:
    """GET /api/me/governance-bundle → JWS compacto o None (error de red).

    401/403 levantan ``_Unauthorized`` para que el caller aplique fail-closed
    inmediato (REQ-GOH-010) — distinto de la degradación de red (gracia).
    """
    import ssl

    url = f"{enroll.api_base()}/api/me/governance-bundle"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {device['token']}",
            "X-Device-Token": device["token"],
        },
    )
    ctx = ssl.create_default_context()  # verificación SIEMPRE (C6)
    try:
        with urllib.request.urlopen(req, timeout=10.0, context=ctx) as resp:
            data = json.loads(resp.read() or b"{}")
            return str(data.get("bundle") or "")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise _Unauthorized(f"HTTP {exc.code}") from None
        return None  # 5xx/etc: degradación de red — gracia offline si aplica
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# GovernanceState — máquina de estados por proceso (D-HRB-05)
# ---------------------------------------------------------------------------
class GovernanceState:
    """Modo operativo evaluado por llamada (hook) con caché en disco.

    ``evaluate()`` es puro-efecto-consultivo: nunca lanza; ante cualquier
    fallo el modo es RESTRICTED (fail-closed, jamás fail-open).
    """

    def __init__(self) -> None:
        self.mode: str = "restricted"
        self.cause: str = "no valid governance bundle"
        self.payload: dict[str, Any] | None = None
        self._last_sync_attempt: float = 0.0
        self._sync_backoff: float = 1.0  # 1 s → 60 s (REQ-GOH-018)
        self._auth_failure: bool = False
        self._last_unbind_mode: str = "readonly"  # del último bundle firmado

    # -- evaluación ---------------------------------------------------------
    def evaluate(self, *, force_sync: bool = False) -> dict[str, Any]:
        """Refresca el estado si hace falta y lo retorna
        ``{mode, cause, payload}``."""
        device = enroll.load_device_token()
        if device is None or not device.get("token"):
            self._set("restricted", "device not enrolled (no device token)")
            return self._snapshot()

        # D-HRB-03 amend (spec 022 v1.1.0): token expirado → causa honesta
        # (NO es revocación). El BundleSyncer de este mismo proceso intenta
        # la recuperación (re-enroll con id_token fresco); mientras tanto el
        # agente queda restringido (fail-closed, jamás fail-open).
        if enroll.device_token_expired(device):
            self._set(
                "restricted",
                "device token expired — re-run trust-login to re-enroll",
            )
            return self._snapshot()

        cache = _bundle_cache_path()
        jws = None
        if cache.exists():
            try:
                jws = cache.read_text(encoding="utf-8").strip() or None
            except OSError:
                jws = None

        payload = self._try_verify(jws, device["device_id"])
        if payload is not None:
            self._auth_failure = False
            self._set("normal", "", payload)
            self._sync_backoff = 1.0
            return self._snapshot()

        # Sin bundle válido en caché: UN intento de sync con backoff.
        now = time.monotonic()
        if force_sync or (now - self._last_sync_attempt) >= self._sync_backoff:
            self._last_sync_attempt = now
            fresh = self._attempt_sync(device)
            if fresh is not None:
                self._auth_failure = False
                self._sync_backoff = 1.0
                self._set("normal", "", fresh)
                return self._snapshot()
            self._sync_backoff = min(60.0, self._sync_backoff * 2.0)

        if self._auth_failure:
            cause = "device not authorized by the organization"
        elif jws is None:
            cause = "no valid governance bundle"
        else:
            cause = "governance bundle expired, network sync unavailable"
        self._set("restricted", cause)
        return self._snapshot()

    def _try_verify(self, jws: str | None, device_id: str) -> dict[str, Any] | None:
        if not jws:
            return None
        try:
            # remember=False: es el bundle CACHEADO (jti "current") — una
            # re-verificación repetida no es replay (ver verify_bundle).
            payload = verify_bundle(jws, device_id=device_id, remember=False)
            self._last_unbind_mode = str(payload.get("unbind_mode") or "readonly")
            return payload
        except BundleRejected as exc:
            log_event("bundle_rejected", reason=exc.reason)
            # D-GOH-05: el unbind_mode del ÚLTIMO bundle firmado decide el
            # grado del modo restringido. Si la firma sigue siendo válida
            # (p. ej. solo expiró), la extraemos tolerando exp.
            if "expired" in str(exc):
                try:
                    import jwt

                    claims = jwt.decode(
                        jws,
                        pinned_public_key_pem(),
                        algorithms=["RS256"],
                        audience=device_id,
                        options={"verify_exp": False},
                    )
                    self._last_unbind_mode = str(claims.get("unbind_mode") or "readonly")
                except Exception:  # noqa: BLE001 - best-effort, fail a readonly
                    pass
            return None

    def _attempt_sync(self, device: dict[str, str]) -> dict[str, Any] | None:
        """Un sync; retorna el payload verificado o None (restricted)."""
        try:
            jws = _sync_bundle(device)
        except _Unauthorized as exc:
            # 401/403: desligado/revocado → fail-closed INMEDIATO sin gracia
            # (REQ-GOH-010): se invalida la caché y se audita localmente.
            self._auth_failure = True
            try:
                _bundle_cache_path().unlink(missing_ok=True)
            except OSError:
                pass
            log_event("device_unauthorized", detail=str(exc))
            self.payload = None
            return None
        if not jws:
            log_event("sync_unavailable", backoff_s=self._sync_backoff)
            return None
        try:
            payload = verify_bundle(jws, device_id=device["device_id"])
        except BundleRejected as exc:
            log_event("bundle_rejected_at_sync", reason=exc.reason)
            return None
        try:  # caché compartida con el núcleo v2 (contrato del archivo .jws)
            tmp = _bundle_cache_path().with_suffix(".jws.tmp")
            tmp.write_text(jws, encoding="utf-8")
            tmp.replace(_bundle_cache_path())
            log_event(
                "bundle_synced",
                policy_version=payload.get("policy_version"),
                expires_at=payload.get("exp"),
            )
        except OSError:
            pass
        return payload

    def _set(self, mode: str, cause: str, payload: dict[str, Any] | None = None) -> None:
        if mode != self.mode:
            log_event("mode_change", mode=mode, cause=cause)
        self.mode, self.cause, self.payload = mode, cause, payload

    def _snapshot(self) -> dict[str, Any]:
        return {"mode": self.mode, "cause": self.cause, "payload": self.payload}

    # -- hooks ---------------------------------------------------------------
    def on_pre_tool_call(self, tool_name: str = "", args: dict | None = None, **_: Any):
        """Veto (REQ-GOH-005/009/010). Retorna el dict de acción o None.

        El core invoca el hook con ``tool_name=`` (verificado en
        ``hermes_cli/plugins.py::_get_pre_tool_call_directive_details``).
        """
        state = self.evaluate()
        tool = (tool_name or "").strip()
        log_event("veto_eval", tool=tool, mode=state["mode"], cause=state["cause"])
        if tool in INTERNAL_TOOLS:
            return None  # siempre permitidas, incluso en restricted
        if state["mode"] == "restricted":
            self._count_veto(tool, state["cause"])
            return {"action": "block", "message": _RESTRICTED_MSG.format(cause=state["cause"])}
        allowed = list(state["payload"].get("allowed_tools") or [])
        if tool not in allowed:
            self._count_veto(tool, "not in allowed_tools")
            return {
                "action": "block",
                "message": _BLOCK_MSG.format(tool=tool, allowed=", ".join(allowed) or "none"),
            }
        # REQ-PE-011/012 (spec 042, GAP-42-A): enforcement cross-surface de
        # allowed_connectors. Las tools de conector se nombran
        # ``connectors__<slug>__<tool>`` (tools/connectors/gateway/names.py).
        # Veto si el slug del conector no está en allowed_connectors, leído
        # SIEMPRE vía policy_value() (deny-by-default: key ausente = []).
        if tool.startswith("connectors__") and tool != "connectors__execute":
            parts = tool.split("__", 2)
            if len(parts) == 3 and parts[1]:
                connector_slug = parts[1]
                allowed_connectors = list(
                    policy_contract.policy_value(state["payload"], "allowed_connectors")
                )
                if connector_slug not in allowed_connectors:
                    self._count_veto(tool, f"connector '{connector_slug}' not in allowed_connectors")
                    return {
                        "action": "block",
                        "message": (
                            f"BLOCKED by organization policy: connector "
                            f"'{connector_slug}' is not allowed. Allowed "
                            f"connectors: {', '.join(allowed_connectors) or 'none'}."
                        ),
                    }
        return None

    def on_pre_llm_call(self, **_: Any):
        """Aviso de modo restringido por turno (REQ-GOH-009/010).

        En modo normal retorna None: el contexto de gobernanza normal lo
        inyecta el núcleo v2 (spec 019 Inc. 1) — el core concatena los
        contextos de todos los hooks, sin duplicación.
        """
        state = self.evaluate()
        if state["mode"] != "restricted":
            return None
        if self._last_unbind_mode == "lock":
            return {"context": _LOCK_CONTEXT}
        return {"context": _RESTRICTED_CONTEXT.format(cause=state["cause"])}

    def _count_veto(self, tool: str, reason: str) -> None:
        """Conteo por tool para la attestation futura (REQ-GOH-017)."""
        log_event("tool_vetoed", tool=tool, reason=reason)
        try:
            path = _veto_counts_path()
            counts: dict[str, int] = {}
            if path.exists():
                counts = json.loads(path.read_text(encoding="utf-8"))
            counts[tool] = int(counts.get(tool, 0)) + 1
            _atomic_write_json(path, counts)
        except (OSError, ValueError):
            pass


STATE = GovernanceState()  # singleton por proceso


def register(ctx) -> None:
    """Registra los hooks de enforcement (API pública de plugins)."""
    ctx.register_hook("pre_tool_call", STATE.on_pre_tool_call)
    ctx.register_hook("pre_llm_call", STATE.on_pre_llm_call)

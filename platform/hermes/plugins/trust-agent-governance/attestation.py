"""attestation.py — attestation del agente al control plane (spec 019 Inc. 3).

REQ-GOH-012 (D-GOH-06/ADR-018): al arrancar una sesión (``on_session_start``,
catálogo oficial de hooks — verificado en ``hermes_cli/plugins_dispatch.py``)
el plugin reporta al control plane::

    POST /api/devices/{device_id}/attestation
    {"bundle_hash": "<sha256 del bundle JWS vigente>",
     "policy_version": <int|null>, "mode": "normal|restricted",
     "veto_counts": {"<tool>": <intentos bloqueados desde la última attestation>}}

El control plane lo registra en el audit del tenant (``device.attestation``)
y actualiza ``devices.last_attestation_at``. Sin esto, un agente desgobernado
(plugin deshabilitado localmente) es indetectable: la AUSENCIA de
attestation es la señal de detección (job de overdue, REQ-GOH-013).

Detalles honestos del contrato:
  - ``bundle_hash`` = sha256(hex) del JWS cacheado vigente (el artefacto
    firmado que este agente considera autoridad). Sin caché válida → null
    (modo restringido: no hay bundle que atestiguar — y eso SE reporta).
  - ``veto_counts`` = conteo por tool desde la última attestation EXITOSA.
    Fuentes: ``veto-counts.json`` (lo escribe ``enforcement._count_veto``,
    REQ-GOH-017) + ``state.veto_counts`` (núcleo v2). Tras un envío
    exitoso el conteo se consume (drenaje) — la próxima attestation parte
    de cero.
  - 401/403 → fail-closed inmediato, misma semántica que el sync y el
    heartbeat (REQ-GOH-010): desligado/revocado no consume gracia.
  - Error de red → best-effort: se reintenta en la próxima sesión/arranque
    (el conteo de vetos NO se consume, así nada se pierde).

Cero parches al core (REQ-GOH-016): urllib del venv de Hermes, sin imports
de ``hermes_cli``. Cableado aditivo desde ``__init__.py::register(ctx)``.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import bundle_client, state as governance_state

_HTTP_TIMEOUT = 10.0
# Arranque: margen para que el primer sync del BundleSyncer (tick inmediato
# en su hilo daemon) deje un bundle fresco antes de atestiguar.
_STARTUP_DELAY_SECONDS = 1.5


class AttestationError(Exception):
    """La attestation no pudo enviarse (best-effort: se reintenta luego)."""


class UnauthorizedAttestation(Exception):
    """El control plane respondió 401/403 (desligado/revocado, REQ-GOH-010)."""


# ---------------------------------------------------------------------------
# Datos locales que viajan en la attestation
# ---------------------------------------------------------------------------
def bundle_sha256() -> str | None:
    """sha256 (hex) del bundle JWS cacheado vigente; None sin caché."""
    cache = bundle_client.bundle_cache_path()
    try:
        jws = cache.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not jws:
        return None
    return hashlib.sha256(jws.encode("utf-8")).hexdigest()


def _veto_counts_path() -> Path:
    # Mismo contrato de archivo que enforcement._veto_counts_path (spec 022):
    # $HERMES_HOME/trust-agent/veto-counts.json. Duplicado a propósito —
    # cada módulo del plugin declara su contrato de archivo sin acoplarse
    # a imports de lanes hermanas (D-HRB-03).
    return bundle_client.trust_dir() / "veto-counts.json"


def collect_veto_counts() -> dict[str, int]:
    """Conteo por tool desde la última attestation (fusión de ambas fuentes)."""
    counts: dict[str, int] = {}
    try:
        stored = json.loads(_veto_counts_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            for tool, count in stored.items():
                counts[str(tool)] = counts.get(str(tool), 0) + int(count)
    except (OSError, ValueError, TypeError):
        pass
    for tool, count in (governance_state.get_state().snapshot().get("veto_counts") or {}).items():
        counts[str(tool)] = counts.get(str(tool), 0) + int(count)
    return counts


def collect_capability_findings() -> dict[str, Any]:
    """Findings del enforcement batch 2 (t_a868974a): desvíos config-vs-policy
    y pisos (retención/checkpoints) leídos del contrato compartido
    ``capability-findings.json`` (lo escribe ``capability_enforcement``).
    Sin el módulo → dict vacío (canal opcional, retrocompatible)."""
    try:
        data = json.loads(
            (bundle_client.trust_dir() / "capability-findings.json").read_text(
                encoding="utf-8"
            )
        )
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def consume_capability_findings() -> None:
    """Tras una attestation EXITOSA los warnings parten de cero; el ``status``
    lo reescribe la próxima evaluación de la lane batch 2."""
    try:
        data = collect_capability_findings()
        data["warnings"] = {}
        tmp = (bundle_client.trust_dir() / "capability-findings.json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(bundle_client.trust_dir() / "capability-findings.json")
    except OSError:
        pass  # best-effort: un finding re-enviado jamás es peor que perderlo


def consume_veto_counts() -> None:
    """Tras una attestation EXITOSA el conteo parte de cero (REQ-GOH-012)."""
    try:
        tmp = _veto_counts_path().with_suffix(".json.tmp")
        tmp.write_text("{}", encoding="utf-8")
        tmp.replace(_veto_counts_path())
    except OSError:
        pass  # best-effort: un conteo re-enviado jamás es peor que perderlo
    governance_state.get_state().drain_veto_counts()


# ---------------------------------------------------------------------------
# Envío (POST /api/devices/{id}/attestation — auth del bundle, §5.5)
# ---------------------------------------------------------------------------
def post_attestation(
    device: dict[str, str],
    *,
    bundle_hash: str | None,
    policy_version: int | None,
    mode: str,
    veto_counts: dict[str, int],
    capability_findings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """UN POST de attestation. Lanza ``UnauthorizedAttestation`` en 401/403.

    ``capability_findings`` (batch 2, t_a868974a): desvíos config-vs-policy y
    pisos de retención/checkpoints. Opcional/retrocompatible — un plugin sin
    el batch 2 no lo manda y el endpoint aplica el default {}."""
    body = json.dumps(
        {
            "bundle_hash": bundle_hash,
            "policy_version": policy_version,
            "mode": mode if mode in ("normal", "restricted") else "restricted",
            "veto_counts": veto_counts,
            "capability_findings": capability_findings or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{bundle_client.api_base()}/api/devices/{device['device_id']}/attestation",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {device['token']}",
            "X-Device-Token": device["token"],
            "Content-Type": "application/json",
        },
    )
    import ssl

    ctx = ssl.create_default_context()  # verificación SIEMPRE (C6)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise UnauthorizedAttestation(f"HTTP {exc.code}") from None
        raise AttestationError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AttestationError(str(exc)) from None
    if not isinstance(data, dict):
        raise AttestationError("attestation response is not an object")
    return data


def send_attestation(trigger: str = "manual") -> dict[str, Any] | None:
    """Compone y envía UNA attestation. Nunca lanza (best-effort).

    Retorna la respuesta del control plane (dict) o None si no se envió
    (sin device token / error de red). El conteo de vetos SOLO se consume
    tras un envío exitoso.
    """
    device = bundle_client.load_device_token()
    if device is None or not device.get("token") or not device.get("device_id"):
        bundle_client.log_event("attestation_skipped", trigger=trigger,
                                reason="device not enrolled")
        return None
    if bundle_client.device_token_expired(device):
        # An expired token answers 401, which this module (correctly) treats
        # as revocation — so sending it would wipe the signed bundle and
        # report "revoked" for a desktop that was merely closed longer than
        # the TTL. The BundleSyncer refreshes the token; the next session
        # attests (best-effort, counts are not consumed).
        bundle_client.log_event("attestation_skipped", trigger=trigger,
                                reason="device token expired")
        return None

    snapshot = governance_state.get_state().snapshot()
    bundle = snapshot.get("bundle") or {}
    veto_counts = collect_veto_counts()
    capability_findings = collect_capability_findings()
    try:
        resp = post_attestation(
            device,
            bundle_hash=bundle_sha256(),
            policy_version=bundle.get("policy_version"),
            mode=snapshot.get("mode", "restricted"),
            veto_counts=veto_counts,
            capability_findings=capability_findings,
        )
    except UnauthorizedAttestation as exc:
        # Desligado/revocado: fail-closed inmediato, sin gracia (REQ-GOH-010,
        # misma semántica que el sync y el heartbeat).
        try:
            bundle_client.bundle_cache_path().unlink(missing_ok=True)
        except OSError:
            pass
        bundle_client.log_event("device_unauthorized", detail=f"attestation {exc}")
        st = governance_state.get_state()
        st.enter_restricted(governance_state.REASON_REVOKED)
        st.record_sync_error(f"attestation: {exc}")
        return None
    except AttestationError as exc:
        # Best-effort: el conteo NO se consume; la próxima sesión reintenta.
        bundle_client.log_event("attestation_unavailable", trigger=trigger,
                                detail=str(exc))
        return None

    consume_veto_counts()
    consume_capability_findings()
    bundle_client.log_event(
        "attestation_sent",
        trigger=trigger,
        bundle_hash=resp.get("bundle_hash", bundle_sha256()),
        policy_version=bundle.get("policy_version"),
        mode=snapshot.get("mode"),
        veto_counts=veto_counts,
        escalated_tools=resp.get("escalated_tools") or [],
    )
    return resp


def _send_in_thread(trigger: str) -> None:
    """Envía la attestation en un hilo daemon (los hooks son timeout-bounded:
    on_session_start corre síncrono en el camino de sesión del core y una
    llamada de red de 10 s no puede bloquearlo)."""
    threading.Thread(
        target=send_attestation,
        args=(trigger,),
        name="trust-governance-attestation",
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# register(ctx) — cableado aditivo desde __init__.py (API pública de plugins)
# ---------------------------------------------------------------------------
def register(ctx) -> None:
    """Registra la attestation por sesión (REQ-GOH-012).

    - ``on_session_start``: cada sesión nueva del agente atestigua (D-GOH-06:
      attestation POR SESIÓN). Envío en hilo daemon (hook bounded).
    - Arranque (one-shot): el flujo gateway/-z ensambla la sesión ANTES de
      registrar los plugins de usuario (hallazgo 1 del Incremento 1), así que
      el primer ``on_session_start`` puede dispararse antes de este register;
      el one-shot cubre ese caso y atestigua el arranque del agente aunque
      no haya capturado el hook.
    """
    def _on_session_start(**_: Any) -> None:
        _send_in_thread("session_start")

    ctx.register_hook("on_session_start", _on_session_start)

    def _startup() -> None:
        time.sleep(_STARTUP_DELAY_SECONDS)
        send_attestation("startup")

    threading.Thread(
        target=_startup,
        name="trust-governance-attestation-startup",
        daemon=True,
    ).start()

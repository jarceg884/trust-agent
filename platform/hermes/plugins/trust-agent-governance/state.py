"""Estado operativo del plugin de gobernanza (spec 019 §5.4, ADR-016/017).

Máquina de estados expuesta como módulo para que TODOS los componentes del
plugin (y los módulos de los incrementos siguientes: ``enforcement.py`` del
veto ``pre_tool_call``, ``enroll.py`` del enrolamiento) compartan UNA sola
verdad sobre el bundle vigente y el modo operativo — nadie reimplementa la
verificación.

Modos:
  - ``normal``     : bundle firmado vigente; la superficie del agente se
                     rige por ``allowed_tools`` del bundle.
  - ``restricted`` : fail-closed (SIN_BUNDLE ≡ restricted desde el primer
                     turno). Razones: ``no_bundle`` (sin caché válida),
                     ``expired`` (gracia offline agotada sin sync),
                     ``revoked`` (401/403 del control plane — desligado,
                     inmediato, REQ-GOH-010), ``invalid_bundle`` (fallo de
                     verificación sin bundle anterior).

El veto real de tools y la inyección del aviso de modo restringido son del
Incremento 2 (REQ-GOH-005/009/010); este módulo mantiene el estado de modo
que aquellos consumirán (preparación exigida por la tarea del Incremento 1).

Thread-safe: el sync corre en un hilo daemon mientras los hooks se disparan
desde los hilos del agente.
"""
from __future__ import annotations

import threading
import time
from typing import Any

MODE_NORMAL = "normal"
MODE_RESTRICTED = "restricted"

REASON_NO_BUNDLE = "no_bundle"
REASON_EXPIRED = "expired"
REASON_REVOKED = "revoked"
REASON_INVALID = "invalid_bundle"
# D-HRB-03 amend (spec 022 v1.1.0, REQ-HRB-010b): el device token caducó
# (agente apagado > TTL) — NO es una revocación. El BundleSyncer intenta la
# recuperación (re-enroll con id_token fresco); si no puede, el agente queda
# restringido con ESTA razón honesta. El bundle vigente NO se descarta (no
# fue invalidado por la org): al recuperar, el modo vuelve a normal.
REASON_TOKEN_EXPIRED = "token_expired"

# Siempre permitidas: son la superficie de auto-reporte del propio plugin
# (REQ-GOH-005: "governance_status siempre permitida").
PLUGIN_INTERNAL_TOOLS = frozenset({"governance_status"})


class GovernanceState:
    """Estado operativo del plugin (singleton vía :func:`get_state`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mode: str = MODE_RESTRICTED
        self.reason: str = REASON_NO_BUNDLE
        self.payload: dict[str, Any] | None = None
        self.last_sync_at: float | None = None
        self.last_sync_error: str | None = None
        # Conteo de vetos por tool desde la última attestation (REQ-GOH-012/017;
        # el consumo es del Incremento 2/3 — aquí se lleva el contador).
        self.veto_counts: dict[str, int] = {}

    # -- transiciones --------------------------------------------------------
    def apply_bundle(self, payload: dict[str, Any]) -> None:
        """Bundle verificado → modo NORMAL (§5.4)."""
        with self._lock:
            self.payload = payload
            self.mode = MODE_NORMAL
            self.reason = ""
            self.last_sync_at = time.time()
            self.last_sync_error = None
        self._persist_status_file()

    def enter_restricted(self, reason: str) -> None:
        """Fail-closed: modo RESTRINGIDO (sin gracia cuando reason=revoked)."""
        with self._lock:
            self.mode = MODE_RESTRICTED
            self.reason = reason
            if reason in (REASON_REVOKED, REASON_NO_BUNDLE, REASON_INVALID):
                # Desligado/revocado o sin bundle: la caché deja de ser
                # autoridad (REQ-GOH-010 la invalida; sin caché no hay nada).
                self.payload = None
        self._persist_status_file()

    def record_sync_error(self, message: str) -> None:
        with self._lock:
            self.last_sync_error = message
        self._persist_status_file()

    def record_veto(self, tool: str) -> None:
        with self._lock:
            self.veto_counts[tool] = self.veto_counts.get(tool, 0) + 1

    def drain_veto_counts(self) -> dict[str, int]:
        """Consume y retorna el conteo de vetos (spec 019 Inc. 3, REQ-GOH-012):
        tras una attestation EXITOSA el conteo parte de cero."""
        with self._lock:
            drained = dict(self.veto_counts)
            self.veto_counts = {}
            return drained

    # -- lecturas ------------------------------------------------------------
    def grace_remaining_seconds(self, now: float | None = None) -> int | None:
        """Segundos de gracia offline restantes del bundle vigente (None sin bundle)."""
        with self._lock:
            if not self.payload:
                return None
            now = time.time() if now is None else now
            return max(0, int(self.payload.get("exp", 0) - now))

    def is_normal(self) -> bool:
        with self._lock:
            return self.mode == MODE_NORMAL

    def snapshot(self) -> dict[str, Any]:
        """Vista inmutable del estado (tool governance_status / pre_llm_call)."""
        with self._lock:
            payload = dict(self.payload) if self.payload else None
            return {
                "mode": self.mode,
                "reason": self.reason,
                "bundle": payload,
                "last_sync_at": self.last_sync_at,
                "last_sync_error": self.last_sync_error,
                "grace_remaining_seconds": (
                    max(0, int(payload.get("exp", 0)) - time.time())
                    if payload else None
                ),
                "veto_counts": dict(self.veto_counts),
            }

    def _persist_status_file(self) -> None:
        """Best-effort mirror of :meth:`snapshot` to ``$HERMES_HOME/trust-agent/
        governance_status.json`` (same dir/atomic-write convention as
        ``capability-findings.json`` / ``veto-counts.json``) so a surface
        OUTSIDE the agent process -- Trust Agent Desktop's Settings UI, read
        by Electron's main process, never the renderer directly -- can show
        live governance state without going through a chat turn. Lazy import:
        ``bundle_client`` already imports this module, so a top-level import
        here would cycle.
        """
        try:
            from . import bundle_client  # local import: breaks the cycle

            data = self.snapshot()
            trust_dir = bundle_client.trust_dir()
            tmp = trust_dir / "governance_status.json.tmp"
            tmp.write_text(_json_dumps(data), encoding="utf-8")
            tmp.replace(trust_dir / "governance_status.json")
        except OSError:
            pass  # best-effort: a stale/missing status file never blocks enforcement


def _json_dumps(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, indent=2, default=str)


_STATE = GovernanceState()


def get_state() -> GovernanceState:
    """Singleton de estado del plugin (API estable para enforcement/enroll)."""
    return _STATE


def reset_state() -> GovernanceState:
    """Solo para tests: reinstala un estado limpio."""
    global _STATE
    _STATE = GovernanceState()
    return _STATE

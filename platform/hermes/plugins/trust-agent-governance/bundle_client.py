"""bundle_client.py — cliente canónico del governance bundle (spec 019 Inc. 1).

Núcleo v2 del plugin ``trust-agent-governance``: carga del último bundle
cacheado, verificación de firma (RS256 pineado; HS256 prohibido), sync contra
``GET /api/me/governance-bundle`` en arranque y periódico, y aplicación del
resultado a la máquina de estados compartida (:mod:`state`).

Contratos de archivo (compartidos con los módulos de la spec 022 — D-HRB-03):
  - ``$HERMES_HOME/trust-agent/device-token.json``  — almacén canónico del
    device token (``{device_id, token}``); el env ``TRUST_DEVICE_TOKEN`` es
    fallback sin device_id (entonces aud NO es verificable → fail-closed).
  - ``$HERMES_HOME/trust-agent/governance-bundle.jws`` — caché del último
    bundle firmado; quien sincroniza escribe (escritura atómica).
  - ``$HERMES_HOME/trust-agent/bundle-jti.json`` — caché anti-replay PROPIA
    de este cliente (``{"seen": [...], "current": "<jti>"}``). Separada de la
    de ``enforcement.py`` (jti-cache.json): cada verificador mantiene su
    stream; el artefacto cacheado se re-verifica en arranque con
    ``remember=False`` (su jti ES el "current", no un replay).
  - ``$HERMES_HOME/trust-agent/plugin.log`` — log local append-only.

Verificación (REQ-GOH-003): firma RS256 contra la clave pública pineada
(``trust_agent_policy_pubkey.pem`` junto al plugin — dato público, D-GOH-09;
override de tests/rotación vía env ``TRUST_POLICY_PUBKEY``), ``aud ==
device_id`` local, ``exp`` no vencido, claims completos §5.2 y ``jti`` no
repetido. Cualquier fallo → ``BundleRejected``; el caller descarta el bundle
y mantiene el anterior vigente (o modo restringido).

Sync (REQ-GOH-004): en arranque (hilo daemon, primer tick inmediato) y cada
``TRUST_SYNC_INTERVAL`` s (default 300), con re-sync adelantado al
vencimiento del ``exp`` del bundle en caché. 401/403 del control plane =
desligado/revocado → ``UnauthorizedSync`` (fail-closed inmediato sin gracia,
REQ-GOH-010 — la transición la aplica el BundleSyncer). Error de red =
degradación: gracia offline mientras el bundle siga vigente (D-GOH-04).

Heartbeat de presencia (spec 027 §4 — seam con t_f4f99970, mandato Miller):
cada ciclo de sync hace también ``POST /api/me/device-heartbeat`` reportando
la ``policy_version`` APLICADA, el uptime del agente y el modo operativo.
Si el control plane responde ``policy_update_available: true`` → re-baja del
bundle JWS en el MISMO ciclo (propagación en vivo sin reiniciar ni
re-enrolar, D-027-03). El intervalo recomendado del servidor
(``heartbeat_interval_seconds``) se adopta cuando el operador NO fijó
``TRUST_SYNC_INTERVAL`` (autoajuste D-027-02). El heartbeat es best-effort
sobre el sync: un heartbeat que no llega NO cambia el estado de gobernanza
(solo el sync del bundle y sus 401/403 gobiernan las transiciones, salvo
la revocación detectada por el propio heartbeat → fail-closed inmediato).

Cero parches al core (REQ-GOH-016): urllib + PyJWT del venv de Hermes, sin
imports de ``hermes_cli``.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import policy_contract
from . import state as governance_state

DEFAULT_SYNC_INTERVAL_SECONDS = 300
_SYNC_INTERVAL_FLOOR = 5  # piso defensivo: un sync por turno de reloj como mucho
_JTI_LIMIT = 256
_HTTP_TIMEOUT = 10.0

# Claims obligatorios del payload §5.2 (el endpoint los garantiza; el
# verificador los EXIGE — un bundle sin ellos no es confiable).
REQUIRED_CLAIMS = (
    "iss", "aud", "exp", "iat", "jti", "tenant_id", "user_id", "device_id",
    "policy_version", "allowed_tools", "allowed_connectors",
    "approved_models", "budget", "offline_grace_minutes", "unbind_mode",
    "attestation_overdue_hours",
)


class BundleRejected(Exception):
    """Bundle inválido/manipulado/replay — NUNCA se aplica (REQ-GOH-003)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class UnauthorizedSync(Exception):
    """El control plane respondió 401/403 (desligado/revocado, REQ-GOH-010)."""


# ---------------------------------------------------------------------------
# Entorno (contratos compartidos, implementación local — sin acoplamiento a
# módulos de otras lanes; el FORMATO de los archivos es el contrato).
# ---------------------------------------------------------------------------
def hermes_home() -> Path:
    # Context-aware (spec 030 D-030-09, mismo fix que enroll.hermes_home): en
    # un gateway multi-perfil el env del proceso es el home del gateway, NO el
    # del perfil servido — el override context-local de hermes_constants es el
    # que lleva el perfil de la sesión. Fallback a env/default para tests
    # standalone sin hermes_constants importable.
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        env = os.environ.get("HERMES_HOME")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".hermes"


def _named_profile_root(home: Path) -> Path | None:
    """``<root>`` cuando *home* es un perfil con nombre ``<root>/profiles/<name>``
    (predicado del core; forma del path fuera del runtime)."""
    try:
        from hermes_constants import named_profile_home  # type: ignore

        named = named_profile_home(home)
        return Path(named).parent.parent if named is not None else None
    except Exception:  # noqa: BLE001
        if home.parent.name == "profiles" and not home.name.startswith("."):
            return home.parent.parent
        return None


def device_home() -> Path:
    """Home que porta el ENROLAMIENTO del dispositivo en el contexto actual —
    mismo algoritmo que ``enroll.device_home`` (ver su docstring: un perfil
    del escritorio sin enrolamiento propio usa la identidad, el bundle y el
    estado del dispositivo; un perfil enrolado aparte conserva los suyos)."""
    home = hermes_home()
    if (home / "trust-agent" / "device-token.json").is_file():
        return home
    root = _named_profile_root(home)
    if root is not None and (root / "trust-agent" / "device-token.json").is_file():
        return root
    return home


def trust_dir() -> Path:
    d = device_home() / "trust-agent"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # aquí viven secretos del dispositivo
    except OSError:
        pass
    return d


def api_base() -> str:
    return os.environ.get("TRUST_AGENT_API", "http://127.0.0.1:8000").rstrip("/")


def bundle_cache_path() -> Path:
    return trust_dir() / "governance-bundle.jws"


def _plugin_log_path() -> Path:
    return trust_dir() / "plugin.log"


def load_device_token() -> dict[str, str] | None:
    """Almacén canónico device-token.json > env TRUST_DEVICE_TOKEN."""
    path = trust_dir() / "device-token.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("token") and data.get("device_id"):
                return {
                    "device_id": str(data["device_id"]),
                    "token": str(data["token"]),
                }
        except (ValueError, OSError):
            pass
    env = os.environ.get("TRUST_DEVICE_TOKEN", "").strip()
    if env:
        return {"device_id": os.environ.get("TRUST_DEVICE_ID", "").strip(), "token": env}
    return None


def device_token_expired(device: dict[str, str], now: float | None = None) -> bool:
    """True si el claim ``exp`` del device token ya pasó (D-HRB-03 amend).

    Lee el claim SIN verificar firma — solo enruta la máquina de estados
    local (decidir si intentar la recuperación antes de golpear el server);
    el control plane verifica firma/exp/revocación en cada request, así que
    una lectura local maliciosa no relaja nada (fail-closed server-side).
    Token ilegible (env v1, garbage): False — que decida el server (su 401
    → revoked, camino ya existente).
    """
    try:
        import jwt  # PyJWT — disponible en el venv de Hermes

        claims = jwt.decode(
            device["token"],
            options={"verify_signature": False, "verify_exp": False},
        )
        exp = int(claims.get("exp", 0))
    except Exception:  # noqa: BLE001 - ilegible → el server decide
        return False
    return exp <= (time.time() if now is None else now)


def save_renewed_device_token(device: dict[str, str], new_token: str) -> None:
    """Persiste el device token RENOVADO (sliding renewal, REQ-HRB-010a).

    Preserva los campos del archivo existente (``enrolled_at``, ``org_code``,
    ``tenant_slug``) y añade ``renewed_at``. Escritura atómica 0600 (mismo
    contrato de archivo que enroll.save_device_token — el FORMATO es el
    contrato entre módulos).
    """
    path = trust_dir() / "device-token.json"
    payload: dict[str, Any] = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            # Keep EVERY field of the enrollment (org, model_key,
            # model_base_url, product_name…): the managed-config pin restores
            # the org-issued model credential from this file, so a renewal
            # that dropped them silently weakened the pin.
            payload.update(existing)
    except (OSError, ValueError):
        pass  # archivo ausente/corrupto: los campos opcionales se pierden,
        # el token renovado es lo que importa (device_id + token).
    payload.update({
        "device_id": device["device_id"],
        "token": new_token,
        "renewed_at": int(time.time()),
    })
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def log_event(event: str, **fields: Any) -> None:
    """Log local append-only del plugin (evidencia + debugging)."""
    try:
        entry = {"ts": int(time.time()), "event": event, **fields}
        with _plugin_log_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # el log JAMÁS tumba el turno


# ---------------------------------------------------------------------------
# Clave pública pineada (D-GOH-02/D-GOH-09)
# ---------------------------------------------------------------------------
def pinned_public_key_pem() -> str:
    """PEM público pineado junto al plugin (dato público).

    Env ``TRUST_POLICY_PUBKEY`` = ruta alternativa (tests / rotación vía
    profile distribution).
    """
    env = os.environ.get("TRUST_POLICY_PUBKEY", "").strip()
    if env:
        return Path(env).expanduser().read_text(encoding="utf-8")
    return (Path(__file__).resolve().parent / "trust_agent_policy_pubkey.pem").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Anti-replay de jti (caché local propia — ver docstring del módulo)
# ---------------------------------------------------------------------------
def _jti_store_path() -> Path:
    return trust_dir() / "bundle-jti.json"


def _load_jti_store() -> dict[str, Any]:
    try:
        data = json.loads(_jti_store_path().read_text(encoding="utf-8"))
        return {
            "seen": [str(x) for x in data.get("seen", [])],
            "current": str(data.get("current") or ""),
        }
    except (OSError, ValueError):
        return {"seen": [], "current": ""}


def _save_jti_store(store: dict[str, Any]) -> None:
    seen = sorted(set(store["seen"]))[-_JTI_LIMIT:]
    tmp = _jti_store_path().with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps({"seen": seen, "current": store["current"]}), encoding="utf-8"
        )
        tmp.replace(_jti_store_path())
    except OSError:
        pass  # best-effort: la firma/aud/exp siguen siendo los gates duros


# ---------------------------------------------------------------------------
# Verificación (REQ-GOH-003)
# ---------------------------------------------------------------------------
def verify_bundle(
    jws: str, *, device_id: str, remember: bool = True
) -> dict[str, Any]:
    """Verifica el JWS contra la clave pineada. Lanza ``BundleRejected``.

    Controles: algoritmo RS256 pineado (HS256 y cualquier otro: rechazados),
    firma, ``aud == device_id``, ``exp`` no vencido, claims completos §5.2 y
    ``jti`` no repetido. ``remember=False`` re-verifica el artefacto YA
    cacheado (su jti es el "current" del store, no un replay).
    """
    import jwt  # PyJWT — disponible en el venv de Hermes

    if not isinstance(jws, str) or jws.count(".") != 2:
        raise BundleRejected("malformed JWS")
    try:
        header = jwt.get_unverified_header(jws)
    except jwt.PyJWTError as exc:
        raise BundleRejected(f"unreadable header: {exc}") from None
    if header.get("alg") != "RS256":
        # HS256 prohibido (constitution IV); cualquier otro algoritmo también.
        raise BundleRejected(
            f"algorithm {header.get('alg')!r} not allowed (RS256 only)"
        )

    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],  # pineado: nunca aceptamos alg del header
        "audience": device_id,
        "options": {"require": list(REQUIRED_CLAIMS)},
    }
    pinned_issuer = os.environ.get("TRUST_POLICY_ISSUER", "").strip()
    if pinned_issuer:
        decode_kwargs["issuer"] = pinned_issuer
    try:
        payload = jwt.decode(jws, pinned_public_key_pem(), **decode_kwargs)
    except jwt.ExpiredSignatureError:
        raise BundleRejected("bundle expired (offline grace consumed)") from None
    except jwt.InvalidAudienceError:
        raise BundleRejected("bundle audience does not match this device") from None
    except jwt.InvalidIssuerError:
        raise BundleRejected("bundle issuer does not match the pinned issuer") from None
    except jwt.MissingRequiredClaimError as exc:
        raise BundleRejected(f"missing claim: {exc.claim}") from None
    except jwt.PyJWTError as exc:
        raise BundleRejected(f"invalid signature or claims: {exc}") from None

    jti = str(payload.get("jti", ""))
    store = _load_jti_store()
    if jti in store["seen"]:
        if remember or jti != store["current"]:
            raise BundleRejected("replayed bundle (jti already seen)")
    if remember:
        store["seen"] = store["seen"] + [jti]
        store["current"] = jti
        _save_jti_store(store)
        # Spec 019 D-GOH-11 (REQ-GOH-022): warning del contrato de schema
        # SOLO al aplicar un bundle NUEVO (el caché se re-verifica en cada
        # evaluación con remember=False y no debe spamear el log). Keys
        # desconocidas / schema mayor: se ignoran, JAMÁS relajan nada.
        policy_contract.note_schema(payload, log_event)
    return payload


# ---------------------------------------------------------------------------
# Caché en disco (REQ-GOH-004)
# ---------------------------------------------------------------------------
def load_cached_bundle(device_id: str) -> dict[str, Any] | None:
    """Último bundle cacheado, verificado; None si no hay caché válida."""
    cache = bundle_cache_path()
    if not cache.exists():
        return None
    try:
        jws = cache.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not jws:
        return None
    try:
        return verify_bundle(jws, device_id=device_id, remember=False)
    except BundleRejected as exc:
        log_event("cached_bundle_rejected", reason=exc.reason)
        return None


def _write_bundle_cache(jws: str) -> None:
    tmp = bundle_cache_path().with_suffix(".jws.tmp")
    tmp.write_text(jws, encoding="utf-8")
    tmp.replace(bundle_cache_path())


# Botón «Sincronizar política» del desktop (spec 045, seguimiento): Electron escribe
# ``trust-agent/sync-request.json`` ({"requested_at": epoch}) y el bucle de sync despierta.
SYNC_REQUEST_FILE = "sync-request.json"
_SYNC_REQUEST_POLL_SECONDS = 2.0


def sync_requested_at() -> float:
    """Cuándo pidió el desktop el último sync manual (0 si nunca o si el archivo no es válido)."""
    try:
        data = json.loads((trust_dir() / SYNC_REQUEST_FILE).read_text(encoding="utf-8"))
        return float(data.get("requested_at") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


# ---------------------------------------------------------------------------
# Sync (REQ-GOH-001/004; 401/403 → REQ-GOH-010)
# ---------------------------------------------------------------------------
def sync_once(device: dict[str, str]) -> dict[str, Any]:
    """UN sync contra el control plane. Retorna el payload VERIFICADO.

    Lanza ``UnauthorizedSync`` en 401/403 (fail-closed inmediato) y
    ``BundleRejected`` si el bundle recibido no verifica (se descarta; el
    caller mantiene el anterior vigente). Error de red → ``NetworkSyncError``.
    """
    url = f"{api_base()}/api/me/governance-bundle"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {device['token']}",
            "X-Device-Token": device["token"],
        },
    )
    ctx = ssl.create_default_context()  # verificación SIEMPRE (C6)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise UnauthorizedSync(f"HTTP {exc.code}") from None
        raise NetworkSyncError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NetworkSyncError(str(exc)) from None

    jws = str(data.get("bundle") or "")
    if not jws:
        raise NetworkSyncError("control plane returned no bundle")
    payload = verify_bundle(jws, device_id=device["device_id"], remember=True)
    _write_bundle_cache(jws)
    log_event(
        "bundle_synced",
        policy_version=payload.get("policy_version"),
        expires_at=payload.get("exp"),
        offline_grace_minutes=payload.get("offline_grace_minutes"),
    )
    return payload


class NetworkSyncError(Exception):
    """El control plane no alcanzable/degradado — gracia offline si aplica."""


# ---------------------------------------------------------------------------
# Heartbeat de presencia (spec 027 §4 — contrato normativo)
# ---------------------------------------------------------------------------
_PROCESS_STARTED_AT = time.monotonic()


def agent_uptime_seconds() -> int:
    """Uptime del proceso Hermes que corre el plugin (>= 0)."""
    return max(0, int(time.monotonic() - _PROCESS_STARTED_AT))


def post_heartbeat(
    device: dict[str, str], *, applied_policy_version: Any, mode: str
) -> dict[str, Any]:
    """``POST /api/me/device-heartbeat`` — presencia + señal de policy nueva.

    Body (contrato §4): ``{applied_policy_version, uptime_seconds, mode}``.
    Misma auth que el governance-bundle (Bearer + X-Device-Token). 401/403 →
    ``UnauthorizedSync`` (revocado/desligado — fail-closed inmediato en el
    caller); otro error HTTP o de red → ``NetworkSyncError`` (best-effort).
    """
    body = json.dumps(
        {
            "applied_policy_version": applied_policy_version,
            "uptime_seconds": agent_uptime_seconds(),
            "mode": mode if mode in ("normal", "restricted") else "restricted",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base()}/api/me/device-heartbeat",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {device['token']}",
            "X-Device-Token": device["token"],
            "Content-Type": "application/json",
        },
    )
    ctx = ssl.create_default_context()  # verificación SIEMPRE (C6)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise UnauthorizedSync(f"HTTP {exc.code}") from None
        raise NetworkSyncError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NetworkSyncError(str(exc)) from None
    if not isinstance(data, dict):
        raise NetworkSyncError("heartbeat response is not an object")
    return data


# ---------------------------------------------------------------------------
# BundleSyncer — ciclo de vida del sync (arranque + periódico + al vencimiento)
# ---------------------------------------------------------------------------
class BundleSyncer:
    """Hilo daemon de sync. Aplica el resultado a :func:`state.get_state`.

    Transiciones (§5.4): sync OK → NORMAL; 401/403 → RESTRINGIDO(revoked)
    inmediato con caché invalidada; red caída → gracia mientras el bundle
    vigente no expire, luego RESTRINGIDO(expired); sin bundle →
    RESTRINGIDO(no_bundle).
    """

    def __init__(self, interval: float | None = None) -> None:
        explicit = interval is not None
        if interval is None:
            raw = os.environ.get("TRUST_SYNC_INTERVAL", "").strip()
            explicit = bool(raw)
            try:
                interval = float(raw)
            except ValueError:
                interval = 0.0
        self.interval = max(_SYNC_INTERVAL_FLOOR, interval or DEFAULT_SYNC_INTERVAL_SECONDS)
        # True si el operador fijó el intervalo (param o TRUST_SYNC_INTERVAL):
        # entonces el autoajuste del servidor (D-027-02) NO lo pisa.
        self._interval_locked = explicit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick_started = 0.0

    # -- ciclo ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="trust-governance-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._last_tick_started = time.time()
            self.tick()
            # Re-sync periódico Y adelantado al vencimiento del bundle en
            # caché (REQ-GOH-004): dormir como mucho hasta el exp.
            st = governance_state.get_state()
            grace = st.grace_remaining_seconds()
            wait = self.interval if grace is None else min(self.interval, max(grace, 1.0))
            self._sleep_until_next(wait)

    def _sleep_until_next(self, wait: float) -> None:
        """Duerme hasta el próximo ciclo, salvo que el desktop pida «Sincronizar política» (una
        petición más nueva que el último ciclo): entonces despierta ya. Se revisa cada 2 s y el
        piso del intervalo sigue espaciando los ciclos (un sync cada 5 s como mucho)."""
        deadline = time.monotonic() + wait
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if (
                sync_requested_at() > self._last_tick_started
                and time.time() - self._last_tick_started >= _SYNC_INTERVAL_FLOOR
            ):
                return
            self._stop.wait(min(_SYNC_REQUEST_POLL_SECONDS, remaining))

    # -- un ciclo de sync (público para tests y para el arranque) ------------
    def tick(self) -> dict[str, Any]:
        """Ejecuta un ciclo completo (sync + heartbeat) y aplica transiciones.

        Nunca lanza: ante cualquier fallo el estado queda fail-closed.
        Retorna el snapshot del estado tras el ciclo.
        """
        st = governance_state.get_state()
        device = load_device_token()
        if device is None or not device.get("token"):
            st.enter_restricted(governance_state.REASON_NO_BUNDLE)
            st.record_sync_error("device not enrolled (no device token)")
            return st.snapshot()

        if not device.get("device_id"):
            # Token por env sin device_id: aud NO es verificable → fail-closed.
            st.enter_restricted(governance_state.REASON_NO_BUNDLE)
            st.record_sync_error("device token without device_id: cannot verify aud")
            return st.snapshot()

        # D-HRB-03 amend (spec 022 v1.1.0, REQ-HRB-010b): token expirado
        # (agente apagado > TTL) → UN intento de recuperación con el
        # id_token fresco de trust-login ANTES de golpear el sync (el server
        # re-emite directo para un device active del mismo usuario). Sin
        # recuperación: restricted(token_expired) — razón honesta, distinta
        # de revoked (la expiración NO es una revocación).
        if device_token_expired(device):
            device = self._recover_expired_token(st, device)
            if device is None:
                st.enter_restricted(governance_state.REASON_TOKEN_EXPIRED)
                st.record_sync_error(
                    "device token expired; re-run trust-login to re-enroll"
                )
                return st.snapshot()

        self._sync_cycle(st, device)
        # Heartbeat de presencia (spec 027 §4): piggyback en el MISMO ciclo.
        # Se omite solo si el sync ya detectó revocación (el control plane la
        # originó — no hay nada que reportar que no sepa ya).
        if st.reason != governance_state.REASON_REVOKED:
            self._heartbeat_cycle(st, device)
        return st.snapshot()

    def _recover_expired_token(
        self, st, device: dict[str, str]
    ) -> dict[str, str] | None:
        """Recuperación D-HRB-03(c): token expirado → re-enroll best-effort.

        Delega en :func:`enroll.recover_expired_token` (lock de home + POST
        /api/devices/enroll con el id_token fresco → 200 con token directo
        para device active). Retorna el device NUEVO o None (sin id_token,
        red caída, 4xx — el caller entra en restricted(token_expired)).
        Import perezoso: enroll no importa este módulo, pero así el núcleo
        v2 sigue funcionando sin la lane spec 022 presente.
        """
        log_event("device_token_expired", device_id=device.get("device_id"))
        try:
            from . import enroll as enroll_mod

            # Spec 022 v1.2.0: a desktop activated by the admin's one-time
            # token has no trust-login id_token, so it first trades its own
            # expired-but-authentic device token (server checks the device is
            # still active). The id_token re-enroll stays as the fallback for
            # OIDC-enrolled devices.
            outcome = enroll_mod.refresh_expired_device_token()
            if outcome != "active":
                outcome = enroll_mod.recover_expired_token()
        except Exception as exc:  # noqa: BLE001 - la recuperación JAMÁS tumba
            log_event("token_recovery_error", detail=str(exc))
            return None
        if outcome != "active":
            return None
        new_device = load_device_token()
        if (
            new_device
            and new_device.get("token")
            and not device_token_expired(new_device)
        ):
            log_event("device_token_recovered", device_id=new_device.get("device_id"))
            return new_device
        return None

    def _sync_cycle(self, st, device: dict[str, str]) -> None:
        """Un sync del bundle + transiciones de estado (sin heartbeat)."""
        try:
            payload = sync_once(device)
        except UnauthorizedSync as exc:
            # Desligado/revocado: inmediato, sin gracia (REQ-GOH-010).
            try:
                bundle_cache_path().unlink(missing_ok=True)
            except OSError:
                pass
            log_event("device_unauthorized", detail=str(exc))
            st.enter_restricted(governance_state.REASON_REVOKED)
            st.record_sync_error(str(exc))
            return
        except BundleRejected as exc:
            # El bundle nuevo no verifica: se descarta; el anterior vigente
            # sigue siendo autoridad (REQ-GOH-003) si no ha expirado.
            log_event("bundle_rejected_at_sync", reason=exc.reason)
            self._settle_without_fresh_bundle(st, governance_state.REASON_INVALID)
            st.record_sync_error(exc.reason)
            return
        except NetworkSyncError as exc:
            log_event("sync_unavailable", detail=str(exc))
            self._settle_without_fresh_bundle(st, governance_state.REASON_EXPIRED)
            st.record_sync_error(str(exc))
            return

        st.apply_bundle(payload)
        # Spec 045 §6: con cada bundle VERIFICADO, los perfiles de la organización se materializan
        # (instalar/actualizar/archivar/restaurar). Import perezoso: profile_delivery importa este
        # módulo. Jamás tumba el sync: sin red se conserva lo instalado y se reintenta.
        try:
            from . import profile_delivery

            profile_delivery.reconcile(payload, device)
        except Exception as exc:  # noqa: BLE001
            log_event("profiles_reconcile_error", detail=repr(exc)[:500])

    def _heartbeat_cycle(self, st, device: dict[str, str]) -> None:
        """POST heartbeat + propagación en vivo de policy (spec 027 §4)."""
        snap = st.snapshot()
        bundle = snap.get("bundle") or {}
        try:
            resp = post_heartbeat(
                device,
                applied_policy_version=bundle.get("policy_version"),
                mode=snap.get("mode", "restricted"),
            )
        except UnauthorizedSync as exc:
            # Revocación detectada por el heartbeat: fail-closed inmediato,
            # misma semántica que el sync (REQ-GOH-010).
            try:
                bundle_cache_path().unlink(missing_ok=True)
            except OSError:
                pass
            log_event("device_unauthorized", detail=f"heartbeat {exc}")
            st.enter_restricted(governance_state.REASON_REVOKED)
            st.record_sync_error(f"heartbeat: {exc}")
            return
        except NetworkSyncError as exc:
            # Best-effort: un heartbeat que no llega NO cambia el estado de
            # gobernanza (D-027-03 — el bundle vigente sigue siendo autoridad).
            log_event("heartbeat_unavailable", detail=str(exc))
            return

        log_event(
            "heartbeat_ok",
            applied_policy_version=bundle.get("policy_version"),
            current_policy_version=resp.get("current_policy_version"),
            policy_update_available=bool(resp.get("policy_update_available")),
        )
        # Sliding renewal (REQ-HRB-010a): el control plane re-emitió el
        # device token en el MISMO ciclo (camino plugin) — se persiste YA
        # para que el siguiente ciclo (y un restart) usen el token fresco.
        renewed = resp.get("device_token")
        if renewed:
            try:
                save_renewed_device_token(device, str(renewed))
                log_event(
                    "device_token_renewed",
                    expires_in=resp.get("expires_in"),
                )
            except OSError as exc:
                # Sin persistencia el token viejo sigue en disco: caducará
                # a su hora y la recuperación D-HRB-03(c) tomará el relevo.
                log_event("device_token_renewal_unpersisted", detail=str(exc))
        # Autoajuste (D-027-02): adoptamos el intervalo recomendado del
        # servidor SOLO si el operador no fijó TRUST_SYNC_INTERVAL.
        if not self._interval_locked:
            try:
                server_interval = float(resp.get("heartbeat_interval_seconds") or 0)
            except (TypeError, ValueError):
                server_interval = 0.0
            if server_interval > 0:
                self.interval = max(_SYNC_INTERVAL_FLOOR, min(server_interval, 3600.0))
        # Propagación en vivo (D-027-03): policy nueva disponible → re-baja
        # del bundle EN EL MISMO CICLO — sin reiniciar ni re-enrolar. El
        # nuevo bundle rige desde el siguiente turno.
        if resp.get("policy_update_available"):
            log_event("policy_update_pull", current=resp.get("current_policy_version"))
            self._sync_cycle(st, device)

    def _settle_without_fresh_bundle(self, st, expired_reason: str) -> None:
        """Sin bundle nuevo: gracia offline si el vigente no expiró (D-GOH-04)."""
        payload = st.snapshot().get("bundle")
        if payload and int(payload.get("exp", 0)) > time.time():
            return  # dentro de la gracia: el vigente sigue siendo autoridad
        # Sin bundle vigente: si nunca hubo, el reason honesto es no_bundle.
        st.enter_restricted(
            expired_reason if payload else governance_state.REASON_NO_BUNDLE
        )

    # -- arranque (llamada síncrona desde register(ctx)) ----------------------
    def load_cache_only(self) -> dict[str, Any]:
        """Carga de caché local, SIN red (rápida: register() no bloquea).

        Aplica el bundle cacheado verificado (modo NORMAL) o deja el estado
        en restringido (§5.4 SIN_BUNDLE). El sync de red lo hace el hilo.
        """
        st = governance_state.get_state()
        device = load_device_token()
        if device is not None and device.get("device_id"):
            cached = load_cached_bundle(device["device_id"])
            if cached is not None:
                st.apply_bundle(cached)
                log_event(
                    "startup_from_cache",
                    policy_version=cached.get("policy_version"),
                    expires_at=cached.get("exp"),
                )
                return st.snapshot()
        st.enter_restricted(governance_state.REASON_NO_BUNDLE)
        return st.snapshot()

    def startup(self) -> dict[str, Any]:
        """Caché local + primer sync de red (tests y verificación manual)."""
        self.load_cache_only()
        # Primer sync inmediato (el hilo lo repite periódicamente).
        return self.tick()

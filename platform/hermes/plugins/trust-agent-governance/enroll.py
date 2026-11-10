"""enroll.py — enrolamiento del Hermes local a una organización Trust Agent.

Spec 022 (hermes-real-bridge), REQ-HRB-001/002, D-HRB-01/02/03. Extiende el
plugin ``trust-agent-governance`` (spec 010 v1 / spec 019 v2) SIN tocar el
core de Hermes: todo por ``ctx.register_command`` (API pública verificada
en ``hermes_cli/plugins.py::register_command`` — handler ``fn(raw_args)``).

Flujo (API existente spec 007, verificada E2E — cero cambios server-side):

  1. Keypair local RSA-2048 (D-HRB-02): privada PKCS#8 ``0600`` en
     ``$HERMES_HOME/trust-agent/device_key.pem``, pública SPKI en
     ``device_key.pub.pem``; fingerprint = sha256(hex) del PEM público.
     Reutilizable entre re-enrolamientos (upsert por fingerprint).
  2. ``POST /api/devices/enroll`` {org_code, id_token, public_key,
     fingerprint, device_name, platform} -> 202 + enrollment_token (one-time).
  3. Aprobación admin (fuera de este comando) -> ``POST /api/devices/enroll/
     exchange`` {enrollment_token}: 202 mientras pending, 200 con
     {device_token, device_id, status:"active"} al aprobarse, 410 si
     consumido/rechazado.
  4. Device token persistido en ``$HERMES_HOME/trust-agent/device-token.json``
     (0600) — almacén canónico que ``enforcement.py`` y el núcleo v2 leen
     (D-HRB-03). El env ``TRUST_DEVICE_TOKEN`` sigue soportado como fallback.

El id_token OIDC (acr>=2) NUNCA se teclea en el chat (D-HRB-01): llega por
archivo (argumento del comando, ``TRUST_ENROLL_ID_TOKEN_FILE`` o
``$HERMES_HOME/trust-agent/id-token.txt``) producido por el flujo OIDC del
desktop/CLI. Este módulo jamás maneja passwords ni códigos MFA.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:  # POSIX: flock
    import fcntl
except ImportError:  # pragma: no cover - fuera de Linux/macOS
    fcntl = None  # type: ignore[assignment]
try:  # Windows: LockFileEx por byte (msvcrt.locking)
    import msvcrt
except ImportError:  # pragma: no cover - fuera de Windows
    msvcrt = None  # type: ignore[assignment]

TOOLSET = "trust_agent"

# Auto-enroll zero-UI (mandato Miller): intervalo entre intentos y timeout de
# polling del canje mientras el admin aprueba (aprobación asíncronica).
AUTO_ENROLL_INTERVAL_S = 15.0
AUTO_ENROLL_POLL_S = 20.0

# Espera máxima tras el lock de auto-enroll de otro proceso del MISMO home
# (un intento ajeno dura ~AUTO_ENROLL_POLL_S + overhead; el comando manual
# puede sostenerlo TRUST_ENROLL_TIMEOUT s — en ese caso esperamos y si no
# cede, el ciclo reintenta en el próximo tick).
AUTOENROLL_LOCK_WAIT_S = 30.0

# Mensajes de usuario en inglés (UI copy); comentarios/docstrings en español.
_USAGE = (
    "Usage: /trust-enroll <org_code> [id_token_file]\n"
    "  Enrolls this Hermes instance into the organization <org_code>.\n"
    "  id_token_file: path to a file containing an OIDC id_token with MFA\n"
    "  (acr>=2), produced by your organization's desktop/CLI login flow.\n"
    "  Default: $TRUST_ENROLL_ID_TOKEN_FILE or "
    "<home>/trust-agent/id-token.txt\n"
    "  This command never asks for passwords or MFA codes in the chat."
)


# ---------------------------------------------------------------------------
# Home / estado en disco
# ---------------------------------------------------------------------------
def hermes_home() -> Path:
    """Home real del perfil activo (nunca hardcodeado).

    Orden context-aware (spec 030 D-030-09): ``hermes_constants.get_hermes_home()``
    resuelve override context-local → ``HERMES_HOME`` → default. En un gateway
    multi-perfil (modelo un-gateway-por-host) el proceso sirve sesiones de
    VARIOS perfiles: su env ``HERMES_HOME`` es el home del gateway (default),
    y el override context-local (``set_hermes_home_override``, gateway/run.py)
    es el que apunta al perfil de la sesión servida. Leer el env PRIMERO
    resolvía el home del gateway y el plugin no encontraba el device token
    del perfil → modo restringido espurio. Solo si ``hermes_constants`` no
    está importable (tests standalone) se cae al env/default.
    """
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 - fuera del runtime de Hermes (tests)
        env = os.environ.get("HERMES_HOME")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".hermes"


def trust_dir() -> Path:
    d = hermes_home() / "trust-agent"
    d.mkdir(parents=True, exist_ok=True)
    try:  # 0700: aquí viven la clave privada y el device token.
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _write_secret(path: Path, data: str) -> None:
    """Escribe un secreto con permisos 0600 (escritura atómica)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def api_base() -> str:
    return os.environ.get("TRUST_AGENT_API", "http://127.0.0.1:8000").rstrip("/")


# ---------------------------------------------------------------------------
# Lock de enroll por home (fix t_dd2f5f65: race multi-proceso)
# ---------------------------------------------------------------------------
class _HomeLock:
    """flock exclusivo sobre ``$HERMES_HOME/trust-agent/.autoenroll.lock``.

    Con ``gateway.standalone:true`` un ``hermes chat`` son DOS procesos con el
    plugin cargado (gateway standalone + TUI); un gateway explícito extra
    añade más. Cada ``POST /api/devices/enroll`` resetea
    ``enrollment_token_hash`` del device pending (upsert por fingerprint), así
    que dos enroll concurrentes del mismo home se pisan el token one-time
    (410s mutuos). Este lock serializa el enroll ENTRE PROCESOS del mismo
    home; el kernel libera el flock si el proceso muere (sin locks huérfanos).
    """

    def __init__(self, timeout_s: float = AUTOENROLL_LOCK_WAIT_S):
        self.timeout_s = timeout_s
        self.acquired = False
        self._fh = None

    @staticmethod
    def _try_lock(fh: Any) -> None:
        """Lock exclusivo NO bloqueante; OSError si otro lo tiene.

        POSIX ``flock`` y el lock por byte de Windows (``msvcrt.locking`` =
        ``LockFileEx`` sobre el byte 0) son por handle abierto: excluyen a
        otros procesos Y a otros hilos del mismo proceso (cada ``__enter__``
        abre su propio handle). Sin la rama Windows el lock era un no-op en la
        plataforma principal: el BundleSyncer y el watcher refrescaban el
        token a la vez y dos enroll concurrentes podían pisarse el one-time.
        """
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    @staticmethod
    def _unlock(fh: Any) -> None:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_UN)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

    def __enter__(self) -> "_HomeLock":
        if fcntl is None and msvcrt is None:
            self.acquired = True  # sin primitiva de lock: solo queda reuse-first
            return self
        try:
            fh = (trust_dir() / ".autoenroll.lock").open("a+")
        except OSError:
            self.acquired = True  # sin lock disponible: proseguir (best-effort)
            return self
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._try_lock(fh)
                self.acquired = True
                self._fh = fh
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    return self
                time.sleep(0.2)

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            try:
                self._unlock(self._fh)
            except OSError:
                pass
            self._fh.close()
            self._fh = None
        self.acquired = False


# ---------------------------------------------------------------------------
# HTTP (urllib puro — sin dependencias nuevas; TLS siempre verificado)
# ---------------------------------------------------------------------------
def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any]]:
    """Request JSON al control plane. Retorna (status, json). Lanza
    ``urllib.error.HTTPError``/``URLError`` tal cual — el caller traduce."""
    url = f"{api_base()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    ctx = ssl.create_default_context()  # verificación SIEMPRE (nunca verify=False, C6)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read() or b"{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        return exc.code, payload


def _error_message(payload: dict[str, Any], default: str) -> str:
    err = payload.get("error") or {}
    reason = err.get("reason") or err.get("code") or ""
    msg = err.get("message") or default
    return f"{msg} ({reason})" if reason else msg


# ---------------------------------------------------------------------------
# Keypair del dispositivo (D-HRB-02)
# ---------------------------------------------------------------------------
def load_or_create_keypair() -> tuple[str, str, str]:
    """-> (private_pem, public_pem, fingerprint). Genera si no existe."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv_path = trust_dir() / "device_key.pem"
    pub_path = trust_dir() / "device_key.pub.pem"

    if priv_path.exists() and pub_path.exists():
        private_pem = priv_path.read_text(encoding="utf-8")
        public_pem = pub_path.read_text(encoding="utf-8")
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        _write_secret(priv_path, private_pem)
        pub_path.write_text(public_pem, encoding="utf-8")
        os.chmod(pub_path, 0o644)

    fingerprint = hashlib.sha256(public_pem.encode()).hexdigest()
    return private_pem, public_pem, fingerprint


# ---------------------------------------------------------------------------
# Device token store (D-HRB-03)
# ---------------------------------------------------------------------------
def save_device_token(
    *, device_id: str, token: str, org_code: str, tenant_slug: str,
    model_key: str | None = None,
) -> Path:
    path = trust_dir() / "device-token.json"
    model_base_url = current_model_base_url()
    payload = {
        "device_id": device_id,
        "token": token,
        "enrolled_at": int(time.time()),
        "org_code": org_code,
        "tenant_slug": tenant_slug,
        "model_base_url": model_base_url,
    }
    if model_key:
        # Kept in the same secret file as the device token so the managed
        # sync can restore the organization-issued credential if config.yaml
        # is edited by hand.
        payload["model_key"] = model_key
    _write_secret(path, json.dumps(payload, indent=2))
    # The activation exchange also issues the LiteLLM model key for this
    # device. Persist it into the agent config immediately — otherwise the
    # seeded model gateway answers 401 and the app looks "not responding"
    # even though governance/enrollment succeeded (found live on Windows).
    if model_key:
        apply_model_key(model_key, model_base_url)
    # El enrollment token ya fue canjeado: ya no sirve (one-time).
    try:
        (trust_dir() / "enrollment-token.json").unlink(missing_ok=True)
    except OSError:
        pass
    return path


def current_model_base_url() -> str:
    """Read the seeded (baked) LiteLLM base URL from config.yaml before any
    user edit. Stored in device-token.json as the trusted provider endpoint."""
    try:
        import yaml

        path = hermes_home() / "config.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        model = data.get("model") if isinstance(data, dict) else None
        base = model.get("base_url") if isinstance(model, dict) else None
        return str(base).strip() if base else ""
    except (OSError, ValueError, ImportError):
        return ""


def apply_model_key(model_key: str, model_base_url: str | None = None) -> None:
    """Write the server-issued model key into ``$HERMES_HOME/config.yaml``.

    Only touches ``model.api_key`` / ``model.base_url`` (and preserves every
    other key). Best effort: a read-only profile must never fail the
    enrollment itself.
    """
    try:
        import yaml

        path = hermes_home() / "config.yaml"
        data: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        model = data.get("model")
        model = dict(model) if isinstance(model, dict) else {}
        changed = False
        if model.get("api_key") != model_key:
            model["api_key"] = model_key
            changed = True
        if model_base_url and model.get("base_url") != model_base_url:
            model["base_url"] = model_base_url
            changed = True
        if not changed:
            return
        data["model"] = model
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError, ImportError):
        pass


def _save_enrollment_token(enrollment_token: str, device_id: str, org_code: str) -> None:
    """Persiste el enrollment token one-time (zero-UI a través de restarts).

    Si el proceso muere entre el POST enroll y el canje (p. ej. el admin aún
    no había aprobado), el siguiente arranque puede canjear ESTE token en
    vez de re-POSTear (un dispositivo ya active responde 409 al re-enroll).
    """
    payload = {
        "enrollment_token": enrollment_token,
        "device_id": device_id,
        "org_code": org_code,
        "saved_at": int(time.time()),
    }
    _write_secret(trust_dir() / "enrollment-token.json", json.dumps(payload, indent=2))


def _load_enrollment_token() -> dict[str, str] | None:
    try:
        data = json.loads(
            (trust_dir() / "enrollment-token.json").read_text(encoding="utf-8"))
        if data.get("enrollment_token"):
            return {
                "enrollment_token": str(data["enrollment_token"]),
                "device_id": str(data.get("device_id", "")),
                "org_code": str(data.get("org_code", "")),
            }
    except (OSError, ValueError):
        pass
    return None


def load_device_token() -> dict[str, str] | None:
    """Almacén canónico: device-token.json > env TRUST_DEVICE_TOKEN."""
    path = trust_dir() / "device-token.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("token") and data.get("device_id"):
                result = {
                    "device_id": str(data["device_id"]),
                    "token": str(data["token"]),
                    "org_code": str(data.get("org_code") or ""),
                    "tenant_slug": str(data.get("tenant_slug") or ""),
                    "model_base_url": str(data.get("model_base_url") or ""),
                    "model_key": str(data.get("model_key") or ""),
                }
                # Migration from pre-P-26 token files: enrollment.json carries
                # the org-issued model key, so the managed sync can still pin
                # credentials on installs enrolled by an older build.
                if not result["model_key"] or not result["model_base_url"]:
                    try:
                        enrollment = json.loads(
                            (trust_dir() / "enrollment.json").read_text(encoding="utf-8")
                        )
                        if not result["model_key"]:
                            result["model_key"] = str(enrollment.get("model_key") or "")
                        if not result["model_base_url"]:
                            base = str(enrollment.get("model_base_url") or "").strip()
                            if not base:
                                portal = str(enrollment.get("portal_url") or "").strip()
                                if portal:
                                    from urllib.parse import urlsplit
                                    parsed = urlsplit(portal)
                                    if parsed.hostname:
                                        base = f"{parsed.scheme or 'http'}://{parsed.hostname}:4000"
                            result["model_base_url"] = base
                    except (OSError, ValueError):
                        pass
                return result
        except (ValueError, OSError):
            pass
    env = os.environ.get("TRUST_DEVICE_TOKEN", "").strip()
    if env:
        # El env v1 no porta device_id: se resuelve con el claim del token
        # en el primer sync (enforcement lo tolera; aud se valida si hay id).
        return {"device_id": os.environ.get("TRUST_DEVICE_ID", ""), "token": env}
    return None


def device_token_expired(device: dict[str, str], now: float | None = None) -> bool:
    """True si el claim ``exp`` del device token ya pasó (D-HRB-03 amend).

    Lectura SIN verificar firma — solo decide si el watcher/syncer debe
    re-enrutar al flujo de recuperación; el control plane verifica firma,
    exp y revocación en cada request (fail-closed server-side). Token
    ilegible: False (el server dirá 401 → revoked, camino existente).
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


# ---------------------------------------------------------------------------
# Comando /trust-enroll (D-HRB-01)
# ---------------------------------------------------------------------------
def _read_id_token(path_str: str | None) -> str:
    candidates = []
    if path_str:
        candidates.append(Path(path_str).expanduser())
    env_file = os.environ.get("TRUST_ENROLL_ID_TOKEN_FILE", "").strip()
    if env_file:
        candidates.append(Path(env_file).expanduser())
    candidates.append(trust_dir() / "id-token.txt")
    for cand in candidates:
        if cand.is_file():
            token = cand.read_text(encoding="utf-8").strip()
            if token:
                return token
    raise FileNotFoundError(
        "id_token file not found. Tried: "
        + ", ".join(str(c) for c in candidates)
    )


def _poll_exchange(enrollment_token: str, timeout_s: float) -> tuple[str, dict[str, Any]]:
    """Polling del canje one-time. -> ("active", {...}) | ("pending", {...})
    | ("gone", {...}) | ("error", {...})."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while True:
        status, payload = _request(
            "POST", "/api/devices/enroll/exchange",
            body={"enrollment_token": enrollment_token},
        )
        if status == 200:
            return "active", payload
        if status == 202:
            last = payload
            if time.monotonic() >= deadline:
                return "pending", last
            time.sleep(3.0)
            continue
        if status == 410:
            return "gone", payload
        return "error", payload


# ---------------------------------------------------------------------------
# Flujo compartido (comando manual + auto-enroll zero-UI)
# ---------------------------------------------------------------------------
def _exchange_saved_raw(
    saved: dict[str, str], org_code: str, poll_timeout_s: float
) -> tuple[str, dict[str, Any]]:
    """Canjea el enrollment token persistido. -> outcome crudo:

    "active" (device token guardado) | "pending" | "gone" (token consumido/
    caducado/rechazado — se descarta del disco) | "exchange" (error transitorio).
    """
    outcome, ex = _poll_exchange(saved["enrollment_token"], poll_timeout_s)
    if outcome == "active":
        token = ex.get("device_token", "")
        device_id = ex.get("device_id") or saved.get("device_id", "")
        if token and device_id:
            path = save_device_token(
                device_id=device_id, token=token,
                org_code=org_code, tenant_slug=org_code,
                model_key=ex.get("model_key") if isinstance(ex.get("model_key"), str) else None,
            )
            return "active", {"device_id": device_id, "token_path": str(path)}
        return "exchange", {"message": f"exchange response incomplete: {ex}"}
    if outcome == "gone":
        try:
            (trust_dir() / "enrollment-token.json").unlink(missing_ok=True)
        except OSError:
            pass
        return "gone", {"message": _error_message(ex, "gone")}
    if outcome == "pending":
        return "pending", {"device_id": saved.get("device_id", "")}
    return "exchange", {"message": _error_message(ex, "exchange error")}


def device_type() -> str:
    """Tipo de dispositivo reportado al enrolar (spec 034, D-034-04).

    ``cli`` = instalador curl|bash ligado a la org (deja
    ``TRUST_DEVICE_TYPE=cli`` en ``$HERMES_HOME/.env``); ``desktop`` =
    bundle desktop (default retrocompatible). Solo afecta cómo el admin
    VE el dispositivo en la consola — el flujo de enrolamiento, las
    policies y el audit son idénticos.
    """
    value = os.environ.get("TRUST_DEVICE_TYPE", "").strip().lower()
    return "cli" if value == "cli" else "desktop"


def _enroll_attempt(
    org_code: str, id_token: str, poll_timeout_s: float
) -> tuple[str, dict[str, Any]]:
    """Un intento de enroll, YA bajo el lock del home (fix t_dd2f5f65).

    REUSE-FIRST: si hay un enrollment token guardado para ESTA org, se canjea
    SIN re-POSTear ``/api/devices/enroll`` — cada POST resetea el hash del
    token del device pending (upsert por fingerprint) e invalida el token que
    otro proceso del mismo home está poll-eando (la causa de los 410s mutuos).
    Solo se POSTea cuando no hay token guardado o el guardado ya no sirve:

    - "gone" (consumido/caducado/rechazado) → se descarta y se re-POSTea
      (re-enroll de device rejected: ADR-013 lo permite server-side).
    - "exchange" (error transitorio del canje) → NO se re-POSTea (resetear el
      hash sería peor): se reintenta el canje en el próximo ciclo.
    """
    saved = _load_enrollment_token()
    if saved and saved.get("enrollment_token") and saved.get("org_code") == org_code:
        outcome, info = _exchange_saved_raw(saved, org_code, poll_timeout_s)
        if outcome in ("active", "pending", "exchange"):
            return outcome, info
        # outcome == "gone": el token guardado ya no sirve — fresh enroll.
    return _enroll_flow(org_code, id_token, poll_timeout_s)


def _enroll_flow(
    org_code: str, id_token: str, poll_timeout_s: float
) -> tuple[str, dict[str, Any]]:
    """Enroll → exchange → device token. -> (outcome, info).

    outcome: "active" (info = {device_id, token, token_path}) | "pending"
    (info = {device_id}) | "usage" | "network" | "mfa" | "http" | "exchange".
    Nunca lanza: todo error se devuelve como outcome.
    """
    try:
        _, public_pem, fingerprint = load_or_create_keypair()
    except Exception as exc:  # noqa: BLE001 - reportar, no tumbar
        return "http", {"message": f"could not generate the device keypair: {exc}"}

    device_name = os.environ.get("TRUST_DEVICE_NAME") or socket.gethostname()
    try:
        status, payload = _request(
            "POST",
            "/api/devices/enroll",
            body={
                "org_code": org_code,
                "id_token": id_token,
                "public_key": public_pem,
                "fingerprint": fingerprint,
                "device_name": device_name,
                "platform": f"{_platform.system()} {_platform.release()}",
                "device_type": device_type(),
            },
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return "network", {"message": f"control plane unreachable at {api_base()} ({exc})"}
    if status == 403:
        return "mfa", {"message": _error_message(payload, "MFA (acr>=2) required")}
    if status == 200 and payload.get("device_token"):
        # D-HRB-03 amend (spec 022 v1.1.0, REQ-HRB-010b): device ACTIVE del
        # mismo usuario + id_token MFA fresco → el server re-emite el device
        # token DIRECTO (sin enrollment token ni re-aprobación admin). Este
        # es el camino de recuperación del token expirado.
        device_id = str(payload.get("device_id") or "")
        token = str(payload["device_token"])
        if not device_id:
            return "exchange", {"message": f"reissue response without device_id: {payload}"}
        path = save_device_token(
            device_id=device_id, token=token,
            org_code=org_code, tenant_slug=org_code,
            model_key=payload.get("model_key") if isinstance(payload.get("model_key"), str) else None,
        )
        return "active", {"device_id": device_id, "token_path": str(path), "reissued": True}
    if status == 409:
        # D-HRB-03 amend: el server ya SOLO responde 409 cuando el
        # fingerprint pertenece a OTRO usuario. El fallback del enrollment
        # token persistido se conserva para compatibilidad (zero-UI a
        # través de restarts contra servers viejos que aún responden 409
        # para device active).
        saved = _load_enrollment_token()
        if saved and saved.get("enrollment_token"):
            outcome, info = _exchange_saved_raw(saved, org_code, poll_timeout_s)
            if outcome == "gone":
                return "exchange", {
                    "message": "saved enrollment token was consumed or expired "
                               "(410) and the device is already active: ask an "
                               "admin to revoke the device so it can re-enroll. "
                               + info.get("message", "")
                }
            return outcome, info
        return "http", {"message": _error_message(payload, "HTTP 409")}
    if status >= 400:
        return "http", {"message": _error_message(payload, f"HTTP {status}")}

    enrollment_token = payload.get("enrollment_token", "")
    device_id = payload.get("device_id", "")
    if not enrollment_token or not device_id:
        return "http", {"message": f"unexpected response: {payload}"}

    # Zero-UI: persistir el token one-time ANTES de canjear (supervivencia
    # a restarts mientras el admin aprueba).
    try:
        _save_enrollment_token(enrollment_token, device_id, org_code)
    except OSError:
        pass  # best-effort: sin persistencia igual canjeamos ahora

    outcome, ex = _poll_exchange(enrollment_token, poll_timeout_s)
    if outcome == "active":
        token = ex.get("device_token", "")
        if not token:
            return "exchange", {"message": f"exchange response without token: {ex}"}
        path = save_device_token(
            device_id=device_id, token=token,
            org_code=org_code, tenant_slug=org_code,
            model_key=ex.get("model_key") if isinstance(ex.get("model_key"), str) else None,
        )
        return "active", {"device_id": device_id, "token_path": str(path)}
    if outcome == "pending":
        return "pending", {"device_id": device_id}
    if outcome == "gone":
        return "exchange", {
            "message": "enrollment token rejected or already used (410): "
                       + _error_message(ex, "gone")
        }
    return "exchange", {"message": _error_message(ex, "exchange error")}


def cmd_trust_enroll(raw_args: str = "") -> str:
    """Handler del comando (``fn(raw_args: str) -> str``). Nunca crashea la
    sesión: todo error se devuelve como mensaje de chat."""
    args = (raw_args or "").strip().split()
    if not args:
        return _USAGE
    org_code = args[0]
    id_token_file = args[1] if len(args) > 1 else None

    try:
        id_token = _read_id_token(id_token_file)
    except FileNotFoundError as exc:
        return f"Enrollment failed: {exc}\n\n{_USAGE}"

    timeout_s = float(os.environ.get("TRUST_ENROLL_TIMEOUT", "120"))
    # Mismo lock del home que el auto-enroll: un /trust-enroll manual y el
    # watcher de otro proceso (gateway standalone) no pueden pisarse el
    # enrollment token one-time (fix t_dd2f5f65).
    with _HomeLock(timeout_s=AUTOENROLL_LOCK_WAIT_S) as lock:
        if not lock.acquired:
            return (
                "Enrollment failed: another Hermes process in this home is "
                "already enrolling; retry in a moment."
            )
        outcome, info = _enroll_attempt(org_code, id_token, timeout_s)

    if outcome == "active":
        return (
            "Device enrolled and ACTIVE.\n"
            f"  device_id:    {info['device_id']}\n"
            f"  organization: {org_code}\n"
            f"  token stored: {info['token_path']} (0600)\n"
            "The governance bundle will sync on the next turn; ask me to run "
            "governance_status to confirm NORMAL mode."
        )
    if outcome == "pending":
        return (
            "Enrollment is PENDING admin approval.\n"
            f"  device_id: {info['device_id']}\n"
            f"  Polling timed out after {timeout_s:.0f}s. Re-run "
            f"/trust-enroll {org_code} once an admin approves the device "
            "(safe: the fingerprint upserts, no duplicate rows)."
        )
    return f"Enrollment failed: {info.get('message', outcome)}"


# ---------------------------------------------------------------------------
# Auto-enroll ZERO-UI (mandato Miller: primera ejecución con binding = ya
# enrolado; nada de pantallas ni comandos para el usuario invitado)
# ---------------------------------------------------------------------------
def org_binding() -> str | None:
    """org_code del binding de la organización.

    Fuentes (en orden): env ``TRUST_ORG_CODE`` (instalador/launcher) o el
    archivo ``$HERMES_HOME/trust-agent/org-code.txt`` horneado por el bundle
    de descarga (spec 023). org_code es identificador público, NO secreto —
    la seguridad del enrolamiento está en la aprobación admin (spec 007).
    """
    env = os.environ.get("TRUST_ORG_CODE", "").strip()
    if env:
        return env
    try:
        code = (trust_dir() / "org-code.txt").read_text(encoding="utf-8").strip()
        return code or None
    except OSError:
        return None


def _log(event: str, **fields: Any) -> None:
    """Log local del plugin (mismo formato que enforcement/bundle_client).

    Siempre incluye ``pid`` (atribución de procesos: gateway host vs TUI
    embebido vs managed workers — ver enforcement.log_event).
    """
    try:
        entry = {"ts": int(time.time()), "pid": os.getpid(), "event": event, **fields}
        with (trust_dir() / "plugin.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Login OIDC por navegador, zero-UI (mandato Miller: nada de pantallas ni
# comandos para el usuario invitado). Mismo flujo que el trust-login
# standalone del bundle de descarga (spec 023) — portado aquí para que
# corra DENTRO del proceso del gateway (mismo intérprete ya resuelto, sin
# que Electron tenga que localizar un Python por su cuenta: el gap real que
# dejaba al usuario sin ningún camino para iniciar sesión, t_8f2c1a04).
# ---------------------------------------------------------------------------
_BROWSER_LOGIN_TIMEOUT_S = 300.0  # 5 min — más corto que el CLI (10 min):
# esto corre en el watcher de fondo, no bloquea nada visible al usuario, y
# el próximo ciclo reintenta solo si hace falta.
_BROWSER_LOGIN_COOLDOWN_S = 120.0  # no reabrir el navegador en cada tick de
# 15s si el usuario dejó la pestaña sin terminar.


def oidc_issuer() -> str | None:
    env = os.environ.get("TRUST_OIDC_ISSUER", "").strip()
    if env:
        return env
    try:
        for line in (hermes_home() / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TRUST_OIDC_ISSUER="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def oidc_client_id() -> str:
    env = os.environ.get("TRUST_OIDC_CLIENT_ID", "").strip()
    if env:
        return env
    try:
        for line in (hermes_home() / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TRUST_OIDC_CLIENT_ID="):
                return line.split("=", 1)[1].strip() or "trust-agent-web"
    except OSError:
        pass
    return "trust-agent-web"


def _browser_login_cooldown_path() -> Path:
    return trust_dir() / ".browser-login-last-attempt"


def _admin_activated() -> bool:
    """True when this home was activated with an admin one-time token.

    The desktop's activation flow (spec 044) writes ``enrollment.json`` with
    the portal it redeemed against; such a home has no OIDC login to recover
    with, so the watcher must never open a browser for it.
    """
    try:
        data = json.loads((trust_dir() / "enrollment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("portal_url") or data.get("activated_at"))


def _browser_login_on_cooldown() -> bool:
    try:
        last = float(_browser_login_cooldown_path().read_text().strip())
    except (OSError, ValueError):
        return False
    return (time.time() - last) < _BROWSER_LOGIN_COOLDOWN_S


def attempt_browser_login(timeout_s: float = _BROWSER_LOGIN_TIMEOUT_S) -> bool:
    """Abre el navegador real al login OIDC+MFA de la organización y espera
    el callback local — igual que ``trust-login.py`` standalone, pero sin
    ``sys.exit`` (esto corre dentro del proceso del gateway, nunca lo puede
    tumbar). -> True si quedó un id_token nuevo en disco.

    Nunca lanza: cualquier fallo se loguea (plugin.log) y se retorna False
    — el watcher reintenta en el siguiente ciclo tras el cooldown.
    """
    import base64
    import hashlib as _hashlib
    import http.server
    import secrets as _secrets
    import threading as _threading
    import urllib.parse
    import urllib.request
    import webbrowser

    issuer = oidc_issuer()
    if not issuer:
        _log("browser_login_skipped", reason="no TRUST_OIDC_ISSUER configured")
        return False
    try:
        _browser_login_cooldown_path().write_text(str(time.time()))
    except OSError:
        pass

    client_id = oidc_client_id()
    redirect_uri = "http://127.0.0.1:8765/callback"

    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    verifier = _b64url(_secrets.token_bytes(48))
    challenge = _b64url(_hashlib.sha256(verifier.encode()).digest())
    state = _secrets.token_urlsafe(24)
    nonce = _secrets.token_urlsafe(16)
    result: dict = {}
    done = _threading.Event()

    class CB(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            result.update(
                code=(qs.get("code") or [None])[0],
                state=(qs.get("state") or [None])[0],
                error=(qs.get("error") or [None])[0],
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h3>Sign-in complete.</h3>"
                b"<p>You can close this tab and return to Trust Agent.</p>"
                b"<script>window.close()</script></body></html>"
            )
            done.set()

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), CB)
    except OSError as exc:
        # Puerto ocupado (otro intento concurrente de este mismo home): no
        # es un fallo real, el próximo ciclo o el otro intento lo resuelve.
        _log("browser_login_attempt", outcome="port_busy", detail=str(exc))
        return False
    _threading.Thread(target=server.serve_forever, daemon=True).start()

    auth_url = (
        f"{issuer}/protocol/openid-connect/auth?"
        + urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id,
            "redirect_uri": redirect_uri, "scope": "openid",
            "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "acr_values": "2",
        })
    )
    _log("browser_login_opened", issuer=issuer, auth_url=auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception as exc:  # noqa: BLE001 - abrir navegador nunca tumba nada
        _log("browser_login_open_failed", detail=str(exc))

    ok = done.wait(timeout=timeout_s)
    server.shutdown()
    if not ok:
        _log("browser_login_attempt", outcome="timeout")
        return False
    if result.get("error") or not result.get("code"):
        _log("browser_login_attempt", outcome="idp_error", detail=str(result.get("error")))
        return False
    if result.get("state") != state:
        _log("browser_login_attempt", outcome="state_mismatch")
        return False

    try:
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": client_id,
            "code": result["code"], "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }).encode()
        req = urllib.request.Request(
            f"{issuer}/protocol/openid-connect/token", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            tokens = json.loads(resp.read())
        id_token = tokens.get("id_token")
        if not id_token:
            _log("browser_login_attempt", outcome="no_id_token_in_response")
            return False
        _write_secret(trust_dir() / "id-token.txt", id_token)
        _log("browser_login_attempt", outcome="success")
        return True
    except Exception as exc:  # noqa: BLE001 - reportar, nunca tumbar el watcher
        _log("browser_login_attempt", outcome="exchange_error", detail=str(exc))
        return False


def auto_enroll_once(poll_timeout_s: float = AUTO_ENROLL_POLL_S) -> str:
    """Un intento de auto-enrolamiento. -> outcome del flujo (para tests).

    Precondiciones (zero-UI): binding de org presente + id_token del login
    OIDC de primera ejecución (archivo default) — si falta el id_token pero
    SÍ hay binding de org, este mismo método abre el navegador al login real
    de la organización (``attempt_browser_login``, con cooldown para no
    reabrir pestañas en cada tick) en vez de quedarse pasivo esperando un
    archivo que, en el desktop real, nadie más iba a producir nunca
    (t_8f2c1a04: "no he podido enrolar nunca" — no había NINGÚN camino desde
    instalar hasta tener credenciales). El enrolamiento manual /trust-enroll
    sigue disponible para cuando el usuario quiera re-disparar a mano.

    D-HRB-03 amend (spec 022 v1.1.0): la presencia del archivo device-token
    json YA NO basta para cortar en "already_enrolled" — si el token caducó
    (agente apagado > TTL), se re-enruta al flujo (el server re-emite directo
    para un device active del mismo usuario con id_token MFA fresco).

    Serializado entre procesos del mismo home (fix t_dd2f5f65): el lock
    evita que el gateway standalone y el TUI re-POSTeen enroll a la vez
    (cada POST resetea el hash del token one-time del device pending).
    """
    device = load_device_token()
    if device is not None and not device_token_expired(device):
        return "already_enrolled"
    if device is not None and device.get("token"):
        # Spec 022 v1.2.0 (D-HRB-03 amend 2): an enrolled desktop whose token
        # merely expired (closed longer than the TTL) trades its own
        # authentic token for a fresh one first. Opening the browser for an
        # OIDC login here was wrong for admin-activated desktops: they never
        # had an id_token, so the watcher popped a login tab every cycle.
        refreshed = refresh_expired_device_token()
        if refreshed == "active":
            return "already_enrolled"
        if refreshed in ("network", "busy"):
            return refreshed
        # "rejected" (revoked / too old): only an OIDC-enrolled home can
        # still recover below with a fresh id_token.
    org_code = org_binding()
    if not org_code:
        return "no_binding"
    try:
        id_token = _read_id_token(None)
    except FileNotFoundError:
        if _admin_activated():
            # Spec 044: activated with the admin's one-time token — there is
            # no org browser login to fall back to. The employee needs a new
            # activation token; the desktop shows the restricted state.
            return "needs_activation"
        # Vinculado a una organización pero sin credenciales todavía: abre
        # el navegador real al login de la organización (una vez por
        # cooldown, nunca en cada tick de 15s) en vez de esperar pasivo un
        # archivo que nadie más iba a producir en el desktop real.
        if not _browser_login_on_cooldown():
            attempt_browser_login()
        return "no_id_token"

    with _HomeLock(timeout_s=AUTOENROLL_LOCK_WAIT_S) as lock:
        if not lock.acquired:
            _log("auto_enroll_attempt", org_code=org_code, outcome="busy",
                 detail="another process of this home holds the enroll lock")
            return "busy"
        # Otro proceso pudo completar el enroll mientras esperábamos el lock:
        device = load_device_token()
        if device is not None and not device_token_expired(device):
            return "already_enrolled"
        outcome, info = _enroll_attempt(org_code, id_token, poll_timeout_s)

    if outcome == "active":
        _log(
            "auto_enrolled",
            org_code=org_code,
            device_id=info.get("device_id"),
            token_path=info.get("token_path"),
        )
    else:
        _log("auto_enroll_attempt", org_code=org_code, outcome=outcome,
             detail=info.get("message", ""))
    return outcome


def refresh_expired_device_token() -> str:
    """Refresh D-HRB-03 amend 2 (spec 022 v1.2.0): token expirado → fresco.

    Los escritorios activados por el admin con token one-time (spec 044) no
    tienen ``id-token.txt`` de ``trust-login``: su propio device token
    auténtico es la credencial. ``POST /api/devices/token/refresh`` devuelve
    un token fresco si el device sigue ``active`` y el token se emitió dentro
    de la ventana del server; 401 si fue revocado, es muy viejo o forjado.

    -> "active" (token fresco en disco) | "rejected" (el server no lo
    refresca: revocado/viejo/endpoint ausente) | "network" | "no_token" |
    "busy" (otro proceso del home tiene el lock).
    """
    device = load_device_token()
    if device is None or not device.get("token"):
        return "no_token"
    with _HomeLock(timeout_s=AUTOENROLL_LOCK_WAIT_S) as lock:
        if not lock.acquired:
            return "busy"
        # Otro proceso del home pudo refrescar ya (mismo lock):
        device = load_device_token()
        if device is None or not device.get("token"):
            return "no_token"
        if not device_token_expired(device):
            return "active"
        try:
            status, payload = _request(
                "POST", "/api/devices/token/refresh",
                {"device_token": device["token"]},
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            _log("device_token_refresh_failed", outcome="network", detail=str(exc))
            return "network"
        fresh = payload.get("device_token") if status == 200 else None
        if not fresh:
            _log("device_token_refresh_failed", outcome="rejected", status=status,
                 detail=_error_message(payload, ""))
            return "rejected"
        path = trust_dir() / "device-token.json"
        data: dict[str, Any] = {}
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data.update(existing)  # preserva org, model_key, model_base_url…
        except (OSError, ValueError):
            pass
        data.update({
            "device_id": str(payload.get("device_id") or device["device_id"]),
            "token": str(fresh),
            "renewed_at": int(time.time()),
        })
        _write_secret(path, json.dumps(data, indent=2))
    _log("device_token_refreshed", device_id=data.get("device_id"))
    return "active"


def recover_expired_token(poll_timeout_s: float = AUTO_ENROLL_POLL_S) -> str:
    """Recuperación D-HRB-03(c) (spec 022 v1.1.0, REQ-HRB-010b).

    Device token expirado (agente apagado > TTL) → UN re-enroll con el
    id_token fresco que ``trust-login`` dejó en ``id-token.txt``: el server
    re-emite el token DIRECTO (200) para un device ``active`` del mismo
    usuario. -> outcome del flujo ("active" = token fresco en disco).

    Llamado por el ``BundleSyncer`` (bundle_client) en su ciclo cuando
    detecta la expiración local; mismo lock de home que el auto-enroll
    (dos procesos del mismo home no se pisan).
    """
    org_code = org_binding()
    if not org_code:
        return "no_binding"
    try:
        id_token = _read_id_token(None)
    except FileNotFoundError:
        return "no_id_token"

    with _HomeLock(timeout_s=AUTOENROLL_LOCK_WAIT_S) as lock:
        if not lock.acquired:
            return "busy"
        # Otro proceso del home pudo recuperar ya (mismo lock):
        device = load_device_token()
        if device is not None and not device_token_expired(device):
            return "active"
        outcome, info = _enroll_attempt(org_code, id_token, poll_timeout_s)

    if outcome == "active":
        _log("token_recovered", org_code=org_code,
             device_id=info.get("device_id"), reissued=bool(info.get("reissued")))
    else:
        _log("token_recovery_failed", org_code=org_code, outcome=outcome,
             detail=info.get("message", ""))
    return outcome


def _auto_enroll_loop(stop: threading.Event) -> None:
    """Ciclo zero-UI: reintenta hasta lograr token o perder precondiciones.

    La aprobación del admin es asíncronica: mientras esté pending (o el
    enrollment token se consumió), reintenta cada AUTO_ENROLL_INTERVAL_S —
    el upsert por fingerprint (spec 007) hace seguro el reintento. Termina
    al obtener device token (el BundleSyncer lo recoge en su próximo tick).
    "busy" (otro proceso del home tiene el lock) se reintenta igual: el
    otro proceso está haciendo el trabajo de este home.
    """
    backoff = AUTO_ENROLL_INTERVAL_S
    while not stop.is_set():
        outcome = auto_enroll_once()
        if outcome in ("already_enrolled", "active"):
            return
        if outcome == "needs_activation":
            # Admin-activated home whose token cannot be refreshed (revoked
            # or too old): nothing to do until a new activation lands; a slow
            # re-check keeps the log quiet and picks up the new token.
            backoff = 300.0
        elif outcome in ("no_binding", "no_id_token"):
            backoff = max(backoff, 60.0)  # espera a que el desktop escriba
        elif outcome == "network":
            backoff = min(60.0, backoff * 2.0)
        else:
            backoff = AUTO_ENROLL_INTERVAL_S
        stop.wait(backoff)


def start_auto_enroll_watcher() -> threading.Event:
    """Arranca el watcher daemon. Retorna el Event de parada (tests)."""
    stop = threading.Event()
    threading.Thread(
        target=_auto_enroll_loop,
        args=(stop,),
        name="trust-auto-enroll",
        daemon=True,
    ).start()
    return stop


def register(ctx) -> None:
    """Registra el comando /trust-enroll + el watcher de auto-enroll zero-UI.

    El watcher es un hilo daemon SIN I/O en este register (rápido): solo
    cuando el perfil tiene binding de org + id_token de primera ejecución
    intenta enrolar en background (mandato Miller: nada de pantallas).
    """
    ctx.register_command(
        "trust-enroll",
        cmd_trust_enroll,
        description=(
            "Enroll this Hermes instance into a Trust Agent organization "
            "(device keypair + admin approval + device token)."
        ),
        args_hint="<org_code> [id_token_file]",
    )
    try:
        start_auto_enroll_watcher()
    except Exception:  # noqa: BLE001 - el watcher JAMÁS tumba el registro
        pass

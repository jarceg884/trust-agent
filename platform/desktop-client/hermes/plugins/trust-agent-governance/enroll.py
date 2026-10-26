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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TOOLSET = "trust_agent"

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
    """Home real del perfil activo ($HERMES_HOME resuelto, nunca hardcodeado)."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 - fuera del runtime de Hermes (tests)
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
    *, device_id: str, token: str, org_code: str, tenant_slug: str
) -> Path:
    path = trust_dir() / "device-token.json"
    payload = {
        "device_id": device_id,
        "token": token,
        "enrolled_at": int(time.time()),
        "org_code": org_code,
        "tenant_slug": tenant_slug,
    }
    _write_secret(path, json.dumps(payload, indent=2))
    return path


def load_device_token() -> dict[str, str] | None:
    """Almacén canónico: device-token.json > env TRUST_DEVICE_TOKEN."""
    path = trust_dir() / "device-token.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("token") and data.get("device_id"):
                return {"device_id": str(data["device_id"]), "token": str(data["token"])}
        except (ValueError, OSError):
            pass
    env = os.environ.get("TRUST_DEVICE_TOKEN", "").strip()
    if env:
        # El env v1 no porta device_id: se resuelve con el claim del token
        # en el primer sync (enforcement lo tolera; aud se valida si hay id).
        return {"device_id": os.environ.get("TRUST_DEVICE_ID", ""), "token": env}
    return None


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

    try:
        _, public_pem, fingerprint = load_or_create_keypair()
    except Exception as exc:  # noqa: BLE001 - reportar, no tumbar
        return f"Enrollment failed: could not generate the device keypair: {exc}"

    device_name = os.environ.get("TRUST_DEVICE_NAME") or socket.gethostname()
    timeout_s = float(os.environ.get("TRUST_ENROLL_TIMEOUT", "120"))

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
            },
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return (f"Enrollment failed: control plane unreachable at "
                f"{api_base()} ({exc}). Check TRUST_AGENT_API and your network.")
    if status == 403:
        return ("Enrollment failed: MFA evidence missing in the id_token "
                f"(acr>=2 required). {_error_message(payload, 'MFA required')}")
    if status >= 400:
        return f"Enrollment failed: {_error_message(payload, f'HTTP {status}')}"

    enrollment_token = payload.get("enrollment_token", "")
    device_id = payload.get("device_id", "")
    if not enrollment_token or not device_id:
        return f"Enrollment failed: unexpected response from control plane: {payload}"

    # El admin debe aprobar el dispositivo mientras hacemos polling.
    outcome, ex = _poll_exchange(enrollment_token, timeout_s)
    if outcome == "active":
        token = ex.get("device_token", "")
        if not token:
            return f"Enrollment failed: exchange response without token: {ex}"
        path = save_device_token(
            device_id=device_id, token=token,
            org_code=org_code, tenant_slug=org_code,
        )
        return (
            "Device enrolled and ACTIVE.\n"
            f"  device_id:    {device_id}\n"
            f"  organization: {org_code}\n"
            f"  token stored: {path} (0600)\n"
            "The governance bundle will sync on the next turn; ask me to run "
            "governance_status to confirm NORMAL mode."
        )
    if outcome == "pending":
        return (
            "Enrollment is PENDING admin approval.\n"
            f"  device_id: {device_id}\n"
            f"  Polling timed out after {timeout_s:.0f}s. Re-run "
            f"/trust-enroll {org_code} once an admin approves the device "
            "(safe: the fingerprint upserts, no duplicate rows)."
        )
    if outcome == "gone":
        return ("Enrollment failed: the enrollment token was rejected or "
                "already used (410). Re-run /trust-enroll to request a fresh "
                f"one. Details: {_error_message(ex, 'gone')}")
    return f"Enrollment failed during exchange: {_error_message(ex, 'exchange error')}"


def register(ctx) -> None:
    """Registra el comando /trust-enroll (API pública de plugins)."""
    ctx.register_command(
        "trust-enroll",
        cmd_trust_enroll,
        description=(
            "Enroll this Hermes instance into a Trust Agent organization "
            "(device keypair + admin approval + device token)."
        ),
        args_hint="<org_code> [id_token_file]",
    )

"""RFC 6238 TOTP — verificación first-party del operador (spec 025 D-PS-05).

El operador NO vive en ningún realm de Keycloak (está fuera del
multi-tenant), así que el TOTP del spec 011 (KC ``CONFIGURE_TOTP`` + claim
``acr``) no aplica: el control plane verifica el código él mismo con el
algoritmo estándar RFC 6238 — SHA-1, 6 dígitos, paso de 30 s, secreto de
160 bits base32, ventana de deriva ±1 paso. Mismo algoritmo que usan los
authenticators (Google Authenticator, etc.) y el OTP Form de Keycloak.

La corrección se fija con los vectores de prueba del RFC 6238 (tests/
test_provider_superuser.py): con la semilla ASCII del RFC los códodos en
T=59/1111111109/… coinciden exactamente.
"""
import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1  # ±1 paso (±30 s) de tolerancia de reloj
SECRET_BYTES = 20  # 160 bits, estándar de facto


def generate_secret() -> str:
    """Secret nuevo de 160 bits, base32 sin padding (formato otpauth)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode(
        "ascii"
    ).rstrip("=")


def _decode_secret(secret_b32: str) -> bytes:
    """Base32 → bytes; tolera padding ausente y minúsculas."""
    normalized = secret_b32.strip().upper().replace(" ", "")
    pad = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + pad)


def totp_at(key: bytes, timestamp: int, step: int = STEP_SECONDS) -> str:
    """Código TOTP (RFC 4226 HMAC-SHA-1 truncado) para un epoch dado."""
    counter = int(timestamp // step)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (
        int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    ) % (10 ** DIGITS)
    return f"{code:0{DIGITS}d}"


def verify(
    secret_b32: str,
    code: str,
    *,
    at: int | None = None,
    drift: int = DRIFT_STEPS,
) -> bool:
    """Constant-time verify con ventana ±drift pasos. Fail-closed: todo
    input que no sean DIGITS dígitos → False (sin excepciones)."""
    if not code or not code.isdigit() or len(code) != DIGITS:
        return False
    try:
        key = _decode_secret(secret_b32)
    except Exception:
        return False
    now = int(time.time()) if at is None else int(at)
    for offset in range(-drift, drift + 1):
        ts = now + offset * STEP_SECONDS
        if ts < 0:
            # Contador HOTP negativo no existe (RFC 4226 es unsigned 8 bytes):
            # saltar el paso en vez de reventar (fail-closed, sin excepción).
            continue
        expected = totp_at(key, ts)
        if hmac.compare_digest(expected, code):
            return True
    return False


def otpauth_url(
    secret_b32: str, username: str, issuer: str = "Trust Agent Provider"
) -> str:
    """URL otpauth://totp para el QR / entrada manual del authenticator."""
    label = urllib.parse.quote(f"{issuer}:{username}")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret_b32}"
        f"&issuer={urllib.parse.quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )

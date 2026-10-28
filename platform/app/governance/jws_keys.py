"""Claves de firma del governance bundle (spec 019, D-GOH-02, ADR-016).

El bundle viaja como JWS compacto RS256 firmado por el control plane; la
clave pública se publica en ``GET /.well-known/policy-jwks.json`` y se
pinea en el plugin (``trust_agent_policy_pubkey.pem``) vía la profile
distribution. HS256 está prohibido (constitution IV).

DECISIÓN (dónde vive la clave): el par de claves vive en el ENTORNO del
control plane — ``TRUST_POLICY_JWS_PRIVATE_KEY_B64`` (PEM PKCS#8 RSA,
base64) — siguiendo el patrón existente de secretos fail-closed
(``TRUST_DEVICE_TOKEN_SECRET``, ``TRUST_VAULT_KEK_B64``): arranque
fail-closed sin clave, rotación = nuevo valor de env + ``kid`` nuevo
(JWKS + clave pineada del plugin rotan con la distribución). Alternativa
descartada: par en DB con privada cifrada — más compleja (envelope KEK,
job de rotación) sin beneficio adicional en el despliegue single-node
actual (constitution VII); el env ya ES el almacén de secretos del
servicio y la clave nunca toca disco de la DB.

El ``kid`` es el thumbprint JWK RFC 7638 de la clave pública: el plugin
lo recalcula desde su clave pineada y rechaza cualquier bundle cuyo
header no coincida (rotación verificable, cero estado compartido).
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ..config import Settings

ALG = "RS256"


def _load_private_pem(settings: Settings) -> bytes:
    raw = settings.policy_jws_private_key_b64
    if not raw or "change-me" in raw.lower():
        raise RuntimeError(
            "TRUST_POLICY_JWS_PRIVATE_KEY_B64 is missing or still a "
            "placeholder. Generate a signing keypair for the governance "
            "bundle (spec 019) with: python -c \"from cryptography."
            "hazmat.primitives.asymmetric import rsa; from cryptography."
            "hazmat.primitives import serialization; k=rsa."
            "generate_private_key(public_exponent=65537, key_size=2048); "
            "print(base64.b64encode(k.private_bytes(serialization.Encoding."
            "PEM, serialization.PrivateFormat.PKCS8, serialization."
            "NoEncryption())).decode())\" — bundle signing is fail-closed "
            "(constitution IV)."
        )
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001 - fail-closed con mensaje claro
        raise RuntimeError(
            "TRUST_POLICY_JWS_PRIVATE_KEY_B64 is not valid base64: "
            f"{exc}. Bundle signing is fail-closed (constitution IV)."
        ) from exc


@lru_cache(maxsize=4)
def _cached_key(settings_url: str, raw_b64: str):
    # Cache por contenido de la clave (no por objeto Settings, que no es
    # hashable): get_settings() es lru_cache así que en la práctica hay
    # una entrada por proceso.
    del settings_url
    try:
        key = serialization.load_pem_private_key(
            base64.b64decode(raw_b64, validate=True), password=None
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"TRUST_POLICY_JWS_PRIVATE_KEY_B64 does not decode to a valid "
            f"PKCS#8 RSA private key: {exc}. Bundle signing is fail-closed "
            "(constitution IV)."
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise RuntimeError(
            "The governance bundle signing key must be RSA >= 2048 bits "
            "(spec 019 §5.1: RS256; HS256 is forbidden)."
        )
    return key


def load_signing_key(settings: Settings) -> rsa.RSAPrivateKey:
    """Clave privada de firma (fail-closed; valida presencia y formato)."""
    if not settings.policy_jws_private_key_b64:
        raise RuntimeError(
            "TRUST_POLICY_JWS_PRIVATE_KEY_B64 is not set — the governance "
            "bundle cannot be signed. Generate a keypair (see "
            "app/governance/jws_keys.py docstring) and set it before "
            "starting the control plane (fail-closed, constitution IV)."
        )
    _load_private_pem(settings)  # valida placeholder/base64 con buen mensaje
    return _cached_key("settings", settings.policy_jws_private_key_b64)


def public_key(settings: Settings) -> rsa.RSAPublicKey:
    return load_signing_key(settings).public_key()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def public_jwk(settings: Settings) -> dict:
    """JWK pública con ``kid`` (thumbprint RFC 7638) — la entrada del JWKS."""
    pub = public_key(settings)
    numbers = pub.public_numbers()
    jwk = {
        "kty": "RSA",
        "n": _b64url(
            numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        ),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }
    return {**jwk, "kid": jwk_thumbprint(jwk), "alg": ALG, "use": "sig"}


def jwk_thumbprint(jwk: dict) -> str:
    """Thumbprint RFC 7638 (sha256 del JSON canónico con kty/n/e sorted)."""
    canonical = json.dumps(
        {k: jwk[k] for k in ("e", "kty", "n")}, sort_keys=True, separators=(",", ":")
    ).encode()
    return _b64url(hashlib.sha256(canonical).digest())


def public_jwks(settings: Settings) -> dict:
    return {"keys": [public_jwk(settings)]}


def private_pem(settings: Settings) -> bytes:
    """PEM PKCS#8 de la clave privada (para jwt.encode)."""
    return load_signing_key(settings).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

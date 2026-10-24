"""Envelope encryption primitive (REQ-VLT-001, ADR-009, D-CP-14).

    ciphertext_secret = AES-256-GCM(secret, DEK, nonce)
    ciphertext_dek    = AES-256-GCM(DEK, KEK, nonce)

One random nonce per credential, reused for both cipherments: nonce reuse is
forbidden per KEY, and the two operations use different keys (DEK vs KEK),
so a single fresh nonce per credential is safe and fits the schema's single
`nonce` column. The KEK NEVER lives in the database.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..errors import service_unavailable

_NONCE_LEN = 12
_KEY_LEN = 32


def load_kek(kek_b64: str) -> bytes:
    """Decode and validate the master key (32 bytes, base64)."""
    try:
        kek = base64.b64decode(kek_b64, validate=True)
    except Exception:
        raise service_unavailable("VAULT_KEK_B64 is not valid base64.")
    if len(kek) != _KEY_LEN:
        raise service_unavailable("VAULT_KEK_B64 must decode to 32 bytes.")
    return kek


def encrypt_secret(
    secret: bytes, kek: bytes, key_id: str
) -> tuple[bytes, bytes, bytes, str]:
    """-> (ciphertext_secret, ciphertext_dek, nonce, key_id)."""
    if len(kek) != _KEY_LEN:
        raise service_unavailable("Invalid KEK length.")
    dek = os.urandom(_KEY_LEN)
    nonce = os.urandom(_NONCE_LEN)
    ciphertext_secret = AESGCM(dek).encrypt(nonce, secret, None)
    ciphertext_dek = AESGCM(kek).encrypt(nonce, dek, None)
    return ciphertext_secret, ciphertext_dek, nonce, key_id


def decrypt_secret(
    ciphertext_secret: bytes, ciphertext_dek: bytes, nonce: bytes, kek: bytes
) -> bytes:
    """Decrypt with the KEK; without the correct KEK this is impossible."""
    dek = AESGCM(kek).decrypt(nonce, ciphertext_dek, None)
    return AESGCM(dek).decrypt(nonce, ciphertext_secret, None)

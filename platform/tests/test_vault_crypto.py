"""AC-CP-052 — vault envelope encryption primitive (ADR-009)."""
import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.errors import ApiError
from app.vault.crypto import decrypt_secret, encrypt_secret, load_kek

from tests.conftest import TEST_KEK


def test_roundtrip():
    kek = load_kek(TEST_KEK)
    secret = b"canary-vmware-api-key-123"
    ct_secret, ct_dek, nonce, key_id = encrypt_secret(secret, kek, "kek-v1")
    assert key_id == "kek-v1"
    assert ct_secret != secret and ct_dek != kek
    assert decrypt_secret(ct_secret, ct_dek, nonce, kek) == secret


def test_canary_not_in_ciphertext():
    kek = load_kek(TEST_KEK)
    secret = b"canary-SECRET-VALUE-9f8e7d"
    ct_secret, ct_dek, nonce, _ = encrypt_secret(secret, kek, "kek-v1")
    assert b"canary-SECRET-VALUE" not in ct_secret
    assert b"canary-SECRET-VALUE" not in ct_dek
    assert b"canary-SECRET-VALUE" not in nonce


def test_wrong_kek_cannot_decrypt():
    kek = load_kek(TEST_KEK)
    other_kek = load_kek(base64.b64encode(b"\x02" * 32).decode())
    secret = b"another-canary-value"
    ct_secret, ct_dek, nonce, _ = encrypt_secret(secret, kek, "kek-v1")
    with pytest.raises(InvalidTag):
        decrypt_secret(ct_secret, ct_dek, nonce, other_kek)


def test_load_kek_validation():
    with pytest.raises(ApiError):
        load_kek("not-base64!!!")
    with pytest.raises(ApiError):
        load_kek(base64.b64encode(b"\x01" * 16).decode())  # wrong length


def test_each_credential_gets_fresh_dek():
    """DEK is random per credential: two cipherments of the same secret differ."""
    kek = load_kek(TEST_KEK)
    a = encrypt_secret(b"same-secret", kek, "k")
    b = encrypt_secret(b"same-secret", kek, "k")
    assert a[0] != b[0] and a[1] != b[1] and a[2] != b[2]

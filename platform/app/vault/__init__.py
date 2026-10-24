"""Vault domain (credential API arrives with spec 006).

This package ships the envelope-encryption primitive (ADR-009): per-credential
DEK (AES-256-GCM) encrypts the secret; the KEK from the environment encrypts
the DEK. Only ciphertext ever reaches the database.
"""

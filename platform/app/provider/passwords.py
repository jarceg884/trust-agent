"""Password hashing argon2id (spec 025 D-PS-02, constitution IV).

``password_hash`` es un PHC string (``$argon2id$v=19$m=65536,t=3,p=4$…``)
con salt aleatoria por usuario — NADA de plaintext ni hashes sin KDF.
``verify_password`` es booleano y sin oracle: el llamador responde el mismo
401 genérico para usuario inexistente y password malo.
"""
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

# Defaults de argon2-cffi: argon2id, m=64 MiB, t=3, p=4 (OWASP-aligned).
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(phc_hash: str, password: str) -> bool:
    """True solo si el password corresponde al hash PHC. Cualquier hash
    malformado o error de verificación → False (fail-closed, sin oracle)."""
    try:
        return _hasher.verify(phc_hash, password)
    except InvalidHashError:
        return False
    except Argon2Error:
        return False

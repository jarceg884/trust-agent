"""Content-addressed store for sanitized marketplace packages (spec 045 §1).

``<agent_profiles_dir>/<sha256>.tar.gz``, written once (tmp + fsync + rename) and never modified:
identical content is stored once across tenants and versions, and a package can only be read by
its digest — a digest that is not 64 lowercase hex chars never becomes a path.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from ..config import get_settings

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def root() -> Path:
    return Path(get_settings().agent_profiles_dir)


def path_for(sha256: str) -> Path:
    if not _SHA_RE.match(sha256 or ""):
        raise ValueError("invalid package digest")
    return root() / f"{sha256}.tar.gz"


def put(data: bytes, sha256: str) -> Path:
    """Store *data* under its digest (idempotent). Refuses data that does not hash to it."""
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("package content does not match its digest")
    target = path_for(sha256)
    if target.is_file() and target.stat().st_size == len(data):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".incoming-", suffix=".tar.gz", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return target


def exists(sha256: str) -> bool:
    try:
        return path_for(sha256).is_file()
    except ValueError:
        return False

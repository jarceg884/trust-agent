"""Sanitize an exported Hermes profile into a canonical marketplace package (spec 045 §2).

The upload is UNTRUSTED. It is read as a stream through a decompression cap (gzip bombs), every
member is validated before its data is read (no links, devices, absolute paths, ``..``, more than
one root), and only distribution-owned content survives — an explicit allowlist at the profile
root, mirroring Hermes' own distribution model (``hermes_cli/profile_distribution.py``
``DEFAULT_DIST_OWNED`` / ``USER_OWNED_EXCLUDE``):

    SOUL.md · config.yaml (governance-pinned keys removed) · skills/** · mcp.json ·
    cron/jobs.json · distribution.yaml · profile.yaml (display_name/description only) ·
    .env.template / .env.EXAMPLE · .no-bundled-skills

Credentials are dropped at any depth, text files are re-scanned for secrets, and the result is
re-packed deterministically (sorted entries, fixed mtime, uid/gid 0, gzip mtime 0): the same
content always yields the same sha256, which is what the signed bundle pins on the device.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
import zlib
from dataclasses import dataclass, field
from typing import Any

import yaml

from .redact import redact_text

# Hermes' canonical profile id (hermes_constants.PROFILE_ID_RE).
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FIXED_MTIME = 946684800  # 2000-01-01T00:00:00Z — deterministic packages.


@dataclass(frozen=True)
class Limits:
    max_compressed: int = 50 * 1024 * 1024
    max_uncompressed: int = 200 * 1024 * 1024
    max_files: int = 5000
    max_file: int = 20 * 1024 * 1024
    max_depth: int = 24
    max_path: int = 512


DEFAULT_LIMITS = Limits()


class ArchiveRejected(Exception):
    """The upload is not an acceptable Hermes profile export (``rule`` is machine-readable)."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class SanitizedPackage:
    slug: str
    data: bytes
    sha256: str
    size: int
    manifest: dict[str, Any] = field(default_factory=dict)


# --- What survives -----------------------------------------------------------------------------
KEEP_ROOT_FILES = frozenset({
    "SOUL.md", "config.yaml", "mcp.json", "distribution.yaml", "profile.yaml",
    ".env.template", ".env.EXAMPLE", ".env.example", ".no-bundled-skills",
})
# profile.yaml is Hermes' metadata ABOUT the profile (hermes_cli/profiles.read_profile_meta):
# only the parts that describe the agent travel; previous_names / ui_meta / description_auto are
# the exporting machine's history and UI state.
PROFILE_META_KEYS = ("display_name", "description")
CRON_STORE = ("cron", "jobs.json")
# Credential stores, dropped at ANY depth (hermes_cli.profiles export exclusions + OS stores).
_CREDENTIAL_FILES = frozenset({
    ".env", "auth.json", ".op.env", ".anthropic_oauth.json", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "credentials.json", "token.json", "id_rsa", "id_ed25519", "id_ecdsa",
})
# Hermes' own credential dirs live at the profile root; OS/tool stores are dropped anywhere.
_ROOT_CREDENTIAL_DIRS = frozenset({"auth", "bot-desktop"})
_CREDENTIAL_DIRS = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".git"})
_CREDENTIAL_NESTED = frozenset({(".config", "gh"), (".config", "gcloud")})
_ENV_KEEP = frozenset({".env.template", ".env.EXAMPLE", ".env.example"})
# config.yaml keys the governance pins on the device (P-26) — never imported (spec 045 §2).
CONFIG_PINNED_KEYS = (
    "model", "providers", "fallback_providers", "provider_routing", "auxiliary",
    "local_runtime", "moa", "gateway", "plugins",
)
# Text files re-scanned for secrets (hermes_cli.profiles._EXPORT_REDACT_SUFFIXES).
_REDACT_SUFFIXES = frozenset({
    ".md", ".txt", ".yaml", ".yml", ".json", ".jsonl", ".toml", ".ini", ".cfg", ".conf", ".py",
    ".sh", ".bash", ".zsh", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".xml", ".csv", ".ps1",
    ".env", ".example", ".template",
})
_SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".ps1", ".bat", ".cmd", ".rb", ".pl"})
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class _CappedReader(io.RawIOBase):
    """Decompressed-byte budget: a gzip bomb dies here, before tarfile buffers anything."""

    def __init__(self, inner, budget: int) -> None:
        self._inner = inner
        self._left = budget

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._inner.read(len(buffer))
        if len(data) > self._left:
            raise ArchiveRejected("too_large", "El perfil descomprimido supera el límite permitido.")
        self._left -= len(data)
        buffer[: len(data)] = data
        return len(data)


def _member_parts(name: str, limits: Limits) -> tuple[str, ...]:
    if not name or name.startswith("/") or "\\" in name or _CONTROL_RE.search(name):
        raise ArchiveRejected("invalid_path", "El archivo contiene rutas absolutas o inválidas.")
    parts = tuple(p for p in name.split("/") if p not in ("", "."))
    if any(p == ".." for p in parts):
        raise ArchiveRejected("path_traversal", "El archivo intenta escribir fuera del perfil (`..`).")
    if not parts:
        raise ArchiveRejected("invalid_path", "El archivo contiene una entrada sin nombre.")
    if len(parts) > limits.max_depth or len(name) > limits.max_path:
        raise ArchiveRejected("invalid_path", "El archivo contiene rutas demasiado profundas o largas.")
    if ":" in parts[0]:
        raise ArchiveRejected("invalid_path", "El archivo contiene rutas con unidad de disco.")
    return parts


def _is_credential(rel: tuple[str, ...]) -> bool:
    if rel[0] in _ROOT_CREDENTIAL_DIRS:
        return True
    for index, part in enumerate(rel):
        if part in _CREDENTIAL_DIRS:
            return True
        if index + 1 < len(rel) and (part, rel[index + 1]) in _CREDENTIAL_NESTED:
            return True
    name = rel[-1]
    lower = name.lower()
    if name in _CREDENTIAL_FILES:
        return True
    if lower.startswith(".env") and name not in _ENV_KEEP:
        return True
    return "oauth" in lower and lower.endswith(".json")


def _drop_label(rel: tuple[str, ...]) -> str | None:
    """None = keep; otherwise the label reported in ``manifest.stripped``."""
    if _is_credential(rel):
        return "credentials"
    if len(rel) == 1:
        return None if rel[0] in KEEP_ROOT_FILES else rel[0]
    top = rel[0]
    if top == "skills":
        # Root-level dot entries are Hermes bookkeeping (.hub, .usage.json, curator state…).
        if rel[1].startswith(".") or rel[-1].endswith(".lock"):
            return "skills-bookkeeping"
        return None
    if top == "cron":
        return None if rel == CRON_STORE else "cron-runtime"
    return top


def _sanitize_config(raw: bytes) -> tuple[bytes, list[str], list[str]]:
    try:
        data = yaml.safe_load(raw.decode("utf-8-sig")) if raw.strip() else {}
    except (yaml.YAMLError, UnicodeDecodeError):
        raise ArchiveRejected("invalid_config", "config.yaml no es YAML válido.") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ArchiveRejected("invalid_config", "config.yaml debe ser un mapa de claves.")
    removed = [key for key in CONFIG_PINNED_KEYS if key in data]
    for key in removed:
        data.pop(key, None)
    out = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return out.encode("utf-8"), removed, [str(k) for k in data]


def _redactable(rel: tuple[str, ...]) -> bool:
    name = rel[-1]
    lower = name.lower()
    if name == ".cursorrules" or lower.startswith("config.yaml"):
        return True
    dot = lower.rfind(".")
    return dot > 0 and lower[dot:] in _REDACT_SUFFIXES or lower in _ENV_KEEP


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _env_names(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().removeprefix("export ").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            names.append(key)
    return names


def _canonical_tar(slug: str, files: dict[str, tuple[bytes, int]]) -> bytes:
    dirs: set[str] = {slug}
    for path in files:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            dirs.add("/".join([slug, *parts[:depth]]))
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            entries = [(d, None) for d in dirs] + [(f"{slug}/{p}", p) for p in files]
            for name, key in sorted(entries, key=lambda e: e[0]):
                info = tarfile.TarInfo(name)
                info.mtime = FIXED_MTIME
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if key is None:
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                else:
                    payload, mode = files[key]
                    info.size = len(payload)
                    info.mode = mode
                    tar.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def sanitize_archive(
    data: bytes, *, slug_override: str | None = None, limits: Limits = DEFAULT_LIMITS
) -> SanitizedPackage:
    """Validate + sanitize an uploaded ``hermes profile export`` archive."""
    if len(data) > limits.max_compressed:
        raise ArchiveRejected("too_large", "El archivo supera el tamaño máximo permitido.")
    if not data.startswith(b"\x1f\x8b"):
        raise ArchiveRejected("not_gzip", "Sube el .tar.gz que genera `hermes profile export`.")
    if slug_override is not None and not PROFILE_ID_RE.match(slug_override):
        raise ArchiveRejected(
            "invalid_name", "El nombre debe usar minúsculas, números, '-' o '_' (máx. 64)."
        )

    root: str | None = None
    kept: dict[str, tuple[bytes, int]] = {}
    stripped: dict[str, int] = {}
    file_count = 0
    total = 0
    try:
        stream = _CappedReader(gzip.GzipFile(fileobj=io.BytesIO(data)), limits.max_uncompressed + 4 * 1024 * 1024)
        with tarfile.open(fileobj=io.BufferedReader(stream), mode="r|") as tar:
            for member in tar:
                parts = _member_parts(member.name, limits)
                if root is None:
                    root = parts[0]
                elif parts[0] != root:
                    raise ArchiveRejected(
                        "multiple_roots",
                        "El archivo debe contener un único perfil (una sola carpeta raíz).",
                    )
                if member.issym() or member.islnk():
                    raise ArchiveRejected("link", "El archivo contiene enlaces simbólicos o duros.")
                if member.isdir():
                    continue  # directories are rebuilt from the kept files
                if not member.isfile():
                    raise ArchiveRejected("special_file", "El archivo contiene dispositivos o FIFOs.")
                file_count += 1
                if file_count > limits.max_files:
                    raise ArchiveRejected("too_many_files", "El perfil contiene demasiados archivos.")
                if member.size > limits.max_file:
                    raise ArchiveRejected("file_too_large", f"El archivo {'/'.join(parts[1:])} es demasiado grande.")
                total += member.size
                if total > limits.max_uncompressed:
                    raise ArchiveRejected("too_large", "El perfil descomprimido supera el límite permitido.")
                rel = parts[1:]
                if not rel:
                    raise ArchiveRejected("invalid_path", "La raíz del perfil debe ser una carpeta.")
                label = _drop_label(rel)
                if label is not None:
                    stripped[label] = stripped.get(label, 0) + 1
                    continue
                handle = tar.extractfile(member)
                payload = handle.read() if handle is not None else b""
                mode = 0o755 if member.mode & 0o111 else 0o644
                kept["/".join(rel)] = (payload, mode)
    except ArchiveRejected:
        raise
    except (tarfile.TarError, OSError, EOFError, zlib.error):
        raise ArchiveRejected("corrupt", "El archivo está dañado o no es un .tar.gz válido.") from None

    if root is None:
        raise ArchiveRejected("empty", "El archivo está vacío.")
    slug = slug_override or root
    if not PROFILE_ID_RE.match(slug):
        raise ArchiveRejected(
            "invalid_name",
            f"'{root}' no es un nombre de perfil de Hermes válido: indica un nombre en minúsculas.",
        )
    if slug == "default":
        raise ArchiveRejected(
            "default_profile",
            "Exportaste el perfil principal ('default'): indica un nombre para el perfil.",
        )

    manifest: dict[str, Any] = {"format": "hermes-export"}
    config_removed: list[str] = []
    if "config.yaml" in kept:
        payload, mode = kept["config.yaml"]
        cleaned, config_removed, config_keys = _sanitize_config(payload)
        kept["config.yaml"] = (cleaned, mode)
        manifest["config_keys"] = config_keys
    manifest["config_removed_keys"] = config_removed

    profile_meta: dict[str, str] = {}
    if "profile.yaml" in kept:
        try:
            meta = yaml.safe_load(kept["profile.yaml"][0]) or {}
        except yaml.YAMLError:
            meta = {}
        if isinstance(meta, dict):
            profile_meta = {
                k: str(meta.get(k) or "").strip()[:240]
                for k in PROFILE_META_KEYS if str(meta.get(k) or "").strip()
            }
        if profile_meta:
            kept["profile.yaml"] = (
                yaml.safe_dump(profile_meta, allow_unicode=True, sort_keys=True).encode(),
                kept["profile.yaml"][1],
            )
        else:
            del kept["profile.yaml"]
    manifest["profile_meta"] = profile_meta

    redactions = 0
    for path, (payload, mode) in list(kept.items()):
        rel = tuple(path.split("/"))
        if not _redactable(rel):
            continue
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        cleaned_text, hits = redact_text(text)
        if hits:
            redactions += hits
            kept[path] = (cleaned_text.encode("utf-8"), mode)
    manifest["redactions"] = redactions

    soul = kept.get("SOUL.md", (b"", 0))[0].decode("utf-8", "replace")
    manifest["has_soul"] = bool(soul.strip())
    manifest["soul_excerpt"] = soul.strip()[:800]

    skills = []
    for path in sorted(kept):
        if path.startswith("skills/") and path.endswith("/SKILL.md"):
            skill_dir = path[len("skills/") : -len("/SKILL.md")]
            front = _frontmatter(kept[path][0].decode("utf-8", "replace"))
            prefix = f"skills/{skill_dir}/"
            has_scripts = any(
                other.startswith(prefix)
                and (other[len(prefix):].startswith("scripts/") or kept[other][1] & 0o111
                     or other[other.rfind("."):].lower() in _SCRIPT_SUFFIXES)
                for other in kept
            )
            skills.append({
                "path": skill_dir,
                "name": str(front.get("name") or skill_dir.rsplit("/", 1)[-1]),
                "description": str(front.get("description") or "")[:240],
                "has_scripts": has_scripts,
            })
    manifest["skills"] = skills
    manifest["has_scripts"] = any(s["has_scripts"] for s in skills)

    mcp_servers: set[str] = set()
    if "config.yaml" in kept:
        cfg = yaml.safe_load(kept["config.yaml"][0]) or {}
        if isinstance(cfg.get("mcp_servers"), dict):
            mcp_servers.update(str(k) for k in cfg["mcp_servers"])
    if "mcp.json" in kept:
        try:
            doc = json.loads(kept["mcp.json"][0])
        except (ValueError, UnicodeDecodeError):
            doc = None
        if isinstance(doc, dict):
            for key in ("mcpServers", "servers", "mcp_servers"):
                if isinstance(doc.get(key), dict):
                    mcp_servers.update(str(k) for k in doc[key])
    manifest["mcp_servers"] = sorted(mcp_servers)

    cron_jobs: list[dict] = []
    if "cron/jobs.json" in kept:
        try:
            doc = json.loads(kept["cron/jobs.json"][0])
        except (ValueError, UnicodeDecodeError):
            doc = None
        jobs = doc.get("jobs") if isinstance(doc, dict) else doc
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for job in jobs if isinstance(jobs, list) else []:
            if isinstance(job, dict):
                cron_jobs.append({
                    "name": str(job.get("name") or job.get("id") or "")[:120],
                    "schedule": str(job.get("schedule") or job.get("cron") or "")[:120],
                })
    manifest["cron_jobs"] = cron_jobs

    env_requires: list[str] = []
    distribution = None
    if "distribution.yaml" in kept:
        try:
            dist = yaml.safe_load(kept["distribution.yaml"][0]) or {}
        except yaml.YAMLError:
            dist = {}
        if isinstance(dist, dict):
            manifest["format"] = "hermes-distribution"
            distribution = {k: str(dist.get(k) or "")[:240] for k in ("name", "version", "description", "author", "license")}
            for item in dist.get("env_requires") or []:
                if isinstance(item, dict) and item.get("name"):
                    env_requires.append(str(item["name"]))
    for name in _ENV_KEEP:
        if name in kept:
            env_requires.extend(_env_names(kept[name][0].decode("utf-8", "replace")))
    manifest["env_requires"] = sorted(set(env_requires))
    manifest["distribution"] = distribution

    manifest["stripped"] = dict(sorted(stripped.items()))
    manifest["file_count"] = len(kept)
    manifest["total_size"] = sum(len(p) for p, _ in kept.values())
    manifest["files"] = [{"path": p, "size": len(kept[p][0])} for p in sorted(kept)][:2000]

    package = _canonical_tar(slug, kept)
    return SanitizedPackage(
        slug=slug, data=package, sha256=hashlib.sha256(package).hexdigest(),
        size=len(package), manifest=manifest,
    )

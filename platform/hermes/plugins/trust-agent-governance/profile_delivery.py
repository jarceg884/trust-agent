"""Spec 045 §6 — materialización de los perfiles de la organización en el equipo.

En cada sync verificado, :func:`reconcile` lleva ``<root>/profiles`` al estado que nombra el
bundle FIRMADO (``policy_profiles``):

- **Instalar / actualizar** (D-045-E): el paquete se descarga del control plane y su sha256 debe
  ser el del bundle firmado (el edge es HTTP plano: un paquete alterado en tránsito se rechaza).
  Se extrae con reglas estrictas a un staging del mismo volumen y se sustituyen SOLO las rutas del
  paquete; memorias, sesiones, ``state.db`` y workspace del empleado se conservan (semántica
  ``update_distribution`` de Hermes). Una instalación nueva se arma entera en staging —config ya
  gobernado, plugin copiado, marcador— y aparece con UN rename: Hermes nunca ve un perfil a medias
  ni uno sin gobernanza. En una actualización cada archivo se sustituye de forma atómica y el
  marcador se escribe AL FINAL: si algo se interrumpe, el siguiente sync la repite entera.
- **Marcador** ``.trust-agent-managed.json``: solo los perfiles que lo llevan son de la
  organización. Un perfil del empleado nunca se toca; si su nombre choca con el slug, el de la
  organización se instala como ``<slug>-org``.
- **Archivar** (D-045-D): un perfil gestionado que sale de la política (o se archiva en el
  marketplace) se mueve con sus datos a ``trust-agent/archived-profiles/<slug>@<ts>``. Si vuelve,
  se restaura el archivo más reciente del mismo ``profile_id`` y se actualiza a la versión vigente.
  En Windows, mientras el gateway tiene abierta su base, el rename falla: queda pendiente (el
  escritorio ya no lo muestra) y se reintenta en el siguiente sync.
- **Default** (D-045-B): ``trust-agent/managed-profiles.json`` publica para el escritorio los
  perfiles instalados y el default; ``default_seq`` sube solo cuando cambia el default (o cuando se
  instala), para no pelear con un cambio manual del empleado durante la sesión.
- **MCP y cron** (D-045-C, §6.4): ver :func:`apply_runtime_policy`.

Sin red: se conserva lo instalado, se registra el evento y se reintenta en el siguiente sync.
:func:`reconcile` nunca lanza.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import stat
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import bundle_client

try:  # POSIX: flock
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
try:  # Windows: lock por byte (LockFileEx)
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

_VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

MARKER = ".trust-agent-managed.json"
STATE_FILE = "managed-profiles.json"
ARCHIVE_DIR = "archived-profiles"
STAGING_DIR = "profile-staging"
GOVERNANCE_PLUGIN = "trust-agent-governance"
SCHEMA = 1

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")  # hermes_constants.PROFILE_ID_RE
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Límites del parser del equipo = los del servidor (spec 045 §2, sanitize.Limits).
MAX_PACKAGE = 50 * 1024 * 1024
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_FILES = 5000
MAX_FILE = 20 * 1024 * 1024
MAX_DEPTH = 24
MAX_PATH = 512
_HTTP_TIMEOUT = 60.0

# Forma canónica del paquete (sanitize.KEEP_ROOT_FILES + skills/** + cron/jobs.json). El equipo
# acepta EXACTAMENTE esa forma: aunque el servidor ya la garantiza, un paquete con cualquier otra
# ruta (plugins/, .env, memories/, …) se rechaza entero — defensa en profundidad.
ROOT_FILES = frozenset({
    "SOUL.md", "config.yaml", "mcp.json", "distribution.yaml", "profile.yaml",
    ".env.template", ".env.EXAMPLE", ".env.example", ".no-bundled-skills",
})
CRON_STORE = ("cron", "jobs.json")
_CREDENTIAL_NAMES = frozenset({
    ".env", "auth.json", ".op.env", ".anthropic_oauth.json", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "credentials.json", "token.json", "id_rsa", "id_ed25519", "id_ecdsa",
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".git",
})
# Archivos que el equipo GENERA (no se copian tal cual del paquete).
_GENERATED = frozenset({"config.yaml", "profile.yaml", "/".join(CRON_STORE)})
# Directorios de usuario de un perfil nuevo (hermes_cli.profiles._PROFILE_DIRS).
PROFILE_DIRS = ("memories", "sessions", "skills", "skins", "logs", "plans", "workspace", "cron", "home")

_WIN_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)
_WIN_BAD_CHARS = frozenset('<>:"|?*\\')
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PackageRejected(Exception):
    """El paquete no es aceptable (sha distinto, forma no canónica, límites)."""


class PackageUnavailable(Exception):
    """No se pudo obtener el paquete ahora (red, 404 transitorio): se reintenta en el próximo sync."""


class Deferred(Exception):
    """La operación choca con archivos en uso (Windows): se reintenta en el próximo sync."""


# ---------------------------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yaml():
    import yaml  # copia vendor del plugin

    return yaml


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)  # Python ≥ 3.12 (junctions de Windows)
    return bool(isjunction and isjunction(path))


def _retry(fn, *args, attempts: int = 5, delay: float = 0.2):
    """Windows: un archivo abierto por otro proceso (lectura de Hermes) rechaza replace/unlink
    con PermissionError unos instantes. Tras los reintentos → :class:`Deferred`."""
    for i in range(attempts):
        try:
            return fn(*args)
        except PermissionError as exc:
            if i == attempts - 1:
                raise Deferred(f"{getattr(fn, '__name__', 'op')}: {exc}") from None
            time.sleep(delay * (i + 1))
    return None


def _atomic_write(dest: Path, data: bytes) -> bool:
    """Escribe *dest* de forma atómica (tmp en el mismo directorio + replace). False si ya era igual."""
    try:
        if dest.is_file() and not _is_link(dest) and dest.read_bytes() == data:
            return False
    except OSError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if _is_link(dest):
            _retry(os.unlink, dest)
        _retry(os.replace, tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def _write_json(dest: Path, data: dict[str, Any]) -> bool:
    return _atomic_write(dest, (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def _dump_yaml(data: dict[str, Any]) -> bytes:
    return _yaml().safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")


def _load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        loaded = _yaml().safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    except Exception:  # noqa: BLE001 - yaml.YAMLError sin importar el tipo
        return {}
    return loaded if isinstance(loaded, dict) else {}


class _Lock:
    """Lock exclusivo NO bloqueante sobre ``trust-agent/.profiles.lock``.

    En el gateway multiplexado cada perfil carga su propia instancia del plugin (y su BundleSyncer):
    la materialización se serializa entre hilos y procesos; quien no obtiene el lock se salta el
    ciclo (otro está reconciliando el mismo estado, y todo es idempotente)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False
        self._fh = None

    def __enter__(self) -> "_Lock":
        if fcntl is None and msvcrt is None:
            self.acquired = True
            return self
        try:
            fh = self.path.open("a+")
        except OSError:
            return self
        try:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return self
        self._fh = fh
        self.acquired = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._fh, fcntl.LOCK_UN)
                else:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._fh.close()
            self._fh = None
        self.acquired = False


# ---------------------------------------------------------------------------------------------
# Entradas del bundle
# ---------------------------------------------------------------------------------------------
def desired_profiles(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Entradas válidas de ``policy_profiles``; ``None`` si el bundle no lo trae (servidor previo
    a la 045: no se toca nada). Las filas legacy ``{name, model}`` (spec 044) no son entregables."""
    raw = payload.get("policy_profiles") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("profile_id") or "")
        slug = str(item.get("slug") or "")
        sha = str(item.get("sha256") or "")
        if not (_UUID_RE.match(pid) and PROFILE_ID_RE.match(slug) and _SHA_RE.match(sha)):
            continue
        if pid in seen:
            continue
        # El bundle está firmado, pero el equipo solo descarga de SU ruta de paquetes.
        if item.get("package_path") != f"/api/me/agent-profiles/{pid}/package":
            continue
        try:
            version = int(item.get("version") or 0)
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0 or size > MAX_PACKAGE:
            continue
        seen.add(pid)
        out.append({
            "profile_id": pid, "slug": slug, "sha256": sha, "version": version, "size": size,
            "name": str(item.get("name") or slug)[:200],
            "description": str(item.get("description") or "")[:2000],
            "model": str(item.get("model") or ""),
            "default": bool(item.get("default")),
            "package_path": item["package_path"],
        })
    if out and not any(e["default"] for e in out):
        out[0]["default"] = True
    return out


# ---------------------------------------------------------------------------------------------
# Paquete: descarga verificada + extracción estricta
# ---------------------------------------------------------------------------------------------
def download_package(entry: dict[str, Any], device: dict[str, str], dest_dir: Path) -> Path:
    """Descarga el paquete a *dest_dir* y verifica tamaño y sha256 contra el bundle firmado."""
    url = f"{bundle_client.api_base()}{entry['package_path']}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {device['token']}", "X-Device-Token": device["token"]}
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest_dir), prefix=".pkg-", suffix=".tar.gz")
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            try:
                with urllib.request.urlopen(
                    req, timeout=_HTTP_TIMEOUT, context=ssl.create_default_context()
                ) as resp:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > entry["size"] or total > MAX_PACKAGE:
                            raise PackageRejected("package larger than the signed bundle says")
                        digest.update(chunk)
                        out.write(chunk)
            except urllib.error.HTTPError as exc:
                raise PackageUnavailable(f"HTTP {exc.code}") from None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise PackageUnavailable(str(exc)) from None
        if digest.hexdigest() != entry["sha256"]:
            raise PackageRejected("sha256 does not match the signed bundle")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return Path(tmp)


def _check_parts(name: str) -> tuple[str, ...]:
    if not name or len(name) > MAX_PATH or _CONTROL_RE.search(name) or "\\" in name:
        raise PackageRejected(f"invalid path in package: {name[:80]!r}")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PackageRejected(f"absolute path in package: {name[:80]!r}")
    parts = tuple(p for p in name.split("/") if p not in ("",))
    if not parts or len(parts) > MAX_DEPTH:
        raise PackageRejected(f"invalid path in package: {name[:80]!r}")
    for part in parts:
        if part in (".", ".."):
            raise PackageRejected(f"path traversal in package: {name[:80]!r}")
        if set(part) & _WIN_BAD_CHARS or part.endswith((" ", ".")):
            raise PackageRejected(f"path not valid on Windows: {name[:80]!r}")
        if part.split(".")[0].lower() in _WIN_RESERVED:
            raise PackageRejected(f"reserved Windows name in package: {name[:80]!r}")
    return parts


def _allowed_rel(rel: tuple[str, ...]) -> bool:
    if any(p in _CREDENTIAL_NAMES for p in rel):
        return False
    if len(rel) == 1:
        return rel[0] in ROOT_FILES
    if rel[0] == "skills":
        return True
    return rel == CRON_STORE


def extract_package(pkg: Path, slug: str, dest: Path) -> list[str]:
    """Extrae el paquete canónico a *dest* (creado vacío). Un único directorio raíz == *slug*,
    solo archivos regulares, solo la forma canónica, límites del servidor. Devuelve las rutas
    relativas (``/``) de los archivos, ordenadas. Cualquier violación → :class:`PackageRejected`."""
    dest.mkdir(parents=True, exist_ok=False)
    files: list[str] = []
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(pkg, "r:gz") as tar:
            for member in tar:
                parts = _check_parts(member.name)
                if parts[0] != slug:
                    raise PackageRejected("package root does not match the profile slug")
                if member.isdir():
                    continue
                if not member.isreg():
                    raise PackageRejected(f"links and special files are not allowed: {member.name[:80]!r}")
                rel = parts[1:]
                if not rel or not _allowed_rel(rel):
                    raise PackageRejected(f"path not allowed in a profile package: {member.name[:80]!r}")
                key = "/".join(rel)
                if key.casefold() in seen:
                    raise PackageRejected(f"duplicate path in package: {key[:80]!r}")
                seen.add(key.casefold())
                if member.size > MAX_FILE:
                    raise PackageRejected(f"file too large: {key[:80]!r}")
                total += member.size
                if total > MAX_UNCOMPRESSED or len(files) >= MAX_FILES:
                    raise PackageRejected("package exceeds the size limits")
                src = tar.extractfile(member)
                if src is None:
                    raise PackageRejected(f"unreadable member: {key[:80]!r}")
                target = dest.joinpath(*rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > member.size:
                            raise PackageRejected(f"member larger than declared: {key[:80]!r}")
                        out.write(chunk)
                files.append(key)
    except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
        raise PackageRejected(f"unreadable package: {exc}") from None
    return sorted(files)


# ---------------------------------------------------------------------------------------------
# Política en runtime sobre un perfil gestionado (modelo, metadatos, MCP, cron)
# ---------------------------------------------------------------------------------------------
def _config_enforcement():
    try:
        from . import config_enforcement  # lane spec 030 (siempre presente en el desktop)

        return config_enforcement
    except ImportError:  # pragma: no cover - núcleo v2 sin lanes
        return None


def governed_config(
    cfg: dict[str, Any], entry: dict[str, Any], payload: dict[str, Any], *, apply_model: bool
) -> dict[str, Any]:
    """Config de un perfil gestionado: el del paquete + P-26 + el modelo que la política asigna
    a ESTE perfil (solo si *apply_model*: al instalar/actualizar o cuando la política lo cambia)."""
    cfg = dict(cfg)
    if apply_model and entry.get("model"):
        model = cfg.get("model")
        model = dict(model) if isinstance(model, dict) else {}
        model["default"] = entry["model"]
        cfg["model"] = model
    ce = _config_enforcement()
    if ce is not None:
        ce.normalize_profile_config(cfg, ce.managed_model_pin(payload), ce.approved_models(payload))
    return cfg


def _policy(payload: dict[str, Any], key: str) -> Any:
    from . import policy_contract  # deny-by-default (REQ-GOH-023)

    return policy_contract.policy_value(payload, key)


def mcp_server_allowed(payload: dict[str, Any], server: str) -> bool:
    """P-6 con la misma normalización que el veto de tools (``-``/``_`` equivalentes)."""
    allowed = _policy(payload, "allowed_mcp_servers")
    allowed = {str(a) for a in allowed} if isinstance(allowed, (list, tuple, set)) else set()
    if "*" in allowed:
        return True
    target = server.replace("_", "-")
    return any(a.replace("_", "-") == target for a in allowed)


def scheduled_tasks_allowed(payload: dict[str, Any]) -> bool:
    return _policy(payload, "allow_scheduled_tasks") is True


def apply_mcp_policy(cfg: dict[str, Any], marker: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """§6.4: un MCP server que la política no permite queda ``enabled: false`` en el config del
    perfil — la clave que leen todos los lectores de Hermes — y su PROCESO no arranca (P-6 ya veta
    sus tools). Solo se reactiva lo que desactivó el plugin (``marker.mcp_disabled``): un server que
    el paquete ya traía apagado sigue apagado."""
    servers = cfg.get("mcp_servers")
    if not isinstance(servers, dict):
        marker["mcp_disabled"] = []
        return []
    ours = set(marker.get("mcp_disabled") or [])
    changes: list[str] = []
    updated: dict[str, Any] = {}
    for name, conf in servers.items():
        name = str(name)
        if not isinstance(conf, dict):
            updated[name] = conf
            continue
        conf = dict(conf)
        if not mcp_server_allowed(payload, name):
            if conf.get("enabled", True) is not False:
                conf["enabled"] = False
                ours.add(name)
                changes.append(f"mcp_disabled:{name}")
        elif name in ours:
            conf["enabled"] = True
            ours.discard(name)
            changes.append(f"mcp_enabled:{name}")
        updated[name] = conf
    cfg["mcp_servers"] = updated
    marker["mcp_disabled"] = sorted(ours & set(updated))
    return changes


# --- cron ---------------------------------------------------------------------------------------
ORG_CRON_PAUSE = "Paused by your organization's policy: scheduled tasks are not allowed."
# cron.job_definition.JOB_DEFINITION_FIELDS: lo que define un job; el resto es estado del scheduler.
_JOB_DEFINITION_FIELDS = frozenset({
    "name", "prompt", "skills", "skill", "model", "provider", "base_url",
    "script", "no_agent", "monitor_script", "monitor_url", "context_from",
    "schedule", "schedule_display", "deliver", "origin", "enabled_toolsets",
    "workdir", "attach_to_session", "reasoning_effort", "failure_deliver",
    "timezone", "grace_minutes", "catch_up",
})


def _job_active(job: dict[str, Any]) -> bool:
    return job.get("enabled", True) is not False and job.get("state") != "paused" and not job.get("paused_at")


class CronStore:
    """El store de cron de UN perfil. Dentro de Hermes usa su API (``cron.jobs`` con
    ``use_cron_store``: lock del store, ``next_run_at`` correcto, sin carrera con el ticker); fuera
    de Hermes (tests) edita ``cron/jobs.json`` con los mismos campos."""

    def __init__(self, home: Path) -> None:
        self.home = home
        try:
            from cron import jobs as cron_jobs  # type: ignore
            from cron.job_definition import import_job_definitions  # type: ignore

            self._api: Any = (cron_jobs, import_job_definitions)
        except Exception:  # noqa: BLE001 - sin Hermes importable
            self._api = None

    # -- JSON directo (fallback) --------------------------------------------------------------
    def _path(self) -> Path:
        return self.home.joinpath(*CRON_STORE)

    def _read(self) -> dict[str, Any]:
        data = _read_json(self._path()) or {}
        if not isinstance(data.get("jobs"), list):
            data["jobs"] = []
        return data

    def _write(self, data: dict[str, Any]) -> None:
        data["updated_at"] = _now()
        _atomic_write(self._path(), (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    def _edit(self, job_id: str, fields: dict[str, Any]) -> bool:
        data = self._read()
        for job in data["jobs"]:
            if isinstance(job, dict) and str(job.get("id")) == job_id:
                job.update(fields)
                self._write(data)
                return True
        return False

    # -- operaciones ------------------------------------------------------------------------
    def jobs(self) -> list[dict[str, Any]]:
        if self._api is not None:
            cron_jobs, _ = self._api
            with cron_jobs.use_cron_store(self.home):
                return [j for j in cron_jobs.load_jobs() if isinstance(j, dict)]
        return [j for j in self._read()["jobs"] if isinstance(j, dict)]

    def import_definitions(self, shipped: dict[str, dict[str, Any]]) -> None:
        """Ids nuevos llegan PAUSADOS por la organización (luego la política decide); los
        conocidos refrescan su definición y conservan su estado. ValueError si uno no se puede
        programar (no se escribe nada)."""
        if not shipped:
            return
        if self._api is not None:
            cron_jobs, import_job_definitions = self._api
            with cron_jobs.use_cron_store(self.home):
                import_job_definitions(shipped, paused_reason=ORG_CRON_PAUSE)
            return
        data = self._read()
        by_id = {str(j.get("id")): j for j in data["jobs"] if isinstance(j, dict) and j.get("id")}
        now = _now()
        for job_id, job in shipped.items():
            authored = {k: v for k, v in job.items() if k in _JOB_DEFINITION_FIELDS}
            if job_id in by_id:
                local = by_id[job_id]
                for key in [k for k in local if k in _JOB_DEFINITION_FIELDS]:
                    local.pop(key)
                local.update(authored)
            else:
                data["jobs"].append({"id": job_id, **authored, "enabled": False, "state": "paused",
                                     "paused_at": now, "paused_reason": ORG_CRON_PAUSE,
                                     "created_at": now, "next_run_at": None})
        self._write(data)

    def pause(self, job_id: str) -> bool:
        if self._api is not None:
            cron_jobs, _ = self._api
            with cron_jobs.use_cron_store(self.home):
                return cron_jobs.pause_job(job_id, reason=ORG_CRON_PAUSE) is not None
        return self._edit(job_id, {"enabled": False, "state": "paused", "paused_at": _now(),
                                   "paused_reason": ORG_CRON_PAUSE})

    def resume(self, job_id: str) -> bool:
        if self._api is not None:
            cron_jobs, _ = self._api
            with cron_jobs.use_cron_store(self.home):
                return cron_jobs.resume_job(job_id) is not None
        return self._edit(job_id, {"enabled": True, "state": "scheduled", "paused_at": None,
                                   "paused_reason": None})

    def remove(self, job_id: str) -> bool:
        if self._api is not None:
            cron_jobs, _ = self._api
            with cron_jobs.use_cron_store(self.home):
                return bool(cron_jobs.remove_job(job_id))
        data = self._read()
        kept = [j for j in data["jobs"] if not (isinstance(j, dict) and str(j.get("id")) == job_id)]
        if len(kept) == len(data["jobs"]):
            return False
        data["jobs"] = kept
        self._write(data)
        return True


def shipped_cron_jobs(staged_store: Path) -> dict[str, dict[str, Any]]:
    """Jobs del paquete (``{"jobs": [...]}`` de Hermes) por id. JSON inválido → PackageRejected."""
    data = _read_json(staged_store)
    if data is None:
        raise PackageRejected("cron/jobs.json in the package is not valid JSON")
    jobs = data.get("jobs")
    out: dict[str, dict[str, Any]] = {}
    for job in jobs if isinstance(jobs, list) else []:
        if isinstance(job, dict) and job.get("id") and isinstance(job.get("schedule"), (dict, str)):
            out[str(job["id"])] = job
    return out


def apply_cron_policy(home: Path, marker: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """§6.4 / P-9 extendido: con ``allow_scheduled_tasks=false`` los jobs de la organización
    quedan pausados (el ticker no los ejecuta); cuando la política los permite se reanudan SOLO
    los que pausó la organización — una pausa del empleado se respeta."""
    org_ids = {str(i) for i in marker.get("cron_jobs") or []}
    if not org_ids:
        marker["cron_paused"] = []
        return []
    store = CronStore(home)
    allowed = scheduled_tasks_allowed(payload)
    ours = {str(i) for i in marker.get("cron_paused") or []}
    changes: list[str] = []
    for job in store.jobs():
        job_id = str(job.get("id") or "")
        if job_id not in org_ids:
            continue
        paused_by_org = job.get("paused_reason") == ORG_CRON_PAUSE
        if not allowed:
            if _job_active(job) and store.pause(job_id):
                changes.append(f"cron_paused:{job_id}")
            if _job_active(job) or paused_by_org or job_id in ours:
                ours.add(job_id)
        elif not _job_active(job) and (paused_by_org or job_id in ours):
            try:
                resumed = store.resume(job_id)
            except ValueError:  # one-shot vencido: queda pausado (lo decide Hermes)
                resumed = False
            if resumed:
                ours.discard(job_id)
                changes.append(f"cron_resumed:{job_id}")
        else:
            ours.discard(job_id)
    marker["cron_paused"] = sorted(ours & org_ids)
    return changes


def apply_runtime_policy(
    profile_dir: Path, cfg: dict[str, Any], marker: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """MCP y cron (D-045-C, §6.4) sobre un perfil gestionado. Muta *marker* (lo que el plugin
    apagó/pausó) y devuelve el config y los cambios aplicados."""
    changes = apply_mcp_policy(cfg, marker, payload)
    changes += apply_cron_policy(profile_dir, marker, payload)
    return cfg, changes


# ---------------------------------------------------------------------------------------------
# Escritura de un perfil gestionado
# ---------------------------------------------------------------------------------------------
def _plugin_source() -> Path:
    return Path(__file__).resolve().parent


def _copy_plugin(profile_dir: Path) -> None:
    """Copia ESTE plugin a ``<perfil>/plugins/trust-agent-governance`` (sin ``__pycache__``):
    el manager de plugins de cada perfil lo carga desde su propio home."""
    src = _plugin_source()
    dest = profile_dir / "plugins" / GOVERNANCE_PLUGIN
    if dest.exists() and dest.resolve() == src:
        return  # este mismo perfil: el plugin que corre ES esa copia
    (profile_dir / "plugins").mkdir(parents=True, exist_ok=True)
    tmp = profile_dir / "plugins" / f".{GOVERNANCE_PLUGIN}.{uuid.uuid4().hex[:8]}"
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if dest.exists():
        old = profile_dir / "plugins" / f".{GOVERNANCE_PLUGIN}.old.{uuid.uuid4().hex[:8]}"
        _retry(os.replace, dest, old)
        _retry(os.replace, tmp, dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        _retry(os.replace, tmp, dest)


def _refuse_links(profile_dir: Path, rels: Iterable[str]) -> None:
    """Nunca escribir A TRAVÉS de un symlink/junction dentro del perfil (acabaría fuera de él)."""
    checked: set[Path] = set()
    for rel in rels:
        path = profile_dir
        for part in rel.split("/")[:-1]:
            path = path / part
            if path in checked:
                continue
            checked.add(path)
            if _is_link(path):
                raise PackageRejected(f"refusing to write through a link inside the profile: {rel[:80]!r}")


def _prune_empty_dirs(profile_dir: Path, rel: str) -> None:
    parts = rel.split("/")[:-1]
    while len(parts) > 1:  # nunca el directorio de primer nivel (skills/, cron/)
        path = profile_dir.joinpath(*parts)
        try:
            path.rmdir()
        except OSError:
            return
        parts = parts[:-1]


def write_payload(
    profile_dir: Path,
    staged: Path,
    files: list[str],
    entry: dict[str, Any],
    payload: dict[str, Any],
    old_marker: dict[str, Any] | None,
) -> dict[str, Any]:
    """Escribe el paquete extraído (*staged*) sobre *profile_dir*: sustituye SOLO las rutas del
    paquete, elimina las que la versión anterior traía y esta no, genera config/profile.yaml/cron
    y deja el marcador AL FINAL. Devuelve el marcador nuevo."""
    old_files = [str(f) for f in ((old_marker or {}).get("files") or [])]
    _refuse_links(profile_dir, list(files) + old_files + ["config.yaml", "profile.yaml", "/".join(CRON_STORE), "plugins/x"])

    # 1) cron primero: es lo único que puede rechazar contenido (JSON inválido, un job que no se
    #    puede programar) antes de tocar nada.
    shipped = shipped_cron_jobs(staged.joinpath(*CRON_STORE)) if "/".join(CRON_STORE) in files else {}
    if shipped:
        try:
            CronStore(profile_dir).import_definitions(shipped)
        except ValueError as exc:
            raise PackageRejected(f"cron job cannot be scheduled: {exc}") from None

    # 2) archivos del paquete, uno a uno y de forma atómica.
    for rel in files:
        if rel in _GENERATED:
            continue
        _atomic_write(profile_dir / rel, (staged / rel).read_bytes())

    # 3) config.yaml (de la organización): paquete + P-26 + modelo de la política + MCP/cron.
    package_cfg = _load_yaml_file(staged / "config.yaml") if "config.yaml" in files else {}
    cfg = governed_config(package_cfg, entry, payload, apply_model=True)
    marker: dict[str, Any] = {
        "schema": SCHEMA,
        "profile_id": entry["profile_id"],
        "slug": entry["slug"],
        "dir": profile_dir.name,
        "version": entry["version"],
        "sha256": entry["sha256"],
        "model": entry.get("model") or "",
        "name": entry["name"],
        "description": entry["description"],
        "files": files,
        "installed_at": (old_marker or {}).get("installed_at") or _now(),
        "updated_at": _now(),
        "mcp_disabled": list((old_marker or {}).get("mcp_disabled") or []),
        "cron_jobs": sorted(shipped),
        "cron_paused": list((old_marker or {}).get("cron_paused") or []),
    }
    # Jobs que la versión anterior traía y esta ya no: fuera del store (solo los de la organización).
    store = CronStore(profile_dir)
    for job_id in sorted({str(i) for i in (old_marker or {}).get("cron_jobs") or []} - set(shipped)):
        store.remove(job_id)
    cfg, _changes = apply_runtime_policy(profile_dir, cfg, marker, payload)
    _atomic_write(profile_dir / "config.yaml", _dump_yaml(cfg))

    # 4) profile.yaml: nombre y descripción del marketplace (los edita el admin sin versión nueva).
    meta = _load_yaml_file(staged / "profile.yaml") if "profile.yaml" in files else {}
    meta = {k: v for k, v in meta.items() if k in ("display_name", "description")}
    meta["display_name"] = entry["name"]
    if entry["description"]:
        meta["description"] = entry["description"]
    _atomic_write(profile_dir / "profile.yaml", _dump_yaml(meta))

    # 5) lo que la versión anterior traía y esta ya no (skills retiradas, mcp.json, …).
    for rel in old_files:
        if rel in files or rel in _GENERATED:
            continue
        path = profile_dir / rel
        if path.is_file() and not _is_link(path):
            _retry(os.unlink, path)
            _prune_empty_dirs(profile_dir, rel)

    # 6) marcador AL FINAL: hasta aquí, un fallo deja el marcador viejo y el próximo sync repite.
    _write_json(profile_dir / MARKER, marker)
    return marker


# ---------------------------------------------------------------------------------------------
# Estado local
# ---------------------------------------------------------------------------------------------
def _managed_dirs(profiles_root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """profile_id → (dir, marcador) de los perfiles de la organización instalados."""
    out: dict[str, tuple[Path, dict[str, Any]]] = {}
    if not profiles_root.is_dir():
        return out
    for entry in sorted(profiles_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or _is_link(entry):
            continue
        marker = _read_json(entry / MARKER)
        if marker and _UUID_RE.match(str(marker.get("profile_id") or "")):
            out.setdefault(str(marker["profile_id"]), (entry, marker))
    return out


def _latest_archive(archive_root: Path, profile_id: str) -> tuple[Path, dict[str, Any]] | None:
    best: tuple[str, Path, dict[str, Any]] | None = None
    if not archive_root.is_dir():
        return None
    for entry in archive_root.iterdir():
        if not entry.is_dir() or _is_link(entry):
            continue
        marker = _read_json(entry / MARKER)
        if not marker or marker.get("profile_id") != profile_id:
            continue
        stamp = str(marker.get("archived_at") or "")
        if best is None or stamp > best[0]:
            best = (stamp, entry, marker)
    return (best[1], best[2]) if best else None


def _free_name(profiles_root: Path, slug: str, profile_id: str) -> str:
    """``slug`` si está libre (o ya es de este profile_id); si no, ``slug-org`` (spec 045 §6.2)."""
    candidates = [slug, f"{slug}-org"] + [f"{slug}-org-{i}" for i in range(2, 10)]
    for name in candidates:
        if not PROFILE_ID_RE.match(name):
            continue
        path = profiles_root / name
        if not path.exists():
            return name
        marker = _read_json(path / MARKER)
        if marker and marker.get("profile_id") == profile_id:
            return name
    raise Deferred(f"no free profile name for {slug}")


def _tombstone_path(profile_dir: Path) -> Path:
    return profile_dir.parent / ".deleted" / profile_dir.name  # hermes_constants.profile_tombstone_path


def _is_tombstoned(profile_dir: Path) -> bool:
    return _tombstone_path(profile_dir).exists()


def _set_tombstone(profile_dir: Path) -> None:
    """El tombstone de Hermes (``profiles/.deleted/<name>``): el perfil deja de ser vivo — el
    multiplexor deja de servirlo y el escritorio de listarlo — mientras sus datos siguen ahí."""
    try:
        from hermes_constants import mark_named_profile_deleted  # type: ignore

        mark_named_profile_deleted(profile_dir)
        return
    except ImportError:
        pass
    path = _tombstone_path(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("deleted\n", encoding="utf-8")


def _clear_tombstone(profile_dir: Path) -> None:
    try:
        from hermes_constants import clear_named_profile_deleted  # type: ignore

        clear_named_profile_deleted(profile_dir)
        return
    except ImportError:
        pass
    _tombstone_path(profile_dir).unlink(missing_ok=True)


def _archive(profile_dir: Path, marker: dict[str, Any], archive_root: Path) -> str:
    """Retira un perfil gestionado como lo hace ``hermes profile delete``: tombstone (el
    multiplexor deja de servirlo en su rescan — ≤ 30 s — y el escritorio de listarlo) → se mueve al
    archivo con sus datos → se quita el tombstone. Solo con la API pública de Hermes
    (``hermes_constants``; el plugin nunca importa ``hermes_cli``, AC-GOH-016): si el rename aún
    choca con archivos que el gateway tiene abiertos (Windows), el perfil queda oculto con el
    tombstone y el siguiente sync, ya liberado, completa el archivo."""
    if not _is_tombstoned(profile_dir):
        _set_tombstone(profile_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{marker.get('slug') or profile_dir.name}@{stamp}"
    i = 2
    while (archive_root / name).exists():
        name = f"{marker.get('slug') or profile_dir.name}@{stamp}-{i}"
        i += 1
    archived = dict(marker, archived_at=_now(), archived_from=profile_dir.name)
    _write_json(profile_dir / MARKER, archived)
    try:
        _retry(os.replace, profile_dir, archive_root / name, attempts=3)
    except Deferred:
        _write_json(profile_dir / MARKER, marker)  # sigue en profiles/ (oculto por el tombstone)
        raise
    _clear_tombstone(profile_dir)  # el nombre queda libre
    return name


# ---------------------------------------------------------------------------------------------
# Reconciliación
# ---------------------------------------------------------------------------------------------
def profiles_root_for_device() -> Path | None:
    """``<root>/profiles`` del home que porta el enrolamiento. Un perfil con enrolamiento propio
    (legacy) no materializa perfiles anidados."""
    home = bundle_client.device_home()
    if bundle_client._named_profile_root(home) is not None:
        return None
    return home / "profiles"


def reconcile(payload: dict[str, Any], device: dict[str, str]) -> dict[str, Any] | None:
    """Un ciclo de materialización. Nunca lanza; devuelve el estado publicado (o None si no aplica)."""
    try:
        desired = desired_profiles(payload)
        if desired is None:
            return None
        profiles_root = profiles_root_for_device()
        if profiles_root is None:
            return None
        trust = bundle_client.trust_dir()
        with _Lock(trust / ".profiles.lock") as lock:
            if not lock.acquired:
                return None
            return _reconcile_locked(payload, device, desired, profiles_root, trust)
    except Exception as exc:  # noqa: BLE001 - la materialización jamás tumba el sync
        bundle_client.log_event("profiles_reconcile_error", detail=repr(exc)[:500])
        return None


def _reconcile_locked(
    payload: dict[str, Any],
    device: dict[str, str],
    desired: list[dict[str, Any]],
    profiles_root: Path,
    trust: Path,
) -> dict[str, Any]:
    archive_root = trust / ARCHIVE_DIR
    staging_root = trust / STAGING_DIR
    shutil.rmtree(staging_root, ignore_errors=True)  # restos de un ciclo interrumpido
    profiles_root.mkdir(parents=True, exist_ok=True)
    installed = _managed_dirs(profiles_root)
    wanted = {e["profile_id"] for e in desired}
    published: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for entry in desired:
        pid = entry["profile_id"]
        try:
            current = installed.get(pid)
            if current is not None and _is_tombstoned(current[0]):
                _clear_tombstone(current[0])  # su archivo seguía pendiente: vuelve en su sitio
                events.append({"event": "profile_restored", "slug": entry["slug"], "dir": current[0].name})
            if current is None:
                restored = _latest_archive(archive_root, pid)
                if restored is not None:
                    current = _restore(restored, profiles_root, entry)
                    events.append({"event": "profile_restored", "slug": entry["slug"], "dir": current[0].name})
            if current is None:
                current = _install(entry, payload, device, profiles_root, staging_root)
                events.append({"event": "profile_installed", "slug": entry["slug"], "dir": current[0].name,
                               "version": entry["version"]})
            elif current[1].get("sha256") != entry["sha256"]:
                current = _update(current, entry, payload, device, staging_root)
                events.append({"event": "profile_updated", "slug": entry["slug"], "dir": current[0].name,
                               "version": entry["version"]})
            else:
                current = _refresh(current, entry, payload)
            published.append({**_public(entry), "dir": current[0].name, "status": "installed"})
        except PackageRejected as exc:
            events.append({"event": "profile_package_rejected", "slug": entry["slug"], "reason": str(exc)[:300]})
            published.append(_pending(entry, installed.get(pid)))
        except (PackageUnavailable, Deferred) as exc:
            events.append({"event": "profile_sync_deferred", "slug": entry["slug"], "reason": str(exc)[:300]})
            published.append(_pending(entry, installed.get(pid)))
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    pending_archive: list[dict[str, Any]] = []
    for pid, (path, marker) in sorted(installed.items()):
        if pid in wanted:
            continue
        try:
            archived = _archive(path, marker, archive_root)
            events.append({"event": "profile_archived", "slug": marker.get("slug"), "archive": archived})
        except (Deferred, OSError) as exc:
            events.append({"event": "profile_archive_deferred", "slug": marker.get("slug"), "reason": str(exc)[:300]})
            pending_archive.append({"profile_id": pid, "slug": marker.get("slug"), "dir": path.name})

    for event in events:
        name = event.pop("event")
        bundle_client.log_event(name, **event)
    return _publish(trust, payload, published, pending_archive)


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in ("profile_id", "slug", "name", "description", "version", "sha256", "model", "default")}


def _pending(entry: dict[str, Any], current: tuple[Path, dict[str, Any]] | None) -> dict[str, Any]:
    """Sin el paquete nuevo: si hay una versión instalada se sigue usando (status installed con
    su versión); si no, queda pendiente y el escritorio no la lista todavía."""
    if current is not None:
        path, marker = current
        if path.is_dir():
            return {**_public(entry), "version": marker.get("version"), "sha256": marker.get("sha256"),
                    "dir": path.name, "status": "installed", "update_pending": True}
    return {**_public(entry), "dir": None, "status": "pending"}


def _install(entry, payload, device, profiles_root: Path, staging_root: Path):
    name = _free_name(profiles_root, entry["slug"], entry["profile_id"])
    pkg = download_package(entry, device, staging_root)
    build = staging_root / f"build-{uuid.uuid4().hex[:8]}"
    extracted = build / "pkg"
    files = extract_package(pkg, entry["slug"], extracted)
    target = build / name
    target.mkdir()
    for d in PROFILE_DIRS:
        (target / d).mkdir(exist_ok=True)
    _copy_plugin(target)
    marker = write_payload(target, extracted, files, entry, payload, None)
    final = profiles_root / name
    if final.exists():
        raise Deferred(f"profile directory appeared meanwhile: {name}")
    _clear_tombstone(final)  # un tombstone viejo con ese nombre lo dejaría sin servir
    _retry(os.replace, target, final)  # el multiplexor lo sirve en su rescan (≤ 30 s)
    return final, marker


def _update(current, entry, payload, device, staging_root: Path):
    path, old_marker = current
    pkg = download_package(entry, device, staging_root)
    extracted = staging_root / f"pkg-{uuid.uuid4().hex[:8]}"
    files = extract_package(pkg, entry["slug"], extracted)
    marker = write_payload(path, extracted, files, entry, payload, old_marker)
    return path, marker


def _restore(archived: tuple[Path, dict[str, Any]], profiles_root: Path, entry: dict[str, Any]):
    src, marker = archived
    preferred = str(marker.get("archived_from") or marker.get("dir") or entry["slug"])
    name = preferred if PROFILE_ID_RE.match(preferred) and not (profiles_root / preferred).exists() else \
        _free_name(profiles_root, entry["slug"], entry["profile_id"])
    final = profiles_root / name
    _clear_tombstone(final)
    _retry(os.replace, src, final)
    marker = {k: v for k, v in marker.items() if k not in ("archived_at", "archived_from")}
    marker["dir"] = name
    marker["restored_at"] = _now()
    _write_json(final / MARKER, marker)
    try:
        _copy_plugin(final)  # el plugin pudo cambiar mientras estaba archivado
    except OSError:
        pass
    return final, marker


def _refresh(current, entry, payload):
    """Mismo paquete: aplica lo que la política puede cambiar sin versión nueva — modelo del
    perfil, nombre/descripción, MCP y cron. Escribe solo si algo cambia."""
    path, marker = current
    marker = dict(marker)
    cfg_path = path / "config.yaml"
    cfg = _load_yaml_file(cfg_path)
    model_changed = (entry.get("model") or "") != (marker.get("model") or "")
    new_cfg = governed_config(cfg, entry, payload, apply_model=model_changed)
    new_cfg, _changes = apply_runtime_policy(path, new_cfg, marker, payload)
    if new_cfg != cfg:
        _atomic_write(cfg_path, _dump_yaml(new_cfg))
    if (entry["name"], entry["description"]) != (marker.get("name"), marker.get("description")):
        meta = _load_yaml_file(path / "profile.yaml")
        meta["display_name"] = entry["name"]
        if entry["description"]:
            meta["description"] = entry["description"]
        else:
            meta.pop("description", None)
        _atomic_write(path / "profile.yaml", _dump_yaml(meta))
    marker.update(model=entry.get("model") or "", name=entry["name"], description=entry["description"],
                  version=entry["version"], dir=path.name)
    if not (path / "plugins" / GOVERNANCE_PLUGIN / "plugin.yaml").is_file():
        _copy_plugin(path)
    _write_json(path / MARKER, marker)  # no-op si no cambió
    return path, marker


def _publish(trust: Path, payload: dict[str, Any], published: list[dict[str, Any]],
             pending_archive: list[dict[str, Any]]) -> dict[str, Any]:
    """``trust-agent/managed-profiles.json`` para el escritorio (selector y perfil de arranque)."""
    path = trust / STATE_FILE
    previous = _read_json(path) or {}
    default = next((p for p in published if p["default"] and p.get("status") == "installed"), None)
    default_key = f"{default['profile_id']}:{default['dir']}" if default else None
    seq = int(previous.get("default_seq") or 0)
    if default_key and default_key != previous.get("default_key"):
        seq += 1
    state = {
        "schema": SCHEMA,
        "policy_version": payload.get("policy_version"),
        "profiles": published,
        "default": {"profile_id": default["profile_id"], "dir": default["dir"]} if default else None,
        "default_key": default_key if default_key else previous.get("default_key"),
        "default_seq": seq,
        "archive_pending": pending_archive,
    }
    comparable = {k: v for k, v in previous.items() if k != "updated_at"}
    if comparable != state:
        state["updated_at"] = _now()
        _write_json(path, state)
        return state
    return previous

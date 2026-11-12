"""Marketplace of agent profiles (spec 045): import, versions, factory catalog, lifecycle.

A marketplace profile is a sanitized Hermes profile package (``sanitize.py``) plus tenant
metadata. Versions are immutable and the highest one is current (D-045-E). Profiles are never
deleted — they are archived (D-045-03), so a policy or a device can always resolve what it had.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import conflict, not_found, require_uuid, validation_failed
from . import storage
from .sanitize import ArchiveRejected, SanitizedPackage, sanitize_archive

FACTORY_DIR = Path(__file__).resolve().parent / "factory"
_SUMMARY_KEYS = (
    "format", "has_soul", "skills", "has_scripts", "mcp_servers", "cron_jobs", "env_requires",
    "redactions", "stripped", "file_count", "total_size", "distribution", "config_removed_keys",
    "profile_meta",
)


def _role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "org_admin"


# --- Factory catalog (D-045-A) -------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def factory_catalog() -> tuple[dict, ...]:
    doc = json.loads((FACTORY_DIR / "factory.json").read_text(encoding="utf-8"))
    return tuple(doc["profiles"])


@functools.lru_cache(maxsize=16)
def _factory_package(key: str) -> SanitizedPackage:
    item = next(i for i in factory_catalog() if i["key"] == key)
    return sanitize_archive((FACTORY_DIR / item["archive"]).read_bytes())


def _latest(conn: Connection, profile_id: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT version, sha256, size FROM agent_profile_versions"
            " WHERE profile_id = CAST(:p AS uuid) ORDER BY version DESC LIMIT 1"
        ),
        {"p": profile_id},
    ).mappings().first()
    return dict(row) if row else None


def _insert_version(
    conn: Connection, tenant_id: str, profile_id: str, version: int,
    pkg: SanitizedPackage, imported_by: str | None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO agent_profile_versions"
            " (tenant_id, profile_id, version, sha256, size, manifest, imported_by)"
            " VALUES (CAST(:t AS uuid), CAST(:p AS uuid), :v, :sha, :size,"
            "  CAST(:manifest AS jsonb), CAST(:by AS uuid))"
        ),
        {
            "t": tenant_id, "p": profile_id, "v": version, "sha": pkg.sha256,
            "size": pkg.size, "manifest": json.dumps(pkg.manifest), "by": imported_by,
        },
    )


def _free_slug(conn: Connection, wanted: str) -> str:
    taken = {r[0] for r in conn.execute(text("SELECT slug FROM agent_profiles"))}
    if wanted not in taken:
        return wanted
    for n in range(2, 100):
        candidate = f"{wanted}-factory" if n == 2 else f"{wanted}-factory-{n}"
        if candidate not in taken:
            return candidate
    raise conflict("No se pudo reservar un nombre para el perfil de fábrica.")


def ensure_factory_profiles(conn: Connection, tenant_id: str) -> int:
    """Idempotent: every tenant's marketplace carries the factory profiles, at their current
    release. A newer factory package (product release) becomes a new version; an archived
    factory profile stays archived (the admin decided)."""
    existing = {
        r["factory_key"]: dict(r)
        for r in conn.execute(
            text("SELECT id, factory_key FROM agent_profiles WHERE factory_key IS NOT NULL")
        ).mappings()
    }
    seeded = 0
    for item in factory_catalog():
        pkg = _factory_package(item["key"])
        storage.put(pkg.data, pkg.sha256)
        row = existing.get(item["key"])
        if row is None:
            profile_id = conn.execute(
                text(
                    "INSERT INTO agent_profiles"
                    " (tenant_id, slug, name, description, source, factory_key)"
                    " VALUES (CAST(:t AS uuid), :slug, :name, :desc, 'factory', :key)"
                    " ON CONFLICT DO NOTHING RETURNING id"
                ),
                {
                    "t": tenant_id, "slug": _free_slug(conn, item["key"]),
                    "name": item["name"], "desc": item["description"], "key": item["key"],
                },
            ).scalar()
            if profile_id is None:
                continue  # a concurrent request seeded it first
            _insert_version(conn, tenant_id, str(profile_id), 1, pkg, None)
            audit.emit(
                conn, tenant_id=tenant_id, actor_role="system",
                action="agent_profile.factory_seeded", target_type="agent_profile",
                target_id=str(profile_id),
                payload={"factory_key": item["key"], "sha256": pkg.sha256},
            )
            seeded += 1
            continue
        latest = _latest(conn, str(row["id"]))
        if latest is not None and latest["sha256"] == pkg.sha256:
            continue  # the common path: no lock taken on a plain list
        conn.execute(
            text("SELECT 1 FROM agent_profiles WHERE id = CAST(:p AS uuid) FOR UPDATE"),
            {"p": str(row["id"])},
        )
        latest = _latest(conn, str(row["id"]))  # re-read under the lock
        if latest is None or latest["sha256"] != pkg.sha256:
            version = (latest["version"] if latest else 0) + 1
            _insert_version(conn, tenant_id, str(row["id"]), version, pkg, None)
            audit.emit(
                conn, tenant_id=tenant_id, actor_role="system",
                action="agent_profile.version_added", target_type="agent_profile",
                target_id=str(row["id"]),
                payload={"factory_key": item["key"], "version": version, "sha256": pkg.sha256},
            )
    return seeded


# --- Import (D-045-02) ---------------------------------------------------------------------------
def _display_name(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title() or slug


def import_package(
    conn: Connection, principal: Principal, data: bytes, slug_override: str | None = None
) -> dict:
    try:
        pkg = sanitize_archive(data, slug_override=slug_override)
    except ArchiveRejected as exc:
        raise validation_failed(exc.message, {"rule": exc.rule}) from None
    ensure_factory_profiles(conn, principal.tenant_id)  # factory slugs are reserved first
    row = conn.execute(
        text(
            "SELECT id, source, status FROM agent_profiles WHERE slug = :s FOR UPDATE"
        ),
        {"s": pkg.slug},
    ).mappings().first()
    storage.put(pkg.data, pkg.sha256)

    if row is None:
        distribution = pkg.manifest.get("distribution") or {}
        meta = pkg.manifest.get("profile_meta") or {}
        name = (meta.get("display_name") or _display_name(pkg.slug))[:120]
        description = (distribution.get("description") or meta.get("description") or "")[:1000]
        profile_id = str(conn.execute(
            text(
                "INSERT INTO agent_profiles (tenant_id, slug, name, description, source, created_by)"
                " VALUES (CAST(:t AS uuid), :slug, :name, :desc, 'imported', CAST(:by AS uuid))"
                " RETURNING id"
            ),
            {
                "t": principal.tenant_id, "slug": pkg.slug, "name": name,
                "desc": description, "by": principal.user_id,
            },
        ).scalar())
        _insert_version(conn, principal.tenant_id, profile_id, 1, pkg, principal.user_id)
        audit.emit(
            conn, tenant_id=principal.tenant_id, actor_role=_role(principal),
            action="agent_profile.imported", actor_id=principal.user_id,
            target_type="agent_profile", target_id=profile_id,
            payload=_audit_payload(pkg, 1),
        )
        return {"profile": get_profile(conn, profile_id), "created": True, "version_added": True, "unchanged": False}

    profile_id = str(row["id"])
    if row["source"] == "factory":
        raise conflict(
            f"'{pkg.slug}' es un perfil de fábrica. Impórtalo con otro nombre.",
            {"rule": "factory_slug", "slug": pkg.slug},
        )
    latest = _latest(conn, profile_id)
    if latest and latest["sha256"] == pkg.sha256:
        return {"profile": get_profile(conn, profile_id), "created": False, "version_added": False, "unchanged": True}
    version = (latest["version"] if latest else 0) + 1
    _insert_version(conn, principal.tenant_id, profile_id, version, pkg, principal.user_id)
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_role(principal),
        action="agent_profile.version_added", actor_id=principal.user_id,
        target_type="agent_profile", target_id=profile_id,
        payload=_audit_payload(pkg, version),
    )
    if row["status"] == "archived":
        _set_status(conn, principal, profile_id, "active", "agent_profile.restored")
    return {"profile": get_profile(conn, profile_id), "created": False, "version_added": True, "unchanged": False}


def _audit_payload(pkg: SanitizedPackage, version: int) -> dict:
    m = pkg.manifest
    return {
        "slug": pkg.slug, "version": version, "sha256": pkg.sha256, "size": pkg.size,
        "skills": len(m.get("skills") or []), "mcp_servers": m.get("mcp_servers") or [],
        "cron_jobs": len(m.get("cron_jobs") or []), "redactions": m.get("redactions", 0),
        "stripped": m.get("stripped") or {},
    }


# --- Read ----------------------------------------------------------------------------------------
def policy_usage(conn: Connection) -> dict[str, list[dict]]:
    """profile_id -> policies that assign it (and whether as their default)."""
    usage: dict[str, list[dict]] = {}
    rows = conn.execute(
        text("SELECT id, name, profiles, default_profile_id FROM policy_bundles ORDER BY name")
    ).mappings()
    for row in rows:
        default = str(row["default_profile_id"]) if row["default_profile_id"] else None
        for entry in row["profiles"] or []:
            if isinstance(entry, dict) and entry.get("profile_id"):
                pid = str(entry["profile_id"])
                usage.setdefault(pid, []).append(
                    {"id": str(row["id"]), "name": row["name"], "default": pid == default}
                )
    return usage


def _profile_dict(row: Any, usage: dict[str, list[dict]], full: bool) -> dict:
    manifest = row["manifest"] or {}
    summary = {k: manifest.get(k) for k in _SUMMARY_KEYS}
    summary["soul_excerpt"] = (manifest.get("soul_excerpt") or "")[: (800 if full else 280)]
    out = {
        "id": str(row["id"]),
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "source": row["source"],
        "factory_key": row["factory_key"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "latest": {
            "version": row["version"],
            "sha256": row["sha256"],
            "size": row["size"],
            "imported_at": row["imported_at"].isoformat() if row["imported_at"] else None,
            "imported_by": row["imported_by_email"],
            "summary": summary,
        },
        "policies": usage.get(str(row["id"]), []),
    }
    if full:
        out["latest"]["manifest"] = manifest
    return out


_PROFILE_SQL = (
    "SELECT p.id, p.slug, p.name, p.description, p.source, p.factory_key, p.status,"
    " p.created_at, p.updated_at, v.version, v.sha256, v.size, v.manifest, v.imported_at,"
    " u.email AS imported_by_email"
    " FROM agent_profiles p"
    " JOIN LATERAL (SELECT * FROM agent_profile_versions v"
    "   WHERE v.profile_id = p.id ORDER BY v.version DESC LIMIT 1) v ON true"
    " LEFT JOIN users u ON u.id = v.imported_by"
)


def list_profiles(conn: Connection, include_archived: bool = False) -> list[dict]:
    rows = conn.execute(
        text(
            _PROFILE_SQL
            + " WHERE (:all OR p.status = 'active')"
            " ORDER BY p.source DESC, lower(p.name)"
        ),
        {"all": include_archived},
    ).mappings().all()
    usage = policy_usage(conn)
    return [_profile_dict(r, usage, full=False) for r in rows]


def get_profile(conn: Connection, profile_id: str) -> dict:
    profile_id = require_uuid(profile_id, "profile_id")
    row = conn.execute(
        text(_PROFILE_SQL + " WHERE p.id = CAST(:p AS uuid)"), {"p": profile_id}
    ).mappings().first()
    if row is None:
        raise not_found("Perfil no encontrado.")
    out = _profile_dict(row, policy_usage(conn), full=True)
    out["versions"] = [
        {
            "version": v["version"], "sha256": v["sha256"], "size": v["size"],
            "imported_at": v["imported_at"].isoformat() if v["imported_at"] else None,
            "imported_by": v["email"],
        }
        for v in conn.execute(
            text(
                "SELECT v.version, v.sha256, v.size, v.imported_at, u.email"
                " FROM agent_profile_versions v LEFT JOIN users u ON u.id = v.imported_by"
                " WHERE v.profile_id = CAST(:p AS uuid) ORDER BY v.version DESC"
            ),
            {"p": profile_id},
        ).mappings()
    ]
    return out


def package_path(conn: Connection, profile_id: str, version: int | None = None) -> tuple[Path, str, str, int]:
    """(path, sha256, slug, version) of a stored package version (current by default)."""
    profile_id = require_uuid(profile_id, "profile_id")
    row = conn.execute(
        text(
            "SELECT p.slug, v.version, v.sha256 FROM agent_profiles p"
            " JOIN agent_profile_versions v ON v.profile_id = p.id"
            " WHERE p.id = CAST(:p AS uuid) AND (CAST(:v AS integer) IS NULL OR v.version = :v)"
            " ORDER BY v.version DESC LIMIT 1"
        ),
        {"p": profile_id, "v": version},
    ).mappings().first()
    if row is None:
        raise not_found("Perfil o versión no encontrados.")
    path = storage.path_for(row["sha256"])
    if not path.is_file():
        raise not_found("El paquete del perfil no está disponible en el servidor.")
    return path, row["sha256"], row["slug"], row["version"]


# --- Lifecycle (D-045-03) ------------------------------------------------------------------------
def update_profile(
    conn: Connection, principal: Principal, profile_id: str,
    name: str | None, description: str | None,
) -> dict:
    profile_id = require_uuid(profile_id, "profile_id")
    fields: dict[str, str] = {}
    if name is not None:
        name = name.strip()
        if not name or len(name) > 120:
            raise validation_failed("El nombre es obligatorio (máx. 120 caracteres).")
        fields["name"] = name
    if description is not None:
        if len(description) > 1000:
            raise validation_failed("La descripción admite hasta 1000 caracteres.")
        fields["description"] = description.strip()
    if not fields:
        return get_profile(conn, profile_id)
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    updated = conn.execute(
        text(f"UPDATE agent_profiles SET {sets} WHERE id = CAST(:p AS uuid) RETURNING id"),
        {**fields, "p": profile_id},
    ).scalar()
    if updated is None:
        raise not_found("Perfil no encontrado.")
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_role(principal),
        action="agent_profile.updated", actor_id=principal.user_id,
        target_type="agent_profile", target_id=profile_id, payload={"fields": sorted(fields)},
    )
    return get_profile(conn, profile_id)


def _set_status(conn: Connection, principal: Principal, profile_id: str, status: str, action: str, payload: dict | None = None) -> None:
    updated = conn.execute(
        text("UPDATE agent_profiles SET status = :s WHERE id = CAST(:p AS uuid) RETURNING id"),
        {"s": status, "p": profile_id},
    ).scalar()
    if updated is None:
        raise not_found("Perfil no encontrado.")
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_role(principal),
        action=action, actor_id=principal.user_id,
        target_type="agent_profile", target_id=profile_id, payload=payload or {},
    )


def archive_profile(conn: Connection, principal: Principal, profile_id: str, confirm: bool) -> dict:
    profile_id = require_uuid(profile_id, "profile_id")
    current = get_profile(conn, profile_id)
    if current["status"] == "archived":
        return current
    policies = current["policies"]
    if policies and not confirm:
        raise conflict(
            f"Este perfil se usa en {len(policies)} política(s). Al archivarlo, los equipos lo"
            " archivarán en su próxima sincronización. Confirma para continuar.",
            {"rule": "in_use", "policies": policies},
        )
    _set_status(conn, principal, profile_id, "archived", "agent_profile.archived",
                {"policies": [p["id"] for p in policies]})
    return get_profile(conn, profile_id)


def restore_profile(conn: Connection, principal: Principal, profile_id: str) -> dict:
    profile_id = require_uuid(profile_id, "profile_id")
    current = get_profile(conn, profile_id)
    if current["status"] == "active":
        return current
    _set_status(conn, principal, profile_id, "active", "agent_profile.restored")
    return get_profile(conn, profile_id)


# --- Policy assignment (D-045-04) ----------------------------------------------------------------
def delivered_profiles(conn: Connection, studio_bundle: dict | None) -> list[dict]:
    """Spec 045 §5 (D-045-05): what a device of this policy receives.

    Active marketplace profiles of the policy at their CURRENT version, in policy order. An
    archived (or vanished) profile is simply absent: that absence is the device's signal to
    archive it (D-045-D). Exactly one entry carries ``default: True`` — the policy's default,
    or the first delivered profile when the default is not deliverable (archived), so a device
    never lands without a starting agent. Legacy spec 044 ``{name, model}`` rows are skipped.
    """
    entries = [
        e for e in ((studio_bundle or {}).get("profiles") or [])
        if isinstance(e, dict) and e.get("profile_id")
    ]
    if not entries:
        return []
    rows = {
        str(r["id"]): r
        for r in conn.execute(
            text(
                "SELECT p.id, p.slug, p.name, p.description, p.status, v.version, v.sha256, v.size"
                " FROM agent_profiles p"
                " JOIN LATERAL (SELECT version, sha256, size FROM agent_profile_versions"
                "   WHERE profile_id = p.id ORDER BY version DESC LIMIT 1) v ON true"
                " WHERE p.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [str(e["profile_id"]) for e in entries]},
        ).mappings()
    }
    delivered: list[dict] = []
    for entry in entries:
        row = rows.get(str(entry["profile_id"]))
        if row is None or row["status"] != "active":
            continue
        delivered.append({
            "profile_id": str(row["id"]),
            "slug": row["slug"],
            "name": row["name"],
            "description": (row["description"] or "")[:280],
            "version": row["version"],
            "sha256": row["sha256"],
            "size": row["size"],
            "model": str(entry.get("model") or ""),
            "default": False,
            "package_path": f"/api/me/agent-profiles/{row['id']}/package",
        })
    wanted = str((studio_bundle or {}).get("default_profile_id") or "")
    chosen = next((p for p in delivered if p["profile_id"] == wanted), delivered[0] if delivered else None)
    if chosen is not None:
        chosen["default"] = True
    return delivered


def validate_policy_profiles(
    conn: Connection, profiles: list, default_profile_id: str | None, models: list[str]
) -> tuple[list[dict], str | None]:
    """Normalize ``[{profile_id, model}]`` + default; raise on anything a device must not get."""
    if not isinstance(profiles, list):
        raise validation_failed("Los perfiles de la política deben ser una lista.")
    normalized: list[dict] = []
    seen: set[str] = set()
    for entry in profiles:
        if not isinstance(entry, dict) or not entry.get("profile_id"):
            raise validation_failed(
                "Cada perfil de la política se elige del marketplace: {profile_id, model}."
            )
        pid = require_uuid(str(entry["profile_id"]), "profile_id")
        model = str(entry.get("model") or "").strip()
        if not model:
            raise validation_failed("Cada perfil de la política necesita un modelo.")
        if pid in seen:
            raise validation_failed("Un perfil aparece dos veces en la política.")
        seen.add(pid)
        if models and model not in models:
            raise validation_failed(
                f"Un perfil usa el modelo '{model}', que no está entre los modelos de esta"
                " política. Márcalo en la sección Modelos y vuelve a guardar."
            )
        normalized.append({"profile_id": pid, "model": model})
    if normalized:
        rows = {
            str(r["id"]): r["status"]
            for r in conn.execute(
                text("SELECT id, status FROM agent_profiles WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": list(seen)},
            ).mappings()
        }
        for pid in seen:
            if pid not in rows:
                raise validation_failed("Un perfil de la política no existe en el marketplace.")
            if rows[pid] != "active":
                raise validation_failed("Un perfil de la política está archivado en el marketplace.")
    default = require_uuid(default_profile_id, "default_profile_id") if default_profile_id else None
    if normalized and default is None:
        raise validation_failed("Elige el perfil por defecto de la política.")
    if default is not None and default not in seen:
        raise validation_failed("El perfil por defecto debe ser uno de los perfiles de la política.")
    return normalized, default

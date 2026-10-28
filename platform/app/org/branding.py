"""Tenant white-label branding — core (spec 012) + versionado draft/publish
con uploads de assets (spec 028, REQ-BRD-001…005 de la spec 001).

Contrato (spec 028 §3):

- ``tenants.branding`` es SOLO lo PUBLICADO (lo que ven console, desktop y
  usuarios). El estado de edición vive en ``tenants.branding_draft`` y jamás
  es visible para usuarios (REQ-BRD-004: los cambios no publicados no son
  visibles). ``tenants.branding_version`` es la versión publicada.
- ``PUT /api/branding`` edita el DRAFT (D-028-04; la spec 012 publicaba en
  caliente — cambio de contrato documentado, REQ-BRD-004 manda). Audit
  ``branding.draft.updated``.
- ``POST /api/branding/publish`` publica atómico: INSERT en
  ``branding_versions`` (versión N+1 con el jsonb exacto) + UPDATE de
  ``tenants.branding``/``branding_version`` en la misma tx. Audit
  ``branding.published`` con la versión.
- ``POST /api/branding/rollback {version}`` re-publica una versión anterior
  como versión NUEVA (historial append-only). Audit ``branding.rolled_back``.
- Assets (D-028-01): logos light/dark + favicon por tenant en
  ``branding_assets`` (bytea, PNG/SVG ≤ 2 MB, validación por magic bytes,
  SVG sin contenido activo). ``GET /api/branding/assets/{id}`` sirve los
  bytes a cualquier principal autenticado del tenant (RLS aísla).
- ``source`` es DERIVADO, nunca almacenado: "provider" cuando el tenant no
  definió nada (jsonb vacío o exactamente el stamp de provisioning, spec
  004 DEFAULT_BRANDING — AC-ONB-007/AC-BRD-005). Legacy ``primary_color``
  (spec 004) mapea a ``accent_color`` al leer.
"""
import hashlib
import json
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import top_role_key
from ..auth.service import Principal
from ..errors import not_found, validation_failed

# Provider default (D-ORG-04): what GET returns when source="provider".
PROVIDER_DEFAULT = {
    "product_name": "Trust Agent",
    "tagline": "AI governance for your organization",
    "accent_color": "#4F46E5",
    "logo_url": None,
}

# What spec 004 provisioning stamps into tenants.branding (legacy keys).
PROVISION_STAMP = {
    "product_name": "Trust Agent",
    "primary_color": "#4F46E5",
    "logo_url": None,
}

CANONICAL_KEYS = ("product_name", "tagline", "accent_color", "logo_url")
# Asset references ride the same jsonb (ids only — bytes live in
# branding_assets, D-028-01).
ASSET_KEYS = {
    "logo_light": "logo_light_asset_id",
    "logo_dark": "logo_dark_asset_id",
    "favicon": "favicon_asset_id",
}
ASSET_KINDS = tuple(ASSET_KEYS.keys())
MAX_ASSET_BYTES = 2 * 1024 * 1024  # REQ-BRD-001: 2 MB
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# SVG active content (D-028-05): reject, never sanitize-and-hope.
_SVG_SCRIPT = re.compile(r"<\s*script", re.IGNORECASE)
_SVG_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_SVG_JS_URL = re.compile(r"javascript\s*:", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Stored-state helpers
# ---------------------------------------------------------------------------

def _load(conn: Connection, column: str = "branding") -> dict:
    row = conn.execute(
        text(f"SELECT {column} AS data FROM tenants")
    ).mappings().first()
    if row is None:
        raise RuntimeError("branding: tenant row missing in scope")
    return dict(row["data"] or {})


def _load_version(conn: Connection) -> int:
    row = conn.execute(text("SELECT branding_version FROM tenants")).first()
    return int(row[0]) if row else 0


def _canonical(stored: dict) -> dict:
    """Project a stored jsonb onto the canonical keys (legacy mapping)."""
    canon = {
        "product_name": stored.get("product_name"),
        "tagline": stored.get("tagline"),
        "accent_color": stored.get("accent_color")
        or stored.get("primary_color"),  # legacy spec 004 key
        "logo_url": stored.get("logo_url"),
    }
    for key in ASSET_KEYS.values():
        canon[key] = stored.get(key)
    return canon


def _effective(canon: dict) -> dict:
    """Fill unset canonical fields with the provider default."""
    out = {}
    for key in CANONICAL_KEYS:
        value = canon.get(key)
        out[key] = value if value not in (None, "") else PROVIDER_DEFAULT[key]
    for key in ASSET_KEYS.values():
        out[key] = canon.get(key)
    return out


def _asset_url(asset_id: str | None) -> str | None:
    return f"/api/branding/assets/{asset_id}" if asset_id else None


def _asset_meta(conn: Connection, asset_id: str | None) -> dict | None:
    """Metadata of a draft/published asset (mime/size/sha) for the UI."""
    if not asset_id:
        return None
    row = conn.execute(
        text("SELECT id, kind, mime, size_bytes, sha256 FROM branding_assets "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": asset_id},
    ).mappings().first()
    if row is None:
        return None
    return {
        "asset_id": str(row["id"]),
        "kind": row["kind"],
        "mime": row["mime"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "url": _asset_url(str(row["id"])),
    }


def _public_state(conn: Connection, canon: dict, *, version: int) -> dict:
    """Effective published branding with derived source + asset URLs."""
    stamp_canon = _canonical(PROVISION_STAMP)
    # Provider default when nothing was defined (empty jsonb) or when the
    # stored jsonb is exactly the provisioning stamp (AC-ONB-007).
    empty = all(value is None for value in canon.values())
    source = "provider" if empty or canon == stamp_canon else "tenant"
    effective = _effective(canon)
    light = _asset_url(effective.get("logo_light_asset_id"))
    dark = _asset_url(effective.get("logo_dark_asset_id"))
    favicon = _asset_url(effective.get("favicon_asset_id"))
    # logo_url stays backward compatible (D-028-06): external URL if the
    # tenant set one, else the light asset — existing consumers (<img src>)
    # keep working with zero changes.
    logo_url = effective.get("logo_url") or light
    return {
        "product_name": effective["product_name"],
        "tagline": effective["tagline"],
        "accent_color": effective["accent_color"],
        "logo_url": logo_url,
        "logo_light_url": light,
        "logo_dark_url": dark,
        "favicon_url": favicon,
        "source": source,
        "version": version,
    }


def _draft_state(conn: Connection, draft: dict, *, version: int) -> dict:
    """Draft editor state: canonical fields + asset metadata + diff flag."""
    canon = _canonical(draft)
    published = _load(conn)
    # Compare EFFECTIVE projections, not raw canon: a freshly provisioned
    # tenant carries the spec 004 stamp (product_name/primary_color) while
    # the draft is empty — both project onto the provider default, so the
    # editor must NOT claim "unpublished changes" (AC-ONB-007).
    has_changes = _effective(canon) != _effective(_canonical(published))
    return {
        "product_name": canon.get("product_name"),
        "tagline": canon.get("tagline"),
        "accent_color": canon.get("accent_color"),
        "logo_url": canon.get("logo_url"),
        "logo_light": _asset_meta(conn, canon.get("logo_light_asset_id")),
        "logo_dark": _asset_meta(conn, canon.get("logo_dark_asset_id")),
        "favicon": _asset_meta(conn, canon.get("favicon_asset_id")),
        "version": version,
        "has_changes": has_changes,
    }


# ---------------------------------------------------------------------------
# GET /api/branding — published, effective (unchanged consumer contract)
# ---------------------------------------------------------------------------

def get_branding(conn: Connection) -> dict:
    stored = _load(conn)
    version = _load_version(conn)
    return _public_state(conn, _canonical(stored), version=version)


def get_draft(conn: Connection) -> dict:
    draft = _load(conn, "branding_draft")
    return _draft_state(conn, draft, version=_load_version(conn))


# ---------------------------------------------------------------------------
# PUT /api/branding — edit the DRAFT (REQ-BRD-004, D-028-04)
# ---------------------------------------------------------------------------

def _validate(key: str, value: object) -> object:
    if key == "product_name":
        name = str(value).strip()
        if not 1 <= len(name) <= 80:
            raise validation_failed("product_name must be 1-80 characters.")
        return name
    if key == "tagline":
        tagline = str(value).strip()
        if len(tagline) > 140:
            raise validation_failed("tagline must be at most 140 characters.")
        return tagline or None
    if key == "accent_color":
        color = str(value).strip()
        if not _HEX_COLOR.match(color):
            raise validation_failed("accent_color must be a #RRGGBB hex color.")
        return color
    if key == "logo_url":
        url = str(value).strip()
        if not url:
            return None
        if not (url.startswith("http://") or url.startswith("https://")) \
                or len(url) > 2048:
            raise validation_failed(
                "logo_url must be an http(s) URL of at most 2048 characters."
            )
        return url
    raise validation_failed(f"Unknown branding field: {key}")


def put_draft(
    conn: Connection, principal: Principal, *, body: dict,
) -> dict:
    """PUT /api/branding — merge onto the DRAFT jsonb (unknown keys are
    preserved), audit ``branding.draft.updated``, return draft + published.

    Contract change vs spec 012 (documented in spec 028 D-028-04): this
    used to publish in place. REQ-BRD-004 requires unpublished changes to
    stay invisible until POST /api/branding/publish.
    """
    updates = {
        k: _validate(k, v) for k, v in body.items() if k in CANONICAL_KEYS
    }
    if not updates:
        raise validation_failed(
            "Provide at least one of: product_name, tagline, accent_color, "
            "logo_url."
        )
    draft = _load(conn, "branding_draft")
    draft.update(updates)
    conn.execute(
        text("UPDATE tenants SET branding_draft = CAST(:b AS jsonb), "
             "updated_at = now()"),
        {"b": json.dumps(draft)},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="branding.draft.updated", actor_id=principal.user_id,
        target_type="tenant", target_id=principal.tenant_id,
        payload={"keys": sorted(updates)},
    )
    return {
        "draft": _draft_state(conn, draft, version=_load_version(conn)),
        "published": get_branding(conn),
    }


# ---------------------------------------------------------------------------
# POST /api/branding/assets — upload logo light/dark or favicon
# ---------------------------------------------------------------------------

def _validate_asset(kind: str, content: bytes) -> str:
    """Validate kind/size/content; return the DERIVED mime (never trust the
    client-declared content type)."""
    if kind not in ASSET_KINDS:
        raise validation_failed(
            f"kind must be one of: {', '.join(ASSET_KINDS)}."
        )
    if not content:
        raise validation_failed("Empty file.")
    if len(content) > MAX_ASSET_BYTES:
        raise validation_failed(
            f"File exceeds the 2 MB limit ({len(content)} bytes)."
        )
    if content.startswith(_PNG_MAGIC):
        return "image/png"
    # SVG: strict utf-8, rooted at <?xml/<svg, no active content (D-028-05).
    try:
        svg = content.decode("utf-8")
    except UnicodeDecodeError:
        raise validation_failed(
            "Only PNG and SVG files are allowed."
        )
    stripped = svg.lstrip("\ufeff \t\r\n")
    if not (stripped.startswith("<?xml") or stripped.startswith("<svg")) \
            or "<svg" not in svg:
        raise validation_failed(
            "Only PNG and SVG files are allowed."
        )
    if _SVG_SCRIPT.search(svg) or _SVG_HANDLER.search(svg) \
            or _SVG_JS_URL.search(svg):
        raise validation_failed(
            "SVG with active content (script/event handlers/javascript "
            "URLs) is not allowed."
        )
    return "image/svg+xml"


def upload_asset(
    conn: Connection, principal: Principal, *, kind: str, content: bytes,
) -> dict:
    """POST /api/branding/assets — insert a NEW branding_assets row (append
    only: published versions keep referencing their own assets) and point the
    draft at it. Uploading a light logo replaces an external logo_url (the
    asset wins the alias, D-028-06)."""
    mime = _validate_asset(kind, content)
    sha256 = hashlib.sha256(content).hexdigest()
    row = conn.execute(
        text(
            "INSERT INTO branding_assets "
            " (tenant_id, kind, mime, size_bytes, sha256, content, created_by)"
            " VALUES (CAST(:tenant_id AS uuid), :kind, :mime, :size, :sha, "
            " :content, CAST(:created_by AS uuid)) RETURNING id"
        ),
        {
            "tenant_id": principal.tenant_id, "kind": kind, "mime": mime,
            "size": len(content), "sha": sha256, "content": content,
            "created_by": principal.user_id,
        },
    ).first()
    asset_id = str(row[0])
    draft = _load(conn, "branding_draft")
    draft[ASSET_KEYS[kind]] = asset_id
    if kind == "logo_light":
        # The uploaded asset replaces an external logo_url in the alias.
        draft["logo_url"] = None
    conn.execute(
        text("UPDATE tenants SET branding_draft = CAST(:b AS jsonb), "
             "updated_at = now()"),
        {"b": json.dumps(draft)},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="branding.asset.uploaded", actor_id=principal.user_id,
        target_type="tenant", target_id=principal.tenant_id,
        payload={"kind": kind, "asset_id": asset_id, "mime": mime,
                 "size_bytes": len(content), "sha256": sha256},
    )
    return {
        "asset": _asset_meta(conn, asset_id),
        "draft": _draft_state(conn, draft, version=_load_version(conn)),
    }


def get_asset(conn: Connection, asset_id: str) -> dict:
    """GET /api/branding/assets/{id} — row for the binary response (RLS
    scopes the SELECT to the caller's tenant; other tenants get 404)."""
    row = conn.execute(
        text("SELECT mime, sha256, content FROM branding_assets "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": asset_id},
    ).mappings().first()
    if row is None:
        raise not_found("Branding asset not found.")
    return {"mime": row["mime"], "sha256": row["sha256"],
            "content": row["content"]}


# ---------------------------------------------------------------------------
# POST /api/branding/publish — atomic draft → published (REQ-BRD-004)
# ---------------------------------------------------------------------------

def publish(conn: Connection, principal: Principal) -> dict:
    draft = _load(conn, "branding_draft")
    draft_canon = _canonical(draft)
    published_canon = _canonical(_load(conn))
    if not draft:
        raise validation_failed("The branding draft is empty.")
    # Same effective-projection comparison as the draft state (spec 028:
    # the spec 004 stamp projects onto the provider default).
    if _effective(draft_canon) == _effective(published_canon):
        raise validation_failed("No unpublished changes to publish.")
    version = _load_version(conn) + 1
    data = json.dumps(draft_canon)
    conn.execute(
        text(
            "INSERT INTO branding_versions "
            " (tenant_id, version, data, published_by) "
            " VALUES (CAST(:tenant_id AS uuid), :version, "
            " CAST(:data AS jsonb), CAST(:published_by AS uuid))"
        ),
        {"tenant_id": principal.tenant_id, "version": version,
         "data": data, "published_by": principal.user_id},
    )
    conn.execute(
        text("UPDATE tenants SET branding = CAST(:b AS jsonb), "
             "branding_version = :version, updated_at = now()"),
        {"b": data, "version": version},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="branding.published", actor_id=principal.user_id,
        target_type="tenant", target_id=principal.tenant_id,
        payload={"version": version,
                 "keys": sorted(k for k in draft_canon
                                if draft_canon[k] is not None)},
    )
    return {
        "version": version,
        "published": get_branding(conn),
        "draft": _draft_state(conn, draft, version=version),
    }


# ---------------------------------------------------------------------------
# GET /api/branding/versions + POST /api/branding/rollback
# ---------------------------------------------------------------------------

def list_versions(conn: Connection) -> dict:
    rows = conn.execute(
        text("SELECT version, data, published_by, published_at "
             "FROM branding_versions ORDER BY version DESC")
    ).mappings().all()
    versions = []
    for row in rows:
        data = dict(row["data"] or {})
        versions.append({
            "version": row["version"],
            "published_at": row["published_at"].isoformat()
            if row["published_at"] else None,
            "published_by": str(row["published_by"])
            if row["published_by"] else None,
            "summary": {
                "product_name": data.get("product_name"),
                "tagline": data.get("tagline"),
                "accent_color": data.get("accent_color"),
                "has_logo_light": bool(data.get("logo_light_asset_id")),
                "has_logo_dark": bool(data.get("logo_dark_asset_id")),
                "has_favicon": bool(data.get("favicon_asset_id")),
            },
        })
    return {"versions": versions, "current_version": _load_version(conn)}


def rollback(conn: Connection, principal: Principal, *, version: int) -> dict:
    """POST /api/branding/rollback {version} — re-publish an older version's
    exact data as a NEW version (history is append-only, D-028-03). The
    draft is synced to the restored data so the editor reflects reality."""
    if not isinstance(version, int) or version < 1:
        raise validation_failed("version must be a positive integer.")
    current = _load_version(conn)
    row = conn.execute(
        text("SELECT data FROM branding_versions "
             "WHERE tenant_id = CAST(:tenant_id AS uuid) AND version = :v"),
        {"tenant_id": principal.tenant_id, "v": version},
    ).mappings().first()
    if row is None:
        raise not_found(f"Branding version {version} not found.")
    if version == current:
        raise validation_failed(
            f"Version {version} is already the published version."
        )
    data = json.dumps(dict(row["data"]))
    new_version = current + 1
    conn.execute(
        text(
            "INSERT INTO branding_versions "
            " (tenant_id, version, data, published_by) "
            " VALUES (CAST(:tenant_id AS uuid), :version, "
            " CAST(:data AS jsonb), CAST(:published_by AS uuid))"
        ),
        {"tenant_id": principal.tenant_id, "version": new_version,
         "data": data, "published_by": principal.user_id},
    )
    conn.execute(
        text("UPDATE tenants SET branding = CAST(:b AS jsonb), "
             "branding_draft = CAST(:b AS jsonb), "
             "branding_version = :version, updated_at = now()"),
        {"b": data, "version": new_version},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="branding.rolled_back", actor_id=principal.user_id,
        target_type="tenant", target_id=principal.tenant_id,
        payload={"from_version": current, "to_version": version,
                 "new_version": new_version},
    )
    return {
        "version": new_version,
        "restored_from": version,
        "published": get_branding(conn),
        "draft": _draft_state(conn, json.loads(data), version=new_version),
    }

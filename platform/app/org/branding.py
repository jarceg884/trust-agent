"""Tenant white-label branding (spec 012, REQ-ORG-110/111, D-ORG-04).

Canonical shape (console types.ts §"Contratos PENDIENTES"):

    {product_name, tagline?, accent_color?, logo_url?, source}

`source` is DERIVED, never stored: "provider" when the tenant defined
nothing (empty jsonb) or when the stored jsonb is exactly the default
stamped by provisioning (spec 004 DEFAULT_BRANDING) — a freshly
provisioned tenant satisfies AC-ONB-007/AC-BRD-005. Anything else is
"tenant". Legacy `primary_color` (spec 004) maps to `accent_color` on
read. v1 has no uploads: logo_url is data (http(s) URL).
"""
import json
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.rbac import top_role_key
from ..auth.service import Principal
from ..errors import validation_failed

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
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _canonical(stored: dict) -> dict:
    """Project the stored jsonb onto the 4 canonical keys (legacy mapping)."""
    canon = {
        "product_name": stored.get("product_name"),
        "tagline": stored.get("tagline"),
        "accent_color": stored.get("accent_color")
        or stored.get("primary_color"),  # legacy spec 004 key
        "logo_url": stored.get("logo_url"),
    }
    return canon


def _load(conn: Connection) -> dict:
    row = conn.execute(text("SELECT branding FROM tenants")).mappings().first()
    if row is None:
        raise RuntimeError("branding: tenant row missing in scope")
    return dict(row["branding"] or {})


def _effective(canon: dict) -> dict:
    """Fill unset canonical fields with the provider default."""
    out = {}
    for key in CANONICAL_KEYS:
        value = canon.get(key)
        out[key] = value if value not in (None, "") else PROVIDER_DEFAULT[key]
    return out


def get_branding(conn: Connection) -> dict:
    """GET /api/branding — effective branding with derived source."""
    stored = _load(conn)
    canon = _canonical(stored)
    # A freshly provisioned tenant still shows the provider default
    # (AC-ONB-007): compare the canonical projection of the stamp.
    stamp_canon = _canonical(PROVISION_STAMP)
    if not stored or canon == stamp_canon:
        return {**PROVIDER_DEFAULT, "source": "provider"}
    return {**_effective(canon), "source": "tenant"}


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


def put_branding(
    conn: Connection, principal: Principal, *, body: dict,
) -> dict:
    """PUT /api/branding — merge onto the stored jsonb (unknown keys are
    preserved), audit `branding.updated`, return the effective branding."""
    updates = {
        k: _validate(k, v) for k, v in body.items() if k in CANONICAL_KEYS
    }
    if not updates:
        raise validation_failed(
            "Provide at least one of: product_name, tagline, accent_color, "
            "logo_url."
        )
    stored = _load(conn)
    stored.update(updates)
    conn.execute(
        text("UPDATE tenants SET branding = CAST(:b AS jsonb), "
             "updated_at = now()"),
        {"b": json.dumps(stored)},
    )
    audit.emit(
        conn, tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        action="branding.updated", actor_id=principal.user_id,
        target_type="tenant", target_id=principal.tenant_id,
        payload={"keys": sorted(updates)},
    )
    return {**_effective(_canonical(stored)), "source": "tenant"}

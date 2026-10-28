"""Endpoints de descarga (spec 023).

Públicos (sin autenticación — punto de entrada del flujo app→organización):

  GET /download/{slug}          página pública con branding del tenant
  GET /download/{slug}/bundle   tarball white-label con el binding de la org

Gestión (Admin Console del cliente — contrato t_360a752e, D-023-09):

  GET /api/download-link        → {url, enabled, updated_at}
  PUT /api/download-link        {enabled} (auditado)

Seguridad: rate limit positivo en ops.login_throttle (D-023-07), sondeo de
slugs desconocidos quema el contador de IP (anti-enumeración, igual que
GET /api/auth/org-config), tenant suspendido → fail-closed, audit
``download.issued`` por descarga (REQ-HDL-004). org_code es público
(D-023-02): la seguridad del enrolamiento es la aprobación admin (spec 007).
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth import throttle
from ..auth.rbac import top_role_key
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..config import get_settings
from ..db import get_engine, get_migrator_engine, set_tenant
from ..errors import ApiError, not_found, permission_denied, tenant_suspended
from ..org import branding as branding_service
from . import bundle as bundle_builder
from . import cli as cli_builder
from . import page as page_renderer

public_router = APIRouter()  # sin dependencias de auth: es público
admin_router = APIRouter(
    prefix="/api", dependencies=[Depends(require_mfa)]
)


class DownloadLinkBody(BaseModel):
    enabled: bool


def _client_ip(request: Request) -> str | None:
    if not request.client or not request.client.host:
        return None
    try:
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


def _link_disabled() -> ApiError:
    return ApiError(
        status_code=403, code="forbidden", reason="link_disabled",
        message=(
            "Downloads are currently disabled for this organization. "
            "Contact your organization admin."
        ),
    )


def _public_tenant(slug: str) -> dict | None:
    """Fila completa del tenant por slug, vía migrator engine (pre-auth, sin
    contexto RLS — mismo patrón que auth/org-config y find_tenant_by_slug,
    D-DB-14). Solo columnas no sensibles."""
    slug = (slug or "").strip().lower()
    with get_migrator_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, slug, status, download_enabled, "
                "download_updated_at, oidc_issuer_url, oidc_client_id "
                "FROM tenants WHERE slug = :slug"
            ),
            {"slug": slug},
        ).mappings().first()
        return dict(row) if row else None


def _tenant_branding(tenant_id: str) -> tuple[dict, str | None]:
    """Branding PUBLICADO + logo como data URI, leído dentro del scope del
    tenant (RLS). El data URI evita exponer el endpoint autenticado de
    assets en una página pública (D-023-11)."""
    with get_engine().begin() as conn:
        set_tenant(conn, tenant_id)
        branding = branding_service.get_branding(conn)
        asset_id = None
        for key in ("logo_light_url", "logo_url"):
            url = branding.get(key) or ""
            if url.startswith("/api/branding/assets/"):
                asset_id = url.rsplit("/", 1)[-1]
                break
        data_uri = None
        if asset_id:
            asset = branding_service.get_asset(conn, asset_id)
            data_uri = (
                f"data:{asset['mime']};base64,"
                f"{base64.b64encode(asset['content']).decode()}"
            )
    return branding, data_uri


def _page_throttle(request: Request, slug: str) -> None:
    settings = get_settings()
    ip = _client_ip(request)
    page_key = f"dl:page:{ip}"
    ip_key = throttle.ip_key(ip)
    with get_engine().begin() as conn:
        throttle.check(conn, [page_key, ip_key])
        throttle.register_hit(
            conn, page_key,
            window_secs=settings.download_page_rate_window_seconds,
            max_hits=settings.download_page_rate_max,
        )


@public_router.get("/download/{slug}", response_class=HTMLResponse)
def download_page(slug: str, request: Request):
    """Página pública de descarga (REQ-HDL-001)."""
    _page_throttle(request, slug)
    tenant = _public_tenant(slug)
    if tenant is None:
        # Anti-enumeración: el sondeo quema el contador de IP (D-023-07).
        with get_engine().begin() as conn:
            throttle.register_failure(
                conn, [throttle.ip_key(_client_ip(request))]
            )
        raise not_found("Unknown organization code.")

    if tenant["status"] != "active":
        # Fail-closed sin filtrar branding del tenant (REQ-HDL-007).
        return HTMLResponse(
            page_renderer.render_suspended_page(branding={}, logo_data_uri=None),
            status_code=403,
        )

    branding, logo_uri = _tenant_branding(str(tenant["id"]))
    return HTMLResponse(
        page_renderer.render_page(
            slug=tenant["slug"],
            org_name=tenant["name"],
            branding=branding,
            logo_data_uri=logo_uri,
            enabled=bool(tenant["download_enabled"]),
            api_base=str(request.base_url).rstrip("/"),
        )
    )


@public_router.get("/download/{slug}/bundle")
def download_bundle(slug: str, request: Request):
    """Tarball white-label con el binding de la org (REQ-HDL-002/003/004)."""
    settings = get_settings()
    ip = _client_ip(request)
    bundle_key = f"dl:bundle:{slug}:{ip}"
    ip_key = throttle.ip_key(ip)
    with get_engine().begin() as conn:
        throttle.check(conn, [bundle_key, ip_key])
        throttle.register_hit(
            conn, bundle_key,
            window_secs=settings.download_bundle_rate_window_seconds,
            max_hits=settings.download_bundle_rate_max,
        )

    tenant = _public_tenant(slug)
    if tenant is None:
        with get_engine().begin() as conn:
            throttle.register_failure(conn, [ip_key])
        raise not_found("Unknown organization code.")
    if tenant["status"] != "active":
        raise tenant_suspended()
    if not tenant["download_enabled"]:
        raise _link_disabled()

    # api_base: el origen desde el que el empleado descargó (request.base_url
    # — el mismo que usa el admin para el link estable, D-023-01/06).
    api_base = str(request.base_url).rstrip("/")
    branding, _ = _tenant_branding(str(tenant["id"]))
    payload, sha256, filename = bundle_builder.build_bundle(
        slug=tenant["slug"],
        api_base=api_base,
        branding=branding,
        litellm_base_url=settings.litellm_base_url or "http://127.0.0.1:4000",
        oidc_issuer_url=tenant["oidc_issuer_url"] or "",
        oidc_client_id=tenant["oidc_client_id"] or "trust-agent-web",
    )

    # Audit download.issued por descarga (REQ-HDL-004) — dentro del scope.
    tenant_id = str(tenant["id"])
    with get_engine().begin() as conn:
        set_tenant(conn, tenant_id)
        audit.emit(
            conn,
            tenant_id=tenant_id,
            actor_role="system",
            action="download.issued",
            target_type="tenant",
            target_id=tenant_id,
            payload={
                "slug": tenant["slug"],
                "ip": ip,
                "user_agent": request.headers.get("user-agent", "")[:200],
                "bundle_sha256": sha256,
                "filename": filename,
            },
        )

    return Response(
        content=payload,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Bundle-SHA256": sha256,
        },
    )


@public_router.get("/download/{slug}/cli")
def download_cli_installer(slug: str, request: Request):
    """Instalador CLI por tenant (spec 034, REQ-034-001/002, D-034-01).

    Script bash estilo ``curl -fsSL <link> | bash`` con el org_code del
    tenant embebido. Misma seguridad que el bundle: rate limit positivo,
    anti-enumeración, fail-closed (suspendido/disabled) y audit
    ``download.issued`` con ``artifact: "cli"``.
    """
    settings = get_settings()
    ip = _client_ip(request)
    cli_key = f"dl:cli:{slug}:{ip}"
    ip_key = throttle.ip_key(ip)
    with get_engine().begin() as conn:
        throttle.check(conn, [cli_key, ip_key])
        throttle.register_hit(
            conn, cli_key,
            window_secs=settings.download_bundle_rate_window_seconds,
            max_hits=settings.download_bundle_rate_max,
        )

    tenant = _public_tenant(slug)
    if tenant is None:
        with get_engine().begin() as conn:
            throttle.register_failure(conn, [ip_key])
        raise not_found("Unknown organization code.")
    if tenant["status"] != "active":
        raise tenant_suspended()
    if not tenant["download_enabled"]:
        raise _link_disabled()

    # api_base: el origen desde el que el empleado descargó (D-023-01/06) —
    # el instalador apunta el binding y la descarga del bundle a ESTE origen.
    api_base = str(request.base_url).rstrip("/")
    script = cli_builder.build_cli_installer(
        slug=tenant["slug"],
        api_base=api_base,
        litellm_base_url=settings.litellm_base_url or "http://127.0.0.1:4000",
        model=bundle_builder.DEFAULT_MODEL,
    )
    sha256 = hashlib.sha256(script).hexdigest()

    # Audit download.issued por servido (REQ-034-002) — dentro del scope.
    tenant_id = str(tenant["id"])
    with get_engine().begin() as conn:
        set_tenant(conn, tenant_id)
        audit.emit(
            conn,
            tenant_id=tenant_id,
            actor_role="system",
            action="download.issued",
            target_type="tenant",
            target_id=tenant_id,
            payload={
                "slug": tenant["slug"],
                "ip": ip,
                "user_agent": request.headers.get("user-agent", "")[:200],
                "artifact": "cli",
                "installer_sha256": sha256,
            },
        )

    return Response(
        content=script,
        media_type="text/x-shellscript; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="trust-agent-{slug}-cli.sh"',
            "X-Installer-SHA256": sha256,
        },
    )


# --- Gestión del link (Admin Console del cliente, D-023-09) ------------------

def _link_state(conn: Connection, request: Request) -> dict:
    row = conn.execute(
        text(
            "SELECT slug, download_enabled, download_updated_at FROM tenants"
        )
    ).mappings().first()
    if row is None:
        raise not_found("Tenant not found in scope.")
    base = str(request.base_url).rstrip("/")
    return {
        "url": f"{base}/download/{row['slug']}",
        "enabled": bool(row["download_enabled"]),
        "updated_at": row["download_updated_at"].isoformat(),
    }


@admin_router.get("/download-link")
def get_download_link(
    request: Request,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Estado del link estable del tenant (contrato exacto de la UI)."""
    if "download_link.manage" not in principal.permissions:
        raise permission_denied("Missing permission: download_link.manage")
    return _link_state(conn, request)


@admin_router.put("/download-link")
def put_download_link(
    body: DownloadLinkBody,
    request: Request,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Habilita/deshabilita el link público (auditado, REQ-HDL-005)."""
    if "download_link.manage" not in principal.permissions:
        raise permission_denied("Missing permission: download_link.manage")
    conn.execute(
        text(
            "UPDATE tenants SET download_enabled = :enabled, "
            "download_updated_at = now() WHERE slug = (SELECT slug FROM tenants)"
        ),
        {"enabled": body.enabled},
    )
    audit.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=top_role_key(principal.role_keys),
        actor_id=principal.user_id,
        action="download.link.enabled" if body.enabled else "download.link.disabled",
        target_type="tenant",
        target_id=principal.tenant_id,
        payload={"url": _link_state(conn, request)["url"]},
    )
    # get_tx commitea la transacción al salir (engine.begin()).
    return _link_state(conn, request)

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
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
from . import installers as installers_service
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


def _tenant_default_model(tenant_id: str) -> str:
    """Default model of the tenant's current policy bundle.

    The generic installer has no tenant baked in, so the activation flow asks
    the control plane for this value after redeeming the one-time token. A
    tenant without a policy falls back to the product default (an operator
    can override it with TRUST_DEFAULT_MODEL).
    """
    with get_engine().begin() as conn:
        set_tenant(conn, tenant_id)
        row = conn.execute(
            text(
                "SELECT default_model FROM policy_bundles "
                "WHERE tenant_id = :tid ORDER BY updated_at DESC LIMIT 1"
            ),
            {"tid": tenant_id},
        ).mappings().first()
    if row and row["default_model"]:
        return str(row["default_model"])
    return bundle_builder.DEFAULT_MODEL


def _public_litellm_base(request: Request) -> str:
    """Gateway URL employees can actually reach from their own machine.

    ``TRUST_LITELLM_BASE_URL`` is the API-internal URL (usually loopback for
    host networking); the public value can be pinned with
    ``TRUST_LITELLM_PUBLIC_BASE_URL``. When only a loopback URL is configured,
    derive the public host from the download request and keep LiteLLM's
    canonical port 4000.
    """
    settings = get_settings()
    configured = (settings.litellm_public_base_url or settings.litellm_base_url or "").strip()
    parsed = urlparse(configured)
    if configured and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return configured.rstrip("/")
    origin = request.url
    hostname = origin.hostname or "127.0.0.1"
    scheme = origin.scheme or "http"
    return f"{scheme}://{hostname}:4000"


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
            installers=installers_service.available_installers(
                tenant["slug"]
            ),
            certificate_available=(
                installers_service.certificate_path(tenant["slug"]) is not None
            ),
        )
    )


@public_router.get("/download/{slug}/desktop-config")
def desktop_config(slug: str, request: Request):
    """Public tenant metadata consumed by the generic desktop activation flow.

    One MSIX can serve every organization: the installer ships with an empty
    org binding and asks the employee only for portal URL + org + one-time
    enrollment token. After exchange, the desktop fetches this endpoint to
    materialize the tenant's name, accent, OIDC realm and model gateway in
    ``~/.trust-agent`` before the first gateway start.

    Returns only public data (the same values already exposed on the public
    download page and in the signed governance bundle): no secrets, keys or
    user data.
    """
    _page_throttle(request, slug)
    tenant = _public_tenant(slug)
    if tenant is None:
        with get_engine().begin() as conn:
            throttle.register_failure(
                conn, [throttle.ip_key(_client_ip(request))]
            )
        raise not_found("Unknown organization code.")
    if tenant["status"] != "active":
        raise tenant_suspended()
    if not tenant["download_enabled"]:
        raise _link_disabled()

    settings = get_settings()
    branding, _ = _tenant_branding(str(tenant["id"]))
    return JSONResponse(
        {
            "org_code": tenant["slug"],
            "org_name": tenant["name"],
            "product_name": branding.get("product_name") or "Trust Agent",
            "tagline": branding.get("tagline") or "",
            "accent_color": branding.get("accent_color") or "#4F46E5",
            "api_base": str(request.base_url).rstrip("/"),
            "litellm_base": _public_litellm_base(request),
            "model": _tenant_default_model(str(tenant["id"])),
            "skin": f"trust-{tenant['slug']}",
            "oidc_issuer_url": tenant["oidc_issuer_url"] or "",
            "oidc_client_id": tenant["oidc_client_id"] or "trust-agent-web",
        }
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
        litellm_base_url=_public_litellm_base(request),
        model=_tenant_default_model(str(tenant["id"])),
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
    branding, _ = _tenant_branding(str(tenant["id"]))
    script = cli_builder.build_cli_installer(
        slug=tenant["slug"],
        api_base=api_base,
        litellm_base_url=_public_litellm_base(request),
        model=_tenant_default_model(str(tenant["id"])),
        product_name=branding.get("product_name") or "Trust Agent",
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


@public_router.get("/download/{slug}/installer/{platform}")
def download_installer(slug: str, platform: str, request: Request):
    """Instalador desktop por plataforma (spec 040, REQ-PI-003/004).

    Sirve el artefacto construido EN EL HOST (NSIS ``.exe`` / AppImage /
    ``.dmg``) para la versión del plugin ACTUAL. Misma seguridad que el
    bundle: rate limit, anti-enumeración, fail-closed y audit
    ``download.issued`` con ``artifact: "installer-{platform}"`` (D-040-08).
    Sin build servido → 404 ``installer_not_built`` (honesto, REQ-PI-009:
    jamás se finge un artefacto — hoy macOS sin runner).
    """
    if platform not in installers_service.PLATFORMS:
        raise not_found("Unknown installer platform.")
    # Descargas limitadas a Windows hasta nuevo aviso (decisión de producto):
    # macOS/Linux se anuncian como coming soon y no sirven artefacto alguno.
    if platform != "windows":
        raise not_found("The macOS and Linux installers are coming soon. Downloads are currently Windows-only.")

    settings = get_settings()
    ip = _client_ip(request)
    installer_key = f"dl:installer:{slug}:{platform}:{ip}"
    ip_key = throttle.ip_key(ip)
    with get_engine().begin() as conn:
        throttle.check(conn, [installer_key, ip_key])
        throttle.register_hit(
            conn, installer_key,
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

    artifact = installers_service.installer_path(tenant["slug"], platform)
    info = installers_service.available_installers(tenant["slug"]).get(platform)
    if artifact is None or info is None:
        raise ApiError(
            status_code=404,
            code="not_found",
            reason="installer_not_built",
            message=(
                "No installer build is currently available for this "
                "platform. Use the CLI installer or contact your "
                "organization admin."
            ),
        )

    # Audit download.issued por plataforma (REQ-PI-003, D-040-08).
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
                "artifact": f"installer-{platform}",
                "platform": platform,
                "filename": info["filename"],
                "installer_sha256": info["sha256"],
                "plugin_version": installers_service.current_installer_dir(
                    tenant["slug"]
                ).name,
            },
        )

    return FileResponse(
        artifact,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{info["filename"]}"'
            ),
            "X-Installer-SHA256": info["sha256"],
        },
    )


@public_router.get("/download/{slug}/certificate")
def download_certificate(slug: str, request: Request):
    """Serve the signing certificate that matches this organization's MSIX.

    Windows requires the self-signed publisher certificate to be installed
    once (Trusted People) before it will enable the MSIX installer. The
    certificate belongs to the build, not to the tenant, but exposing it on
    the organization's download page removes the manual GitHub round-trip.
    """
    _page_throttle(request, slug)
    tenant = _public_tenant(slug)
    if tenant is None:
        with get_engine().begin() as conn:
            throttle.register_failure(
                conn, [throttle.ip_key(_client_ip(request))]
            )
        raise not_found("Unknown organization code.")
    if tenant["status"] != "active":
        raise tenant_suspended()
    if not tenant["download_enabled"]:
        raise _link_disabled()

    certificate = installers_service.certificate_path(tenant["slug"])
    if certificate is None:
        raise ApiError(
            status_code=404,
            code="not_found",
            reason="certificate_not_built",
            message=(
                "No signing certificate is currently available for this "
                "organization's installer."
            ),
        )

    digest = hashlib.sha256(certificate.read_bytes()).hexdigest()
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
                "ip": _client_ip(request),
                "user_agent": request.headers.get("user-agent", "")[:200],
                "artifact": "certificate",
                "certificate_sha256": digest,
            },
        )

    return FileResponse(
        certificate,
        media_type="application/x-x509-ca-cert",
        headers={
            "Content-Disposition": 'attachment; filename="shawm-cert.cer"',
            "X-Certificate-SHA256": digest,
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

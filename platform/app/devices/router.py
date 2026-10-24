"""Device endpoints (/api/devices/**) — spec 007.

Routes:
  POST /api/devices/enroll               id_token (JWKS+MFA) + org_code -> pending
  POST /api/devices/enroll/exchange      one-time enrollment_token -> device token
  POST /api/devices/{device_id}/token    renew device token (session + MFA + owner)
  GET  /api/devices/me                   own devices
  GET  /api/devices                      admin list (devices.manage)
  GET  /api/devices/{device_id}          admin detail (scope enforced, 404 outside)
  POST /api/devices/{device_id}/approve  pending -> active
  POST /api/devices/{device_id}/reject   pending -> rejected
  POST /api/devices/{device_id}/revoke   -> revoked + sessions revoked (immediate)
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.rbac import require_permission
from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..auth.jwks import verify_id_token
from ..auth.mfa import mfa_evidence
from ..config import get_settings
from ..db import get_engine, set_tenant
from ..errors import invalid_token, tenant_suspended, validation_failed
from . import service

router = APIRouter(prefix="/api/devices")

_MANAGE = [Depends(require_mfa), Depends(require_permission("devices.manage"))]


class EnrollBody(BaseModel):
    org_code: str
    id_token: str
    public_key: str
    fingerprint: str
    device_name: str | None = None
    platform: str | None = None


class ExchangeBody(BaseModel):
    enrollment_token: str


@router.post("/enroll", status_code=202)
def enroll(body: EnrollBody):
    """Request device enrollment (first-class flow, ADR-013 — no scraping).

    The tenant ALWAYS comes from the signed tenant_id claim (D-CP-03); the
    org_code is validated against the tenant's slug (D-DEV-02) and only helps
    the desktop disambiguate the realm. The enrollment token is returned
    EXACTLY ONCE here (ADR-006).
    """
    settings = get_settings()
    claims = verify_id_token(
        body.id_token,
        idp_base_urls=settings.idp_base_urls,
        audience=settings.oidc_audience,
        leeway=settings.jwt_leeway_seconds,
    )
    if not mfa_evidence(claims):
        from ..errors import mfa_required

        raise mfa_required()
    tenant_id = claims["tenant_id"]
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        trow = conn.execute(
            text("SELECT id, status, slug, oidc_issuer_url FROM tenants")
        ).mappings().first()
        if trow is None:
            raise invalid_token()
        if trow["oidc_issuer_url"] and trow["oidc_issuer_url"] != claims["iss"]:
            raise invalid_token()
        if trow["status"] != "active":
            raise tenant_suspended()
        if trow["slug"] != body.org_code:
            raise validation_failed(
                "org_code does not match the organization of the verified token."
            )
        urow = conn.execute(
            text(
                "SELECT id FROM users WHERE oidc_subject = :sub "
                "AND status = 'active'"
            ),
            {"sub": claims["sub"]},
        ).mappings().first()
        if urow is None:
            raise invalid_token()
        return service.enroll(
            conn,
            tenant_id=str(trow["id"]),
            user_id=str(urow["id"]),
            public_key=body.public_key,
            fingerprint=body.fingerprint,
            device_name=body.device_name,
            platform=body.platform,
        )


@router.post("/enroll/exchange")
def exchange(body: ExchangeBody):
    """Redeem the enrollment token (one-time). Polls as 202 while pending."""
    result = service.exchange(get_engine(), body.enrollment_token)
    if result.get("status") == "pending":
        return JSONResponse(status_code=202, content=result)
    return result


@router.post("/{device_id}/token")
def renew_device_token(
    device_id: str,
    principal: Principal = Depends(require_mfa),
    conn: Connection = Depends(get_tx),
):
    """Renew the short-lived device token (session + MFA + ownership)."""
    return service.renew_token(conn, principal=principal, device_id=device_id)


@router.get("/me", dependencies=[Depends(require_mfa)])
def my_devices(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return {"devices": service.my_devices(conn, principal)}


@router.get("", dependencies=_MANAGE)
def list_devices(
    status: str | None = None,
    department_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return {
        "devices": service.list_devices(
            conn, principal=principal, status=status, department_id=department_id,
        )
    }


@router.get("/{device_id}", dependencies=_MANAGE)
def get_device(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.get_device(conn, principal=principal, device_id=device_id)


@router.post("/{device_id}/approve", dependencies=_MANAGE)
def approve(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.approve(conn, principal=principal, device_id=device_id)


@router.post("/{device_id}/reject", dependencies=_MANAGE)
def reject(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.reject(conn, principal=principal, device_id=device_id)


@router.post("/{device_id}/revoke", dependencies=_MANAGE)
def revoke(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.revoke(conn, principal=principal, device_id=device_id)

"""Device endpoints (/api/devices/**) — spec 007.

Routes:
  POST /api/devices/enroll               id_token (JWKS+MFA) + org_code -> pending
                                         (202 + enrollment_token) | active same
                                         owner -> 200 + fresh device_token
                                         (D-HRB-03 amend, spec 022 v1.1.0)
  POST /api/devices/enroll/exchange      one-time enrollment_token -> device token
  POST /api/devices/token/refresh        expired-but-authentic device token of an
                                         active device -> fresh device token
                                         (spec 022 v1.2.0, no session needed)
  POST /api/devices/{device_id}/token    renew device token (session + MFA + owner)
  GET  /api/devices/me                   own devices
  GET  /api/devices                      admin list (devices.manage)
  GET  /api/devices/presence             live presence: who is connected (spec 027)
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
from ..errors import invalid_token, optional_uuid, require_uuid, tenant_suspended, validation_failed
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
    # Spec 034 (D-034-04): 'cli' (instalador curl|bash ligado a la org) o
    # 'desktop' (bundle desktop). Default retrocompatible.
    device_type: str = "desktop"


class ExchangeBody(BaseModel):
    enrollment_token: str
    # Spec 044: real machine identity supplied at redemption so the
    # admin-issued slot binds to this device (optional for backwards
    # compatibility with the legacy device-initiated flow).
    fingerprint: str | None = None
    public_key: str | None = None
    device_name: str | None = None
    platform: str | None = None


class RefreshBody(BaseModel):
    """Spec 022 v1.2.0: the device's own (possibly expired) device token."""

    device_token: str


class PreauthorizeBody(BaseModel):
    """Admin-issued enrollment (Helios-style one-time token)."""

    user_id: str
    device_name: str | None = None
    platform: str | None = None


@router.post("/enroll", status_code=202)
def enroll(body: EnrollBody):
    """Request device enrollment (first-class flow, ADR-013 — no scraping).

    The tenant ALWAYS comes from the signed tenant_id claim (D-CP-03); the
    org_code is validated against the tenant's slug (D-DEV-02) and only helps
    the desktop disambiguate the realm. The enrollment token is returned
    EXACTLY ONCE here (ADR-006).

    D-HRB-03 amend (spec 022 v1.1.0): an ALREADY-ACTIVE device of the same
    user gets a fresh device token DIRECTLY (200) — the caller re-proved
    identity with a fresh MFA id_token; no admin re-approval, no one-time
    enrollment token. 202 + enrollment_token remains the pending flow.
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
        if body.device_type not in ("desktop", "cli"):
            raise validation_failed("device_type must be 'desktop' or 'cli'.")
        urow = conn.execute(
            text(
                "SELECT id FROM users WHERE oidc_subject = :sub "
                "AND status = 'active'"
            ),
            {"sub": claims["sub"]},
        ).mappings().first()
        if urow is None:
            raise invalid_token()
        # D-AUTH-11 (spec 015 v1.1.0): MFA evidence is REQUIRED here, so the
        # verified claims prove a second factor — sync the directory column
        # in the same tenant-scoped transaction. Guarded UPDATE (no-op once
        # true).
        from ..auth.service import sync_mfa_enrolled

        sync_mfa_enrolled(conn, str(urow["id"]))
        result = service.enroll(
            conn,
            tenant_id=str(trow["id"]),
            user_id=str(urow["id"]),
            public_key=body.public_key,
            fingerprint=body.fingerprint,
            device_name=body.device_name,
            platform=body.platform,
            device_type=body.device_type,
        )
    # D-HRB-03 amend: direct re-issue for an active device answers 200
    # (the decorator default is 202 for the pending flow).
    if result.get("status") == "active":
        return JSONResponse(status_code=200, content=result)
    return result


@router.post("/enroll/exchange")
def exchange(body: ExchangeBody):
    """Redeem the enrollment token (one-time). Polls as 202 while pending."""
    result = service.exchange(
        get_engine(),
        body.enrollment_token,
        fingerprint=body.fingerprint,
        public_key=body.public_key,
        device_name=body.device_name,
        platform=body.platform,
    )
    if result.get("status") == "pending":
        return JSONResponse(status_code=202, content=result)
    return result


@router.post("/preauthorize", dependencies=_MANAGE)
def preauthorize(
    body: PreauthorizeBody,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Issue a one-time enrollment token for a user (Helios-style).

    The admin picks the person; the returned ``enrollment_token`` is shown
    EXACTLY ONCE and then redeemed by the app with URL + organization + token.
    """
    user_id = require_uuid(body.user_id, "user_id")
    return service.preauthorize(
        conn,
        tenant_id=principal.tenant_id,
        user_id=user_id,
        actor=principal,
        device_name=body.device_name,
        platform=body.platform,
    )


@router.post("/token/refresh")
def refresh_device_token(body: RefreshBody):
    """Refresh an EXPIRED device token of an active device (spec 022 v1.2.0).

    No session: the authentic device token is the credential. 401 uniform
    when the token is forged, too old, or the device/user/tenant is no
    longer active (revocation stays immediate).
    """
    return service.refresh_expired_token(get_engine(), body.device_token)


@router.post("/{device_id}/token")
def renew_device_token(
    device_id: str,
    principal: Principal = Depends(require_mfa),
    conn: Connection = Depends(get_tx),
):
    """Renew the short-lived device token (session + MFA + ownership)."""
    device_id = require_uuid(device_id, "device_id")  # t_378503a1: 422, not 500
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
    department_id = optional_uuid(department_id, "department_id")
    return {
        "devices": service.list_devices(
            conn, principal=principal, status=status, department_id=department_id,
        )
    }


@router.get("/presence", dependencies=_MANAGE)
def device_presence(
    department_id: str | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """GET /api/devices/presence (spec 027 REQ-027-04): live connections.

    Registered BEFORE /{device_id} so "presence" is not captured as a uuid.
    """
    department_id = optional_uuid(department_id, "department_id")
    return service.presence(
        conn, principal=principal, department_id=department_id
    )


@router.get("/{device_id}", dependencies=_MANAGE)
def get_device(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    device_id = require_uuid(device_id, "device_id")  # t_378503a1: 422, not 500
    return service.get_device(conn, principal=principal, device_id=device_id)


@router.post("/{device_id}/approve", dependencies=_MANAGE)
def approve(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    device_id = require_uuid(device_id, "device_id")  # t_378503a1: 422, not 500
    return service.approve(conn, principal=principal, device_id=device_id)


@router.post("/{device_id}/reject", dependencies=_MANAGE)
def reject(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    device_id = require_uuid(device_id, "device_id")  # t_378503a1: 422, not 500
    return service.reject(conn, principal=principal, device_id=device_id)


@router.post("/{device_id}/revoke", dependencies=_MANAGE)
def revoke(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    device_id = require_uuid(device_id, "device_id")  # t_378503a1: 422, not 500
    return service.revoke(conn, principal=principal, device_id=device_id)


@router.delete("/{device_id}", status_code=204, dependencies=_MANAGE)
def delete_device(
    device_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Delete an enrolled device (and its sessions/keys) from the console."""
    device_id = require_uuid(device_id, "device_id")
    service.delete(conn, principal=principal, device_id=device_id)
    return None

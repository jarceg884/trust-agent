"""DeviceGate — business routes may require an ACTIVE enrolled device
(REQ-DEV-003). The device state is checked in the DB on every request (no
cache in v1: revocation is immediate; the <=30s cache of ADR-013 can be
added later if the load profile demands it).
"""
from fastapi import Depends, Request
from sqlalchemy import text

from ..config import get_settings
from ..db import get_engine, set_tenant
from ..errors import device_not_authorized, invalid_token
from ..auth.service import Principal, get_principal
from .tokens import verify_device_token


def check_device_access(
    conn, device_id: str, principal: Principal, *, touch: bool = True
) -> None:
    """Device row must exist, belong to the principal and be ACTIVE.

    Shared by the DeviceGate dependency and the governance-bundle auth
    (spec 019 §5.5): revocation is enforced here on every request.
    `touch` updates last_seen_at with a 60 s throttle (D-DEV-09).
    """
    row = conn.execute(
        text(
            "SELECT status FROM devices "
            "WHERE id = CAST(:did AS uuid) AND user_id = CAST(:uid AS uuid)"
        ),
        {"did": device_id, "uid": principal.user_id},
    ).mappings().first()
    if row is None or row["status"] != "active":
        raise device_not_authorized()
    if touch:
        conn.execute(
            text(
                "UPDATE devices SET last_seen_at = now(), updated_at = now() "
                "WHERE id = CAST(:did AS uuid) "
                "  AND (last_seen_at IS NULL "
                "       OR last_seen_at < now() - interval '60 seconds')"
            ),
            {"did": device_id},
        )


def require_device(
    request: Request, principal: Principal = Depends(get_principal)
) -> Principal:
    token = request.headers.get("x-device-token", "")
    if not token:
        raise device_not_authorized()
    settings = get_settings()
    claims = verify_device_token(settings, token)
    if claims["tenant_id"] != principal.tenant_id or \
            claims["user_id"] != principal.user_id:
        raise invalid_token("Device token does not match the principal.")

    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, principal.tenant_id)
        check_device_access(conn, claims["device_id"], principal)
    return principal

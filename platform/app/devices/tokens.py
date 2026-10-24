"""Device tokens — short-lived JWTs signed by the control plane (ADR-013).

The control plane is both issuer and verifier, so a symmetric signature with
the algorithm PINNED at verification time is safe (no attacker-selected
algorithm: we never accept `alg` from the token header). IdP tokens, by
contrast, are RS256/ES256 only (app.auth.jwks).
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from ..config import Settings
from ..errors import invalid_token

_DEVICE_ALG = "HS256"


def issue_device_token(
    settings: Settings,
    *,
    device_id: str,
    user_id: str,
    tenant_id: str,
) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "device_id": str(device_id),
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.device_token_ttl_minutes)).timestamp()
        ),
    }
    return jwt.encode(claims, settings.device_token_secret, algorithm=_DEVICE_ALG)


def verify_device_token(settings: Settings, token: str) -> dict:
    """Verify signature (pinned HS256), expiry and required claims."""
    try:
        claims = jwt.decode(
            token,
            settings.device_token_secret,
            algorithms=[_DEVICE_ALG],
            options={"require": ["exp", "iat", "jti", "device_id", "user_id",
                                 "tenant_id"]},
        )
    except jwt.PyJWTError:
        raise invalid_token("Invalid device token.")
    return claims

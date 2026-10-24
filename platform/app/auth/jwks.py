"""JWT verification against the IdP's JWKS (arquitectura §5.1, REQ-SEC-002).

Rules (lesson C6 / high finding "unverified JWT"):
- signature ALWAYS verified against JWKS (never decode without validating);
- algorithms RS256/ES256 only (HS256 excluded: algorithm confusion);
- exp, iat, aud, iss, sub AND the signed tenant_id claim are required;
- issuer pinned to the configured IdP base URL (+ tenant.oidc_issuer_url match
  checked by the caller);
- leeway <= 60s for clock skew;
- any failure -> 401 invalid_token with NO oracle about which claim failed.
"""
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from ..errors import invalid_token

ALLOWED_ALGS = ("RS256", "ES256")
REQUIRED_CLAIMS = ["exp", "iat", "aud", "iss", "sub", "tenant_id"]


@lru_cache(maxsize=64)
def _jwks_client(jwks_uri: str) -> PyJWKClient:
    # Key cache 5 min (skill keycloak-admin-oidc §4).
    return PyJWKClient(jwks_uri, cache_keys=True, lifespan=300)


def _jwks_uri_for_issuer(issuer: str) -> str:
    # Keycloak convention, confirmed by realm discovery:
    # {issuer}/protocol/openid-connect/certs
    return issuer.rstrip("/") + "/protocol/openid-connect/certs"


def verify_id_token(
    token: str,
    *,
    idp_base_urls: list[str] | str,
    audience: str,
    leeway: int = 60,
) -> dict:
    """Verify an OIDC ID/access token and return its claims.

    Raises ApiError(401, invalid_token) on ANY problem.
    """
    if isinstance(idp_base_urls, str):
        bases = [b.strip().rstrip("/") for b in idp_base_urls.split(",") if b.strip()]
    else:
        bases = [b.rstrip("/") for b in idp_base_urls]
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise invalid_token()

    if header.get("alg") not in ALLOWED_ALGS:
        raise invalid_token()

    # The issuer is read unverified ONLY to locate the JWKS; it is then
    # validated: pinned to a configured IdP base and re-checked by decode.
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        raise invalid_token()

    issuer = unverified.get("iss")
    if not isinstance(issuer, str) or not any(
        issuer.startswith(base + "/") for base in bases
    ):
        raise invalid_token()

    try:
        client = _jwks_client(_jwks_uri_for_issuer(issuer))
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(ALLOWED_ALGS),
            audience=audience,
            issuer=issuer,
            options={"require": REQUIRED_CLAIMS},
            leeway=leeway,
        )
    except (jwt.PyJWTError, Exception):
        # PyJWKClientError is not a PyJWTError; collapse everything to the
        # same opaque 401 (no claim oracle).
        raise invalid_token()

    if not isinstance(claims.get("tenant_id"), str):
        raise invalid_token()
    return claims

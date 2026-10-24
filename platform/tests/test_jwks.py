"""AC-CP-021 — JWT verification against a real JWKS served over HTTP.

Six malicious tokens, all collapsing to the same opaque 401 invalid_token
(no claim oracle): broken signature, expired, wrong aud, wrong iss, HS256,
missing tenant_id claim.
"""
import pytest

from app.auth.jwks import verify_id_token
from app.config import get_settings
from app.errors import ApiError
from tests.conftest import TENANT_A


def _verify(idp, token):
    settings = get_settings()
    return verify_id_token(
        token,
        idp_base_urls=settings.idp_base_urls,
        audience=settings.oidc_audience,
        leeway=settings.jwt_leeway_seconds,
    )


def _base_kwargs(idp):
    return dict(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                issuer=idp.issuer("tenant-a"))


def test_valid_token_returns_claims(idp):
    token = idp.make_jwt(**_base_kwargs(idp))
    claims = _verify(idp, token)
    assert claims["sub"] == "sub-alice-orgadmin"
    assert claims["tenant_id"] == TENANT_A
    assert claims["iss"] == idp.issuer("tenant-a")
    assert claims["aud"] == "trust-agent-web"


def test_reject_broken_signature(idp):
    """Same kid, different signing key -> JWKS lookup succeeds, signature fails."""
    token = idp.make_jwt(**_base_kwargs(idp), sign_with_other_key=True)
    with pytest.raises(ApiError) as e:
        _verify(idp, token)
    assert e.value.reason == "invalid_token"


def test_reject_expired(idp):
    token = idp.make_jwt(**_base_kwargs(idp), expires_in=-1000)
    with pytest.raises(ApiError) as e:
        _verify(idp, token)
    assert e.value.reason == "invalid_token"


def test_reject_wrong_audience(idp):
    token = idp.make_jwt(**_base_kwargs(idp), audience="other-client")
    with pytest.raises(ApiError) as e:
        _verify(idp, token)
    assert e.value.reason == "invalid_token"


def test_reject_wrong_issuer(idp):
    token = idp.make_jwt(
        sub="sub-alice-orgadmin", tenant_id=TENANT_A,
        issuer="http://evil.example/realms/tenant-a",
    )
    with pytest.raises(ApiError) as e:
        _verify(idp, token)
    assert e.value.reason == "invalid_token"


def test_reject_hs256(idp):
    """Algorithm confusion: HS256 is never accepted (arq. §5.1)."""
    token = idp.make_jwt(**_base_kwargs(idp), algorithm="HS256")
    with pytest.raises(ApiError) as e:
        _verify(idp, token)
    assert e.value.reason == "invalid_token"


def test_reject_missing_tenant_id_claim(idp):
    token = idp.make_jwt(**_base_kwargs(idp), omit_tenant_id=True)
    with pytest.raises(ApiError) as e:
        _verify(idp, token)
    assert e.value.reason == "invalid_token"


def test_reject_garbage(idp):
    with pytest.raises(ApiError) as e:
        _verify(idp, "not-a-jwt")
    assert e.value.reason == "invalid_token"

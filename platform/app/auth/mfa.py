"""MFA evidence extraction from OIDC claims (REQ-ORG-007, D-CP-07).

Keycloak 26 does NOT emit the `amr` claim (verified live, 2026-10-23: a real
password+TOTP login yields `amr: null, acr: "1"` in both tokens). A gate that
only accepts `amr` therefore rejects every user of a real KC26 realm — the
test suite passed only because the JWKS double in tests/conftest.py does
emit `amr`.

Two signals are accepted (decision D-MFA-01, spec 003/007):

1. `amr` (OIDC standard) containing "mfa" or "otp" — emitted by the test
   double and by any IdP that implements it.
2. `acr` numerically >= TRUST_MFA_MIN_ACR (default 2). With the realm
   configured by `ensure_mfa_acr()` (attribute `acr.loa.map` = {"1":0,"2":1}
   plus a LoA-1 condition on the Conditional OTP sub-flow), a password+OTP
   login reaches LoA 1 and the tokens carry `acr:"2"`, while a password-only
   login carries `acr:"1"`.

Anything else (missing claims, non-numeric acr, amr that is not a list)
fails closed.
"""
from ..config import get_settings

#: Values inside `amr` that count as a second factor.
AMR_MFA_METHODS = ("mfa", "otp")


def _amr_mfa(claims: dict) -> bool:
    amr = claims.get("amr")
    if not isinstance(amr, list):
        return False
    return any(x in AMR_MFA_METHODS for x in amr)


def _acr_mfa(claims: dict) -> bool:
    acr = claims.get("acr")
    if acr is None:
        return False
    try:
        return int(str(acr)) >= get_settings().mfa_min_acr
    except (TypeError, ValueError):
        return False  # non-numeric acr ("silver", etc.) — unmapped, fail closed


def mfa_evidence(claims: dict) -> bool:
    """True when the verified token claims prove a second factor."""
    return _amr_mfa(claims) or _acr_mfa(claims)

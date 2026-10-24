"""D-MFA-01 — MFA evidence: `amr` (OIDC standard) OR `acr` >= TRUST_MFA_MIN_ACR.

Keycloak 26 never emits `amr` (live probe 2026-10-23, spec 010 smoke): a real
password+TOTP login carries `amr: null, acr: "1"` against a default realm.
With the realm wired by `ensure_mfa_acr()` (attribute `acr.loa.map` =
{"1":0,"2":1} + LoA-1 condition on the Conditional OTP sub-flow), password+OTP
logins carry `acr:"2"` and password-only logins carry `acr:"1"`.

These tests pin the gate contract at unit and API level (the test IdP double
emits whichever claims we ask it to). The live KC26 path is verified by the
probe documented in skills/identity/keycloak26-amr-mfa.
"""
from app.auth.mfa import mfa_evidence
from tests.conftest import TENANT_A, SUBS, bearer_headers, mint


# ---------------------------------------------------------------------------
# Unit: claim evaluation (app/auth/mfa.py)
# ---------------------------------------------------------------------------

def test_amr_with_mfa_method():
    assert mfa_evidence({"amr": ["mfa"]}) is True
    assert mfa_evidence({"amr": ["otp"]}) is True
    assert mfa_evidence({"amr": ["pwd", "otp"]}) is True


def test_amr_without_mfa_method_fails_closed():
    assert mfa_evidence({"amr": ["pwd"]}) is False
    assert mfa_evidence({"amr": []}) is False


def test_amr_malformed_fails_closed():
    # A bare string or dict is not a valid amr — never trust it.
    assert mfa_evidence({"amr": "mfa"}) is False
    assert mfa_evidence({"amr": {"mfa": True}}) is False
    assert mfa_evidence({}) is False


def test_acr_at_or_above_threshold():
    # Default TRUST_MFA_MIN_ACR = 2 (Keycloak 26: OTP -> acr "2").
    assert mfa_evidence({"acr": "2"}) is True
    assert mfa_evidence({"acr": 2}) is True
    assert mfa_evidence({"acr": "3"}) is True


def test_acr_below_threshold_fails_closed():
    assert mfa_evidence({"acr": "1"}) is False  # password-only KC26 login
    assert mfa_evidence({"acr": 1}) is False
    assert mfa_evidence({"acr": "0"}) is False


def test_acr_non_numeric_fails_closed():
    # Unmapped acr values ("silver", floats, junk) never pass.
    assert mfa_evidence({"acr": "silver"}) is False
    assert mfa_evidence({"acr": "2.5"}) is False
    assert mfa_evidence({"acr": ""}) is False
    assert mfa_evidence({"acr": None}) is False


def test_kc26_real_shape():
    # What a real KC26 password+OTP login looks like after ensure_mfa_acr().
    assert mfa_evidence({"amr": None, "acr": "2"}) is True
    # ...and what a default realm produced before the fix (the bug).
    assert mfa_evidence({"amr": None, "acr": "1"}) is False
    assert mfa_evidence({"acr": "1"}) is False


# ---------------------------------------------------------------------------
# API: session mint + MfaGate (REQ-CP-022/024 with the D-MFA-01 contract)
# ---------------------------------------------------------------------------

def test_mint_with_acr_mfa_passes_gate(client, idp):
    """KC26 shape (no amr, acr "2") -> mfa_verified session, /api/me 200."""
    tokens = mint(client, idp, "alice", amr=None, extra_claims={"acr": "2"})
    assert tokens["mfa_verified"] is True
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    assert resp.json()["mfa_verified"] is True


def test_mint_with_basic_acr_blocked(client, idp):
    """Negative: acr "1" (password-only) -> session without MFA, /api/me 403."""
    tokens = mint(client, idp, "alice", amr=None, extra_claims={"acr": "1"})
    assert tokens["mfa_verified"] is False
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"


def test_amr_still_accepted(client, idp):
    """Regression: the standard `amr` signal keeps working (test double)."""
    tokens = mint(client, idp, "alice", amr=["otp"])
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200


def test_raw_jwt_bearer_with_acr_mfa(client, idp):
    """Stateless JWT path (authenticate -> via=jwt) evaluates acr too."""
    token = idp.make_jwt(
        sub=SUBS["alice"], tenant_id=TENANT_A,
        issuer=idp.issuer("tenant-a"), amr=None, extra_claims={"acr": "2"},
    )
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["via"] == "jwt"
    assert resp.json()["mfa_verified"] is True


def test_raw_jwt_bearer_with_basic_acr_blocked(client, idp):
    token = idp.make_jwt(
        sub=SUBS["alice"], tenant_id=TENANT_A,
        issuer=idp.issuer("tenant-a"), amr=None, extra_claims={"acr": "1"},
    )
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"


def test_device_enroll_gate_accepts_acr_mfa(client, idp):
    """REQ-DEV-001a with the D-MFA-01 contract: enroll evaluates acr.

    With acr "2" the MFA gate passes and the request proceeds to the
    org_code validation (422 with a wrong org code — proving the 403
    mfa_required gate was cleared); with acr "1" it stops at 403.
    """
    token_mfa = idp.make_jwt(
        sub=SUBS["alice"], tenant_id=TENANT_A,
        issuer=idp.issuer("tenant-a"), amr=None, extra_claims={"acr": "2"},
    )
    resp = client.post("/api/devices/enroll", json={
        "org_code": "wrong-org",
        "id_token": token_mfa,
        "public_key": "not-a-key",
        "fingerprint": "aa:bb",
    })
    assert resp.status_code == 422  # passed the MFA gate, failed org_code

    token_basic = idp.make_jwt(
        sub=SUBS["alice"], tenant_id=TENANT_A,
        issuer=idp.issuer("tenant-a"), amr=None, extra_claims={"acr": "1"},
    )
    resp = client.post("/api/devices/enroll", json={
        "org_code": "wrong-org",
        "id_token": token_basic,
        "public_key": "not-a-key",
        "fingerprint": "aa:bb",
    })
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"

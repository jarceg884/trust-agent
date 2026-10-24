"""AC-CP-024/025 — MfaGate and console cookie + CSRF."""
from tests.conftest import TENANT_A, bearer_headers, mint


def test_no_mfa_blocks_business_routes(client, idp):
    """AC-CP-024: token without amr -> 403 mfa_required on /api/me."""
    tokens = mint(client, idp, "alice", amr=None)
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"


def test_no_mfa_allows_auth_routes(client, idp):
    """Logout is part of the auth flow: exempt from MfaGate."""
    tokens = mint(client, idp, "alice", amr=None)
    resp = client.post("/api/auth/logout", headers=bearer_headers(tokens))
    assert resp.status_code == 204


def test_amr_otp_counts_as_mfa(client, idp):
    tokens = mint(client, idp, "alice", amr=["otp"])
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200


def test_admin_cookie_flags(client, idp):
    """AC-CP-025: Secure + HttpOnly + SameSite=Lax (REQ-SEC-010)."""
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), amr=["mfa"])
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "admin"})
    assert resp.status_code == 201
    set_cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" in set_cookie
    assert "trust_session=" in set_cookie
    # the session token itself is NOT returned in the body for admin kind
    assert resp.json()["session_token"] is None


def _admin_cookie(client, idp):
    """Mint an admin session and return (Cookie header, csrf token).

    The cookie is sent manually: httpx refuses to send Secure cookies over
    plain http (which is exactly the flag we assert above).
    """
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), amr=["mfa"])
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "admin"})
    assert resp.status_code == 201, resp.text
    raw = resp.headers["set-cookie"]
    value = raw.split("trust_session=", 1)[1].split(";", 1)[0]
    return {"Cookie": f"trust_session={value}"}, resp.json()["csrf_token"]


def test_cookie_get_does_not_require_csrf(client, idp):
    cookie, _ = _admin_cookie(client, idp)
    resp = client.get("/api/me", headers=cookie)
    assert resp.status_code == 200
    assert resp.json()["email"] == "alice@tenant-a.test"


def test_cookie_mutation_requires_csrf(client, idp):
    """POST with cookie auth and no X-CSRF-Token -> 403 csrf_required."""
    cookie, _ = _admin_cookie(client, idp)
    resp = client.post("/api/_probe/csrf", headers=cookie)
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "csrf_required"


def test_cookie_mutation_with_csrf_ok(client, idp):
    cookie, csrf = _admin_cookie(client, idp)
    resp = client.post("/api/_probe/csrf",
                       headers={**cookie, "X-CSRF-Token": csrf})
    assert resp.status_code == 200
    assert resp.json() == {"mutated": True}


def test_wrong_csrf_rejected(client, idp):
    cookie, _ = _admin_cookie(client, idp)
    resp = client.post("/api/_probe/csrf",
                       headers={**cookie, "X-CSRF-Token": "wrong-token"})
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "csrf_required"


def test_bearer_mutation_does_not_require_csrf(client, idp):
    """Desktop Bearer flow: no cookie, no CSRF (AC-CP-025)."""
    tokens = mint(client, idp, "alice", kind="desktop")
    resp = client.post("/api/_probe/csrf", headers=bearer_headers(tokens))
    assert resp.status_code == 200

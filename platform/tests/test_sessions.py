"""AC-CP-020/022/023/032 — session minting, storage, restart, refresh, logout."""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import reset_engine
from app.main import create_app
from tests.conftest import (
    TENANT_A, TENANT_B, bearer_headers, mint,
)


def test_mint_session_via_api(client, idp):
    tokens = mint(client, idp, "alice")
    assert tokens["session_token"].startswith("sts1.")
    assert tokens["refresh_token"].startswith("rt1.")
    assert tokens["mfa_verified"] is True
    assert tokens["csrf_token"] is None  # desktop kind: no CSRF


def test_session_token_never_stored_in_clear(client, idp, admin_conn):
    """Only hashes live in the DB (AC-CP-022, ADR-007)."""
    tokens = mint(client, idp, "alice")
    secret = tokens["session_token"].rsplit(".", 1)[-1]
    refresh_secret = tokens["refresh_token"].rsplit(".", 1)[-1]
    row = admin_conn.execute(
        "SELECT jti, refresh_token_hash, csrf_token_hash FROM sessions "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert secret not in row
    assert refresh_secret not in row
    assert row[0] != secret and row[1] != refresh_secret  # they are sha256 hex


def test_whoami_with_session(client, idp):
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@tenant-a.test"
    assert body["tenant_id"] == TENANT_A
    assert {r["role_key"] for r in body["roles"]} == {"org_admin"}
    assert "departments.manage" in body["permissions"]
    assert body["mfa_verified"] is True
    assert body["via"] == "session"


def test_session_survives_api_restart(client, idp):
    """AC-CP-022/042: state lives in PostgreSQL, not in the process."""
    tokens = mint(client, idp, "alice")
    reset_engine()
    with TestClient(create_app()) as fresh:
        resp = fresh.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    assert resp.json()["email"] == "alice@tenant-a.test"
    # the shared session client keeps working with a rebuilt engine
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200


def test_raw_jwt_bearer_path(client, idp):
    """Desktop/plugin stateless path: verified JWT directly as Bearer."""
    token = idp.make_jwt(sub="sub-carol-user", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), amr=["mfa"])
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["via"] == "jwt"
    assert body["email"] == "carol@tenant-a.test"


def test_raw_jwt_disabled_user_rejected(client, idp):
    token = idp.make_jwt(sub="sub-frank-disabled", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), amr=["mfa"])
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"


def test_raw_jwt_unknown_subject_rejected(client, idp):
    token = idp.make_jwt(sub="sub-ghost", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), amr=["mfa"])
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_tenant_hint_forgery_does_not_bypass(client, idp):
    """A sts token with tenant B as hint but tenant A's secret finds no row."""
    tokens = mint(client, idp, "alice")
    parts = tokens["session_token"].split(".")
    forged = f"sts1.{TENANT_B}.{parts[2]}.{parts[3]}"
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"


def test_refresh_rotation_and_replay_detection(client, idp):
    """AC-CP-023: one-time refresh; replay revokes the session."""
    tokens = mint(client, idp, "alice")
    resp = client.post("/api/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new_tokens = resp.json()

    # old session token was rotated away
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 401
    # new session token works
    resp = client.get("/api/me", headers=bearer_headers(new_tokens))
    assert resp.status_code == 200

    # replay of the consumed refresh -> session revoked
    resp = client.post("/api/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401
    resp = client.get("/api/me", headers=bearer_headers(new_tokens))
    assert resp.status_code == 401


def test_logout_revokes_session(client, idp):
    """AC-CP-032."""
    tokens = mint(client, idp, "alice")
    resp = client.post("/api/auth/logout", headers=bearer_headers(tokens))
    assert resp.status_code == 204
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 401
    resp = client.post("/api/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_mint_rejects_tampered_id_token(client, idp):
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"))
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    resp = client.post("/api/auth/session",
                       json={"id_token": tampered, "kind": "desktop"})
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"


def test_mint_rejects_issuer_not_matching_tenant_pin(client, idp):
    """Tenant A pins oidc_issuer_url: a token from another realm is rejected."""
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-b"))
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401

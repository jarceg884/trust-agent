"""AC-CP-020/026 — E2E: authorization-code + PKCE S256 against the REAL
Keycloak (127.0.0.1:8080), Admin REST provisioning (ADR-010) and session
minting through the API.

The realm `trust-agent-cp` is created idempotently via the Admin REST API
with: public client `trust-agent-web` (PKCE S256, directAccessGrants OFF),
a `tenant_id` protocol mapper (user attribute -> signed ID token claim) and
a user whose attribute carries the tenant UUID. The login form dance needs
the authorize cookies (skill keycloak-admin-oidc pitfall #2).

ROPC is never used for end users: the only password grant in this file is
admin-cli for the provider-side Admin REST bootstrap (documented exception,
ADR-015).
"""
import base64
import hashlib
import http.client
import json
import re
import secrets
import urllib.parse

import pytest

from tests.conftest import ADMIN_DSN, PLATFORM_DIR, TENANT_B, TEST_DB

KC_HOST = "127.0.0.1"
KC_PORT = 8080
REALM = "trust-agent-cp"
CLIENT_ID = "trust-agent-web"
# Callback del helper trust-login del bundle de descarga (spec 023 D-023-13):
# el flujo real de desktop. Retirado el demo :9090 (t_b8769ecb).
REDIRECT_URI = "http://127.0.0.1:8765/callback"
KC_USER = "cp-e2e-user"
KC_PASSWORD = "CpE2ePass2026!"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "KeycloakDemo2026"


def _kc_available() -> bool:
    try:
        conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=3)
        conn.request("GET", f"/realms/{REALM}/.well-known/openid-configuration")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status in (200, 404)
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _kc_available(), reason="Keycloak local (127.0.0.1:8080) not reachable"
)


def _request(method, path, data=None, headers=None):
    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
    body = urllib.parse.urlencode(data) if data is not None else None
    conn.request(method, path, body=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", **(headers or {})
    })
    resp = conn.getresponse()
    raw = resp.read().decode()
    return resp.status, raw, resp.getheaders()


def _admin_token() -> str:
    # Internal bootstrap ONLY (admin-cli, ADR-015): never offered to end users.
    status, raw, _ = _request(
        "POST", f"/realms/master/protocol/openid-connect/token",
        {"grant_type": "password", "client_id": "admin-cli",
         "username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )
    assert status == 200, raw
    return json.loads(raw)["access_token"]


def _json_request(method, path, payload=None, token=None):
    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode()
    return resp.status, raw


@pytest.fixture(scope="module", autouse=True)
def kc_realm():
    """Idempotent realm provisioning via Admin REST (ADR-010: no scraping)."""
    token = _admin_token()

    # realm (create if missing)
    status, _ = _json_request("GET", f"/admin/realms/{REALM}", token=token)
    if status == 404:
        status, raw = _json_request(
            "POST", "/admin/realms", {"realm": REALM, "enabled": True}, token
        )
        assert status in (201, 409), raw

    # public client with PKCE S256, ROPC disabled
    status, raw = _json_request(
        "GET", f"/admin/realms/{REALM}/clients?clientId={CLIENT_ID}", token=token
    )
    clients = json.loads(raw) if status == 200 else []
    if clients:
        client_uuid = clients[0]["id"]
        # Heal redirect URIs of pre-existing clients (t_b8769ecb: the demo
        # :9090 callback was retired; the real-flow callback is :8765).
        if REDIRECT_URI not in clients[0].get("redirectUris", []):
            status, raw = _json_request(
                "PUT", f"/admin/realms/{REALM}/clients/{client_uuid}",
                {"redirectUris": [REDIRECT_URI]}, token,
            )
            assert status == 204, raw
    else:
        status, raw = _json_request(
            "POST", f"/admin/realms/{REALM}/clients",
            {
                "clientId": CLIENT_ID, "enabled": True,
                "protocol": "openid-connect", "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "redirectUris": [REDIRECT_URI],
                "attributes": {"pkce.code.challenge.method": "S256"},
            },
            token,
        )
        assert status == 201, raw
        status, raw = _json_request(
            "GET", f"/admin/realms/{REALM}/clients?clientId={CLIENT_ID}",
            token=token,
        )
        client_uuid = json.loads(raw)[0]["id"]

    # tenant_id mapper: user attribute -> signed ID token claim (ADR-015)
    status, raw = _json_request(
        "GET",
        f"/admin/realms/{REALM}/clients/{client_uuid}/protocol-mappers/models",
        token=token,
    )
    mappers = json.loads(raw) if status == 200 else []
    if not any(m["name"] == "tenant_id" for m in mappers):
        status, raw = _json_request(
            "POST",
            f"/admin/realms/{REALM}/clients/{client_uuid}/protocol-mappers/models",
            {
                "name": "tenant_id", "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-attribute-mapper",
                "config": {
                    "user.attribute": "tenant_id",
                    "claim.name": "tenant_id",
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                },
            },
            token,
        )
        assert status in (201, 409), raw

    # Keycloak 26: the declarative User Profile is ON by default and silently
    # DROPS undeclared attributes -> enable unmanaged attributes so the
    # tenant_id attribute survives (pitfall verified against the real realm).
    status, raw = _json_request(
        "GET", f"/admin/realms/{REALM}/users/profile", token=token
    )
    assert status == 200, raw
    profile = json.loads(raw)
    if profile.get("unmanagedAttributePolicy") != "ENABLED":
        profile["unmanagedAttributePolicy"] = "ENABLED"
        status, raw = _json_request(
            "PUT", f"/admin/realms/{REALM}/users/profile", profile, token
        )
        assert status in (200, 204), raw

    # user with the tenant attribute + password
    status, raw = _json_request(
        "GET", f"/admin/realms/{REALM}/users?username={KC_USER}", token=token
    )
    users = json.loads(raw) if status == 200 else []
    if users:
        user_uuid = users[0]["id"]
    else:
        status, raw = _json_request(
            "POST", f"/admin/realms/{REALM}/users",
            {
                "username": KC_USER, "enabled": True,
                "email": "cp-e2e@tenant-b.test",
                "firstName": "CP", "lastName": "E2E",
                "attributes": {"tenant_id": [TENANT_B]},
            },
            token,
        )
        assert status == 201, raw
        status, raw = _json_request(
            "GET", f"/admin/realms/{REALM}/users?username={KC_USER}", token=token
        )
        user_uuid = json.loads(raw)[0]["id"]

    # Always (re)assert the tenant attribute — idempotent.
    status, raw = _json_request(
        "PUT", f"/admin/realms/{REALM}/users/{user_uuid}",
        {
            "username": KC_USER, "enabled": True,
            "email": "cp-e2e@tenant-b.test",
            "firstName": "CP", "lastName": "E2E",
            "attributes": {"tenant_id": [TENANT_B]},
        },
        token,
    )
    assert status in (204, 409), raw
    status, raw = _json_request(
        "PUT", f"/admin/realms/{REALM}/users/{user_uuid}/reset-password",
        {"type": "password", "value": KC_PASSWORD, "temporary": False},
        token,
    )
    assert status in (204, 409), raw

    yield {"user_uuid": user_uuid}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pkce_login() -> str:
    """Full authorization-code + PKCE S256 dance -> id_token."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI,
        "state": "st-" + _b64url(secrets.token_bytes(8)),
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    status, body, headers = _request(
        "GET",
        f"/realms/{REALM}/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}",
    )
    assert status == 200, f"authorize HTTP {status}"
    action = re.search(r'action="([^"]+)"', body).group(1).replace("&amp;", "&")
    cookies = "; ".join(
        v.split(";")[0] for k, v in headers if k.lower() == "set-cookie"
    )

    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
    path = action.split(f"{KC_HOST}:{KC_PORT}", 1)[-1]
    conn.request(
        "POST", path,
        body=urllib.parse.urlencode(
            {"username": KC_USER, "password": KC_PASSWORD, "credentialId": ""}
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Cookie": cookies},
    )
    resp = conn.getresponse()
    location = resp.getheader("Location") or ""
    assert "code=" in location, f"no code in redirect: {location[:120]!r}"
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

    status, raw, _ = _request(
        "POST", f"/realms/{REALM}/protocol/openid-connect/token",
        {
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI, "code": code,
            "code_verifier": verifier,
        },
    )
    assert status == 200, raw
    return json.loads(raw)["id_token"]


@pytest.fixture(scope="module")
def e2e_user_row(kc_realm):
    """Seed a platform user for the Keycloak subject, in tenant B."""
    import psycopg

    sub = kc_realm["user_uuid"]
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        c.execute(
            "INSERT INTO users (tenant_id, department_id, email, display_name, "
            "status, mfa_enrolled, oidc_subject) "
            "VALUES (%s, '30000000-0000-0000-0000-000000000003', "
            "'cp-e2e@tenant-b.test', 'CP E2E', 'active', false, %s) "
            "ON CONFLICT (tenant_id, email) DO UPDATE SET oidc_subject = EXCLUDED.oidc_subject",
            (TENANT_B, sub),
        )
        c.execute(
            "INSERT INTO user_roles (tenant_id, user_id, role_key) "
            "SELECT %s, id, 'user' FROM users WHERE oidc_subject = %s",
            (TENANT_B, sub),
        )
    return sub


def test_pkce_flow_mints_session_and_gate_is_policy_aware(
    client, e2e_user_row,
):
    """The full real flow: PKCE -> id_token with signed tenant_id -> session.

    Spec 015 (D-AUTH-07): the MfaGate is POLICY-AWARE. The Keycloak user has
    NO OTP enrolled, so the minted session carries mfa_verified=false:

      - tenant-b has NO org require_mfa row (how NEW tenants are born,
        migration 0014 default false) -> the session reaches the business
        API (200) and the app suggests MFA via the persistent banner;
      - with an explicit org row require_mfa=true (what EXISTING tenants
        carry) -> 403 mfa_required, the spec-011 behavior intact.
    """
    id_token = _pkce_login()

    # sanity: the signed token carries the tenant_id claim from the mapper
    import jwt as pyjwt

    unverified = pyjwt.decode(id_token, options={"verify_signature": False})
    assert unverified["tenant_id"] == TENANT_B

    resp = client.post("/api/auth/session",
                       json={"id_token": id_token, "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    tokens = resp.json()
    assert tokens["mfa_verified"] is False
    assert tokens["session_token"].startswith("sts1.")

    headers = {"Authorization": f"Bearer {tokens['session_token']}"}
    # (a) policy false (default): password-only session is usable.
    resp = client.get("/api/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["mfa_verified"] is False

    # (b) policy true (existing tenants): same session shape -> 403.
    import psycopg

    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        c.execute(
            "INSERT INTO policies (id, tenant_id, level, key, value) "
            "VALUES (gen_random_uuid(), %s, 'org', 'require_mfa', "
            "'true'::jsonb)",
            (TENANT_B,),
        )
    try:
        resp = client.get("/api/me", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["reason"] == "mfa_required"
    finally:
        with psycopg.connect(ADMIN_DSN.format(db=TEST_DB),
                             autocommit=True) as c:
            c.execute(
                "DELETE FROM policies WHERE tenant_id = %s "
                "AND key = 'require_mfa' AND level = 'org'",
                (TENANT_B,),
            )


def test_tampered_keycloak_token_rejected(client, e2e_user_row):
    id_token = _pkce_login()
    tampered = id_token[:-6] + "AAAAAA"
    resp = client.post("/api/auth/session",
                       json={"id_token": tampered, "kind": "desktop"})
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"


def test_no_password_grant_in_app_code():
    """AC-CP-026: the API never transports user passwords / ROPC."""
    import os

    app_dir = os.path.join(PLATFORM_DIR, "app")
    hits = []
    for root, _, files in os.walk(app_dir):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path) as fh:
                    for i, line in enumerate(fh, 1):
                        if "grant_type=password" in line or "verify=False" in line:
                            hits.append(f"{path}:{i}")
    assert hits == []

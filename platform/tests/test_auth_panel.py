"""Spec 015 v1.3.0 (ADR-019) — Auth panel: direct grant + headless TOTP dance.

Two layers:

1. PURE UNIT (always run): grant-outcome classification, config-totp page
   parsing (fixture HTML captured from the live KC 26.2.5), dance-state
   persistence round-trip against the ephemeral DB.

2. LIVE KEYCLOAK (skipped when 127.0.0.1:8080 is unreachable): the full
   panel surface against a dedicated realm — login matrix (generic 401
   without oracle, OTP hint only for enrolled accounts, acr:"2" evidence),
   the COMPLETE headless TOTP enrollment dance (setup → QR → confirm →
   MFA-verified session, users.mfa_enrolled sync, audit), the
   UPDATE_PASSWORD first-login flow and the voluntary password change.

The panel NEVER relaxes the MFA gate: the evidence is the signed `acr`
claim of the direct-grant id_token (D-AUTH-13), verified by JWKS.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
import uuid

import httpx
import pytest

from tests.conftest import ADMIN_DSN, PLATFORM_DIR, TEST_DB

# ---------------------------------------------------------------------------
# Fixtures of the live layer
# ---------------------------------------------------------------------------

KC_BASE = "http://127.0.0.1:8080"
REALM = "trust-agent-panel"
ORG = REALM
PASSWORD = "PanelE2ePass2026!"
NEW_PASSWORD = "PanelE2eNew2026!!"
TENANT_PANEL = "33333333-3333-3333-3333-333333333333"
EMAIL_MAIN = "panel-main@tenant-panel.test"
EMAIL_OTP = "panel-otp@tenant-panel.test"
EMAIL_TMP = "panel-tmp@tenant-panel.test"


def _kc_available() -> bool:
    try:
        httpx.get(f"{KC_BASE}/realms/master/.well-known/openid-configuration",
                  timeout=3)
        return True
    except httpx.HTTPError:
        return False


live_kc = pytest.mark.skipif(
    not _kc_available(), reason="Keycloak local (127.0.0.1:8080) not reachable"
)


def _totp(raw_secret: str, offset: int = 0) -> str:
    """KC's totpSecret is the RAW secret string; the key is its UTF-8 bytes
    (live-verified against the direct grant + the config-totp form)."""
    key = raw_secret.encode()
    counter = int(time.time()) // 30 + offset
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def _fresh_window_totp(raw_secret: str) -> str:
    """KC 26.2 REJECTS OTP code reuse inside the validity window (the
    enrollment test already consumed this window's code in the dance —
    live-verified: a repeat grant with the same code 401s). Wait for the
    next 30 s window so the direct grant sees a FRESH code."""
    now = time.time()
    time.sleep(31 - (now % 30))
    return _totp(raw_secret)


# ---------------------------------------------------------------------------
# 1) Pure unit layer
# ---------------------------------------------------------------------------

CONFIG_TOTP_HTML = """
<html><body>
<form id="kc-totp-settings-form"
      action="http://127.0.0.1:8080/realms/trust-agent-panel/login-actions/required-action?session_code=abc&amp;execution=CONFIGURE_TOTP&amp;client_id=trust-agent-web">
  <input type="text" id="totp" name="totp" autocomplete="off" />
  <input type="hidden" id="totpSecret" name="totpSecret" value="dL9zTwGWTDNLvr4DTm9k" />
  <input type="text" id="userLabel" name="userLabel" autocomplete="off" />
  <input class="pf-v5-c-check__input" type="checkbox" id="logout-sessions" name="logout-sessions" value="on" checked>
</form>
<img id="kc-totp-secret-qr-code" src="data:image/png;base64, iVBORw0KGgoAAAANSUhEUg==" />
</body></html>
"""

LOGIN_PAGE_HTML = """
<form action="http://127.0.0.1:8080/realms/trust-agent-panel/login-actions/authenticate?session_code=x&amp;execution=y&amp;client_id=trust-agent-web">
  <input id="username" name="username" value="" type="text" />
  <input id="password" name="password" value="" type="password" />
  <input type="hidden" id="id-hidden-input" name="credentialId" />
</form>
"""


def _import_panel():
    import sys

    if PLATFORM_DIR not in sys.path:
        sys.path.insert(0, PLATFORM_DIR)
    from app.auth import panel

    return panel


def test_grant_outcome_classification():
    panel = _import_panel()

    def resp(status, payload):
        return httpx.Response(status, json=payload, request=httpx.Request("POST", "http://x"))

    assert panel._grant_outcome(resp(200, {"id_token": "abc"})) == ("ok", {"id_token": "abc"})
    assert panel._grant_outcome(
        resp(401, {"error": "invalid_grant",
                   "error_description": "Invalid user credentials"})
    ) == ("invalid_credentials", None)
    assert panel._grant_outcome(
        resp(400, {"error": "invalid_grant",
                   "error_description": "Account is not fully set up"})
    ) == ("account_not_setup", None)
    # Any other error (disabled account, expired, ...) is a generic error —
    # the caller answers a generic 401 (no oracle, D-AUTH-16).
    assert panel._grant_outcome(
        resp(401, {"error": "invalid_grant", "error_description": "Account disabled"})
    ) == ("error", None)
    assert panel._grant_outcome(resp(503, {"error": "unknown"})) == ("error", None)


def test_parse_login_and_config_totp_pages():
    panel = _import_panel()
    action, fields = panel._parse_form(LOGIN_PAGE_HTML)
    assert action == (
        "http://127.0.0.1:8080/realms/trust-agent-panel/login-actions/"
        "authenticate?session_code=x&execution=y&client_id=trust-agent-web"
    )
    assert "credentialId" in fields

    action, fields = panel._parse_form(CONFIG_TOTP_HTML)
    assert "session_code=abc" in action and "&amp;" not in action
    assert fields["totpSecret"] == "dL9zTwGWTDNLvr4DTm9k"
    assert panel._page_kind(CONFIG_TOTP_HTML, fields) == "config_totp"

    # OTP form (post-enrollment second challenge): totp input, NO secret.
    otp_form = '<form action="http://x/otp"><input name="totp" /></form>'
    assert panel._page_kind(otp_form, {}) == "otp_form"


def test_extract_qr_normalizes_space():
    panel = _import_panel()
    qr = panel._extract_qr(CONFIG_TOTP_HTML)
    assert qr == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    assert panel._extract_qr("<p>no qr here</p>") is None


def test_panel_acct_key_shape():
    panel = _import_panel()
    key = panel._panel_acct_key(TENANT_PANEL, "User@Example.com ")
    digest = hashlib.sha256("user@example.com".encode()).hexdigest()
    assert key == f"acct:{TENANT_PANEL}:{digest}"


def test_dance_state_roundtrip(db):
    """ops.auth_dances: hashed handle, TTL, update + delete (D-AUTH-14)."""
    panel = _import_panel()
    state = {"cookies": {"AUTH_SESSION_ID": "x"}, "form_action": "http://x",
             "form_fields": {"totpSecret": "s"}, "qr_png": "data:...",
             "pkce_verifier": "v", "redirect_uri": "http://cb", "stage": "config_totp"}
    handle = panel._dance_save("totp_enroll", REALM, TENANT_PANEL, "a@b.c", state)
    row = panel._dance_load(handle)
    assert row is not None and row["kind"] == "totp_enroll"
    assert row["state"]["form_fields"]["totpSecret"] == "s"
    # the handle is hashed at rest
    import psycopg

    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        cur = c.execute(
            "SELECT count(*) FROM ops.auth_dances WHERE handle_hash = %s",
            (handle,),
        )
        assert cur.fetchone()[0] == 0
    # unknown / expired handles fail closed
    assert panel._dance_load("bogus-handle") is None
    state["stage"] = "otp_form"
    panel._dance_update(handle, state)
    assert panel._dance_load(handle)["state"]["stage"] == "otp_form"
    panel._dance_delete(handle)
    assert panel._dance_load(handle) is None

# ---------------------------------------------------------------------------
# 2) Live Keycloak layer
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def panel_realm(env):
    """Dedicated realm wired exactly like provisioning does it (ADR-019).
    Requested only by the live-KC tests; the pure unit layer never touches
    Keycloak."""
    if not _kc_available():
        pytest.skip("Keycloak local (127.0.0.1:8080) not reachable")
    from app.config import Settings
    from app.tenants.keycloak_admin import KeycloakAdminClient

    kc = KeycloakAdminClient(Settings(
        database_url=os.environ["TRUST_DATABASE_URL"],
        keycloak_admin_url=KC_BASE,
        keycloak_admin_user="admin",
        keycloak_admin_password="KeycloakDemo2026",
    ))
    kc.ensure_realm(REALM)
    kc.enable_unmanaged_attributes(REALM)
    kc.ensure_tenant_id_mapper(REALM)
    kc.ensure_mfa_acr(REALM)
    kc.ensure_direct_grant_enabled(REALM)
    kc.ensure_direct_grant_flow(REALM)

    # Users (names set: VERIFY_PROFILE must not trigger — probe finding).
    for username, email, actions in (
        ("panel-main", EMAIL_MAIN, []),
        ("panel-otp", EMAIL_OTP, []),
        ("panel-tmp", EMAIL_TMP, ["UPDATE_PASSWORD"]),
    ):
        user = kc.find_user(REALM, username)
        if user is None:
            r = kc._request(
                "POST", f"/admin/realms/{REALM}/users", step="panel_test",
                json_body={
                    "username": username, "enabled": True, "email": email,
                    "emailVerified": True, "firstName": "Panel",
                    "lastName": username.title(),
                    "requiredActions": actions,
                    "attributes": {"tenant_id": [TENANT_PANEL]},
                },
            )
            assert r.status_code == 201, r.text
            user = kc.find_user(REALM, username)
        assert user is not None
        r = kc._request(
            "PUT",
            f"/admin/realms/{REALM}/users/{user['id']}/reset-password",
            step="panel_test",
            json_body={"type": "password", "value": PASSWORD,
                       "temporary": bool(actions)},
        )
        assert r.status_code in (200, 204), r.text
        # Idempotency across runs: the enrollment test must find the user
        # WITHOUT an OTP credential (a previous run may have enrolled one).
        if username == "panel-otp":
            r = kc._request(
                "GET",
                f"/admin/realms/{REALM}/users/{user['id']}/credentials",
                step="panel_test",
            )
            for cred in r.json():
                if cred.get("type") == "otp":
                    kc._request(
                        "DELETE",
                        f"/admin/realms/{REALM}/users/{user['id']}/"
                        f"credentials/{cred['id']}",
                        step="panel_test",
                    )
        # ...and the first-login test must find UPDATE_PASSWORD pending
        # (a previous run completed it).
        if username == "panel-tmp":
            r = kc._request(
                "PUT", f"/admin/realms/{REALM}/users/{user['id']}",
                step="panel_test",
                json_body={"requiredActions": ["UPDATE_PASSWORD"]},
            )
            assert r.status_code in (200, 204), r.text
    yield kc


@pytest.fixture(scope="module")
def panel_tenant_rows(panel_realm):
    """Tenant + users + roles in the ephemeral platform DB."""
    import psycopg

    kc = panel_realm
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        c.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_realm) "
            "VALUES (%s, 'Tenant Panel', %s, 'active', %s) "
            "ON CONFLICT (id) DO NOTHING",
            (TENANT_PANEL, ORG, REALM),
        )
        for username, email in (
            ("panel-main", EMAIL_MAIN),
            ("panel-otp", EMAIL_OTP),
            ("panel-tmp", EMAIL_TMP),
        ):
            sub = kc.find_user(REALM, username)["id"]
            c.execute(
                "INSERT INTO users (tenant_id, email, display_name, status, "
                "mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, 'active', "
                "false, %s) ON CONFLICT (tenant_id, email) DO UPDATE SET "
                "oidc_subject = EXCLUDED.oidc_subject, mfa_enrolled = false",
                (TENANT_PANEL, email, username.title(), sub),
            )
            c.execute(
                "INSERT INTO user_roles (tenant_id, user_id, role_key) "
                "SELECT %s, id, 'org_admin' FROM users WHERE oidc_subject = %s",
                (TENANT_PANEL, sub),
            )
    yield


@pytest.fixture(autouse=True)
def _clear_throttle(db):
    """The panel burns IP counters on every 401 — reset between tests so the
    progressive lockout of one test cannot bleed into the next. Depends on
    the session `db` fixture: the ephemeral database must exist first."""
    import psycopg

    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        c.execute("DELETE FROM ops.login_throttle")
    yield


def _login(client, email, password, totp=None):
    return client.post("/api/auth/panel/login", json={
        "org": ORG, "email": email, "password": password, "totp": totp,
    })


@live_kc
def test_login_wrong_password_is_generic_401(client, panel_tenant_rows):
    """D-AUTH-16: no oracle — wrong password and unknown email answer the
    SAME generic envelope."""
    r = _login(client, EMAIL_MAIN, "WrongPassword1!")
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["reason"] == "invalid_credentials"
    assert body["message"] == "Invalid email or password."

    r = _login(client, "nobody@tenant-panel.test", "WhateverPass1!")
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "invalid_credentials"
    assert r.json()["error"]["message"] == "Invalid email or password."


@live_kc
def test_unknown_org_is_404(client, panel_tenant_rows):
    r = client.post("/api/auth/panel/login", json={
        "org": "no-such-org", "email": "a@b.c", "password": "x",
    })
    assert r.status_code == 404


@live_kc
def test_login_success_mints_admin_session(client, panel_tenant_rows):
    """D-AUTH-12: password-only grant → acr 1 → session with mfa_verified
    false; the session is operative on the business surface."""
    r = _login(client, EMAIL_MAIN, PASSWORD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["mfa_verified"] is False
    assert body["session_token"].startswith("sts1.")
    assert body["csrf_token"]

    me = client.get("/api/me", headers={
        "Authorization": f"Bearer {body['session_token']}",
    })
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL_MAIN
    assert me.json()["mfa_verified"] is False


@live_kc
def test_totp_enrollment_full_dance(client, panel_tenant_rows, panel_realm):
    """D-AUTH-14 / AC-AUTH-014: setup → QR → confirm → MFA-verified session,
    users.mfa_enrolled sync, otp credential in the IdP, audit event."""
    import psycopg

    # setup: prove the password, start the dance
    r = client.post("/api/auth/panel/totp/setup", json={
        "org": ORG, "email": EMAIL_OTP, "password": PASSWORD,
    })
    assert r.status_code == 200, r.text
    handle = r.json()["handle"]
    assert handle

    # the QR endpoint serves the PNG data-URI for this handle only
    r = client.get(f"/api/auth/panel/totp/qr?handle={handle}")
    assert r.status_code == 200
    assert r.json()["qr_png"].startswith("data:image/png;base64,")
    r = client.get("/api/auth/panel/totp/qr?handle=not-a-handle")
    assert r.status_code == 410

    # white-box: the raw secret lives in the dance state (never exposed by
    # any endpoint); cached for the acr-evidence test below. psycopg already
    # decodes jsonb to dict.
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        cur = c.execute(
            "SELECT state FROM ops.auth_dances WHERE kind = 'totp_enroll'"
        )
        state = cur.fetchone()[0]
    secret = state["form_fields"]["totpSecret"]
    _ENROLL_SECRET["secret"] = secret

    # wrong code → 401 invalid_totp_code, dance stays alive for retry
    r = client.post("/api/auth/panel/totp/confirm", json={
        "handle": handle, "code": "000000",
    })
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "invalid_totp_code"

    # good code → enrollment completes, MFA-verified session minted
    r = client.post("/api/auth/panel/totp/confirm", json={
        "handle": handle, "code": _totp(secret),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["mfa_verified"] is True

    # the dance is consumed (single use)
    r = client.get(f"/api/auth/panel/totp/qr?handle={handle}")
    assert r.status_code == 410

    # directory sync (D-AUTH-11) + IdP credential + audit
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        cur = c.execute(
            "SELECT mfa_enrolled FROM users WHERE tenant_id = %s AND email = %s",
            (TENANT_PANEL, EMAIL_OTP),
        )
        assert cur.fetchone()[0] is True
        cur = c.execute(
            "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
            "AND action = 'auth.totp_enrolled'",
            (TENANT_PANEL,),
        )
        assert cur.fetchone()[0] >= 1
    kc_user = panel_realm.find_user(REALM, "panel-otp")
    assert panel_realm.user_has_otp_credential(REALM, kc_user["id"]) is True

    # the MFA-verified session passes the (unrelaxed) MFA gate
    me = client.get("/api/me", headers={
        "Authorization": f"Bearer {body['session_token']}",
    })
    assert me.status_code == 200
    assert me.json()["mfa_verified"] is True


_ENROLL_SECRET: dict = {}


@live_kc
def test_login_with_totp_carries_acr2_evidence(client, panel_tenant_rows):
    """D-AUTH-13 / AC-AUTH-013: after enrollment the direct grant REQUIRES
    the code; with it the session is MFA-verified (signed acr evidence,
    gate intact). The mfa hint appears ONLY for the enrolled account."""
    secret = _ENROLL_SECRET.get("secret")
    assert secret, "the enrollment test must run first (file order)"

    # missing code → 401 with the mfa hint (the account HAS OTP)
    r = _login(client, EMAIL_OTP, PASSWORD)
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "mfa_code_required"

    # wrong code → the hint again (the grant 401s and the account HAS OTP —
    # the message covers both wrong-password and wrong-code, D-AUTH-16)
    r = _login(client, EMAIL_OTP, PASSWORD, totp="000000")
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "mfa_code_required"

    # the hint never appears for accounts WITHOUT OTP
    r = _login(client, EMAIL_MAIN, "WrongPassword1!")
    assert r.json()["error"]["reason"] == "invalid_credentials"

    # good code → MFA-verified session (acr:"2" signed by the IdP). Fresh
    # window: this window's code was already consumed by the enrollment
    # dance and KC 26.2 rejects OTP replay.
    r = _login(client, EMAIL_OTP, PASSWORD, totp=_fresh_window_totp(secret))
    assert r.status_code == 200, r.text
    assert r.json()["mfa_verified"] is True


@live_kc
def test_update_password_first_login_flow(client, panel_tenant_rows):
    """D-AUTH-15 / AC-AUTH-015a: one-time password → update_password panel
    (proof HANDLE — the one-time password is not re-asked) → new password
    operative + session minted + audit."""
    # the temp password is PROVEN by the grant outcome → proof handle
    r = _login(client, EMAIL_TMP, PASSWORD)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "update_password"
    assert body["handle"]

    # set the new password with the proof handle (no current password)
    r = client.post("/api/auth/panel/password", json={
        "org": ORG, "email": EMAIL_TMP, "current_password": "",
        "new_password": NEW_PASSWORD, "totp": None,
        "proof_handle": body["handle"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # the proof handle is consumed and cannot reset anything again
    r = client.post("/api/auth/panel/password", json={
        "org": ORG, "email": EMAIL_TMP, "current_password": "",
        "new_password": "ShouldNotWork2026!", "totp": None,
        "proof_handle": body["handle"],
    })
    assert r.status_code == 410

    # the new password works; the old one no longer does
    r = _login(client, EMAIL_TMP, NEW_PASSWORD)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    r = _login(client, EMAIL_TMP, PASSWORD)
    assert r.status_code == 401

    # audit WITHOUT credential material (D-AUTH-16)
    import psycopg

    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as c:
        cur = c.execute(
            "SELECT payload FROM audit_log WHERE tenant_id = %s "
            "AND action = 'auth.password_changed' ORDER BY seq DESC LIMIT 1",
            (TENANT_PANEL,),
        )
        payload = cur.fetchone()[0]
    dumped = json.dumps(payload)
    assert payload["via"] == "panel"
    assert NEW_PASSWORD not in dumped
    assert PASSWORD not in dumped


@live_kc
def test_voluntary_password_change_requires_current(client, panel_tenant_rows):
    """D-AUTH-15 / AC-AUTH-015b: wrong current password → generic 401; the
    right one changes it and the new one operates on the next login."""
    r = client.post("/api/auth/panel/password", json={
        "org": ORG, "email": EMAIL_MAIN, "current_password": "NopeNope1!",
        "new_password": "AnotherNew2026!!",
    })
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "invalid_credentials"

    r = client.post("/api/auth/panel/password", json={
        "org": ORG, "email": EMAIL_MAIN, "current_password": PASSWORD,
        "new_password": "AnotherNew2026!!",
    })
    assert r.status_code == 200
    r = _login(client, EMAIL_MAIN, "AnotherNew2026!!")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@live_kc
def test_password_change_otp_hint_for_enrolled_account(client, panel_tenant_rows):
    """D-AUTH-16 consistency: an OTP-enrolled account changing password
    without the code gets the mfa hint (the password may be correct — the
    grant just needs the second factor)."""
    r = client.post("/api/auth/panel/password", json={
        "org": ORG, "email": EMAIL_OTP, "current_password": PASSWORD,
        "new_password": "WhateverNew2026!!",
    })
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "mfa_code_required"


@live_kc
def test_lockout_after_repeated_failures(client, panel_tenant_rows):
    """REQ-SEC-006 on the panel: progressive lockout persisted in DB."""
    for _ in range(5):
        r = _login(client, EMAIL_MAIN, "BadPassword1!")
        assert r.status_code == 401
    r = _login(client, EMAIL_MAIN, "AnotherNew2026!!")
    assert r.status_code == 429
    assert r.json()["error"]["reason"] == "rate_limited"


@live_kc
def test_totp_setup_wrong_password_is_generic_401(client, panel_tenant_rows):
    r = client.post("/api/auth/panel/totp/setup", json={
        "org": ORG, "email": EMAIL_MAIN, "password": "Wrong1!",
    })
    assert r.status_code == 401
    assert r.json()["error"]["reason"] == "invalid_credentials"


@live_kc
def test_totp_setup_already_enrolled_conflict(client, panel_tenant_rows):
    r = client.post("/api/auth/panel/totp/setup", json={
        "org": ORG, "email": EMAIL_OTP, "password": PASSWORD,
    })
    assert r.status_code == 409
    assert r.json()["error"]["reason"] == "already_enrolled"

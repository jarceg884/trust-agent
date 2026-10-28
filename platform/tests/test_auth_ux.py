"""Spec 015 — Auth UX: org-config, self-registration, brokered
auto-provision and the policy-aware MfaGate.

Unit level with the DECLARED IdP double (same contract as the rest of the
suite) and a declared Keycloak Admin REST double for the user-creation
paths (same pattern as tests/test_org_endpoints.py::FakeKeycloak). The live
Keycloak 26 path — themed login, inline TOTP enrollment, Google brokering —
is verified end-to-end in docs/evidencia/auth-ux/ (probe + browser run).

Seed semantics (conftest, D-AUTH-07):
  - tenant-a carries an explicit org row require_mfa=true (existing tenants
    keep their row) → the MfaGate fires for sessions without MFA.
  - tenant-b has NO org row → catalog default false (how NEW tenants are
    born) → MFA is optional and the app suggests enrolling.
"""
import uuid as _uuid

import pytest

from conftest import (
    TEST_DB,
    TENANT_A,
    TENANT_B,
    USER_CAROL,
    USER_DAVE,
    _admin,
    bearer_headers,
    mint,
)


class FakeKeycloakAuthUx:
    """Declared double: the slices of KeycloakAdminClient used by the
    self-registration (create_user_with_password) and the brokered
    auto-provision (get_user) paths."""

    def __init__(self):
        self.created = []
        self.looked_up = []
        self.users = {}  # sub -> representation

    def create_user_with_password(
        self, realm, *, username, email, tenant_id, display_name, password,
    ):
        self.created.append({
            "realm": realm, "username": username, "email": email,
            "tenant_id": tenant_id, "display_name": display_name,
            "password": password,
        })
        sub = str(_uuid.uuid4())
        self.users[sub] = {"enabled": True, "email": email}
        return sub

    def get_user(self, realm, user_id):
        self.looked_up.append({"realm": realm, "sub": user_id})
        return self.users.get(user_id)


@pytest.fixture
def fake_keycloak(monkeypatch):
    fake = FakeKeycloakAuthUx()
    monkeypatch.setattr("app.auth.register._keycloak", lambda: fake)
    return fake


def _register(client, *, org="tenant-b", email="new.user@tenant-b.test",
              password="a-strong-password-123", display_name="New User"):
    return client.post("/api/auth/register", json={
        "org": org, "email": email,
        "password": password, "display_name": display_name,
    })


def _brokered_token(idp, tenant=TENANT_B, sub=None, realm="tenant-b"):
    """Verified JWT for a subject that has NO users row (first brokered
    login shape: tenant_id claim present, arbitrary sub)."""
    return idp.make_jwt(
        sub=sub or str(_uuid.uuid4()), tenant_id=tenant,
        issuer=idp.issuer(realm), audience="trust-agent-web",
        amr=None, extra_claims={"acr": "1"},
    )


# ---------------------------------------------------------------------------
# GET /api/auth/org-config (D-AUTH-08, AC-AUTH-007)
# ---------------------------------------------------------------------------

def test_org_config_reflects_org_policies(client):
    """tenant-a: explicit org row require_mfa=true (existing tenants)."""
    resp = client.get("/api/auth/org-config", params={"org": "tenant-a"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["org"] == "tenant-a"
    assert body["require_mfa"] is True
    # catalog defaults (migration 0014)
    assert body["allow_self_registration"] is True
    # fail-closed: no reachable Keycloak admin → the Google hint stays off
    assert body["google_login_enabled"] is False


def test_org_config_default_optional_mfa(client):
    """tenant-b: NO org row → catalog default false (how new tenants are
    born, D-AUTH-07) — MFA optional, banner suggests it."""
    resp = client.get("/api/auth/org-config", params={"org": "tenant-b"})
    assert resp.status_code == 200
    assert resp.json()["require_mfa"] is False


def test_org_config_unknown_org_404(client):
    resp = client.get("/api/auth/org-config", params={"org": "no-such-org"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/auth/register (D-AUTH-04, AC-AUTH-002)
# ---------------------------------------------------------------------------

def test_register_happy_path(client, fake_keycloak):
    resp = _register(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["org"] == "tenant-b"
    user_id = body["user_id"]

    # Keycloak got the permanent password (temporary=False is the double's
    # contract; the real client sends reset-password temporary=false).
    assert len(fake_keycloak.created) == 1
    call = fake_keycloak.created[0]
    assert call["realm"] == "tenant-b"
    assert call["username"] == "new.user@tenant-b.test"
    assert call["password"] == "a-strong-password-123"

    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT status, oidc_subject FROM users "
            f"WHERE tenant_id = '{TENANT_B}' AND id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == "active"
        cur.execute(
            "SELECT role_key FROM user_roles "
            f"WHERE tenant_id = '{TENANT_B}' AND user_id = %s",
            (user_id,),
        )
        assert cur.fetchone()[0] == "user"
        cur.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_B}' AND action = 'user.self_registered' "
            "AND payload->>'email' = 'new.user@tenant-b.test'"
        )
        assert cur.fetchone()[0] == 1


def test_register_policy_off_403(client, fake_keycloak):
    """allow_self_registration=false → 403 self_registration_disabled."""
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO policies (id, tenant_id, level, key, value) "
            f"VALUES (gen_random_uuid(), '{TENANT_B}', 'org', "
            "'allow_self_registration', 'false'::jsonb)"
        )
    try:
        resp = _register(client, email="blocked@tenant-b.test")
        assert resp.status_code == 403
        assert resp.json()["error"]["reason"] == "self_registration_disabled"
        assert fake_keycloak.created == []
    finally:
        with _admin(TEST_DB) as c, c.cursor() as cur:
            cur.execute(
                "DELETE FROM policies "
                f"WHERE tenant_id = '{TENANT_B}' AND key = "
                "'allow_self_registration'"
            )


def test_register_duplicate_email_409(client, fake_keycloak):
    resp = _register(client, email="dave@tenant-b.test")
    assert resp.status_code == 409
    assert resp.json()["error"]["reason"] == "conflict"


def test_register_validation(client, fake_keycloak):
    short = _register(client, password="short")
    assert short.status_code == 422
    bad_email = _register(client, email="not-an-email")
    assert bad_email.status_code == 422
    unknown_org = _register(client, org="no-such-org")
    assert unknown_org.status_code == 404
    assert fake_keycloak.created == []


# ---------------------------------------------------------------------------
# Brokered auto-provision on mint (D-AUTH-05)
# ---------------------------------------------------------------------------

def test_mint_auto_provisions_brokered_google_user(client, idp, fake_keycloak):
    """First Google login: verified token, no users row → auto-provision
    with the minimum role + audit user.auto_provisioned via=google."""
    sub = str(_uuid.uuid4())
    fake_keycloak.users[sub] = {
        "enabled": True,
        "email": "google.user@gmail.test",
        "firstName": "Gina",
        "lastName": "Google",
        "federatedIdentities": [
            {"identityProvider": "google", "userId": "g-123",
             "userName": "google.user@gmail.test"},
        ],
    }
    resp = client.post("/api/auth/session", json={
        "id_token": _brokered_token(idp, sub=sub), "kind": "desktop",
    })
    assert resp.status_code == 201, resp.text
    tokens = resp.json()

    me = client.get("/api/me", headers=bearer_headers(tokens))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "google.user@gmail.test"

    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT u.id, r.role_key FROM users u "
            "JOIN user_roles r ON r.user_id = u.id "
            f"WHERE u.tenant_id = '{TENANT_B}' AND u.oidc_subject = %s",
            (sub,),
        )
        row = cur.fetchone()
        assert row is not None and row[1] == "user"
        cur.execute(
            "SELECT payload->>'via' FROM audit_log "
            f"WHERE tenant_id = '{TENANT_B}' AND action = 'user.auto_provisioned' "
            "AND target_id = %s",
            (str(row[0]),),
        )
        assert cur.fetchone()[0] == "google"


def test_whoami_exposes_tenant_name(client, idp):
    """t_b14cbefb: whoami carries the human-facing org name (tenants.name)
    as an ADDITIVE key next to tenant_slug — the console topbar shows the
    org name and degrades the slug to tooltip/secondary. Verified against
    the seeded row so the join cannot drift (session AND jwt paths share
    _build_principal, so one path suffices here)."""
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_slug"] == "tenant-a"
    assert body["tenant_name"] == "Tenant A"
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT name FROM tenants WHERE id = %s", (body["tenant_id"],)
        )
        assert cur.fetchone()[0] == body["tenant_name"]


def test_mint_unknown_user_fails_closed_when_policy_off(
    client, idp, fake_keycloak,
):
    """allow_self_registration=false → no auto-provision: unknown_user 401
    (the pre-015 behavior)."""
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO policies (id, tenant_id, level, key, value) "
            f"VALUES (gen_random_uuid(), '{TENANT_B}', 'org', "
            "'allow_self_registration', 'false'::jsonb)"
        )
    try:
        resp = client.post("/api/auth/session", json={
            "id_token": _brokered_token(idp), "kind": "desktop",
        })
        assert resp.status_code == 401
        assert fake_keycloak.looked_up == []  # never even asked Keycloak
    finally:
        with _admin(TEST_DB) as c, c.cursor() as cur:
            cur.execute(
                "DELETE FROM policies "
                f"WHERE tenant_id = '{TENANT_B}' AND key = "
                "'allow_self_registration'"
            )


# ---------------------------------------------------------------------------
# Policy-aware MfaGate (D-AUTH-07, AC-AUTH-003/005)
# ---------------------------------------------------------------------------

def test_gate_fires_when_org_requires_mfa(client, idp):
    """tenant-a (require_mfa=true): session without MFA → 403 mfa_required
    — spec 011 behavior intact (AC-AUTH-005)."""
    tokens = mint(client, idp, "alice", amr=None, extra_claims={"acr": "1"})
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"


def test_gate_open_when_mfa_optional(client, idp):
    """tenant-b (require_mfa=false): password-only session reaches the
    business API (AC-AUTH-003) — the banner suggests MFA instead."""
    tokens = mint(client, idp, "dave", tenant=TENANT_B,
                  amr=None, extra_claims={"acr": "1"})
    assert tokens["mfa_verified"] is False
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    assert resp.json()["mfa_verified"] is False


def test_gate_still_requires_mfa_when_mfa_verified_missing(client, idp):
    """tenant-b with MFA evidence: mfa_verified=true round-trips."""
    tokens = mint(client, idp, "dave", tenant=TENANT_B,
                  amr=None, extra_claims={"acr": "2"})
    assert tokens["mfa_verified"] is True
    resp = client.get("/api/me", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    assert resp.json()["mfa_verified"] is True


# ---------------------------------------------------------------------------
# D-AUTH-11 — users.mfa_enrolled sync (GAP-10.2, AC-AUTH-010)
# ---------------------------------------------------------------------------

def _mfa_flag(user_id):
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute("SELECT mfa_enrolled FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()[0]


def _set_mfa_flag(user_id, value):
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "UPDATE users SET mfa_enrolled = %s WHERE id = %s",
            (value, user_id),
        )


def test_mint_with_mfa_evidence_syncs_mfa_enrolled(client, idp):
    """AC-AUTH-010: a mint whose VERIFIED claims prove a second factor
    flips users.mfa_enrolled in the same transaction — the exact moment
    the inline TOTP enrollment converges (console re-dances with acr=2)."""
    _set_mfa_flag(USER_CAROL, False)
    try:
        tokens = mint(client, idp, "carol", amr=("mfa",))
        assert tokens["mfa_verified"] is True
        assert _mfa_flag(USER_CAROL) is True
    finally:
        _set_mfa_flag(USER_CAROL, True)


def test_mint_without_mfa_evidence_does_not_flip(client, idp):
    """Password-only mint (acr=1, no amr): the column stays false — the
    sync only follows real MFA evidence."""
    _set_mfa_flag(USER_CAROL, False)
    try:
        tokens = mint(client, idp, "carol", amr=None, extra_claims={"acr": "1"})
        assert tokens["mfa_verified"] is False
        assert _mfa_flag(USER_CAROL) is False
    finally:
        _set_mfa_flag(USER_CAROL, True)


def test_mfa_enrolled_is_monotonic(client, idp):
    """D-AUTH-11: a later password-only mint NEVER clears the column —
    not proving a second factor does not prove the user un-enrolled."""
    tokens = mint(client, idp, "dave", tenant=TENANT_B,
                  amr=None, extra_claims={"acr": "1"})
    assert tokens["mfa_verified"] is False
    assert _mfa_flag(USER_DAVE) is True  # seeded true, stays true


def test_stateless_jwt_with_mfa_evidence_syncs_mfa_enrolled(client, idp):
    """Desktop/plugin path: the raw id_token as Bearer never mints a DB
    session — the JWT branch of authenticate() carries the same sync."""
    from conftest import SUBS

    _set_mfa_flag(USER_CAROL, False)
    try:
        token = idp.make_jwt(
            sub=SUBS["carol"], tenant_id=TENANT_A,
            issuer=idp.issuer("tenant-a"), audience="trust-agent-web",
            amr=["mfa"],
        )
        resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert _mfa_flag(USER_CAROL) is True
    finally:
        _set_mfa_flag(USER_CAROL, True)


def test_directory_exposes_mfa_enrolled(client, idp):
    """AC-AUTH-010: GET /api/users reflects the synced column so the
    directory never says 'no MFA' about a user that has it active."""
    tokens = mint(client, idp, "alice", amr=("mfa",))
    resp = client.get("/api/users", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    users = {u["email"]: u for u in resp.json()["users"]}
    assert users["carol@tenant-a.test"]["mfa_enrolled"] is True
    assert users["frank@tenant-a.test"]["mfa_enrolled"] is False

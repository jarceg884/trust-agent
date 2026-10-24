"""REQ-SEC-006 — login rate limiting persisted in ops.login_throttle.

AC-SEC-006 scenarios:
  - consecutive failures (5) -> 429 rate_limited with Retry-After;
  - the counter survives an API process restart (engine reset);
  - the failure window expires and the counter restarts;
  - lockout is progressive (doubles per extra failure inside the window);
  - per-account and per-IP counting are independent;
  - a successful login resets the account counter;
  - a flooding IP is blocked BEFORE token verification (even valid logins).

The default TestClient host ("testclient") is not an IP, so _client_ip()
returns None there — perfect for exercising the account-only counters; the
IP tests use a dedicated TestClient with a real client address.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from conftest import SUBS, TENANT_A

from app.auth import throttle


@pytest.fixture(autouse=True)
def _clean_throttle(admin_conn):
    """Isolate the counters per test (the rest of the suite must not see them)."""
    def _wipe():
        with admin_conn.cursor() as cur:
            cur.execute("DELETE FROM ops.login_throttle")
    _wipe()
    yield
    _wipe()


def _frank_token(idp):
    """Verified claims, but the user is disabled -> business-tx failure."""
    return idp.make_jwt(
        sub=SUBS["frank"], tenant_id=TENANT_A, issuer=idp.issuer("tenant-a")
    )


def _post_mint(client, token):
    return client.post("/api/auth/session", json={"id_token": token})


def _acct_row(admin_conn, key):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT fail_count, locked_until - now() AS remaining, locked_until "
            "FROM ops.login_throttle WHERE key = %s", (key,)
        )
        return cur.fetchone()


def test_consecutive_failures_lock_the_account(client, idp, admin_conn):
    token = _frank_token(idp)
    for _ in range(5):
        resp = _post_mint(client, token)
        assert resp.status_code == 401, resp.text
    # 6th attempt: locked with the stable reason + Retry-After.
    resp = _post_mint(client, token)
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["reason"] == "rate_limited"
    retry_after = int(resp.headers["Retry-After"])
    assert 1 <= retry_after <= 30
    # First lockout lasts the base delay (30 s by default).
    key = throttle.account_key(TENANT_A, SUBS["frank"])
    fail_count, remaining, locked_until = _acct_row(admin_conn, key)
    assert fail_count == 5
    assert locked_until is not None
    assert 25 <= remaining.total_seconds() <= 30


def test_locked_account_does_not_block_other_accounts(client, idp, admin_conn):
    from conftest import mint

    token = _frank_token(idp)
    for _ in range(5):
        assert _post_mint(client, token).status_code == 401
    assert _post_mint(client, token).status_code == 429
    # A different account from the same (non-IP) client still logs in.
    tokens = mint(client, idp, "alice")
    assert tokens["session_token"]


def test_counter_survives_api_restart(client, idp, admin_conn):
    """AC-SEC-006: the counter is in PostgreSQL, not in process memory."""
    from app.db import reset_engine

    token = _frank_token(idp)
    for _ in range(5):
        assert _post_mint(client, token).status_code == 401
    assert _post_mint(client, token).status_code == 429

    reset_engine()  # simulate a process restart (fresh engine, no memory state)

    resp = _post_mint(client, token)
    assert resp.status_code == 429, "the lock must survive an API restart"
    key = throttle.account_key(TENANT_A, SUBS["frank"])
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT fail_count FROM ops.login_throttle WHERE key = %s", (key,)
        )
        assert cur.fetchone()[0] == 5


def test_window_expiry_restarts_the_counter(client, idp, admin_conn):
    token = _frank_token(idp)
    for _ in range(5):
        assert _post_mint(client, token).status_code == 401
    assert _post_mint(client, token).status_code == 429

    # Pretend the whole failure window happened long ago (window is 900 s).
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE ops.login_throttle "
            "SET last_fail_at = now() - interval '16 minutes', "
            "    window_started_at = now() - interval '16 minutes', "
            "    locked_until = NULL"
        )

    resp = _post_mint(client, token)
    assert resp.status_code == 401, "window expired: not locked anymore"
    key = throttle.account_key(TENANT_A, SUBS["frank"])
    fail_count, remaining, locked_until = _acct_row(admin_conn, key)
    assert fail_count == 1, "the counter restarts from 1 in a fresh window"
    assert locked_until is None


def test_lockout_is_progressive(client, idp, admin_conn):
    token = _frank_token(idp)
    for _ in range(5):
        assert _post_mint(client, token).status_code == 401

    # First lock: base delay (30 s).
    key = throttle.account_key(TENANT_A, SUBS["frank"])
    fail_count, remaining, _ = _acct_row(admin_conn, key)
    assert fail_count == 5
    assert 25 <= remaining.total_seconds() <= 30

    # Expire the lock but stay INSIDE the failure window -> next failure
    # re-locks with a doubled delay (progressive backoff).
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE ops.login_throttle SET locked_until = now() - interval '1 second'"
        )
    assert _post_mint(client, token).status_code == 401
    fail_count, remaining, _ = _acct_row(admin_conn, key)
    assert fail_count == 6
    assert 55 <= remaining.total_seconds() <= 60


def test_successful_login_resets_the_account_counter(client, idp, admin_conn):
    from conftest import mint

    # 4 verified-claim failures for alice (tenant A pins its issuer; a token
    # from realm tenant-b is rejected) -> counter at 4, not yet locked.
    bad = idp.make_jwt(
        sub=SUBS["alice"], tenant_id=TENANT_A, issuer=idp.issuer("tenant-b")
    )
    for _ in range(4):
        assert _post_mint(client, bad).status_code == 401
    key = throttle.account_key(TENANT_A, SUBS["alice"])
    fail_count, _, _ = _acct_row(admin_conn, key)
    assert fail_count == 4

    mint(client, idp, "alice")  # 201 (mint asserts it)

    fail_count, _, locked_until = _acct_row(admin_conn, key)
    assert fail_count == 0
    assert locked_until is None


def test_flooding_ip_is_blocked_before_verification(db, idp, admin_conn):
    """Per-IP counting: garbage tokens burn the IP counter; once locked even
    a VALID token from that IP gets 429 before any verification."""
    from app.main import create_app

    flood_ip = "203.0.113.7"
    with TestClient(create_app(), client=(flood_ip, 44444)) as flood_client:
        for _ in range(5):
            garbage = idp.make_jwt(
                sub=SUBS["alice"], tenant_id=TENANT_A,
                issuer=idp.issuer("tenant-a"), sign_with_other_key=True,
            )
            assert _post_mint(flood_client, garbage).status_code == 401

        valid = idp.make_jwt(
            sub=SUBS["alice"], tenant_id=TENANT_A, issuer=idp.issuer("tenant-a")
        )
        resp = _post_mint(flood_client, valid)
        assert resp.status_code == 429, resp.text
        assert resp.json()["error"]["reason"] == "rate_limited"
        assert int(resp.headers["Retry-After"]) >= 1

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT fail_count FROM ops.login_throttle WHERE key = %s",
            (f"ip:{flood_ip}",),
        )
        assert cur.fetchone()[0] == 5


def test_account_key_is_depersonalized():
    """The provider-plane table stores the subject hashed, never raw."""
    key = throttle.account_key(TENANT_A, "sub-alice-orgadmin")
    assert "sub-alice-orgadmin" not in key
    assert key == f"acct:{TENANT_A}:" + hashlib.sha256(
        b"sub-alice-orgadmin"
    ).hexdigest()

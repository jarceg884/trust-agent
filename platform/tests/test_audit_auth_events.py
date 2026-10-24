"""AC-AU-020/021/022/023 — auth + jobs audit instrumentation (spec 008).

Every flow emits its event inside the same business transaction (D-AU-07);
failed logins that must survive the 401 rollback use an explicit commit
(D-AU-03/04).
"""
from app.config import get_settings
from app.db import build_engine
from app.jobs import service as job_service
from tests.conftest import (
    TENANT_A, USER_ALICE, bearer_headers, mint,
)


def _last_events(admin_conn, action, tenant=TENANT_A, limit=5):
    rows = admin_conn.execute(
        "SELECT seq, actor_role, actor_id, action, target_type, target_id, payload "
        "FROM audit_log WHERE tenant_id = %s AND action = %s "
        "ORDER BY id DESC LIMIT %s",
        (tenant, action, limit),
    ).fetchall()
    return [dict(zip(("seq", "actor_role", "actor_id", "action", "target_type",
                      "target_id", "payload"), r)) for r in rows]


def test_auth_login_event(client, idp, admin_conn):
    """AC-AU-020: successful mint -> auth.login with actor + session target."""
    tokens = mint(client, idp, "alice")
    session_id = tokens["session_token"].split(".")[2]
    events = _last_events(admin_conn, "auth.login", limit=1)
    assert events, "auth.login missing after successful mint"
    ev = events[0]
    assert str(ev["actor_id"]) == USER_ALICE
    assert ev["actor_role"] == "org_admin"
    assert ev["target_type"] == "session"
    assert ev["target_id"] == session_id
    assert ev["payload"]["kind"] == "desktop"
    assert ev["payload"]["mfa_verified"] is True
    assert ev["payload"]["via"] == "oidc"


def test_login_failed_unknown_user(client, idp, admin_conn):
    """AC-AU-020: valid JWT, unknown sub -> 401 AND auth.login_failed persisted."""
    before = admin_conn.execute(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = 'auth.login_failed'",
        (TENANT_A,),
    ).fetchone()[0]
    token = idp.make_jwt(
        sub="sub-ghost-user", tenant_id=TENANT_A, issuer=idp.issuer("tenant-a"),
        amr=["mfa"],
    )
    resp = client.post("/api/auth/session", json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"
    after = admin_conn.execute(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = 'auth.login_failed'",
        (TENANT_A,),
    ).fetchone()[0]
    assert after == before + 1, "auth.login_failed must survive the 401 rollback"
    ev = _last_events(admin_conn, "auth.login_failed", limit=1)[0]
    assert ev["payload"]["reason"] == "unknown_user"
    assert ev["actor_role"] == "system"


def test_login_failed_pre_tenant_not_audited(client, idp, admin_conn):
    """AC-AU-020 / D-AU-03: pre-tenant failures (bad signature) emit NOTHING.

    A forged token must not pollute any tenant's chain.
    """
    def _total():
        return admin_conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]

    before = _total()
    token = idp.make_jwt(
        sub="sub-alice-orgadmin", tenant_id=TENANT_A,
        issuer=idp.issuer("tenant-a"), sign_with_other_key=True,
    )
    resp = client.post("/api/auth/session", json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401
    assert _total() == before, "forged-token failure must not touch any chain"


def test_auth_logout_event(client, idp, admin_conn):
    """AC-AU-021: logout -> auth.logout with actor + session target."""
    tokens = mint(client, idp, "alice")
    session_id = tokens["session_token"].split(".")[2]
    resp = client.post("/api/auth/logout", headers=bearer_headers(tokens))
    assert resp.status_code == 204
    ev = _last_events(admin_conn, "auth.logout", limit=1)[0]
    assert str(ev["actor_id"]) == USER_ALICE
    assert ev["target_id"] == session_id


def test_refresh_reuse_audited(client, idp, admin_conn):
    """AC-AU-022: refresh replay -> 401, session revoked AND auth.login_failed."""
    tokens = mint(client, idp, "alice")
    first = client.post("/api/auth/refresh",
                        json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    replay = client.post("/api/auth/refresh",
                         json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
    ev = _last_events(admin_conn, "auth.login_failed", limit=1)[0]
    assert ev["payload"]["reason"] == "refresh_reuse"
    assert ev["target_type"] == "session"
    # The session was revoked by the reuse detection (spec 003 behavior).
    session_id = tokens["session_token"].split(".")[2]
    revoked = admin_conn.execute(
        "SELECT status FROM sessions WHERE id = %s", (session_id,)
    ).fetchone()
    assert revoked[0] == "revoked"


def test_job_events(client, idp, admin_conn):
    """AC-AU-023: job.created on enqueue (idempotent hit does not re-emit),
    job.completed when the worker completes the job."""
    from app.jobs.worker import run_once

    engine = build_engine(get_settings())
    job = job_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="echo",
        payload={"audit": True}, idempotency_key="audit-job-test",
    )
    job_id = str(job["id"])

    def _count(action):
        return admin_conn.execute(
            "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
            "AND action = %s AND target_id = %s",
            (TENANT_A, action, job_id),
        ).fetchone()[0]

    assert _count("job.created") == 1
    # Idempotent hit: same (tenant, type, key) -> no new event.
    again = job_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="echo",
        payload={"audit": True}, idempotency_key="audit-job-test",
    )
    assert str(again["id"]) == job_id
    assert _count("job.created") == 1

    # Drain the queue with the real worker loop until our job is done.
    migrator = job_service.build_migrator_engine(get_settings().database_migrator_url)
    for _ in range(20):
        ran = run_once(engine, migrator)
        status = admin_conn.execute(
            "SELECT status FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()[0]
        if status == "succeeded":
            break
        if not ran:
            break
    assert status == "succeeded"
    assert _count("job.completed") == 1
    ev = _last_events(admin_conn, "job.completed", limit=5)
    assert any(e["target_id"] == job_id for e in ev)

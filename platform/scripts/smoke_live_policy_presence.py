"""Live smoke — spec 027 (live policy + presence) over a REAL uvicorn server.

Boots the actual control plane (uvicorn, real socket) on a fresh DB created
by the canonical Alembic line, drives the REAL enrollment flow
(enroll -> approve -> exchange) and then the full spec 027 journey with
plain HTTP calls — no TestClient, nothing simulated:

  1. heartbeat (plugin path: Bearer device token from the exchange)
     -> 200, presence persisted (last_heartbeat_at / applied / uptime)
  2. admin updates a policy -> heartbeat signals policy_update_available
  3. device re-pulls the governance bundle (JWS, new policy_version)
  4. heartbeat confirms -> policy_update_available=false + audit
     policy.propagated {from_version, to_version}
  5. GET /api/devices/presence (admin) -> online_count=1, who's connected,
     applied policy version, uptime
  6. audit sampling: repeated heartbeats -> a single device.heartbeat row
  7. revoke -> heartbeat 401 -> presence shows the device offline and out
     of the online count

Run: platform/.venv/bin/python scripts/smoke_live_policy_presence.py
"""
import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

PLATFORM = "/root/trust-agent/platform"
VENV_PYTHON = f"{PLATFORM}/.venv/bin/python"
SMOKE_DB = "trust_smoke_027"
PORT = 8917
ADMIN = ("host=127.0.0.1 port=55432 user=postgres password=trustlocal "
         "dbname=postgres")

TENANT_A = "11111111-1111-1111-1111-111111111111"
DEPT_A1 = "30000000-0000-0000-0000-000000000001"
USER_ALICE = "40000000-0000-0000-0000-000000000001"
USER_CAROL = "40000000-0000-0000-0000-000000000003"

sys.path.insert(0, PLATFORM)

PASS = 0


def ok(label, cond, detail=""):
    global PASS
    if not cond:
        raise AssertionError(f"FAIL {label} {detail}")
    PASS += 1
    print(f"PASS {label} {detail}")


def req(method, path, body=None, headers=None, want_headers=False):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        **(headers or {})})
    try:
        with urllib.request.urlopen(r) as resp:
            out = (resp.status, json.loads(resp.read() or b"{}"))
            if want_headers:
                return out + (resp.headers,)
            return out
    except urllib.error.HTTPError as e:
        out = (e.code, json.loads(e.read() or b"{}"))
        if want_headers:
            return out + (e.headers,)
        return out


def main():
    import psycopg
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from tests.conftest import _IdP

    # 1. Fresh smoke DB via the canonical Alembic line
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {SMOKE_DB}")
        c.execute(f"CREATE DATABASE {SMOKE_DB}")
    proc = subprocess.run(
        [f"{PLATFORM}/.venv/bin/alembic", "-c", "alembic.ini", "upgrade",
         "head"],
        cwd=PLATFORM, capture_output=True, text=True,
        env=dict(os.environ, DATABASE_URL=(
            f"postgresql+psycopg://postgres:trustlocal@127.0.0.1:55432/"
            f"{SMOKE_DB}")),
    )
    assert proc.returncode == 0, f"alembic failed:\n{proc.stderr}"

    # 2. Seed tenant A + alice (org_admin) + carol (user)
    dsn = ADMIN.replace("dbname=postgres", f"dbname={SMOKE_DB}")
    idp = _IdP()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_issuer_url) "
            "VALUES (%s,'Tenant A','tenant-a','active',%s)",
            (TENANT_A, idp.issuer("tenant-a")))
        c.execute("INSERT INTO departments (id, tenant_id, name) "
                  "VALUES (%s, %s, 'IT')", (DEPT_A1, TENANT_A))
        c.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            "display_name, status, mfa_enrolled, oidc_subject) VALUES "
            "(%s,%s,%s,'alice@tenant-a.test','Alice','active',true,%s), "
            "(%s,%s,%s,'carol@tenant-a.test','Carol','active',true,%s)",
            (USER_ALICE, TENANT_A, DEPT_A1, "sub-alice-orgadmin",
             USER_CAROL, TENANT_A, DEPT_A1, "sub-carol-user"))
        c.execute("INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                  "VALUES (gen_random_uuid(), %s, %s, 'org_admin'), "
                  "(gen_random_uuid(), %s, %s, 'user')",
                  (TENANT_A, USER_ALICE, TENANT_A, USER_CAROL))

    # 3. Boot the real server (policy JWS key generated for the smoke)
    jws_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jws_priv_b64 = base64.b64encode(jws_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    jws_pub_pem = jws_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    env = dict(
        os.environ,
        TRUST_DATABASE_URL=(
            f"postgresql+psycopg://trust_app:trust_app_dev@127.0.0.1:55432/"
            f"{SMOKE_DB}"),
        TRUST_DATABASE_MIGRATOR_URL=(
            f"postgresql+psycopg://trust_migrator:trust_migrator_dev@"
            f"127.0.0.1:55432/{SMOKE_DB}"),
        TRUST_IDP_BASE_URLS=idp.base_url,
        TRUST_OIDC_AUDIENCE="trust-agent-web",
        TRUST_DEVICE_TOKEN_SECRET="smoke-device-secret-32-bytes-ok!",
        TRUST_VAULT_KEK_B64=base64.b64encode(b"\x01" * 32).decode(),
        TRUST_POLICY_JWS_PRIVATE_KEY_B64=jws_priv_b64.decode(),
        TRUST_POLICY_JWS_ISSUER="https://trust-agent.smoke-027/",
    )
    server = subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=PLATFORM, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    def heartbeat(applied, uptime=300):
        return req("POST", "/api/me/device-heartbeat", {
            "applied_policy_version": applied,
            "uptime_seconds": uptime,
            "mode": "normal",
        }, headers={"Authorization": f"Bearer {device_token}",
                    "X-Device-Token": device_token})

    try:
        for _ in range(60):
            try:
                status, body = req("GET", "/healthz")
                if status == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("server did not come up")
        print(f"SMOKE /healthz -> {status} {body}")

        def jwt_for(sub):
            return idp.make_jwt(sub=sub, tenant_id=TENANT_A,
                                issuer=idp.issuer("tenant-a"), amr=["mfa"])

        # 4. REAL enrollment flow (spec 007): enroll -> approve -> exchange
        st, alice, ahdr = req("POST", "/api/auth/session",
                              {"id_token": jwt_for("sub-alice-orgadmin"),
                               "kind": "admin"}, want_headers=True)
        assert st == 201, alice
        cookie = ahdr.get("Set-Cookie").split(";")[0]
        alice_h = {"Cookie": cookie, "X-CSRF-Token": alice["csrf_token"]}

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        st, enrolled = req("POST", "/api/devices/enroll", {
            "org_code": "tenant-a", "id_token": jwt_for("sub-carol-user"),
            "public_key": pub, "fingerprint": f"smoke027-{uuid.uuid4().hex[:8]}",
            "device_name": "carol-hermes-desktop", "platform": "linux",
        })
        ok("enroll", st == 202 and enrolled["status"] == "pending")
        device_id = enrolled["device_id"]
        etoken = enrolled["enrollment_token"]

        st, _ = req("POST", f"/api/devices/{device_id}/approve",
                    headers=alice_h)
        ok("approve", st == 200)
        st, exchanged = req("POST", "/api/devices/enroll/exchange",
                            {"enrollment_token": etoken})
        ok("exchange", st == 200 and exchanged.get("device_token"))
        device_token = exchanged["device_token"]

        # 5. AC-027-01: heartbeat (plugin path) -> presence persisted
        st, hb = heartbeat(applied=0, uptime=300)
        ok("heartbeat-200", st == 200, f"current=v{hb['current_policy_version']}")
        ok("heartbeat-no-update", hb["policy_update_available"] is False)
        ok("heartbeat-contract",
           hb["heartbeat_interval_seconds"] == 300
           and hb["online_window_seconds"] == 600 and hb["server_time"])
        with psycopg.connect(dsn, autocommit=True) as c:
            row = c.execute(
                "SELECT last_heartbeat_at IS NOT NULL, applied_policy_version, "
                "agent_uptime_seconds FROM devices WHERE id = %s",
                (device_id,)).fetchone()
        ok("presence-persisted", row == (True, 0, 300), str(row))

        # 6. AC-027-02: admin updates the policy -> update signal
        st, pol = req("PUT", "/api/policies/org",
                      {"key": "max_daily_sessions", "value": 5},
                      headers=alice_h)
        ok("admin-put-policy", st == 200, f"version=v{pol['version']}")
        v1 = pol["version"]
        st, hb = heartbeat(applied=0)
        ok("update-available",
           st == 200 and hb["policy_update_available"] is True
           and hb["current_policy_version"] == v1)

        # 7. AC-027-03: the device re-pulls the signed bundle (no restart,
        #    no re-enroll) and applies the new version.
        st, bundle = req("GET", "/api/me/governance-bundle", headers={
            "Authorization": f"Bearer {device_token}",
            "X-Device-Token": device_token})
        ok("bundle-200", st == 200)
        claims = pyjwt.decode(bundle["bundle"], jws_pub_pem,
                              algorithms=["RS256"], audience=device_id,
                              issuer="https://trust-agent.smoke-027/")
        ok("bundle-new-version", claims["policy_version"] == v1,
           f"v{claims['policy_version']}")

        # 8. heartbeat confirms -> no update + audit policy.propagated
        st, hb = heartbeat(applied=v1, uptime=360)
        ok("confirmed-no-update", st == 200
           and hb["policy_update_available"] is False)
        with psycopg.connect(dsn, autocommit=True) as c:
            row = c.execute(
                "SELECT payload FROM audit_log WHERE action='policy.propagated' "
                "AND device_id = %s", (device_id,)).fetchone()
        ok("audit-propagated",
           row is not None and row[0]["from_version"] == 0
           and row[0]["to_version"] == v1, str(row and row[0]))

        # 9. AC-027-05: sampling — repeated heartbeats, single audit row
        heartbeat(applied=v1, uptime=361)
        heartbeat(applied=v1, uptime=362)
        with psycopg.connect(dsn, autocommit=True) as c:
            n = c.execute(
                "SELECT count(*) FROM audit_log WHERE action='device.heartbeat' "
                "AND device_id = %s", (device_id,)).fetchone()[0]
        ok("audit-sampled", n == 1, f"{n} row(s)")

        # 10. AC-027-04: presence view (admin) — how many & who
        st, presence = req("GET", "/api/devices/presence", headers=alice_h)
        ok("presence-200", st == 200)
        row = next(d for d in presence["devices"] if d["id"] == device_id)
        ok("presence-online",
           presence["online_count"] == 1 and row["online"] is True
           and row["status"] == "active")
        ok("presence-who",
           row["email"] == "carol@tenant-a.test"
           and row["department_name"] == "IT"
           and row["device_name"] == "carol-hermes-desktop")
        ok("presence-policy",
           row["applied_policy_version"] == v1
           and row["policy_up_to_date"] is True
           and row["agent_uptime_seconds"] == 362
           and row["last_heartbeat_at"])

        # 11. AC-027-07: revoke -> heartbeat 401 -> out of connected
        st, _ = req("POST", f"/api/devices/{device_id}/revoke",
                    headers=alice_h)
        ok("revoke", st == 200)
        st, hb = heartbeat(applied=v1)
        ok("heartbeat-401-after-revoke",
           st == 401 and hb["error"]["reason"] == "device_not_authorized")
        st, presence = req("GET", "/api/devices/presence", headers=alice_h)
        row = next(d for d in presence["devices"] if d["id"] == device_id)
        ok("presence-offline-after-revoke",
           presence["online_count"] == 0 and row["online"] is False
           and row["status"] == "revoked")

        print(f"\nSMOKE PASS {PASS}/{PASS} — spec 027 live journey OK")
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=10)
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (SMOKE_DB,))
            c.execute(f"DROP DATABASE IF EXISTS {SMOKE_DB}")


if __name__ == "__main__":
    main()

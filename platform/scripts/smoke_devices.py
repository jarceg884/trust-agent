"""Live smoke — spec 007 devices flow over a REAL uvicorn server.

Boots the actual control plane (uvicorn, real socket), a real JWKS IdP
(in-process HTTP server), and drives the full enrollment flow with plain
HTTP calls: enroll -> approve -> exchange -> revoke -> 410. No TestClient.
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
SMOKE_DB = "trust_smoke_007"
PORT = 8907
ADMIN = ("host=127.0.0.1 port=55432 user=postgres password=trustlocal "
         "dbname=postgres")

TENANT_A = "11111111-1111-1111-1111-111111111111"
DEPT_A1 = "30000000-0000-0000-0000-000000000001"
USER_ALICE = "40000000-0000-0000-0000-000000000001"
USER_CAROL = "40000000-0000-0000-0000-000000000003"

sys.path.insert(0, PLATFORM)


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
            "INSERT INTO tenants (id, name, slug, status, oidc_realm, "
            "oidc_issuer_url) VALUES (%s,'Tenant A','tenant-a','active',"
            "'tenant-a', %s)", (TENANT_A, idp.issuer("tenant-a")))
        c.execute("INSERT INTO departments (id, tenant_id, name) "
                  "VALUES (%s, %s, 'IT')", (DEPT_A1, TENANT_A))
        c.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            "display_name, status, mfa_enrolled, oidc_subject) VALUES "
            "(%s,%s,%s,'alice@tenant-a.test','Alice','active',true,%s), "
            "(%s,%s,%s,'carol@tenant-a.test','Carol','active',true,%s)",
            (USER_ALICE, TENANT_A, DEPT_A1, "sub-alice-orgadmin",
             USER_CAROL, TENANT_A, DEPT_A1, "sub-carol-user"))
        c.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key) VALUES "
            f"(gen_random_uuid(), '{TENANT_A}', '{USER_ALICE}', 'org_admin'), "
            f"(gen_random_uuid(), '{TENANT_A}', '{USER_CAROL}', 'user')")

    # 3. Boot the real server
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
    )
    server = subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=PLATFORM, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
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

        # 4. Full flow over real HTTP
        def jwt_for(sub):
            return idp.make_jwt(sub=sub, tenant_id=TENANT_A,
                                issuer=idp.issuer("tenant-a"), amr=["mfa"])

        # alice session — Admin Console style: HttpOnly cookie + CSRF token
        # (the session_token is deliberately NOT returned in the body for
        # kind="admin"; the cookie is the credential).
        st, alice, ahdr = req("POST", "/api/auth/session",
                              {"id_token": jwt_for("sub-alice-orgadmin"),
                               "kind": "admin"}, want_headers=True)
        print(f"SMOKE alice session -> {st} (cookie auth)")
        assert st == 201, alice
        cookie = ahdr.get("Set-Cookie").split(";")[0]
        alice_h = {"Cookie": cookie, "X-CSRF-Token": alice["csrf_token"]}
        st, body = req("GET", "/api/devices", headers=alice_h)
        print(f"SMOKE alice GET /api/devices -> {st}")
        assert st == 200

        # carol enrolls her desktop (real keypair, first-class: no scraping)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        fp = f"smoke-{uuid.uuid4().hex[:8]}"
        st, enrolled = req("POST", "/api/devices/enroll", {
            "org_code": "tenant-a", "id_token": jwt_for("sub-carol-user"),
            "public_key": pub, "fingerprint": fp,
            "device_name": "carol-desktop", "platform": "linux",
        })
        print(f"SMOKE enroll -> {st} status={enrolled.get('status')}")
        assert st == 202 and enrolled["status"] == "pending"
        device_id = enrolled["device_id"]
        etoken = enrolled["enrollment_token"]

        # exchange while pending -> 202 (polling)
        st, body = req("POST", "/api/devices/enroll/exchange",
                       {"enrollment_token": etoken})
        print(f"SMOKE exchange(pending) -> {st} {body.get('status')}")
        assert st == 202

        # admin approves from the console
        st, body = req("POST", f"/api/devices/{device_id}/approve",
                       headers=alice_h)
        print(f"SMOKE approve -> {st} {body}")
        assert st == 200 and body["status"] == "active"

        # exchange -> signed device token (one-time)
        st, body = req("POST", "/api/devices/enroll/exchange",
                       {"enrollment_token": etoken})
        print(f"SMOKE exchange(active) -> {st} expires_in={body.get('expires_in')}")
        assert st == 200 and body["device_token"]
        device_token = body["device_token"]

        # verify the device token signature locally (HS256 pinned)
        import jwt as pyjwt
        claims = pyjwt.decode(device_token, "smoke-device-secret-32-bytes-ok!",
                              algorithms=["HS256"])
        assert claims["device_id"] == device_id
        assert claims["user_id"] == USER_CAROL
        print(f"SMOKE device_token claims OK (device={device_id[:8]}..., "
              f"exp-iat={claims['exp'] - claims['iat']}s)")

        # second redemption -> 410 gone
        st, body = req("POST", "/api/devices/enroll/exchange",
                       {"enrollment_token": etoken})
        print(f"SMOKE exchange(2nd) -> {st} reason={body['error']['reason']}")
        assert st == 410

        # carol's own dashboard shows the device
        st, carol = req("POST", "/api/auth/session",
                        {"id_token": jwt_for("sub-carol-user"),
                         "kind": "desktop"})
        carol_h = {"Authorization": f"Bearer {carol['session_token']}"}
        st, body = req("GET", "/api/devices/me", headers=carol_h)
        mine = [d for d in body["devices"] if d["id"] == device_id]
        print(f"SMOKE /api/devices/me -> {st} status={mine[0]['status']}")
        assert st == 200 and mine[0]["status"] == "active"

        # revoke -> immediate: exchange dead, /me shows revoked
        st, body = req("POST", f"/api/devices/{device_id}/revoke",
                       headers=alice_h)
        print(f"SMOKE revoke -> {st} {body.get('status')}")
        assert st == 200 and body["status"] == "revoked"
        st, body = req("GET", "/api/devices/me", headers=carol_h)
        mine = [d for d in body["devices"] if d["id"] == device_id]
        assert mine[0]["status"] == "revoked"
        print("SMOKE /api/devices/me after revoke -> revoked OK")

        # audit trail with device_id
        with psycopg.connect(dsn, autocommit=True) as c:
            rows = c.execute(
                "SELECT action FROM audit_log WHERE device_id = %s "
                "ORDER BY seq", (device_id,)).fetchall()
            actions = [r[0] for r in rows]
            print(f"SMOKE audit actions -> {actions}")
            assert actions == ["device.enrolled", "device.approved",
                               "device.revoked"]
            ver = c.execute("SELECT audit_verify_chain(%s)",
                            (TENANT_A,)).fetchone()
            status = ver[0][0] if isinstance(ver[0], tuple) else ver[0]
            print(f"SMOKE audit chain -> {status}")
            assert status == "ok"

        print("SMOKE PASS: flujo completo de devices sobre uvicorn real")
    finally:
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        idp._server.shutdown()
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s", (SMOKE_DB,))
            c.execute(f"DROP DATABASE IF EXISTS {SMOKE_DB}")


if __name__ == "__main__":
    main()

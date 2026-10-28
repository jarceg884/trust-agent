#!/usr/bin/env python3
"""Smoke vivo de Roles & Access (spec 017, feedback Miller #4).

Evidencia REAL (constitution I): control plane REAL (uvicorn, socket real,
HTTP real) + PostgreSQL REAL (DB efimera `trust_smoke_017` creada con la
línea Alembic CANÓNICA — incluye la migración 0010_roles_access, así que el
permiso data-driven `roles.assign` es dato real del catálogo, no un mock),
RLS activa, sesiones en PostgreSQL, CSRF del control plane y audit log
inmutable con verificación de cadena.

Único doble declarado (mismo patrón que scripts/smoke_devices.py, aceptado
en spec 007): un IdP de test que sirve JWKS real por HTTP y firma JWT RS256
con amr=["mfa"] — el control plane verifica la firma DE VERDAD (PyJWKClient).

DESVIACIÓN DOCUMENTADA respecto del plan §5 original (constitución II,
razón explícita): el plan pedía sesión contra el API compartido :8000 con
el tenant real `final-e2e` y un issuer arbitrario. Eso es imposible sin
mutar fixtures compartidos: `verify_id_token` pinea el issuer a
TRUST_IDP_BASE_URLS (app/auth/jwks.py) y la fila del tenant pinea
oidc_issuer_url al Keycloak real :8080 — usar el tenant final-e2e exigiría
resetear la contraseña y re-enrolar TOTP del admin/worker compartidos en el
Keycloak real (fixtures de otros lanes). El patrón smoke_devices (servidor
real dedicado + DB real efimera) mantiene TODO real salvo el IdP declarado.

Camino verificado (AC-RA-001..005, D-17-01..05):
 1. GET /api/users → directorio real.
 2. GET /api/me → el org_admin tiene roles.assign (data-driven, 0010 real).
 3. POST /api/users/{carol}/roles (dept_admin @ IT) → 201, reflejo en
    directorio, audit role.assigned.
 4. DELETE del rol con scope → 204, audit role.revoked.
 5. Escalada viva: carol (user plain, sesión propia) POST/DELETE roles → 403.
 6. Auto-asignación del admin sobre sí mismo → 422 (D-17-02).
 7. Scope múltiple: DELETE ambiguo sin department_id → 422.
 8. PUT status=disabled → sesión de carol revocada; status=active → reactivado.
 9. GET /api/departments → departamentos del tenant.
10. audit_verify_chain → ok.

Uso: .venv/bin/python scripts/smoke_roles_access.py
"""
import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

PLATFORM = "/root/trust-agent/platform"
VENV_PYTHON = f"{PLATFORM}/.venv/bin/python"
SMOKE_DB = "trust_smoke_017"
PORT = 8910
ADMIN = ("host=127.0.0.1 port=55432 user=postgres password=trustlocal "
         "dbname=postgres")

TENANT = "70000000-0000-0000-0000-000000000017"
DEPT_IT = "71000000-0000-0000-0000-000000000001"
DEPT_FIN = "71000000-0000-0000-0000-000000000002"
ALICE = "72000000-0000-0000-0000-000000000001"   # org_admin
CAROL = "72000000-0000-0000-0000-000000000002"   # plain user

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def req(method, path, body=None, headers=None, want_headers=False):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
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
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    import psycopg
    from tests.conftest import _IdP

    # 1. DB efimera con la línea Alembic canónica (0010 incluido).
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

    dsn = ADMIN.replace("dbname=postgres", f"dbname={SMOKE_DB}")

    # El permiso roles.assign es dato REAL del catálogo migrado.
    with psycopg.connect(dsn, autocommit=True) as c:
        rows = c.execute(
            "SELECT role_key FROM catalog.role_permissions "
            "WHERE permission_key = 'roles.assign'").fetchall()
        check("catálogo real: roles.assign solo para org_admin (0010)",
              [r[0] for r in rows] == ["org_admin"], str(rows))

    # 2. Seed: tenant + 2 deptos + alice (org_admin) + carol (user).
    idp = _IdP()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_realm, "
            "oidc_issuer_url) VALUES (%s,'Roles Smoke','roles-smoke','active',"
            "'roles-smoke', %s)", (TENANT, idp.issuer("roles-smoke")))
        c.execute(
            "INSERT INTO departments (id, tenant_id, name) VALUES "
            f"(%s, '{TENANT}', 'IT'), (%s, '{TENANT}', 'Finance')",
            (DEPT_IT, DEPT_FIN))
        c.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            "display_name, status, mfa_enrolled, oidc_subject) VALUES "
            "(%s,%s,%s,'alice@roles-smoke.test','Alice','active',true,%s), "
            "(%s,%s,%s,'carol@roles-smoke.test','Carol','active',true,%s)",
            (ALICE, TENANT, DEPT_IT, "sub-alice-orgadmin",
             CAROL, TENANT, DEPT_IT, "sub-carol-user"))
        c.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key) VALUES "
            f"(gen_random_uuid(), '{TENANT}', '{ALICE}', 'org_admin'), "
            f"(gen_random_uuid(), '{TENANT}', '{CAROL}', 'user')")

    # 3. Boot del servidor REAL (fail-closed env: device secret, vault KEK,
    #    policy JWS key spec 019).
    policy_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
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
        TRUST_POLICY_JWS_PRIVATE_KEY_B64=base64.b64encode(
            policy_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption())).decode(),
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

        def jwt_for(sub):
            return idp.make_jwt(sub=sub, tenant_id=TENANT,
                                issuer=idp.issuer("roles-smoke"),
                                amr=["mfa"])

        # Sesiones reales en PostgreSQL (cookie+CSRF admin / bearer desktop).
        st, alice, ahdr = req("POST", "/api/auth/session",
                              {"id_token": jwt_for("sub-alice-orgadmin"),
                               "kind": "admin"}, want_headers=True)
        check("POST /api/auth/session (admin org_admin) → 201", st == 201,
              f"HTTP {st} {str(alice)[:160]}")
        alice_h = {"Cookie": ahdr.get("Set-Cookie").split(";")[0],
                   "X-CSRF-Token": alice["csrf_token"]}
        st, carol = req("POST", "/api/auth/session",
                        {"id_token": jwt_for("sub-carol-user"),
                         "kind": "desktop"})
        check("POST /api/auth/session (carol, user plain) → 201", st == 201,
              f"HTTP {st} {str(carol)[:160]}")
        carol_h = {"Authorization": f"Bearer {carol['session_token']}"}

        # 1. Directorio real.
        st, body = req("GET", "/api/users", headers=alice_h)
        users = body.get("users") if st == 200 else None
        check("GET /api/users → 200 directorio real", st == 200 and users,
              f"HTTP {st} {str(body)[:160]}")
        in_dir = next((u for u in users or [] if u["user_id"] == CAROL), None)
        check("carol presente con su rol user (sin scope)",
              in_dir is not None
              and in_dir["roles"] == [{"role_key": "user",
                                       "department_id": None}],
              str(in_dir)[:200] if in_dir else "missing")

        # 2. Permiso data-driven (migración 0010 real).
        st, body = req("GET", "/api/me", headers=alice_h)
        check("GET /api/me → admin con roles.assign (data-driven)",
              st == 200 and "roles.assign" in body.get("permissions", []),
              f"HTTP {st} {str(body)[:200]}")
        st, body = req("GET", "/api/me", headers=carol_h)
        check("carol SIN roles.assign (sin camino de escalada)",
              st == 200
              and "roles.assign" not in body.get("permissions", []),
              f"HTTP {st} {str(body)[:200]}")

        # 3. Asignación dept_admin con scope (AC-RA-001).
        st, body = req("POST", f"/api/users/{CAROL}/roles",
                       {"role_key": "dept_admin",
                        "department_id": DEPT_IT}, headers=alice_h)
        check("POST /api/users/{carol}/roles (dept_admin @ IT) → 201",
              st == 201, f"HTTP {st} {str(body)[:200]}")
        check("la respuesta trae el rol con scope",
              st == 201 and {"role_key": "dept_admin",
                             "department_id": DEPT_IT}
              in body.get("roles", []), str(body)[:200])
        st, body = req("GET", "/api/users", headers=alice_h)
        in_dir = next((u for u in body.get("users", [])
                       if u["user_id"] == CAROL), {})
        check("el directorio refleja el rol asignado",
              {"role_key": "dept_admin", "department_id": DEPT_IT}
              in in_dir.get("roles", []), str(in_dir)[:200])

        # 4. Revocación con scope (AC-RA-002).
        st, body = req("DELETE",
                       f"/api/users/{CAROL}/roles/dept_admin"
                       f"?department_id={DEPT_IT}", headers=alice_h)
        check("DELETE /api/users/{carol}/roles/dept_admin → 204", st == 204,
              f"HTTP {st} {str(body)[:200]}")

        # 5. Escalada viva con la sesión del usuario plain → 403.
        st, body = req("POST", f"/api/users/{ALICE}/roles",
                       {"role_key": "org_admin"}, headers=carol_h)
        err = (body or {}).get("error", {})
        check("carol (user plain) intentando asignar roles → 403",
              st == 403 and err.get("reason") == "permission_denied",
              f"HTTP {st} {str(body)[:200]}")
        st, body = req("DELETE", f"/api/users/{ALICE}/roles/org_admin",
                       headers=carol_h)
        check("carol intentando revocar roles → 403", st == 403,
              f"HTTP {st} {str(body)[:200]}")

        # 6. Auto-asignación → 422 (D-17-02).
        st, body = req("POST", f"/api/users/{ALICE}/roles",
                       {"role_key": "dept_admin",
                        "department_id": DEPT_IT}, headers=alice_h)
        check("auto-asignación del admin sobre sí mismo → 422", st == 422,
              f"HTTP {st} {str(body)[:200]}")

        # 7. Scope múltiple: ambiguo sin department_id → 422 (D-17-04).
        for dept in (DEPT_IT, DEPT_FIN):
            st, body = req("POST", f"/api/users/{CAROL}/roles",
                           {"role_key": "dept_admin", "department_id": dept},
                           headers=alice_h)
            check(f"asignación dept_admin @ {'IT' if dept == DEPT_IT else 'Finance'} → 201",
                  st == 201, f"HTTP {st} {str(body)[:200]}")
        st, body = req("DELETE", f"/api/users/{CAROL}/roles/dept_admin",
                       headers=alice_h)
        check("DELETE ambiguo (2 scopes, sin department_id) → 422",
              st == 422, f"HTTP {st} {str(body)[:200]}")
        for dept in (DEPT_IT, DEPT_FIN):
            st, body = req("DELETE",
                           f"/api/users/{CAROL}/roles/dept_admin"
                           f"?department_id={dept}", headers=alice_h)
            check(f"DELETE dept_admin @ {'IT' if dept == DEPT_IT else 'Finance'} → 204",
                  st == 204, f"HTTP {st} {str(body)[:200]}")

        # 8. Status vía PUT (D-17-05): disable revoca la sesión de carol.
        st, body = req("PUT", f"/api/users/{CAROL}",
                       {"status": "disabled"}, headers=alice_h)
        check("PUT /api/users/{carol} status=disabled → 200",
              st == 200 and body.get("status") == "disabled",
              f"HTTP {st} {str(body)[:200]}")
        st, body = req("GET", "/api/me", headers=carol_h)
        check("la sesión de carol quedó revocada al desactivarlo",
              st == 401, f"HTTP {st} {str(body)[:160]}")
        st, body = req("PUT", f"/api/users/{CAROL}",
                       {"status": "active"}, headers=alice_h)
        check("PUT status=active → 200 (reactivado)",
              st == 200 and body.get("status") == "active",
              f"HTTP {st} {str(body)[:200]}")

        # 9. Departamentos del tenant (REQ-RA-006).
        st, body = req("GET", "/api/departments", headers=alice_h)
        ids = {d["department_id"] for d in body.get("departments", [])} \
            if st == 200 else set()
        check("GET /api/departments → 200 departamentos del tenant",
              st == 200 and ids == {DEPT_IT, DEPT_FIN},
              f"HTTP {st} {str(body)[:200]}")

        # 10. Audit real: eventos + cadena inmutable verificada.
        with psycopg.connect(dsn, autocommit=True) as c:
            actions = [r[0] for r in c.execute(
                "SELECT action FROM audit_log WHERE target_id = %s "
                "ORDER BY seq", (CAROL,)).fetchall()]
            check("audit: role.assigned emitido", "role.assigned" in actions,
                  str(actions))
            check("audit: role.revoked emitido", "role.revoked" in actions,
                  str(actions))
            check("audit: user.disabled emitido", "user.disabled" in actions,
                  str(actions))
            ver = c.execute("SELECT audit_verify_chain(%s)",
                            (TENANT,)).fetchone()
            status = ver[0][0] if isinstance(ver[0], tuple) else ver[0]
            check("audit chain → ok (inmutabilidad verificada)",
                  status == "ok", str(status))

        # Sin residuo: carol activo con su rol original.
        st, body = req("GET", "/api/users", headers=alice_h)
        in_dir = next((u for u in body.get("users", [])
                       if u["user_id"] == CAROL), {})
        check("sin residuo: carol activo con su rol original",
              in_dir.get("status") == "active"
              and in_dir.get("roles") == [{"role_key": "user",
                                           "department_id": None}],
              str(in_dir)[:200])

        print()
        if failures:
            print(f"SMOKE FAIL — {len(failures)} fallo(s): {failures}")
            return 1
        print("SMOKE PASS — roles & access verificado contra servidor real: "
              "asignación + revocación + scopes + 403 escalada + 422 "
              "auto-modificación + status + departamentos + audit chain, "
              "sin residuo")
        return 0
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
    sys.exit(main())

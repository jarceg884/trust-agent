#!/usr/bin/env python3
"""E2E spec 036 — login real PKCE+TOTP + verificaciones API del bloque
`presentation` + export de cookies para el navegador (Chromium vía CDP).

Variante de console/scripts/smoke_console.py (mismo baile: one-time password
→ UPDATE_PASSWORD → CONFIGURE_TOTP real RFC 6238 → callback mint). NADA
simulado (constitution I / REQ-036-009).

Uso:
  .venv/bin/python scripts/e2e_policies_ux.py <one_time_password> [cookies.json]
"""
import base64
import hashlib
import hmac
import http.client
import json
import re
import struct
import sys
import time
import urllib.parse

CONSOLE_HOST, CONSOLE_PORT = "127.0.0.1", 3100
KC_HOST, KC_PORT = "127.0.0.1", 8080
API_HOST, API_PORT = "127.0.0.1", 8000
SLUG = "policies-ux"
ADMIN_EMAIL = f"admin@{SLUG}.test"
WORKER_EMAIL = f"worker@{SLUG}.test"
NEW_PASSWORD = "PoliciesUxAdmin2026!"
PROVIDER_TOKEN = "test-provider-token"
KC_ADMIN_USER = "admin"
KC_ADMIN_PASSWORD = "KeycloakDemo2026"
DB_DSN = ("host=127.0.0.1 port=55432 user=postgres "
          "password=trustlocal dbname=trust_agent")

COOKIES: dict[str, str] = {}


def db(sql, params=None, fetch=True):
    import psycopg
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if fetch else None


def fetch(host, port, path, *, method="GET", data=None, headers=None,
          follow=True, max_hops=8):
    h = dict(headers or {})
    if COOKIES:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPConnection(host, port, timeout=20)
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    hdrs = {k.lower(): v for k, v in resp.getheaders()}
    for raw in resp.msg.get_all("Set-Cookie") or []:
        name, _, rest = raw.partition("=")
        value = rest.split(";")[0]
        if value:
            COOKIES[name.strip()] = value
        else:
            COOKIES.pop(name.strip(), None)
    status = resp.status
    conn.close()

    if follow and status in (301, 302, 303, 307) and max_hops > 0:
        loc = hdrs.get("location", "")
        if loc:
            if loc.startswith("http"):
                u = urllib.parse.urlparse(loc)
                return fetch(u.hostname, u.port or 80,
                             u.path + ("?" + u.query if u.query else ""),
                             method="GET" if status in (301, 302, 303) else method,
                             data=None, headers=headers, follow=True,
                             max_hops=max_hops - 1)
            return fetch(host, port, loc,
                         method="GET" if status in (301, 302, 303) else method,
                         data=None, headers=headers, follow=True,
                         max_hops=max_hops - 1)
    return status, body, hdrs, path


def kc_admin_token() -> str:
    st, body, _, _ = fetch(KC_HOST, KC_PORT, "/realms/master/protocol/openid-connect/token",
                           method="POST",
                           data=urllib.parse.urlencode({
                               "grant_type": "password", "client_id": "admin-cli",
                               "username": KC_ADMIN_USER, "password": KC_ADMIN_PASSWORD,
                           }).encode())
    if st != 200:
        raise RuntimeError(f"admin-cli token failed: HTTP {st}")
    return json.loads(body)["access_token"]


def reset_user(ota_password: str, email: str = ADMIN_EMAIL,
               required_actions: list[str] | None = None) -> None:
    token = kc_admin_token()
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    st, body, _, _ = fetch(KC_HOST, KC_PORT,
                        f"/admin/realms/{SLUG}/users?username={urllib.parse.quote(email)}&exact=true",
                        headers=auth)
    user_id = json.loads(body)[0]["id"]
    st, body, _, _ = fetch(KC_HOST, KC_PORT, f"/admin/realms/{SLUG}/users/{user_id}/credentials",
                        headers=auth)
    for cred in json.loads(body):
        if cred["type"] == "otp":
            fetch(KC_HOST, KC_PORT,
                  f"/admin/realms/{SLUG}/users/{user_id}/credentials/{cred['id']}",
                  method="DELETE", headers=auth)
    fetch(KC_HOST, KC_PORT, f"/admin/realms/{SLUG}/users/{user_id}", method="PUT",
          data=json.dumps({"requiredActions": required_actions or ["CONFIGURE_TOTP", "UPDATE_PASSWORD"]}).encode(),
          headers=auth)
    st, _, _, _ = fetch(KC_HOST, KC_PORT,
                     f"/admin/realms/{SLUG}/users/{user_id}/reset-password", method="PUT",
                     data=json.dumps({"type": "password", "value": ota_password,
                                      "temporary": True}).encode(),
                     headers=auth)
    if st not in (200, 204):
        raise RuntimeError(f"reset-password failed: HTTP {st}")
    print(f"[reset] {email} en estado pristine (OTP borrado, OTA fresco)")


def form_action(html: str) -> str:
    return re.search(r'action="([^"]+)"', html).group(1).replace("&amp;", "&")


def form_fields_of(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]*>", html):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n and v:
            out[n.group(1)] = v.group(1)
    return out


def totp_secret_of(html: str) -> str:
    tag = re.search(r'<input[^>]*name="totpSecret"[^>]*>', html)
    if tag:
        m = re.search(r'value="([A-Za-z0-9]{16,})"', tag.group(0))
        if m:
            return m.group(1)
    m = re.search(r'value="([A-Za-z0-9]{16,})"', html)
    if m:
        return m.group(1)
    m = re.search(r'>([A-Za-z0-9]{16,})<', html)
    if m:
        return m.group(1)
    raise ValueError("TOTP secret not found")


def totp(secret: str, t: float | None = None, counter: int | None = None) -> str:
    key = secret.encode("utf-8")
    if counter is None:
        counter = int((t if t is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6
    return str(code).zfill(6)


def kc_path(url: str) -> str:
    return url.split(f"{KC_HOST}:{KC_PORT}")[-1]


def login_dance(password: str, secret_state: dict, email: str = ADMIN_EMAIL,
                new_password: str | None = None) -> bool:
    new_password = new_password or NEW_PASSWORD
    for k in list(COOKIES):
        if not k.startswith("ta_"):
            COOKIES.pop(k)
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, f"/auth/login?org={SLUG}")
    if not (st == 200 and 'action="' in body):
        print(f"    [dance] login page FAIL HTTP {st}")
        return False
    action = form_action(body)
    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(action), method="POST",
                              data=urllib.parse.urlencode({
                                  "username": email, "password": password,
                                  "credentialId": ""}).encode(),
                              follow=False)
    loc = hdrs.get("location", "")

    def follow_hop(status, location, body_in):
        if not location or "code=" in location or status not in (301, 302, 303, 307):
            return status, body_in, location, hdrs
        if location.startswith(f"http://{KC_HOST}:{KC_PORT}"):
            s2, b2, h2, _ = fetch(KC_HOST, KC_PORT, kc_path(location), follow=False)
        else:
            s2, b2, h2, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                                  location if location.startswith("/") else "/" + location,
                                  follow=False)
        return s2, b2, h2.get("location", ""), h2

    st, body, loc, hdrs = follow_hop(st, loc, body)
    for _step in range(6):
        if "code=" in loc:
            break
        if not (st == 200 and 'action="' in body):
            break
        act = form_action(body)
        if "password-new" in body:
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                      data=urllib.parse.urlencode({
                                          "username": email, "password": password,
                                          "password-new": new_password,
                                          "password-confirm": new_password,
                                          "credentialId": ""}).encode(),
                                      follow=False)
        elif 'name="totpSecret"' in body:
            secret = totp_secret_of(body)
            print(f"    [dance] enrolando TOTP secret={secret}")
            secret_state["secret"] = secret
            secret_state["used_counter"] = int(time.time()) // 30
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                      data=urllib.parse.urlencode({
                                          "totp": totp(secret), "totpSecret": secret,
                                          "mode": "totp", "userLabel": "e2e-policies-ux",
                                          "credentialId": ""}).encode(),
                                      follow=False)
        elif 'name="otp"' in body or 'name="totp"' in body:
            code_field = "otp" if 'name="otp"' in body else "totp"
            counter = int(time.time()) // 30
            if counter == secret_state.get("used_counter"):
                counter += 1
            fields = form_fields_of(body)
            fields[code_field] = totp(secret_state.get("secret", ""), counter=counter)
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                      data=urllib.parse.urlencode(fields).encode(),
                                      follow=False)
        else:
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                      data=b"", follow=False)
        loc = hdrs.get("location", "")
        st, body, loc, hdrs = follow_hop(st, loc, body)

    hops = 0
    while "code=" not in loc and hops < 6 and loc:
        if loc.startswith(f"http://{KC_HOST}:{KC_PORT}"):
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(loc), follow=False)
        else:
            st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                                      loc if loc.startswith("/") else "/" + loc,
                                      follow=False)
        nxt = hdrs.get("location", "")
        if not nxt and 'action="' in body:
            act = form_action(body)
            if 'name="totp"' in body:
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                          data=urllib.parse.urlencode(
                                              {"totp": totp(secret_state.get("secret", "")),
                                               "credentialId": ""}).encode(),
                                          follow=False)
                nxt = hdrs.get("location", "")
            else:
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                          data=b"", follow=False)
                nxt = hdrs.get("location", "")
        loc = nxt
        hops += 1
    if "code=" not in loc:
        print("    [dance] sin code tras los hops")
        return False
    for _attempt in range(3):
        st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                                  loc if loc.startswith("/") else loc.split(f"{CONSOLE_HOST}:{CONSOLE_PORT}")[-1],
                                  follow=False)
        final = hdrs.get("location", "")
        if "/overview" in final:
            print("    [dance] callback: sesión mintada (MFA verificada)")
            return True
        if "/protocol/openid-connect/auth" in final:
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(final), follow=False)
            loc2 = hdrs.get("location", "")
            hops = 0
            while "code=" not in (loc2 or "") and hops < 4:
                if loc2 and st in (301, 302, 303, 307):
                    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(loc2), follow=False)
                    loc2 = hdrs.get("location", "")
                elif 'name="otp"' in body or 'name="totp"' in body:
                    act = form_action(body)
                    code_field = "otp" if 'name="otp"' in body else "totp"
                    fields = form_fields_of(body)
                    counter = int(time.time()) // 30
                    if counter == secret_state.get("used_counter"):
                        counter += 1
                    fields[code_field] = totp(secret_state.get("secret", ""), counter=counter)
                    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                              data=urllib.parse.urlencode(fields).encode(),
                                              follow=False)
                    loc2 = hdrs.get("location", "")
                    if "code=" in loc2:
                        secret_state["used_counter"] = counter
                else:
                    break
                hops += 1
            if "code=" not in (loc2 or ""):
                return False
            loc = loc2
            continue
        return False
    return False


def proxy(path, method="GET", payload=None):
    """Llamada al control plane a través del proxy BFF del console."""
    headers = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
        headers["X-CSRF-Token"] = COOKIES.get("ta_console_csrf", "")
    elif method != "GET":
        headers["X-CSRF-Token"] = COOKIES.get("ta_console_csrf", "")
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, f"/api/proxy{path}",
                           method=method, data=data, headers=headers, follow=False)
    try:
        return st, json.loads(body) if body else None
    except json.JSONDecodeError:
        return st, body


def provision_tenant() -> tuple[str, str | None]:
    """Provisioning idempotente por slug (provider API real).

    → (tenant_id, one_time_password | None). OTA None = tenant ya existía
    (se usa reset_user para dejar el admin pristine).
    """
    st, body, _, _ = fetch(API_HOST, API_PORT, "/api/admin/tenants",
                           method="POST",
                           data=json.dumps({
                               "organization_name": "Policies UX Corp",
                               "admin_email": ADMIN_EMAIL,
                               "initial_budget_usd": 100.0,
                               "model_plan": "standard",
                               "slug": SLUG,
                           }).encode(),
                           headers={"Content-Type": "application/json",
                                    "Authorization": f"Bearer {PROVIDER_TOKEN}"},
                           follow=False)
    body = json.loads(body) if isinstance(body, str) else body
    if st == 201:
        ota = body["one_time_activation"]["one_time_password"]
        print(f"[provision] tenant {SLUG} creado (OTA one-time)")
        return body["tenant"]["id"], ota
    # Ya existe (corrida previa): idempotente por slug.
    rows = db("SELECT id FROM tenants WHERE slug = %s", (SLUG,))
    if not rows:
        raise RuntimeError(f"provisioning falló: HTTP {st} {str(body)[:200]}")
    print(f"[provision] tenant {SLUG} ya existía (HTTP {st}) — reutilizando")
    return str(rows[0][0]), None


def ensure_worker(tenant_id: str) -> str:
    """Usuario regular del depto IT (KC real + fila users) — idempotente."""
    rows = db("SELECT id FROM users WHERE tenant_id = %s AND email = %s",
              (tenant_id, WORKER_EMAIL))
    if rows:
        return str(rows[0][0])
    dept_rows = db(
        "SELECT id FROM departments WHERE tenant_id = %s "
        "AND profile_template = 'it_operations'", (tenant_id,))
    dept_id = str(dept_rows[0][0])
    token = kc_admin_token()
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    fetch(KC_HOST, KC_PORT, f"/admin/realms/{SLUG}/users", method="POST",
          data=json.dumps({
              "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
              "firstName": "Policies", "lastName": "Worker",
              "attributes": {"tenant_id": [tenant_id]},
          }).encode(), headers=auth)
    st, body, _, _ = fetch(KC_HOST, KC_PORT,
                           f"/admin/realms/{SLUG}/users?username={urllib.parse.quote(WORKER_EMAIL)}&exact=true",
                           headers=auth)
    worker_uuid = json.loads(body)[0]["id"]
    db("INSERT INTO users (tenant_id, department_id, email, display_name, status, "
       "mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', true, %s)",
       (tenant_id, dept_id, WORKER_EMAIL, "Policies Worker", worker_uuid),
       fetch=False)
    db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
       "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s",
       (tenant_id, worker_uuid), fetch=False)
    rows = db("SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))
    print(f"[provision] usuario obrero {WORKER_EMAIL} creado (depto IT)")
    return str(rows[0][0])


def main(ota_password: str, cookies_out: str) -> int:
    failures = []

    def check(name, cond, detail=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    tenant_id, ota = provision_tenant()
    if ota is None:
        # Tenant preexistente: admin pristine para el baile (OTA fresco).
        ota = ota_password
        reset_user(ota)
    secret_state: dict = {}
    check("login #1 real (PKCE + one-time + enrolamiento TOTP)",
          login_dance(ota, secret_state))
    check("cookies de sesión (ta_console_session/refresh/csrf)",
          {"ta_console_session", "ta_console_refresh", "ta_console_csrf"} <= set(COOKIES),
          str(sorted(COOKIES)))
    if failures:
        return 1

    # Escenario AC-036-005: org require_mfa=true ANTES del login definitivo
    # (spec 015 D-MFA-01: el console exige step-up LoA 2 → sesión MFA-verified).
    st, body = proxy("/api/policies/org", method="PUT",
                     payload={"key": "require_mfa", "value": True})
    check("setup org require_mfa=true (PUT real, sesión #1)", st == 200, f"HTTP {st}")

    # El baile de enrolamiento termina con re-auth acr=2 → sesión MFA-verified
    # (verificado en DB). Si no lo estuviera, step-up con el secreto enrolado.
    st, me = proxy("/api/me")
    if not (st == 200 and me.get("mfa_verified") is True):
        for k in list(COOKIES):
            if k.startswith("ta_"):
                COOKIES.pop(k)
        check("step-up MFA (re-dance con el secreto enrolado)",
              login_dance(NEW_PASSWORD, secret_state))
        st, me = proxy("/api/me")
    check("sesión admin MFA-verified (whoami.mfa_verified)",
          st == 200 and me.get("mfa_verified") is True,
          f"HTTP {st} mfa_verified={me.get('mfa_verified') if st == 200 else '?'}")
    if failures:
        return 1

    worker_user_id = ensure_worker(tenant_id)
    dept_rows = db("SELECT id, name FROM departments WHERE tenant_id = %s "
                   "AND profile_template = 'it_operations'", (tenant_id,))
    dept_id, dept_name = str(dept_rows[0][0]), dept_rows[0][1]

    # --- AC-036-001 (vivo): presentation 1:1 + categories + efectivas -------
    st, matrix = proxy("/api/policies/matrix")
    check("GET /api/policies/matrix → 200", st == 200, f"HTTP {st}")
    catalog_keys = {e["key"] for e in matrix["catalog"]}
    pres_keys = set(matrix["presentation"])
    check("presentation cobertura 1:1 con el catálogo (47/47)",
          catalog_keys == pres_keys and len(pres_keys) == 47,
          f"catalog={len(catalog_keys)} presentation={len(pres_keys)} "
          f"missing={sorted(catalog_keys - pres_keys)[:5]} extra={sorted(pres_keys - catalog_keys)[:5]}")
    check("9 categorías legibles", len(matrix["categories"]) == 9)
    check("org_effective presente (combine(org,∅,∅))", "org_effective" in matrix)
    check("departments[].effective presente (combine(org,dept,∅))",
          all("effective" in d for d in matrix["departments"]))

    # --- Estado del tenant para el E2E de UI --------------------------------
    st, users = proxy("/api/users")
    print(f"[info] usuarios del tenant: {[u['email'] for u in users['users']]}")

    state = {
        "cookies": {k: v for k, v in COOKIES.items() if k.startswith("ta_")},
        "tenant_id": tenant_id,
        "tenant_slug": SLUG,
        "admin_email": ADMIN_EMAIL,
        "dept_id": dept_id,
        "dept_name": dept_name,
        "worker_user_id": worker_user_id,
        "worker_email": WORKER_EMAIL,
    }

    # --- Sesión dept_admin (AC-036-002: sin conmutador "Technical view") ----
    dept_admin_email = f"deptadmin@{SLUG}.test"
    dept_admin_pw = "DeptAdminPolicies2026!"
    rows = db("SELECT id FROM users WHERE tenant_id = %s AND email = %s",
              (tenant_id, dept_admin_email))
    if not rows:
        dept_rows = db(
            "SELECT id FROM departments WHERE tenant_id = %s "
            "AND profile_template = 'it_operations'", (tenant_id,))
        dept_id_local = str(dept_rows[0][0])
        token = kc_admin_token()
        auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        fetch(KC_HOST, KC_PORT, f"/admin/realms/{SLUG}/users", method="POST",
              data=json.dumps({
                  "username": dept_admin_email, "enabled": True,
                  "email": dept_admin_email, "firstName": "Dept", "lastName": "Admin",
                  "attributes": {"tenant_id": [tenant_id]},
              }).encode(), headers=auth)
        st, body, _, _ = fetch(KC_HOST, KC_PORT,
                               f"/admin/realms/{SLUG}/users?username={urllib.parse.quote(dept_admin_email)}&exact=true",
                               headers=auth)
        da_uuid = json.loads(body)[0]["id"]
        db("INSERT INTO users (tenant_id, department_id, email, display_name, status, "
           "mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', true, %s)",
           (tenant_id, dept_id_local, dept_admin_email, "Dept Admin", da_uuid),
           fetch=False)
        db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
           "SELECT %s, id, 'dept_admin', department_id FROM users WHERE oidc_subject = %s",
           (tenant_id, da_uuid), fetch=False)
        print(f"[provision] dept_admin {dept_admin_email} creado (depto IT)")
    # Baile propio del dept_admin (contraseña definitiva + enrolamiento TOTP).
    reset_user(dept_admin_pw, dept_admin_email, required_actions=["CONFIGURE_TOTP"])
    for k in list(COOKIES):
        COOKIES.pop(k)
    da_state: dict = {}
    da_ok = login_dance(dept_admin_pw, da_state, email=dept_admin_email,
                        new_password=dept_admin_pw)
    check("login real dept_admin (PKCE+TOTP)", da_ok)
    if da_ok:
        st, me = proxy("/api/me")
        check("dept_admin /api/me → rol dept_admin",
              st == 200 and any(r["role_key"] == "dept_admin" for r in me["roles"]),
              f"HTTP {st}")
        state["dept_admin_cookies"] = {
            k: v for k, v in COOKIES.items() if k.startswith("ta_")}
        state["dept_admin_email"] = dept_admin_email
        # Volver a las cookies del admin para el resto del flujo.
        for k in list(COOKIES):
            COOKIES.pop(k)
        COOKIES.update(state["cookies"])

    with open(cookies_out, "w") as fh:
        json.dump(state, fh, indent=2)
    print(f"[ok] sesión + estado del tenant exportados → {cookies_out}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/tmp/policies_ux_cookies.json"))

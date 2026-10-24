#!/usr/bin/env python3
"""E2E FINAL EN VIVO — validación global de la plataforma (t_bf72a163).

Flujo completo contra infraestructura REAL corriendo:
  control plane :8000 (uvicorn vivo) · Keycloak 26.2 :8080 · PostgreSQL 17
  (contenedor trust-db) · doble HTTP DECLARADO de LiteLLM (D-GOV-15).

  FASE A: proveedor crea tenant → admin login REAL PKCE S256 + TOTP real
          (acr=2, D-MFA-01) → configura org (presupuestos, políticas,
          modelos) → usuario obrero login real + puerta MFA (403 real) →
          enrola dispositivo (202→approve→token, 2º canje 410) → publica
          agente + grants + invoca → usa modelo aprobado vía key virtual.
  (reinicio del API entre fases — AC-SEC-003: sesiones en PostgreSQL)
  FASE B: las sesiones sobreviven el reinicio → ingest de spend (worker
          real) → presupuesto se descuenta (ledger + counters + overview)
          → corte (key bloqueada, 401 gateway, notificación, audit) →
          audit log refleja TODO (API + CLI, cadena íntegra) → revocación
          del dispositivo bloquea acceso (401 inmediato).

DOBLES DECLARADOS (constitution I):
- LiteLLM: doble HTTP tests/litellm_fake.py en 127.0.0.1:39989 con la
  semántica documentada de la Admin API. Contra un proxy real solo
  cambia TRUST_LITELLM_BASE_URL.
- NADA MÁS: IdP 100% real (login PKCE + enrolamiento TOTP real RFC 6238
  por los formularios reales de Keycloak 26 — sin JWKS double).

Uso:
  python scripts/e2e_final_live.py --phase a --state /tmp/e2e_final_state.json
  # (reiniciar el API)
  python scripts/e2e_final_live.py --phase b --state /tmp/e2e_final_state.json
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import time
import urllib.parse

import psycopg

API_HOST, API_PORT = "127.0.0.1", 8000
KC_HOST, KC_PORT = "127.0.0.1", 8080
LITELLM_PORT = 39989
CLIENT_ID = "trust-agent-web"
REDIRECT_URI = "http://127.0.0.1:9090/auth/callback"
PROVIDER_TOKEN = "test-provider-token"
KC_ADMIN_PASSWORD = "KeycloakDemo2026"
DB_DSN = "host=127.0.0.1 port=55432 user=postgres password=trustlocal dbname=trust_agent"

SLUG = "final-e2e"
ADMIN_EMAIL = "admin@final-e2e.test"
ADMIN_NEW_PASSWORD = "FinalAdmin2026!"
WORKER_EMAIL = "worker@final-e2e.test"
WORKER_PASSWORD = "FinalWorker2026!"
QWEN = "qwen2.5-72b-instruct"

FAILURES = []
PASSES = 0
COOKIES: dict[str, str] = {}


def check(name, cond, detail=""):
    global PASSES
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)
    return ok


def api(method, path, payload=None, bearer=None, headers=None):
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=20)
    body = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw) if raw else {}, raw
    except json.JSONDecodeError:
        return resp.status, {}, raw


def db(sql, params=None, fetch=True):
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if fetch else None


# ---------------------------------------------------------------------------
# Keycloak: Admin REST + baile PKCE real (formularios reales de KC26)
# ---------------------------------------------------------------------------
def kc_fetch(path, *, method="GET", data=None, headers=None, follow=False):
    h = dict(headers or {})
    if COOKIES:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=20)
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
    if follow and status in (301, 302, 303, 307):
        loc = hdrs.get("location", "")
        if loc:
            p = loc.split(f"{KC_HOST}:{KC_PORT}")[-1]
            return kc_fetch(p if p.startswith("/") else "/" + p, method="GET",
                            headers=headers)
    return status, body, hdrs


def kc_admin_token():
    st, body, _ = kc_fetch(
        "/realms/master/protocol/openid-connect/token", method="POST",
        data=urllib.parse.urlencode({
            "grant_type": "password", "client_id": "admin-cli",
            "username": "admin", "password": KC_ADMIN_PASSWORD}).encode())
    assert st == 200, f"admin-cli: HTTP {st}"
    return json.loads(body)["access_token"]


def kc_json(method, path, payload=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    st, body, _ = kc_fetch(path, method=method,
                           data=json.dumps(payload).encode() if payload is not None else None,
                           headers=h)
    return st, body


def kc_user_uuid(email):
    token = kc_admin_token()
    st, raw = kc_json("GET", f"/admin/realms/{SLUG}/users?username={urllib.parse.quote(email)}&exact=true", token=token)
    assert st == 200, raw
    return json.loads(raw)[0]["id"]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def totp(secret: str, counter: int | None = None) -> str:
    """RFC 6238 — clave HMAC = bytes UTF-8 raw del secreto (verificado vs KC26)."""
    key = secret.encode("utf-8")
    if counter is None:
        counter = int(time.time()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6
    return str(code).zfill(6)


def form_action(html):
    return re.search(r'action="([^"]+)"', html).group(1).replace("&amp;", "&")


def _hop(st, body, loc, hdrs):
    """Sigue redirects internos de KC26 (sin code=) hasta el siguiente form.

    KC26 responde a cada POST del baile con 302 a la página de la siguiente
    required action; sin seguir ese hop el body llega vacío y el baile muere
    (bug del intento 1). Solo se siguen hops del propio KC — el redirect
    final con code= apunta a :9090 y NO se sigue (se parsea)."""
    for _ in range(4):
        if not loc or "code=" in loc or st not in (301, 302, 303, 307):
            return st, body, loc, hdrs
        path = loc.split(f"{KC_HOST}:{KC_PORT}")[-1]
        if not path.startswith("/"):
            return st, body, loc, hdrs
        st, body, hdrs = kc_fetch(path)
        loc = hdrs.get("location", "")
    return st, body, loc, hdrs


def form_fields(html):
    out = {}
    for tag in re.findall(r"<input[^>]*>", html):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n and v:
            out[n.group(1)] = v.group(1)
    return out


def totp_secret_of(html):
    tag = re.search(r'<input[^>]*name="totpSecret"[^>]*>', html)
    if tag:
        m = re.search(r'value="([A-Za-z0-9]{16,})"', tag.group(0))
        if m:
            return m.group(1)
    m = re.search(r'value="([A-Za-z0-9]{16,})"', html) or re.search(r'>([A-Za-z0-9]{16,})<', html)
    if m:
        return m.group(1)
    raise ValueError("TOTP secret not found in KC page")


def pkce_login(username, password, *, new_password=None, totp_state=None,
               refuse_totp=False):
    """Baile REAL authorization-code + PKCE S256 contra el realm.

    Maneja required actions encadenadas de KC26 (UPDATE_PASSWORD,
    CONFIGURE_TOTP con enrolamiento real, segundo factor OTP).
    Con refuse_totp=True NO enrola TOTP: si KC exige CONFIGURE_TOTP
    (comportamiento D-MFA-01: usuario sin credencial TOTP → enrolamiento
    inline forzado), devuelve ("MFA_REQUIRED", None) — evidencia de que el
    IdP es fail-closed y no entrega code sin MFA.
    -> (id_token | "MFA_REQUIRED" | None, acr | None)
    """
    for k in list(COOKIES):
        COOKIES.pop(k)
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI, "state": "st-final-e2e",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    st, body, hdrs = kc_fetch(
        f"/realms/{SLUG}/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}")
    assert st == 200 and 'action="' in body, f"authorize HTTP {st}"
    action = form_action(body)
    st, body, hdrs = kc_fetch(action.split(f"{KC_HOST}:{KC_PORT}")[-1], method="POST",
                              data=urllib.parse.urlencode({
                                  "username": username, "password": password,
                                  "credentialId": ""}).encode())
    loc = hdrs.get("location", "")
    st, body, loc, hdrs = _hop(st, body, loc, hdrs)
    used_counter = None
    for _ in range(8):
        if "code=" in loc:
            break
        if not (st == 200 and 'action="' in body):
            break
        act = form_action(body)
        if "password-new" in body and new_password:
            st, body, hdrs = kc_fetch(act.split(f"{KC_HOST}:{KC_PORT}")[-1], method="POST",
                                      data=urllib.parse.urlencode({
                                          "username": username, "password": password,
                                          "password-new": new_password,
                                          "password-confirm": new_password,
                                          "credentialId": ""}).encode())
        elif 'name="totpSecret"' in body:
            if refuse_totp:
                # D-MFA-01: sin credencial TOTP el IdP deriva inline a
                # CONFIGURE_TOTP y NO hay code — MFA obligatoria en el IdP.
                return "MFA_REQUIRED", None
            secret = totp_secret_of(body)
            if totp_state is not None:
                totp_state["secret"] = secret
            used_counter = int(time.time()) // 30
            code = totp(secret, counter=used_counter)
            print(f"    [dance] enrolamiento TOTP real: secret={secret} code={code}", flush=True)
            st, body, hdrs = kc_fetch(act.split(f"{KC_HOST}:{KC_PORT}")[-1], method="POST",
                                      data=urllib.parse.urlencode({
                                          "totp": code, "totpSecret": secret,
                                          "mode": "totp", "userLabel": "final-e2e",
                                          "credentialId": ""}).encode())
        elif 'name="otp"' in body or 'name="totp"' in body:
            secret = (totp_state or {}).get("secret", "")
            counter = int(time.time()) // 30
            if counter == used_counter:
                counter += 1  # el código de enrolamiento no es reutilizable
            fields = form_fields(body)
            code_field = "otp" if 'name="otp"' in body else "totp"
            fields[code_field] = totp(secret, counter=counter)
            print(f"    [dance] 2º factor OTP real: {fields[code_field]}", flush=True)
            st, body, hdrs = kc_fetch(act.split(f"{KC_HOST}:{KC_PORT}")[-1], method="POST",
                                      data=urllib.parse.urlencode(fields).encode())
        else:
            st, body, hdrs = kc_fetch(act.split(f"{KC_HOST}:{KC_PORT}")[-1], method="POST",
                                      data=b"")
        loc = hdrs.get("location", "")
        st, body, loc, hdrs = _hop(st, body, loc, hdrs)
    if "code=" not in loc:
        return None, None
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    st, raw, _ = kc_fetch(f"/realms/{SLUG}/protocol/openid-connect/token", method="POST",
                          data=urllib.parse.urlencode({
                              "grant_type": "authorization_code",
                              "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
                              "code": code, "code_verifier": verifier}).encode())
    assert st == 200, raw
    tokens = json.loads(raw)
    claims = json.loads(base64.urlsafe_b64decode(
        tokens["id_token"].split(".")[1] + "==").decode())
    return tokens["id_token"], claims.get("acr")


def mint(id_token, kind="desktop"):
    st, body, raw = api("POST", "/api/auth/session",
                        {"id_token": id_token, "kind": kind})
    assert st == 201, f"session mint HTTP {st}: {raw[:200]}"
    return body


def cp_env() -> dict:
    """Env exacta del control plane vivo (para el subprocess del spend worker)."""
    out = subprocess.run(["pgrep", "-f", "uvicorn app.main:app --host 127.0.0.1 --port 8000"],
                         capture_output=True, text=True)
    pid = out.stdout.split()[0]
    env = {}
    for item in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
        if item:
            k, _, v = item.partition(b"=")
            env[k.decode()] = v.decode()
    return env


def run_spend_worker():
    env = cp_env()
    proc = subprocess.run(
        ["/root/trust-agent/platform/.venv/bin/python", "-m",
         "app.governance.spend_worker", "--once"],
        capture_output=True, text=True, timeout=120,
        cwd="/root/trust-agent/platform", env=env)
    print(f"    [spend-worker] rc={proc.returncode} {proc.stdout.strip()[:300]} "
          f"{proc.stderr.strip()[:200]}", flush=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def litellm_chat(key, model):
    conn = http.client.HTTPConnection("127.0.0.1", LITELLM_PORT, timeout=15)
    conn.request("POST", "/chat/completions",
                 body=json.dumps({"model": model,
                                  "messages": [{"role": "user", "content": "ping"}]}).encode(),
                 headers={"Content-Type": "application/json",
                          "Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body[:200]


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def load_state(path):
    with open(path) as f:
        return json.load(f)


# ===========================================================================
# FASE A
# ===========================================================================
def phase_a(state_path):
    S = {}
    print("=== FASE A: provision → admin → org → worker → dispositivo → agente → modelo ===", flush=True)

    # --- S1: proveedor crea tenant -----------------------------------------
    st, body, raw = api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {PROVIDER_TOKEN}"}, payload={
        "organization_name": "Final E2E Corp", "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0, "model_plan": "standard", "slug": SLUG})
    check("S1 proveedor: POST /api/admin/tenants → 201 created", st == 201 and body.get("created") is True,
          f"HTTP {st} {raw[:200]}")
    tenant = body["tenant"]
    S["tenant_id"] = tenant["id"]
    S["ota"] = body["one_time_activation"]["one_time_password"]
    check("S1 tenant activo con issuer del realm real",
          tenant["status"] == "active" and tenant["oidc_issuer_url"].endswith(f"/realms/{SLUG}"),
          tenant["oidc_issuer_url"])
    check("S1 healthcheck del provisioning ok", body["healthcheck"]["status"] == "ok")
    check("S1 one-time password entregado UNA vez (≥16 chars)",
          S["ota"] and len(S["ota"]) >= 16)

    rows = db("SELECT count(*) FROM departments WHERE tenant_id = %s", (S["tenant_id"],))
    check("S1 4 departamentos seed", rows[0][0] == 4, f"n={rows[0][0]}")
    rows = db("SELECT id FROM departments WHERE tenant_id = %s AND profile_template = 'it_operations'",
              (S["tenant_id"],))
    S["dept_it"] = str(rows[0][0])
    rows = db("SELECT amount_cents FROM budgets WHERE tenant_id = %s AND level = 'org'", (S["tenant_id"],))
    check("S1 presupuesto org 50000¢ ($500)", rows[0][0] == 50000, f"{rows[0][0]}¢")
    rows = db("SELECT u.id, u.oidc_subject FROM users u JOIN user_roles r ON r.user_id = u.id "
              "WHERE u.tenant_id = %s AND r.role_key = 'org_admin'", (S["tenant_id"],))
    S["admin_user_id"], S["admin_sub"] = str(rows[0][0]), str(rows[0][1])
    rows = db("SELECT key, value FROM policies WHERE tenant_id = %s AND level = 'org'", (S["tenant_id"],))
    pols = {r[0]: r[1] for r in rows}
    check("S1 políticas default del tenant (require_mfa, approved_models_only, "
          "explicit_shared_agent_grants, scoped_credentials)",
          all(k in pols for k in ("require_mfa", "approved_models_only",
                                  "explicit_shared_agent_grants", "scoped_credentials")),
          str(sorted(pols)))
    st, raw_kc, _ = kc_fetch(f"/admin/realms/{SLUG}", headers={
        "Authorization": f"Bearer {kc_admin_token()}"})
    check("S1 realm Keycloak real existe", st == 200, f"HTTP {st}")
    chain = db("SELECT audit_verify_chain(%s)", (S["tenant_id"],))[0][0]
    # audit_verify_chain devuelve (status, detail) — se toma el status.
    chain_status = chain[0] if isinstance(chain, (tuple, list)) else chain
    check("S1 cadena de audit del tenant recién nacido: íntegra", chain_status == "ok",
          str(chain))
    rows = db("SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = 'tenant.provisioned'",
              (S["tenant_id"],))
    check("S1 audit: tenant.provisioned", rows[0][0] >= 1)

    # --- S2: admin login REAL PKCE + TOTP + sesión MFA ----------------------
    print("--- S2 admin: login real PKCE S256 + enrolamiento TOTP real ---", flush=True)
    totp_state = {}
    id_token, acr = pkce_login(ADMIN_EMAIL, S["ota"], new_password=ADMIN_NEW_PASSWORD,
                               totp_state=totp_state)
    check("S2 login REAL del admin (PKCE + UPDATE_PASSWORD + CONFIGURE_TOTP) → code",
          id_token is not None)
    check("S2 el realm real emite acr=2 con password+OTP (D-MFA-01)", str(acr) == "2", f"acr={acr}")
    S["admin"] = mint(id_token)
    check("S2 sesión admin minteada con MFA verificada (acr real, sin dobles)",
          S["admin"]["session_token"].startswith("sts1.") and S["admin"]["mfa_verified"] is True,
          f"mfa_verified={S['admin'].get('mfa_verified')}")
    st, me, raw = api("GET", "/api/me", bearer=S["admin"]["session_token"])
    check("S2 GET /api/me → 200 org_admin", st == 200 and me["email"] == ADMIN_EMAIL
          and any(r["role_key"] == "org_admin" for r in me["roles"]), f"HTTP {st} {raw[:150]}")

    # --- S3: usuario obrero REAL + puerta MFA + configuración de la org -----
    print("--- S3 worker: alta real en el realm + puerta MFA + config de la org ---", flush=True)
    token = kc_admin_token()
    st, raw_kc = kc_json("POST", f"/admin/realms/{SLUG}/users", {
        "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
        "firstName": "Final", "lastName": "Worker",
        "attributes": {"tenant_id": [S["tenant_id"]]}}, token=token)
    check("S3 usuario obrero creado en el realm real", st in (201, 409), f"HTTP {st}")
    worker_uuid = kc_user_uuid(WORKER_EMAIL)
    st, _ = kc_json("PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
                    {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
                    token=token)
    check("S3 password del obrero fijado (no temporal)", st in (204, 409), f"HTTP {st}")

    # Fila de directorio + rol user en depto IT (fixture declarado: POST /api/users
    # pendiente de contrato — tarea dev t_f72dc691).
    db("INSERT INTO users (tenant_id, department_id, email, display_name, status, "
       "mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', true, %s)",
       (S["tenant_id"], S["dept_it"], WORKER_EMAIL, "Final Worker", worker_uuid), fetch=False)
    db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
       "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s",
       (S["tenant_id"], worker_uuid), fetch=False)
    rows = db("SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))
    S["worker_user_id"] = str(rows[0][0])
    S["worker_sub"] = worker_uuid

    # (a) login SIN TOTP del obrero: el IdP es fail-closed (D-MFA-01) —
    # un usuario sin credencial TOTP recibe CONFIGURE_TOTP inline y NO hay
    # code. La puerta 403 mfa_required del control plane (token sin acr)
    # se ejerce en la suite pytest con el doble JWKS declarado (paso 2).
    id_token, acr = pkce_login(WORKER_EMAIL, WORKER_PASSWORD, refuse_totp=True)
    check("S3 login del obrero SIN TOTP: el IdP NO entrega code (MFA obligatoria, fail-closed)",
          id_token == "MFA_REQUIRED",
          f"resultado={id_token!r} acr={acr!r}")

    # (b) enrolamiento TOTP inline (el mismo baile) → login con OTP → acr=2
    worker_totp = {}
    id_token, acr = pkce_login(WORKER_EMAIL, WORKER_PASSWORD, totp_state=worker_totp)
    check("S3 obrero enrola TOTP real y loguea con OTP → acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    S["worker"] = mint(id_token)
    check("S3 sesión del obrero MFA-verificada", S["worker"]["mfa_verified"] is True)
    st, me, raw = api("GET", "/api/me", bearer=S["worker"]["session_token"])
    check("S3 GET /api/me del obrero → 200 rol user", st == 200
          and any(r["role_key"] == "user" for r in me["roles"]), f"HTTP {st}")

    # (c) admin configura la org: políticas
    admin = S["admin"]["session_token"]
    worker = S["worker"]["session_token"]
    st, body, raw = api("PUT", "/api/policies/org",
                        {"key": "allowed_connectors", "value": ["vmware", "email"]}, bearer=admin)
    check("S3 política org allowed_connectors=[vmware,email] → 200", st == 200, raw[:150])
    st, body, raw = api("PUT", f"/api/policies/dept/{S['dept_it']}",
                        {"key": "allowed_connectors", "value": ["vmware"]}, bearer=admin)
    check("S3 política depto IT estrecha a [vmware] → 200", st == 200, raw[:150])
    st, body, raw = api("PUT", f"/api/policies/dept/{S['dept_it']}",
                        {"key": "allowed_connectors", "value": ["vmware", "email", "telegram"]},
                        bearer=admin)
    check("S3 no-relajación: depto amplía por encima de org → 422", st == 422, f"HTTP {st}")
    st, body, raw = api("PUT", "/api/policies/org", {"key": "require_mfa", "value": False},
                        bearer=admin)
    check("S3 invariable require_mfa=false → 422", st == 422, f"HTTP {st}")
    st, body, raw = api("GET", "/api/connectors/available", bearer=worker)
    check("S3 efecto deny-overrides para el obrero: SOLO vmware visible",
          st == 200 and [c["slug"] for c in body["connectors"]] == ["vmware"], raw[:200])
    st, body, raw = api("GET", "/api/me/policies", bearer=worker)
    check("S3 política efectiva del obrero consultable (/api/me/policies)", st == 200, raw[:150])

    # (d) presupuestos jerárquicos
    st, body, raw = api("PUT", f"/api/budgets/dept/{S['dept_it']}", {"amount_cents": 20000},
                        bearer=admin)
    check("S3 presupuesto depto IT $200 → 200", st == 200, raw[:150])
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": 5000}, bearer=admin)
    check("S3 presupuesto obrero $50 → 200", st == 200, raw[:150])
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": 25000}, bearer=admin)
    check("S3 Σ usuarios > depto → 422", st == 422, f"HTTP {st}")
    st, body, raw = api("PUT", f"/api/budgets/dept/{S['dept_it']}", {"amount_cents": 60000},
                        bearer=admin)
    check("S3 Σ deptos > org → 422", st == 422, f"HTTP {st}")
    st, ov, raw = api("GET", "/api/budgets/overview", bearer=admin)
    check("S3 overview: org $500, depto IT $200, unallocated $300",
          st == 200 and ov["organization"]["budget_cents"] == 50000
          and any(d["budget_cents"] == 20000 for d in ov["departments"])
          and ov.get("unassigned_cents") == 30000, raw[:300])
    st, me_b, raw = api("GET", "/api/budgets/me", bearer=worker)
    check("S3 /api/budgets/me del obrero: $50 sin corte",
          st == 200 and me_b["user"]["budget_cents"] == 5000 and me_b["cutoff"] is False,
          raw[:200])

    # --- S4: enrolamiento de dispositivo desde el desktop --------------------
    print("--- S4 dispositivo: enroll → approve → token one-time ---", flush=True)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    # id_token MFA-real del obrero (acr=2) — el mismo que minteó la sesión
    st, body, raw = api("POST", "/api/devices/enroll", payload={
        "org_code": SLUG, "id_token": id_token, "public_key": public_key,
        "fingerprint": "fp-final-e2e-laptop", "device_name": "final-e2e-laptop",
        "platform": "linux"})
    check("S4 POST /api/devices/enroll (org_code + id_token acr=2 + RSA-2048 real) → 202 pending",
          st == 202 and body["status"] == "pending", f"HTTP {st} {raw[:200]}")
    S["device_id"] = body["device_id"]
    S["enrollment_token"] = body["enrollment_token"]
    check("S4 enrollment_token one-time enr1.*", S["enrollment_token"].startswith("enr1."))
    st, _, _ = api("POST", "/api/devices/enroll/exchange", {"enrollment_token": S["enrollment_token"]})
    check("S4 polling pending → 202 repetible", st == 202, f"HTTP {st}")
    st, body, raw = api("POST", f"/api/devices/{S['device_id']}/approve", bearer=admin)
    check("S4 admin aprueba el dispositivo → active", st == 200 and body["status"] == "active",
          f"HTTP {st} {raw[:150]}")
    st, body, raw = api("POST", "/api/devices/enroll/exchange", {"enrollment_token": S["enrollment_token"]})
    check("S4 canje one-time → 200 device token", st == 200 and body.get("device_token"),
          f"HTTP {st} {raw[:150]}")
    S["device_token"] = body["device_token"]
    st, _, _ = api("POST", "/api/devices/enroll/exchange", {"enrollment_token": S["enrollment_token"]})
    check("S4 segundo canje → 410 (one-time estricto)", st == 410, f"HTTP {st}")
    st, body, raw = api("GET", "/api/devices/me", bearer=worker)
    check("S4 el obrero ve su dispositivo active",
          st == 200 and any(d["id"] == S["device_id"] and d["status"] == "active"
                            for d in body["devices"]), raw[:200])

    # --- S5: agente compartido + grants + modelo aprobado -------------------
    print("--- S5 agente: publish → grants → invoke; modelo aprobado vía key virtual ---", flush=True)
    st, body, raw = api("POST", "/api/agents",
                        {"name": "it-health-check", "description": "Infra health check",
                         "scope": "dept"}, bearer=worker)
    check("S5 obrero publica agente compartido → 201",
          st == 201 and body.get("id"), f"HTTP {st} {raw[:200]}")
    S["agent_id"] = body["id"]
    rows = db("SELECT count(*) FROM agent_grants WHERE agent_id = %s", (S["agent_id"],))
    check("S5 publicado SIN ningún grant (default cerrado, AC-AGT-001)", rows[0][0] == 0,
          f"grants={rows[0][0]}")
    st, body, raw = api("POST", f"/api/agents/{S['agent_id']}/invoke", bearer=worker)
    check("S5 el dueño invoca su agente → 200 facturado al dueño",
          st == 200 and body.get("billed_to_user_id") == S["worker_user_id"], raw[:200])
    st, body, raw = api("POST", f"/api/agents/{S['agent_id']}/grants",
                        {"user_id": S["admin_user_id"], "permission": "invoke"}, bearer=worker)
    check("S5 grant explícito invoke al admin → 201", st == 201, f"HTTP {st} {raw[:150]}")
    S["grant_id"] = body.get("grant_id") or body.get("id")
    st, body, raw = api("POST", f"/api/agents/{S['agent_id']}/invoke", bearer=admin)
    check("S5 el admin (con grant) invoca → 200 facturado al INVOCADOR",
          st == 200 and body.get("billed_to_user_id") == S["admin_user_id"], raw[:200])
    st, body, raw = api("DELETE",
                        f"/api/agents/{S['agent_id']}/grants/{S['grant_id']}", bearer=worker)
    check("S5 revocación del grant → 204", st == 204, f"HTTP {st}")
    st, body, raw = api("POST", f"/api/agents/{S['agent_id']}/invoke", bearer=admin)
    check("S5 tras revocar: invocación denegada (403/404)", st in (403, 404), f"HTTP {st}")

    st, body, raw = api("GET", "/api/models/allowed-for-me", bearer=worker)
    check("S5 catálogo de modelos aprobados para el obrero incluye qwen",
          st == 200 and QWEN in body["models"], raw[:200])
    st, body, raw = api("POST", "/api/models/verify", {"model_name": QWEN}, bearer=worker)
    check("S5 /api/models/verify qwen → allowed", st == 200 and body.get("allowed") is True,
          raw[:150])
    st, body, raw = api("POST", "/api/models/keys", {"user_id": S["worker_user_id"]},
                        bearer=admin)
    check("S5 emisión de key virtual → 201, plaintext EXACTAMENTE una vez, max_budget $50",
          st == 201 and body["key"].startswith("sk-fake-") and body["max_budget_usd"] == 50.0,
          f"HTTP {st} {raw[:200]}")
    S["key"] = body["key"]
    S["key_id"] = body["key_id"]
    rows = db("SELECT litellm_key_hash FROM api_keys WHERE id = %s", (S["key_id"],))
    check("S5 la DB guarda SOLO sha256(key) (C4)",
          rows[0][0] == hashlib.sha256(S["key"].encode()).hexdigest())
    st, body, raw = api("GET", "/api/models/keys/me", bearer=worker)
    check("S5 metadata de keys sin plaintext", st == 200 and S["key"] not in raw)

    st, resp_body = litellm_chat(S["key"], QWEN)
    check("S5 invocación 1 al data plane con modelo aprobado → 200", st == 200, resp_body)
    st, resp_body = litellm_chat(S["key"], QWEN)
    check("S5 invocación 2 al data plane → 200", st == 200, resp_body)
    st, resp_body = litellm_chat(S["key"], "gpt-4o-not-approved")
    check("S5 modelo NO aprobado → 403 del gateway (lista de la key)",
          st == 403, f"HTTP {st} {resp_body[:120]}")

    save_state(state_path, S)
    print(f"\nFASE A: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# FASE B
# ===========================================================================
def phase_b(state_path):
    S = load_state(state_path)
    admin = S["admin"]["session_token"]
    worker = S["worker"]["session_token"]
    print("=== FASE B (tras reinicio del API): sesiones → spend → corte → audit → revocación ===",
          flush=True)

    # --- S0b: las sesiones sobreviven el reinicio (AC-SEC-003) --------------
    st, me, raw = api("GET", "/api/me", bearer=admin)
    check("S0b la sesión admin SOBREVIVE el reinicio del API (PostgreSQL, C2)",
          st == 200 and me["email"] == ADMIN_EMAIL, f"HTTP {st}")
    st, me, raw = api("GET", "/api/me", bearer=worker)
    check("S0b la sesión del obrero sobrevive el reinicio", st == 200, f"HTTP {st}")

    # --- S6: el presupuesto se descuenta ------------------------------------
    print("--- S6 spend: ingest real del worker → ledger → counters → overview ---", flush=True)
    run_spend_worker()
    rows = db("SELECT count(*), coalesce(sum(cost_billed_cents),0) FROM spend_ledger "
              "WHERE tenant_id = %s AND user_id = %s", (S["tenant_id"], S["worker_user_id"]))
    check("S6 ledger: ≥2 filas con costo > 0 para el obrero",
          rows[0][0] >= 2 and rows[0][1] >= 200, f"filas={rows[0][0]} costo={rows[0][1]}¢")
    rows = db("SELECT spent_cents FROM budget_counters WHERE level = 'user' "
              "AND entity_id = %s AND period_start = date_trunc('month', now())::date",
              (S["worker_user_id"],))
    check("S6 counter del obrero descontado (≥200¢)", rows and rows[0][0] >= 200,
          str(rows[0][0] if rows else None))
    st, ov, raw = api("GET", "/api/budgets/overview", bearer=admin)
    org_used = ov.get("organization", {}).get("spent_cents")
    check("S6 overview refleja el gasto (org spent_cents ≥ 200¢)",
          st == 200 and org_used is not None and org_used >= 200,
          f"spent={org_used} {raw[:300]}")
    st, me_b, raw = api("GET", "/api/budgets/me", bearer=worker)
    check("S6 /api/budgets/me del obrero refleja el gasto sin corte",
          st == 200 and me_b["cutoff"] is False, raw[:250])

    # --- S7: corte de presupuesto -------------------------------------------
    print("--- S7 corte: presupuesto < gasto (force) → key bloqueada → 401 → notificación ---", flush=True)
    rows = db("SELECT spent_cents FROM budget_counters WHERE level = 'user' AND entity_id = %s "
              "AND period_start = date_trunc('month', now())::date", (S["worker_user_id"],))
    spent = rows[0][0]
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": spent - 50, "force": True}, bearer=admin)
    check("S7 reducción por debajo del gasto con force=true → 200 (auditable)",
          st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": spent - 100}, bearer=admin)
    check("S7 sin force → 422", st == 422, f"HTTP {st}")
    api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
        {"amount_cents": spent - 50, "force": True}, bearer=admin)
    run_spend_worker()
    rows = db("SELECT status FROM api_keys WHERE id = %s", (S["key_id"],))
    check("S7 la key virtual queda BLOQUEADA por hash en el gateway", rows[0][0] == "blocked",
          rows[0][0])
    st, resp_body = litellm_chat(S["key"], QWEN)
    check("S7 el gateway rechaza la key bloqueada → 401", st == 401, f"HTTP {st}")
    st, body, raw = api("GET", "/api/me/notifications", bearer=worker)
    check("S7 notificación budget_cutoff al obrero",
          st == 200 and any(n["type"] == "budget_cutoff" for n in body["notifications"]),
          raw[:250])
    st, me_b, raw = api("GET", "/api/budgets/me", bearer=worker)
    check("S7 /api/budgets/me: cutoff=true level=user",
          st == 200 and me_b["cutoff"] is True and me_b.get("cutoff_level") == "user",
          raw[:250])
    rows = db("SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = 'budget.cutoff'",
              (S["tenant_id"],))
    check("S7 audit budget.cutoff", rows[0][0] >= 1)
    # recuperación
    st, _, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                     {"amount_cents": 15000, "force": True}, bearer=admin)
    check("S7 recuperación: subir presupuesto → 200", st == 200, f"HTTP {st}")
    run_spend_worker()
    rows = db("SELECT status FROM api_keys WHERE id = %s", (S["key_id"],))
    check("S7 la key se rehabilita tras subir el presupuesto", rows[0][0] == "active", rows[0][0])
    st, resp_body = litellm_chat(S["key"], QWEN)
    check("S7 invocación tras recuperación → 200", st == 200, resp_body[:120])

    # --- S8: el audit log refleja TODO ---------------------------------------
    print("--- S8 audit: trail completo + cadena íntegra (API + CLI) ---", flush=True)
    expected = {
        "tenant.provisioned", "policy.updated", "budget.updated",
        "model.key_issued", "agent.shared", "agent.granted", "agent.revoked",
        "agent.invoked", "device.enrolled", "device.approved",
        "budget.cutoff", "model.key_blocked",
    }
    found, offset = set(), 0
    while True:
        st, body, raw = api("GET", f"/api/audit/events?limit=200&offset={offset}", bearer=admin)
        assert st == 200, raw
        page = body["items"]
        if not page:
            break
        found |= {e["action"] for e in page}
        offset += 200
    missing = expected - found
    check("S8 audit contiene TODAS las acciones del flujo", not missing,
          f"faltan: {sorted(missing)}" if missing else f"{len(found)} acciones distintas")
    print(f"    [audit] inventario de acciones: {sorted(found)}", flush=True)
    st, body, raw = api("POST", "/api/admin/audit/verify", bearer=admin)
    check("S8 verificación de cadena vía API → ok", st == 200 and body.get("status") == "ok",
          raw[:200])
    proc = subprocess.run(["/root/trust-agent/platform/.venv/bin/verify-audit-chain",
                           "verify", S["tenant_id"]], capture_output=True, text=True,
                          timeout=60, env=cp_env(), cwd="/root/trust-agent/platform")
    check("S8 CLI verify-audit-chain → exit 0, cadena ok",
          proc.returncode == 0 and '"status"' in proc.stdout and "ok" in proc.stdout,
          proc.stdout.strip()[:200])
    # aislamiento del audit: el admin del tenant NO ve eventos de otros tenants
    rows = db("SELECT count(*) FROM audit_log WHERE tenant_id = %s", (S["tenant_id"],))
    st, body, raw = api("GET", "/api/audit/events?limit=200", bearer=admin)
    check("S8 alcance del audit = su propio tenant (total == filas SQL del tenant)",
          st == 200 and body["total"] == rows[0][0],
          f"api={body.get('total')} sql={rows[0][0]}")

    # --- S9: revocación del dispositivo bloquea el acceso --------------------
    print("--- S9 revocación del dispositivo → acceso bloqueado ---", flush=True)
    session_id = S["worker"]["session_token"].split(".")[2]
    db("UPDATE sessions SET device_id = %s WHERE id = %s",
       (S["device_id"], session_id), fetch=False)
    st, _, raw = api("GET", "/api/me", bearer=worker)
    check("S9 sesión ligada al dispositivo operativa antes de revocar", st == 200, f"HTTP {st}")
    st, body, raw = api("POST", f"/api/devices/{S['device_id']}/revoke", bearer=admin)
    check("S9 admin revoca el dispositivo → 200", st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = api("GET", "/api/me", bearer=worker)
    check("S9 la sesión ligada al dispositivo revocado → 401 INMEDIATO",
          st == 401, f"HTTP {st} {raw[:150]}")
    rows = db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'device.revoked' AND device_id = %s",
              (S["tenant_id"], S["device_id"]))
    check("S9 audit device.revoked con device_id", rows[0][0] >= 1)
    proc = subprocess.run(["/root/trust-agent/platform/.venv/bin/verify-audit-chain",
                           "verify", S["tenant_id"]], capture_output=True, text=True,
                          timeout=60, env=cp_env(), cwd="/root/trust-agent/platform")
    check("S9 cadena de audit íntegra tras TODO el flujo (CLI exit 0)", proc.returncode == 0,
          proc.stdout.strip()[:150])

    print(f"\nFASE B: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["a", "b"], required=True)
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    rc = phase_a(args.state) if args.phase == "a" else phase_b(args.state)
    if FAILURES:
        print(f"\nE2E FINAL FAIL — {len(FAILURES)}: {FAILURES}", flush=True)
    else:
        print("\nE2E FINAL PASS — flujo global completo con evidencia real", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())

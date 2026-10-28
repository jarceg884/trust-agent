#!/usr/bin/env python3
"""RE-VALIDACIÓN CONTRA LITELLM REAL — spend/corte/model-access/pricing (t_c4766c4d).

Cubre el riesgo 2 de docs/validacion-final.md §6: toda la validación de
gasto/corte corrió contra el doble declarado (D-GOV-15). Este script
ejerce el MISMO flujo de gobernanza contra un proxy LiteLLM REAL
(127.0.0.1:4000, venv propio en litellm-real/, PostgreSQL dedicado
trust-agent-litellm-db :55433) con upstream Huawei Cloud MaaS
ap-southeast-1 (OpenAI-compatible, modelo glm-5.2, key 'Inforum' vía
env — NUNCA en el repo).

Escenario (un tenant nuevo, todo real):
  R0  gateway real vivo: health, /v1/models, tabla de budgets en su DB,
      invocación directa con master key (MaaS real) y el doble 39989
      sigue arriba (D-GOV-15 conservado).
  R1  proveedor crea tenant (model_plan=['glm-5.2'] — solo el modelo que
      EXISTE de verdad en MaaS).
  R2  admin: login REAL PKCE S256 + enrolamiento TOTP RFC 6238 (acr=2).
  R3  usuario obrero real + sesión MFA.
  R4  presupuestos: depto $50, obrero $0.02 (2¢) — la key hereda
      max_budget=0.02 USD y budget_duration='30d'.
  R5  key virtual por usuario (plaintext UNA vez, sha256 en DB) →
      invocación REAL 200 → modelo no aprobado → 403 del gateway.
  R6  corte NATIVO de LiteLLM: loop de invocaciones reales hasta que el
      gateway rechaza por max_budget agotado (sin intervención del
      control plane — spend worker detenido durante el loop).
  R7  ingesta del spend worker contra /spend/logs/v2 del proxy real:
      ledger idempotente, costo = pricing REAL MaaS (fórmula verificada
      contra los tokens del log), counters, /reload/model_cost_map.
  R8  corte del control plane: budget < gasto (force) → key bloqueada
      POR HASH en el gateway real → 401 → notificación budget_cutoff →
      recuperación (subir budget) → key rehabilitada → 200.

Uso:
  python scripts/revalidate_litellm_real.py --state /tmp/litellm_real_state.json
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import json
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
LITELLM_HOST, LITELLM_PORT = "127.0.0.1", 4000
DOUBLE_PORT = 39989
LITELLM_REAL_ENV = "/root/trust-agent/litellm-real/.env"
CLIENT_ID = "trust-agent-web"
# Callback del helper trust-login del bundle de descarga (spec 023 D-023-13):
# el flujo real de desktop. Retirado el demo :9090 (t_b8769ecb).
REDIRECT_URI = "http://127.0.0.1:8765/callback"
PROVIDER_TOKEN = "test-provider-token"
KC_ADMIN_PASSWORD = "KeycloakDemo2026"
DB_DSN = ("host=127.0.0.1 port=55432 user=postgres "
          "password=trustlocal dbname=trust_agent")

SLUG = "litellm-real"
ADMIN_EMAIL = "admin@litellm-real.test"
ADMIN_NEW_PASSWORD = "LitellmRealAdmin2026!"
WORKER_EMAIL = "worker@litellm-real.test"
WORKER_PASSWORD = "LitellmRealWorker2026!"
MODEL = "glm-5.2"
NOT_APPROVED_MODEL = "glm-5.3"  # deployment REAL del catálogo MaaS (spec 018),
# en el model_list del proxy pero NO aprobado para este tenant (model_plan=['glm-5.2'])
USER_BUDGET_CENTS = 2          # $0.02 — la key hereda max_budget=0.02 USD
DEPT_BUDGET_CENTS = 5000       # $50
# Pricing REAL MaaS GLM-5.2 Period 1 (USD por millón de tokens):
# input $1.40 · output $4.40 · cache-hit $0.26
PRICE_IN, PRICE_OUT, PRICE_CACHED = 1.4e-6, 4.4e-6, 0.26e-6

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
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=30)
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


def master_key() -> str:
    for line in open(LITELLM_REAL_ENV):
        if line.startswith("LITELLM_MASTER_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("LITELLM_MASTER_KEY not found in litellm-real/.env")


def litellm_admin(method, path, payload=None, params=None):
    """Admin API del proxy REAL con la master key (nunca se imprime)."""
    if params:
        path = f"{path}?{urllib.parse.urlencode(params)}"
    conn = http.client.HTTPConnection(LITELLM_HOST, LITELLM_PORT, timeout=30)
    body = json.dumps(payload).encode() if payload is not None else None
    conn.request(method, path, body=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {master_key()}"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw) if raw else {}, raw
    except json.JSONDecodeError:
        return resp.status, {}, raw


def _key_info_fields(info: dict) -> dict:
    """Aplana la respuesta de /key/info.

    Formato REAL observado (t_c4766c4d): {"key": "<hash>", "info": {...}}.
    El doble devuelve el registro directo; versiones antiguas anidaban
    bajo "key".
    """
    k = info.get("info")
    if isinstance(k, dict):
        return k
    k = info.get("key")
    if isinstance(k, list) and k and isinstance(k[0], dict):
        return k[0]
    if isinstance(k, dict):
        return k
    return info


def litellm_chat(key, model, prompt="ping", max_tokens=512, timeout=120):
    """Invocación al data plane del proxy REAL (upstream MaaS verdadero)."""
    conn = http.client.HTTPConnection(LITELLM_HOST, LITELLM_PORT, timeout=timeout)
    conn.request("POST", "/v1/chat/completions",
                 body=json.dumps({"model": model, "max_tokens": max_tokens,
                                  "messages": [{"role": "user", "content": prompt}]}).encode(),
                 headers={"Content-Type": "application/json",
                          "Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw) if raw else {}, raw
    except json.JSONDecodeError:
        return resp.status, {}, raw


def spend_worker(action):
    """stop/start/exec del spend worker del stack (contenedor compose)."""
    if action in ("stop", "start"):
        subprocess.run(["docker", action, "trust-agent-spend-worker"],
                       check=True, capture_output=True)
        if action == "start":
            time.sleep(3)
        return ""
    proc = subprocess.run(
        ["docker", "exec", "trust-agent-spend-worker",
         "python", "-m", "app.governance.spend_worker", "--once"],
        capture_output=True, text=True, timeout=180)
    out = (proc.stdout + proc.stderr).strip()
    print(f"    [spend-worker] rc={proc.returncode} {out[:400]}", flush=True)
    return proc.returncode, out


# ---------------------------------------------------------------------------
# Keycloak: baile PKCE real (copiado y probado de scripts/e2e_final_live.py)
# ---------------------------------------------------------------------------
def kc_fetch(path, *, method="GET", data=None, headers=None, follow=False):
    h = dict(headers or {})
    if COOKIES:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=30)
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
    """Sigue redirects internos de KC26 (sin code=) hasta el siguiente form."""
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


def pkce_login(username, password, *, new_password=None, totp_state=None):
    """Baile REAL authorization-code + PKCE S256 contra el realm (KC26)."""
    for k in list(COOKIES):
        COOKIES.pop(k)
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI, "state": "st-litellm-real",
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
            secret = totp_secret_of(body)
            if totp_state is not None:
                totp_state["secret"] = secret
            used_counter = int(time.time()) // 30
            code = totp(secret, counter=used_counter)
            print(f"    [dance] enrolamiento TOTP real: code={code}", flush=True)
            st, body, hdrs = kc_fetch(act.split(f"{KC_HOST}:{KC_PORT}")[-1], method="POST",
                                      data=urllib.parse.urlencode({
                                          "totp": code, "totpSecret": secret,
                                          "mode": "totp", "userLabel": "litellm-real",
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


def main():
    global SLUG, ADMIN_EMAIL, WORKER_EMAIL
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--slug", default="litellm-real",
                    help="slug del tenant (uno nuevo por corrida: el "
                         "provisioning es idempotente por slug)")
    args = ap.parse_args()
    SLUG = args.slug
    ADMIN_EMAIL = f"admin@{SLUG}.test"
    WORKER_EMAIL = f"worker@{SLUG}.test"
    S = {}

    # =====================================================================
    print("=== R0: gateway LiteLLM REAL vivo (propia PostgreSQL, MaaS upstream) ===",
          flush=True)
    conn = http.client.HTTPConnection(LITELLM_HOST, LITELLM_PORT, timeout=10)
    conn.request("GET", "/health/liveliness")
    resp = conn.getresponse()
    check("R0 proxy real /health/liveliness → 200", resp.status == 200,
          resp.read().decode()[:60])
    conn.close()

    st, body, raw = litellm_admin("GET", "/v1/models")
    check("R0 /v1/models del proxy real lista glm-5.2",
          st == 200 and any(m.get("id") == MODEL for m in body.get("data", [])),
          raw[:150])

    # Budgets requieren DB (hallazgo clave): la DB dedicada tiene las tablas.
    out = subprocess.run(
        ["docker", "exec", "trust-agent-litellm-db", "psql", "-U", "postgres",
         "-d", "litellm", "-tAc",
         "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"],
        capture_output=True, text=True)
    tables = int(out.stdout.strip() or 0)
    check("R0 PostgreSQL dedicada del proxy con tablas de budgets (sin DB fallan abiertos)",
          tables >= 10, f"tablas={tables}")
    out = subprocess.run(
        ["docker", "exec", "trust-agent-litellm-db", "psql", "-U", "postgres",
         "-d", "litellm", "-tAc",
         "SELECT count(*) FROM information_schema.tables "
         "WHERE table_name ILIKE '%budget%'"],
        capture_output=True, text=True)
    check("R0 tabla LiteLLM_BudgetTable presente en la DB propia",
          int(out.stdout.strip() or 0) >= 1, out.stdout.strip())

    st, body, raw = litellm_chat(master_key(), MODEL, "Responde solo: OK", 512)
    usage = body.get("usage", {})
    check("R0 invocación DIRECTA con master key → 200 real de MaaS (usage con tokens)",
          st == 200 and usage.get("total_tokens", 0) > 0,
          f"HTTP {st} usage={usage}")

    import socket
    dbl = socket.create_connection(("127.0.0.1", DOUBLE_PORT), timeout=3) is not None
    check("R0 el doble declarado 39989 sigue arriba (D-GOV-15 conservado para tests)",
          dbl)

    # =====================================================================
    print("=== R1: proveedor crea tenant (model_plan=['glm-5.2']) ===", flush=True)
    st, body, raw = api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {PROVIDER_TOKEN}"}, payload={
        "organization_name": "LiteLLM Real Corp", "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0, "model_plan": [MODEL], "slug": SLUG})
    check("R1 POST /api/admin/tenants → 201", st == 201 and body.get("created") is True,
          f"HTTP {st} {raw[:200]}")
    tenant = body["tenant"]
    S["tenant_id"] = tenant["id"]
    S["ota"] = body["one_time_activation"]["one_time_password"]
    check("R1 tenant activo con realm real",
          tenant["status"] == "active"
          and tenant["oidc_issuer_url"].endswith(f"/realms/{SLUG}"))
    rows = db("SELECT id FROM departments WHERE tenant_id = %s "
              "AND profile_template = 'it_operations'", (S["tenant_id"],))
    S["dept_it"] = str(rows[0][0])
    rows = db("SELECT u.id, u.oidc_subject FROM users u JOIN user_roles r "
              "ON r.user_id = u.id WHERE u.tenant_id = %s "
              "AND r.role_key = 'org_admin'", (S["tenant_id"],))
    S["admin_user_id"], S["admin_sub"] = str(rows[0][0]), str(rows[0][1])
    rows = db("SELECT mc.model_name FROM model_access ma "
              "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
              "WHERE ma.tenant_id = %s AND ma.status = 'approved'",
              (S["tenant_id"],))
    check("R1 model_plan=['glm-5.2'] → SOLO glm-5.2 aprobado (existe de verdad en MaaS)",
          [r[0] for r in rows] == [MODEL], str([r[0] for r in rows]))

    # =====================================================================
    print("=== R2: admin login REAL PKCE S256 + TOTP (acr=2) ===", flush=True)
    totp_state = {}
    id_token, acr = pkce_login(ADMIN_EMAIL, S["ota"], new_password=ADMIN_NEW_PASSWORD,
                               totp_state=totp_state)
    check("R2 login REAL del admin → code", id_token is not None)
    check("R2 el realm real emite acr=2 (D-MFA-01)", str(acr) == "2", f"acr={acr}")
    S["admin"] = mint(id_token)
    admin = S["admin"]["session_token"]
    st, me, raw = api("GET", "/api/me", bearer=admin)
    check("R2 GET /api/me → 200 org_admin", st == 200 and me["email"] == ADMIN_EMAIL,
          f"HTTP {st}")

    # =====================================================================
    print("=== R3: usuario obrero real + sesión MFA ===", flush=True)
    token = kc_admin_token()
    st, _ = kc_json("POST", f"/admin/realms/{SLUG}/users", {
        "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
        "firstName": "Litellm", "lastName": "Real",
        "attributes": {"tenant_id": [S["tenant_id"]]}}, token=token)
    check("R3 usuario obrero creado en el realm real", st in (201, 409), f"HTTP {st}")
    st, raw_kc = kc_json("GET",
                         f"/admin/realms/{SLUG}/users?username={urllib.parse.quote(WORKER_EMAIL)}&exact=true",
                         token=token)
    worker_uuid = json.loads(raw_kc)[0]["id"]
    kc_json("PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
            {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
            token=token)
    db("INSERT INTO users (tenant_id, department_id, email, display_name, status, "
       "mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', true, %s)",
       (S["tenant_id"], S["dept_it"], WORKER_EMAIL, "Litellm Real", worker_uuid),
       fetch=False)
    db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
       "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s",
       (S["tenant_id"], worker_uuid), fetch=False)
    rows = db("SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))
    S["worker_user_id"] = str(rows[0][0])
    worker_totp = {}
    id_token, acr = pkce_login(WORKER_EMAIL, WORKER_PASSWORD, totp_state=worker_totp)
    check("R3 obrero enrola TOTP real y loguea → acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    S["worker"] = mint(id_token)
    worker = S["worker"]["session_token"]

    # =====================================================================
    print("=== R4: presupuestos (obrero $0.02 → key con max_budget nativo) ===",
          flush=True)
    st, body, raw = api("PUT", f"/api/budgets/dept/{S['dept_it']}",
                        {"amount_cents": DEPT_BUDGET_CENTS}, bearer=admin)
    check("R4 presupuesto depto IT $50 → 200", st == 200, raw[:150])
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": USER_BUDGET_CENTS}, bearer=admin)
    check(f"R4 presupuesto obrero ${USER_BUDGET_CENTS / 100} → 200", st == 200,
          raw[:150])

    # =====================================================================
    print("=== R5: key virtual → invocación REAL → 403 modelo no aprobado ===",
          flush=True)
    st, body, raw = api("GET", "/api/models/allowed-for-me", bearer=worker)
    check("R5 modelos efectivos del obrero = [glm-5.2]",
          st == 200 and body["models"] == [MODEL], raw[:200])
    st, body, raw = api("POST", "/api/models/keys",
                        {"user_id": S["worker_user_id"]}, bearer=admin)
    check("R5 emisión de key virtual contra el proxy REAL → 201",
          st == 201 and body.get("key", "").startswith("sk-"),
          f"HTTP {st} {raw[:150]}")
    S["key"] = body["key"]
    S["key_id"] = body["key_id"]
    S["key_hash"] = hashlib.sha256(S["key"].encode()).hexdigest()
    check("R5 max_budget_usd=0.02 y budget_duration='30d' en la key",
          body.get("max_budget_usd") == USER_BUDGET_CENTS / 100.0
          and body.get("budget_duration") == "30d",
          f"max={body.get('max_budget_usd')} dur={body.get('budget_duration')}")
    rows = db("SELECT litellm_key_hash FROM api_keys WHERE id = %s", (S["key_id"],))
    check("R5 la DB guarda SOLO sha256(key) (C4)",
          rows[0][0] == S["key_hash"])
    st, info, raw = litellm_admin("GET", "/key/info", params={"key": S["key_hash"]})
    kinfo = _key_info_fields(info)
    check("R5 /key/info del proxy real acepta el HASH como handle",
          st == 200 and kinfo.get("spend") is not None,
          f"HTTP {st} spend={kinfo.get('spend')} {raw[:120]}")

    st, body, raw = litellm_chat(S["key"], MODEL)
    check("R5 invocación 1 con key virtual → 200 REAL de MaaS",
          st == 200 and body.get("usage", {}).get("total_tokens", 0) > 0,
          f"HTTP {st}")
    st, body, raw = litellm_chat(S["key"], NOT_APPROVED_MODEL)
    check(f"R5 modelo NO aprobado ({NOT_APPROVED_MODEL}) → 403 del gateway real",
          st == 403, f"HTTP {st} {raw[:150]}")

    # =====================================================================
    print("=== R6: corte NATIVO de LiteLLM (max_budget agotado, sin control plane) ===",
          flush=True)
    spend_worker("stop")  # el corte nativo debe ser SOLO del gateway
    native_status, native_body = None, ""
    calls_ok = 1  # la invocación de R5 ya gastó
    for i in range(12):
        st, body, raw = litellm_chat(
            S["key"], MODEL,
            prompt="Escribe un ensayo largo y detallado sobre la historia del "
                   "ferrocarril en Costa Rica y su impacto económico.",
            max_tokens=1500)
        if st == 200:
            calls_ok += 1
            print(f"    [native] invocación {calls_ok} → 200 "
                  f"(tokens={body.get('usage', {}).get('total_tokens')})", flush=True)
            time.sleep(1)
            continue
        native_status, native_body = st, raw
        break
    check("R6 el gateway real CORTA por max_budget agotado (corte nativo, en vivo)",
          native_status is not None and native_status in (400, 402, 403, 422, 429)
          and "budget" in native_body.lower(),
          f"HTTP {native_status} {native_body[:180]}")
    st, info, raw = litellm_admin("GET", "/key/info", params={"key": S["key_hash"]})
    kinfo = _key_info_fields(info)
    key_spend = kinfo.get("spend")
    # LiteLLM chequea spend_actual < max ANTES de la llamada: la última
    # invocación puede completarse POR ENCIMA del max (0.0277 con max 0.02,
    # observado) — el corte llega en la SIGUIENTE. Ventana: hasta max + una
    # invocación grande (~$0.014).
    check("R6 /key/info: spend de la key ≈ max_budget (0.02 USD) tras el "
          "corte nativo (la última llamada puede excederlo)",
          st == 200 and key_spend is not None
          and 0.005 <= float(key_spend) <= 0.05,
          f"spend={key_spend} max={kinfo.get('max_budget')}")
    print(f"    [native] invocaciones 200 antes del corte: {calls_ok}; "
          f"spend de la key: ${key_spend}", flush=True)

    # =====================================================================
    print("=== R7: ingesta del spend worker contra /spend/logs/v2 del proxy real ===",
          flush=True)
    # El worker continuo vuelve a arrancar (docker exec necesita el
    # contenedor vivo); sus ciclos de 15 s pueden ingerir antes del --once
    # — la ingesta es idempotente y los checks de abajo comparan el ledger
    # contra los LOGS del gateway, no contra conteos absolutos.
    spend_worker("start")
    rc, out = spend_worker("once")
    check("R7 spend worker --once contra el proxy real → rc 0", rc == 0,
          out[:200])
    ledger = db("SELECT request_id, cost_base_cents, tokens_in, tokens_out "
                "FROM spend_ledger WHERE tenant_id = %s AND user_id = %s",
                (S["tenant_id"], S["worker_user_id"]))
    n_rows = len(ledger)
    total_base = sum(r[1] for r in ledger)
    # HALLAZGO real: el gateway también loguea los INTENTOS fallidos
    # (403/422, spend 0.0) — el ledger los conserva como telemetría
    # honesta. Una invocación barata puede redondear a 0¢ (p. ej.
    # $0.000543 → 0¢), así que la correspondencia se verifica contra los
    # LOGS de la key, no contra un conteo de filas con costo.
    check(f"R7 ledger: {n_rows} filas para el obrero (éxitos + intentos "
          f"fallidos del gateway), costo total {total_base}¢",
          n_rows >= calls_ok and total_base > 0,
          f"filas={n_rows} costo={total_base}¢")
    rows = db("SELECT DISTINCT model_name, mode FROM spend_ledger "
              "WHERE tenant_id = %s AND cost_base_cents > 0", (S["tenant_id"],))
    check("R7 ledger: modelo glm-5.2 (model_group público), modo central",
          [tuple(r) for r in rows] == [(MODEL, "central")], str(rows))

    # Fidelidad de la ingesta: cada fila del ledger == su log del proxy real.
    import datetime
    today = datetime.date.today()
    st, logs_raw, raw = litellm_admin(
        "GET", "/spend/logs/v2",
        params={"start_date": (today - datetime.timedelta(days=1)).isoformat(),
                "end_date": (today + datetime.timedelta(days=1)).isoformat(),
                "page": 1, "page_size": 100})
    check("R7 GET /spend/logs/v2 del proxy real → 200 con data",
          st == 200 and logs_raw.get("data"), f"HTTP {st} filas={len(logs_raw.get('data', []))}")
    # HALLAZGO real: el nombre público del modelo viaja en `model_group`
    # (`model` es el interno "openai/glm-5.2") y el hash de la key en
    # `api_key` (no existe `user_api_key_hash`); `spend_logs_metadata`
    # NO se estampa (null) — la resolución tenant/user es POR HASH.
    key_logs = {r.get("request_id"): r for r in logs_raw.get("data", [])
                if (r.get("api_key") or r.get("user_api_key_hash")
                    or r.get("token")) == S["key_hash"]}
    success_logs = [r for r in key_logs.values()
                    if float(r.get("spend") or 0) > 0]
    check(f"R7 el proxy real registró {calls_ok} invocaciones exitosas de "
          f"la key (spend > 0) + intentos fallidos a $0",
          len(success_logs) == calls_ok,
          f"exitosas={len(success_logs)} (esperadas {calls_ok}) "
          f"total_logs_key={len(key_logs)}")
    check("R7 TODOS los logs de la key quedaron en el ledger (éxitos + "
          "fallos; ingesta completa e idempotente)",
          n_rows == len(key_logs),
          f"ledger={n_rows} logs_key={len(key_logs)}")
    mismatch = []
    for rid, base_cents, tin, tout in ledger:
        log = key_logs.get(rid)
        if log is None:
            mismatch.append(f"{rid}: sin log")
            continue
        if round(float(log.get("spend") or 0) * 100) != base_cents:
            mismatch.append(f"{rid}: ledger {base_cents}¢ vs log "
                            f"{round(float(log.get('spend') or 0) * 100)}¢")
    check("R7 cada fila del ledger calca su log real (costo redondeado; "
          "tenant/user resueltos por hash — el proxy real no estampa "
          "spend_logs_metadata)",
          ledger and not mismatch,
          "; ".join(mismatch[:3]) or f"{len(ledger)} filas verificadas")
    logs = {r.get("request_id"): r for r in logs_raw.get("data", [])
            if (r.get("model_group") or r.get("model")) == MODEL}

    # Pricing REAL MaaS: el costo del log sigue la fórmula GLM-5.2 P1
    # (cached tokens viven en metadata.usage_object.prompt_tokens_details).
    price_errs = []
    for rid, log in logs.items():
        u_in = int(log.get("prompt_tokens") or log.get("tokens") or 0)
        u_out = int(log.get("completion_tokens") or 0)
        meta = log.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta or "{}")
        usage = meta.get("usage_object") or {}
        cached = int((usage.get("prompt_tokens_details") or {})
                     .get("cached_tokens") or 0)
        expected = (u_in - cached) * PRICE_IN + cached * PRICE_CACHED \
            + u_out * PRICE_OUT
        actual = float(log.get("spend") or 0)
        if u_out == 0 and actual == 0:
            continue  # intento fallido ($0) — no aplica la fórmula
        if expected <= 0 or abs(actual - expected) / expected > 0.05:
            price_errs.append(f"{rid}: ${actual:.6f} vs esperado "
                              f"${expected:.6f} (in={u_in} cached={cached} "
                              f"out={u_out})")
    check("R7 pricing REAL MaaS: spend del log = fórmula GLM-5.2 "
          "($1.40/M in, $4.40/M out, $0.26/M cache) ±5%",
          not price_errs, "; ".join(price_errs[:2]) or f"{len(logs)} logs verificados")

    # Pricing update en caliente: /reload/model_cost_map + una invocación más.
    st, body, raw = litellm_admin("POST", "/reload/model_cost_map")
    check("R7 POST /reload/model_cost_map → 200 (pricing refresh en caliente)",
          st == 200, f"HTTP {st} {raw[:150]}")

    rows = db("SELECT spent_cents FROM budget_counters WHERE level = 'user' "
              "AND entity_id = %s AND period_start = date_trunc('month', now())::date",
              (S["worker_user_id"],))
    spent = rows[0][0] if rows else 0
    check("R7 counter del obrero descontado (== suma del ledger, margen 0)",
          spent == total_base, f"counter={spent}¢ ledger={total_base}¢")

    # =====================================================================
    print("=== R8: corte del control plane → bloqueo por hash → 401 → notificación → unblock ===",
          flush=True)
    # budget por debajo del gasto (force, auditable) — el camino del corte
    new_budget = max(1, spent - 1)
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": new_budget, "force": True}, bearer=admin)
    check("R8 reducción bajo el gasto con force=true → 200 (auditable)",
          st == 200, f"HTTP {st} {raw[:150]}")
    spend_worker("once")
    rows = db("SELECT status FROM api_keys WHERE id = %s", (S["key_id"],))
    check("R8 la key queda BLOQUEADA (por hash) en el gateway real",
          rows[0][0] == "blocked", rows[0][0])
    st, body, raw = litellm_chat(S["key"], MODEL)
    check("R8 invocación con key bloqueada → 401 del gateway real",
          st == 401, f"HTTP {st} {raw[:120]}")
    st, body, raw = api("GET", "/api/me/notifications", bearer=worker)
    check("R8 notificación budget_cutoff al obrero",
          st == 200 and any(n["type"] == "budget_cutoff"
                            for n in body["notifications"]), raw[:200])
    st, me_b, raw = api("GET", "/api/budgets/me", bearer=worker)
    check("R8 /api/budgets/me: cutoff=true level=user",
          st == 200 and me_b["cutoff"] is True and me_b.get("cutoff_level") == "user",
          raw[:200])
    rows = db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'budget.cutoff'", (S["tenant_id"],))
    check("R8 audit budget.cutoff", rows[0][0] >= 1)

    # Recuperación: subir presupuesto → key rehabilitada (por hash) → 200.
    api("PUT", f"/api/budgets/dept/{S['dept_it']}", {"amount_cents": 30000},
        bearer=admin)
    st, body, raw = api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                        {"amount_cents": 20000, "force": True}, bearer=admin)
    check("R8 recuperación: subir presupuesto a $200 → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    spend_worker("once")
    rows = db("SELECT status FROM api_keys WHERE id = %s", (S["key_id"],))
    check("R8 la key se rehabilita tras subir el presupuesto", rows[0][0] == "active",
          rows[0][0])
    st, body, raw = litellm_chat(S["key"], MODEL)
    check("R8 invocación tras recuperación (post /reload/model_cost_map) → 200",
          st == 200, f"HTTP {st} {raw[:120]}")
    rows = db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'model.key_unblocked'", (S["tenant_id"],))
    check("R8 audit model.key_unblocked", rows[0][0] >= 1)

    # Audit del flujo completo + cadena íntegra.
    expected = {"tenant.provisioned", "model.approved", "model.key_issued",
                "budget.cutoff", "model.key_blocked", "model.key_unblocked"}
    rows = db("SELECT DISTINCT action FROM audit_log WHERE tenant_id = %s",
              (S["tenant_id"],))
    found = {r[0] for r in rows}
    check("R8 audit contiene las acciones del flujo", expected <= found,
          f"faltan: {sorted(expected - found)}" if not expected <= found
          else f"{len(found)} acciones")
    proc = subprocess.run(["docker", "exec", "trust-agent-api",
                           "verify-audit-chain", "verify", S["tenant_id"]],
                          capture_output=True, text=True, timeout=60)
    check("R8 CLI verify-audit-chain → exit 0, cadena íntegra",
          proc.returncode == 0 and "ok" in proc.stdout,
          proc.stdout.strip()[:150])

    # El worker continuo del stack vuelve a su régimen normal (15 s).
    spend_worker("start")

    # Estado (SIN el plaintext de la key — solo hash).
    S.pop("key", None)
    S.pop("ota", None)
    with open(args.state, "w") as f:
        json.dump(S, f, indent=2)

    print(f"\nRE-VALIDACIÓN LITELLM REAL: {PASSES} PASS, {len(FAILURES)} FAIL",
          flush=True)
    if FAILURES:
        print(f"FAIL — {FAILURES}", flush=True)
        return 1
    print("RE-VALIDACIÓN PASS — spend/corte/model-access/pricing contra el "
          "gateway REAL, con evidencia en vivo", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Evidence: users.mfa_enrolled syncs after inline TOTP enrollment (GAP-10.2).

Spec 015 v1.1.0 D-AUTH-11 / AC-AUTH-010 (task t_edbcface). Real flow against
the LIVE stack — nothing simulated (constitution I):

  1. Self-register a fresh user via POST /api/auth/register (public API).
  2. SQL: users.mfa_enrolled = false right after the INSERT.
  3. Real OIDC dance with acr_values=2 (the "Set up MFA" banner path):
     console /auth/login?org=<slug>&acr=2 → KC 26 login form → inline
     CONFIGURE_TOTP enrollment (RFC 6238 code computed here) → code with
     acr=2 → console callback → POST /api/auth/session mint (kind=admin).
  4. SQL: users.mfa_enrolled = true — the sync fired in the mint
     transaction (D-AUTH-11).
  5. GET /api/proxy/users with the minted cookies → the directory entry
     shows mfa_enrolled: true (the exact payload the Users page renders).
  6. GET /users → 200 HTML (the page that paints the MFA badge).

Usage:
  .venv/bin/python scripts/evidence_mfa_enrolled_sync.py <org-slug>
  # e.g. .venv/bin/python scripts/evidence_mfa_enrolled_sync.py qa-p2rev-223359

Requires: api :8000, console :3100, Keycloak :8080, PostgreSQL :55432 up
(docker compose), and allow_self_registration=true for the org.
"""
import hashlib
import hmac
import http.client
import json
import re
import struct
import sys
import time
import urllib.parse

import psycopg

API_HOST, API_PORT = "127.0.0.1", 8000
CONSOLE_HOST, CONSOLE_PORT = "127.0.0.1", 3100
KC_HOST, KC_PORT = "127.0.0.1", 8080
DB_HOST, DB_PORT = "127.0.0.1", 55432
DB_ADMIN_DSN = (
    f"host={DB_HOST} port={DB_PORT} user=postgres "
    "password=trustlocal dbname=trust_agent"
)

STAMP = time.strftime("%H%M%S")
PASSWORD = "Gap102Sync2026!x"

COOKIES: dict[str, str] = {}
failures: list[str] = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def fetch(host, port, path, *, method="GET", data=None, headers=None,
          follow=True, max_hops=8):
    """Manual cookie jar (KC cookies are Secure; a real browser accepts them
    on 127.0.0.1 — this replicates that, same as smoke_console.py)."""
    h = dict(headers or {})
    if COOKIES:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPConnection(host, port, timeout=25)
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
                             u.path + (f"?{u.query}" if u.query else ""),
                             method="GET" if status in (301, 302, 303) else method,
                             data=None, headers=headers, follow=True,
                             max_hops=max_hops - 1)
            return fetch(host, port, loc,
                         method="GET" if status in (301, 302, 303) else method,
                         data=None, headers=headers, follow=True,
                         max_hops=max_hops - 1)
    return status, body, hdrs, path


def form_action(html):
    return re.search(r'action="([^"]+)"', html).group(1).replace("&amp;", "&")


def form_fields_of(html):
    out = {}
    for tag in re.findall(r"<input[^>]*>", html):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n and v:
            out[n.group(1)] = v.group(1)
    return out


def totp_secret_of(html):
    """TOTP secret from the KC enrollment page (raw mixed-case alphanum —
    HMAC key is the raw UTF-8 bytes, verified against KC 26)."""
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
    raise ValueError("TOTP secret not found on the enrollment page")


def totp(secret, counter=None):
    key = secret.encode("utf-8")
    if counter is None:
        counter = int(time.time()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6
    return str(code).zfill(6)


def kc_path(url):
    return url.split(f"{KC_HOST}:{KC_PORT}")[-1]


def sql_mfa_flag(email):
    with psycopg.connect(DB_ADMIN_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT u.email, u.mfa_enrolled, u.status, t.slug "
            "FROM users u JOIN tenants t ON t.id = u.tenant_id "
            "WHERE u.email = %s", (email,))
        row = cur.fetchone()
        return row


def main(org):
    email = f"gap102-{STAMP}@{org}.test"

    # --- 1) Self-registration (public API, real) ---------------------------
    payload = json.dumps({
        "org": org, "email": email,
        "password": PASSWORD, "display_name": "Gap102 Sync",
    }).encode()
    st, body, _, _ = fetch(API_HOST, API_PORT, "/api/auth/register",
                           method="POST", data=payload,
                           headers={"Content-Type": "application/json"})
    check("POST /api/auth/register → 201 (self-registration real)",
          st == 201, f"HTTP {st} {body[:200]}")

    # --- 2) Column is false right after the INSERT ------------------------
    row = sql_mfa_flag(email)
    check("SQL tras INSERT: users.mfa_enrolled = false (estado pre-fix)",
          row is not None and row[1] is False, f"row={row}")

    # --- 3) Real dance with acr=2: inline TOTP enrollment + mint ----------
    for k in list(COOKIES):
        COOKIES.pop(k)
    st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                              f"/auth/login?org={org}&acr=2", follow=False)
    loc = hdrs.get("location", "")
    check("GET /auth/login?org&acr=2 → redirect al authorize con acr_values=2",
          st in (301, 302, 303, 307)
          and "/protocol/openid-connect/auth" in loc
          and "acr_values=2" in loc, f"HTTP {st} loc={loc[:160]}")

    st, body, _, _ = fetch(KC_HOST, KC_PORT, kc_path(loc))
    check("login form del realm servido (tema trust-agent)",
          st == 200 and 'action="' in body, f"HTTP {st}")
    assert 'action="' in body, "no login form from the realm"
    action = form_action(body)
    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(action), method="POST",
                              data=urllib.parse.urlencode({
                                  "username": email, "password": PASSWORD,
                                  "credentialId": ""}).encode(),
                              follow=False)
    loc = hdrs.get("location", "")
    hops = 0
    secret = None
    used_counter = None
    while "code=" not in (loc or "") and hops < 8:
        if loc and st in (301, 302, 303, 307):
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(loc),
                                      follow=False)
            loc = hdrs.get("location", "")
        elif st == 200 and 'action="' in body:
            act = form_action(body)
            if 'name="totpSecret"' in body:
                secret = totp_secret_of(body)
                used_counter = int(time.time()) // 30
                print(f"    [dance] enrolamiento TOTP inline: secret={secret} "
                      f"code={totp(secret, used_counter)}")
                st, body, hdrs, _ = fetch(
                    KC_HOST, KC_PORT, kc_path(act), method="POST",
                    data=urllib.parse.urlencode({
                        "totp": totp(secret, used_counter),
                        "totpSecret": secret, "mode": "totp",
                        "userLabel": "gap102-evidence",
                        "credentialId": ""}).encode(),
                    follow=False)
                loc = hdrs.get("location", "")
            elif 'name="otp"' in body or 'name="totp"' in body:
                code_field = "otp" if 'name="otp"' in body else "totp"
                fields = form_fields_of(body)
                counter = int(time.time()) // 30
                if counter == used_counter:
                    counter += 1  # enrollment code is not reusable
                fields[code_field] = totp(secret, counter)
                st, body, hdrs, _ = fetch(
                    KC_HOST, KC_PORT, kc_path(act), method="POST",
                    data=urllib.parse.urlencode(fields).encode(),
                    follow=False)
                loc = hdrs.get("location", "")
            else:
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act),
                                          method="POST", data=b"",
                                          follow=False)
                loc = hdrs.get("location", "")
        else:
            break
        hops += 1
    check("baile KC con acr=2 → code (enrolamiento TOTP real completado)",
          "code=" in (loc or ""), f"HTTP {st} loc={(loc or '')[:160]}")

    # Callback del console: canje + mint (kind=admin) + cookies.
    st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                              loc if loc.startswith("/") else
                              loc.split(f"{CONSOLE_HOST}:{CONSOLE_PORT}")[-1],
                              follow=False)
    final = hdrs.get("location", "")
    check("callback del console → 307 /overview (sesión mintada, acr=2)",
          st in (301, 302, 303, 307) and "/overview" in final,
          f"HTTP {st} loc={final[:160]}")

    # --- 4) SQL: the sync fired in the mint transaction -------------------
    time.sleep(0.5)
    row = sql_mfa_flag(email)
    check("SQL tras el mint: users.mfa_enrolled = true (D-AUTH-11 sync)",
          row is not None and row[1] is True, f"row={row}")

    # --- 5) Directory payload (what the Users page renders) ---------------
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, "/api/proxy/users")
    users = json.loads(body).get("users", []) if st == 200 else []
    entry = next((u for u in users if u.get("email") == email), None)
    check("GET /api/proxy/users → entrada del usuario con mfa_enrolled=true",
          st == 200 and entry is not None
          and entry.get("mfa_enrolled") is True,
          f"HTTP {st} entry={entry}")

    # --- 6) The Users page itself (renders the badge from that payload) ---
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, "/users")
    check("GET /users → 200 HTML (página del directorio, badge MFA)",
          st == 200, f"HTTP {st}")

    # --- 7) Monotonic live check: password-only mint (acr=1) keeps true ---
    # S5 semantics (skill keycloak26-auth-ux): acr_values=1 with TOTP
    # enrolled → password-only → acr:"1" → mint WITHOUT MFA evidence. The
    # column must stay true (D-AUTH-11: high-water mark, never cleared).
    for k in list(COOKIES):
        if not k.startswith("ta_"):
            COOKIES.pop(k)
    st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                              f"/auth/login?org={org}", follow=False)
    loc = hdrs.get("location", "")
    check("segundo baile acr=1 (password-only) → authorize con acr_values=1",
          "acr_values=1" in loc, f"loc={loc[:160]}")
    st, body, _, _ = fetch(KC_HOST, KC_PORT, kc_path(loc))
    assert 'action="' in body, "no login form (2nd dance)"
    action = form_action(body)
    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(action), method="POST",
                              data=urllib.parse.urlencode({
                                  "username": email, "password": PASSWORD,
                                  "credentialId": ""}).encode(),
                              follow=False)
    loc = hdrs.get("location", "")
    hops = 0
    while "code=" not in (loc or "") and hops < 6:
        if loc and st in (301, 302, 303, 307):
            st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(loc),
                                      follow=False)
            loc = hdrs.get("location", "")
        else:
            break
        hops += 1
    check("password-only → code SIN formulario OTP (acr=1)",
          "code=" in (loc or ""), f"HTTP {st} loc={(loc or '')[:160]}")
    st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                              loc if loc.startswith("/") else
                              loc.split(f"{CONSOLE_HOST}:{CONSOLE_PORT}")[-1],
                              follow=False)
    final = hdrs.get("location", "")
    check("callback del 2º baile → 307 /overview (mint sin MFA)",
          st in (301, 302, 303, 307) and "/overview" in final,
          f"HTTP {st} loc={final[:160]}")
    row = sql_mfa_flag(email)
    check("SQL tras mint sin evidencia MFA: mfa_enrolled SIGUE true (monotónico)",
          row is not None and row[1] is True, f"row={row}")
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, "/api/proxy/me")
    me = json.loads(body) if st == 200 else {}
    check("whoami del 2º baile: mfa_verified=false (sesión password-only)",
          st == 200 and me.get("mfa_verified") is False, f"HTTP {st} {body[:160]}")

    print()
    if failures:
        print(f"EVIDENCE FAIL — {len(failures)} fallo(s): {failures}")
        return 1
    print("EVIDENCE PASS — GAP-10.2 convergido con flujo real: registro → "
          "enrolamiento TOTP inline (acr=2) → mint → users.mfa_enrolled=true "
          "en SQL → directorio (/api/users + página /users) refleja el estado.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

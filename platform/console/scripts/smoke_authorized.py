#!/usr/bin/env python3
"""Smoke autorizado del Admin Console (spec 009 T18, camino MFA positivo).

CONTEXTO (hallazgo real, 2026-10-24): Keycloak 26 NO emite claim `amr` en el
id_token tras un login real password+OTP (verificado: amr=None, acr=1), por
lo que la puerta MFA del control plane (`_amr_mfa` en auth/router.py y
auth/service.py) rechaza TODO login real de Keycloak. Es un bug del control
plane (tarea dev derivada). Mientras tanto, el camino MFA-positivo se
verifica con el MISMO patrón que usa la suite e2e del plataforma: un IdP de
test que sirve JWKS real por HTTP y firma JWT RS256 con amr=["mfa"] — el
control plane verifica la firma de verdad contra ese JWKS (PyJWKClient).

Todo lo demás es 100% real: sesión admin en PostgreSQL (RLS activa), datos
del tenant demo-org, proxy BFF del console, CSRF del control plane.

Uso: .venv/bin/python console/scripts/smoke_authorized.py
"""
import http.client
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

API_HOST, API_PORT = "127.0.0.1", 8000  # API canónico del compose (antes 8001, entorno ad-hoc retirado)
CONSOLE_HOST, CONSOLE_PORT = "127.0.0.1", 3100
IDP_PORT = 8908
ISSUER = f"http://127.0.0.1:{IDP_PORT}/realms/demo-org"
TENANT_ID = "8142bc02-0c21-4b5b-b409-a9ed492ea7d0"  # demo-org provisionado real (provider API)
ADMIN_SUB = "32239d41-3392-4d8c-b00c-2eb80e12dc6d"
ADMIN_EMAIL = "alex.morgan@demo-org.test"
KID = "console-smoke-kid"

COOKIES: dict[str, str] = {}


def fetch(host: str, port: int, path: str, *, method="GET", data=None,
          headers=None, follow=False):
    h = dict(headers or {})
    if COOKIES:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
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
    if follow and status in (301, 302, 303, 307) and hdrs.get("location"):
        loc = hdrs["location"]
        if loc.startswith("http"):
            u = urllib.parse.urlparse(loc)
            return fetch(u.hostname, u.port or 80, u.path + ("?" + u.query if u.query else ""))
        return fetch(host, port, loc)
    return status, body, hdrs


def start_idp(jwks: dict) -> None:
    """IdP de test (patrón conftest._IdP): JWKS real por HTTP."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.endswith("/protocol/openid-connect/certs"):
                payload = json.dumps(jwks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", IDP_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    # --- IdP de test con JWKS real (patrón e2e del plataforma) -------------
    # La clave se persiste: el control plane cachea el JWKS 5 min por issuer
    # (jwks.py lifespan=300); reutilizar la clave evita fallos por caché.
    import os
    key_path = "/tmp/console_smoke_idp_key.pem"
    from cryptography.hazmat.primitives import serialization
    if os.path.exists(key_path):
        key = serialization.load_pem_private_key(
            open(key_path, "rb").read(), password=None)
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    start_idp({"keys": [jwk]})

    now = int(time.time())
    id_token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "trust-agent-web",
            "sub": ADMIN_SUB,
            "tenant_id": TENANT_ID,
            "exp": now + 300,
            "iat": now,
            "amr": ["mfa"],
        },
        key,
        algorithm="RS256",
        headers={"kid": KID},
    )

    # --- Mint de sesión admin (lo que hace el callback del console) --------
    st, body, hdrs = fetch(API_HOST, API_PORT, "/api/auth/session", method="POST",
                           data=json.dumps({"id_token": id_token, "kind": "admin"}).encode())
    minted = json.loads(body) if st == 201 else {}
    check("POST /api/auth/session (kind=admin) → 201", st == 201, f"HTTP {st} {body[:160]}")
    session_cookie = None
    for raw in hdrs.get("set-cookie", "").split(","):
        pass
    # set-cookie llega como header único (posible múltiple): releer del dict
    # (fetch ya guardó trust_session en COOKIES si vino)
    session_cookie = COOKIES.get("trust_session")
    check("la sesión llega como Set-Cookie trust_session (sesión admin)", bool(session_cookie),
          str(sorted(COOKIES)))
    check("el body trae refresh_token y csrf_token (session_token=null en admin)",
          bool(minted.get("refresh_token")) and bool(minted.get("csrf_token")) and minted.get("session_token") is None,
          body[:200])

    # --- Cookies del console (custodia idéntica al callback; prefijo por app F2)
    COOKIES.clear()
    COOKIES["ta_console_session"] = session_cookie
    COOKIES["ta_console_refresh"] = minted["refresh_token"]
    COOKIES["ta_console_csrf"] = minted["csrf_token"]

    def proxy(path, method="GET", payload=None, extra_headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        h = {"Content-Type": "application/json", **(extra_headers or {})}
        return fetch(CONSOLE_HOST, CONSOLE_PORT, f"/api/proxy{path}", method=method, data=data, headers=h)

    # --- Datos reales por el proxy ------------------------------------------
    st, body, _ = proxy("/me")
    me = json.loads(body) if st == 200 else {}
    check("GET /api/proxy/me → 200 whoami real (demo-org)", st == 200 and me.get("email") == ADMIN_EMAIL,
          f"HTTP {st} {body[:160]}")
    check("whoami: rol org_admin + MFA verificada",
          any(r["role_key"] == "org_admin" for r in me.get("roles", [])) and me.get("mfa_verified") is True,
          body[:200])

    st, body, _ = proxy("/budgets/overview")
    ov = json.loads(body) if st == 200 else {}
    initial_budget = ov.get("organization", {}).get("budget_cents")
    check("GET /api/proxy/budgets/overview → 200 datos reales",
          st == 200 and isinstance(initial_budget, int),
          f"HTTP {st} {body[:200]}")

    st, body, _ = proxy("/audit/events?limit=5")
    au = json.loads(body) if st == 200 else {}
    check("GET /api/proxy/audit/events → 200 con eventos reales", st == 200 and au.get("total", 0) >= 1,
          f"HTTP {st} {body[:160]}")

    st, body, _ = proxy("/policies?level=org")
    check("GET /api/proxy/policies?level=org → 200 (org_admin)", st == 200, f"HTTP {st} {body[:160]}")

    st, body, _ = proxy("/models/access")
    check("GET /api/proxy/models/access → 200 matriz real", st == 200 and "models" in body, f"HTTP {st} {body[:160]}")

    st, body, _ = proxy("/devices")
    check("GET /api/proxy/devices → 200 (devices.manage)", st == 200, f"HTTP {st} {body[:160]}")

    st, body, _ = proxy("/vault/credentials")
    check("GET /api/proxy/vault/credentials → 200 metadata", st == 200, f"HTTP {st} {body[:160]}")

    st, body, _ = proxy("/agents")
    check("GET /api/proxy/agents → 200", st == 200, f"HTTP {st} {body[:160]}")

    # --- CSRF: negativo y positivo (lo valida el control plane) ------------
    st, body, _ = proxy("/budgets/org", method="PUT", payload={"amount_cents": 50000, "period": "monthly"})
    err = (json.loads(body) or {}).get("error", {})
    check("mutación sin X-CSRF-Token → 403 csrf_required",
          st == 403 and err.get("reason") == "csrf_required", f"HTTP {st} {body[:160]}")

    st, body, _ = proxy("/budgets/org", method="PUT",
                        payload={"amount_cents": (initial_budget or 0) + 10000, "period": "monthly"},
                        extra_headers={"X-CSRF-Token": COOKIES["ta_console_csrf"]})
    check("mutación con X-CSRF-Token → 200 (presupuesto actualizado)", st == 200, f"HTTP {st} {body[:200]}")

    st, body, _ = proxy("/budgets/overview")
    ov = json.loads(body) if st == 200 else {}
    check("el nuevo presupuesto se refleja en el overview",
          ov.get("organization", {}).get("budget_cents") == (initial_budget or 0) + 10000,
          body[:200])

    # --- Contratos spec 012 (antes degradación honesta 404, hoy reales) ------
    st, body, _ = proxy("/users")
    users = (json.loads(body) or {}).get("users") if st == 200 else None
    check("GET /api/proxy/users → 200 directorio real (spec 012 activada)",
          st == 200 and isinstance(users, list), f"HTTP {st} {body[:160]}")
    st, body, _ = proxy("/branding")
    branding = json.loads(body) if st == 200 else {}
    check("GET /api/proxy/branding → 200 branding real (spec 012 activada)",
          st == 200 and branding.get("product_name") and
          branding.get("source") in ("provider", "tenant"),
          f"HTTP {st} {body[:160]}")

    # --- Páginas HTML con sesión ---------------------------------------------
    for page in ("/overview", "/budgets", "/models", "/devices", "/credentials",
                 "/agents", "/audit", "/users", "/branding", "/organization", "/departments"):
        st, body, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, page)
        check(f"GET {page} con sesión → 200 HTML", st == 200 and "Governance" in body, f"HTTP {st}")

    # --- Logout (revoca en el control plane + limpia cookies) ---------------
    st, body, hdrs = fetch(CONSOLE_HOST, CONSOLE_PORT, "/auth/logout", method="POST", follow=False)
    check("POST /auth/logout → redirect a /login", st in (301, 302, 303, 307) and "/login" in hdrs.get("location", ""),
          f"HTTP {st}")
    check("cookies de sesión limpiadas tras logout",
          not COOKIES.get("ta_console_session") and not COOKIES.get("ta_console_csrf"), str(sorted(COOKIES)))

    print()
    if failures:
        print(f"SMOKE FAIL — {len(failures)} fallo(s): {failures}")
        return 1
    print("SMOKE PASS — camino autorizado real: sesión admin PostgreSQL + proxy BFF + CSRF + 11 páginas + logout")
    return 0


if __name__ == "__main__":
    sys.exit(main())

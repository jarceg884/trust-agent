#!/usr/bin/env python3
"""Sesión admin REAL para la evidencia del header human-facing (t_b14cbefb).

Reutiliza el baile PKCE S256 + TOTP RFC 6238 de scripts/e2e_final_live.py
contra el Keycloak 26 vivo (patrón de la evidencia hermes-download):

  1. Login REAL del admin de "hermes-dl" (TOTP ya enrolado — secret de la
     sesión de t_360a752e) → id_token con acr=2.
  2. Mint de sesión admin (POST /api/auth/session) → cookie trust_session.
  3. Verificación whoami: el campo NUEVO tenant_name debe exponer
     tenants.name ("Hermes Download Evidence Corp").
  4. display_name del admin vía API REAL del directorio
     (PUT /api/users/{id} + X-CSRF-Token) — el cambio que un org_admin
     haría desde la página Users; audit real incluido.
  5. Re-verificación whoami con el nombre poblado.
  6. Guarda la sesión en JSON para inyectar las cookies del BFF (:3100).

Nada simulado: usuario, TOTP, sesión, whoami y UPDATE son reales.
"""
import http.client
import json
import sys

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # noqa: E402

API_HOST, API_PORT = "127.0.0.1", 8000
SLUG = "hermes-dl"
ADMIN_EMAIL = "admin@hermes-dl.test"
ADMIN_PASSWORD = "HermesDl2026!pass"
ADMIN_DISPLAY_NAME = "Ana Torres"
# TOTP secret capturado en el enrolamiento real de la evidencia t_360a752e.
TOTP_SECRET = json.load(open(
    "/root/.hermes/kanban/boards/trust-agent/workspaces/t_360a752e/session.json"
))["totp_secret"]
OUT = "/root/.hermes/kanban/boards/trust-agent/workspaces/t_b14cbefb/session.json"

with open("/root/trust-agent/.env") as fh:
    for line in fh:
        if line.startswith("TRUST_PROVIDER_TOKEN="):
            e2e.PROVIDER_TOKEN = line.partition("=")[2].strip()
e2e.SLUG = SLUG
e2e.REDIRECT_URI = "http://127.0.0.1:3100/auth/callback"


def req(method, path, *, payload=None, session=None, csrf=None):
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=20)
    headers = {"Content-Type": "application/json"}
    if session:
        headers["Cookie"] = f"trust_session={session}"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    conn.request(method, path, body=json.dumps(payload).encode() if payload else None,
                 headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    set_cookies = resp.msg.get_all("Set-Cookie") or []
    conn.close()
    return resp.status, (json.loads(raw) if raw and resp.status != 204 else None), set_cookies


def main() -> int:
    # 1) Login REAL PKCE + TOTP (usuario ya enrolado).
    id_token, acr = e2e.pkce_login(
        ADMIN_EMAIL, ADMIN_PASSWORD,
        totp_state={"secret": TOTP_SECRET}, acr_values="2")
    assert id_token, "login PKCE falló (sin code)"
    print(f"[login] id_token obtenido, acr={acr}")

    # 2) Mint de sesión admin (MFA verificada).
    st, body, set_cookies = req("POST", "/api/auth/session",
                                payload={"id_token": id_token, "kind": "admin"})
    assert st == 201, f"mint HTTP {st}: {body}"
    session_cookie = None
    for c in set_cookies:
        if c.startswith("trust_session="):
            session_cookie = c.split(";", 1)[0].partition("=")[2]
    assert session_cookie, f"sin trust_session en Set-Cookie: {set_cookies}"
    assert body["mfa_verified"] is True, f"sesión sin MFA: {body}"
    csrf = body["csrf_token"]
    print(f"[session] minted, mfa_verified=true")

    # 3) whoami con el campo NUEVO tenant_name (backend desplegado).
    st, me, _ = req("GET", "/api/me", session=session_cookie)
    assert st == 200, f"/api/me HTTP {st}"
    assert me["tenant_slug"] == SLUG, me
    assert me.get("tenant_name") == "Hermes Download Evidence Corp", (
        f"tenant_name inesperado: {me.get('tenant_name')!r}")
    print(f"[whoami] tenant_name={me['tenant_name']!r} (nuevo campo OK)")
    print(f"[whoami] display_name actual={me['display_name']!r}")

    # 4) display_name vía API REAL del directorio (audit real).
    st, upd, _ = req("PUT", f"/api/users/{me['user_id']}",
                     payload={"display_name": ADMIN_DISPLAY_NAME},
                     session=session_cookie, csrf=csrf)
    assert st == 200, f"PUT /api/users HTTP {st}: {upd}"
    print(f"[directory] PUT display_name={ADMIN_DISPLAY_NAME!r} → 200")

    # 5) Re-verificación whoami.
    st, me2, _ = req("GET", "/api/me", session=session_cookie)
    assert st == 200 and me2["display_name"] == ADMIN_DISPLAY_NAME, me2
    assert me2["tenant_name"] == "Hermes Download Evidence Corp"
    print(f"[whoami] display_name={me2['display_name']!r} tenant_name="
          f"{me2['tenant_name']!r} — todo verificado")

    out = {
        "slug": SLUG,
        "admin_email": ADMIN_EMAIL,
        "acr": acr,
        "trust_session": session_cookie,
        "csrf_token": csrf,
        "refresh_token": body["refresh_token"],
        "expires_at": body["expires_at"],
    }
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"[ok] sesión guardada en {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

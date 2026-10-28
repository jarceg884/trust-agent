#!/usr/bin/env python3
"""Sesión admin REAL para la evidencia de Hermes Download (t_360a752e).

Reutiliza el baile PKCE S256 + TOTP RFC 6238 de scripts/e2e_final_live.py
contra el Keycloak 26 vivo (patrón de la evidencia spec 021):

  1. Proveedor real provisiona el tenant "hermes-dl" (POST /api/admin/tenants).
  2. Login REAL del admin (one-time password → UPDATE_PASSWORD → CONFIGURE_TOTP
     con secret capturado → 2º factor OTP) → id_token con acr=2.
  3. Mint de sesión admin en el control plane (POST /api/auth/session) —
     captura la cookie trust_session + csrf_token.
  4. Guarda todo en JSON para inyectar las cookies del BFF en el navegador
     (ta_console_session/refresh/csrf) y usar la UI real del console (:3100).

Nada simulado: tenant, usuario, TOTP y sesión son reales y viven en
PostgreSQL + Keycloak.
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
OUT = "/root/.hermes/kanban/boards/trust-agent/workspaces/t_360a752e/session.json"

# Token del proveedor REAL (el que valida el control plane vivo) y cliente
# OIDC del console — redirect registrado en Keycloak.
with open("/root/trust-agent/.env") as fh:
    for line in fh:
        if line.startswith("TRUST_PROVIDER_TOKEN="):
            e2e.PROVIDER_TOKEN = line.partition("=")[2].strip()
e2e.SLUG = SLUG
e2e.REDIRECT_URI = "http://127.0.0.1:3100/auth/callback"


def mint_admin_session(id_token: str) -> dict:
    """POST /api/auth/session kind=admin → cookie trust_session + csrf."""
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=20)
    conn.request(
        "POST", "/api/auth/session",
        body=json.dumps({"id_token": id_token, "kind": "admin"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    set_cookies = resp.msg.get_all("Set-Cookie") or []
    conn.close()
    assert resp.status == 201, f"mint HTTP {resp.status}: {raw[:300]}"
    session_cookie = None
    for c in set_cookies:
        if c.startswith("trust_session="):
            session_cookie = c.split(";", 1)[0].partition("=")[2]
    body = json.loads(raw)
    assert session_cookie, f"sin trust_session en Set-Cookie: {set_cookies}"
    assert body["mfa_verified"] is True, f"sesión sin MFA: {body}"
    return {
        "trust_session": session_cookie,
        "csrf_token": body["csrf_token"],
        "refresh_token": body["refresh_token"],
        "expires_at": body["expires_at"],
    }


def main() -> int:
    # 1) Proveedor real provisiona el tenant.
    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Hermes Download Evidence Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0, "model_plan": "standard", "slug": SLUG})
    if st == 409:  # ya existe de un intento anterior: re-usar
        print(f"[reprovision] tenant {SLUG} ya existe (HTTP 409) — se reutiliza")
        ota = None
        tenant_id = e2e.db("SELECT id FROM tenants WHERE slug = %s", (SLUG,))[0][0]
    else:
        assert st == 201, f"provision HTTP {st}: {raw[:300]}"
        tenant_id = body["tenant"]["id"]
        ota = body["one_time_activation"]["one_time_password"]
        print(f"[provision] tenant {SLUG} creado: {tenant_id}")

    # 2) Login REAL PKCE + TOTP (primer login: UPDATE_PASSWORD + CONFIGURE_TOTP).
    totp_state = {}
    if ota is not None:
        id_token, acr = e2e.pkce_login(
            ADMIN_EMAIL, ota, new_password=ADMIN_PASSWORD, totp_state=totp_state)
    else:
        id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD,
                                       totp_state=totp_state)
    assert id_token, "login PKCE falló (sin code)"
    print(f"[login] id_token obtenido, acr={acr}")

    # 3) Mint de sesión admin (MFA verificada).
    session = mint_admin_session(id_token)
    print(f"[session] minted, mfa_verified=true, expires_at={session['expires_at']}")

    # 4) Verificación whoami con la cookie (como la enviará el proxy BFF).
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=20)
    conn.request("GET", "/api/me", headers={
        "Cookie": f"trust_session={session['trust_session']}"})
    resp = conn.getresponse()
    me = json.loads(resp.read().decode())
    conn.close()
    assert resp.status == 200, f"/api/me HTTP {resp.status}"
    roles = [r["role_key"] for r in me["roles"]]
    print(f"[whoami] {me['email']} roles={roles} mfa={me['mfa_verified']} "
          f"tenant_slug={me.get('tenant_slug')}")
    assert "org_admin" in roles

    # 5) Estado REAL del endpoint de gestión (spec 023, t_526030f3):
    #    debe ser 404 hoy — la UI degrada con banner honesto.
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=20)
    conn.request("GET", "/api/download-link", headers={
        "Cookie": f"trust_session={session['trust_session']}"})
    resp = conn.getresponse()
    dl_status = resp.status
    resp.read()
    conn.close()
    print(f"[download-link] GET /api/download-link → HTTP {dl_status} "
          "(404 esperado: spec 023 en vuelo)")

    out = {
        "slug": SLUG,
        "tenant_id": str(tenant_id),
        "admin_email": ADMIN_EMAIL,
        "acr": acr,
        "totp_secret": totp_state.get("secret"),
        "download_link_status": dl_status,
        **session,
    }
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"[ok] sesión guardada en {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

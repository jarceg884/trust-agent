#!/usr/bin/env python3
"""E2E t_42e23b01 — provisioning + sesión admin real para la verificación
de page titles dinámicos y refresh del shell sin reload (spec 028
REQ-BRD2-007/008, AC-028-7).

Nada simulado: provisiona un tenant fresco contra la API :8000, login REAL
PKCE S256 contra Keycloak :8080 (OTA → password), y minta la sesión ADMIN
real (POST /api/auth/session kind=admin → Set-Cookie trust_session).

Salida JSON por stdout: slug, tenant_id, credenciales y las cookies del
console (ta_console_session/refresh/csrf) para inyectarlas en el navegador.
"""
import base64
import http.client
import importlib.util
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path("/opt/trust-agent/platform/scripts")
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

STAMP = time.strftime("%H%M%S")
SLUG = f"t42e2-{STAMP}"
rv.SLUG = SLUG
ADMIN_EMAIL = f"admin@{SLUG}.test"
ADMIN_PW = "T42e2Admin2026!"


def mint_admin(id_token):
    """POST /api/auth/session kind=admin → body + cookie trust_session."""
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=30)
    conn.request("POST", "/api/auth/session",
                 body=json.dumps({"id_token": id_token, "kind": "admin"}).encode(),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode()
    hdrs = resp.getheaders()
    conn.close()
    trust_session = None
    for h in hdrs:
        if h[0].lower() == "set-cookie" and h[1].startswith("trust_session="):
            trust_session = h[1].split("=", 1)[1].split(";", 1)[0]
    return resp.status, json.loads(raw) if raw else {}, trust_session


def main():
    out = {"slug": SLUG}
    st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "T42E2 Branding Titles Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0,
        "model_plan": ["glm-5.2"],
        "slug": SLUG})
    assert st == 201, f"provision HTTP {st}: {raw[:200]}"
    out["tenant_id"] = body.get("tenant_id", body.get("tenant", {}).get("id"))
    ota = body["one_time_activation"]["one_time_password"]
    print(f"[PASS] tenant {SLUG} provisionado ({out['tenant_id']})", flush=True)

    id_token, acr = rv.pkce_login(ADMIN_EMAIL, ota, new_password=ADMIN_PW)
    assert id_token is not None, "login PKCE falló"
    print(f"[PASS] login PKCE real admin acr={acr}", flush=True)

    st, minted, trust_session = mint_admin(id_token)
    assert st == 201, f"mint admin HTTP {st}: {minted}"
    assert trust_session, "mint admin sin cookie trust_session"
    print(f"[PASS] sesión admin minteada (mfa_verified={minted.get('mfa_verified')})",
          flush=True)

    out.update({
        "admin_email": ADMIN_EMAIL,
        "admin_password": ADMIN_PW,
        "trust_session": trust_session,
        "refresh_token": minted["refresh_token"],
        "csrf_token": minted["csrf_token"],
    })
    print("JSON:" + json.dumps(out))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Aislamiento del fallo 400 del authorize (diagnóstico del probe T2).

Etapa 1: realm + cliente + usuario, SIN acr.loa.map y SIN tocar el flujo
         → authorize pelado (sin acr_values) debe dar 200 (login page).
Etapa 2: + acr.loa.map {"1":1,"2":2} → authorize con acr_values=1.
Etapa 3: + reestructuración del flujo (D-AUTH-06) → authorize acr=1 y acr=2.

Uso: .venv/bin/python scripts/probe_isolate.py
"""
import sys

sys.path.insert(0, "scripts")

import probe_loa_flow as P  # noqa: E402


def try_authorize(label: str, acr: str | None) -> None:
    b = P.Browser()
    status, body, hdrs, _v = P.start_auth(b, acr)
    kind = P.page_kind(body)
    title = P.page_title(body)
    loc = hdrs.get("location", "")
    print(f"[{label}] authorize acr={acr!r} -> HTTP {status} kind={kind} "
          f"title={title!r} loc={loc[:100]}")
    if status != 200:
        import re
        msg = re.search(r'instruction">([^<]+)', body)
        print(f"    error page: {msg.group(1) if msg else body[:200]!r}")


def main() -> int:
    print("Aislamiento del 400 en authorize — KC 26.2.5 vivo\n")
    # Etapa 1: realm pelado (sin map, sin flujo)
    P.admin("DELETE", f"/admin/realms/{P.REALM}")
    P.admin_ok("POST", "/admin/realms", {"realm": P.REALM, "enabled": True})
    P.admin_ok("POST", f"/admin/realms/{P.REALM}/clients", {
        "clientId": P.CLIENT_ID, "enabled": True, "protocol": "openid-connect",
        "publicClient": True, "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "redirectUris": [P.REDIRECT_URI],
        "attributes": {"pkce.code.challenge.method": "S256"},
    })
    P.admin_ok("POST", f"/admin/realms/{P.REALM}/users", {
        "username": P.EMAIL, "enabled": True, "email": P.EMAIL,
        "emailVerified": True, "firstName": "Probe", "lastName": "User",
    })
    raw = P.admin_ok("GET",
                     f"/admin/realms/{P.REALM}/users?username={P.EMAIL}&exact=true")
    uid = __import__("json").loads(raw)[0]["id"]
    P.admin_ok("PUT", f"/admin/realms/{P.REALM}/users/{uid}/reset-password",
               {"type": "password", "value": P.PASSWORD, "temporary": False})
    try_authorize("etapa1-realm-pelado", None)

    # Etapa 2: + acr.loa.map
    rep = __import__("json").loads(P.admin_ok("GET", f"/admin/realms/{P.REALM}"))
    rep["attributes"] = {**(rep.get("attributes") or {}),
                         "acr.loa.map": P.ACR_MAP}
    P.admin_ok("PUT", f"/admin/realms/{P.REALM}", rep)
    try_authorize("etapa2-con-acr-map", "1")
    try_authorize("etapa2-con-acr-map", None)

    # Etapa 3: + flujo reestructurado
    P.restructure_flow()
    try_authorize("etapa3-flujo-nuevo", "1")
    try_authorize("etapa3-flujo-nuevo", "2")
    try_authorize("etapa3-flujo-nuevo", None)

    P.admin("DELETE", f"/admin/realms/{P.REALM}")
    print("\n[cleanup] realm eliminado")
    return 0


if __name__ == "__main__":
    sys.exit(main())

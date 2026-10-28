#!/usr/bin/env python3
"""Evidencia EN VIVO del fix t_378503a1 contra el stack real (127.0.0.1:8000).

Flujo 100% real (nada simulado):
  1. Proveedor real provisiona el tenant "uuid-fix" (POST /api/admin/tenants).
  2. Login REAL PKCE S256 + TOTP RFC 6238 contra el Keycloak 26 vivo
     (reutiliza el baile de scripts/e2e_final_live.py) -> id_token acr=2.
  3. Mint de sesión (POST /api/auth/session, kind=desktop) -> Bearer token.
  4. REPRO del bug: PUT /api/budgets/dept/not-a-uuid -> debe ser 422
     validation_failed (antes del fix: 500 internal_error).
  5. Control: UUID bien formado pero inexistente -> 404 not_found.
  6. Barrido en vivo de las demás rutas con path param UUID malformado.
"""
import json
import sys
import time
import uuid as uuidlib

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # noqa: E402

SLUG = "uuid-fix"
ADMIN_EMAIL = "admin@uuid-fix.test"
ADMIN_PASSWORD = "UuidFix2026!pass"
OUT = "/root/.hermes/kanban/boards/trust-agent/workspaces/t_378503a1/live_evidence.json"

with open("/root/trust-agent/.env") as fh:
    for line in fh:
        if line.startswith("TRUST_PROVIDER_TOKEN="):
            e2e.PROVIDER_TOKEN = line.partition("=")[2].strip()
e2e.SLUG = SLUG
e2e.REDIRECT_URI = "http://127.0.0.1:9090/auth/callback"

BAD = "not-a-uuid"
results = []


def record(name, expected, status, body):
    ok = status == expected
    reason = (body.get("error") or {}).get("reason", "")
    results.append({"check": name, "expected": expected, "got": status,
                    "reason": reason, "pass": ok})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: HTTP {status} "
          f"(esperado {expected}, reason={reason})", flush=True)
    return ok


def main():
    # 1) Provisioning real del tenant.
    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "UUID Validation Fix Evidence Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": "standard", "slug": SLUG})
    if st == 409:
        print(f"[reprovision] tenant {SLUG} ya existe (HTTP 409) — se reutiliza")
        ota = None
    else:
        assert st == 201, f"provision HTTP {st}: {raw[:300]}"
        ota = body["one_time_activation"]["one_time_password"]
        print(f"[provision] tenant {SLUG} creado")
    # Si el tenant ya existía, su password ya fue rotado por una corrida
    # anterior de este mismo script: login directo con ADMIN_PASSWORD.
    totp_state = {}
    if ota is not None:
        id_token, acr = e2e.pkce_login(
            ADMIN_EMAIL, ota, new_password=ADMIN_PASSWORD, totp_state=totp_state)
    else:
        id_token, acr = e2e.pkce_login(
            ADMIN_EMAIL, ADMIN_PASSWORD, totp_state=totp_state)
    assert id_token and id_token != "MFA_REQUIRED", f"login PKCE falló: {id_token}"
    print(f"[login] id_token obtenido, acr={acr}")

    # 2) Mint de sesión (Bearer).
    st, body, raw = e2e.api("POST", "/api/auth/session", payload={
        "id_token": id_token, "kind": "desktop"})
    assert st == 201, f"session HTTP {st}: {raw[:300]}"
    bearer = body["session_token"]
    assert bearer, "sin session_token en el body"
    print("[session] minted (kind=desktop, MFA verificada)")

    H = {"Authorization": f"Bearer {bearer}"}

    # 3) REPRO del bug reportado por ui (spec 021, §5).
    st, body, _ = e2e.api("PUT", f"/api/budgets/dept/{BAD}",
                          payload={"amount_cents": 100}, headers=H)
    record("REPRO PUT /api/budgets/dept/not-a-uuid -> 422", 422, st, body)

    # 4) Control: UUID válido inexistente sigue siendo 404 (sin oráculo).
    ghost = str(uuidlib.uuid4())
    st, body, _ = e2e.api("PUT", f"/api/budgets/dept/{ghost}",
                          payload={"amount_cents": 100}, headers=H)
    record("CONTROL PUT /api/budgets/dept/<uuid inexistente> -> 404",
           404, st, body)

    # 5) Barrido en vivo de las demás rutas con id malformado.
    sweep = [
        ("PUT", f"/api/budgets/user/{BAD}", {"amount_cents": 100}),
        ("PUT", f"/api/policies/dept/{BAD}",
         {"key": "session_timeout_minutes", "value": 30}),
        ("PUT", f"/api/policies/user/{BAD}",
         {"key": "session_timeout_minutes", "value": 30}),
        ("PUT", f"/api/departments/{BAD}", {"name": "X"}),
        ("DELETE", f"/api/departments/{BAD}", None),
        ("PUT", f"/api/users/{BAD}", {"display_name": "X"}),
        ("POST", f"/api/users/{BAD}/disable", None),
        ("GET", f"/api/devices/{BAD}", None),
        ("POST", f"/api/devices/{BAD}/revoke", None),
        ("GET", f"/api/vault/credentials/{BAD}", None),
        ("GET", f"/api/connectors/instances/{BAD}", None),
        ("POST", f"/api/models/keys/{BAD}/revoke", None),
        ("GET", f"/api/jobs/{BAD}", None),
        ("GET", f"/api/agents/{BAD}", None),
        ("GET", f"/api/audit/events?actor_id={BAD}", None),
        ("GET", f"/api/policies?level=dept&department_id={BAD}", None),
    ]
    for method, path, payload in sweep:
        st, body, _ = e2e.api(method, path, payload=payload, headers=H)
        record(f"{method} {path.split('?')[0]}{' (query)' if '?' in path else ''}",
               422, st, body)

    passed = sum(1 for r in results if r["pass"])
    print(f"\n=== {passed}/{len(results)} checks PASS ===")
    with open(OUT, "w") as fh:
        json.dump({"slug": SLUG, "admin_email": ADMIN_EMAIL, "acr": acr,
                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "results": results}, fh, indent=2)
    print(f"[ok] evidencia guardada en {OUT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

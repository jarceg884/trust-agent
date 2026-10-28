#!/usr/bin/env python3
"""Complemento del barrido en vivo (t_378503a1): rutas que quedaron fuera
del primer barrido — vault connections, grants anidados y notifications.
Reusa el tenant uuid-fix ya provisionado (password ya rotado)."""
import json
import os
import sys

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # noqa: E402

SLUG = "uuid-fix"
ADMIN_EMAIL = "admin@uuid-fix.test"
ADMIN_PASSWORD = "UuidFix2026!pass"
OUT = "/root/.hermes/kanban/boards/trust-agent/workspaces/t_378503a1/live_evidence_extra.json"

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
    # Secret TOTP real capturado durante el enrolamiento de la primera
    # corrida (live_repro.py lo imprimió: [dance] enrolamiento TOTP real).
    # REDACTADO post-gate (t_b62edff2): una semilla TOTP es una credencial
    # y no vive en el repo. Para reproducir el flujo:
    #   E2E_TOTP_SECRET=... python3 live_repro_extra.py
    totp_state = {"secret": os.environ["E2E_TOTP_SECRET"]}
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD,
                                   totp_state=totp_state)
    assert id_token and id_token != "MFA_REQUIRED", f"login falló: {id_token}"
    st, body, raw = e2e.api("POST", "/api/auth/session", payload={
        "id_token": id_token, "kind": "desktop"})
    assert st == 201, f"session HTTP {st}: {raw[:300]}"
    H = {"Authorization": f"Bearer {body['session_token']}"}
    print("[session] minted")

    checks = [
        ("GET", f"/api/vault/connections/{BAD}", None),
        ("PUT", f"/api/vault/connections/{BAD}", {"name": "X"}),
        ("POST", f"/api/vault/connections/{BAD}/revoke", None),
        ("POST", f"/api/vault/connections/{BAD}/use", None),
        ("POST", f"/api/me/notifications/{BAD}/read", None),
        ("POST", f"/api/devices/{BAD}/token", None),
        ("POST", f"/api/agents/{BAD}/grants",
         {"user_id": "30000000-0000-0000-0000-000000000009",
          "permission": "invoke"}),
    ]
    for method, path, payload in checks:
        st, body, _ = e2e.api(method, path, payload=payload, headers=H)
        record(f"{method} {path}", 422, st, body)

    # grant_id malformado sobre agente inexistente: el 422 de agent_id gana
    # (validación de ruta antes que la búsqueda).
    st, body, _ = e2e.api("DELETE", f"/api/agents/{BAD}/grants/{BAD}",
                          headers=H)
    record("DELETE /api/agents/not-a-uuid/grants/not-a-uuid", 422, st, body)

    passed = sum(1 for r in results if r["pass"])
    print(f"\n=== {passed}/{len(results)} checks PASS ===")
    with open(OUT, "w") as fh:
        json.dump({"slug": SLUG, "results": results}, fh, indent=2)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

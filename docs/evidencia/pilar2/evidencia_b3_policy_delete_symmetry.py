#!/usr/bin/env python3
"""Evidencia real del fix t_fdc728d2 (gap B-3, REQ-GOV-025) contra el
stack vivo.

Reproduce EXACTAMENTE el escenario del BUG B-3 de la revisión E2E del
Pilar 2 (docs/revision-pilar2-governance-e2e.md §"BUG B-3"):

  PUT /api/policies/user/{user_id} {"key": "allowed_models",
                                    "value": ["glm-5.2"]}  → 200
  DELETE /api/policies/user/allowed_models?user_id={user_id}   → 404  (BUG)
  DELETE ...?user_id=…&department_id=…                         → 204  (workaround)

Con el fix (resolución del depto igual que el upsert, REQ-GOV-025):
  DELETE /api/policies/user/allowed_models?user_id={user_id}   → 204

Nada simulado: API :8000, Keycloak :8080 y PostgreSQL reales, tenant
fresco provisionado en caliente, login REAL PKCE S256. El usuario de
prueba pertenece a un departamento (depto resuelto por el upsert), que
es exactamente la condición del bug.
"""
import importlib.util
import time
from pathlib import Path

SCRIPTS = Path("/root/trust-agent/platform/scripts")
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

STAMP = time.strftime("%H%M%S")
SLUG = f"b3-{STAMP}"
rv.SLUG = SLUG
ADMIN_EMAIL = f"admin@{SLUG}.test"
ADMIN_PW = "B3Admin2026!"
REG_EMAIL = f"worker@{SLUG}.test"
REG_PW = "B3Worker2026!"
PASSES = 0
FAILURES = []


def check(name, ok, detail=""):
    global PASSES
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  · {detail}" if detail else ""),
          flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)
    return ok


def main():
    print(f"=== t_fdc728d2 (gap B-3) — simetría PUT/DELETE de policies "
          f"de usuario (tenant {SLUG}) ===", flush=True)

    # ---------- Provisioning + login REAL del admin ----------
    st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "B3 Policy Symmetry Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0,
        "model_plan": ["glm-5.2"],
        "slug": SLUG})
    check("E0a POST /api/admin/tenants → 201 (realm KC nuevo)", st == 201,
          f"HTTP {st} {raw[:200]}")
    ota = body["one_time_activation"]["one_time_password"]
    id_token, acr = rv.pkce_login(ADMIN_EMAIL, ota, new_password=ADMIN_PW)
    check("E0b login REAL admin (PKCE S256)", id_token is not None,
          f"acr={acr}")
    admin = rv.mint(id_token, "desktop")["session_token"]

    # Usuario de prueba con departamento (auto-registro + asignación de
    # depto): el depto lo resuelve el upsert, la condición exacta del bug.
    st, body, raw = rv.api("POST", "/api/auth/register", payload={
        "org": SLUG, "email": REG_EMAIL, "password": REG_PW,
        "display_name": "B3 Worker"})
    check("E0c POST /api/auth/register (auto-registro) → 201", st == 201,
          f"HTTP {st} {raw[:200]}")
    st, body, raw = rv.api("POST", "/api/departments", bearer=admin,
                           payload={"name": "B3 Ops",
                                    "initial_budget_cents": 5000})
    check("E0d POST /api/departments → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    dept_id = body.get("department_id", "")

    st, body, raw = rv.api("GET", "/api/users", bearer=admin)
    users = {u["email"]: u for u in body.get("users", [])}
    user_id = users[REG_EMAIL]["user_id"]
    st, body, raw = rv.api("PUT", f"/api/users/{user_id}", bearer=admin,
                           payload={"department_id": dept_id})
    check("E0e PUT /api/users/{id} (asignar depto) → 200", st == 200,
          f"HTTP {st} {raw[:150]}")

    # La fila de la DB confirma el depto del usuario (condición B-3).
    rows = rv.db(
        "SELECT department_id FROM users WHERE id = %s", (user_id,))
    check("E0f el usuario tiene department_id en la DB",
          rows and rows[0][0] is not None,
          f"department_id={rows[0][0] if rows else None}")

    # ---------- El repro exacto del BUG B-3 ----------
    st, body, raw = rv.api("PUT", f"/api/policies/user/{user_id}",
                           bearer=admin,
                           payload={"key": "allowed_models",
                                    "value": ["glm-5.2"]})
    check("E1 PUT /api/policies/user/{user_id} SIN department_id → 200 "
          "(el upsert resuelve el depto)", st == 200,
          f"HTTP {st} {raw[:200]}")

    st, body, raw = rv.api(
        "DELETE", f"/api/policies/user/allowed_models?user_id={user_id}",
        bearer=admin)
    check("E2 DELETE /api/policies/user/allowed_models?user_id=… SIN "
          "department_id → 204 (REQ-GOV-025; antes del fix: 404)",
          st == 204, f"HTTP {st} {raw[:200]}")

    st, body, raw = rv.api(
        "DELETE", f"/api/policies/user/allowed_models?user_id={user_id}",
        bearer=admin)
    check("E3 segundo DELETE idéntico → 404 (la fila quedó desactivada)",
          st == 404, f"HTTP {st} {raw[:150]}")

    # ---------- Compatibilidad hacia atrás + contrato del PUT ----------
    st, body, raw = rv.api("PUT", f"/api/policies/user/{user_id}",
                           bearer=admin,
                           payload={"key": "allowed_models",
                                    "value": ["glm-5.2"]})
    check("E4 re-PUT (misma fila reactivada, version+1) → 200", st == 200,
          f"HTTP {st} {raw[:200]}")

    st, body, raw = rv.api(
        "DELETE",
        f"/api/policies/user/allowed_models"
        f"?user_id={user_id}&department_id={dept_id}",
        bearer=admin)
    check("E5 DELETE con department_id EXPLÍCITO sigue → 204 (compat)",
          st == 204, f"HTTP {st} {raw[:200]}")

    st, body, raw = rv.api(
        "DELETE",
        "/api/policies/user/allowed_models"
        "?user_id=00000000-0000-0000-0000-000000000000",
        bearer=admin)
    check("E6 DELETE con user_id inexistente → 404 User not found "
          "(mismo contrato que el PUT)", st == 404,
          f"HTTP {st} {raw[:150]}")

    # ---------- El audit registra el depto resuelto ----------
    rows = rv.db(
        "SELECT payload FROM audit_log "
        "WHERE action = 'policy.updated' "
        "AND payload->>'deactivated' = 'true' "
        "AND payload->>'user_id' = %s "
        "ORDER BY ts DESC LIMIT 1", (user_id,))
    dept_audited = (rows and rows[0][0]
                    and rows[0][0].get("department_id") == dept_id)
    check("E7 el audit del deactivate lleva el department_id RESUELTO",
          bool(dept_audited),
          f"payload.department_id={rows[0][0].get('department_id') if rows else None}")

    print(f"\n=== {PASSES} PASS, {len(FAILURES)} FAIL ===", flush=True)
    if FAILURES:
        print("FALLOS: " + ", ".join(FAILURES), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

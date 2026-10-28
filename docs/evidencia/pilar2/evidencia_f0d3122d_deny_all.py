#!/usr/bin/env python3
"""Evidencia real del fix t_f0d3122d (QA P4e/P4e1) contra el stack vivo.

Reproduce EXACTAMENTE el escenario del fallo reportado por QA en la
revisión del Pilar 2 (docs/revision-pilar2-panel-admin.md §4):

  org aprueba glm-5.2 + org allowed_models=["glm-5.2"] →
  user allowed_models=[] (deny-all explícito, el valor MÁS restrictivo
  que el upsert acepta) → el gate effective_models_for_user debe dejar
  el conjunto efectivo VACÍO y verify debe devolver 403.

Antes del fix, `models/access.py` colapsaba [] a "sin restricción"
(`or []` + truthiness) y el usuario conservaba acceso a glm-5.2.

Cubre además (spec delta 003/005/018, AC-GOV-036):
  - clave ausente en los 3 niveles → default de catálogo null
    (migración 0019) = heredar, sin restricción adicional;
  - deny [] a nivel ORG → deny-all para todo el tenant (admin incluido);
  - restauración por DELETE de la policy (ausente = hereda de nuevo).

Nada simulado: API :8000, Keycloak :8080 y PostgreSQL reales, tenant
fresco provisionado en caliente, login REAL PKCE S256.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path("/root/trust-agent/platform/scripts")
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

STAMP = time.strftime("%H%M%S")
SLUG = f"f0d3-{STAMP}"
rv.SLUG = SLUG
ADMIN_EMAIL = f"admin@{SLUG}.test"
ADMIN_PW = "F0d3Admin2026!"
REG_EMAIL = f"worker@{SLUG}.test"
REG_PW = "F0d3Worker2026!"
PASSES = 0
FAILURES = []


def check(name, ok, detail=""):
    global PASSES
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  · {detail}" if detail else ""), flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)
    return ok


def main():
    print(f"=== t_f0d3122d — evidencia deny-all explícito (tenant {SLUG}) ===",
          flush=True)

    # ---------- Provisioning + logins REALES ----------
    st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "F0d3 DenyAll Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0,
        "model_plan": ["glm-5.2"],
        "slug": SLUG})
    check("E0a POST /api/admin/tenants → 201 (realm KC nuevo)", st == 201,
          f"HTTP {st} {raw[:200]}")
    tenant_id = body.get("tenant_id", body.get("tenant", {}).get("id", ""))
    ota = body["one_time_activation"]["one_time_password"]
    id_token, acr = rv.pkce_login(ADMIN_EMAIL, ota, new_password=ADMIN_PW)
    check("E0b login REAL admin (PKCE S256)", id_token is not None,
          f"acr={acr}")
    admin = rv.mint(id_token, "desktop")["session_token"]

    st, cfg, raw = rv.api("GET", f"/api/auth/org-config?org={SLUG}")
    check("E0c GET /api/auth/org-config (público) → 200", st == 200,
          f"HTTP {st}")
    st, body, raw = rv.api("POST", "/api/auth/register", payload={
        "org": SLUG, "email": REG_EMAIL, "password": REG_PW,
        "display_name": "F0d3 Worker"})
    check("E0d POST /api/auth/register (auto-registro) → 201", st == 201,
          f"HTTP {st} {raw[:200]}")
    w_id_token, _ = rv.pkce_login(REG_EMAIL, REG_PW)
    check("E0e login REAL worker", w_id_token is not None)
    worker = rv.mint(w_id_token, "desktop")["session_token"]

    st, body, raw = rv.api("POST", "/api/departments", bearer=admin,
                           payload={"name": "F0d3 Ops",
                                    "initial_budget_cents": 5000})
    check("E0f POST /api/departments → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    dept_id = body.get("department_id", "")

    st, body, raw = rv.api("GET", "/api/users", bearer=admin)
    users = {u["email"]: u for u in body.get("users", [])}
    worker_id = users[REG_EMAIL]["user_id"]
    st, body, raw = rv.api("PUT", f"/api/users/{worker_id}", bearer=admin,
                           payload={"department_id": dept_id})
    check("E0g PUT /api/users/{id} (asignar depto) → 200", st == 200,
          f"HTTP {st} {raw[:150]}")

    # ---------- E1: baseline — clave ausente = null (migración 0019) ------
    print("=== E1: baseline — sin policy: null = heredar (migr. 0019) ===",
          flush=True)
    st, body, raw = rv.api("GET", "/api/me/policies", bearer=worker)
    check("E1a efectiva del worker: allowed_models null (default de catálogo"
          " tras migración 0019 — antes era [], indistinguible de deny-all)",
          st == 200 and body.get("allowed_models") is None,
          f"HTTP {st} allowed_models={body.get('allowed_models')!r}")
    st, body, raw = rv.api("GET", "/api/models/allowed-for-me", bearer=worker)
    check("E1b allowed-for-me baseline = [glm-5.2] (aprobación org heredada)",
          st == 200 and body.get("models") == ["glm-5.2"],
          f"HTTP {st} models={body.get('models')}")

    # ---------- E2: el escenario exacto del fallo QA P4e ------------------
    print("=== E2: escenario QA P4e — user [] = deny-all explícito ===",
          flush=True)
    st, body, raw = rv.api("PUT", "/api/policies/org", bearer=admin,
                           payload={"key": "allowed_models",
                                    "value": ["glm-5.2"]})
    check("E2a PUT /api/policies/org allowed_models=[glm-5.2] → 200",
          st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("PUT", f"/api/policies/user/{worker_id}",
                           bearer=admin, payload={
                               "key": "allowed_models", "value": []})
    check("E2b PUT /api/policies/user allowed_models=[] → 200 (el upsert"
          " acepta [] como valor MÁS restrictivo, REQ-POL-007)",
          st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/me/policies", bearer=worker)
    check("E2c efectiva del worker (combinator deny-overrides) = [] — esto"
          " ya funcionaba (QA P4e1)",
          st == 200 and body.get("allowed_models") == [],
          f"HTTP {st} allowed_models={body.get('allowed_models')!r}")
    st, body, raw = rv.api("GET", "/api/models/allowed-for-me",
                           bearer=worker)
    check("E2d FIX t_f0d3122d (QA P4e): allowed-for-me = [] — el gate YA NO"
          " ignora la lista vacía",
          st == 200 and body.get("models") == [],
          f"HTTP {st} models={body.get('models')}")
    st, body, raw = rv.api("POST", "/api/models/verify", bearer=worker,
                           payload={"model_name": "glm-5.2"})
    check("E2e POST /api/models/verify glm-5.2 → 403 model_not_approved",
          st == 403 and body.get("error", {}).get("reason")
          == "model_not_approved",
          f"HTTP {st} {raw[:200]}")
    rows = rv.db("SELECT count(*) FROM audit_log WHERE tenant_id=%s "
                 "AND action='model.access_denied'", (tenant_id,))
    check("E2f audit model.access_denied persistido en DB",
          rows and rows[0][0] >= 1, f"count={rows[0][0] if rows else 0}")

    # ---------- E3: clave ausente otra vez = hereda ------------------------
    print("=== E3: DELETE de la policy user → ausente = hereda ===",
          flush=True)
    st, body, raw = rv.api("DELETE",
                           f"/api/policies/user/allowed_models"
                           f"?user_id={worker_id}&department_id={dept_id}",
                           bearer=admin)
    check("E3a DELETE /api/policies/user/allowed_models → 204", st == 204,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/models/allowed-for-me",
                           bearer=worker)
    check("E3b allowed-for-me vuelve a [glm-5.2] (hereda org)",
          st == 200 and body.get("models") == ["glm-5.2"],
          f"HTTP {st} models={body.get('models')}")

    # ---------- E4: deny [] a nivel ORG = deny-all para todo el tenant ----
    print("=== E4: org [] = deny-all para TODO el tenant ===", flush=True)
    st, body, raw = rv.api("PUT", "/api/policies/org", bearer=admin,
                           payload={"key": "allowed_models", "value": []})
    check("E4a PUT /api/policies/org allowed_models=[] → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    for who, tok in (("worker", worker), ("admin", admin)):
        st, body, raw = rv.api("GET", "/api/models/allowed-for-me",
                               bearer=tok)
        check(f"E4b allowed-for-me del {who} = [] (deny-all org)",
              st == 200 and body.get("models") == [],
              f"HTTP {st} models={body.get('models')}")
    st, body, raw = rv.api("POST", "/api/models/verify", bearer=worker,
                           payload={"model_name": "glm-5.2"})
    check("E4c verify del worker → 403 model_not_approved", st == 403,
          f"HTTP {st} {raw[:150]}")

    # ---------- E5: restaurar el estado "sin policy" ----------------------
    print("=== E5: restaurar — org ausente = null/heredar ===", flush=True)
    st, body, raw = rv.api("DELETE", "/api/policies/org/allowed_models",
                           bearer=admin)
    check("E5a DELETE /api/policies/org/allowed_models → 204", st == 204,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/me/policies", bearer=worker)
    check("E5b efectiva del worker: allowed_models null de nuevo",
          st == 200 and body.get("allowed_models") is None,
          f"HTTP {st} allowed_models={body.get('allowed_models')!r}")
    st, body, raw = rv.api("GET", "/api/models/allowed-for-me",
                           bearer=worker)
    check("E5c allowed-for-me = [glm-5.2] (solo aprobaciones)",
          st == 200 and body.get("models") == ["glm-5.2"],
          f"HTTP {st} models={body.get('models')}")

    print(f"\n=== RESULTADO: {PASSES} PASS, {len(FAILURES)} FAIL ===",
          flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Repro del GAP M-1 contra el stack VIVO (pre-fix): aprobar un modelo a
nivel `user` sin `department_id` en el body → CheckViolation → HTTP 500.

Provisiona un tenant fresco, login REAL (PKCE S256 + TOTP), y ejercita
PUT /api/models/access con level=user. Reutiliza la maquinaria de
platform/scripts/revalidate_litellm_real.py. Nada simulado.

Uso:
  python3 repro_m1.py            # espera 500 (pre-fix) o 201 (post-fix)
"""
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

SCRIPTS = Path("/root/trust-agent/platform/scripts")
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

STAMP = time.strftime("%H%M%S")
SLUG = f"m1-{STAMP}"
rv.SLUG = SLUG
ADMIN_EMAIL = f"admin@{SLUG}.test"
ADMIN_PW = "M1FixAdmin2026!"


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  · {detail}" if detail else ""), flush=True)
    return ok, detail


def main():
    print(f"=== M-1 repro: tenant fresco {SLUG} + login REAL ===", flush=True)
    st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "M1 Fix Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0,
        "model_plan": ["glm-5.2"],
        "slug": SLUG})
    check("P0a provisioning → 201", st == 201, f"HTTP {st} {raw[:200]}")
    assert st == 201, raw[:500]
    tenant_id = body.get("tenant_id", body.get("tenant", {}).get("id", ""))
    ota = body["one_time_activation"]["one_time_password"]
    totp_state = {}
    id_token, acr = rv.pkce_login(ADMIN_EMAIL, ota, new_password=ADMIN_PW,
                                  totp_state=totp_state)
    check("P0b login REAL admin (PKCE S256 + TOTP)", id_token is not None,
          f"acr={acr}")
    admin = rv.mint(id_token, "desktop")["session_token"]
    st, me, raw = rv.api("GET", "/api/me", bearer=admin)
    admin_user_id = me.get("user_id", "")
    check("P0c GET /api/me → org_admin", st == 200 and admin_user_id,
          f"HTTP {st} user_id={admin_user_id}")

    # Depto + asignar el admin a ese depto (el nivel user exige depto).
    st, body, raw = rv.api("POST", "/api/departments", bearer=admin, payload={
        "name": "M1 Engineering"})
    check("P1a POST /api/departments → 201", st == 201, f"HTTP {st} {raw[:150]}")
    dept_id = body.get("department_id", "")
    st, body, raw = rv.api("PUT", f"/api/users/{admin_user_id}",
                           bearer=admin, payload={"department_id": dept_id})
    check("P1b PUT /api/users/{admin} department → 200", st == 200,
          f"HTTP {st} {raw[:150]}")

    # Org aprueba glm-5.2 (subset rule para niveles inferiores).
    st, body, raw = rv.api("PUT", "/api/models/access", bearer=admin,
                           payload={"model_name": "glm-5.2", "level": "org",
                                    "mode": "central"})
    check("P2a PUT level=org glm-5.2 → 201", st == 201,
          f"HTTP {st} {raw[:150]}")

    # ===== EL REPRO: level=user SIN department_id (lo que manda la UI) =====
    st, body, raw = rv.api("PUT", "/api/models/access", bearer=admin,
                           payload={"model_name": "glm-5.2", "level": "user",
                                    "user_id": admin_user_id,
                                    "mode": "central"})
    print(f"\n>>> REPRO M-1: PUT level=user (sin department_id) → HTTP {st}",
          flush=True)
    print(f">>> body: {raw[:400]}", flush=True)
    if st == 500:
        check("M-1 REPRODUCIDO: 500 internal_error (bug vivo)", True,
              "CheckViolation model_access_scope_check")
        result = "BUG-REPRODUCED"
    elif st == 201:
        check("M-1 CORREGIDO: 201 aprobado a nivel user", True, raw[:200])
        result = "FIXED"
        # Verificación de la fila en DB: department_id resuelto del usuario.
        rows = rv.db(
            "SELECT ma.level, ma.department_id::text, ma.user_id::text, "
            "ma.mode, ma.status "
            "FROM model_access ma JOIN catalog.model_catalog mc "
            "ON mc.id = ma.model_id WHERE ma.tenant_id = %s "
            "AND mc.model_name = 'glm-5.2' AND ma.level = 'user'",
            (tenant_id,))
        print(f">>> fila user-level en DB: {rows}", flush=True)
        check("M-1b fila user-level con department_id resuelto",
              bool(rows) and rows[0][1] == dept_id
              and rows[0][2] == admin_user_id, str(rows))
        # Denegación explícita por usuario (revoke) también sin department_id.
        st2, body2, raw2 = rv.api(
            "POST", "/api/models/access/revoke", bearer=admin,
            payload={"model_name": "glm-5.2", "level": "user",
                     "user_id": admin_user_id})
        check("M-1c POST revoke level=user (sin dept) → 200", st2 == 200,
              f"HTTP {st2} {raw2[:200]}")
        st3, body3, raw3 = rv.api("GET", "/api/models/allowed-for-me",
                                  bearer=admin)
        check("M-1d deny-all a nivel user tras revoke (intersección)",
              st3 == 200 and body3.get("models") == [],
              f"HTTP {st3} {raw3[:200]}")
        # Re-aprobar restaura.
        st4, _, raw4 = rv.api("PUT", "/api/models/access", bearer=admin,
                              payload={"model_name": "glm-5.2",
                                       "level": "user",
                                       "user_id": admin_user_id,
                                       "mode": "central"})
        check("M-1e re-aprobar level=user → 201", st4 == 201,
              f"HTTP {st4} {raw4[:150]}")
        st5, body5, _ = rv.api("GET", "/api/models/allowed-for-me",
                               bearer=admin)
        check("M-1f allowed-for-me restaura glm-5.2",
              st5 == 200 and body5.get("models") == ["glm-5.2"],
              str(body5.get("models")))
        # department_id explícito INCORRECTO → 422 (no silencio, no 500).
        st6, _, raw6 = rv.api("PUT", "/api/models/access", bearer=admin,
                              payload={"model_name": "glm-5.2",
                                       "level": "user",
                                       "department_id": str(uuid.uuid4()),
                                       "user_id": admin_user_id})
        check("M-1g department_id mismatch → 422", st6 == 422,
              f"HTTP {st6} {raw6[:200]}")
    else:
        check(f"M-1 estado inesperado HTTP {st}", False, raw[:300])
        result = f"UNEXPECTED-{st}"

    print(f"\ntenant: {SLUG} {tenant_id}", flush=True)
    print(f"RESULT: {result}", flush=True)
    # Credenciales para la verificación UI (browser) posterior.
    import json as _json
    _json.dump({
        "slug": SLUG, "tenant_id": tenant_id,
        "admin_email": ADMIN_EMAIL, "admin_pw": ADMIN_PW,
        "totp_secret": totp_state.get("secret", ""),
        "admin_user_id": admin_user_id, "dept_id": dept_id,
        "result": result,
    }, open("/root/.hermes/kanban/boards/trust-agent/workspaces/"
            "t_f75e6a18/m1_tenant.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

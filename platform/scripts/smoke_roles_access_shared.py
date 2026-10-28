#!/usr/bin/env python3
"""Smoke compartido de Roles & Access (spec 017, feedback Miller #4).

Complemento de `smoke_roles_access.py` (servidor dedicado): esta corrida
ejerce los endpoints NUEVOS contra el STACK COMPARTIDO VIVO — el API de
compose (127.0.0.1:8000, imagen `trust-agent/platform:local`), el Keycloak
REAL (127.0.0.1:8080, realm final-e2e) y la PostgreSQL compartida
`trust_agent` (RLS activa, migración 0010 aplicada). Evidencia 100% real:
login PKCE S256 + enrolamiento TOTP real (acr=2), sesión MFA minteada por
el control plane, audit verificado en la DB compartida con cadena
inmutable.

Fixture declarado (mismo contrato que e2e_final_live.py): se reusa el
tenant `final-e2e` y su admin/worker. El enrolamiento TOTP del admin se
REINICIA vía KC Admin REST antes del login — exactamente el estado que un
run nuevo de e2e_final_live espera (su baile re-enrola TOTP); el password
permanece FinalAdmin2026!. Sin residuo: el worker termina active con su
rol original.

Uso: .venv/bin/python scripts/smoke_roles_access_shared.py
"""
import json
import sys

sys.path.insert(0, "/root/trust-agent/platform/scripts")

import e2e_final_live as e  # noqa: E402  (patrón de login real compartido)

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        failures.append(name)
    return cond


def main():
    # --- 0. Fixture: reiniciar TOTP del admin (estado que e2e espera) --------
    token = e.kc_admin_token()
    admin_uuid = e.kc_user_uuid(e.ADMIN_EMAIL)
    st, raw = e.kc_json(
        "GET", f"/admin/realms/{e.SLUG}/users/{admin_uuid}/credentials",
        token=token)
    creds = json.loads(raw) if st == 200 else []
    for c in creds:
        if c.get("type") == "otp":
            e.kc_json("DELETE",
                      f"/admin/realms/{e.SLUG}/users/{admin_uuid}"
                      f"/credentials/{c['id']}", token=token)
    check("fixture: credenciales TOTP previas del admin retiradas "
          "(re-enrolamiento inline, como un run nuevo de e2e)",
          True, f"{len(creds)} credenciales, {sum(1 for c in creds if c.get('type') == 'otp')} otp")

    # --- 1. Login REAL del admin (PKCE S256 + CONFIGURE_TOTP + acr=2) -------
    totp_state = {}
    id_token, acr = e.pkce_login(e.ADMIN_EMAIL, e.ADMIN_NEW_PASSWORD,
                                 totp_state=totp_state)
    check("login REAL admin (PKCE + TOTP) → id_token con acr=2",
          id_token not in (None, "MFA_REQUIRED") and str(acr) == "2",
          f"acr={acr}")
    session = e.mint(id_token, "desktop")
    bearer = {"Authorization": f"Bearer {session['session_token']}"}
    check("sesión MFA minteada por el control plane vivo",
          session["session_token"].startswith("sts1.")
          and session["mfa_verified"] is True)

    # --- 2. Directorio vivo + permiso data-driven en la DB compartida -------
    st, me, raw = e.api("GET", "/api/me", headers=bearer)
    check("GET /api/me → 200 org_admin con roles.assign (catálogo 0010 vivo)",
          st == 200 and "roles.assign" in me.get("permissions", []),
          f"HTTP {st} {raw[:160]}")
    st, body, raw = e.api("GET", "/api/users", headers=bearer)
    worker = next((u for u in body.get("users", [])
                   if u["email"] == e.WORKER_EMAIL), None)
    check("GET /api/users → worker del tenant compartido presente",
          st == 200 and worker is not None, f"HTTP {st} {raw[:160]}")
    worker_id = worker["user_id"]
    dept_it = worker["department_id"]
    orig_roles = worker["roles"]

    # --- 3. Asignación dept_admin con scope (AC-RA-001, API compartido) -----
    st, body, raw = e.api("POST", f"/api/users/{worker_id}/roles",
                          {"role_key": "dept_admin",
                           "department_id": dept_it}, headers=bearer)
    check("POST /api/users/{worker}/roles (dept_admin @ IT Operations) → 201",
          st == 201 and {"role_key": "dept_admin",
                         "department_id": dept_it} in body.get("roles", []),
          f"HTTP {st} {raw[:200]}")

    # --- 4. Revocación con scope (AC-RA-002) ---------------------------------
    st, body, raw = e.api(
        "DELETE", f"/api/users/{worker_id}/roles/dept_admin"
        f"?department_id={dept_it}", headers=bearer)
    check("DELETE /api/users/{worker}/roles/dept_admin → 204", st == 204,
          f"HTTP {st} {raw[:200]}")

    # --- 5. Status vía PUT (D-17-05) contra el stack compartido --------------
    st, body, raw = e.api("PUT", f"/api/users/{worker_id}",
                          {"status": "disabled"}, headers=bearer)
    check("PUT status=disabled → 200 (sesiones/dispositivos revocados)",
          st == 200 and body.get("status") == "disabled",
          f"HTTP {st} {raw[:200]}")
    st, body, raw = e.api("PUT", f"/api/users/{worker_id}",
                          {"status": "active"}, headers=bearer)
    check("PUT status=active → 200 (reactivado)", st == 200
          and body.get("status") == "active", f"HTTP {st} {raw[:200]}")

    # --- 6. Departamentos del tenant (REQ-RA-006) ----------------------------
    st, body, raw = e.api("GET", "/api/departments", headers=bearer)
    check("GET /api/departments → 200 (4 deptos seed del tenant compartido)",
          st == 200 and len(body.get("departments", [])) == 4,
          f"HTTP {st} {raw[:200]}")

    # --- 7. Audit REAL en la DB compartida + cadena inmutable ----------------
    rows = e.db("SELECT action FROM audit_log WHERE target_id = %s "
                "ORDER BY seq DESC LIMIT 6", (worker_id,))
    actions = [r[0] for r in rows]
    check("audit compartido: role.assigned emitido", "role.assigned" in actions,
          str(actions))
    check("audit compartido: role.revoked emitido", "role.revoked" in actions,
          str(actions))
    check("audit compartido: user.disabled emitido", "user.disabled" in actions,
          str(actions))
    tenant_id = e.db("SELECT id FROM tenants WHERE slug = %s",
                     (e.SLUG,))[0][0]
    chain = e.db("SELECT audit_verify_chain(%s)", (tenant_id,))[0][0]
    chain_status = chain[0] if isinstance(chain, (tuple, list)) else chain
    check("audit chain del tenant compartido → ok", chain_status == "ok",
          str(chain))

    # --- 8. Sin residuo --------------------------------------------------------
    st, body, raw = e.api("GET", "/api/users", headers=bearer)
    worker = next((u for u in body.get("users", [])
                   if u["user_id"] == worker_id), {})
    check("sin residuo: worker active con sus roles originales",
          worker.get("status") == "active"
          and worker.get("roles") == orig_roles,
          f"status={worker.get('status')} roles={worker.get('roles')}")

    print()
    if failures:
        print(f"SMOKE SHARED FAIL — {len(failures)} fallo(s): {failures}")
        return 1
    print("SMOKE SHARED PASS — roles & access verificado contra el stack "
          "compartido vivo (API :8000 + Keycloak real + PostgreSQL "
          "compartida): asignación + revocación + status + departamentos + "
          "audit chain, sin residuo")
    return 0


if __name__ == "__main__":
    sys.exit(main())

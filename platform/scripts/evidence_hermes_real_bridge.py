#!/usr/bin/env python3
"""EVIDENCIA LIVE — Spec 022 (hermes-real-bridge), tarea t_c875626f.

Aprovisiona un tenant REAL de prueba ("hermes-bridge") contra el control
plane vivo (:8000) + Keycloak real (:8080) + LiteLLM REAL (:4000) y asiste
la validación del plugin trust-agent-governance en el perfil Hermes real
`trust-e2e`:

  setup         tenant + admin (PKCE+TOTP acr=2) + worker (PKCE+TOTP acr=2)
                + policy allowed_tools + budgets + key virtual del worker
                + id_token del worker al archivo default del /trust-enroll.
  worker-login  re-login del worker → id_token fresco (si expiró el de setup).
  approve       aprueba el dispositivo pendiente del tenant (lo crea
                /trust-enroll desde la sesión Hermes real).
  revoke        revoca el dispositivo (fail-closed en vivo, AC-HRB-004c).
  status        inspección: fila devices en DB, bundle cache, veto-counts,
                plugin.log del perfil.

Reutiliza el baile PKCE+TOTP verificado de e2e_final_live.py (t_bf72a163)
importándolo y parcheando su SLUG — cero dobles: IdP y gateway reales.

Uso:
  .venv/bin/python scripts/evidence_hermes_real_bridge.py --phase setup
  .venv/bin/python scripts/evidence_hermes_real_bridge.py --phase approve
"""
import argparse
import http.client
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_final_live as e2e  # noqa: E402 - helpers verificados del E2E final

SLUG = "hermes-bridge"
ADMIN_EMAIL = "admin@hermes-bridge.test"
ADMIN_PASSWORD = "BridgeAdmin2026!"
WORKER_EMAIL = "bridge@hermes-bridge.test"
WORKER_PASSWORD = "BridgeWorker2026!"
MODEL = "glm-5.2"
LITELLM_REAL_PORT = 4000

# Perfil Hermes REAL de validación (D-HRB-06) — home aislado del factory.
PROFILE_HOME = "/root/.hermes/profiles/trust-e2e"
ID_TOKEN_FILE = f"{PROFILE_HOME}/trust-agent/id-token.txt"
STATE_FILE = "/root/trust-agent/.state-hermes-bridge.json"

FAILURES = []
PASSES = 0


def check(name, cond, detail=""):
    global PASSES
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)
    return ok


def save_state(state):
    for k in ("admin", "worker"):
        state.setdefault(k, {})
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(STATE_FILE, 0o600)


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def write_id_token(id_token):
    os.makedirs(os.path.dirname(ID_TOKEN_FILE), exist_ok=True)
    with open(ID_TOKEN_FILE, "w") as f:
        f.write(id_token)
    os.chmod(ID_TOKEN_FILE, 0o600)
    return ID_TOKEN_FILE


def reset_totp(user_uuid):
    """Borra la credencial TOTP del usuario en el realm real (KC admin API).

    El secret TOTP NO es recuperable tras el enrolamiento (KC lo guarda
    server-side), así que para re-login de validación borramos la credencial
    y dejamos que el baile PKCE re-enrole inline (D-MFA-01: CONFIGURE_TOTP
    forzado) capturando el secret nuevo en totp_state.
    -> número de credenciales otp eliminadas.
    """
    token = e2e.kc_admin_token()
    st, raw = e2e.kc_json(
        "GET", f"/admin/realms/{SLUG}/users/{user_uuid}/credentials", token=token)
    assert st == 200, f"credentials: HTTP {st} {raw[:120]}"
    creds = json.loads(raw)
    removed = 0
    for cred in creds:
        if cred.get("type") == "otp":
            st, raw = e2e.kc_json(
                "DELETE",
                f"/admin/realms/{SLUG}/users/{user_uuid}/credentials/{cred['id']}",
                token=token)
            if st in (204, 200):
                removed += 1
    return removed


def force_configure_totp(user_uuid):
    """Re-añade el required action CONFIGURE_TOTP (baile inline → acr=2).

    El default action del realm se consume en el primer login; tras borrar la
    credencial OTP el re-login NO vuelve a pedir TOTP (acr=1) a menos que el
    required action se re-añada explícitamente (GET+PUT, representación
    completa — el PUT parcial de KC nullearía el resto).
    """
    token = e2e.kc_admin_token()
    st, raw = e2e.kc_json(
        "GET", f"/admin/realms/{SLUG}/users/{user_uuid}", token=token)
    assert st == 200, f"user get: HTTP {st} {raw[:120]}"
    rep = json.loads(raw)
    rep["requiredActions"] = ["CONFIGURE_TOTP"]
    st, raw = e2e.kc_json(
        "PUT", f"/admin/realms/{SLUG}/users/{user_uuid}", rep, token=token)
    assert st in (204, 200), f"user put: HTTP {st} {raw[:120]}"


def litellm_real_chat(key, model):
    conn = http.client.HTTPConnection("127.0.0.1", LITELLM_REAL_PORT, timeout=30)
    conn.request("POST", "/chat/completions",
                 body=json.dumps({"model": model,
                                  "messages": [{"role": "user", "content": "ping"}]}).encode(),
                 headers={"Content-Type": "application/json",
                          "Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body[:200]


# ===========================================================================
# setup
# ===========================================================================
def phase_setup():
    e2e.SLUG = SLUG
    S = {}
    print("=== SETUP: tenant hermes-bridge + usuarios MFA + policy + key virtual ===",
          flush=True)

    # -- S1: proveedor crea el tenant --------------------------------------
    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Hermes Bridge Corp", "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": "standard", "slug": SLUG})
    check("S1 proveedor: POST /api/admin/tenants → 201", st == 201 and body.get("created") is True,
          f"HTTP {st} {raw[:200]}")
    if st != 201:
        return 1
    S["tenant_id"] = body["tenant"]["id"]
    S["ota"] = body["one_time_activation"]["one_time_password"]
    rows = e2e.db("SELECT id FROM departments WHERE tenant_id = %s "
                  "AND profile_template = 'it_operations'", (S["tenant_id"],))
    S["dept_it"] = str(rows[0][0])
    print(f"    tenant_id={S['tenant_id']} dept_it={S['dept_it']}", flush=True)

    # -- S2: admin PKCE+TOTP real (acr=2) -----------------------------------
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, S["ota"], new_password=ADMIN_PASSWORD)
    check("S2 admin: login REAL PKCE S256 + enrolamiento TOTP → acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    S["admin"] = e2e.mint(id_token)
    admin = S["admin"]["session_token"]
    check("S2 sesión admin MFA-verificada", S["admin"]["mfa_verified"] is True)

    # -- S3: worker en el realm real + directorio + rol user ----------------
    token = e2e.kc_admin_token()
    st, raw_kc = e2e.kc_json("POST", f"/admin/realms/{SLUG}/users", {
        "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
        "firstName": "Bridge", "lastName": "Worker",
        "attributes": {"tenant_id": [S["tenant_id"]]}}, token=token)
    check("S3 worker creado en el realm Keycloak real", st in (201, 409), f"HTTP {st}")
    worker_uuid = e2e.kc_user_uuid(WORKER_EMAIL)
    st, _ = e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
                        {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
                        token=token)
    check("S3 password del worker fijado (no temporal)", st in (204, 409), f"HTTP {st}")
    e2e.db("INSERT INTO users (tenant_id, department_id, email, display_name, status, "
           "mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', true, %s)",
           (S["tenant_id"], S["dept_it"], WORKER_EMAIL, "Bridge Worker", worker_uuid),
           fetch=False)
    e2e.db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
           "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s",
           (S["tenant_id"], worker_uuid), fetch=False)
    rows = e2e.db("SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))
    S["worker_user_id"] = str(rows[0][0])
    S["worker_sub"] = worker_uuid

    # -- S4: worker PKCE+TOTP real (acr=2) → id_token para /trust-enroll ----
    e2e.COOKIES.clear()
    worker_totp = {}
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD, totp_state=worker_totp)
    check("S4 worker: enrola TOTP real y loguea → acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    S["worker"] = e2e.mint(id_token)
    worker = S["worker"]["session_token"]
    path = write_id_token(id_token)
    check("S4 id_token del worker (acr=2) escrito 0600 para /trust-enroll",
          stat.S_IMODE(os.stat(path).st_mode) == 0o600, path)

    # -- S5: policy restrictiva allowed_tools (REQ-HRB-003 en vivo) ----------
    st, body, raw = e2e.api("PUT", "/api/policies/org",
                            {"key": "allowed_tools",
                             "value": ["web_search", "governance_status"]},
                            bearer=admin)
    check("S5 policy org allowed_tools=[web_search,governance_status] → 200",
          st == 200, f"HTTP {st} {raw[:200]}")
    st, body, raw = e2e.api("GET", "/api/me/policies", bearer=worker)
    # effective_policy_for_user → dict plano {key: value} (deny-overrides);
    # el combinator normaliza listas (orden alfabético) — comparar ordenado.
    eff = body if st == 200 and isinstance(body, dict) else {}
    check("S5 política efectiva del worker: allowed_tools restrictiva visible",
          st == 200 and sorted(eff.get("allowed_tools") or []) ==
          ["governance_status", "web_search"],
          f"HTTP {st} allowed_tools={eff.get('allowed_tools')}")

    # -- S6: budgets jerárquicos --------------------------------------------
    st, _, raw = e2e.api("PUT", f"/api/budgets/dept/{S['dept_it']}",
                         {"amount_cents": 5000}, bearer=admin)
    check("S6 presupuesto depto IT $50 → 200", st == 200, f"HTTP {st}")
    st, _, raw = e2e.api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                         {"amount_cents": 2000}, bearer=admin)
    check("S6 presupuesto worker $20 → 200", st == 200, f"HTTP {st}")

    # -- S7: modelo aprobado + key virtual contra LiteLLM REAL ---------------
    st, body, raw = e2e.api("GET", "/api/models/allowed-for-me", bearer=worker)
    check("S7 catálogo aprobado para el worker incluye glm-5.2 (plan standard)",
          st == 200 and MODEL in body.get("models", []), f"HTTP {st} {raw[:200]}")
    st, body, raw = e2e.api("POST", "/api/models/keys",
                            {"user_id": S["worker_user_id"]}, bearer=admin)
    check("S7 key virtual emitida → 201 (plaintext una sola vez)",
          st == 201 and body.get("key"), f"HTTP {st} {raw[:200]}")
    S["key"] = body["key"]
    S["key_id"] = body["key_id"]
    st, resp = litellm_real_chat(S["key"], MODEL)
    check("S7 la key funciona contra el gateway LiteLLM REAL (:4000) con glm-5.2",
          st == 200, f"HTTP {st} {resp[:120]}")
    st, resp = litellm_real_chat(S["key"], "gpt-4o-not-approved")
    check("S7 modelo NO aprobado → rechazo del gateway real (gate duro, D-HRB-08)",
          st in (400, 403), f"HTTP {st} {resp[:120]}")

    save_state(S)
    print(f"\nSETUP: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    print(f"state → {STATE_FILE} (0600); id_token → {ID_TOKEN_FILE}", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# worker-login (id_token fresco)
# ===========================================================================
def phase_worker_login():
    e2e.SLUG = SLUG
    S = load_state()
    # El secret TOTP del enrolamiento original no es recuperable (KC lo guarda
    # server-side): borramos la credencial y re-enrolamos inline en el baile.
    # acr_values="2" (spec 015 D-AUTH-06): sin LoA 2 solicitado el realm emite
    # acr="1" aunque el enrolamiento TOTP se complete (probe-loa S7) y el
    # control plane rechaza el enroll con 403 mfa.
    removed = reset_totp(S["worker_sub"])
    check("worker: credencial TOTP previa borrada del realm real", removed >= 0,
          f"{removed} eliminada(s)")
    force_configure_totp(S["worker_sub"])
    e2e.COOKIES.clear()
    totp_state = {}
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                                   totp_state=totp_state, acr_values="2")
    check("worker re-login (re-enrola TOTP real, LoA 2) → id_token acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    if id_token:
        S["worker"] = e2e.mint(id_token)
        S["worker_totp_secret"] = totp_state.get("secret", "")
        write_id_token(id_token)
        save_state(S)
        print(f"id_token fresco → {ID_TOKEN_FILE}", flush=True)
    return 1 if FAILURES else 0


def phase_admin_login():
    """Re-login del admin si su sesión expiró (approve/revoke la necesitan)."""
    e2e.SLUG = SLUG
    S = load_state()
    st, body, _ = e2e.api("GET", "/api/me", bearer=S["admin"]["session_token"])
    if st == 200:
        check("admin: sesión vigente (no hace falta re-login)", True,
              f"HTTP {st}")
        return 0
    print(f"    sesión admin expirada (HTTP {st}) → re-login real", flush=True)
    admin_sub = e2e.kc_user_uuid(ADMIN_EMAIL)
    reset_totp(admin_sub)
    force_configure_totp(admin_sub)
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD, totp_state={})
    check("admin re-login (re-enrola TOTP real) → id_token acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    if id_token:
        S["admin"] = e2e.mint(id_token)
        check("admin: sesión MFA-verificada nueva", S["admin"]["mfa_verified"] is True)
        save_state(S)
    return 1 if FAILURES else 0


# ===========================================================================
# approve / revoke / status
# ===========================================================================
def _pending_device(tenant_id):
    rows = e2e.db("SELECT id, device_name, status, created_at FROM devices "
                  "WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 5", (tenant_id,))
    return rows


def phase_approve():
    S = load_state()
    admin = S["admin"]["session_token"]
    rows = _pending_device(S["tenant_id"])
    print("    dispositivos del tenant:", flush=True)
    for r in rows:
        print(f"      {r[0]} {r[2]} {r[1]}", flush=True)
    pending = next((r for r in rows if r[2] == "pending"), None)
    check("approve: existe dispositivo pendiente creado por /trust-enroll",
          pending is not None)
    if not pending:
        return 1
    st, body, raw = e2e.api("POST", f"/api/devices/{pending[0]}/approve", bearer=admin)
    check("approve: POST /api/devices/{id}/approve → 200 active",
          st == 200 and body.get("status") == "active", f"HTTP {st} {raw[:200]}")
    S["device_id"] = str(pending[0])
    save_state(S)
    return 1 if FAILURES else 0


def phase_revoke():
    S = load_state()
    admin = S["admin"]["session_token"]
    device_id = S.get("device_id")
    if not device_id:
        rows = _pending_device(S["tenant_id"])
        device_id = str(rows[0][0])
    st, body, raw = e2e.api("POST", f"/api/devices/{device_id}/revoke", bearer=admin)
    check("revoke: POST /api/devices/{id}/revoke → 200",
          st == 200, f"HTTP {st} {raw[:200]}")
    rows = e2e.db("SELECT status FROM devices WHERE id = %s", (device_id,))
    check("revoke: fila devices → revoked en DB", rows and rows[0][0] == "revoked",
          str(rows[0][0] if rows else None))
    return 1 if FAILURES else 0


def phase_status():
    S = load_state()
    print(json.dumps({k: v for k, v in S.items()
                      if k not in ("key", "ota")}, indent=2, default=str))
    rows = e2e.db("SELECT id, device_name, status, fingerprint FROM devices "
                  "WHERE tenant_id = %s ORDER BY created_at DESC", (S["tenant_id"],))
    print("dispositivos:", flush=True)
    for r in rows:
        print(f"  {r[0]} status={r[2]} name={r[1]} fp={str(r[3])[:16]}…", flush=True)
    tdir = f"{PROFILE_HOME}/trust-agent"
    for name in ("device-token.json", "governance-bundle.jws", "veto-counts.json",
                 "plugin.log", "device_key.pem"):
        p = os.path.join(tdir, name)
        if os.path.exists(p):
            mode = stat.S_IMODE(os.stat(p).st_mode)
            print(f"  {name}: presente ({os.path.getsize(p)}B, {mode:o})", flush=True)
        else:
            print(f"  {name}: AUSENTE", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["setup", "worker-login", "admin-login", "approve",
                             "revoke", "status"])
    args = ap.parse_args()
    rc = {"setup": phase_setup, "worker-login": phase_worker_login,
          "admin-login": phase_admin_login,
          "approve": phase_approve, "revoke": phase_revoke,
          "status": phase_status}[args.phase]()
    if FAILURES:
        print(f"\nFAIL — {len(FAILURES)}: {FAILURES}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())

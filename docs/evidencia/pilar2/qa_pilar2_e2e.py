#!/usr/bin/env python3
"""QA Pilar 2 — Revisión e2e del panel admin por cliente (console de org completa).

Evidencia 100% real contra el stack vivo (API :8000, Keycloak :8080,
LiteLLM real, PostgreSQL compartida): provisioning de tenant fresco,
login REAL PKCE S256 + TOTP (acr=2), y ejercicio de los 10 puntos del
alcance por API. Nada simulado.

Reutiliza la maquinaria de scripts/revalidate_litellm_real.py.
"""
import importlib.util
import json
import sys
import time
import uuid
import hashlib
from pathlib import Path

SCRIPTS = Path("/root/trust-agent/platform/scripts")
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

STAMP = time.strftime("%H%M%S")
SLUG = f"pilar2-{STAMP}"
rv.SLUG = SLUG
ADMIN_EMAIL = f"admin@{SLUG}.test"
ADMIN_PW = "Pilar2Admin2026!"
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


def audit_actions(tenant_id, prefix=None):
    sql = "SELECT action FROM audit_log WHERE tenant_id=%s ORDER BY seq"
    rows = rv.db(sql, (tenant_id,))
    acts = [r[0] for r in rows]
    if prefix:
        return [a for a in acts if a.startswith(prefix)]
    return acts


def pkce_login2(username, password, *, new_password=None, totp_state=None,
                acr_values="2"):
    """Baile REAL PKCE S256 con acr_values (LoA 2) — lo que la pantalla de
    login real envía según /api/auth/org-config (spec 015 D-AUTH-08)."""
    import base64
    import hashlib
    import secrets
    import urllib.parse
    for k in list(rv.COOKIES):
        rv.COOKIES.pop(k)
    verifier = rv._b64url(secrets.token_bytes(64))
    challenge = rv._b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": rv.CLIENT_ID,
        "scope": "openid", "redirect_uri": rv.REDIRECT_URI,
        "state": "st-pilar2-qa", "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if acr_values:
        params["acr_values"] = acr_values
    st, body, hdrs = rv.kc_fetch(
        f"/realms/{rv.SLUG}/protocol/openid-connect/auth?"
        f"{urllib.parse.urlencode(params)}")
    assert st == 200 and 'action="' in body, f"authorize HTTP {st}: {body[:200]}"
    action = rv.form_action(body)
    st, body, hdrs = rv.kc_fetch(
        action.split(f"{rv.KC_HOST}:{rv.KC_PORT}")[-1], method="POST",
        data=urllib.parse.urlencode({
            "username": username, "password": password,
            "credentialId": ""}).encode())
    loc = hdrs.get("location", "")
    st, body, loc, hdrs = rv._hop(st, body, loc, hdrs)
    used_counter = None
    for _ in range(10):
        if "code=" in loc:
            break
        if not (st == 200 and 'action="' in body):
            break
        act = rv.form_action(body)
        if "password-new" in body and new_password:
            st, body, hdrs = rv.kc_fetch(
                act.split(f"{rv.KC_HOST}:{rv.KC_PORT}")[-1], method="POST",
                data=urllib.parse.urlencode({
                    "username": username, "password": password,
                    "password-new": new_password,
                    "password-confirm": new_password,
                    "credentialId": ""}).encode())
        elif 'name="totpSecret"' in body:
            secret = rv.totp_secret_of(body)
            if totp_state is not None:
                totp_state["secret"] = secret
            used_counter = int(time.time()) // 30
            code = rv.totp(secret, counter=used_counter)
            print(f"    [dance] enrolamiento TOTP real: code={code}",
                  flush=True)
            st, body, hdrs = rv.kc_fetch(
                act.split(f"{rv.KC_HOST}:{rv.KC_PORT}")[-1], method="POST",
                data=urllib.parse.urlencode({
                    "totp": code, "totpSecret": secret, "mode": "totp",
                    "userLabel": "pilar2-qa", "credentialId": ""}).encode())
        elif 'name="otp"' in body or 'name="totp"' in body:
            secret = (totp_state or {}).get("secret", "")
            counter = int(time.time()) // 30
            if counter == used_counter:
                counter += 1
            fields = rv.form_fields(body)
            code_field = "otp" if 'name="otp"' in body else "totp"
            fields[code_field] = rv.totp(secret, counter=counter)
            print(f"    [dance] 2º factor OTP real: {fields[code_field]}",
                  flush=True)
            st, body, hdrs = rv.kc_fetch(
                act.split(f"{rv.KC_HOST}:{rv.KC_PORT}")[-1], method="POST",
                data=urllib.parse.urlencode(fields).encode())
        else:
            st, body, hdrs = rv.kc_fetch(
                act.split(f"{rv.KC_HOST}:{rv.KC_PORT}")[-1], method="POST",
                data=b"")
        loc = hdrs.get("location", "")
        st, body, loc, hdrs = rv._hop(st, body, loc, hdrs)
    if "code=" not in loc:
        return None, None
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    st, raw, _ = rv.kc_fetch(
        f"/realms/{rv.SLUG}/protocol/openid-connect/token", method="POST",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": rv.CLIENT_ID,
            "redirect_uri": rv.REDIRECT_URI, "code": code,
            "code_verifier": verifier}).encode())
    assert st == 200, raw
    tokens = json.loads(raw)
    claims = json.loads(base64.urlsafe_b64decode(
        tokens["id_token"].split(".")[1] + "==").decode())
    return tokens["id_token"], claims.get("acr")



def main():
    # ================= P0: provisioning + login REAL =================
    print("=== P0: provisioning tenant fresco + login REAL (PKCE S256+TOTP) ===",
          flush=True)
    st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Pilar2 QA Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0,
        "model_plan": ["glm-5.2"],
        "slug": SLUG})
    check("P0a POST /api/admin/tenants → 201 (realm KC nuevo)", st == 201,
          f"HTTP {st} {raw[:200]}")
    tenant_id = body.get("tenant_id", body.get("tenant", {}).get("id", ""))
    ota = body["one_time_activation"]["one_time_password"]
    totp_state = {}
    id_token, acr = rv.pkce_login(ADMIN_EMAIL, ota, new_password=ADMIN_PW,
                                  totp_state=totp_state)
    check("P0b login REAL admin (PKCE S256, primer login sin TOTP forzado)",
          id_token is not None, f"acr={acr}")
    check("P0b2 MFA opcional por defecto (acr=1, require_mfa=false — "
          "feedback #10 / t_ccf901f4)", str(acr) == "1", f"acr={acr}")
    admin = rv.mint(id_token, "desktop")["session_token"]
    check("P0c sesión minteada por el control plane", admin.startswith("sts1."))
    st, me, raw = rv.api("GET", "/api/me", bearer=admin)
    check("P0d GET /api/me → org_admin", st == 200
          and "org_admin" in [r.get("role_key") for r in me.get("roles", [])],
          f"HTTP {st} {raw[:150]}")

    # ================= P1: Departments CRUD =================
    print("=== P1: Departments CRUD (spec 016) ===", flush=True)
    st, body, raw = rv.api("POST", "/api/departments", bearer=admin, payload={
        "name": "QA Pilar2 Operations", "initial_budget_cents": 5000})
    check("P1a POST /api/departments (nombre+budget inicial) → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    dept_id = body.get("department_id", "")
    st, body, raw = rv.api("PUT", f"/api/departments/{dept_id}", bearer=admin,
                           payload={"name": "QA Pilar2 Ops Renamed"})
    check("P1b PUT rename → 200", st == 200, f"HTTP {st} {raw[:120]}")
    st, body, raw = rv.api("POST", "/api/departments", bearer=admin,
                           payload={"name": "QA Pilar2 Empty"})
    empty_id = body.get("department_id", "")
    st, body, raw = rv.api("DELETE", f"/api/departments/{empty_id}",
                           bearer=admin)
    check("P1c DELETE depto vacío → 200/204", st in (200, 204),
          f"HTTP {st} {raw[:120]}")
    acts = audit_actions(tenant_id, "department.")
    check("P1d audit department.created/renamed/deleted en DB",
          "department.created" in acts and "department.renamed" in acts
          and "department.deleted" in acts, str(acts))

    # ================= P10 (parcial API): self-registration + org-config ==
    # (antes que P2/P3: necesitamos un usuario plain para escalada y budget)
    print("=== P10: Auth UX — org-config + auto-registro + MFA opcional ===",
          flush=True)
    st, cfg, raw = rv.api("GET", f"/api/auth/org-config?org={SLUG}")
    check("P10a GET /api/auth/org-config (público) → 200", st == 200,
          f"HTTP {st} {raw[:200]}")
    print("    org-config:", json.dumps(cfg), flush=True)
    reg_email = f"worker@{SLUG}.test"
    reg_pw = "Pilar2Worker2026!"
    st, body, raw = rv.api("POST", "/api/auth/register", payload={
        "org": SLUG, "email": reg_email, "password": reg_pw,
        "display_name": "Pilar2 Worker"})
    check("P10b POST /api/auth/register (auto-registro) → 201", st == 201,
          f"HTTP {st} {raw[:200]}")
    wtotp = {}
    w_id_token, w_acr = rv.pkce_login(reg_email, reg_pw, totp_state=wtotp)
    check("P10c login del auto-registrado SIN TOTP forzado (MFA opcional)",
          w_id_token is not None, f"id_token={'ok' if w_id_token else 'None'}")
    worker = rv.mint(w_id_token, "desktop")["session_token"]
    st, wme, raw = rv.api("GET", "/api/me", bearer=worker)
    check("P10d el auto-registrado entra con rol user (acr sin MFA)",
          st == 200 and [r.get("role_key") for r in wme.get("roles", [])]
          == ["user"], f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/me/notifications", bearer=worker)
    sug = raw.lower()
    check("P10e sugerencia de MFA presente (notifications/banner)",
          st == 200 and ("mfa" in sug or "authenticator" in sug),
          f"HTTP {st} {raw[:200]}")
    # Google IdP configurado en el realm (KC Admin REST)
    try:
        ktok = rv.kc_admin_token()
        st, raw = rv.kc_json("GET",
                             f"/admin/realms/{SLUG}/identity-provider/instances",
                             token=ktok)
        idps = json.loads(raw) if st == 200 else []
        check("P10f Google identity provider en el realm KC",
              any(p.get("providerId") == "google" for p in idps),
              str([p.get("providerId") for p in idps]))
    except Exception as e:
        check("P10f Google identity provider en el realm KC", False, repr(e))

    # ================= P2: Usuarios y roles =================
    print("=== P2: Usuarios y roles (spec 017) ===", flush=True)
    st, body, raw = rv.api("GET", "/api/users", bearer=admin)
    users = {u["email"]: u for u in body.get("users", [])}
    check("P2a GET /api/users lista admin + auto-registrado",
          st == 200 and reg_email in users and ADMIN_EMAIL in users,
          f"HTTP {st} {list(users)}")
    worker_id = users[reg_email]["user_id"]
    st, body, raw = rv.api("POST", f"/api/users/{worker_id}/roles",
                           bearer=admin, payload={
                               "role_key": "dept_admin",
                               "department_id": dept_id})
    check("P2b POST /api/users/{id}/roles (dept_admin scoped) → 201",
          st == 201, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("DELETE",
                           f"/api/users/{worker_id}/roles/dept_admin"
                           f"?department_id={dept_id}", bearer=admin)
    check("P2c DELETE /api/users/{id}/roles/dept_admin → 204", st == 204,
          f"HTTP {st} {raw[:120]}")
    st, body, raw = rv.api("PUT", f"/api/users/{worker_id}", bearer=admin,
                           payload={"department_id": dept_id})
    check("P2d PUT /api/users/{id} (asignar depto) → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", f"/api/users/{worker_id}/roles",
                           bearer=worker, payload={"role_key": "org_admin"})
    check("P2e escalada: user plain asigna roles → 403", st == 403,
          f"HTTP {st} {raw[:120]}")
    acts = audit_actions(tenant_id, "role.")
    check("P2f audit role.assigned/role.revoked en DB",
          "role.assigned" in acts and "role.revoked" in acts, str(acts))

    # ================= P3: Budgets jerárquicos =================
    print("=== P3: Budgets jerárquicos org→depto→usuario ===", flush=True)
    st, body, raw = rv.api("GET", "/api/budgets/overview", bearer=admin)
    check("P3a GET /api/budgets/overview → 200 con deptos y users anidados",
          st == 200 and "departments" in body, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("PUT", "/api/budgets/org", bearer=admin,
                           payload={"amount_cents": 100000})
    check("P3b PUT /api/budgets/org → 200", st == 200, f"HTTP {st} {raw[:120]}")
    st, body, raw = rv.api("PUT", f"/api/budgets/dept/{dept_id}",
                           bearer=admin, payload={"amount_cents": 40000})
    check("P3c PUT /api/budgets/dept → 200", st == 200, f"HTTP {st} {raw[:120]}")
    st, body, raw = rv.api("PUT", f"/api/budgets/dept/{dept_id}",
                           bearer=admin, payload={"amount_cents": 200000})
    check("P3d Σ deptos > org → 422 visible", st == 422,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("PUT", f"/api/budgets/user/{worker_id}",
                           bearer=admin, payload={"amount_cents": 10000})
    check("P3e PUT /api/budgets/user → 200 (edición por usuario)",
          st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("PUT", f"/api/budgets/user/{worker_id}",
                           bearer=admin, payload={"amount_cents": 60000})
    check("P3f Σ usuarios > depto → 422", st == 422, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("PUT", "/api/budgets/dept/no-es-uuid",
                           bearer=admin, payload={"amount_cents": 1})
    check("P3g id no-UUID → 422 (bug t_378503a1 curado)", st == 422,
          f"HTTP {st} {raw[:120]}")
    st, body, raw = rv.api("GET", "/api/budgets/history", bearer=admin)
    check("P3h GET /api/budgets/history → 200", st == 200,
          f"HTTP {st} {raw[:120]}")

    # ================= P4: Policies =================
    print("=== P4: Policies (org→depto→usuario, deny-overrides) ===",
          flush=True)
    st, body, raw = rv.api("GET", "/api/policies?level=org", bearer=admin)
    check("P4a GET /api/policies?level=org → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("PUT", "/api/policies/org", bearer=admin,
                           payload={"key": "allowed_models",
                                    "value": ["glm-5.2"]})
    check("P4b PUT /api/policies/org allowed_models → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/me/policies", bearer=admin)
    eff = json.dumps(body)
    check("P4c policy efectiva del admin refleja allowed_models",
          st == 200 and "glm-5.2" in eff, f"HTTP {st} {eff[:200]}")
    st, body, raw = rv.api("PUT", f"/api/policies/user/{worker_id}",
                           bearer=admin, payload={
                               "key": "allowed_models", "value": []})
    check("P4d PUT /api/policies/user (deny a nivel usuario) → 200",
          st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/me/policies", bearer=worker)
    check("P4e1 policy efectiva del worker (combinator) = allowed_models []",
          st == 200 and body.get("allowed_models") == [],
          f"HTTP {st} {raw[:200]}")
    st, body, raw = rv.api("GET", "/api/models/allowed-for-me", bearer=worker)
    check("P4e deny-overrides: usuario con deny → conjunto efectivo vacío",
          st == 200 and body.get("models") == [], f"HTTP {st} {raw[:150]}")
    if st == 200 and body.get("models"):
        print("    !! BUG: enforcement ignora allowed_models=[] "
              "(models/access.py:197-199 `or []` + truthiness) — el "
              "combinator dice deny-all pero el gate deja pasar "
              f"{body.get('models')}", flush=True)
    st, body, raw = rv.api("DELETE",
                           f"/api/policies/user/allowed_models"
                           f"?user_id={worker_id}&department_id={dept_id}",
                           bearer=admin)
    check("P4f DELETE policy de usuario (restaurar) → 204", st == 204,
          f"HTTP {st} {raw[:150]}")
    acts = audit_actions(tenant_id, "policy.")
    check("P4g audit policy.updated en DB", "policy.updated" in acts,
          str(acts[:8]))

    # ================= P5: Model access sobre MaaS oficial =================
    print("=== P5: Model access sobre MaaS oficial (spec 018) ===", flush=True)
    MAAS_MODELS = {"glm-5.3", "glm-5.2", "glm-5.1", "deepseek-v4.1-flash",
                   "deepseek-v4-flash", "qwen3-32b"}
    st, body, raw = rv.api("GET", "/api/models", bearer=admin)
    api_models = {m["model_name"]: m for m in body.get("models", [])}
    check("P5a GET /api/models == 6 modelos oficiales MaaS",
          st == 200 and set(api_models) == MAAS_MODELS,
          f"HTTP {st} {sorted(api_models)}")
    g52 = api_models.get("glm-5.2", {})
    check("P5b glm-5.2 precios USD/M oficiales (140/440¢, ctx 1M)",
          g52.get("context_tokens") == 1_000_000
          and float(g52.get("input_price_cents_per_m")) == 140.0
          and float(g52.get("output_price_cents_per_m")) == 440.0,
          json.dumps(g52))
    st, body, raw = rv.api("GET", "/api/models/access", bearer=admin)
    check("P5c GET /api/models/access con sección policy (approved_models_only)",
          st == 200 and body.get("policy", {}).get("approved_models_only")
          is True, f"HTTP {st} {raw[:200]}")
    st, body, raw = rv.api("PUT", "/api/models/access", bearer=admin,
                           payload={"model_name": "deepseek-v4-flash",
                                    "level": "org", "mode": "central"})
    check("P5d PUT /api/models/access aprueba deepseek-v4-flash → 201",
          st == 201, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", "/api/models/keys", bearer=admin,
                           payload={})
    check("P5e key virtual del tenant → 201", st == 201,
          f"HTTP {st} {raw[:120]}")
    vkey = body.get("key", "")
    st, body, raw = rv.litellm_chat(vkey, "glm-5.2",
                                    prompt="Responde solo: ok", max_tokens=2000)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    check("P5f invocación REAL glm-5.2 (key virtual→LiteLLM→MaaS) → 200",
          st == 200 and content, f"HTTP {st} {str(content)[:80]}")
    st, body, raw = rv.litellm_chat(vkey, "qwen3-32b", prompt="x",
                                    max_tokens=100)
    check("P5g modelo no aprobado con la misma key → 403 (policy)", st == 403,
          f"HTTP {st}")

    # ================= P6: Agentes default =================
    print("=== P6: Agentes default gobernados por policy (spec 020) ===",
          flush=True)
    st, body, raw = rv.api("GET", "/api/agent-profiles", bearer=admin)
    profiles = {p["key"] for p in body.get("profiles", [])}
    check("P6a GET /api/agent-profiles → 4 perfiles default",
          st == 200 and profiles == {"analyst", "coder", "ops", "support"},
          f"HTTP {st} {sorted(profiles)}")
    st, body, raw = rv.api("POST", "/api/agent-profiles/analyst/adopt",
                           bearer=admin, payload={})
    check("P6b adopt analyst → 201 (instancia tenant-scoped)", st == 201,
          f"HTTP {st} {raw[:150]}")
    agent_id = body.get("agent_id", body.get("agent", {}).get("agent_id", ""))
    st, body, raw = rv.api("POST", f"/api/agents/{agent_id}/invoke",
                           bearer=admin, payload={"model_name": "glm-5.2"})
    check("P6c invoke con modelo aprobado → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", f"/api/agents/{agent_id}/invoke",
                           bearer=admin, payload={"model_name": "gpt-4o"})
    check("P6d invoke con modelo NO aprobado → 403", st == 403,
          f"HTTP {st} {raw[:150]}")
    acts = audit_actions(tenant_id)
    check("P6e audit agent.profile_adopted + agent.invoked en DB",
          "agent.profile_adopted" in acts and "agent.invoked" in acts,
          str([a for a in acts if a.startswith("agent.")]))

    # ================= P7: Devices =================
    print("=== P7: Devices (enroll→approve→exchange + reject audit) ===",
          flush=True)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    def make_dev(i):
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = k.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        fp = hashlib.sha256(pem.encode()).hexdigest()
        return pem, f"qa-pilar2-device-{i}", fp

    pem1, name1, fp1 = make_dev(1)
    st, body, raw = rv.api("POST", "/api/devices/enroll", payload={
        "org_code": SLUG, "id_token": id_token, "public_key": pem1,
        "fingerprint": fp1, "device_name": name1})
    check("P7a0 enroll con id_token SIN MFA (acr=1) → 403 mfa_required",
          st == 403 and "mfa" in raw.lower(), f"HTTP {st} {raw[:150]}")
    # MFA opcional PERO exigible por policy: require_mfa=true → enrolar TOTP
    # del admin vía KC Admin REST (required action CONFIGURE_TOTP) → re-login
    st, body, raw = rv.api("PUT", "/api/policies/org", bearer=admin,
                           payload={"key": "require_mfa", "value": True})
    check("P7a1 PUT /api/policies/org require_mfa=true → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    ktok = rv.kc_admin_token()
    st, raw = rv.kc_json(
        "GET", f"/admin/realms/{SLUG}/users?email={ADMIN_EMAIL}"
        "&exact=true", token=ktok)
    kusers = json.loads(raw) if st == 200 else []
    check("P7a1b KC Admin REST: usuario admin localizado", bool(kusers),
          f"HTTP {st} {raw[:120]}")
    if kusers:
        st2, raw2 = rv.kc_json(
            "PUT", f"/admin/realms/{SLUG}/users/{kusers[0]['id']}",
            payload={"requiredActions": ["CONFIGURE_TOTP"]}, token=ktok)
        check("P7a1c required action CONFIGURE_TOTP asignado",
              st2 in (200, 204), f"HTTP {st2} {raw2[:120]}")
    id_token, acr = pkce_login2(ADMIN_EMAIL, ADMIN_PW, totp_state=totp_state,
                                acr_values="2")
    check("P7a2 re-login admin con acr_values=2 + TOTP real (acr=2)",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    # la sesión anterior fue minteada con acr=1: require_mfa=true la rechaza
    # (evidencia de que la policy se aplica a sesiones, no solo al enroll)
    admin = rv.mint(id_token, "desktop")["session_token"]
    st, body, raw = rv.api("GET", "/api/me", bearer=admin)
    check("P7a3 re-mint de sesión con MFA → /api/me ok (mfa_verified)",
          st == 200 and body.get("mfa_verified") is True,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", "/api/devices/enroll", payload={
        "org_code": SLUG, "id_token": id_token, "public_key": pem1,
        "fingerprint": fp1, "device_name": name1})
    check("P7a POST /api/devices/enroll (admin, id_token MFA) → 202",
          st == 202, f"HTTP {st} {raw[:200]}")
    enroll_tok_1 = body.get("enrollment_token", "")
    pem2, name2, fp2 = make_dev(2)
    st, body, raw = rv.api("POST", "/api/devices/enroll", payload={
        "org_code": SLUG, "id_token": id_token, "public_key": pem2,
        "fingerprint": fp2, "device_name": name2})
    enroll_tok_2 = body.get("enrollment_token", "")
    check("P7b 2º enrolamiento → 202", st == 202, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/devices", bearer=admin)
    devs = {d["device_name"]: d for d in body.get("devices", [])}
    check("P7c GET /api/devices lista los 2 pending", st == 200
          and name1 in devs and name2 in devs
          and devs[name1]["status"] == "pending",
          f"HTTP {st} {raw[:200]}")
    if name1 not in devs or name2 not in devs:
        print(f"FALLO EN CADENA: devices visibles={list(devs)}", flush=True)
        return 1
    dev1 = devs[name1].get("device_id") or devs[name1]["id"]
    dev2 = devs[name2].get("device_id") or devs[name2]["id"]
    st, body, raw = rv.api("POST", f"/api/devices/{dev1}/approve",
                           bearer=admin)
    check("P7d approve device 1 → 200", st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", f"/api/devices/{dev2}/reject",
                           bearer=admin)
    check("P7e reject device 2 → 200", st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", "/api/devices/enroll/exchange",
                           payload={"enrollment_token": enroll_tok_1})
    check("P7f exchange del token one-time → device token (tras approve)",
          st == 200 and body.get("device_token", ""), f"HTTP {st} {raw[:150]}")
    acts = audit_actions(tenant_id, "device.")
    check("P7g audit device.enrolled/approved/rejected (F4: reject≠revoked)",
          "device.enrolled" in acts and "device.approved" in acts
          and "device.rejected" in acts, str(acts))

    # ================= P8: Audit con análisis =================
    print("=== P8: Audit — filtros, verify, export ===", flush=True)
    st, body, raw = rv.api("GET", "/api/audit/events?action=department.created",
                           bearer=admin)
    items = body.get("items", [])
    check("P8a GET /api/audit/events?action=… filtra (n>0, solo esa acción)",
          st == 200 and len(items) > 0
          and all(e["action"] == "department.created" for e in items),
          f"HTTP {st} n={len(items)} total={body.get('total')}")
    st, body, raw = rv.api("GET",
                           f"/api/audit/events?actor_id={worker_id}",
                           bearer=admin)
    items = body.get("items", [])
    check("P8b filtro por actor (n>0, solo ese actor)", st == 200
          and len(items) > 0
          and all(e.get("actor_id") == worker_id for e in items),
          f"HTTP {st} n={len(items)} total={body.get('total')}")
    st, body, raw = rv.api("GET", "/api/audit/events?q=analyst", bearer=admin)
    items = body.get("items", [])
    check("P8b2 búsqueda de texto libre q=analyst (n>0)", st == 200
          and len(items) > 0, f"HTTP {st} n={len(items)}")
    st, body, raw = rv.api("POST", "/api/admin/audit/verify", bearer=admin)
    check("P8c POST /api/admin/audit/verify → cadena íntegra",
          st == 200 and body.get("status") == "ok", f"HTTP {st} {raw[:200]}")
    st, body, raw = rv.api("GET", "/api/audit/events/export?format=csv",
                           bearer=admin)
    check("P8d export CSV con hashes de la cadena", st == 200
          and "hash_prev" in raw and "hash" in raw,
          f"HTTP {st} {raw[:120]}")

    # ================= P9: Branding =================
    print("=== P9: Branding white-label ===", flush=True)
    st, body, raw = rv.api("GET", "/api/branding", bearer=admin)
    check("P9a GET /api/branding → 200 con defaults del proveedor",
          st == 200 and body.get("product_name") == "Trust Agent"
          and body.get("source") == "provider", f"HTTP {st} {raw[:200]}")
    st, body, raw = rv.api("PUT", "/api/branding", bearer=admin, payload={
        "product_name": "Pilar2 Corp AI", "accent_color": "#0F766E",
        "tagline": "Governed AI for Pilar2"})
    check("P9b PUT /api/branding (3 claves canónicas) → 200", st == 200,
          f"HTTP {st} {raw[:200]}")
    st, body, raw = rv.api("GET", "/api/branding", bearer=admin)
    check("P9c persistencia: GET refleja el branding editado",
          body.get("product_name") == "Pilar2 Corp AI"
          and body.get("accent_color") == "#0F766E"
          and body.get("source") == "tenant", raw[:200])
    acts = audit_actions(tenant_id, "branding.")
    check("P9d audit branding.updated en DB", "branding.updated" in acts,
          str(acts))

    # ================= Resumen =================
    print(f"\n{'=' * 60}\nQA Pilar 2 (API, stack vivo): {PASSES} PASS, "
          f"{len(FAILURES)} FAIL", flush=True)
    print("tenant:", SLUG, tenant_id, flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

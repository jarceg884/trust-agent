#!/usr/bin/env python3
"""Evidencia REAL de la spec 024 (custom connections) contra el stack vivo.

Journey completo del entregable 5, todo contra el control plane de compose
(127.0.0.1:8000, imagen trust-agent/platform:local), Keycloak 26 real y la
PostgreSQL compartida (RLS activa, migraciones 0012..0014 aplicadas):

  1. Proveedor real provisiona el tenant "custom-conn" (POST /api/admin/tenants).
  2. Login REAL del admin (PKCE S256 + CONFIGURE_TOTP + acr=2) → sesión MFA.
  3. Invite async del worker (jobs-worker real) → primer login real del worker.
  4. CRUD de conexión personalizada (database) + one-time secret + 422s.
  5. Policy allowed_connections en los 3 niveles (deny-overrides):
     org permite → depto estrecha → usuario la usa (403/200) → removida.
  6. Bundle JWS con claim allowed_connections, verificado RS256 contra el
     JWKS público del control plane (device real enrolado + aprobado).
  7. Audit connection.* en la DB compartida + cadena inmutable.

Nada simulado. Salida: PASS/FAIL por check + state.json (cookies de sesión
del admin para la evidencia de navegador de la UI).

Uso: cd platform && .venv/bin/python \
     ../docs/evidencia/custom-connections/evidence_custom_connections.py
"""
import base64
import json
import sys
import time

sys.path.insert(0, "/root/trust-agent/platform/scripts")
sys.path.insert(0, "/root/trust-agent/platform")

import http.client  # noqa: E402
import e2e_final_live as e2e  # noqa: E402  (patrón de login real compartido)

SLUG = "custom-conn"
ADMIN_EMAIL = "admin@custom-conn.test"
ADMIN_PASSWORD = "CustomConn2026!pass"
WORKER_EMAIL = "worker@custom-conn.test"
WORKER_PASSWORD = "CustomWorker2026!pass"
OUT = ("/root/.hermes/kanban/boards/trust-agent/workspaces/t_b16825d0/"
       "state.json")

with open("/root/trust-agent/.env") as fh:
    for line in fh:
        if line.startswith("TRUST_PROVIDER_TOKEN="):
            e2e.PROVIDER_TOKEN = line.partition("=")[2].strip()
e2e.SLUG = SLUG
e2e.REDIRECT_URI = "http://127.0.0.1:3100/auth/callback"

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        failures.append(name)
    return cond


def mint(id_token, kind):
    st, body, raw = e2e.api("POST", "/api/auth/session",
                            {"id_token": id_token, "kind": kind})
    assert st == 201, f"mint HTTP {st}: {raw[:300]}"
    return body


def bearer(session):
    return {"Authorization": f"Bearer {session['session_token']}"}


def b64url_json(seg):
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


def main():
    state = {}

    # --- 1. Provisión real del tenant --------------------------------------
    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Custom Connections Evidence Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 500.0, "model_plan": "standard",
        "slug": SLUG})
    if st == 409:
        print(f"[reprovision] tenant {SLUG} ya existe — se reutiliza")
        ota = None
        tenant_id = e2e.db("SELECT id FROM tenants WHERE slug = %s",
                           (SLUG,))[0][0]
    else:
        assert st == 201, f"provision HTTP {st}: {raw[:300]}"
        tenant_id = body["tenant"]["id"]
        ota = body["one_time_activation"]["one_time_password"]
    state["tenant_id"] = str(tenant_id)
    check("provisión real del tenant de evidencia", True, str(tenant_id)[:8])

    # --- 2. Login REAL del admin (PKCE + TOTP, acr=2) ----------------------
    totp_state = {}
    if ota is not None:
        id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ota,
                                       new_password=ADMIN_PASSWORD,
                                       totp_state=totp_state)
    else:
        id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD,
                                       totp_state=totp_state)
    check("login REAL admin (PKCE S256 + TOTP) → acr=2",
          id_token not in (None, "MFA_REQUIRED") and str(acr) == "2",
          f"acr={acr}")
    admin = mint(id_token, "admin")
    check("sesión admin MFA minteada", admin["mfa_verified"] is True)
    admin_h = bearer(admin)
    state["admin_cookies"] = {
        "ta_console_session": admin["session_token"],
        "ta_console_refresh": admin["refresh_token"],
        "ta_console_csrf": admin["csrf_token"],
    }

    st, me, _ = e2e.api("GET", "/api/me", headers=admin_h)
    check("GET /api/me → org_admin con connections.manage",
          st == 200 and "connections.manage" in me.get("permissions", []),
          f"HTTP {st}")
    admin_id = me["user_id"]

    # --- 3. Departamento + invite async del worker --------------------------
    st, body, raw = e2e.api("GET", "/api/departments", headers=admin_h)
    assert st == 200, f"departments HTTP {st}: {raw[:200]}"
    dept_it = next(d["id"] for d in body["departments"]
                   if "IT" in d["name"] or "Engineering" in d["name"])
    state["dept_it"] = dept_it

    st, body, raw = e2e.api("POST", "/api/users", payload={
        "email": WORKER_EMAIL, "department_id": dept_it, "role_key": "user",
        "display_name": "Conn Worker"}, headers=admin_h)
    check("POST /api/users (invite async) → 202 job encolado",
          st == 202 and body.get("job_id"), f"HTTP {st} {raw[:200]}")
    job_id = body["job_id"]

    worker_ota = None
    for _ in range(40):
        st, job, raw = e2e.api("GET", f"/api/jobs/{job_id}",
                               headers=admin_h)
        assert st == 200, f"job HTTP {st}: {raw[:200]}"
        if job.get("status") == "succeeded":
            worker_ota = (job.get("result") or {}).get("one_time_password")
            break
        if job.get("status") == "failed":
            break
        time.sleep(1)
    check("jobs-worker real creó al worker (Keycloak + DB) → OTA entregado",
          worker_ota is not None, f"status={job.get('status')}")

    wtotp = {}
    w_id_token, w_acr = e2e.pkce_login(WORKER_EMAIL, worker_ota,
                                       new_password=WORKER_PASSWORD,
                                       totp_state=wtotp)
    check("primer login REAL del worker (OTA → password + TOTP, acr=2)",
          w_id_token not in (None, "MFA_REQUIRED") and str(w_acr) == "2",
          f"acr={w_acr}")
    worker = mint(w_id_token, "desktop")
    worker_h = bearer(worker)
    st, wme, _ = e2e.api("GET", "/api/me", headers=worker_h)
    worker_id = wme["user_id"]
    check("worker sin connections.manage (RBAC data-driven)",
          st == 200 and "connections.manage" not in wme.get("permissions", []))

    # --- 4. CRUD de conexión personalizada ---------------------------------
    st, body, raw = e2e.api("POST", "/api/vault/connections", payload={
        "name": "Analytics DB", "kind": "database",
        "metadata": {"type": "postgresql", "host": "analytics.internal",
                     "port": 5432, "database": "analytics",
                     "username": "analyst"},
        "secret_value": "pg-S3cret-024"}, headers=admin_h)  # gitleaks:allow fixture FALSO (dummy de entrada al vault de un tenant de dev; el vault lo cifra y nunca lo devuelve)
    check("POST /api/vault/connections (database) → 201 + secret one-time",
          st == 201 and body.get("secret_value") == "pg-S3cret-024"
          and body.get("status") == "active",
          f"HTTP {st} {raw[:200]}")
    conn_id = body["connection_id"]
    state["connection_id"] = conn_id

    st, body, raw = e2e.api("GET", "/api/vault/connections", headers=admin_h)
    row = next((c for c in body["connections"] if c["id"] == conn_id), {})
    check("GET list → metadata SIN secreto ni ciphertext",
          st == 200 and "secret_value" not in row
          and "ciphertext_secret" not in row
          and row.get("metadata", {}).get("host") == "analytics.internal",
          f"HTTP {st} keys={sorted(row)[:8]}")

    st, body, raw = e2e.api("POST", "/api/vault/connections", payload={
        "name": "Bad DB", "kind": "database",
        "metadata": {"type": "postgresql", "host": "x", "port": 5432,
                     "database": "x", "username": "x", "password": "no"},
        "secret_value": "x"}, headers=admin_h)
    check("metadata con clave secreta → 422 (el secreto vive cifrado aparte)",
          st == 422, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("POST", "/api/vault/connections", payload={
        "name": "Bad kind", "kind": "ftp",
        "metadata": {}, "secret_value": "x"}, headers=admin_h)
    check("kind desconocido → 422", st == 422, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("POST", "/api/vault/connections", payload={
        "name": "Nope", "kind": "api_key",
        "metadata": {"service": "svc", "auth": "bearer"},
        "secret_value": "x"}, headers=worker_h)
    check("worker (sin connections.manage) crea → 403",
          st == 403, f"HTTP {st} {raw[:160]}")

    # --- 5. Policy allowed_connections: 3 niveles, deny-overrides -----------
    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("uso ANTES de policy → 403 (custom siempre requiere id explícito)",
          st == 403, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("PUT", "/api/policies/org", payload={
        "key": "allowed_connections", "value": [conn_id]}, headers=admin_h)
    check("PUT org allowed_connections=[id] → 200",
          st == 200, f"HTTP {st} {raw[:200]}")

    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("worker USA la conexión permitida en org → 200 + secreto inyectado",
          st == 200 and body.get("secret_value") == "pg-S3cret-024",
          f"HTTP {st} {raw[:200]}")

    st, body, raw = e2e.api("PUT", f"/api/policies/dept/{dept_it}",
                            payload={"key": "allowed_connections",
                                     "value": []}, headers=admin_h)
    check("depto ESTRECHA a [] → 200", st == 200, f"HTTP {st} {raw[:200]}")

    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("worker del depto estrechado → 403 connector_not_allowed",
          st == 403 and "not allowed" in raw, f"HTTP {st} {raw[:160]}")

    st, _, raw = e2e.api("DELETE",
                         f"/api/policies/dept/allowed_connections"
                         f"?department_id={dept_it}", headers=admin_h)
    check("DELETE policy de depto → 204", st == 204, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("uso de nuevo permitido (solo org) → 200", st == 200,
          f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("PUT", f"/api/policies/user/{worker_id}",
                            payload={"key": "allowed_connections",
                                     "value": []}, headers=admin_h)
    check("nivel usuario estrecha a [] → 200", st == 200,
          f"HTTP {st} {raw[:200]}")

    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("worker estrechado por nivel usuario → 403", st == 403,
          f"HTTP {st} {raw[:160]}")

    st, _, raw = e2e.api("DELETE",
                         f"/api/policies/user/allowed_connections"
                         f"?user_id={worker_id}", headers=admin_h)
    check("DELETE policy de usuario → 204", st == 204, f"HTTP {st} {raw[:160]}")

    st, _, raw = e2e.api("DELETE", "/api/policies/org/allowed_connections",
                         headers=admin_h)
    check("org REMUEVE la conexión de la policy → 204 (sweep en la misma tx)",
          st == 204, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("tras remover de la policy → 403 (corte inmediato de facto)",
          st == 403, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("PUT", "/api/policies/org", payload={
        "key": "allowed_connections", "value": ["token-inexistente"]},
        headers=admin_h)
    check("token desconocido en el upsert → 422 (typo-protection D-24-07)",
          st == 422, f"HTTP {st} {raw[:160]}")

    st, body, raw = e2e.api("PUT", "/api/policies/org", payload={
        "key": "allowed_connections", "value": [conn_id]}, headers=admin_h)
    check("re-permitir en org para el bundle → 200", st == 200,
          f"HTTP {st} {raw[:160]}")

    # --- 6. Bundle JWS con claim allowed_connections (device real) ----------
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pk = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    st, body, raw = e2e.api("POST", "/api/devices/enroll", payload={
        "org_code": SLUG, "id_token": w_id_token, "public_key": pk,
        "fingerprint": "fp-custom-conn-024", "device_name": "conn-laptop",
        "platform": "linux"})
    check("device enroll real del worker → 202 pending",
          st == 202 and body.get("status") == "pending", f"HTTP {st}")
    device_id, enr = body["device_id"], body["enrollment_token"]

    st, _, _ = e2e.api("POST", f"/api/devices/{device_id}/approve",
                       headers=admin_h)
    check("admin aprueba el device → 200", st == 200, f"HTTP {st}")
    st, body, raw = e2e.api("POST", "/api/devices/enroll/exchange",
                            {"enrollment_token": enr})
    check("canje one-time del enrollment_token → device_token",
          st == 200 and body.get("device_token"), f"HTTP {st}")
    device_token = body["device_token"]

    st, body, raw = e2e.api("GET", "/api/me/governance-bundle", headers={
        **worker_h, "X-Device-Token": device_token})
    check("GET /api/me/governance-bundle → 200 JWS", st == 200,
          f"HTTP {st} {raw[:160]}")
    bundle = body["bundle"]

    import jwt  # PyJWT del venv de platform
    st, jwks_doc, _ = e2e.api(
        "GET", "/.well-known/policy-jwks.json")
    pub = jwks_doc["keys"][0]
    claims = jwt.decode(bundle, key=jwt.PyJWK(pub).key, algorithms=["RS256"],
                        audience=device_id)
    eff = claims.get("allowed_connections") or {}
    check("bundle verifica RS256 contra el JWKS público y lleva el claim "
          "allowed_connections con la conexión (catalog+custom)",
          isinstance(eff.get("custom"), list)
          and any(c.get("id") == conn_id for c in eff["custom"])
          and "secret_value" not in json.dumps(eff),
          f"claim={json.dumps(eff)[:200]}")

    # --- 7. Revocación + audit + cadena -------------------------------------
    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/revoke",
                            headers=admin_h)
    check("POST revoke → 200 (irreversible)", st == 200, f"HTTP {st}")
    st, body, raw = e2e.api("POST",
                            f"/api/vault/connections/{conn_id}/use",
                            headers=worker_h)
    check("uso de conexión REVOCADA → 410 gone", st == 410,
          f"HTTP {st} {raw[:160]}")

    rows = e2e.db(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = CAST(%s AS uuid) AND action LIKE 'connection.%%' "
        "ORDER BY seq", (str(tenant_id),))
    actions = [r[0] for r in rows]
    check("audit connection.created/updated NO (solo created)/used/revoked "
          "presentes", "connection.created" in actions
          and "connection.used" in actions and "connection.revoked" in actions,
          str(actions))
    check("audit SIN secreto en ningún payload",
          all("pg-S3cret-024" not in (r[1] or "") for r in rows))
    chain = e2e.db("SELECT audit_verify_chain(CAST(%s AS uuid))",
                   (str(tenant_id),))[0][0]
    chain_status = chain[0] if isinstance(chain, (tuple, list)) else chain
    check("cadena de audit del tenant → ok", chain_status == "ok", str(chain))

    # --- 8. Estado para la evidencia de navegador ---------------------------
    # Conexión viva adicional para la UI (la anterior quedó revocada a propósito).
    st, body, raw = e2e.api("POST", "/api/vault/connections", payload={
        "name": "Payments API", "kind": "api_key",
        "metadata": {"service": "payments-svc", "auth": "bearer"},
        "secret_value": "sk-live-024-UI"}, headers=admin_h)
    check("conexión api_key para la evidencia UI → 201 one-time",
          st == 201 and body.get("secret_value") == "sk-live-024-UI",
          f"HTTP {st} {raw[:160]}")
    state["ui_connection_id"] = body["connection_id"]
    state.update({
        "slug": SLUG, "admin_email": ADMIN_EMAIL, "worker_id": worker_id,
        "dept_it": dept_it, "device_id": device_id,
    })
    with open(OUT, "w") as fh:
        json.dump(state, fh, indent=2)
    print(f"[ok] estado guardado en {OUT}")

    print()
    if failures:
        print(f"EVIDENCE FAIL — {len(failures)} fallo(s): {failures}")
        return 1
    print("EVIDENCE PASS — spec 024 verificada contra el stack vivo: "
          "provisión + logins reales PKCE/TOTP + CRUD one-time + policy "
          "3 niveles deny-overrides + bundle JWS con claim + audit íntegro")
    return 0


if __name__ == "__main__":
    sys.exit(main())

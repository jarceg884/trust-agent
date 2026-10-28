#!/usr/bin/env python3
"""Setup QA gobernanza E2E (t_a296ae9b): tenant fresco qa-gov-e2e.

Real, nada simulado:
  1. POST /api/admin/tenants (provisioning real: realm KC, deptos seed,
     presupuesto org, políticas default).
  2. Login admin REAL PKCE S256 + enrolamiento TOTP real (RFC 6238 base32,
     fix verificado de t_877b0cb5) → acr=2 → sesión minteada.
  3. Invita 2 workers (POST /api/users → job → Keycloak real) en 2 deptos
     distintos; login real de cada worker (password nueva + TOTP real).
  4. Guarda estado en gov_state.json para los pasos siguientes.
"""
import importlib.util
import json
import time

SPEC = importlib.util.spec_from_file_location(
    "e2e", "/root/trust-agent/platform/scripts/e2e_final_live.py")
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)

SLUG = "qa-gov-e2e"
ADMIN_EMAIL = "admin@qa-gov-e2e.test"
ADMIN_NEW_PASSWORD = "QaGovAdmin2026!"
W1_EMAIL = "w1@qa-gov-e2e.test"
W2_EMAIL = "w2@qa-gov-e2e.test"
W_PASSWORD = "QaGovWorker2026!"

e2e.SLUG = SLUG

# FIX TOTP RFC 6238 base32 (KC26 SHA1/6/30) — verificado en vivo t_877b0cb5
import base64 as _b64, hashlib as _hl, hmac as _hm, struct as _st


def totp_b32(secret: str, counter: int | None = None) -> str:
    """RFC 6238 — la clave HMAC es base32-decode si el secret es base32
    válido (caso qa-console, t_877b0cb5) o bytes UTF-8 raw en caso
    contrario (caso KC26 con secrets no-base32, verificado en
    e2e_final_live 79/79). KC26 acepta ambos al validar el OTP."""
    s = secret.strip().replace(" ", "")
    try:
        pad = s.upper() + "=" * (-len(s) % 8)
        key = _b64.b32decode(pad)
    except Exception:
        key = s.encode("utf-8")
    c = int(time.time()) // 30 if counter is None else counter
    digest = _hm.new(key, _st.pack(">Q", c), _hl.sha1).digest()
    off = digest[-1] & 0x0F
    return str((_st.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 10**6).zfill(6)


e2e.totp = totp_b32

S = {}
ok = True


def check(name, cond, detail=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if not cond:
        ok = False
    return cond


# --- 1) provisioning real (idempotente: reanuda si ya existe) ---------------
st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
    "Authorization": "Bearer test-provider-token"}, payload={
    "organization_name": "QA Governance E2E Corp", "admin_email": ADMIN_EMAIL,
    "initial_budget_usd": 100.0, "model_plan": "standard", "slug": SLUG})
if st == 201:
    check("S1 provisioning real POST /api/admin/tenants → 201",
          body.get("created") is True, f"HTTP {st} {raw[:200]}")
    S["tenant_id"] = body["tenant"]["id"]
    S["ota_admin"] = body["one_time_activation"]["one_time_password"]
    check("S1 tenant active + realm real",
          body["tenant"]["status"] == "active"
          and body["tenant"]["oidc_issuer_url"].endswith(f"/realms/{SLUG}"),
          body["tenant"]["oidc_issuer_url"])
    check("S1 healthcheck provisioning ok", body["healthcheck"]["status"] == "ok")
    fresh = True
elif st in (409, 200):
    print(f"[setup] tenant ya existía (HTTP {st}) — reanudando")
    S["tenant_id"] = body["tenant"]["id"]
    fresh = False
else:
    raise SystemExit(f"provisioning HTTP {st}: {raw[:300]}")

rows = e2e.db("SELECT id, name, profile_template FROM departments WHERE tenant_id = %s "
              "ORDER BY name", (S["tenant_id"],))
print(f"[setup] departamentos seed: {[(str(r[0]), r[1], r[2]) for r in rows]}")
S["departments"] = [{"id": str(r[0]), "name": r[1], "profile": r[2]} for r in rows]
check("S1 4 departamentos seed", len(rows) == 4, f"n={len(rows)}")

rows = e2e.db("SELECT amount_cents FROM budgets WHERE tenant_id = %s AND level = 'org'",
              (S["tenant_id"],))
check("S1 presupuesto org inicial $100 (10000¢)", rows[0][0] == 10000, f"{rows[0][0]}¢")

rows = e2e.db("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id "
              "WHERE u.tenant_id = %s AND r.role_key = 'org_admin'", (S["tenant_id"],))
S["admin_user_id"] = str(rows[0][0])

# --- 2) login admin real PKCE + TOTP (acr_values=2: el realm exige LoA 2) ---
_wait = 30 - (int(time.time()) % 30) + 1
print(f"[setup] esperando {_wait}s por ventana TOTP fresca…", flush=True)
time.sleep(_wait)
totp_state = {}
id_token, acr = e2e.pkce_login(
    ADMIN_EMAIL, S["ota_admin"] if fresh else ADMIN_NEW_PASSWORD,
    new_password=ADMIN_NEW_PASSWORD if fresh else None,
    totp_state=totp_state, acr_values="2")
check("S2 login admin REAL (PKCE + UPDATE_PASSWORD + CONFIGURE_TOTP) → code",
      id_token is not None)
check("S2 realm emite acr=2 (password+OTP reales)", str(acr) == "2", f"acr={acr}")
S["admin_totp_secret"] = totp_state["secret"]
admin = e2e.mint(id_token, kind="desktop")
check("S2 sesión admin minteada mfa_verified", admin["mfa_verified"] is True)
S["admin_session"] = admin["session_token"]
st, me, raw = e2e.api("GET", "/api/me", bearer=S["admin_session"])
check("S2 GET /api/me → 200 org_admin", st == 200 and me["email"] == ADMIN_EMAIL,
      f"HTTP {st}")

# --- 3) invitar 2 workers en deptos distintos ------------------------------
dept_a = next(d for d in S["departments"] if d["profile"] == "it_operations")
dept_b = next(d for d in S["departments"] if d["id"] != dept_a["id"])
S["dept_a"], S["dept_b"] = dept_a, dept_b
print(f"[setup] depto A (IT) = {dept_a['name']} {dept_a['id']}")
print(f"[setup] depto B     = {dept_b['name']} {dept_b['id']}")

for tag, email, dept in (("w1", W1_EMAIL, dept_a), ("w2", W2_EMAIL, dept_b)):
    st, body, raw = e2e.api("POST", "/api/users", payload={
        "email": email, "department_id": dept["id"], "role_key": "user",
        "display_name": f"QA {tag}"}, bearer=S["admin_session"])
    if st == 202:
        check(f"S3 invite {tag} ({email}, depto {dept['name']}) → 202", True,
              f"HTTP {st}")
        otp = None
        for _ in range(30):
            rows = e2e.db(
                "SELECT status, result FROM jobs WHERE tenant_id = %s "
                "AND job_type = 'user.invite' AND payload->>'email' = %s "
                "ORDER BY created_at DESC LIMIT 1", (S["tenant_id"], email))
            if rows and rows[0][0] in ("done", "succeeded"):
                otp = rows[0][1].get("one_time_password") if isinstance(rows[0][1], dict) else None
                if otp:
                    break
            time.sleep(2)
        check(f"S3 job user.invite {tag} done + one-time password", bool(otp))
        S[f"{tag}_ota"] = otp
        S[f"{tag}_fresh"] = True
    elif st == 422 and "exist" in raw.lower():
        print(f"[setup] {tag} ya invitado (corrida anterior) — login con password fija")
        S[f"{tag}_fresh"] = False
    else:
        check(f"S3 invite {tag} → 202", False, f"HTTP {st} {raw[:200]}")

# login real de cada worker (password nueva + TOTP enrolado, LoA 2)
for tag, email in (("w1", W1_EMAIL), ("w2", W2_EMAIL)):
    _wait = 30 - (int(time.time()) % 30) + 1
    time.sleep(_wait)
    tstate = {}
    id_token, acr = e2e.pkce_login(
        email, S[f"{tag}_ota"] if S.get(f"{tag}_fresh") else W_PASSWORD,
        new_password=W_PASSWORD if S.get(f"{tag}_fresh") else None,
        totp_state=tstate, acr_values="2")
    check(f"S3 login REAL {tag} (PKCE+password nueva+TOTP) → acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    sess = e2e.mint(id_token, kind="desktop")
    S[f"{tag}_session"] = sess["session_token"]
    S[f"{tag}_totp_secret"] = tstate["secret"]
    rows = e2e.db("SELECT id, department_id FROM users WHERE tenant_id = %s AND email = %s",
                  (S["tenant_id"], email))
    S[f"{tag}_user_id"] = str(rows[0][0])
    check(f"S3 {tag} en directorio, depto correcto",
          str(rows[0][1]) == (S["dept_a"] if tag == "w1" else S["dept_b"])["id"])

with open("/root/.hermes/kanban/boards/trust-agent/workspaces/t_ef306f32/gov_state.json", "w") as f:
    json.dump(S, f, indent=2)
print("\nSETUP " + ("OK" if ok else "CON FALLOS") + " — estado en gov_state.json")
raise SystemExit(0 if ok else 1)

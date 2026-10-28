#!/usr/bin/env python3
"""Evidencia en vivo — spec 026 (Self-signup de organizaciones, mandato Miller).

Journey completo REQ-SS-007 contra el stack real (nada simulado):
  S1  POST /api/auth/signup-org PÚBLICO → 201; tenant pending_activation
  S2  El provider ve el tenant pending con budget org 0¢
  S3  Login REAL del admin (PKCE S256 + TOTP inline contra Keycloak 26)
  S4  Fail-closed: /api/me y toda la superficie de negocio → 423; logout → 204
  S5  Validaciones: slug duplicado → 409; payload inválido → 422; abuso → 429
  S6  El provider activa + asigna budget (UNA transacción, 2 audits +
      notificación); errores 409/422/401
  S7  El admin re-entra: /api/me → 200; notificación tenant_activated visible
  S8  Spend REAL: invocación glm-5.2 vía key virtual → proxy LiteLLM → MaaS;
      el budget org descuenta y el corte sigue armado

Reutiliza la maquinaria de scripts/revalidate_litellm_real.py (login PKCE
S256 + TOTP real, cliente del API, chat vía el proxy LiteLLM real).

Uso: .venv/bin/python scripts/evidence_tenant_self_signup.py --slug <slug>
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True,
                    help="slug nuevo por corrida (el signup es idempotente "
                         "por slug: 409 si ya existe)")
    ap.add_argument("--budget-usd", type=float, default=75.5)
    args = ap.parse_args()
    slug = args.slug
    setattr(rv, "SLUG", slug)
    admin_email = f"founder@{slug}.test"
    admin_pw = "MillerFounder2026!"
    admin_name = "Miller Founder"
    org_name = "Miller SelfSignup Corp"

    print("=== S1: signup público de organización (REQ-SS-001) ===", flush=True)
    st, body, raw = rv.api("POST", "/api/auth/signup-org", payload={
        "organization_name": org_name, "slug": slug,
        "admin_email": admin_email, "admin_password": admin_pw,
        "admin_display_name": admin_name})
    check("S1a POST /api/auth/signup-org (público) → 201",
          st == 201, f"HTTP {st} {raw[:180]}")
    check("S1b respuesta: status=pending_activation, sin credenciales",
          body.get("status") == "pending_activation"
          and "one_time" not in raw and "password" not in raw.lower(),
          json.dumps(body)[:180])

    print("=== S2: el provider ve el tenant APAGADO y SIN budget ===",
          flush=True)
    st, body, raw = rv.api("GET", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"})
    entry = next((t for t in body.get("tenants", [])
                  if t.get("slug") == slug), None)
    check("S2a GET /api/admin/tenants lista el tenant pending_activation",
          st == 200 and entry is not None
          and entry.get("status") == "pending_activation",
          f"HTTP {st} {json.dumps(entry)[:180] if entry else raw[:120]}")
    check("S2b budget org del tenant nuevo = 0¢ (nace SIN budget)",
          entry is not None and entry.get("org_budget_cents") == 0,
          f"org_budget_cents={entry.get('org_budget_cents') if entry else '?'}")

    print("=== S3: login REAL del admin (PKCE S256 + TOTP inline) ===",
          flush=True)
    totp_state = {}
    id_token, acr = rv.pkce_login(admin_email, admin_pw, totp_state=totp_state)
    check("S3a baile OIDC real del admin contra el realm nuevo",
          id_token is not None, f"acr={acr}")
    minted = rv.mint(id_token)
    session = {"Authorization": f"Bearer {minted['session_token']}"}

    print("=== S4: fail-closed — el tenant APAGADO no funciona (REQ-SS-002) ===",
          flush=True)
    surface = ["/api/me", "/api/models", "/api/agents", "/api/devices",
               "/api/budgets/overview", "/api/policies?level=org",
               "/api/vault/credentials", "/api/departments"]
    all_423 = True
    details = []
    for path in surface:
        st, body, raw = rv.api("GET", path, bearer=session[
            "Authorization"].split(" ")[1])
        ok = st == 423 and "tenant_pending_activation" in raw
        details.append(f"{path.split('?')[0]}:{st}")
        all_423 = all_423 and ok
    check("S4a TODA la superficie de negocio responde 423 "
          "tenant_pending_activation", all_423, " ".join(details))
    st, body, raw = rv.api("POST", "/api/auth/logout",
                           bearer=minted["session_token"])
    check("S4b POST /api/auth/logout → 204 (el usuario puede salir)",
          st == 204, f"HTTP {st}")

    print("=== S5: validaciones del signup público (AC-26-002) ===",
          flush=True)
    st, body, raw = rv.api("POST", "/api/auth/signup-org", payload={
        "organization_name": org_name, "slug": slug,
        "admin_email": f"other@{slug}.test",
        "admin_password": "AnotherStrong2026!",
        "admin_display_name": "Other"})
    check("S5a slug duplicado → 409", st == 409, f"HTTP {st}")
    st, body, raw = rv.api("POST", "/api/auth/signup-org", payload={
        "organization_name": org_name, "slug": f"{slug}-x",
        "admin_email": f"weak@{slug}.test", "admin_password": "short",
        "admin_display_name": "Weak"})
    check("S5b password < 12 → 422", st == 422, f"HTTP {st}")
    st, body, raw = rv.api("POST", "/api/auth/signup-org", payload={
        "organization_name": org_name, "slug": "Bad_Slug!",
        "admin_email": f"bad@{slug}.test",
        "admin_password": "AnotherStrong2026!",
        "admin_display_name": "Bad"})
    check("S5c slug inválido → 422", st == 422, f"HTTP {st}")
    # Rate limit: cada rechazo quema el contador IP (login_max_failures=5).
    got_429 = False
    for i in range(8):
        st, body, raw = rv.api("POST", "/api/auth/signup-org", payload={
            "organization_name": org_name, "slug": f"rl-{slug}-{i}",
            "admin_email": "not-an-email",
            "admin_password": "AnotherStrong2026!",
            "admin_display_name": "RL"})
        if st == 429:
            got_429 = True
            break
    check("S5d abuso desde la IP → 429 (rate-limited)", got_429,
          f"último HTTP {st}")

    print("=== S6: el provider ACTIVA + asigna budget (REQ-SS-003/004) ===",
          flush=True)
    st, body, raw = rv.api("POST", f"/api/admin/tenants/{slug}/activate",
                           headers={"Authorization":
                                    f"Bearer {rv.PROVIDER_TOKEN}"},
                           payload={"initial_budget_usd": args.budget_usd})
    check("S6a POST /api/admin/tenants/{slug}/activate → 200",
          st == 200, f"HTTP {st} {raw[:180]}")
    check("S6b status → active y org_admin notificado",
          body.get("tenant", {}).get("status") == "active"
          and body.get("notified_org_admins", 0) >= 1,
          json.dumps(body)[:220])
    st, body, raw = rv.api("GET", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"})
    entry = next((t for t in body.get("tenants", [])
                  if t.get("slug") == slug), None)
    expected_cents = int(round(args.budget_usd * 100))
    check("S6c budget org = monto asignado por el provider",
          entry is not None and entry.get("org_budget_cents") == expected_cents,
          f"org_budget_cents={entry.get('org_budget_cents') if entry else '?'} "
          f"(esperado {expected_cents})")
    st, body, raw = rv.api("POST", f"/api/admin/tenants/{slug}/activate",
                           headers={"Authorization":
                                    f"Bearer {rv.PROVIDER_TOKEN}"},
                           payload={"initial_budget_usd": 10.0})
    check("S6d activar un tenant ya active → 409", st == 409, f"HTTP {st}")
    st, body, raw = rv.api("POST", f"/api/admin/tenants/{slug}/activate",
                           headers={"Authorization":
                                    f"Bearer {rv.PROVIDER_TOKEN}"},
                           payload={"initial_budget_usd": 0})
    check("S6e budget ≤ 0 → 422", st == 422, f"HTTP {st}")
    st, body, raw = rv.api("POST", f"/api/admin/tenants/{slug}/activate",
                           payload={"initial_budget_usd": 10.0})
    check("S6f sin provider token → 401", st == 401, f"HTTP {st}")

    print("=== S7: el admin re-entra y TODO funciona (REQ-SS-002→active) ===",
          flush=True)
    id_token2, acr2 = rv.pkce_login(admin_email, admin_pw,
                                    totp_state=totp_state)
    check("S7a re-login real del admin (OTP 2º factor)", id_token2 is not None,
          f"acr={acr2}")
    minted2 = rv.mint(id_token2)
    tok2 = minted2["session_token"]
    st, body, raw = rv.api("GET", "/api/me", bearer=tok2)
    check("S7b GET /api/me → 200 (ya no 423)", st == 200,
          f"HTTP {st} {raw[:120]}")
    st, body, raw = rv.api("GET", "/api/models", bearer=tok2)
    check("S7c GET /api/models → 200 (superficie funcional)",
          st == 200, f"HTTP {st}")
    st, body, raw = rv.api("GET", "/api/me/notifications", bearer=tok2)
    notes = body.get("notifications", [])
    activated = [n for n in notes if n.get("type") == "tenant_activated"]
    check("S7d notificación tenant_activated al org_admin (REQ-SS-004)",
          st == 200 and len(activated) >= 1,
          f"HTTP {st} types={[n.get('type') for n in notes][:5]}")

    print("=== S8: spend REAL descuenta del budget asignado (AC-26-006) ===",
          flush=True)
    st, body, raw = rv.api("GET", "/api/budgets/overview", bearer=tok2)
    before = body.get("organization", {})
    check("S8a GET /api/budgets/overview → 200 con budget org activo",
          st == 200 and before.get("budget_cents") == expected_cents,
          f"HTTP {st} {json.dumps(before)[:160]}")
    st, body, raw = rv.api("POST", "/api/models/keys", bearer=tok2, payload={})
    check("S8b emisión de key virtual → 201", st == 201,
          f"HTTP {st} {raw[:120]}")
    key = body.get("key", "")
    # Prompt largo: una invocación mínima redondea a 0¢ (round(usd*100));
    # se pide una respuesta extensa para que el spend sea visible en centavos.
    # timeout 480 s: la generación de miles de tokens vía proxy → MaaS excede
    # el default de 120 s de litellm_chat (hallazgo de la corrida 20:30 UTC).
    st, body, raw = rv.litellm_chat(key, "glm-5.2",
                                    prompt="Escribe un ensayo largo y "
                                           "detallado de al menos 3000 "
                                           "palabras sobre la historia de la "
                                           "ingeniería de software.",
                                    max_tokens=6000, timeout=480)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    check("S8c invocación REAL glm-5.2 (key virtual → proxy → MaaS)",
          st == 200 and content, f"HTTP {st} {str(content)[:80]}")
    # El spend-worker registra el evento de forma asíncrona: polling del
    # overview hasta que el budget org descuenta (máx ~90 s).
    spent_before = before.get("spent_cents", 0) or 0
    spent_after = spent_before
    after = before
    for _ in range(18):
        import time as _time
        _time.sleep(5)
        st, body, raw = rv.api("GET", "/api/budgets/overview", bearer=tok2)
        after = body.get("organization", {})
        spent_after = after.get("spent_cents", 0) or 0
        if spent_after > spent_before:
            break
    check("S8d el spend descuenta del budget org asignado",
          spent_after > spent_before,
          f"spent {spent_before}¢ → {spent_after}¢")
    check("S8e el corte sigue armado (spent ≤ budget)",
          spent_after <= (after.get("budget_cents") or 0),
          f"{spent_after}¢ / {after.get('budget_cents')}¢")

    print(f"\n{'=' * 60}\nEvidencia spec 026: {PASSES} PASS, "
          f"{len(FAILURES)} FAIL", flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

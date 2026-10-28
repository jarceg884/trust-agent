#!/usr/bin/env python3
"""Evidencia en vivo — spec 018 (Model Access sobre MaaS real + policy).

Reutiliza la maquinaria de scripts/revalidate_litellm_real.py (login REAL
PKCE S256 + TOTP contra Keycloak, provisioning idempotente por slug).

Cadena verificada (nada simulado):
  E1  GET {MAAS_ENDPOINT}/models (key "Inforum") == catálogo de la plataforma
  E2  GET /api/models → 6 deployments reales, contexto, precios USD/M oficiales
  E3  GET /api/models/access → sección policy (approved_models_only, allowed_models)
  E4  GET /api/policies?level=org → model_approvals con glm-5.2 aprobado
  E5  PUT /api/models/access (deepseek-v4-flash, nivel org) → aparece en
      model_approvals y en el acceso efectivo del usuario
  E6  PUT /api/policies/org allowed_models=['glm-5.2'] → policy restringe el
      conjunto efectivo (deny-overrides org→dept→user→policy)
  E7  Invocación REAL glm-5.2 con key virtual del tenant → proxy LiteLLM → MaaS

Uso: .venv/bin/python scripts/evidence_model_access_maas.py --slug <slug>
      .venv/bin/python scripts/evidence_model_access_maas.py --slug <slug> \
          --password <pw>   # reuse: tenant existente, sin provisioning
"""

import argparse
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

MAAS_MODELS = {"glm-5.3", "glm-5.2", "glm-5.1",
               "deepseek-v4.1-flash", "deepseek-v4-flash", "qwen3-32b"}
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
                    help="slug nuevo por corrida (provisioning idempotente)")
    ap.add_argument("--password", default=None,
                    help="modo reuse: entrar con esta contraseña del admin en "
                         "vez de provisionar (útil cuando el tenant ya existe; "
                         "el TOTP se re-enrola vía CONFIGURE_TOTP)")
    args = ap.parse_args()
    setattr(rv, "SLUG", args.slug)
    setattr(rv, "ADMIN_EMAIL", f"admin@{args.slug}.test")
    admin_pw = "ModelAccessAdmin2026!"

    print("=== E1: MaaS real /models ↔ catálogo ===", flush=True)
    st, body, raw = rv.litellm_admin("GET", "/v1/models")
    proxy_models = {m["id"] for m in body.get("data", [])}
    check("E1 proxy LiteLLM real /v1/models == 6 deployments MaaS",
          st == 200 and proxy_models == MAAS_MODELS, str(sorted(proxy_models)))

    print("=== Provisioning + login REAL (PKCE S256 + TOTP) ===", flush=True)
    totp_state = {}
    if args.password:
        # Modo reuse: el tenant ya existe (p.ej. provisionado antes de una
        # regresión de login en realms nuevos). P1 verifica su existencia por
        # el API del provider; el dance re-enrola el TOTP (CONFIGURE_TOTP).
        st, body, raw = rv.api("GET", "/api/admin/tenants", headers={
            "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"})
        check("P1 (reuse) GET /api/admin/tenants contiene el tenant",
              st == 200 and rv.SLUG in raw, f"HTTP {st}")
        id_token, acr = rv.pkce_login(rv.ADMIN_EMAIL, args.password,
                                      totp_state=totp_state)
    else:
        st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
            "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
            "organization_name": "Model Access MaaS Corp",
            "admin_email": rv.ADMIN_EMAIL,
            "initial_budget_usd": 100.0, "model_plan": ["glm-5.2"],
            "slug": rv.SLUG})
        check("P1 POST /api/admin/tenants → 201", st == 201,
              f"HTTP {st} {raw[:150]}")
        ota = body["one_time_activation"]["one_time_password"]
        id_token, acr = rv.pkce_login(rv.ADMIN_EMAIL, ota, new_password=admin_pw,
                                      totp_state=totp_state)
    check("P2 login REAL admin (acr=2)", id_token is not None and str(acr) == "2",
          f"acr={acr}")
    admin = rv.mint(id_token)["session_token"]

    print("=== E2: catálogo de la plataforma (doc oficial MaaS) ===", flush=True)
    st, body, raw = rv.api("GET", "/api/models", bearer=admin)
    api_models = {m["model_name"]: m for m in body.get("models", [])}
    check("E2a GET /api/models devuelve exactamente los 6 deployments reales",
          st == 200 and set(api_models) == MAAS_MODELS,
          f"HTTP {st} {sorted(api_models)}")
    check("E2b los modelos genéricos retirados ya no existen",
          "qwen2.5-72b-instruct" not in api_models
          and "deepseek-v3" not in api_models)

    def cents(v):
        return float(v) if v is not None else None

    g52 = api_models.get("glm-5.2", {})
    check("E2c glm-5.2: contexto 1M, output 128K, 140/440 ¢/M (doc oficial)",
          g52.get("context_tokens") == 1_000_000
          and g52.get("max_output_tokens") == 128_000
          and cents(g52.get("input_price_cents_per_m")) == 140.0
          and cents(g52.get("output_price_cents_per_m")) == 440.0,
          json.dumps(g52))
    q32 = api_models.get("qwen3-32b", {})
    check("E2d qwen3-32b: límites NULL (no documentados), precio 27/269.5",
          q32.get("context_tokens") is None
          and q32.get("max_output_tokens") is None
          and cents(q32.get("input_price_cents_per_m")) == 27.0
          and cents(q32.get("output_price_cents_per_m")) == 269.5,
          json.dumps(q32))

    print("=== E3/E4: policy integrada (spec 018 D-018-02) ===", flush=True)
    st, body, raw = rv.api("GET", "/api/models/access", bearer=admin)
    pol = body.get("policy", {})
    check("E3a GET /api/models/access incluye sección policy", st == 200 and pol,
          f"HTTP {st} {raw[:150]}")
    check("E3b policy.approved_models_only === true (floor invariable)",
          pol.get("approved_models_only") is True, json.dumps(pol))
    check("E3c policy.allowed_models es null sin policy org",
          pol.get("allowed_models") is None, json.dumps(pol))
    st, body, raw = rv.api("GET", "/api/policies?level=org", bearer=admin)
    approvals = body.get("model_approvals", [])
    approved_names = [a["model_name"] for a in approvals]
    check("E4a GET /api/policies?level=org incluye model_approvals=[glm-5.2]",
          st == 200 and approved_names == ["glm-5.2"],
          f"HTTP {st} {approved_names}")
    a0 = approvals[0] if approvals else {}
    check("E4b model_approvals expone modo y estado",
          a0.get("mode") == "central" and a0.get("status") == "approved",
          json.dumps(a0))

    print("=== E5: aprobación org de deepseek-v4-flash → policy visible ===",
          flush=True)
    st, body, raw = rv.api("PUT", "/api/models/access", bearer=admin, payload={
        "model_name": "deepseek-v4-flash", "level": "org", "mode": "central"})
    check("E5a PUT /api/models/access (deepseek-v4-flash, org) → 201",
          st == 201, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/policies?level=org", bearer=admin)
    approved_names = [a["model_name"] for a in body.get("model_approvals", [])]
    check("E5b model_approvals ahora = [deepseek-v4-flash, glm-5.2]",
          approved_names == ["deepseek-v4-flash", "glm-5.2"], str(approved_names))

    print("=== E6: policy allowed_models restringe (deny-overrides) ===",
          flush=True)
    st, body, raw = rv.api("PUT", "/api/policies/org", bearer=admin, payload={
        "key": "allowed_models", "value": ["glm-5.2"]})
    check("E6a PUT /api/policies/org allowed_models=['glm-5.2'] → 200",
          st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("GET", "/api/models/access", bearer=admin)
    pol = body.get("policy", {})
    check("E6b /api/models/access refleja policy.allowed_models=['glm-5.2']",
          pol.get("allowed_models") == ["glm-5.2"], json.dumps(pol))
    st, body, raw = rv.api("GET", "/api/models/allowed-for-me", bearer=admin)
    check("E6c conjunto efectivo del admin = ['glm-5.2'] "
          "(aprobados ∩ policy, deny-overrides)",
          st == 200 and body.get("models") == ["glm-5.2"],
          f"HTTP {st} {raw[:150]}")
    st, body, raw = rv.api("POST", "/api/models/verify", bearer=admin, payload={
        "model_name": "deepseek-v4-flash"})
    check("E6d verify deepseek-v4-flash → 403 (aprobado pero fuera de policy)",
          st == 403, f"HTTP {st}")

    print("=== E7: invocación REAL glm-5.2 (key virtual → proxy → MaaS) ===",
          flush=True)
    st, body, raw = rv.api("POST", "/api/models/keys", bearer=admin, payload={})
    check("E7a emisión de key virtual → 201", st == 201, f"HTTP {st} {raw[:120]}")
    key = body.get("key", "")
    st, body, raw = rv.litellm_chat(key, "glm-5.2",
                                    prompt="Responde solo: ok", max_tokens=2000)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    check("E7b chat/completions glm-5.2 vía proxy → MaaS real responde",
          st == 200 and content, f"HTTP {st} {str(content)[:80]}")
    st, body, raw = rv.litellm_chat(key, "deepseek-v4-flash", prompt="x",
                                    max_tokens=100)
    check("E7c deepseek-v4-flash con la misma key → 403 (policy)",
          st == 403, f"HTTP {st}")

    print(f"\n{'=' * 60}\nEvidencia spec 018: {PASSES} PASS, "
          f"{len(FAILURES)} FAIL", flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

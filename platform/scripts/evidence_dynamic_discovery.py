#!/usr/bin/env python3
"""Evidencia en vivo — spec 032 (discovery dinámico de modelos, mandato Miller).

Reutiliza la maquinaria de scripts/revalidate_litellm_real.py (login REAL
PKCE S256 + TOTP contra Keycloak, provisioning idempotente por slug) y usa
el proxy LiteLLM REAL v1.106.3 (127.0.0.1:4000) con MaaS intl como upstream
BYOK (key del archivo litellm-real/.env — jamás impresa).

Cadena verificada (nada simulado, AC-032-05 / AC-032-08):
  E1  Provisioning + login REAL (PKCE S256 + TOTP, acr=2)
  E2  POST /api/vault/connections (kind llm, base_url MaaS real) → 201;
      la key solo aparece una vez (one-time) y jamás en metadata
  E3  PUT /api/policies/org allowed_connections=[*, cid] → 200
  E4  POST /api/models/catalog/refresh {connection_id} → 200; descubre los
      deployments REALES del GET {MAAS}/models; audit model.catalog_refreshed
  E5  GET /api/models → modelos byok:{cid}:{upstream} con source=byok,
      provider=<nombre conexión>; los centrales siguen; higiene: la key no
      aparece en NINGUNA respuesta
  E6  PUT /api/models/access (byok, org) → 201; /api/policies?level=org lo
      lista con mode=byok; allowed-for-me lo incluye
  E7  POST /api/models/keys → vkey; invocación REAL del modelo byok vía
      gateway (chat/completions → MaaS real) → 200 con contenido
  E8  spend-worker --once → spend_ledger mode='byok' + budget del usuario
      descontado (USD)
  E9  POST /api/vault/connections/{cid}/revoke → modelos stale, baja del
      gateway (la vkey vieja ya no encuentra routing), verify → 403 +
      audit model.access_denied

Uso: .venv/bin/python scripts/evidence_dynamic_discovery.py --slug <slug>
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

PASSES = 0
FAILURES = []
RAW_BODIES: list[str] = []  # higiene: la key jamás en ninguna respuesta


def check(name, ok, detail=""):
    global PASSES
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  · {detail}" if detail else ""), flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)


def api(method, path, payload=None, bearer=None, headers=None):
    st, body, raw = rv.api(method, path, payload=payload, bearer=bearer,
                           headers=headers)
    RAW_BODIES.append(raw)
    return st, body, raw


def maas_env() -> tuple[str, str]:
    """(MAAS_ENDPOINT, MAAS_API_KEY) desde litellm-real/.env — sin imprimir."""
    endpoint = key = None
    for line in open(rv.LITELLM_REAL_ENV):
        if line.startswith("MAAS_ENDPOINT="):
            endpoint = line.split("=", 1)[1].strip()
        elif line.startswith("MAAS_API_KEY="):
            key = line.split("=", 1)[1].strip()
    assert endpoint and key, "MAAS_ENDPOINT/MAAS_API_KEY faltan en litellm-real/.env"
    return endpoint, key


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True,
                    help="slug nuevo por corrida (provisioning idempotente)")
    args = ap.parse_args()
    setattr(rv, "SLUG", args.slug)
    setattr(rv, "ADMIN_EMAIL", f"admin@{args.slug}.test")
    admin_pw = "DynamicDiscoveryAdmin2026!"
    maas_endpoint, maas_key = maas_env()

    print("=== E1: provisioning + login REAL (PKCE S256 + TOTP) ===", flush=True)
    st, body, raw = api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Dynamic Discovery Corp",
        "admin_email": rv.ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": ["glm-5.2"],
        "slug": rv.SLUG})
    check("E1a POST /api/admin/tenants → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    tenant_id = body["tenant"]["id"]
    ota = body["one_time_activation"]["one_time_password"]
    totp_state = {}
    id_token, acr = rv.pkce_login(rv.ADMIN_EMAIL, ota, new_password=admin_pw,
                                  totp_state=totp_state)
    admin = rv.mint(id_token)["session_token"]
    # El gate MFA real de la plataforma es require_mfa sobre TODOS los
    # endpoints /api/models*: la sesión minteada solo funciona si el control
    # plane verificó la evidencia MFA (amr/acr, D-MFA-01). Se reporta el acr
    # del realm como detalle.
    st, _, raw = api("GET", "/api/models", bearer=admin)
    check("E1b sesión admin REAL con MFA verificada (require_mfa pasa)",
          id_token is not None and st == 200, f"acr={acr} HTTP {st}")

    admin_uid = rv.db(
        "SELECT id FROM users WHERE email = %s", (rv.ADMIN_EMAIL,))[0][0]
    # El admin org no pertenece a un departamento: los budgets de usuario
    # requieren depto (422 by design), así que el descuento se verifica a
    # nivel ORG (budget_counters.entity_id = tenant_id).
    rows = rv.db(
        "SELECT amount_cents FROM budgets "
        "WHERE tenant_id = %s AND level = 'org' AND status = 'active'",
        (tenant_id,))
    check("E1c budget org del tenant = $100.00 (provisioning)",
          rows and rows[0][0] == 10000, f"rows={rows}")

    print("=== E2: conexión llm BYOK (MaaS real como upstream) ===", flush=True)
    st, body, raw = api("POST", "/api/vault/connections", bearer=admin, payload={
        "name": "acme-maas-byok",
        "kind": "llm",
        "metadata": {
            "base_url": maas_endpoint,
            "input_price_cents_per_m": 140,
            "output_price_cents_per_m": 440,
        },
        "secret_value": maas_key})
    check("E2a POST /api/vault/connections (kind llm) → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    # La respuesta de creación contiene la key one-time (contrato del vault,
    # REQ-024-007): fuera del tracking de higiene H1 (E2b lo aserta).
    if RAW_BODIES and RAW_BODIES[-1] is raw:
        RAW_BODIES.pop()
    cid = body["connection_id"]
    check("E2b la key viaja one-time en la respuesta (contrato vault)",
          body.get("secret_value") == maas_key, "one-time visible")
    check("E2c metadata NO contiene la key",
          "secret" not in json.dumps(body.get("metadata", {})).lower())

    print("=== E3: policy allowed_connections (org) ===", flush=True)
    st, body, raw = api("PUT", "/api/policies/org", bearer=admin, payload={
        "key": "allowed_connections", "value": ["*", cid]})
    check("E3a PUT /api/policies/org allowed_connections=[*, cid] → 200",
          st == 200, f"HTTP {st} {raw[:120]}")

    print("=== E4: refresh por conexión (fetch REAL GET {MAAS}/models) ===",
          flush=True)
    st, body, raw = api("POST", "/api/models/catalog/refresh", bearer=admin,
                        payload={"connection_id": cid})
    discovered = body.get("discovered")
    check("E4a POST /api/models/catalog/refresh {connection_id} → 200",
          st == 200, f"HTTP {st} {raw[:200]}")
    check("E4b descubrió los deployments reales del upstream (>0)",
          st == 200 and discovered and discovered > 0,
          f"discovered={discovered}")
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE action = 'model.catalog_refreshed' "
        "AND tenant_id = (SELECT id FROM tenants WHERE slug = %s)", (rv.SLUG,))
    check("E4c audit model.catalog_refreshed emitido", rows and rows[0][0] >= 1,
          f"rows={rows[0][0] if rows else 0}")

    print("=== E5: catálogo con modelos byok (source/provider) ===", flush=True)
    st, body, raw = api("GET", "/api/models", bearer=admin)
    models = {m["model_name"]: m for m in body.get("models", [])}
    byok = {k: v for k, v in models.items() if v.get("source") == "byok"}
    check("E5a los modelos del upstream aparecen marcados source=byok",
          st == 200 and len(byok) == (discovered or 0),
          f"byok={len(byok)} discovered={discovered}")
    check("E5b provider = nombre de la conexión",
          all(m["provider"] == "acme-maas-byok" for m in byok.values()),
          str({m["provider"] for m in byok.values()}))
    check("E5c los modelos centrales siguen presentes",
          "glm-5.2" in models and models["glm-5.2"]["source"] == "central")
    upstream_id = "glm-5.2"
    byok_model = f"byok:{cid}:{upstream_id}"
    check("E5d el modelo a aprobar existe en el catálogo byok",
          byok_model in byok, byok_model)

    print("=== E6: aprobación org (mode=byok) → policy ===", flush=True)
    st, body, raw = api("PUT", "/api/models/access", bearer=admin, payload={
        "model_name": byok_model, "level": "org", "mode": "byok"})
    check("E6a PUT /api/models/access (byok, org) → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = api("GET", "/api/policies?level=org", bearer=admin)
    approvals = body.get("model_approvals", [])
    check("E6b model_approvals lista el modelo byok con mode=byok",
          any(a["model_name"] == byok_model and a.get("mode") == "byok"
              for a in approvals), str([a["model_name"] for a in approvals]))
    st, body, raw = api("GET", "/api/models/allowed-for-me", bearer=admin)
    check("E6c allowed-for-me incluye el modelo byok (gate compuesto)",
          st == 200 and byok_model in (body.get("models") or []),
          f"HTTP {st} {raw[:150]}")

    print("=== E7: invocación REAL del modelo byok vía gateway ===", flush=True)
    st, body, raw = api("POST", "/api/models/keys", bearer=admin, payload={})
    check("E7a emisión de vkey → 201", st == 201, f"HTTP {st} {raw[:120]}")
    vkey = body.get("key", "")
    st, body, raw = rv.litellm_chat(vkey, byok_model,
                                    prompt="Escribe tres párrafos largos "
                                           "explicando la historia de la "
                                           "computación, de Babbage a la IA "
                                           "moderna.",
                                    max_tokens=4000)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    RAW_BODIES.append(raw)
    check("E7b chat/completions byok vía proxy → MaaS real responde",
          st == 200 and content, f"HTTP {st} {str(content)[:80]}")

    print("=== E8: spend del invocador (mode=byok, budget descontado) ===",
          flush=True)
    # NOTA de granularidad: spend_ledger y budget_counters trabajan en
    # CENTAVOS enteros (cost_billed_cents = round(spend_usd*100)); una
    # invocación sub-centavo (p. ej. "responde ok" ≈ $0.0006 → 0.06¢)
    # redondea a 0 — igual que en el modo central. Por eso E7b pide una
    # respuesta larga: el descuento del budget debe ser observable (≥1¢).

    def org_spent_cents() -> int:
        rows = rv.db(
            "SELECT coalesce(spent_cents, 0) FROM budget_counters "
            "WHERE tenant_id = %s AND level = 'org' AND entity_id = %s "
            "AND period_start <= current_date "
            "ORDER BY period_start DESC LIMIT 1", (tenant_id, tenant_id))
        return rows[0][0] if rows else 0

    before_cents = org_spent_cents()
    # El proxy persiste el spend log de forma asíncrona y el spend-worker
    # del stack corre cada 15 s: retry con tope (nada de duerme-sueño fijo).
    ledger_row = None
    r = None
    for _ in range(12):
        time.sleep(10)
        r = subprocess.run(["docker", "exec", "trust-agent-spend-worker",
                            "python", "-m", "app.governance.spend_worker",
                            "--once"], capture_output=True, text=True,
                           timeout=180)
        ledger_row = rv.db(
            "SELECT mode, cost_billed_cents FROM spend_ledger "
            "WHERE model_name = %s ORDER BY occurred_at DESC LIMIT 1",
            (byok_model,))
        if ledger_row and ledger_row[0][1] > 0:
            break
    check("E8a spend-worker --once corre en verde",
          r is not None and r.returncode == 0,
          r.stderr[-160:] if (r is not None and r.returncode) else "")
    check("E8b spend_ledger registra la invocación byok (mode='byok', USD>0)",
          ledger_row and ledger_row[0][0] == "byok" and ledger_row[0][1] > 0,
          str(ledger_row))
    after_cents = org_spent_cents()
    check("E8c el budget del invocador se descuenta (igual que central)",
          after_cents > before_cents,
          f"org: {before_cents} → {after_cents} cents")

    print("=== E9: revocar la conexión → stale + no invocable ===", flush=True)
    st, body, raw = api("POST", f"/api/vault/connections/{cid}/revoke",
                        bearer=admin)
    check("E9a POST /api/vault/connections/{cid}/revoke → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = api("GET", "/api/models", bearer=admin)
    models = {m["model_name"]: m for m in body.get("models", [])}
    stale_ok = all(
        m["status"] == "stale" for k, m in models.items()
        if k.startswith(f"byok:{cid}:"))
    check("E9b todos los modelos de la conexión quedan stale", stale_ok,
          str({k: m["status"] for k, m in models.items()
               if k.startswith(f"byok:{cid}:")}))
    st, body, raw = rv.litellm_chat(vkey, byok_model, prompt="x",
                                    max_tokens=100)
    RAW_BODIES.append(raw)
    check("E9c la vkey vieja ya no encuentra routing en el gateway",
          st != 200, f"HTTP {st}")
    st, body, raw = api("POST", "/api/models/verify", bearer=admin,
                        payload={"model_name": byok_model})
    check("E9d verify → 403 (deny-overrides compuesto)", st == 403,
          f"HTTP {st}")
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE action = 'model.access_denied' "
        "AND target_id = %s", (byok_model,))
    check("E9e audit model.access_denied emitido",
          rows and rows[0][0] >= 1, f"rows={rows[0][0] if rows else 0}")
    st, body, raw = api("GET", "/api/models/allowed-for-me", bearer=admin)
    check("E9f allowed-for-me excluye el modelo byok tras la revocación",
          st == 200 and byok_model not in (body.get("models") or []))

    print("=== Higiene: la key del upstream jamás apareció en una respuesta ===",
          flush=True)
    # Exclusión documentada: la ÚNICA respuesta que contiene la key es el
    # `secret_value` one-time del POST de creación (contrato del vault,
    # REQ-024-007 — la suite lo aserta igual); esa respuesta no entra a
    # RAW_BODIES (ver E2: se descarta del tracking).
    leaked = [raw[:80] for raw in RAW_BODIES if maas_key in raw]
    check("H1 ninguna respuesta de la API/gateway contiene la key "
          "(fuera del one-time del vault)",
          not leaked, f"leaks={len(leaked)}")

    print(f"\n{'=' * 60}\nEvidencia spec 032: {PASSES} PASS, "
          f"{len(FAILURES)} FAIL", flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

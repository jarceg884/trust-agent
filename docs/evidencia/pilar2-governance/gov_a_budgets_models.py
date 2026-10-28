#!/usr/bin/env python3
"""QA t_a296ae9b — Script A: budgets jerárquicos (config) + model access
sobre MaaS oficial + keys virtuales + llamadas REALES + ingest de spend.

Todo contra infra viva: API :8000, Keycloak :8080, proxy LiteLLM REAL :4000
(→ Huawei MaaS oficial), spend worker real. Nada simulado.
"""
import importlib.util
import json
import os
import subprocess
import time
import urllib.request

SPEC = importlib.util.spec_from_file_location(
    "e2e", "/root/trust-agent/platform/scripts/e2e_final_live.py")
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)

S = json.load(open("/root/.hermes/kanban/boards/trust-agent/workspaces/t_ef306f32/gov_state.json"))
admin = S["admin_session"]
w1s, w2s = S["w1_session"], S["w2_session"]
TENANT = S["tenant_id"]
DEPT_A, DEPT_B = S["dept_a"]["id"], S["dept_b"]["id"]
W1, W2 = S["w1_user_id"], S["w2_user_id"]
MAAS_ENDPOINT = "https://api-ap-southeast-1.modelarts-maas.com/openai/v1"
# REDACTADO post-gate (t_b62edff2, constitution IV): el VALOR de la key MaaS
# ("Inforum") vive SOLO en el .env — jamás en el repo. Para reproducir:
#   MAAS_API_KEY=... python3 gov_a_budgets_models.py
MAAS_KEY = os.environ["MAAS_API_KEY"]
LITELLM = "http://127.0.0.1:4000"  # proxy REAL (litellm-real, pid 4163348)

PASSES, FAILS = [], []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    (PASSES if cond else FAILS).append(name)
    return cond


def run_worker():
    p = subprocess.run(["docker", "exec", "trust-agent-spend-worker",
                        "python", "-m", "app.governance.spend_worker", "--once"],
                       capture_output=True, text=True, timeout=180)
    line = (p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr).strip() else ""
    print(f"    [worker] rc={p.returncode} {line[:220]}", flush=True)
    return p.returncode == 0


def chat(key, model, max_tokens=500, prompt="Reply with the single word OK."):
    req = urllib.request.Request(
        f"{LITELLM}/v1/chat/completions",
        data=json.dumps({"model": model, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


print("=== P3.1 PRESUPUESTOS JERÁRQUICOS (configuración) ===", flush=True)

st, ov, raw = e2e.api("GET", "/api/budgets/overview", bearer=admin)
check("b1 GET /api/budgets/overview → 200, org $100",
      st == 200 and ov["organization"]["budget_cents"] == 10000, raw[:200])

st, b, raw = e2e.api("PUT", f"/api/budgets/dept/{DEPT_A}", {"amount_cents": 2000}, bearer=admin)
check("b2 PUT dept IT $20 → 200", st == 200, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", f"/api/budgets/dept/{DEPT_B}", {"amount_cents": 2000}, bearer=admin)
check("b2 PUT dept CloudOps $20 → 200", st == 200, f"HTTP {st} {raw[:150]}")

st, b, raw = e2e.api("PUT", f"/api/budgets/dept/{DEPT_A}", {"amount_cents": 9000}, bearer=admin)
check("b3 PADRE RESTRINGE: depto que hace Σ=$110 > org $100 → 422",
      st == 422 and "org" in raw, f"HTTP {st} {raw[:200]}")

st, b, raw = e2e.api("PUT", f"/api/budgets/user/{W1}", {"amount_cents": 1000}, bearer=admin)
check("b4 PUT user w1 $10 → 200", st == 200, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", f"/api/budgets/user/{W1}", {"amount_cents": 2500}, bearer=admin)
check("b4 PADRE RESTRINGE: user w1 $25 > depto IT $20 → 422",
      st == 422 and "department" in raw.lower(), f"HTTP {st} {raw[:200]}")
st, b, raw = e2e.api("PUT", f"/api/budgets/user/{W2}", {"amount_cents": 1000}, bearer=admin)
check("b4 PUT user w2 $10 → 200", st == 200, f"HTTP {st} {raw[:150]}")

st, ov, raw = e2e.api("GET", "/api/budgets/overview", bearer=admin)
depts = {d["department_id"]: d for d in ov.get("departments", [])}
check("b5 overview: unassigned = org − Σ deptos = $60",
      st == 200 and ov.get("unassigned_cents") == 6000
      and depts.get(DEPT_A, {}).get("budget_cents") == 2000
      and depts.get(DEPT_B, {}).get("budget_cents") == 2000,
      f"unassigned={ov.get('unassigned_cents')} raw={raw[:250]}")

st, me, raw = e2e.api("GET", "/api/budgets/me", bearer=w1s)
check("b6 /api/budgets/me w1: $10, cutoff=false",
      st == 200 and me["user"]["budget_cents"] == 1000 and me["cutoff"] is False, raw[:200])

print("\n=== P5 MODEL ACCESS SOBRE MaaS OFICIAL ===", flush=True)

# m0: catálogo vivo del MaaS oficial (endpoint internacional, key del proveedor)
req = urllib.request.Request(f"{MAAS_ENDPOINT}/models",
                             headers={"Authorization": f"Bearer {MAAS_KEY}"})
with urllib.request.urlopen(req, timeout=30) as r:
    maas_models = sorted(m["id"] for m in json.loads(r.read())["data"])
print(f"    [maas] GET {{MAAS}}/models HTTP 200 → {maas_models}", flush=True)
check("m0 MaaS oficial vivo: GET /models → 200 con 6 deployments", len(maas_models) == 6)

st, body, raw = e2e.api("GET", "/api/models", bearer=admin)
api_models = sorted(m["model_name"] for m in body["models"])
g52 = next((m for m in body["models"] if m["model_name"] == "glm-5.2"), {})
check("m1 GET /api/models = 6 deployments REALES (1:1 con MaaS vivo)",
      st == 200 and api_models == maas_models, f"{api_models}")
check("m1 pricing oficial: glm-5.2 = 140¢/M in, 440¢/M out",
      float(g52.get("input_cost_cents_per_m", -1)) == 140.0
      and float(g52.get("output_cost_cents_per_m", -1)) == 440.0, str(g52)[:200])

st, acc, raw = e2e.api("GET", "/api/models/access", bearer=admin)
pol_block = acc.get("policy", {})
check("m2 GET /api/models/access → catálogo + policy (approved_models_only=true)",
      st == 200 and pol_block.get("approved_models_only") is True
      and "allowed_models" in pol_block, raw[:250])
approved_org = {m["model_name"] for m in acc.get("models", []) if m.get("org_status") == "approved"}
print(f"    [access] aprobados a nivel org antes del test: {sorted(approved_org)}", flush=True)

# m3: aprobar a nivel org los dos modelos del test
for model in ("glm-5.2", "deepseek-v4-flash"):
    st, b, raw = e2e.api("PUT", "/api/models/access",
                         {"model_name": model, "level": "org", "mode": "central"}, bearer=admin)
    check(f"m3 aprobar ORG {model} → 201", st == 201, f"HTTP {st} {raw[:150]}")

# m4: regla de subconjunto
st, b, raw = e2e.api("PUT", "/api/models/access",
                     {"model_name": "qwen3-32b", "level": "dept", "department_id": DEPT_A},
                     bearer=admin)
check("m4 SUBCONJUNTO: depto aprueba qwen3-32b sin aprobación org → 422",
      st == 422 and "Subset" in raw, f"HTTP {st} {raw[:200]}")
st, b, raw = e2e.api("PUT", "/api/models/access",
                     {"model_name": "glm-5.2", "level": "dept", "department_id": DEPT_A}, bearer=admin)
check("m4 aprobar DEPT IT glm-5.2 (⊆ org) → 201", st == 201, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", "/api/models/access",
                     {"model_name": "deepseek-v4-flash", "level": "user", "user_id": W1}, bearer=admin)
check("m4 aprobar USER w1 deepseek-v4-flash (⊆ org) → 201", st == 201, f"HTTP {st} {raw[:150]}")

# m5: lista efectiva de w1 = org ∩ dept ∩ user
st, body, raw = e2e.api("GET", "/api/models/allowed-for-me", bearer=w1s)
eff_w1 = sorted(body.get("models", []))
check("m5 allowed-for-me w1 = org ∩ dept ∩ user = [deepseek-v4-flash, glm-5.2]",
      st == 200 and eff_w1 == ["deepseek-v4-flash", "glm-5.2"], f"{eff_w1} {raw[:200]}")
st, b, raw = e2e.api("POST", "/api/models/verify", {"model_name": "glm-5.2"}, bearer=w1s)
check("m5 verify glm-5.2 (w1) → 200 allowed", st == 200 and b.get("allowed") is True, raw[:150])
st, b, raw = e2e.api("POST", "/api/models/verify", {"model_name": "qwen3-32b"}, bearer=w1s)
check("m5 verify qwen3-32b (no aprobado) → 403 model_not_approved",
      st == 403 and b.get("reason") == "model_not_approved", f"HTTP {st} {raw[:200]}")
rows = e2e.db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'model.access_denied'", (TENANT,))
check("m5 audit model.access_denied registrado", rows[0][0] >= 1, f"n={rows[0][0]}")

# m6: policy allowed_models estrecha la intersección (deny-overrides)
st, b, raw = e2e.api("PUT", "/api/policies/org",
                     {"key": "allowed_models", "value": ["glm-5.2", "deepseek-v4-flash"]}, bearer=admin)
check("m6 policy org allowed_models=[glm-5.2,deepseek-v4-flash] → 200", st == 200, raw[:150])
st, b, raw = e2e.api("PUT", f"/api/policies/user/{W1}",
                     {"key": "allowed_models", "value": ["glm-5.2"]}, bearer=admin)
check("m6 policy user w1 allowed_models=[glm-5.2] (estrecha) → 200", st == 200, raw[:150])
st, body, raw = e2e.api("GET", "/api/models/allowed-for-me", bearer=w1s)
check("m6 allowed-for-me w1 tras policy user = [glm-5.2] (policy ∩ aprobaciones)",
      st == 200 and sorted(body.get("models", [])) == ["glm-5.2"], raw[:200])
st, b, raw = e2e.api("POST", "/api/models/verify", {"model_name": "deepseek-v4-flash"}, bearer=w1s)
check("m6 verify deepseek-v4-flash tras policy → 403 (aprobado pero fuera de policy)",
      st == 403 and b.get("reason") == "model_not_approved", f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("DELETE", f"/api/policies/user/{W1}/allowed_models", bearer=admin)
check("m6 DELETE policy user allowed_models → 204", st == 204, f"HTTP {st}")
st, body, raw = e2e.api("GET", "/api/models/allowed-for-me", bearer=w1s)
check("m6 allowed-for-me w1 restaurado a 2 modelos", st == 200
      and sorted(body.get("models", [])) == ["deepseek-v4-flash", "glm-5.2"], raw[:200])

# m7: keys virtuales REALES contra el proxy real
import hashlib
st, b, raw = e2e.api("POST", "/api/models/keys", {"user_id": W1}, bearer=admin)
k1 = b.get("key", "")
check("m7 key virtual w1 → 201, key REAL del proxy (no fake), max_budget $10",
      st == 201 and k1 and not k1.startswith("sk-fake")
      and b.get("max_budget_usd") == 10.0, f"HTTP {st} {raw[:200]}")
S["w1_key"], S["w1_key_id"] = k1, b.get("key_id")
st, b, raw = e2e.api("POST", "/api/models/keys", {"user_id": W2}, bearer=admin)
k2 = b.get("key", "")
check("m7 key virtual w2 → 201 real", st == 201 and k2 and not k2.startswith("sk-fake"),
      f"HTTP {st} {raw[:150]}")
S["w2_key"], S["w2_key_id"] = k2, b.get("key_id")
rows = e2e.db("SELECT litellm_key_hash FROM api_keys WHERE id = %s", (S["w1_key_id"],))
check("m7 DB guarda SOLO sha256(key) (constitución IV)",
      rows and rows[0][0] == hashlib.sha256(k1.encode()).hexdigest())

# m8: LLAMADAS REALES contra MaaS oficial vía el gateway de la plataforma
st, body = chat(k1, "glm-5.2", max_tokens=400)
content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")[:80]
check("m8 LLAMADA REAL MaaS: w1 key + glm-5.2 vía proxy :4000 → 200",
      st == 200 and bool(content), f"HTTP {st} model={body.get('model')} content={content!r}")
print(f"    [maas] usage real: {body.get('usage')}", flush=True)
st, body = chat(k1, "qwen3-32b", max_tokens=10)
check("m8 modelo NO aprobado con la key → 403 del gateway",
      st == 403, f"HTTP {st} {str(body)[:150]}")

# acumular spend real (varias llamadas por usuario)
for i in range(4):
    st1, _ = chat(k1, "glm-5.2", max_tokens=600, prompt="Count from 1 to 50, numbers only.")
    st2, _ = chat(k2, "deepseek-v4-flash", max_tokens=600, prompt="Count from 1 to 50, numbers only.")
    print(f"    [maas] ronda {i+1}: w1→{st1} w2→{st2}", flush=True)
    time.sleep(2)
check("m8 4 rondas de llamadas reales por usuario → 200", st1 == 200 and st2 == 200)

# m9: ingest de spend REAL → ledger + counters + overview
ingested = 0
for attempt in range(6):
    run_worker()
    rows = e2e.db("SELECT count(*), coalesce(sum(cost_billed_cents),0) FROM spend_ledger "
                  "WHERE tenant_id = %s", (TENANT,))
    ingested = rows[0][0]
    if ingested >= 8:
        break
    time.sleep(8)
rows = e2e.db("SELECT count(*), coalesce(sum(cost_billed_cents),0), "
              "string_agg(DISTINCT mode, ','), string_agg(DISTINCT model_name, ',') "
              "FROM spend_ledger WHERE tenant_id = %s", (TENANT,))
n, cost, modes, models = rows[0]
check("m9 ledger: ≥8 requests reales ingested con costo > 0",
      n >= 8 and float(cost) > 0, f"filas={n} costo={cost}¢ modos={modes} modelos={models}")
rows = e2e.db("SELECT level, entity_id, spent_cents FROM budget_counters "
              "WHERE tenant_id = %s ORDER BY level", (TENANT,))
print(f"    [counters] {[(r[0], str(r[1])[:8], float(r[2])) for r in rows]}", flush=True)
lv = {r[0]: float(r[2]) for r in rows}
check("m9 counters de los 3 niveles descontados con spend REAL (org/dept/user > 0)",
      lv.get("org", 0) > 0 and lv.get("dept", 0) > 0 and lv.get("user", 0) > 0, str(lv))
st, ov, raw = e2e.api("GET", "/api/budgets/overview", bearer=admin)
check("m9 overview refleja el gasto real (org spent_cents > 0)",
      st == 200 and float(ov["organization"].get("spent_cents", 0)) > 0,
      f"org_spent={ov['organization'].get('spent_cents')}")
st, me, raw = e2e.api("GET", "/api/budgets/me", bearer=w1s)
check("m9 /api/budgets/me w1 refleja gasto real, sin corte",
      st == 200 and float(me["user"].get("spent_cents", 0)) > 0 and me["cutoff"] is False,
      raw[:250])

json.dump(S, open("/root/.hermes/kanban/boards/trust-agent/workspaces/t_ef306f32/gov_state.json", "w"),
          indent=2)
print(f"\nSCRIPT A: {len(PASSES)} PASS, {len(FAILS)} FAIL {FAILS}", flush=True)
raise SystemExit(0 if not FAILS else 1)

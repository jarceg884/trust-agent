#!/usr/bin/env python3
"""QA t_a296ae9b — Script B: subconjunto/retiro model access, DELETE policy
correcto, y CASCADA DE CORTES con spend real (user → dept → org → recovery).
"""
import importlib.util
import json
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
K1, K2 = S["w1_key"], S["w2_key"]
LITELLM = "http://127.0.0.1:4000"

PASSES, FAILS = [], []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    (PASSES if cond else FAILS).append(name)
    return cond


def run_worker():
    p = subprocess.run(["docker", "exec", "trust-agent-spend-worker",
                        "python", "-m", "app.governance.spend_worker", "--once"],
                       capture_output=True, text=True, timeout=180)
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


def key_status(key_id):
    rows = e2e.db("SELECT status FROM api_keys WHERE id = %s", (key_id,))
    return rows[0][0] if rows else None


print("=== P5 (cont.): retiro org + regla de subconjunto ===", flush=True)

# retiro org de qwen3-32b (modelo que MaaS oficial ya no sirve)
st, b, raw = e2e.api("POST", "/api/models/access/revoke",
                     {"model_name": "qwen3-32b", "level": "org"}, bearer=admin)
check("r1 retiro ORG qwen3-32b → 200", st == 200, f"HTTP {st} {raw[:150]}")

st, body, raw = e2e.api("GET", "/api/models/allowed-for-me", bearer=w1s)
check("r1 retiro org → qwen3-32b FUERA de la lista efectiva (intersección)",
      st == 200 and "qwen3-32b" not in body.get("models", []),
      f"{body.get('models')}")

st, b, raw = e2e.api("PUT", "/api/models/access",
                     {"model_name": "qwen3-32b", "level": "dept", "department_id": DEPT_A},
                     bearer=admin)
check("r2 SUBCONJUNTO: depto aprueba qwen3-32b retirado de org → 422",
      st == 422 and "Subset" in raw, f"HTTP {st} {raw[:200]}")

# repro formal del bug de aprobación a nivel usuario (evidencia del GAP)
st, b, raw = e2e.api("PUT", "/api/models/access",
                     {"model_name": "deepseek-v4-flash", "level": "user", "user_id": W1},
                     bearer=admin)
check("r3 BUG: aprobar USER w1 deepseek-v4-flash → 500 (check DB violation)",
      st == 500, f"HTTP {st} {raw[:150]}")

# DELETE correcto de la policy de usuario (query param)
st, b, raw = e2e.api("DELETE", f"/api/policies/user/allowed_models?user_id={W1}", bearer=admin)
check("r4 DELETE /api/policies/user/allowed_models?user_id=… → 204", st == 204, f"HTTP {st}")
st, body, raw = e2e.api("GET", "/api/models/allowed-for-me", bearer=w1s)
eff = sorted(body.get("models", []))
check("r4 allowed-for-me w1 restaurado (sin policy user)", st == 200
      and eff == ["deepseek-v4-flash", "glm-5.2"], f"{eff}")

print("\n=== P3.2 CASCADA DE CORTES con spend real ===", flush=True)
rows = e2e.db("SELECT level, entity_id, spent_cents FROM budget_counters WHERE tenant_id = %s",
              (TENANT,))
print(f"    [counters] {[(r[0], str(r[1])[:8], float(r[2])) for r in rows]}", flush=True)

# --- corte nivel USER ---
st, b, raw = e2e.api("PUT", f"/api/budgets/user/{W1}", {"amount_cents": 0, "force": True},
                     bearer=admin)
check("e1 reducir budget w1 a $0 con force → 200", st == 200, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", f"/api/budgets/user/{W1}", {"amount_cents": 0}, bearer=admin)
check("e1 sin force → 422 (gasto ya incurrido)", st == 422, f"HTTP {st}")
e2e.api("PUT", f"/api/budgets/user/{W1}", {"amount_cents": 0, "force": True}, bearer=admin)
run_worker()
check("e1 key w1 BLOQUEADA en la plataforma", key_status(S["w1_key_id"]) == "blocked",
      key_status(S["w1_key_id"]))
st, body = chat(K1, "glm-5.2", max_tokens=20)
check("e1 gateway REAL rechaza la key bloqueada → 401", st == 401, f"HTTP {st} {str(body)[:120]}")
st, body = chat(K2, "deepseek-v4-flash", max_tokens=20)
check("e1 key w2 (otro depto, presupuesto intacto) sigue OK → 200", st == 200, f"HTTP {st}")
st, me, raw = e2e.api("GET", "/api/budgets/me", bearer=w1s)
check("e1 /api/budgets/me w1: cutoff=true level=user",
      st == 200 and me["cutoff"] is True and me.get("cutoff_level") == "user", raw[:250])
st, body, raw = e2e.api("GET", "/api/me/notifications", bearer=w1s)
check("e1 notificación budget_cutoff al usuario w1",
      st == 200 and any(n["type"] == "budget_cutoff" for n in body.get("notifications", [])),
      raw[:200])
rows = e2e.db("SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = 'budget.cutoff'",
              (TENANT,))
check("e1 audit budget.cutoff", rows[0][0] >= 1, f"n={rows[0][0]}")
rows = e2e.db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'budget.forced_below_spend'", (TENANT,))
check("e1 audit budget.forced_below_spend", rows[0][0] >= 1, f"n={rows[0][0]}")

# --- recuperación user ---
st, b, raw = e2e.api("PUT", f"/api/budgets/user/{W1}", {"amount_cents": 1000, "force": True},
                     bearer=admin)
run_worker()
check("e2 recuperación user $10 → key activa de nuevo",
      key_status(S["w1_key_id"]) == "active", key_status(S["w1_key_id"]))
st, body = chat(K1, "glm-5.2", max_tokens=20)
check("e2 llamada real tras recuperación → 200", st == 200, f"HTTP {st}")

# --- corte nivel DEPT ---
st, b, raw = e2e.api("PUT", f"/api/budgets/dept/{DEPT_A}", {"amount_cents": 0, "force": True},
                     bearer=admin)
check("e3 reducir depto IT a $0 con force → 200", st == 200, f"HTTP {st} {raw[:150]}")
run_worker()
check("e3 PADRE RESTRINGE HIJOS: key de w1 (depto IT) bloqueada por corte de DEPTO",
      key_status(S["w1_key_id"]) == "blocked", key_status(S["w1_key_id"]))
check("e3 key de w2 (depto CloudOps) NO afectada",
      key_status(S["w2_key_id"]) == "active", key_status(S["w2_key_id"]))
st, me, raw = e2e.api("GET", "/api/budgets/me", bearer=w1s)
check("e3 /api/budgets/me w1: cutoff level=dept",
      st == 200 and me["cutoff"] is True and me.get("cutoff_level") == "dept", raw[:250])
st, body = chat(K2, "deepseek-v4-flash", max_tokens=20)
check("e3 w2 sigue invocando real → 200 (aislamiento entre deptos)", st == 200, f"HTTP {st}")

# --- recuperación dept ---
e2e.api("PUT", f"/api/budgets/dept/{DEPT_A}", {"amount_cents": 2000, "force": True}, bearer=admin)
run_worker()
check("e4 recuperación depto IT $20 → key w1 activa", key_status(S["w1_key_id"]) == "active",
      key_status(S["w1_key_id"]))

# --- corte nivel ORG ---
st, b, raw = e2e.api("PUT", "/api/budgets/org", {"amount_cents": 0, "force": True}, bearer=admin)
check("e5 reducir ORG a $0 con force → 200", st == 200, f"HTTP {st} {raw[:150]}")
run_worker()
check("e5 CORTE ORG: key w1 bloqueada", key_status(S["w1_key_id"]) == "blocked",
      key_status(S["w1_key_id"]))
check("e5 CORTE ORG: key w2 TAMBIÉN bloqueada (padre restringe a todos)",
      key_status(S["w2_key_id"]) == "blocked", key_status(S["w2_key_id"]))
st, me, raw = e2e.api("GET", "/api/budgets/me", bearer=w1s)
check("e5 /api/budgets/me w1: cutoff level=org",
      st == 200 and me["cutoff"] is True and me.get("cutoff_level") == "org", raw[:250])
st, body, raw = e2e.api("GET", "/api/me/notifications", bearer=admin)
check("e5 notificación budget_cutoff al org_admin",
      st == 200 and any(n["type"] == "budget_cutoff" for n in body.get("notifications", [])),
      raw[:200])
st, body = chat(K2, "deepseek-v4-flash", max_tokens=20)
check("e5 gateway rechaza key w2 con corte org → 401", st == 401, f"HTTP {st}")

# --- recuperación org ---
e2e.api("PUT", "/api/budgets/org", {"amount_cents": 10000, "force": True}, bearer=admin)
run_worker()
ok1 = key_status(S["w1_key_id"]) == "active"
ok2 = key_status(S["w2_key_id"]) == "active"
st1, _ = chat(K1, "glm-5.2", max_tokens=20)
st2, _ = chat(K2, "deepseek-v4-flash", max_tokens=20)
check("e6 recuperación ORG $100 → ambas keys activas y llamadas reales 200",
      ok1 and ok2 and st1 == 200 and st2 == 200, f"w1={ok1}/{st1} w2={ok2}/{st2}")

rows = e2e.db("SELECT action, count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action IN ('budget.cutoff','budget.forced_below_spend','model.key_blocked',"
              "'model.key_unblocked') GROUP BY action", (TENANT,))
print(f"    [audit] {rows}", flush=True)

print(f"\nSCRIPT B: {len(PASSES)} PASS, {len(FAILS)} FAIL {FAILS}", flush=True)
raise SystemExit(0 if not FAILS else 1)

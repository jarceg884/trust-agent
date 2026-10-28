#!/usr/bin/env python3
"""QA t_a296ae9b — Script C (P4 POLICIES): CRUD por nivel, validaciones,
versionado, deny-overrides efectivo para usuarios, aplicación sobre agentes.
"""
import importlib.util
import json

SPEC = importlib.util.spec_from_file_location(
    "e2e", "/root/trust-agent/platform/scripts/e2e_final_live.py")
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)

S = json.load(open("/root/.hermes/kanban/boards/trust-agent/workspaces/t_ef306f32/gov_state.json"))
admin = S["admin_session"]
w1s, w2s = S["w1_session"], S["w2_session"]
TENANT = S["tenant_id"]
DEPT_A = S["dept_a"]["id"]
W1 = S["w1_user_id"]

PASSES, FAILS = [], []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    (PASSES if cond else FAILS).append(name)
    return cond


print("=== P4 POLICIES: CRUD + validaciones ===", flush=True)

st, b, raw = e2e.api("PUT", "/api/policies/org",
                     {"key": "allowed_connectors", "value": ["vmware", "email"]}, bearer=admin)
check("p1 PUT org allowed_connectors=[vmware,email] → 200 v1",
      st == 200 and b.get("version") == 1, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", "/api/policies/org",
                     {"key": "allowed_connectors", "value": ["vmware", "email", "slack"]},
                     bearer=admin)
check("p1 segundo PUT (cambio de valor) → version 2",
      st == 200 and b.get("version") == 2, f"HTTP {st} {raw[:150]}")
e2e.api("PUT", "/api/policies/org",
        {"key": "allowed_connectors", "value": ["vmware", "email"]}, bearer=admin)

st, b, raw = e2e.api("PUT", f"/api/policies/dept/{DEPT_A}",
                     {"key": "allowed_connectors", "value": ["vmware"]}, bearer=admin)
check("p2 depto IT estrecha a [vmware] → 200", st == 200, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", f"/api/policies/dept/{DEPT_A}",
                     {"key": "allowed_connectors", "value": ["vmware", "email", "slack"]},
                     bearer=admin)
check("p2 NO-RELAJACIÓN: depto amplía por encima de org → 422", st == 422, f"HTTP {st} {raw[:200]}")

for level_path, payload in (
    ("org", {"key": "require_mfa", "value": False}),
    (f"dept/{DEPT_A}", {"key": "approved_models_only", "value": False}),
    (f"user/{W1}", {"key": "explicit_shared_agent_grants", "value": False}),
):
    st, b, raw = e2e.api("PUT", f"/api/policies/{level_path}", payload, bearer=admin)
    check(f"p3 INVARIABLE {payload['key']} en {level_path.split('/')[0]} → 422",
          st == 422, f"HTTP {st} {raw[:150]}")

st, b, raw = e2e.api("PUT", "/api/policies/org",
                     {"key": "no_such_key", "value": True}, bearer=admin)
check("p4 clave fuera del catálogo → 422", st == 422, f"HTTP {st} {raw[:150]}")
st, b, raw = e2e.api("PUT", "/api/policies/org",
                     {"key": "session_timeout_minutes", "value": "forty"}, bearer=admin)
check("p4 tipo incorrecto (string en int) → 422", st == 422, f"HTTP {st} {raw[:150]}")

print("\n=== P4: política EFECTIVA deny-overrides para usuarios ===", flush=True)
st, body, raw = e2e.api("GET", "/api/me/policies", bearer=w1s)
eff = body.get("policies", body)
check("p5 /api/me/policies w1 → allowed_connectors=[vmware] (org ∩ depto)",
      st == 200 and eff.get("allowed_connectors") == ["vmware"], raw[:250])
st, body, raw = e2e.api("GET", "/api/connectors/available", bearer=w1s)
check("p5 conectores disponibles para w1 = SOLO vmware (efecto real)",
      st == 200 and [c["slug"] for c in body["connectors"]] == ["vmware"], raw[:200])
st, body, raw = e2e.api("GET", "/api/connectors/available", bearer=w2s)
check("p5 w2 (depto sin policy propia) hereda org = [vmware,email]",
      st == 200 and [c["slug"] for c in body["connectors"]] == ["vmware", "email"], raw[:200])

st, b, raw = e2e.api("PUT", f"/api/policies/user/{W1}",
                     {"key": "allowed_connectors", "value": []}, bearer=admin)
check("p6 policy user w1 allowed_connectors=[] (deny-all, más restrictivo) → 200",
      st == 200, f"HTTP {st} {raw[:150]}")
st, body, raw = e2e.api("GET", "/api/connectors/available", bearer=w1s)
check("p6 w1 en deny-all: 0 conectores disponibles",
      st == 200 and body["connectors"] == [], raw[:200])
st, body, raw = e2e.api("GET", "/api/connectors/available", bearer=w2s)
check("p6 w2 NO afectado por la policy de w1",
      st == 200 and [c["slug"] for c in body["connectors"]] == ["vmware", "email"], raw[:150])
st, b, raw = e2e.api("DELETE",
                     f"/api/policies/user/allowed_connectors?user_id={W1}&department_id={DEPT_A}",
                     bearer=admin)
check("p6 DELETE policy user (con department_id) → 204", st == 204, f"HTTP {st}")
st, body, raw = e2e.api("GET", "/api/connectors/available", bearer=w1s)
check("p6 w1 vuelve a [vmware] tras el delete", st == 200
      and [c["slug"] for c in body["connectors"]] == ["vmware"], raw[:150])

st, b, raw = e2e.api("DELETE", "/api/policies/org/allowed_connectors", bearer=admin)
check("p7 DELETE policy org allowed_connectors → 204", st == 204, f"HTTP {st}")
st, body, raw = e2e.api("GET", "/api/me/policies", bearer=w1s)
eff = body.get("policies", body)
check("p7 tras delete org: la efectiva cae al default (depto [vmware] sigue)",
      st == 200 and eff.get("allowed_connectors") == ["vmware"], raw[:250])

print("\n=== P4: exposición de aprobaciones como policy (spec 018) ===", flush=True)
st, body, raw = e2e.api("GET", "/api/policies?level=org", bearer=admin)
ma = body.get("model_approvals")
check("p8 GET /api/policies?level=org incluye model_approvals (AC-018-02)",
      st == 200 and isinstance(ma, list) and len(ma) >= 2, f"{str(ma)[:200]}")
st, body, raw = e2e.api("GET", "/api/models/access", bearer=admin)
check("p8 GET /api/models/access expone policy.allowed_models vigente",
      st == 200 and body.get("policy", {}).get("allowed_models") is not None,
      str(body.get("policy"))[:150])

print("\n=== P4: aplicación de policies sobre AGENTES (gate de modelos) ===", flush=True)
st, b, raw = e2e.api("POST", "/api/agents",
                     {"name": "qa-gov-agent", "description": "QA gov agent",
                      "scope": "dept"}, bearer=w1s)
check("p9 w1 publica agente → 201", st == 201, f"HTTP {st} {raw[:150]}")
AID = b.get("id")
st, b, raw = e2e.api("POST", f"/api/agents/{AID}/invoke",
                     {"model_name": "qwen3-32b"}, bearer=w1s)
check("p9 invoke con modelo NO efectivo → 403 model_not_approved (policy sobre agente)",
      st == 403 and b.get("reason") == "model_not_approved", f"HTTP {st} {raw[:200]}")
st, b, raw = e2e.api("POST", f"/api/agents/{AID}/invoke",
                     {"model_name": "glm-5.2"}, bearer=w1s)
check("p9 invoke con modelo efectivo → 200", st == 200, f"HTTP {st} {raw[:150]}")

rows = e2e.db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'policy.updated'", (TENANT,))
check("p10 audit policy.updated (≥5 cambios)", rows[0][0] >= 5, f"n={rows[0][0]}")
rows = e2e.db("SELECT count(*) FROM audit_log WHERE tenant_id = %s "
              "AND action = 'agent.invoked'", (TENANT,))
check("p10 audit agent.invoked", rows[0][0] >= 1, f"n={rows[0][0]}")

print(f"\nSCRIPT C: {len(PASSES)} PASS, {len(FAILS)} FAIL {FAILS}", flush=True)
raise SystemExit(0 if not FAILS else 1)

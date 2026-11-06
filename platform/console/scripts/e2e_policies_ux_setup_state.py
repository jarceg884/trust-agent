#!/usr/bin/env python3
"""Setup de estado para el E2E de UI de policies (escriben los PUT reales que
las verificaciones asumen — mismo patrón que el flujo original de spec 036):

- org max_daily_sessions=5 (base del 422 de relajación a nivel usuario).
- usuario worker: allowed_tools SIN web_search (sub-toggle por ítem + badge
  "Set for this user").

Uso: python3 scripts/e2e_policies_ux_setup_state.py <state.json>
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3100"
state = json.load(open(sys.argv[1]))
COOKIES = "; ".join(f"{k}={v}" for k, v in state["cookies"].items())
W = state["worker_user_id"]


def call(path, method="GET", payload=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        headers={"Cookie": COOKIES, "Content-Type": "application/json",
                 "X-CSRF-Token": state["cookies"].get("ta_console_csrf", "")},
        data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


st, body = call("/api/proxy/api/policies/org", "PUT",
                {"key": "max_daily_sessions", "value": 5})
print(f"org max_daily_sessions=5 → HTTP {st}")
assert st == 200, body

st, body = call(f"/api/proxy/api/policies/user/{W}", "PUT",
                {"key": "allowed_tools",
                 "value": ["read_file", "write_file", "governance_status"]})
print(f"user allowed_tools (sin web_search) → HTTP {st}")
assert st == 200, body
print("setup ok")

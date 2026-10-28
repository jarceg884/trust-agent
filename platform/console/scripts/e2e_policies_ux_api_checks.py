#!/usr/bin/env python3
"""Checks API del E2E spec 036 (AC-036-003): gates del engine intactos.

- PUT approved_models_only=false a nivel org → 422 (invariable, gate existente).
- PUT require_mfa a nivel usuario → 422 (org_only, gate existente).
- GET /api/policies/effective del worker: web_search ausente (AC-036-003/005).

Uso: python3 scripts/e2e_policies_ux_api_checks.py <state.json>
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3100"
state = json.load(open(sys.argv[1]))
COOKIES = "; ".join(f"{k}={v}" for k, v in state["cookies"].items())
W = state["worker_user_id"]
failures = []


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


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — {detail}"))
    if not cond:
        failures.append(name)


# Invariable: PUT a org → 422 con el mensaje real del gate (AC-036-003).
st, body = call("/api/proxy/api/policies/org", "PUT",
                {"key": "approved_models_only", "value": False})
check("AC-036-003: PUT approved_models_only=false (org) → 422 (gate invariable)",
      st == 422, f"HTTP {st} {body}")

# org_only: PUT a nivel usuario → 422 (AC-036-003).
st, body = call(f"/api/proxy/api/policies/user/{W}", "PUT",
                {"key": "require_mfa", "value": False})
check("AC-036-003: PUT require_mfa a nivel usuario → 422 (gate org_only)",
      st == 422, f"HTTP {st} {body}")

# Efectiva del worker: web_search ausente, source=user (AC-036-003/005).
st, body = call(f"/api/proxy/api/policies/effective?user_id={W}")
row = next(k for k in body["keys"] if k["key"] == "allowed_tools")
check("AC-036-003/005: effective.allowed_tools sin web_search, source=user",
      st == 200 and "web_search" not in row["effective"] and row["source"] == "user",
      f"HTTP {st} row={row}")

# La escritura del E2E dejó max_daily_sessions=5 a nivel org (AC-036-004 setup).
st, body = call("/api/proxy/api/policies/matrix")
check("AC-036-004 setup: org fijó max_daily_sessions=5",
      st == 200 and body["org_effective"].get("max_daily_sessions") == 5,
      f"HTTP {st} val={body.get('org_effective', {}).get('max_daily_sessions')}")

print(f"\n{4 - len(failures)}/4 checks PASS")
sys.exit(1 if failures else 0)

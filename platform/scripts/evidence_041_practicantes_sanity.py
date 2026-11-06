#!/usr/bin/env python3
"""Sanity: sesión admin del console + estado actual de named policies."""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3100"
STATE_FILE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/builder-state.json"
state = json.load(open(STATE_FILE))
COOKIES = "; ".join(f"{k}={v}" for k, v in state["cookies"].items())


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


st, me = call("/api/proxy/api/me")
print("admin /api/me →", st,
      json.dumps({k: me.get(k) for k in ("email", "mfa_verified", "roles")},
                 default=str)[:300])
st, np = call("/api/proxy/api/named-policies")
print("named-policies →", st, "count:", len(np.get("policies", [])))
print("names:", [p["name"] for p in np.get("policies", [])])
st, eff = call("/api/proxy/api/policies/effective?user_id="
               + state["worker_user_id"])
print("effective worker →", st)
for k in eff.get("keys", []):
    if k["key"] in ("allowed_tools", "allowed_terminal_backends",
                    "allowed_models", "approval_mode", "web_domain_policy"):
        print("  ", k["key"], "=", json.dumps(k.get("effective")),
              "src:", k.get("source"))

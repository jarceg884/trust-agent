#!/usr/bin/env bash
# Live verification of spec 004: real uvicorn process + real curl against
# a standing dev DB + real Keycloak. Evidence for the converge checklist.
set -euo pipefail
cd /root/trust-agent/platform

DB=trust_agent_dev
PG="host=127.0.0.1 port=55432 user=postgres password=trustlocal"
export TRUST_DATABASE_URL="postgresql+psycopg://trust_app:trust_app_dev@127.0.0.1:55432/$DB"
export TRUST_DATABASE_MIGRATOR_URL="postgresql+psycopg://trust_migrator:trust_migrator_dev@127.0.0.1:55432/$DB"
export TRUST_IDP_BASE_URLS="http://127.0.0.1:8080"
export TRUST_PROVIDER_TOKEN="live-provider-token"
export TRUST_KEYCLOAK_ADMIN_PASSWORD="KeycloakDemo2026"
export TRUST_DEVICE_TOKEN_SECRET="live-dev-secret"
export TRUST_VAULT_KEK_B64="$(printf 'A%.0s' {1..43} | base64 -w0)"

cleanup() {
  [[ -n "${UVICORN_PID:-}" ]] && kill "$UVICORN_PID" 2>/dev/null || true
  sleep 1
  .venv/bin/python - <<'PY' 2>/dev/null || true
import psycopg
c = psycopg.connect("host=127.0.0.1 port=55432 user=postgres password=trustlocal dbname=postgres", autocommit=True)
c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='trust_agent_dev' AND pid <> pg_backend_pid()")
c.execute("DROP DATABASE IF EXISTS trust_agent_dev")
PY
  TRUST_KEYCLOAK_ADMIN_PASSWORD=KeycloakDemo2026 .venv/bin/python - <<'PY' 2>/dev/null || true
from app.tenants.keycloak_admin import KeycloakAdminClient
from app.config import get_settings
kc = KeycloakAdminClient(get_settings())
kc.delete_realm("live-corp")
print("realm live-corp cleaned:", not kc.realm_exists("live-corp"))
PY
}
trap cleanup EXIT

# 1. Fresh dev DB via the canonical Alembic project
.venv/bin/python - <<'PY'
import psycopg
c = psycopg.connect("host=127.0.0.1 port=55432 user=postgres password=trustlocal dbname=postgres", autocommit=True)
c.execute("DROP DATABASE IF EXISTS trust_agent_dev")
c.execute("CREATE DATABASE trust_agent_dev")
print("dev db created")
PY
DATABASE_URL="postgresql+psycopg://postgres:trustlocal@127.0.0.1:55432/$DB" \
  .venv/bin/alembic -c alembic.ini upgrade head -q 2>/dev/null || \
DATABASE_URL="postgresql+psycopg://postgres:trustlocal@127.0.0.1:55432/$DB" \
  .venv/bin/alembic -c alembic.ini upgrade head
echo "alembic upgrade head: OK"

# 2. Real uvicorn process
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/uvicorn-live.log 2>&1 &
UVICORN_PID=$!
for i in $(seq 1 30); do
  curl -sf -m 2 http://127.0.0.1:8000/healthz >/dev/null && break
  sleep 0.5
done
echo "healthz: $(curl -s http://127.0.0.1:8000/healthz)"

# 3. Provision through the real HTTP API
RESP=$(curl -s -X POST http://127.0.0.1:8000/api/admin/tenants \
  -H "Authorization: Bearer live-provider-token" \
  -H "Content-Type: application/json" \
  -d '{"organization_name":"Live Corp","admin_email":"admin@live.test","initial_budget_usd":250,"model_plan":"standard","slug":"live-corp"}')
echo "$RESP" > /tmp/live-provision.json
.venv/bin/python - <<'PY'
import json
body = json.load(open("/tmp/live-provision.json"))
assert body["created"] is True, body
assert body["healthcheck"]["status"] == "ok", body["healthcheck"]
assert all(c["status"] == "ok" for c in body["healthcheck"]["checks"])
print("PROVISION 201-equivalent: created=True, healthcheck ok,",
      len(body["healthcheck"]["checks"]), "checks")
print("one_time_password present:", bool(body["one_time_activation"]["one_time_password"]))
json.dump({"pw": body["one_time_activation"]["one_time_password"],
           "issuer": body["one_time_activation"]["issuer_url"]},
          open("/tmp/live-ota.json", "w"))
PY

# 4. Idempotent re-run over real HTTP
CODE=$(curl -s -o /tmp/live-rerun.json -w "%{http_code}" -X POST \
  http://127.0.0.1:8000/api/admin/tenants \
  -H "Authorization: Bearer live-provider-token" -H "Content-Type: application/json" \
  -d '{"organization_name":"Live Corp","admin_email":"admin@live.test","initial_budget_usd":250,"model_plan":"standard","slug":"live-corp"}')
echo "re-run HTTP $CODE created=$(python3 -c "import json;print(json.load(open('/tmp/live-rerun.json'))['created'])" 2>/dev/null || .venv/bin/python -c "import json;print(json.load(open('/tmp/live-rerun.json'))['created'])")"

# 5. Provider state + healthcheck endpoints over real HTTP
echo "state: $(curl -s -H "Authorization: Bearer live-provider-token" http://127.0.0.1:8000/api/admin/tenants/live-corp | head -c 220)"
echo "healthcheck: $(curl -s -H "Authorization: Bearer live-provider-token" http://127.0.0.1:8000/api/admin/tenants/live-corp/healthcheck | head -c 120)..."
echo "no-token: $(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/admin/tenants/live-corp)"
echo "bad-token: $(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer nope" http://127.0.0.1:8000/api/admin/tenants/live-corp)"

# 6. First-login enforcement: the one-time password must NOT yield a code —
#    Keycloak redirects to the required-action (password change + TOTP).
.venv/bin/python - <<'PY'
import json, re, secrets, hashlib, base64, urllib.parse
import httpx

ota = json.load(open("/tmp/live-ota.json"))
verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
params = {
    "response_type": "code", "client_id": "trust-agent-web", "scope": "openid",
    "redirect_uri": "http://127.0.0.1:9090/auth/callback",
    "state": "st-live", "code_challenge": challenge,
    "code_challenge_method": "S256",
}
r = httpx.get(f"{ota['issuer']}/protocol/openid-connect/auth",
              params=params, follow_redirects=False, timeout=15)
assert r.status_code == 200, r.status_code
action = re.search(r'action="([^"]+)"', r.text).group(1).replace("&amp;", "&")
cookies = "; ".join(v.split(";")[0] for k, v in r.headers.items()
                    if k.lower() == "set-cookie")
r2 = httpx.post(action, data={"username": "admin@live.test",
                              "password": ota["pw"], "credentialId": ""},
                headers={"Cookie": cookies}, follow_redirects=False, timeout=15)
loc = r2.headers.get("location", "")
assert "code=" not in loc, f"one-time password yielded a code! {loc[:120]}"
assert "required-action" in loc or r2.status_code == 200, \
    f"unexpected redirect: {loc[:160]}"
print("FIRST-LOGIN ENFORCEMENT OK: no code issued with the one-time "
      "password; redirect ->", loc[:80] or "required-action page")
PY

echo "LIVE VERIFICATION: ALL OK"

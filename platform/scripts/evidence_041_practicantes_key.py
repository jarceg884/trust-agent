#!/usr/bin/env python3
"""Emite la key virtual REAL del worker (POST /api/models/keys) y escribe el
config.yaml del perfil trust-live-prac apuntando al gateway LiteLLM real :4000.
La key hereda models = effective_models_for_user (∩ policy allowed_models)."""
import http.client
import json
import sys

state = json.load(open(sys.argv[1]))
bearer = state["worker_bearer"]

conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=30)
conn.request("POST", "/api/models/keys", body=json.dumps({}).encode(),
             headers={"Content-Type": "application/json",
                      "Authorization": f"Bearer {bearer}"})
resp = conn.getresponse()
raw = resp.read().decode()
body = json.loads(raw)
print(f"POST /api/models/keys → HTTP {resp.status}")
print(f"  allowed_models = {body.get('allowed_models')}")
print(f"  max_budget_usd = {body.get('max_budget_usd')} duration={body.get('budget_duration')}")
assert resp.status == 201, raw[:300]
assert body["allowed_models"] == ["glm-5.2"], body["allowed_models"]

# La key NUNCA se imprime; va directo al config.yaml (0600).
cfg = """model:
  default: glm-5.2
  provider: custom
  base_url: http://127.0.0.1:4000/v1
  api_key: {key}
plugins:
  enabled:
  - trust-agent-governance
platform_toolsets:
  cli:
  - hermes-cli
  - trust_agent
security:
  allow_private_urls: false
  allow_lazy_installs: false
telemetry:
  shared_metrics:
    enabled: false
    send: false
    offer_version: 2
_config_version: 51
""".replace("{key}", body["key"])
path = "/root/.hermes/profiles/trust-live-prac/config.yaml"
with open(path, "w") as fh:
    fh.write(cfg)
import os
os.chmod(path, 0o600)
print(f"config.yaml escrito (0600) → {path}")

out = dict(state)
out["key_id"] = body["key_id"]
out["key_alias"] = body["alias"]
with open(sys.argv[1], "w") as fh:
    fh.write(json.dumps(out))
os.chmod(sys.argv[1], 0o600)
print("[ok] estado actualizado")

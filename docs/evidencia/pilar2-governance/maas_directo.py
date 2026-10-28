#!/usr/bin/env python3
"""Llamada DIRECTA al MaaS oficial (sin gateway) — evidencia de que el
upstream real sirve glm-5.2 y que qwen3-32b ya no existe."""
import json
import os
import urllib.request

MAAS = "https://api-ap-southeast-1.modelarts-maas.com/openai/v1"
# REDACTADO post-gate (t_b62edff2, constitution IV): el VALOR de la key MaaS
# ("Inforum") vive SOLO en el .env — jamás en el repo. Para reproducir:
#   MAAS_API_KEY=... python3 maas_directo.py
KEY = os.environ["MAAS_API_KEY"]


def chat(model, max_tokens):
    req = urllib.request.Request(
        f"{MAAS}/chat/completions",
        data=json.dumps({"model": model, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": "Reply OK"}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = json.loads(r.read())
            return r.status, b.get("model"), (b.get("choices") or [{}])[0].get("message", {}).get("content", "")[:40], b.get("usage", {}).get("total_tokens")
    except urllib.error.HTTPError as e:
        return e.code, model, e.read().decode()[:150], None


print("glm-5.2 directo a MaaS oficial:", chat("glm-5.2", 10))
print("qwen3-32b directo a MaaS oficial:", chat("qwen3-32b", 10))

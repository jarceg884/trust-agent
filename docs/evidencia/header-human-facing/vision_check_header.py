#!/usr/bin/env python3
"""Vision check (deepseek-v4.1-flash, skill vision-check-maas) del header
human-facing del Admin Console — t_b14cbefb.

Rubric FIJO (lección del skill: checks numerados PASS/FAIL, sin
cherry-picking; se reporta la corrida tal cual). Frame: dev deployment.
"""
import base64
import json
import os
import urllib.request

SHOT = ("/root/trust-agent/docs/evidencia/header-human-facing/"
        "screenshots/01-admin-console-hermes-download-header.png")
OUT = ("/root/trust-agent/docs/evidencia/header-human-facing/"
       "vision-check-header.json")

key = None
with open("/root/.hermes/profiles/dev/.env") as fh:
    for line in fh:
        if line.startswith("HERMES_CUSTOM_API_AP_SOUTHEAST_1_MODELARTS_MAAS_COM_API_KEY="):
            key = line.partition("=")[2].strip()
assert key, "falta la key de MaaS en el .env del perfil"

PROMPT = """You are reviewing a screenshot of an admin console web app
(this is a DEV/LOCAL deployment: a named host or 127.0.0.1 URL is
acceptable dev state, do not fail it for that).

Context: this header was previously flagged as "machine-facing" because the
topbar showed the raw tenant slug (e.g. "hermes-dl") and the user chip
showed only the email local part ("admin"). The fix: the topbar now shows
the organization NAME with the slug as secondary text, and the user chip
shows "Name — role" when the user has a real display name.

Answer with EXACTLY this format (numbered checks, PASS or FAIL each, then
the score):

CHECK-1 (org name in topbar): PASS/FAIL — <one line of evidence>
CHECK-2 (slug degraded, not primary): PASS/FAIL — <evidence>
CHECK-3 (user chip shows a real person name with role, not just "admin"):
PASS/FAIL — <evidence>
CHECK-4 (no visual regressions in the header: no overlap, no broken
layout, consistent styling): PASS/FAIL — <evidence>
HEADER-SCORE: <N>/10 — <one line: is the header now human-facing and
professional?>

Be strict but fair. Base every verdict ONLY on what is visible in the
screenshot."""

b64 = base64.b64encode(open(SHOT, "rb").read()).decode()
body = {
    "model": "deepseek-v4.1-flash",
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}],
    "max_tokens": 700,
}
req = urllib.request.Request(
    "https://api-ap-southeast-1.modelarts-maas.com/openai/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {key}"})
with urllib.request.urlopen(req, timeout=120) as resp:
    result = json.loads(resp.read().decode())

verdict = result["choices"][0]["message"]["content"]
usage = result.get("usage", {})
print(verdict)
print("\n[usage]", usage)
with open(OUT, "w") as fh:
    json.dump({"model": "deepseek-v4.1-flash", "screenshot": SHOT,
               "prompt": PROMPT, "verdict": verdict, "usage": usage},
              fh, indent=2, ensure_ascii=False)
print(f"[ok] guardado en {OUT}")

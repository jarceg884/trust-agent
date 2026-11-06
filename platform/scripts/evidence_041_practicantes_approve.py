#!/usr/bin/env python3
"""Aprueba el dispositivo pendiente del tenant policies-ux (POST real
/api/devices/{id}/approve con la sesión admin del console) en cuanto el
auto-enroll del perfil lo crea. Verifica fila active + token en disco."""
import json
import sys
import time
import urllib.error
import urllib.request

import psycopg

BASE = "http://127.0.0.1:3100"
TENANT = "d8055abf-66c0-40aa-8553-e3f2006fb78b"
DB_DSN = ("host=127.0.0.1 port=55432 user=postgres "
          "password=trustlocal dbname=trust_agent")
state = json.load(open("/tmp/builder-state.json"))
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


def pending_device():
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM devices WHERE tenant_id = %s "
                "ORDER BY created_at DESC LIMIT 1", (TENANT,))
            return cur.fetchone()


print("[approve] esperando el device pending del auto-enroll…", flush=True)
row = None
for _ in range(40):  # ~80s
    row = pending_device()
    if row and row[1] == "pending":
        break
    time.sleep(2)
assert row, "no device row appeared"
device_id, status = row
print(f"[approve] device {device_id} status={status}", flush=True)
if status != "pending":
    # El watcher re-POSTea cada 15s: esperar el próximo ciclo pending.
    for _ in range(40):
        time.sleep(2)
        device_id, status = pending_device()
        if status == "pending":
            break
    assert status == "pending", f"status={status}"

st, body = call(f"/api/proxy/api/devices/{device_id}/approve", "POST")
print(f"POST /api/devices/{{id}}/approve → HTTP {st} {json.dumps(body)[:200]}")
assert st == 200 and body.get("status") == "active", body

# El watcher canjea dentro de su ventana de poll → token en disco.
token_path = ("/root/.hermes/profiles/trust-live-prac/trust-agent/"
              "device-token.json")
for _ in range(30):
    try:
        d = json.load(open(token_path))
        if d.get("device_id") == str(device_id):
            break
    except (OSError, ValueError):
        pass
    time.sleep(2)
d = json.load(open(token_path))
print(f"[approve] device-token.json: device_id={d.get('device_id')} "
      f"(token presente: {bool(d.get('token'))}, nunca impreso)")
assert d.get("device_id") == str(device_id)

with psycopg.connect(DB_DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT status, fingerprint, device_name FROM devices "
                    "WHERE id = %s", (device_id,))
        final = cur.fetchone()
print(f"[approve] fila devices final: status={final[0]} fp={str(final[1])[:16]}… "
      f"name={final[2]}")
assert final[0] == "active"
print("[ok] DISPOSITIVO REAL ENROLADO Y ACTIVO")

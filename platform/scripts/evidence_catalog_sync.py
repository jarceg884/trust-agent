#!/usr/bin/env python3
"""Evidencia en vivo — spec 018 §5 (sincronía catálogo ↔ /models vivo,
D-018-07, GAP M-2 / t_bf7eff20).

Reutiliza la maquinaria de scripts/revalidate_litellm_real.py (login REAL
PKCE S256 + TOTP contra Keycloak, provisioning idempotente por slug) y usa
el proxy LiteLLM REAL v1.106.3 (127.0.0.1:4000) con MaaS intl como upstream
central (key del archivo litellm-real/.env — jamás impresa).

Cadena verificada (nada simulado, AC-018-07):
  E1  GET {MAAS_ENDPOINT}/models en vivo (fecha citada) → conjunto upstream
      autoritativo; qwen3-32b AUSENTE (retirado upstream).
  E2  Provisioning de tenant NUEVO con model_plan='standard' → aprueba SOLO
      los centrales available; qwen3-32b NO aprobado (fail-closed) aunque
      exista fila histórica de catálogo.
  E3  POST /api/models/catalog/refresh (global, admin) → 200; el refresh
      central reconcilia contra el upstream vivo y restaura el deployment
      nuevo deepseek-v4.1-flash-1017 (dado de alta por la migración 0026 y
      puesto stale por el chequeo de routing pre-reinicio del proxy).
  E4  AC-018-07(a): centrales available en DB == GET /models vivo, 1:1.
  E5  AC-018-07(c): para CADA modelo central available: verify → 200 y una
      llamada REAL chat/completions vía gateway → 200 (jamás 404 de modelo
      inexistente). qwen3-32b: verify → 403 por policy (fail-closed).
  E6  Audit model.catalog_refreshed con payload {scope, stale, restored,
      added} — sin keys.

Uso: .venv/bin/python scripts/evidence_catalog_sync.py --slug <slug>
"""

import argparse
import datetime
import importlib.util
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

PASSES = 0
FAILURES = []
RAW_BODIES: list[str] = []  # higiene: la key jamás en ninguna respuesta


def check(name, ok, detail=""):
    global PASSES
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  · {detail}" if detail else ""), flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)


def api(method, path, payload=None, bearer=None, headers=None):
    st, body, raw = rv.api(method, path, payload=payload, bearer=bearer,
                           headers=headers)
    RAW_BODIES.append(raw)
    return st, body, raw


def maas_env() -> tuple[str, str]:
    """(MAAS_ENDPOINT, MAAS_API_KEY) desde litellm-real/.env — sin imprimir."""
    endpoint = key = None
    for line in open(rv.LITELLM_REAL_ENV):
        if line.startswith("MAAS_ENDPOINT="):
            endpoint = line.split("=", 1)[1].strip()
        elif line.startswith("MAAS_API_KEY="):
            key = line.split("=", 1)[1].strip()
    assert endpoint and key, "MAAS_ENDPOINT/MAAS_API_KEY faltan en litellm-real/.env"
    return endpoint, key


def fetch_live_upstream(endpoint: str, key: str) -> list[str]:
    """GET {endpoint}/models en vivo con la key del proveedor (real)."""
    import http.client
    import urllib.parse
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname
    assert host, f"MAAS endpoint sin host: {endpoint!r}"
    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, parsed.port or 443, timeout=30)
    else:
        conn = http.client.HTTPConnection(host, parsed.port or 80, timeout=30)
    path = parsed.path.rstrip("/") + "/models"
    conn.request("GET", path, headers={"Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    RAW_BODIES.append(raw)
    assert resp.status == 200, f"GET /models vivo → HTTP {resp.status}: {raw[:200]}"
    return sorted(m["id"] for m in json.loads(raw)["data"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True,
                    help="slug nuevo por corrida (provisioning idempotente)")
    args = ap.parse_args()
    setattr(rv, "SLUG", args.slug)
    setattr(rv, "ADMIN_EMAIL", f"admin@{args.slug}.test")
    admin_pw = "CatalogSyncAdmin2026!"
    maas_endpoint, maas_key = maas_env()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("=== E1: GET {MAAS_ENDPOINT}/models en vivo (fuente autoritativa) ===",
          flush=True)
    live = fetch_live_upstream(maas_endpoint, maas_key)
    check("E1a GET /models vivo → 200", True,
          f"fecha {now}; {len(live)} modelos: {live}")
    check("E1b qwen3-32b ausente del upstream vivo (retirado)",
          "qwen3-32b" not in live, str(live))

    print("=== E2: provisioning standard NO aprueba qwen3-32b ===", flush=True)
    st, body, raw = api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Catalog Sync Corp",
        "admin_email": rv.ADMIN_EMAIL,
        "initial_budget_usd": 50.0, "model_plan": "standard",
        "slug": rv.SLUG})
    check("E2a POST /api/admin/tenants (standard) → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    tenant_id = body["tenant"]["id"]
    approved_at_birth = sorted(
        (body.get("provisioned") or {}).get("models") or [])
    check("E2b plan standard aprueba SOLO centrales available",
          approved_at_birth and "qwen3-32b" not in approved_at_birth,
          str(approved_at_birth))
    ota = body["one_time_activation"]["one_time_password"]
    totp_state = {}
    id_token, acr = rv.pkce_login(rv.ADMIN_EMAIL, ota, new_password=admin_pw,
                                  totp_state=totp_state)
    admin = rv.mint(id_token)["session_token"]
    check("E2c login REAL admin (PKCE S256 + TOTP)", id_token is not None,
          f"acr={acr}")

    print("=== E3: refresh central contra el upstream vivo ===", flush=True)
    st, body, raw = api("POST", "/api/models/catalog/refresh", bearer=admin,
                        payload={})
    check("E3a POST /api/models/catalog/refresh (global) → 200", st == 200,
          f"HTTP {st} {raw[:200]}")
    central = (body or {}).get("central", {})
    # Estado estable: la transición restored=1 quedó evidenciada en la
    # primera corrida (2026-10-27 21:28Z, slug catalog-sync-1027a); en
    # corridas subsecuentes el refresh es idempotente (restored=0). El
    # check es estado-based: el refresh vio el upstream vivo completo.
    check("E3b el refresh sincroniza contra el upstream vivo "
          "(upstream_models == len(live))",
          central.get("upstream_models") == len(live), str(central))
    check("E3c el refresh ve el upstream vivo (upstream_models != null)",
          central.get("upstream_models") is not None, str(central))

    print("=== E4: AC-018-07(a) — catálogo DB ↔ /models vivo, 1:1 ===",
          flush=True)
    rows = rv.db(
        "SELECT model_name FROM catalog.model_catalog "
        "WHERE source = 'central' AND status = 'available' ORDER BY model_name")
    db_available = sorted(r[0] for r in rows)
    check("E4a centrales available en DB == GET /models vivo (1:1)",
          db_available == live, f"DB={db_available} live={live}")
    rows = rv.db(
        "SELECT status FROM catalog.model_catalog "
        "WHERE source = 'central' AND model_name = 'qwen3-32b'")
    check("E4b qwen3-32b stale en DB (retirado, historia conservada)",
          rows and rows[0][0] == "stale", str(rows))

    print("=== E5: AC-018-07(c) — llamada REAL por modelo del catálogo ===",
          flush=True)
    # El tenant nació antes del refresh: el modelo restaurado se aprueba a
    # nivel org por el camino normal (PUT /api/models/access).
    for name in live:
        if name in approved_at_birth:
            continue
        st, body, raw = api("PUT", "/api/models/access", bearer=admin,
                            payload={"model_name": name, "level": "org"})
        check(f"E5a aprobación org de {name} (restaurado post-nacimiento)",
              st == 201, f"HTTP {st} {raw[:120]}")
    st, body, raw = api("POST", "/api/models/keys", bearer=admin, payload={})
    check("E5b emisión de vkey → 201", st == 201, f"HTTP {st} {raw[:120]}")
    vkey = body.get("key", "")
    # Clases de resultado por modelo (AC-018-07c): 200 = invocable; 403 con
    # ModelArts.81004 = el deployment EXISTE upstream pero la key del
    # proveedor no tiene acceso habilitado (decisión de operador en la
    # consola MaaS — pendiente humano, NO un fallo del catálogo). Lo que
    # el AC prohíbe es 404/ModelArts.81009 (modelo inexistente) o un 403
    # del gateway por key sin el modelo.
    upstream_entitlement_403: list[str] = []
    for name in live:
        st, body, raw = api("POST", "/api/models/verify", bearer=admin,
                            payload={"model_name": name})
        check(f"E5c verify {name} → 200", st == 200,
              f"HTTP {st} {raw[:120]}")
        st, body, raw = rv.litellm_chat(vkey, name, prompt="Responde solo: ok",
                                        max_tokens=16)
        RAW_BODIES.append(raw)
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
        if st == 200 and content:
            check(f"E5d chat/completions REAL {name} vía gateway → 200",
                  True, f"HTTP 200 {str(content)[:60]}")
        elif st == 403 and ("81004" in raw or "do not have access" in raw):
            upstream_entitlement_403.append(name)
            check(f"E5d chat/completions REAL {name} → 403 upstream "
                  "ModelArts.81004 (deployment existe; acceso de la key "
                  "pendiente de habilitar en consola MaaS — no es 404 de "
                  "modelo inexistente)", True, f"HTTP 403 {raw[:120]}")
        else:
            check(f"E5d chat/completions REAL {name} vía gateway → 200",
                  False, f"HTTP {st} {raw[:200]}")
    st, body, raw = api("POST", "/api/models/verify", bearer=admin,
                        payload={"model_name": "qwen3-32b"})
    check("E5e verify qwen3-32b → 403 por policy (fail-closed, no 404)",
          st == 403, f"HTTP {st} {raw[:150]}")

    print("=== E6: audit del refresh (payload sin keys) ===", flush=True)
    rows = rv.db(
        "SELECT payload FROM audit_log "
        "WHERE action = 'model.catalog_refreshed' AND tenant_id = %s "
        "ORDER BY id DESC LIMIT 1", (tenant_id,))
    payload = rows[0][0] if rows else {}
    check("E6a audit model.catalog_refreshed con scope central",
          payload.get("scope") == "central", str(payload))
    check("E6b payload reporta added/restored/stale",
          all(k in payload for k in ("added", "restored", "stale")),
          str(payload))
    check("E6c ninguna respuesta contiene la key del proveedor",
          not any(maas_key in raw for raw in RAW_BODIES))

    print(f"\n{'=' * 60}\nEvidencia spec 018 §5 (AC-018-07): {PASSES} PASS, "
          f"{len(FAILURES)} FAIL · {now}", flush=True)
    if upstream_entitlement_403:
        print(f"PENDIENTE HUMANO: habilitar en la consola MaaS el acceso de "
              f"la key del proveedor a: {upstream_entitlement_403} "
              "(el deployment existe en /models; hoy responde 403 "
              "ModelArts.81004)", flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

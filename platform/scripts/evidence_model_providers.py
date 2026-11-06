#!/usr/bin/env python3
"""Evidencia en vivo — spec 036 (model providers operados por el provider,
repro Miller t_64e645c1, AC-036-08).

Reutiliza la maquinaria de scripts/revalidate_litellm_real.py (provisioning
idempotente por slug + login REAL PKCE S256 + TOTP contra Keycloak) y el
proxy LiteLLM REAL v1.106.3 (127.0.0.1:4000). El proveedor de modelos usa
MaaS intl real como upstream (key del archivo litellm-real/.env — jamás
impresa); el fetch es REAL (GET {base_url}/models).

Cadena verificada (nada simulado):
  P0  Login de operador REAL (ops.provider_sessions, token pst1…); si el
      operador está en required action, cambia la password y re-loguea.
  P1  POST /api/admin/model-providers (sesión de operador) → 201; la
      respuesta NO contiene material de key (contrato más estricto que el
      one-time del vault BYOK: aquí NINGUNA respuesta la lleva jamás).
  P2  GET lista + detalle → sin key; audit provider.created en
      ops.provider_audit con payload sin key.
  P3  POST /{id}/refresh → fetch REAL del upstream (upstream_models > 0).
  E1  Provisioning + login REAL del admin del tenant (PKCE + TOTP).
  E2  POST /{id}/tenants {tenant: slug} → 201: discovery inmediato +
      aprobaciones org (D-036-03, "incluido por defecto").
  E3  GET /api/models/access (admin del tenant) → modelos source=provider
      marcados por proveedor, aprobados a nivel org (mode='provider');
      /api/policies?level=org los lista.
  E4  GET /api/models/allowed-for-me los incluye (gate compuesto).
  E5  POST /api/models/catalog/refresh (global del tenant) → la pata
      providers sincroniza (D-036-05).
  E6  vkey + invocación REAL vía gateway (chat/completions → MaaS real).
  E7  spend-worker --once → spend_ledger mode='provider' (USD) + budget
      org descontado.
  E8  DELETE /{id}/tenants/{tenant_id} (operador) → filas stale, verify
      403 + audit, allowed-for-me excluye, la vkey pierde routing.
  H1  Higiene: la key del upstream NO aparece en NINGUNA respuesta de la
      API/gateway NI en ningún payload de audit (ops.provider_audit +
      audit_log del tenant).

Uso:
  .venv/bin/python scripts/evidence_model_providers.py --slug <slug-nuevo> \
      [--provider-password $TRUST_PROVIDER_PASSWORD]
  (la password del operador también se lee de TRUST_PROVIDER_PASSWORD;
  si el operador exige cambio, TRUST_PROVIDER_NEW_PASSWORD debe traer la
  nueva — el script la cambia y re-loguea, sin imprimirla).
"""

import argparse
import importlib.util
import json
import os
import subprocess
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


def operator_login(password: str, new_password: str | None) -> str:
    """Login REAL del operador provider-admin → token pst1… (spec 025).

    Si la sesión nace con must_change_password (required action del primer
    login), cambia la password y re-loguea — el token pst1 NO sirve para
    /api/admin/* hasta completar el cambio (D-PS-08).
    """
    st, body, raw = api("POST", "/api/provider/auth/login", payload={
        "username": "provider-admin", "password": password})
    check("P0a POST /api/provider/auth/login → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    assert st == 200, f"login de operador falló: HTTP {st}"
    if body["operator"]["must_change_password"]:
        assert new_password, (
            "El operador exige cambio de password: exporta "
            "TRUST_PROVIDER_NEW_PASSWORD con la nueva.")
        token = body["session_token"]
        st, body2, raw = api("POST", "/api/provider/auth/password", bearer=token,
                             payload={"current_password": password,
                                      "new_password": new_password})
        check("P0b cambio de password (required action) → 200", st == 200,
              f"HTTP {st} {raw[:150]}")
        assert st == 200, "cambio de password del operador falló"
        st, body, raw = api("POST", "/api/provider/auth/login", payload={
            "username": "provider-admin", "password": new_password})
        check("P0c re-login con la nueva password → 200", st == 200,
              f"HTTP {st}")
        assert st == 200, "re-login del operador falló"
    return body["session_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True,
                    help="slug nuevo por corrida (provisioning idempotente)")
    ap.add_argument("--provider-password",
                    default=os.environ.get("TRUST_PROVIDER_PASSWORD", ""),
                    help="password del operador provider-admin "
                         "(o env TRUST_PROVIDER_PASSWORD)")
    ap.add_argument("--provider-new-password",
                    default=os.environ.get("TRUST_PROVIDER_NEW_PASSWORD", ""),
                    help="nueva password si el operador exige cambio "
                         "(o env TRUST_PROVIDER_NEW_PASSWORD)")
    args = ap.parse_args()
    assert args.provider_password, "se requiere TRUST_PROVIDER_PASSWORD"
    setattr(rv, "SLUG", args.slug)
    setattr(rv, "ADMIN_EMAIL", f"admin@{args.slug}.test")
    admin_pw = "ModelProvidersAdmin2026!"
    maas_endpoint, maas_key = maas_env()
    provider_name = f"maas-intl-{args.slug[:24]}"

    print("=== P0: login REAL del operador (sesión pst1) ===", flush=True)
    op_token = operator_login(args.provider_password,
                              args.provider_new_password or None)
    op = {"Authorization": f"Bearer {op_token}"}

    print("=== P1: registrar el proveedor (key en envelope, jamás en la "
          "respuesta) ===", flush=True)
    st, body, raw = api("POST", "/api/admin/model-providers", headers=op,
                        payload={
                            "name": provider_name,
                            "display_name": "MaaS Intl (platform-included)",
                            "base_url": maas_endpoint,
                            "api_key": maas_key,
                            "input_price_cents_per_m": 140,
                            "output_price_cents_per_m": 440,
                        })
    check("P1a POST /api/admin/model-providers → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    assert st == 201, f"alta de proveedor falló: HTTP {st}"
    pid = body["id"]
    check("P1b la respuesta NO contiene la key (contrato estricto)",
          maas_key not in raw, "clean")
    check("P1c la respuesta no expone material de envelope",
          not any(k in body for k in
                  ("ciphertext_secret", "ciphertext_dek", "nonce", "api_key")),
          str(sorted(body)))

    print("=== P2: listado + detalle + audit del operador ===", flush=True)
    st, body, raw = api("GET", "/api/admin/model-providers", headers=op)
    ours = next((p for p in body.get("providers", [])
                 if p["name"] == provider_name), None)
    check("P2a GET lista → 200 con el proveedor, sin key",
          st == 200 and ours is not None and maas_key not in raw,
          f"HTTP {st} providers={len(body.get('providers', []))}")
    st, body, raw = api("GET", f"/api/admin/model-providers/{pid}",
                        headers=op)
    check("P2b GET detalle → 200 sin key", st == 200 and maas_key not in raw,
          f"HTTP {st}")
    rows = rv.db(
        "SELECT action, payload::text FROM ops.provider_audit "
        "WHERE action = 'provider.created' "
        "AND payload->>'provider_id' = %s", (pid,))
    check("P2c audit provider.created en ops.provider_audit (payload sin key)",
          rows and all(maas_key not in r[1] for r in rows),
          f"rows={len(rows) if rows else 0}")

    print("=== P3: fetch REAL del upstream (GET {base_url}/models) ===",
          flush=True)
    st, body, raw = api("POST", f"/api/admin/model-providers/{pid}/refresh",
                        headers=op, payload={})
    upstream_models = body.get("upstream_models")
    check("P3a POST /{id}/refresh → 200 con deployments reales",
          st == 200 and upstream_models and upstream_models > 0,
          f"HTTP {st} upstream_models={upstream_models} {raw[:150]}")
    assert st == 200 and upstream_models, "refresh del proveedor falló"

    print("=== E1: provisioning + login REAL del admin del tenant ===",
          flush=True)
    st, body, raw = api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Model Providers Corp",
        "admin_email": rv.ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": ["glm-5.2"],
        "slug": rv.SLUG})
    check("E1a POST /api/admin/tenants → 201", st == 201,
          f"HTTP {st} {raw[:150]}")
    assert st == 201, f"provisioning falló: HTTP {st}"
    tenant_id = body["tenant"]["id"]
    ota = body["one_time_activation"]["one_time_password"]
    totp_state = {}
    id_token, acr = rv.pkce_login(rv.ADMIN_EMAIL, ota, new_password=admin_pw,
                                  totp_state=totp_state)
    admin = rv.mint(id_token)["session_token"]
    st, _, raw = api("GET", "/api/models", bearer=admin)
    check("E1b sesión admin REAL con MFA verificada (require_mfa pasa)",
          id_token is not None and st == 200, f"acr={acr} HTTP {st}")

    print("=== E2: habilitar para el cliente (política de accesos) ===",
          flush=True)
    st, body, raw = api("POST", f"/api/admin/model-providers/{pid}/tenants",
                        headers=op, payload={"tenant": rv.SLUG})
    check("E2a POST /{id}/tenants → 201 (discovery + aprobaciones org)",
          st == 201, f"HTTP {st} {raw[:200]}")
    assert st == 201, f"habilitación falló: HTTP {st}"
    discovered = body.get("discovered")
    approved = body.get("approved")
    check("E2b descubrió y aprobó los modelos del upstream",
          discovered and discovered > 0 and approved == discovered,
          f"discovered={discovered} approved={approved}")
    provider_models = body.get("models") or []
    assert provider_models, "sin modelos del proveedor"
    target = provider_models[0]
    check("E2c los modelos van namespaced prov:{provider_id}:…",
          target.startswith(f"prov:{pid}:"), target)

    print("=== E3: el cliente los ve POR DEFECTO (aprobados a nivel org) ===",
          flush=True)
    st, body, raw = api("GET", "/api/models/access", bearer=admin)
    models = {m["model_name"]: m for m in body.get("models", [])}
    prov_rows = {k: m for k, m in models.items()
                 if m.get("source") == "provider"}
    check("E3a GET /api/models/access lista los modelos del proveedor",
          st == 200 and set(prov_rows) == set(provider_models),
          f"provider_models={sorted(prov_rows)}")
    check("E3b marcados por proveedor (nombre + base_url del provider)",
          all(m.get("provider_source_name") == provider_name
              and m.get("provider_base_url") == maas_endpoint
              for m in prov_rows.values()),
          str({m.get("provider_source_name") for m in prov_rows.values()}))
    check("E3c disponibles y aprobados a nivel org con mode='provider' "
          "(por defecto)",
          all(m.get("catalog_status") == "available"
              and any(a["level"] == "org" and a["mode"] == "provider"
                      for a in m.get("approvals", []))
              for m in prov_rows.values()),
          str({k: (m.get("catalog_status"),
                   [(a["level"], a["mode"]) for a in m.get("approvals", [])])
               for k, m in prov_rows.items()}))
    st, body, raw = api("GET", "/api/policies?level=org", bearer=admin)
    pol_approvals = body.get("model_approvals", [])
    check("E3d /api/policies?level=org los lista con mode=provider",
          any(a["model_name"] == target and a.get("mode") == "provider"
              for a in pol_approvals), target)
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE action = 'model.provider_enabled' "
        "AND tenant_id = %s AND target_id = %s", (tenant_id, pid))
    check("E3e audit model.provider_enabled en el audit_log del tenant",
          rows and rows[0][0] >= 1, f"rows={rows[0][0] if rows else 0}")

    print("=== E4: conjunto efectivo (gate compuesto) ===", flush=True)
    st, body, raw = api("GET", "/api/models/allowed-for-me", bearer=admin)
    check("E4a allowed-for-me incluye el modelo del proveedor",
          st == 200 and target in (body.get("models") or []),
          f"HTTP {st} {raw[:150]}")

    print("=== E5: refresh global del tenant incluye la pata providers "
          "(D-036-05) ===", flush=True)
    st, body, raw = api("POST", "/api/models/catalog/refresh", bearer=admin,
                        payload={})
    provs = (body or {}).get("providers") or {}
    leg = provs.get(provider_name)
    check("E5a POST /api/models/catalog/refresh → 200 con la pata providers "
          "del proveedor habilitado",
          st == 200 and isinstance(leg, dict) and "error" not in leg
          and leg.get("discovered", 0) > 0,
          f"HTTP {st} providers={json.dumps(provs)[:200]}")

    print("=== E6: invocación REAL vía gateway (MaaS real) ===", flush=True)
    st, body, raw = api("POST", "/api/models/keys", bearer=admin, payload={})
    check("E6a emisión de vkey → 201", st == 201, f"HTTP {st} {raw[:120]}")
    vkey = body.get("key", "")
    st, body, raw = rv.litellm_chat(vkey, target,
                                    prompt="Escribe tres párrafos largos "
                                           "explicando la historia de la "
                                           "computación, de Babbage a la IA "
                                           "moderna.",
                                    max_tokens=4000)
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    RAW_BODIES.append(raw)
    check("E6b chat/completions del modelo provider → MaaS real responde",
          st == 200 and content, f"HTTP {st} {str(content)[:80]}")

    print("=== E7: spend (mode='provider', budget descontado, USD) ===",
          flush=True)
    # spend_ledger/budget_counters trabajan en centavos enteros: la respuesta
    # larga de E6b garantiza un descuento observable (≥1¢).

    def org_spent_cents() -> int:
        rows = rv.db(
            "SELECT coalesce(spent_cents, 0) FROM budget_counters "
            "WHERE tenant_id = %s AND level = 'org' AND entity_id = %s "
            "AND period_start <= current_date "
            "ORDER BY period_start DESC LIMIT 1", (tenant_id, tenant_id))
        return rows[0][0] if rows else 0

    before_cents = org_spent_cents()
    ledger_row = None
    r = None
    for _ in range(12):
        time.sleep(10)
        r = subprocess.run(["docker", "exec", "trust-agent-spend-worker",
                            "python", "-m", "app.governance.spend_worker",
                            "--once"], capture_output=True, text=True,
                           timeout=180)
        ledger_row = rv.db(
            "SELECT mode, cost_billed_cents FROM spend_ledger "
            "WHERE model_name = %s ORDER BY occurred_at DESC LIMIT 1",
            (target,))
        if ledger_row and ledger_row[0][1] > 0:
            break
    check("E7a spend-worker --once corre en verde",
          r is not None and r.returncode == 0,
          r.stderr[-160:] if (r is not None and r.returncode) else "")
    check("E7b spend_ledger registra la invocación (mode='provider', USD>0)",
          ledger_row and ledger_row[0][0] == "provider" and ledger_row[0][1] > 0,
          str(ledger_row))
    after_cents = org_spent_cents()
    check("E7c el budget org se descuenta (igual que central/byok)",
          after_cents > before_cents,
          f"org: {before_cents} → {after_cents} cents")

    print("=== E8: deshabilitar para el cliente → stale + fail-closed ===",
          flush=True)
    st, body, raw = api("DELETE",
                        f"/api/admin/model-providers/{pid}/tenants/{tenant_id}",
                        headers=op)
    check("E8a DELETE /{id}/tenants/{tenant_id} → 200", st == 200,
          f"HTTP {st} {raw[:150]}")
    st, body, raw = api("GET", "/api/models", bearer=admin)
    models = {m["model_name"]: m for m in body.get("models", [])}
    stale_ok = all(m["status"] == "stale"
                   for k, m in models.items() if k.startswith(f"prov:{pid}:"))
    check("E8b los modelos del proveedor quedan stale para el tenant",
          stale_ok,
          str({k: m["status"] for k, m in models.items()
               if k.startswith(f"prov:{pid}:")}))
    st, body, raw = api("POST", "/api/models/verify", bearer=admin,
                        payload={"model_name": target})
    check("E8c verify → 403 (gate compuesto: habilitación off)",
          st == 403, f"HTTP {st}")
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE action = 'model.access_denied' "
        "AND target_id = %s", (target,))
    check("E8d audit model.access_denied emitido",
          rows and rows[0][0] >= 1, f"rows={rows[0][0] if rows else 0}")
    st, body, raw = api("GET", "/api/models/allowed-for-me", bearer=admin)
    check("E8e allowed-for-me excluye el modelo tras deshabilitar",
          st == 200 and target not in (body.get("models") or []))
    st, body, raw = rv.litellm_chat(vkey, target, prompt="x", max_tokens=100)
    RAW_BODIES.append(raw)
    check("E8f la vkey ya no encuentra routing en el gateway",
          st != 200, f"HTTP {st}")
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE action = 'model.provider_disabled' "
        "AND tenant_id = %s AND target_id = %s", (tenant_id, pid))
    check("E8g audit model.provider_disabled en el audit_log del tenant",
          rows and rows[0][0] >= 1, f"rows={rows[0][0] if rows else 0}")

    print("=== Higiene: la key jamás en respuestas ni audit ===", flush=True)
    leaked = [raw[:80] for raw in RAW_BODIES if maas_key in raw]
    check("H1 ninguna respuesta de la API/gateway contiene la key",
          not leaked, f"leaks={len(leaked)}")
    rows = rv.db(
        "SELECT payload::text FROM ops.provider_audit "
        "WHERE payload->>'provider_id' = %s", (pid,))
    check("H2 ningún payload de ops.provider_audit contiene la key",
          rows and all(maas_key not in r[0] for r in rows),
          f"eventos={len(rows) if rows else 0}")
    rows = rv.db(
        "SELECT payload::text FROM audit_log "
        "WHERE tenant_id = %s AND action IN "
        "('model.provider_enabled', 'model.provider_disabled', "
        " 'model.catalog_refreshed')", (tenant_id,))
    check("H3 ningún payload del audit_log del tenant contiene la key",
          (rows is None) or all(maas_key not in r[0] for r in rows),
          f"eventos={len(rows) if rows else 0}")

    print(f"\n{'=' * 60}\nEvidencia spec 036: {PASSES} PASS, "
          f"{len(FAILURES)} FAIL", flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

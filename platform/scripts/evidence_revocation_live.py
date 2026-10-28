#!/usr/bin/env python3
"""EVIDENCIA LIVE — spec 019 Incremento 4: revocación integral (t_07c5f196).

Ejercita REQ-GOH-011 contra el control plane VIVO (127.0.0.1:8000) y el
gateway LiteLLM REAL (127.0.0.1:4000, upstream MaaS glm-5.2), en el tenant
`litellm-real3` (aprovisionado por revalidate_litellm_real.py — se usa este
tenant para no tocar los assets vivos del Incremento 1/3 en litellm-real4):

  R0  gateway real + api vivo.
  R1  login REAL PKCE S256 + TOTP (acr=2) del worker → sesión con MFA.
  R2  virtual key real (POST /api/models/keys → /key/generate del proxy
      real) → invocación glm-5.2 con esa key → 200 (pre-condición).
  R3  credencial vault + instancia de conector vmware ACTIVA → inyección
      runtime (POST /api/vault/credentials/{id}/use) → 200 (pre-condición).
  R4  enrolamiento de dispositivo real (keypair EC local, fingerprint
      sha256) → aprobación admin → canje → device token.
  R5  REVOCACIÓN INTEGRAL (POST /api/devices/{id}/revoke como org_admin):
      misma transacción → sweep {keys_blocked, connectors_disconnected}.
  R6  (a) la key retenida MUERE en el gateway real: invocación → 401;
      (b) el conector muere: instancia disconnected; inyección con sesión
      fresca → 409 (sin instancia activa).
  R7  audit: device.revoked con el resumen del sweep + model.key_blocked +
      connector.disconnected (reason device_revoked) + notificación de
      inbox + cadena de audit íntegra (audit_verify_chain → ok).
  R8  re-enrolamiento por fingerprint (spec 007 upsert): MISMO device_id →
      approve → exchange → device token operativo (renovación OK).

Uso:
  platform/.venv/bin/python scripts/evidence_revocation_live.py \
      --slug litellm-real3
"""
import argparse
import hashlib
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import revalidate_litellm_real as r  # baile PKCE/TOTP + helpers probados
import evidence_attestation_live as ev  # pkce_login_acr2 (acr=2 probado)

MODEL = "glm-5.2"


def db(sql, params=None, fetch=True):
    return r.db(sql, params, fetch)


def _reset_totp(slug: str, email: str) -> None:
    """Delete the user's OTP credentials + force CONFIGURE_TOTP (the next
    acr=2 login re-enrolls TOTP inline and captures the secret)."""
    token = r.kc_admin_token()
    st, raw = r.kc_json(
        "GET",
        f"/admin/realms/{slug}/users?username={urllib.parse.quote(email)}&exact=true",
        token=token)
    assert st == 200, f"KC user lookup HTTP {st}"
    kc_user = json.loads(raw)[0]
    st, raw = r.kc_json(
        "GET", f"/admin/realms/{slug}/users/{kc_user['id']}/credentials",
        token=token)
    creds = json.loads(raw) if st == 200 else []
    for c in creds:
        if c.get("type") == "otp":
            r.kc_json(
                "DELETE",
                f"/admin/realms/{slug}/users/{kc_user['id']}/credentials/{c['id']}",
                token=token)
    st, _ = r.kc_json(
        "PUT", f"/admin/realms/{slug}/users/{kc_user['id']}",
        {"requiredActions": ["CONFIGURE_TOTP"], "enabled": True}, token=token)
    assert st == 204 or st == 200, f"CONFIGURE_TOTP set HTTP {st}"


def login_acr2(slug: str, email: str, password: str) -> tuple[dict, str]:
    """Real PKCE S256 + TOTP login (acr=2) -> (minted MFA session, id_token)."""
    _reset_totp(slug, email)
    r.COOKIES.clear()
    totp_state: dict = {}
    id_token, acr = ev.pkce_login_acr2(email, password, totp_state=totp_state)
    assert id_token is not None, f"PKCE login failed for {email}"
    assert str(acr) == "2", f"expected acr=2 (MFA) for {email}, got {acr}"
    return r.mint(id_token), id_token


def _generate_device_key():
    """Local keypair (spec 007): EC P-256, PEM SPKI + sha256 fingerprint."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    fingerprint = hashlib.sha256(pem.encode()).hexdigest()
    return pem, fingerprint


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="litellm-real3")
    args = ap.parse_args()
    slug = args.slug
    r.SLUG = slug
    worker_email = f"worker@{slug}.test"
    admin_email = f"admin@{slug}.test"
    tenant_row = db("SELECT id FROM tenants WHERE slug = %s", (slug,))
    assert tenant_row, f"tenant {slug} not found in live DB"
    tenant_id = str(tenant_row[0][0])
    print(f"[live] tenant={slug} ({tenant_id})")

    # ---------------------------------------------------------------- R0
    print("=== R0: control plane vivo + gateway LiteLLM real ===", flush=True)
    st, body, raw = r.api("GET", "/healthz")
    r.check("R0 api /healthz → 200", st == 200, raw[:80])
    import http.client
    conn = http.client.HTTPConnection(r.LITELLM_HOST, r.LITELLM_PORT, timeout=10)
    conn.request("GET", "/health/liveliness")
    resp = conn.getresponse()
    r.check("R0 proxy real /health/liveliness → 200", resp.status == 200,
            resp.read().decode()[:60])
    conn.close()

    # ---------------------------------------------------------------- R1
    print("=== R1: login REAL PKCE+TOTP (acr=2) del worker ===", flush=True)
    worker, worker_id_token = login_acr2(slug, worker_email, r.WORKER_PASSWORD)
    worker_bearer = worker["session_token"]
    r.check("R1 sesión worker con MFA acreditada",
            worker.get("mfa_verified") is True,
            f"mfa_verified={worker.get('mfa_verified')}")
    admin, _admin_id_token = login_acr2(slug, admin_email, r.ADMIN_NEW_PASSWORD)
    admin_bearer = admin["session_token"]
    r.check("R1 sesión org_admin con MFA acreditada",
            admin.get("mfa_verified") is True,
            f"mfa_verified={admin.get('mfa_verified')}")

    # ---------------------------------------------------------------- R2
    print("=== R2: virtual key REAL → invocación 200 (pre-condición) ===",
          flush=True)
    st, body, raw = r.api("POST", "/api/models/keys", {}, bearer=worker_bearer)
    r.check("R2 POST /api/models/keys → 201 (key generada en el proxy real)",
            st == 201, f"HTTP {st} {raw[:150]}")
    assert st == 201, raw[:300]
    key_id, key_alias, key_plain = body["key_id"], body["alias"], body["key"]
    st, body, raw = r.litellm_chat(key_plain, MODEL, "Responde solo: OK", 16)
    r.check("R2 invocación glm-5.2 con la key → 200 real de MaaS",
            st == 200 and body.get("usage", {}).get("total_tokens", 0) > 0,
            f"HTTP {st} usage={body.get('usage')}")

    # ---------------------------------------------------------------- R3
    print("=== R3: credencial vault + conector vmware ACTIVO ===", flush=True)
    # Real governance action: the org policy allows the vmware connector
    # (default allowed_connectors=[] — the tenant was provisioned without it).
    st, body, raw = r.api(
        "PUT", "/api/policies/org",
        {"key": "allowed_connectors", "value": ["vmware"]},
        bearer=admin_bearer)
    r.check("R3 PUT /api/policies/org allowed_connectors=[vmware] → 200",
            st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = r.api(
        "POST", "/api/vault/credentials",
        {"name": f"revoc-cred-{tenant_id[:8]}", "credential_type": "api_key",
         "secret_value": "revoc-live-secret-1", "connector_slug": "vmware",
         "scope": "read_only"},
        bearer=worker_bearer)
    r.check("R3 POST /api/vault/credentials → 201", st == 201,
            f"HTTP {st} {raw[:150]}")
    assert st == 201, raw[:300]
    credential_id = body["credential_id"]
    st, body, raw = r.api(
        "POST", "/api/connectors/instances",
        {"connector_slug": "vmware", "credential_id": credential_id},
        bearer=worker_bearer)
    r.check("R3 POST /api/connectors/instances → 201 (vmware activo)",
            st == 201, f"HTTP {st} {raw[:150]}")
    assert st == 201, raw[:300]
    instance_id = body["id"]
    st, body, raw = r.api(
        "POST", f"/api/vault/credentials/{credential_id}/use", {},
        bearer=worker_bearer)
    r.check("R3 inyección runtime del conector → 200 (pre-condición)",
            st == 200, f"HTTP {st} {raw[:120]}")

    # ---------------------------------------------------------------- R4
    print("=== R4: enrolamiento real del dispositivo ===", flush=True)
    public_key, fingerprint = _generate_device_key()
    st, body, raw = r.api(
        "POST", "/api/devices/enroll",
        {"org_code": slug, "id_token": worker_id_token,
         "public_key": public_key, "fingerprint": fingerprint,
         "device_name": "revoc-live-inc4", "platform": "linux"},
        bearer=worker_bearer)
    r.check("R4 POST /api/devices/enroll → 202 (pending)", st == 202,
            f"HTTP {st} {raw[:150]}")
    assert st == 202, raw[:300]
    device_id = body["device_id"]
    enrollment_token = body["enrollment_token"]
    print(f"[live] device={device_id} fp={fingerprint[:16]}…")

    st, body, raw = r.api("POST", f"/api/devices/{device_id}/approve", {},
                          bearer=admin_bearer)
    r.check("R4 aprobación admin → 200 (active)", st == 200,
            f"HTTP {st} {raw[:150]}")
    st, body, raw = r.api(
        "POST", "/api/devices/enroll/exchange",
        {"enrollment_token": enrollment_token})
    r.check("R4 canje del enrollment token → 200 (device token vivo)",
            st == 200, f"HTTP {st} {raw[:150]}")
    assert st == 200, raw[:300]

    # ---------------------------------------------------------------- R5
    print("=== R5: REVOCACIÓN INTEGRAL (mismo request de siempre) ===",
          flush=True)
    st, body, raw = r.api("POST", f"/api/devices/{device_id}/revoke", {},
                          bearer=admin_bearer)
    r.check("R5 POST /api/devices/{id}/revoke → 200", st == 200,
            f"HTTP {st} {raw[:200]}")
    assert st == 200, raw[:300]
    sweep = body.get("sweep", {})
    r.check("R5 sweep.keys_blocked incluye la key del usuario",
            any(k.get("key_id") == key_id for k in sweep.get("keys_blocked", [])),
            json.dumps(sweep.get("keys_blocked", []))[:200])
    r.check("R5 sweep.connectors_disconnected incluye la instancia vmware",
            any(c.get("instance_id") == instance_id
                for c in sweep.get("connectors_disconnected", [])),
            json.dumps(sweep.get("connectors_disconnected", []))[:200])

    # ---------------------------------------------------------------- R6
    print("=== R6: el acceso muere de verdad (gateway real + vault) ===",
          flush=True)
    st, body, raw = r.litellm_chat(key_plain, MODEL, "Responde solo: OK", 16)
    r.check("R6 (a) invocación con la key RETENIDA → 401 del gateway real",
            st == 401, f"HTTP {st} {raw[:150]}")
    rows = db("SELECT status FROM api_keys WHERE id = %s", (key_id,))
    r.check("R6 (a) api_keys.status = blocked en la DB viva",
            rows and rows[0][0] == "blocked", str(rows))
    rows = db("SELECT status FROM connector_instances WHERE id = %s",
              (instance_id,))
    r.check("R6 (b) connector_instances.status = disconnected",
            rows and rows[0][0] == "disconnected", str(rows))
    worker2, worker2_id_token = login_acr2(slug, worker_email, r.WORKER_PASSWORD)
    st, body, raw = r.api(
        "POST", f"/api/vault/credentials/{credential_id}/use", {},
        bearer=worker2["session_token"])
    r.check("R6 (b) inyección con sesión fresca → 409 (sin instancia activa)",
            st == 409, f"HTTP {st} {raw[:150]}")

    # ---------------------------------------------------------------- R7
    print("=== R7: audit con resumen + cadena íntegra ===", flush=True)
    rows = db(
        "SELECT payload FROM audit_log WHERE tenant_id = %s "
        "AND action = 'device.revoked' AND device_id = %s",
        (tenant_id, device_id))
    r.check("R7 audit device.revoked presente", len(rows) == 1, str(len(rows)))
    payload = rows[0][0] if rows else {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    r.check("R7 device.revoked porta el resumen del sweep",
            payload.get("sweep", {}).get("keys_blocked")
            and payload["sweep"].get("connectors_disconnected"),
            json.dumps(payload.get("sweep", {}))[:250])
    rows = db(
        "SELECT payload->>'reason' FROM audit_log WHERE tenant_id = %s "
        "AND action = 'model.key_blocked' AND device_id = %s",
        (tenant_id, device_id))
    r.check("R7 audit model.key_blocked (reason device_revoked)",
            rows and rows[0][0] == "device_revoked", str(rows))
    rows = db(
        "SELECT payload->>'reason' FROM audit_log WHERE tenant_id = %s "
        "AND action = 'connector.disconnected' AND device_id = %s",
        (tenant_id, device_id))
    r.check("R7 audit connector.disconnected (reason device_revoked)",
            rows and rows[0][0] == "device_revoked", str(rows))
    rows = db(
        "SELECT count(*) FROM notifications WHERE tenant_id = %s "
        "AND type = 'connector_disconnected' "
        "AND payload->>'reason' = 'device_revoked'",
        (tenant_id,))
    r.check("R7 notificación de inbox al dueño del conector",
            rows and rows[0][0] >= 1, str(rows))
    rows = db("SELECT status FROM audit_verify_chain(%s)", (tenant_id,))
    r.check("R7 cadena de audit íntegra (audit_verify_chain → ok)",
            rows and rows[0][0] == "ok", str(rows))

    # ---------------------------------------------------------------- R8
    print("=== R8: re-enrolamiento por fingerprint (spec 007 upsert) ===",
          flush=True)
    public_key2, _ = _generate_device_key()
    st, body, raw = r.api(
        "POST", "/api/devices/enroll",
        {"org_code": slug, "id_token": worker2_id_token,
         "public_key": public_key2, "fingerprint": fingerprint,
         "device_name": "revoc-live-inc4", "platform": "linux"},
        bearer=worker2["session_token"])
    r.check("R8 re-enroll con el MISMO fingerprint → 202", st == 202,
            f"HTTP {st} {raw[:150]}")
    assert st == 202, raw[:300]
    r.check("R8 upsert: MISMO device_id (sin fila nueva)",
            body["device_id"] == device_id, str(body["device_id"]))
    re_enrollment_token = body["enrollment_token"]
    st, body, raw = r.api("POST", f"/api/devices/{device_id}/approve", {},
                          bearer=admin_bearer)
    r.check("R8 segunda aprobación → 200 (active de nuevo)", st == 200,
            f"HTTP {st} {raw[:150]}")
    st, body, raw = r.api(
        "POST", "/api/devices/enroll/exchange",
        {"enrollment_token": re_enrollment_token})
    r.check("R8 canje del nuevo enrollment token → 200 (device token)",
            st == 200, f"HTTP {st} {raw[:150]}")
    st, body, raw = r.api(
        "POST", f"/api/devices/{device_id}/token", {},
        bearer=worker2["session_token"])
    r.check("R8 renovación del device token (sesión+MFA+owner) → 200",
            st == 200, f"HTTP {st} {raw[:150]}")

    print(f"[live] DONE — passes={r.PASSES} failures={r.FAILURES}", flush=True)
    return 1 if r.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

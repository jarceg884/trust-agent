#!/usr/bin/env python3
"""EVIDENCIA LIVE — spec 019 Incremento 3: attestation del agente (t_f9156ccd).

Renueva el device token del perfil Hermes REAL enrolado (`trust-live-inc1`,
dispositivo `fp-live-inc1` del tenant `litellm-real4` en la DB viva) para que
la siguiente sesión real del agente pueda atestiguar contra el control plane
vivo (127.0.0.1:8000):

  1. DB viva: localiza el dispositivo (fingerprint) → device_id/user/tenant.
  2. Keycloak real (realm del tenant): resetea el credential OTP del usuario
     (el baile re-enrola TOTP en el siguiente login y captura el secreto —
     mismo mecanismo del script de re-validación LiteLLM real).
  3. Login REAL PKCE S256 + TOTP (acr=2) → id_token.
  4. POST /api/auth/session → sesión con MFA acreditada.
  5. POST /api/devices/{id}/token (sesión+MFA+owner) → device token fresco
     (TTL 15 min — NUNCA se imprime; se escribe directo al almacén canónico
     del perfil: $HERMES_HOME/trust-agent/device-token.json).
  6. Sincroniza la copia del plugin en el perfil con el árbol fuente
     (attestation.py + __init__.py + state.py) para que la sesión corra el
     código del Incremento 3.

Después: `hermes -p trust-live-inc1 -z "<prompt>"` → el plugin envía la
attestation → fila `device.attestation` en audit_log del tenant (verificable
por SQL).

Uso:
  platform/.venv/bin/python scripts/evidence_attestation_live.py \
      --fingerprint fp-live-inc1 --profile trust-live-inc1
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import revalidate_litellm_real as r  # baile PKCE/TOTP probado contra KC26

DB_DSN = r.DB_DSN


def pkce_login_acr2(username: str, password: str, *, totp_state: dict):
    """Baile PKCE S256 pidiendo acr_values=2 (flujo LoA de spec 015 D-AUTH-06:
    sin ese parámetro el realm entrega password-only con acr=1; con él aparece
    el formulario OTP — con enrolamiento inline si el usuario no tiene
    credential TOTP). Reutiliza los helpers probados de revalidate_litellm_real.
    """
    import base64
    import hashlib
    import secrets
    import time
    import urllib.parse

    for k in list(r.COOKIES):
        r.COOKIES.pop(k)
    verifier = r._b64url(secrets.token_bytes(64))
    challenge = r._b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": r.CLIENT_ID, "scope": "openid",
        "redirect_uri": r.REDIRECT_URI, "state": "st-attestation-live",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "acr_values": "2",
    }
    st, body, hdrs = r.kc_fetch(
        f"/realms/{r.SLUG}/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}")
    assert st == 200 and 'action="' in body, f"authorize HTTP {st}"
    action = r.form_action(body)
    st, body, hdrs = r.kc_fetch(
        action.split(f"{r.KC_HOST}:{r.KC_PORT}")[-1], method="POST",
        data=urllib.parse.urlencode({
            "username": username, "password": password, "credentialId": ""}).encode())
    loc = hdrs.get("location", "")
    st, body, loc, hdrs = r._hop(st, body, loc, hdrs)
    used_counter = None
    for _ in range(8):
        if "code=" in loc:
            break
        if not (st == 200 and 'action="' in body):
            break
        act = r.form_action(body)
        if 'name="totpSecret"' in body:
            secret = r.totp_secret_of(body)
            totp_state["secret"] = secret
            used_counter = int(time.time()) // 30
            code = r.totp(secret, counter=used_counter)
            print(f"    [dance] enrolamiento TOTP real: code={code}", flush=True)
            st, body, hdrs = r.kc_fetch(
                act.split(f"{r.KC_HOST}:{r.KC_PORT}")[-1], method="POST",
                data=urllib.parse.urlencode({
                    "totp": code, "totpSecret": secret, "mode": "totp",
                    "userLabel": "attestation-live", "credentialId": ""}).encode())
        elif 'name="otp"' in body or 'name="totp"' in body:
            secret = totp_state.get("secret", "")
            counter = int(time.time()) // 30
            if counter == used_counter:
                counter += 1  # el código de enrolamiento no es reutilizable
            fields = r.form_fields(body)
            code_field = "otp" if 'name="otp"' in body else "totp"
            fields[code_field] = r.totp(secret, counter=counter)
            print(f"    [dance] 2º factor OTP real: {fields[code_field]}", flush=True)
            st, body, hdrs = r.kc_fetch(
                act.split(f"{r.KC_HOST}:{r.KC_PORT}")[-1], method="POST",
                data=urllib.parse.urlencode(fields).encode())
        else:
            st, body, hdrs = r.kc_fetch(
                act.split(f"{r.KC_HOST}:{r.KC_PORT}")[-1], method="POST", data=b"")
        loc = hdrs.get("location", "")
        st, body, loc, hdrs = r._hop(st, body, loc, hdrs)
    if "code=" not in loc:
        return None, None
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    st, raw, _ = r.kc_fetch(
        f"/realms/{r.SLUG}/protocol/openid-connect/token", method="POST",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": r.CLIENT_ID,
            "redirect_uri": r.REDIRECT_URI, "code": code,
            "code_verifier": verifier}).encode())
    assert st == 200, raw
    tokens = json.loads(raw)
    claims = json.loads(base64.urlsafe_b64decode(
        tokens["id_token"].split(".")[1] + "==").decode())
    return tokens["id_token"], claims.get("acr")


PLUGIN_SRC = Path("/root/trust-agent/platform/hermes/plugins/"
                  "trust-agent-governance")
PLUGIN_FILES = ("attestation.py", "__init__.py", "state.py",
                "bundle_client.py", "trust_agent_policy_pubkey.pem")


def db(sql, params=None, fetch=True):
    import psycopg

    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if fetch else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fingerprint", default="fp-live-inc1")
    ap.add_argument("--profile", default="trust-live-inc1")
    args = ap.parse_args()

    # 1. Dispositivo en la DB viva
    rows = db(
        "SELECT d.id, u.email, t.slug FROM devices d "
        "JOIN users u ON u.id = d.user_id JOIN tenants t ON t.id = u.tenant_id "
        "WHERE d.fingerprint = %s", (args.fingerprint,))
    assert rows, f"device {args.fingerprint} not found in live DB"
    device_id, email, slug = str(rows[0][0]), rows[0][1], rows[0][2]
    print(f"[live] device={device_id} user={email} tenant={slug}")

    # 2. Reset del credential OTP del usuario en el realm real (el login
    #    re-enrola TOTP y captura el secreto nuevo).
    r.SLUG = slug
    token = r.kc_admin_token()
    st, raw = r.kc_json(
        "GET",
        f"/admin/realms/{slug}/users?username={r.urllib.parse.quote(email)}&exact=true",
        token=token)
    assert st == 200, f"KC user lookup HTTP {st}"
    kc_user = json.loads(raw)[0]
    st, raw = r.kc_json(
        "GET", f"/admin/realms/{slug}/users/{kc_user['id']}/credentials",
        token=token)
    creds = json.loads(raw) if st == 200 else []
    otp_ids = [c["id"] for c in creds if c.get("type") == "otp"]
    for cid in otp_ids:
        st, raw = r.kc_json(
            "DELETE", f"/admin/realms/{slug}/users/{kc_user['id']}/credentials/{cid}",
            token=token)
        print(f"[live] OTP credential reset (HTTP {st})")
    # Sin credential OTP el realm ya no exige 2FA (flujo condicional): forzar
    # el re-enrolamiento TOTP en el siguiente login (acr=2).
    st, raw = r.kc_json(
        "PUT", f"/admin/realms/{slug}/users/{kc_user['id']}",
        {"requiredActions": ["CONFIGURE_TOTP"], "enabled": True}, token=token)
    print(f"[live] requiredAction CONFIGURE_TOTP set (HTTP {st})")
    assert r.WORKER_PASSWORD, "worker password constant missing"

    # 3. Login REAL PKCE S256 + TOTP pidiendo acr_values=2 (acr=2)
    r.COOKIES.clear()
    totp_state = {}
    id_token, acr = pkce_login_acr2(email, r.WORKER_PASSWORD, totp_state=totp_state)
    assert id_token is not None, "PKCE login failed"
    assert str(acr) == "2", f"expected acr=2 (MFA), got {acr}"
    print("[live] PKCE+TOTP login OK (acr=2)")

    # 4. Sesión con MFA acreditada
    session = r.mint(id_token)
    bearer = session["session_token"]
    print("[live] session minted (mfa_verified=%s)" % session.get("mfa_verified"))

    # 5. Renovación del device token (sesión+MFA+owner) — TTL 15 min
    st, body, raw = r.api("POST", f"/api/devices/{device_id}/token",
                          bearer=bearer)
    assert st == 200, f"token renew HTTP {st}: {raw[:200]}"
    device_token = body["device_token"]

    # 6. Almacén canónico del perfil + sincronización del plugin
    home = Path("/root/.hermes/profiles") / args.profile
    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": device_id, "token": device_token}),
        encoding="utf-8")
    print(f"[live] device-token.json written (expires_in={body['expires_in']}s)")
    plugin_dst = home / "plugins" / "trust-agent-governance"
    for name in PLUGIN_FILES:
        src = PLUGIN_SRC / name
        if src.exists():
            shutil.copy2(src, plugin_dst / name)
            print(f"[live] plugin synced: {name}")
    # pycache viejo fuera: la sesión debe cargar el código NUEVO
    pycache = plugin_dst / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
        print("[live] plugin __pycache__ cleared")

    print("[live] READY — run: hermes -p %s -z \"<prompt>\" (within 15 min)"
          % args.profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())

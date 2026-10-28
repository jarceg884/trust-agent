#!/usr/bin/env python3
"""Workaround documentado (NO un fix del producto): cura el realm de
evidencia `custom-conn` del bug de t_1844fb06 (ver reporte en su card).

El bug: `_ensure_mfa_acr_flow` (keycloak_admin.py 2b) deja el realm sin
`auth-username-password-form` porque el loop DELETE final barre el listing
PLANO de `forms` y borra también la copia nueva dentro de "Password LoA".

Esta cura replica la INTENCIÓN del código del producto con las mismas
operaciones de KC Admin REST: agrega el formulario username/password dentro
del subflow "Password LoA" (requirement REQUIRED, priority 20). Es
idempotente. Cuando t_1844fb06 corrija keycloak_admin.py y se rebuild la
imagen, los realms nuevos saldrán sanos y este script será innecesario.

Uso: .venv/bin/python heal_realm_flow.py [slug]
"""
import json
import sys
import urllib.parse

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # noqa: E402

SLUG = sys.argv[1] if len(sys.argv) > 1 else "custom-conn"
FORMS_ALIAS = f"{e2e.SLUG if False else ''}trust-browser-loa forms"


def main():
    token = e2e.kc_admin_token()
    flat_path = (f"/admin/realms/{SLUG}/authentication/flows/"
                 "trust-browser-loa/executions")
    st, raw = e2e.kc_json("GET", flat_path, token=token)
    assert st == 200, f"flow listing HTTP {st}"
    flat = json.loads(raw)

    pwd = next((x for x in flat
                if "Password LoA" in (x.get("displayName") or "")), None)
    assert pwd, "subflow 'Password LoA' no encontrado — ¿realm viejo?"
    pwd_id = pwd["flowId"]

    alias = urllib.parse.quote("trust-browser-loa forms")
    st, raw = e2e.kc_json(
        "GET", f"/admin/realms/{SLUG}/authentication/flows/{alias}/executions",
        token=token)
    assert st == 200, f"forms listing HTTP {st}"
    forms_flat = json.loads(raw)
    already = any(x.get("providerId") == "auth-username-password-form"
                  and x.get("level") == 1
                  and "Password LoA" in str(x)
                  for x in forms_flat)
    # Chequeo robusto: ¿existe el formulario en algún punto del árbol?
    already = any(x.get("providerId") == "auth-username-password-form"
                  for x in forms_flat)
    if already:
        print(f"[ok] {SLUG}: el formulario username/password ya existe — "
              "nada que hacer")
        return 0

    st, raw = e2e.kc_json(
        "POST", f"/admin/realms/{SLUG}/authentication/executions?flow={pwd_id}",
        payload={"authenticator": "auth-username-password-form",
                 "requirement": "REQUIRED", "priority": 20,
                 "parentFlow": pwd_id}, token=token)
    assert st in (201, 204), f"add execution HTTP {st}: {raw[:300]}"
    print(f"[ok] {SLUG}: auth-username-password-form agregado dentro de "
          f"'Password LoA' (flow={pwd_id[:8]}…)")

    # Verificación: el authorize GET debe devolver 200 con formulario.
    import hashlib
    import secrets

    def b64u(b):
        import base64
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    ver = b64u(secrets.token_bytes(64))
    ch = b64u(hashlib.sha256(ver.encode()).digest())
    params = {"response_type": "code", "client_id": e2e.CLIENT_ID,
              "scope": "openid",
              "redirect_uri": "http://127.0.0.1:3100/auth/callback",
              "state": "heal", "code_challenge": ch,
              "code_challenge_method": "S256"}
    st, body, _ = e2e.kc_fetch(
        f"/realms/{SLUG}/protocol/openid-connect/auth?"
        f"{urllib.parse.urlencode(params)}")
    ok = st == 200 and 'action="' in body
    print(f"[{'PASS' if ok else 'FAIL'}] authorize GET → {st} con formulario")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

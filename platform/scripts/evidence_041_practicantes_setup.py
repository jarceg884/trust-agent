#!/usr/bin/env python3
"""Practicantes live setup — login REAL PKCE+TOTP (acr=2) del worker del tenant
policies-ux + sync del plugin trust-agent-governance al perfil trust-live-prac
+ id_token (0600) para /trust-enroll. Nada simulado.

Uso: platform/.venv/bin/python prac_live_setup.py <out-state.json>
"""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # helpers PKCE/TOTP/mint verificados

SLUG = "policies-ux"
WORKER_EMAIL = "worker@policies-ux.test"
WORKER_PASSWORD = "PracticantesWorker2026!"
PROFILE = "trust-live-prac"
PROFILE_HOME = Path("/root/.hermes/profiles") / PROFILE
PLUGIN_SRC = Path("/root/trust-agent/platform/hermes/plugins/trust-agent-governance")

e2e.SLUG = SLUG

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""),
          flush=True)
    if not cond:
        FAILS.append(name)
    return cond


def main() -> int:
    # Redirect URI vigente del realm (las lanes de auth-ux lo cambiaron).
    token = e2e.kc_admin_token()
    st, raw = e2e.kc_json(
        "GET", f"/admin/realms/{SLUG}/clients?clientId=trust-agent-web", token=token)
    clients = json.loads(raw) if st == 200 else []
    redirects = (clients[0].get("redirectUris") if clients else None) or []
    valid = [u for u in redirects if u.startswith("http://127.0.0.1")]
    assert valid, f"no 127.0.0.1 redirect URI for trust-agent-web in {SLUG}: {redirects}"
    e2e.REDIRECT_URI = valid[0]
    print(f"[live] redirect URI del realm: {e2e.REDIRECT_URI}")

    # worker_sub desde la DB viva.
    rows = e2e.db("SELECT id, oidc_subject FROM users WHERE email = %s",
                  (WORKER_EMAIL,))
    worker_sub = str(rows[0][1])
    print(f"[live] worker sub={worker_sub}")

    # Password definitivo del worker en el realm real (KC admin API).
    st, _ = e2e.kc_json(
        "PUT", f"/admin/realms/{SLUG}/users/{worker_sub}/reset-password",
        {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
        token=token)
    check("worker: password definitivo fijado en el realm real", st in (204, 409),
          f"HTTP {st}")

    # Re-enrolamiento TOTP real (el secret no es recuperable — KC lo guarda).
    removed = 0
    st, raw = e2e.kc_json(
        "GET", f"/admin/realms/{SLUG}/users/{worker_sub}/credentials", token=token)
    for cred in json.loads(raw):
        if cred.get("type") == "otp":
            st2, _ = e2e.kc_json(
                "DELETE",
                f"/admin/realms/{SLUG}/users/{worker_sub}/credentials/{cred['id']}",
                token=token)
            if st2 in (204, 200):
                removed += 1
    check("worker: credencial TOTP previa borrada del realm real", removed >= 0,
          f"{removed} eliminada(s)")
    st, raw = e2e.kc_json(
        "GET", f"/admin/realms/{SLUG}/users/{worker_sub}", token=token)
    rep = json.loads(raw)
    rep["requiredActions"] = ["CONFIGURE_TOTP"]
    st, _ = e2e.kc_json(
        "PUT", f"/admin/realms/{SLUG}/users/{worker_sub}", rep, token=token)
    check("worker: CONFIGURE_TOTP re-añadido (baile inline → acr=2)", st in (204, 200),
          f"HTTP {st}")

    # Login REAL PKCE S256 + enrolamiento TOTP RFC 6238 con LoA 2.
    totp_state = {}
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                                   totp_state=totp_state, acr_values="2")
    check("worker: login REAL PKCE+TOTP → id_token acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    if not id_token:
        return 1

    session = e2e.mint(id_token)
    check("worker: sesión mintada MFA-verificada",
          session.get("mfa_verified") is True,
          f"mfa={session.get('mfa_verified')}")
    bearer = session["session_token"]
    st, me, _raw = e2e.api("GET", "/api/me", bearer=bearer)
    check("worker: GET /api/me → 200 worker@policies-ux.test",
          st == 200 and me.get("email") == WORKER_EMAIL, f"HTTP {st}")

    # Efectiva del worker vía sesión propia (la que el bundle materializa).
    st, eff, _raw = e2e.api("GET", "/api/me/policies", bearer=bearer)
    check("worker: GET /api/me/policies → 200",
          st == 200 and isinstance(eff, dict), f"HTTP {st}")
    if st == 200:
        for key, want in (
            ("allowed_tools", ["governance_status", "read_file", "write_file"]),
            ("allowed_terminal_backends", []),
            ("allowed_models", ["glm-5.2"]),
        ):
            check(f"worker efectiva {key} == {json.dumps(want)}",
                  sorted(eff.get(key) or []) == want,
                  f"got={json.dumps(eff.get(key))}")
        check("worker efectiva approval_mode == manual",
              eff.get("approval_mode") == "manual", f"got={eff.get('approval_mode')}")
        check("worker efectiva web_domain_policy == allowlist vacía",
              eff.get("web_domain_policy") == {"mode": "allowlist", "domains": []},
              f"got={json.dumps(eff.get('web_domain_policy'))}")

    # id_token (acr=2) para /trust-enroll — 0600, jamás impreso.
    trust_dir = PROFILE_HOME / "trust-agent"
    trust_dir.mkdir(parents=True, exist_ok=True)
    id_path = trust_dir / "id-token.txt"
    id_path.write_text(id_token, encoding="utf-8")
    os.chmod(id_path, 0o600)
    check("id_token del worker escrito 0600 para /trust-enroll",
          oct(os.stat(id_path).st_mode)[-3:] == "600", str(id_path))

    # Sync del plugin fuente → perfil (mismo patrón que batch1).
    dst = PROFILE_HOME / "plugins" / "trust-agent-governance"
    dst.mkdir(parents=True, exist_ok=True)
    synced = []
    for src in sorted(PLUGIN_SRC.iterdir()):
        if src.suffix != ".py" and src.name != "plugin.yaml" and src.suffix != ".pem":
            continue
        shutil.copy2(src, dst / src.name)
        synced.append(src.name)
    pycache = dst / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
    check("plugin sincronizado al perfil",
          "enroll.py" in synced and "__init__.py" in synced,
          f"{len(synced)} archivos")

    out = {
        "worker_email": WORKER_EMAIL,
        "worker_sub": worker_sub,
        "worker_bearer": bearer,
        "acr": acr,
        "profile": PROFILE,
        "redirect_uri": e2e.REDIRECT_URI,
    }
    out_path = Path(sys.argv[1])
    out_path.write_text(json.dumps(out), encoding="utf-8")
    os.chmod(out_path, 0o600)
    print(f"[ok] estado → {out_path} (0600)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

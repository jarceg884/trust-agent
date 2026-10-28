#!/usr/bin/env python3
"""Sesión member (rol user, SIN org_admin) para el gate de /hermes-download.

Usuario real invited-probe@final-e2e.test (rol "user"), invitado por el admin
del tenant final-e2e vía el job asíncrono D-ORG-02 — la one-time password se
lee del jobs.result del tenant (mecanismo de entrega documentado). Primer
login real: UPDATE_PASSWORD + enrolamiento TOTP RFC 6238 → id_token acr=2 →
mint de sesión kind=admin. El principal NO tiene org_admin: sirve para
verificar el gate de la sección Hermes Download (AC-AC-033.2).
"""
import http.client
import json
import sys

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # noqa: E402

SLUG = "final-e2e"
EMAIL = "invited-probe@final-e2e.test"
NEW_PASSWORD = "InvitedProbe2026!pass"
JOB_ID = "00adac65-44cc-4323-a8d2-dbb9b0a5b421"
OUT = "/root/.hermes/kanban/boards/trust-agent/workspaces/t_360a752e/session_member.json"

e2e.SLUG = SLUG
e2e.REDIRECT_URI = "http://127.0.0.1:3100/auth/callback"


def mint_session(id_token: str) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=20)
    conn.request("POST", "/api/auth/session",
                 body=json.dumps({"id_token": id_token, "kind": "admin"}).encode(),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    set_cookies = resp.msg.get_all("Set-Cookie") or []
    conn.close()
    assert resp.status == 201, f"mint HTTP {resp.status}: {raw[:300]}"
    session_cookie = None
    for c in set_cookies:
        if c.startswith("trust_session="):
            session_cookie = c.split(";", 1)[0].partition("=")[2]
    body = json.loads(raw)
    assert session_cookie, f"sin trust_session: {set_cookies}"
    return {"trust_session": session_cookie, "csrf_token": body["csrf_token"],
            "refresh_token": body["refresh_token"]}


def main() -> int:
    # One-time password del invite (jobs.result — entrega al admin, D-ORG-02).
    rows = e2e.db("SELECT result::text FROM jobs WHERE id = %s", (JOB_ID,))
    ota = json.loads(rows[0][0])["one_time_password"]
    print(f"[invite] one-time password recuperada del job ({len(ota)} chars)")

    totp_state = {}
    id_token, acr = e2e.pkce_login(EMAIL, ota, new_password=NEW_PASSWORD,
                                   totp_state=totp_state)
    assert id_token, "login PKCE del member falló"
    print(f"[login] member id_token obtenido, acr={acr}")
    session = mint_session(id_token)
    print("[session] minted")

    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=20)
    conn.request("GET", "/api/me", headers={
        "Cookie": f"trust_session={session['trust_session']}"})
    resp = conn.getresponse()
    me = json.loads(resp.read().decode())
    conn.close()
    roles = [r["role_key"] for r in me["roles"]]
    print(f"[whoami] {me['email']} roles={roles} tenant_slug={me.get('tenant_slug')}")
    assert resp.status == 200
    assert "org_admin" not in roles, "el member NO debe ser org_admin"

    with open(OUT, "w") as fh:
        json.dump({"slug": SLUG, "email": EMAIL, "roles": roles,
                   "totp_secret": totp_state.get("secret"), **session}, fh, indent=2)
    print(f"[ok] sesión member guardada en {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

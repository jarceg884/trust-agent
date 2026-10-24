#!/usr/bin/env python3
"""Prueba e2e: authorization-code + PKCE (S256) contra Keycloak local + verificacion JWKS.

Keycloak 26.2 en http://127.0.0.1:8080, realm trust-agent-test (creado antes via Admin REST).
Cliente publico trust-agent-web, usuario demo / DemoPass2026!.
"""
import base64
import hashlib
import json
import re
import secrets
import sys
import urllib.parse
import urllib.request
import http.client

KC = "127.0.0.1:8080"
REALM = "trust-agent-test"
CLIENT_ID = "trust-agent-web"
REDIRECT_URI = "http://127.0.0.1:9090/auth/callback"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def http_post(path, data, headers=None):
    conn = http.client.HTTPConnection(KC, timeout=10)
    conn.request("POST", path, body=urllib.parse.urlencode(data),
                 headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    resp = conn.getresponse()
    return resp.status, resp.read().decode(), dict(resp.getheaders())


def http_get(path, headers=None):
    conn = http.client.HTTPConnection(KC, timeout=10)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    # lista completa de (name, value): varios Set-Cookie no se colapsan
    return resp.status, resp.read().decode(), resp.getheaders()


def get_code(scope: str):
    verifier = b64url(secrets.token_bytes(64))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": scope,
        "redirect_uri": REDIRECT_URI, "state": "st-" + b64url(secrets.token_bytes(8)),
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    auth_path = f"/realms/{REALM}/protocol/openid-connect/auth?" + urllib.parse.urlencode(params)
    status, body, headers = http_get(auth_path)
    assert status == 200, f"authorize HTTP {status}"
    m = re.search(r'action="([^"]+)"', body)
    assert m, "no login form action found"
    action = m.group(1).replace("&amp;", "&")
    # cookies de sesion del authorize GET (AUTH_SESSION_ID / KC_RESTART)
    cookies = "; ".join(v.split(";")[0] for k, v in headers if k.lower() == "set-cookie")
    conn = http.client.HTTPConnection(KC, timeout=10)
    path = action.split("127.0.0.1:8080", 1)[1] if "127.0.0.1:8080" in action else action
    conn.request("POST", path,
                 body=urllib.parse.urlencode({"username": "demo", "password": "DemoPass2026!", "credentialId": ""}),
                 headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookies})
    resp = conn.getresponse()
    location = resp.getheader("Location") or ""
    assert "code=" in location, f"no code in redirect: {location[:120]!r} status={resp.status}"
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
    return code, verifier


def main():
    # 1) flujo CON scope=openid -> debe incluir id_token
    code, verifier = get_code("openid")
    status, body, _ = http_post(
        f"/realms/{REALM}/protocol/openid-connect/token",
        {"grant_type": "authorization_code", "client_id": CLIENT_ID,
         "redirect_uri": REDIRECT_URI, "code": code, "code_verifier": verifier})
    payload = json.loads(body)
    print(f"[1] exchange scope=openid: HTTP {status}, id_token len={len(payload.get('id_token',''))}, "
          f"access len={len(payload.get('access_token',''))}, refresh={bool(payload.get('refresh_token'))}")
    id_token = payload["id_token"]

    # 2) verificacion JWKS con PyJWT (iss/aud pinned, required claims)
    import jwt
    from jwt import PyJWKClient
    jwks_client = PyJWKClient(f"http://{KC}/realms/{REALM}/protocol/openid-connect/certs",
                              cache_keys=True, lifespan=300)
    key = jwks_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(id_token, key.key, algorithms=["RS256"], audience=CLIENT_ID,
                        issuer=f"http://{KC}/realms/{REALM}",
                        options={"require": ["exp", "iat", "aud", "iss", "sub"]})
    print(f"[2] JWT verificado: sub={claims['sub'][:8]}... iss={claims['iss']} "
          f"aud={claims['aud']} email={claims.get('email')} exp_en={claims['exp'] - claims['iat']}s")

    # 3) caso negativo: code_verifier incorrecto -> invalid_grant
    code2, _ = get_code("openid")
    status, body, _ = http_post(
        f"/realms/{REALM}/protocol/openid-connect/token",
        {"grant_type": "authorization_code", "client_id": CLIENT_ID,
         "redirect_uri": REDIRECT_URI, "code": code2, "code_verifier": "verifier-equivocado-123"})
    err = json.loads(body)
    print(f"[3] verifier incorrecto: HTTP {status}, error={err.get('error')}, desc={err.get('error_description','')[:60]}")

    # 4) refresh token grant
    refresh = payload["refresh_token"]
    status, body, _ = http_post(
        f"/realms/{REALM}/protocol/openid-connect/token",
        {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": refresh})
    r = json.loads(body)
    print(f"[4] refresh_token: HTTP {status}, nuevo id_token={bool(r.get('id_token'))}, "
          f"nuevo refresh={bool(r.get('refresh_token'))}")

    # 5) introspeccion con el client (publico: solo con client_id no funciona; usar userinfo)
    access = payload["access_token"]
    status, body, _ = http_get(f"/realms/{REALM}/protocol/openid-connect/userinfo",
                               {"Authorization": f"Bearer {access}"})
    ui = json.loads(body)
    print(f"[5] userinfo con access_token: HTTP {status}, sub={ui.get('sub','')[:8]}..., email={ui.get('email')}")

    print("OK: flujo completo probado")


if __name__ == "__main__":
    sys.exit(main())

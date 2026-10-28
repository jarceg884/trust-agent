#!/usr/bin/env python3
"""Probe EN VIVO (investigación desechable) — spec 015 T2 / decisión D-AUTH-06.

Congela (o corrige) la semántica del flujo browser LoA rediseñado contra el
Keycloak 26.2 REAL del stack (127.0.0.1:8080, contenedor trust-agent-idp):

  realm attribute  acr.loa.map = {"1":1,"2":2}
  copia de browser con:
    forms
      ├── subflow "Password LoA"    (condición LoA nivel 1 + Username Password Form)
      └── subflow "…Conditional OTP" (condición LoA nivel 2, SIN user-configured)

Escenarios (todos con baile PKCE S256 real, cookies manuales — patrón
smoke_console.py — y TOTP RFC6238 con clave = bytes UTF-8 raw del secret):

  S1  login fresco acr_values=1 SIN TOTP   → ¿code con acr "1" sin formulario OTP?
  S2  login fresco acr_values=2 SIN TOTP   → ¿deriva inline a CONFIGURE_TOTP?
  S3  completar enrolamiento TOTP real     → ¿code con acr "2"?
  S4  login acr_values=2 CON TOTP          → ¿OTP form → code acr "2"?
  S5  login acr_values=1 CON TOTP          → ¿password-only → code acr "1"?
  S6  re-login SSO (cookie, sesión LoA 1): authorize acr=1 / acr=2 /
      acr=2+prompt=login → comportamiento del step-up sobre sesión sin MFA.
  S7  authorize SIN acr_values             → ¿qué acr emite? (impacto e2e)
  S8  re-login SSO sobre sesión LoA 2 con acr=2 → ¿step-up otorgado por maxAge?
  G1  IdP google con credenciales placeholder DECLARADAS + kc_idp_hint=google
      → ¿302 a accounts.google.com? (mecánica de brokering, sin credenciales
      reales del operador — AC-AUTH-006 queda como gap documentado).
  B1  IdP "double-oidc" contra un realm KC local DECLARADO como doble
      (probe-idp-double) → primer login brokered completo: auto-creación del
      usuario ("Create User If Unique"), claims del token y acr resultante.
      NUNCA presentado como Google: es la mecánica, no el proveedor.

Uso:
  .venv/bin/python scripts/probe_loa_flow.py [--keep] [--skip-broker]

El realm desechable probe-loa (y probe-idp-double) se borran al inicio y al
final (éxito). Bootstrap admin-cli con el credencial de dev del repo
(ADR-015, autorizado por el orquestador en el card t_1844fb06).
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import json
import re
import secrets
import struct
import sys
import time
import urllib.parse

KC_HOST, KC_PORT = "127.0.0.1", 8080
REALM = "probe-loa"
DOUBLE_REALM = "probe-idp-double"
FLOW = "probe-browser-loa"
PASSWORD_SUBFLOW = "Password LoA"
CLIENT_ID = "probe-web"
# Callback del helper trust-login del bundle de descarga (spec 023 D-023-13):
# el flujo real de desktop. Retirado el demo :9090 (t_b8769ecb).
REDIRECT_URI = "http://127.0.0.1:8765/callback"
EMAIL = "probe@probe.test"
PASSWORD = "ProbePass2026!"
DOUBLE_EMAIL = "double@probe.test"
DOUBLE_PASSWORD = "DoublePass2026!"
BROKER_CLIENT_ID = "probe-broker"
BROKER_CLIENT_SECRET = "probe-broker-secret-2026"
KC_ADMIN_USER = "admin"
KC_ADMIN_PASSWORD = "KeycloakDemo2026"  # credencial de dev del repo (ADR-015)
ACR_MAP = '{"1":1,"2":2}'

FINDINGS: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


def finding(key: str, value: str) -> None:
    line = f"{key}: {value}"
    FINDINGS.append(line)
    log(f"\n*** FINDING {line}\n")


def die(msg: str) -> None:
    log(f"\n!!! FATAL: {msg}")
    log("\n== FINDINGS ACUMULADOS ==")
    for f in FINDINGS:
        log(f"  - {f}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Admin REST (bootstrap admin-cli, ADR-015 — patrón de la suite e2e)
# ---------------------------------------------------------------------------
_ADMIN_TOKEN = None


def admin_token() -> str:
    global _ADMIN_TOKEN
    if _ADMIN_TOKEN is None:
        conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
        conn.request(
            "POST", "/realms/master/protocol/openid-connect/token",
            body=urllib.parse.urlencode({
                "grant_type": "password", "client_id": "admin-cli",
                "username": KC_ADMIN_USER, "password": KC_ADMIN_PASSWORD,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        raw = resp.read().decode()
        conn.close()
        if resp.status != 200:
            die(f"admin-cli bootstrap HTTP {resp.status}: {raw[:200]}")
        _ADMIN_TOKEN = json.loads(raw)["access_token"]
    return _ADMIN_TOKEN


def admin(method: str, path: str, payload=None) -> tuple[int, str]:
    def once() -> tuple[int, str]:
        conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=20)
        body = json.dumps(payload) if payload is not None else None
        conn.request(method, path, body=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token()}",
        })
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        conn.close()
        return resp.status, raw

    status, raw = once()
    if status == 401:  # token expirado a mitad del probe → re-bootstrap una vez
        global _ADMIN_TOKEN
        _ADMIN_TOKEN = None
        status, raw = once()
    return status, raw


def admin_ok(method: str, path: str, payload=None, allow=()) -> str:
    status, raw = admin(method, path, payload)
    if status not in (200, 201, 204, 409, *allow):
        die(f"{method} {path} -> HTTP {status}: {raw[:300]}")
    return raw


# ---------------------------------------------------------------------------
# Navegador headless con cookie jar MANUAL (las cookies de KC llegan con flag
# Secure y un jar estándar se niega a reenviarlas sobre http:// — ver
# smoke_console.py; un navegador real SÍ las acepta en 127.0.0.1).
# ---------------------------------------------------------------------------
class Browser:
    """Cookie jar MANUAL keyed por host: dos realms KC en el mismo puerto
    (127.0.0.1:8080 y localhost:8080) comparten nombre de cookie
    AUTH_SESSION_ID — un jar plano pierde la sesión del realm real al
    loguearse contra el doble (hallazgo B1: "Restart login cookie not
    found"). Un navegador real no sufre esto con Google porque
    accounts.google.com es OTRO host; el doble local debe vivir en
    localhost para reproducir esa separación.
    """

    def __init__(self) -> None:
        self.cookies: dict[str, dict[str, str]] = {}

    def _jar(self, host: str) -> dict[str, str]:
        return self.cookies.setdefault(host, {})

    def fetch(self, path: str, *, method="GET", data=None,
              host=KC_HOST, port=KC_PORT) -> tuple[int, str, dict]:
        # Acepta paths absolutos (http://host:port/...) — el host manda.
        if path.startswith("http://"):
            rest = path[len("http://"):]
            host, _, rest = rest.partition("/")
            host, _, port_s = host.partition(":")
            port = int(port_s or 80)
            path = "/" + rest
        headers = {}
        jar = self._jar(f"{host}:{port}")
        if jar:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn = http.client.HTTPConnection(host, port, timeout=20)
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        for raw_cookie in resp.msg.get_all("Set-Cookie") or []:
            name, _, rest = raw_cookie.partition("=")
            value = rest.split(";")[0]
            if value:
                jar[name.strip()] = value
            else:
                jar.pop(name.strip(), None)
        status = resp.status
        conn.close()
        return status, body, hdrs


def path_of(url: str) -> str:
    """URL absoluta → la devuelve tal cual (fetch la parsea); relativa de KC
    → path para http.client."""
    if url.startswith("http://"):
        return url
    return url.split(f"{KC_HOST}:{KC_PORT}", 1)[-1]


def has_code(loc: str) -> bool:
    """True solo si la URL lleva un parámetro `code` EXACTO (no session_code)."""
    if not loc:
        return False
    return "code" in urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)


def form_action(html: str) -> str:
    return re.search(r'action="([^"]+)"', html).group(1).replace("&amp;", "&")


def form_fields_of(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]*>", html):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n and v:
            out[n.group(1)] = v.group(1)
    return out


def page_kind(html: str) -> str:
    if 'name="totpSecret"' in html:
        return "configure_totp"
    if 'name="password-new"' in html:
        return "update_password"
    if 'name="otp"' in html or 'name="totp"' in html:
        return "otp"
    if 'name="username"' in html:
        return "login"
    # Re-autenticación de KC (step-up sobre SSO): input password SIN input
    # username (el usuario intentado va readonly en kc-attempted-username).
    # NOTA: los divs id="input-error-container-*" están SIEMPRE presentes —
    # el check de error debe ir al final y basarse en kc-error real.
    if 'name="password"' in html:
        return "reauth"
    if "kc-error" in html or 'class="pf-v5-c-alert' in html:
        return "error"
    return "other"


def page_title(html: str) -> str:
    m = re.search(r"<title>([^<]+)</title>", html)
    return m.group(1).strip() if m else "?"


# ---------------------------------------------------------------------------
# TOTP RFC 6238 (SHA1, 6 dígitos, 30 s) — la clave HMAC son los bytes UTF-8
# RAW del secret de KC26 (NO base32 — pitfall #11 del skill keycloak-admin-oidc).
# ---------------------------------------------------------------------------
def totp(secret: str, counter: int) -> str:
    digest = hmac.new(secret.encode("utf-8"), struct.pack(">Q", counter),
                      hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0]
            & 0x7FFFFFFF) % 10**6
    return str(code).zfill(6)


class TotpPool:
    """Secrets enrolados + contadores consumidos (otpPolicyCodeReusable=false:
    un código NO sirve dos veces; lookahead=1 acepta counter+1)."""

    def __init__(self) -> None:
        self.secrets: list[str] = []
        self.used: set[int] = set()

    @property
    def current(self) -> str:
        assert self.secrets, "TOTP no enrolado todavía"
        return self.secrets[-1]

    def enroll(self, secret: str) -> None:
        self.secrets.append(secret)

    def fresh_code(self, secret: str) -> str:
        counter = int(time.time()) // 30
        for c in (counter, counter + 1):
            if c not in self.used:
                self.used.add(c)
                return totp(secret, c)
        time.sleep(30 - (time.time() % 30) + 1)
        c = int(time.time()) // 30
        self.used.add(c)
        return totp(secret, c)


def totp_secret_of(html: str) -> str:
    tag = re.search(r'<input[^>]*name="totpSecret"[^>]*>', html)
    if tag:
        m = re.search(r'value="([A-Za-z0-9]{16,})"', tag.group(0))
        if m:
            return m.group(1)
    m = re.search(r'value="([A-Za-z0-9]{16,})"', html)
    if m:
        return m.group(1)
    raise ValueError("totpSecret no encontrado en la página")


# ---------------------------------------------------------------------------
# Baile PKCE S256
# ---------------------------------------------------------------------------
def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def start_auth(browser: Browser, acr: str | None = None,
               extra: dict | None = None):
    """-> (status, body, headers, verifier)"""
    verifier = b64url(secrets.token_bytes(64))
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI, "state": "probe",
        "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    if acr is not None:
        params["acr_values"] = acr
    if extra:
        params.update(extra)
    status, body, hdrs = browser.fetch(
        f"/realms/{REALM}/protocol/openid-connect/auth?"
        f"{urllib.parse.urlencode(params)}")
    return status, body, hdrs, verifier


def exchange(browser: Browser, location: str, verifier: str) -> dict:
    assert has_code(location), f"sin code en {location[:160]}"
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(location).query)["code"][0]
    status, body, _ = browser.fetch(
        f"/realms/{REALM}/protocol/openid-connect/token", method="POST",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI, "code": code,
            "code_verifier": verifier,
        }).encode())
    if status != 200:
        die(f"token exchange HTTP {status}: {body[:200]}")
    return json.loads(body)


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def post_credentials(browser: Browser, html: str):
    return browser.fetch(
        path_of(form_action(html)), method="POST",
        data=urllib.parse.urlencode({
            "username": EMAIL, "password": PASSWORD, "credentialId": "",
        }).encode())


def drive(browser: Browser, status: int, body: str, hdrs: dict, verifier: str,
          pool: TotpPool, *, username: str = EMAIL, password: str = PASSWORD,
          max_steps: int = 10):
    """Avanza el flujo de KC (forms en cadena) hasta el code.

    -> (tokens | None, secuencia_de_páginas, info_de_fallo)
    """
    seq: list[str] = []

    def record(kind: str) -> None:
        seq.append(kind)
        log(f"    [drive] página: {kind}")

    for _step in range(max_steps):
        loc = hdrs.get("location", "")
        if has_code(loc):
            if not loc.startswith(REDIRECT_URI):
                # Code INTERMEDIO del broker (aterriza en …/broker/<idp>/
                # endpoint): pertenece al doble, NO se canjea contra el realm
                # real — se sigue el redirect y KC lo canjea por dentro.
                record("broker-hop")
                status, body, hdrs = browser.fetch(path_of(loc))
                continue
            tokens = exchange(browser, loc, verifier)
            record(f"code→acr={jwt_claims(tokens['id_token']).get('acr')!r}")
            return tokens, seq, None
        if status in (301, 302, 303, 307) and loc:
            if "required-action" in loc:
                record(f"required-action:{loc.split('execution=')[-1][:40]}")
            status, body, hdrs = browser.fetch(path_of(loc))
            continue
        kind = page_kind(body)
        if kind == "login":
            record("login")
            status, body, hdrs = browser.fetch(
                path_of(form_action(body)), method="POST",
                data=urllib.parse.urlencode({
                    "username": username, "password": password,
                    "credentialId": "",
                }).encode())
        elif kind == "reauth":
            # Step-up sobre SSO: KC pide re-autenticar (password sin username).
            record("reauth")
            status, body, hdrs = browser.fetch(
                path_of(form_action(body)), method="POST",
                data=urllib.parse.urlencode({
                    "password": password, "credentialId": "",
                }).encode())
        elif kind == "configure_totp":
            record("configure_totp")
            secret = totp_secret_of(body)
            pool.enroll(secret)
            code = pool.fresh_code(secret)
            log(f"    [drive] enrolando TOTP secret={secret} code={code}")
            status, body, hdrs = browser.fetch(
                path_of(form_action(body)), method="POST",
                data=urllib.parse.urlencode({
                    "totp": code, "totpSecret": secret, "mode": "totp",
                    "userLabel": "probe", "credentialId": "",
                }).encode())
        elif kind == "otp":
            record("otp")
            secret = pool.current
            fields = form_fields_of(body)
            code = pool.fresh_code(secret)
            fields["otp" if 'name="otp"' in body else "totp"] = code
            log(f"    [drive] 2º factor OTP code={code}")
            status, body, hdrs = browser.fetch(
                path_of(form_action(body)), method="POST",
                data=urllib.parse.urlencode(fields).encode())
        elif kind == "error":
            record("error")
            msg = (re.search(r'class="instruction">([^<]+)', body)
                   or re.search(r'kc-error-message[^>]*>\s*<p[^>]*>([^<]+)', body))
            import pathlib
            pathlib.Path("/tmp/probe_drive_error.html").write_text(body)
            return None, seq, {
                "kind": "error",
                "detail": msg.group(1).strip() if msg else "(ver /tmp/probe_drive_error.html)",
            }
        elif "authenticationExecution" in form_fields_of(body):
            # Página intermedia de KC (auto-submit por JS en un navegador
            # real; headless debe POSTear el form manualmente).
            record("intermediate")
            fields = form_fields_of(body)
            status, body, hdrs = browser.fetch(
                path_of(form_action(body)), method="POST",
                data=urllib.parse.urlencode(fields).encode())
        else:
            record(f"other:{page_title(body)}")
            log(f"    [drive] form desconocido: "
                f"inputs={sorted(form_fields_of(body))}")
            return None, seq, {
                "kind": "other",
                "title": page_title(body),
                "inputs": sorted(form_fields_of(body)),
                "body_head": body[:300],
            }
    return None, seq, {"kind": "max_steps"}


# ---------------------------------------------------------------------------
# Setup del realm desechable
# ---------------------------------------------------------------------------
def setup_realm() -> None:
    admin("DELETE", f"/admin/realms/{REALM}")
    admin("DELETE", f"/admin/realms/{DOUBLE_REALM}")
    admin_ok("POST", "/admin/realms", {"realm": REALM, "enabled": True})
    rep = json.loads(admin_ok("GET", f"/admin/realms/{REALM}"))
    rep["attributes"] = {**(rep.get("attributes") or {}),
                         "acr.loa.map": ACR_MAP}
    admin_ok("PUT", f"/admin/realms/{REALM}", rep)
    admin_ok("POST", f"/admin/realms/{REALM}/clients", {
        "clientId": CLIENT_ID, "enabled": True, "protocol": "openid-connect",
        "publicClient": True, "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "redirectUris": [REDIRECT_URI],
        "attributes": {"pkce.code.challenge.method": "S256"},
    })
    admin_ok("POST", f"/admin/realms/{REALM}/users", {
        "username": EMAIL, "enabled": True, "email": EMAIL,
        "emailVerified": True, "firstName": "Probe", "lastName": "User",
        "attributes": {"tenant_id": ["probe-tenant"]},
    })
    raw = admin_ok("GET", f"/admin/realms/{REALM}/users?username={EMAIL}&exact=true")
    uid = json.loads(raw)[0]["id"]
    admin_ok("PUT", f"/admin/realms/{REALM}/users/{uid}/reset-password",
             {"type": "password", "value": PASSWORD, "temporary": False})
    log(f"[setup] realm {REALM} + cliente + usuario listos "
        f"(acr.loa.map = {ACR_MAP})")


# ---------------------------------------------------------------------------
# Restructura del flujo browser (D-AUTH-06) — la pieza a congelar
# ---------------------------------------------------------------------------
def flow_executions(alias: str) -> list:
    raw = admin_ok(
        "GET",
        f"/admin/realms/{REALM}/authentication/flows/"
        f"{urllib.parse.quote(alias)}/executions")
    return json.loads(raw)


def set_loa_config(execution_id: str, level: str) -> None:
    body = {"alias": f"probe-loa-level-{level}",
            "config": {"loa-condition-level": level,
                       "loa-max-age": "36000"}}
    status, raw = admin(
        "POST",
        f"/admin/realms/{REALM}/authentication/executions/{execution_id}/config",
        body)
    if status == 404:
        status, raw = admin(
            "PUT",
            f"/admin/realms/{REALM}/authentication/executions/{execution_id}/config",
            body)
    if status not in (201, 204):
        die(f"config LoA nivel {level}: HTTP {status} {raw[:200]}")


def add_execution(flow_id: str, authenticator: str, requirement: str,
                  priority: int) -> None:
    status, raw = admin(
        "POST",
        f"/admin/realms/{REALM}/authentication/executions?flow={flow_id}",
        {"authenticator": authenticator, "requirement": requirement,
         "priority": priority, "parentFlow": flow_id})
    if status not in (201, 204):
        die(f"add execution {authenticator}: HTTP {status} {raw[:200]}")


def delete_execution(execution_id: str) -> None:
    status, raw = admin(
        "DELETE",
        f"/admin/realms/{REALM}/authentication/executions/{execution_id}")
    if status not in (200, 204, 404):
        die(f"delete execution: HTTP {status} {raw[:200]}")


def restructure_flow() -> None:
    admin_ok("POST",
             f"/admin/realms/{REALM}/authentication/flows/browser/copy",
             {"newName": FLOW})
    flat = flow_executions(FLOW)
    log("== copia del flujo browser (listing plano, incluye anidados) ==")
    for e in flat:
        log(f"    {e.get('displayName')!r:55} provider={e.get('providerId')!r:32}"
            f" req={e.get('requirement')}")

    forms = next(e for e in flat
                 if (e.get("displayName") or "").endswith(" forms"))
    forms_alias = forms["displayName"]
    otp_alias = next(e["displayName"] for e in flat
                     if "Conditional OTP" in (e.get("displayName") or ""))

    # -- 1) subflow "Password LoA" dentro de forms --------------------------
    # Endpoint empírico: POST /flows/{aliasPadre}/executions/flow. El alias del
    # subflow copiado es el displayName prefijado ("probe-browser-loa forms").
    # Campo proveedor y requirement: se prueban candidatos (hallazgo del probe).
    created = None
    for provider_field in ("providerId", "provider"):
        for provider in ("basic-flow", "form-flow", "registration-flow", ""):
            for requirement in ("CONDITIONAL", "ALTERNATIVE"):
                payload = {"alias": PASSWORD_SUBFLOW,
                           "description": "password wrapper (LoA 1)",
                           "type": "basic-flow",
                           "requirement": requirement}
                if provider:
                    payload[provider_field] = provider
                status, raw = admin(
                    "POST",
                    f"/admin/realms/{REALM}/authentication/flows/"
                    f"{urllib.parse.quote(forms_alias)}/executions/flow",
                    payload)
                if status in (201, 204):
                    created = (provider_field, provider, requirement)
                    break
                log(f"    subflow try {provider_field}={provider!r} "
                    f"req={requirement} -> {status} {raw[:80]}")
            if created:
                break
        if created:
            break
    if not created:
        die("no se pudo crear el subflow Password LoA (ver intentos arriba)")
    finding("subflow-create",
            f"POST /flows/{forms_alias!r}/executions/flow con "
            f"{created[0]}={created[1]!r} requirement={created[2]} -> 201")

    # -- 2) contenido del subflow nuevo --------------------------------------
    # GET /authentication/flows solo lista flows TOP-LEVEL: el alias del
    # subflow nuevo se descubre del listing plano de ejecuciones de la copia
    # (el alias de un subflow copiado/creado ES su displayName — hallazgo).
    flat = flow_executions(FLOW)
    pwd_flow = next(e for e in flat
                    if PASSWORD_SUBFLOW in (e.get("displayName") or ""))
    pwd_alias = pwd_flow["displayName"]
    pwd_id = pwd_flow["flowId"]

    # HALLAZGO (causa raíz del 400 UNKNOWN_USER): dentro de un padre, los
    # subflows CONDITIONAL van a la lista REQUIRED y se procesan POR PRIORIDAD;
    # el OTP Form lanza UNKNOWN_USER si corre con authUser==null. El wrapper
    # "Password LoA" debe tener prioridad MENOR que "Conditional OTP" para
    # renderear el login antes. La creación ignora requirement y priority →
    # se fijan con PUT .../flows/{FLOW}/executions con la entrada del listing.
    entry = dict(pwd_flow)
    entry["requirement"] = "CONDITIONAL"
    entry["priority"] = 15  # antes que Conditional OTP (priority 20)
    status, raw = admin(
        "PUT",
        f"/admin/realms/{REALM}/authentication/flows/"
        f"{urllib.parse.quote(FLOW)}/executions", entry)
    if status not in (200, 204):
        die(f"set requirement/priority del subflow: HTTP {status} {raw[:200]}")
    finding("subflow-requirement-priority",
            "la creación ignora requirement y priority; PUT flows/{FLOW}/"
            "executions los fija. CRÍTICO: Password LoA debe correr ANTES que "
            "Conditional OTP (priority menor) — si no, el OTP Form evalúa con "
            "authUser==null y el authorize muere con 400 UNKNOWN_USER")

    # condición LoA nivel 1 (primero, priority bajo)
    add_execution(pwd_id, "conditional-level-of-authentication", "REQUIRED", 10)
    pwd_execs = flow_executions(pwd_alias)
    loa_exec = next(e for e in pwd_execs
                    if e.get("providerId") == "conditional-level-of-authentication")
    set_loa_config(loa_exec["id"], "1")
    # username+password dentro del wrapper
    add_execution(pwd_id, "auth-username-password-form", "REQUIRED", 20)

    # -- 3) mover el form original: borrarlo de forms ------------------------
    forms_execs = flow_executions(forms_alias)
    orig = next(e for e in forms_execs
                if e.get("providerId") == "auth-username-password-form")
    delete_execution(orig["id"])

    # -- 4) Conditional OTP: LoA nivel 2, SIN user-configured ----------------
    otp_execs = flow_executions(otp_alias)
    for e in otp_execs:
        if e.get("providerId") == "conditional-user-configured":
            delete_execution(e["id"])
    otp_flow = next(e for e in flat
                    if (e.get("displayName") or "") == otp_alias)
    add_execution(otp_flow["flowId"],
                  "conditional-level-of-authentication", "REQUIRED", 10)
    otp_execs = flow_executions(otp_alias)
    loa2 = next(e for e in otp_execs
                if e.get("providerId") == "conditional-level-of-authentication")
    set_loa_config(loa2["id"], "2")

    # -- 5) bind --------------------------------------------------------------
    rep = json.loads(admin_ok("GET", f"/admin/realms/{REALM}"))
    admin_ok("PUT", f"/admin/realms/{REALM}", {"browserFlow": FLOW})
    log(f"[flow] {FLOW} reestructurado y ligado "
        f"(Password LoA@1 + Conditional OTP@2, user-configured eliminado)")
    _dump_flow()


def _dump_flow() -> None:
    log("== flujo final ==")
    for e in flow_executions(FLOW):
        log(f"    {e.get('displayName')!r:55} provider={e.get('providerId')!r:32}"
            f" req={e.get('requirement')}")


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------
def fresh_login(acr: str | None, pool: TotpPool, name: str,
                extra: dict | None = None):
    log(f"\n== {name}: login fresco acr_values={acr!r} ==")
    b = Browser()
    status, body, hdrs, verifier = start_auth(b, acr, extra)
    if status != 200:
        import pathlib
        err_file = pathlib.Path("/tmp/probe_authorize_error.html")
        err_file.write_text(body)
        msg = re.search(r'kc-error-message[^>]*>([^<]+)', body)
        log(f"    [authorize] HTTP {status}: error="
            f"{msg.group(1).strip() if msg else body[400:1200]!r}")
        return None, None, f"authorize HTTP {status}"
    tokens, seq, fail = drive(b, status, body, hdrs, verifier, pool)
    return tokens, (b, seq), fail


def scenario_s1_s2(pool: TotpPool):
    # S1 — acr 1 sin TOTP: password-only debe completar SIN formulario OTP.
    tokens, ctx, fail = fresh_login("1", pool, "S1")
    b1, seq1 = ctx if ctx else (None, [])
    if tokens is None:
        finding("S1-acr1-sin-totp", f"FALLO {fail} — secuencia {seq1}")
        die("S1 no completó (hipótesis D-AUTH-06 refutada en el primer caso)")
    acr = jwt_claims(tokens["id_token"]).get("acr")
    finding("S1-acr1-sin-totp",
            f"secuencia={seq1} → code con acr={acr!r} "
            f"({'OK: sin OTP' if acr == '1' and 'otp' not in seq1 and 'configure_totp' not in seq1 else 'INESPERADO'})")

    # S2 — acr 2 sin TOTP: debe derivar inline a CONFIGURE_TOTP (sin code).
    log("\n== S2: login fresco acr_values=2 sin TOTP ==")
    b2 = Browser()
    status, body, hdrs0, verifier = start_auth(b2, "2")
    status, body, hdrs = post_credentials(b2, body)
    loc = hdrs.get("location", "")
    kind = page_kind(body)
    if has_code(loc):
        finding("S2-acr2-sin-totp",
                f"INESPERADO: code directo (acr="
                f"{jwt_claims(exchange(b2, loc, verifier)['id_token']).get('acr')!r})")
    elif "required-action" in loc and "CONFIGURE_TOTP" in loc:
        finding("S2-acr2-sin-totp",
                f"OK: redirect a required-action CONFIGURE_TOTP (sin code) — {loc[:90]}")
    elif kind == "configure_totp":
        finding("S2-acr2-sin-totp", "OK: página CONFIGURE_TOTP inline (200)")
    else:
        finding("S2-acr2-sin-totp", f"INESPERADO: kind={kind} loc={loc[:120]}")
    return b2, verifier, status, body, hdrs


def scenario_s3(b: Browser, verifier: str, pool: TotpPool,
                status: int, body: str, hdrs: dict) -> None:
    log("\n== S3: completar enrolamiento TOTP real → code ==")
    # Continuamos EXACTAMENTE donde quedó S2 (redirect al required action o
    # página CONFIGURE_TOTP ya renderizada): drive() sigue desde ahí.
    tokens, seq, fail = drive(b, status, body, hdrs, verifier, pool)
    if tokens is None:
        finding("S3-enrolamiento", f"FALLO {fail} — secuencia {seq}")
        die("S3 no completó el enrolamiento")
    acr = jwt_claims(tokens["id_token"]).get("acr")
    finding("S3-enrolamiento",
            f"secuencia={seq} → code con acr={acr!r} "
            f"({'OK' if acr == '2' else 'INESPERADO'})")


def scenario_s4_s5(pool: TotpPool):
    tokens, ctx, fail = fresh_login("2", pool, "S4")
    b4, seq4 = ctx if ctx else (None, [])
    acr4 = jwt_claims(tokens["id_token"]).get("acr") if tokens else None
    finding("S4-acr2-con-totp",
            f"secuencia={seq4} → acr={acr4!r} "
            f"({'OK: OTP verificado' if acr4 == '2' else 'INESPERADO: ' + str(fail)})")

    tokens, ctx, fail = fresh_login("1", pool, "S5")
    b5, seq5 = ctx if ctx else (None, [])
    acr5 = jwt_claims(tokens["id_token"]).get("acr") if tokens else None
    ok5 = (acr5 == "1" and "otp" not in seq5 and "configure_totp" not in seq5)
    finding("S5-acr1-con-totp",
            f"secuencia={seq5} → acr={acr5!r} "
            f"({'OK: password-only pese a TOTP enrolado' if ok5 else 'INESPERADO'})")
    return b5


def scenario_s6(b_sso_loa1: Browser, pool: TotpPool) -> None:
    log("\n== S6: re-login SSO (cookie, sesión LoA 1) ==")
    b = b_sso_loa1

    # (a) authorize acr=1 sobre la cookie
    status, body, hdrs, verifier = start_auth(b, "1")
    tokens, seq, fail = drive(b, status, body, hdrs, verifier, pool)
    acr = jwt_claims(tokens["id_token"]).get("acr") if tokens else None
    finding("S6a-SSO-acr1",
            f"secuencia={seq} → acr={acr!r} "
            f"(sesión LoA 1 + cookie: ¿code sin password?)")

    # (b) authorize acr=2 sobre la MISMA cookie
    status, body, hdrs, verifier = start_auth(b, "2")
    tokens, seq, fail = drive(b, status, body, hdrs, verifier, pool)
    acr = jwt_claims(tokens["id_token"]).get("acr") if tokens else None
    finding("S6b-SSO-acr2",
            f"secuencia={seq} → acr={acr!r} fail={fail} "
            f"(¿step-up real con OTP, o cookie short-circuit sin OTP?)")

    # (c) authorize acr=2 + prompt=login (fuerza re-autenticación fresca)
    status, body, hdrs, verifier = start_auth(b, "2", {"prompt": "login"})
    tokens, seq, fail = drive(b, status, body, hdrs, verifier, pool)
    acr = jwt_claims(tokens["id_token"]).get("acr") if tokens else None
    finding("S6c-SSO-acr2-prompt-login",
            f"secuencia={seq} → acr={acr!r} "
            f"(prompt=login: ¿password+OTP forzados sobre sesión SSO?)")


def scenario_s7(pool: TotpPool) -> None:
    tokens, ctx, fail = fresh_login(None, pool, "S7")
    b7, seq7 = ctx if ctx else (None, [])
    acr = jwt_claims(tokens["id_token"]).get("acr") if tokens else None
    finding("S7-bare-authorize",
            f"secuencia={seq7} → acr={acr!r} "
            f"(SIN acr_values: impacto directo en la suite e2e)")


def scenario_s8(pool: TotpPool) -> None:
    # Sesión LoA 2 (login completo con OTP) → authorize acr=2 con cookie:
    # ¿step-up otorgado por loa-max-age sin volver a pedir OTP?
    tokens, ctx, fail = fresh_login("2", pool, "S8-setup")
    b8, _ = ctx if ctx else (None, [])
    if tokens is None or b8 is None:
        finding("S8-SSO-loa2-acr2", f"FALLO setup: {fail}")
        return
    status, body, hdrs, verifier = start_auth(b8, "2")
    tokens2, seq, fail = drive(b8, status, body, hdrs, verifier, pool)
    acr = jwt_claims(tokens2["id_token"]).get("acr") if tokens2 else None
    finding("S8-SSO-loa2-acr2",
            f"secuencia={seq} → acr={acr!r} "
            f"(sesión LoA 2 + acr=2: ¿code sin OTP por maxAge?)")


# ---------------------------------------------------------------------------
# G1 — mecánica del IdP google (credenciales placeholder DECLARADAS)
# ---------------------------------------------------------------------------
def scenario_g1() -> None:
    log("\n== G1: IdP google (placeholder declarado) + kc_idp_hint ==")
    status, raw = admin(
        "POST", f"/admin/realms/{REALM}/identity-provider/instances",
        {"alias": "google", "providerId": "google", "enabled": True,
         "trustEmail": True, "firstBrokerLoginFlowAlias": "first broker login",
         "config": {"clientId": "placeholder-client-id",
                    "clientSecret": "placeholder-client-secret"}})
    if status not in (201, 409):
        finding("G1-google-idp", f"FALLO creación: HTTP {status} {raw[:200]}")
        return
    # ¿El botón aparece en la página de login? (social-providers del tema)
    b = Browser()
    st, body, _, _ = start_auth(b, "1")
    button = ("google" in body.lower()
              and ("sign in with google" in body.lower()
                   or "zocial google" in body.lower()
                   or "social" in body.lower()))
    # ¿kc_idp_hint=google redirige a accounts.google.com? Se siguen los hops
    # internos del broker (…/broker/google/login → 302 externo); NO se sigue
    # el redirect externo (placeholder, no se toca Google).
    b2 = Browser()
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI, "state": "probe",
        "code_challenge": b64url(hashlib.sha256(b64url(secrets.token_bytes(64)).encode()).digest()),
        "code_challenge_method": "S256", "acr_values": "1",
        "kc_idp_hint": "google",
    }
    st2, body2, hdrs2 = b2.fetch(
        f"/realms/{REALM}/protocol/openid-connect/auth?"
        f"{urllib.parse.urlencode(params)}")
    hops = []
    external = ""
    for _hop in range(5):
        loc = hdrs2.get("location", "")
        hops.append(loc[:90])
        if not loc:
            break
        if loc.startswith("https://") and KC_HOST not in loc:
            external = loc
            break
        if has_code(loc) or st2 not in (301, 302, 303, 307):
            break
        st2, body2, hdrs2 = b2.fetch(path_of(loc))
    finding("G1-google-idp",
            f"creado HTTP {status}; botón en login={button}; "
            f"redirect externo={'OK → ' + external[:80] if external.startswith('https://accounts.google.com/') else external[:80] or 'NINGUNO'}; "
            f"hops={hops}")


# ---------------------------------------------------------------------------
# B1 — brokering completo contra un doble OIDC local DECLARADO
# ---------------------------------------------------------------------------
def setup_double_realm() -> None:
    admin_ok("POST", "/admin/realms", {"realm": DOUBLE_REALM, "enabled": True})
    admin_ok("POST", f"/admin/realms/{DOUBLE_REALM}/clients", {
        "clientId": BROKER_CLIENT_ID, "enabled": True,
        "protocol": "openid-connect", "publicClient": False,
        "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
        "secret": BROKER_CLIENT_SECRET,
        "redirectUris": [f"http://{KC_HOST}:{KC_PORT}/realms/{REALM}/broker/double-oidc/endpoint"],
    })
    admin_ok("POST", f"/admin/realms/{DOUBLE_REALM}/users", {
        "username": DOUBLE_EMAIL, "enabled": True, "email": DOUBLE_EMAIL,
        "emailVerified": True, "firstName": "Double", "lastName": "Oidc",
    })
    raw = admin_ok("GET", f"/admin/realms/{DOUBLE_REALM}/users?username={DOUBLE_EMAIL}&exact=true")
    uid = json.loads(raw)[0]["id"]
    admin_ok("PUT", f"/admin/realms/{DOUBLE_REALM}/users/{uid}/reset-password",
             {"type": "password", "value": DOUBLE_PASSWORD, "temporary": False})
    # IdP oidc en probe-loa apuntando al doble. El doble vive en
    # localhost:8080 (NO 127.0.0.1): las cookies son por-host y ambos
    # realms usan el mismo nombre AUTH_SESSION_ID — con el doble en otro
    # hostname el jar del navegador separa las sesiones igual que con
    # accounts.google.com en producción (hallazgo B1).
    base = f"http://localhost:{KC_PORT}/realms/{DOUBLE_REALM}/protocol/openid-connect"
    status, raw = admin(
        "POST", f"/admin/realms/{REALM}/identity-provider/instances",
        {"alias": "double-oidc", "providerId": "oidc", "enabled": True,
         "trustEmail": True, "firstBrokerLoginFlowAlias": "first broker login",
         "config": {
             "clientId": BROKER_CLIENT_ID,
             "clientSecret": BROKER_CLIENT_SECRET,
             "authorizationUrl": f"{base}/auth",
             "tokenUrl": f"{base}/token",
             "userinfoUrl": f"{base}/userinfo",
             "issuer": f"http://localhost:{KC_PORT}/realms/{DOUBLE_REALM}",
             "jwksUrl": f"{base}/certs",
             "useJwksUrl": "true",
             "validateSignature": "true",
         }})
    if status not in (201, 409):
        die(f"IdP double-oidc: HTTP {status} {raw[:300]}")
    log(f"[broker] realm doble {DOUBLE_REALM} + IdP double-oidc listos")


def scenario_b1(pool: TotpPool) -> None:
    log("\n== B1: primer login brokered (doble OIDC local declarado) ==")
    b = Browser()
    verifier = b64url(secrets.token_bytes(64))
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI, "state": "probe",
        "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256", "acr_values": "1",
        "kc_idp_hint": "double-oidc",
    }
    status, body, hdrs = b.fetch(
        f"/realms/{REALM}/protocol/openid-connect/auth?"
        f"{urllib.parse.urlencode(params)}")
    # El IdP redirector manda 302 al authorize del doble; se siguen los hops
    # internos hasta la página de login del DOBLE.
    loc = ""
    for _hop in range(8):
        if status not in (301, 302, 303, 307):
            break
        loc = hdrs.get("location", "")
        if not loc:
            break
        if has_code(loc) and loc.startswith(REDIRECT_URI):
            break  # code FINAL del realm real (no debería pasar sin credenciales)
        status, body, hdrs = b.fetch(path_of(loc))
    if status != 200 or page_kind(body) != "login":
        finding("B1-brokered", f"INESPERADO tras hint: HTTP {status} "
                               f"kind={page_kind(body)} loc={loc[:120]}")
        return
    if DOUBLE_REALM not in body:
        log(f"    [B1] aviso: la página de login no muestra el realm del doble "
            f"(title={page_title(body)})")
    # credenciales del DOBLE → vuelve por el broker endpoint → first broker
    # login → (Create User If Unique) → code del realm REAL.
    status, body, hdrs = b.fetch(path_of(form_action(body)), method="POST",
                                 data=urllib.parse.urlencode({
                                     "username": DOUBLE_EMAIL,
                                     "password": DOUBLE_PASSWORD,
                                     "credentialId": ""}).encode())
    tokens, seq, fail = drive(b, status, body, hdrs, verifier, pool)
    if tokens is None:
        finding("B1-brokered", f"FALLO {fail} — secuencia {seq}")
        return
    claims = jwt_claims(tokens["id_token"])
    # usuario auto-creado en el realm real por "Create User If Unique"
    raw = admin_ok("GET", f"/admin/realms/{REALM}/users?username={DOUBLE_EMAIL}&exact=true")
    users = json.loads(raw)
    fed = None
    if users:
        raw_u = admin_ok("GET", f"/admin/realms/{REALM}/users/{users[0]['id']}")
        fed = json.loads(raw_u).get("federatedIdentities")
    finding("B1-brokered",
            f"secuencia={seq} → acr={claims.get('acr')!r} "
            f"sub={claims.get('sub')[:8]}… email={claims.get('email')!r} "
            f"tenant_id={claims.get('tenant_id')!r} "
            f"usuario_auto_creado={bool(users)} federado={fed!r}")


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true",
                        help="no borrar los realms al final")
    parser.add_argument("--skip-broker", action="store_true",
                        help="saltar G1/B1 (solo escenarios LoA)")
    args = parser.parse_args()

    log("Probe D-AUTH-06 contra Keycloak 26.2.5 vivo "
        f"(http://{KC_HOST}:{KC_PORT})\n")
    setup_realm()
    restructure_flow()

    pool = TotpPool()
    b2, verifier2, st2, body2, hdrs2 = scenario_s1_s2(pool)
    scenario_s3(b2, verifier2, pool, st2, body2, hdrs2)
    b5 = scenario_s4_s5(pool)
    if b5 is not None:
        scenario_s6(b5, pool)
    scenario_s7(pool)
    scenario_s8(pool)

    if not args.skip_broker:
        scenario_g1()
        setup_double_realm()
        scenario_b1(pool)

    if not args.keep:
        admin("DELETE", f"/admin/realms/{REALM}")
        admin("DELETE", f"/admin/realms/{DOUBLE_REALM}")
        log("\n[cleanup] realms desechables eliminados")

    log("\n" + "=" * 70)
    log("FINDINGS (para congelar D-AUTH-06 y actualizar el skill):")
    for f in FINDINGS:
        log(f"  - {f}")
    log("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())

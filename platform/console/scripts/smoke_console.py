#!/usr/bin/env python3
"""Smoke E2E del Admin Console contra infraestructura REAL (spec 009 T18).

Flujo completo sin simulación (constitution I):
  1. GET /auth/login?org=…        → redirect al authorize del realm con PKCE S256
  2. Login real en Keycloak 26:   credenciales one-time → required actions
                                   (cambio de password + enrolamiento TOTP real,
                                   código RFC 6238 generado aquí)
  3. Callback del console:        canje code → mint sesión admin → cookies
  4. Verificaciones de datos reales por el proxy BFF (whoami, budgets,
     audit, users/branding spec 012, CSRF negativo, página HTML).

Notas técnicas:
- Cookies manuales (dict): las cookies de Keycloak llegan con flag `Secure` y
  http.cookiejar se niega a reenviarlas sobre http://. Un navegador real SÍ
  las acepta en 127.0.0.1 (excepción localhost secure-context); el manejo
  manual replica exactamente ese comportamiento.
- Sin redirects automáticos: cada hop se inspecciona (302/307).

Uso: .venv/bin/python scripts/smoke_console.py <one_time_password>
"""
import base64
import hashlib
import hmac
import http.client
import json
import re
import struct
import sys
import time
import urllib.parse

CONSOLE_HOST, CONSOLE_PORT = "127.0.0.1", 3100
KC_HOST, KC_PORT = "127.0.0.1", 8080
SLUG = "console-smoke2"
ADMIN_EMAIL = "admin@console-smoke2.test"
NEW_PASSWORD = "SmokeAdmin2026!"
# Bootstrap interno admin-cli (ADR-015, patrón de la suite e2e): solo para
# dejar el usuario de test en estado pristine antes del baile.
KC_ADMIN_USER = "admin"
KC_ADMIN_PASSWORD = "KeycloakDemo2026"

COOKIES: dict[str, str] = {}


def kc_admin_token() -> str:
    st, body, _, _ = fetch(KC_HOST, KC_PORT, "/realms/master/protocol/openid-connect/token",
                        method="POST",
                        data=urllib.parse.urlencode({
                            "grant_type": "password", "client_id": "admin-cli",
                            "username": KC_ADMIN_USER, "password": KC_ADMIN_PASSWORD,
                        }).encode())
    if st != 200:
        raise RuntimeError(f"admin-cli token failed: HTTP {st}")
    return json.loads(body)["access_token"]


def reset_user(ota_password: str) -> None:
    """Deja al admin de test en estado pristine (idempotente).

    El smoke consume el one-time password y enrola un TOTP nuevo en cada
    corrida: sin reset, la segunda corrida falla en las credenciales.
    Usa Admin REST (mismo bootstrap que la suite e2e).
    """
    token = kc_admin_token()
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    st, body, _, _ = fetch(KC_HOST, KC_PORT,
                        f"/admin/realms/{SLUG}/users?username={urllib.parse.quote(ADMIN_EMAIL)}&exact=true",
                        headers=auth)
    user_id = json.loads(body)[0]["id"]
    # 1) borrar credenciales OTP previas
    st, body, _, _ = fetch(KC_HOST, KC_PORT, f"/admin/realms/{SLUG}/users/{user_id}/credentials",
                        headers=auth)
    for cred in json.loads(body):
        if cred["type"] == "otp":
            fetch(KC_HOST, KC_PORT,
                  f"/admin/realms/{SLUG}/users/{user_id}/credentials/{cred['id']}",
                  method="DELETE", headers=auth)
    # 2) required actions pristine del alta
    fetch(KC_HOST, KC_PORT, f"/admin/realms/{SLUG}/users/{user_id}", method="PUT",
          data=json.dumps({"requiredActions": ["CONFIGURE_TOTP", "UPDATE_PASSWORD"]}).encode(),
          headers=auth)
    # 3) one-time password fresco
    st, _, _, _ = fetch(KC_HOST, KC_PORT,
                     f"/admin/realms/{SLUG}/users/{user_id}/reset-password", method="PUT",
                     data=json.dumps({"type": "password", "value": ota_password,
                                      "temporary": True}).encode(),
                     headers=auth)
    if st not in (200, 204):
        raise RuntimeError(f"reset-password failed: HTTP {st}")
    print("[reset] usuario de test en estado pristine (OTP borrado, OTA fresco)")


def fetch(host: str, port: int, path: str, *, method="GET", data=None,
          headers=None, follow=True, max_hops=8):
    """Request con cookie jar manual. -> (status, body, headers_dict, path)."""
    h = dict(headers or {})
    if COOKIES:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIES.items())
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn = http.client.HTTPConnection(host, port, timeout=20)
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    hdrs = {k.lower(): v for k, v in resp.getheaders()}
    for raw in resp.msg.get_all("Set-Cookie") or []:
        name, _, rest = raw.partition("=")
        value = rest.split(";")[0]
        if value:
            COOKIES[name.strip()] = value
        else:
            COOKIES.pop(name.strip(), None)
    status = resp.status
    conn.close()

    if follow and status in (301, 302, 303, 307) and max_hops > 0:
        loc = hdrs.get("location", "")
        if loc:
            if loc.startswith("http"):
                u = urllib.parse.urlparse(loc)
                return fetch(u.hostname, u.port or 80,
                             u.path + ("?" + u.query if u.query else ""),
                             method="GET" if status in (301, 302, 303) else method,
                             data=None, headers=headers, follow=True,
                             max_hops=max_hops - 1)
            return fetch(host, port, loc,
                         method="GET" if status in (301, 302, 303) else method,
                         data=None, headers=headers, follow=True,
                         max_hops=max_hops - 1)
    return status, body, hdrs, path


def form_action(html: str) -> str:
    return re.search(r'action="([^"]+)"', html).group(1).replace("&amp;", "&")


def form_fields_of(html: str) -> dict[str, str]:
    """Todos los inputs con value del form (hidden fields incluidos)."""
    out: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]*>", html):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n and v:
            out[n.group(1)] = v.group(1)
    return out


def totp_secret_of(html: str) -> str:
    """Extrae el secreto TOTP de la página de enrolamiento de Keycloak.

    Keycloak 26 genera secretos alfanuméricos mixtos (la clave HMAC son los
    bytes UTF-8 raw — ver totp()): se extrae SIN normalizar mayúsculas.
    Estrategias: (1) input oculto name="totpSecret", (2) cualquier
    value="…", (3) texto expuesto.
    """
    candidates: list[str] = []
    tag = re.search(r'<input[^>]*name="totpSecret"[^>]*>', html)
    if tag:
        m = re.search(r'value="([A-Za-z0-9]{16,})"', tag.group(0))
        if m:
            candidates.append(m.group(1))
    m = re.search(r'value="([A-Za-z0-9]{16,})"', html)
    if m:
        candidates.append(m.group(1))
    m = re.search(r'>([A-Za-z0-9]{16,})<', html)
    if m:
        candidates.append(m.group(1))
    for c in candidates:
        return c
    open("/tmp/kc_totp_debug.html", "w").write(html)
    raise ValueError(
        "TOTP secret not found — page dumped to /tmp/kc_totp_debug.html"
    )


def totp(secret: str, t: float | None = None, counter: int | None = None) -> str:
    """RFC 6238 (SHA1, 6 dígitos, 30 s) — política default de Keycloak.

    La clave HMAC son los bytes UTF-8 RAW del secreto: verificado
    empíricamente contra Keycloak 26 (el secreto es alfanumérico mixto, NO
    base32 — b32decode falla con 'Non-base32 digit found' y el enrolamiento
    con clave raw pasa la validación del realm).
    """
    key = secret.encode("utf-8")
    if counter is None:
        counter = int((t if t is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6
    return str(code).zfill(6)


def kc_path(url: str) -> str:
    """URL completa de KC → path (para http.client)."""
    return url.split(f"{KC_HOST}:{KC_PORT}")[-1]


def main(ota_password: str) -> int:
    failures = []

    def check(name, cond, detail=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    # --- 0) /login ---------------------------------------------------------
    reset_user(ota_password)
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, "/login")
    check("GET /login → 200 con formulario de org code", st == 200 and "Organization code" in body, f"HTTP {st}")

    # --- 1) /auth/login arranca PKCE (sin seguir el redirect) --------------
    st, _, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, f"/auth/login?org={SLUG}", follow=False)
    loc = hdrs.get("location", "")
    check("GET /auth/login?org → redirect al authorize del realm",
          st in (301, 302, 303, 307) and "/protocol/openid-connect/auth" in loc, f"HTTP {st} loc={loc[:120]}")
    check("PKCE S256 en el authorize", "code_challenge_method=S256" in loc and "code_challenge=" in loc)
    check("redirect URI del console (:3100) en el authorize", "3100" in loc, loc[:200])

    # --- 2+3) Login real en Keycloak + callback del console ----------------
    secret_state: dict[str, str] = {}

    def login_dance(password: str) -> bool:
        """Un baile completo console → Keycloak → callback. True si mintea sesión.

        Primer login: one-time password → (UPDATE_PASSWORD) → CONFIGURE_TOTP
        (enrolamiento real; el secreto queda en secret_state). Logins
        siguientes: password + código OTP como segundo factor.
        """
        for k in list(COOKIES):  # sesiones KC previas no deben interferir
            if not k.startswith("ta_"):
                COOKIES.pop(k)
        st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, f"/auth/login?org={SLUG}")
        if not (st == 200 and 'action="' in body):
            print(f"    [dance] login page FAIL HTTP {st}")
            return False
        action = form_action(body)
        st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(action), method="POST",
                                  data=urllib.parse.urlencode({
                                      "username": ADMIN_EMAIL, "password": password,
                                      "credentialId": ""}).encode(),
                                  follow=False)
        # Bucle de required actions: KC encadena en el MISMO login
        # (p. ej. CONFIGURE_TOTP → UPDATE_PASSWORD → code). Cada paso puede
        # responder 302 (con code o al siguiente paso) o 200 (siguiente form).
        loc = hdrs.get("location", "")

        def follow_hop(status: int, location: str, body_in: str) -> tuple[int, str, str, dict]:
            """Sigue un redirect interno de KC → (status, body, location, headers)."""
            if not location or "code=" in location or status not in (301, 302, 303, 307):
                return status, body_in, location, hdrs
            if location.startswith(f"http://{KC_HOST}:{KC_PORT}"):
                s2, b2, h2, _ = fetch(KC_HOST, KC_PORT, kc_path(location), follow=False)
            else:
                s2, b2, h2, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                                      location if location.startswith("/") else "/" + location,
                                      follow=False)
            return s2, b2, h2.get("location", ""), h2

        st, body, loc, hdrs = follow_hop(st, loc, body)
        for _step in range(6):
            if "code=" in loc:
                break
            if not (st == 200 and 'action="' in body):
                break
            act = form_action(body)
            if "password-new" in body:
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                          data=urllib.parse.urlencode({
                                              "username": ADMIN_EMAIL, "password": password,
                                              "password-new": NEW_PASSWORD,
                                              "password-confirm": NEW_PASSWORD,
                                              "credentialId": ""}).encode(),
                                          follow=False)
            elif 'name="totpSecret"' in body:
                try:
                    secret = totp_secret_of(body)
                except ValueError as exc:
                    print(f"    [dance] totp secret FAIL: {exc}")
                    return False
                print(f"    [dance] enrolando TOTP secret={secret} code={totp(secret)}")
                with open("/tmp/kc_smoke_secret.txt", "w") as f:
                    f.write(secret)  # debug cross-run del secreto enrolado
                secret_state["secret"] = secret
                # otpPolicyCodeReusable=false: el código del enrolamiento NO
                # puede reutilizarse; el 2º factor usa counter+1 (lookahead 1).
                secret_state["used_counter"] = int(time.time()) // 30
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                          data=urllib.parse.urlencode({
                                              "totp": totp(secret), "totpSecret": secret,
                                              "mode": "totp", "userLabel": "smoke-console",
                                              "credentialId": ""}).encode(),
                                          follow=False)
            elif 'name="otp"' in body or 'name="totp"' in body:
                # Segundo factor: el form de autenticación usa name="otp";
                # otros renders usan "totp". Se reenvían TODOS los hidden
                # fields del form (p. ej. selectedCredentialId).
                code_field = "otp" if 'name="otp"' in body else "totp"
                secret = secret_state.get("secret", "")
                counter = int(time.time()) // 30
                if counter == secret_state.get("used_counter"):
                    counter += 1  # código del enrolamiento consumido (no reusable)
                fields = form_fields_of(body)
                fields[code_field] = totp(secret, counter=counter)
                print(f"    [dance] 2º factor OTP: code={fields[code_field]} counter={counter} fields={sorted(fields)}")
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                          data=urllib.parse.urlencode(fields).encode(),
                                          follow=False)
            else:
                # formulario desconocido (p. ej. consent): se acepta tal cual
                print(f"    [dance] form desconocido: inputs={re.findall(r'<input[^>]*name=([^ >]+)', body)[:6]} "
                      f"title={re.search(r'<title>([^<]+)', body).group(1) if re.search(r'<title>([^<]+)', body) else '?'}")
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                          data=b"", follow=False)
            loc = hdrs.get("location", "")
            st, body, loc, hdrs = follow_hop(st, loc, body)
        print(f"    [dance] tras factores: HTTP {st} loc={loc}")
        # hops intermedios hasta el code
        hops = 0
        while "code=" not in loc and hops < 6 and loc:
            if loc.startswith(f"http://{KC_HOST}:{KC_PORT}"):
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(loc), follow=False)
            else:
                st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                                          loc if loc.startswith("/") else "/" + loc, follow=False)
            nxt = hdrs.get("location", "")
            if not nxt and 'action="' in body:
                act = form_action(body)
                if 'name="totp"' in body:
                    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                              data=urllib.parse.urlencode(
                                                  {"totp": totp(secret_state.get("secret", "")),
                                                   "credentialId": ""}).encode(),
                                              follow=False)
                    nxt = hdrs.get("location", "")
                else:
                    st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                              data=b"", follow=False)
                    nxt = hdrs.get("location", "")
            loc = nxt
            hops += 1
        if "code=" not in loc:
            print("    [dance] sin code tras los hops")
            return False
        # Callback del console: canje + mint + cookies. Respuestas posibles:
        #  - 307 /overview → sesión mintada con MFA verificada
        #  - 307 al authorize → step-up MFA (D-MFA-01): el console re-intenta
        #    el authorize con acr_values; la condición LoA del realm fuerza el
        #    formulario OTP sobre la sesión SSO existente.
        for _attempt in range(3):
            st, body, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT,
                                      loc if loc.startswith("/") else loc.split(f"{CONSOLE_HOST}:{CONSOLE_PORT}")[-1],
                                      follow=False)
            final = hdrs.get("location", "")
            if "/overview" in final:
                print(f"    [dance] callback: HTTP {st} → /overview (sesión mintada)")
                return True
            if "/protocol/openid-connect/auth" in final:
                print("    [dance] step-up MFA: re-authorize con acr_values (LoA)")
                st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(final), follow=False)
                loc2 = hdrs.get("location", "")
                hops = 0
                while "code=" not in (loc2 or "") and hops < 4:
                    if loc2 and st in (301, 302, 303, 307):
                        st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(loc2), follow=False)
                        loc2 = hdrs.get("location", "")
                    elif 'name="otp"' in body or 'name="totp"' in body:
                        act = form_action(body)
                        code_field = "otp" if 'name="otp"' in body else "totp"
                        fields = form_fields_of(body)
                        counter = int(time.time()) // 30
                        if counter == secret_state.get("used_counter"):
                            counter += 1  # código del enrolamiento ya consumido
                        fields[code_field] = totp(secret_state.get("secret", ""), counter=counter)
                        print(f"    [dance] step-up OTP: code={fields[code_field]}")
                        st, body, hdrs, _ = fetch(KC_HOST, KC_PORT, kc_path(act), method="POST",
                                                  data=urllib.parse.urlencode(fields).encode(),
                                                  follow=False)
                        loc2 = hdrs.get("location", "")
                        if "code=" in loc2:
                            secret_state["used_counter"] = counter
                    else:
                        break
                    hops += 1
                if "code=" not in (loc2 or ""):
                    print(f"    [dance] step-up sin code: {(loc2 or '')[:120]}")
                    return False
                loc = loc2
                continue
            print(f"    [dance] callback inesperado: HTTP {st} loc={final[:120]} body={body[:120]!r}")
            return False
        return False

    check("login #1 real (PKCE + one-time password + enrolamiento TOTP + callback mint)",
          login_dance(ota_password))
    check("cookies de sesión presentes (ta_session/ta_refresh/ta_csrf)",
          {"ta_session", "ta_refresh", "ta_csrf"} <= set(COOKIES), str(sorted(COOKIES)))

    # --- 4) Datos reales por el proxy ---------------------------------------
    def proxy(path, method="GET", payload=None, extra_headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        h = {"Content-Type": "application/json", **(extra_headers or {})}
        return fetch(CONSOLE_HOST, CONSOLE_PORT, f"/api/proxy{path}", method=method, data=data, headers=h)

    st, body, _, _ = proxy("/me")
    me = json.loads(body) if st == 200 else {}
    # Con D-MFA-01 (control plane: amr OR acr>=2; console: step-up con
    # acr_values), el login REAL de Keycloak 26 llega a sesión MFA-verificada.
    check("GET /api/proxy/me → 200 whoami real (login real KC26 + step-up MFA)",
          st == 200 and me.get("email") == ADMIN_EMAIL, f"HTTP {st} {body[:160]}")
    check("whoami: rol org_admin", any(r["role_key"] == "org_admin" for r in me.get("roles", [])))
    check("whoami MFA verificada (acr>=2 tras el step-up real)", me.get("mfa_verified") is True, body[:200])

    st, body, _, _ = proxy("/budgets/overview")
    ov = json.loads(body) if st == 200 else {}
    initial_budget = ov.get("organization", {}).get("budget_cents")
    check("GET /api/proxy/budgets/overview → 200 datos reales",
          st == 200 and isinstance(initial_budget, int), f"HTTP {st} {body[:200]}")
    # El overview solo lista departamentos CON presupuesto asignado; el alta
    # crea los departamentos sin presupuesto → unassigned == budget org.
    check("overview coherente: sin presupuestos de depto, todo el org budget sin asignar",
          ov.get("unassigned_cents") == initial_budget and ov.get("dept_budgets_total_cents") == 0,
          body[:300])

    st, body, _, _ = proxy("/audit/events?limit=5")
    au = json.loads(body) if st == 200 else {}
    check("GET /api/proxy/audit/events → 200 con eventos reales", st == 200 and au.get("total", 0) >= 1, f"HTTP {st} {body[:160]}")

    # CSRF negativo: mutación SIN header → 403 csrf_required del control plane
    st, body, _, _ = proxy("/budgets/org", method="PUT", payload={"amount_cents": initial_budget, "period": "monthly"})
    err = (json.loads(body) or {}).get("error", {})
    check("mutación sin X-CSRF-Token → 403 csrf_required (lo valida el control plane)",
          st == 403 and err.get("reason") == "csrf_required", f"HTTP {st} {body[:160]}")

    # Mutación CON CSRF → real
    st, body, _, _ = proxy("/budgets/org", method="PUT",
                           payload={"amount_cents": initial_budget + 100000, "period": "monthly"},
                           extra_headers={"X-CSRF-Token": COOKIES.get("ta_csrf", "")})
    check("mutación con X-CSRF-Token → 200 (presupuesto org actualizado)", st == 200, f"HTTP {st} {body[:200]}")

    st, body, _, _ = proxy("/budgets/overview")
    ov = json.loads(body) if st == 200 else {}
    check("el nuevo presupuesto se refleja en el overview",
          ov.get("organization", {}).get("budget_cents") == initial_budget + 100000, body[:200])

    # Spec 012 ACTIVADA: /users y /branding ya existen (antes eran 404 de
    # degradación honesta). Ahora deben devolver 200 con datos reales del
    # tenant: directorio (aquí: solo el admin) y branding default provider.
    st, body, _, _ = proxy("/users")
    users_ok = st == 200 and any(
        u.get("email") == ADMIN_EMAIL for u in json.loads(body).get("users", []))
    check("GET /api/proxy/users → 200 con directorio real (spec 012)",
          users_ok, f"HTTP {st} {body[:160]}")
    st, body, _, _ = proxy("/branding")
    branding_ok = st == 200 and json.loads(body).get("source") == "provider"
    check("GET /api/proxy/branding → 200 default provider (spec 012)",
          branding_ok, f"HTTP {st} {body[:160]}")

    # Página HTML con sesión (el shell renderiza la puerta MFA — 200 real)
    st, body, _, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, "/overview")
    check("GET /overview con sesión → 200 HTML (puerta MFA renderizada)", st == 200, f"HTTP {st}")

    # Sin sesión → redirect a /login
    saved = dict(COOKIES)
    COOKIES.clear()
    st, _, hdrs, _ = fetch(CONSOLE_HOST, CONSOLE_PORT, "/overview", follow=False)
    check("GET /overview sin sesión → redirect a /login",
          st in (301, 302, 303, 307) and "/login" in hdrs.get("location", ""), f"HTTP {st}")
    COOKIES.update(saved)

    print()
    if failures:
        print(f"SMOKE FAIL — {len(failures)} fallo(s): {failures}")
        return 1
    print("SMOKE PASS — flujo REAL completo: PKCE S256 + one-time password + enrolamiento "
          "TOTP real + step-up MFA (acr) + sesión admin MFA-verificada + datos reales por el proxy")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

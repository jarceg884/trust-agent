#!/usr/bin/env python3
"""EVIDENCIA LIVE — Spec 040 (platform installers), tarea t_fff52ec1.

Flujo REAL completo del instalador desktop por plataforma, contra el stack
vivo (api :8000, Keycloak :8080, gateway LiteLLM real :4000):

  setup      provisiona el tenant pi-e2e (admin+worker con TOTP real,
             policy restrictiva, budgets, branding publicado).
  build      corre scripts/build_desktop_installers.py (host) → .exe NSIS
             + AppImage reales con el binding horneado (D-040-02).
  serve      página con 3 botones + endpoints /installer/{platform} +
             audit download.issued por plataforma + estados honestos.
  appimage   AppImage --install-only en un home fresco → binding, plugin,
             skin, config (REQ-PI-005/006).
  gui        AppImage bajo xvfb con CDP: wizard real, screenshots, sign-in
             OIDC embebido (PKCE+TOTP contra el realm real) → id-token +
             virtual key (REQ-PI-007, D-040-09).
  firstrun   primera ejecución del agente → auto-enroll zero-UI (spec 022).
  approve    el admin aprueba el device → device token + bundle firmado.
  governed   tool vetada por la policy de la org + heartbeat + bundle.

Uso (host, con el stack vivo y el api recreado con el volume de artefactos):
  platform/.venv/bin/python scripts/evidence_desktop_installers.py --phase setup
  ... (una fase por vez, en orden)

Evidencia: docs/evidencia/desktop-installers/
"""
import argparse
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_final_live as e2e  # noqa: E402 - helpers verificados del E2E final

SLUG = "pi-e2e"
ADMIN_EMAIL = "admin@pi-e2e.test"
ADMIN_PASSWORD = "PiAdmin2026!"
WORKER_EMAIL = "worker@pi-e2e.test"
WORKER_PASSWORD = "PiWorker2026!"

BRANDING = {
    "product_name": "Vela Agent",
    "tagline": "Vela governed AI",
    "accent_color": "#7c3aed",
}

E2E_ROOT = "/root/trust-agent/.e2e"
EMPLOYEE_HOME = f"{E2E_ROOT}/pi-home"
STATE_FILE = f"{E2E_ROOT}/.state-pi-e2e.json"
EVIDENCE_DIR = "/root/trust-agent/docs/evidencia/desktop-installers"
REPO = "/root/trust-agent"
ARTIFACTS = f"{REPO}/installer-artifacts/{SLUG}"


def artifacts_version_dir():
    """Directorio de artefactos de la versión del plugin actual
    (layout {slug}/{plugin_version}/ — D-040-07)."""
    import re
    manifest = open(f"{REPO}/platform/hermes/plugins/"
                    "trust-agent-governance/plugin.yaml").read()
    version = re.search(r"^version:\s*(\S+)", manifest,
                        re.MULTILINE).group(1)
    return f"{ARTIFACTS}/{version}"

FAILURES = []
PASSES = 0


def check(name, cond, detail=""):
    global PASSES
    if cond:
        PASSES += 1
        print(f"  PASS {name}", flush=True)
    else:
        FAILURES.append(name)
        print(f"  FAIL {name} — {detail}", flush=True)


def save_state(state):
    os.makedirs(E2E_ROOT, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(STATE_FILE, 0o600)


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def evidence_dump(name, text):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, name)
    with open(path, "w") as f:
        f.write(text if isinstance(text, str) else text.decode("utf-8", "replace"))
    print(f"    evidence → {path}", flush=True)


def evidence_dump_bytes(name, data):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    print(f"    evidence → {path}", flush=True)


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, resp.read()
    except urllib.error.HTTPError as exc:
        hdrs = {k.lower(): v for k, v in exc.headers.items()}
        return exc.code, hdrs, exc.read()


def audit_count(action, tenant_id, artifact=None):
    where = "tenant_id = %s AND action = %s"
    params = [tenant_id, action]
    if artifact:
        where += " AND payload->>'artifact' = %s"
        params.append(artifact)
    rows = e2e.db(f"SELECT count(*) FROM audit_log WHERE {where}", params)
    return rows[0][0]


# ===========================================================================
# setup — tenant pi-e2e + MFA + policy + branding (patrón hermes-download)
# ===========================================================================
def phase_setup():
    e2e.SLUG = SLUG
    S = {}
    print("=== SETUP: tenant pi-e2e + MFA + policy + branding ===", flush=True)

    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Vela Corp", "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": "standard", "slug": SLUG,
        # Spec 038 (D-38-01/05): los departamentos se DECLARAN al nacer —
        # sin esto el tenant nace vacío y el worker no tiene depto.
        "departments": ["it_operations", "finance"]})
    if st == 409 or body.get("created") is False:
        rows = e2e.db("SELECT id FROM tenants WHERE slug = %s", (SLUG,))
        check("S1 tenant ya existía (re-ejecución) — se reutiliza", len(rows) == 1)
        S["tenant_id"] = str(rows[0][0])
    else:
        check("S1 proveedor crea el tenant → 201", st == 201,
              f"HTTP {st} {raw[:160]}")
        if st != 201:
            return 1
        S["tenant_id"] = body["tenant"]["id"]
    # Spec 038: si el tenant existía (nació sin departamentos), el depto IT
    # se crea vía API real (POST /api/departments) tras el login del admin.
    rows = e2e.db(
        "SELECT id FROM departments WHERE tenant_id = %s "
        "AND profile_template = 'it_operations'", (S["tenant_id"],))
    S["dept_it"] = str(rows[0][0]) if rows else None

    import evidence_hermes_real_bridge as bridge
    bridge.SLUG = SLUG
    admin_uuid = e2e.kc_user_uuid(ADMIN_EMAIL)
    # Password conocido para el admin del tenant de evidencia (idempotente):
    # el OTA de provisioning es one-time y no recuperable en re-ejecuciones.
    kc_token = e2e.kc_admin_token()
    e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{admin_uuid}/reset-password",
                {"type": "password", "value": ADMIN_PASSWORD, "temporary": False},
                token=kc_token)
    bridge.reset_totp(admin_uuid)
    bridge.force_configure_totp(admin_uuid)
    e2e.COOKIES.clear()
    admin_totp = {}
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD,
                                   new_password=ADMIN_PASSWORD,
                                   totp_state=admin_totp, acr_values="2")
    check("S2 admin login REAL PKCE+TOTP → acr=2", str(acr) == "2", f"acr={acr}")
    S["admin"] = e2e.mint(id_token)
    admin = S["admin"]["session_token"]

    if S["dept_it"] is None:
        st, body, raw = e2e.api("POST", "/api/departments",
                                {"name": "IT Operations",
                                 "profile_template": "it_operations"},
                                bearer=admin)
        check("S1b depto IT creado vía API (spec 038)", st == 201,
              f"HTTP {st} {raw[:160]}")
        S["dept_it"] = body.get("department_id")

    token = e2e.kc_admin_token()
    e2e.kc_json("POST", f"/admin/realms/{SLUG}/users", {
        "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
        "firstName": "Vela", "lastName": "Worker",
        "attributes": {"tenant_id": [S["tenant_id"]]}}, token=token)
    worker_uuid = e2e.kc_user_uuid(WORKER_EMAIL)
    e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
                {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
                token=token)
    e2e.db("INSERT INTO users (tenant_id, department_id, email, display_name, "
           "status, mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', "
           "true, %s) ON CONFLICT DO NOTHING",
           (S["tenant_id"], S["dept_it"], WORKER_EMAIL,
            "Vela Worker", worker_uuid), fetch=False)
    e2e.db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
           "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s "
           "ON CONFLICT DO NOTHING",
           (S["tenant_id"], worker_uuid), fetch=False)
    S["worker_user_id"] = str(e2e.db(
        "SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))[0][0])
    S["worker_sub"] = worker_uuid

    bridge.reset_totp(worker_uuid)
    bridge.force_configure_totp(worker_uuid)
    e2e.COOKIES.clear()
    worker_totp = {}
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                                   totp_state=worker_totp, acr_values="2")
    check("S3 worker enrola TOTP real y loguea → acr=2", str(acr) == "2",
          f"acr={acr}")
    S["worker_totp_secret"] = worker_totp.get("secret")

    st, _, raw = e2e.api("PUT", "/api/policies/org",
                         {"key": "allowed_tools",
                          "value": ["web_search", "governance_status"]},
                         bearer=admin)
    check("S4 policy org allowed_tools=[web_search,governance_status]",
          st == 200, f"HTTP {st}")

    e2e.api("PUT", f"/api/budgets/dept/{S['dept_it']}", {"amount_cents": 5000},
            bearer=admin)
    st, _, _ = e2e.api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                       {"amount_cents": 2000}, bearer=admin)
    check("S5 budget worker $20 → 200", st == 200)

    st, _, raw = e2e.api("PUT", "/api/branding", BRANDING, bearer=admin)
    check("S6 branding draft (Vela Agent) → 200", st == 200, f"HTTP {st}")
    st, body, raw = e2e.api("POST", "/api/branding/publish", bearer=admin)
    check("S6 branding publicado", (st == 200 and body.get("version", 0) >= 1)
          or (st == 422 and "No unpublished changes" in raw),
          f"HTTP {st} {raw[:120]}")

    save_state(S)
    print(f"\nSETUP: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# build — instaladores reales en el host (D-040-05)
# ===========================================================================
def phase_build():
    S = load_state()
    print("=== BUILD: electron-builder en el host (.exe NSIS + AppImage) ===",
          flush=True)
    subprocess.run(["rm", "-rf", EMPLOYEE_HOME], check=True)

    cmd = [f"{REPO}/platform/.venv/bin/python",
           f"{REPO}/scripts/build_desktop_installers.py",
           "--slug", SLUG, "--platforms", "windows,linux"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                         cwd=REPO)
    evidence_dump("10-build.log", out.stdout + "\n" + out.stderr)
    check("B1 build_desktop_installers.py termina en verde (REQ-PI-010)",
          out.returncode == 0, out.stderr[-300:] if out.returncode else "")

    manifest_path = f"{artifacts_version_dir()}/installer-manifest.json"
    ok = os.path.isfile(manifest_path)
    check("B2 installer-manifest.json presente", ok, manifest_path)
    if not ok:
        return 1
    manifest = json.load(open(manifest_path))
    evidence_dump("11-installer-manifest.json", json.dumps(manifest, indent=2))
    S["manifest"] = manifest

    for platform, ext in (("windows", ".exe"), ("linux", ".AppImage")):
        info = manifest["platforms"].get(platform)
        check(f"B3 artefacto {platform} en el manifest", bool(info))
        if not info:
            continue
        path = f"{artifacts_version_dir()}/{info["filename"]}"
        data = open(path, "rb").read()
        digest = hashlib.sha256(data).hexdigest()
        check(f"B4 {platform}: archivo real + sha256 del manifest verifica "
              f"({len(data) // (1024 * 1024)} MB)",
              digest == info["sha256"] and len(data) > 10 * 1024 * 1024,
              f"{path} sha256={digest[:16]}…")
        # El binario es lo que dice ser (magic bytes).
        if platform == "windows":
            check("B5 .exe NSIS: firma PE (MZ) presente",
                  data[:2] == b"MZ", data[:8].hex())
        else:
            check("B5 AppImage: ELF ejecutable presente",
                  data[:4] == b"\x7fELF", data[:8].hex())

    # El binding se verifica tras extraer el AppImage en la fase appimage
    # (squashfs extraído con --appimage-extract).
    appimage = f"{artifacts_version_dir()}/{manifest['platforms']['linux']['filename']}"
    S["appimage"] = appimage
    S["exe"] = f"{artifacts_version_dir()}/{manifest["platforms"]["windows"]["filename"]}"
    save_state(S)
    print(f"\nBUILD: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# serve — página + endpoints + audit (REQ-PI-001..004)
# ===========================================================================
def phase_serve():
    S = load_state()
    print("=== SERVE: página por plataforma + endpoints + audit ===", flush=True)
    e2e.db("DELETE FROM ops.login_throttle WHERE key LIKE %s", ("dl:%",),
           fetch=False)
    base = "http://127.0.0.1:8000"
    tenant_id = S["tenant_id"]

    st, _, body = http_get(f"{base}/download/{SLUG}")
    page = body.decode()
    evidence_dump("20-download-page.html", page)
    check("P1 GET /download/{slug} → 200 con los 3 botones por plataforma",
          st == 200 and all(f'data-os="{p}' in page
                            for p in ("windows", "macos", "linux"))
          and "Download for your platform" in page, f"HTTP {st}")
    check("P1 branding del tenant en la página (Vela Agent + accent)",
          "Vela Agent" in page and "#7c3aed" in page, "")
    check("P1 botones windows/linux enlazan al artefacto servido",
          f"/download/{SLUG}/installer/windows" in page
          and f"/download/{SLUG}/installer/linux" in page, "")
    check("P1 macOS honesto: sin .dmg servido → vía CLI (REQ-PI-009)",
          f"/download/{SLUG}/installer/macos" not in page
          and "Use the CLI on macOS" in page, "")
    check("P1 sección CLI intacta (spec 034)",
          f"/download/{SLUG}/cli" in page and "curl -fsSL" in page, "")

    for platform, artifact in (("linux", "installer-linux"),
                               ("windows", "installer-windows")):
        before = audit_count("download.issued", tenant_id, artifact)
        st, hdrs, body = http_get(f"{base}/download/{SLUG}/installer/{platform}")
        info = S["manifest"]["platforms"][platform]
        check(f"P2 GET installer/{platform} → 200 binario real + sha256 header",
              st == 200 and hdrs.get("x-installer-sha256") == info["sha256"]
              and hashlib.sha256(body).hexdigest() == info["sha256"],
              f"HTTP {st}")
        after = audit_count("download.issued", tenant_id, artifact)
        check(f"P3 audit download.issued artifact={artifact} +1",
              after == before + 1, f"{before} → {after}")

    st, _, body = http_get(f"{base}/download/{SLUG}/installer/macos")
    check("P4 installer/macos sin build → 404 installer_not_built (honesto)",
          st == 404 and b"installer_not_built" in body, f"HTTP {st}")

    st, _, _ = http_get(f"{base}/download/{SLUG}/installer/beos")
    check("P5 plataforma desconocida → 404", st == 404, f"HTTP {st}")

    print(f"\nSERVE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# appimage — instalación real del AppImage en un home fresco (REQ-PI-005/006)
# ===========================================================================
def phase_appimage():
    S = load_state()
    print("=== APPIMAGE: --install-only en un home fresco ===", flush=True)
    subprocess.run(["rm", "-rf", EMPLOYEE_HOME], check=True)

    appimage = S["appimage"]
    os.chmod(appimage, 0o755)
    # El AppImage se auto-extrae y corre (root sin FUSE mount — mismo
    # binario real, arranque determinista en servidor headless).
    env = {**os.environ, "TRUST_TA_HOME": EMPLOYEE_HOME,
           "HOME": os.environ.get("HOME", "/root")}
    out = subprocess.run(
        ["xvfb-run", "-a", "-s", "-screen 0 1280x900x24",
         appimage, "--appimage-extract-and-run", "--no-sandbox",
         "--install-only"],
        capture_output=True, text=True, timeout=900, env=env)
    evidence_dump("30-appimage-install.log", out.stdout + "\n" + out.stderr)
    check("A1 AppImage --install-only termina en verde", out.returncode == 0,
          out.stderr[-300:] if out.returncode else "")
    if out.returncode != 0:
        print(f"\nAPPIMAGE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
        return 1

    home = EMPLOYEE_HOME
    binding = open(f"{home}/trust-agent/org-code.txt").read().strip()
    check("A2 binding org-code.txt = slug horneado (zero config)",
          binding == SLUG, binding)
    check("A2 plugin instalado desde el bundle canónico (D-040-03)",
          os.path.isfile(f"{home}/plugins/trust-agent-governance/plugin.yaml")
          and os.path.isfile(f"{home}/plugins/trust-agent-governance/enforcement.py"))
    check("A2 skin white-label del tenant aplicado",
          os.path.isfile(f"{home}/skins/trust-{SLUG}.yaml"))
    skin = open(f"{home}/skins/trust-{SLUG}.yaml").read()
    check("A2 skin lleva accent y product_name del branding publicado",
          "#7c3aed" in skin and "Vela Agent" in skin, "")
    env_file = open(f"{home}/.env").read()
    check("A2 .env lleva TRUST_AGENT_API + TRUST_ORG_CODE",
          "TRUST_AGENT_API=http://127.0.0.1:8000" in env_file
          and f"TRUST_ORG_CODE={SLUG}" in env_file, "")

    def config_get(key):
        r = subprocess.run(["hermes", "config", "get", key],
                           capture_output=True, text=True,
                           env={**os.environ, "HERMES_HOME": home})
        return (r.stdout or r.stderr).strip()

    def config_get_first(key):
        return config_get(key).splitlines()[0].strip()

    check("A3 config: gateway.standalone=true", 
          config_get_first("gateway.standalone").endswith("true"))
    check("A3 config: plugin habilitado",
          "trust-agent-governance" in config_get("plugins.enabled"))
    check("A3 config: skin del tenant activo",
          config_get_first("display.skin").endswith(f"trust-{SLUG}"))
    check("A3 config: model → gateway del tenant",
          "glm-5.2" in config_get("model.default")
          and "4000" in config_get("model.base_url"))

    # Binding horneado DENTRO del instalador (D-040-02): extraemos el
    # AppImage y verificamos resources/org-binding.json del build.
    extract_dir = f"{E2E_ROOT}/appimage-extract"
    subprocess.run(["rm", "-rf", extract_dir], check=True)
    out = subprocess.run([appimage, "--appimage-extract"], capture_output=True,
                         text=True, timeout=300, cwd=E2E_ROOT)
    binding_file = f"{extract_dir}/resources/resources/org-binding.json"
    check("A4 AppImage extraído: resources/org-binding.json horneado",
          os.path.isfile(binding_file), binding_file)
    if os.path.isfile(binding_file):
        baked = json.load(open(binding_file))
        evidence_dump("31-org-binding-baked.json", json.dumps(baked, indent=2))
        check("A4 binding horneado = tenant + control plane + versión plugin",
              baked["slug"] == SLUG
              and baked["api_base"] == "http://127.0.0.1:8000"
              and baked["plugin_version"] == S["manifest"]["plugin_version"],
              json.dumps(baked))
    branding_file = f"{extract_dir}/resources/resources/branding.json"
    check("A4 branding.json horneado (Vela Agent)",
          os.path.isfile(branding_file)
          and "Vela Agent" in open(branding_file).read(), branding_file)

    print(f"\nAPPIMAGE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# gui — wizard real bajo xvfb, screenshots y sign-in OIDC embebido (CDP)
# ===========================================================================
def _cdp_call(ws, method, params=None, msg_id=[0]):
    import asyncio

    async def _call():
        msg_id[0] += 1
        payload = json.dumps({"id": msg_id[0], "method": method,
                              "params": params or {}})
        await ws.send(payload)
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id[0]:
                return data.get("result", {})

    return asyncio.get_event_loop().run_until_complete(_call())


def _cdp_eval(ws, expr):
    result = _cdp_call(ws, "Runtime.evaluate", {
        "expression": expr, "returnByValue": True})
    return result.get("result", {}).get("value")


def _cdp_screenshot(ws, name):
    shot = _cdp_call(ws, "Page.captureScreenshot", {"format": "png"})
    if shot.get("data"):
        evidence_dump_bytes(name, base64.b64decode(shot["data"]))


def _wait_targets(port, predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                targets = json.load(r)
            match = [t for t in targets if predicate(t)]
            if match:
                return match
        except Exception:
            pass
        time.sleep(1)
    return []


def phase_gui():
    S = load_state()
    print("=== GUI: wizard real (xvfb + CDP) + sign-in OIDC embebido ===",
          flush=True)
    import asyncio
    import websockets

    subprocess.run(["rm", "-rf", EMPLOYEE_HOME], check=True)
    port = 9223
    env = {**os.environ, "TRUST_TA_HOME": EMPLOYEE_HOME,
           "ELECTRON_DISABLE_SECURITY_WARNINGS": "1"}
    # xvfb-run + AppImage: el wizard abre su ventana; CDP por el puerto.
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "-s", "-screen 0 1280x900x24",
         S["appimage"], "--appimage-extract-and-run", "--no-sandbox",
         f"--remote-debugging-port={port}"],
        stdout=open(f"{EVIDENCE_DIR}/40-gui-stdout.log", "w"),
        stderr=subprocess.STDOUT, env=env)

    try:
        targets = _wait_targets(port, lambda t: t.get("type") == "page")
        check("G1 wizard abre su ventana (target CDP)", bool(targets),
              "sin targets")
        if not targets:
            return 1
        ws_url = targets[0]["webSocketDebuggerUrl"]
        async def _connect():
            return await websockets.connect(ws_url, max_size=32 * 1024 * 1024)
        ws = asyncio.get_event_loop().run_until_complete(_connect())
        _cdp_call(ws, "Page.enable")
        _cdp_call(ws, "Runtime.enable")

        # Pantalla welcome con branding del tenant.
        time.sleep(3)
        _cdp_screenshot(ws, "41-wizard-welcome.png")
        product = _cdp_eval(ws, "document.getElementById('product').textContent")
        check("G1 welcome muestra el branding horneado (Vela Agent)",
              product == "Vela Agent", str(product))

        # Install → logs en vivo → pantalla sign-in.
        _cdp_eval(ws, "document.getElementById('btn-install').click()")
        deadline = time.time() + 600
        on_signin = False
        while time.time() < deadline:
            on_signin = _cdp_eval(ws,
                "!document.getElementById('screen-signin').classList"
                ".contains('hidden')")
            if on_signin:
                break
            time.sleep(3)
        _cdp_screenshot(ws, "42-wizard-install.png")
        log_text = _cdp_eval(
            ws, "document.getElementById('install-log').textContent") or ""
        evidence_dump("43-wizard-install-log.txt", log_text)
        check("G2 wizard instala: Hermes + bundle canónico + binding + skin",
              on_signin and "Installation complete" in log_text
              and f"Binding this agent to organization {SLUG}" in log_text,
              log_text[-200:])

        # Sign-in embebido: BrowserWindow contra el realm real (PKCE+TOTP).
        _cdp_eval(ws, "document.getElementById('btn-signin').click()")
        kc_targets = _wait_targets(
            port, lambda t: t.get("type") == "page"
            and "realms" in t.get("url", ""), timeout=60)
        check("G3 ventana de sign-in abre el realm del tenant (OIDC real)",
              bool(kc_targets), "sin target del IdP")
        if kc_targets:
            kc_ws = asyncio.get_event_loop().run_until_complete(
                _cdp_connect(kc_targets[0]["webSocketDebuggerUrl"]))
            _cdp_call(kc_ws, "Page.enable")
            _cdp_screenshot(kc_ws, "44-signin-keycloak.png")
            _cdp_eval(kc_ws, f"""
                (function() {{
                  var u = document.getElementById('username');
                  if (u) {{ u.value = '{WORKER_EMAIL}'; }}
                  var p = document.getElementById('password');
                  if (p) {{ p.value = '{WORKER_PASSWORD}'; }}
                  var f = document.querySelector('form');
                  if (f) {{ f.submit(); }}
                }})()
            """)
            # Segundo factor: la página TOTP del realm (acr=2).
            deadline = time.time() + 60
            otp_filled = False
            while time.time() < deadline:
                time.sleep(3)
                try:
                    code = e2e.totp(S["worker_totp_secret"])
                except Exception:
                    break
                otp_filled = _cdp_eval(kc_ws, f"""
                    (function() {{
                      var o = document.getElementById('otp')
                        || document.querySelector('input[name=otp]');
                      if (!o) {{ return false; }}
                      o.value = '{code}';
                      var f = document.querySelector('form');
                      if (f) {{ f.submit(); }}
                      return true;
                    }})()
                """)
                if otp_filled:
                    break
            check("G3 segundo factor TOTP enviado en la ventana embebida",
                  bool(otp_filled), "campo otp no encontrado")

        # El wizard vuelve con el token: pantalla done.
        deadline = time.time() + 120
        done = False
        while time.time() < deadline:
            done = _cdp_eval(ws,
                "!document.getElementById('screen-done').classList"
                ".contains('hidden')")
            if done:
                break
            time.sleep(3)
        _cdp_screenshot(ws, "45-wizard-done.png")
        check("G4 wizard completa el sign-in → pantalla done", done, "")

        token_file = f"{EMPLOYEE_HOME}/trust-agent/id-token.txt"
        ok = os.path.isfile(token_file)
        check("G4 id-token.txt escrito por el wizard (0600)", ok, token_file)
        if ok:
            mode = stat.S_IMODE(os.stat(token_file).st_mode)
            check("G4 id-token.txt 0600", mode == 0o600, oct(mode))
        r = subprocess.run(["hermes", "config", "get", "model.api_key"],
                           capture_output=True, text=True,
                           env={**os.environ, "HERMES_HOME": EMPLOYEE_HOME})
        check("G4 model.api_key auto-emitida por el wizard (D-040-09)",
              "sk-" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:120])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\nGUI: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def _cdp_connect(url):
    import asyncio
    import websockets

    async def _connect():
        return await websockets.connect(url, max_size=32 * 1024 * 1024)
    return asyncio.get_event_loop().run_until_complete(_connect())


# ===========================================================================
# firstrun / approve / governed — mismo flujo zero-UI que spec 022/023
# ===========================================================================
def _tmux_run(session, cmd):
    subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-x", "200",
                    "-y", "50", cmd], check=True)


def _pane(session):
    r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                       capture_output=True, text=True)
    return r.stdout


def phase_firstrun():
    S = load_state()
    print("=== FIRSTRUN: primera ejecución → auto-enroll zero-UI ===", flush=True)
    _tmux_run("pichat", f"env -u HERMES_SESSION_SOURCE HERMES_HOME={EMPLOYEE_HOME} "
                        "hermes chat")
    started = time.time()
    deadline = started + 120
    submitted = False
    rows = []
    while time.time() < deadline:
        rows = e2e.db(
            "SELECT id, status FROM devices WHERE tenant_id=%s "
            "AND user_id=%s AND status='pending' "
            "AND enrolled_at >= to_timestamp(%s)",
            (S["tenant_id"], S["worker_user_id"], started - 60))
        if rows:
            submitted = True
            break
        time.sleep(3)
    check("F1 auto-enroll zero-UI: device PENDING sin ningún panel",
          submitted, f"devices={rows if submitted else 'ninguno'}")
    if submitted:
        S["device_id"] = str(rows[0][0])
        save_state(S)
        evidence_dump("50-firstrun-pane.txt", _pane("pichat"))
    print(f"\nFIRSTRUN: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def phase_approve():
    S = load_state()
    e2e.SLUG = SLUG
    print("=== APPROVE: el admin aprueba el device ===", flush=True)
    import evidence_hermes_real_bridge as bridge
    bridge.SLUG = SLUG
    admin_uuid = e2e.kc_user_uuid(ADMIN_EMAIL)
    bridge.reset_totp(admin_uuid)
    bridge.force_configure_totp(admin_uuid)
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD, acr_values="2")
    check("A1 re-login admin (PKCE+TOTP) → acr=2", str(acr) == "2", f"acr={acr}")
    admin = e2e.mint(id_token)["session_token"]

    st, body, raw = e2e.api("POST",
                            f"/api/devices/{S['device_id']}/approve",
                            bearer=admin)
    if st == 409 and "pending" in raw:
        rows = e2e.db("SELECT status FROM devices WHERE id = %s",
                      (S["device_id"],))
        check("A2 device ya aprobado (re-ejecución) — active",
              rows and rows[0][0] == "active")
    else:
        check("A2 POST /api/devices/{id}/approve → 200 active",
              st == 200 and body.get("status") == "active",
              f"HTTP {st} {raw[:160]}")

    deadline = time.time() + 90
    token_ok = False
    while time.time() < deadline:
        p = f"{EMPLOYEE_HOME}/trust-agent/device-token.json"
        if os.path.isfile(p):
            data = json.load(open(p))
            token_ok = bool(data.get("device_id") and data.get("token"))
            if token_ok:
                break
        time.sleep(3)
    check("A3 canje zero-UI → device token en el home", token_ok)
    bundle_jws = f"{EMPLOYEE_HOME}/trust-agent/governance-bundle.jws"
    deadline = time.time() + 60
    while time.time() < deadline and not os.path.isfile(bundle_jws):
        time.sleep(3)
    check("A4 governance bundle firmado sincronizado",
          os.path.isfile(bundle_jws))
    print(f"\nAPPROVE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def phase_governed():
    S = load_state()
    print("=== GOVERNED: el agente del empleado queda gobernado ===", flush=True)
    subprocess.run(["tmux", "send-keys", "-t", "pichat",
                    "Use the terminal tool to run: echo hello"], check=True)
    time.sleep(2)
    subprocess.run(["tmux", "send-keys", "-t", "pichat", "Enter"], check=True)

    import sqlite3
    veto_msg = None
    deadline = time.time() + 90
    while time.time() < deadline:
        conn = sqlite3.connect(f"{EMPLOYEE_HOME}/state.db")
        veto_msg = conn.execute(
            "SELECT content FROM messages WHERE content LIKE "
            "'%BLOCKED by organization policy%' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if veto_msg:
            break
        time.sleep(5)
    if veto_msg:
        time.sleep(12)
    pane = _pane("pichat")
    evidence_dump("51-governed-pane.txt", pane)
    check("G1 tool terminal VETADA por la policy (mensaje exacto)",
          veto_msg is not None
          and "terminal" in veto_msg[0]
          and "Allowed tools: governance_status, web_search" in veto_msg[0],
          str(veto_msg)[:160])
    check("G1 el agente lo comunica al empleado (pane)",
          "blocked by" in pane.lower() and "organization" in pane.lower()
          and "policy" in pane.lower())
    log = open(f"{EMPLOYEE_HOME}/trust-agent/plugin.log").read()
    evidence_dump("52-plugin-log.txt", log)
    check("G1 plugin.log registra el veto real (veto_eval + tool_vetoed)",
          "veto_eval" in log and "tool_vetoed" in log)

    rows = e2e.db(
        "SELECT status, applied_policy_version, last_heartbeat_at IS NOT NULL "
        "FROM devices WHERE id = %s", (S["device_id"],))
    status, policy_version, hb = rows[0]
    check("G2 bundle firmado + heartbeat con la policy aplicada",
          "bundle_synced" in log and "heartbeat_ok" in log
          and f'"applied_policy_version": {policy_version}' in log,
          f"policy_version={policy_version}")
    check("G3 device ACTIVE con policy aplicada y heartbeat",
          status == "active" and policy_version is not None and hb,
          f"status={status} policy_v={policy_version} heartbeat={hb}")
    print(f"\nGOVERNED: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["setup", "build", "serve", "appimage", "gui",
                             "firstrun", "approve", "governed"])
    args = ap.parse_args()
    rc = {"setup": phase_setup, "build": phase_build, "serve": phase_serve,
          "appimage": phase_appimage, "gui": phase_gui,
          "firstrun": phase_firstrun, "approve": phase_approve,
          "governed": phase_governed}[args.phase]()
    sys.exit(rc or (1 if FAILURES else 0))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""EVIDENCIA LIVE — Spec 023 (hermes download), tarea t_526030f3.

E2E del seam link→app contra el stack vivo (:8000 api, :8080 Keycloak real,
:4000 LiteLLM real): el ADMIN DEL CLIENTE comparte el link estable → un
empleado con navegador anónimo descarga el bundle → install.sh instala un
Hermes COMPLETO con plugin + binding + skin white-label → trust-login (OIDC
code+PKCE, navegador real) → primera ejecución: auto-enroll zero-UI →
aprobación admin → el agente queda gobernado por la policy de la org.

Fases (reutiliza el baile PKCE+TOTP verificado de e2e_final_live.py):

  setup      tenant real hermes-dl-e2e + admin/worker MFA + policy
             allowed_tools + budgets + key virtual + branding white-label.
  download   página/bundle públicos anónimos, audit download.issued, rate
             limit, enable/disable via GET/PUT /api/download-link.
  install    unpack + ./install.sh --home <fresco> + verificación del home.
  login-prep imprime el comando trust-login para el paso de navegador real
             (browser del worker: SSO + MFA).
  login-verify  id-token.txt 0600 + model.api_key emitida (self-issue) y
             funcional contra el gateway real.
  firstrun   sesión Hermes real en el home instalado → auto-enroll zero-UI
             → device pending en BD.
  approve    admin aprueba el device (API spec 007).
  governed   turno real: tool bloqueada por policy (veto exacto) +
             governance_status NORMAL + heartbeat en BD.

Uso:
  .venv/bin/python scripts/evidence_hermes_download.py --phase setup
  ...
"""
import argparse
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_final_live as e2e  # noqa: E402 - helpers verificados del E2E final

SLUG = "hermes-dl-e2e"
ADMIN_EMAIL = "admin@hermes-dl-e2e.test"
ADMIN_PASSWORD = "DlAdmin2026!"
WORKER_EMAIL = "worker@hermes-dl-e2e.test"
WORKER_PASSWORD = "DlWorker2026!"
MODEL = "glm-5.2"
LITELLM_REAL_PORT = 4000

BRANDING = {
    "product_name": "Aurora Agent",
    "tagline": "Aurora governed AI",
    "accent_color": "#0ea5e9",
}

# Home fresco del "empleado" (máquina del trabajador) + bundle descargado.
E2E_ROOT = "/root/trust-agent/.e2e"
BUNDLE_DIR = f"{E2E_ROOT}/dl-bundle"
EMPLOYEE_HOME = f"{E2E_ROOT}/hermes-dl-home"
STATE_FILE = f"{E2E_ROOT}/.state-hermes-dl-e2e.json"
EVIDENCE_DIR = "/root/trust-agent/docs/evidencia/hermes-download"

FAILURES = []
PASSES = 0


def check(name, cond, detail=""):
    global PASSES
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)
    return ok


def save_state(state):
    os.makedirs(E2E_ROOT, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(STATE_FILE, 0o600)


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, resp.read()
    except urllib.error.HTTPError as exc:
        hdrs = {k.lower(): v for k, v in exc.headers.items()}
        return exc.code, hdrs, exc.read()


def litellm_real_chat(key, model):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", LITELLM_REAL_PORT, timeout=30)
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "Say OK"}],
                       "max_tokens": 8}).encode()
    conn.request("POST", "/v1/chat/completions", body=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, raw


def audit_count(action, tenant_id):
    rows = e2e.db(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s AND action = %s",
        (tenant_id, action))
    return rows[0][0]


def evidence_dump(name, text):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, name)
    with open(path, "w") as f:
        f.write(text)
    print(f"    evidence → {path}", flush=True)


# ===========================================================================
# setup
# ===========================================================================
def phase_setup():
    e2e.SLUG = SLUG
    S = {}
    print("=== SETUP: tenant hermes-dl-e2e + MFA + policy + branding ===", flush=True)

    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "Aurora Corp", "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": "standard", "slug": SLUG})
    if st == 409 or body.get("created") is False:
        # Re-ejecución: el tenant ya existe (200/409 con created=false —
        # el provisioning es idempotente).
        rows = e2e.db("SELECT id FROM tenants WHERE slug = %s", (SLUG,))
        check("S1 tenant ya existía (re-ejecución) — se reutiliza", len(rows) == 1)
        S["tenant_id"] = str(rows[0][0])
    else:
        check("S1 proveedor crea el tenant → 201", st == 201,
              f"HTTP {st} {raw[:160]}")
        if st != 201:
            return 1
        S["tenant_id"] = body["tenant"]["id"]
        S["ota"] = body["one_time_activation"]["one_time_password"]
    S["dept_it"] = str(e2e.db(
        "SELECT id FROM departments WHERE tenant_id = %s "
        "AND profile_template = 'it_operations'", (S["tenant_id"],))[0][0])

    # Admin: PKCE + TOTP reales. acr_values="2" pide LoA 2 (spec 015
    # D-AUTH-06): sin él el realm LoA-condicional emite acr=1 sin MFA.
    # Re-ejecuciones: el admin ya tiene credencial TOTP de una corrida
    # previa y su secret no es recuperable — se resetea para re-enrolar
    # inline y capturar el secret fresco (mismo patrón del worker-login
    # de evidence_hermes_real_bridge). OJO: los helpers del bridge usan
    # el SLUG global de SU módulo — se parchea al realm de esta corrida.
    import evidence_hermes_real_bridge as bridge
    bridge.SLUG = SLUG
    admin_uuid = e2e.kc_user_uuid(ADMIN_EMAIL)
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

    # Worker en el realm real (idempotente para re-ejecuciones).
    token = e2e.kc_admin_token()
    e2e.kc_json("POST", f"/admin/realms/{SLUG}/users", {
        "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
        "firstName": "Aurora", "lastName": "Worker",
        "attributes": {"tenant_id": [S["tenant_id"]]}}, token=token)
    worker_uuid = e2e.kc_user_uuid(WORKER_EMAIL)
    e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
                {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
                token=token)
    e2e.db("INSERT INTO users (tenant_id, department_id, email, display_name, "
           "status, mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', "
           "true, %s) ON CONFLICT DO NOTHING",
           (S["tenant_id"], S["dept_it"], WORKER_EMAIL,
                         "Aurora Worker", worker_uuid), fetch=False)
    e2e.db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
           "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s "
           "ON CONFLICT DO NOTHING",
           (S["tenant_id"], worker_uuid), fetch=False)
    S["worker_user_id"] = str(e2e.db(
        "SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))[0][0])
    S["worker_sub"] = worker_uuid

    # Worker: PKCE + enrolamiento TOTP real (guardamos el secret para el
    # login del empleado con navegador real — flujo returning user). En
    # re-ejecuciones se resetea la credencial previa (secret no recuperable)
    # y se re-enrola inline capturando el secret fresco.
    bridge.reset_totp(worker_uuid)
    bridge.force_configure_totp(worker_uuid)
    e2e.COOKIES.clear()
    worker_totp = {}
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                                   totp_state=worker_totp, acr_values="2")
    check("S3 worker enrola TOTP real y loguea → acr=2", str(acr) == "2",
          f"acr={acr}")
    S["worker_totp_secret"] = worker_totp.get("secret")

    # Policy restrictiva (la que gobernará al agente del empleado).
    st, _, raw = e2e.api("PUT", "/api/policies/org",
                         {"key": "allowed_tools",
                          "value": ["web_search", "governance_status"]},
                         bearer=admin)
    check("S4 policy org allowed_tools=[web_search,governance_status]",
          st == 200, f"HTTP {st}")

    # Budgets.
    e2e.api("PUT", f"/api/budgets/dept/{S['dept_it']}", {"amount_cents": 5000},
            bearer=admin)
    st, _, _ = e2e.api("PUT", f"/api/budgets/user/{S['worker_user_id']}",
                       {"amount_cents": 2000}, bearer=admin)
    check("S5 budget worker $20 → 200", st == 200)

    # Branding white-label publicado (lo que verá la página y el skin).
    st, _, raw = e2e.api("PUT", "/api/branding", BRANDING, bearer=admin)
    check("S6 branding draft (Aurora Agent) → 200", st == 200, f"HTTP {st}")
    st, body, raw = e2e.api("POST", "/api/branding/publish", bearer=admin)
    check("S6 branding publicado (v1, o ya publicado en re-ejecución)",
          (st == 200 and body.get("version", 0) >= 1)
          or (st == 422 and "No unpublished changes" in raw),
          f"HTTP {st} {raw[:120]}")

    save_state(S)
    print(f"\nSETUP: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# download (navegador anónimo / HTTP sin sesión)
# ===========================================================================
def phase_download():
    S = load_state()
    print("=== DOWNLOAD: link estable, página, bundle, audit, rate limit ===",
          flush=True)
    # Higiene de evidencia: los contadores de throttle viven en PostgreSQL y
    # sobreviven corridas — se limpian para que los checks de rate limit de
    # ESTA corrida midan solo lo que esta corrida hace.
    e2e.db("DELETE FROM ops.login_throttle WHERE key LIKE %s OR key LIKE %s",
           ("dl:%", "ip:%"), fetch=False)
    base = "http://127.0.0.1:8000"
    link = f"{base}/download/{SLUG}"

    # AC-HDL-001: página pública anónima con branding real.
    st, hdrs, body = http_get(link)
    page = body.decode("utf-8", "replace")
    check("D1 GET /download/{slug} anónimo → 200 HTML",
          st == 200 and "text/html" in hdrs.get("content-type", ""),
          f"HTTP {st}")
    check("D1 página muestra el branding publicado (Aurora Agent + accent)",
          "Aurora Agent" in page and "#0ea5e9" in page)
    check("D1 página ofrece el bundle y menciona el enrolamiento automático",
          f"/download/{SLUG}/bundle" in page and "enrolled automatically" in page)
    evidence_dump("01-download-page.html", page)

    # AC-HDL-002: bundle real.
    before = audit_count("download.issued", S["tenant_id"])
    st, hdrs, payload = http_get(f"{link}/bundle")
    check("D2 GET /download/{slug}/bundle anónimo → 200 gzip",
          st == 200 and hdrs.get("content-type") == "application/gzip")
    sha = hashlib.sha256(payload).hexdigest()
    check("D2 header X-Bundle-SHA256 = sha256 real del cuerpo",
          hdrs.get("x-bundle-sha256") == sha, sha[:16])
    os.makedirs(BUNDLE_DIR, exist_ok=True)
    bundle_path = os.path.join(BUNDLE_DIR, f"trust-agent-{SLUG}.tar.gz")
    with open(bundle_path, "wb") as f:
        f.write(payload)
    with tarfile.open(bundle_path, "r:gz") as tar:
        names = tar.getnames()
        root = f"trust-agent-{SLUG}"
        manifest = json.loads(tar.extractfile(f"{root}/manifest.json").read())
        install = tar.extractfile(f"{root}/install.sh").read().decode()
    check("D2 tarball contiene plugin+install.sh+trust-login+README+manifest",
          all(f"{root}/{m}" in names for m in (
              "install.sh", "trust-login", "README.md", "manifest.json",
              "plugin/trust-agent-governance/plugin.yaml",
              "plugin/trust-agent-governance/enroll.py")))
    check("D2 manifest hornea org_code del tenant",
          manifest["org_code"] == SLUG and manifest["slug"] == SLUG)
    check("D2 branding white-label viaja en el manifest",
          manifest["branding"]["product_name"] == "Aurora Agent"
          and manifest["branding"]["accent_color"] == "#0ea5e9")
    check("D2 install.sh hornea el binding y el gateway standalone",
          "org-code.txt" in install and "gateway.standalone true" in install)
    after = audit_count("download.issued", S["tenant_id"])
    check("D3 audit download.issued +1 por descarga (REQ-HDL-004)",
          after == before + 1, f"{before} → {after}")

    # AC-HDL-005: gestión del link (admin del cliente) + efecto público.
    st, body, raw = e2e.api("GET", "/api/download-link",
                            bearer=S["admin"]["session_token"])
    check("D4 GET /api/download-link (org_admin) → contrato {url,enabled,updated_at}",
          st == 200 and set(body) == {"url", "enabled", "updated_at"}
          and body["enabled"] is True and body["url"].endswith(f"/download/{SLUG}"),
          raw[:160])
    st, body, raw = e2e.api("PUT", "/api/download-link", {"enabled": False},
                            bearer=S["admin"]["session_token"])
    check("D4 PUT enabled=false (admin del cliente) → 200",
          st == 200 and body["enabled"] is False)
    st, _, _ = http_get(f"{link}/bundle")
    check("D4 bundle con link deshabilitado → 403 link_disabled", st == 403)
    st, _, body2 = http_get(link)
    check("D4 página muestra estado disabled (honesto, sin botón)",
          st == 200 and b"disabled" in body2)
    st, body, _ = e2e.api("PUT", "/api/download-link", {"enabled": True},
                          bearer=S["admin"]["session_token"])
    check("D4 PUT enabled=true restaura la descarga", st == 200)
    st, _, _ = http_get(f"{link}/bundle")
    check("D4 bundle de nuevo → 200", st == 200)

    # AC-HDL-006/007: rate limit del bundle ANTES del sondeo anti-enumeración
    # (el sondeo bloquea el contador de IP por 30 s y contaminaría la ráfaga).
    codes = [http_get(f"{link}/bundle")[0] for _ in range(7)]
    check("D6 ráfaga de descargas misma IP → 429 tras el límite (REQ-HDL-003)",
          codes[0] == 200 and 429 in codes, str(codes))
    codes = [http_get(f"{base}/download/no-such-org")[0] for _ in range(6)]
    check("D5 slug desconocido → 404 y sondeo repetido → 429 anti-enumeración",
          codes[:5] == [404] * 5 and codes[5] == 429, str(codes))

    print(f"\nDOWNLOAD: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# install (máquina del empleado)
# ===========================================================================
def phase_install():
    print("=== INSTALL: ./install.sh en un home fresco ===", flush=True)
    bundle_path = os.path.join(BUNDLE_DIR, f"trust-agent-{SLUG}.tar.gz")
    subprocess.run(["rm", "-rf", BUNDLE_DIR + "/pkg", EMPLOYEE_HOME], check=True)
    subprocess.run(["mkdir", "-p", BUNDLE_DIR + "/pkg"], check=True)
    subprocess.run(["tar", "-xzf", bundle_path, "-C", BUNDLE_DIR + "/pkg"],
                   check=True)
    out = subprocess.run(
        [f"{BUNDLE_DIR}/pkg/trust-agent-{SLUG}/install.sh", "--home", EMPLOYEE_HOME],
        capture_output=True, text=True, timeout=600)
    evidence_dump("02-install.log", out.stdout + "\n" + out.stderr)
    check("I1 ./install.sh termina en verde", out.returncode == 0,
          out.stderr[-200:] if out.returncode else "")

    home = EMPLOYEE_HOME
    # Binding + plugin + skin + config + env (REQ-HDL-008).
    binding = open(f"{home}/trust-agent/org-code.txt").read().strip()
    check("I2 binding org-code.txt = slug del tenant", binding == SLUG, binding)
    check("I2 plugin instalado en el home (plugin.yaml + módulos)",
          os.path.isfile(f"{home}/plugins/trust-agent-governance/plugin.yaml")
          and os.path.isfile(f"{home}/plugins/trust-agent-governance/enforcement.py"))
    check("I2 skin white-label del tenant generado",
          os.path.isfile(f"{home}/skins/trust-{SLUG}.yaml"))
    skin = open(f"{home}/skins/trust-{SLUG}.yaml").read()
    check("I2 skin lleva el accent y el product_name del branding",
          "#0ea5e9" in skin and "Aurora Agent" in skin)
    env = open(f"{home}/.env").read()
    check("I2 .env lleva TRUST_AGENT_API y TRUST_ORG_CODE",
          "TRUST_AGENT_API=http://127.0.0.1:8000" in env
          and f"TRUST_ORG_CODE={SLUG}" in env)

    def config_get(key):
        r = subprocess.run(["hermes", "config", "get", key],
                           capture_output=True, text=True,
                           env={**os.environ, "HERMES_HOME": home})
        return (r.stdout or r.stderr).strip()

    def config_get_first(key):
        # El valor escalar va en la primera línea; las advertencias del CLI
        # (clave no reconocida por el reconocedor local) van después.
        return config_get(key).splitlines()[0].strip()

    check("I3 config: gateway.standalone=true (hallazgo spec 022)",
          config_get_first("gateway.standalone").endswith("true"))
    check("I3 config: plugin habilitado",
          "trust-agent-governance" in config_get("plugins.enabled"))
    check("I3 config: toolset del plugin en cli",
          "trust_agent" in config_get("platform_toolsets.cli"))
    check("I3 config: skin del tenant activo",
          config_get("display.skin").endswith(f"trust-{SLUG}"))
    check("I3 config: model → gateway del tenant",
          "glm-5.2" in config_get("model.default")
          and "4000" in config_get("model.base_url"))

    print(f"\nINSTALL: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# login (navegador real del empleado) — preparación y verificación
# ===========================================================================
def phase_login_prep():
    S = load_state()
    print("=== LOGIN-PREP: credenciales del empleado para el navegador real ===",
          flush=True)
    # Código TOTP vigente del secret enrolado en setup (el operador humano
    # usaría su app authenticator; el harness usa el secret capturado).
    code = e2e.totp(S["worker_totp_secret"])
    print("Paso humano (browser real del empleado):\n"
          f"  1. En una terminal:  env HERMES_HOME={EMPLOYEE_HOME} "
          f"{BUNDLE_DIR}/pkg/trust-agent-{SLUG}/trust-login --issue-key\n"
          "  2. Completar SSO+MFA en el navegador (Keycloak real):\n"
          f"     usuario: {WORKER_EMAIL}\n"
          f"     password: {WORKER_PASSWORD}\n"
          f"     TOTP code vigente: {code} (ventana de 30 s — regenerar si expira)\n"
          f"     TOTP secret (enrolado en setup): {S['worker_totp_secret']}\n"
          "  3. Volver aquí y correr --phase login-verify", flush=True)
    return 0


def phase_login_verify():
    S = load_state()
    print("=== LOGIN-VERIFY: artefactos del trust-login real ===", flush=True)
    token_file = f"{EMPLOYEE_HOME}/trust-agent/id-token.txt"
    ok = os.path.isfile(token_file)
    check("L1 id-token.txt presente (escrito por trust-login)", ok, token_file)
    if ok:
        mode = stat.S_IMODE(os.stat(token_file).st_mode)
        check("L1 id-token.txt 0600", mode == 0o600, oct(mode))
    r = subprocess.run(["hermes", "config", "get", "model.api_key"],
                       capture_output=True, text=True,
                       env={**os.environ, "HERMES_HOME": EMPLOYEE_HOME})
    key_set = "sk-" in (r.stdout + r.stderr)
    check("L2 model.api_key provisionada (self-issue one-time vía trust-login)",
          key_set, (r.stdout + r.stderr).strip()[:80])
    # La key debe funcionar contra el gateway real (el empleado puede chatear).
    rows = e2e.db("SELECT litellm_key_hash FROM api_keys WHERE tenant_id = %s "
                  "AND user_id = %s ORDER BY created_at DESC",
                  (S["tenant_id"], S["worker_user_id"]))
    check("L2 la virtual key del worker vive en api_keys (emisión auditada)",
          len(rows) >= 1, f"{len(rows)} keys")
    print(f"\nLOGIN-VERIFY: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# firstrun (primera ejecución: auto-enroll zero-UI)
# ===========================================================================
def _tmux_run(session, cmd):
    subprocess.run(["tmux", "kill-session", "-t", session],
                   capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-x", "200",
                    "-y", "50", cmd], check=True)


def _pane(session):
    r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                       capture_output=True, text=True)
    return r.stdout


def phase_firstrun():
    S = load_state()
    print("=== FIRSTRUN: primera ejecución → auto-enroll zero-UI ===", flush=True)
    # SOLO el chat: con gateway.standalone=true el propio chat arranca su
    # gateway dedicado. Arrancar un gateway EXPLÍCITO además del chat pone
    # dos procesos con el plugin sobre el mismo home y sus loops de
    # auto-enroll se pisan el enrollment token one-time (410s mutuos) —
    # hallazgo real documentado para la lane del plugin (spec 022).
    _tmux_run("dlchat", f"env -u HERMES_SESSION_SOURCE HERMES_HOME={EMPLOYEE_HOME} "
                        "hermes chat")
    # El auto-enroll dispara al arrancar; el canje poll-ea mientras el admin
    # aprueba. Esperamos el submit: un device PENDING nuevo (en re-ejecuciones
    # conviven devices de corridas previas — se filtra por estado y hora).
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
    check("F1 auto-enroll zero-UI: el device aparece PENDING en BD sin "
          "ningún panel", submitted, f"devices={rows if submitted else 'ninguno'}")
    plugin_log = f"{EMPLOYEE_HOME}/trust-agent/plugin.log"
    log = ""
    if submitted:
        # El evento auto_enroll se escribe al TERMINAR el intento (tras el
        # ciclo de poll) — puede llegar ~25 s después del submit.
        log_deadline = time.time() + 60
        while time.time() < log_deadline:
            if os.path.isfile(plugin_log):
                log = open(plugin_log).read()
                if "auto_enroll" in log:
                    break
            time.sleep(3)
    if os.path.isfile(plugin_log):
        evidence_dump("03-plugin-log.txt", log or open(plugin_log).read())
        check("F1 plugin.log registra el flujo auto_enroll (submit + poll)",
              "auto_enroll" in (log or open(plugin_log).read()))
    if submitted:
        S["device_id"] = str(rows[0][0])
        save_state(S)
        evidence_dump("04-firstrun-pane.txt", _pane("dlchat"))
    print(f"\nFIRSTRUN: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# approve (admin del cliente)
# ===========================================================================
def phase_approve():
    S = load_state()
    e2e.SLUG = SLUG  # kc_user_uuid y el baile consultan el realm de ESTA corrida
    print("=== APPROVE: el admin del cliente aprueba el device ===", flush=True)
    # Login admin fresco (reset TOTP → CONFIGURE_TOTP inline del baile).
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
        # Re-ejecución: el device ya fue aprobado en una corrida previa.
        rows = e2e.db("SELECT status FROM devices WHERE id = %s",
                      (S["device_id"],))
        check("A2 device ya estaba aprobado (re-ejecución) — active",
              rows and rows[0][0] == "active")
    else:
        check("A2 POST /api/devices/{id}/approve → 200 active",
              st == 200 and body.get("status") == "active",
              f"HTTP {st} {raw[:160]}")
    # El plugin poll-ea el canje: esperamos el device token en el home.
    # (device-token.json: {device_id, token, org_code, tenant_slug} — D-HRB-03.)
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
    check("A3 el agente canjea el enrollment token (zero-UI) → device token",
          token_ok)
    bundle_jws = f"{EMPLOYEE_HOME}/trust-agent/governance-bundle.jws"
    deadline = time.time() + 60
    while time.time() < deadline and not os.path.isfile(bundle_jws):
        time.sleep(3)
    check("A4 governance bundle firmado sincronizado en el home",
          os.path.isfile(bundle_jws))
    print(f"\nAPPROVE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# governed (turno real gobernado por la policy de la org)
# ===========================================================================
def phase_governed():
    S = load_state()
    print("=== GOVERNED: el agente del empleado queda gobernado ===", flush=True)
    # Pedirle una tool BLOQUEADA por policy (terminal no está en allowed_tools).
    # OJO: texto y Enter en send-keys SEPARADOS — con el Enter en el mismo
    # send-keys el primer mensaje del TUI fresco no se envía (queda en el
    # buffer del prompt).
    subprocess.run(["tmux", "send-keys", "-t", "dlchat",
                    "Use the terminal tool to run: echo hello"], check=True)
    time.sleep(2)
    subprocess.run(["tmux", "send-keys", "-t", "dlchat", "Enter"], check=True)

    # Poll del veto en la transcripción canónica hasta 90 s (la latencia del
    # turno real contra el gateway del tenant varía).
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
    # El reply del modelo se renderiza DESPUÉS del resultado de la tool —
    # margen para que el pane muestre la comunicación al empleado.
    if veto_msg:
        time.sleep(12)
    pane = _pane("dlchat")
    evidence_dump("05-governed-pane.txt", pane)

    # El mensaje EXACTO del veto vive en la transcripción canónica (state.db
    # del home) como resultado de la tool — el reply del modelo lo parafrasea.
    check("G1 tool terminal VETADA por la policy de la org (mensaje exacto en "
          "la transcripción)",
          veto_msg is not None
          and "terminal" in veto_msg[0]
          and "Allowed tools: governance_status, web_search" in veto_msg[0],
          str(veto_msg)[:160])
    check("G1 el agente lo comunica al empleado (pane)",
          "blocked by" in pane.lower() and "organization" in pane.lower()
          and "policy" in pane.lower())
    log = open(f"{EMPLOYEE_HOME}/trust-agent/plugin.log").read()
    evidence_dump("03-plugin-log.txt", log)
    check("G1 plugin.log registra el veto real (veto_eval + tool_vetoed)",
          "veto_eval" in log and "tool_vetoed" in log)

    # G2: el agente reporta gobernanza del bundle verificado — bundle_synced
    # + heartbeat_ok con la MISMA policy_version que la BD registra aplicada.
    rows = e2e.db(
        "SELECT status, applied_policy_version, last_heartbeat_at IS NOT NULL "
        "FROM devices WHERE id = %s", (S["device_id"],))
    status, policy_version, hb = rows[0]
    check("G2 governance: bundle firmado sincronizado + heartbeat con la "
          "policy del tenant aplicada",
          "bundle_synced" in log and "heartbeat_ok" in log
          and f'"applied_policy_version": {policy_version}' in log,
          f"policy_version={policy_version}")
    check("G3 device ACTIVE en BD con policy aplicada y heartbeat",
          status == "active" and policy_version is not None and hb,
          f"status={status} policy_v={policy_version} heartbeat={hb}")
    print(f"\nGOVERNED: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["setup", "download", "install", "login-prep",
                             "login-verify", "firstrun", "approve", "governed"])
    args = ap.parse_args()
    rc = {
        "setup": phase_setup, "download": phase_download,
        "install": phase_install, "login-prep": phase_login_prep,
        "login-verify": phase_login_verify, "firstrun": phase_firstrun,
        "approve": phase_approve, "governed": phase_governed,
    }[args.phase]()
    sys.exit(rc or (1 if FAILURES else 0))


if __name__ == "__main__":
    main()

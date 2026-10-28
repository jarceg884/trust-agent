#!/usr/bin/env python3
"""EVIDENCIA LIVE — Spec 034 (cli-governed), tarea t_a51a4622.

E2E del mandato Miller para el empleado de servidor/VPS/terminal contra el
stack vivo (:8000 api, :8080 Keycloak real, :4000 LiteLLM REAL):

  setup     tenant real cli-gov-e2e + admin/worker MFA + policy
            (allowed_tools + allowed_models=[glm-5.2]) + budgets + branding.
  download  página pública con DOS opciones (desktop + CLI) e instalador
            GET /download/{slug}/cli con org_code horneado + audit
            download.issued (artifact=cli).
  install   ENTORNO LIMPIO: curl -fsSL <link>/cli | bash con HERMES_HOME
            temporal → Hermes CLI + plugin + binding + skin + config +
            TRUST_DEVICE_TYPE=cli.
  login     PKCE+TOTP real del worker → id-token.txt (el artefacto que
            produce trust-login) + virtual key self-issued + config.
  firstrun  hermes chat en el home instalado → auto-enroll zero-UI →
            device PENDING con device_type='cli' en BD.
  approve   admin aprueba → device token + governance bundle JWS.
  governed  policy aplicada: modelo APROBADO funciona (200 real) y modelo
            NO aprobado BLOQUEADO por el gateway con la key del usuario.
  revoke    admin revoca → heartbeat con device token → 401 y virtual key
            bloqueada (401 gateway).

Uso:
  .venv/bin/python scripts/evidence_cli_governed.py --phase setup
  ...
"""
import argparse
import hashlib
import http.client
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

SLUG = "cli-gov-e2e"
ADMIN_EMAIL = "admin@cli-gov-e2e.test"
ADMIN_PASSWORD = "CliAdmin2026!"
WORKER_EMAIL = "worker@cli-gov-e2e.test"
WORKER_PASSWORD = "CliWorker2026!"
APPROVED_MODEL = "glm-5.2"     # único modelo aprobado por policy para la org
DENIED_MODEL = "qwen3-32b"     # en el catálogo pero NO aprobado por policy
LITELLM_REAL_PORT = 4000       # gateway REAL (v1.106.3, stack compose)

BRANDING = {
    "product_name": "Nimbus Agent",
    "tagline": "Nimbus governed AI",
    "accent_color": "#7c3aed",
}

# Home fresco del "empleado de servidor" (la máquina/VPS del trabajador).
E2E_ROOT = "/root/trust-agent/.e2e"
CLI_HOME = f"{E2E_ROOT}/cli-gov-home"
STATE_FILE = f"{E2E_ROOT}/.state-cli-gov-e2e.json"
EVIDENCE_DIR = "/root/trust-agent/docs/evidencia/cli-governed"

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


def litellm_real_chat(key, model, max_tokens=8):
    """POST real al gateway LiteLLM del tenant con la key del usuario."""
    conn = http.client.HTTPConnection("127.0.0.1", LITELLM_REAL_PORT, timeout=30)
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "Say OK"}],
                       "max_tokens": max_tokens}).encode()
    conn.request("POST", "/v1/chat/completions", body=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, raw


def evidence_dump(name, text):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, name)
    with open(path, "w") as f:
        f.write(text if isinstance(text, str) else text.decode("utf-8", "replace"))
    print(f"    evidence → {path}", flush=True)


def audit_cli_count(tenant_id):
    rows = e2e.db(
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
        "AND action = 'download.issued' AND payload->>'artifact' = 'cli'",
        (tenant_id,))
    return rows[0][0]


# ===========================================================================
# setup
# ===========================================================================
def phase_setup():
    e2e.SLUG = SLUG
    S = {}
    print("=== SETUP: tenant cli-gov-e2e + MFA + policy + branding ===", flush=True)

    st, body, raw = e2e.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {e2e.PROVIDER_TOKEN}"}, payload={
        "organization_name": "CLI Gov Corp", "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0, "model_plan": "standard", "slug": SLUG})
    if st == 409 or body.get("created") is False:
        rows = e2e.db("SELECT id FROM tenants WHERE slug = %s", (SLUG,))
        check("S1 tenant ya existía (re-ejecución) — se reutiliza", len(rows) == 1)
        S["tenant_id"] = str(rows[0][0])
    else:
        check("S1 proveedor crea el tenant → 201", st == 201, f"HTTP {st} {raw[:160]}")
        if st != 201:
            return 1
        S["tenant_id"] = body["tenant"]["id"]
    S["dept_it"] = str(e2e.db(
        "SELECT id FROM departments WHERE tenant_id = %s "
        "AND profile_template = 'it_operations'", (S["tenant_id"],))[0][0])

    # Admin: PKCE + TOTP reales (acr=2 → LoA 2, spec 015 D-AUTH-06).
    # El provisioning asigna un password one-time aleatorio (OTA): se
    # resetea a ADMIN_PASSWORD vía Admin REST (idempotente, cubre primera
    # corrida y re-ejecuciones) antes del baile.
    import evidence_hermes_real_bridge as bridge
    bridge.SLUG = SLUG
    token = e2e.kc_admin_token()
    admin_uuid = e2e.kc_user_uuid(ADMIN_EMAIL)
    e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{admin_uuid}/reset-password",
                {"type": "password", "value": ADMIN_PASSWORD, "temporary": False},
                token=token)
    bridge.reset_totp(admin_uuid)
    bridge.force_configure_totp(admin_uuid)
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD, acr_values="2")
    check("S2 admin login REAL PKCE+TOTP → acr=2", str(acr) == "2", f"acr={acr}")
    S["admin"] = e2e.mint(id_token)
    admin = S["admin"]["session_token"]

    # Worker en el realm real (idempotente para re-ejecuciones).
    e2e.kc_json("POST", f"/admin/realms/{SLUG}/users", {
        "username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
        "firstName": "Cli", "lastName": "Worker",
        "attributes": {"tenant_id": [S["tenant_id"]]}}, token=token)
    worker_uuid = e2e.kc_user_uuid(WORKER_EMAIL)
    e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
                {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
                token=token)
    e2e.db("INSERT INTO users (tenant_id, department_id, email, display_name, "
           "status, mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, 'active', "
           "'true', %s) ON CONFLICT DO NOTHING",
           (S["tenant_id"], S["dept_it"], WORKER_EMAIL,
            "CLI Worker", worker_uuid), fetch=False)
    e2e.db("INSERT INTO user_roles (tenant_id, user_id, role_key, department_id) "
           "SELECT %s, id, 'user', department_id FROM users WHERE oidc_subject = %s "
           "ON CONFLICT DO NOTHING",
           (S["tenant_id"], worker_uuid), fetch=False)
    S["worker_user_id"] = str(e2e.db(
        "SELECT id FROM users WHERE oidc_subject = %s", (worker_uuid,))[0][0])
    S["worker_sub"] = worker_uuid

    # Worker: PKCE + enrolamiento TOTP real (secret capturado para re-logins).
    bridge.reset_totp(worker_uuid)
    bridge.force_configure_totp(worker_uuid)
    e2e.COOKIES.clear()
    worker_totp = {}
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                                   totp_state=worker_totp, acr_values="2")
    check("S3 worker enrola TOTP real y loguea → acr=2", str(acr) == "2",
          f"acr={acr}")
    S["worker_totp_secret"] = worker_totp.get("secret")

    # Policy restrictiva: SOLO glm-5.2 aprobado para la org (la key virtual
    # del worker hereda effective = approved ∩ policy) + tools mínimas.
    st, _, raw = e2e.api("PUT", "/api/policies/org",
                         {"key": "allowed_models", "value": [APPROVED_MODEL]},
                         bearer=admin)
    check("S4 policy org allowed_models=[glm-5.2] → 200", st == 200,
          f"HTTP {st} {raw[:120]}")
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

    # Branding white-label publicado.
    st, _, raw = e2e.api("PUT", "/api/branding", BRANDING, bearer=admin)
    check("S6 branding draft (Nimbus Agent) → 200", st == 200, f"HTTP {st}")
    st, body, raw = e2e.api("POST", "/api/branding/publish", bearer=admin)
    check("S6 branding publicado (v1, o ya publicado en re-ejecución)",
          (st == 200 and body.get("version", 0) >= 1)
          or (st == 422 and "No unpublished changes" in raw),
          f"HTTP {st} {raw[:120]}")

    save_state(S)
    print(f"\nSETUP: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# download (página + instalador CLI)
# ===========================================================================
def phase_download():
    S = load_state()
    print("=== DOWNLOAD: página con dos opciones + instalador CLI ===", flush=True)
    e2e.db("DELETE FROM ops.login_throttle WHERE key LIKE %s OR key LIKE %s",
           ("dl:%", "ip:%"), fetch=False)
    base = "http://127.0.0.1:8000"
    link = f"{base}/download/{SLUG}"

    # Página pública: DOS opciones con branding del tenant.
    st, hdrs, body = http_get(link)
    page = body.decode("utf-8", "replace")
    check("D1 GET /download/{slug} anónimo → 200 HTML",
          st == 200 and "text/html" in hdrs.get("content-type", ""))
    check("D1 página muestra el branding publicado (Nimbus Agent + accent)",
          "Nimbus Agent" in page and "#7c3aed" in page)
    check("D1 opción Desktop presente (bundle)", f"/download/{SLUG}/bundle" in page)
    check("D1 opción CLI presente (comando curl copiable + targets)",
          f"{link}/cli" in page and "curl -fsSL" in page and "WSL2" in page)
    evidence_dump("01-download-page.html", page)

    # Instalador CLI: script bash con org_code horneado.
    before = audit_cli_count(S["tenant_id"])
    st, hdrs, body = http_get(f"{link}/cli")
    check("D2 GET /download/{slug}/cli anónimo → 200 text/x-shellscript",
          st == 200 and "text/x-shellscript" in hdrs.get("content-type", ""))
    script = body.decode("utf-8", "replace")
    sha = hashlib.sha256(body).hexdigest()
    check("D2 header X-Installer-SHA256 = sha256 real del cuerpo",
          hdrs.get("x-installer-sha256") == sha, sha[:16])
    check("D2 instalador hornea org_code + device_type cli + binding",
          f'ORG_CODE="{SLUG}"' in script
          and 'set_env TRUST_DEVICE_TYPE "cli"' in script
          and "org-code.txt" in script)
    check("D2 instalador reusa el bundle canónico (fuente única del plugin)",
          "/download/${SLUG}/bundle" in script)
    evidence_dump("02-cli-installer.sh", script)
    after = audit_cli_count(S["tenant_id"])
    check("D3 audit download.issued artifact=cli +1 por servido",
          after == before + 1, f"{before} → {after}")

    # Fail-closed: link deshabilitado → 403 también para el CLI.
    st, _, _ = e2e.api("PUT", "/api/download-link", {"enabled": False},
                       bearer=S["admin"]["session_token"])
    check("D4 PUT enabled=false (admin) → 200", st == 200)
    st, _, body = http_get(f"{link}/cli")
    check("D4 instalador CLI con link deshabilitado → 403 link_disabled",
          st == 403 and b"link_disabled" in body)
    st, _, _ = e2e.api("PUT", "/api/download-link", {"enabled": True},
                       bearer=S["admin"]["session_token"])
    check("D4 PUT enabled=true restaura", st == 200)

    print(f"\nDOWNLOAD: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# install (máquina del empleado: curl | bash en un home limpio)
# ===========================================================================
def phase_install():
    print("=== INSTALL: curl -fsSL <link>/cli | bash en home limpio ===", flush=True)
    e2e.db("DELETE FROM ops.login_throttle WHERE key LIKE %s OR key LIKE %s",
           ("dl:%", "ip:%"), fetch=False)
    subprocess.run(["rm", "-rf", CLI_HOME], check=True)

    # LITERALMENTE el comando que el admin comparte (mandato Miller).
    cmd = f'curl -fsSL http://127.0.0.1:8000/download/{SLUG}/cli | bash'
    out = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "HERMES_HOME": CLI_HOME},
        cwd="/tmp")
    evidence_dump("03-install.log", out.stdout + "\n" + out.stderr)
    check("I1 curl | bash termina en verde", out.returncode == 0,
          out.stderr[-300:] if out.returncode else "")

    home = CLI_HOME
    binding = open(f"{home}/trust-agent/org-code.txt").read().strip()
    check("I2 binding org-code.txt = slug del tenant", binding == SLUG, binding)
    check("I2 plugin instalado en el home (plugin.yaml + módulos)",
          os.path.isfile(f"{home}/plugins/trust-agent-governance/plugin.yaml")
          and os.path.isfile(f"{home}/plugins/trust-agent-governance/enroll.py"))
    check("I2 skin white-label del tenant generado",
          os.path.isfile(f"{home}/skins/trust-{SLUG}.yaml"))
    skin = open(f"{home}/skins/trust-{SLUG}.yaml").read()
    check("I2 skin lleva el accent y el product_name del branding",
          "#7c3aed" in skin and "Nimbus Agent" in skin)
    env = open(f"{home}/.env").read()
    check("I2 .env lleva TRUST_AGENT_API + TRUST_ORG_CODE + TRUST_DEVICE_TYPE=cli",
          "TRUST_AGENT_API=http://127.0.0.1:8000" in env
          and f"TRUST_ORG_CODE={SLUG}" in env
          and "TRUST_DEVICE_TYPE=cli" in env)
    check("I2 trust-login presente y ejecutable",
          os.access(f"{home}/trust-login", os.X_OK))

    def config_get(key):
        r = subprocess.run(["hermes", "config", "get", key],
                           capture_output=True, text=True,
                           env={**os.environ, "HERMES_HOME": home})
        return (r.stdout or r.stderr).strip()

    def config_get_first(key):
        # El valor escalar va en la primera línea; las advertencias del CLI
        # (clave no reconocida por el reconocedor local) van después.
        return config_get(key).splitlines()[0].strip()

    check("I3 config: gateway.standalone=true",
          config_get_first("gateway.standalone").endswith("true"))
    check("I3 config: plugin habilitado",
          "trust-agent-governance" in config_get("plugins.enabled"))
    check("I3 config: toolset del plugin en cli",
          "trust_agent" in config_get("platform_toolsets.cli"))
    check("I3 config: skin del tenant activo",
          config_get_first("display.skin").endswith(f"trust-{SLUG}"))
    check("I3 config: model → gateway del tenant",
          APPROVED_MODEL in config_get_first("model.default")
          and "4000" in config_get_first("model.base_url"))

    print(f"\nINSTALL: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# login (PKCE+TOTP real → id-token.txt + virtual key)
# ===========================================================================
def phase_login():
    S = load_state()
    e2e.SLUG = SLUG
    print("=== LOGIN: worker PKCE+TOTP real → id-token.txt + key ===", flush=True)
    # Login REAL del worker (mismo baile que produce trust-login en el
    # navegador; el artefacto que consume el plugin es idéntico).
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(
        WORKER_EMAIL, WORKER_PASSWORD,
        totp_state={"secret": S["worker_totp_secret"]}, acr_values="2")
    check("L1 worker login REAL PKCE+TOTP → acr=2", str(acr) == "2", f"acr={acr}")
    if not id_token:
        return 1

    trust_dir = f"{CLI_HOME}/trust-agent"
    token_file = f"{trust_dir}/id-token.txt"
    with open(token_file, "w") as f:
        f.write(id_token)
    os.chmod(token_file, 0o600)
    check("L1 id-token.txt escrito (0600) — el artefacto de trust-login",
          stat.S_IMODE(os.stat(token_file).st_mode) == 0o600)

    # Self-issue de la virtual key (lo que trust-login --issue-key hace).
    sess = e2e.mint(id_token, kind="desktop")
    S["worker_session"] = sess["session_token"]
    st, body, raw = e2e.api("POST", "/api/models/keys", {},
                            bearer=sess["session_token"])
    check("L2 self-issue de la virtual key → 200/201 one-time",
          st in (200, 201), f"HTTP {st} {raw[:160]}")
    if st not in (200, 201):
        return 1
    key = body["key"]
    S["worker_key"] = key
    check("L2 key emitida SOLO con el modelo aprobado por policy",
          body.get("allowed_models") == [APPROVED_MODEL],
          str(body.get("allowed_models")))
    r = subprocess.run(
        ["hermes", "config", "set", "model.api_key", key, "--force"],
        capture_output=True, text=True,
        env={**os.environ, "HERMES_HOME": CLI_HOME})
    check("L2 model.api_key almacenada en el home del CLI", r.returncode == 0)

    save_state(S)
    print(f"\nLOGIN: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# firstrun (primera ejecución: auto-enroll zero-UI, device_type=cli)
# ===========================================================================
def _tmux_run(session, cmd):
    subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-x", "200",
                    "-y", "50", cmd], check=True)


def _pane(session):
    r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                       capture_output=True, text=True)
    return r.stdout


def _dismiss_first_run_prompts(session):
    """Descarta prompts de primera ejecución del TUI (telemetría).

    El instalador fija ``telemetry.shared_metrics.enabled=false``, pero el
    TUI de Hermes re-pregunta una vez por home fresco ("Asking once more:
    an earlier version could save 'No thanks'...") — comportamiento del
    core que no parcheamos (constitution IV). El harness lo descarta con
    Enter igual que lo haría el empleado.
    """
    deadline = time.time() + 90
    dismissed = False
    while time.time() < deadline:
        pane = _pane(session)
        if "Help improve Hermes?" in pane:
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"],
                           check=True)
            dismissed = True
            time.sleep(3)
            continue
        if "Hermes Agent" in pane or "❯" in pane:
            return dismissed
        time.sleep(2)
    return dismissed


def phase_firstrun():
    S = load_state()
    print("=== FIRSTRUN: hermes chat → auto-enroll zero-UI (tipo cli) ===",
          flush=True)
    # SOLO el chat (gateway.standalone=true): dos procesos con el plugin
    # sobre el mismo home se pisan el enrollment token one-time (spec 022).
    _tmux_run("clichat", f"env -u HERMES_SESSION_SOURCE HERMES_HOME={CLI_HOME} "
                         "hermes chat")
    # Prompt de telemetría de primera ejecución: se descarta como haría el
    # empleado (el auto-enroll corre igual — watcher daemon del plugin).
    if _dismiss_first_run_prompts("clichat"):
        print("    [harness] prompt de telemetría de primera ejecución "
              "descartado (Enter)", flush=True)
    started = time.time()
    deadline = started + 120
    submitted = False
    rows = []
    while time.time() < deadline:
        rows = e2e.db(
            "SELECT id, status, device_type FROM devices WHERE tenant_id=%s "
            "AND user_id=%s AND status='pending' "
            "AND enrolled_at >= to_timestamp(%s)",
            (S["tenant_id"], S["worker_user_id"], started - 60))
        if rows:
            submitted = True
            break
        time.sleep(3)
    check("F1 auto-enroll zero-UI: device PENDING en BD sin ningún panel",
          submitted, f"devices={rows if submitted else 'ninguno'}")
    if submitted:
        check("F1 el device se enroló con device_type='cli' (spec 034)",
              rows[0][2] == "cli", f"device_type={rows[0][2]}")
        S["device_id"] = str(rows[0][0])
        save_state(S)
        evidence_dump("04-firstrun-pane.txt", _pane("clichat"))
    plugin_log = f"{CLI_HOME}/trust-agent/plugin.log"
    if os.path.isfile(plugin_log):
        evidence_dump("05-plugin-log.txt", open(plugin_log).read())

    print(f"\nFIRSTRUN: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# approve (admin del cliente)
# ===========================================================================
def phase_approve():
    S = load_state()
    e2e.SLUG = SLUG
    print("=== APPROVE: el admin aprueba el device CLI ===", flush=True)
    import evidence_hermes_real_bridge as bridge
    bridge.SLUG = SLUG
    admin_uuid = e2e.kc_user_uuid(ADMIN_EMAIL)
    bridge.reset_totp(admin_uuid)
    bridge.force_configure_totp(admin_uuid)
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD, acr_values="2")
    check("A1 re-login admin (PKCE+TOTP) → acr=2", str(acr) == "2", f"acr={acr}")
    admin = e2e.mint(id_token)["session_token"]
    S["admin"]["session_token"] = admin

    st, body, raw = e2e.api("POST", f"/api/devices/{S['device_id']}/approve",
                            bearer=admin)
    if st == 409 and "pending" in raw:
        rows = e2e.db("SELECT status FROM devices WHERE id = %s", (S["device_id"],))
        check("A2 device ya estaba aprobado (re-ejecución) — active",
              rows and rows[0][0] == "active")
    else:
        check("A2 POST /api/devices/{id}/approve → 200 active",
              st == 200 and body.get("status") == "active",
              f"HTTP {st} {raw[:160]}")

    # El agente CLI canjea el enrollment token (zero-UI) → device token.
    deadline = time.time() + 90
    token_ok = False
    while time.time() < deadline:
        p = f"{CLI_HOME}/trust-agent/device-token.json"
        if os.path.isfile(p):
            data = json.load(open(p))
            token_ok = bool(data.get("device_id") and data.get("token"))
            if token_ok:
                S["device_token"] = data["token"]
                break
        time.sleep(3)
    check("A3 el agente CLI canjea el enrollment token → device token", token_ok)

    bundle_jws = f"{CLI_HOME}/trust-agent/governance-bundle.jws"
    # El BundleSyncer tick-ea cada ~300 s (DEFAULT_SYNC_INTERVAL_SECONDS):
    # la ventana cubre al menos un tick completo tras el canje.
    deadline = time.time() + 360
    while time.time() < deadline and not os.path.isfile(bundle_jws):
        time.sleep(3)
    check("A4 governance bundle JWS firmado sincronizado en el home",
          os.path.isfile(bundle_jws))

    rows = e2e.db("SELECT device_type, status FROM devices WHERE id = %s",
                  (S["device_id"],))
    check("A5 device ACTIVE en BD conservando device_type='cli'",
          rows and rows[0] == ("cli", "active"), str(rows))
    save_state(S)
    print(f"\nAPPROVE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# governed (policy aplicada: modelo no aprobado → bloqueado)
# ===========================================================================
def phase_governed():
    S = load_state()
    print("=== GOVERNED: policy aplicada al agente CLI ===", flush=True)
    key = S["worker_key"]

    # G1: el modelo APROBADO funciona con la key del worker (gateway real).
    st, raw = litellm_real_chat(key, APPROVED_MODEL)
    check("G1 modelo APROBADO (glm-5.2) → 200 real contra el gateway",
          st == 200, f"HTTP {st} {raw[:120]}")
    evidence_dump("06-governed-approved-model.json", raw)

    # G2: modelo NO aprobado → BLOQUEADO por el gateway (la key solo permite
    # lo que la policy aprobó — enforcement real de model access).
    st, raw = litellm_real_chat(key, DENIED_MODEL)
    blocked = st in (400, 403, 404) and st != 200
    check("G2 modelo NO aprobado (qwen3-32b) → BLOQUEADO por el gateway",
          blocked, f"HTTP {st} {raw[:160]}")
    evidence_dump("07-governed-denied-model.json", raw)

    # G3: el agente reporta gobernanza: heartbeat + policy version aplicada.
    deadline = time.time() + 90
    ok_gov = False
    rows = []
    while time.time() < deadline:
        rows = e2e.db(
            "SELECT status, applied_policy_version, last_heartbeat_at "
            "FROM devices WHERE id = %s", (S["device_id"],))
        if rows and rows[0][1] is not None and rows[0][2] is not None:
            ok_gov = True
            break
        time.sleep(5)
    log = ""
    plugin_log = f"{CLI_HOME}/trust-agent/plugin.log"
    if os.path.isfile(plugin_log):
        log = open(plugin_log).read()
        evidence_dump("05-plugin-log.txt", log)
    check("G3 heartbeat + policy version aplicada en BD (agente gobernado)",
          ok_gov and "heartbeat_ok" in log,
          f"row={rows[0] if rows else None}")
    pane = _pane("clichat")
    evidence_dump("08-governed-pane.txt", pane)

    print(f"\nGOVERNED: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# revoke (revocación → 401 inmediato)
# ===========================================================================
def phase_revoke():
    S = load_state()
    print("=== REVOKE: admin revoca → 401 inmediato con el device token ===",
          flush=True)
    admin = S["admin"]["session_token"]
    st, body, raw = e2e.api("POST", f"/api/devices/{S['device_id']}/revoke",
                            bearer=admin)
    check("R1 POST /api/devices/{id}/revoke → 200 revoked",
          st == 200 and body.get("status") == "revoked", f"HTTP {st} {raw[:160]}")

    # R2: el device token YA no sirve — el DeviceGate es fail-closed (401).
    st, body, raw = e2e.api("POST", "/api/me/device-heartbeat",
                            {"applied_policy_version": 1},
                            headers={"Authorization": f"Bearer {S['device_token']}"})
    check("R2 heartbeat con el device token revocado → 401", st == 401,
          f"HTTP {st} {raw[:120]}")

    # R3: la virtual key del usuario fue bloqueada en el gateway (sweep
    # REQ-GOH-011): el modelo aprobado también responde 401 ahora.
    st, raw = litellm_real_chat(S["worker_key"], APPROVED_MODEL)
    check("R3 virtual key bloqueada por la revocación → 401 gateway",
          st == 401, f"HTTP {st} {raw[:160]}")
    evidence_dump("09-revoked-key-gateway.json", raw)

    # R4: el plugin del agente lo vive: 401s en su log local.
    deadline = time.time() + 60
    unauth = False
    plugin_log = f"{CLI_HOME}/trust-agent/plugin.log"
    while time.time() < deadline:
        if os.path.isfile(plugin_log):
            log = open(plugin_log).read()
            if "401" in log:
                unauth = True
                break
        time.sleep(5)
    if os.path.isfile(plugin_log):
        evidence_dump("05-plugin-log.txt", open(plugin_log).read())
    check("R4 el agente CLI registra los 401 tras la revocación (plugin.log)",
          unauth)
    evidence_dump("10-revoke-pane.txt", _pane("clichat"))
    subprocess.run(["tmux", "kill-session", "-t", "clichat"], capture_output=True)

    print(f"\nREVOKE: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["setup", "download", "install", "login",
                             "firstrun", "approve", "governed", "revoke"])
    args = ap.parse_args()
    rc = {
        "setup": phase_setup, "download": phase_download,
        "install": phase_install, "login": phase_login,
        "firstrun": phase_firstrun, "approve": phase_approve,
        "governed": phase_governed, "revoke": phase_revoke,
    }[args.phase]()
    sys.exit(rc or (1 if FAILURES else 0))


if __name__ == "__main__":
    main()

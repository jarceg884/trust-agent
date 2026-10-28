#!/usr/bin/env python3
"""EVIDENCIA LIVE — Renovación del device token (t_0b6e8e2d, spec 022 v1.1.0).

Validación EN VIVO de REQ-HRB-010 (amend D-HRB-03) contra el stack real
(api :8000 reconstruida con el fix, Keycloak real :8080, gateway LiteLLM real
:4000, PostgreSQL). Nada simulado: cada fase produce artefactos en
docs/evidencia/token-renewal/ y checks PASS/FAIL reproducibles.

Fases (secuenciales; --phase setup una vez, luego renewal → expiryrecovery
→ revocation → audit):

  setup          bundle FRESCO del link de la org (GET /download/{slug}/bundle,
                 con el plugin NUEVO) → install → workerlogin (PKCE+TOTP acr=2)
                 → firstrun (auto-enroll zero-UI, sync 30 s) → approve (admin)
  renewal        AC-HRB-010(1): sesión viva MÁS ALLÁ del TTL original —
                 heartbeat_ok sostenidos, device_token_renewed avanzando,
                 device-token.json con renewed_at, modo NORMAL > 15 min
  expiryrecovery AC-HRB-010(3): agente APAGADO > TTL → token muerto →
                 trust-login fresco → arranque → recuperación automática
                 (POST /api/devices/enroll → 200 directo, audit
                 device.token_reissued) → NORMAL SIN intervención admin
  revocation     AC-HRB-010(2): admin revoca a mitad de sesión → siguiente
                 ciclo 401 → RESTRICTED inmediato (AC-HRB-004c intacto)
  audit          resumen SQL del audit_log del tenant para la corrida

Uso:
  .venv/bin/python scripts/evidence_token_renewal_live.py --phase setup
  ... (secuencial; cada fase persiste su estado en .e2e/.state-token-renewal.json)

Requisitos: stack levantado con la imagen reconstruida (docker compose build
migrate && docker compose up -d) — el bundle descargado lleva el plugin con
sliding renewal; TRUST_SYNC_INTERVAL=30 en el chat para ciclos rápidos.

Artefactos: cada `--phase setup` estampa un run_id y TODAS las fases de esa
corrida escriben en docs/evidencia/token-renewal/<run_id>/ (symlink `latest`
al más nuevo) — las re-ejecuciones ya NO pisan los artefactos de corridas
anteriores (hallazgo QA t_c260323f #5).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_final_live as e2e  # noqa: E402 - helpers verificados del E2E final

# Cada --phase corre en un proceso NUEVO: sin esto, expiryrecovery/revocation
# heredarían el SLUG default "final-e2e" (realm inexistente → authorize 400).
e2e.SLUG = "hermes-dl-e2e"

SLUG = "hermes-dl-e2e"
ADMIN_EMAIL = "admin@hermes-dl-e2e.test"
ADMIN_PASSWORD = "DlAdmin2026!"
WORKER_EMAIL = "worker@hermes-dl-e2e.test"
WORKER_PASSWORD = "DlWorker2026!"

E2E_ROOT = "/root/trust-agent/.e2e"
HOME_DIR = f"{E2E_ROOT}/hermes-dl-home-renewal"
STATE_FILE = f"{E2E_ROOT}/.state-hermes-dl-e2e.json"
RN_STATE_FILE = f"{E2E_ROOT}/.state-token-renewal.json"
EVIDENCE_BASE = "/root/trust-agent/docs/evidencia/token-renewal"
TMUX = "renewchat"
SYNC_INTERVAL = 30  # ciclos de sync/heartbeat rápidos (runbook D-HRB-03)

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


def evidence_dir():
    """Artefactos POR CORRIDA (hallazgo QA t_c260323f #5): antes cada
    re-ejecución pisaba los 00-08 de la corrida anterior en el directorio
    compartido. `setup` estampa run_id en el estado de renovación; cada
    fase (proceso aparte) resuelve aquí su subdirectorio. Sin run_id
    (p. ej. audit suelto sin setup) cae al directorio base."""
    run_id = load_rn_state().get("run_id")
    return os.path.join(EVIDENCE_BASE, run_id) if run_id else EVIDENCE_BASE


def refresh_latest_symlink():
    """Apuntar EVIDENCE_BASE/latest a la corrida vigente (best effort)."""
    latest = os.path.join(EVIDENCE_BASE, "latest")
    tmp = latest + ".tmp"
    try:
        if os.path.islink(tmp) or os.path.isfile(tmp):
            os.remove(tmp)
        os.symlink(os.path.basename(evidence_dir()), tmp)
        os.replace(tmp, latest)
    except OSError as e:
        print(f"    warn: symlink latest no actualizado: {e}", flush=True)


def evidence_dump(name, text):
    d = evidence_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w") as f:
        f.write(text)
    print(f"    evidence → {path}", flush=True)


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def load_rn_state():
    if os.path.isfile(RN_STATE_FILE):
        with open(RN_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_rn_state(s):
    with open(RN_STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)
    os.chmod(RN_STATE_FILE, 0o600)


def tmux_run(cmd):
    subprocess.run(["tmux", "kill-session", "-t", TMUX], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", TMUX, "-x", "200",
                    "-y", "50", cmd], check=True)


def chat_cmd():
    return (f"env -u HERMES_SESSION_SOURCE TRUST_SYNC_INTERVAL={SYNC_INTERVAL} "
            f"HERMES_HOME={HOME_DIR} hermes chat")


def pane():
    r = subprocess.run(["tmux", "capture-pane", "-t", TMUX, "-p"],
                       capture_output=True, text=True)
    return r.stdout


def send(msg):
    subprocess.run(["tmux", "send-keys", "-t", TMUX, msg], check=True)
    time.sleep(2)
    subprocess.run(["tmux", "send-keys", "-t", TMUX, "Enter"], check=True)


def messages(like=None, after=0):
    import sqlite3
    conn = sqlite3.connect(f"{HOME_DIR}/state.db")
    try:
        if like:
            rows = conn.execute(
                "SELECT content FROM messages WHERE content LIKE ? "
                "AND rowid > ? ORDER BY rowid DESC", (like, after)).fetchall()
        else:
            rows = conn.execute(
                "SELECT content FROM messages WHERE rowid > ? "
                "ORDER BY rowid DESC LIMIT 20", (after,)).fetchall()
    except sqlite3.OperationalError:
        # Race conocida del TUI fresco: la tabla messages nace con el primer
        # turno — mientras tanto no hay transcripción que consultar.
        return []
    finally:
        conn.close()
    return [r[0] for r in rows]


def max_message_rowid():
    """rowid máximo en state.db (0 si aún no hay transcripción)."""
    import sqlite3
    try:
        conn = sqlite3.connect(f"{HOME_DIR}/state.db")
        try:
            row = conn.execute("SELECT max(rowid) FROM messages").fetchone()
            return row[0] or 0
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0


def ask_agent(prompt, like, needle, attempts=3, timeout=60):
    """Envía un prompt al chat y espera una respuesta que contenga `needle`.

    El PRIMER mensaje a un TUI fresco dispara el boot lazy del gateway
    (~8 s) y se traga (0 filas en state.db) → un solo intento es no
    determinístico (hallazgo QA t_c260323f #3: R7/E7 flaqueaban). Se
    reintenta el round-trip y solo se miran filas NUEVAS (rowid > baseline)
    para no matchear respuestas de fases anteriores."""
    baseline = max_message_rowid()
    for attempt in range(1, attempts + 1):
        send(prompt)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for h in messages(like, after=baseline):
                if needle in h:
                    return h
            time.sleep(5)
        print(f"    ask_agent: intento {attempt}/{attempts} sin respuesta"
              + ("; reintentando (el primer mensaje puede perderse en el "
                 "boot lazy del gateway)" if attempt < attempts else ""),
              flush=True)
    return None


def plugin_log():
    p = f"{HOME_DIR}/trust-agent/plugin.log"
    return open(p).read() if os.path.isfile(p) else ""


def mode_change_events(log=None):
    """Los eventos mode_change que el plugin registra en plugin.log
    (una línea JSON por evento; tolera prefijos no-JSON)."""
    events = []
    for line in (log if log is not None else plugin_log()).splitlines():
        i = line.find("{")
        if i < 0 or '"mode_change"' not in line:
            continue
        try:
            evt = json.loads(line[i:])
        except ValueError:
            continue
        if evt.get("event") == "mode_change":
            events.append(evt)
    return events


def home_pids():
    """PIDs de TODO proceso cuyo entorno lleva HERMES_HOME=HOME_DIR.

    `hermes gateway run --idle-exit` SOBREVIVE a `tmux kill-session` y al
    rm -rf del home porque HERMES_HOME viaja en el ENTORNO, no en la
    cmdline — un kill por patrón de `ps` nunca lo matchea (hallazgo QA
    t_c260323f #2: contaminó una corrida y falseó la premisa "agente
    apagado" de expiryrecovery)."""
    pids = []
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        pid_n = int(pid)
        if pid_n == me:
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read()
        except OSError:
            continue
        for entry in env.split(b"\0"):
            if (entry.startswith(b"HERMES_HOME=")
                    and entry[len(b"HERMES_HOME="):].decode(
                        errors="replace") == HOME_DIR):
                pids.append(pid_n)
                break
    return pids


def kill_home_processes():
    """TERM→KILL a todo proceso con HERMES_HOME=HOME_DIR (ver home_pids).
    Devuelve la lista de PIDs encontrados (los haya matado o no)."""
    victims = home_pids()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        alive = [p for p in victims if os.path.exists(f"/proc/{p}")]
        if not alive:
            break
        for p in alive:
            try:
                os.kill(p, sig)
            except ProcessLookupError:
                pass
        time.sleep(2)
    return victims


def token_file_data():
    p = f"{HOME_DIR}/trust-agent/device-token.json"
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return json.load(f)


def token_exp(token):
    """exp del JWT SIN verificar firma (solo para cronometrar la evidencia;
    el server verifica todo)."""
    import jwt as pyjwt
    claims = pyjwt.decode(token, options={"verify_signature": False,
                                          "verify_exp": False})
    return int(claims["exp"])


def wait_db(sql, params, timeout, interval=3):
    deadline = time.time() + timeout
    rows = []
    while time.time() < deadline:
        rows = e2e.db(sql, params)
        if rows:
            return rows
        time.sleep(interval)
    return rows


def hermes_config(*args):
    r = subprocess.run(["hermes", *args], capture_output=True, text=True,
                       env={**os.environ, "HERMES_HOME": HOME_DIR})
    return (r.stdout or r.stderr).strip()


def admin_session():
    """Login PKCE+TOTP real del admin del cliente → session_token."""
    import evidence_hermes_real_bridge as bridge
    bridge.SLUG = SLUG
    admin_uuid = e2e.kc_user_uuid(ADMIN_EMAIL)
    bridge.reset_totp(admin_uuid)
    bridge.force_configure_totp(admin_uuid)
    e2e.COOKIES.clear()
    id_token, acr = e2e.pkce_login(ADMIN_EMAIL, ADMIN_PASSWORD,
                                   new_password=ADMIN_PASSWORD,
                                   totp_state={}, acr_values="2")
    check("admin re-login PKCE+TOTP real → acr=2", str(acr) == "2", f"acr={acr}")
    return e2e.mint(id_token)["session_token"]


def worker_id_token():
    """Login PKCE+TOTP real del empleado → (id_token, acr)."""
    S = load_state()
    e2e.COOKIES.clear()
    totp_state = {"secret": S["worker_totp_secret"]}
    return e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                          totp_state=totp_state, acr_values="2")


# ===========================================================================
# setup — bundle fresco (plugin NUEVO) → install → login → firstrun → approve
# ===========================================================================
def phase_setup():
    S = load_state()
    e2e.SLUG = SLUG
    print("=== SETUP: bundle fresco del link → home fresco → enrolado ===",
          flush=True)

    # Higiene PREVIA al rm -rf (hallazgos QA t_c260323f #1/#2): primero se
    # mata la sesión tmux previa y TODO proceso con este HERMES_HOME en el
    # entorno (el gateway sobrevive a tmux kill-session y racea el borrado
    # con "Directory not empty", además de contaminar la corrida nueva).
    subprocess.run(["tmux", "kill-session", "-t", TMUX], capture_output=True)
    victims = kill_home_processes()
    if victims:
        print(f"    procesos residuales del home terminados: {victims}",
              flush=True)

    # Run id de esta corrida (hallazgo QA #5): se estampa ANTES del primer
    # artefacto para que TODAS las fases escriban en su propio subdirectorio.
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    rn_prev = load_rn_state()
    rn_prev["run_id"] = run_id
    save_rn_state(rn_prev)
    refresh_latest_symlink()
    print(f"    evidence dir de esta corrida: {evidence_dir()}", flush=True)

    # 1) Bundle FRESCO del endpoint público (lleva el plugin con el fix —
    #    se construye in-memory desde la imagen reconstruida).
    os.makedirs(f"{E2E_ROOT}/rn-bundle", exist_ok=True)
    tgz = f"{E2E_ROOT}/rn-bundle/trust-agent-{SLUG}.tar.gz"
    import urllib.request
    with urllib.request.urlopen(
            f"http://127.0.0.1:8000/download/{SLUG}/bundle", timeout=60) as r:
        data = r.read()
    with open(tgz, "wb") as f:
        f.write(data)
    check("S1 GET /download/{slug}/bundle → tar.gz fresco (in-memory)",
          len(data) > 10000, f"{len(data)} bytes")

    # 2) Home fresco + install.
    subprocess.run(["rm", "-rf", HOME_DIR], check=True)
    os.makedirs(f"{E2E_ROOT}/rn-bundle/pkg", exist_ok=True)
    subprocess.run(["tar", "-xzf", tgz, "-C", f"{E2E_ROOT}/rn-bundle/pkg"],
                   check=True)
    out = subprocess.run(
        [f"{E2E_ROOT}/rn-bundle/pkg/trust-agent-{SLUG}/install.sh",
         "--home", HOME_DIR],
        capture_output=True, text=True, timeout=600)
    evidence_dump("00-install.log", out.stdout + "\n" + out.stderr)
    check("S2 ./install.sh --home fresco en verde", out.returncode == 0,
          out.stderr[-200:] if out.returncode else "")
    check("S2 plugin NUEVO instalado (bundle_client con sliding renewal)",
          "save_renewed_device_token" in open(
              f"{HOME_DIR}/plugins/trust-agent-governance/bundle_client.py"
          ).read())
    check("S2 enroll NUEVO instalado (recover_expired_token)",
          "recover_expired_token" in open(
              f"{HOME_DIR}/plugins/trust-agent-governance/enroll.py"
          ).read())

    # 3) Worker login (PKCE+TOTP real) → id-token.txt + virtual key.
    id_token, acr = worker_id_token()
    check("S3 worker login PKCE+TOTP real → acr=2", str(acr) == "2", f"acr={acr}")
    token_file = f"{HOME_DIR}/trust-agent/id-token.txt"
    with open(token_file, "w") as f:
        f.write(id_token)
    os.chmod(token_file, 0o600)
    sess = e2e.mint(id_token, kind="desktop")
    st, body, raw = e2e.api("POST", "/api/models/keys", payload={},
                            bearer=sess["session_token"])
    key = body.get("key") if isinstance(body, dict) else None
    check("S3 virtual key self-emitida (POST /api/models/keys)", st in (200, 201),
          f"HTTP {st}")
    if key:
        subprocess.run(["hermes", "config", "set", "model.api_key", key,
                        "--force"], capture_output=True, text=True,
                       env={**os.environ, "HERMES_HOME": HOME_DIR})

    # 4) Firstrun: auto-enroll zero-UI con sync rápido (30 s).
    tmux_run(chat_cmd())
    started = time.time()
    rows = wait_db(
        "SELECT id, status FROM devices WHERE tenant_id=%s AND user_id=%s "
        "AND status='pending' AND enrolled_at >= to_timestamp(%s)",
        (S["tenant_id"], S["worker_user_id"], started - 60), timeout=150)
    check("S4 auto-enroll zero-UI: device PENDING en BD", bool(rows),
          f"devices={rows if rows else 'ninguno'}")
    device_id = str(rows[0][0]) if rows else None

    # 5) Approve por el admin del cliente.
    admin = admin_session()
    st, body, raw = e2e.api("POST", f"/api/devices/{device_id}/approve",
                            bearer=admin)
    check("S5 admin aprueba el device → active",
          st == 200 and body.get("status") == "active", f"HTTP {st}")

    deadline = time.time() + 90
    token_ok = False
    while time.time() < deadline:
        data = token_file_data()
        if data and data.get("device_id") and data.get("token"):
            token_ok = True
            break
        time.sleep(3)
    check("S5 el agente canjea → device-token.json en el home", token_ok)
    if not token_ok:
        return 1

    data = token_file_data()
    first_exp = token_exp(data["token"])
    save_rn_state({
        "run_id": run_id,
        "device_id": device_id,
        "first_token": data["token"],
        "first_exp": first_exp,
        "first_renewed_at": data.get("renewed_at"),
        # Baseline de contadores del plugin.log al FIN del setup: la fase
        # renewal mide el CRECIMIENTO desde aquí (re-ejecuciones incluidas).
        "hb_baseline": plugin_log().count("heartbeat_ok"),
        "renewed_baseline": plugin_log().count("device_token_renewed"),
        "setup_at": int(time.time()),
    })
    print(f"    device={device_id} first_exp={first_exp} "
          f"(TTL window corre desde ahora)", flush=True)
    evidence_dump("01-plugin-log-setup.txt", plugin_log())
    print(f"\nSETUP: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# renewal — AC-HRB-010(1): NORMAL sostenido MÁS ALLÁ del TTL original
# ===========================================================================
def phase_renewal():
    rn = load_rn_state()
    first_exp = rn["first_exp"]
    print("=== RENEWAL: sesión viva > TTL original (sliding renewal) ===",
          flush=True)
    print(f"    first_exp={first_exp} — esperando hasta pasarlo "
          f"({max(0, first_exp - time.time()):.0f}s restantes)", flush=True)

    renewals_before = rn.get("renewed_baseline", 0)
    hb_before = rn.get("hb_baseline", 0)
    unauthorized_seen = False

    # Vivimos hasta first_exp + margen: si NO hay renovación, el token
    # original muere en first_exp y el sync da 401 (el bug de t_0b6e8e2d).
    deadline = first_exp + 120
    while time.time() < deadline:
        time.sleep(30)
        log = plugin_log()
        if "device_unauthorized" in log:
            unauthorized_seen = True
            break
        print(f"    t+{time.time() - (first_exp - 900):.0f}s "
              f"heartbeat_ok={log.count('heartbeat_ok')} "
              f"renewed={log.count('device_token_renewed')}", flush=True)

    log = plugin_log()
    check("R1 SIN device_unauthorized más allá del TTL original (el bug era "
          "401 permanente tras la expiración)", not unauthorized_seen)
    check("R2 heartbeat_ok sostenidos durante toda la ventana",
          log.count("heartbeat_ok") > hb_before + 3,
          f"heartbeat_ok={log.count('heartbeat_ok')}")
    check("R3 device_token_renewed registrado por el plugin (sliding renewal)",
          log.count("device_token_renewed") > renewals_before + 1,
          f"renewed={log.count('device_token_renewed')}")

    data = token_file_data()
    check("R4 device-token.json ROTÓ (token != original, renewed_at avanza)",
          data and data["token"] != rn["first_token"]
          and data.get("renewed_at", 0) > (rn.get("first_renewed_at") or 0),
          f"renewed_at={data.get('renewed_at') if data else None}")
    now = time.time()
    check("R5 el token VIGENTE expira DESPUÉS del TTL original (ventana "
          "deslizante viva)", data and token_exp(data["token"]) > now + 600,
          f"exp={token_exp(data['token']) if data else None} now={now:.0f}")
    check("R6 hemos vivido MÁS ALLÁ del exp del primer token", now > first_exp,
          f"now-first_exp={now - first_exp:.0f}s")

    # Modo NORMAL reportado por el agente (governance_status en vivo).
    # Con reintentos: el primer mensaje a un TUI fresco se puede tragar en
    # el boot lazy del gateway (hallazgo QA t_c260323f #3).
    gs = ask_agent(
        "Run the governance_status tool and show me its complete output.",
        like="%governance%", needle="NORMAL")
    check("R7 governance_status EN VIVO: modo NORMAL tras superar el TTL",
          gs is not None, str(gs)[:160] if gs else "sin respuesta")

    rows = e2e.db(
        "SELECT status, last_heartbeat_at >= now() - interval '120 seconds' "
        "FROM devices WHERE id = %s", (rn["device_id"],))
    check("R8 BD: device active con heartbeat fresco (<2 min)",
          bool(rows) and rows[0][0] == "active" and rows[0][1],
          f"row={rows}")

    evidence_dump("02-plugin-log-renewal.txt", plugin_log())
    d = evidence_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "02-device-token-renewal.json"), "w") as f:
        redacted = dict(data or {})
        if redacted.get("token"):
            redacted["token"] = redacted["token"][:24] + "...REDACTED"
        json.dump(redacted, f, indent=2)
    evidence_dump("03-renewal-pane.txt", pane())
    print(f"\nRENEWAL: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# expiryrecovery — AC-HRB-010(3): token muerto → trust-login → auto-recovery
# ===========================================================================
def phase_expiryrecovery():
    rn = load_rn_state()
    print("=== EXPIRY-RECOVERY: agente apagado > TTL → recuperación sin admin "
          "===", flush=True)

    # 1) Apagar el agente DE VERDAD y esperar la muerte REAL del token.
    #    Un gateway sobreviviente siguió renovando el token durante la
    #    ventana OFF y falseó E1/E3/E4/E6 (hallazgo QA t_c260323f #2):
    #    matar por /proc/<pid>/environ, no solo la sesión tmux.
    subprocess.run(["tmux", "kill-session", "-t", TMUX], capture_output=True)
    victims = kill_home_processes()
    check("E0 agente APAGADO de verdad (0 procesos vivos con este "
          "HERMES_HOME tras el kill)", not home_pids(),
          f"terminados={victims}")
    data = token_file_data()
    exp = token_exp(data["token"])
    wait = exp - time.time() + 30
    print(f"    agente OFF; token expira en {max(0, wait):.0f}s — esperando",
          flush=True)
    if wait > 0:
        time.sleep(wait)
    check("E1 el token en disco está EXPIRADO (exp < now)",
          token_exp(token_file_data()["token"]) < time.time())

    # 2) El empleado re-autentica (trust-login: SSO+MFA → id-token.txt fresco).
    id_token, acr = worker_id_token()
    check("E2 trust-login real (PKCE+TOTP) → id_token fresco acr=2",
          id_token is not None and str(acr) == "2", f"acr={acr}")
    token_file = f"{HOME_DIR}/trust-agent/id-token.txt"
    with open(token_file, "w") as f:
        f.write(id_token)
    os.chmod(token_file, 0o600)

    # 3) Arrancar el agente: el BundleSyncer detecta la expiración y se
    #    recupera SOLO (POST /api/devices/enroll → 200 directo).
    dead_token = token_file_data()["token"]
    tmux_run(chat_cmd())
    recovered = False
    deadline = time.time() + 180
    while time.time() < deadline:
        log = plugin_log()
        if "device_token_recovered" in log:
            recovered = True
            break
        time.sleep(5)
    check("E3 recuperación AUTOMÁTICA: device_token_expired → "
          "device_token_recovered en el MISMO arranque", recovered)
    log = plugin_log()
    check("E4 la expiración se reportó como token_expired (NO revoked)",
          "device_token_expired" in log
          and "device_unauthorized" not in log.split("device_token_expired")[-1],
          "razón honesta en plugin.log")

    data = token_file_data()
    check("E5 device-token.json tiene un token NUEVO y VIGENTE",
          data and data["token"] != dead_token
          and token_exp(data["token"]) > time.time(),
          f"exp={token_exp(data['token']) if data else None}")

    # 6) El server lo audita como re-emisión directa (200, sin admin).
    rn2 = load_rn_state()
    rows = e2e.db(
        "SELECT count(*) FROM audit_log WHERE tenant_id=%s "
        "AND action='device.token_reissued' AND device_id=%s",
        (load_state()["tenant_id"], rn["device_id"]))
    check("E6 audit device.token_reissued en la BD del tenant (camino 200 "
          "directo, sin aprobación admin)", bool(rows) and rows[0][0] >= 1,
          f"count={rows[0][0] if rows else 0}")

    # 7) Modo NORMAL de vuelta, sin intervención admin. Con reintentos:
    #    el arranque recién hecho dispara el boot lazy del gateway y el
    #    primer mensaje se puede tragar (hallazgo QA t_c260323f #3).
    gs = ask_agent(
        "Run the governance_status tool and show me its complete output.",
        like="%governance%", needle="NORMAL")
    check("E7 governance_status EN VIVO: NORMAL de vuelta sin admin",
          gs is not None, str(gs)[:160] if gs else "sin respuesta")

    evidence_dump("04-plugin-log-expiryrecovery.txt", plugin_log())
    evidence_dump("05-expiryrecovery-pane.txt", pane())
    print(f"\nEXPIRY-RECOVERY: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# revocation — AC-HRB-010(2): fail-closed inmediato intacto
# ===========================================================================
def phase_revocation():
    rn = load_rn_state()
    print("=== REVOCATION: admin revoca a mitad de sesión → 401 → RESTRICTED "
          "===", flush=True)
    # Baseline de transiciones restricted PREVIAS (re-ejecuciones): V3 exige
    # un mode_change NUEVO en esta corrida, no uno heredado del plugin.log.
    restricted_before = sum(1 for e in mode_change_events()
                            if e.get("mode") == "restricted")
    admin = admin_session()
    st, body, raw = e2e.api("POST", f"/api/devices/{rn['device_id']}/revoke",
                            bearer=admin)
    check("V1 admin revoca el device (sweep integral) → 200",
          st == 200, f"HTTP {st} {raw[:160]}")

    # El siguiente ciclo (≤ sync interval + margen) es 401 → restricted.
    saw_401 = False
    deadline = time.time() + SYNC_INTERVAL + 90
    while time.time() < deadline:
        if "device_unauthorized" in plugin_log():
            saw_401 = True
            break
        time.sleep(5)
    check("V2 siguiente ciclo: device_unauthorized (401) en plugin.log",
          saw_401)

    # V3: RESTRICTED verificado con el mode_change que el propio plugin
    # registra en plugin.log. Preguntarle al chat es ESTRUCTURALMENTE
    # imposible tras un sweep integral: REQ-GOH-011 bloquea la virtual
    # key del worker → el LLM responde 401 "Key is blocked" y el agente
    # no puede contestar (hallazgo QA t_c260323f #4 — comportamiento
    # correcto del producto, fail-closed integral).
    restricted = None
    deadline = time.time() + 60
    while time.time() < deadline:
        evts = [e for e in mode_change_events()
                if e.get("mode") == "restricted"]
        if len(evts) > restricted_before:
            restricted = evts[-1]
            break
        time.sleep(5)
    check("V3 RESTRICTED inmediato: mode_change→restricted registrado por "
          "el plugin (el chat es inalcanzable tras el sweep integral — se "
          "lee plugin.log)",
          restricted is not None,
          json.dumps(restricted)[:200] if restricted else "sin mode_change")

    rows = e2e.db("SELECT status FROM devices WHERE id = %s", (rn["device_id"],))
    check("V4 BD: device REVOKED", rows and rows[0][0] == "revoked", f"{rows}")
    check("V5 la renovación JAMÁS relajó la revocación: sin heartbeat_ok "
          "nuevo tras el 401",
          plugin_log().split("device_unauthorized")[-1].count("heartbeat_ok") == 0)

    evidence_dump("06-plugin-log-revocation.txt", plugin_log())
    evidence_dump("07-revocation-pane.txt", pane())
    print(f"\nREVOCATION: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    print("NOTA: la revocación integral también bloqueó la virtual key del "
          "worker (sweep REQ-GOH-011) — restaurarla es flujo de budget, no de "
          "esta corrida.", flush=True)
    return 1 if FAILURES else 0


# ===========================================================================
# audit — resumen de la corrida en el audit_log de la org
# ===========================================================================
def phase_audit():
    S = load_state()
    rn = load_rn_state()
    print("=== AUDIT: la corrida en el audit_log del tenant ===", flush=True)
    rows = e2e.db(
        "SELECT action, count(*) FROM audit_log WHERE tenant_id=%s "
        "GROUP BY action ORDER BY action", (S["tenant_id"],))
    evidence_dump("08-audit-summary.txt",
                  "\n".join(f"{a}: {c}" for a, c in rows))
    print("    " + ", ".join(f"{a}={c}" for a, c in rows), flush=True)
    actions = {a: c for a, c in rows}
    for action in ("device.enrolled", "device.approved", "device.heartbeat",
                   "device.token_reissued", "device.revoked"):
        check(f"A1 audit '{action}' presente", actions.get(action, 0) > 0,
              f"count={actions.get(action, 0)}")
    rows = e2e.db(
        "SELECT payload->'fingerprint', ts FROM audit_log "
        "WHERE tenant_id=%s AND action='device.token_reissued' "
        "AND device_id=%s ORDER BY seq", (S["tenant_id"], rn["device_id"]))
    print(f"    re-emisiones del device de esta corrida: {rows}", flush=True)
    check("A2 device.token_reissued lleva fingerprint en el payload",
          bool(rows) and all(r[0] for r in rows))
    print(f"\nAUDIT: {PASSES} PASS, {len(FAILURES)} FAIL", flush=True)
    return 1 if FAILURES else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["setup", "renewal", "expiryrecovery",
                             "revocation", "audit"])
    args = ap.parse_args()
    rc = {
        "setup": phase_setup,
        "renewal": phase_renewal,
        "expiryrecovery": phase_expiryrecovery,
        "revocation": phase_revocation,
        "audit": phase_audit,
    }[args.phase]()
    sys.exit(rc)


if __name__ == "__main__":
    main()

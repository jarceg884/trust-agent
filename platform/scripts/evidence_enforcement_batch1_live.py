#!/usr/bin/env python3
"""EVIDENCIA LIVE — spec 030 (t_7e2852f8): enforcement batch 1 en sesión REAL.

Ejercita los 8 requisitos de la spec 030 contra el control plane VIVO
(127.0.0.1:8000) con el perfil Hermes REAL enrolado (`trust-live-inc1`,
dispositivo `fp-live-inc1` del tenant `litellm-real4` en la DB viva) y el
plugin `trust-agent-governance` activo (código fuente sincronizado al perfil
antes de las sesiones). Nada simulado: cada veto ocurre en una sesión live
(`hermes -p trust-live-inc1 -z`) cuyo bundle JWS se re-sincroniza contra el
control plane con las policies org reales del tenant.

Fases (cada fase = PUT de policies org → warm-up que sincroniza el bundle →
sesión de evidencia). NOTA: `hermes -z` fuerza HERMES_YOLO_MODE=1 en toda
sesión one-shot (hermes_cli/oneshot.py: "Non-interactive by definition"), así
que el modo efectivo de aprobación de TODA sesión -z es `off`. Las fases A y B
ponen approval_mode=off para aislar sus keys del veto P-1; la fase C sube el
piso a manual y demuestra el veto P-1 contra el YOLO forzado.

  A  approval_mode=off, allowed_terminal_backends=["docker"],
     web_domain_policy=allowlist [example.org], private-net denegada:
       A1 terminal `echo hello`        → veto P-2 (backend local ∉ [docker])
       A2 write_file /tmp/batch1-*.txt → veto P-5 (fuera del sandbox=cwd)
       A3 web_extract 127.0.0.1:8000   → veto P-20 (red privada)
       A4 web_extract https://evil.com → veto P-19 (dominio fuera de allowlist)
  B  allowed_terminal_backends=["local"]:
       B1 terminal `TRUST_SECRET_X=1 echo hello` → veto P-18 (env passthrough)
       B2 terminal `hermes update`               → veto P-29 (self-update)
       B3 terminal `echo hello`                  → CONTROL POSITIVO: ejecuta
  C  approval_mode=manual (el -z ya fuerza HERMES_YOLO_MODE=1):
       C1 terminal `echo hello` → veto P-1 (modo efectivo off < piso manual)
  D  providers (P-26, pre_llm_call — contexto, no veto de llamada):
       D1 control negativo: config custom→gateway → SIN mensaje de provider
       D2 control positivo: fallback_providers [anthropic] en config.yaml →
           veto contado (provider_not_allowed) en plugin.log → config restaurada

Verificaciones por veto: stdout de la sesión con el mensaje
"BLOCKED by organization policy", filas `tool_vetoed` (layer=config_batch1)
en plugin.log del perfil, conteos en veto-counts.json, y attestation
(device.attestation en audit_log del tenant) reportando los vetos.

Al terminar RESTAURA las 9 policies org a su valor original (GET antes,
PUT/DELETE después) y el config.yaml del perfil a su contenido original.

Uso:
  platform/.venv/bin/python scripts/evidence_enforcement_batch1_live.py \
      --slug litellm-real4 --profile trust-live-inc1 --fingerprint fp-live-inc1
"""
import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import revalidate_litellm_real as r  # baile PKCE/TOTP + helpers probados
import evidence_revocation_live as rv  # login_acr2 (acr=2 probado)

PLUGIN_SRC = Path("/root/trust-agent/platform/hermes/plugins/"
                  "trust-agent-governance")
PROFILE_HOME = Path("/root/.hermes/profiles")
BLOCK = "BLOCKED by organization policy"

# Las 9 keys org que la evidencia toca (8 del batch + allowed_tools, que
# debe incluir las tools de prueba para que el veto del batch 1 —y no el
# deny-list de allowed_tools (spec 022)— sea el que dispare).
BATCH1_KEYS = (
    "allowed_tools", "approval_mode", "allowed_terminal_backends",
    "require_write_sandbox", "allowed_env_passthrough", "web_domain_policy",
    "allow_private_network_access", "allowed_providers", "allow_self_updates",
)

TOOLS_FOR_TESTS = [
    "web_search", "web_extract", "read_file", "write_file", "patch",
    "terminal", "process_manage", "execute_code", "governance_status",
]

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    return ok


def db(sql, params=None, fetch=True):
    return r.db(sql, params, fetch)


# ---------------------------------------------------------------------------
# Policies org (admin con MFA)
# ---------------------------------------------------------------------------
def get_org_policies(bearer: str) -> dict:
    st, body, raw = r.api("GET", "/api/policies?level=org", bearer=bearer)
    assert st == 200, f"GET policies org HTTP {st}: {raw[:200]}"
    return {p["key"]: p["value"] for p in body["policies"]}


def put_org(bearer: str, key: str, value) -> None:
    st, body, raw = r.api("PUT", "/api/policies/org",
                          {"key": key, "value": value}, bearer=bearer)
    assert st == 200, f"PUT {key} HTTP {st}: {raw[:200]}"


def del_org(bearer: str, key: str) -> None:
    st, body, raw = r.api(
        "DELETE", f"/api/policies/org/{urllib.parse.quote(key)}",
        bearer=bearer)
    assert st in (200, 204), f"DELETE {key} HTTP {st}: {raw[:200]}"


# ---------------------------------------------------------------------------
# Plugin: sincronización fuente → perfil + log del perfil
# ---------------------------------------------------------------------------
def sync_plugin(profile: str) -> list[str]:
    dst = PROFILE_HOME / profile / "plugins" / "trust-agent-governance"
    dst.mkdir(parents=True, exist_ok=True)
    synced = []
    for src in sorted(PLUGIN_SRC.iterdir()):
        if src.suffix != ".py" and src.name != "plugin.yaml" \
                and src.suffix != ".pem":
            continue
        if src.name == "capability_enforcement.py":
            # Módulo de la lane hermana (batch 2): se sincroniza SOLO si
            # compila — si está a medio escribir, se omite y el cableado
            # try/except de __init__ lo tolera.
            try:
                py_compile.compile(str(src), doraise=True)
            except py_compile.PyCompileError as exc:
                print(f"  [sync] {src.name} omitido (no compila): {exc}")
                continue
        shutil.copy2(src, dst / src.name)
        synced.append(src.name)
    pycache = dst / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
    return synced


def nudge_gateway_reload(profile: str) -> dict | None:
    """Fuerza re-discovery del plugin en el gateway host (control socket
    ``reload-plugins``).

    Modelo un-gateway-por-host: las tools de las sesiones las ejecuta el
    gateway multiplexor, que cachea el plugin manager por perfil y retiene el
    CÓDIGO VIEJO del plugin en memoria. Sin este nudge, las sesiones de
    evidencia serían evaluadas por la instancia anterior del plugin (que
    resolvía el home del gateway en vez del perfil → modo restringido espurio,
    vetos fail-closed en lugar de los vetos de policy del batch 1).
    """
    code = (
        "import json, sys; sys.path.insert(0, '/usr/local/lib/hermes-agent')\n"
        "from pathlib import Path\n"
        "from gateway.control_socket import reload_gateway_plugins\n"
        f"print(json.dumps(reload_gateway_plugins("
        f"Path('/root/.hermes'),"
        f" profile_home=Path({str(PROFILE_HOME / profile)!r}))))\n"
    )
    proc = subprocess.run(["hermes", "--print-runtime-command"],
                          capture_output=True, text=True, timeout=30)
    runtime = json.loads(proc.stdout)
    py = runtime[0]
    nudge = subprocess.run([py, "-I", "-c", code],
                           capture_output=True, text=True, timeout=60)
    try:
        return json.loads(nudge.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"  [nudge] stderr: {nudge.stderr.strip()[:200]}")
        return None


def plugin_log(profile: str) -> list[dict]:
    path = PROFILE_HOME / profile / "trust-agent" / "plugin.log"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def veto_counts(profile: str) -> dict:
    path = PROFILE_HOME / profile / "trust-agent" / "veto-counts.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def batch1_veto_reasons(profile: str, since_ts: float) -> dict:
    """reason → count de los vetos del batch 1 desde since_ts."""
    reasons: dict[str, int] = {}
    for row in plugin_log(profile):
        if row.get("event") != "tool_vetoed" or row.get("layer") != "config_batch1":
            continue
        if float(row.get("ts", 0)) < since_ts:
            continue
        reason = str(row.get("reason", "")).split(" (")[0]
        reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


# ---------------------------------------------------------------------------
# Sesiones live (hermes -z) con env controlado
# ---------------------------------------------------------------------------
def run_session(profile: str, prompt: str, *, cwd: Path,
                extra_env: dict | None = None, timeout: int = 300):
    # Aislamiento del env: el worker kanban corre DENTRO de un gateway host
    # (_HERMES_GATEWAY, HERMES_GATEWAY_SESSION, HERMES_RPC_*, ...). Si la
    # sesión hija hereda ese routing, sus tool calls los ejecuta el gateway
    # (perfil default, SIN device token → modo restringido) y los vetos que
    # se observan son los de fail-closed, no los del batch 1. Se strippea
    # todo prefijo HERMES_*/_HERMES_* (mismo aislamiento que el tool
    # terminal del core); la sesión hija resuelve su propio HERMES_HOME
    # desde --profile y ejecuta las tools in-process con su bundle.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HERMES_", "_HERMES_"))}
    env["TRUST_SYNC_INTERVAL"] = "20"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["hermes", "-p", profile, "-z", prompt],
        capture_output=True, text=True, cwd=str(cwd), env=env,
        timeout=timeout)
    return proc.stdout, proc.stderr, proc.returncode


def warmup(profile: str, cwd: Path) -> None:
    """Sesión mínima: fuerza el primer sync del bundle con la policy vigente
    para que la sesión de evidencia arranque desde caché fresca (modo normal,
    policy de la fase) y no desde el bundle vencido (modo restringido)."""
    out, err, code = run_session(profile, "Reply with exactly: OK", cwd=cwd)
    print(f"  [warmup] exit={code} out={out.strip()[:60]!r}"
          + (f" err={err.strip()[:120]!r}" if err.strip() else ""))


def evidence_session(profile: str, prompt: str, cwd: Path,
                     label: str, expected_reasons: list[str],
                     since_ts: float, extra_env: dict | None = None):
    print(f"  [session {label}] corriendo...", flush=True)
    out, err, code = run_session(profile, prompt, cwd=cwd,
                                 extra_env=extra_env)
    text = out + "\n" + err
    print(f"  [session {label}] exit={code} stdout={len(out)} chars")
    blocks = text.count(BLOCK)
    check(f"{label}: stdout contiene vetos '{BLOCK}'",
          blocks >= len(expected_reasons),
          f"{blocks} ocurrencias en stdout")
    reasons = batch1_veto_reasons(profile, since_ts)
    for reason in expected_reasons:
        check(f"{label}: plugin.log registra veto '{reason}'",
              reasons.get(reason, 0) >= 1,
              f"reasons vistos: {reasons}")
    return out, err, code


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="litellm-real4")
    ap.add_argument("--profile", default="trust-live-inc1")
    ap.add_argument("--fingerprint", default="fp-live-inc1")
    args = ap.parse_args()
    slug, profile = args.slug, args.profile
    r.SLUG = slug

    # El redirect URI del cliente `trust-agent-web` cambió con las lanes de
    # auth-UX (spec 015): el constante de revalidate (8765) ya no está
    # registrado en realms nuevos. Resolver el vigente del realm real.
    kc_token = r.kc_admin_token()
    st, raw = r.kc_json(
        "GET", f"/admin/realms/{slug}/clients?clientId=trust-agent-web",
        token=kc_token)
    clients = json.loads(raw) if st == 200 else []
    redirects = (clients[0].get("redirectUris") if clients else None) or []
    valid = [u for u in redirects if u.startswith("http://127.0.0.1")]
    assert valid, f"no 127.0.0.1 redirect URI for trust-agent-web in {slug}"
    r.REDIRECT_URI = valid[0]
    print(f"[live] redirect URI del realm: {r.REDIRECT_URI}")

    # cwd de las sesiones: subdirectorio dedicado — el sandbox P-5 toma el
    # cwd como raíz, así /tmp/batch1-*.txt queda FUERA.
    cwd = Path("/tmp/batch1-live-cwd")
    cwd.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- R0
    print("=== R0: control plane vivo ===", flush=True)
    st, body, raw = r.api("GET", "/healthz")
    check("R0 api /healthz → 200", st == 200, raw[:80])

    rows = db(
        "SELECT d.id, u.email FROM devices d JOIN users u ON u.id = d.user_id "
        "WHERE d.fingerprint = %s", (args.fingerprint,))
    assert rows, f"device {args.fingerprint} not found in live DB"
    device_id, worker_email = str(rows[0][0]), rows[0][1]
    print(f"[live] device={device_id} user={worker_email} tenant={slug}")

    # -------------------------------------------------------------- R1
    print("=== R1: login REAL PKCE+TOTP (acr=2): worker + org_admin ===",
          flush=True)
    worker, _ = rv.login_acr2(slug, worker_email, r.WORKER_PASSWORD)
    check("R1 sesión worker con MFA", worker.get("mfa_verified") is True,
          f"mfa={worker.get('mfa_verified')}")
    admin_email = f"admin@{slug}.test"
    admin, _ = rv.login_acr2(slug, admin_email, r.ADMIN_NEW_PASSWORD)
    admin_bearer = admin["session_token"]
    check("R1 sesión org_admin con MFA", admin.get("mfa_verified") is True,
          f"mfa={admin.get('mfa_verified')}")

    # -------------------------------------------------------------- R2
    print("=== R2: snapshot de policies org (para restaurar al final) ===",
          flush=True)
    original = get_org_policies(admin_bearer)
    print(f"[live] policies org actuales: {sorted(original)}")
    for key in BATCH1_KEYS:
        print(f"  {key}: "
              f"{'SET ' + json.dumps(original[key])[:80] if key in original else 'ABSENT'}")

    # -------------------------------------------------------------- R3
    print("=== R3: renovación del device token + sync del plugin ===",
          flush=True)
    st, body, raw = r.api("POST", f"/api/devices/{device_id}/token",
                          bearer=worker["session_token"])
    assert st == 200, f"token renew HTTP {st}: {raw[:200]}"
    trust = PROFILE_HOME / profile / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": device_id, "token": body["device_token"]}),
        encoding="utf-8")
    check("R3 device token renovado (TTL "
          f"{body.get('expires_in')}s, nunca impreso)", True)
    synced = sync_plugin(profile)
    check("R3 plugin sincronizado al perfil",
          "config_enforcement.py" in synced and "__init__.py" in synced,
          f"{len(synced)} archivos: {', '.join(synced)}")
    # El gateway host ejecuta las tools de las sesiones y cachea el código del
    # plugin: nudge reload-plugins para que evalúe con el código sincronizado.
    nudge_ts = time.time()
    answer = nudge_gateway_reload(profile)
    check("R3 gateway host recargó el plugin (reload-plugins)",
          bool(answer and answer.get("reloaded")),
          f"reloaded={bool(answer and answer.get('reloaded'))}, "
          f"plugins={len((answer or {}).get('plugins', []))}")
    # Verificación del home context-aware: la instancia recargada del gateway
    # re-registra y su batch1_active debe caer en el plugin.log del PERFIL
    # (fila nueva tras el nudge — el pid del gateway es estable entre corridas,
    # no se puede comparar por pid). En la ventana de polling no hay sesiones
    # hijo corriendo, así que toda fila nueva es del gateway.
    fresh = [row for row in plugin_log(profile)
             if row.get("event") == "batch1_active"
             and float(row.get("ts", 0)) >= nudge_ts]
    for _ in range(8):  # el reload (unload+register) puede tardar >2s
        if fresh:
            break
        time.sleep(2)
        fresh = [row for row in plugin_log(profile)
                 if row.get("event") == "batch1_active"
                 and float(row.get("ts", 0)) >= nudge_ts]
    check("R3 instancia recargada del gateway registra batch1_active en el "
          "plugin.log del PERFIL (home context-aware)",
          bool(fresh), f"filas nuevas: {len(fresh)}")

    # config.yaml del perfil: backup para restaurar al final. El endpoint de
    # modelo se repunta al gateway LiteLLM REAL (127.0.0.1:4000) con una
    # virtual key REAL del worker — el doble (39989) es la fake de la Admin
    # API: su /chat/completions devuelve "ok" canned, sin LLM no hay tool
    # calls y por tanto no hay vetos que observar.
    cfg_path = PROFILE_HOME / profile / "config.yaml"
    cfg_original = cfg_path.read_text(encoding="utf-8")
    st, body, raw = r.api("POST", "/api/models/keys", {},
                          bearer=worker["session_token"])
    assert st == 201, f"POST /api/models/keys HTTP {st}: {raw[:200]}"
    real_key = body["key"]
    st, body, raw = r.litellm_chat(real_key, "glm-5.2", "Responde solo: OK", 16)
    check("R3 virtual key REAL contra el gateway :4000 → 200 de MaaS",
          st == 200, f"HTTP {st} usage={body.get('usage')}")
    assert st == 200, raw[:300]
    cfg_live = []
    for line in cfg_original.splitlines():
        if line.strip().startswith("base_url:"):
            cfg_live.append("  base_url: http://127.0.0.1:4000/v1")
        elif line.strip().startswith("api_key:"):
            cfg_live.append(f"  api_key: {real_key}")
        else:
            cfg_live.append(line)
    cfg_path.write_text("\n".join(cfg_live) + "\n", encoding="utf-8")
    check("R3 config del perfil repuntado al gateway real "
          "(se restaura al final)", True)

    # Policies de la fase A + warm-up
    print("=== R4: fase A — policies org del batch 1 ===", flush=True)
    phase_a = {
        "allowed_tools": TOOLS_FOR_TESTS,
        "approval_mode": "off",
        "allowed_terminal_backends": ["docker"],
        "require_write_sandbox": True,
        "allowed_env_passthrough": [],
        "web_domain_policy": {"mode": "allowlist", "domains": ["example.org"]},
        "allow_private_network_access": False,
        "allowed_providers": ["litellm_gateway"],
        "allow_self_updates": False,
    }
    for key, value in phase_a.items():
        put_org(admin_bearer, key, value)
    check("R4 policies org fase A aplicadas (9 keys)", True,
          ", ".join(phase_a))
    warmup(profile, cwd)

    # ------------------------------------------------- R5: sesión A (live)
    print("=== R5: sesión live A — vetos P-2/P-5/P-19/P-20 ===", flush=True)
    ts_a = time.time()
    tmp_target = f"/tmp/batch1-live-{int(ts_a)}.txt"
    prompt_a = (
        "Run these 4 tool tests IN ORDER. For each test call the tool EXACTLY "
        "ONCE, then report the VERBATIM tool output you received, labeled "
        "TEST 1..4. Do NOT retry, do NOT use other tools, do NOT paraphrase.\n"
        "TEST 1: terminal — run the command: echo hello\n"
        f"TEST 2: write_file — write the content 'hi' to the path: {tmp_target}\n"
        "TEST 3: web_extract — extract the URL: http://127.0.0.1:8000/healthz\n"
        "TEST 4: web_extract — extract the URL: https://evil.com/x\n"
    )
    out_a, _, _ = evidence_session(
        profile, prompt_a, cwd, "A",
        ["terminal_backend_not_allowed", "write_outside_sandbox",
         "private_network_access", "domain_not_in_allowlist"],
        ts_a)
    check("A: el archivo fuera del sandbox NO fue creado",
          not Path(tmp_target).exists(), tmp_target)

    # ------------------------------------------------- R6: sesión B (live)
    print("=== R6: fase B — backend local permitido; vetos P-18/P-29 ===",
          flush=True)
    put_org(admin_bearer, "allowed_terminal_backends", ["local"])
    warmup(profile, cwd)
    ts_b = time.time()
    prompt_b = (
        "Run these 3 tool tests IN ORDER. For each test call the terminal "
        "tool EXACTLY ONCE, then report the VERBATIM tool output you "
        "received, labeled TEST 1..3. Do NOT retry, do NOT use other tools.\n"
        "TEST 1: run the command: TRUST_SECRET_X=1 echo hello\n"
        "TEST 2: run the command: hermes update\n"
        "TEST 3: run the command: echo hello\n"
    )
    out_b, _, _ = evidence_session(
        profile, prompt_b, cwd, "B",
        ["env_passthrough_not_allowed", "self_update_not_allowed"],
        ts_b)
    check("B: control positivo — `echo hello` ejecutó (hello + exit_code "
          "reales de la tool en stdout)",
          "hello" in out_b.lower() and "exit_code" in out_b,
          "la tool permitida corre sin veto")

    # ------------------------------------------------- R7: sesión C (live)
    print("=== R7: fase C — approval_mode=manual + HERMES_YOLO_MODE=1 ===",
          flush=True)
    put_org(admin_bearer, "approval_mode", "manual")
    warmup(profile, cwd)
    ts_c = time.time()
    prompt_c = (
        "Run this tool test: call the terminal tool EXACTLY ONCE with the "
        "command: echo hello. Then report the VERBATIM tool output you "
        "received. Do NOT retry, do NOT use other tools.\n"
    )
    evidence_session(
        profile, prompt_c, cwd, "C",
        ["approval_mode_below_floor"], ts_c,
        extra_env={"HERMES_YOLO_MODE": "1"})

    # ------------------------------------------------- R8: providers (P-26)
    print("=== R8: providers — control negativo y positivo (pre_llm_call) ===",
          flush=True)
    neg = [row for row in plugin_log(profile)
           if row.get("event") == "tool_vetoed"
           and "provider_not_allowed" in str(row.get("reason", ""))
           and float(row.get("ts", 0)) >= ts_a]
    check("R8 control negativo: sesiones A–C SIN veto de provider "
          "(custom→gateway clasificado litellm_gateway)",
          not neg, f"{len(neg)} vetos de provider inesperados")
    # Control positivo: fallback_providers fuera de la allowlist en config
    cfg_live_now = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        cfg_live_now + "\nfallback_providers:\n  - anthropic\n",
        encoding="utf-8")
    try:
        ts_d = time.time()
        out_d, err_d, code_d = run_session(
            profile, "Reply with exactly: OK", cwd=cwd)
        pos = [row for row in plugin_log(profile)
               if row.get("event") == "tool_vetoed"
               and "provider_not_allowed" in str(row.get("reason", ""))
               and float(row.get("ts", 0)) >= ts_d]
        check("R8 control positivo: fallback anthropic → veto de provider "
              "en plugin.log", len(pos) >= 1,
              f"{len(pos)} veto(s): {pos[:1]}")
    finally:
        cfg_path.write_text(cfg_original, encoding="utf-8")
        check("R8 config.yaml del perfil restaurado",
              cfg_path.read_text(encoding="utf-8") == cfg_original)

    # ------------------------------------------------- R9: auditoría
    print("=== R9: auditoría local + attestation ===", flush=True)
    # REQ-030-009: el veto queda en plugin.log (layer=config_batch1) y suma al
    # conteo por tool que la PRÓXIMA attestation fusiona y reporta — tras un
    # envío exitoso el conteo se DRENA (spec 019: la próxima parte de cero),
    # así que el archivo vacío al final es el contrato correcto. La
    # verificación end-to-end: la suma de veto_counts de las attestations del
    # control plane >= vetos del batch registrados en plugin.log en la corrida.
    vetos_run = [row for row in plugin_log(profile)
                 if row.get("event") == "tool_vetoed"
                 and row.get("layer") == "config_batch1"
                 and float(row.get("ts", 0)) >= ts_a]
    check("R9 plugin.log registra los vetos del batch (layer=config_batch1)",
          len(vetos_run) >= 8,
          f"{len(vetos_run)} vetos: "
          + json.dumps({r.get("tool"): sum(1 for x in vetos_run
                                           if x.get("tool") == r.get("tool"))
                        for r in vetos_run}))
    attest = db(
        "SELECT payload FROM audit_log WHERE action = 'device.attestation' "
        "AND tenant_id = (SELECT tenant_id FROM devices WHERE id = CAST(%s AS uuid)) "
        "ORDER BY ts DESC LIMIT 12", (device_id,)) or []
    with_counts = []
    for row in attest:
        payload = row[0]
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except ValueError:
            continue
        vc = (data or {}).get("veto_counts") or {}
        if vc:
            with_counts.append(vc)
    check("R9 attestation (device.attestation) reporta veto_counts",
          bool(with_counts), f"últimas con conteo: {with_counts[:2]}")
    reported = sum(sum(vc.values()) for vc in with_counts)
    check("R9 la attestation fusionó los vetos del batch (suma reportada "
          ">= vetos locales de la corrida)",
          reported >= len(vetos_run),
          f"reportados={reported} vs plugin.log={len(vetos_run)}")

    # ------------------------------------------------- R10: restauración
    print("=== R10: restauración de policies org ===", flush=True)
    for key in BATCH1_KEYS:
        if key in original:
            put_org(admin_bearer, key, original[key])
        else:
            del_org(admin_bearer, key)
    after = get_org_policies(admin_bearer)
    restored = all(
        (key in original and after.get(key) == original[key])
        or (key not in original and key not in after)
        for key in BATCH1_KEYS)
    check("R10 policies org restauradas (GET verifica)", restored,
          f"diff: {sorted(set(after) ^ set(original))}")

    # ------------------------------------------------------------- resumen
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n=== RESULTADO: {passed}/{len(RESULTS)} checks PASS ===",
          flush=True)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

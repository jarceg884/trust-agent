#!/usr/bin/env python3
"""EVIDENCIA LIVE — Spec 041 REQ-041-017: escenario "Practicantes" en vivo.

Hermes REAL enrolado (perfil trust-live-prac, dispositivo activo del worker@
policies-ux.test) contra el control plane vivo (:8000), Keycloak real (:8080)
y gateway LiteLLM REAL (:4000, upstream MaaS). Nada simulado.

  R0  control plane vivo + fila devices active
  R1  sesión worker (re-mint si expiró) + efectiva EXACTA vía /api/me/policies
  R2  device token renovado + warm-up (bundle JWS sincronizado)
  R3  gateway real: glm-5.2 (incluido) PASA; glm-5.3 y deepseek (aprobados NO
      incluidos) BLOQUEADOS por la key restringida por policy
  R4  sesión live W: web_search BLOQUEADA + terminal BLOQUEADO (vetos reales)
  R5  sesión live M: request al modelo aprobado glm-5.2 PASA por el gateway
  R6  sesión live X: default glm-5.3 (aprobado NO incluido) → gateway 403
  R7  effective del usuario por API admin EXACTA
  R8  audit: named_policy.created/assigned con keys[]

Uso: platform/.venv/bin/python prac_live_evidence.py <state.json> <log-out>
"""
import http.client
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/trust-agent/platform/scripts")
import e2e_final_live as e2e  # helpers verificados (api/db/pkce)

SLUG = "policies-ux"
TENANT = "d8055abf-66c0-40aa-8553-e3f2006fb78b"
PROFILE = "trust-live-prac"
PROFILE_HOME = Path("/root/.hermes/profiles") / PROFILE
TRUST = PROFILE_HOME / "trust-agent"
BLOCK = "BLOCKED by organization policy"
WORKER_EMAIL = "worker@policies-ux.test"
WORKER_PASSWORD = "PracticantesWorker2026!"
LITELLM_HOST, LITELLM_PORT = "127.0.0.1", 4000

RESULTS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)
    return ok


def db(sql, params=None, fetch=True):
    return e2e.db(sql, params, fetch)


def plugin_log():
    rows = []
    path = TRUST / "plugin.log"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def veto_reasons(since_ts, tool=None):
    reasons = {}
    for row in plugin_log():
        if row.get("event") != "tool_vetoed":
            continue
        if float(row.get("ts", 0)) < since_ts:
            continue
        if tool and row.get("tool") != tool:
            continue
        reason = str(row.get("reason", "")).split(" (")[0]
        reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


def run_session(prompt, *, timeout=300, cwd="/tmp/prac-live-cwd"):
    """Sesión one-shot REAL del perfil enrolado (env aislado del gateway host)."""
    Path(cwd).mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HERMES_", "_HERMES_"))}
    env["TRUST_SYNC_INTERVAL"] = "20"
    proc = subprocess.run(["hermes", "-p", PROFILE, "-z", prompt],
                          capture_output=True, text=True, cwd=cwd, env=env,
                          timeout=timeout)
    return proc.stdout, proc.stderr, proc.returncode


def litellm_chat(key, model, max_tokens=32):
    conn = http.client.HTTPConnection(LITELLM_HOST, LITELLM_PORT, timeout=60)
    conn.request("POST", "/v1/chat/completions",
                 body=json.dumps({"model": model, "max_tokens": max_tokens,
                                  "messages": [{"role": "user",
                                                "content": "Responde solo: OK"}]}).encode(),
                 headers={"Content-Type": "application/json",
                          "Authorization": f"Bearer {key}"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw), raw
    except json.JSONDecodeError:
        return resp.status, {}, raw


def admin_call(path, method="GET", payload=None):
    """API admin vía proxy BFF del console (sesión admin MFA)."""
    import urllib.error
    import urllib.request
    state = json.load(open("/tmp/builder-state.json"))
    cookies = "; ".join(f"{k}={v}" for k, v in state["cookies"].items())
    req = urllib.request.Request(
        "http://127.0.0.1:3100" + path, method=method,
        headers={"Cookie": cookies, "Content-Type": "application/json",
                 "X-CSRF-Token": state["cookies"].get("ta_console_csrf", "")},
        data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def worker_key_from_config():
    """La key virtual del config.yaml (nunca impresa)."""
    for line in (PROFILE_HOME / "config.yaml").read_text().splitlines():
        if line.strip().startswith("api_key:"):
            return line.split("api_key:", 1)[1].strip()
    raise RuntimeError("api_key not found in config.yaml")


def fresh_worker_session(state):
    """Re-mint si la sesión expiró (baile PKCE+TOTP real de nuevo)."""
    st, me, _ = e2e.api("GET", "/api/me", bearer=state["worker_bearer"])
    if st == 200:
        check("R1 sesión worker vigente", True, me.get("email"))
        return state
    print("  [dance] sesión worker expirada → re-login real", flush=True)
    token = e2e.kc_admin_token()
    rows = db("SELECT oidc_subject FROM users WHERE email = %s", (WORKER_EMAIL,))
    sub = str(rows[0][0])
    st, raw = e2e.kc_json("GET", f"/admin/realms/{SLUG}/users/{sub}/credentials",
                          token=token)
    for cred in json.loads(raw):
        if cred.get("type") == "otp":
            e2e.kc_json("DELETE",
                        f"/admin/realms/{SLUG}/users/{sub}/credentials/{cred['id']}",
                        token=token)
    st, raw = e2e.kc_json("GET", f"/admin/realms/{SLUG}/users/{sub}", token=token)
    rep = json.loads(raw)
    rep["requiredActions"] = ["CONFIGURE_TOTP"]
    e2e.kc_json("PUT", f"/admin/realms/{SLUG}/users/{sub}", rep, token=token)
    id_token, acr = e2e.pkce_login(WORKER_EMAIL, WORKER_PASSWORD,
                                   totp_state={}, acr_values="2")
    check("R1 re-login worker PKCE+TOTP acr=2", id_token is not None and str(acr) == "2",
          f"acr={acr}")
    session = e2e.mint(id_token)
    state["worker_bearer"] = session["session_token"]
    with open(sys.argv[1], "w") as fh:
        fh.write(json.dumps(state))
    return state


def main() -> int:
    e2e.SLUG = SLUG
    state = json.load(open(sys.argv[1]))

    # ------------------------------------------------------------------ R0
    print("=== R0: control plane vivo + dispositivo ===", flush=True)
    st, body, raw = e2e.api("GET", "/healthz")
    check("R0 api /healthz → 200", st == 200, raw[:80])
    rows = db("SELECT d.id, d.status, d.fingerprint, d.device_name, u.email "
              "FROM devices d JOIN users u ON u.id = d.user_id "
              "WHERE d.tenant_id = %s", (TENANT,))
    dev = next((r for r in rows if r[1] == "active"), None)
    check("R0 dispositivo REAL activo del worker en la DB viva",
          dev is not None and dev[4] == WORKER_EMAIL,
          f"device={dev[0] if dev else None} status={dev[1] if dev else None} "
          f"fp={str(dev[2])[:16]}… name={dev[3] if dev else None}" if dev else "none")
    device_id = str(dev[0])
    rows = db("SELECT id FROM users WHERE email = %s", (WORKER_EMAIL,))
    state["worker_user_id"] = str(rows[0][0])

    # ------------------------------------------------------------------ R1
    print("=== R1: sesión worker + efectiva EXACTA ===", flush=True)
    state = fresh_worker_session(state)
    bearer = state["worker_bearer"]
    st, eff, _ = e2e.api("GET", "/api/me/policies", bearer=bearer)
    check("R1 GET /api/me/policies → 200", st == 200, f"HTTP {st}")
    if st == 200:
        check("R1 allowed_tools SIN web_search",
              sorted(eff.get("allowed_tools") or []) ==
              ["governance_status", "read_file", "write_file"],
              json.dumps(eff.get("allowed_tools")))
        check("R1 allowed_terminal_backends == []",
              eff.get("allowed_terminal_backends") == [],
              json.dumps(eff.get("allowed_terminal_backends")))
        check("R1 allowed_models == [glm-5.2]",
              eff.get("allowed_models") == ["glm-5.2"],
              json.dumps(eff.get("allowed_models")))
        check("R1 approval_mode == manual", eff.get("approval_mode") == "manual",
              json.dumps(eff.get("approval_mode")))
        check("R1 web_domain_policy == allowlist vacía",
              eff.get("web_domain_policy") == {"mode": "allowlist", "domains": []},
              json.dumps(eff.get("web_domain_policy")))

    # ------------------------------------------------------------------ R2
    print("=== R2: device token + bundle JWS ===", flush=True)
    st, body, raw = e2e.api("POST", f"/api/devices/{device_id}/token",
                            bearer=bearer)
    check("R2 device token renovado (TTL "
          f"{body.get('expires_in') if st == 200 else '?'}s, nunca impreso)",
          st == 200, f"HTTP {st}")
    (TRUST / "device-token.json").write_text(
        json.dumps({"device_id": device_id, "token": body["device_token"]}),
        encoding="utf-8")
    ts_warm = time.time()
    out, err, code = run_session("Reply with exactly: OK")
    print(f"  [warmup] exit={code} out={out.strip()[:60]!r}"
          + (f" err={err.strip()[:120]!r}" if err.strip() else ""), flush=True)
    bundle = TRUST / "governance-bundle.jws"
    check("R2 warm-up exit 0 + bundle JWS sincronizado al perfil",
          code == 0 and bundle.exists(),
          f"exit={code} bundle={bundle.exists()}")
    synced = [r for r in plugin_log()
              if r.get("event") == "bundle_synced" and float(r.get("ts", 0)) >= ts_warm]
    check("R2 plugin.log registra bundle_synced (policy del worker)",
          bool(synced),
          f"policy_version={synced[0].get('policy_version') if synced else None}")

    # ------------------------------------------------------------------ R3
    print("=== R3: gateway LiteLLM real — modelo incluido vs excluidos ===",
          flush=True)
    key = worker_key_from_config()
    st_ok, body_ok, raw_ok = litellm_chat(key, "glm-5.2")
    check("R3 glm-5.2 (aprobado INCLUIDO) → 200 del gateway real",
          st_ok == 200, f"HTTP {st_ok} usage={body_ok.get('usage')}")
    for model in ("glm-5.3", "deepseek-v4.1-flash"):
        st_no, _b, raw_no = litellm_chat(key, model)
        blocked = st_no in (400, 403, 404)
        check(f"R3 {model} (aprobado NO incluido) → BLOQUEADO por el gateway "
              f"(HTTP {st_no})", blocked, raw_no[:160])

    # ------------------------------------------------------------------ R4
    print("=== R4: sesión live — web_search y terminal BLOQUEADOS ===", flush=True)
    ts_w = time.time()
    prompt_w = (
        "Run these 2 tool tests IN ORDER. For each test call the tool EXACTLY "
        "ONCE, then report the VERBATIM tool output you received, labeled "
        "TEST 1..2. Do NOT retry, do NOT use other tools, do NOT paraphrase.\n"
        "TEST 1: web_search — search the internet for: weather in Madrid today\n"
        "TEST 2: terminal — run the command: echo hello\n"
    )
    out_w, err_w, code_w = run_session(prompt_w)
    text_w = out_w + "\n" + err_w
    print(f"  [session W] exit={code_w} stdout={len(out_w)} chars", flush=True)
    blocks = text_w.count(BLOCK)
    check("R4 stdout contiene vetos 'BLOCKED by organization policy' (web+terminal)",
          blocks >= 2, f"{blocks} ocurrencias en stdout")
    reasons_w = veto_reasons(ts_w)
    print(f"  [session W] reasons={reasons_w}", flush=True)
    check("R4 plugin.log: veto de web_search (not in allowed_tools)",
          reasons_w.get("not in allowed_tools", 0) >= 1,
          f"reasons={reasons_w}")
    check("R4 plugin.log: veto de terminal (policy Practicantes)",
          any(reasons_w.get(r, 0) >= 1 for r in
              ("not in allowed_tools", "terminal_backend_not_allowed",
               "approval_mode_below_floor")),
          f"reasons={reasons_w}")
    tools_vetoed = {r.get("tool") for r in plugin_log()
                    if r.get("event") == "tool_vetoed"
                    and float(r.get("ts", 0)) >= ts_w}
    check("R4 los vetos son de web_search y terminal",
          {"web_search", "terminal"} <= tools_vetoed, f"tools={tools_vetoed}")

    # ------------------------------------------------------------------ R5
    print("=== R5: sesión live — modelo aprobado incluido PASA ===", flush=True)
    ts_m = time.time()
    out_m, err_m, code_m = run_session(
        "Reply with exactly: OK and nothing else.")
    ok_reply = code_m == 0 and "OK" in out_m
    check("R5 sesión live con glm-5.2 por el gateway → respuesta real del modelo",
          ok_reply, f"exit={code_m} out={out_m.strip()[:80]!r}")
    no_veto_llm = not [r for r in plugin_log()
                       if r.get("event") == "tool_vetoed"
                       and r.get("tool") == "pre_llm_call"
                       and float(r.get("ts", 0)) >= ts_m]
    check("R5 sin veto de provider (custom→gateway clasificado litellm_gateway)",
          no_veto_llm, "")

    # ------------------------------------------------------------------ R6
    print("=== R6: sesión live — modelo aprobado NO incluido BLOQUEADO ===",
          flush=True)
    cfg_path = PROFILE_HOME / "config.yaml"
    cfg_orig = cfg_path.read_text(encoding="utf-8")
    cfg_x = cfg_orig.replace("default: glm-5.2", "default: glm-5.3")
    assert cfg_x != cfg_orig
    cfg_path.write_text(cfg_x, encoding="utf-8")
    try:
        out_x, err_x, code_x = run_session(
            "Reply with exactly: OK and nothing else.", timeout=240)
        text_x = out_x + "\n" + err_x
        print(f"  [session X] exit={code_x} stdout={len(out_x)} chars", flush=True)
        blocked = ("403" in text_x or "not allowed" in text_x.lower()
                   or "error" in text_x.lower())
        check("R6 default glm-5.3 (aprobado NO incluido) → el gateway rechaza "
              "la llamada (sesión sin respuesta normal)",
              blocked and "OK" != out_x.strip(),
              f"exit={code_x} out={out_x.strip()[:120]!r} err={err_x.strip()[:160]!r}")
    finally:
        cfg_path.write_text(cfg_orig, encoding="utf-8")
        check("R6 config.yaml restaurado (default glm-5.2)",
              cfg_path.read_text(encoding="utf-8") == cfg_orig, "")

    # ------------------------------------------------------------------ R7
    print("=== R7: effective del usuario por API admin ===", flush=True)
    st, effa = admin_call(
        f"/api/proxy/api/policies/effective?user_id={state.get('worker_user_id')}")
    by = {k["key"]: k for k in effa.get("keys", [])} if st == 200 else {}
    check("R7 admin GET /api/policies/effective → 200", st == 200, f"HTTP {st}")
    if st == 200:
        check("R7 allowed_tools == [governance_status, read_file, write_file] (user)",
              sorted(by["allowed_tools"]["effective"]) ==
              ["governance_status", "read_file", "write_file"]
              and by["allowed_tools"]["source"] == "user",
              json.dumps(by["allowed_tools"]))
        check("R7 allowed_terminal_backends == [] (user)",
              by["allowed_terminal_backends"]["effective"] == []
              and by["allowed_terminal_backends"]["source"] == "user",
              json.dumps(by["allowed_terminal_backends"]))
        check("R7 allowed_models == [glm-5.2] (user)",
              by["allowed_models"]["effective"] == ["glm-5.2"]
              and by["allowed_models"]["source"] == "user",
              json.dumps(by["allowed_models"]))

    # ------------------------------------------------------------------ R8
    print("=== R8: audit del flujo nombrado ===", flush=True)
    rows = db("SELECT action, payload FROM audit_log "
              "WHERE tenant_id = %s AND action LIKE 'named_policy.%%' "
              "ORDER BY ts DESC LIMIT 10", (TENANT,))
    actions = {r[0] for r in rows}
    check("R8 audit_log: named_policy.created + named_policy.assigned",
          {"named_policy.created", "named_policy.assigned"} <= actions,
          f"acciones={sorted(actions)}")
    assigned = next((r for r in rows if r[0] == "named_policy.assigned"), None)
    payload = json.loads(assigned[1]) if isinstance(assigned[1], str) else assigned[1]
    check("R8 payload de named_policy.assigned con name=Practicantes + keys[]",
          payload.get("name") == "Practicantes" and bool(payload.get("keys")),
          json.dumps(payload)[:200])

    # ------------------------------------------------------------------ resumen
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n=== RESULTADO: {passed}/{len(RESULTS)} checks PASS ===", flush=True)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

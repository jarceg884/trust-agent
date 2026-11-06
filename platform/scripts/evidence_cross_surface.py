#!/usr/bin/env python3
"""evidence_cross_surface.py — E2E cross-surface de enforcement de mensajerías
y conectores (REQ-PE-011/012, spec 042 GAP-42-A).

Demuestra que bloquear Telegram/conectores por el admin Muerde en TODAS las
superficies del mismo usuario. El script simula dos superficies (desktop + CLI)
del mismo usuario enrolado, aplica el bloqueo y verifica que AMBAS lo hacen
cumplir en su siguiente sync + turno, con la misma policy_version.

Corrida:
    python3 platform/scripts/evidence_cross_surface.py

Sin red real: el bundle se firma RS256 de verdad y se verifica con la clave
pineada (camino completo del plugin). Las dos superficies son dos instancias
del plugin con el mismo device_id (bundle por usuario, bundle.py:225).

Criterios de aceptación verificados:
  AC-011-1: messaging_permissions={"telegram": false} → no despacha por telegram.
  AC-011-2: allowed_connectors=[] → toda tool de conector vetada.
  AC-011-3: fail-closed — key ausente → default restrictivo.
  AC-012-1: evidence_cross_surface.py en vivo, 0 FAIL.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Asegurar que platform/ está en el path para importar el plugin
PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))

PLUGIN_DIR = PLATFORM / "hermes" / "plugins" / "trust-agent-governance"
DEVICE_ID = "11111111-2222-3333-4444-555555555555"
BLOCK_PREFIX = "BLOCKED by organization policy:"

# Colores para output
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

_results: list[tuple[str, bool, str]] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    status = f"{GREEN}PASS{RESET}" if condition else f"{RED}FAIL{RESET}"
    line = f"  [{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    _results.append((name, condition, detail))


def _load_plugin(pkg_name: str):
    """Carga el paquete del plugin con un nombre único por superficie."""
    pkg_spec = importlib.util.spec_from_file_location(
        pkg_name, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg
    pkg_spec.loader.exec_module(pkg)
    for sub in ("enroll", "policy_contract", "enforcement", "config_enforcement"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{sub}", PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    pkg.enforcement.enroll = pkg.enroll
    pkg.config_enforcement.enforcement = pkg.enforcement
    return pkg


def _make_bundle(signing_key, *, allowed_connectors=None, messaging_permissions=None,
                 allowed_tools=None, policy_version=42):
    import jwt
    now = int(time.time())
    payload = {
        "iss": "https://trust-agent.control-plane/",
        "aud": DEVICE_ID,
        "iat": now, "exp": now + 3600, "jti": uuid.uuid4().hex,
        "tenant_id": "demo-miller-tenant",
        "user_id": "empleado@demo-miller.local",
        "device_id": DEVICE_ID,
        "policy_version": policy_version,
        "schema_version": 2,
        "allowed_tools": allowed_tools or [
            "web_search", "web_extract", "read_file", "write_file", "patch",
            "terminal", "process_manage", "execute_code", "governance_status",
            "connectors__gmail__send", "connectors__telegram__send_message",
        ],
        "allowed_connectors": allowed_connectors if allowed_connectors is not None else [],
        "approved_models": ["glm-5.2"],
        "budget": {
            "user": {"remaining_cents": 4200, "cutoff": False},
            "dept": {"remaining_cents": 150000, "cutoff": False},
            "org": {"remaining_cents": 9800000, "cutoff": False},
        },
        "offline_grace_minutes": 60,
        "unbind_mode": "readonly",
        "attestation_overdue_hours": 24,
        "approval_mode": "manual",
        "allowed_terminal_backends": ["docker"],
        "require_write_sandbox": True,
        "allowed_env_passthrough": [],
        "web_domain_policy": {"mode": "allowlist", "domains": ["example.org"]},
        "allow_private_network_access": False,
        "allowed_providers": ["litellm_gateway"],
        "allow_self_updates": False,
    }
    if messaging_permissions is not None:
        payload["messaging_permissions"] = messaging_permissions
    return jwt.encode(payload, signing_key, algorithm="RS256")


def _setup_surface(home: Path, signing_key, jws: str):
    """Prepara una superficie: enrola el dispositivo + cachea el bundle."""
    d = home / "trust-agent"
    d.mkdir(parents=True, exist_ok=True)
    (d / "device-token.json").write_text(json.dumps({
        "device_id": DEVICE_ID, "token": "devtok.test",
        "enrolled_at": int(time.time()),
    }))
    os.chmod(d / "device-token.json", 0o600)
    (d / "governance-bundle.jws").write_text(jws)


def main() -> int:
    import tempfile

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    print(f"\n{BOLD}=== evidence_cross_surface.py — GAP-42-A E2E ==={RESET}")
    print(f"REQ-PE-011/012, spec 042: bloquear Telegram debe morder en TODAS las superficies\n")

    # Generar clave de firma + pinear la pública
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pubkey_pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    # ------------------------------------------------------------------
    # Fase 1: admin aplica bloqueo (policy v42 → v43)
    # ------------------------------------------------------------------
    print(f"{BOLD}Fase 1: Admin aplica bloqueo{RESET}")
    print("  messaging_permissions = {'telegram': {'allowed': false}}")
    print("  allowed_connectors = []")
    print()

    # Bundle con bloqueo (policy v43)
    blocked_jws = _make_bundle(
        signing_key,
        allowed_connectors=[],
        messaging_permissions={"telegram": {"allowed": False}},
        policy_version=43,
    )

    # ------------------------------------------------------------------
    # Fase 2: dos superficies del mismo usuario hacen sync + turno
    # ------------------------------------------------------------------
    print(f"{BOLD}Fase 2: Dos superficies (desktop + CLI) sincronizan{RESET}")

    # Superficie 1: Desktop
    home1 = Path(tempfile.mkdtemp(prefix="surface-desktop-"))
    os.environ["HERMES_HOME"] = str(home1)
    os.environ["TRUST_AGENT_API"] = "http://127.0.0.1:1"  # puerto cerrado
    os.environ["TRUST_POLICY_PUBKEY"] = ""
    pubkey_path1 = home1 / "pubkey.pem"
    pubkey_path1.write_text(pubkey_pem)
    os.environ["TRUST_POLICY_PUBKEY"] = str(pubkey_path1)
    _setup_surface(home1, signing_key, blocked_jws)
    plugin_desktop = _load_plugin("tag_plugin_desktop")
    plugin_desktop.enforcement.STATE = plugin_desktop.enforcement.GovernanceState()
    plugin_desktop.config_enforcement._CONFIG_CACHE.update(mtime=-1.0, path="", data={})
    snap_desktop = plugin_desktop.enforcement.STATE.evaluate()
    print(f"  Desktop: mode={snap_desktop['mode']}, "
          f"policy_version={snap_desktop['payload'].get('policy_version')}")

    # Superficie 2: CLI (mismo usuario, mismo device_id — bundle por usuario)
    home2 = Path(tempfile.mkdtemp(prefix="surface-cli-"))
    os.environ["HERMES_HOME"] = str(home2)
    pubkey_path2 = home2 / "pubkey.pem"
    pubkey_path2.write_text(pubkey_pem)
    os.environ["TRUST_POLICY_PUBKEY"] = str(pubkey_path2)
    _setup_surface(home2, signing_key, blocked_jws)
    plugin_cli = _load_plugin("tag_plugin_cli")
    plugin_cli.enforcement.STATE = plugin_cli.enforcement.GovernanceState()
    plugin_cli.config_enforcement._CONFIG_CACHE.update(mtime=-1.0, path="", data={})
    snap_cli = plugin_cli.enforcement.STATE.evaluate()
    print(f"  CLI:     mode={snap_cli['mode']}, "
          f"policy_version={snap_cli['payload'].get('policy_version')}")
    print()

    # ------------------------------------------------------------------
    # Fase 3: verificación cross-surface
    # ------------------------------------------------------------------
    print(f"{BOLD}Fase 3: Verificación cross-surface{RESET}\n")

    # AC-011-1: messaging_permissions={"telegram": false} → no despacha por telegram
    print(f"{BOLD}AC-011-1: messaging_permissions bloquea Telegram{RESET}")
    for name, plugin, snap in [("Desktop", plugin_desktop, snap_desktop),
                                ("CLI", plugin_cli, snap_cli)]:
        result = plugin.config_enforcement.on_pre_tool_call(
            tool_name="connectors__telegram__send_message", args={})
        _check(
            f"{name}: connectors__telegram__send_message blocked by messaging_permissions",
            result is not None and result["action"] == "block"
            and BLOCK_PREFIX in result["message"]
            and "telegram" in result["message"],
            result["message"][:80] if result else "NOT BLOCKED",
        )

    # AC-011-2: allowed_connectors=[] → toda tool de conector vetada
    print(f"\n{BOLD}AC-011-2: allowed_connectors=[] vetas todo conector{RESET}")
    for name, plugin, snap in [("Desktop", plugin_desktop, snap_desktop),
                                ("CLI", plugin_cli, snap_cli)]:
        result = plugin.enforcement.STATE.on_pre_tool_call(
            tool_name="connectors__gmail__send", args={})
        _check(
            f"{name}: connectors__gmail__send blocked by allowed_connectors",
            result is not None and result["action"] == "block"
            and BLOCK_PREFIX in result["message"],
            result["message"][:80] if result else "NOT BLOCKED",
        )

    # AC-011-3: fail-closed — key ausente → default restrictivo
    print(f"\n{BOLD}AC-011-3: fail-closed (key ausente = restrictivo){RESET}")
    # Simular bundle sin allowed_connectors (schema 1 viejo)
    import jwt
    raw_payload = jwt.decode(blocked_jws, options={"verify_signature": False})
    raw_payload_no_connectors = dict(raw_payload)
    raw_payload_no_connectors.pop("allowed_connectors", None)
    raw_payload_no_connectors["jti"] = uuid.uuid4().hex  # nuevo jti
    no_connector_jws = jwt.encode(raw_payload_no_connectors, signing_key, algorithm="RS256")
    _setup_surface(home1, signing_key, no_connector_jws)
    plugin_desktop.enforcement.STATE = plugin_desktop.enforcement.GovernanceState()
    snap_fc = plugin_desktop.enforcement.STATE.evaluate()
    result_fc = plugin_desktop.enforcement.STATE.on_pre_tool_call(
        tool_name="connectors__gmail__send", args={})
    _check(
        "Fail-closed: allowed_connectors ausente → conector vetado",
        result_fc is not None and result_fc["action"] == "block",
        "deny-by-default funciona",
    )

    # policy_value raises on unknown key
    try:
        plugin_desktop.policy_contract.policy_value(raw_payload, "nonexistent_key")
        _check("Fail-closed: unknown key → KeyError", False, "no exception raised")
    except KeyError:
        _check("Fail-closed: unknown key → KeyError", True, "ignore-but-never-relax")

    # AC-012-1: misma policy_version en ambas superficies
    print(f"\n{BOLD}AC-012-1: Cross-surface consistency{RESET}")
    pv_desktop = snap_desktop["payload"].get("policy_version")
    pv_cli = snap_cli["payload"].get("policy_version")
    _check(
        "Misma policy_version en desktop y CLI",
        pv_desktop == pv_cli == 43,
        f"desktop={pv_desktop}, cli={pv_cli}",
    )

    # gateway.platforms.telegram.enabled=false (pin del managed scope)
    print(f"\n{BOLD}Pin de gateway.platforms (messaging_permissions){RESET}")
    for name, home, plugin in [("Desktop", home1, plugin_desktop),
                                ("CLI", home2, plugin_cli)]:
        # Reset HERMES_HOME para esta superficie + re-setup con el bundle bloqueado
        os.environ["HERMES_HOME"] = str(home)
        os.environ["TRUST_POLICY_PUBKEY"] = str(home / "pubkey.pem")
        _setup_surface(home, signing_key, blocked_jws)
        # Fresh state para evitar caché del fail-closed test
        plugin.enforcement.STATE = plugin.enforcement.GovernanceState()
        plugin.config_enforcement._CONFIG_CACHE.update(mtime=-1.0, path="", data={})
        snap = plugin.enforcement.STATE.evaluate()
        payload = snap.get("payload")
        if payload is None:
            _check(f"{name}: gateway.platforms.telegram.enabled=false", False,
                   f"mode={snap.get('mode')}, no payload")
            continue
        # Escribir config.yaml inicial
        import yaml
        (home / "config.yaml").write_text(
            yaml.safe_dump({"model": {"provider": "custom"}}, sort_keys=False))
        plugin.config_enforcement._CONFIG_CACHE.update(mtime=-1.0, path="", data={})
        plugin.config_enforcement.sync_managed_config(payload, force=True)
        config = yaml.safe_load((home / "config.yaml").read_text())
        platforms = config.get("gateway", {}).get("platforms", {})
        telegram_enabled = platforms.get("telegram", {}).get("enabled")
        _check(
            f"{name}: gateway.platforms.telegram.enabled=false",
            telegram_enabled is False,
            f"enabled={telegram_enabled}",
        )

    # ------------------------------------------------------------------
    # Resumen
    # ------------------------------------------------------------------
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    print(f"\n{'='*60}")
    if failed == 0:
        print(f"{GREEN}{BOLD}=== E2E CROSS-SURFACE: {passed}/{total} PASS, 0 FAIL ==={RESET}")
    else:
        print(f"{RED}{BOLD}=== E2E CROSS-SURFACE: {passed}/{total} PASS, {failed} FAIL ==={RESET}")
        for name, ok, detail in _results:
            if not ok:
                print(f"  {RED}FAIL{RESET}: {name} — {detail}")
    print()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

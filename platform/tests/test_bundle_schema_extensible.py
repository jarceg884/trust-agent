"""Spec 019 D-GOH-11 (t_150e8931) — schema versionado y extensible del bundle.

Criterios de aceptación (evidencia real, nada simulado):
  - AC-GOH-022: bundle con ``schema_version`` mayor al soportado y keys
    desconocidas → verifica, aplica las keys conocidas, ignora las
    desconocidas con warning ``unknown_schema_keys`` en plugin.log y JAMÁS
    relaja nada (una key desconocida nunca desbloquea una capacidad).
    Bundle viejo (sin ``schema_version``) → verifica en el plugin nuevo.
  - AC-GOH-023: key conocida ausente (bundle viejo) → default RESTRINGIDO
    del plugin (RESTRICTIVE_DEFAULTS), NO el default del catálogo;
    ``governance_status`` (tool interna) siempre permitida.
  - REQ-GOH-003 re-test: firma inválida → fail-closed (BundleRejected) en
    AMBOS verificadores del plugin (bundle_client + enforcement).
  - REQ-GOH-024 (plugin lado): un bundle REAL del control plane (catálogo
    completo aplanado) no contiene keys desconocidas para el plugin.

El paquete del plugin se carga con importlib (el directorio lleva guion —
igual que el loader de plugins de Hermes).
"""
from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import DEVICE_ALICE, POLICY_JWS_PUBLIC_PEM

PLATFORM = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM / "hermes" / "plugins" / "trust-agent-governance"

DEVICE_ID = "11111111-2222-3333-4444-555555555555"


# =============================================================================
# Control plane REAL sobre HTTP (uvicorn en hilo, puerto efímero) — local a
# este módulo (el de test_governance_bundle no es visible cross-module)
# =============================================================================
@pytest.fixture(scope="module")
def real_api(db, env):
    import uvicorn

    from app.main import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn (control plane real) no arrancó"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def pinned_pubkey_file(tmp_path_factory):
    """Clave pública pineada del par de firma de la sesión (conftest)."""
    path = tmp_path_factory.mktemp("pinned") / "trust_agent_policy_pubkey.pem"
    path.write_text(POLICY_JWS_PUBLIC_PEM, encoding="utf-8")
    return path


# =============================================================================
# Carga del paquete del plugin (patrón de test_hrb_enforcement/_governance_bundle)
# =============================================================================
@pytest.fixture(scope="module")
def tag():
    spec = importlib.util.spec_from_file_location(
        "tag_schema_ext_pkg", PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["tag_schema_ext_pkg"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _signer(signing_key, tmp_path):
    """PEM público pineado en un archivo (como la profile distribution)."""
    pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    path = tmp_path / "trust_agent_policy_pubkey.pem"
    path.write_text(pem, encoding="utf-8")
    return path


@pytest.fixture()
def plugin(tag, signing_key, tmp_path, monkeypatch):
    """Home aislado + clave pineada + módulo enforcement cargado."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TRUST_AGENT_API", "http://127.0.0.1:1")  # puerto cerrado
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(_signer(signing_key, tmp_path)))
    monkeypatch.delenv("TRUST_DEVICE_TOKEN", raising=False)
    enforcement = importlib.import_module("tag_schema_ext_pkg.enforcement")
    setattr(tag, "enforcement", enforcement)
    tag.state.reset_state()
    return tag


def _payload(**overrides) -> dict:
    """Payload §5.2 (schema 1: SIN schema_version — bundle viejo)."""
    now = int(time.time())
    base = {
        "iss": "https://trust-agent.control-plane/",
        "aud": DEVICE_ID,
        "iat": now,
        "exp": now + 60 * 60,
        "jti": uuid.uuid4().hex,
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "device_id": DEVICE_ID,
        "policy_version": 7,
        "allowed_tools": ["web_search", "governance_status"],
        "allowed_connectors": [],
        "approved_models": ["glm-5.2"],
        "budget": {
            "user": {"remaining_cents": 4200, "cutoff": False},
            "dept": {"remaining_cents": 150000, "cutoff": False},
            "org": {"remaining_cents": 9800000, "cutoff": False},
        },
        "offline_grace_minutes": 60,
        "unbind_mode": "readonly",
        "attestation_overdue_hours": 24,
    }
    base.update(overrides)
    return base


def _sign(signing_key, payload: dict) -> str:
    return jwt.encode(payload, signing_key, algorithm="RS256")


def _plugin_log(tmp_path) -> str:
    path = tmp_path / "trust-agent" / "plugin.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# =============================================================================
# AC-GOH-022 — keys desconocidas / schema mayor: ignore-but-never-relax
# =============================================================================
def test_ac_goh_022_unknown_keys_ignored_with_warning(plugin, signing_key, tmp_path):
    """Bundle del futuro: schema 99 + keys que este plugin no conoce (una de
    ellas con nombre tentador ``allow_everything``). Debe verificar, aplicar
    las keys conocidas, ignorar las desconocidas y dejar warning en el log."""
    tag = plugin
    payload = _payload(
        schema_version=99,
        allow_everything=True,          # desconocida — JAMÁS debe relajar
        teleport_enabled=True,          # desconocida
        allow_voice_mode=False,         # conocida — se aplica tal cual
        allowed_mcp_servers=["fs"],
    )
    verified = tag.bundle_client.verify_bundle(_sign(signing_key, payload), device_id=DEVICE_ID)
    # Las keys conocidas se aplican con el valor FIRMADO.
    assert verified["schema_version"] == 99
    assert tag.policy_contract.policy_value(verified, "allow_voice_mode") is False
    assert tag.policy_contract.policy_value(verified, "allowed_mcp_servers") == ["fs"]
    # Las desconocidas se reportan (no se leen, no se aplican).
    unknown = tag.policy_contract.unknown_policy_keys(verified)
    assert unknown == ["allow_everything", "teleport_enabled"]
    # Warning en el log local (REQ-GOH-022).
    log = _plugin_log(tmp_path)
    assert "unknown_schema_keys" in log
    assert "allow_everything" in log
    # Never-relax: una key desconocida jamás se consulta — KeyError explícito.
    with pytest.raises(KeyError):
        tag.policy_contract.policy_value(verified, "allow_everything")
    # La capability "todo permitido" que la key desconocida pretendía sigue
    # denegada: allowed_tools es el que rige y no cambió.
    assert verified["allowed_tools"] == ["web_search", "governance_status"]


def test_ac_goh_022_old_bundle_without_schema_version_verifies(plugin, signing_key):
    """Bundle schema 1 (viejo, sin schema_version ni keys nuevas) → verifica
    en el plugin nuevo (compatibilidad retro, REQ-GOH-022)."""
    tag = plugin
    assert "schema_version" not in _payload()  # el fixture es schema 1
    verified = tag.bundle_client.verify_bundle(
        _sign(signing_key, _payload()), device_id=DEVICE_ID
    )
    assert tag.policy_contract.schema_version_of(verified) == 1
    assert tag.policy_contract.unknown_policy_keys(verified) == []


def test_ac_goh_022_enforcement_verifier_tolerates_unknown_too(plugin, signing_key, tmp_path):
    """El segundo verificador (enforcement.py, defensa en profundidad)
    aplica el MISMO contrato: unknown keys no rompen la verificación."""
    tag = plugin
    payload = _payload(schema_version=3, future_key_x=True)
    verified = tag.enforcement.verify_bundle(
        _sign(signing_key, payload), device_id=DEVICE_ID
    )
    assert verified["schema_version"] == 3
    log = _plugin_log(tmp_path)
    assert "unknown_schema_keys" in log
    assert "future_key_x" in log


# =============================================================================
# AC-GOH-023 — deny-by-default por key ausente (default del PLUGIN, no del
# catálogo) + tools internas siempre permitidas
# =============================================================================
def test_ac_goh_023_absent_keys_get_plugin_restrictive_defaults(plugin, signing_key):
    """Bundle viejo sin las keys nuevas: cada key ausente toma el default
    RESTRINGIDO del plugin (REQ-GOH-023), no el default del catálogo."""
    tag = plugin
    old = _payload()  # schema 1: sin allow_voice_mode, allowed_mcp_servers...
    pc = tag.policy_contract
    # Booleanos de capacidad: ausente = prohibido.
    assert pc.capability_allowed(old, "allow_voice_mode") is False
    assert pc.capability_allowed(old, "allow_bot_mode") is False
    assert pc.capability_allowed(old, "allow_credential_vault") is False
    assert pc.capability_allowed(old, "allow_self_updates") is False
    # Listas allow: ausente = nada permitido.
    assert pc.policy_value(old, "allowed_mcp_servers") == []
    assert pc.policy_value(old, "allowed_plugins") == []
    # OJO: allowed_skills tiene default de catálogo ["*"] — el default del
    # PLUGIN para una key AUSENTE es [] (deny-by-default, no catálogo).
    assert pc.policy_value(old, "allowed_skills") == []
    # Requisitos: ausente = exigido.
    assert pc.policy_value(old, "require_write_sandbox") is True
    assert pc.policy_value(old, "require_checkpoints") is True
    # Enums/enteros: ausente = valor más restrictivo.
    assert pc.policy_value(old, "approval_mode") == "manual"
    assert pc.policy_value(old, "max_delegation_depth") == 0
    assert pc.policy_value(old, "session_retention_days") == 1
    assert pc.policy_value(old, "web_domain_policy") == {"mode": "allowlist", "domains": []}
    # Sin bundle (modo restringido): mismos defaults restrictivos.
    assert pc.capability_allowed(None, "allow_voice_mode") is False
    assert pc.policy_value(None, "allowed_mcp_servers") == []


def test_ac_goh_023_configured_key_unlocks_only_via_signed_bundle(plugin, signing_key):
    """La capability solo se desbloquea cuando la org la configura y el
    valor viaja FIRMADO en el bundle (no por default del catálogo)."""
    tag = plugin
    payload = _payload(schema_version=2, allow_voice_mode=True)
    verified = tag.bundle_client.verify_bundle(
        _sign(signing_key, payload), device_id=DEVICE_ID
    )
    assert tag.policy_contract.capability_allowed(verified, "allow_voice_mode") is True


def test_ac_goh_023_internal_tools_always_allowed(plugin):
    """Excepción documentada (REQ-GOH-005): las tools internas del plugin
    (governance_status) siempre permitidas — sin bundle incluido."""
    tag = plugin
    assert "governance_status" in tag.enforcement.INTERNAL_TOOLS
    assert "governance_status" in tag.state.PLUGIN_INTERNAL_TOOLS


# =============================================================================
# REQ-GOH-003 re-test — firma inválida → fail-closed (ambos verificadores)
# =============================================================================
def test_retest_goh_003_tampered_bundle_fail_closed(plugin, signing_key):
    tag = plugin
    jws = _sign(signing_key, _payload())
    head, _, tail = jws.split(".")
    forged = base64.urlsafe_b64encode(
        json.dumps(_payload(allow_voice_mode=True)).encode()
    ).decode().rstrip("=")
    with pytest.raises(tag.bundle_client.BundleRejected):
        tag.bundle_client.verify_bundle(f"{head}.{forged}.{tail}", device_id=DEVICE_ID)
    with pytest.raises(tag.enforcement.BundleRejected):
        tag.enforcement.verify_bundle(f"{head}.{forged}.{tail}", device_id=DEVICE_ID)


def test_retest_goh_003_valid_bundle_still_accepted(plugin, signing_key):
    tag = plugin
    verified = tag.bundle_client.verify_bundle(
        _sign(signing_key, _payload(schema_version=2)), device_id=DEVICE_ID
    )
    assert verified["policy_version"] == 7


# =============================================================================
# REQ-GOH-024 (lado plugin) — bundle REAL del control plane: catálogo
# completo aplanado, sin keys desconocidas para este plugin
# =============================================================================
def test_goh_024_real_bundle_full_catalog_no_unknown_keys(
    tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file, admin_conn
):
    """Sync REAL contra el control plane (uvicorn + DB efímera canónica):
    el bundle trae schema_version 2 y TODAS las keys del catálogo vigente —
    ninguna desconocida para el plugin (el catálogo de la DB de test es el
    criterio: lo que hay ahí es lo que el endpoint compila)."""
    home = tmp_path
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TRUST_AGENT_API", real_api)
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey_file))
    monkeypatch.setenv("TRUST_SYNC_INTERVAL", "300")
    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)

    from app.config import get_settings
    from app.devices.tokens import issue_device_token
    from conftest import TENANT_A, USER_ALICE

    token = issue_device_token(
        get_settings(), device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A
    )
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": DEVICE_ALICE, "token": token}), encoding="utf-8"
    )

    tag.state.reset_state()
    snapshot = tag.bundle_client.BundleSyncer().startup()
    assert snapshot["mode"] == "normal", snapshot
    bundle = snapshot["bundle"]
    pc = tag.policy_contract

    # Schema versionado presente (REQ-GOH-022/024).
    assert pc.schema_version_of(bundle) == 2
    # El catálogo REAL de la DB de test: todo lo que hay ahí viaja en el
    # bundle y es conocido por el plugin (allowed_connections es structural,
    # compilado aparte por spec 024).
    with admin_conn.cursor() as cur:
        cur.execute("SELECT key FROM catalog.policy_keys")
        catalog_keys = {r[0] for r in cur.fetchall()}
    assert catalog_keys, "catálogo de policy keys vacío — seed roto"
    for key in catalog_keys - {"allowed_connections"}:
        assert key in bundle, f"catalog key missing from bundle: {key}"
        assert key in pc.KNOWN_POLICY_KEYS, f"catalog key unknown to plugin: {key}"
    assert pc.unknown_policy_keys(bundle) == []
    # El valor firmado del catálogo se lee vía el accessor del contrato.
    assert pc.policy_value(bundle, "require_mfa") == bundle["require_mfa"]

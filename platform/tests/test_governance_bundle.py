"""Spec 019 — Incremento 1: governance bundle JWS + plugin v2 (t_f99e12ab).

Criterios de aceptación cubiertos (evidencia real, nada simulado):
  - AC-GOH-001: GET /api/me/governance-bundle → 200 con JWS que verifica;
    device revocado → 401 device_not_authorized; sin MFA → 403 mfa_required;
    camino plugin (Bearer = device token) → 200.
  - AC-GOH-002: el payload valida contra el contrato §5.2 (claims, tipos,
    exp = iat + offline_grace_minutes*60, presupuesto por nivel, enum).
  - AC-GOH-003: el verificador del plugin rechaza bundle manipulado, de otro
    device (aud), con exp ausente, HS256 y jti repetido; el válido se acepta.
  - AC-GOH-004: arranque con sync OK (control plane REAL sobre HTTP contra la
    DB efímera canónica) / caído con caché vigente (gracia offline) / sin
    caché → modo restringido.
  - AC-GOH-008: governance_status con bundle (campos reales) y sin bundle
    ("NO VALID governance bundle", sin cifras inventadas).
  - AC-GOH-016: cero imports/escrituras contra hermes_cli en el plugin.

El control plane se levanta REAL: la app FastAPI verdadera sobre HTTP
(uvicorn en hilo, puerto efímero) contra la PostgreSQL efímera creada por
la cadena canónica de Alembic (fixture ``db`` del conftest).
"""
from __future__ import annotations

import base64
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

from conftest import (
    DEVICE_ALICE,
    POLICY_JWS_ISSUER,
    POLICY_JWS_PUBLIC_PEM,
    TENANT_A,
    USER_ALICE,
    bearer_headers,
    mint,
)

PLATFORM_DIR = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM_DIR / "hermes" / "plugins" / "trust-agent-governance"

# Claims obligatorios §5.2 (espejo del contrato del endpoint y del verificador).
REQUIRED_CLAIMS = (
    "iss", "aud", "exp", "iat", "jti", "tenant_id", "user_id", "device_id",
    "policy_version", "allowed_tools", "allowed_connectors",
    "approved_models", "budget", "offline_grace_minutes", "unbind_mode",
    "attestation_overdue_hours",
)


# =============================================================================
# Carga del paquete del plugin (nombre sintético — el directorio lleva guiones)
# =============================================================================
@pytest.fixture(scope="session")
def tag():
    spec = importlib.util.spec_from_file_location(
        "trust_agent_governance_plugin",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["trust_agent_governance_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


# =============================================================================
# Control plane REAL sobre HTTP (uvicorn en hilo, puerto efímero)
# =============================================================================
@pytest.fixture(scope="session")
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


def _issue_device_token() -> str:
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    return issue_device_token(
        get_settings(), device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A
    )


def _get_bundle(client, idp, *, amr=("mfa",), bearer=None):
    """GET /api/me/governance-bundle con sesión MFA (o Bearer explícito)."""
    headers = {}
    if bearer is None:
        tokens = mint(client, idp, "alice", amr=amr)
        headers.update(bearer_headers(tokens))
    else:
        headers["Authorization"] = f"Bearer {bearer}"
    headers["X-Device-Token"] = _issue_device_token()
    return client.get("/api/me/governance-bundle", headers=headers)


# =============================================================================
# AC-GOH-001 — endpoint: 200 + firma verificable; 401 revocado; 403 sin MFA
# =============================================================================
def test_ac_goh_001_bundle_ok_and_signature_verifies(client, idp):
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    jws = body["bundle"]
    assert isinstance(jws, str) and jws.count(".") == 2
    # La firma verifica con la clave pública del par de firma (pineada).
    header = jwt.get_unverified_header(jws)
    assert header["alg"] == "RS256"  # HS256 prohibido (constitution IV)
    assert header.get("kid")
    payload = jwt.decode(
        jws, POLICY_JWS_PUBLIC_PEM, algorithms=["RS256"], audience=DEVICE_ALICE
    )
    assert payload["tenant_id"] == TENANT_A
    assert payload["user_id"] == USER_ALICE
    assert payload["device_id"] == DEVICE_ALICE
    # Metadatos de sync (§5.5)
    assert body["policy_version"] == payload["policy_version"]
    assert body["offline_grace_minutes"] == payload["offline_grace_minutes"]
    assert body["expires_at"]


def test_ac_goh_001_revoked_device_401(client, idp, admin_conn):
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE devices SET status = 'revoked' WHERE id = %s", (DEVICE_ALICE,))
    try:
        resp = _get_bundle(client, idp)
        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["reason"] == "device_not_authorized"
    finally:
        with admin_conn.cursor() as cur:
            cur.execute("UPDATE devices SET status = 'active' WHERE id = %s", (DEVICE_ALICE,))


def test_ac_goh_001_without_mfa_403(client, idp):
    resp = _get_bundle(client, idp, amr=None)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["reason"] == "mfa_required"


def test_ac_goh_001_plugin_path_bearer_device_token(client):
    """Camino plugin (spec 019 §5.5 + bundle.py): el Bearer ES el device
    token — MFA heredada del enrolamiento/canje (spec 019 §8)."""
    dt = _issue_device_token()
    resp = _get_bundle(client, idp=None, bearer=dt)
    assert resp.status_code == 200, resp.text
    payload = jwt.decode(
        resp.json()["bundle"], POLICY_JWS_PUBLIC_PEM,
        algorithms=["RS256"], audience=DEVICE_ALICE,
    )
    assert payload["device_id"] == DEVICE_ALICE


def test_policy_jwks_public_endpoint(client):
    """§5.5: JWKS público del firmante (rotación de claves)."""
    resp = client.get("/.well-known/policy-jwks.json")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA" and keys[0]["alg"] == "RS256"
    assert keys[0]["use"] == "sig" and keys[0]["kid"]


# =============================================================================
# AC-GOH-002 — el payload valida contra el contrato §5.2
# =============================================================================
def test_ac_goh_002_payload_matches_contract(client, idp):
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200
    payload = jwt.decode(
        resp.json()["bundle"], POLICY_JWS_PUBLIC_PEM,
        algorithms=["RS256"], audience=DEVICE_ALICE,
    )
    for claim in REQUIRED_CLAIMS:
        assert claim in payload, f"missing claim: {claim}"
    # exp = iat + offline_grace_minutes * 60 (§5.2: el exp ES la gracia)
    assert payload["exp"] == payload["iat"] + payload["offline_grace_minutes"] * 60
    # Piso del proveedor (D-GOH-08)
    assert payload["offline_grace_minutes"] >= 15
    assert payload["unbind_mode"] in ("readonly", "lock")
    assert payload["attestation_overdue_hours"] >= 1
    assert isinstance(payload["policy_version"], int)
    for list_claim in ("allowed_tools", "allowed_connectors", "approved_models"):
        assert isinstance(payload[list_claim], list)
    budget = payload["budget"]
    for level in ("user", "dept", "org"):
        assert isinstance(budget[level]["remaining_cents"], int)
        assert isinstance(budget[level]["cutoff"], bool)
    assert payload["iss"] == POLICY_JWS_ISSUER
    assert payload["jti"]


# =============================================================================
# AC-GOH-003 — verificador del plugin: manipulado / otro aud / jti repetido
# =============================================================================
class _Signer:
    """Firma bundles de prueba con un par de claves propio del test."""

    def __init__(self, tmp_path: Path):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self.public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.pubkey_file = tmp_path / "pinned-pub.pem"
        self.pubkey_file.write_text(self.public_pem.decode(), encoding="utf-8")

    def payload(self, **overrides) -> dict:
        now = int(time.time())
        base = {
            "iss": POLICY_JWS_ISSUER,
            "aud": DEVICE_ALICE,
            "iat": now,
            "exp": now + 60 * 60,
            "jti": uuid.uuid4().hex,
            "tenant_id": TENANT_A,
            "user_id": USER_ALICE,
            "device_id": DEVICE_ALICE,
            "policy_version": 7,
            "allowed_tools": ["web_search", "governance_status"],
            "allowed_connectors": ["vmware"],
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

    def sign(self, payload: dict, algorithm: str = "RS256") -> str:
        if algorithm == "HS256":
            return jwt.encode(payload, "symmetric-secret", algorithm="HS256")
        return jwt.encode(payload, self.private_pem, algorithm="RS256")


@pytest.fixture()
def verifier(tag, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    signer = _Signer(tmp_path)
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(signer.pubkey_file))
    tag.state.reset_state()
    return tag, signer


def test_ac_goh_003_valid_bundle_accepted(verifier):
    tag, signer = verifier
    jws = signer.sign(signer.payload())
    payload = tag.bundle_client.verify_bundle(jws, device_id=DEVICE_ALICE)
    assert payload["policy_version"] == 7
    # El jti queda registrado como current del store anti-replay.
    store = json.loads(
        (tag.bundle_client.trust_dir() / "bundle-jti.json").read_text(encoding="utf-8")
    )
    assert store["current"] == payload["jti"]


def test_ac_goh_003_tampered_bundle_rejected(verifier):
    tag, signer = verifier
    jws = signer.sign(signer.payload())
    # Alterar el payload tras la firma (segment 1) rompe la firma.
    head, _, tail = jws.split(".")
    tampered_payload = signer.payload(policy_version=999)
    forged_segment = base64.urlsafe_b64encode(
        json.dumps(tampered_payload).encode()
    ).decode().rstrip("=")
    with pytest.raises(tag.bundle_client.BundleRejected):
        tag.bundle_client.verify_bundle(
            f"{head}.{forged_segment}.{tail}", device_id=DEVICE_ALICE
        )


def test_ac_goh_003_wrong_audience_rejected(verifier):
    tag, signer = verifier
    jws = signer.sign(signer.payload(aud="99999999-9999-9999-9999-999999999999"))
    with pytest.raises(tag.bundle_client.BundleRejected) as excinfo:
        tag.bundle_client.verify_bundle(jws, device_id=DEVICE_ALICE)
    assert "audience" in excinfo.value.reason


def test_ac_goh_003_missing_exp_rejected(verifier):
    tag, signer = verifier
    payload = signer.payload()
    del payload["exp"]
    with pytest.raises(tag.bundle_client.BundleRejected) as excinfo:
        tag.bundle_client.verify_bundle(signer.sign(payload), device_id=DEVICE_ALICE)
    assert "exp" in excinfo.value.reason or "missing" in excinfo.value.reason


def test_ac_goh_003_hs256_rejected(verifier):
    tag, signer = verifier
    jws = signer.sign(signer.payload(), algorithm="HS256")
    with pytest.raises(tag.bundle_client.BundleRejected) as excinfo:
        tag.bundle_client.verify_bundle(jws, device_id=DEVICE_ALICE)
    assert "algorithm" in excinfo.value.reason


def test_ac_goh_003_replayed_jti_rejected(verifier):
    tag, signer = verifier
    jws = signer.sign(signer.payload())
    tag.bundle_client.verify_bundle(jws, device_id=DEVICE_ALICE)
    # Re-envío del MISMO bundle (mismo jti) → rechazado (anti-replay).
    with pytest.raises(tag.bundle_client.BundleRejected) as excinfo:
        tag.bundle_client.verify_bundle(jws, device_id=DEVICE_ALICE)
    assert "replayed" in excinfo.value.reason


def test_ac_goh_003_expired_bundle_rejected(verifier):
    tag, signer = verifier
    now = int(time.time())
    jws = signer.sign(signer.payload(iat=now - 7200, exp=now - 60))
    with pytest.raises(tag.bundle_client.BundleRejected) as excinfo:
        tag.bundle_client.verify_bundle(jws, device_id=DEVICE_ALICE)
    assert "expired" in excinfo.value.reason


# =============================================================================
# Entorno del plugin para los escenarios de arranque (AC-GOH-004/008)
# =============================================================================
@pytest.fixture(scope="session")
def pinned_pubkey_file(tmp_path_factory):
    """Clave pública pineada del par de firma de la sesión (conftest)."""
    path = tmp_path_factory.mktemp("pinned") / "trust_agent_policy_pubkey.pem"
    path.write_text(POLICY_JWS_PUBLIC_PEM, encoding="utf-8")
    return path


def _plugin_env(monkeypatch, home: Path, api: str, pinned_pubkey_file: Path):
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TRUST_AGENT_API", api)
    monkeypatch.setenv("TRUST_POLICY_PUBKEY", str(pinned_pubkey_file))
    monkeypatch.setenv("TRUST_SYNC_INTERVAL", "300")
    trust = home / "trust-agent"
    trust.mkdir(parents=True, exist_ok=True)
    return trust


def _write_device_token(trust: Path):
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": DEVICE_ALICE, "token": _issue_device_token()}),
        encoding="utf-8",
    )


DEAD_API = "http://127.0.0.1:1"  # puerto cerrado: control plane caído


# =============================================================================
# AC-GOH-004 — arranque: sync OK / caído con caché vigente / sin caché
# =============================================================================
def test_ac_goh_004_startup_sync_ok(tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file):
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    syncer = tag.bundle_client.BundleSyncer()
    snapshot = syncer.startup()
    assert snapshot["mode"] == "normal", snapshot
    assert snapshot["bundle"]["aud"] == DEVICE_ALICE
    assert snapshot["bundle"]["policy_version"] >= 0
    # Bundle fresco en caché con jti nuevo: un segundo sync trae otro jti.
    first_jti = snapshot["bundle"]["jti"]
    cache = trust / "governance-bundle.jws"
    assert cache.exists()
    snapshot2 = syncer.tick()
    assert snapshot2["mode"] == "normal"
    assert snapshot2["bundle"]["jti"] != first_jti
    # El log local registra el sync (evidencia de detección, ADR-018).
    log = (trust / "plugin.log").read_text(encoding="utf-8")
    assert "bundle_synced" in log


def test_ac_goh_004_plane_down_with_valid_cache(tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file):
    # 1) Sync OK para poblar la caché con un bundle vigente.
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    tag.bundle_client.BundleSyncer().startup()
    # 2) Control plane caído + arranque en frío: la caché vigente sostiene el
    #    modo normal dentro de la gracia offline (D-GOH-04).
    monkeypatch.setenv("TRUST_AGENT_API", DEAD_API)
    tag.state.reset_state()
    snapshot = tag.bundle_client.BundleSyncer().startup()
    assert snapshot["mode"] == "normal", snapshot
    assert snapshot["grace_remaining_seconds"] > 0
    assert snapshot["bundle"]["aud"] == DEVICE_ALICE


def test_ac_goh_004_no_cache_restricted_mode(tag, tmp_path, monkeypatch, pinned_pubkey_file):
    trust = _plugin_env(monkeypatch, tmp_path, DEAD_API, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    snapshot = tag.bundle_client.BundleSyncer().startup()
    assert snapshot["mode"] == "restricted"
    assert snapshot["reason"] == "no_bundle"
    assert snapshot["bundle"] is None


def test_ac_goh_004_no_device_token_restricted(tag, tmp_path, monkeypatch, pinned_pubkey_file):
    _plugin_env(monkeypatch, tmp_path, DEAD_API, pinned_pubkey_file)
    tag.state.reset_state()
    snapshot = tag.bundle_client.BundleSyncer().startup()
    assert snapshot["mode"] == "restricted"
    assert snapshot["bundle"] is None


def test_ac_goh_004_revocation_restricts_immediately(tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file, admin_conn):
    """REQ-GOH-010 (preparación del estado para el Incremento 2): un 401 del
    sync pasa el plugin a restringido al instante, sin consumir gracia."""
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    syncer = tag.bundle_client.BundleSyncer()
    assert syncer.startup()["mode"] == "normal"
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE devices SET status = 'revoked' WHERE id = %s", (DEVICE_ALICE,))
    try:
        snapshot = syncer.tick()
        assert snapshot["mode"] == "restricted"
        assert snapshot["reason"] == "revoked"
        # La caché deja de ser autoridad (invalidada).
        assert snapshot["bundle"] is None
        assert not (trust / "governance-bundle.jws").exists()
    finally:
        with admin_conn.cursor() as cur:
            cur.execute("UPDATE devices SET status = 'active' WHERE id = %s", (DEVICE_ALICE,))


# =============================================================================
# AC-GOH-008 — governance_status con y sin bundle
# =============================================================================
def test_ac_goh_008_status_with_bundle(tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file):
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    tag.bundle_client.BundleSyncer().startup()
    out = tag._handle_governance_status()
    assert "NORMAL" in out
    assert "Policy version" in out
    assert "$" in out  # presupuesto por nivel en USD
    assert "glm-5.2" in out  # modelo aprobado real del seed
    assert "governance_status" in out  # allowed_tools del default del proveedor
    assert "min" in out  # gracia restante


def test_ac_goh_008_status_without_bundle(tag, tmp_path, monkeypatch, pinned_pubkey_file):
    _plugin_env(monkeypatch, tmp_path, DEAD_API, pinned_pubkey_file)
    tag.state.reset_state()
    out = tag._handle_governance_status()
    assert "NO VALID governance bundle" in out
    assert "RESTRICTED" in out
    # Sin cifras inventadas: no hay presupuesto/modelos falsos en la salida.
    assert "$" not in out
    assert "glm" not in out


def test_pre_llm_call_context_carries_precedence_clause(tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file):
    """REQ-GOH-006: el contexto por turno porta presupuesto/modelos/tools y
    la cláusula de precedencia EXACTA."""
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    tag.bundle_client.BundleSyncer().startup()
    ctx = tag._on_pre_llm_call()
    assert ctx and "context" in ctx
    assert tag.PRECEDENCE_CLAUSE in ctx["context"]
    assert "Approved models" in ctx["context"]
    assert "Allowed tools" in ctx["context"]
    assert "Budget remaining" in ctx["context"]


def test_system_prompt_section_within_limit(tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file):
    """REQ-GOH-007: la sección declara identidad gobernada + precedencia y
    respeta el límite de 4000 chars del core."""
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    _write_device_token(trust)
    tag.state.reset_state()
    tag.bundle_client.BundleSyncer().startup()
    content = tag._system_prompt_section_content()
    assert len(content) <= 4000
    assert tag.PRECEDENCE_CLAUSE in content
    assert TENANT_A in content  # identidad del tenant (sin datos sensibles)


# =============================================================================
# AC-GOH-024 — schema versionado + catálogo completo aplanado (D-GOH-11,
# t_150e8931): el payload lleva schema_version y TODAS las keys del catálogo
# vigente, compiladas DINÁMICAMENTE (una key nueva en el catálogo viaja sin
# cambio de código del endpoint).
# =============================================================================
def test_ac_goh_024_schema_version_and_full_catalog(client, idp, admin_conn):
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text
    payload = jwt.decode(
        resp.json()["bundle"], POLICY_JWS_PUBLIC_PEM,
        algorithms=["RS256"], audience=DEVICE_ALICE,
    )
    # schema_version: entero, schema 2 (D-GOH-11).
    assert isinstance(payload["schema_version"], int)
    assert payload["schema_version"] >= 2
    # Toda key del catálogo vigente viaja aplanada en el payload, con su
    # valor efectivo combinado (allowed_connections es structural: se
    # compila aparte por spec 024 — NO es la policy key homónima).
    with admin_conn.cursor() as cur:
        cur.execute("SELECT key FROM catalog.policy_keys")
        catalog_keys = {r[0] for r in cur.fetchall()}
    assert catalog_keys, "catálogo de policy keys vacío — seed roto"
    for key in catalog_keys - {"allowed_connections"}:
        assert key in payload, f"catalog key missing from bundle: {key}"
    # El claim structural allowed_connections sigue siendo la compilación
    # del vault (spec 024: {"catalog": [...], "custom": [...]}), no la
    # policy key homónima (lista de tokens).
    assert isinstance(payload["allowed_connections"], dict)
    # El piso de la gracia se conserva pese a la compilación dinámica.
    assert payload["offline_grace_minutes"] >= 15


def test_ac_goh_024_new_catalog_key_travels_without_code_change(client, idp, admin_conn):
    """La compilación es DINÁMICA: una key NUEVA insertada en el catálogo (lo
    que hará la migración de t_fb62560f con las 29 keys) aparece en el
    siguiente bundle SIN tocar el endpoint (AC-GOH-024)."""
    new_key = "allow_voice_mode"  # una de las 29 de la matriz (P-21)
    with admin_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM catalog.policy_keys WHERE key = %s", (new_key,))
        already = cur.fetchone() is not None
        if not already:
            cur.execute(
                "INSERT INTO catalog.policy_keys (key, value_type, invariable,"
                " default_value, description) VALUES (%s, 'boolean', false,"
                " 'false', 'test: dynamic catalog key (AC-GOH-024)')",
                (new_key,),
            )
    try:
        resp = _get_bundle(client, idp)
        assert resp.status_code == 200, resp.text
        payload = jwt.decode(
            resp.json()["bundle"], POLICY_JWS_PUBLIC_PEM,
            algorithms=["RS256"], audience=DEVICE_ALICE,
        )
        # La key nueva viaja con el default del catálogo (nadie la configuró).
        assert payload[new_key] is False
    finally:
        if not already:
            with admin_conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM catalog.policy_keys WHERE key = %s", (new_key,)
                )


# =============================================================================
# AC-GOH-016 — cero parches al core de Hermes
# =============================================================================
def test_ac_goh_016_no_hermes_cli_patches():
    """El plugin no importa ni escribe hermes_cli/: solo la API pública
    ``ctx.register_*`` (grep de verificación automatizado)."""
    offenders = []
    for py in sorted(PLUGIN_DIR.rglob("*.py")):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("import hermes_cli") or stripped.startswith("from hermes_cli"):
                offenders.append(f"{py.name}:{lineno}")
    assert not offenders, f"imports de hermes_cli en el plugin: {offenders}"

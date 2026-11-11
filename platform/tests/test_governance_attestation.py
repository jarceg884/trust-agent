"""Spec 019 — Incremento 3: attestation del agente + job de overdue
(t_f9156ccd).

Criterios de aceptación cubiertos (evidencia real, nada simulado):
  - AC-GOH-012: POST /api/devices/{id}/attestation con bundle válido →
    fila ``device.attestation`` en audit_log del tenant con el hash correcto
    (verificable por DB); sesión en modo restringido → attestation con el
    modo reportado.
  - AC-GOH-013: dispositivo ``active`` sin attestation más allá del umbral →
    el job de detección genera la notificación al admin (inbox real) y la
    fila de audit; NO duplica dentro de la misma ventana; una attestation
    llegada resetea el reloj.
  - AC-GOH-017: 3+ intentos bloqueados de la misma tool → incluidos en la
    attestation siguiente (veto_counts) y visibles en audit
    (escalated_tools); el conteo se consume tras el envío exitoso.

El control plane se levanta REAL: la app FastAPI verdadera sobre HTTP
(uvicorn en hilo, puerto efímero) contra la PostgreSQL efímera creada por
la cadena canónica de Alembic (fixtures ``db``/``real_api`` del conftest,
mismo patrón que test_governance_bundle.py).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import jwt
import pytest

from conftest import (
    DEVICE_ALICE,
    DEVICE_CAROL,
    POLICY_JWS_PUBLIC_PEM,
    TENANT_A,
    TEST_DB,
    USER_ALICE,
    _admin,
)

from app.db import build_engine, tenant_tx
from app.devices import attestation as attestation_service
from app.devices import attestation_worker

PLATFORM_DIR = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PLATFORM_DIR / "hermes" / "plugins" / "trust-agent-governance"


# =============================================================================
# Carga del paquete del plugin (nombre sintético — el directorio lleva guiones)
# =============================================================================
@pytest.fixture(scope="session")
def tag():
    spec = importlib.util.spec_from_file_location(
        "trust_agent_governance_plugin_attestation",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["trust_agent_governance_plugin_attestation"] = mod
    spec.loader.exec_module(mod)
    # El submódulo attestation se cablea lazy en register(); para los tests
    # lo cargamos explícitamente bajo el mismo paquete sintético.
    mod.attestation = importlib.import_module(
        "trust_agent_governance_plugin_attestation.attestation"
    )
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


def _plugin_headers() -> dict[str, str]:
    """Auth del camino plugin (§5.5): Bearer = device token + X-Device-Token."""
    token = _issue_device_token()
    return {"Authorization": f"Bearer {token}", "X-Device-Token": token}


def _post_attestation(client, body: dict, *, device_id: str = DEVICE_ALICE,
                      headers: dict | None = None):
    return client.post(
        f"/api/devices/{device_id}/attestation",
        json=body,
        headers=headers if headers is not None else _plugin_headers(),
    )


def _fetch_audit(action: str, *, device_id: str = DEVICE_ALICE) -> list[dict]:
    """Lectura directa de audit_log del tenant (verificación por DB)."""
    with _admin(TEST_DB).cursor() as cur:
        cur.execute(
            "SELECT a.action, a.device_id, a.payload, a.tenant_id "
            "FROM audit_log a WHERE a.action = %s AND a.device_id = %s "
            "ORDER BY a.id DESC",
            (action, device_id),
        )
        rows = cur.fetchall()
    return [
        {"action": r[0], "device_id": str(r[1]), "payload": r[2],
         "tenant_id": str(r[3])}
        for r in rows
    ]


def _device_row() -> dict:
    with _admin(TEST_DB).cursor() as cur:
        cur.execute(
            "SELECT status, last_attestation_at, last_attestation_overdue_at "
            "FROM devices WHERE id = %s", (DEVICE_ALICE,)
        )
        status, last_att, last_overdue = cur.fetchone()
    return {
        "status": status,
        "last_attestation_at": last_att,
        "last_attestation_overdue_at": last_overdue,
    }


def _run_sweep() -> dict:
    """Un ciclo del job de overdue contra la DB real (camino del worker)."""
    from app.config import get_settings

    engine = build_engine(get_settings())
    with tenant_tx(engine, TENANT_A) as conn:
        return attestation_service.sweep_attestation_overdue(conn, tenant_id=TENANT_A)


# =============================================================================
# AC-GOH-012 — attestation con bundle válido → fila device.attestation
# =============================================================================
def test_ac_goh_012_attestation_with_valid_bundle(client):
    # Bundle real del dispositivo (camino plugin) → hash del artefacto firmado.
    resp = client.get("/api/me/governance-bundle", headers=_plugin_headers())
    assert resp.status_code == 200, resp.text
    jws = resp.json()["bundle"]
    payload = jwt.decode(
        jws, POLICY_JWS_PUBLIC_PEM, algorithms=["RS256"], audience=DEVICE_ALICE
    )
    bundle_hash = hashlib.sha256(jws.encode()).hexdigest()

    resp = _post_attestation(
        client,
        {
            "bundle_hash": bundle_hash,
            "policy_version": payload["policy_version"],
            "mode": "normal",
            "veto_counts": {},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recorded"] is True
    assert body["device_id"] == DEVICE_ALICE
    assert body["attestation_overdue_hours"] >= 1

    # Fila en audit_log del tenant con el hash correcto (verificable por DB).
    rows = _fetch_audit("device.attestation")
    assert rows, "no device.attestation row in audit_log"
    row = rows[0]
    assert row["tenant_id"] == TENANT_A
    assert row["device_id"] == DEVICE_ALICE
    assert row["payload"]["bundle_hash"] == bundle_hash
    assert row["payload"]["policy_version"] == payload["policy_version"]
    assert row["payload"]["mode"] == "normal"

    # La señal de "agente reportando" queda persistida en el dispositivo.
    assert _device_row()["last_attestation_at"] is not None


def test_ac_goh_012_restricted_mode_reported(client):
    """Sesión en modo restringido (sin bundle) → attestation con modo reportado."""
    resp = _post_attestation(
        client,
        {"bundle_hash": None, "policy_version": None,
         "mode": "restricted", "veto_counts": {}},
    )
    assert resp.status_code == 200, resp.text
    rows = _fetch_audit("device.attestation")
    assert rows[0]["payload"]["mode"] == "restricted"
    assert rows[0]["payload"]["bundle_hash"] is None


def test_ac_goh_012_auth_gates(client):
    """Auth del bundle (§5.5): sin X-Device-Token → 401; device del path que
    no es el del token → 401; dispositivo revocado → 401 inmediato."""
    # Sin X-Device-Token
    token = _issue_device_token()
    resp = client.post(
        f"/api/devices/{DEVICE_ALICE}/attestation",
        json={"mode": "normal"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "device_not_authorized"

    # Token válido de alice pero path de OTRO dispositivo: el token no
    # atestigua dispositivos ajenos.
    resp = _post_attestation(client, {"mode": "normal"}, device_id=DEVICE_CAROL)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "device_not_authorized"

    # Dispositivo revocado → 401 (revocación inmediata, REQ-GOH-010).
    with _admin(TEST_DB).cursor() as cur:
        cur.execute("UPDATE devices SET status = 'revoked' WHERE id = %s",
                    (DEVICE_ALICE,))
    try:
        resp = _post_attestation(client, {"mode": "normal"})
        assert resp.status_code == 401
        assert resp.json()["error"]["reason"] == "device_not_authorized"
    finally:
        with _admin(TEST_DB).cursor() as cur:
            cur.execute("UPDATE devices SET status = 'active' WHERE id = %s",
                        (DEVICE_ALICE,))


def test_attestation_rejects_negative_veto_counts(client):
    """Contrato tipado: conteos negativos → 422 (nada de vetos falsificados)."""
    resp = _post_attestation(
        client,
        {"mode": "normal", "veto_counts": {"terminal": -1}},
    )
    assert resp.status_code == 422


# =============================================================================
# AC-GOH-013 — job de overdue: notificación + audit, sin duplicar
# =============================================================================
@pytest.fixture()
def overdue_device():
    """Dispositivo active sin attestation, aprobado hace 48 h (umbral 24)."""
    with _admin(TEST_DB).cursor() as cur:
        cur.execute(
            "UPDATE devices SET last_attestation_at = NULL, "
            "last_attestation_overdue_at = NULL, "
            "approved_at = now() - interval '48 hours' "
            "WHERE id = %s", (DEVICE_ALICE,)
        )
    yield
    with _admin(TEST_DB).cursor() as cur:
        cur.execute(
            "UPDATE devices SET last_attestation_at = NULL, "
            "last_attestation_overdue_at = NULL, approved_at = NULL "
            "WHERE id = %s", (DEVICE_ALICE,)
        )


def _overdue_notification_count() -> int:
    with _admin(TEST_DB).cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM notifications WHERE type = %s "
            "AND payload->>'device_id' = %s",
            ("device_attestation_overdue", DEVICE_ALICE),
        )
        return cur.fetchone()[0]


def test_ac_goh_013_overdue_notifies_admin_and_audits(overdue_device):
    result = _run_sweep()
    assert result["overdue_hours"] >= 1
    assert result["notified"] == 1
    assert DEVICE_ALICE in result["device_ids"]

    # Notificación REAL al org_admin (inbox existente).
    with _admin(TEST_DB).cursor() as cur:
        cur.execute(
            "SELECT user_id, type, payload FROM notifications "
            "WHERE type = %s AND payload->>'device_id' = %s",
            ("device_attestation_overdue", DEVICE_ALICE),
        )
        rows = cur.fetchall()
    assert rows, "no device_attestation_overdue notification"
    assert str(rows[0][0]) == USER_ALICE  # org_admin del tenant A
    assert rows[0][2]["overdue_hours"] == result["overdue_hours"]

    # Fila de audit del tenant.
    audit_rows = _fetch_audit("device.attestation_overdue")
    assert audit_rows
    assert audit_rows[0]["tenant_id"] == TENANT_A
    assert audit_rows[0]["payload"]["device_id"] == DEVICE_ALICE

    # Marca de dedupe persistida (crash-safe: nada en memoria).
    assert _device_row()["last_attestation_overdue_at"] is not None


def test_ac_goh_013_no_duplicate_within_same_window(overdue_device):
    first = _run_sweep()
    assert first["notified"] == 1
    notifications_after_first = _overdue_notification_count()
    audit_after_first = len(_fetch_audit("device.attestation_overdue"))

    # Segunda pasada del job dentro de la MISMA ventana → sin duplicar.
    second = _run_sweep()
    assert second["notified"] == 0
    assert _overdue_notification_count() == notifications_after_first
    assert len(_fetch_audit("device.attestation_overdue")) == audit_after_first


def test_ac_goh_013_attestation_resets_the_clock(overdue_device, client):
    """La attestation llegada resetea el reloj: el dispositivo deja de estar
    overdue aunque la marca de la ventana anterior siga en la fila."""
    assert _run_sweep()["notified"] == 1

    resp = _post_attestation(
        client,
        {"bundle_hash": None, "policy_version": None,
         "mode": "normal", "veto_counts": {}},
    )
    assert resp.status_code == 200

    # Ya no overdue: la referencia vuelve a ser la attestation recién llegada.
    result = _run_sweep()
    assert result["notified"] == 0
    assert DEVICE_ALICE not in result["device_ids"]


def test_ac_goh_013_worker_cross_tenant_cycle(overdue_device, env):
    """El worker real (run_once) lista tenants cross-tenant (D-CP-10) y
    ejecuta el sweep dentro del scope de cada uno."""
    from app.config import get_settings
    from app.jobs.service import build_migrator_engine

    engine = build_engine(get_settings())
    migrator = build_migrator_engine(get_settings().database_migrator_url)
    summary = attestation_worker.run_once(engine, migrator)
    assert TENANT_A in summary["tenants"]
    assert summary["tenants"][TENANT_A]["notified"] == 1
    assert summary["notified"] == 1
    # Idempotente: un segundo ciclo no duplica.
    summary2 = attestation_worker.run_once(engine, migrator)
    assert summary2["notified"] == 0


# =============================================================================
# AC-GOH-017 — escalada de vetos en la attestation siguiente
# =============================================================================
@pytest.fixture(scope="session")
def pinned_pubkey_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("pinned-att") / "trust_agent_policy_pubkey.pem"
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


def test_ac_goh_017_veto_counts_in_next_attestation(
    tag, real_api, tmp_path, monkeypatch, pinned_pubkey_file
):
    """3+ intentos bloqueados de la misma tool → la attestation siguiente
    incluye el conteo y el audit lo muestra (escalated_tools)."""
    trust = _plugin_env(monkeypatch, tmp_path, real_api, pinned_pubkey_file)
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": DEVICE_ALICE, "token": _issue_device_token()}),
        encoding="utf-8",
    )
    tag.state.reset_state()
    # Sync real: bundle vigente en caché (modo normal).
    snapshot = tag.bundle_client.BundleSyncer().startup()
    assert snapshot["mode"] == "normal", snapshot

    # Vetos acumulados desde la última attestation: 3 de terminal (umbral de
    # escalación) + 1 de browser — como los escribe enforcement._count_veto.
    (trust / "veto-counts.json").write_text(
        json.dumps({"terminal": 3, "browser": 1}), encoding="utf-8"
    )
    tag.state.get_state().record_veto("browser")  # fuente del núcleo v2

    resp = tag.attestation.send_attestation(trigger="test")
    assert resp is not None, "attestation was not sent"
    assert resp["escalated_tools"] == ["terminal"]  # 3+ → escalada (audit)

    # Visible en audit: la fila device.attestation porta el conteo completo.
    rows = _fetch_audit("device.attestation")
    assert rows, "no device.attestation row"
    payload = rows[0]["payload"]
    assert payload["veto_counts"] == {"terminal": 3, "browser": 2}
    assert payload["escalated_tools"] == ["terminal"]
    # El hash reportado es el del bundle vigente en caché (AC-GOH-012).
    cached = (trust / "governance-bundle.jws").read_text(encoding="utf-8").strip()
    assert payload["bundle_hash"] == hashlib.sha256(cached.encode()).hexdigest()

    # El conteo se CONSUME tras el envío exitoso: la próxima attestation
    # parte de cero (REQ-GOH-012: "desde la última attestation").
    assert json.loads((trust / "veto-counts.json").read_text(encoding="utf-8")) == {}
    assert tag.state.get_state().snapshot()["veto_counts"] == {}

    # El log local registra el envío (evidencia de detección, ADR-018).
    log = (trust / "plugin.log").read_text(encoding="utf-8")
    assert "attestation_sent" in log


def test_attestation_skips_expired_device_token_not_revoked(
    tag, tmp_path, monkeypatch, pinned_pubkey_file
):
    """Spec 022 v1.2.0: un token EXPIRADO no se envía (el server diría 401 y
    eso se trataría como revocación: borraría el bundle firmado y marcaría
    revoked a un escritorio que solo estuvo cerrado más que el TTL)."""
    import time as _time

    import jwt as _jwt

    trust = _plugin_env(monkeypatch, tmp_path, "http://controlplane.invalid",
                        pinned_pubkey_file)
    now = int(_time.time())
    expired = _jwt.encode(
        {"device_id": DEVICE_ALICE, "user_id": "u", "tenant_id": "t",
         "jti": "x", "iat": now - 4500, "exp": now - 3600},
        "test-device-secret", algorithm="HS256",
    )
    (trust / "device-token.json").write_text(
        json.dumps({"device_id": DEVICE_ALICE, "token": expired}), encoding="utf-8"
    )
    (trust / "governance-bundle.jws").write_text("cached-bundle", encoding="utf-8")
    tag.state.reset_state()
    calls = []
    monkeypatch.setattr(tag.attestation, "post_attestation",
                        lambda *a, **k: calls.append(1) or {})

    assert tag.attestation.send_attestation(trigger="startup") is None
    assert calls == [], "no se envía con un token expirado"
    assert tag.state.get_state().snapshot().get("reason") != "revoked"
    assert (trust / "governance-bundle.jws").exists()
    log = (trust / "plugin.log").read_text(encoding="utf-8")
    assert "device token expired" in log
    assert "device_unauthorized" not in log


def test_attestation_registers_on_session_start_hook(tag):
    """register(ctx) cablea on_session_start (API pública) y el callback no
    bloquea (envío en hilo daemon — el hook es timeout-bounded en el core)."""
    registered: dict[str, list] = {}

    class _Ctx:
        def register_hook(self, name, cb):
            registered.setdefault(name, []).append(cb)

    tag.attestation.register(_Ctx())
    assert "on_session_start" in registered
    # El callback retorna rápido (dispara el hilo y sale).
    before = time.time()
    registered["on_session_start"][0](session_id="s-test", model="glm-5.2")
    assert time.time() - before < 1.0

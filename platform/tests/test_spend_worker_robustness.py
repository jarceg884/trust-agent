"""QA t_82b2ffaa — regresiones de robustez de la validación final.

Cubre tres hallazgos de docs/validacion-final.md §5 con evidencia real:
- H2 [MEDIUM]: un log del gateway que referencia un tenant inexistente
  (píldora envenenada) debe saltarse con rechazo durable auditado, no
  tumbar el ciclo completo.
- H3 [LOW]: el pool de la app corre con `lock_timeout`; un escritor de
  audit atascado sobre el advisory lock por tenant falla rápido
  (SQLSTATE 55P03) en lugar de bloquear el trail indefinidamente.
- H6 [LOW]: la suite pinea su env TRUST_* (no hereda el del shell).
"""
import os
import time
from datetime import datetime, timezone

import pytest

from tests.conftest import MIGRATOR_DB_URL, TENANT_A, TEST_DB, USER_CAROL, _admin

ORPHAN_TENANT = "99999999-9999-9999-9999-999999999999"
QWEN = "glm-5.2"

# Sandbox de módulo (t_e000bf40): test_orphan_tenant_log_rejected_not_poison
# ingesta via el worker una fila de HOY para Carol (log "healthy-qa-h2")
# que, sin limpieza, rompe los conteos EXACTOS de la ventana 'today' del
# tenant en test_usage_analytics. El fixture (conftest) borra al salir
# SOLO el delta de filas creadas aquí y restaura budget_counters.
pytestmark = pytest.mark.usefixtures("ledger_delta_sandbox")


@pytest.fixture()
def worker(litellm_fake):
    from app.db import get_engine
    from app.governance.spend_worker import run_once
    from app.jobs.service import build_migrator_engine
    from app.models.litellm import LiteLLMClient

    litellm = LiteLLMClient(litellm_fake.base_url, "sk-master-test")
    migrator = build_migrator_engine(MIGRATOR_DB_URL)

    def _run():
        return run_once(get_engine(), migrator, litellm)

    return _run


def _append_log(litellm_fake, *, request_id, tenant_id, user_id, spend=1.0):
    """Inject one spend log into the gateway double (metadata stamped)."""
    litellm_fake.logs.append({
        "request_id": request_id,
        "user_api_key_hash": "hash-" + request_id,
        "model": QWEN,
        "spend": spend,
        "tokens": 100,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "startTime": datetime.now(timezone.utc).isoformat(),
        "metadata": {"tenant_id": tenant_id, "user_id": user_id},
    })


def test_orphan_tenant_log_rejected_not_poison(litellm_fake, worker,
                                               admin_conn):
    """H2: un log huérfano (tenant borrado de la DB) se rechaza con
    evidencia durable y dedupeda en ops.spend_rejections; el ciclo
    COMPLETA y los demás tenants se ingesta con normalidad."""
    _append_log(litellm_fake, request_id="healthy-qa-h2",
                tenant_id=TENANT_A, user_id=USER_CAROL)
    _append_log(litellm_fake, request_id="orphan-qa-h2-1",
                tenant_id=ORPHAN_TENANT, user_id="ghost-user")

    summary = worker()

    # El ciclo completó: el tenant sano se ingesta y el cursor avanzó.
    assert "error" not in summary["tenants"][TENANT_A], summary
    assert summary["tenants"][TENANT_A]["ingest"]["ingested"] >= 1
    assert summary["cursor_frozen"] is False
    # El log huérfano fue rechazado (skip + registro), no crash.
    assert summary["rejected"] == [{
        "tenant_id": ORPHAN_TENANT, "logs": 1, "recorded": 1,
        "reason": "tenant_not_found",
    }]
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT request_id, reason FROM ops.spend_rejections "
            "WHERE tenant_id = %s", (ORPHAN_TENANT,),
        )
        rows = cur.fetchall()
    assert rows == [("orphan-qa-h2-1", "tenant_not_found")]

    # Segundo ciclo sobre la misma ventana (cursor de granularidad
    # diaria): evidencia dedupeda, sigue sin crash.
    summary2 = worker()
    assert summary2["rejected"][0]["recorded"] == 0
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM ops.spend_rejections "
            "WHERE tenant_id = %s", (ORPHAN_TENANT,),
        )
        assert cur.fetchone()[0] == 1


def test_audit_advisory_lock_times_out_instead_of_hanging(db):
    """H3: las conexiones del pool llevan lock_timeout; un INSERT de audit
    bloqueado por el advisory lock por tenant aborta con 55P03 rápido en
    lugar de colgar el trail del tenant indefinidamente."""
    from app.audit import service as audit
    from app.config import get_settings
    from app.db import build_engine, tenant_tx

    settings = get_settings().model_copy(
        update={"database_lock_timeout": "1s"}
    )
    engine = build_engine(settings)
    blocker = _admin(TEST_DB)  # superuser session holding the advisory lock
    lock_key = None
    try:
        with blocker.cursor() as cur:
            lock_key = cur.execute(
                "SELECT hashtextextended('audit:' || %s::text, 0)", (TENANT_A,)
            ).fetchone()[0]
            cur.execute("SELECT pg_advisory_lock(%s)", (lock_key,))

        started = time.monotonic()
        with pytest.raises(Exception) as excinfo:
            with tenant_tx(engine, TENANT_A) as conn:
                audit.emit(conn, tenant_id=TENANT_A, actor_role="system",
                           action="audit.verified",
                           payload={"source": "qa-h3"})
        elapsed = time.monotonic() - started
        assert elapsed < 30, "el wait del lock debe fallar rápido, no colgar"
        orig = getattr(excinfo.value, "orig", excinfo.value)
        assert getattr(orig, "sqlstate", None) == "55P03" \
            or "lock" in str(excinfo.value).lower(), str(excinfo.value)
    finally:
        if lock_key is not None:
            with blocker.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        blocker.close()
        engine.dispose()


def test_suite_env_is_pinned(env):
    """H6: el fixture de sesión limpia el TRUST_* heredado del shell y
    pinea valores determinísticos — TRUST_COOKIE_SECURE sigue en true
    aunque el shell lanzador exportara =false (reproducción de QA)."""
    assert os.environ["TRUST_COOKIE_SECURE"] == "true"
    assert os.environ["TRUST_MFA_MIN_ACR"] == "2"
    from app.config import get_settings

    settings = get_settings()
    assert settings.cookie_secure is True
    assert settings.mfa_min_acr == 2
    assert settings.database_lock_timeout == "10s"

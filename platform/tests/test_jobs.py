"""AC-CP-040/041/042 — jobs in PostgreSQL: idempotency, SKIP LOCKED, restart."""
import os
import subprocess
import threading
import uuid
from contextlib import contextmanager

from app.config import get_settings
from app.db import build_engine, reset_engine, tenant_tx
from app.jobs import service as jobs_service
from app.jobs.worker import run_once
from tests.conftest import PLATFORM_DIR, TENANT_A, TENANT_B


@contextmanager
def scoped(engine, tenant_id=TENANT_A):
    with tenant_tx(engine, tenant_id) as conn:
        yield conn


def test_enqueue_is_idempotent(db):
    """Same (tenant, type, idempotency_key) -> one job, existing returned.

    t_e000bf40: pide el fixture `db` — es el primer test del archivo y
    no pedía ningún fixture, así que al correr el archivo aislado la DB
    de sesión aún no existía (la crea lazy el primer test con `client`)
    y fallaba con OperationalError.
    """
    engine = build_engine(get_settings())
    first = jobs_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="echo",
        payload={"n": 1}, idempotency_key="idem-1",
    )
    second = jobs_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="echo",
        payload={"n": 2}, idempotency_key="idem-1",
    )
    assert first["id"] == second["id"]
    assert second["payload"] == {"n": 1}  # original payload, no overwrite


def test_worker_processes_echo_job_subprocess(client, idp):
    """`python -m app.jobs.worker --once` (real process) completes echo jobs."""
    engine = build_engine(get_settings())
    job = jobs_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="echo",
        payload={"hello": "world"}, idempotency_key=f"echo-{uuid.uuid4().hex}",
    )
    proc = subprocess.run(
        [os.path.join(PLATFORM_DIR, ".venv", "bin", "python"),
         "-m", "app.jobs.worker", "--once"],
        cwd=PLATFORM_DIR, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    with scoped(engine) as conn:
        row = jobs_service.get_job(conn, job["id"])
    assert row["status"] == "succeeded"
    assert row["result"] == {"echo": {"hello": "world"}, "job_type": "echo"}


def test_worker_marks_unknown_type_failed():
    engine = build_engine(get_settings())
    migrator = jobs_service.build_migrator_engine(
        get_settings().database_migrator_url
    )
    job = jobs_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="unknown_type_xyz",
        payload={}, idempotency_key=None,
    )
    # run_once claims globally: keep going until OUR job is no longer pending
    for _ in range(50):
        with scoped(engine) as conn:
            row = jobs_service.get_job(conn, job["id"])
        if row["status"] != "pending":
            break
        assert run_once(engine, migrator) == 1
    assert row["status"] == "failed"
    assert "unknown job type" in row["error"]


def test_retry_until_max_attempts(db):
    """Failing handler retries, then fails for good with error populated."""
    engine = build_engine(get_settings())
    migrator = jobs_service.build_migrator_engine(
        get_settings().database_migrator_url
    )

    def _boom(conn, job):
        raise RuntimeError("handler exploded")

    jobs_service.register_handler("fail_test", _boom)
    job = jobs_service.enqueue(
        engine, tenant_id=TENANT_A, job_type="fail_test",
        payload={}, max_attempts=2,
    )

    def _claim_ours():
        claimed = jobs_service.claim_next(migrator)
        guard = 0
        while claimed is not None and str(claimed["id"]) != str(job["id"]):
            jobs_service.complete(
                engine, tenant_id=str(claimed["tenant_id"]),
                job_id=str(claimed["id"]), result={},
            )
            claimed = jobs_service.claim_next(migrator)
            guard += 1
            assert guard < 100
        return claimed

    # attempt 1: fail -> requeued immediately (retry_delay 0)
    claimed = _claim_ours()
    assert claimed is not None and claimed["attempts"] == 1
    jobs_service.fail(engine, tenant_id=TENANT_A, job_id=str(job["id"]),
                      error="handler exploded", max_attempts=2,
                      retry_delay_seconds=0)

    # attempt 2: fail again -> failed for good
    claimed = _claim_ours()
    assert claimed is not None and claimed["attempts"] == 2
    jobs_service.fail(engine, tenant_id=TENANT_A, job_id=str(job["id"]),
                      error="handler exploded", max_attempts=2,
                      retry_delay_seconds=0)

    with scoped(engine) as conn:
        row = jobs_service.get_job(conn, job["id"])
    assert row["status"] == "failed"
    assert row["attempts"] == 2
    assert "handler exploded" in row["error"]


def test_concurrent_claim_no_double_claim():
    """Two claimers with SKIP LOCKED: every job claimed exactly once."""
    engine = build_engine(get_settings())
    ids = set()
    for i in range(6):
        job = jobs_service.enqueue(
            engine, tenant_id=TENANT_A, job_type="echo",
            payload={"i": i}, idempotency_key=f"conc-{i}",
        )
        ids.add(str(job["id"]))

    claimed = []
    lock = threading.Lock()

    def _claimer():
        migrator = jobs_service.build_migrator_engine(
            get_settings().database_migrator_url
        )
        while True:
            job = jobs_service.claim_next(migrator)
            if job is None:
                return
            with lock:
                claimed.append(str(job["id"]))

    threads = [threading.Thread(target=_claimer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == len(set(claimed))  # no double claims
    assert ids.issubset(set(claimed))  # every test job claimed exactly once


def test_jobs_survive_api_restart(client, idp):
    """AC-CP-042: enqueued job + active session survive a process restart."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.conftest import bearer_headers, mint

    tokens = mint(client, idp, "alice")
    engine = build_engine(get_settings())
    job = jobs_service.enqueue(
        engine, tenant_id=TENANT_B, job_type="echo",
        payload={"restart": True}, idempotency_key="restart-test",
    )
    reset_engine()
    with TestClient(create_app()) as fresh:
        resp = fresh.get(f"/api/jobs/{job['id']}",
                         headers=bearer_headers(tokens))
        # alice is tenant A: the tenant B job does not exist for her (404),
        # but the API restarted cleanly and her session still works.
        assert resp.status_code == 404
        assert fresh.get("/api/me", headers=bearer_headers(tokens)).status_code == 200
    # the job is still claimable after the restart
    migrator = jobs_service.build_migrator_engine(
        get_settings().database_migrator_url
    )
    claimed = jobs_service.claim_next(migrator)
    while claimed and str(claimed["id"]) != str(job["id"]):
        claimed = jobs_service.claim_next(migrator)
    assert claimed is not None and claimed["job_type"] == "echo"

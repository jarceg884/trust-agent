"""Job worker — `python -m app.jobs.worker` (arq. §3.8, REQ-SEC-009).

Own process, state in PostgreSQL: claims jobs one at a time with
FOR UPDATE SKIP LOCKED and executes them as trust_app inside the tenant
scope of each job. Restart-safe (C2): nothing lives in this process.
"""
import argparse
import signal
import sys
import time
from typing import Any

from ..config import get_settings
from ..db import build_engine, tenant_tx
from ..org.invite import register as register_invite_handler
from .service import (
    _HANDLERS,
    build_migrator_engine,
    claim_next,
    complete,
    enqueue,
    fail,
    register_handler,
)

# Spec 012: the worker knows how to run "user.invite" jobs (Keycloak Admin
# REST side lives in the handler, D-ORG-02).
register_invite_handler()


# Spec 032 (D-032-03): periodic catalog refresh job — re-syncs the model
# catalog of the tenant from its connected APIs (central gateway + every
# active llm connection). Real fetches, audited per refresh.
def _handle_catalog_refresh(conn, job) -> dict:
    from ..models import discovery

    return discovery.refresh_tenant(
        conn, str(job["tenant_id"]), actor_role="system"
    )


register_handler("model.catalog_refresh", _handle_catalog_refresh)


def enqueue_periodic_refresh(app_engine, migrator_engine, hours: float) -> int:
    """Idempotently enqueue one refresh job per window for EVERY active
    tenant (restart-safe: the idempotency key dedupes within the window;
    nothing critical lives in this process, C2).

    Spec 018 §5 (D-018-07): the refresh covers all active tenants — the
    central leg reconciles the catalog against the live upstream /models,
    and central drift affects every tenant, not only those with llm
    connections (spec 032 behavior).
    """
    window = int(time.time() // (max(hours, 0.1) * 3600))
    from ..models import discovery

    count = 0
    for tenant_id in discovery.active_tenants(migrator_engine):
        enqueue(
            app_engine,
            tenant_id=tenant_id,
            job_type="model.catalog_refresh",
            payload={"scope": "tenant", "window": window},
            idempotency_key=f"catalog-refresh-{window}",
        )
        count += 1
    return count


def run_once(app_engine, migrator_engine) -> int:
    """Claim and execute at most one job. Returns 1 if a job ran, 0 if idle."""
    job = claim_next(migrator_engine)
    if job is None:
        return 0
    tenant_id = str(job["tenant_id"])
    job_type = job["job_type"]
    handler = _HANDLERS.get(job_type)
    if handler is None:
        fail(app_engine, tenant_id=tenant_id, job_id=str(job["id"]),
             error=f"unknown job type: {job_type}", max_attempts=1)
        return 1
    try:
        with tenant_tx(app_engine, tenant_id) as conn:
            result = handler(conn, job)
    except Exception as exc:  # handlers isolate their own failures
        fail(app_engine, tenant_id=tenant_id, job_id=str(job["id"]),
             error=f"{type(exc).__name__}: {exc}",
             max_attempts=job["max_attempts"])
        return 1
    complete(app_engine, tenant_id=tenant_id, job_id=str(job["id"]),
             result=result)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.worker")
    parser.add_argument("--once", action="store_true",
                        help="process jobs until the queue is empty, then exit")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="polling seconds in continuous mode")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_migrator_url:
        print("TRUST_DATABASE_MIGRATOR_URL is required (job claim, D-CP-10).",
              file=sys.stderr)
        return 2
    app_engine = build_engine(settings)
    migrator_engine = build_migrator_engine(settings.database_migrator_url)

    stop = {"flag": False}

    def _stop(signum: int, frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Spec 032: periodic catalog-refresh enqueue (idempotent per window).
    next_refresh_enqueue = 0.0

    while not stop["flag"]:
        ran = run_once(app_engine, migrator_engine)
        if args.once and ran == 0:
            return 0
        if ran == 0:
            time.sleep(args.interval)
        if time.time() >= next_refresh_enqueue:
            try:
                enqueued = enqueue_periodic_refresh(
                    app_engine, migrator_engine,
                    settings.catalog_refresh_hours,
                )
                if enqueued:
                    print(
                        f"[jobs-worker] catalog refresh enqueued for "
                        f"{enqueued} tenant(s)",
                        flush=True,
                    )
            except Exception as exc:  # scheduling never kills the loop
                print(
                    f"[jobs-worker] catalog refresh enqueue failed: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            next_refresh_enqueue = time.time() + 900.0
    return 0


if __name__ == "__main__":
    sys.exit(main())

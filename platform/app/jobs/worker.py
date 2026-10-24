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
from .service import _HANDLERS, build_migrator_engine, claim_next, complete, fail

# Spec 012: the worker knows how to run "user.invite" jobs (Keycloak Admin
# REST side lives in the handler, D-ORG-02).
register_invite_handler()


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

    while not stop["flag"]:
        ran = run_once(app_engine, migrator_engine)
        if args.once and ran == 0:
            return 0
        if ran == 0:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())

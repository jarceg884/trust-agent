"""Audit chain verifier CLI (spec 008, REQ-AU-010 / arq. §5.6).

    python -m app.audit.cli verify <tenant_id>
    python -m app.audit.cli verify --all
    python -m app.audit.cli purge <tenant_id>     # retention purge job
    python -m app.audit.cli purge --all           # (REQ-AUD-006, spec 013)
    verify-audit-chain <tenant_id>     # console script (pyproject)

Verification ALWAYS runs as trust_app inside the tenant's scope
(set_config per transaction). --all lists tenants through the dedicated
trust_migrator connection (BYPASSRLS) used ONLY for that ops listing —
the same pattern as the job claim (D-CP-10).

Exit codes: 0 = all chains ok, 1 = at least one broken, 2 = usage/connection
error. Output: one JSON line per tenant plus a summary line.
"""
import argparse
import json
import sys

from sqlalchemy import create_engine, text

from ..config import get_settings
from ..db import build_engine, tenant_tx
from .service import emit_verification, verify_chain


def _list_tenant_ids(migrator_url: str) -> list[str]:
    """Ops-plane tenant listing only (BYPASSRLS role, D-CP-10 pattern)."""
    engine = create_engine(
        migrator_url,
        pool_pre_ping=True,
        connect_args={"application_name": "verify-audit-chain"},
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id FROM tenants ORDER BY id")).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        engine.dispose()


def verify_tenant(app_engine, tenant_id: str, *, emit_event: bool = True) -> dict:
    """Verify one tenant's chain inside its own scope; audit the run (D-AU-05)."""
    with tenant_tx(app_engine, tenant_id) as conn:
        result = verify_chain(conn, tenant_id)
        if emit_event:
            emit_verification(
                conn, tenant_id=tenant_id, result=result, source="cli"
            )
    return {"tenant_id": tenant_id, **result}


def purge_tenant(migrator_engine, tenant_id: str) -> dict:
    """Run the audited retention purge for one tenant (REQ-AUD-006).

    The purge MUST run as trust_migrator: the app role is INSERT-only on
    audit_log by design (REVOKE DELETE + immutability trigger). The SQL
    function audit_purge_expired deletes the expired events, writes the
    chain checkpoint and emits audit.purged with the count — all in one
    transaction under the chain's advisory lock.
    """
    with migrator_engine.begin() as conn:
        purged = conn.execute(
            text("SELECT audit_purge_expired(CAST(:t AS uuid)) AS purged"),
            {"t": str(tenant_id)},
        ).scalar_one()
    return {"tenant_id": tenant_id, "purged_rows": int(purged)}


def _run_purge(settings, tenant_ids: list[str]) -> int:
    from sqlalchemy import create_engine

    if not settings.database_migrator_url:
        print(
            "TRUST_DATABASE_MIGRATOR_URL is required for purge "
            "(the app role is INSERT-only on audit_log).",
            file=sys.stderr,
        )
        return 2
    migrator_engine = create_engine(
        settings.database_migrator_url,
        pool_pre_ping=True,
        connect_args={"application_name": "audit-purge-job"},
    )
    try:
        results = []
        for tenant_id in tenant_ids:
            try:
                results.append(purge_tenant(migrator_engine, tenant_id))
            except Exception as exc:
                results.append({
                    "tenant_id": tenant_id,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        for r in results:
            print(json.dumps(r))
        print(json.dumps({
            "total": len(results),
            "purged_rows": sum(
                r.get("purged_rows", 0) for r in results
            ),
            "errors": sum(1 for r in results if r.get("status") == "error"),
        }))
        return 1 if any(r.get("status") == "error" for r in results) else 0
    finally:
        migrator_engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-audit-chain",
        description="Verify the append-only audit hash chain per tenant.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="verify one tenant or all of them")
    group = verify.add_mutually_exclusive_group(required=True)
    group.add_argument("tenant_id", nargs="?", help="tenant UUID to verify")
    group.add_argument("--all", action="store_true",
                       help="verify every tenant (ops listing via migrator role)")
    purge = sub.add_parser(
        "purge",
        help="retention purge job (REQ-AUD-006): delete expired audit "
             "events, checkpoint the chain, emit audit.purged",
    )
    purge_group = purge.add_mutually_exclusive_group(required=True)
    purge_group.add_argument("tenant_id", nargs="?", help="tenant UUID to purge")
    purge_group.add_argument("--all", action="store_true",
                             help="purge every tenant (ops listing via migrator role)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        print("TRUST_DATABASE_URL is required.", file=sys.stderr)
        return 2

    if args.command == "purge":
        if not settings.database_migrator_url:
            print(
                "TRUST_DATABASE_MIGRATOR_URL is required for purge.",
                file=sys.stderr,
            )
            return 2
        if getattr(args, "all", False):
            tenant_ids = _list_tenant_ids(settings.database_migrator_url)
        else:
            tenant_ids = [args.tenant_id]
        return _run_purge(settings, tenant_ids)

    app_engine = build_engine(settings)
    try:
        if args.command == "verify":
            if getattr(args, "all", False):
                if not settings.database_migrator_url:
                    print(
                        "TRUST_DATABASE_MIGRATOR_URL is required for --all "
                        "(tenant listing, D-CP-10).",
                        file=sys.stderr,
                    )
                    return 2
                tenant_ids = _list_tenant_ids(settings.database_migrator_url)
            else:
                tenant_ids = [args.tenant_id]

            results = []
            for tenant_id in tenant_ids:
                try:
                    results.append(verify_tenant(app_engine, tenant_id))
                except Exception as exc:
                    results.append({
                        "tenant_id": tenant_id,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            for r in results:
                print(json.dumps(r))
            broken = [r for r in results if r.get("status") != "ok"]
            print(json.dumps({
                "total": len(results),
                "ok": len(results) - len(broken),
                "broken": len(broken),
            }))
            return 1 if broken else 0
    finally:
        app_engine.dispose()
    return 2  # pragma: no cover - unreachable


if __name__ == "__main__":
    sys.exit(main())

"""Spend aggregator + enforcement worker (ADR-014, REQ-BUD-005…010).

`python -m app.governance.spend_worker`

One cycle:
1. Read the incremental cursor from `ops.spend_cursor` (provider plane).
2. Pull `/spend/logs/v2` from the LiteLLM proxy since the cursor date.
3. Map each log to (tenant, user): metadata first (stamped at key
   issuance), fallback = read-only `trust_migrator` lookup of
   `api_keys.litellm_key_hash` (RLS hides api_keys cross-tenant from
   trust_app — same pattern as the job claim, D-CP-10).
4. Per tenant: ingest (ledger + counters, idempotent) then enforce
   (cutoffs / thresholds / key blocks / notifications).
5. Advance the cursor.

Crash-safe: the ledger is idempotent by request_id, so a crash between
ingest and cursor advance re-ingests without duplicating (D-GOV-04).

Poison-pill hardening (QA H2, t_82b2ffaa): a gateway log referencing a
tenant that no longer exists in the DB is REJECTED — skipped with a
durable, deduped record in `ops.spend_rejections` — instead of crashing
the whole cycle. A failure on one EXISTing tenant isolates to that
tenant and freezes the cursor so the next cycle retries everything
(idempotent ingest makes the retry free).
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from ..config import get_settings
from ..db import build_engine, plain_tx
from ..models.litellm import LiteLLMClient, SpendLog
from .spend import enforce_tenant, ingest_entries, RawSpend

# Milli-centavo (D-GOV-17, spec 005 / migración 0029): los costos del ledger
# llevan 3 decimales de centavo. ROUND_HALF_UP es conservador para el corte —
# jamás redondea POR DEBAJO del costo real.
_MILLI_CENT = Decimal("0.001")


def _usd_to_milli_cents(spend_usd: float | None) -> Decimal:
    """USD → centavos con precisión de milli-centavo (0.001¢).

    Gap B-2 (t_8bd1fee8): `round(usd * 100)` a entero truncaba a 0¢ las
    llamadas reales pequeñas de MaaS (ej. $0.000045 = 0.0045¢ → 0¢) y los
    presupuestos sub-contaban gasto real. La cadena Decimal evita además
    el error binario del float: str(4.5e-05) es exacto.
    """
    if spend_usd is None:
        spend_usd = 0.0
    return (Decimal(str(spend_usd)) * 100).quantize(
        _MILLI_CENT, rounding=ROUND_HALF_UP
    )


def _parse_time(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.now(timezone.utc)


def _resolve_by_hash(migrator_engine, key_hash: str) -> tuple[str, str] | None:
    """Read-only cross-tenant lookup of a key hash -> (tenant_id, user_id).

    Runs on the dedicated trust_migrator connection (BYPASSRLS) used ONLY
    for this resolution — never for business writes (D-CP-10 pattern).
    """
    if not key_hash:
        return None
    with migrator_engine.connect() as conn:
        row = conn.execute(
            text("SELECT tenant_id, user_id FROM api_keys "
                 "WHERE litellm_key_hash = :h LIMIT 1"),
            {"h": key_hash},
        ).mappings().first()
    if row is None:
        return None
    return str(row["tenant_id"]), str(row["user_id"])


def build_entries(
    logs: list[SpendLog], migrator_engine,
) -> dict[str, list[RawSpend]]:
    """Group spend logs by tenant, resolving tenant/user per log."""
    by_tenant: dict[str, list[RawSpend]] = {}
    for log in logs:
        tenant_id = log.tenant_id
        user_id = log.user_id
        if not tenant_id or not user_id:
            resolved = _resolve_by_hash(migrator_engine, log.key_hash or "")
            if resolved is None:
                continue
            tenant_id, user_id = resolved
        by_tenant.setdefault(tenant_id, []).append(
            RawSpend(
                request_id=log.request_id,
                tenant_id=tenant_id,
                user_id=user_id,
                model_name=log.model,
                cost_base_cents=_usd_to_milli_cents(log.spend_usd),
                tokens_in=log.tokens_in,
                tokens_out=log.tokens_out,
                occurred_at=_parse_time(log.occurred_at),
            )
        )
    return by_tenant


def _tenant_exists(migrator_engine, tenant_id: str) -> bool:
    """Read-only cross-tenant existence check (BYPASSRLS, D-CP-10 pattern).

    The app engine CANNOT see the tenants row of a deleted tenant (RLS
    hides it), so the check runs on the dedicated migrator connection —
    the same one used for key-hash resolution.
    """
    with migrator_engine.connect() as conn:
        return conn.execute(
            text("SELECT 1 FROM tenants WHERE id = CAST(:t AS uuid)"),
            {"t": tenant_id},
        ).scalar() is not None


def _record_rejections(app_engine, tenant_id: str, entries: list[RawSpend]) -> int:
    """Persist one append-only rejection row per orphan log (ops plane).

    `audit_log` cannot hold this evidence: its `tenant_id` is a FK to
    `tenants` and the tenant does NOT exist — same reasoning as
    D-DB-14/ops.login_throttle (no trustworthy tenant => provider-plane
    ops table is the correct venue). Deduped on (tenant_id, request_id,
    reason): re-fetching the same window (the cursor has date
    granularity) never duplicates evidence. Returns rows actually
    inserted (0 when the batch was already recorded).
    """
    recorded = 0
    with plain_tx(app_engine) as conn:
        for e in entries:
            # Logs without request_id get a synthetic dedupe key so they
            # don't all collapse into one row.
            request_id = e.request_id or f"no-request-id:{e.occurred_at.isoformat()}"
            result = conn.execute(
                text(
                    "INSERT INTO ops.spend_rejections "
                    "(tenant_id, request_id, reason, occurred_at) "
                    "VALUES (CAST(:t AS uuid), :rid, :reason, "
                    "CAST(:occ AS timestamptz)) "
                    "ON CONFLICT (tenant_id, request_id, reason) DO NOTHING"
                ),
                {
                    "t": tenant_id,
                    "rid": request_id,
                    "reason": "tenant_not_found",
                    "occ": e.occurred_at,
                },
            )
            recorded += result.rowcount
    return recorded


def run_once(app_engine, migrator_engine, litellm: LiteLLMClient) -> dict:
    """One aggregation cycle. Returns a summary (evidence for tests/logs).

    Poison-pill hardening (QA H2, t_82b2ffaa):
    - logs of a tenant that no longer exists are rejected (skipped +
      recorded in ops.spend_rejections) and do NOT crash the cycle;
    - an unexpected failure on one existing tenant isolates to that
      tenant (`summary["tenants"][t]["error"]`) and freezes the cursor
      (`summary["cursor_frozen"]`), so the next cycle retries — the
      ledger is idempotent by request_id, so nothing double-counts;
    - permanent rejections DO advance the cursor: orphan logs must not
      clog the pipeline of every other tenant.
    """
    with plain_tx(app_engine) as conn:
        cursor = conn.execute(
            text("SELECT last_request_id, last_occurred_at "
                 "FROM ops.spend_cursor WHERE id = 1")
        ).mappings().first()
    last_occurred = cursor["last_occurred_at"] if cursor else None
    today = date.today()
    start = (last_occurred.date() - timedelta(days=1)) if last_occurred \
        else today - timedelta(days=1)
    logs = litellm.spend_logs(
        start_date=start.isoformat(),
        end_date=(today + timedelta(days=1)).isoformat(),
    )
    by_tenant = build_entries(logs, migrator_engine)

    # Partition: orphan tenants (no row in tenants) are rejected for good.
    existing = {t for t in by_tenant if _tenant_exists(migrator_engine, t)}
    rejected = {t: e for t, e in by_tenant.items() if t not in existing}

    summary = {"logs": len(logs), "tenants": {}, "rejected": []}
    for tenant_id, entries in rejected.items():
        recorded = _record_rejections(app_engine, tenant_id, entries)
        summary["rejected"].append({
            "tenant_id": tenant_id,
            "logs": len(entries),
            "recorded": recorded,
            "reason": "tenant_not_found",
        })

    cycle_failed = False
    max_occurred = last_occurred
    for tenant_id in existing:
        entries = by_tenant[tenant_id]
        try:
            ingested = ingest_entries(app_engine, tenant_id, entries)
            enforced = enforce_tenant(app_engine, litellm, tenant_id)
        except Exception as exc:  # isolate: one tenant never poisons the rest
            cycle_failed = True
            summary["tenants"][tenant_id] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        summary["tenants"][tenant_id] = {"ingest": ingested, "enforce": enforced}
        for e in entries:
            if max_occurred is None or e.occurred_at > max_occurred:
                max_occurred = e.occurred_at

    if not cycle_failed:
        # Permanent rejections also advance the cursor (they are recorded
        # in ops.spend_rejections, so no evidence is lost).
        for entries in rejected.values():
            for e in entries:
                if max_occurred is None or e.occurred_at > max_occurred:
                    max_occurred = e.occurred_at
        with plain_tx(app_engine) as conn:
            conn.execute(
                text(
                    "UPDATE ops.spend_cursor SET last_occurred_at = :occ, "
                    "last_request_id = :rid, updated_at = now() WHERE id = 1"
                ),
                {
                    "occ": max_occurred,
                    "rid": logs[-1].request_id if logs else
                    (cursor["last_request_id"] if cursor else None),
                },
            )
    summary["cursor"] = max_occurred.isoformat() if max_occurred else None
    summary["cursor_frozen"] = cycle_failed
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.governance.spend_worker")
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="polling seconds in continuous mode (arq. §7)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_migrator_url:
        print("TRUST_DATABASE_MIGRATOR_URL is required (hash resolution, "
              "D-CP-10).", file=sys.stderr)
        return 2
    if not settings.litellm_base_url:
        print("TRUST_LITELLM_BASE_URL is required (fail-closed, D-GOV-15).",
              file=sys.stderr)
        return 2
    app_engine = build_engine(settings)
    from ..jobs.service import build_migrator_engine

    migrator_engine = build_migrator_engine(settings.database_migrator_url)
    litellm = LiteLLMClient(settings.litellm_base_url,
                            settings.litellm_master_key)

    while True:
        try:
            summary = run_once(app_engine, migrator_engine, litellm)
            if summary["rejected"]:
                # Operator-visible evidence (also durable in
                # ops.spend_rejections).
                print(f"[spend-worker] rejected orphan-tenant logs: "
                      f"{summary['rejected']}", file=sys.stderr, flush=True)
            if summary["logs"] or args.once:
                print(f"[spend-worker] {summary}", flush=True)
        except Exception as exc:  # keep the loop alive; next cycle retries
            print(f"[spend-worker] cycle failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())

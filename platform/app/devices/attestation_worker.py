"""Attestation overdue worker — `python -m app.devices.attestation_worker`.

Un ciclo (spec 019 Inc. 3, REQ-GOH-013):
1. Lista los tenants con dispositivos ``active`` (conexión de lectura
   trust_migrator BYPASSRLS — D-CP-10, mismo patrón que el claim de jobs
   y la resolución de key-hash del spend worker; NUNCA business writes).
2. Por tenant, dentro de su scope (tenant_tx, RLS): sweep de dispositivos
   ``active`` sin attestation más allá de ``attestation_overdue_hours`` →
   notificación ``device_attestation_overdue`` a los org_admin + audit
   ``device.attestation_overdue``, sin duplicar dentro de la misma ventana.

Crash-safe: nada vive en este proceso; el dedupe es una columna de la fila
(``devices.last_attestation_overdue_at``), así que un reinicio nunca
duplica notificaciones (C2).

Detección de agente desgobernado (ADR-018): un usuario del SO puede
deshabilitar el plugin localmente — entonces no hay attestation y este
worker es la señal que llega al admin para revocar el dispositivo (las
garantías duras — modelos/presupuesto/vault — son remotas).
"""
import argparse
import sys
import time

from sqlalchemy import text

from ..config import get_settings
from ..db import build_engine, tenant_tx
from .attestation import sweep_attestation_overdue


def _tenants_with_active_devices(migrator_engine) -> list[str]:
    """Read-only cross-tenant listing (BYPASSRLS, D-CP-10 pattern)."""
    with migrator_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT tenant_id FROM devices WHERE status = 'active'")
        ).mappings().all()
    return sorted(str(r["tenant_id"]) for r in rows)


def run_once(app_engine, migrator_engine) -> dict:
    """One detection cycle. Returns a summary (evidence for tests/logs)."""
    summary: dict = {"tenants": {}, "notified": 0}
    for tenant_id in _tenants_with_active_devices(migrator_engine):
        try:
            with tenant_tx(app_engine, tenant_id) as conn:
                result = sweep_attestation_overdue(conn, tenant_id=tenant_id)
        except Exception as exc:  # isolate: one tenant never poisons the rest
            summary["tenants"][tenant_id] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        summary["tenants"][tenant_id] = result
        summary["notified"] += result["notified"]
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.devices.attestation_worker")
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit")
    parser.add_argument("--interval", type=float, default=900.0,
                        help="polling seconds in continuous mode (default 900)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_migrator_url:
        print("TRUST_DATABASE_MIGRATOR_URL is required (tenant listing, D-CP-10).",
              file=sys.stderr)
        return 2
    app_engine = build_engine(settings)
    from ..jobs.service import build_migrator_engine

    migrator_engine = build_migrator_engine(settings.database_migrator_url)

    while True:
        try:
            summary = run_once(app_engine, migrator_engine)
            if summary["notified"] or args.once:
                print(f"[attestation-worker] {summary}", flush=True)
        except Exception as exc:  # keep the loop alive; next cycle retries
            print(f"[attestation-worker] cycle failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())

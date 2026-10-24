"""AC-013-006 (REQ-AUD-006, spec 013 D-013-04) — retención y purga de audit.

- PUT /api/admin/audit/retention: < 365 → 422 (piso invariable); cambio
  auditado como audit.retention_updated; solo org_admin.
- Job de purga (CLI/migrator): audit_purge_expired borra los eventos
  vencidos, deja checkpoint de cadena, registra audit.purged con conteo.
- La cadena sigue verificando ok tras purgar y tras eventos nuevos,
  incluida la purga TOTAL del tenant (el INSERT continúa desde el
  checkpoint).
- Fail-closed: DELETE sin el flag de autorización se rechaza incluso para
  trust_migrator; retención explícita < 365 en el job → error.
"""
import json

import pytest
from sqlalchemy import create_engine, text

from conftest import (
    ADMIN_PASSWORD, DB_HOST, DB_PORT, MIGRATOR_DB_URL, TENANT_A, USER_CAROL,
    _admin, bearer_headers, mint,
)

TEST_DB = "trust_agent_platform_test"

# Tenant dedicado del test (creado/eliminado aquí): su cadena empieza
# vacía, así que las filas con ts viejo son el PREFIJO de su cadena — el
# invariante de producción (orden ts ≈ orden id) que la purga exige.
PURGE_TENANT = "99999999-9999-9999-9999-999999999999"
FULL_PURGE_TENANT = "99999999-9999-9999-9999-99999999999a"


@pytest.fixture()
def migrator_engine():
    engine = create_engine(
        MIGRATOR_DB_URL,
        connect_args={"application_name": "audit-retention-test"},
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def purge_tenants(db, migrator_engine):
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO tenants (id, name, slug) VALUES "
            "(%s,'Purge Test','purge-test'), "
            "(%s,'Full Purge Test','full-purge-test')",
            (PURGE_TENANT, FULL_PURGE_TENANT),
        )
    yield PURGE_TENANT, FULL_PURGE_TENANT
    # Limpieza por la vía autorizada (migrator + flag de purga).
    with migrator_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        for t in (PURGE_TENANT, FULL_PURGE_TENANT):
            conn.execute(
                text("DELETE FROM audit_log WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": t},
            )
            conn.execute(
                text("DELETE FROM audit_chain_checkpoint "
                     "WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": t},
            )
            conn.execute(
                text("DELETE FROM tenants WHERE id = CAST(:t AS uuid)"),
                {"t": t},
            )


def _insert_event(conn, tenant_id, *, ts, action="auth.login", seq_note=""):
    """INSERT directo con ts explícito (el trigger asigna seq+hash)."""
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_role, action, "
            "target_type, target_id, payload, ts) "
            "VALUES (CAST(:t AS uuid), 'system', :action, 'tenant', "
            ":t, CAST(:p AS jsonb), CAST(:ts AS timestamptz))"
        ),
        {"t": tenant_id, "action": action,
         "p": json.dumps({"note": seq_note}), "ts": str(ts)},
    )


def _old_ts(days=400):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(days=days)


def _rows(migrator_engine, tenant_id, action=None):
    with migrator_engine.connect() as conn:
        sql = ("SELECT seq, action, payload FROM audit_log "
               "WHERE tenant_id = CAST(:t AS uuid)")
        params = {"t": tenant_id}
        if action:
            sql += " AND action = :action"
            params["action"] = action
        return conn.execute(text(sql + " ORDER BY id"), params).mappings().all()


def _verify(app_engine_scope, tenant_id):
    """Verificación como trust_app dentro del scope del tenant (RLS)."""
    from app.audit.service import verify_chain
    from app.db import tenant_tx

    with tenant_tx(app_engine_scope, tenant_id) as conn:
        return verify_chain(conn, tenant_id)


def test_retention_endpoint_validation_and_audit(client, idp, admin_conn):
    """AC: retención < 365 → 422; cambio válido auditado; no-admin → 403."""
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    resp = client.put("/api/admin/audit/retention",
                      json={"retention_days": 300},
                      headers=bearer_headers(alice))
    assert resp.status_code == 422, resp.text

    resp = client.put("/api/admin/audit/retention",
                      json={"retention_days": 300},
                      headers=bearer_headers(carol))
    assert resp.status_code == 403

    resp = client.put("/api/admin/audit/retention",
                      json={"retention_days": 400},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retention_days"] == 400
    assert body["minimum_days"] == 365
    assert body["previous_retention_days"] == 365

    resp = client.get("/api/admin/audit/retention",
                      headers=bearer_headers(alice))
    assert resp.json()["retention_days"] == 400

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' "
            "AND action = 'audit.retention_updated' "
            "AND (payload->>'retention_days')::int = 400"
        )
        assert cur.fetchone()[0] >= 1

    # Limpieza: volver al default 365.
    resp = client.put("/api/admin/audit/retention",
                      json={"retention_days": 365},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200


def test_purge_job_checkpoints_chain_and_audits(
    client, idp, migrator_engine, purge_tenants, admin_conn,
):
    from app.db import get_engine
    from app.audit.cli import purge_tenant as cli_purge

    tenant, full_tenant = purge_tenants

    # Prefijo viejo (3 filas, ts -400d) + 1 fila reciente.
    with migrator_engine.begin() as conn:
        for i in range(3):
            _insert_event(conn, tenant, ts=_old_ts(), seq_note=f"old-{i}")
        _insert_event(conn, tenant, ts=_old_ts(days=0), seq_note="recent")

    result = cli_purge(migrator_engine, tenant)
    assert result["purged_rows"] == 3, result

    remaining = _rows(migrator_engine, tenant)
    assert [r["seq"] for r in remaining if r["action"] != "audit.purged"] == [4]
    purged_events = [r for r in remaining if r["action"] == "audit.purged"]
    assert len(purged_events) == 1
    assert purged_events[0]["payload"]["purged_rows"] == 3
    assert purged_events[0]["payload"]["retention_days"] == 365

    # Checkpoint de cadena: último (seq, hash) purgado.
    with migrator_engine.connect() as conn:
        cp = conn.execute(
            text("SELECT last_seq, last_hash, purged_rows "
                 "FROM audit_chain_checkpoint WHERE tenant_id = CAST(:t AS uuid)"),
            {"t": tenant},
        ).mappings().first()
    assert cp["last_seq"] == 3
    assert cp["purged_rows"] == 3
    assert len(cp["last_hash"]) == 64

    # La cadena verifica ok desde el checkpoint (como trust_app, con RLS).
    assert _verify(get_engine(), tenant) == {"status": "ok"}

    # Un evento NUEVO continúa la cadena y sigue verificando.
    with migrator_engine.begin() as conn:
        _insert_event(conn, tenant, ts=_old_ts(days=0), seq_note="post-purge")
    seqs = [r["seq"] for r in _rows(migrator_engine, tenant)]
    assert seqs == [4, 5, 6]  # reciente, audit.purged, post-purge
    assert _verify(get_engine(), tenant) == {"status": "ok"}

    # Purga TOTAL: todas las filas viejas → checkpoint; el INSERT siguiente
    # continúa desde el checkpoint (genesis a través de la purga).
    with migrator_engine.begin() as conn:
        _insert_event(conn, full_tenant, ts=_old_ts(), seq_note="old-1")
        _insert_event(conn, full_tenant, ts=_old_ts(), seq_note="old-2")
    assert cli_purge(migrator_engine, full_tenant)["purged_rows"] == 2
    assert _rows(migrator_engine, full_tenant)[0]["action"] == "audit.purged"
    assert _verify(get_engine(), full_tenant) == {"status": "ok"}
    with migrator_engine.begin() as conn:
        _insert_event(conn, full_tenant, ts=_old_ts(days=0),
                      seq_note="post-full-purge")
    rows = _rows(migrator_engine, full_tenant)
    assert [r["seq"] for r in rows] == [3, 4]  # audit.purged, post-full-purge
    assert _verify(get_engine(), full_tenant) == {"status": "ok"}

    # Idempotencia: purgar sin vencidos → 0.
    assert cli_purge(migrator_engine, tenant)["purged_rows"] == 0


def test_purge_fail_closed(migrator_engine, purge_tenants):
    """DELETE sin el flag de autorización se rechaza incluso para el
    migrator; el job rechaza retención explícita < 365."""
    tenant, _ = purge_tenants
    with migrator_engine.begin() as conn:
        _insert_event(conn, tenant, ts=_old_ts(), seq_note="old")
    # Sin set_config('app.audit_purge'): el trigger rechaza el DELETE
    # (transacción propia para no arrastrar el rollback).
    with pytest.raises(Exception):
        with migrator_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM audit_log WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": tenant},
            )
    with pytest.raises(Exception):
        with migrator_engine.begin() as conn:
            conn.execute(
                text("SELECT audit_purge_expired(CAST(:t AS uuid), 300)"),
                {"t": tenant},
            )
    # La fila sigue intacta.
    assert len(_rows(migrator_engine, tenant)) == 1


def test_purge_cli_main(client, idp, migrator_engine, purge_tenants, capsys):
    """El job existe como CLI cron-able: python -m app.audit.cli purge."""
    from app.audit.cli import main

    tenant, _ = purge_tenants
    with migrator_engine.begin() as conn:
        _insert_event(conn, tenant, ts=_old_ts(), seq_note="old")
    assert main(["purge", tenant]) == 0
    out = capsys.readouterr().out
    assert '"purged_rows": 1' in out
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary == {"total": 1, "purged_rows": 1, "errors": 0}

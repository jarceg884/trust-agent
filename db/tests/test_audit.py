"""AC-DB-019/020/021 — Audit log: cadena de hash por tenant, INSERT-only,
detección de manipulación con verificador que señala la fila exacta."""
import uuid

import pytest
import psycopg.errors

from conftest import TENANT_A, TENANT_B, tenant_tx


def _insert_event(conn, tenant_id: str, action: str = "job.created", payload: str = "{}"):
    return conn.execute(
        "INSERT INTO audit_log (tenant_id, actor_role, action, payload) "
        "VALUES (%s, 'system', %s, %s) RETURNING id, seq, hash_prev, hash",
        (tenant_id, action, payload),
    ).fetchone()


def test_chain_links_seq_and_hashes(app_conn):
    """seq consecutivos por tenant + hash_prev enlazado al hash anterior."""
    with tenant_tx(app_conn, TENANT_A) as conn:
        first = conn.execute(
            "SELECT seq, hash_prev, hash FROM audit_log WHERE tenant_id = %s ORDER BY id LIMIT 1",
            (TENANT_A,),
        ).fetchone()
        assert first[0] == 1
        assert first[1] == "0" * 64, "la génesis debe ser 64 ceros"
        rows = conn.execute(
            "SELECT seq, hash_prev, hash FROM audit_log WHERE tenant_id = %s ORDER BY id",
            (TENANT_A,),
        ).fetchall()
    for prev, cur in zip(rows, rows[1:]):
        assert cur[0] == prev[0] + 1
        assert cur[1] == prev[2], "hash_prev debe encadenar con el hash de la fila anterior"


def test_verify_chain_ok_and_continues_after_app_inserts(app_conn):
    with tenant_tx(app_conn, TENANT_A) as conn:
        before = conn.execute(
            "SELECT max(seq) FROM audit_log WHERE tenant_id = %s", (TENANT_A,)
        ).fetchone()[0]
        _insert_event(conn, TENANT_A, "policy.updated", '{"k": "allowed_connectors"}')
        _insert_event(conn, TENANT_A, "model.approved", '{"m": "deepseek-v3"}')
        after = conn.execute(
            "SELECT max(seq) FROM audit_log WHERE tenant_id = %s", (TENANT_A,)
        ).fetchone()[0]
        assert after == before + 2
        status, seq = conn.execute(
            "SELECT * FROM audit_verify_chain(%s)", (TENANT_A,)
        ).fetchone()
    assert status == "ok" and seq is None


def test_seq_is_per_tenant(app_conn):
    """La cadena de B es independiente de la de A (seq propios)."""
    with tenant_tx(app_conn, TENANT_B) as conn:
        _insert_event(conn, TENANT_B, "job.completed", '{"ok": true}')
        seqs = [
            r[0]
            for r in conn.execute(
                "SELECT seq FROM audit_log WHERE tenant_id = %s ORDER BY id", (TENANT_B,)
            ).fetchall()
        ]
    assert seqs == list(range(1, len(seqs) + 1)), "seq de B debe empezar en 1 y ser consecutivo"


def test_append_only_for_app_role(app_conn):
    """Sin grant UPDATE/DELETE: la app no puede mutar el audit log."""
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE audit_log SET payload = '{}' WHERE tenant_id = %s", (TENANT_A,))
    with tenant_tx(app_conn, TENANT_A) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_log WHERE tenant_id = %s", (TENANT_A,))


def test_append_only_even_for_superuser(admin_conn):
    """El trigger de rechazo aplica a cualquier rol, incluido el superusuario."""
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        admin_conn.execute("UPDATE audit_log SET payload = '{}' WHERE id = 1")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        admin_conn.execute("DELETE FROM audit_log WHERE id = 1")


def test_tamper_detection_reports_exact_seq(admin_conn):
    """AC-DB-021: manipulación real (trigger deshabilitado por un superusuario
    de test) → el verificador señala la fila exacta; otros tenants quedan 'ok'."""
    tenant_c = uuid.uuid4()
    admin_conn.execute(
        "INSERT INTO tenants (id, name, slug) VALUES (%s, 'Tamper T', %s)",
        (tenant_c, f"tamper-{tenant_c.hex[:8]}"),
    )
    for _ in range(3):
        admin_conn.execute(
            "INSERT INTO audit_log (tenant_id, actor_role, action, payload) "
            "VALUES (%s, 'system', 'job.created', '{}')",
            (str(tenant_c),),
        )
    status, _ = admin_conn.execute(
        "SELECT * FROM audit_verify_chain(%s)", (str(tenant_c),)
    ).fetchone()
    assert status == "ok"

    # Manipulación como superusuario: deshabilitar el trigger, alterar, rehabilitar.
    admin_conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable_trigger")
    admin_conn.execute(
        "UPDATE audit_log SET payload = '{\"tampered\": true}' "
        "WHERE tenant_id = %s AND seq = 2",
        (str(tenant_c),),
    )
    admin_conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable_trigger")

    status, seq = admin_conn.execute(
        "SELECT * FROM audit_verify_chain(%s)", (str(tenant_c),)
    ).fetchone()
    assert status == "broken" and seq == 2, "el verificador debe señalar la fila manipulada (seq 2)"

    # La cadena de A sigue íntegra (aislamiento por tenant de la cadena).
    status_a, _ = admin_conn.execute(
        "SELECT * FROM audit_verify_chain(%s)", (TENANT_A,)
    ).fetchone()
    assert status_a == "ok"

    # Limpieza del tenant de test (con trigger deshabilitado, como superusuario).
    admin_conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable_trigger")
    admin_conn.execute("DELETE FROM audit_log WHERE tenant_id = %s", (str(tenant_c),))
    admin_conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable_trigger")
    admin_conn.execute("DELETE FROM tenants WHERE id = %s", (str(tenant_c),))


def test_audit_action_is_versioned_data(admin_conn):
    """action es FK al enum versionado: una acción fuera de catálogo no persiste."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_conn.execute(
            "INSERT INTO audit_log (tenant_id, actor_role, action) "
            "VALUES (%s, 'system', 'accion.inventada')",
            (TENANT_A,),
        )

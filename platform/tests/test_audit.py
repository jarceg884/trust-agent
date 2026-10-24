"""AC-CP-050 — audit emission + chain verification endpoint."""
from app.audit import service as audit_service
from app.db import build_engine, tenant_tx
from app.config import get_settings
from tests.conftest import TENANT_A, USER_ALICE, bearer_headers, mint


def test_emit_and_verify_chain(client, idp, admin_conn):
    """3 events -> consecutive seq, chain verifies OK via the org_admin API."""
    engine = build_engine(get_settings())
    with tenant_tx(engine, TENANT_A) as conn:
        for i in range(3):
            audit_service.emit(
                conn,
                tenant_id=TENANT_A,
                actor_role="org_admin",
                actor_id=USER_ALICE,
                action="policy.updated",
                target_type="policy",
                target_id=f"key-{i}",
                payload={"index": i},
            )

    seqs = [r[0] for r in admin_conn.execute(
        "SELECT seq FROM audit_log WHERE tenant_id = %s ORDER BY id DESC LIMIT 3",
        (TENANT_A,),
    ).fetchall()]
    assert seqs == sorted(seqs, reverse=True)
    assert len(set(seqs)) == 3

    tokens = mint(client, idp, "alice")
    resp = client.post("/api/admin/audit/verify", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_emit_requires_known_action(db):
    """action is FK to catalog.audit_actions: unknown actions are rejected."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.db import build_engine, tenant_tx
    from app.config import get_settings
    from sqlalchemy import text

    engine = build_engine(get_settings())
    with pytest.raises(IntegrityError):
        with tenant_tx(engine, TENANT_A) as conn:
            conn.execute(text(
                "INSERT INTO audit_log (tenant_id, actor_role, action) "
                "VALUES (CAST(:t AS uuid), 'system', 'action.invented')"
            ), {"t": TENANT_A})

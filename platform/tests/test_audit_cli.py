"""AC-AU-010/011/012 — chain verifier CLI + tamper detection (spec 008).

The CLI runs as a REAL subprocess (exit codes verified by the OS, not by
importing main()). Tampering uses the test superuser with the immutability
trigger disabled — the only possible path (D-AU-01); the application path
(trust_app) is closed by grants + trigger and is asserted to fail.
"""
import json
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text

from app.audit import service as audit_service
from app.config import get_settings
from app.db import build_engine, tenant_tx
from tests.conftest import (
    PLATFORM_DIR,
    TENANT_A,
    _admin,
    bearer_headers,
    mint,
)
from tests.conftest import TEST_DB
TAMPER_UPDATE_TENANT = "99999999-0000-0000-0000-000000000001"
TAMPER_DELETE_TENANT = "99999999-0000-0000-0000-000000000002"
TAMPER_ADMIN_UPDATE = "99999999-0000-0000-0000-0000000000a1"
TAMPER_ADMIN_DELETE = "99999999-0000-0000-0000-0000000000a2"


def run_cli(*args):
    """Real subprocess: python -m app.audit.cli <args> (env inherited)."""
    return subprocess.run(
        [sys.executable, "-m", "app.audit.cli", *args],
        cwd=PLATFORM_DIR, capture_output=True, text=True, timeout=120,
    )


def _last_audit_verified(admin_conn, tenant_id):
    return admin_conn.execute(
        "SELECT payload FROM audit_log WHERE tenant_id = %s "
        "AND action = 'audit.verified' ORDER BY id DESC LIMIT 1",
        (tenant_id,),
    ).fetchone()[0]


def test_cli_verify_ok_and_audited(client, idp, admin_conn):
    """AC-AU-010/011: CLI on an intact chain -> exit 0, one JSON line per
    tenant + summary; the run itself is audited as audit.verified source=cli."""
    proc = run_cli("verify", TENANT_A)
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
    assert lines[0] == {"tenant_id": TENANT_A, "status": "ok"}
    assert lines[-1] == {"total": 1, "ok": 1, "broken": 0}

    payload = _last_audit_verified(admin_conn, TENANT_A)
    assert payload["source"] == "cli"
    assert payload["result"] == "ok"


def test_cli_verify_all(admin_conn):
    """AC-AU-010: --all verifies every seeded tenant (ops listing via the
    migrator role) and exits 0 while all chains are intact."""
    proc = run_cli("verify", "--all")
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
    summary = lines[-1]
    assert summary["broken"] == 0
    assert summary["ok"] == summary["total"] >= 2
    assert TENANT_A in {line["tenant_id"] for line in lines[:-1]}


def test_app_role_cannot_mutate():
    """AC-AU-012 (app path closed): UPDATE/DELETE as trust_app inside the
    tenant scope -> PostgreSQL permission denied (grants, REQ-AUD-001)."""
    engine = build_engine(get_settings())
    with tenant_tx(engine, TENANT_A) as conn:
        with pytest.raises(Exception, match="(?i)permission denied"):
            conn.execute(
                text("UPDATE audit_log SET payload = '{}' "
                     "WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": TENANT_A},
            )
    with tenant_tx(engine, TENANT_A) as conn:
        with pytest.raises(Exception, match="(?i)permission denied"):
            conn.execute(
                text("DELETE FROM audit_log WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": TENANT_A},
            )


# ---------------------------------------------------------------------------
# Tamper scenarios (dedicated tenants; superuser + trigger disabled, the only
# possible manipulation path — D-AU-01)
# ---------------------------------------------------------------------------

def _seed_tamper_tenant(idp, tenant_id, admin_user_id, admin_sub):
    """Dedicated tenant with an org_admin user and exactly 3 audit events."""
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_realm, oidc_issuer_url) "
            "VALUES (%s, %s, %s, 'active', 'tamper', %s)",
            (tenant_id, f"Tamper {tenant_id[-4:]}", f"tamper-{tenant_id[-6:]}",
             idp.issuer("tenant-a")),
        )
        # Migration 0044: every user belongs to a department. Seeded by SQL:
        # no audit event, so the tenant keeps EXACTLY the 3 events below.
        dept_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO departments (id, tenant_id, name) VALUES (%s, %s, 'General')",
            (dept_id, tenant_id),
        )
        cur.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, display_name, "
            "status, mfa_enrolled, oidc_subject) "
            "VALUES (%s, %s, %s, %s, %s, 'active', true, %s)",
            (admin_user_id, tenant_id, dept_id, f"admin@{tenant_id[:8]}.test",
             "Tamper Admin", admin_sub),
        )
        cur.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key, department_id) "
            "VALUES (gen_random_uuid(), %s, %s, 'org_admin', NULL)",
            (tenant_id, admin_user_id),
        )
    engine = build_engine(get_settings())
    with tenant_tx(engine, tenant_id) as conn:
        for i in range(3):
            audit_service.emit(
                conn, tenant_id=tenant_id, actor_role="org_admin",
                actor_id=admin_user_id, action="policy.updated",
                target_type="policy", target_id=f"tamper-{i}",
                payload={"index": i},
            )
        assert audit_service.verify_chain(conn, tenant_id) == {"status": "ok"}


def _tamper(statement, params):
    """Superuser mutation with the immutability trigger disabled + re-enabled."""
    with _admin(TEST_DB) as c, c.cursor() as cur:
        cur.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable_trigger")
        cur.execute(statement, params)
        cur.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable_trigger")


def _mint_tamper_admin(client, idp, tenant_id, admin_sub):
    token = idp.make_jwt(
        sub=admin_sub, tenant_id=tenant_id, issuer=idp.issuer("tenant-a"),
        amr=["mfa"],
    )
    resp = client.post("/api/auth/session", json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['session_token']}"}


def test_tamper_update_detected(client, idp, admin_conn):
    """AC-AU-012: UPDATE of a middle row's payload -> API verify reports
    broken at seq 2 and the CLI exits 1 with the same seq."""
    _seed_tamper_tenant(idp, TAMPER_UPDATE_TENANT, TAMPER_ADMIN_UPDATE,
                        "sub-tamper-admin-update")
    _tamper(
        "UPDATE audit_log SET payload = payload || '{\"tampered\": true}'::jsonb "
        "WHERE tenant_id = %s AND seq = 2",
        (TAMPER_UPDATE_TENANT,),
    )

    headers = _mint_tamper_admin(client, idp, TAMPER_UPDATE_TENANT,
                                 "sub-tamper-admin-update")
    resp = client.post("/api/admin/audit/verify", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "broken", "seq": 2}

    proc = run_cli("verify", TAMPER_UPDATE_TENANT)
    assert proc.returncode == 1
    line = json.loads(proc.stdout.strip().splitlines()[0])
    assert line == {"tenant_id": TAMPER_UPDATE_TENANT, "status": "broken", "seq": 2}
    # the verification run is audited even on a broken chain (D-AU-05)
    payload = _last_audit_verified(admin_conn, TAMPER_UPDATE_TENANT)
    assert payload["result"] == "broken" and payload["broken_seq"] == 2


def test_tamper_delete_detected(client, idp, admin_conn):
    """AC-AU-012: DELETE of a middle row -> the gap breaks the chain at the
    NEXT row (seq 3): API and CLI both report it."""
    _seed_tamper_tenant(idp, TAMPER_DELETE_TENANT, TAMPER_ADMIN_DELETE,
                        "sub-tamper-admin-delete")
    _tamper(
        "DELETE FROM audit_log WHERE tenant_id = %s AND seq = 2",
        (TAMPER_DELETE_TENANT,),
    )

    headers = _mint_tamper_admin(client, idp, TAMPER_DELETE_TENANT,
                                 "sub-tamper-admin-delete")
    resp = client.post("/api/admin/audit/verify", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "broken", "seq": 3}

    proc = run_cli("verify", TAMPER_DELETE_TENANT)
    assert proc.returncode == 1
    line = json.loads(proc.stdout.strip().splitlines()[0])
    assert line == {"tenant_id": TAMPER_DELETE_TENANT, "status": "broken", "seq": 3}

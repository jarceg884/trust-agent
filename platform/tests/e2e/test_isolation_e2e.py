"""QA e2e — aislamiento multi-tenant (constitution III).

Tenant A no ve tenant B NI por API NI por DB:

- API: IDs cruzados → 404 (sin oráculo), audit ajeno invisible, hint de
  tenant forjado en el token de sesión → 401.
- DB: RLS como `trust_app` (el rol real de la app): sin scope → 0 filas
  (fail-closed), scope A → 0 filas de B, INSERT/UPDATE cross-tenant
  rechazados por WITH CHECK, y audit_log inmutable hasta para el propio
  tenant (trigger raise_exception).

Incluye el tenant recién aprovisionado por el flujo e2e (e2e-corp) como
tercer inquilino cuando el flujo ya corrió en la misma sesión.
"""
import psycopg
import pytest

from tests.conftest import (
    DEPT_A1, DEPT_B1, DEVICE_ALICE, TENANT_A, TENANT_B,
    USER_ALICE, USER_DAVE, bearer_headers, mint,
)

import os

APP_DSN = (
    "host=127.0.0.1 port=55432 user=trust_app password=trust_app_dev "
    f"dbname={os.environ.get('TRUST_TEST_DB', 'trust_agent_platform_test')}"
)


def _app_conn():
    return psycopg.connect(APP_DSN)  # NO autocommit: transacción real


def _e2e_tenant_id(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute("SELECT id FROM tenants WHERE slug = 'e2e-corp'")
        row = cur.fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# API — cross-tenant es 404 (sin oráculo), nunca 403/leak
# ---------------------------------------------------------------------------
def test_api_cross_tenant_ids_are_404(client, idp, admin_conn):
    alice = mint(client, idp, "alice")   # org_admin A (con devices.manage)
    dave = mint(client, idp, "dave", tenant=TENANT_B)  # user B (sin permiso)

    # Un dispositivo del tenant B (creado por seed directo para la prueba)
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO devices (id, tenant_id, user_id, fingerprint, status) "
            "VALUES ('51000000-0000-0000-0000-00000000000b', %s, %s, "
            "'fp-b-device', 'active') ON CONFLICT (id) DO NOTHING",
            (TENANT_B, USER_DAVE),
        )
    admin_conn.commit()

    # Alice (A, con permiso devices.manage) NO ve el dispositivo de B → 404
    resp = client.get(
        "/api/devices/51000000-0000-0000-0000-00000000000b",
        headers=bearer_headers(alice))
    assert resp.status_code == 404

    # Dave (B, SIN permiso) choca con la puerta RBAC → 403 permission_denied
    # (el permiso es por rol, no filtra información del recurso ajeno)
    resp = client.get(f"/api/devices/{DEVICE_ALICE}",
                      headers=bearer_headers(dave))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "permission_denied"

    # Alice (A) no administra el departamento de B
    resp = client.put(f"/api/budgets/dept/{DEPT_B1}",
                      json={"amount_cents": 100},
                      headers=bearer_headers(alice))
    assert resp.status_code == 404

    # Alice no toca el presupuesto de un usuario de B
    resp = client.put(f"/api/budgets/user/{USER_DAVE}",
                      json={"amount_cents": 100},
                      headers=bearer_headers(alice))
    assert resp.status_code == 404


def test_api_audit_of_other_tenant_invisible(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    # Dave (B) genera un evento auditable propio (login ya lo hizo al mint);
    # su actor jamás aparece en el audit visible para Alice.
    resp = client.get("/api/audit/events?actor_id=" + USER_DAVE,
                      headers=bearer_headers(alice))
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    # Y el inbox ajeno es invisible (ya cubierto en spec 005; re-verificado)
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    resp = client.get("/api/me/notifications",
                      headers=bearer_headers(dave))
    dave_notes = {n["id"] for n in resp.json()["notifications"]}
    resp = client.get("/api/me/notifications",
                      headers=bearer_headers(alice))
    alice_notes = {n["id"] for n in resp.json()["notifications"]}
    assert not (dave_notes & alice_notes)


def test_api_forged_tenant_hint_in_session_token(client, idp):
    """sts1.<tenant>.<session>.<secret>: cambiar el hint de tenant al token
    de Alice no lo traslada al tenant B — el lookup está scoped por hint."""
    alice = mint(client, idp, "alice")
    token = alice["session_token"]
    parts = token.split(".")
    parts[1] = TENANT_B  # hint forjado
    forged = ".".join(parts)
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_api_provisioned_tenant_isolated(client, idp, admin_conn):
    """El org_admin del tenant e2e no ve recursos de A ni de B (y viceversa)."""
    e2e = _e2e_tenant_id(admin_conn)
    if e2e is None:
        pytest.skip("el flujo e2e (e2e-corp) no corrió en esta sesión")

    # admin del tenant e2e (mint directo con issuer sustituido por el flujo)
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT u.oidc_subject FROM users u "
            "JOIN user_roles r ON r.user_id = u.id "
            "WHERE u.tenant_id = %s AND r.role_key = 'org_admin'", (e2e,))
        sub = str(cur.fetchone()[0])
    token = idp.make_jwt(sub=sub, tenant_id=e2e,
                         issuer=idp.issuer("e2e-corp"), amr=["mfa"])
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    e2e_admin = bearer_headers(resp.json())

    # El admin de e2e no ve el departamento ni el dispositivo de A
    assert client.put(f"/api/budgets/dept/{DEPT_A1}",
                      json={"amount_cents": 1},
                      headers=e2e_admin).status_code == 404
    assert client.get(f"/api/devices/{DEVICE_ALICE}",
                      headers=e2e_admin).status_code == 404

    # Alice (A) no ve dispositivos del tenant e2e
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM devices WHERE tenant_id = %s LIMIT 1", (e2e,))
        row = cur.fetchone()
    if row:
        alice = mint(client, idp, "alice")
        assert client.get(f"/api/devices/{row[0]}",
                          headers=bearer_headers(alice)).status_code == 404


# ---------------------------------------------------------------------------
# DB — RLS como trust_app (el rol con el que corre la app)
# ---------------------------------------------------------------------------
def test_db_rls_fail_closed_without_scope():
    with _app_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM users")
        assert cur.fetchone()[0] == 0, "sin scope NO debe ver NINGUNA fila"
        cur.execute("SELECT count(*) FROM sessions")
        assert cur.fetchone()[0] == 0
        c.rollback()


def test_db_rls_tenant_a_blind_to_tenant_b():
    with _app_conn() as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)",
                    (TENANT_A,))
        cur.execute("SELECT count(*) FROM users")
        n_a = cur.fetchone()[0]
        assert n_a >= 4, "Alice/Bob/Carol/Frank viven en el tenant A"
        cur.execute(
            "SELECT count(*) FROM users WHERE tenant_id = %s", (TENANT_B,))
        assert cur.fetchone()[0] == 0, "RLS: 0 filas del tenant B con scope A"
        cur.execute(
            "SELECT count(*) FROM devices WHERE tenant_id = %s", (TENANT_B,))
        assert cur.fetchone()[0] == 0
        c.rollback()


def test_db_rls_insert_cross_tenant_rejected():
    with _app_conn() as c, c.cursor() as cur:
        # Sin scope: el INSERT muere en WITH CHECK
        with pytest.raises(psycopg.errors.Error):
            cur.execute(
                "INSERT INTO departments (tenant_id, name) "
                "VALUES (%s, 'evil-dept')", (TENANT_A,))
        c.rollback()
    with _app_conn() as c, c.cursor() as cur:
        # Con scope A: insertar una fila del tenant B también muere
        cur.execute("SELECT set_config('app.tenant_id', %s, true)",
                    (TENANT_A,))
        with pytest.raises(psycopg.errors.Error):
            cur.execute(
                "INSERT INTO departments (tenant_id, name) "
                "VALUES (%s, 'evil-dept-b')", (TENANT_B,))
        c.rollback()


def test_db_rls_update_cross_tenant_is_noop(client, idp):
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    dave_session = dave["session_token"].split(".")[2]
    with _app_conn() as c, c.cursor() as cur:
        # scope A intenta revocar la sesión de Dave (B): 0 filas tocadas
        cur.execute("SELECT set_config('app.tenant_id', %s, true)",
                    (TENANT_A,))
        cur.execute(
            "UPDATE sessions SET status = 'revoked' "
            "WHERE id = %s", (dave_session,))
        assert cur.rowcount == 0, "RLS: UPDATE cross-tenant no toca nada"
        c.rollback()
    # La sesión de Dave sigue viva
    resp = client.get("/api/me", headers=bearer_headers(dave))
    assert resp.status_code == 200


def test_db_audit_log_immutable_even_for_own_tenant():
    """El trigger raise_exception niega UPDATE/DELETE a TODO rol de app."""
    with _app_conn() as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)",
                    (TENANT_A,))
        cur.execute("SELECT min(seq) FROM audit_log")
        seq = cur.fetchone()[0]
        assert seq is not None, "el tenant A tiene audit"
        with pytest.raises(psycopg.errors.Error) as exc:
            cur.execute(
                "UPDATE audit_log SET action = 'tampered' WHERE seq = %s",
                (seq,))
        msg = str(exc.value)
        assert "append-only" in msg or "permission denied" in msg, msg
        c.rollback()
        with pytest.raises(psycopg.errors.Error) as exc:
            cur.execute("DELETE FROM audit_log WHERE seq = %s", (seq,))
        msg = str(exc.value)
        assert "append-only" in msg or "permission denied" in msg, msg
        c.rollback()

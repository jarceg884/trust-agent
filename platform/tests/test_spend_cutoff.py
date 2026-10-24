"""REQ-GOV-005…011, 050 — agregación de spend, cortes y notificaciones.

Flujo REAL de extremo a extremo contra el doble HTTP declarado de LiteLLM
(D-GOV-15): emisión de key → invocaciones al gateway → worker
spend-aggregator → ledger/counters → enforcement (bloqueos, umbrales,
notificaciones). Los montos se calculan dinámicamente desde los logs del
doble para no depender del orden de ejecución de otras suites.
"""
import pytest

from conftest import (
    DEPT_A1, MIGRATOR_DB_URL, TENANT_A, TENANT_B, USER_ALICE, USER_BOB,
    USER_CAROL, USER_DAVE, _admin, bearer_headers, mint,
)

TEST_DB = "trust_agent_platform_test"
QWEN = "qwen2.5-72b-instruct"


@pytest.fixture()
def worker(litellm_fake):
    from app.db import get_engine
    from app.governance.spend_worker import run_once
    from app.jobs.service import build_migrator_engine
    from app.models.litellm import LiteLLMClient

    litellm = LiteLLMClient(litellm_fake.base_url, "sk-master-test")
    migrator = build_migrator_engine(MIGRATOR_DB_URL)

    def _run():
        return run_once(get_engine(), migrator, litellm)

    return _run


def _issue_key(client, tokens, user_id):
    resp = client.post("/api/models/keys", json={"user_id": user_id},
                       headers=bearer_headers(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fake_logs(litellm_fake, tenant_id=TENANT_A, user_id=None):
    return [
        l for l in litellm_fake.logs
        if l["metadata"].get("tenant_id") == tenant_id
        and (user_id is None or l["metadata"].get("user_id") == user_id)
    ]


def _counter(admin_conn, level, entity_id):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT spent_cents FROM budget_counters "
            "WHERE level = %s AND entity_id = %s "
            "AND period_start = date_trunc('month', now())::date",
            (level, entity_id),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def _notifications(admin_conn, user_id, type_=None):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT id, type, payload FROM notifications "
            f"WHERE tenant_id = '{TENANT_A}' AND user_id = %s "
            + ("AND type = %s" if type_ else ""),
            (user_id, type_) if type_ else (user_id,),
        )
        return cur.fetchall()


def test_ingest_idempotent_counters_byok(client, idp, litellm_fake, worker,
                                         admin_conn):
    """AC-GOV-005 + AC-GOV-034/011(byok): ledger idempotente por request_id,
    counters por nivel, modo byok sin margen."""
    alice = mint(client, idp, "alice")
    carol_key = _issue_key(client, alice, USER_CAROL)

    status, _ = litellm_fake.chat(carol_key["key"], QWEN)
    assert status == 200
    status, _ = litellm_fake.chat(carol_key["key"], QWEN)
    assert status == 200

    expected = len(_fake_logs(litellm_fake))
    summary = worker()
    ingest = summary["tenants"][TENANT_A]["ingest"]
    assert ingest["ingested"] == expected  # todos los logs del tenant A
    assert ingest["skipped"] == 0

    # Ledger: una fila por request, modo byok (aprobación user de Carol),
    # sin margen (billed == base).
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), "
            "bool_and(mode = 'byok'), "
            "bool_and(cost_billed_cents = cost_base_cents) "
            f"FROM spend_ledger WHERE tenant_id = '{TENANT_A}' "
            f"AND user_id = '{USER_CAROL}'"
        )
        rows, all_byok, no_margin = cur.fetchone()
    assert rows == len(_fake_logs(litellm_fake, user_id=USER_CAROL))
    assert all_byok, "Carol tiene aprobación user-level byok"
    assert no_margin, "BYOK no aplica margen (REQ-BUD-011)"

    carol_cents = 100 * rows
    assert _counter(admin_conn, "user", USER_CAROL) == carol_cents
    assert _counter(admin_conn, "dept", DEPT_A1) >= carol_cents
    assert _counter(admin_conn, "org", TENANT_A) >= carol_cents

    # Segunda pasada: idempotente (0 filas nuevas, counters iguales).
    worker()
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM spend_ledger "
            f"WHERE tenant_id = '{TENANT_A}'"
        )
        assert cur.fetchone()[0] == expected
    assert _counter(admin_conn, "user", USER_CAROL) == carol_cents

    # Cursor avanzado (ops.spend_cursor).
    with admin_conn.cursor() as cur:
        cur.execute("SELECT last_occurred_at FROM ops.spend_cursor WHERE id = 1")
        assert cur.fetchone()[0] is not None


def test_central_margin_applied(client, idp, litellm_fake, worker, admin_conn):
    """AC-GOV-011: modo central con margen MaaS del tenant → billed = base × 1.1."""
    with admin_conn.cursor() as cur:
        cur.execute(f"UPDATE tenants SET maas_margin_bps = 1000 "
                    f"WHERE id = '{TENANT_A}'")
    admin_conn.commit()

    alice = mint(client, idp, "alice")
    alice_key = _issue_key(client, alice, USER_ALICE)
    status, _ = litellm_fake.chat(alice_key["key"], QWEN)
    assert status == 200

    worker()
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT mode, cost_base_cents, cost_billed_cents "
            f"FROM spend_ledger WHERE tenant_id = '{TENANT_A}' "
            f"AND user_id = '{USER_ALICE}' ORDER BY ingested_at DESC LIMIT 1"
        )
        mode, base, billed = cur.fetchone()
    assert mode == "central"
    assert base == 100
    assert billed == 110  # 10 % de margen

    with admin_conn.cursor() as cur:
        cur.execute(f"UPDATE tenants SET maas_margin_bps = 0 "
                    f"WHERE id = '{TENANT_A}'")
    admin_conn.commit()


def test_user_cutoff_blocks_keys_and_notifies(client, idp, litellm_fake,
                                              worker, admin_conn):
    """AC-GOV-006: gasto ≥ presupuesto de usuario → keys bloqueadas en el
    gateway, audit budget.cutoff, notificación; invocación siguiente → 401."""
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    spent = _counter(admin_conn, "user", USER_CAROL)
    assert spent > 0, "el ingest anterior dejó gasto para Carol"
    # Presupuesto por debajo del gasto (force audita la decisión).
    resp = client.put(f"/api/budgets/user/{USER_CAROL}",
                      json={"amount_cents": spent - 50, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text

    blocked_before = len(litellm_fake.block_calls)
    worker()
    assert len(litellm_fake.block_calls) > blocked_before

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM api_keys "
            f"WHERE tenant_id = '{TENANT_A}' AND user_id = '{USER_CAROL}' "
            "AND status <> 'revoked'"
        )
        statuses = {r[0] for r in cur.fetchall()}
    assert statuses == {"blocked"}

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'budget.cutoff' "
            "AND payload->>'level' = 'user'"
        )
        assert cur.fetchone()[0] >= 1

    assert len(_notifications(admin_conn, USER_CAROL, "budget_cutoff")) >= 1

    # El estado propio refleja el corte (AC-GOV-012).
    me = client.get("/api/me/budget", headers=bearer_headers(carol))
    assert me.status_code == 200
    assert me.json()["cutoff"] is True
    assert me.json()["cutoff_level"] == "user"


def test_threshold_notification_and_unblock(client, idp, litellm_fake, worker,
                                            admin_conn):
    """AC-GOV-009 + AC-GOV-010: umbral 80 % notificado (deduplicado por
    ventana); al subir el presupuesto las keys se desbloquean."""
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    spent = _counter(admin_conn, "user", USER_CAROL)
    # Presupuesto por encima del gasto pero dentro del umbral 80 %.
    budget = spent + 50
    assert spent >= budget * 0.8  # sigue en zona de umbral
    resp = client.put(f"/api/budgets/user/{USER_CAROL}",
                      json={"amount_cents": budget, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200

    unblocked_before = len(litellm_fake.unblock_calls)
    worker()
    assert len(litellm_fake.unblock_calls) > unblocked_before  # recuperación

    carol_threshold = [
        n for n in _notifications(admin_conn, USER_CAROL, "budget_threshold_80")
        if n[2]["scope"] == f"user:{USER_CAROL}"
    ]
    assert len(carol_threshold) == 1  # exactamente una por ventana

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'budget.threshold'"
        )
        assert cur.fetchone()[0] >= 1

    # Segunda pasada del worker: sin duplicar la notificación.
    worker()
    carol_threshold = [
        n for n in _notifications(admin_conn, USER_CAROL, "budget_threshold_80")
        if n[2]["scope"] == f"user:{USER_CAROL}"
    ]
    assert len(carol_threshold) == 1

    # Las keys vuelven a estar activas y el gateway acepta (AC-GOV-010).
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM api_keys "
            f"WHERE tenant_id = '{TENANT_A}' AND user_id = '{USER_CAROL}' "
            "AND status <> 'revoked'"
        )
        assert {r[0] for r in cur.fetchall()} == {"active"}


def test_native_gateway_cutoff(client, idp, litellm_fake, admin_conn):
    """Corte NATIVO del gateway (max_budget de la key, ADR-014 plano 1):
    presupuesto de $2 → la 3ª invocación → 429 budget_exceeded."""
    alice = mint(client, idp, "alice")
    # Presupuesto de 200 cents ($2) para que la key nueva nazca con
    # max_budget = 2.0 USD (cada invocación cuesta $1 en el doble).
    resp = client.put(f"/api/budgets/user/{USER_CAROL}",
                      json={"amount_cents": 200, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200
    key = _issue_key(client, alice, USER_CAROL)
    assert key["max_budget_usd"] == 2.0

    status, _ = litellm_fake.chat(key["key"], QWEN)
    assert status == 200
    status, _ = litellm_fake.chat(key["key"], QWEN)
    assert status == 200
    status, body = litellm_fake.chat(key["key"], QWEN)
    assert status == 429
    assert body["error"]["type"] == "budget_exceeded"

    # Restaurar el presupuesto de Carol (limpieza; sin worker: las keys
    # activas no se tocan y el gasto pendiente se ingesta en el siguiente
    # ciclo sin superar el presupuesto restaurado).
    resp = client.put(f"/api/budgets/user/{USER_CAROL}",
                      json={"amount_cents": 100000, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200


def test_dept_cutoff_scopes_to_department(client, idp, litellm_fake, worker,
                                          admin_conn):
    """AC-GOV-007: corte de depto bloquea SOLO a sus usuarios; el depto
    vecino sigue operando."""
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")

    bob_key = _issue_key(client, bob, USER_BOB)
    status, _ = litellm_fake.chat(bob_key["key"], QWEN)
    assert status == 200
    worker()

    dept_spent = _counter(admin_conn, "dept", DEPT_A1)
    assert dept_spent > 0
    resp = client.put(f"/api/budgets/dept/{DEPT_A1}",
                      json={"amount_cents": 1, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200

    blocked_before = list(litellm_fake.block_calls)
    worker()
    new_blocks = set(litellm_fake.block_calls) - set(blocked_before)
    assert new_blocks  # se bloquearon keys del depto A1
    # La key de Bob (depto A2, con remanente) NO está entre las bloqueadas.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT litellm_key_hash FROM api_keys "
            f"WHERE tenant_id = '{TENANT_A}' AND user_id = '{USER_BOB}'"
        )
        bob_hashes = {r[0] for r in cur.fetchall()}
    assert bob_hashes and not (bob_hashes & new_blocks)

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT k.user_id, k.status FROM api_keys k "
            f"JOIN users u ON u.id = k.user_id "
            f"WHERE k.tenant_id = '{TENANT_A}' AND u.department_id = '{DEPT_A1}' "
            "AND k.status <> 'revoked'"
        )
        rows = cur.fetchall()
    assert rows and all(s == "blocked" for _, s in rows)

    # Notificaciones a los usuarios del depto (nivel dept).
    for uid in (USER_CAROL, USER_ALICE):
        cutoffs = [n for n in _notifications(admin_conn, uid, "budget_cutoff")
                   if n[2].get("level") == "dept"]
        assert cutoffs, f"falta notificación de corte de depto para {uid}"

    # El depto vecino (A2) sigue invocando OK.
    status, _ = litellm_fake.chat(bob_key["key"], QWEN)
    assert status == 200

    # Restaurar el presupuesto del depto y desbloquear (limpieza).
    resp = client.put(f"/api/budgets/dept/{DEPT_A1}",
                      json={"amount_cents": 400000, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200
    worker()


def test_org_cutoff_and_tenant_isolation(client, idp, litellm_fake, worker,
                                         admin_conn):
    """AC-GOV-008: corte de org bloquea TODO el tenant A y notifica a los
    org_admin; el tenant B no se afecta."""
    alice = mint(client, idp, "alice")
    dave = mint(client, idp, "dave", tenant=TENANT_B)

    dave_key = _issue_key(client, dave, USER_DAVE)
    status, _ = litellm_fake.chat(dave_key["key"], QWEN)
    assert status == 200
    worker()

    resp = client.put("/api/budgets/org",
                      json={"amount_cents": 1, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200

    blocked_before = list(litellm_fake.block_calls)
    worker()
    new_blocks = set(litellm_fake.block_calls) - set(blocked_before)
    assert new_blocks

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM api_keys "
            f"WHERE tenant_id = '{TENANT_A}' AND status = 'blocked'"
        )
        assert cur.fetchone()[0] >= 1
        cur.execute(
            "SELECT count(*) FROM api_keys "
            f"WHERE tenant_id = '{TENANT_B}' AND status = 'blocked'"
        )
        assert cur.fetchone()[0] == 0

    # org_admin notificada del corte de organización.
    org_cutoffs = [n for n in _notifications(admin_conn, USER_ALICE,
                                             "budget_cutoff")
                   if n[2].get("level") == "org"]
    assert org_cutoffs

    # El tenant B sigue operando con normalidad.
    status, _ = litellm_fake.chat(dave_key["key"], QWEN)
    assert status == 200

    # Restaurar y desbloquear el tenant A (limpieza para QA downstream).
    resp = client.put("/api/budgets/org",
                      json={"amount_cents": 1000000, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200
    worker()


def test_notifications_endpoint_scoped(client, idp, admin_conn):
    """AC-GOV-050: inbox propio, filtro unread, marcar leído; el inbox ajeno
    es invisible (RLS + filtro)."""
    carol = mint(client, idp, "carol")
    dave = mint(client, idp, "dave", tenant=TENANT_B)

    resp = client.get("/api/me/notifications",
                      headers=bearer_headers(carol))
    assert resp.status_code == 200
    notes = resp.json()["notifications"]
    assert notes, "Carol tiene notificaciones de cortes/umbrales"
    assert all(n["type"] in ("budget_cutoff", "budget_threshold_80")
               for n in notes)

    # unread_only
    unread = client.get("/api/me/notifications?unread_only=true",
                        headers=bearer_headers(carol)).json()["notifications"]
    assert len(unread) == len([n for n in notes if n["read_at"] is None])

    # marcar leída
    target = unread[0]["id"]
    marked = client.post(f"/api/me/notifications/{target}/read",
                         headers=bearer_headers(carol))
    assert marked.status_code == 204
    again = client.get("/api/me/notifications?unread_only=true",
                       headers=bearer_headers(carol)).json()["notifications"]
    assert all(n["id"] != target for n in again)

    # El inbox de Dave (tenant B) no contiene las notificaciones de Carol.
    dave_notes = client.get("/api/me/notifications",
                            headers=bearer_headers(dave)).json()["notifications"]
    carol_ids = {n["id"] for n in notes}
    assert not (carol_ids & {n["id"] for n in dave_notes})

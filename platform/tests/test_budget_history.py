"""AC-013-012 (REQ-BUD-012, spec 013 D-013-03) — histórico por período.

GET /api/budgets/history devuelve ventanas CERRADAS con presupuesto vs
usado por nivel y desglose por modelo; el spent de cada nivel cuadra con
la suma del ledger en esa ventana (reconciliación). Ingesta directa con
ocurred_at controlado (ayer vs hoy) para ventanas daily determinísticas.
El usuario de la ingesta es Alice (no Carol) para no acoplar con las
expectativas exactas de ledger de test_spend_cutoff.
"""
from datetime import date, datetime, timedelta, timezone

from conftest import (
    DEPT_A1, TENANT_A, USER_ALICE, _admin, bearer_headers, mint,
)

TEST_DB = "trust_agent_platform_test"
QWEN = "qwen2.5-72b-instruct"


def _put_budget(client, path, body, tokens):
    resp = client.put(path, json=body, headers=bearer_headers(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _raw(request_id, when, cost=100):
    from app.governance.spend import RawSpend

    return RawSpend(
        request_id=request_id, tenant_id=TENANT_A, user_id=USER_ALICE,
        model_name=QWEN, cost_base_cents=cost, tokens_in=10, tokens_out=10,
        occurred_at=when,
    )


def _ingest(entries):
    from app.db import get_engine
    from app.governance.spend import ingest_entries

    return ingest_entries(get_engine(), TENANT_A, entries)


def test_budget_history_closed_periods_and_reconciliation(
    client, idp, admin_conn,
):
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")

    from app.governance.budgets import today as utc_today

    yesterday = utc_today() - timedelta(days=1)
    when_yesterday = datetime(
        yesterday.year, yesterday.month, yesterday.day, 12, 0,
        tzinfo=timezone.utc,
    )
    when_today = datetime.now(timezone.utc)

    try:
        # Períodos daily en los tres niveles → la ventana de AYER queda
        # cerrada; la de hoy no (aún vigente).
        _put_budget(client, "/api/budgets/org",
                    {"amount_cents": 1_000_000, "period": "daily",
                     "force": True}, alice)
        _put_budget(client, f"/api/budgets/dept/{DEPT_A1}",
                    {"amount_cents": 400_000, "period": "daily",
                     "force": True}, alice)
        _put_budget(client, f"/api/budgets/user/{USER_ALICE}",
                    {"amount_cents": 200_000, "period": "daily",
                     "force": True}, alice)

        summary = _ingest([
            _raw("hist-yesterday-1", when_yesterday),
            _raw("hist-yesterday-2", when_yesterday, cost=50),
            _raw("hist-today-1", when_today),
        ])
        assert summary["ingested"] == 3, summary

        # org_admin: la ventana de ayer aparece con los tres niveles.
        resp = client.get("/api/budgets/history", headers=bearer_headers(alice))
        assert resp.status_code == 200, resp.text
        periods = resp.json()["periods"]
        closed = [p for p in periods
                  if p["period_start"] == yesterday.isoformat()]
        assert closed, f"la ventana de {yesterday} debe estar en el histórico"
        window = closed[0]
        assert window["period_end"] == yesterday.isoformat()

        # Alice usa modo central; margen del tenant A = 0 → billed = base.
        assert window["organization"]["spent_cents"] == 150
        assert window["organization"]["budget_cents"] == 1_000_000
        dept = next(d for d in window["departments"]
                    if d["department_id"] == DEPT_A1)
        assert dept["spent_cents"] == 150
        assert dept["name"] == "IT"
        user = next(u for u in window["users"] if u["user_id"] == USER_ALICE)
        assert user["spent_cents"] == 150

        # Desglose por modelo en la ventana.
        assert window["by_model"] == [
            {"model_name": QWEN, "spent_cents": 150},
        ]

        # Reconciliación (AC): el spent por nivel cuadra con el ledger.
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(cost_billed_cents), 0) FROM spend_ledger "
                f"WHERE tenant_id = '{TENANT_A}' AND occurred_at::date = %s",
                (yesterday,),
            )
            ledger_sum = cur.fetchone()[0]
        assert ledger_sum == 150
        assert window["organization"]["spent_cents"] == ledger_sum

        # La ventana de HOY (vigente) no aparece: solo períodos cerrados.
        assert all(p["period_start"] != utc_today().isoformat()
                   for p in periods)

        # dept_admin (Bob, A2): scope a su depto — sin bloqueo org, sin
        # datos del depto vecino A1.
        resp = client.get("/api/budgets/history", headers=bearer_headers(bob))
        assert resp.status_code == 200, resp.text
        scoped = [p for p in resp.json()["periods"]
                  if p["period_start"] == yesterday.isoformat()]
        assert scoped, "su propia ventana debe aparecer"
        assert scoped[0]["organization"] is None
        assert scoped[0]["departments"] == []
        assert scoped[0]["users"] == []
        assert scoped[0]["by_model"] == []

        # Usuario final: sin acceso.
        resp = client.get("/api/budgets/history", headers=bearer_headers(carol))
        assert resp.status_code == 403
    finally:
        # Restaurar los períodos del seed (monthly).
        _put_budget(client, "/api/budgets/org",
                    {"amount_cents": 1_000_000, "period": "monthly",
                     "force": True}, alice)
        _put_budget(client, f"/api/budgets/dept/{DEPT_A1}",
                    {"amount_cents": 400_000, "period": "monthly",
                     "force": True}, alice)
        _put_budget(client, f"/api/budgets/user/{USER_ALICE}",
                    {"amount_cents": 200_000, "period": "monthly",
                     "force": True}, alice)
        # Limpiar las filas de ingesta directa de este test: la suite de
        # spend_cutoff cuenta el ledger del tenant A completo.
        with admin_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM spend_ledger WHERE tenant_id = %s "
                "AND request_id LIKE %s", (TENANT_A, "hist-%"))
            cur.execute(
                "DELETE FROM budget_counters WHERE tenant_id = %s "
                "AND period_start = period_end", (TENANT_A,))
        admin_conn.commit()

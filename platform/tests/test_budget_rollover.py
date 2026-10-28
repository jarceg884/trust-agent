"""AC-BUD-010 / AC-013-010 — reinicio de contadores en el rollover de período.

Flujo REAL con período daily y reloj simulado (permitido por el AC):
ingesta directa (occurred_at controlado, sin chats del doble de LiteLLM
para no acoplar con las expectativas de test_spend_cutoff) → corte (key
bloqueada) → avanza el reloj un día → enforcement desbloquea y el contador
de la ventana nueva NACE en 0 (cada ventana es su propia fila de
budget_counters; la vieja conserva su gasto). El reloj se inyecta por el
seam `budgets.today()` (spec 013 D-013-01).
"""
from datetime import date, timedelta

import pytest

from conftest import (
    TENANT_A, USER_ALICE, _admin, bearer_headers, mint,
)

import os  # noqa: E402 (honor TRUST_TEST_DB como tests/conftest.py)
TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")
QWEN = "glm-5.2"


def _issue_key(client, tokens, user_id):
    resp = client.post("/api/models/keys", json={"user_id": user_id},
                       headers=bearer_headers(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _key_status(admin_conn, key_id):
    with admin_conn.cursor() as cur:
        cur.execute("SELECT status FROM api_keys WHERE id = %s", (key_id,))
        return cur.fetchone()[0]


def _user_counter(admin_conn, period_start):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT spent_cents FROM budget_counters "
            "WHERE level = 'user' AND entity_id = %s AND period_start = %s",
            (USER_ALICE, period_start),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _ingest_now(request_id, cost=100):
    from datetime import datetime, timezone

    from app.db import get_engine
    from app.governance.spend import ingest_entries, RawSpend

    summary = ingest_entries(get_engine(), TENANT_A, [
        RawSpend(
            request_id=request_id, tenant_id=TENANT_A, user_id=USER_ALICE,
            model_name=QWEN, cost_base_cents=cost, tokens_in=10,
            tokens_out=10, occurred_at=datetime.now(timezone.utc),
        ),
    ])
    assert summary["ingested"] == 1, summary


def _enforce(litellm_fake):
    from app.db import get_engine
    from app.governance.spend import enforce_tenant
    from app.models.litellm import LiteLLMClient

    return enforce_tenant(
        get_engine(),
        LiteLLMClient(litellm_fake.base_url, "sk-master-test"),
        TENANT_A,
    )


def test_period_window_boundaries():
    """Unit: ventanas daily/weekly/monthly correctas (D-GOV-07)."""
    from app.governance.budgets import period_window

    d = date(2026, 10, 23)  # Friday
    assert period_window("daily", d) == (d, d)
    assert period_window("weekly", d) == (date(2026, 10, 19), date(2026, 10, 25))
    assert period_window("monthly", d) == (date(2026, 10, 1), date(2026, 10, 31))
    with pytest.raises(Exception):
        period_window("yearly", d)


def test_daily_rollover_resets_counter_and_unblocks(
    client, idp, litellm_fake, admin_conn, monkeypatch,
):
    """AC-013-010: con period=daily, tras el corte el avance del reloj
    reinicia el contador (ventana nueva en 0) y la key se desbloquea."""
    from app.governance import budgets

    alice = mint(client, idp, "alice")
    alice_key = _issue_key(client, alice, USER_ALICE)

    # Presupuesto daily holgado primero (force: puede haber gasto hoy de
    # tests anteriores con ventana daily).
    resp = client.put(f"/api/budgets/user/{USER_ALICE}",
                      json={"amount_cents": 100_000, "period": "daily",
                            "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text

    # Gasto de HOY (ingesta directa) → counter de la ventana daily de hoy.
    _ingest_now("rollover-today-1")
    today = budgets.today()  # UTC: el mismo reloj que occurred_at
    spent_today = _user_counter(admin_conn, today)
    assert spent_today and spent_today > 0

    # Bajar el presupuesto al gasto exacto → enforcement corta (key blocked).
    # D-GOV-17: _user_counter devuelve Decimal (milli-centavo); el contrato
    # del API es amount_cents entero → coerción (el gasto ingestado aquí es
    # entero, la coerción es exacta).
    resp = client.put(f"/api/budgets/user/{USER_ALICE}",
                      json={"amount_cents": int(spent_today), "period": "daily",
                            "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    _enforce(litellm_fake)
    assert _key_status(admin_conn, alice_key["key_id"]) == "blocked"

    # Reloj simulado: avanza un día → la ventana vigente es la de mañana.
    tomorrow = today + timedelta(days=1)
    monkeypatch.setattr(budgets, "today", lambda: tomorrow)

    summary = _enforce(litellm_fake)
    assert summary["unblocked"] >= 1, "el corte debe levantarse al rollover"
    assert _key_status(admin_conn, alice_key["key_id"]) == "active"

    # El contador de la ventana NUEVA nace en 0; la vieja conserva su gasto.
    assert _user_counter(admin_conn, tomorrow) is None  # sin gasto aún → 0
    assert _user_counter(admin_conn, today) == spent_today

    me = client.get("/api/budgets/me", headers=bearer_headers(alice))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["spent_cents"] == 0
    assert body["user"]["cutoff"] is False
    assert body["cutoff"] is False

    # Limpieza: devolver el presupuesto de Alice al seed (monthly $2,000).
    resp = client.put(f"/api/budgets/user/{USER_ALICE}",
                      json={"amount_cents": 200_000, "period": "monthly",
                            "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    # Limpiar la fila de ingesta directa (la suite de spend_cutoff cuenta
    # el ledger del tenant A completo) y sus counters de ventana daily.
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM spend_ledger WHERE tenant_id = %s "
            "AND request_id = 'rollover-today-1'", (TENANT_A,))
        cur.execute(
            "DELETE FROM budget_counters WHERE tenant_id = %s "
            "AND period_start = period_end", (TENANT_A,))
    admin_conn.commit()

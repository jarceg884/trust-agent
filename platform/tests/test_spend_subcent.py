"""REQ-GOV-016 / AC-GOV-016…016c — precisión sub-centavo (gap B-2, t_8bd1fee8).

Regresión del bug: `round(usd*100)` + columnas bigint truncaban a 0¢ las
llamadas reales pequeñas de MaaS (9/10 filas del ledger en 0¢, counters de
user/depto en 0.0) y los presupuestos sub-contaban gasto real — el corte
llegaba tarde o nunca para requests chicos.

D-GOV-17 (spec 005, migración 0029): ledger/counters en numeric(12,3)
(milli-centavo), cuantización ROUND_HALF_UP a 0.001¢, corte por SUMA
exacta (`spent >= amount`) sin redondeo adicional.

Los tests son robustos al estado compartido de la suite completa: los
asserts de counters usan DELTAS sobre baseline y el corte usa Bob (depto
A2), que ninguna suite hermana toca.
"""
from datetime import datetime, timezone
from decimal import Decimal
import math

import pytest

from conftest import (
    DEPT_A1, TENANT_A, USER_BOB, USER_CAROL, bearer_headers, mint,
)

QWEN = "glm-5.2"
PREFIX = "subcent-"


def _raw(n, cost, user_id=USER_CAROL, when=None):
    from app.governance.spend import RawSpend

    return RawSpend(
        request_id=f"{PREFIX}{n}",
        tenant_id=TENANT_A,
        user_id=user_id,
        model_name=QWEN,
        cost_base_cents=cost,
        tokens_in=10,
        tokens_out=10,
        occurred_at=when or datetime.now(timezone.utc),
    )


def _ingest(entries, tenant=TENANT_A):
    from app.db import get_engine
    from app.governance.spend import ingest_entries

    return ingest_entries(get_engine(), tenant, entries)


def _enforce(litellm_fake, tenant=TENANT_A):
    from app.db import get_engine
    from app.governance.spend import enforce_tenant
    from app.models.litellm import LiteLLMClient

    litellm = LiteLLMClient(litellm_fake.base_url, "sk-master-test")
    return enforce_tenant(get_engine(), litellm, tenant)


@pytest.fixture
def ledger_sandbox(admin_conn):
    """Snapshot de budget_counters (A) → yield → limpieza exacta de MIS filas
    y restauración de counters (mismo patrón que test_usage_analytics)."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, level, entity_id, period_start, period_end, "
            "spent_cents, budget_cents FROM budget_counters "
            f"WHERE tenant_id = '{TENANT_A}'"
        )
        snapshot = cur.fetchall()
    yield
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM spend_ledger WHERE request_id LIKE %s",
            (f"{PREFIX}%",),
        )
        cur.execute(
            "DELETE FROM budget_counters " f"WHERE tenant_id = '{TENANT_A}'"
        )
        if snapshot:
            cur.executemany(
                "INSERT INTO budget_counters (tenant_id, level, entity_id, "
                "period_start, period_end, spent_cents, budget_cents) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                snapshot,
            )
    admin_conn.commit()


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


# ---------------------------------------------------------------------------
# Unitario — redondeo del ingest (AC-GOV-016a)
# ---------------------------------------------------------------------------
def test_usd_to_milli_cents_rounds_half_up_never_to_zero():
    """$0.000045 = 0.0045¢ → 0.005¢ (half-up), JAMÁS 0¢ (el bug B-2)."""
    from app.governance.spend_worker import _usd_to_milli_cents

    assert _usd_to_milli_cents(4.5e-05) == Decimal("0.005")
    assert _usd_to_milli_cents(0.027) == Decimal("2.700")
    assert _usd_to_milli_cents(0.0) == Decimal("0.000")
    assert _usd_to_milli_cents(None) == Decimal("0.000")
    # half-up en el borde exacto del milli-centavo
    assert _usd_to_milli_cents(0.000045) == Decimal("0.005")
    assert _usd_to_milli_cents(0.000035) == Decimal("0.004")


def test_apply_margin_decimal_exact():
    from app.governance.spend import _apply_margin

    # central 1000 bps: 0.005¢ × 1.1 = 0.0055 → 0.006¢ (half-up)
    assert _apply_margin(Decimal("0.005"), "central", 1000) == Decimal("0.006")
    # int legacy se coerciona: 1000¢ × 1.1 = 1100¢ exacto
    assert _apply_margin(1000, "central", 1000) == Decimal("1100.000")
    # byok sin margen (REQ-BUD-011)
    assert _apply_margin(Decimal("0.004"), "byok", 1000) == Decimal("0.004")


# ---------------------------------------------------------------------------
# AC-GOV-016 — el ledger acumula el gasto fraccionario real
# ---------------------------------------------------------------------------
def test_subcent_spend_accumulates_in_ledger_and_counters(
    client, idp, admin_conn, ledger_sandbox,
):
    """El repro del gap B-2: N llamadas de 0.004¢ (ninguna redondea a ≥1¢)
    DEBEN mover ledger y counters — antes quedaban 9/10 filas en 0¢."""
    carol = mint(client, idp, "carol")
    me_before = client.get(
        "/api/budgets/me", headers=bearer_headers(carol)
    ).json()["user"]["spent_cents"]

    # Baseline de counters (la suite comparte estado entre archivos).
    base_user = _counter(admin_conn, "user", USER_CAROL)
    base_dept = _counter(admin_conn, "dept", DEPT_A1)
    base_org = _counter(admin_conn, "org", TENANT_A)

    entries = [_raw(n, Decimal("0.004")) for n in range(1, 11)]
    summary = _ingest(entries)
    assert summary["ingested"] == 10, summary

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), bool_and(cost_base_cents > 0), "
            "min(cost_base_cents), sum(cost_base_cents) "
            f"FROM spend_ledger WHERE tenant_id = '{TENANT_A}' "
            "AND request_id LIKE %s",
            (f"{PREFIX}%",),
        )
        rows, all_positive, min_cost, total = cur.fetchone()
    assert rows == 10
    assert all_positive, "ninguna fila sub-centavo puede truncarse a 0¢ (B-2)"
    assert min_cost == Decimal("0.004")
    assert total == Decimal("0.040")

    # Counters de los 3 niveles acumulan la suma fraccionaria exacta (delta).
    assert _counter(admin_conn, "user", USER_CAROL) - base_user \
        == Decimal("0.040")
    assert _counter(admin_conn, "dept", DEPT_A1) - base_dept \
        == Decimal("0.040")
    assert _counter(admin_conn, "org", TENANT_A) - base_org \
        == Decimal("0.040")

    # Idempotencia preservada (D-GOV-04): re-ingesta no duplica.
    summary2 = _ingest(entries)
    assert summary2["ingested"] == 0, summary2
    assert _counter(admin_conn, "user", USER_CAROL) - base_user \
        == Decimal("0.040")

    # La API expone el gasto fraccionario como número JSON (AC-GOV-016c).
    me_after = client.get(
        "/api/budgets/me", headers=bearer_headers(carol)
    ).json()["user"]["spent_cents"]
    assert isinstance(me_after, (int, float))
    assert me_after - me_before == pytest.approx(0.04, abs=1e-6)


# ---------------------------------------------------------------------------
# AC-GOV-016a — margen MaaS sobre costo fraccionario, en Decimal exacto
# ---------------------------------------------------------------------------
def test_subcent_margin_applied_exactly(
    client, idp, admin_conn, ledger_sandbox,
):
    with admin_conn.cursor() as cur:
        cur.execute(f"UPDATE tenants SET maas_margin_bps = 1000 "
                    f"WHERE id = '{TENANT_A}'")
    admin_conn.commit()
    try:
        # Bob (depto A2, sin aprobaciones propias) resuelve mode=central
        # (default D-GOV-06); Carol sería byok y no aplicaría margen.
        summary = _ingest([_raw("m1", Decimal("0.005"), user_id=USER_BOB)])
        assert summary["ingested"] == 1, summary
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT cost_base_cents, cost_billed_cents "
                f"FROM spend_ledger WHERE tenant_id = '{TENANT_A}' "
                "AND request_id = %s",
                (f"{PREFIX}m1",),
            )
            base, billed = cur.fetchone()
        assert base == Decimal("0.005")
        # 0.005 × 1.1 = 0.0055 → 0.006¢ (ROUND_HALF_UP, nunca 0.005 por floor)
        assert billed == Decimal("0.006")
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(f"UPDATE tenants SET maas_margin_bps = 0 "
                        f"WHERE id = '{TENANT_A}'")
        admin_conn.commit()


# ---------------------------------------------------------------------------
# AC-GOV-016b — el corte cruza por la SUMA, no por llamada individual
# ---------------------------------------------------------------------------
def test_cutoff_fires_on_accumulated_subcent_sum(
    client, idp, litellm_fake, admin_conn, ledger_sandbox,
):
    """Bob (depto A2): el escenario es delta-based — la suite completa deja
    gasto previo (test_dept_cutoff de test_spend_cutoff usa Bob) y keys
    viejas bloqueadas (test_org_cutoff), así que el test ancla TODO a su
    propia key y a deltas sobre el baseline real."""
    bob = mint(client, idp, "bob")
    resp = client.post("/api/models/keys", json={"user_id": USER_BOB},
                       headers=bearer_headers(bob))
    assert resp.status_code == 201, resp.text
    key_id = resp.json()["key_id"]

    baseline = _counter(admin_conn, "user", USER_BOB)  # gasto previo real
    # Presupuesto estrictamente MAYOR que el baseline y a ≤ 2¢: el corte
    # solo puede llegar por la SUMA de mis entradas sub-centavo.
    budget_amount = int(baseline) + 2
    resp = client.put(f"/api/budgets/user/{USER_BOB}",
                      json={"amount_cents": budget_amount, "force": True},
                      headers=bearer_headers(bob))
    assert resp.status_code == 200, resp.text

    per_call = Decimal("0.004")
    gap = Decimal(budget_amount) - Decimal(str(baseline))
    to_cross = math.ceil(gap / per_call)  # entradas para alcanzar el monto
    try:
        # Fase 1: (to_cross - 1) × 0.004¢ → suma JUSTO por debajo → SIN
        # corte, aunque sean cientos de requests sub-centavo (el bug B-2
        # dejaba este gasto invisible para los counters).
        _ingest([_raw(1000 + n, per_call, user_id=USER_BOB)
                 for n in range(to_cross - 1)])
        _enforce(litellm_fake)
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM api_keys WHERE id = CAST(%s AS uuid)",
                (key_id,),
            )
            assert cur.fetchone()[0] == "active"
        delta = _counter(admin_conn, "user", USER_BOB) - Decimal(str(baseline))
        assert delta == per_call * (to_cross - 1)

        # Fase 2: UNA llamada más de 0.004¢ (sub-centavo) cruza por SUMA.
        _ingest([_raw(2000, per_call, user_id=USER_BOB)])
        blocked_before = len(litellm_fake.block_calls)
        summary = _enforce(litellm_fake)
        assert summary["blocked"] >= 1, summary

        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM api_keys WHERE id = CAST(%s AS uuid)",
                (key_id,),
            )
            assert cur.fetchone()[0] == "blocked"
            cur.execute(
                "SELECT count(*) FROM audit_log "
                f"WHERE tenant_id = '{TENANT_A}' AND action = 'budget.cutoff' "
                "AND payload->>'level' = 'user' "
                "AND payload->>'user_id' = %s",
                (USER_BOB,),
            )
            assert cur.fetchone()[0] >= 1
            cur.execute(
                "SELECT count(*) FROM notifications "
                f"WHERE tenant_id = '{TENANT_A}' AND user_id = '{USER_BOB}' "
                "AND type = 'budget_cutoff'"
            )
            assert cur.fetchone()[0] >= 1
        assert len(litellm_fake.block_calls) > blocked_before

        # El estado propio refleja el corte con el acumulado fraccionario.
        me = client.get("/api/budgets/me",
                        headers=bearer_headers(bob)).json()
        assert me["cutoff"] is True
        assert me["cutoff_level"] == "user"
        expected_spent = float(baseline) + float(per_call * to_cross)
        assert me["user"]["spent_cents"] == pytest.approx(
            expected_spent, abs=1e-6
        )
    finally:
        with admin_conn.cursor() as cur:
            # Bob no tenía presupuesto user antes del test (solo Carol/Alice
            # en la siembra): eliminar la fila restaura el estado exacto.
            cur.execute(
                "DELETE FROM budgets "
                f"WHERE tenant_id = '{TENANT_A}' AND level = 'user' "
                f"AND user_id = '{USER_BOB}'"
            )
            cur.execute(
                "UPDATE api_keys SET status = 'active', updated_at = now() "
                "WHERE id = CAST(%s AS uuid)",
                (key_id,),
            )
        admin_conn.commit()

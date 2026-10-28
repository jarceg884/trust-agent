"""Usage analytics sobre el ledger real (spec 035, AC-035-001…005).

El admin ve requests y consumos POR USUARIO (mandato Miller). Los datos
se crean con el ingest REAL (`spend.ingest_entries`, la misma vía del
worker) sobre `spend_ledger`, y cada assert del API se reconcilia con
SQL directo al ledger — nada simulado (constitution I).

Sandbox: los ingests mueven `budget_counters` de otros tests; el
fixture `ledger_sandbox` snapshotea los counters de los tenants A/B,
y al salir borra SOLO las filas 'usg-%' del ledger y restaura los
counters exactos (los counters son derivados: spent = Σ billed del
ledger en la ventana).
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, TENANT_B, USER_ALICE, USER_BOB,
    USER_CAROL, USER_DAVE, bearer_headers, mint,
)

QWEN = "glm-5.2"
DEEPSEEK = "deepseek-v4-flash"
PREFIX = "usg-"


def _raw(request_id, when, *, user_id, model, cost, tin=10, tout=10):
    from app.governance.spend import RawSpend

    return RawSpend(
        request_id=request_id, tenant_id=TENANT_A, user_id=user_id,
        model_name=model, cost_base_cents=cost, tokens_in=tin,
        tokens_out=tout, occurred_at=when,
    )


def _ingest(entries, tenant=TENANT_A):
    from app.db import get_engine
    from app.governance.spend import ingest_entries

    return ingest_entries(get_engine(), tenant, entries)


def _at(day_offset: int, hour: int = 12) -> datetime:
    from app.governance.budgets import today as utc_today

    day = utc_today() - timedelta(days=day_offset)
    return datetime(day.year, day.month, day.day, hour, 0,
                    tzinfo=timezone.utc)


@pytest.fixture
def ledger_sandbox(admin_conn):
    """Snapshot de budget_counters (A/B) → yield → limpieza exacta."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, level, entity_id, period_start, period_end, "
            "spent_cents, budget_cents FROM budget_counters "
            f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}')"
        )
        snapshot = cur.fetchall()
    yield
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM spend_ledger WHERE request_id LIKE %s",
            (f"{PREFIX}%",),
        )
        cur.execute(
            "DELETE FROM budget_counters "
            f"WHERE tenant_id IN ('{TENANT_A}', '{TENANT_B}')"
        )
        if snapshot:
            cur.executemany(
                "INSERT INTO budget_counters (tenant_id, level, entity_id, "
                "period_start, period_end, spent_cents, budget_cents) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                snapshot,
            )
    admin_conn.commit()


def _ledger_sum(admin_conn, where, params):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), COALESCE(SUM(tokens_in), 0), "
            "COALESCE(SUM(tokens_out), 0), "
            "COALESCE(SUM(cost_base_cents), 0), "
            "COALESCE(SUM(cost_billed_cents), 0) "
            f"FROM spend_ledger WHERE {where}", params,
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# AC-035-001 — agregado por usuario reconciliado con el ledger
# ---------------------------------------------------------------------------
def test_usage_aggregate_reconciles_with_ledger(
    client, idp, admin_conn, ledger_sandbox,
):
    alice = mint(client, idp, "alice")
    now = datetime.now(timezone.utc)

    # 2 usuarios × 2 modelos; una entrada de hace 31 días queda FUERA
    # de la ventana 30d y una de hoy dentro de todas.
    summary = _ingest([
        _raw(f"{PREFIX}a1", now, user_id=USER_CAROL, model=QWEN, cost=100),
        _raw(f"{PREFIX}a2", now, user_id=USER_CAROL, model=QWEN, cost=40),
        _raw(f"{PREFIX}a3", now, user_id=USER_ALICE, model=DEEPSEEK,
             cost=25, tin=7, tout=5),
        _raw(f"{PREFIX}a4", _at(31), user_id=USER_CAROL, model=QWEN,
             cost=999),
    ])
    assert summary["ingested"] == 4, summary

    resp = client.get("/api/usage?period=today",
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scope"] == "org"
    assert data["period"]["kind"] == "today"

    by_user = {u["user_id"]: u for u in data["users"]}
    carol = by_user[USER_CAROL]
    alice_row = by_user[USER_ALICE]
    # Carol: glm-5.2 con approval user-level BYOK → mode byok; margen A=0.
    assert carol["requests"] == 2
    assert carol["cost_billed_cents"] == 140
    assert carol["tokens_in"] == 20 and carol["tokens_out"] == 20
    assert carol["byok_requests"] == 2 and carol["central_requests"] == 0
    assert carol["department_id"] == DEPT_A1
    assert carol["models"] == [
        {"model_name": QWEN, "mode": "byok", "requests": 2,
         "cost_billed_cents": 140},
    ]
    assert alice_row["requests"] == 1
    assert alice_row["cost_billed_cents"] == 25
    assert alice_row["central_requests"] == 1

    # Reconciliación (AC): totales == SQL directo al ledger de HOY.
    row = _ledger_sum(
        admin_conn, "tenant_id = %s AND occurred_at::date = CURRENT_DATE",
        (TENANT_A,),
    )
    assert data["totals"] == {
        "requests": row[0], "tokens_in": row[1], "tokens_out": row[2],
        "cost_base_cents": row[3], "cost_billed_cents": row[4],
    }

    # Ventana 30d: entra la entrada de hace 31 días SOLO en custom/∞;
    # 30d cubre hoy-29..hoy → la de 31 días NO aparece.
    resp = client.get("/api/usage?period=30d",
                      headers=bearer_headers(alice))
    carol_30d = next(u for u in resp.json()["users"]
                     if u["user_id"] == USER_CAROL)
    assert carol_30d["requests"] == 2  # la de 31 días excluida

    # custom cubriendo los 31 días la incluye.
    from app.governance.budgets import today as utc_today

    resp = client.get(
        f"/api/usage?period=custom&from={utc_today() - timedelta(days=31)}"
        f"&to={utc_today()}",
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 200, resp.text
    carol_full = next(u for u in resp.json()["users"]
                      if u["user_id"] == USER_CAROL)
    assert carol_full["requests"] == 3
    assert carol_full["cost_billed_cents"] == 1139

    # Filtro por modelo: solo deepseek → solo Alice.
    resp = client.get("/api/usage?period=today&model=" + DEEPSEEK,
                      headers=bearer_headers(alice))
    users = resp.json()["users"]
    assert [u["user_id"] for u in users] == [USER_ALICE]

    # Filtro por modo byok: solo Carol (su approval user-level es byok).
    resp = client.get("/api/usage?period=today&mode=byok",
                      headers=bearer_headers(alice))
    users = resp.json()["users"]
    assert [u["user_id"] for u in users] == [USER_CAROL]


# ---------------------------------------------------------------------------
# AC-035-002 — scope dept_admin, 403 sin admin, aislamiento entre tenants
# ---------------------------------------------------------------------------
def test_usage_scope_dept_admin_403_and_isolation(
    client, idp, admin_conn, ledger_sandbox,
):
    alice = mint(client, idp, "alice")   # org_admin A
    bob = mint(client, idp, "bob")       # dept_admin A (dept A2)
    carol = mint(client, idp, "carol")   # user A (dept A1)
    now = datetime.now(timezone.utc)

    # Carol (A1) y Bob (A2) en el tenant A; Dave en el tenant B.
    _ingest([
        _raw(f"{PREFIX}s1", now, user_id=USER_CAROL, model=QWEN, cost=100),
        _raw(f"{PREFIX}s2", now, user_id=USER_BOB, model=DEEPSEEK,
             cost=50),
    ])
    _ingest_b_dave(now)

    # org_admin A: ve a Carol y Bob, jamás a Dave (tenant B, RLS).
    resp = client.get("/api/usage?period=today",
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    ids = {u["user_id"] for u in resp.json()["users"]}
    assert ids == {USER_CAROL, USER_BOB}, ids

    # dept_admin A2: solo su departamento — Carol (A1) invisible.
    resp = client.get("/api/usage?period=today",
                      headers=bearer_headers(bob))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scope"] == "dept"
    ids = {u["user_id"] for u in data["users"]}
    assert ids == {USER_BOB}, ids

    # Usuario sin rol admin → 403.
    resp = client.get("/api/usage", headers=bearer_headers(carol))
    assert resp.status_code == 403, resp.text
    resp = client.get("/api/usage/requests",
                      headers=bearer_headers(carol))
    assert resp.status_code == 403, resp.text


def _ingest_b_dave(now):
    from app.governance.spend import RawSpend

    _ingest([RawSpend(
        request_id=f"{PREFIX}s3", tenant_id=TENANT_B, user_id=USER_DAVE,
        model_name=QWEN, cost_base_cents=77, tokens_in=10, tokens_out=10,
        occurred_at=now,
    )], tenant=TENANT_B)


# ---------------------------------------------------------------------------
# AC-035-003 — validación de ventana/filtros: 422, nunca 500
# ---------------------------------------------------------------------------
def test_usage_validation_422(client, idp):
    alice = mint(client, idp, "alice")
    h = bearer_headers(alice)

    # custom sin from/to
    resp = client.get("/api/usage?period=custom", headers=h)
    assert resp.status_code == 422, resp.text
    # to < from
    resp = client.get("/api/usage?period=custom&from=2026-01-02&to=2026-01-01",
                      headers=h)
    assert resp.status_code == 422, resp.text
    # fecha malformada
    resp = client.get("/api/usage?period=custom&from=nofecha&to=2026-01-01",
                      headers=h)
    assert resp.status_code == 422, resp.text
    # período desconocido
    resp = client.get("/api/usage?period=yearly", headers=h)
    assert resp.status_code == 422, resp.text
    # modo inválido
    resp = client.get("/api/usage?mode=hybrid", headers=h)
    assert resp.status_code == 422, resp.text
    # user_id malformado → 422 (no CAST/500)
    resp = client.get("/api/usage?user_id=no-soy-uuid", headers=h)
    assert resp.status_code == 422, resp.text
    resp = client.get("/api/usage/requests?user_id=no-soy-uuid", headers=h)
    assert resp.status_code == 422, resp.text
    # nivel de export inválido
    resp = client.get("/api/usage/export?level=everything", headers=h)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# AC-035-004 — drill-down paginado
# ---------------------------------------------------------------------------
def test_usage_requests_pagination(client, idp, admin_conn, ledger_sandbox):
    alice = mint(client, idp, "alice")
    # t_e000bf40: anclar las filas a hoy-12:00 UTC en vez de `now`. Con
    # `now - i minutos` la suite que cruza la medianoche UTC pone p3/p4
    # en AYER y el conteo exacto de la ventana today falla (evidencia:
    # full-suite que arrancó 23:59:43 UTC → total 4 != 5).
    base = _at(0, 12)

    entries = [
        _raw(f"{PREFIX}p{i}", base - timedelta(minutes=i),
             user_id=USER_CAROL, model=QWEN, cost=10 + i)
        for i in range(5)
    ]
    _ingest(entries)

    h = bearer_headers(alice)
    resp = client.get(
        f"/api/usage/requests?period=today&user_id={USER_CAROL}"
        "&limit=2&offset=0", headers=h,
    )
    assert resp.status_code == 200, resp.text
    page1 = resp.json()
    assert page1["total"] == 5
    assert page1["limit"] == 2 and page1["offset"] == 0
    assert len(page1["requests"]) == 2
    first = page1["requests"][0]
    assert set(first) == {
        "request_id", "occurred_at", "user_id", "email", "display_name",
        "department_id", "model_name", "mode", "tokens_in", "tokens_out",
        "cost_base_cents", "cost_billed_cents",
    }
    assert first["user_id"] == USER_CAROL
    assert first["model_name"] == QWEN
    assert first["mode"] == "byok"

    # Orden occurred_at DESC: la más reciente primero.
    times = [r["occurred_at"] for r in page1["requests"]]
    assert times == sorted(times, reverse=True)

    # Paginación: offset avanza sobre el mismo conjunto ordenado.
    resp = client.get(
        f"/api/usage/requests?period=today&user_id={USER_CAROL}"
        "&limit=2&offset=4", headers=h,
    )
    page3 = resp.json()
    assert page3["total"] == 5
    assert len(page3["requests"]) == 1

    all_ids = {r["request_id"] for r in page1["requests"]} | \
              {r["request_id"] for r in page3["requests"]}
    resp = client.get(
        f"/api/usage/requests?period=today&user_id={USER_CAROL}"
        "&limit=2&offset=2", headers=h,
    )
    all_ids |= {r["request_id"] for r in resp.json()["requests"]}
    assert all_ids == {f"{PREFIX}p{i}" for i in range(5)}

    # Filtro modelo en el drill-down.
    resp = client.get(
        f"/api/usage/requests?period=today&user_id={USER_CAROL}"
        f"&model={DEEPSEEK}", headers=h,
    )
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# AC-035-005 — export CSV + audit usage.exported
# ---------------------------------------------------------------------------
def test_usage_export_csv_and_audit(client, idp, admin_conn, ledger_sandbox):
    alice = mint(client, idp, "alice")
    now = datetime.now(timezone.utc)

    _ingest([
        _raw(f"{PREFIX}e1", now, user_id=USER_CAROL, model=QWEN, cost=100),
        _raw(f"{PREFIX}e2", now, user_id=USER_ALICE, model=DEEPSEEK,
             cost=25, tin=7, tout=5),
    ])

    h = bearer_headers(alice)

    # Nivel users
    resp = client.get("/api/usage/export?level=users&period=today",
                      headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == ("user_id,email,display_name,department,requests,"
                        "tokens_in,tokens_out,cost_base_usd,"
                        "cost_billed_usd,models")
    carol_line = next(l for l in lines if USER_CAROL in l)
    assert ",1.00," in carol_line  # 100 cents → USD 2 decimales, sin símbolo
    assert f"{QWEN}(byok):1" in carol_line

    # Nivel requests
    resp = client.get("/api/usage/export?level=requests&period=today",
                      headers=h)
    assert resp.status_code == 200, resp.text
    lines = resp.text.strip().splitlines()
    assert lines[0] == ("request_id,occurred_at,email,display_name,"
                        "department_id,model_name,mode,tokens_in,"
                        "tokens_out,cost_base_usd,cost_billed_usd")
    req_line = next(l for l in lines if f"{PREFIX}e2" in l)
    # D-GOV-17 (AC-GOV-016c): filas por request en USD con 5 decimales —
    # el sub-centavo debe ser visible, no truncado a $0.00 (gap B-2).
    assert ",0.25000," in req_line  # 25 cents → USD 0.25000
    assert DEEPSEEK in req_line and "central" in req_line

    # Audit: usage.exported con el payload del filtro (2 exports).
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'usage.exported' "
            "ORDER BY seq DESC LIMIT 2"
        )
        payloads = [r[0] for r in cur.fetchall()]
    assert len(payloads) == 2
    levels = {p["level"] for p in payloads}
    assert levels == {"users", "requests"}
    for p in payloads:
        assert p["rows"] >= 1
        assert "from" in p and "to" in p

    # dept_admin también puede exportar SU scope (y queda auditado).
    bob = mint(client, idp, "bob")
    resp = client.get("/api/usage/export?level=users&period=today",
                      headers=bearer_headers(bob))
    assert resp.status_code == 200, resp.text
    assert USER_CAROL not in resp.text  # fuera de su scope

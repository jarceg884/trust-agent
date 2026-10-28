"""E2E usage analytics (spec 035, AC-035-006, mandato Miller).

Invocaciones REALES de 2 usuarios con modelos DISTINTOS por el data
plane (doble HTTP declarado D-GOV-15, semántica documentada del
gateway), ingest REAL vía el worker (`spend_worker.run_once`) al
ledger REAL (`spend_ledger`), y el admin ve los requests y consumos de
cada uno con los montos correctos — cada cifra del API se reconcilia
con SQL directo al ledger. Nada simulado (constitution I).

Escenario (seed del conftest, tenant A):
- Carol (usuario, dept IT/A1): approval user-level BYOK de glm-5.2 →
  su key permite glm-5.2 y el ingest estampa mode=byok.
- Bob (dept_admin, dept A2): sin filas dept → hereda el org central
  (glm-5.2 + deepseek-v4-flash) → su key permite deepseek-v4-flash y
  el ingest estampa mode=central.

Limpieza: las filas del ledger creadas aquí (request_ids capturados de
las respuestas del chat) se borran al final y los budget_counters del
tenant A se restauran al snapshot exacto (los counters son derivados).
"""
from datetime import datetime, timezone

import pytest

from conftest import (
    MIGRATOR_DB_URL, TENANT_A, USER_BOB, USER_CAROL, bearer_headers, mint,
)

QWEN = "glm-5.2"
DEEPSEEK = "deepseek-v4-flash"

# Sandbox de módulo (t_e000bf40): este e2e chatea como Carol y Bob (tenant
# A) y corre el spend worker. Su limpieza (paso 7) borra las filas del
# ledger pero NO los logs del doble — y el worker re-fetchea TODA la
# ventana del día en cada ciclo (spend_worker: start = last_occurred.date()
# - 1), así que cualquier worker run posterior (p.ej. el de
# test_tenant_self_signup, último antes de test_usage_analytics)
# RE-INGESTA esas filas ya borradas y rompe los conteos EXACTOS de la
# ventana 'today' en test_usage_analytics. El fixture (conftest) remueve
# los logs del doble, borra el delta de filas y restaura counters.
pytestmark = pytest.mark.usefixtures("ledger_delta_sandbox")


def _worker_run(litellm_fake):
    from app.db import get_engine
    from app.governance.spend_worker import run_once
    from app.jobs.service import build_migrator_engine
    from app.models.litellm import LiteLLMClient

    # t_e000bf40: MIGRATOR_DB_URL se importa a nivel de módulo (arriba).
    # Antes se importaba AQUÍ desde tests.conftest: si ese módulo no había
    # sido importado aún en collection, la importación ocurría a tiempo
    # de test — DESPUÉS del scrub de TRUST_* del fixture `env` — y
    # TEST_DB caía al nombre default, conectando el migrator a la DB
    # equivocada (o inexistente bajo un override TRUST_TEST_DB).

    return run_once(
        get_engine(),
        build_migrator_engine(MIGRATOR_DB_URL),
        LiteLLMClient(litellm_fake.base_url, "sk-master-test"),
    )


@pytest.fixture
def counters_snapshot(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, level, entity_id, period_start, period_end, "
            "spent_cents, budget_cents FROM budget_counters "
            f"WHERE tenant_id = '{TENANT_A}'"
        )
        snapshot = cur.fetchall()
    yield snapshot
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM budget_counters "
            f"WHERE tenant_id = '{TENANT_A}'"
        )
        if snapshot:
            cur.executemany(
                "INSERT INTO budget_counters (tenant_id, level, entity_id, "
                "period_start, period_end, spent_cents, budget_cents) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                snapshot,
            )
    admin_conn.commit()


def _ledger_sum_for_user(admin_conn, user_id):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), COALESCE(SUM(cost_billed_cents), 0), "
            "COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM spend_ledger "
            "WHERE tenant_id = %s AND user_id = %s "
            "AND occurred_at::date = CURRENT_DATE",
            (TENANT_A, user_id),
        )
        return cur.fetchone()


def test_usage_e2e_two_users_two_models(
    client, idp, admin_conn, litellm_fake, counters_snapshot,
):
    alice = mint(client, idp, "alice")  # org_admin A
    admin = bearer_headers(alice)

    # --- 1. Keys virtuales reales para los 2 usuarios (metadata con
    # tenant/user — el plaintext vive solo en esta respuesta).
    resp = client.post("/api/models/keys", json={"user_id": USER_CAROL},
                       headers=admin)
    assert resp.status_code == 201, resp.text
    carol_key = resp.json()["key"]
    assert resp.json()["allowed_models"] == [QWEN]

    resp = client.post("/api/models/keys", json={"user_id": USER_BOB},
                       headers=admin)
    assert resp.status_code == 201, resp.text
    bob_key = resp.json()["key"]
    assert resp.json()["allowed_models"] == [DEEPSEEK, QWEN]

    # --- 2. Invocaciones reales: Carol ×2 con glm-5.2 (byok),
    # Bob ×1 con deepseek-v4-flash (central).
    carol_request_ids = []
    for _ in range(2):
        status, body = litellm_fake.chat(carol_key, QWEN)
        assert status == 200, body
        carol_request_ids.append(body["id"])
    status, body = litellm_fake.chat(bob_key, DEEPSEEK)
    assert status == 200, body
    bob_request_id = body["id"]

    per_call_qwen = litellm_fake.per_call_cents(QWEN)
    per_call_deepseek = litellm_fake.per_call_cents(DEEPSEEK)
    assert per_call_qwen > 0 and per_call_deepseek > 0
    # D-GOV-17: per_call_cents es Decimal exacto (deepseek = 2.700¢, no 3¢
    # redondeados). El API devuelve int si el monto es entero y float si
    # hay fracción → para comparar el lado JSON se usa float().
    per_call_qwen_f = float(per_call_qwen)
    per_call_deepseek_f = float(per_call_deepseek)

    # --- 3. Ingest REAL del worker al ledger.
    summary = _worker_run(litellm_fake)
    ingest = summary["tenants"][TENANT_A]["ingest"]
    assert ingest["ingested"] >= 3, summary  # las 3 invocaciones

    # --- 4. El admin ve los requests y consumos de CADA usuario.
    resp = client.get("/api/usage?period=today", headers=admin)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    by_user = {u["user_id"]: u for u in data["users"]}
    assert USER_CAROL in by_user and USER_BOB in by_user

    carol = by_user[USER_CAROL]
    bob = by_user[USER_BOB]

    # Modelos distintos por usuario, con el modo correcto.
    assert carol["models"] == [
        {"model_name": QWEN, "mode": "byok", "requests": 2,
         "cost_billed_cents": 2 * per_call_qwen_f},
    ]
    assert bob["models"] == [
        {"model_name": DEEPSEEK, "mode": "central", "requests": 1,
         "cost_billed_cents": per_call_deepseek_f},
    ]
    assert carol["byok_requests"] == 2
    assert bob["central_requests"] == 1

    # Montos correctos: el agregado del API == SQL directo al ledger
    # (reconciliación por usuario, AC-035-006).
    n, billed, tin, tout = _ledger_sum_for_user(admin_conn, USER_CAROL)
    assert carol["requests"] == n
    assert carol["cost_billed_cents"] == float(billed)
    assert carol["tokens_in"] == tin and carol["tokens_out"] == tout
    assert carol["cost_billed_cents"] >= 2 * per_call_qwen_f

    n, billed, tin, tout = _ledger_sum_for_user(admin_conn, USER_BOB)
    assert bob["requests"] == n
    assert bob["cost_billed_cents"] == float(billed)
    assert bob["cost_billed_cents"] >= per_call_deepseek_f

    # --- 5. Drill-down: los requests individuales de Carol, con los
    # montos por request correctos.
    resp = client.get(
        f"/api/usage/requests?period=today&user_id={USER_CAROL}"
        "&limit=200", headers=admin,
    )
    assert resp.status_code == 200, resp.text
    page = resp.json()
    by_id = {r["request_id"]: r for r in page["requests"]}
    for rid in carol_request_ids:
        row = by_id[rid]
        assert row["model_name"] == QWEN
        assert row["mode"] == "byok"
        assert row["cost_billed_cents"] == per_call_qwen_f
        assert row["cost_base_cents"] == per_call_qwen_f  # byok sin margen
        assert row["tokens_in"] == litellm_fake.TOKENS_IN
        assert row["tokens_out"] == litellm_fake.TOKENS_OUT
        assert row["occurred_at"].startswith(
            datetime.now(timezone.utc).date().isoformat()
        )

    resp = client.get(
        f"/api/usage/requests?period=today&user_id={USER_BOB}",
        headers=admin,
    )
    bob_row = next(r for r in resp.json()["requests"]
                   if r["request_id"] == bob_request_id)
    assert bob_row["model_name"] == DEEPSEEK
    assert bob_row["mode"] == "central"
    assert bob_row["cost_billed_cents"] == per_call_deepseek_f

    # --- 6. Export CSV por request contiene las invocaciones reales,
    # y queda auditado (usage.exported).
    resp = client.get("/api/usage/export?level=requests&period=today",
                      headers=admin)
    assert resp.status_code == 200, resp.text
    for rid in carol_request_ids + [bob_request_id]:
        assert rid in resp.text
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'usage.exported'"
        )
        assert cur.fetchone()[0] >= 1

    # --- 7. Limpieza: borrar SOLO las filas de esta corrida y
    # restaurar los counters (el fixture counters_snapshot restaura).
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM spend_ledger WHERE request_id = ANY(%s)",
            (carol_request_ids + [bob_request_id],),
        )
    admin_conn.commit()

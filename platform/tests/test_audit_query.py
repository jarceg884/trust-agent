"""AC-AU-001/002/003/011/031 — audit query API, export, verify emission and
domain-action chaining (spec 008).

All queries run through the real API against the session PostgreSQL database
(RLS active); seeds use the canonical `audit.emit` inside `tenant_tx`.
"""
import csv
import io
from datetime import datetime, timezone

import pytest

from app.audit import service as audit_service
from app.config import get_settings
from app.db import build_engine, tenant_tx
from tests.conftest import (
    DEPT_A2,
    DEVICE_CAROL,
    TENANT_A,
    TENANT_B,
    USER_ALICE,
    USER_BOB,
    USER_CAROL,
    USER_DAVE,
    bearer_headers,
    mint,
)

TAGS = ["aq-alpha", "aq-beta", "aq-gamma", "aq-delta", "aq-epsilon"]
EVENTS_PATH = "/api/audit/events"


def _emit_query_seed() -> None:
    """5 events with unique tags: distinct actors, roles, actions, targets."""
    engine = build_engine(get_settings())
    with tenant_tx(engine, TENANT_A) as conn:
        audit_service.emit(
            conn, tenant_id=TENANT_A, actor_role="org_admin", actor_id=USER_ALICE,
            action="policy.updated", target_type="policy", target_id="audit-q-alpha",
            payload={"tag": "aq-alpha"},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_A, actor_role="dept_admin", actor_id=USER_BOB,
            action="policy.updated", target_type="policy", target_id="audit-q-beta",
            payload={"tag": "aq-beta"},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_A, actor_role="user", actor_id=USER_CAROL,
            action="device.enrolled", target_type="device", target_id="audit-q-gamma",
            device_id=DEVICE_CAROL, payload={"tag": "aq-gamma"},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_A, actor_role="system",
            action="budget.cutoff", target_type="budget", target_id="audit-q-delta",
            cost_cents=1234, payload={"tag": "aq-delta"},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_A, actor_role="org_admin", actor_id=USER_ALICE,
            action="model.consumed", target_type="model", target_id="audit-q-epsilon",
            cost_cents=500, payload={"tag": "aq-epsilon"},
        )


@pytest.fixture(scope="module")
def seeded(client, admin_conn):
    """Emit the query seed once; return the [t0, t1) window of its timestamps."""
    t0 = datetime.now(timezone.utc)
    _emit_query_seed()
    t1 = datetime.now(timezone.utc)
    return {"t0": t0, "t1": t1}


def _get(client, headers, params):
    resp = client.get(EVENTS_PATH, headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_filters_and_pagination(client, idp, seeded):
    """AC-AU-001: every filter returns exactly the expected subset; limit/offset
    paginate over the same total; items carry the full shape incl. hashes."""
    headers = bearer_headers(mint(client, idp, "alice"))

    body = _get(client, headers, {"q": "aq-"})
    assert body["total"] == 5
    assert [i["target_id"] for i in body["items"]] == [
        "audit-q-epsilon", "audit-q-delta", "audit-q-gamma",
        "audit-q-beta", "audit-q-alpha",
    ]  # ORDER BY id DESC
    item = body["items"][0]
    for key in ("id", "seq", "ts", "actor_role", "actor_id", "action",
                "target_type", "target_id", "device_id", "cost_cents",
                "payload", "hash_prev", "hash"):
        assert key in item, key
    assert len(item["hash"]) == 64 and len(item["hash_prev"]) == 64

    # single filters -> exactly one seeded event each
    r = _get(client, headers, {"target_id": "audit-q-alpha"})
    assert r["total"] == 1 and r["items"][0]["payload"]["tag"] == "aq-alpha"
    r = _get(client, headers, {
        "actor_id": USER_BOB, "action": "policy.updated",
        "target_id": "audit-q-beta",
    })
    assert r["total"] == 1 and r["items"][0]["actor_id"] == USER_BOB
    r = _get(client, headers, {"action": "device.enrolled", "device_id": DEVICE_CAROL})
    assert r["total"] == 1 and r["items"][0]["device_id"] == DEVICE_CAROL
    r = _get(client, headers, {"target_type": "budget", "q": "aq-"})
    assert r["total"] == 1 and r["items"][0]["cost_cents"] == 1234
    # department filter: only events whose actor belongs to that department
    r = _get(client, headers, {"department_id": DEPT_A2, "q": "aq-"})
    assert r["total"] == 1 and r["items"][0]["payload"]["tag"] == "aq-beta"

    # time range: the seed lives inside [t0, t1)
    r = _get(client, headers, {"since": seeded["t0"].isoformat(), "q": "aq-"})
    assert r["total"] == 5
    r = _get(client, headers, {"until": seeded["t0"].isoformat(), "q": "aq-"})
    assert r["total"] == 0
    r = _get(client, headers, {"since": seeded["t0"].isoformat(),
                               "until": seeded["t1"].isoformat(), "q": "aq-"})
    assert r["total"] == 5

    # free text: user wildcards are literal (D-AU-11)
    assert _get(client, headers, {"q": "aq%al"})["total"] == 0
    assert _get(client, headers, {"q": "aq_al"})["total"] == 0
    assert _get(client, headers, {"q": "aq-gamma"})["total"] == 1

    # pagination over the same total
    pages = [
        _get(client, headers, {"q": "aq-", "limit": 2, "offset": off})
        for off in (0, 2, 4)
    ]
    assert all(p["total"] == 5 for p in pages)
    ids = [i["id"] for p in pages for i in p["items"]]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert (pages[0]["items"][0]["id"] > pages[1]["items"][0]["id"]
            > pages[2]["items"][0]["id"])


def test_role_scopes(client, idp, seeded):
    """AC-AU-002: org_admin sees the whole tenant; dept_admin only events of
    their departments; user only their own; another tenant sees nothing (RLS);
    system-emitted events (actor NULL) are org_admin-only."""
    alice = bearer_headers(mint(client, idp, "alice"))
    bob = bearer_headers(mint(client, idp, "bob"))
    carol = bearer_headers(mint(client, idp, "carol"))
    dave = bearer_headers(mint(client, idp, "dave", tenant=TENANT_B))

    def tags(headers):
        return sorted(i["payload"]["tag"] for i in _get(client, headers, {"q": "aq-"})["items"])

    assert tags(alice) == sorted(TAGS)   # org_admin: whole tenant
    assert tags(bob) == ["aq-beta"]      # dept_admin of Finance (A2)
    assert tags(carol) == ["aq-gamma"]   # user: own events only
    assert tags(dave) == []              # tenant B: RLS hides tenant A

    # system event (actor_id NULL) -> org_admin only
    params = {"action": "budget.cutoff", "q": "aq-"}
    assert _get(client, alice, params)["total"] == 1
    assert _get(client, bob, params)["total"] == 0
    assert _get(client, carol, params)["total"] == 0


def test_export_csv_json(client, idp, seeded):
    """AC-AU-003: CSV/JSON export of the filtered range; row count == total of
    the same paginated query; CSV carries the full columns incl. hashes."""
    headers = bearer_headers(mint(client, idp, "alice"))
    paged = _get(client, headers, {"q": "aq-"})
    assert paged["total"] == 5

    csv_resp = client.get(
        f"{EVENTS_PATH}/export", headers=headers, params={"format": "csv", "q": "aq-"}
    )
    assert csv_resp.status_code == 200, csv_resp.text
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    assert len(rows) == paged["total"]
    assert rows[0]["hash"] and rows[0]["hash_prev"]
    assert rows[0]["action"] == "model.consumed"  # id DESC, same order

    json_resp = client.get(
        f"{EVENTS_PATH}/export", headers=headers, params={"format": "json", "q": "aq-"}
    )
    assert json_resp.status_code == 200
    items = json_resp.json()
    assert [i["id"] for i in items] == [i["id"] for i in paged["items"]]


def test_verify_emits_event(client, idp, admin_conn):
    """AC-AU-011: POST verify -> audit.verified with source=api, result=ok;
    the append keeps the chain valid (D-AU-05)."""
    headers = bearer_headers(mint(client, idp, "alice"))
    resp = client.post("/api/admin/audit/verify", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok"}

    payload = admin_conn.execute(
        "SELECT payload FROM audit_log WHERE tenant_id = %s "
        "AND action = 'audit.verified' ORDER BY id DESC LIMIT 1",
        (TENANT_A,),
    ).fetchone()[0]
    assert payload["source"] == "api"
    assert payload["result"] == "ok"

    engine = build_engine(get_settings())
    with tenant_tx(engine, TENANT_A) as conn:
        assert audit_service.verify_chain(conn, TENANT_A) == {"status": "ok"}


def test_domain_actions_chain(admin_conn):
    """AC-AU-031: representative domain actions of every contract group
    (provisioning, policy, vault, budget cutoff, devices, model consumption)
    chain correctly with cost_cents/device_id populated and verify OK."""
    engine = build_engine(get_settings())
    with tenant_tx(engine, TENANT_B) as conn:
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="provider",
            action="tenant.provisioned", target_type="tenant",
            target_id=f"tenant:{TENANT_B}",
            payload={"slug": "tenant-b", "plan": "starter", "idempotency_key": "q-31"},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="org_admin", actor_id=USER_DAVE,
            action="policy.updated", target_type="policy", target_id="policy:require_mfa",
            payload={"level": "org", "version": 2},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="system",
            action="vault.credential_used", target_type="credential",
            target_id="credential:q-1", payload={"connector_slug": "email"},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="system",
            action="budget.cutoff", target_type="budget", target_id="budget:q-1",
            cost_cents=9999, payload={"level": "user", "spend_cents": 9999},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="user", actor_id=USER_DAVE,
            action="device.enrolled", target_type="device", target_id="device:q-1",
            device_id=DEVICE_CAROL,
            payload={"fingerprint": "fp-q-31", "user_id": USER_DAVE},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="org_admin", actor_id=USER_DAVE,
            action="device.revoked", target_type="device", target_id="device:q-1",
            device_id=DEVICE_CAROL, payload={"user_id": USER_DAVE},
        )
        audit_service.emit(
            conn, tenant_id=TENANT_B, actor_role="user", actor_id=USER_DAVE,
            action="model.consumed", target_type="model", target_id="model:qwen-32b",
            device_id=DEVICE_CAROL, cost_cents=4213,
            payload={"user_id": USER_DAVE, "cost_cents": 4213},
        )
        # the whole tenant chain (seed logins + the 7 domain events) verifies
        assert audit_service.verify_chain(conn, TENANT_B) == {"status": "ok"}

    seqs = [r[0] for r in admin_conn.execute(
        "SELECT seq FROM audit_log WHERE tenant_id = %s ORDER BY id DESC LIMIT 7",
        (TENANT_B,),
    ).fetchall()]
    assert seqs == sorted(seqs, reverse=True)
    assert seqs[0] - seqs[-1] == 6  # 7 consecutive seqs, no gaps

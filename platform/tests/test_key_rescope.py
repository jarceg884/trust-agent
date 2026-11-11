"""Spec 044 — el sync del bundle re-alcancea las claves del gateway.

Una clave LiteLLM nace con los modelos aprobados DEL MOMENTO (issue_key). Si
la política retira un modelo, la clave vieja lo sigue aceptando en el gateway
— reproducido en vivo en el pilot: HTTP 200 con un modelo ya desautorizado.
El sync del bundle (GET /api/me/governance-bundle) debe converger las claves
activas del usuario a los ``approved_models`` que acaba de entregar.

Evidencia real contra el test double documentado ``litellm_fake`` (D-GOV-15):
data plane incluida — el modelo retirado responde 200 antes del sync y 403
después. Los modelos se DERIVAN del bundle real (nada hardcodeado): la clave
se emite con el conjunto efectivo y el bundle lo estrecha vía policy.
"""
from __future__ import annotations

import hashlib
import json

import jwt as pyjwt
import pytest

from conftest import DEPT_A1, TENANT_A, USER_ALICE, bearer_headers, mint

# The hole is reproduced with a REAL 200 chat as Alice (tenant A): without the
# module sandbox that log stayed in the session-wide gateway double and any
# later worker run ingested it into the shared ledger — skewing the exact
# per-tenant counts of test_usage_analytics / test_spend_cutoff.
pytestmark = pytest.mark.usefixtures("ledger_delta_sandbox")

from app.models import keys as keys_service


def _issue_key(client, idp) -> dict:
    """POST /api/models/keys para uno mismo (camino real de la consola)."""
    tokens = mint(client, idp, "alice")
    resp = client.post(
        "/api/models/keys",
        json={"user_id": USER_ALICE},
        headers=bearer_headers(tokens),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _key_row(admin_conn):
    return admin_conn.execute(
        "SELECT id, alias, litellm_key_hash, allowed_models FROM api_keys "
        "WHERE user_id = %s AND status = 'active'",
        (USER_ALICE,),
    ).fetchone()


def _get_bundle(client, idp):
    from test_governance_bundle import _get_bundle as get

    return get(client, idp)


def _approved_from_bundle(resp) -> list[str]:
    payload = pyjwt.decode(resp.json()["bundle"], options={"verify_signature": False})
    return list(payload["approved_models"])


def _widen_key(admin_conn, litellm_fake, plaintext: str, models: list[str]) -> None:
    """Estado del incidente: clave emitida bajo una política más ancha."""
    admin_conn.execute(
        "UPDATE api_keys SET allowed_models = %s "
        "WHERE user_id = %s AND status = 'active'",
        (json.dumps(sorted(models)), USER_ALICE),
    )
    admin_conn.commit()
    token = hashlib.sha256(plaintext.encode()).hexdigest()
    litellm_fake.keys[token]["public"]["models"] = sorted(models)


def test_sync_rescopes_key_to_current_policy(client, idp, admin_conn, litellm_fake):
    issued = _issue_key(client, idp)
    assert issued["allowed_models"], "la fixture debe aprobar algún modelo"

    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text
    approved = sorted(set(_approved_from_bundle(resp)))
    assert sorted(set(issued["allowed_models"])) == approved, (
        "la clave nace alineada con la política; el hueco aparece cuando la "
        "política ESTRECHA después (simulado abajo ensanchando la clave)"
    )
    # Modelo real del catálogo que la política de alice NO aprueba — el que
    # quedaría servido por una clave emitida antes del cambio (el incidente).
    gone = next(m for m in sorted(litellm_fake.prices) if m not in approved)
    wide = approved + [gone]

    # Clave ancha (pre-fix): el modelo retirado SIGUE respondiendo en el
    # gateway — el hueco, en vivo.
    _widen_key(admin_conn, litellm_fake, issued["key"], wide)
    status, _ = litellm_fake.chat(issued["key"], gone)
    assert status == 200, "el hueco no se reprodujo"
    litellm_fake.update_calls.clear()

    # El sync converge la clave a los aprobados del bundle.
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text

    row = _key_row(admin_conn)
    assert sorted(row[3]) == approved
    calls = [c for c in litellm_fake.update_calls if "models" in c]
    assert calls, "el gateway nunca recibió el re-alcanceo"
    assert sorted(calls[-1]["models"]) == approved

    # DESPUÉS: el gateway rechaza el modelo retirado.
    status, _ = litellm_fake.chat(issued["key"], gone)
    assert status == 403

    audited = admin_conn.execute(
        "SELECT payload FROM audit_log WHERE action = 'model.key_rescoped' "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    assert audited, "el re-alcanceo no quedó auditado"
    audit_payload = audited[0] if isinstance(audited[0], dict) else json.loads(audited[0])
    assert sorted(audit_payload["allowed_models"]) == approved


def test_sync_without_change_never_calls_the_gateway(client, idp, admin_conn,
                                                     litellm_fake):
    _issue_key(client, idp)
    # Primer sync: converge la clave a la política (puede re-alcancear).
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text
    litellm_fake.update_calls.clear()

    # Segundo sync sin cambios: cero llamadas al gateway.
    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text
    assert not [c for c in litellm_fake.update_calls if "models" in c]


def test_rescope_to_no_models_pins_the_sentinel_never_an_empty_list(
        client, idp, admin_conn, litellm_fake):
    """Deny-all, never fail-open: ``models: []`` in LiteLLM means EVERY model, so an empty
    approval is never sent as []. The key is pinned to NO_MODELS_SENTINEL (every model 403)
    and a later non-empty rescope restores it. (Before: [] was a no-op and the key kept its
    previous models at the gateway.)"""
    from app.db import get_engine, set_tenant

    issued = _issue_key(client, idp)
    model = issued["allowed_models"][0]
    assert litellm_fake.chat(issued["key"], model)[0] == 200
    litellm_fake.update_calls.clear()

    def rescope(models):
        with get_engine().begin() as conn:
            set_tenant(conn, TENANT_A)
            principal = keys_service._target_principal(conn, TENANT_A, USER_ALICE)
            return keys_service.rescope_user_keys(
                conn, principal, user_id=USER_ALICE, models=models
            )

    assert rescope([]) >= 1
    sent = [c["models"] for c in litellm_fake.update_calls if "models" in c]
    assert sent and all(m == [keys_service.NO_MODELS_SENTINEL] for m in sent)
    assert litellm_fake.chat(issued["key"], model)[0] == 403
    assert list(_key_row(admin_conn)[3]) == [], "the DB keeps the policy truth: []"
    assert rescope([]) == 0, "already deny-all: nothing to send"

    assert rescope([model]) >= 1
    assert litellm_fake.chat(issued["key"], model)[0] == 200


def test_sync_survives_gateway_outage(client, idp, admin_conn, litellm_fake,
                                      monkeypatch):
    """El sync no se rompe si el gateway está caído; reintenta el próximo."""
    issued = _issue_key(client, idp)
    resp = _get_bundle(client, idp)
    approved = sorted(set(_approved_from_bundle(resp)))
    gone = next(m for m in sorted(litellm_fake.prices) if m not in approved)
    wide = approved + [gone]

    _widen_key(admin_conn, litellm_fake, issued["key"], wide)

    class GatewayDown:
        def update_key_models(self, *args, **kwargs):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(keys_service, "get_litellm", lambda: GatewayDown())

    resp = _get_bundle(client, idp)
    assert resp.status_code == 200, resp.text

    row = _key_row(admin_conn)
    assert sorted(row[3]) == sorted(wide), (
        "con el gateway caído la clave queda ancha (documentado) — pero el "
        "sync debe responder 200 para que el dispositivo no pierda el bundle"
    )


# A second org-approved model for alice's department, so Model Access is WIDER than a policy
# that lists one model (seed: org approves glm-5.2 + deepseek-v4-flash; dept A1 only glm-5.2).
WIDE_MODEL = "deepseek-v4-flash"


def _alice_effective_models() -> list[str]:
    from app.db import get_engine, set_tenant
    from app.models import access

    with get_engine().begin() as conn:
        set_tenant(conn, TENANT_A)
        principal = keys_service._target_principal(conn, TENANT_A, USER_ALICE)
        return access.effective_models_for_user(conn, principal)


def _dept_policy(client, idp, models: list[str]) -> str:
    """A Policy Studio bundle for alice's department listing *models* (caller deletes it)."""
    resp = client.post(
        "/api/policy-bundles",
        json={
            "name": "key-scope-check", "description": "", "desktop": {},
            "credentials": {}, "global_prompt": "", "profiles": [], "contracts": [],
            "models": models, "default_model": models[0], "agents": [],
            "department_id": DEPT_A1, "user_id": None,
        },
        headers=bearer_headers(mint(client, idp, "alice")),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture
def alice_wide_model_access(client, idp, admin_conn):
    """Approve WIDE_MODEL for dept A1 and restore exactly the previous row state afterwards."""
    select = (
        "SELECT ma.id, ma.status FROM model_access ma "
        "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
        "WHERE ma.level = 'dept' AND ma.department_id = %s AND mc.model_name = %s"
    )
    before = admin_conn.execute(select, (DEPT_A1, WIDE_MODEL)).fetchone()
    resp = client.put(
        "/api/models/access",
        json={"model_name": WIDE_MODEL, "level": "dept", "department_id": DEPT_A1},
        headers=bearer_headers(mint(client, idp, "alice")),
    )
    assert resp.status_code in (200, 201), resp.text
    effective = _alice_effective_models()
    assert len(effective) >= 2, f"fixture: alice needs 2+ effective models, has {effective}"
    yield effective
    after = admin_conn.execute(select, (DEPT_A1, WIDE_MODEL)).fetchone()
    if before is None and after is not None:
        admin_conn.execute("DELETE FROM model_access WHERE id = %s", (after[0],))
    elif before is not None:
        admin_conn.execute(
            "UPDATE model_access SET status = %s WHERE id = %s", (before[1], before[0])
        )
    admin_conn.commit()


def _exchange_with_model_key(client, idp) -> dict:
    """Admin-issued enrollment token redeemed by a client that never syncs a bundle."""
    admin = bearer_headers(mint(client, idp, "alice"))
    pre = client.post(
        "/api/devices/preauthorize",
        json={"user_id": USER_ALICE, "device_name": "scope-check", "platform": "test"},
        headers=admin,
    )
    assert pre.status_code in (200, 201), pre.text
    redeemed = client.post(
        "/api/devices/enroll/exchange",
        json={"enrollment_token": pre.json()["enrollment_token"],
              "fingerprint": f"scope-check-{pre.json()['device_id']}"},
    )
    assert redeemed.status_code == 200, redeemed.text
    body = redeemed.json()
    assert body.get("model_key"), "the exchange must provision the model key"
    return body


def test_key_is_born_with_the_bundle_scope(client, idp, admin_conn, litellm_fake,
                                           alice_wide_model_access):
    """Live gap (pilot e2e-lab, 2026-11-11): Model Access approved 3 models and the Policy
    Studio listed 2. The activation issued the key with all 3; only the device's FIRST bundle
    sync narrowed it, so a client that redeemed its enrollment token directly and never synced
    kept a model outside the policy. Keys are now born with the bundle's scope — through the
    console AND through the enrollment exchange — before any sync happens."""
    effective = alice_wide_model_access
    policy_models = [m for m in effective if m != WIDE_MODEL][:1] or effective[:1]
    outside = next(m for m in effective if m not in policy_models)
    bundle_id = _dept_policy(client, idp, policy_models)
    admin = bearer_headers(mint(client, idp, "alice"))
    try:
        issued = _issue_key(client, idp)  # console path, no sync yet
        assert issued["allowed_models"] == policy_models
        assert litellm_fake.chat(issued["key"], outside)[0] == 403
        assert litellm_fake.chat(issued["key"], policy_models[0])[0] == 200

        exchanged = _exchange_with_model_key(client, idp)  # enrollment path, never syncs
        assert exchanged["allowed_models"] == policy_models
        assert litellm_fake.chat(exchanged["model_key"], outside)[0] == 403

        # The bundle agrees with the keys: what the device is told == what the gateway accepts.
        resp = _get_bundle(client, idp)
        assert resp.status_code == 200, resp.text
        assert _approved_from_bundle(resp) == policy_models
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)


def test_revoking_every_approval_denies_the_key_even_when_the_policy_lists_models(
        client, idp, admin_conn, litellm_fake):
    """D-CP-15 deny-overrides. Before: a user with NO approved models whose policy listed
    models got those models in the bundle (``not allowed or …``) and the sync re-scoped the
    key TO them — revoking every approval did not cut the gateway. Now the bundle approves
    nothing and the key is pinned to the deny-all sentinel; restoring access restores it."""
    alice = bearer_headers(mint(client, idp, "alice"))
    issued = _issue_key(client, idp)
    model = issued["allowed_models"][0]
    bundle_id = _dept_policy(client, idp, [model])
    deny = client.put(
        f"/api/policies/user/{USER_ALICE}",
        json={"key": "allowed_models", "value": []},
        headers=alice,
    )
    assert deny.status_code == 200, deny.text
    try:
        assert _alice_effective_models() == []
        litellm_fake.update_calls.clear()
        resp = _get_bundle(client, idp)
        assert resp.status_code == 200, resp.text
        assert _approved_from_bundle(resp) == []
        sent = [c["models"] for c in litellm_fake.update_calls if "models" in c]
        assert [keys_service.NO_MODELS_SENTINEL] in sent and [] not in sent
        assert litellm_fake.chat(issued["key"], model)[0] == 403
    finally:
        client.delete(
            f"/api/policies/user/allowed_models?user_id={USER_ALICE}&department_id={DEPT_A1}",
            headers=alice,
        )
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=alice)

    resp = _get_bundle(client, idp)  # access restored: the next sync restores the key
    assert resp.status_code == 200, resp.text
    assert litellm_fake.chat(issued["key"], model)[0] == 200

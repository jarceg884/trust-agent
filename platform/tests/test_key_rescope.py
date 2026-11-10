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

from conftest import TENANT_A, USER_ALICE, bearer_headers, mint

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


def test_rescope_refuses_empty_models(client, idp, admin_conn, litellm_fake):
    """Fail-closed: una lista vacía en LiteLLM permite TODO — jamás se envía."""
    from app.db import get_engine, set_tenant

    _issue_key(client, idp)
    litellm_fake.update_calls.clear()

    with get_engine().begin() as conn:
        set_tenant(conn, TENANT_A)
        principal = keys_service._target_principal(conn, TENANT_A, USER_ALICE)
        assert keys_service.rescope_user_keys(
            conn, principal, user_id=USER_ALICE, models=[]
        ) == 0

    assert not [c for c in litellm_fake.update_calls if "models" in c]


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

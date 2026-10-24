"""AC-CP-053 — device tokens + DeviceGate (ADR-013)."""
import pytest

from app.config import get_settings
from app.devices.tokens import issue_device_token, verify_device_token
from app.errors import ApiError
from tests.conftest import (
    DEVICE_ALICE, DEVICE_CAROL, TENANT_A, USER_ALICE, bearer_headers, mint,
)


def _device_headers(settings, device_id=TENANT_A and DEVICE_ALICE,
                    user_id=USER_ALICE, tenant=TENANT_A, ttl_valid=True):
    token = issue_device_token(
        settings, device_id=device_id, user_id=user_id, tenant_id=tenant,
    )
    return {"X-Device-Token": token}


def test_device_token_roundtrip(env):
    settings = get_settings()
    token = issue_device_token(
        settings, device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A,
    )
    claims = verify_device_token(settings, token)
    assert claims["device_id"] == DEVICE_ALICE
    assert claims["user_id"] == USER_ALICE
    assert claims["tenant_id"] == TENANT_A
    assert claims["exp"] > claims["iat"]


def test_device_token_wrong_secret_rejected(db):
    import jwt as pyjwt

    settings = get_settings()
    token = issue_device_token(
        settings, device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A,
    )
    forged = pyjwt.decode(token, options={"verify_signature": False})
    bad = pyjwt.encode(forged, "another-secret", algorithm="HS256")
    with pytest.raises(ApiError):
        verify_device_token(settings, bad)


def test_gate_allows_active_device(client, idp):
    settings = get_settings()
    tokens = mint(client, idp, "alice")
    headers = {**bearer_headers(tokens),
               **_device_headers(settings, DEVICE_ALICE, USER_ALICE)}
    resp = client.get("/api/_probe/device", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"device_ok": True, "user_id": USER_ALICE}


def test_gate_blocks_revoked_device_immediately(client, idp, admin_conn):
    """No cache: revocation is effective on the very next request."""
    settings = get_settings()
    tokens = mint(client, idp, "alice")
    headers = {**bearer_headers(tokens),
               **_device_headers(settings, DEVICE_ALICE, USER_ALICE)}
    assert client.get("/api/_probe/device", headers=headers).status_code == 200

    admin_conn.execute(
        "UPDATE devices SET status = 'revoked' WHERE id = %s", (DEVICE_ALICE,)
    )
    resp = client.get("/api/_probe/device", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "device_not_authorized"

    admin_conn.execute(
        "UPDATE devices SET status = 'active' WHERE id = %s", (DEVICE_ALICE,)
    )


def test_gate_blocks_pending_device(client, idp):
    settings = get_settings()
    tokens = mint(client, idp, "carol")
    headers = {**bearer_headers(tokens),
               **_device_headers(settings, DEVICE_CAROL,
                                 "40000000-0000-0000-0000-000000000003")}
    resp = client.get("/api/_probe/device", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "device_not_authorized"


def test_gate_blocks_missing_token(client, idp):
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/_probe/device", headers=bearer_headers(tokens))
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "device_not_authorized"


def test_gate_blocks_device_of_another_user(client, idp):
    """alice presents a device token bound to carol -> rejected."""
    settings = get_settings()
    tokens = mint(client, idp, "alice")
    headers = {**bearer_headers(tokens),
               **_device_headers(settings, DEVICE_CAROL,
                                 "40000000-0000-0000-0000-000000000003")}
    resp = client.get("/api/_probe/device", headers=headers)
    assert resp.status_code == 401

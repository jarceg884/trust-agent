"""AC-CP-001/002/003 — healthz, settings fail-closed, error envelope."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import reset_engine
from app.main import create_app


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "database": "up"}


def test_healthz_db_down(env):
    """PostgreSQL unreachable -> 503 with the standard envelope."""
    saved = os_environ = dict(__import__("os").environ)
    import os

    os.environ["TRUST_DATABASE_URL"] = (
        "postgresql+psycopg://trust_app:trust_app_dev@127.0.0.1:55432/trust_agent_missing_db"
    )
    get_settings.cache_clear()
    reset_engine()
    try:
        with TestClient(create_app()) as c:
            resp = c.get("/healthz")
        assert resp.status_code == 503
        err = resp.json()["error"]
        assert err["reason"] == "service_unavailable"
        assert err["request_id"]
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()
        reset_engine()


def test_settings_fail_closed_without_database_url(env):
    """No DATABASE_URL -> the app refuses to boot (AC-CP-002)."""
    import os

    from pydantic import ValidationError

    saved = dict(os.environ)
    os.environ.pop("TRUST_DATABASE_URL", None)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()


def test_create_app_fails_closed_without_device_token_secret(env):
    """No TRUST_DEVICE_TOKEN_SECRET -> create_app refuses to boot (QA §5.1)."""
    import os

    saved = dict(os.environ)
    os.environ.pop("TRUST_DEVICE_TOKEN_SECRET", None)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="TRUST_DEVICE_TOKEN_SECRET"):
            create_app()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()


def test_create_app_fails_closed_on_placeholder_device_token_secret(env):
    """A leftover 'change-me' placeholder is rejected exactly like a missing
    secret — the old insecure default must never boot (QA §5.1)."""
    import os

    saved = dict(os.environ)
    os.environ["TRUST_DEVICE_TOKEN_SECRET"] = (
        "device-token-dev-secret-change-me"
    )
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="TRUST_DEVICE_TOKEN_SECRET"):
            create_app()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()


def test_error_envelope_unknown_route(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["reason"] == "not_found"
    assert err["request_id"]
    assert resp.headers["X-Request-Id"] == err["request_id"]


def test_error_envelope_unauthenticated(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["reason"] == "invalid_token"
    assert err["request_id"]


def test_error_envelope_validation(client):
    resp = client.post("/api/auth/session", json={"id_token": 123})
    assert resp.status_code == 422
    assert resp.json()["error"]["reason"] == "validation_failed"


def test_request_id_header_on_success(client):
    resp = client.get("/healthz")
    assert resp.headers["X-Request-Id"].startswith("req_")

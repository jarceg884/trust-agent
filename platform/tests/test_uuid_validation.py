"""Contrato de ids UUID malformados → 422 validation_failed, nunca 500
(t_378503a1 — bug hallado por ui durante la evidencia de la spec 021).

Cobertura:
- AC-1  repro del bug: PUT /api/budgets/dept/not-a-uuid → 422 (era 500).
- AC-2  id UUID bien formado pero inexistente → 404 (sin oráculo, D-CP-09).
- AC-3  barrido de TODAS las rutas del control plane con path param UUID
        (budgets, policies, departments, users, devices, agents, vault,
        connectors, model keys, jobs, notifications) + query/body params
        que llegan a `CAST(:x AS uuid)` (filtros de audit, policies list,
        devices list, model access, invite, grants).
- AC-4  red de seguridad: un DataError 22P02 de PostgreSQL que se escape
        de la validación de ruta responde 422, no 500, y NO envenena el
        pool de conexiones (la siguiente request funciona).
- AC-5  canonización: un UUID en mayúsculas o sin guiones se normaliza y
        funciona igual que la forma canónica.
"""
import uuid

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import DEPT_A1, USER_CAROL, bearer_headers, mint

BAD = "not-a-uuid"


def _assert_422(resp):
    """422 con el envelope estándar: invalid_request / validation_failed."""
    assert resp.status_code == 422, (resp.status_code, resp.text)
    err = resp.json()["error"]
    assert err["code"] == "invalid_request"
    assert err["reason"] == "validation_failed"
    assert err["request_id"]


# ============================================================================
# AC-1 — repro del bug reportado (PUT /api/budgets/dept/not-a-uuid)
# ============================================================================
def test_repro_put_dept_budget_malformed_uuid(client, idp):
    """El repro real de la spec 021: id no-UUID → 422 (antes: 500)."""
    alice = mint(client, idp, "alice")
    resp = client.put(f"/api/budgets/dept/{BAD}",
                      json={"amount_cents": 100},
                      headers=bearer_headers(alice))
    _assert_422(resp)
    assert "UUID" in resp.json()["error"]["message"]


# ============================================================================
# AC-2 — UUID bien formado pero inexistente sigue siendo 404
# ============================================================================
def test_nonexistent_uuid_still_404(client, idp):
    """Un UUID válido pero inexistente → 404 (el 422 es SOLO de formato)."""
    alice = mint(client, idp, "alice")
    resp = client.put(f"/api/budgets/dept/{uuid.uuid4()}",
                      json={"amount_cents": 100},
                      headers=bearer_headers(alice))
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["reason"] == "not_found"


# ============================================================================
# AC-3 — barrido de rutas con path param UUID
# ============================================================================
# (method, path, json, usuario)
PATH_CASES = [
    ("PUT", f"/api/budgets/dept/{BAD}", {"amount_cents": 100}, "alice"),
    ("PUT", f"/api/budgets/user/{BAD}", {"amount_cents": 100}, "alice"),
    ("PUT", f"/api/policies/dept/{BAD}",
     {"key": "session_timeout_minutes", "value": 30}, "alice"),
    ("PUT", f"/api/policies/user/{BAD}",
     {"key": "session_timeout_minutes", "value": 30}, "alice"),
    ("PUT", f"/api/departments/{BAD}", {"name": "X"}, "alice"),
    ("DELETE", f"/api/departments/{BAD}", None, "alice"),
    ("PUT", f"/api/users/{BAD}", {"display_name": "X"}, "alice"),
    ("POST", f"/api/users/{BAD}/disable", None, "alice"),
    ("GET", f"/api/devices/{BAD}", None, "alice"),
    ("POST", f"/api/devices/{BAD}/revoke", None, "alice"),
    ("POST", f"/api/devices/{BAD}/token", None, "carol"),
    ("GET", f"/api/vault/credentials/{BAD}", None, "carol"),
    ("POST", f"/api/vault/credentials/{BAD}/rotate", None, "carol"),
    ("POST", f"/api/vault/credentials/{BAD}/revoke", None, "carol"),
    ("POST", f"/api/vault/credentials/{BAD}/use", None, "carol"),
    ("GET", f"/api/connectors/instances/{BAD}", None, "carol"),
    ("DELETE", f"/api/connectors/instances/{BAD}", None, "carol"),
    ("POST", f"/api/models/keys/{BAD}/revoke", None, "carol"),
    ("GET", f"/api/jobs/{BAD}", None, "carol"),
    ("POST", f"/api/me/notifications/{BAD}/read", None, "carol"),
    ("GET", f"/api/agents/{BAD}", None, "carol"),
    ("POST", f"/api/agents/{BAD}/invoke", None, "carol"),
    ("GET", f"/api/agents/{BAD}/grants", None, "alice"),
    ("POST", f"/api/agents/{BAD}/grants",
     {"user_id": USER_CAROL, "permission": "invoke"}, "alice"),
    ("DELETE", f"/api/agents/{BAD}/grants/{BAD}", None, "alice"),
]


@pytest.mark.parametrize("method,path,body,who", PATH_CASES)
def test_path_param_malformed_uuid_is_422(client, idp, method, path, body, who):
    """Cada path param UUID malformado → 422 validation_failed, nunca 500."""
    tokens = mint(client, idp, who)
    resp = client.request(method, path, json=body,
                          headers=bearer_headers(tokens))
    _assert_422(resp)


# ============================================================================
# AC-3 (cont.) — query y body params que llegan a CAST(:x AS uuid)
# ============================================================================
QUERY_BODY_CASES = [
    ("GET", f"/api/policies?level=dept&department_id={BAD}", None, "alice"),
    ("GET", f"/api/policies?level=user&user_id={BAD}", None, "alice"),
    ("DELETE", f"/api/policies/dept/k?department_id={BAD}", None, "alice"),
    ("DELETE", f"/api/policies/user/k?user_id={BAD}", None, "alice"),
    ("GET", f"/api/audit/events?actor_id={BAD}", None, "alice"),
    ("GET", f"/api/audit/events?device_id={BAD}", None, "alice"),
    ("GET", f"/api/audit/events?department_id={BAD}", None, "alice"),
    ("GET", f"/api/audit/events/export?actor_id={BAD}", None, "alice"),
    ("GET", f"/api/devices?department_id={BAD}", None, "alice"),
    ("PUT", "/api/models/access",
     {"model_name": "glm-5.2", "level": "dept",
      "department_id": BAD, "mode": "central"}, "alice"),
    ("PUT", "/api/models/access",
     {"model_name": "glm-5.2", "level": "user",
      "user_id": BAD, "mode": "central"}, "alice"),
    ("POST", "/api/models/access/revoke",
     {"model_name": "glm-5.2", "level": "dept",
      "department_id": BAD, "mode": "central"}, "alice"),
    ("POST", "/api/models/keys", {"user_id": BAD}, "alice"),
    ("POST", "/api/users",
     {"email": "uuid-check@tenant-a.test", "department_id": BAD}, "alice"),
    ("PUT", f"/api/users/{USER_CAROL}", {"department_id": BAD}, "alice"),
]


@pytest.mark.parametrize("method,path,body,who", QUERY_BODY_CASES)
def test_query_body_malformed_uuid_is_422(client, idp, method, path, body, who):
    """Query/body params UUID malformados → 422 validation_failed."""
    tokens = mint(client, idp, who)
    resp = client.request(method, path, json=body,
                          headers=bearer_headers(tokens))
    _assert_422(resp)


def test_grant_body_malformed_user_id_is_422(client, idp):
    """Body de grants con user_id no-UUID sobre un agente EXISTENTE → 422
    (el 404 del agente no puede enmascarar el error de formato)."""
    carol = mint(client, idp, "carol")
    pub = client.post("/api/agents",
                      json={"name": "uuid-check-agent", "scope": "dept"},
                      headers=bearer_headers(carol))
    assert pub.status_code == 201, pub.text
    agent_id = pub.json()["id"]
    resp = client.post(f"/api/agents/{agent_id}/grants",
                       json={"user_id": BAD, "permission": "invoke"},
                       headers=bearer_headers(carol))
    _assert_422(resp)


# ============================================================================
# AC-4 — red de seguridad: DataError 22P02 → 422 sin envenenar el pool
# ============================================================================
@pytest.fixture(scope="module")
def raw_client(client):
    """App real + sonda que ejecuta un CAST crudo a uuid SIN validación de
    ruta, para ejercitar la red de seguridad del exception handler."""
    from app.db import get_engine
    from app.main import create_app

    app = create_app()
    probe = APIRouter(prefix="/api/_probe")

    @probe.get("/raw-uuid-cast")
    def raw_uuid_cast(x: str):
        with get_engine().connect() as conn:
            conn.execute(text("SELECT CAST(:x AS uuid)"), {"x": x})
        return {"ok": True}

    app.include_router(probe)
    with TestClient(app) as c:
        yield c


def test_data_error_safety_net(raw_client):
    """Un 22P02 que escape a la validación → 422 (no 500) y el pool queda
    usable: la request siguiente con input válido funciona."""
    resp = raw_client.get(f"/api/_probe/raw-uuid-cast?x={BAD}")
    _assert_422(resp)
    # El pool NO queda envenenado por la transacción abortada.
    ok = raw_client.get(
        "/api/_probe/raw-uuid-cast?x=30000000-0000-0000-0000-000000000001")
    assert ok.status_code == 200, ok.text


# ============================================================================
# AC-5 — canonización: mayúsculas y sin guiones se normalizan
# ============================================================================
def test_uuid_canonicalization(client, idp):
    """Un UUID en mayúsculas o sin guiones es el MISMO recurso (PostgreSQL
    los acepta; require_uuid los normaliza antes de comparar scopes)."""
    alice = mint(client, idp, "alice")
    # Autocontenido: refresca el budget de org primero para que el check
    # Σ dept ≤ org no falle por estado dejado por suites anteriores.
    resp = client.put("/api/budgets/org",
                      json={"amount_cents": 2_000_000},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    undashed = DEPT_A1.replace("-", "")
    for variant in (DEPT_A1.upper(), undashed):
        resp = client.put(f"/api/budgets/dept/{variant}",
                          json={"amount_cents": 400000},
                          headers=bearer_headers(alice))
        assert resp.status_code == 200, (variant, resp.text)
        assert resp.json()["department_id"] == DEPT_A1
    # Limpieza: devolver el budget de org al valor del seed (1M) para no
    # afectar a suites posteriores que calculan contra lo no asignado.
    resp = client.put("/api/budgets/org",
                      json={"amount_cents": 1_000_000, "force": True},
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text

"""QA e2e — flujo completo REAL del control plane (specs 003→008).

Un solo tenant recién aprovisionado atraviesa TODOS los dominios:

  alta de tenant (Keycloak 26 real + PostgreSQL 17 real)
  → login real PKCE S256 del admin (JWKS real) + puerta MFA
  → departamentos seed + usuario obrero fedado desde el IdP real
  → políticas (catálogo, no-relajación)
  → presupuestos jerárquicos (validación de suma)
  → uso de modelo (key virtual → data plane → ingest → corte → recuperación)
  → enrolamiento de dispositivo (primera clase, one-time, revocación)
  → conector (vault one-time + gobernanza deny-overrides)
  → audit verificado (API + CLI, cadena íntegra).

DOBLES DECLARADOS (constitution I — nada simulado sin declararlo):
- LiteLLM: doble HTTP `tests/litellm_fake.py` (D-GOV-15), con la semántica
  documentada de la Admin API. Contra un proxy real solo cambia
  TRUST_LITELLM_BASE_URL.
- IdP para pasos con MFA: el mock JWKS de `tests/conftest.py` (RSA real,
  verificación PyJWKClient real). El realm Keycloak del tenant ES real y su
  login PKCE se ejercita ANTES; se sustituye `tenants.oidc_issuer_url` al
  doble porque Keycloak 26 NO permite enrolar TOTP vía Admin REST (probe
  real: POST /users/{id}/credentials → 404) y el scraping de HTML está
  prohibido por diseño (hallazgo C5 de la auditoría AIDLC).
- MFA vía `acr` (D-MFA-01): KC26 nunca emite `amr`. Spec 015 (D-AUTH-06): el
  aprovisionamiento cablea cada realm con `ensure_mfa_acr` (acr.loa.map
  {"1":1,"2":2} + subflows condicionales Password LoA@1 / Conditional OTP@2),
  así que el OTP es CONDICIONAL al `acr_values` pedido: acr 1 → password-only
  → code con acr "1"; acr 2 sin TOTP → el OTP Form deriva inline a
  CONFIGURE_TOTP sin code (assert de los pasos 2 y 3). El camino real
  password+TOTP → acr "2" → sesión mfa_verified se verifica en vivo con el
  probe del skill `identity/keycloak26-amr-mfa`. La puerta MFA del control
  plane es policy-aware (D-AUTH-07): require_mfa=false deja pasar sesiones
  sin MFA (banner persistente en la UI); true → 403 mfa_required (spec 011).
"""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import subprocess
import urllib.parse

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import PLATFORM_DIR, bearer_headers

KC_HOST, KC_PORT = "127.0.0.1", 8080
CLIENT_ID = "trust-agent-web"
# Callback del helper trust-login del bundle de descarga (spec 023 D-023-13):
# el flujo real de desktop. Retirado el demo :9090 (t_b8769ecb).
REDIRECT_URI = "http://127.0.0.1:8765/callback"
PROVIDER = {"Authorization": "Bearer test-provider-token"}
SLUG = "e2e-corp"
ADMIN_EMAIL = "admin@e2e-corp.test"
WORKER_EMAIL = "worker@e2e-corp.test"
WORKER_PASSWORD = "E2eWorkerPass2026!"
QWEN = "glm-5.2"

_S = {}  # estado compartido entre los pasos ordenados del flujo


def _kc_available() -> bool:
    try:
        conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=3)
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _kc_available(), reason="Keycloak local (127.0.0.1:8080) not reachable"
)


@pytest.fixture(scope="module", autouse=True)
def _realm_cleanup():
    yield
    from app.config import get_settings
    from app.tenants.keycloak_admin import KeycloakAdminClient

    kc = KeycloakAdminClient(get_settings())
    try:
        kc.delete_realm(SLUG)
    except Exception:  # noqa: BLE001 — limpieza best-effort
        pass


# ---------------------------------------------------------------------------
# Helpers Keycloak (Admin REST + baile PKCE real, sin scraping)
# ---------------------------------------------------------------------------
def _request(method, path, data=None, headers=None):
    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
    body = urllib.parse.urlencode(data) if data is not None else None
    conn.request(method, path, body=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    resp = conn.getresponse()
    return resp.status, resp.read().decode(), resp.getheaders()


def _json_request(method, path, payload=None, token=None):
    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    return resp.status, resp.read().decode()


def _admin_token() -> str:
    # Bootstrap interno admin-cli (ADR-015): jamás ofrecido a usuarios.
    status, raw, _ = _request(
        "POST", "/realms/master/protocol/openid-connect/token",
        {"grant_type": "password", "client_id": "admin-cli",
         "username": "admin", "password": "KeycloakDemo2026"},
    )
    assert status == 200, raw
    return json.loads(raw)["access_token"]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pkce_login(username: str, password: str, acr_values: str | None = None):
    """Baile authorization-code + PKCE S256 completo.

    -> (id_token | None, location_final) — sin code si KC exige required
    action (p. ej. password temporal o TOTP pendiente).

    Spec 015 (D-AUTH-06): `acr_values` es EXPLÍCITO — el flujo LoA nuevo ya
    no fuerza el OTP en un authorize bare (probe S7): acr 1 = password-only,
    acr 2 = OTP (enrolamiento inline CONFIGURE_TOTP sin credencial TOTP).
    """
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {
        "response_type": "code", "client_id": CLIENT_ID, "scope": "openid",
        "redirect_uri": REDIRECT_URI, "state": "st-e2e",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if acr_values:
        params["acr_values"] = acr_values
    status, body, headers = _request(
        "GET",
        f"/realms/{SLUG}/protocol/openid-connect/auth?{urllib.parse.urlencode(params)}",
    )
    assert status == 200, f"authorize HTTP {status}"
    action = re.search(r'action="([^"]+)"', body).group(1).replace("&amp;", "&")
    cookies = "; ".join(
        v.split(";")[0] for k, v in headers if k.lower() == "set-cookie")

    conn = http.client.HTTPConnection(KC_HOST, KC_PORT, timeout=15)
    path = action.split(f"{KC_HOST}:{KC_PORT}", 1)[-1]
    conn.request("POST", path,
                 body=urllib.parse.urlencode(
                     {"username": username, "password": password,
                      "credentialId": ""}),
                 headers={"Content-Type": "application/x-www-form-urlencoded",
                          "Cookie": cookies})
    resp = conn.getresponse()
    location = resp.getheader("Location") or ""
    if "code=" not in location:
        return None, location  # required action u otro bloqueo: SIN code
    code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
    status, raw, _ = _request(
        "POST", f"/realms/{SLUG}/protocol/openid-connect/token",
        {"grant_type": "authorization_code", "client_id": CLIENT_ID,
         "redirect_uri": REDIRECT_URI, "code": code,
         "code_verifier": verifier},
    )
    assert status == 200, raw
    return json.loads(raw)["id_token"], location


def _kc_user_uuid(username: str) -> str:
    token = _admin_token()
    status, raw = _json_request(
        "GET", f"/admin/realms/{SLUG}/users?username={username}&exact=true",
        token=token)
    assert status == 200, raw
    return json.loads(raw)[0]["id"]


def _mint_e2e(client, idp, sub: str, *, kind="desktop", amr=("mfa",)):
    """Sesión vía el doble JWKS declarado (ver docstring del módulo)."""
    token = idp.make_jwt(sub=sub, tenant_id=_S["tenant_id"],
                         issuer=idp.issuer(SLUG),
                         amr=list(amr) if amr is not None else None)
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": kind})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _q(admin_conn, sql, params=None):
    with admin_conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _worker_run(litellm_fake):
    from app.db import get_engine
    from app.governance.spend_worker import run_once
    from app.jobs.service import build_migrator_engine
    from app.models.litellm import LiteLLMClient
    from tests.conftest import MIGRATOR_DB_URL

    return run_once(
        get_engine(),
        build_migrator_engine(MIGRATOR_DB_URL),
        LiteLLMClient(litellm_fake.base_url, "sk-master-test"),
    )


# ---------------------------------------------------------------------------
# Paso 1 — alta de tenant (spec 004)
# ---------------------------------------------------------------------------
def test_01_provision_tenant(client, admin_conn):
    resp = client.post(
        "/api/admin/tenants", headers=PROVIDER,
        json={"organization_name": "E2E Corp", "admin_email": ADMIN_EMAIL,
              "initial_budget_usd": 500.0, "model_plan": "standard",
              # Spec 038 (D-38-05): declared structure (backward-compatible
              # template keys) — the seed of 4 no longer exists.
              "departments": ["it_operations", "finance"],
              "slug": SLUG},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    tenant = body["tenant"]
    assert tenant["slug"] == SLUG and tenant["status"] == "active"
    assert tenant["oidc_issuer_url"] == f"http://{KC_HOST}:{KC_PORT}/realms/{SLUG}"
    assert body["healthcheck"]["status"] == "ok", body["healthcheck"]
    ota = body["one_time_activation"]
    assert ota["one_time_password"] and len(ota["one_time_password"]) >= 16

    tid = tenant["id"]
    _S.update(tenant_id=tid, ota=ota)

    rows = _q(admin_conn,
              "SELECT count(*) AS n FROM departments WHERE tenant_id = %s",
              (tid,))
    assert rows[0]["n"] == 2  # declared: it_operations + finance (spec 038)
    rows = _q(admin_conn,
              "SELECT id, profile_template FROM departments "
              "WHERE tenant_id = %s AND profile_template = 'it_operations'",
              (tid,))
    assert rows, "falta el departamento IT"
    _S["dept_it"] = str(rows[0]["id"])

    rows = _q(admin_conn,
              "SELECT u.id, u.oidc_subject FROM users u "
              "JOIN user_roles r ON r.user_id = u.id "
              "WHERE u.tenant_id = %s AND r.role_key = 'org_admin'", (tid,))
    assert rows, "falta el org_admin"
    _S["admin_user_id"] = str(rows[0]["id"])
    _S["admin_sub"] = str(rows[0]["oidc_subject"])

    rows = _q(admin_conn,
              "SELECT amount_cents FROM budgets "
              "WHERE tenant_id = %s AND level = 'org'", (tid,))
    assert rows[0]["amount_cents"] == 50000  # USD centavos

    # El realm existe de verdad. El admin nace con password one-time
    # (UPDATE_PASSWORD) y SIN CONFIGURE_TOTP — spec 015 D-AUTH-06: el MFA ya
    # no se fuerza en el aprovisionamiento (opcional + banner persistente;
    # el enrolamiento ocurre inline vía el OTP form cuando se pide acr 2).
    from app.config import get_settings
    from app.tenants.keycloak_admin import KeycloakAdminClient
    kc = KeycloakAdminClient(get_settings())
    assert kc.realm_exists(SLUG)
    user = kc.find_user(SLUG, ADMIN_EMAIL)
    assert user is not None
    assert "UPDATE_PASSWORD" in user["requiredActions"], user["requiredActions"]
    assert "CONFIGURE_TOTP" not in user["requiredActions"], user["requiredActions"]

    # Cadena de audit del tenant recién nacido: íntegra
    with admin_conn.cursor() as cur:
        cur.execute("SELECT audit_verify_chain(%s)", (tid,))
        assert cur.fetchone()[0][0] == "ok"


# ---------------------------------------------------------------------------
# Paso 2 — login real PKCE + puerta MFA + sustitución declarada del doble
# ---------------------------------------------------------------------------
def test_02_real_pkce_login_and_mfa_gate(client, admin_conn, idp):
    # (a) El password one-time NO entrega code: KC exige required actions
    id_token, location = _pkce_login(ADMIN_EMAIL, _S["ota"]["one_time_password"])
    assert id_token is None, "el password one-time NO debe obtener un code"
    assert "code=" not in location

    # (b) Completar las required actions vía Admin REST (sin scraping)
    token = _admin_token()
    user_uuid = _kc_user_uuid(ADMIN_EMAIL)
    status, raw = _json_request(
        "PUT", f"/admin/realms/{SLUG}/users/{user_uuid}/reset-password",
        {"type": "password", "value": "E2eAdminPass2026!", "temporary": False},
        token=token)
    assert status in (204, 409), raw
    status, raw = _json_request(
        "GET", f"/admin/realms/{SLUG}/users/{user_uuid}", token=token)
    user = json.loads(raw)
    user["requiredActions"] = []
    status, raw = _json_request(
        "PUT", f"/admin/realms/{SLUG}/users/{user_uuid}", user, token=token)
    assert status in (204, 409), raw

    # (c) Spec 015 D-AUTH-06 (probe S1/S2): el flujo LoA nuevo hace el OTP
    # CONDICIONAL. acr_values=1 → password-only → code con acr "1" (SIN
    # formulario OTP); acr_values=2 → el OTP Form corre y, sin credencial
    # TOTP, Keycloak deriva inline a CONFIGURE_TOTP — NO hay code
    # (fail-closed en el IdP cuando se pide el nivel 2). La verificación de
    # password+TOTP real → acr "2" → sesión mfa_verified está cubierta por
    # el probe en vivo del skill identity/keycloak26-amr-mfa.
    id_token, location = _pkce_login(
        ADMIN_EMAIL, "E2eAdminPass2026!", acr_values="1")
    assert id_token is not None, "acr 1 password-only DEBE obtener un code"
    payload = json.loads(base64.urlsafe_b64decode(
        id_token.split(".")[1] + "=="))
    assert payload.get("acr") == "1", payload.get("acr")
    id_token, location = _pkce_login(
        ADMIN_EMAIL, "E2eAdminPass2026!", acr_values="2")
    assert id_token is None, "sin TOTP el login acr 2 NO debe obtener un code"
    assert "execution=CONFIGURE_TOTP" in location, location

    # (d) DOBLE DECLARADO: KC26 no permite enrolar TOTP vía Admin REST
    # (probe: POST credentials -> 404) y el scraping está prohibido (C5).
    # Sustituimos el issuer al doble JWKS para los pasos MFA del flujo.
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE tenants SET oidc_issuer_url = %s WHERE id = %s",
            (idp.issuer(SLUG), _S["tenant_id"]),
        )
    admin_conn.commit()

    # (e) Puerta MFA POLICY-AWARE (spec 015 D-AUTH-07): el tenant e2e nace
    # con require_mfa=false (default del catálogo tras la migración 0014) →
    # la sesión sin MFA PASA (200; la app sugiere enrolarla con el banner).
    # Al activar la policy org require_mfa=true, el MISMO token sin MFA
    # recibe 403 mfa_required (comportamiento spec 011 intacto).
    no_mfa = _mint_e2e(client, idp, _S["admin_sub"], amr=None)
    assert no_mfa["mfa_verified"] is False
    resp = client.get("/api/me",
                      headers=bearer_headers(no_mfa))
    assert resp.status_code == 200, resp.text
    with admin_conn.cursor() as cur:
        # Un tenant nuevo no tiene fila require_mfa (el provisioning solo
        # siembra claves invariables; el default del catálogo aplica).
        # La activamos como lo haría el org_admin vía API (fila org explícita).
        cur.execute(
            "DELETE FROM policies WHERE tenant_id = %s AND key = 'require_mfa'",
            (_S["tenant_id"],),
        )
        cur.execute(
            "INSERT INTO policies (tenant_id, level, key, value) "
            "VALUES (%s, 'org', 'require_mfa', 'true'::jsonb)",
            (_S["tenant_id"],),
        )
    admin_conn.commit()
    resp = client.get("/api/me",
                      headers=bearer_headers(no_mfa))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"

    # (f) Token con amr -> sesión MFA -> negocio OK
    _S["admin"] = _mint_e2e(client, idp, _S["admin_sub"])
    resp = client.get("/api/budgets/overview",
                      headers=bearer_headers(_S["admin"]))
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Paso 3 — usuario obrero fedado desde el IdP real
# ---------------------------------------------------------------------------
def test_03_worker_user_from_idp(client, admin_conn, idp):
    # Usuario REAL en el realm del tenant (Admin REST, mapper tenant_id)
    token = _admin_token()
    status, raw = _json_request(
        "POST", f"/admin/realms/{SLUG}/users",
        {"username": WORKER_EMAIL, "enabled": True, "email": WORKER_EMAIL,
         "firstName": "E2E", "lastName": "Worker",
         "attributes": {"tenant_id": [_S["tenant_id"]]}},
        token=token)
    assert status in (201, 409), raw
    worker_uuid = _kc_user_uuid(WORKER_EMAIL)
    status, raw = _json_request(
        "PUT", f"/admin/realms/{SLUG}/users/{worker_uuid}/reset-password",
        {"type": "password", "value": WORKER_PASSWORD, "temporary": False},
        token=token)
    assert status in (204, 409), raw

    # Fila de directorio (sincronización IdP→DB, patrón del fixture
    # e2e_user_row de spec 003) + rol user en el depto IT
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (tenant_id, department_id, email, display_name, "
            "status, mfa_enrolled, oidc_subject) VALUES (%s, %s, %s, %s, "
            "'active', true, %s)",
            (_S["tenant_id"], _S["dept_it"], WORKER_EMAIL, "E2E Worker",
             worker_uuid),
        )
        cur.execute(
            "INSERT INTO user_roles (tenant_id, user_id, role_key, "
            "department_id) SELECT %s, id, 'user', department_id "
            "FROM users WHERE oidc_subject = %s",
            (_S["tenant_id"], worker_uuid),
        )
    admin_conn.commit()
    rows = _q(admin_conn, "SELECT id FROM users WHERE oidc_subject = %s",
              (worker_uuid,))
    _S["worker_user_id"] = str(rows[0]["id"])
    _S["worker_sub"] = worker_uuid

    # El obrero también se loguea REAL por PKCE… pidiendo acr 2 sin TOTP el
    # IdP lo manda a enrolar (D-MFA-01/D-AUTH-06): el OTP Form sin
    # credencial TOTP deriva inline a CONFIGURE_TOTP, sin code (fail-closed
    # en el IdP cuando se pide el nivel 2, ya no solo en la puerta).
    # (El issuer del tenant apunta al doble declarado desde el paso 2: lo
    # restauramos al realm real para este login, y lo volvemos a sustituir.)
    kc_issuer = f"http://{KC_HOST}:{KC_PORT}/realms/{SLUG}"
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE tenants SET oidc_issuer_url = %s "
                    "WHERE id = %s", (kc_issuer, _S["tenant_id"]))
    admin_conn.commit()
    id_token, location = _pkce_login(
        WORKER_EMAIL, WORKER_PASSWORD, acr_values="2")
    assert id_token is None, "sin TOTP el login del obrero NO debe obtener un code"
    assert "execution=CONFIGURE_TOTP" in location, location
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE tenants SET oidc_issuer_url = %s "
                    "WHERE id = %s", (idp.issuer(SLUG), _S["tenant_id"]))
    admin_conn.commit()

    # Sesión MFA del obrero vía el doble declarado
    _S["worker"] = _mint_e2e(client, idp, _S["worker_sub"])


# ---------------------------------------------------------------------------
# Paso 4 — políticas (catálogo tipado + no-relajación)
# ---------------------------------------------------------------------------
def test_04_policies(client):
    admin = bearer_headers(_S["admin"])
    # Antes de tocar nada: allowed_connectors por defecto es [] (nada)
    resp = client.get("/api/connectors/available",
                      headers=bearer_headers(_S["worker"]))
    assert resp.status_code == 200
    assert resp.json()["connectors"] == []

    # Política org: permite vmware + email
    resp = client.put("/api/policies/org",
                      json={"key": "allowed_connectors",
                            "value": ["vmware", "email"]},
                      headers=admin)
    assert resp.status_code == 200, resp.text

    # Política depto IT: estrecha a vmware
    resp = client.put(f"/api/policies/dept/{_S['dept_it']}",
                      json={"key": "allowed_connectors", "value": ["vmware"]},
                      headers=admin)
    assert resp.status_code == 200, resp.text

    # No-relajación: el depto NO puede ampliar por encima del nivel superior
    resp = client.put(f"/api/policies/dept/{_S['dept_it']}",
                      json={"key": "allowed_connectors",
                            "value": ["vmware", "email", "telegram"]},
                      headers=admin)
    assert resp.status_code == 422, resp.text

    # Clave invariable del catálogo → 422 (spec 015 D-AUTH-07: require_mfa
    # YA NO es invariable — la migración 0014 la hizo org-tunable).
    resp = client.put("/api/policies/org",
                      json={"key": "scoped_credentials", "value": False},
                      headers=admin)
    assert resp.status_code == 422
    # require_mfa es org-tunable: el org_admin puede apagarla vía API
    # (200 + audit policy.updated — el gate policy-aware reacciona en vivo).
    resp = client.put("/api/policies/org",
                      json={"key": "require_mfa", "value": False},
                      headers=admin)
    assert resp.status_code == 200, resp.text

    # Efecto visible para el obrero (deny-overrides): solo vmware
    resp = client.get("/api/connectors/available",
                      headers=bearer_headers(_S["worker"]))
    assert resp.status_code == 200
    assert [c["slug"] for c in resp.json()["connectors"]] == ["vmware"]


# ---------------------------------------------------------------------------
# Paso 5 — presupuestos jerárquicos con validación de suma
# ---------------------------------------------------------------------------
def test_05_budgets(client):
    admin = bearer_headers(_S["admin"])

    resp = client.put(f"/api/budgets/dept/{_S['dept_it']}",
                      json={"amount_cents": 20000}, headers=admin)
    assert resp.status_code == 200, resp.text
    resp = client.put(f"/api/budgets/user/{_S['worker_user_id']}",
                      json={"amount_cents": 5000}, headers=admin)
    assert resp.status_code == 200, resp.text

    # Σ usuarios ≤ depto
    resp = client.put(f"/api/budgets/user/{_S['worker_user_id']}",
                      json={"amount_cents": 25000}, headers=admin)
    assert resp.status_code == 422, resp.text
    # Σ deptos ≤ org (org = 50000)
    resp = client.put(f"/api/budgets/dept/{_S['dept_it']}",
                      json={"amount_cents": 60000}, headers=admin)
    assert resp.status_code == 422, resp.text

    resp = client.get("/api/budgets/overview", headers=admin)
    assert resp.status_code == 200
    overview = resp.json()
    assert overview["organization"]["budget_cents"] == 50000
    it = next(d for d in overview["departments"]
              if d["department_id"] == _S["dept_it"])
    assert it["budget_cents"] == 20000

    # El obrero ve su propio estado
    resp = client.get("/api/budgets/me",
                      headers=bearer_headers(_S["worker"]))
    assert resp.status_code == 200
    me = resp.json()
    assert me["user"]["budget_cents"] == 5000
    assert me["cutoff"] is False


# ---------------------------------------------------------------------------
# Paso 6 — uso de modelo: key virtual → data plane (doble declarado)
# ---------------------------------------------------------------------------
def test_06_model_usage(client, admin_conn, litellm_fake):
    admin = bearer_headers(_S["admin"])
    worker = bearer_headers(_S["worker"])

    resp = client.get("/api/models/allowed-for-me", headers=worker)
    assert resp.status_code == 200
    assert QWEN in resp.json()["models"], resp.text

    resp = client.post("/api/models/verify",
                       json={"model_name": QWEN}, headers=worker)
    assert resp.status_code == 200 and resp.json()["allowed"] is True

    # Emisión de key virtual: plaintext EXACTAMENTE una vez (C1/C4)
    resp = client.post("/api/models/keys",
                       json={"user_id": _S["worker_user_id"]}, headers=admin)
    assert resp.status_code == 201, resp.text
    key_body = resp.json()
    assert key_body["key"].startswith("sk-fake-")
    assert key_body["max_budget_usd"] == 50.0  # presupuesto user $50
    _S["key"] = key_body["key"]
    _S["key_id"] = key_body["key_id"]

    # El plaintext NO vive en la DB (solo el hash sha256)
    rows = _q(admin_conn,
              "SELECT litellm_key_hash FROM api_keys WHERE id = %s",
              (_S["key_id"],))
    assert rows[0]["litellm_key_hash"] == hashlib.sha256(
        _S["key"].encode()).hexdigest()

    # Metadata de keys: jamás el material
    resp = client.get("/api/models/keys/me", headers=worker)
    assert resp.status_code == 200
    assert resp.json()["keys"], "el obrero debe ver su key (metadata)"
    assert _S["key"] not in resp.text, "el plaintext no debe reaparecer"

    # Data plane (doble HTTP declarado, semántica documentada)
    status, _ = litellm_fake.chat(_S["key"], QWEN)
    assert status == 200
    status, _ = litellm_fake.chat(_S["key"], QWEN)
    assert status == 200


# ---------------------------------------------------------------------------
# Paso 7 — ingest de spend + corte + notificación + recuperación
# ---------------------------------------------------------------------------
def test_07_spend_cutoff_and_recovery(client, admin_conn, litellm_fake):
    admin = bearer_headers(_S["admin"])
    worker = bearer_headers(_S["worker"])

    summary = _worker_run(litellm_fake)
    ingest = summary["tenants"][_S["tenant_id"]]["ingest"]
    assert ingest["ingested"] >= 2  # las 2 invocaciones del paso 6

    rows = _q(admin_conn,
              "SELECT spent_cents FROM budget_counters "
              "WHERE level = 'user' AND entity_id = %s "
              "AND period_start = date_trunc('month', now())::date",
              (_S["worker_user_id"],))
    spent = rows[0]["spent_cents"]
    # Pricing REAL del catálogo MaaS (spec 018): el costo por llamada es
    # dinámico (litellm_fake.per_call_cents) — 2 invocaciones central.
    per_call = litellm_fake.per_call_cents(QWEN)
    assert spent >= 2 * per_call  # BYOK? no: central

    # Corte: presupuesto por debajo del gasto (force auditable).
    # D-GOV-17: spent llega como Decimal (milli-centavo); el API exige
    # amount_cents entero → coerción (el gasto del doble es entero).
    resp = client.put(f"/api/budgets/user/{_S['worker_user_id']}",
                      json={"amount_cents": max(1, int(spent) - int(per_call)),
                            "force": True},
                      headers=admin)
    assert resp.status_code == 200, resp.text

    blocked_before = len(litellm_fake.block_calls)
    _worker_run(litellm_fake)
    assert len(litellm_fake.block_calls) > blocked_before

    rows = _q(admin_conn,
              "SELECT status FROM api_keys WHERE id = %s", (_S["key_id"],))
    assert rows[0]["status"] == "blocked"

    # El gateway rechaza la key bloqueada (401, semántica documentada)
    status, body = litellm_fake.chat(_S["key"], QWEN)
    assert status == 401

    # Notificación al obrero + estado propio en corte
    resp = client.get("/api/me/notifications", headers=worker)
    assert resp.status_code == 200
    assert any(n["type"] == "budget_cutoff"
               for n in resp.json()["notifications"])
    resp = client.get("/api/budgets/me", headers=worker)
    assert resp.json()["cutoff"] is True
    assert resp.json()["cutoff_level"] == "user"

    rows = _q(admin_conn,
              "SELECT count(*) AS n FROM audit_log "
              "WHERE tenant_id = %s AND action = 'budget.cutoff'",
              (_S["tenant_id"],))
    assert rows[0]["n"] >= 1

    # Recuperación: subir el presupuesto desbloquea la key
    # (Σ usuarios ≤ depto: 15000 ≤ 20000 — force NO salta la validación de suma)
    resp = client.put(f"/api/budgets/user/{_S['worker_user_id']}",
                      json={"amount_cents": 15000, "force": True},
                      headers=admin)
    assert resp.status_code == 200
    _worker_run(litellm_fake)
    rows = _q(admin_conn,
              "SELECT status FROM api_keys WHERE id = %s", (_S["key_id"],))
    assert rows[0]["status"] == "active"
    status, _ = litellm_fake.chat(_S["key"], QWEN)
    assert status == 200


# ---------------------------------------------------------------------------
# Paso 8 — enrolamiento de dispositivo (primera clase, one-time, revocación)
# ---------------------------------------------------------------------------
def test_08_device_enrollment(client, admin_conn, idp):
    admin = bearer_headers(_S["admin"])
    worker = bearer_headers(_S["worker"])

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    fp = "fp-e2e-worker-laptop"

    id_token = idp.make_jwt(sub=_S["worker_sub"],
                            tenant_id=_S["tenant_id"],
                            issuer=idp.issuer(SLUG), amr=["mfa"])
    resp = client.post("/api/devices/enroll", json={
        "org_code": SLUG, "id_token": id_token,
        "public_key": public_key, "fingerprint": fp,
        "device_name": "e2e-laptop", "platform": "linux",
    })
    assert resp.status_code == 202, resp.text
    enrolled = resp.json()
    assert enrolled["status"] == "pending"
    assert enrolled["enrollment_token"].startswith("enr1.")
    _S["device_id"] = enrolled["device_id"]
    _S["enrollment_token"] = enrolled["enrollment_token"]

    # Polling: pending -> 202 repetible sin consumir
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token": _S["enrollment_token"]})
    assert resp.status_code == 202
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token": _S["enrollment_token"]})
    assert resp.status_code == 202

    # Aprobación admin
    resp = client.post(f"/api/devices/{_S['device_id']}/approve",
                       headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    # Canje one-time -> device token
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token": _S["enrollment_token"]})
    assert resp.status_code == 200, resp.text
    exchanged = resp.json()
    assert exchanged["device_token"] and exchanged["expires_in"] > 0

    # Segundo canje: 410 uniforme (sin oráculo)
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token": _S["enrollment_token"]})
    assert resp.status_code == 410

    # El obrero ve su dispositivo
    resp = client.get("/api/devices/me", headers=worker)
    assert resp.status_code == 200
    mine = [d for d in resp.json()["devices"]
            if d["id"] == _S["device_id"]]
    assert mine and mine[0]["status"] == "active"

    # Ligadura de sesión al dispositivo y revocación con corte inmediato
    session_id = _S["worker"]["session_token"].split(".")[2]
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE sessions SET device_id = %s WHERE id = %s",
                    (_S["device_id"], session_id))
    admin_conn.commit()

    resp = client.post(f"/api/devices/{_S['device_id']}/revoke",
                       headers=admin)
    assert resp.status_code == 200
    resp = client.get("/api/me", headers=worker)
    assert resp.status_code == 401, "la sesión ligada murió con el dispositivo"

    # Audit del ciclo completo con device_id
    for action in ("device.enrolled", "device.approved", "device.revoked"):
        rows = _q(admin_conn,
                  "SELECT device_id FROM audit_log "
                  "WHERE tenant_id = %s AND action = %s AND device_id = %s",
                  (_S["tenant_id"], action, _S["device_id"]))
        assert rows, f"falta audit {action}"

    # F4 (QA t_877b0cb5): rechazar un segundo enrolamiento pending emite
    # device.rejected — NUNCA device.revoked (trail distinguible).
    key2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key2 = key2.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    id_token2 = idp.make_jwt(sub=_S["worker_sub"],
                             tenant_id=_S["tenant_id"],
                             issuer=idp.issuer(SLUG), amr=["mfa"])
    resp = client.post("/api/devices/enroll", json={
        "org_code": SLUG, "id_token": id_token2,
        "public_key": public_key2, "fingerprint": "fp-e2e-worker-laptop-2",
        "device_name": "e2e-laptop-2", "platform": "linux",
    })
    assert resp.status_code == 202, resp.text
    rejected_id = resp.json()["device_id"]

    resp = client.post(f"/api/devices/{rejected_id}/reject", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    rows = _q(admin_conn,
              "SELECT action FROM audit_log "
              "WHERE tenant_id = %s AND device_id = %s ORDER BY seq",
              (_S["tenant_id"], rejected_id))
    actions = [r["action"] for r in rows]
    assert actions == ["device.enrolled", "device.rejected"], actions
    _S["rejected_device_id"] = rejected_id


# ---------------------------------------------------------------------------
# Paso 9 — conector: vault one-time + gobernanza deny-overrides
# ---------------------------------------------------------------------------
def test_09_connector_and_vault(client, admin_conn, idp):
    admin = bearer_headers(_S["admin"])
    # La sesión del obrero murió con la revocación del dispositivo (paso 8):
    # re-mint vía el doble declarado para el paso de gobernanza.
    _S["worker"] = _mint_e2e(client, idp, _S["worker_sub"])
    worker = bearer_headers(_S["worker"])
    canary = "e2e-canary-" + secrets.token_hex(12)

    # Registro de credencial: valor en claro EXACTAMENTE una vez (C1/C4)
    resp = client.post("/api/vault/credentials", json={
        "name": "vcenter-token", "credential_type": "api_key",
        "secret_value": canary, "connector_slug": "vmware",
        "scope": "read_only",
    }, headers=admin)
    assert resp.status_code == 201, resp.text
    cred = resp.json()
    assert cred["secret_value"] == canary
    _S["credential_id"] = cred["credential_id"]

    # GET posterior: solo metadatos, jamás el secreto
    resp = client.get(f"/api/vault/credentials/{_S['credential_id']}",
                      headers=admin)
    assert resp.status_code == 200
    assert "secret_value" not in resp.json()

    # El canario NO vive en claro en NINGUNA tabla (C4)
    with admin_conn.cursor() as cur:
        for table, col in [("vault_credentials", "name || ' ' || scope"),
                           ("audit_log", "payload::text"),
                           ("connector_instances", "config::text")]:
            cur.execute(
                f"SELECT count(*) FROM {table} "
                f"WHERE tenant_id = %s AND {col} LIKE %s",
                (_S["tenant_id"], f"%{canary}%"),
            )
            assert cur.fetchone()[0] == 0, f"canario en claro en {table}"

    # Instancia de conector permitida por política (org+depto → vmware)
    resp = client.post("/api/connectors/instances", json={
        "connector_slug": "vmware", "credential_id": _S["credential_id"],
        "config": {"host": "vcenter.e2e.test"},
    }, headers=admin)
    assert resp.status_code == 201, resp.text
    _S["instance_id"] = resp.json()["id"]

    # Gobernanza: telegram NO está en allowed_connectors → 403
    resp = client.post("/api/connectors/instances", json={
        "connector_slug": "telegram",
        "config": {"chat_id": "1"},
    }, headers=worker)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["reason"] == "connector_not_allowed"

    # Canal de inyección runtime: el propietario usa su credencial (audit)
    resp = client.post(
        f"/api/vault/credentials/{_S['credential_id']}/use", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["secret_value"] == canary

    rows = _q(admin_conn,
              "SELECT count(*) AS n FROM audit_log "
              "WHERE tenant_id = %s AND action = 'vault.credential_used'",
              (_S["tenant_id"],))
    assert rows[0]["n"] >= 1


# ---------------------------------------------------------------------------
# Paso 10 — audit verificado: trail completo + cadena íntegra (API y CLI)
# ---------------------------------------------------------------------------
def test_10_audit_verified(client, admin_conn):
    admin = bearer_headers(_S["admin"])

    expected_actions = {
        "tenant.provisioned", "user.created", "role.assigned",
        "policy.updated", "budget.updated", "model.approved",
        "model.key_issued", "budget.cutoff", "device.enrolled",
        "device.approved", "device.rejected", "device.revoked",
        "vault.credential_created",
        "vault.credential_used", "connector.connected",
    }
    found = set()
    offset = 0
    while True:
        resp = client.get(f"/api/audit/events?limit=200&offset={offset}",
                          headers=admin)
        assert resp.status_code == 200
        page = resp.json()["items"]
        if not page:
            break
        found |= {e["action"] for e in page}
        offset += 200
    missing = expected_actions - found
    assert not missing, f"acciones de audit ausentes: {missing}"

    # Verificación de cadena vía API
    resp = client.post("/api/admin/audit/verify", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok", resp.text

    # Verificación vía CLI real (subprocess, exit code 0)
    cli = os.path.join(PLATFORM_DIR, ".venv", "bin", "verify-audit-chain")
    proc = subprocess.run(
        [cli, "verify", _S["tenant_id"]],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"CLI: {proc.stdout} {proc.stderr}"
    assert '"status": "ok"' in proc.stdout or '"status":"ok"' in proc.stdout

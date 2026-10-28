"""Spec 026 — Self-signup de organizaciones (mandato Miller).

La cuenta nueva nace ``pending_activation`` y SIN budget; el proveedor la
activa y le asigna el budget inicial. Cubre AC-26-001…006 a nivel unitario
con el IdP mock declarado (mismo contrato que el resto de la suite) y un
double declarado de Keycloak Admin REST para el leg de creación del realm
(mismo patrón que tests/test_auth_ux.py::FakeKeycloakAuthUx). El journey
vivo contra Keycloak 26 real + navegador está en
docs/evidencia/tenant-self-signup/ (script evidence_tenant_self_signup.py).

Orden del módulo = el journey de REQ-SS-007: signup → pending fail-closed
(423) → activación por el proveedor → todo funcional → spend descuenta del
budget asignado. El estado se comparte vía ``_state`` (mismo patrón que
tests/test_provision.py).
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient

from conftest import MIGRATOR_DB_URL, TEST_DB, _admin, bearer_headers

PROVIDER = {"Authorization": "Bearer test-provider-token"}
SLUG = "miller-newco"
ORG_NAME = "Miller Newco"
ADMIN_EMAIL = "founder@miller-newco.test"
ADMIN_PASSWORD = "a-very-strong-passw0rd"  # gitleaks:allow fixture de test (usuario @miller-newco.test, no protege nada real)
ADMIN_NAME = "Miller Founder"
INITIAL_BUDGET_USD = 75.5
MODEL = "glm-5.2"

_state = {}  # shared across tests: tenant_id, admin_user_id, sub, tokens…


class FakeKeycloakSelfSignup:
    """Double declarado: las porciones de KeycloakAdminClient que usa el
    self-signup (realm completo + create_user_with_password + compensación).
    ``fail_at`` simula un fallo de Admin REST en un paso concreto."""

    def __init__(self, issuer_base: str):
        self.issuer_base = issuer_base.rstrip("/")
        self.steps = []
        self.created = []
        self.deleted_realms = []
        self.fail_at = None

    def issuer_for(self, slug: str) -> str:
        return f"{self.issuer_base}/realms/{slug}"

    def _step(self, name: str, slug: str):
        self.steps.append(name)
        if self.fail_at == name:
            from app.tenants.keycloak_admin import KeycloakAdminError
            raise KeycloakAdminError(name, "declared double failure")

    def ensure_realm(self, slug):
        self._step("ensure_realm", slug)

    def enable_unmanaged_attributes(self, slug):
        self._step("enable_unmanaged_attributes", slug)

    def ensure_tenant_id_mapper(self, slug):
        self._step("ensure_tenant_id_mapper", slug)

    def ensure_mfa_acr(self, slug):
        self._step("ensure_mfa_acr", slug)

    def ensure_login_theme(self, slug):
        self._step("ensure_login_theme", slug)

    def ensure_google_idp(self, slug):
        self._step("ensure_google_idp", slug)

    def ensure_tenant_id_hardcode_mapper(self, slug, tenant_id):
        self._step("ensure_tenant_id_hardcode_mapper", slug)

    def create_user_with_password(
        self, realm, *, username, email, tenant_id, display_name, password,
    ):
        self._step("create_user", realm)
        self.created.append({
            "realm": realm, "username": username, "email": email,
            "tenant_id": tenant_id, "display_name": display_name,
            "password": password,
        })
        return str(_uuid.uuid4())

    def delete_realm(self, slug):
        self.deleted_realms.append(slug)


@pytest.fixture
def fake_kc(monkeypatch, idp):
    fake = FakeKeycloakSelfSignup(idp.base_url)
    monkeypatch.setattr("app.tenants.self_signup._keycloak", lambda: fake)
    return fake


@pytest.fixture
def worker(litellm_fake):
    from app.db import get_engine
    from app.governance.spend_worker import run_once
    from app.jobs.service import build_migrator_engine
    from app.models.litellm import LiteLLMClient

    litellm = LiteLLMClient(litellm_fake.base_url, "sk-master-test")
    migrator = build_migrator_engine(MIGRATOR_DB_URL)

    def _run():
        return run_once(get_engine(), migrator, litellm)

    return _run


@pytest.fixture(autouse=True)
def _wipe_signup_throttle(admin_conn):
    """El test de rate-limit deja contadores en ops.login_throttle; se
    limpian antes y después para no contaminar otras corridas."""
    def _wipe():
        with admin_conn.cursor() as cur:
            cur.execute("DELETE FROM ops.login_throttle WHERE key LIKE 'signup:%'")
    _wipe()
    yield
    _wipe()


def _signup(client, *, org=ORG_NAME, slug=None, email=ADMIN_EMAIL,
            password=ADMIN_PASSWORD, name=ADMIN_NAME):
    return client.post("/api/auth/signup-org", json={
        "organization_name": org,
        "admin_email": email,
        "admin_password": password,
        "admin_display_name": name,
        **({"slug": slug} if slug is not None else {}),
    })


def _mint(client, idp):
    """Sesión admin para el founder del tenant nuevo (JWT del IdP mock con
    el issuer que el double declaró al crear el realm)."""
    token = idp.make_jwt(
        sub=_state["sub"], tenant_id=_state["tenant_id"],
        issuer=idp.issuer(SLUG), audience="trust-agent-web", amr=["mfa"],
    )
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _q(admin_conn, sql, params=()):
    with admin_conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# AC-26-001 — signup público: tenant COMPLETO pero pending + budget 0¢
# ---------------------------------------------------------------------------

def test_signup_creates_pending_tenant(client, fake_kc, admin_conn, idp):
    resp = _signup(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    _state["resp_body_keys"] = set(body)

    # Respuesta sin credenciales (la contraseña la eligió el usuario).
    assert body["org"] == SLUG
    assert body["status"] == "pending_activation"
    assert "pending activation" in body["message"]
    assert set(body) == {"org", "status", "tenant", "message"}

    tenant = body["tenant"]
    assert tenant["slug"] == SLUG
    assert tenant["status"] == "pending_activation"
    assert tenant["oidc_issuer_url"] == idp.issuer(SLUG)
    assert tenant["counts"]["departments"] == 4  # plantillas seed
    assert tenant["counts"]["users"] == 1
    assert tenant["counts"]["org_budgets"] == 1
    assert tenant["counts"]["approved_models"] > 0
    assert tenant["counts"]["active_org_policies"] > 0
    _state["tenant_id"] = tenant["id"]

    # El realm KC se creó con la superficie completa (white-label + Google
    # fail-closed) y el admin con password PERMANENTE elegido por el usuario.
    for step in ("ensure_realm", "ensure_login_theme", "ensure_google_idp",
                 "create_user"):
        assert step in fake_kc.steps
    assert len(fake_kc.created) == 1
    call = fake_kc.created[0]
    assert call["realm"] == SLUG
    assert call["username"] == ADMIN_EMAIL
    assert call["password"] == ADMIN_PASSWORD  # literal, verbatim

    # DB real: tenant pending, budget org 0¢, admin org_admin, audit.
    tid = tenant["id"]
    rows = _q(admin_conn,
              "SELECT status FROM tenants WHERE id = %s", (tid,))
    assert rows and rows[0][0] == "pending_activation"

    budget = _q(admin_conn,
                "SELECT amount_cents FROM budgets "
                "WHERE tenant_id = %s AND level = 'org'", (tid,))
    assert budget and budget[0][0] == 0  # nace SIN budget

    users = _q(admin_conn,
               "SELECT u.id, u.oidc_subject, ur.role_key FROM users u "
               "JOIN user_roles ur ON ur.user_id = u.id "
               "WHERE u.tenant_id = %s", (tid,))
    assert len(users) == 1
    admin_user_id, admin_sub, role_key = users[0]
    assert role_key == "org_admin"
    assert admin_sub  # el sub que devolvió el double de KC
    _state["admin_user_id"] = str(admin_user_id)
    _state["sub"] = admin_sub

    audits = _q(admin_conn,
                "SELECT action FROM audit_log WHERE tenant_id = %s "
                "AND action = 'tenant.self_registered'", (tid,))
    assert audits, "falta el audit tenant.self_registered"
    # Los eventos de seed del provisioning también auditan el alta.
    seed_actions = {r[0] for r in _q(
        admin_conn, "SELECT action FROM audit_log WHERE tenant_id = %s", (tid,))}
    assert {"department.created", "user.created", "role.assigned",
            "budget.updated", "policy.updated", "model.approved"} <= seed_actions


# ---------------------------------------------------------------------------
# AC-26-002 — validaciones, 409, flag off, rate limit
# ---------------------------------------------------------------------------

def test_signup_validation_and_conflict(client, fake_kc):
    # Slug ocupado → 409 sin revelar nada del tenant existente.
    dup = _signup(client, org="Another Corp", slug=SLUG,
                  email="other@miller-newco.test")
    assert dup.status_code == 409, dup.text
    # Payload inválido → 422 (email, password corto, slug, nombre vacío).
    assert _signup(client, email="not-an-email").status_code == 422
    assert _signup(client, password="short").status_code == 422
    assert _signup(client, slug="Bad_Slug").status_code == 422
    assert _signup(client, org="   ").status_code == 422
    assert _signup(client, name="  ").status_code == 422
    # Nada de esto llegó a Keycloak ni creó tenant.
    assert len(fake_kc.created) == 0
    assert "ensure_realm" not in fake_kc.steps


def test_signup_disabled_flag_403(client, fake_kc, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "self_signup_enabled", False)
    resp = _signup(client, org="Flag Off Corp", slug="flag-off-corp")
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["reason"] == "self_signup_disabled"
    assert len(fake_kc.created) == 0


def test_signup_rate_limited_per_ip(db, idp, admin_conn):
    """TRUST_LOGIN_MAX_FAILURES=5 en el entorno de la suite: 5 signups
    fallidos desde una IP → el 6º se bloquea ANTES de procesar (429),
    aunque el payload sea válido."""
    from app.main import create_app

    flood_ip = "203.0.115.99"
    with TestClient(create_app(), client=(flood_ip, 44444)) as flood:
        for _ in range(5):
            resp = _signup(flood, org="Flood Corp", slug="flood-corp",
                           password="short")
            assert resp.status_code == 422, resp.text
        blocked = _signup(flood, org="Flood Corp", slug="flood-corp")
        assert blocked.status_code == 429, blocked.text
        assert "Retry-After" in blocked.headers
        # Bloqueado antes de tocar Keycloak/DB: no existe el tenant.
        rows = _q(admin_conn,
                  "SELECT 1 FROM tenants WHERE slug = 'flood-corp'")
        assert rows == []
        # El contador vive en ops.login_throttle (sobrevive reinicios).
        rows = _q(admin_conn,
                  "SELECT fail_count FROM ops.login_throttle "
                  "WHERE key = %s", (f"signup:ip:{flood_ip}",))
        assert rows and rows[0][0] == 5


# ---------------------------------------------------------------------------
# AC-26-003 — tenant apagado = fail-closed (423 en toda la superficie)
# ---------------------------------------------------------------------------

def test_pending_tenant_fail_closed_423(client, idp, fake_kc):
    tokens = _mint(client, idp)  # el mint SE permite (el usuario ve su estado)
    hdr = bearer_headers(tokens)

    resp = client.get("/api/me", headers=hdr)
    assert resp.status_code == 423, resp.text
    assert resp.json()["error"]["reason"] == "tenant_pending_activation"

    # NADA funcional: agents, models, devices, vault, org, governance.
    for path in ("/api/policies", "/api/agents",
                 "/api/budgets/overview", "/api/models",
                 "/api/devices/me", "/api/users", "/api/me/budget",
                 "/api/me/notifications"):
        r = client.get(path, headers=hdr)
        assert r.status_code == 423, f"{path} -> {r.status_code} {r.text}"

    # Ciclo de sesión permitido: el usuario debe poder salir (D-26-03).
    out = client.post("/api/auth/logout", headers=hdr)
    assert out.status_code == 204, out.text


# ---------------------------------------------------------------------------
# AC-26-004 — activación por el proveedor + budget + notificación
# ---------------------------------------------------------------------------

def test_provider_activates_with_budget(client, admin_conn):
    resp = client.post(f"/api/admin/tenants/{SLUG}/activate",
                       headers=PROVIDER,
                       json={"initial_budget_usd": INITIAL_BUDGET_USD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant"]["status"] == "active"
    assert body["notified_org_admins"] == 1

    tid = _state["tenant_id"]
    # Budget org = monto asignado (una sola fila, upsert).
    budget = _q(admin_conn,
                "SELECT amount_cents FROM budgets "
                "WHERE tenant_id = %s AND level = 'org'", (tid,))
    assert budget and budget[0][0] == int(round(INITIAL_BUDGET_USD * 100))

    # Audits del evento de negocio (proveedor).
    actions = {r[0] for r in _q(
        admin_conn, "SELECT action FROM audit_log WHERE tenant_id = %s "
        "AND actor_role = 'provider'", (tid,))}
    assert "tenant.activated" in actions
    assert "budget.assigned" in actions

    # Notificación al admin del cliente (canal existente).
    notes = _q(admin_conn,
               "SELECT type, payload FROM notifications "
               "WHERE tenant_id = %s AND user_id = %s AND type = 'tenant_activated'",
               (tid, _state["admin_user_id"]))
    assert notes, "falta la notificación tenant_activated al org_admin"
    payload = notes[0][1]
    assert float(payload["initial_budget_usd"]) == INITIAL_BUDGET_USD


# ---------------------------------------------------------------------------
# AC-26-005 — errores de activación
# ---------------------------------------------------------------------------

def test_activate_error_cases(client):
    # Ya active → 409.
    again = client.post(f"/api/admin/tenants/{SLUG}/activate",
                        headers=PROVIDER, json={"initial_budget_usd": 10})
    assert again.status_code == 409, again.text
    # Budget no positivo → 422.
    for bad in (0, -5):
        r = client.post(f"/api/admin/tenants/{SLUG}/activate",
                        headers=PROVIDER, json={"initial_budget_usd": bad})
        assert r.status_code == 422, r.text
    # Sin token de proveedor → 401.
    r = client.post(f"/api/admin/tenants/{SLUG}/activate",
                    json={"initial_budget_usd": 10})
    assert r.status_code == 401, r.text


def test_suspended_tenant_still_403_and_activate_409(client, idp):
    """El proveedor puede suspender de nuevo (existe) y suspended sigue
    siendo 403 tenant_suspended; activar un suspended → 409 (reactivate)."""
    r = client.post(f"/api/admin/tenants/{SLUG}/suspend", headers=PROVIDER)
    assert r.status_code == 200, r.text

    tokens = _mint(client, idp)
    me = client.get("/api/me", headers=bearer_headers(tokens))
    assert me.status_code == 403, me.text
    assert me.json()["error"]["reason"] == "tenant_suspended"

    act = client.post(f"/api/admin/tenants/{SLUG}/activate",
                      headers=PROVIDER, json={"initial_budget_usd": 10})
    assert act.status_code == 409, act.text

    # Se devuelve a active para el resto del journey (reactivate existe).
    back = client.post(f"/api/admin/tenants/{SLUG}/reactivate", headers=PROVIDER)
    assert back.status_code == 200, back.text
    assert back.json()["tenant"]["status"] == "active"


# ---------------------------------------------------------------------------
# AC-26-006 — tras activar: todo funcional y el spend descuenta del budget
# ---------------------------------------------------------------------------

def test_after_activation_functional_and_spend(client, idp, litellm_fake,
                                               worker, admin_conn):
    tokens = _mint(client, idp)
    hdr = bearer_headers(tokens)

    me = client.get("/api/me", headers=hdr)
    assert me.status_code == 200, me.text
    assert me.json()["tenant_slug"] == SLUG

    # Spend REAL: key del propio admin → chat vía LiteLLM → worker ingiere.
    key_resp = client.post("/api/models/keys", json={}, headers=hdr)
    assert key_resp.status_code == 201, key_resp.text
    key = key_resp.json()
    status, _ = litellm_fake.chat(key["key"], MODEL)
    assert status == 200

    summary = worker()
    tid = _state["tenant_id"]
    ingest = summary["tenants"][tid]["ingest"]
    assert ingest["ingested"] >= 1

    # El ledger registró el gasto y el counter org descuenta del budget.
    ledger = _q(admin_conn,
                "SELECT cost_billed_cents FROM spend_ledger "
                "WHERE tenant_id = %s", (tid,))
    assert ledger and ledger[0][0] > 0

    counters = _q(admin_conn,
                  "SELECT level, spent_cents FROM budget_counters "
                  "WHERE tenant_id = %s", (tid,))
    org_spent = {lvl: spent for lvl, spent in counters}.get("org", 0)
    assert org_spent > 0, "el spend no descontó del budget org"

    budget = _q(admin_conn,
                "SELECT amount_cents FROM budgets "
                "WHERE tenant_id = %s AND level = 'org'", (tid,))
    assert budget[0][0] == int(round(INITIAL_BUDGET_USD * 100))
    assert org_spent <= budget[0][0]  # el corte sigue operativo

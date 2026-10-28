"""REQ-GOV-030…035 — model access + virtual keys LiteLLM (spec 005 §3.3)."""
import os

import httpx
import pytest

from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, USER_ALICE, USER_BOB, USER_CAROL, USER_DAVE,
    _admin, bearer_headers, mint,
)

TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")

# Sandbox de módulo (t_e000bf40): este módulo chatea con keys de Carol
# (tenant A) contra el doble; esos logs, ingesta cualquier worker
# posterior como filas de HOY del tenant A y rompen los conteos EXACTOS
# de la ventana 'today' en test_usage_analytics. El fixture (conftest)
# remueve los logs del doble y borra el delta de filas del ledger al
# salir del módulo.
pytestmark = pytest.mark.usefixtures("ledger_delta_sandbox")


def test_subset_rule_and_revoke(client, idp, admin_conn):
    """AC-GOV-030: aprobar en depto un modelo no aprobado en org → 422;
    subset → 201 + audit; revocar org → fuera de la lista efectiva."""
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")

    # Retirar deepseek a nivel org.
    revoke = client.post(
        "/api/models/access/revoke",
        json={"model_name": "deepseek-v4-flash", "level": "org"},
        headers=bearer_headers(alice),
    )
    assert revoke.status_code == 200, revoke.text

    # Ahora aprobar deepseek en depto A2 viola el subset → 422.
    violated = client.put(
        "/api/models/access",
        json={"model_name": "deepseek-v4-flash", "level": "dept",
              "department_id": DEPT_A2},
        headers=bearer_headers(bob),
    )
    assert violated.status_code == 422
    assert "Subset rule" in violated.json()["error"]["message"]

    # qwen sigue aprobado en org → subset OK en depto A2.
    ok = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "dept",
              "department_id": DEPT_A2},
        headers=bearer_headers(bob),
    )
    assert ok.status_code == 201, ok.text

    with _admin(TEST_DB) as c:
        approved = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'model.approved'"
        ).fetchone()
        revoked = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'model.revoked'"
        ).fetchone()
    assert approved[0] >= 1 and revoked[0] >= 1

    # Con deepseek retirado en org, la lista efectiva de Bob (A2) ya no lo tiene.
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(bob))
    assert "deepseek-v4-flash" not in allowed.json()["models"]

    # Restaurar deepseek a nivel org para las demás suites.
    restore = client.put(
        "/api/models/access",
        json={"model_name": "deepseek-v4-flash", "level": "org", "mode": "central"},
        headers=bearer_headers(alice),
    )
    assert restore.status_code == 201
    _ = carol  # Carol se usa en los tests de verify


def test_verify_and_effective_list(client, idp, admin_conn):
    """AC-GOV-031: verify de modelo no efectivo → 403 model_not_approved +
    audit; aprobado → 200; lista efectiva = intersección."""
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")

    # Carol (A1): org ambos ∩ depto A1 [qwen] = [qwen].
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(carol))
    assert allowed.json()["models"] == ["glm-5.2"]

    ok = client.post("/api/models/verify",
                     json={"model_name": "glm-5.2"},
                     headers=bearer_headers(carol))
    assert ok.status_code == 200
    denied = client.post("/api/models/verify",
                         json={"model_name": "deepseek-v4-flash"},
                         headers=bearer_headers(carol))
    assert denied.status_code == 403
    assert denied.json()["error"]["reason"] == "model_not_approved"
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'model.access_denied'"
        ).fetchone()
    assert row[0] >= 1

    # Bob (A2, con fila de depto [qwen] del test anterior): [qwen].
    allowed_bob = client.get("/api/models/allowed-for-me",
                             headers=bearer_headers(bob))
    assert allowed_bob.json()["models"] == ["glm-5.2"]
    _ = alice


def test_access_overview(client, idp):
    """AC-GOV-033 + AC-018-01/02: catálogo REAL MaaS con contexto, precios
    oficiales y la sección `policy` (aprobación de modelos = policy)."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/models/access", headers=bearer_headers(alice))
    assert resp.status_code == 200
    body = resp.json()
    models = {m["model_name"]: m for m in body["models"]}
    # AC-018-01: exactamente los 6 deployments reales de MaaS intl en la
    # fuente CENTRAL (spec 032: el catálogo también puede llevar filas byok
    # de conexiones llm — otros tests de la misma sesión siembran las suyas;
    # el AC protege la fuente central, no ausencia de byok).
    central = {name: m for name, m in models.items()
               if m.get("source", "central") == "central"}
    # AC-018-01 (enmienda 2026-10-27, spec 018 §5): 6 deployments vivos +
    # qwen3-32b retirado (stale, historia conservada — migración 0026).
    assert set(central) == {
        "glm-5.3", "glm-5.2", "glm-5.1", "deepseek-v4.1-flash-1017",
        "deepseek-v4.1-flash", "deepseek-v4-flash", "qwen3-32b",
    }
    glm = models["glm-5.2"]
    # Precio oficial MaaS (price-maas-0002, Period 1): $1.40 / $4.40 por M.
    assert float(glm["input_price_cents_per_m"]) == 140
    assert float(glm["output_price_cents_per_m"]) == 440
    # Contexto documentado (Model List intl): 1M contexto, 128K output.
    assert glm["context_tokens"] == 1_000_000
    assert glm["max_output_tokens"] == 128_000
    # qwen3-32b: retirado upstream (404 en vivo) → stale; límites NULL.
    assert models["qwen3-32b"]["catalog_status"] == "stale"
    assert models["qwen3-32b"]["context_tokens"] is None
    assert models["qwen3-32b"]["max_output_tokens"] is None
    assert any(a["level"] == "org" for a in glm["approvals"])

    # AC-018-02: la aprobación de modelos ES policy — sección `policy`.
    policy = body["policy"]
    assert policy["approved_models_only"] is True  # floor invariable
    assert policy["allowed_models"] is None         # sin policy org vigente

    carol = mint(client, idp, "carol")
    denied = client.get("/api/models/access", headers=bearer_headers(carol))
    assert denied.status_code == 403


def test_policy_section_reflects_live_allowed_models(client, idp):
    """AC-018-02: `policy.allowed_models` refleja la policy org VIGENTE
    (deny-overrides): al restringir allowed_models, la sección cambia."""
    alice = mint(client, idp, "alice")
    put = client.put(
        "/api/policies/org",
        json={"key": "allowed_models", "value": ["glm-5.2"]},
        headers=bearer_headers(alice),
    )
    assert put.status_code == 200, put.text

    resp = client.get("/api/models/access", headers=bearer_headers(alice))
    policy = resp.json()["policy"]
    assert policy["allowed_models"] == ["glm-5.2"]
    assert policy["approved_models_only"] is True

    # Limpieza: eliminar la policy para dejar el estado "sin restricción"
    # exacto (allowed_models = null) para las demás suites.
    clear = client.delete("/api/policies/org/allowed_models",
                          headers=bearer_headers(alice))
    assert clear.status_code == 204


def test_model_approvals_exposed_in_policies(client, idp):
    """AC-018-02: GET /api/policies?level=org expone `model_approvals` —
    la matriz de aprobación como parte de la policy del nivel."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/policies?level=org",
                      headers=bearer_headers(alice))
    assert resp.status_code == 200
    body = resp.json()
    approvals = {a["model_name"]: a for a in body["model_approvals"]}
    # El seed aprueba glm-5.2 y deepseek-v4-flash a nivel org (central).
    assert set(approvals) >= {"glm-5.2", "deepseek-v4-flash"}
    assert approvals["glm-5.2"]["mode"] == "central"
    assert approvals["glm-5.2"]["status"] == "approved"


def test_issue_key_one_time_and_gateway_restriction(
    client, idp, litellm_fake, admin_conn
):
    """AC-GOV-032: la key se devuelve UNA vez; DB solo hash; el gateway
    restringe los modelos de la key."""
    alice = mint(client, idp, "alice")
    resp = client.post("/api/models/keys",
                       json={"user_id": USER_CAROL},
                       headers=bearer_headers(alice))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    plaintext = data["key"]
    assert plaintext.startswith("sk-fake-")
    assert data["allowed_models"] == ["glm-5.2"]
    assert data["max_budget_usd"] == 1000.0  # Carol: 100000 cents
    assert data["budget_duration"] == "30d"

    # DB: solo el hash — el plaintext no existe en ninguna tabla.
    with _admin(TEST_DB) as c:
        for table in ("api_keys", "sessions", "audit_log"):
            found = c.execute(
                f"SELECT count(*) FROM {table} "
                f"WHERE CAST(to_jsonb({table}) AS text) LIKE '%{plaintext}%'"
            ).fetchone()
            assert found[0] == 0, f"plaintext found in {table}"
        hashed = c.execute(
            "SELECT count(*) FROM api_keys WHERE allowed_models "
            " @> '\"glm-5.2\"'::jsonb"
        ).fetchone()
    assert hashed[0] >= 1

    # El gateway (doble HTTP declarado) restringe los modelos de la key.
    status, _ = litellm_fake.chat(plaintext, "deepseek-v4-flash")
    assert status == 403  # fuera de la lista de la key
    status, body = litellm_fake.chat(plaintext, "glm-5.2")
    assert status == 200

    # Re-emisión → key nueva distinta.
    second = client.post("/api/models/keys", json={"user_id": USER_CAROL},
                         headers=bearer_headers(alice))
    assert second.status_code == 201
    assert second.json()["key"] != plaintext


def test_key_metadata_never_exposes_secrets(client, idp, litellm_fake):
    """AC-GOV-035: GET keys/me sin key ni hash; revocación bloquea en el
    gateway."""
    carol = mint(client, idp, "carol")
    issued = client.post("/api/models/keys", json={},
                         headers=bearer_headers(carol))
    assert issued.status_code == 201
    plaintext = issued.json()["key"]
    key_id = issued.json()["key_id"]

    listing = client.get("/api/models/keys/me", headers=bearer_headers(carol))
    assert listing.status_code == 200
    body = listing.json()["keys"]
    assert len(body) >= 1
    assert all("key" not in k and "litellm_key_hash" not in k for k in body)
    assert plaintext not in listing.text

    revoked = client.post(f"/api/models/keys/{key_id}/revoke",
                          headers=bearer_headers(carol))
    assert revoked.status_code == 204
    status, _ = litellm_fake.chat(plaintext, "glm-5.2")
    assert status == 401  # bloqueada por el gateway


def test_key_rbac_and_cross_tenant(client, idp):
    """AC-GOV-060: user no emite keys ajenas; cross-tenant → 404; dept_admin
    fuera de su depto → 403."""
    carol = mint(client, idp, "carol")
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")

    # Carol (rol user) no puede emitir para Alice.
    denied = client.post("/api/models/keys", json={"user_id": USER_ALICE},
                         headers=bearer_headers(carol))
    assert denied.status_code == 403
    # Pero sí para sí misma (desktop.use).
    self_key = client.post("/api/models/keys", json={},
                           headers=bearer_headers(carol))
    assert self_key.status_code == 201
    # Cross-tenant: Alice no puede emitir para Dave (tenant B) → 404.
    forged = client.post("/api/models/keys", json={"user_id": USER_DAVE},
                         headers=bearer_headers(alice))
    assert forged.status_code == 404
    # Bob (dept_admin A2) no emite para Carol (A1).
    cross_dept = client.post("/api/models/keys", json={"user_id": USER_CAROL},
                             headers=bearer_headers(bob))
    assert cross_dept.status_code in (403, 404)
    # Carol no aprueba modelos.
    denied_approve = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "org"},
        headers=bearer_headers(carol),
    )
    assert denied_approve.status_code == 403


# --- AC-GOV-036 (fix t_f0d3122d): [] = deny-all explícito, ausente = heredar --

def test_allowed_models_empty_list_is_explicit_deny_all(client, idp, admin_conn):
    """AC-GOV-036(a)+(b): user-level [] y org-level [] → conjunto efectivo
    vacío + verify 403 model_not_approved + audit (el bug original ignoraba
    la lista vacía: `or []` + truthiness en models/access.py:197-199)."""
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    # Org aprueba glm-5.2 (seed) y fija allowed_models=["glm-5.2"].
    put = client.put(
        "/api/policies/org",
        json={"key": "allowed_models", "value": ["glm-5.2"]},
        headers=bearer_headers(alice),
    )
    assert put.status_code == 200, put.text

    # (a) user-level [] — el valor MÁS restrictivo; el upsert lo acepta.
    deny = client.put(
        f"/api/policies/user/{USER_CAROL}",
        json={"key": "allowed_models", "value": []},
        headers=bearer_headers(alice),
    )
    assert deny.status_code == 200, deny.text

    # El combinator computa deny-all (esto ya funcionaba).
    eff = client.get("/api/me/policies",
                     headers=bearer_headers(carol)).json()
    assert eff["allowed_models"] == []

    # El gate YA NO ignora la lista vacía (fallo P4e de QA).
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(carol))
    assert allowed.status_code == 200
    assert allowed.json()["models"] == []
    denied = client.post("/api/models/verify",
                         json={"model_name": "glm-5.2"},
                         headers=bearer_headers(carol))
    assert denied.status_code == 403
    assert denied.json()["error"]["reason"] == "model_not_approved"
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'model.access_denied'"
        ).fetchone()
    assert row[0] >= 1

    # Clave ausente a nivel usuario otra vez → hereda (org ["glm-5.2"]).
    # Desde REQ-GOV-025 (t_fdc728d2) el DELETE de nivel user resuelve el
    # depto del usuario igual que el upsert — user_id bastaría; el
    # department_id explícito se mantiene aquí por compatibilidad.
    clear = client.delete(
        f"/api/policies/user/allowed_models"
        f"?user_id={USER_CAROL}&department_id={DEPT_A1}",
        headers=bearer_headers(alice),
    )
    assert clear.status_code == 204
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(carol))
    assert allowed.json()["models"] == ["glm-5.2"]

    # (b) org-level [] → deny-all para TODO el tenant (admin incluido).
    deny_org = client.put(
        "/api/policies/org",
        json={"key": "allowed_models", "value": []},
        headers=bearer_headers(alice),
    )
    assert deny_org.status_code == 200, deny_org.text
    for tokens in (carol, alice):
        allowed = client.get("/api/models/allowed-for-me",
                             headers=bearer_headers(tokens))
        assert allowed.json()["models"] == []
    denied = client.post("/api/models/verify",
                         json={"model_name": "glm-5.2"},
                         headers=bearer_headers(carol))
    assert denied.status_code == 403

    # Limpieza: volver al estado exacto "sin policy" (null) del seed.
    clear = client.delete("/api/policies/org/allowed_models",
                          headers=bearer_headers(alice))
    assert clear.status_code == 204


def test_allowed_models_absent_key_inherits(client, idp):
    """AC-GOV-036(c): clave ausente en los 3 niveles → default null del
    catálogo (migración 0019) en la efectiva y herencia de aprobaciones."""
    carol = mint(client, idp, "carol")
    eff = client.get("/api/me/policies",
                     headers=bearer_headers(carol)).json()
    assert eff["allowed_models"] is None
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(carol))
    # org {glm-5.2, deepseek-v4-flash} ∩ depto A1 {glm-5.2} ∩ user {glm-5.2}
    assert allowed.json()["models"] == ["glm-5.2"]


def test_level_with_all_rows_denied_denies_all(client, idp):
    """AC-GOV-036(d): un nivel de aprobación con filas pero CERO aprobadas
    deniega todo a ese nivel — revocar la última aprobación de un depto
    (o usuario) jamás relaja al conjunto superior (patrón `if dept:`)."""
    bob = mint(client, idp, "bob")   # dept_admin A2
    alice = mint(client, idp, "alice")

    # Depto A2: asegurar la fila aprobada de glm-5.2 y revocarla.
    ok = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "dept",
              "department_id": DEPT_A2},
        headers=bearer_headers(bob),
    )
    assert ok.status_code == 201, ok.text
    revoke = client.post(
        "/api/models/access/revoke",
        json={"model_name": "glm-5.2", "level": "dept",
              "department_id": DEPT_A2},
        headers=bearer_headers(bob),
    )
    assert revoke.status_code == 200, revoke.text

    # A2 queda con 1 fila denied y 0 aprobadas → deny-all para Bob.
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(bob))
    assert allowed.json()["models"] == []
    denied = client.post("/api/models/verify",
                         json={"model_name": "glm-5.2"},
                         headers=bearer_headers(bob))
    assert denied.status_code == 403

    # Re-aprobar restaura el nivel (approve() re-activa la fila denied).
    restore = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "dept",
              "department_id": DEPT_A2},
        headers=bearer_headers(bob),
    )
    assert restore.status_code == 201, restore.text
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(bob))
    assert allowed.json()["models"] == ["glm-5.2"]

    # Simétrico a nivel usuario: revocar la única fila de Carol la deja
    # en deny-all aunque org/depto sigan aprobando glm-5.2.
    revoke_user = client.post(
        "/api/models/access/revoke",
        json={"model_name": "glm-5.2", "level": "user",
              "department_id": DEPT_A1, "user_id": USER_CAROL},
        headers=bearer_headers(alice),
    )
    assert revoke_user.status_code == 200, revoke_user.text
    carol = mint(client, idp, "carol")
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(carol))
    assert allowed.json()["models"] == []
    # Restaurar la fila byok del seed.
    restore_user = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "user",
              "department_id": DEPT_A1, "user_id": USER_CAROL,
              "mode": "byok"},
        headers=bearer_headers(alice),
    )
    assert restore_user.status_code == 201, restore_user.text
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(carol))
    assert allowed.json()["models"] == ["glm-5.2"]


def test_user_level_resolves_department(client, idp, admin_conn):
    """AC-018-06 (D-018-06, GAP M-1): level=user resolves the user's REAL
    department server-side. The exact UI payload (department_id null)
    used to reach the INSERT as (level='user', dept NULL) and die as
    CheckViolation → 500 against the live constraint — this suite runs
    on the real Alembic schema, so the constraint is exercised for real.
    """
    import uuid as _uuid

    alice = mint(client, idp, "alice")  # org_admin (A)
    bob = mint(client, idp, "bob")      # user in dept A2 (no user rows)

    # --- The exact UI payload: level=user, user_id, NO department_id ---
    ok = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "user",
              "user_id": USER_BOB, "mode": "central"},
        headers=bearer_headers(alice),
    )
    assert ok.status_code == 201, ok.text

    # The row carries Bob's REAL department (A2), resolved by the backend.
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT department_id::text FROM model_access ma "
            "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
            f"WHERE ma.tenant_id = '{TENANT_A}' AND ma.level = 'user' "
            "AND ma.user_id = %s AND mc.model_name = 'glm-5.2'",
            (USER_BOB,),
        ).fetchone()
    assert row is not None and row[0] == DEPT_A2, row

    # Effective set: org approves deepseek; Bob's user row approves it too.
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(bob))
    assert "glm-5.2" in allowed.json()["models"]

    # --- Contract 422s (D-018-06): never a CheckViolation 500 ---
    mismatch = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "user",
              "department_id": DEPT_A1, "user_id": USER_BOB},
        headers=bearer_headers(alice),
    )
    assert mismatch.status_code == 422
    assert "does not match" in mismatch.json()["error"]["message"]

    no_user = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "user"},
        headers=bearer_headers(alice),
    )
    assert no_user.status_code == 422

    dept_no_id = client.put(
        "/api/models/access",
        json={"model_name": "deepseek-v4-flash", "level": "dept"},
        headers=bearer_headers(alice),
    )
    assert dept_no_id.status_code == 422

    org_with_scope = client.put(
        "/api/models/access",
        json={"model_name": "deepseek-v4-flash", "level": "org",
              "department_id": DEPT_A1},
        headers=bearer_headers(alice),
    )
    assert org_with_scope.status_code == 422

    # Cross-tenant user_id does not exist for this tenant (D-CP-09) → 404.
    cross = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "user",
              "user_id": USER_DAVE},
        headers=bearer_headers(alice),
    )
    assert cross.status_code == 404

    # A user WITHOUT a department cannot have a user-level row → 422.
    nodept_id = str(_uuid.uuid4())
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            "display_name, status) VALUES (%s, %s, NULL, "
            "'nodept@tenant-a.test', 'No Dept', 'active')",
            (nodept_id, TENANT_A),
        )
    try:
        nodept = client.put(
            "/api/models/access",
            json={"model_name": "glm-5.2", "level": "user",
                  "user_id": nodept_id},
            headers=bearer_headers(alice),
        )
        assert nodept.status_code == 422
        assert "no department" in nodept.json()["error"]["message"]
    finally:
        with _admin(TEST_DB) as c:
            c.execute("DELETE FROM users WHERE id = %s", (nodept_id,))

    # --- Symmetric revoke by user_id alone (what the UI sends) ---
    revoked = client.post(
        "/api/models/access/revoke",
        json={"model_name": "glm-5.2", "level": "user",
              "user_id": USER_BOB},
        headers=bearer_headers(alice),
    )
    assert revoked.status_code == 200, revoked.text
    # A level whose rows are all denied denies everything (t_f0d3122d).
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(bob))
    assert allowed.json()["models"] == []

    # Re-approve restores the level (approve() re-activates the row).
    restore = client.put(
        "/api/models/access",
        json={"model_name": "glm-5.2", "level": "user",
              "user_id": USER_BOB, "mode": "central"},
        headers=bearer_headers(alice),
    )
    assert restore.status_code == 201, restore.text
    allowed = client.get("/api/models/allowed-for-me",
                         headers=bearer_headers(bob))
    assert "glm-5.2" in allowed.json()["models"]

    # Cleanup: Bob has no user-level rows in the seed.
    with _admin(TEST_DB) as c:
        c.execute(
            "DELETE FROM model_access WHERE tenant_id = %s "
            "AND level = 'user' AND user_id = %s",
            (TENANT_A, USER_BOB),
        )

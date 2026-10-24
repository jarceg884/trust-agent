"""REQ-GOV-030…035 — model access + virtual keys LiteLLM (spec 005 §3.3)."""
import httpx

from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, USER_ALICE, USER_BOB, USER_CAROL, USER_DAVE,
    _admin, bearer_headers, mint,
)

TEST_DB = "trust_agent_platform_test"


def test_subset_rule_and_revoke(client, idp, admin_conn):
    """AC-GOV-030: aprobar en depto un modelo no aprobado en org → 422;
    subset → 201 + audit; revocar org → fuera de la lista efectiva."""
    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")

    # Retirar deepseek a nivel org.
    revoke = client.post(
        "/api/models/access/revoke",
        json={"model_name": "deepseek-v3", "level": "org"},
        headers=bearer_headers(alice),
    )
    assert revoke.status_code == 200, revoke.text

    # Ahora aprobar deepseek en depto A2 viola el subset → 422.
    violated = client.put(
        "/api/models/access",
        json={"model_name": "deepseek-v3", "level": "dept",
              "department_id": DEPT_A2},
        headers=bearer_headers(bob),
    )
    assert violated.status_code == 422
    assert "Subset rule" in violated.json()["error"]["message"]

    # qwen sigue aprobado en org → subset OK en depto A2.
    ok = client.put(
        "/api/models/access",
        json={"model_name": "qwen2.5-72b-instruct", "level": "dept",
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
    assert "deepseek-v3" not in allowed.json()["models"]

    # Restaurar deepseek a nivel org para las demás suites.
    restore = client.put(
        "/api/models/access",
        json={"model_name": "deepseek-v3", "level": "org", "mode": "central"},
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
    assert allowed.json()["models"] == ["qwen2.5-72b-instruct"]

    ok = client.post("/api/models/verify",
                     json={"model_name": "qwen2.5-72b-instruct"},
                     headers=bearer_headers(carol))
    assert ok.status_code == 200
    denied = client.post("/api/models/verify",
                         json={"model_name": "deepseek-v3"},
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
    assert allowed_bob.json()["models"] == ["qwen2.5-72b-instruct"]
    _ = alice


def test_access_overview(client, idp):
    """AC-GOV-033: catálogo global con estado por nivel y pricing."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/models/access", headers=bearer_headers(alice))
    assert resp.status_code == 200
    models = {m["model_name"]: m for m in resp.json()["models"]}
    assert set(models) >= {"qwen2.5-72b-instruct", "deepseek-v3"}
    qwen = models["qwen2.5-72b-instruct"]
    assert qwen["input_price_cents_per_m"] == 400
    assert any(a["level"] == "org" for a in qwen["approvals"])
    carol = mint(client, idp, "carol")
    denied = client.get("/api/models/access", headers=bearer_headers(carol))
    assert denied.status_code == 403


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
    assert data["allowed_models"] == ["qwen2.5-72b-instruct"]
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
            " @> '\"qwen2.5-72b-instruct\"'::jsonb"
        ).fetchone()
    assert hashed[0] >= 1

    # El gateway (doble HTTP declarado) restringe los modelos de la key.
    status, _ = litellm_fake.chat(plaintext, "deepseek-v3")
    assert status == 403  # fuera de la lista de la key
    status, body = litellm_fake.chat(plaintext, "qwen2.5-72b-instruct")
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
    status, _ = litellm_fake.chat(plaintext, "qwen2.5-72b-instruct")
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
        json={"model_name": "qwen2.5-72b-instruct", "level": "org"},
        headers=bearer_headers(carol),
    )
    assert denied_approve.status_code == 403

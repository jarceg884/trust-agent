"""REQ-GOV-020…024 — API de políticas en 3 niveles (spec 005 §3.2)."""
from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, USER_BOB, USER_CAROL, _admin, bearer_headers,
    mint,
)

TEST_DB = "trust_agent_platform_test"


def _put_policy(client, path, key, value, tokens):
    return client.put(path, json={"key": key, "value": value},
                      headers=bearer_headers(tokens))


def test_unknown_key_and_type_validation(client, idp):
    """AC-GOV-020: clave fuera del catálogo → 422; tipo incorrecto → 422."""
    alice = mint(client, idp, "alice")
    unknown = _put_policy(client, "/api/policies/org", "not_a_policy_key", 1, alice)
    assert unknown.status_code == 422
    bad_type = _put_policy(client, "/api/policies/org",
                           "session_timeout_minutes", "thirty", alice)
    assert bad_type.status_code == 422
    bad_list = _put_policy(client, "/api/policies/org",
                           "allowed_connectors", "vmware", alice)
    assert bad_list.status_code == 422
    ok = _put_policy(client, "/api/policies/org", "max_daily_sessions", 5, alice)
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == 1


def test_invariable_keys_rejected_at_every_level(client, idp):
    """AC-GOV-021: invariables del proveedor → 422 en org/depto/usuario."""
    alice = mint(client, idp, "alice")
    for path in ("/api/policies/org",
                 f"/api/policies/dept/{DEPT_A1}",
                 f"/api/policies/user/{USER_CAROL}"):
        resp = _put_policy(client, path, "require_mfa", False, alice)
        assert resp.status_code == 422, path
        resp = _put_policy(client, path, "approved_models_only", False, alice)
        assert resp.status_code == 422, path


def test_versioning_and_audit(client, idp, admin_conn):
    """AC-GOV-022: version +1 por cambio; audit policy.updated con versión;
    la efectiva refleja el valor nuevo."""
    alice = mint(client, idp, "alice")
    first = _put_policy(client, "/api/policies/org", "max_daily_sessions", 5, alice)
    assert first.status_code == 200
    v1 = first.json()["version"]
    second = _put_policy(client, "/api/policies/org", "max_daily_sessions", 3, alice)
    assert second.json()["version"] == v1 + 1
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'policy.updated' "
            "AND payload->>'key' = 'max_daily_sessions' "
            "AND (payload->>'version')::int = %s",
            (v1 + 1,),
        ).fetchone()
    assert row[0] >= 1
    carol = mint(client, idp, "carol")
    effective = client.get("/api/me/policies", headers=bearer_headers(carol))
    assert effective.status_code == 200
    assert effective.json()["max_daily_sessions"] == 3
    # Limpieza: desactivar la política del test.
    resp = client.delete("/api/policies/org/max_daily_sessions",
                         headers=bearer_headers(alice))
    assert resp.status_code == 204
    effective = client.get("/api/me/policies", headers=bearer_headers(carol))
    assert effective.json()["max_daily_sessions"] == 10  # default del catálogo


def test_dept_cannot_relax_org(client, idp):
    """AC-GOV-023: valor de depto que relaja el org → 422; más restrictivo → 200."""
    bob = mint(client, idp, "bob")
    # Seed: org allowed_connectors = [vmware, email].
    relax = _put_policy(client, f"/api/policies/dept/{DEPT_A2}",
                        "allowed_connectors", ["vmware", "email", "slack"], bob)
    assert relax.status_code == 422
    tighten = _put_policy(client, f"/api/policies/dept/{DEPT_A2}",
                          "allowed_connectors", ["vmware"], bob)
    assert tighten.status_code == 200, tighten.text
    # user-level: relajar → 422; igual de restrictivo → 200.
    alice = mint(client, idp, "alice")
    relax_user = _put_policy(client, f"/api/policies/user/{USER_CAROL}",
                             "allowed_connectors",
                             ["vmware", "email", "slack"], alice)
    assert relax_user.status_code == 422
    ok_user = _put_policy(client, f"/api/policies/user/{USER_CAROL}",
                          "allowed_connectors", ["vmware"], alice)
    assert ok_user.status_code == 200


def test_effective_policy_combination(client, idp):
    """AC-GOV-024: la efectiva de Carol = deny-overrides de los 3 niveles."""
    carol = mint(client, idp, "carol")
    resp = client.get("/api/me/policies", headers=bearer_headers(carol))
    assert resp.status_code == 200
    eff = resp.json()
    # org [vmware,email] ∩ dept A1 [vmware] ∩ user [vmware] = [vmware]
    assert eff["allowed_connectors"] == ["vmware"]
    # invariable al piso del proveedor aunque el seed tenga require_mfa=false
    assert eff["require_mfa"] is True
    assert eff["approved_models_only"] is True
    # user-level seed: session_timeout_minutes = 30
    assert eff["session_timeout_minutes"] == 30


def test_policy_rbac(client, idp):
    """AC-GOV-060: user → 403; dept_admin no toca policies org ni otro depto."""
    carol = mint(client, idp, "carol")
    bob = mint(client, idp, "bob")
    denied = _put_policy(client, "/api/policies/org",
                         "max_daily_sessions", 5, carol)
    assert denied.status_code == 403
    org_by_bob = _put_policy(client, "/api/policies/org",
                             "max_daily_sessions", 5, bob)
    assert org_by_bob.status_code == 403  # sin policies.org.manage
    other_dept = _put_policy(client, f"/api/policies/dept/{DEPT_A1}",
                             "allowed_connectors", ["vmware"], bob)
    assert other_dept.status_code == 404  # fuera de su scope


def test_list_policies(client, idp):
    alice = mint(client, idp, "alice")
    resp = client.get("/api/policies?level=org", headers=bearer_headers(alice))
    assert resp.status_code == 200
    keys = {p["key"] for p in resp.json()["policies"]}
    assert "allowed_connectors" in keys
    carol = mint(client, idp, "carol")
    denied = client.get("/api/policies?level=org",
                        headers=bearer_headers(carol))
    assert denied.status_code == 403

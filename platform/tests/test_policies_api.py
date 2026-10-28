"""REQ-GOV-020…024 — API de políticas en 3 niveles (spec 005 §3.2)."""
import os

from conftest import (
    DEPT_A1, DEPT_A2, TENANT_A, USER_BOB, USER_CAROL, _admin, bearer_headers,
    mint,
)

TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")


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
    """AC-GOV-021: invariables del proveedor → 422 en org/depto/usuario.

    Spec 015 (D-AUTH-07): ``require_mfa`` DEJÓ de ser invariable — es una
    policy org-tunable (el org_admin puede apagarla); se verifica aquí mismo
    que el PUT org la acepta, mientras ``approved_models_only`` sigue siendo
    piso invariable del proveedor.
    """
    alice = mint(client, idp, "alice")
    for path in ("/api/policies/org",
                 f"/api/policies/dept/{DEPT_A1}",
                 f"/api/policies/user/{USER_CAROL}"):
        resp = _put_policy(client, path, "approved_models_only", False, alice)
        assert resp.status_code == 422, path
    # require_mfa ahora es variable (spec 015): el org_admin puede apagarla.
    resp = _put_policy(client, "/api/policies/org", "require_mfa", False, alice)
    assert resp.status_code == 200, resp.text
    # ...y la efectiva lo refleja (el gate MFA se vuelve opcional).
    eff = client.get("/api/me/policies",
                     headers=bearer_headers(alice)).json()
    assert eff["require_mfa"] is False
    # Restaurar para no contaminar otros tests del mismo tenant.
    _put_policy(client, "/api/policies/org", "require_mfa", True, alice)


def test_org_only_keys_reject_dept_and_user_overrides(client, idp):
    """Spec 015 (D-AUTH-07/08, hallazgo t_8f716f9f): ``require_mfa`` y
    ``allow_self_registration`` gatillan comportamiento org-wide ANTES de
    que exista contexto de departamento (login UX, auto-registro) — el gate
    lee SOLO el nivel org, así que un override dept/user quedaría guardado,
    visible en la matriz y jamás honrado. El upsert lo rechaza (422); el
    nivel org sigue siendo tunable."""
    alice = mint(client, idp, "alice")
    for key in ("require_mfa", "allow_self_registration"):
        for path in (f"/api/policies/dept/{DEPT_A1}",
                     f"/api/policies/user/{USER_CAROL}"):
            resp = _put_policy(client, path, key, False, alice)
            assert resp.status_code == 422, (key, path, resp.text)
            assert "org-only" in resp.json()["error"]["message"]
    # El nivel org sigue siendo el único tunable (spec 015 D-AUTH-07).
    resp = _put_policy(client, "/api/policies/org", "require_mfa", True, alice)
    assert resp.status_code == 200, resp.text


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
    # org row require_mfa=true (seed spec 015: tenants existentes la conservan)
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


def test_delete_user_policy_without_department_id(client, idp):
    """AC-GOV-025 (gap B-3, t_fdc728d2): PUT /policies/user/{user_id}
    resolves the user's department from `users` and stamps it on the row;
    DELETE /policies/user/{key}?user_id=… WITHOUT department_id must apply
    the SAME resolution. Exact repro of the asymmetry: before the fix this
    DELETE answered 404 for a policy the PUT had just written (200)."""
    alice = mint(client, idp, "alice")
    # The upsert resolves and stamps Carol's department (DEPT_A1) — the
    # caller never passes a department_id on the PUT path.
    put = _put_policy(client, f"/api/policies/user/{USER_CAROL}",
                      "allowed_models", ["glm-5.2"], alice)
    assert put.status_code == 200, put.text
    # DELETE without department_id: resolves the dept like the PUT → 204.
    # (Before REQ-GOV-025: 404 "Policy not found at this level.")
    delete = client.delete(
        "/api/policies/user/allowed_models",
        params={"user_id": USER_CAROL},
        headers=bearer_headers(alice),
    )
    assert delete.status_code == 204, delete.text
    # Really deactivated: a second identical DELETE finds no active row.
    again = client.delete(
        "/api/policies/user/allowed_models",
        params={"user_id": USER_CAROL},
        headers=bearer_headers(alice),
    )
    assert again.status_code == 404, again.text
    # Nonexistent user → 404 "User not found." (same contract as the PUT).
    missing = client.delete(
        "/api/policies/user/allowed_models",
        params={"user_id": "00000000-0000-0000-0000-000000000000"},
        headers=bearer_headers(alice),
    )
    assert missing.status_code == 404, missing.text
    assert "User not found" in missing.json()["error"]["message"]

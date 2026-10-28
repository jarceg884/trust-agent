"""REQ-GOV-040…044 — agentes compartidos con grants explícitos (spec 005 §3.4)."""
from conftest import (
    TENANT_A, TEST_DB, USER_ALICE, USER_BOB, USER_CAROL, USER_DAVE, _admin,
    bearer_headers, mint,
)


def _publish(client, tokens, name="ops-agent"):
    resp = client.post("/api/agents",
                       json={"name": name, "description": "d",
                             "scope": "dept"},
                       headers=bearer_headers(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_publish_creates_no_grants(client, idp, admin_conn):
    """AC-GOV-040: publicar sin grants; invisible para otros; audit."""
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol)

    bob = mint(client, idp, "bob")
    listing = client.get("/api/agents", headers=bearer_headers(bob))
    assert all(a["id"] != agent_id for a in listing.json()["agents"])

    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'agent.shared'"
        ).fetchone()
        grants = c.execute(
            "SELECT count(*) FROM agent_grants "
            f"WHERE tenant_id = '{TENANT_A}' AND agent_id = '{agent_id}'"
        ).fetchone()
    assert row[0] >= 1
    assert grants[0] == 0  # REQ-AGT-001: sin ningún grant por defecto


def test_grants_require_manage_rights(client, idp):
    """AC-GOV-041: terceros sin derecho → 403; dueño → 201; permiso manage →
    422; grant al dueño → 422."""
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol, "grant-agent")

    bob = mint(client, idp, "bob")  # dept_admin de A2, agente en A1
    denied = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_BOB, "permission": "invoke"},
        headers=bearer_headers(bob),
    )
    assert denied.status_code == 403

    ok = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_ALICE, "permission": "invoke"},
        headers=bearer_headers(carol),
    )
    assert ok.status_code == 201, ok.text

    manage = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_ALICE, "permission": "manage"},
        headers=bearer_headers(carol),
    )
    assert manage.status_code == 422

    self_grant = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_CAROL, "permission": "view"},
        headers=bearer_headers(carol),
    )
    assert self_grant.status_code == 422

    # org_admin también puede gestionar grants (manage role-based).
    alice = mint(client, idp, "alice")
    by_admin = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_BOB, "permission": "view"},
        headers=bearer_headers(alice),
    )
    assert by_admin.status_code == 201
    return agent_id


def test_access_without_grant_is_404_view_without_invoke_403(client, idp):
    """AC-GOV-042: sin grant → 404 (sin revelar existencia); view → GET 200,
    invoke 403 + audit; listado scoped."""
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol, "access-agent")
    bob = mint(client, idp, "bob")
    alice = mint(client, idp, "alice")

    # Bob sin grant: GET e invoke → 404.
    assert client.get(f"/api/agents/{agent_id}",
                      headers=bearer_headers(bob)).status_code == 404
    assert client.post(f"/api/agents/{agent_id}/invoke",
                       headers=bearer_headers(bob)).status_code == 404

    # Cross-tenant: Dave (tenant B) → 404.
    dave = mint(client, idp, "dave", tenant="22222222-2222-2222-2222-222222222222")
    assert client.get(f"/api/agents/{agent_id}",
                      headers=bearer_headers(dave)).status_code == 404

    # Alice con grant invoke: GET 200 + invoke 200.
    grant = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_ALICE, "permission": "invoke"},
        headers=bearer_headers(carol),
    )
    assert grant.status_code == 201
    got = client.get(f"/api/agents/{agent_id}",
                     headers=bearer_headers(alice))
    assert got.status_code == 200
    assert got.json()["access"] == "invoke"

    # Bob con grant view: GET 200 pero invoke → 403.
    view_grant = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_BOB, "permission": "view"},
        headers=bearer_headers(alice),
    )
    assert view_grant.status_code == 201
    assert client.get(f"/api/agents/{agent_id}",
                      headers=bearer_headers(bob)).status_code == 200
    denied = client.post(f"/api/agents/{agent_id}/invoke",
                         headers=bearer_headers(bob))
    assert denied.status_code == 403

    # Listado scoped: Alice ve el agente (grant invoke), Bob también (view).
    alice_list = client.get("/api/agents", headers=bearer_headers(alice))
    assert any(a["id"] == agent_id and a["access"] == "invoke"
               for a in alice_list.json()["agents"])
    bob_list = client.get("/api/agents", headers=bearer_headers(bob))
    assert any(a["id"] == agent_id and a["access"] == "view"
               for a in bob_list.json()["agents"])
    return agent_id


def test_revoke_is_immediate(client, idp, admin_conn):
    """AC-GOV-043: revocar → siguiente access/invoke 404 + audit.

    Se usa a Bob (dept_admin de A2, sin grant y sin derechos de manage sobre
    un agente del depto A1): para él la revocación es efectiva al instante.
    Un org_admin seguiría viendo el agente (derecho de manage)."""
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol, "revoke-agent")
    bob = mint(client, idp, "bob")
    grant = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_BOB, "permission": "view"},
        headers=bearer_headers(carol),
    )
    assert grant.status_code == 201
    grant_id = grant.json()["id"]
    assert client.get(f"/api/agents/{agent_id}",
                      headers=bearer_headers(bob)).status_code == 200
    revoked = client.delete(f"/api/agents/{agent_id}/grants/{grant_id}",
                            headers=bearer_headers(carol))
    assert revoked.status_code == 204
    assert client.get(f"/api/agents/{agent_id}",
                      headers=bearer_headers(bob)).status_code == 404
    assert client.post(f"/api/agents/{agent_id}/invoke",
                       headers=bearer_headers(bob)).status_code == 404
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'agent.revoked'"
        ).fetchone()
    assert row[0] >= 1


def test_invoke_billed_to_invoker(client, idp, admin_conn):
    """AC-GOV-044: invoke autorizado → 200 con billed_to = invocador + audit."""
    carol = mint(client, idp, "carol")
    agent_id = _publish(client, carol, "invoke-agent")
    alice = mint(client, idp, "alice")
    grant = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_ALICE, "permission": "invoke"},
        headers=bearer_headers(carol),
    )
    assert grant.status_code == 201
    invoked = client.post(f"/api/agents/{agent_id}/invoke",
                          headers=bearer_headers(alice))
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["billed_to_user_id"] == USER_ALICE
    # El dueño también puede invocar (own).
    own = client.post(f"/api/agents/{agent_id}/invoke",
                      headers=bearer_headers(carol))
    assert own.status_code == 200
    assert own.json()["billed_to_user_id"] == USER_CAROL
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'agent.invoked'"
        ).fetchone()
    assert row[0] >= 2

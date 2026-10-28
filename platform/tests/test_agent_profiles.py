"""Spec 020 — perfiles de agente default gobernados por policy (t_0ed22c94).

AC-020-01…06 contra PostgreSQL vivo (mismo patrón de test_agents.py):
mint real por la API, RLS, audit verificado con SQL directo.

Catalog-agnóstico: los nombres de modelos se DERIVAN del seed (model_access
+ catalog.model_catalog) en vez de hardcodearse — el catálogo global fue
reconstruido con los modelos reales de MaaS en paralelo (spec 018,
0009_model_catalog_maas) y este test no debe romperse por un rename.
"""
from datetime import datetime, timedelta, timezone

from conftest import (
    DEPT_A1, TENANT_A, TENANT_B, TEST_DB, USER_ALICE, USER_BOB, USER_CAROL,
    _admin, bearer_headers, mint,
)


def _approved_models(c, *, level, tenant=TENANT_A, dept=None, user=None):
    """Approved model names at one level for one scope (from the seed)."""
    rows = c.execute(
        "SELECT mc.model_name FROM model_access ma "
        "JOIN catalog.model_catalog mc ON mc.id = ma.model_id "
        "WHERE ma.level = %s AND ma.status = 'approved' AND ma.tenant_id = %s "
        "AND (%s::uuid IS NULL OR ma.department_id = %s::uuid) "
        "AND (%s::uuid IS NULL OR ma.user_id = %s::uuid)",
        (level, tenant, dept, dept, user, user),
    ).fetchall()
    return sorted(r[0] for r in rows)


def _carol_models(admin_conn):
    """(approved_for_carol, not_approved_for_carol) del seed de tenant A.

    approved = intersección sembrada org∩dept∩user (el modelo con fila de
    nivel user para Carol). not_approved = un modelo aprobado a nivel org
    pero EXCLUIDO por el estrechamiento de depto (deny-overrides real); si
    el seed no estrechara, cae al primer modelo del catálogo fuera del set
    efectivo.
    """
    with admin_conn.cursor() as cur:
        user_level = _approved_models(cur, level="user", user=USER_CAROL)
        org = _approved_models(cur, level="org")
        dept = _approved_models(cur, level="dept", dept=DEPT_A1)
    approved = user_level[0]  # el seed siembra exactamente una fila user
    narrowed_out = [m for m in org if m not in dept]
    if narrowed_out:
        return approved, narrowed_out[0]
    catalog = admin_conn.execute(
        "SELECT model_name FROM catalog.model_catalog ORDER BY model_name"
    ).fetchall()
    not_approved = next(m for (m,) in catalog if m != approved)
    return approved, not_approved


def _adopt(client, tokens, key="analyst"):
    return client.post(f"/api/agent-profiles/{key}/adopt",
                       headers=bearer_headers(tokens))


def _fresh_adopt(client, tokens):
    """Adopt the first profile not yet adopted in the tenant (the session
    DB is shared across tests and adoption is unique per tenant+profile)."""
    for key in ("analyst", "coder", "ops", "support"):
        resp = _adopt(client, tokens, key)
        if resp.status_code == 201:
            return resp.json()
    raise AssertionError("no default profile left to adopt in this tenant")


def test_profiles_listed_with_caller_effective_policy(client, idp, admin_conn):
    """AC-020-01: catálogo visible + policy EFECTIVA del caller (real:
    intersección de model_access + policy de conectores + budget)."""
    carol = mint(client, idp, "carol")
    resp = client.get("/api/agent-profiles", headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    keys = {p["key"] for p in data["profiles"]}
    assert keys == {"analyst", "coder", "ops", "support"}
    approved, _ = _carol_models(admin_conn)
    eff = data["effective_policy"]
    # Carol (dept A1): org ∩ dept A1 ∩ user (deny-overrides del seed).
    assert eff["allowed_models"] == [approved]
    # Conectores: org [vmware, email] ∩ dept A1 [vmware] (deny-overrides).
    assert eff["allowed_connectors"] == ["vmware"]
    assert eff["budget_cutoff"] is False
    # Aislamiento: Dave (tenant B) ve SU policy, no la de A.
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    dave_data = client.get(
        "/api/agent-profiles", headers=bearer_headers(dave)).json()
    dave_eff = dave_data["effective_policy"]
    with admin_conn.cursor() as cur:
        dave_org = _approved_models(cur, level="org", tenant=TENANT_B)
    assert dave_eff["allowed_models"] == dave_org
    assert dave_eff["allowed_connectors"] == []  # B no define policy → floor
    assert dave_eff["allowed_connectors"] != eff["allowed_connectors"]


def test_adopt_creates_tenant_instance_with_audit(client, idp, admin_conn):
    """AC-020-02: adopt → instancia en agents con profile_key, owner =
    adoptante, visible como 'own'; audit agent.profile_adopted; doble
    adopción (aunque la intente OTRO usuario del tenant) → 422."""
    carol = mint(client, idp, "carol")
    resp = _adopt(client, carol)
    assert resp.status_code == 201, resp.text
    adopted = resp.json()
    assert adopted["profile_key"] == "analyst"
    assert adopted["owner_user_id"] == USER_CAROL

    listing = client.get("/api/agents", headers=bearer_headers(carol))
    row = next(a for a in listing.json()["agents"]
               if a["id"] == adopted["agent_id"])
    assert row["access"] == "own"
    assert row["name"] == "Analyst"

    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT profile_key, owner_user_id FROM agents "
            f"WHERE id = '{adopted['agent_id']}'"
        ).fetchone()
        audited = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' "
            "AND action = 'agent.profile_adopted' "
            "AND payload->>'profile_key' = 'analyst'"
        ).fetchone()
    assert row[0] == "analyst"
    assert str(row[1]) == USER_CAROL
    assert audited[0] >= 1

    # Otro usuario del MISMO tenant no puede adoptarlo de nuevo.
    alice = mint(client, idp, "alice")
    again = _adopt(client, alice)
    assert again.status_code == 422
    assert "already adopted" in again.json()["error"]["message"]

    # Key desconocida → 404.
    assert _adopt(client, alice, "nope").status_code == 404


def test_adoption_is_isolated_per_tenant(client, idp):
    """AC-020-03: el tenant B adopta el mismo perfil → instancia propia
    DISTINTA (nada global)."""
    carol = mint(client, idp, "carol")
    a = _fresh_adopt(client, carol)
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    b = _fresh_adopt(client, dave)
    assert b["agent_id"] != a["agent_id"]
    # Dave NO ve la instancia de A (aislamiento en el listado).
    dave_agents = client.get("/api/agents",
                             headers=bearer_headers(dave)).json()["agents"]
    assert all(x["id"] != a["agent_id"] for x in dave_agents)


def test_adopted_instance_uses_explicit_grants(client, idp):
    """AC-020-04: grants como cualquier agente propio: grant invoke →
    el grantee invoca; sin grant → 404 (sin oráculo)."""
    carol = mint(client, idp, "carol")
    agent_id = _fresh_adopt(client, carol)["agent_id"]

    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    # Bob sin grant → 404.
    assert client.get(f"/api/agents/{agent_id}",
                      headers=bearer_headers(bob)).status_code == 404
    assert client.post(f"/api/agents/{agent_id}/invoke",
                       headers=bearer_headers(bob)).status_code == 404

    grant = client.post(
        f"/api/agents/{agent_id}/grants",
        json={"user_id": USER_ALICE, "permission": "invoke"},
        headers=bearer_headers(carol),
    )
    assert grant.status_code == 201, grant.text
    invoked = client.post(f"/api/agents/{agent_id}/invoke",
                          headers=bearer_headers(alice))
    assert invoked.status_code == 200
    assert invoked.json()["billed_to_user_id"] == USER_ALICE


def test_invoke_model_governed_by_policy(client, idp, admin_conn):
    """AC-020-05: modelo aprobado → 200; no aprobado → 403
    model_not_approved + audit; sin model_name → 200 (compat)."""
    carol = mint(client, idp, "carol")
    agent_id = _fresh_adopt(client, carol)["agent_id"]
    approved, not_approved = _carol_models(admin_conn)

    ok = client.post(f"/api/agents/{agent_id}/invoke",
                     json={"model_name": approved},
                     headers=bearer_headers(carol))
    assert ok.status_code == 200, ok.text
    assert ok.json()["model_name"] == approved

    denied = client.post(f"/api/agents/{agent_id}/invoke",
                         json={"model_name": not_approved},
                         headers=bearer_headers(carol))
    assert denied.status_code == 403
    assert denied.json()["error"]["reason"] == "model_not_approved"

    with _admin(TEST_DB) as c:
        denied_audit = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'model.access_denied' "
            f"AND target_id = '{not_approved}'"
        ).fetchone()
        invoked_audit = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_A}' AND action = 'agent.invoked' "
            f"AND target_id = '{agent_id}' "
            "AND payload->>'model_name' = %s", (approved,)
        ).fetchone()
    assert denied_audit[0] >= 1
    assert invoked_audit[0] >= 1

    # Compatibilidad: invoke sin body sigue funcionando (contrato actual).
    compat = client.post(f"/api/agents/{agent_id}/invoke",
                         headers=bearer_headers(carol))
    assert compat.status_code == 200
    assert "model_name" not in compat.json()


def test_invoke_denied_when_budget_cut_off(client, idp, admin_conn):
    """AC-020-06: invocador cortado por budget → 403 budget_cutoff +
    audit agent.invoke_denied (reason budget_cutoff).

    Corre en el tenant B: el A ya tiene sus 4 perfiles adoptados por los
    tests anteriores (DB de sesión compartida) y la adopción es única por
    tenant+perfil."""
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    agent_id = _fresh_adopt(client, dave)["agent_id"]

    # Presupuesto de usuario de Dave: $1; gasto del período: $1 → cutoff.
    today = datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    end = next_month - timedelta(days=1)
    from conftest import DEPT_B1, USER_DAVE
    with _admin(TEST_DB) as c:
        c.execute(
            "INSERT INTO budgets (tenant_id, level, department_id, user_id, "
            "amount_cents) VALUES "
            f"('{TENANT_B}', 'user', '{DEPT_B1}', '{USER_DAVE}', 100) "
            "ON CONFLICT DO NOTHING"
        )
        c.execute(
            "INSERT INTO budget_counters (tenant_id, level, entity_id, "
            "period_start, period_end, spent_cents, budget_cents) VALUES "
            f"('{TENANT_B}', 'user', '{USER_DAVE}', '{start}', "
            f"'{end}', 100, 100) "
            "ON CONFLICT (tenant_id, level, entity_id, period_start) "
            "DO UPDATE SET spent_cents = 100"
        )

    status = client.get("/api/budgets/me", headers=bearer_headers(dave))
    assert status.json()["cutoff"] is True

    denied = client.post(f"/api/agents/{agent_id}/invoke",
                         headers=bearer_headers(dave))
    assert denied.status_code == 403
    assert denied.json()["error"]["reason"] == "budget_cutoff"

    with _admin(TEST_DB) as c:
        audited = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{TENANT_B}' AND action = 'agent.invoke_denied' "
            f"AND target_id = '{agent_id}' "
            "AND payload->>'reason' = 'budget_cutoff'"
        ).fetchone()
    assert audited[0] >= 1

    # Limpieza (hermeticidad, t_710f0acc): el budget de usuario de Dave
    # (100 cents) y su contador son estado compartido de la suite. Sin este
    # borrado, el spend worker bloquea por corte de user budget cualquier
    # key que Dave emita después — p. ej. la de test_spend_cutoff::
    # test_org_cutoff_and_tenant_isolation, que exige 0 keys bloqueadas en
    # el tenant B tras el corte de org del tenant A.
    with _admin(TEST_DB) as c:
        c.execute(
            "DELETE FROM budgets "
            "WHERE tenant_id = %s AND level = 'user' AND user_id = %s",
            (TENANT_B, USER_DAVE),
        )
        c.execute(
            "DELETE FROM budget_counters "
            "WHERE tenant_id = %s AND level = 'user' AND entity_id = %s",
            (TENANT_B, USER_DAVE),
        )

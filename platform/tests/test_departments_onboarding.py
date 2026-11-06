"""Spec 038 — Organization structure onboarding (AC-38-001…009).

Real PostgreSQL, real API surface. Four purpose-built tenants are seeded
via SQL (the same shape conftest uses for A/B) so every test is
order-independent:

  - ob-wizard-corp   : born EMPTY, active — the full wizard journey (AC-38-004)
  - ob-skip-corp     : born EMPTY, active — the skip journey + empty
                       overview (AC-38-005/007)
  - ob-existing-corp : one department WITH a member + dept-scoped role —
                       the 409 delete guard + rename (AC-38-006)
  - ob-pending-corp  : pending_activation — the wizard is unreachable (AC-38-009)

Provisioning-level ACs (born empty, mixed declaration, self-signup) live in
test_provision.py / test_tenant_self_signup.py (they need Keycloak / the
signup double); this suite covers the catalog endpoint, the onboarding
state surface and the wizard's CRUD journey.
"""
import json
import uuid

import pytest

from tests.conftest import TENANT_A, bearer_headers, mint

SLUGS = {
    "wizard": "ob-wizard-corp",
    "skip": "ob-skip-corp",
    "existing": "ob-existing-corp",
    "pending": "ob-pending-corp",
}


def _q(admin_conn, sql, params=()):
    with admin_conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _payload(value):
    """audit_log.payload comes back as a dict (psycopg jsonb) — tolerate str."""
    return value if isinstance(value, dict) else json.loads(value)


@pytest.fixture(scope="module")
def ob(admin_conn, idp):
    """Seed the four purpose-built tenants (ids + admin subs)."""
    made = {}

    def _tenant(key: str, *, status: str = "active", with_dept: bool = False):
        slug = SLUGS[key]
        tid = str(uuid.uuid4())
        admin_uid = str(uuid.uuid4())
        sub = f"sub-ob-{key}"
        with admin_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (id, name, slug, status, oidc_realm, "
                " oidc_issuer_url) VALUES (%s, %s, %s, %s, %s, %s)",
                (tid, f"OB {key}", slug, status, slug, idp.issuer(slug)),
            )
            cur.execute(
                "INSERT INTO users (id, tenant_id, department_id, email, "
                " display_name, status, mfa_enrolled, oidc_subject) "
                "VALUES (%s, %s, NULL, %s, %s, 'active', true, %s)",
                (admin_uid, tid, f"admin@{slug}.test", f"Admin {key}", sub),
            )
            cur.execute(
                "INSERT INTO user_roles (id, tenant_id, user_id, role_key) "
                "VALUES (gen_random_uuid(), %s, %s, 'org_admin')",
                (tid, admin_uid),
            )
            if with_dept:
                dept_id = str(uuid.uuid4())
                member_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO departments (id, tenant_id, name) "
                    "VALUES (%s, %s, 'IT Operations')",
                    (dept_id, tid),
                )
                cur.execute(
                    "INSERT INTO users (id, tenant_id, department_id, email, "
                    " display_name, status, mfa_enrolled, oidc_subject) "
                    "VALUES (%s, %s, %s, %s, %s, 'active', true, %s)",
                    (member_id, tid, dept_id, f"member@{slug}.test",
                     "Member", f"sub-ob-{key}-member"),
                )
                cur.execute(
                    "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
                    " department_id) VALUES (gen_random_uuid(), %s, %s, "
                    "'dept_admin', %s)",
                    (tid, member_id, dept_id),
                )
                made[f"{key}_dept_id"] = dept_id
        made[key] = {"tenant_id": tid, "admin_user_id": admin_uid, "sub": sub}

    _tenant("wizard")
    _tenant("skip")
    _tenant("existing", with_dept=True)
    _tenant("pending", status="pending_activation")
    yield made


def _login(client, idp, sub: str, tenant_id: str, slug: str) -> dict:
    """Mint a session for one of the purpose-built tenants (mock-IdP JWT)."""
    token = idp.make_jwt(
        sub=sub, tenant_id=tenant_id, issuer=idp.issuer(slug), amr=["mfa"],
    )
    resp = client.post(
        "/api/auth/session", json={"id_token": token, "kind": "desktop"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['session_token']}"}


def _hdr(client, idp, ob, key: str) -> dict:
    t = ob[key]
    return _login(client, idp, t["sub"], t["tenant_id"], SLUGS[key])


# ---------------------------------------------------------------------------
# AC-38-001 — catalog endpoint
# ---------------------------------------------------------------------------

def test_ac38_001_profile_templates_catalog(client, idp, ob):
    headers = _hdr(client, idp, ob, "wizard")
    resp = client.get("/api/org/profile-templates", headers=headers)
    assert resp.status_code == 200, resp.text
    templates = resp.json()["templates"]
    assert len(templates) == 12
    keys = [t["key"] for t in templates]
    assert keys == sorted(keys)
    assert {"engineering", "sales", "marketing", "human_resources", "legal",
            "product", "data_analytics", "security_compliance"} <= set(keys)
    for t in templates:
        assert t["name"] and t["name_es"]
        assert t["description"] and t["description_es"]
        assert t["suggested_monthly_budget_usd"] > 0
    # Read-only: no tenant data leaks into the catalog payload.
    assert set(templates[0]) == {
        "key", "name", "name_es", "description", "description_es",
        "suggested_monthly_budget_usd",
    }
    # Without a session -> 401.
    resp = client.get("/api/org/profile-templates")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AC-38-004 — wizard journey: CRUD + close with audit
# ---------------------------------------------------------------------------

def test_ac38_004_wizard_flow(client, admin_conn, idp, ob):
    headers = _hdr(client, idp, ob, "wizard")
    tid = ob["wizard"]["tenant_id"]

    # The gate reads the flag: pending for a fresh tenant (D-38-02).
    resp = client.get("/api/org/onboarding", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["structure_onboarding_pending"] is True

    # The wizard declares the REAL structure — 2 with a template, 2 custom
    # (names in the customer's language, D-38-04), all via the plain CRUD.
    for name, tpl in (("Finanzas", "finance"), ("Marketing", "marketing"),
                      ("Ventas", None), ("Recursos Humanos", None)):
        resp = client.post(
            "/api/departments", headers=headers,
            json={"name": name, **({"profile_template": tpl} if tpl else {})},
        )
        assert resp.status_code == 201, (name, resp.text)

    # Overview lists EXACTLY those departments (LEFT JOIN, no budgets yet).
    resp = client.get("/api/budgets/overview", headers=headers)
    assert resp.status_code == 200, resp.text
    deptos = resp.json()["departments"]
    assert {d["name"] for d in deptos} == {
        "Finanzas", "Marketing", "Ventas", "Recursos Humanos"
    }
    assert all(d["budget_cents"] is None for d in deptos)

    # Close the onboarding: 4x department.created already audited by the
    # CRUD; the close emits onboarding.structure_completed {wizard, 4, 0, 0}.
    resp = client.put(
        "/api/org/onboarding", headers=headers,
        json={"source": "wizard", "created": 4, "renamed": 0, "deleted": 0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_now"] is True

    rows = _q(
        admin_conn,
        "SELECT actor_id, payload FROM audit_log "
        "WHERE tenant_id = %s AND action = 'onboarding.structure_completed'",
        (tid,),
    )
    assert len(rows) == 1
    assert str(rows[0][0]) == ob["wizard"]["admin_user_id"]
    assert _payload(rows[0][1]) == {
        "source": "wizard", "created": 4, "renamed": 0, "deleted": 0,
    }
    created = _q(
        admin_conn,
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
        "AND action = 'department.created'", (tid,),
    )
    assert created[0][0] == 4

    # The wizard never comes back: flag false, repeated PUT is a no-op
    # without a second audit event.
    resp = client.get("/api/org/onboarding", headers=headers)
    assert resp.json()["structure_onboarding_pending"] is False
    resp = client.put(
        "/api/org/onboarding", headers=headers,
        json={"source": "wizard", "created": 0, "renamed": 0, "deleted": 0},
    )
    assert resp.json()["completed_now"] is False
    assert _q(
        admin_conn,
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
        "AND action = 'onboarding.structure_completed'", (tid,),
    )[0][0] == 1


# ---------------------------------------------------------------------------
# AC-38-005 — skip: no mutations, closed with {source: skip}
# ---------------------------------------------------------------------------

def test_ac38_005_skip_flow(client, admin_conn, idp, ob):
    headers = _hdr(client, idp, ob, "skip")
    tid = ob["skip"]["tenant_id"]

    resp = client.get("/api/org/onboarding", headers=headers)
    assert resp.json()["structure_onboarding_pending"] is True

    resp = client.put(
        "/api/org/onboarding", headers=headers,
        json={"source": "skip", "created": 0, "renamed": 0, "deleted": 0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_now"] is True

    rows = _q(
        admin_conn,
        "SELECT payload FROM audit_log "
        "WHERE tenant_id = %s AND action = 'onboarding.structure_completed'",
        (tid,),
    )
    assert len(rows) == 1
    assert _payload(rows[0][0])["source"] == "skip"
    # Skip mutated NOTHING: no department events at all.
    assert _q(
        admin_conn,
        "SELECT count(*) FROM audit_log WHERE tenant_id = %s "
        "AND action LIKE 'department.%%'", (tid,),
    )[0][0] == 0

    # The wizard does not reappear on the next login.
    resp = client.get("/api/org/onboarding", headers=headers)
    assert resp.json()["structure_onboarding_pending"] is False


# ---------------------------------------------------------------------------
# AC-38-007 — org with 0 departments: overview answers, empty list
# ---------------------------------------------------------------------------

def test_ac38_007_overview_empty_org(client, idp, ob):
    headers = _hdr(client, idp, ob, "skip")  # still 0 departments
    resp = client.get("/api/budgets/overview", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["departments"] == []  # D-013-07 with the empty list


# ---------------------------------------------------------------------------
# AC-38-006 — delete guard on a non-empty department + rename
# ---------------------------------------------------------------------------

def test_ac38_006_delete_409_and_rename(client, admin_conn, idp, ob):
    headers = _hdr(client, idp, ob, "existing")
    tid = ob["existing"]["tenant_id"]
    dept_id = ob["existing_dept_id"]

    # The department has 1 member + 1 dept-scoped role: the REAL 409 with
    # counts and the way out (D-16-02), no bypass.
    resp = client.delete(f"/api/departments/{dept_id}", headers=headers)
    assert resp.status_code == 409, resp.text
    message = resp.json()["error"]["message"]
    assert "1 assigned user(s)" in message
    assert "PUT /api/users/" in message
    assert "1 department-scoped role assignment(s)" in message
    # Still there.
    assert _q(
        admin_conn,
        "SELECT count(*) FROM departments WHERE id = %s", (dept_id,),
    )[0][0] == 1

    # Rename works and audits (D-16-03).
    resp = client.put(
        f"/api/departments/{dept_id}", headers=headers,
        json={"name": "Tecnología"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Tecnología"
    rows = _q(
        admin_conn,
        "SELECT payload FROM audit_log WHERE tenant_id = %s "
        "AND action = 'department.renamed'", (tid,),
    )
    assert rows and _payload(rows[-1][0])["new_name"] == "Tecnología"


# ---------------------------------------------------------------------------
# AC-38-009 — permissions: departments.manage only; pending -> 423
# ---------------------------------------------------------------------------

def test_ac38_009_permissions_and_pending_gate(client, idp, ob):
    # dept_admin / user of an existing tenant -> 403 on BOTH verbs.
    for user in ("bob", "carol"):
        tokens = mint(client, idp, user, tenant=TENANT_A)
        resp = client.get("/api/org/onboarding", headers=bearer_headers(tokens))
        assert resp.status_code == 403, (user, resp.text)
        resp = client.put(
            "/api/org/onboarding", headers=bearer_headers(tokens),
            json={"source": "skip"},
        )
        assert resp.status_code == 403, (user, resp.text)

    # The catalog stays readable by any authenticated principal (REQ-DO-040
    # scopes only the onboarding STATE).
    tokens = mint(client, idp, "carol", tenant=TENANT_A)
    resp = client.get(
        "/api/org/profile-templates", headers=bearer_headers(tokens)
    )
    assert resp.status_code == 200

    # A pending tenant is fail-closed: the wizard is unreachable (423).
    headers = _hdr(client, idp, ob, "pending")
    resp = client.get("/api/org/onboarding", headers=headers)
    assert resp.status_code == 423, resp.text
    resp = client.put(
        "/api/org/onboarding", headers=headers, json={"source": "skip"}
    )
    assert resp.status_code == 423, resp.text


# ---------------------------------------------------------------------------
# D-38-05 unit level — declaration normalization (no KC required)
# ---------------------------------------------------------------------------

def test_resolve_department_specs_normalization():
    from app.tenants.provision import resolve_department_specs

    # None/[] -> EMPTY (D-38-01: never a seed).
    assert resolve_department_specs(None) == []
    assert resolve_department_specs([]) == []

    specs = resolve_department_specs(
        ["finance", {"name": "Finanzas", "profile_template": "finance"},
         {"name": "Ventas"}]
    )
    assert [(s.name, s.profile_template) for s in specs] == [
        ("Finance", "finance"), ("Finanzas", "finance"), ("Ventas", None),
    ]

    import pytest as _pytest
    with _pytest.raises(ValueError, match="Unknown profile template"):
        resolve_department_specs(["nope"])
    with _pytest.raises(ValueError, match="Duplicate department name"):
        resolve_department_specs([{"name": "X"}, {"name": "x"}])
    with _pytest.raises(ValueError, match="1-80"):
        resolve_department_specs([{"name": ""}])
    with _pytest.raises(ValueError, match="1-80"):
        resolve_department_specs([{"name": "x" * 81}])

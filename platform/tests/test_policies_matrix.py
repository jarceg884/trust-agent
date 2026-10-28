"""Spec 033 — matriz unificada de policies + diff efectivo por usuario.

AC-033-001 (matrix: catálogo completo, scoping dept_admin, 403 sin permisos)
AC-033-002 (effective: provenance por key, tightened, denied, 404/422)
"""
from conftest import (
    DEPT_A1,
    DEPT_A2,
    USER_CAROL,
    bearer_headers,
    mint,
)


def _put(client, path, key, value, tokens):
    return client.put(path, json={"key": key, "value": value},
                      headers=bearer_headers(tokens))


def test_matrix_org_admin_sees_everything(client, idp):
    """AC-033-001: catálogo tipado (18+29 keys) + org + deptos + usuarios
    con overrides, con nombres legibles."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/policies/matrix", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    keys = {e["key"] for e in body["catalog"]}
    assert len(keys) >= 47  # 18 clásicas + 29 de la matriz de capacidades
    entry = next(e for e in body["catalog"] if e["key"] == "approval_mode")
    assert entry["choices"] == ["manual", "smart", "off"]
    assert entry["invariable"] is False
    inv = next(e for e in body["catalog"] if e["key"] == "approved_models_only")
    assert inv["invariable"] is True

    org = {p["key"]: p["value"] for p in body["org"]}
    assert org["allowed_connectors"] == ["vmware", "email"]
    assert org["require_mfa"] is True

    depts = {d["department_id"]: d for d in body["departments"]}
    assert depts[DEPT_A1]["name"] == "IT"
    dept_policies = {
        p["key"]: p["value"] for p in depts[DEPT_A1]["policies"]
    }
    assert dept_policies["allowed_connectors"] == ["vmware"]

    users = {u["user_id"]: u for u in body["users"]}
    assert USER_CAROL in users  # seed user-level session_timeout_minutes
    assert users[USER_CAROL]["email"] == "carol@tenant-a.test"
    assert users[USER_CAROL]["department_name"] == "IT"


def test_matrix_marks_org_only_keys(client, idp):
    """Spec 033 + 015 (hallazgo t_8f716f9f): las keys org-only van
    marcadas en el catálogo para que la UI bloquee la edición dept/user;
    el diff efectivo las resuelve como el gate (fila org o default)."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/policies/matrix", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    by_key = {e["key"]: e for e in resp.json()["catalog"]}
    assert by_key["require_mfa"]["org_only"] is True
    assert by_key["allow_self_registration"]["org_only"] is True
    assert by_key["approval_mode"]["org_only"] is False

    resp = client.get(f"/api/policies/effective?user_id={USER_CAROL}",
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    diff = {k["key"]: k for k in resp.json()["keys"]}
    # org row require_mfa=true (seed spec 015) gana; provenance = org.
    assert diff["require_mfa"]["org_only"] is True
    assert diff["require_mfa"]["effective"] is True
    assert diff["require_mfa"]["source"] == "org"


def test_matrix_scoping_and_permission(client, idp):
    """AC-033-001: dept_admin solo ve sus departamentos; rol sin
    permisos de vista → 403."""
    bob = mint(client, idp, "bob")  # dept_admin scoped a A2 (Finance)
    resp = client.get("/api/policies/matrix", headers=bearer_headers(bob))
    assert resp.status_code == 200, resp.text
    ids = {d["department_id"] for d in resp.json()["departments"]}
    assert DEPT_A2 in ids
    assert DEPT_A1 not in ids
    # Usuarios con overrides fuera de su scope no aparecen (Carol es de A1).
    assert USER_CAROL not in {u["user_id"] for u in resp.json()["users"]}

    carol = mint(client, idp, "carol")  # rol user: audit.view (espejo de
    # list_policies) → matriz limitada: catálogo + baseline org, sin
    # departamentos ni usuarios.
    resp = client.get("/api/policies/matrix", headers=bearer_headers(carol))
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "dept"
    assert body["departments"] == []
    assert body["users"] == []
    assert len(body["catalog"]) >= 47


def test_effective_diff_provenance(client, idp):
    """AC-033-002: por key — valores por nivel, effective deny-overrides,
    source, tightened_by_dept/user y denied; todo computado con el
    combinator del backend."""
    alice = mint(client, idp, "alice")
    # Estado limpio para la key de prueba (otros tests comparten la DB).
    client.delete("/api/policies/org/allow_voice_mode",
                  headers=bearer_headers(alice))

    resp = client.get(f"/api/policies/effective?user_id={USER_CAROL}",
                      headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "carol@tenant-a.test"
    assert body["user"]["department_name"] == "IT"
    by_key = {k["key"]: k for k in body["keys"]}
    assert len(by_key) >= 47

    # Provenance completa con una key que ESTE test controla en los tres
    # niveles (allowed_skills: lista, default ["*"]) — el resto de la suite
    # comparte la DB y deja overrides en otras keys. El DELETE es
    # /policies/{level}/{key} con los ids por QUERY PARAM (no la forma
    # del PUT); user_id basta desde REQ-GOV-025 (el depto se resuelve
    # igual que el upsert), el department_id explícito sigue soportado.
    for path in (
        f"/api/policies/user/allowed_skills"
        f"?user_id={USER_CAROL}&department_id={DEPT_A1}",
        f"/api/policies/dept/allowed_skills?department_id={DEPT_A1}",
        "/api/policies/org/allowed_skills",
    ):
        client.delete(path, headers=bearer_headers(alice))
    assert _put(client, "/api/policies/org", "allowed_skills",
                ["hermes-agent", "obsidian", "pdf"], alice).status_code == 200
    assert _put(client, f"/api/policies/dept/{DEPT_A1}", "allowed_skills",
                ["hermes-agent", "obsidian"], alice).status_code == 200
    resp = client.get(f"/api/policies/effective?user_id={USER_CAROL}",
                      headers=bearer_headers(alice))
    skills = {k["key"]: k for k in resp.json()["keys"]}["allowed_skills"]
    assert skills["source"] == "dept"
    assert skills["effective"] == ["hermes-agent", "obsidian"]
    assert skills["tightened_by_dept"] is True
    assert skills["tightened_by_user"] is False
    assert _put(client, f"/api/policies/user/{USER_CAROL}", "allowed_skills",
                ["hermes-agent"], alice).status_code == 200
    resp = client.get(f"/api/policies/effective?user_id={USER_CAROL}",
                      headers=bearer_headers(alice))
    skills = {k["key"]: k for k in resp.json()["keys"]}["allowed_skills"]
    assert skills["source"] == "user"
    assert skills["effective"] == ["hermes-agent"]
    assert skills["tightened_by_user"] is True

    # Seed user-level: session_timeout_minutes 30 < default 60 (min gana).
    timeout = by_key["session_timeout_minutes"]
    assert timeout["user_value"] == 30
    assert timeout["source"] == "user"
    assert timeout["tightened_by_user"] is True
    assert timeout["effective"] == 30

    # Sin overrides: default del catálogo; allow_* false → denied (D-33-03).
    voice = by_key["allow_voice_mode"]
    assert voice["source"] == "default"
    assert voice["effective"] is False
    assert voice["denied"] is True

    # Override org true → source org, deja de estar negada.
    ok = _put(client, "/api/policies/org", "allow_voice_mode", True, alice)
    assert ok.status_code == 200, ok.text
    resp = client.get(f"/api/policies/effective?user_id={USER_CAROL}",
                      headers=bearer_headers(alice))
    voice = {k["key"]: k for k in resp.json()["keys"]}["allow_voice_mode"]
    assert voice["source"] == "org"
    assert voice["effective"] is True
    assert voice["denied"] is False

    # Limpieza: volver al default.
    resp = client.delete("/api/policies/org/allow_voice_mode",
                         headers=bearer_headers(alice))
    assert resp.status_code == 204
    for path in (
        f"/api/policies/user/allowed_skills"
        f"?user_id={USER_CAROL}&department_id={DEPT_A1}",
        f"/api/policies/dept/allowed_skills?department_id={DEPT_A1}",
        "/api/policies/org/allowed_skills",
    ):
        resp = client.delete(path, headers=bearer_headers(alice))
        assert resp.status_code == 204, path


def test_effective_diff_scope_and_validation(client, idp):
    """AC-033-002: dept_admin fuera de scope → 404 sin oráculo;
    user_id malformado → 422 (t_378503a1), no 500."""
    bob = mint(client, idp, "bob")  # dept_admin A2; Carol es de A1
    resp = client.get(f"/api/policies/effective?user_id={USER_CAROL}",
                      headers=bearer_headers(bob))
    assert resp.status_code == 404

    alice = mint(client, idp, "alice")
    resp = client.get("/api/policies/effective?user_id=not-a-uuid",
                      headers=bearer_headers(alice))
    assert resp.status_code == 422
    resp = client.get(
        "/api/policies/effective?user_id=00000000-0000-0000-0000-0000000000ff",
        headers=bearer_headers(alice),
    )
    assert resp.status_code == 404

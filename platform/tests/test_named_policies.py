"""Spec 041 — Políticas nombradas (preset de policy keys, visión Miller).

Cubre los ACs de backend: 001/002 (CRUD + 409), 003 (contrato del builder
1:1 + sha256), 004 (preview dry-run), 005 (asignación atómica + unassign
segura), 006 (stale + reapply), 007 (seed idempotente de plantillas),
008 (vista de destinatarios), 009 (aislamiento multi-tenant + RLS) y 011
(audit completo). REQ-041-010 (engine intacto) se verifica por review/CI:
combinator.py sin diff y policies.py solo importado.

Limpieza: cada test restaura el estado que toca (unassign → deactivate de
las keys escritas, borrado de las políticas nombradas creadas) para no
contaminar las demás suites del tenant A compartido.
"""
import hashlib
import json
import os

from sqlalchemy import text as sa_text

from app.db import get_engine, tenant_tx
from app.governance import named_policy_builder as builder_contract
from app.governance.combinator import ORG_ONLY_KEYS
from conftest import (
    DEPT_A1, DEPT_A2, PLATFORM_DIR, TENANT_A, TENANT_B, USER_ALICE,
    USER_CAROL, bearer_headers, mint,
)

SPEC_CONTRACT = os.path.join(
    PLATFORM_DIR, os.pardir, "specs", "041-named-policies",
    "named-policy-builder.json",
)


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _create(client, headers, name, definition=None, **extra):
    body = {"name": name, **extra}
    if definition is not None:
        body["definition"] = definition
    return client.post("/api/named-policies", json=body, headers=headers)


def _assign(client, headers, policy_id, level, **params):
    return client.post(
        f"/api/named-policies/{policy_id}/assign",
        json={"level": level, **params},
        headers=headers,
    )


def _unassign(client, headers, assignment_id):
    return client.delete(
        f"/api/named-policies/assignments/{assignment_id}", headers=headers
    )


def _delete_policy(client, headers, policy_id, **params):
    return client.delete(
        f"/api/named-policies/{policy_id}", params=params, headers=headers
    )


def _dept_policy_rows(admin_conn, dept_id):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT key, value FROM policies WHERE level = 'dept' "
            "AND department_id = %s AND is_active",
            (dept_id,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# AC-041-003 — contrato del builder: sha256 + cobertura 1:1
# ---------------------------------------------------------------------------

def test_builder_contract_sha256_matches_spec():
    """La copia de plataforma es byte-a-byte la del contrato canónico."""
    platform_copy = os.path.join(
        PLATFORM_DIR, "app", "governance", "named_policy_builder.json",
    )
    assert _sha256(platform_copy) == _sha256(SPEC_CONTRACT)
    assert _sha256(platform_copy) == (
        "207ab6b5b88ffd7576688687efd7904467a1dc2ad7726e7705748c744934f8c9"
    )


def test_builder_coverage_is_1_to_1(admin_conn):
    """44/44 keys no invariables alcanzables por ítem; allowed_tools ==
    known_items del catálogo legible 036; org_only == ORG_ONLY_KEYS;
    ítems sin keys sobrantes; 6 grupos; 3 plantillas."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT key, invariable FROM catalog.policy_keys ORDER BY key"
        )
        catalog = {r[0]: r[1] for r in cur.fetchall()}
    items = builder_contract.builder_items()
    item_keys = {i["key"] for i in items}
    non_invariable = {k for k, inv in catalog.items() if not inv}
    assert len(non_invariable) == 44
    # Toda key no invariable alcanzable por al menos un ítem...
    assert non_invariable <= item_keys, (
        f"keys sin ítem: {sorted(non_invariable - item_keys)}"
    )
    # ...y ningún ítem inventa keys fuera del catálogo (los invariables
    # están excluidos del sidebar).
    assert item_keys <= set(catalog)
    assert not (item_keys & {k for k, inv in catalog.items() if inv})
    # Ítems list-item de allowed_tools == known_items del catálogo legible.
    readable = json.load(open(os.path.join(
        PLATFORM_DIR, "app", "governance", "policy_catalog_readable.json",
    )))
    known = {
        i["value"]
        for i in readable["keys"]["allowed_tools"]["control"]["known_items"]
    }
    list_items = {i["item"] for i in items if i["type"] == "list-item"}
    assert list_items == known
    # org_only de los ítems == ORG_ONLY_KEYS del engine.
    org_only_items = {i["key"] for i in items if i.get("org_only")}
    assert org_only_items == set(ORG_ONLY_KEYS)
    # 6 grupos, 47 capacidades, 3 plantillas (contrato validado en specify).
    assert len(builder_contract.builder_groups()) == 6
    assert len(items) == 47
    assert len(builder_contract.builder_templates()) == 3


def test_list_exposes_builder_block(client, idp):
    """GET /api/named-policies expone el bloque builder a quien tiene
    permiso de ver policies (AC-041-003); un usuario sin permiso → 403."""
    alice = mint(client, idp, "alice")
    resp = client.get("/api/named-policies", headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["builder"]["schema_version"] == "1.0.0"
    assert len(body["builder"]["groups"]) == 6
    assert len(body["builder"]["items"]) == 47
    assert len(body["builder"]["templates"]) == 3
    carol = mint(client, idp, "carol")  # rol user: sin permisos de policies
    denied = client.get("/api/named-policies", headers=bearer_headers(carol))
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# AC-041-001 — creación y validación
# ---------------------------------------------------------------------------

def test_create_validation_and_audit(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)

    # Válida → 201 con id.
    ok = _create(client, headers, "Practicantes", {
        "allowed_tools": ["governance_status"],
        "allowed_terminal_backends": [],
        "approval_mode": "manual",
    })
    assert ok.status_code == 201, ok.text
    policy_id = ok.json()["id"]

    # Nombre duplicado → 422.
    dup = _create(client, headers, "Practicantes", {"allow_voice_mode": False})
    assert dup.status_code == 422

    # Key invariable en definition → 422 con la key en el mensaje.
    invariable = _create(client, headers, "Con piso", {
        "approved_models_only": False,
    })
    assert invariable.status_code == 422
    assert "approved_models_only" in invariable.json()["error"]["message"]

    # Valor de tipo incorrecto → 422 (MISMA validación que el upsert).
    bad_type = _create(client, headers, "Tipos", {
        "session_timeout_minutes": "abc",
    })
    assert bad_type.status_code == 422
    assert "session_timeout_minutes" in bad_type.json()["error"]["message"]

    # Fila con el tenant_id del actor + evento named_policy.created.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, payload FROM audit_log "
            "WHERE action = 'named_policy.created' "
            "AND target_id = %s", (policy_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert str(row[0]) == TENANT_A
    assert row[1]["keys"] == [
        "allowed_terminal_backends", "allowed_tools", "approval_mode",
    ]

    assert _delete_policy(client, headers, policy_id).status_code == 204


def test_create_from_template_copies_definition(client, idp):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    contract = json.load(open(SPEC_CONTRACT))
    created = []
    for tpl in contract["templates"]:
        # Cada definition de plantilla pasa la validación de REQ-041-001
        # (AC-041-007) — se crea una política desde CADA template.
        resp = _create(client, headers, f"Desde {tpl['slug']}",
                       template_slug=tpl["slug"])
        assert resp.status_code == 201, (tpl["slug"], resp.text)
        assert resp.json()["definition"] == tpl["definition"]
        assert resp.json()["source_template"] is None  # copia, no plantilla
        created.append(resp.json()["id"])
    for pid in created:
        assert _delete_policy(client, headers, pid).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-002 — edición, 409 con asignaciones, cascade, borrado directo
# ---------------------------------------------------------------------------

def test_delete_conflict_and_cascade(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    created = _create(client, headers, "Con asignación", {
        "allow_voice_mode": False,
    })
    assert created.status_code == 201
    policy_id = created.json()["id"]

    assigned = _assign(client, headers, policy_id, "dept",
                       department_id=DEPT_A2)
    assert assigned.status_code == 201, assigned.text
    assignment_id = assigned.json()["assignment_id"]

    # Borrar con 1 asignación → 409 con la lista de destinatarios.
    conflict = _delete_policy(client, headers, policy_id)
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["assignments"] == [{
        "assignment_id": assignment_id, "level": "dept",
        "department_id": DEPT_A2, "user_id": None,
    }]

    # cascade=unassign → 204, keys del snapshot desactivadas, eventos
    # named_policy.unassigned + named_policy.deleted.
    cascaded = _delete_policy(client, headers, policy_id,
                              cascade="unassign")
    assert cascaded.status_code == 204
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT is_active FROM policies WHERE level = 'dept' "
            "AND department_id = %s AND key = 'allow_voice_mode'",
            (DEPT_A2,),
        )
        rows = cur.fetchall()
        assert rows and not any(r[0] for r in rows)
        cur.execute(
            "SELECT action FROM audit_log WHERE tenant_id = %s "
            "AND target_id = %s ORDER BY seq",
            (TENANT_A, policy_id),
        )
        actions = [r[0] for r in cur.fetchall()]
    assert "named_policy.unassigned" in actions
    assert "named_policy.deleted" in actions

    # Borrar sin asignaciones → 204 directo.
    clean = _create(client, headers, "Sin asignación", {"allow_bot_mode": False})
    assert _delete_policy(client, headers, clean.json()["id"]).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-004 — preview dry-run (SIN escribir)
# ---------------------------------------------------------------------------

def test_preview_dry_run(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)

    # Un valor ya fijado a nivel depto (seed: dept A1 allowed_connectors
    # [vmware]) → overwrites: true con current_value.
    over = _create(client, headers, "Preview overwrites", {
        "allowed_connectors": ["vmware"],
    })
    assert over.status_code == 201
    resp = client.get(
        f"/api/named-policies/{over.json()['id']}/preview",
        params={"level": "dept", "department_id": DEPT_A1},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    entry = next(k for k in resp.json()["keys"]
                 if k["key"] == "allowed_connectors")
    assert entry["overwrites"] is True
    assert entry["current_value"] == ["vmware"]

    # require_mfa a un usuario → skipped_org_only (org-only, D-AUTH-07/08).
    mfa = _create(client, headers, "Preview mfa", {"require_mfa": True})
    resp = client.get(
        f"/api/named-policies/{mfa.json()['id']}/preview",
        params={"level": "user", "user_id": USER_CAROL},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["skipped_org_only"] == ["require_mfa"]
    assert resp.json()["keys"] == []  # nada que escribir a nivel usuario

    # Política que relaja la org (seed: org allowed_connectors [vmware,
    # email]) → would_relax con la key.
    relax = _create(client, headers, "Preview relaja", {
        "allowed_connectors": ["vmware", "email", "slack"],
    })
    resp = client.get(
        f"/api/named-policies/{relax.json()['id']}/preview",
        params={"level": "dept", "department_id": DEPT_A2},
        headers=headers,
    )
    assert resp.status_code == 200
    assert [w["key"] for w in resp.json()["would_relax"]] == \
        ["allowed_connectors"]

    # Preview no escribe NADA: ni policies ni named_policy_assignments.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM policies WHERE tenant_id = %s), "
            "       (SELECT count(*) FROM named_policy_assignments "
            "        WHERE tenant_id = %s)",
            (TENANT_A, TENANT_A),
        )
        before = cur.fetchone()
    for pid in (over.json()["id"], mfa.json()["id"], relax.json()["id"]):
        client.get(
            f"/api/named-policies/{pid}/preview",
            params={"level": "dept", "department_id": DEPT_A2},
            headers=headers,
        )
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM policies WHERE tenant_id = %s), "
            "       (SELECT count(*) FROM named_policy_assignments "
            "        WHERE tenant_id = %s)",
            (TENANT_A, TENANT_A),
        )
        assert cur.fetchone() == before  # dry-run de verdad

    for pid in (over.json()["id"], mfa.json()["id"], relax.json()["id"]):
        assert _delete_policy(client, headers, pid).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-005 — asignación atómica + unassign segura
# ---------------------------------------------------------------------------

def test_assign_to_dept_materializes(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    definition = {
        "allowed_tools": ["web_search", "read_file", "write_file",
                          "governance_status"],
        "allowed_terminal_backends": ["docker", "local"],
        "approval_mode": "smart",
        "allow_scheduled_tasks": True,
    }
    created = _create(client, headers, "Desarrollo A2", definition)
    assert created.status_code == 201
    policy_id = created.json()["id"]

    assigned = _assign(client, headers, policy_id, "dept",
                       department_id=DEPT_A2)
    assert assigned.status_code == 201, assigned.text
    body = assigned.json()
    assert body["applied_keys"] == definition
    assert body["skipped"] == []

    # Cada key de la definition presente en policies a nivel dept.
    rows = _dept_policy_rows(admin_conn, DEPT_A2)
    for key, value in definition.items():
        assert rows.get(key) == value, key

    # Desasignar limpio: keys del snapshot desactivadas.
    unassigned = _unassign(client, headers, body["assignment_id"])
    assert unassigned.status_code == 200, unassigned.text
    assert sorted(unassigned.json()["deactivated"]) == sorted(definition)
    assert unassigned.json()["kept_modified"] == []
    assert _dept_policy_rows(admin_conn, DEPT_A2) == {}

    assert _delete_policy(client, headers, policy_id).status_code == 204


def test_assign_relaxing_is_atomic_422(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    created = _create(client, headers, "Relajante", {
        "allow_voice_mode": False,  # esta SÍ pasaría…
        "allowed_connectors": ["vmware", "email", "slack"],  # …esta no
    })
    policy_id = created.json()["id"]
    before = _dept_policy_rows(admin_conn, DEPT_A2)

    assigned = _assign(client, headers, policy_id, "dept",
                       department_id=DEPT_A2)
    assert assigned.status_code == 422
    errors = assigned.json()["errors"]
    assert [e["key"] for e in errors] == ["allowed_connectors"]
    # Atomicidad: CERO filas nuevas en policies (ni siquiera la key que
    # habría pasado).
    assert _dept_policy_rows(admin_conn, DEPT_A2) == before
    # Y sin asignación registrada.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM named_policy_assignments "
            "WHERE named_policy_id = %s", (policy_id,),
        )
        assert cur.fetchone()[0] == 0
    assert _delete_policy(client, headers, policy_id).status_code == 204


def test_assign_to_user_skips_org_only(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    created = _create(client, headers, "Usuario con MFA", {
        "require_mfa": True,
        "max_daily_sessions": 3,
    })
    policy_id = created.json()["id"]

    assigned = _assign(client, headers, policy_id, "user", user_id=USER_CAROL)
    assert assigned.status_code == 201, assigned.text
    body = assigned.json()
    assert body["skipped"] == ["require_mfa"]
    assert body["applied_keys"] == {"max_daily_sessions": 3}
    assert body["department_id"] == DEPT_A1  # resuelto del usuario

    # Sin fila user de la key org-only; la otra sí a nivel user.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT key FROM policies WHERE level = 'user' "
            "AND user_id = %s AND is_active",
            (USER_CAROL,),
        )
        keys = {r[0] for r in cur.fetchall()}
    assert "require_mfa" not in keys
    assert "max_daily_sessions" in keys

    # Limpieza: unassign desactiva la key user escrita.
    unassigned = _unassign(client, headers, body["assignment_id"])
    assert unassigned.status_code == 200
    assert unassigned.json()["deactivated"] == ["max_daily_sessions"]
    assert _delete_policy(client, headers, policy_id).status_code == 204


def test_unassign_keeps_manually_edited_keys(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    created = _create(client, headers, "Editada a mano", {
        "approval_mode": "manual",
        "allow_voice_mode": False,
    })
    policy_id = created.json()["id"]
    assigned = _assign(client, headers, policy_id, "dept",
                       department_id=DEPT_A2)
    assert assigned.status_code == 201
    assignment_id = assigned.json()["assignment_id"]

    # Edición manual posterior a la asignación (panel 036): la key deja
    # de valer lo del snapshot.
    manual = client.put(
        f"/api/policies/dept/{DEPT_A2}",
        json={"key": "approval_mode", "value": "smart"},
        headers=headers,
    )
    assert manual.status_code == 200, manual.text

    unassigned = _unassign(client, headers, assignment_id)
    assert unassigned.status_code == 200, unassigned.text
    body = unassigned.json()
    # La key editada a mano se CONSERVA y se reporta; la otra se desactiva.
    assert body["deactivated"] == ["allow_voice_mode"]
    kept = {k["key"]: k for k in body["kept_modified"]}
    assert kept["approval_mode"]["current_value"] == "smart"
    assert kept["approval_mode"]["reason"] == "modified_after_assignment"

    rows = _dept_policy_rows(admin_conn, DEPT_A2)
    assert rows.get("approval_mode") == "smart"  # trabajo manual intacto
    assert "allow_voice_mode" not in rows

    # Limpieza: devolver el depto A2 a su estado previo (sin approval_mode).
    resp = client.delete(
        f"/api/policies/dept/approval_mode",
        params={"department_id": DEPT_A2},
        headers=headers,
    )
    assert resp.status_code == 204
    assert _delete_policy(client, headers, policy_id).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-006 — edición → stale → reapply explícito
# ---------------------------------------------------------------------------

def test_stale_and_reapply(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    created = _create(client, headers, "Reaplicable", {
        "allow_voice_mode": False,
    })
    policy_id = created.json()["id"]

    # Dos asignaciones: depto A2 y usuario Carol (depto A1).
    a_dept = _assign(client, headers, policy_id, "dept",
                     department_id=DEPT_A2).json()
    a_user = _assign(client, headers, policy_id, "user",
                     user_id=USER_CAROL).json()

    # Editar la definition → las asignaciones quedan stale (NO auto-propaga).
    edited = client.put(
        f"/api/named-policies/{policy_id}",
        json={"definition": {
            "allow_voice_mode": False,
            "allowed_connectors": ["vmware", "email"],
        }},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    listed = client.get("/api/named-policies", headers=headers).json()
    policy = next(p for p in listed["policies"] if p["id"] == policy_id)
    assert policy["stale_assignments"] is True
    assert all(a["stale"] for a in policy["assignments"])

    # Reapply: el depto A2 pasa (["vmware","email"] == org seed → no relaja
    # a nivel dept); el usuario Carol FALLA (relaja el depto A1 ["vmware"]).
    reapplied = client.post(f"/api/named-policies/{policy_id}/reapply",
                            headers=headers)
    assert reapplied.status_code == 200, reapplied.text
    results = {r["assignment_id"]: r for r in reapplied.json()["results"]}
    assert results[a_dept["assignment_id"]]["status"] == "applied"
    failed = results[a_user["assignment_id"]]
    assert failed["status"] == "failed"
    assert any("allowed_connectors" in e for e in failed["errors"])

    # La fallada NO cambió sus keys; la exitosa refrescó snapshot y ya no
    # está stale.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT applied_keys, (p.updated_at > a.applied_at) AS stale "
            "FROM named_policy_assignments a "
            "JOIN named_policies p ON p.id = a.named_policy_id "
            "WHERE a.id = %s", (a_user["assignment_id"],),
        )
        user_row = cur.fetchone()
        cur.execute(
            "SELECT applied_keys, (p.updated_at > a.applied_at) AS stale "
            "FROM named_policy_assignments a "
            "JOIN named_policies p ON p.id = a.named_policy_id "
            "WHERE a.id = %s", (a_dept["assignment_id"],),
        )
        dept_row = cur.fetchone()
    assert user_row[0] == {"allow_voice_mode": False}  # snapshot intacto
    assert user_row[1] is True  # sigue stale
    assert dept_row[0] == {
        "allow_voice_mode": False, "allowed_connectors": ["vmware", "email"],
    }
    assert dept_row[1] is False  # refrescada

    # Limpieza completa (unassign de ambos restaura el estado previo).
    for aid in (a_dept["assignment_id"], a_user["assignment_id"]):
        resp = _unassign(client, headers, aid)
        assert resp.status_code == 200, resp.text
    assert _dept_policy_rows(admin_conn, DEPT_A2) == {}
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM policies WHERE level = 'user' "
            "AND user_id = %s AND key = 'allow_voice_mode' AND is_active",
            (USER_CAROL,),
        )
        assert cur.fetchone()[0] == 0
    assert _delete_policy(client, headers, policy_id).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-007 — seed idempotente de plantillas
# ---------------------------------------------------------------------------

def test_seed_builtin_templates_idempotent(admin_conn):
    from app.governance import named_policies as service

    engine = get_engine()
    with tenant_tx(engine, TENANT_B) as conn:
        seeded = service.seed_builtin_templates(conn, TENANT_B)
        assert sorted(seeded) == [
            "desarrollador", "restringido-total", "solo-lectura-web",
        ]
        # Re-correr el seed NO duplica (idempotencia por slug).
        assert service.seed_builtin_templates(conn, TENANT_B) == []
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM named_policies "
            "WHERE tenant_id = %s AND source_template LIKE 'builtin:%%'",
            (TENANT_B,),
        )
        assert cur.fetchone()[0] == 3
        # Las definitions sembradas == las del contrato canónico.
        cur.execute(
            "SELECT source_template, definition FROM named_policies "
            "WHERE tenant_id = %s AND source_template LIKE 'builtin%%'",
            (TENANT_B,),
        )
        rows = {r[0]: r[1] for r in cur.fetchall()}
    contract = {f"builtin:{t['slug']}": t["definition"]
                for t in builder_contract.builder_templates()}
    assert rows == contract
    # Limpieza: el tenant B queda como estaba para las demás suites.
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM named_policies WHERE tenant_id = %s",
            (TENANT_B,),
        )
    admin_conn.commit()


# ---------------------------------------------------------------------------
# AC-041-008 — vista de destinatarios
# ---------------------------------------------------------------------------

def test_recipients_view(client, idp):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    p_dept = _create(client, headers, "Para depto", {
        "allow_voice_mode": False,
    }).json()
    p_user = _create(client, headers, "Para usuario", {
        "allow_bot_mode": False,
    }).json()
    a_dept = _assign(client, headers, p_dept["id"], "dept",
                     department_id=DEPT_A2).json()
    a_user = _assign(client, headers, p_user["id"], "user",
                     user_id=USER_CAROL).json()

    resp = client.get("/api/named-policies/recipients", headers=headers)
    assert resp.status_code == 200
    by_recipient = {
        (r["level"], r["department_id"], r["user_id"]): r["policies"]
        for r in resp.json()["recipients"]
    }
    dept_entry = next(
        p for p in by_recipient[("dept", DEPT_A2, None)]
        if p["named_policy_id"] == p_dept["id"]
    )
    user_entry = next(
        p for p in by_recipient[("user", DEPT_A1, USER_CAROL)]
        if p["named_policy_id"] == p_user["id"]
    )
    assert dept_entry["name"] == "Para depto"
    assert user_entry["name"] == "Para usuario"

    # Un dept_admin de A2 NO ve asignaciones ajenas (usuario Carol vive en
    # A1): su vista lista su depto y los usuarios de SU depto.
    bob = mint(client, idp, "bob")
    resp = client.get("/api/named-policies/recipients",
                      headers=bearer_headers(bob))
    assert resp.status_code == 200
    bob_recipients = resp.json()["recipients"]
    bob_keys = {(r["level"], r["department_id"], r["user_id"])
                for r in bob_recipients}
    assert ("dept", DEPT_A2, None) in bob_keys
    assert ("user", DEPT_A1, USER_CAROL) not in bob_keys
    assert ("dept", DEPT_A1, None) not in bob_keys
    assert ("org", None, None) not in bob_keys  # org es solo org_admin

    # Limpieza.
    for aid in (a_dept["assignment_id"], a_user["assignment_id"]):
        assert _unassign(client, headers, aid).status_code == 200
    for pid in (p_dept["id"], p_user["id"]):
        assert _delete_policy(client, headers, pid).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-009 — aislamiento multi-tenant (API + RLS)
# ---------------------------------------------------------------------------

def test_tenant_isolation_api_and_rls(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    mine = _create(client, headers, "Política de A", {
        "allow_voice_mode": False,
    }).json()

    # Una política del tenant B sembrada directo en la DB (sin API del A).
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO named_policies (tenant_id, name, definition) "
            "VALUES (%s, 'Política de B', %s) RETURNING id",
            (TENANT_B, json.dumps({"allow_voice_mode": True})),
        )
        b_id = str(cur.fetchone()[0])
    admin_conn.commit()

    # El admin del tenant A NO la lista, ni la edita, ni la borra, ni la
    # asigna, ni la previsualiza (404 — sin oráculo de existencia).
    listed = client.get("/api/named-policies", headers=headers).json()
    assert "Política de B" not in [p["name"] for p in listed["policies"]]
    for attempt in (
        client.put(f"/api/named-policies/{b_id}",
                   json={"name": "hack"}, headers=headers),
        client.delete(f"/api/named-policies/{b_id}", headers=headers),
        client.post(f"/api/named-policies/{b_id}/assign",
                    json={"level": "org"}, headers=headers),
        client.get(f"/api/named-policies/{b_id}/preview",
                   params={"level": "org"}, headers=headers),
    ):
        assert attempt.status_code == 404, attempt.text

    # RLS a nivel SQL: el scope del tenant B no ve la política del A y
    # viceversa (fail-closed por tenant_id).
    engine = get_engine()
    with tenant_tx(engine, TENANT_B) as conn:
        n = conn.execute(
            sa_text("SELECT count(*) FROM named_policies WHERE id = "
                    "CAST(:id AS uuid)"),
            {"id": mine["id"]},
        ).scalar()
        assert n == 0
    with tenant_tx(engine, TENANT_A) as conn:
        n = conn.execute(
            sa_text("SELECT count(*) FROM named_policies WHERE id = "
                    "CAST(:id AS uuid)"),
            {"id": b_id},
        ).scalar()
        assert n == 0

    # Limpieza.
    with admin_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM named_policies WHERE tenant_id = %s",
            (TENANT_B,),
        )
    admin_conn.commit()
    assert _delete_policy(client, headers, mine["id"]).status_code == 204


# ---------------------------------------------------------------------------
# AC-041-011 — audit completo (los 6 eventos con keys[] en payload)
# ---------------------------------------------------------------------------

def test_audit_full_flow(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    headers = bearer_headers(alice)
    policy_id = _create(client, headers, "Flujo auditado", {
        "allow_voice_mode": False,
    }).json()["id"]

    client.put(
        f"/api/named-policies/{policy_id}",
        json={"description": "editada"},
        headers=headers,
    )
    assignment_id = _assign(client, headers, policy_id, "dept",
                            department_id=DEPT_A2).json()["assignment_id"]
    client.post(f"/api/named-policies/{policy_id}/reapply", headers=headers)
    _unassign(client, headers, assignment_id)
    assert _delete_policy(client, headers, policy_id).status_code == 204

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT action, actor_id, payload FROM audit_log "
            "WHERE tenant_id = %s AND target_id = %s ORDER BY seq",
            (TENANT_A, policy_id),
        )
        events = cur.fetchall()
    actions = [e[0] for e in events]
    for expected in ("named_policy.created", "named_policy.updated",
                     "named_policy.assigned", "named_policy.reapplied",
                     "named_policy.unassigned", "named_policy.deleted"):
        assert expected in actions, expected
    # Actor y keys[] en el payload de cada evento.
    for action, actor_id, payload in events:
        assert str(actor_id) == USER_ALICE
        assert "keys" in payload and payload["keys"]
    assigned = next(e for e in events if e[0] == "named_policy.assigned")
    assert assigned[2]["level"] == "dept"
    assert assigned[2]["department_id"] == DEPT_A2

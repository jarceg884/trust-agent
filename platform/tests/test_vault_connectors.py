"""Spec 006 (vault + conectores) — ACs VC-001…015 contra PostgreSQL real.

Isolated seed: a dedicated Tenant C (org_admin Erin, users Grace and Hank) so
this suite never touches the shared conftest seed used by sibling specs.
"""
import json
import logging
import uuid

import pytest
from cryptography.exceptions import InvalidTag

from tests.conftest import (
    TEST_KEK,
    TENANT_B,
    USER_DAVE,
    bearer_headers,
    mint,
)
from tests.conftest import SUBS

# --- Tenant C seed identifiers ------------------------------------------------
TENANT_C = "33333333-3333-3333-3333-333333333333"
DEPT_C1 = "30000000-0000-0000-0000-000000000010"
USER_ERIN = "40000000-0000-0000-0000-000000000010"   # org_admin (C)
USER_GRACE = "40000000-0000-0000-0000-000000000011"  # user (C, dept C1)
USER_HANK = "40000000-0000-0000-0000-000000000012"   # user (C, dept C1)

SUBS.update({"erin": "sub-erin", "grace": "sub-grace", "hank": "sub-hank"})

CANARY = "CANARY-SECRET-7f3a9b2c41"


@pytest.fixture(scope="module")
def tenant_c(db, admin_conn):
    """Dedicated tenant with governance policies for deny-overrides tests."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_realm) "
            "VALUES (%s,'Tenant C','tenant-c','active','tenant-c')",
            (TENANT_C,),
        )
        cur.execute(
            "INSERT INTO departments (id, tenant_id, name) "
            f"VALUES (%s, '{TENANT_C}', 'Engineering')",
            (DEPT_C1,),
        )
        cur.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            " display_name, status, mfa_enrolled, oidc_subject) VALUES "
            f"(%s, '{TENANT_C}', '{DEPT_C1}', 'erin@tenant-c.test', 'Erin', "
            " 'active', true, %s), "
            f"(%s, '{TENANT_C}', '{DEPT_C1}', 'grace@tenant-c.test', 'Grace', "
            " 'active', true, %s), "
            f"(%s, '{TENANT_C}', '{DEPT_C1}', 'hank@tenant-c.test', 'Hank', "
            " 'active', true, %s)",
            (USER_ERIN, SUBS["erin"], USER_GRACE, SUBS["grace"],
             USER_HANK, SUBS["hank"]),
        )
        cur.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
            " department_id) VALUES "
            f"(gen_random_uuid(), '{TENANT_C}', '{USER_ERIN}', 'org_admin', NULL), "
            f"(gen_random_uuid(), '{TENANT_C}', '{USER_GRACE}', 'user', NULL), "
            f"(gen_random_uuid(), '{TENANT_C}', '{USER_HANK}', 'user', NULL)"
        )
        # org level: connectors + types + messaging (whatsapp reply, telegram RO)
        cur.execute(
            "INSERT INTO policies (id, tenant_id, level, key, value) VALUES "
            f"(gen_random_uuid(), '{TENANT_C}', 'org', 'allowed_connectors', %s), "
            f"(gen_random_uuid(), '{TENANT_C}', 'org', "
            " 'vault_allowed_credential_types', %s), "
            f"(gen_random_uuid(), '{TENANT_C}', 'org', "
            " 'messaging_permissions', %s)",
            (
                json.dumps(["vmware", "email", "whatsapp", "telegram"]),
                json.dumps(["api_key", "password", "oauth2", "token"]),
                json.dumps({
                    "whatsapp": {"allowed": True, "mode": "reply"},
                    "telegram": {"allowed": True, "mode": "read_only"},
                }),
            ),
        )
        # user level (deny-overrides): grace loses whatsapp; hank keeps only
        # vmware and only api_key credentials
        cur.execute(
            "INSERT INTO policies (id, tenant_id, level, department_id, "
            " user_id, key, value) VALUES "
            f"(gen_random_uuid(), '{TENANT_C}', 'user', '{DEPT_C1}', "
            f"'{USER_GRACE}', 'messaging_permissions', %s), "
            f"(gen_random_uuid(), '{TENANT_C}', 'user', '{DEPT_C1}', "
            f"'{USER_HANK}', 'allowed_connectors', %s), "
            f"(gen_random_uuid(), '{TENANT_C}', 'user', '{DEPT_C1}', "
            f"'{USER_HANK}', 'vault_allowed_credential_types', %s)",
            (
                json.dumps({"whatsapp": {"allowed": False}}),
                json.dumps(["vmware"]),
                json.dumps(["api_key"]),
            ),
        )
    yield TENANT_C


def mint_c(client, idp, user):
    """Mint a session for a Tenant C user via the mock IdP (tenant-c realm)."""
    token = idp.make_jwt(
        sub=SUBS[user], tenant_id=TENANT_C,
        issuer=idp.issuer("tenant-c"), amr=["mfa"],
    )
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_credential(client, tokens, *, name, secret=CANARY, **fields):
    payload = {"name": name, "credential_type": "api_key",
               "secret_value": secret, **fields}
    return client.post("/api/vault/credentials", json=payload,
                       headers=bearer_headers(tokens))


def ensure_instance(client, tokens, slug, credential_id=None):
    """Idempotent instance setup per test: disconnect any active instance of
    the slug for this user, then (re)connect bound to the given credential."""
    headers = bearer_headers(tokens)
    instances = client.get("/api/connectors/instances",
                           headers=headers).json()["instances"]
    for inst in instances:
        if inst["connector_slug"] == slug and inst["status"] == "active":
            assert client.delete(
                f"/api/connectors/instances/{inst['id']}",
                headers=headers).status_code == 200
    resp = client.post("/api/connectors/instances",
                       json={"connector_slug": slug,
                             "credential_id": credential_id},
                       headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def disconnect_active(client, tokens, slug):
    """Disconnect any active instance of `slug` for this user (test setup)."""
    headers = bearer_headers(tokens)
    instances = client.get("/api/connectors/instances",
                           headers=headers).json()["instances"]
    for inst in instances:
        if inst["connector_slug"] == slug and inst["status"] == "active":
            assert client.delete(
                f"/api/connectors/instances/{inst['id']}",
                headers=headers).status_code == 200


def audit_payloads(admin_conn, action):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT payload::text FROM audit_log "
            f"WHERE tenant_id = '{TENANT_C}' AND action = %s",
            (action,),
        )
        return [r[0] for r in cur.fetchall()]


# =============================================================================
# AC-VC-001 / AC-VC-002 / AC-VC-007 — cifrado, one-time, metadatos
# =============================================================================
def test_register_encrypts_and_delivers_once(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    resp = create_credential(client, grace, name="vmware-ro",
                             connector_slug="vmware", scope="read_only")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["secret_value"] == CANARY  # one-time echo (§5.5)
    cred_id = body["credential_id"]

    # AC-VC-007: reads are metadata-only
    listing = client.get("/api/vault/credentials",
                         headers=bearer_headers(grace))
    assert listing.status_code == 200
    assert CANARY not in listing.text
    detail = client.get(f"/api/vault/credentials/{cred_id}",
                        headers=bearer_headers(grace))
    assert detail.status_code == 200
    meta = detail.json()
    assert set(meta) == {"id", "user_id", "name", "credential_type",
                         "connector_slug", "scope", "status",
                         "last_rotated_at", "last_used_at", "created_at"}
    assert meta["connector_slug"] == "vmware" and meta["status"] == "active"
    assert CANARY not in detail.text

    # AC-VC-001: at rest there is only ciphertext; roundtrip with the KEK
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT ciphertext_secret, ciphertext_dek, nonce, key_id "
            "FROM vault_credentials WHERE id = %s", (cred_id,)
        )
        ct_secret, ct_dek, nonce, key_id = cur.fetchone()
    import base64

    from app.vault import crypto

    kek = crypto.load_kek(TEST_KEK)
    assert crypto.decrypt_secret(ct_secret, ct_dek, nonce, kek) == \
        CANARY.encode()
    assert key_id == "kek-v1"
    assert CANARY.encode() not in ct_secret
    wrong_kek = base64.b64encode(b"\x02" * 32).decode()
    with pytest.raises(InvalidTag):
        crypto.decrypt_secret(ct_secret, ct_dek, nonce,
                              crypto.load_kek(wrong_kek))

    # AC-VC-002: the audit payload never carries the secret
    payloads = audit_payloads(admin_conn, "vault.credential_created")
    assert payloads, "vault.credential_created event missing"
    assert all(CANARY not in p for p in payloads)


def test_canary_never_in_logs(client, idp, tenant_c, caplog):
    caplog.set_level(logging.DEBUG)
    grace = mint_c(client, idp, "grace")
    resp = create_credential(client, grace, name="vmware-logs",
                             connector_slug="vmware", scope="read_only")
    assert resp.status_code == 201
    cred_id = resp.json()["credential_id"]
    ensure_instance(client, grace, "vmware", cred_id)
    used = client.post(f"/api/vault/credentials/{cred_id}/use",
                       headers=bearer_headers(grace))
    assert used.status_code == 200
    assert CANARY not in caplog.text


# =============================================================================
# AC-VC-004 — tipos de credencial por política (deny-overrides)
# =============================================================================
def test_credential_type_governance(client, idp, tenant_c):
    hank = mint_c(client, idp, "hank")
    denied = create_credential(client, hank, name="hank-pw",
                               credential_type="password",
                               connector_slug="vmware", scope="read_only")
    assert denied.status_code == 422
    assert denied.json()["error"]["reason"] == "credential_type_not_allowed"
    ok = create_credential(client, hank, name="hank-key",
                           connector_slug="vmware", scope="read_only")
    assert ok.status_code == 201


# =============================================================================
# AC-VC-014 — scope mínimo del catálogo (invariable scoped_credentials)
# =============================================================================
def test_scope_validation(client, idp, tenant_c):
    grace = mint_c(client, idp, "grace")
    too_broad = create_credential(client, grace, name="vmware-admin",
                                  connector_slug="vmware", scope="admin")
    assert too_broad.status_code == 422
    assert too_broad.json()["error"]["reason"] == "validation_failed"
    missing = create_credential(client, grace, name="vmware-noscope",
                                connector_slug="vmware")
    assert missing.status_code == 422
    ok = create_credential(client, grace, name="vmware-ok",
                           connector_slug="vmware", scope="read_only")
    assert ok.status_code == 201
    # connector-less credentials (e.g. BYOK) do not require a scope
    byok = create_credential(client, grace, name="maas-byok")
    assert byok.status_code == 201


# =============================================================================
# AC-VC-008 — rotación one-time
# =============================================================================
def test_rotate_one_time(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    cred = create_credential(client, grace, name="vmware-rot",
                             connector_slug="vmware", scope="read_only")
    cred_id = cred.json()["credential_id"]
    ensure_instance(client, grace, "vmware", cred_id)

    rotated = client.post(f"/api/vault/credentials/{cred_id}/rotate",
                          headers=bearer_headers(grace))
    assert rotated.status_code == 200, rotated.text
    new_value = rotated.json()["secret_value"]
    assert new_value and new_value != CANARY

    listing = client.get("/api/vault/credentials",
                         headers=bearer_headers(grace))
    assert new_value not in listing.text and CANARY not in listing.text

    # the OLD value died: the injection channel now yields the NEW value
    used = client.post(f"/api/vault/credentials/{cred_id}/use",
                       headers=bearer_headers(grace))
    assert used.status_code == 200
    assert used.json()["secret_value"] == new_value

    detail = client.get(f"/api/vault/credentials/{cred_id}",
                        headers=bearer_headers(grace)).json()
    assert detail["last_rotated_at"] is not None
    assert detail["last_used_at"] is not None
    assert audit_payloads(admin_conn, "vault.credential_rotated")


# =============================================================================
# AC-VC-003 — canal de inyección runtime (propietario, auditado)
# =============================================================================
def test_use_channel_ownership(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    hank = mint_c(client, idp, "hank")
    dave = mint(client, idp, "dave", tenant=TENANT_B)  # tenant B

    cred = create_credential(client, grace, name="vmware-use",
                             connector_slug="vmware", scope="read_only")
    cred_id = cred.json()["credential_id"]

    # without an active instance the channel refuses (governance)
    disconnect_active(client, grace, "vmware")
    early = client.post(f"/api/vault/credentials/{cred_id}/use",
                        headers=bearer_headers(grace))
    assert early.status_code == 409

    ensure_instance(client, grace, "vmware", cred_id)

    used = client.post(f"/api/vault/credentials/{cred_id}/use",
                       headers=bearer_headers(grace))
    assert used.status_code == 200
    assert used.json()["secret_value"] == CANARY
    assert used.json()["connector_slug"] == "vmware"

    # another user of the SAME tenant -> 404 (owner-only, no oracle)
    assert client.post(f"/api/vault/credentials/{cred_id}/use",
                       headers=bearer_headers(hank)).status_code == 404
    # cross-tenant forged id -> 404 (RLS)
    assert client.post(f"/api/vault/credentials/{cred_id}/use",
                       headers=bearer_headers(dave)).status_code == 404

    payloads = audit_payloads(admin_conn, "vault.credential_used")
    assert payloads
    assert all(CANARY not in p for p in payloads)


# =============================================================================
# AC-VC-005 — revocación con corte inmediato de instancias
# =============================================================================
def test_revoke_cuts_instances(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    cred = create_credential(client, grace, name="vmware-revoke",
                             connector_slug="vmware", scope="read_only")
    cred_id = cred.json()["credential_id"]
    inst_id = ensure_instance(client, grace, "vmware", cred_id)["id"]

    revoked = client.post(f"/api/vault/credentials/{cred_id}/revoke",
                          headers=bearer_headers(grace))
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    instances = client.get("/api/connectors/instances",
                           headers=bearer_headers(grace)).json()["instances"]
    mine = [i for i in instances if i["id"] == inst_id]
    assert mine and mine[0]["status"] == "disconnected"

    # use after revoke -> 410 gone
    assert client.post(f"/api/vault/credentials/{cred_id}/use",
                       headers=bearer_headers(grace)).status_code == 410

    assert audit_payloads(admin_conn, "vault.credential_revoked")
    disconnected = audit_payloads(admin_conn, "connector.disconnected")
    assert any("credential_revoked" in p for p in disconnected)


def test_org_admin_visibility_and_revoke(client, idp, tenant_c):
    erin = mint_c(client, idp, "erin")
    hank = mint_c(client, idp, "hank")
    cred = create_credential(client, hank, name="hank-vm",
                             connector_slug="vmware", scope="read_only")
    cred_id = cred.json()["credential_id"]

    # org_admin sees tenant-wide metadata; a plain user does not (AC-VC-007)
    erin_list = client.get("/api/vault/credentials",
                           headers=bearer_headers(erin)).json()["credentials"]
    assert any(c["id"] == cred_id for c in erin_list)
    grace = mint_c(client, idp, "grace")
    grace_list = client.get("/api/vault/credentials",
                            headers=bearer_headers(grace)).json()["credentials"]
    assert all(c["id"] != cred_id for c in grace_list)
    assert client.get(f"/api/vault/credentials/{cred_id}",
                      headers=bearer_headers(grace)).status_code == 404

    # org_admin may revoke (governance); idempotent
    first = client.post(f"/api/vault/credentials/{cred_id}/revoke",
                        headers=bearer_headers(erin))
    assert first.status_code == 200
    again = client.post(f"/api/vault/credentials/{cred_id}/revoke",
                        headers=bearer_headers(erin))
    assert again.status_code == 200 and again.json()["status"] == "revoked"


# =============================================================================
# AC-VC-011 / AC-VC-012 — instancias, available y mensajerías
# =============================================================================
def test_available_connectors_per_policy(client, idp, tenant_c):
    grace = mint_c(client, idp, "grace")
    hank = mint_c(client, idp, "hank")
    avail = client.get("/api/connectors/available",
                       headers=bearer_headers(grace)).json()["connectors"]
    assert {c["slug"] for c in avail} == \
        {"vmware", "email", "whatsapp", "telegram"}
    whatsapp = next(c for c in avail if c["slug"] == "whatsapp")
    assert whatsapp["messaging"] == {"allowed": False, "mode": "read_only"}

    hank_avail = client.get("/api/connectors/available",
                            headers=bearer_headers(hank)).json()["connectors"]
    assert {c["slug"] for c in hank_avail} == {"vmware"}


def test_messaging_governance(client, idp, tenant_c):
    grace = mint_c(client, idp, "grace")
    erin = mint_c(client, idp, "erin")

    # user-level deny wins over org allow (deny-overrides)
    denied = client.post("/api/connectors/instances",
                         json={"connector_slug": "whatsapp", "mode": "reply"},
                         headers=bearer_headers(grace))
    assert denied.status_code == 403
    assert denied.json()["error"]["reason"] == "connector_not_allowed"

    # connector outside the allow-list entirely
    slack = client.post("/api/connectors/instances",
                        json={"connector_slug": "slack"},
                        headers=bearer_headers(grace))
    assert slack.status_code == 403

    # mode above the effective policy mode
    full = client.post("/api/connectors/instances",
                       json={"connector_slug": "telegram", "mode": "full"},
                       headers=bearer_headers(grace))
    assert full.status_code == 422
    assert full.json()["error"]["reason"] == "messaging_mode_not_allowed"

    # within policy -> created with the mode persisted
    ok = client.post("/api/connectors/instances",
                     json={"connector_slug": "telegram", "mode": "read_only"},
                     headers=bearer_headers(grace))
    assert ok.status_code == 201, ok.text
    assert ok.json()["mode"] == "read_only"

    # org_admin (no user-level deny): whatsapp at the org mode 'reply'
    erin_ok = client.post("/api/connectors/instances",
                          json={"connector_slug": "whatsapp", "mode": "reply"},
                          headers=bearer_headers(erin))
    assert erin_ok.status_code == 201
    assert erin_ok.json()["mode"] == "reply"


def test_instance_lifecycle(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    created = client.post("/api/connectors/instances",
                          json={"connector_slug": "email",
                                "config": {"imap_host": "imap.example.com",
                                           "imap_port": 993}},
                          headers=bearer_headers(grace))
    assert created.status_code == 201, created.text
    inst_id = created.json()["id"]

    # duplicate active -> 409
    dup = client.post("/api/connectors/instances",
                      json={"connector_slug": "email"},
                      headers=bearer_headers(grace))
    assert dup.status_code == 409

    # secret-looking config keys are rejected (config is non-secret)
    leaky = client.post("/api/connectors/instances",
                        json={"connector_slug": "vmware",
                              "config": {"password": "x"}},
                        headers=bearer_headers(grace))
    assert leaky.status_code == 422

    # disconnect -> disconnected + audit; idempotent; reactivation -> 200
    gone = client.delete(f"/api/connectors/instances/{inst_id}",
                         headers=bearer_headers(grace))
    assert gone.status_code == 200 and gone.json()["status"] == "disconnected"
    again = client.delete(f"/api/connectors/instances/{inst_id}",
                          headers=bearer_headers(grace))
    assert again.status_code == 200
    back = client.post("/api/connectors/instances",
                       json={"connector_slug": "email"},
                       headers=bearer_headers(grace))
    assert back.status_code == 200 and back.json()["status"] == "active"
    assert back.json()["id"] == inst_id
    assert audit_payloads(admin_conn, "connector.connected")


# =============================================================================
# AC-VC-010 — catálogo extensible por datos
# =============================================================================
def test_catalog_extensible_by_data(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO catalog.connector_catalog "
            "(slug, name, category, capabilities, required_scopes) "
            "VALUES ('sample-ext','Sample Extension','infrastructure', "
            "'[\"probe\"]','[\"read_only\"]')"
        )
    try:
        catalog = client.get("/api/connectors",
                             headers=bearer_headers(grace)).json()["connectors"]
        assert "sample-ext" in {c["slug"] for c in catalog}
    finally:
        with admin_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM catalog.connector_catalog WHERE slug = 'sample-ext'"
            )


# =============================================================================
# AC-VC-015 — aislamiento multi-tenant
# =============================================================================
def test_cross_tenant_isolation(client, idp, tenant_c):
    grace = mint_c(client, idp, "grace")
    dave = mint(client, idp, "dave", tenant=TENANT_B)  # tenant B user
    cred = create_credential(client, grace, name="vmware-iso",
                             connector_slug="vmware", scope="read_only")
    cred_id = cred.json()["credential_id"]
    inst_id = ensure_instance(client, grace, "vmware", cred_id)["id"]

    assert client.get(f"/api/vault/credentials/{cred_id}",
                      headers=bearer_headers(dave)).status_code == 404
    assert client.get(f"/api/connectors/instances/{inst_id}",
                      headers=bearer_headers(dave)).status_code == 404
    dave_creds = client.get("/api/vault/credentials",
                            headers=bearer_headers(dave)).json()["credentials"]
    dave_insts = client.get("/api/connectors/instances",
                            headers=bearer_headers(dave)).json()["instances"]
    assert all(c["id"] != cred_id for c in dave_creds)
    assert all(i["id"] != inst_id for i in dave_insts)
    # forged non-uuid id -> 404, never a 500
    assert client.get("/api/vault/credentials/not-a-uuid",
                      headers=bearer_headers(dave)).status_code == 404


# =============================================================================
# AC-VC-006 — audit de todo acceso a secretos + cadena íntegra
# =============================================================================
def test_audit_coverage_and_chain(client, idp, tenant_c, admin_conn):
    grace = mint_c(client, idp, "grace")
    cred = create_credential(client, grace, name="vmware-audit",
                             connector_slug="vmware", scope="read_only")
    cred_id = cred.json()["credential_id"]
    ensure_instance(client, grace, "vmware", cred_id)
    client.post(f"/api/vault/credentials/{cred_id}/use",
                headers=bearer_headers(grace))
    client.post(f"/api/vault/credentials/{cred_id}/rotate",
                headers=bearer_headers(grace))
    client.post(f"/api/vault/credentials/{cred_id}/revoke",
                headers=bearer_headers(grace))

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT action, actor_id FROM audit_log "
            f"WHERE tenant_id = '{TENANT_C}' AND target_id = %s "
            "AND action LIKE 'vault.%%' ORDER BY id",
            (str(cred_id),),
        )
        events = cur.fetchall()
    actions = {a for a, _ in events}
    assert {"vault.credential_created", "vault.credential_used",
            "vault.credential_rotated", "vault.credential_revoked"} <= actions
    assert all(str(actor) == USER_GRACE for _, actor in events)

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM audit_verify_chain(%s)", (TENANT_C,)
        )
        assert cur.fetchone()[0] == "ok"

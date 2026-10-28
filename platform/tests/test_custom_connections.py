"""Spec 024 — conexiones personalizadas del vault gobernadas por policy.

Criterios de aceptación (evidencia real contra PostgreSQL efímera canónica):
  - AC-24-001: flujo completo de la tarjeta — custom creada por org_admin →
    secreto one-time → permitida en org → depto la estrecha → usuario del
    depto 403 → usuario de otro depto 200 (audit connection.used) →
    removida de la org → 403 + sweep corta instancias de catálogo
    (connector.disconnected reason=policy_updated) → revocada → 410 →
    token revocado en policy 422 → tipo de credencial no permitido 422 →
    CRUD de no-org_admin 403 → token desconocido en policy 422.
  - AC-24-002: one-time + higiene de metadata (list/get jamás exponen
    secreto ni ciphertext; validación por kind; anti-secret-keys).
  - AC-24-003: audit connection.created/updated/revoked/used sin secreto +
    cadena íntegra (verify ok).
  - AC-24-004: RLS — tenant B no ve las conexiones de tenant D (API y SQL
    como trust_app sujeto a RLS).
  - AC-24-005: bundle JWS con claim aditivo allowed_connections; un bundle
    firmado ANTES del cambio de policy sigue verificando (§5.2 intacto).

Seed aislado: Tenant D dedicado (org_admin Dana, dept_admin Kate, usuarios
Ivy/Jack en deptos distintos) — nunca toca el seed compartido del conftest.
"""
import json
import uuid

import jwt
import psycopg
import pytest

# Importamos del MISMO módulo-conftest que pytest carga como plugin: existe
# una segunda copia como `tests.conftest` cuyo código a nivel de módulo se
# re-ejecuta (regenerando el keypair del bundle) y rompería la verificación
# de firma (POLICY_JWS_PUBLIC_PEM distinta a la que firmó la app).
from conftest import (
    POLICY_JWS_PUBLIC_PEM,
    SUBS,
    TENANT_B,
    TEST_DB,
    bearer_headers,
    mint,
)

# --- Tenant D seed identifiers ------------------------------------------------
TENANT_D = "44444444-4444-4444-4444-444444444444"
DEPT_D1 = "30000000-0000-0000-0000-000000000020"   # Data
DEPT_D2 = "30000000-0000-0000-0000-000000000021"   # Ops
USER_DANA = "40000000-0000-0000-0000-000000000020"  # org_admin (D)
USER_KATE = "40000000-0000-0000-0000-000000000021"  # dept_admin (D, dept D1)
USER_IVY = "40000000-0000-0000-0000-000000000022"   # user (D, dept D1)
USER_JACK = "40000000-0000-0000-0000-000000000023"  # user (D, dept D2)
DEVICE_IVY = "50000000-0000-0000-0000-000000000010"
DEVICE_JACK = "50000000-0000-0000-0000-000000000011"

SUBS.update({
    "dana": "sub-dana-orgadmin",
    "kate": "sub-kate-deptadmin",
    "ivy": "sub-ivy-user",
    "jack": "sub-jack-user",
})

DB_SECRET = "PG-ROOT-PASSWORD-4d91c7e2"
API_SECRET = "AKIA-PAYMENTS-KEY-77f3"
ROTATED_SECRET = "PG-ROOT-PASSWORD-ROTATED-0a5b"

# Claims obligatorios §5.2 de la spec 019 — el claim nuevo es ADITIVO.
REQUIRED_CLAIMS = (
    "iss", "aud", "exp", "iat", "jti", "tenant_id", "user_id", "device_id",
    "policy_version", "allowed_tools", "allowed_connectors",
    "approved_models", "budget", "offline_grace_minutes", "unbind_mode",
    "attestation_overdue_hours",
)

APP_DSN = (
    "host=127.0.0.1 port=55432 user=trust_app password=trust_app_dev "
    f"dbname={TEST_DB}"
)


@pytest.fixture(scope="module")
def tenant_d(db, admin_conn):
    """Tenant D dedicado: dos deptos, org_admin + dept_admin + 2 usuarios,
    dispositivos activos (para el bundle) y conectores de catálogo base."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug, status, oidc_realm) "
            "VALUES (%s,'Tenant D','tenant-d','active','tenant-d')",
            (TENANT_D,),
        )
        cur.execute(
            "INSERT INTO departments (id, tenant_id, name) VALUES "
            f"(%s, '{TENANT_D}', 'Data'), (%s, '{TENANT_D}', 'Ops')",
            (DEPT_D1, DEPT_D2),
        )
        cur.execute(
            "INSERT INTO users (id, tenant_id, department_id, email, "
            " display_name, status, mfa_enrolled, oidc_subject) VALUES "
            f"(%s, '{TENANT_D}', '{DEPT_D1}', 'dana@tenant-d.test', 'Dana', "
            " 'active', true, %s), "
            f"(%s, '{TENANT_D}', '{DEPT_D1}', 'kate@tenant-d.test', 'Kate', "
            " 'active', true, %s), "
            f"(%s, '{TENANT_D}', '{DEPT_D1}', 'ivy@tenant-d.test', 'Ivy', "
            " 'active', true, %s), "
            f"(%s, '{TENANT_D}', '{DEPT_D2}', 'jack@tenant-d.test', 'Jack', "
            " 'active', true, %s)",
            (USER_DANA, SUBS["dana"], USER_KATE, SUBS["kate"],
             USER_IVY, SUBS["ivy"], USER_JACK, SUBS["jack"]),
        )
        cur.execute(
            "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
            " department_id) VALUES "
            f"(gen_random_uuid(), '{TENANT_D}', '{USER_DANA}', 'org_admin', NULL), "
            f"(gen_random_uuid(), '{TENANT_D}', '{USER_KATE}', 'dept_admin', '{DEPT_D1}'), "
            f"(gen_random_uuid(), '{TENANT_D}', '{USER_IVY}', 'user', NULL), "
            f"(gen_random_uuid(), '{TENANT_D}', '{USER_JACK}', 'user', NULL)"
        )
        cur.execute(
            "INSERT INTO devices (id, tenant_id, user_id, fingerprint, status) VALUES "
            f"(%s, '{TENANT_D}', '{USER_IVY}', 'fp-ivy-d', 'active'), "
            f"(%s, '{TENANT_D}', '{USER_JACK}', 'fp-jack-d', 'active')",
            (DEVICE_IVY, DEVICE_JACK),
        )
        # Catálogo base del tenant: vmware + email (para instancias/sweep).
        cur.execute(
            "INSERT INTO policies (id, tenant_id, level, key, value) VALUES "
            f"(gen_random_uuid(), '{TENANT_D}', 'org', 'allowed_connectors', %s)",
            (json.dumps(["vmware", "email"]),),
        )
    yield TENANT_D


# --- Helpers -------------------------------------------------------------------

def mint_d(client, idp, user):
    """Sesión MFA para un usuario del Tenant D vía el IdP mock (realm d)."""
    token = idp.make_jwt(
        sub=SUBS[user], tenant_id=TENANT_D,
        issuer=idp.issuer("tenant-d"), amr=["mfa"],
    )
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _put_policy(client, tokens, path, key, value):
    return client.put(path, json={"key": key, "value": value},
                      headers=bearer_headers(tokens))


def _delete_policy(client, tokens, level, key, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.delete(f"/api/policies/{level}/{key}?{query}",
                         headers=bearer_headers(tokens))


def _create_conn(client, tokens, *, name, kind, metadata, secret):
    return client.post("/api/vault/connections",
                       json={"name": name, "kind": kind,
                             "metadata": metadata, "secret_value": secret},
                       headers=bearer_headers(tokens))


def _use_conn(client, tokens, connection_id):
    return client.post(f"/api/vault/connections/{connection_id}/use",
                       headers=bearer_headers(tokens))


def _device_token(device_id, user_id):
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    return issue_device_token(
        get_settings(), device_id=device_id, user_id=user_id,
        tenant_id=TENANT_D,
    )


def _get_bundle(client, tokens, *, device_id, user_id):
    headers = bearer_headers(tokens)
    headers["X-Device-Token"] = _device_token(device_id, user_id)
    return client.get("/api/me/governance-bundle", headers=headers)


def _instance_status(admin_conn, user_id, slug):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM connector_instances "
            f"WHERE tenant_id = '{TENANT_D}' AND user_id = %s "
            "AND connector_slug = %s",
            (user_id, slug),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _audit_rows(admin_conn, action, target_id=None):
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM audit_log "
            f"WHERE tenant_id = '{TENANT_D}' AND action = %s "
            + ("AND target_id = %s" if target_id else ""),
            (action, target_id) if target_id else (action,),
        )
        return cur.fetchall()


# =============================================================================
# AC-24-001 — flujo completo de la tarjeta (policy 3 niveles + sweep + 410)
# =============================================================================
def test_ac24_001_full_policy_flow(client, idp, admin_conn, tenant_d):
    dana = mint_d(client, idp, "dana")
    kate = mint_d(client, idp, "kate")
    ivy = mint_d(client, idp, "ivy")
    jack = mint_d(client, idp, "jack")

    # 1) org_admin crea la conexión personalizada → 201, secreto one-time.
    resp = _create_conn(
        client, dana, name="Analytics DB", kind="database",
        metadata={"type": "postgresql", "host": "db.internal",
                  "port": 5432, "database": "analytics",
                  "username": "reporter"},
        secret=DB_SECRET,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    cid = created["connection_id"]
    assert created["secret_value"] == DB_SECRET  # EXACTAMENTE una vez
    assert created["metadata"]["kind"] == "database"
    assert created["metadata"]["status"] == "active"

    # 2) Permitida en org: allowed_connections = ["*", id] (D-24-03).
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", ["*", cid])
    assert resp.status_code == 200, resp.text

    # 3) El depto D1 la estrecha (la saca dejando solo el wildcard).
    resp = _put_policy(client, kate, "/api/policies/dept/" + DEPT_D1,
                       "allowed_connections", ["*"])
    assert resp.status_code == 200, resp.text

    # 4) Usuario del depto que la estrechó → 403 connector_not_allowed.
    resp = _use_conn(client, ivy, cid)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["reason"] == "connector_not_allowed"

    # 5) Usuario del depto vecino la usa → 200 + audit connection.used.
    resp = _use_conn(client, jack, cid)
    assert resp.status_code == 200, resp.text
    used = resp.json()
    assert used["secret_value"] == DB_SECRET
    assert used["kind"] == "database"
    assert _audit_rows(admin_conn, "connection.used", cid), \
        "falta audit connection.used"

    # 6) Instancias de catálogo activas para Jack (para el sweep).
    for slug in ("vmware", "email"):
        resp = client.post("/api/connectors/instances",
                           json={"connector_slug": slug},
                           headers=bearer_headers(jack))
        assert resp.status_code in (200, 201, 409), resp.text
    assert _instance_status(admin_conn, USER_JACK, "vmware") == "active"

    # 7) Bundle ANTES del cambio: claim aditivo con la custom permitida.
    resp = _get_bundle(client, jack, device_id=DEVICE_JACK, user_id=USER_JACK)
    assert resp.status_code == 200, resp.text
    old_jws = resp.json()["bundle"]
    old_payload = jwt.decode(old_jws, POLICY_JWS_PUBLIC_PEM,
                             algorithms=["RS256"], audience=DEVICE_JACK)
    assert set(REQUIRED_CLAIMS) <= set(old_payload), \
        "el claim aditivo rompió los claims obligatorios §5.2"
    claim = old_payload["allowed_connections"]
    assert claim["catalog"] == ["email", "vmware"]
    assert [c["id"] for c in claim["custom"]] == [cid]
    custom_entry = claim["custom"][0]
    assert custom_entry["kind"] == "database"
    assert custom_entry["name"] == "Analytics DB"
    assert "secret" not in json.dumps(custom_entry).lower()

    # 8) Removida de la policy org (y el wildcard con ella) → uso 403.
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", ["email"])
    assert resp.status_code == 200, resp.text
    resp = _use_conn(client, jack, cid)
    assert resp.status_code == 403, resp.text

    # 9) Sweep: la regla combinada corta la instancia de vmware de Jack
    #    (email sigue permitida). Audit connector.disconnected + notificación.
    assert _instance_status(admin_conn, USER_JACK, "vmware") == "disconnected"
    assert _instance_status(admin_conn, USER_JACK, "email") == "active"
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM audit_log "
            f"WHERE tenant_id = '{TENANT_D}' "
            "AND action = 'connector.disconnected' "
            "AND payload->>'reason' = 'policy_updated'"
        )
        assert cur.fetchall(), "falta audit connector.disconnected (sweep)"
        cur.execute(
            "SELECT 1 FROM notifications "
            f"WHERE tenant_id = '{TENANT_D}' AND user_id = '{USER_JACK}' "
            "AND type = 'connector_disconnected'"
        )
        assert cur.fetchone(), "falta notificación connector_disconnected"

    # 10) El bundle firmado ANTES del cambio SIGUE verificando (aditivo).
    reverified = jwt.decode(old_jws, POLICY_JWS_PUBLIC_PEM,
                            algorithms=["RS256"], audience=DEVICE_JACK)
    assert reverified["jti"] == old_payload["jti"]

    # 11) El bundle NUEVO ya no lleva la custom y el catálogo se estrechó.
    resp = _get_bundle(client, jack, device_id=DEVICE_JACK, user_id=USER_JACK)
    assert resp.status_code == 200, resp.text
    new_payload = jwt.decode(resp.json()["bundle"], POLICY_JWS_PUBLIC_PEM,
                             algorithms=["RS256"], audience=DEVICE_JACK)
    assert new_payload["allowed_connections"]["custom"] == []
    assert new_payload["allowed_connections"]["catalog"] == ["email"]

    # 12) Revocación → uso 410; y el id revocado ya no es un token válido.
    resp = client.post(f"/api/vault/connections/{cid}/revoke",
                       headers=bearer_headers(dana))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"
    resp = _use_conn(client, jack, cid)
    assert resp.status_code == 410, resp.text
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", [cid])
    assert resp.status_code == 422, resp.text  # token de conexión revocada

    # 13) Limpieza: restaurar el default ["*"] para las demás suites.
    _delete_policy(client, dana, "dept", "allowed_connections",
                   department_id=DEPT_D1)
    _delete_policy(client, dana, "org", "allowed_connections")


# =============================================================================
# AC-24-002 — one-time + higiene de metadata
# =============================================================================
def test_ac24_002_one_time_and_metadata(client, idp, tenant_d):
    dana = mint_d(client, idp, "dana")
    ivy = mint_d(client, idp, "ivy")

    resp = _create_conn(
        client, dana, name="Payments API", kind="api_key",
        metadata={"service": "payments", "auth": "bearer"},
        secret=API_SECRET,
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["connection_id"]
    assert resp.json()["secret_value"] == API_SECRET

    # Lecturas: metadata únicamente — jamás secreto ni ciphertext.
    resp = client.get("/api/vault/connections", headers=bearer_headers(dana))
    assert resp.status_code == 200
    body = json.dumps(resp.json())
    assert "secret_value" not in body
    assert "ciphertext" not in body
    assert API_SECRET not in body

    resp = client.get(f"/api/vault/connections/{cid}",
                      headers=bearer_headers(dana))
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == cid
    assert "secret_value" not in detail
    assert detail["metadata"]["service"] == "payments"

    # Rotación: el secreto NUEVO se muestra una vez; el viejo muere.
    resp = client.put(f"/api/vault/connections/{cid}",
                      json={"name": "Payments API",
                            "new_secret_value": ROTATED_SECRET},
                      headers=bearer_headers(dana))
    assert resp.status_code == 200, resp.text
    assert resp.json()["secret_value"] == ROTATED_SECRET
    assert resp.json()["metadata"]["last_rotated_at"]

    # Uso real con la policy org → el secreto rotado es el vigente.
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", ["*", cid])
    assert resp.status_code == 200, resp.text
    resp = _use_conn(client, ivy, cid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["secret_value"] == ROTATED_SECRET
    _delete_policy(client, dana, "org", "allowed_connections")

    # Validación por kind (REQ-024-011) y anti-secret-keys → 422.
    resp = _create_conn(client, dana, name="Bad DB", kind="database",
                        metadata={"type": "mysql", "host": "h", "port": 3306,
                                  "database": "x"},
                        secret="s")
    assert resp.status_code == 422, resp.text  # falta username
    resp = _create_conn(client, dana, name="Bad DB 2", kind="database",
                        metadata={"type": "mysql", "host": "h", "port": "x",
                                  "database": "x", "username": "u"},
                        secret="s")
    assert resp.status_code == 422, resp.text  # port no entero
    resp = _create_conn(client, dana, name="Leaky", kind="custom",
                        metadata={"api_key": "nope"}, secret="s")
    assert resp.status_code == 422, resp.text  # clave tipo-secreto en metadata
    resp = _create_conn(client, dana, name="Bad API", kind="api_key",
                        metadata={"service": "svc", "auth": "digest"},
                        secret="s")
    assert resp.status_code == 422, resp.text  # auth inválida
    resp = _create_conn(client, dana, name="Payments API", kind="custom",
                        metadata={}, secret="s")
    assert resp.status_code == 409, resp.text  # nombre duplicado


# =============================================================================
# AC-24-003 — audit sin secreto + cadena íntegra
# =============================================================================
def test_ac24_003_audit_chain(client, idp, admin_conn, tenant_d):
    dana = mint_d(client, idp, "dana")
    ivy = mint_d(client, idp, "ivy")

    resp = _create_conn(client, dana, name="Audit Probe", kind="custom",
                        metadata={"region": "la-north-2"},
                        secret="AUDIT-PROBE-SECRET")
    assert resp.status_code == 201, resp.text
    cid = resp.json()["connection_id"]

    resp = client.put(f"/api/vault/connections/{cid}",
                      json={"metadata": {"region": "la-north-2", "env": "stg"}},
                      headers=bearer_headers(dana))
    assert resp.status_code == 200, resp.text

    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", [cid])
    assert resp.status_code == 200, resp.text
    resp = _use_conn(client, ivy, cid)
    assert resp.status_code == 200, resp.text
    _delete_policy(client, dana, "org", "allowed_connections")

    resp = client.post(f"/api/vault/connections/{cid}/revoke",
                       headers=bearer_headers(dana))
    assert resp.status_code == 200, resp.text

    for action in ("connection.created", "connection.updated",
                   "connection.used", "connection.revoked"):
        rows = _audit_rows(admin_conn, action, cid)
        assert rows, f"falta audit {action}"
        dumped = json.dumps([tuple(r) for r in rows], default=str)
        assert "AUDIT-PROBE-SECRET" not in dumped, \
            f"{action} filtra el secreto en el payload"

    # Cadena de audit íntegra para el tenant (REQ-AUD-007).
    resp = client.post("/api/admin/audit/verify",
                       headers=bearer_headers(dana))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok", resp.text


# =============================================================================
# AC-24-004 — aislamiento multi-tenant (API + SQL con RLS)
# =============================================================================
def test_ac24_004_rls_isolation(client, idp, admin_conn, tenant_d):
    dana = mint_d(client, idp, "dana")
    dave = mint(client, idp, "dave", tenant=TENANT_B)

    resp = _create_conn(client, dana, name="Internal DB", kind="database",
                        metadata={"type": "postgresql", "host": "h",
                                  "port": 5432, "database": "internal",
                                  "username": "svc"},
                        secret="TENANT-D-ONLY-SECRET")
    assert resp.status_code == 201, resp.text
    cid = resp.json()["connection_id"]

    # API: el tenant B no ve nada del tenant D (sin oráculo de existencia).
    resp = client.get("/api/vault/connections", headers=bearer_headers(dave))
    assert resp.status_code == 200, resp.text
    assert resp.json()["connections"] == []
    resp = client.get(f"/api/vault/connections/{cid}",
                      headers=bearer_headers(dave))
    assert resp.status_code == 404, resp.text
    resp = _use_conn(client, dave, cid)
    assert resp.status_code == 404, resp.text

    # SQL: RLS fail-closed como trust_app (rol de aplicación, FORCE RLS).
    with psycopg.connect(APP_DSN) as app_conn:
        with app_conn.transaction():
            app_conn.execute(f"SET LOCAL app.tenant_id = '{TENANT_B}'")
            n = app_conn.execute(
                "SELECT count(*) FROM vault_connections").fetchone()[0]
            assert n == 0, "RLS filtra: tenant B ve conexiones de tenant D"
        with app_conn.transaction():
            app_conn.execute(f"SET LOCAL app.tenant_id = '{TENANT_D}'")
            rows = app_conn.execute(
                "SELECT id FROM vault_connections "
                "WHERE name = 'Internal DB'").fetchall()
            assert rows and str(rows[0][0]) == cid


# =============================================================================
# REQ-024-005/D-24-07 — validación de tokens de policy (422) y RBAC del CRUD
# =============================================================================
def test_policy_token_validation_422(client, idp, tenant_d):
    dana = mint_d(client, idp, "dana")
    kate = mint_d(client, idp, "kate")

    # Token desconocido (no es "*" ni slug del catálogo ni UUID activo).
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", ["not-a-token"])
    assert resp.status_code == 422, resp.text

    # UUID aleatorio: no es una conexión activa del tenant.
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", [str(uuid.uuid4())])
    assert resp.status_code == 422, resp.text

    # Tipo de valor: la key es 'list' — un string no pasa la validación.
    resp = _put_policy(client, dana, "/api/policies/org",
                       "allowed_connections", "email")
    assert resp.status_code == 422, resp.text

    # dept_admin no puede fijar policy de ORG (policies.org.manage).
    resp = _put_policy(client, kate, "/api/policies/org",
                       "allowed_connections", ["*"])
    assert resp.status_code == 403, resp.text


def test_rbac_crud_403(client, idp, tenant_d):
    dana = mint_d(client, idp, "dana")
    kate = mint_d(client, idp, "kate")
    ivy = mint_d(client, idp, "ivy")

    resp = _create_conn(client, dana, name="RBAC Target", kind="custom",
                        metadata={}, secret="s")
    assert resp.status_code == 201, resp.text
    cid = resp.json()["connection_id"]

    # Sin connections.manage (solo org_admin, D-24-04) → 403 en escrituras.
    for tokens in (kate, ivy):
        resp = _create_conn(client, tokens, name=f"Nope {tokens['session_token'][:8]}",
                            kind="custom", metadata={}, secret="s")
        assert resp.status_code == 403, resp.text
        resp = client.put(f"/api/vault/connections/{cid}",
                          json={"name": "Hijacked"},
                          headers=bearer_headers(tokens))
        assert resp.status_code == 403, resp.text
        resp = client.post(f"/api/vault/connections/{cid}/revoke",
                           headers=bearer_headers(tokens))
        assert resp.status_code == 403, resp.text

    # Un usuario sin la conexión en su policy efectiva tampoco la VE.
    resp = client.get("/api/vault/connections", headers=bearer_headers(ivy))
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["connections"]]
    assert "RBAC Target" not in names
    resp = client.get(f"/api/vault/connections/{cid}",
                      headers=bearer_headers(ivy))
    assert resp.status_code == 404, resp.text  # sin oráculo de existencia


# =============================================================================
# "tipo no permitido" — gate vault_allowed_credential_types (422, spec 006)
# =============================================================================
def test_credential_type_not_allowed_422(client, idp, tenant_d):
    dana = mint_d(client, idp, "dana")
    jack = mint_d(client, idp, "jack")

    # Nivel usuario estrecha los tipos de credencial a oauth2 solamente.
    resp = _put_policy(client, dana, f"/api/policies/user/{USER_JACK}",
                       "vault_allowed_credential_types", ["oauth2"])
    assert resp.status_code == 200, resp.text
    try:
        resp = client.post("/api/vault/credentials",
                           json={"name": "Jack Key", "credential_type": "api_key",
                                 "secret_value": "k"},
                           headers=bearer_headers(jack))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["reason"] == "credential_type_not_allowed"
    finally:
        _delete_policy(client, dana, "user", "vault_allowed_credential_types",
                       user_id=USER_JACK)

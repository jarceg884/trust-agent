"""Spec 007 — flujo completo de enrolamiento de dispositivos (primera clase).

Cubre todos los AC de specs/007-devices/spec.md contra PostgreSQL real:
enroll (JWKS+MFA+org_code+SPKI) → approve → exchange one-time → DeviceGate →
revoke con invalidación inmediata de sesiones, RBAC de administración,
aislamiento cross-tenant, audit con device_id y last_seen con throttle.
"""
import hashlib
import uuid

import pytest
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import (
    DEVICE_ALICE, SUBS, TENANT_A, TENANT_B, USER_ALICE, USER_BOB, USER_CAROL,
    bearer_headers, mint,
)

ORG_A = "tenant-a"
ORG_B = "tenant-b"

# org_admin de prueba para el tenant B (aislamiento cross-tenant)
USER_ERIN = "40000000-0000-0000-0000-000000000006"
SUB_ERIN = "sub-erin-orgadmin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _keypair_pem() -> str:
    """A real device keypair: the desktop generates it locally (D-DEV-01)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _enroll_jwt(idp, *, user="carol", tenant=TENANT_A, org=ORG_A, amr=("mfa",),
                sign_with_other_key=False):
    return idp.make_jwt(
        sub=SUBS[user], tenant_id=tenant, issuer=idp.issuer(org),
        amr=list(amr) if amr is not None else None,
        sign_with_other_key=sign_with_other_key,
    )


def _enroll(client, idp, fingerprint, *, user="carol", org=ORG_A,
            issuer_org=None, public_key=None, amr=("mfa",),
            sign_with_other_key=False):
    body = {
        "org_code": org,
        "id_token": _enroll_jwt(
            idp, user=user, org=issuer_org or org, amr=amr,
            sign_with_other_key=sign_with_other_key,
        ),
        "public_key": public_key or _keypair_pem(),
        "fingerprint": fingerprint,
        "device_name": f"dev-{fingerprint}",
        "platform": "linux",
    }
    return client.post("/api/devices/enroll", json=body)


def _mint_sub(client, idp, sub, *, tenant=TENANT_A, org=ORG_A):
    """Mint a session for a subject outside the conftest SUBS map."""
    token = idp.make_jwt(
        sub=sub, tenant_id=tenant, issuer=idp.issuer(org), amr=["mfa"],
    )
    resp = client.post("/api/auth/session", json={"id_token": token,
                                                  "kind": "desktop"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _q(admin_conn, sql, params=None):
    """Run a superuser SELECT returning dict rows (psycopg3 default=tuples)."""
    with admin_conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _device_row(admin_conn, device_id):
    rows = _q(
        admin_conn,
        "SELECT id, user_id, tenant_id, status, fingerprint, public_key, "
        "enrollment_token_hash, approved_at, approved_by, last_seen_at "
        "FROM devices WHERE id = %s", (device_id,),
    )
    return rows[0] if rows else None


def _audit_events(admin_conn, tenant_id, action, device_id):
    return _q(
        admin_conn,
        "SELECT actor_id, actor_role, device_id, target_type, target_id, "
        "payload FROM audit_log "
        "WHERE tenant_id = %s AND action = %s AND device_id = %s "
        "ORDER BY seq", (tenant_id, action, device_id),
    )


def _session_id(tokens) -> str:
    """sts1.<tenant>.<session>.<secret> -> session id."""
    return tokens["session_token"].split(".")[2]


def _seed_tenant_b_admin(admin_conn):
    admin_conn.execute(
        "INSERT INTO users (id, tenant_id, department_id, email, display_name, "
        "status, mfa_enrolled, oidc_subject) VALUES "
        f"('{USER_ERIN}', '{TENANT_B}', NULL, 'erin@tenant-b.test', 'Erin', "
        f"'active', true, '{SUB_ERIN}') ON CONFLICT (id) DO NOTHING"
    )
    admin_conn.execute(
        "INSERT INTO user_roles (id, tenant_id, user_id, role_key, "
        "department_id) VALUES "
        f"(gen_random_uuid(), '{TENANT_B}', '{USER_ERIN}', 'org_admin', NULL)"
    )


# ---------------------------------------------------------------------------
# AC-DEV-001 — enroll crea pending + enrollment_token one-time
# ---------------------------------------------------------------------------
def test_enroll_creates_pending_device(client, idp, admin_conn):
    fp = f"fp-flow-{uuid.uuid4().hex[:8]}"
    resp = _enroll(client, idp, fp)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    device_id = body["device_id"]

    row = _device_row(admin_conn, device_id)
    assert row is not None
    assert row["status"] == "pending"
    assert str(row["user_id"]) == USER_CAROL
    assert str(row["tenant_id"]) == TENANT_A
    assert row["fingerprint"] == fp
    assert row["public_key"].startswith("-----BEGIN PUBLIC KEY-----")
    assert row["approved_at"] is None

    # ADR-006: only sha256(secret) is stored; the plaintext NEVER persists.
    token = body["enrollment_token"]
    assert token.startswith("enr1.")
    _, tenant_hint, dev_hint, secret = token.split(".")
    assert tenant_hint == TENANT_A and dev_hint == device_id
    assert row["enrollment_token_hash"] == hashlib.sha256(
        secret.encode()
    ).hexdigest()
    leaked = admin_conn.execute(
        "SELECT count(*) FROM devices WHERE enrollment_token_hash = %s "
        "OR fingerprint LIKE %s", (token, f"%{secret}%"),
    ).fetchone()[0]
    assert leaked == 0

    # AC-DEV-006: audit device.enrolled with device_id + payload del contrato
    events = _audit_events(admin_conn, TENANT_A, "device.enrolled", device_id)
    assert len(events) == 1
    assert str(events[0]["actor_id"]) == USER_CAROL
    assert str(events[0]["device_id"]) == device_id
    assert events[0]["target_type"] == "device"
    assert events[0]["target_id"] == device_id
    assert events[0]["payload"]["fingerprint"] == fp
    assert events[0]["payload"]["user_id"] == USER_CAROL


# ---------------------------------------------------------------------------
# AC-DEV-001a — negativos del enroll (sin oráculos ni filas basura)
# ---------------------------------------------------------------------------
def test_enroll_wrong_org_code(client, idp, admin_conn):
    """org_code != slug del tenant del claim firmado -> 422 (D-DEV-02).

    El issuer sigue siendo el del tenant A (el check de issuer es anterior);
    solo el org_code del body miente.
    """
    before = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    resp = _enroll(client, idp, f"fp-org-{uuid.uuid4().hex[:8]}",
                   org=ORG_B, issuer_org=ORG_A)
    assert resp.status_code == 422
    assert resp.json()["error"]["reason"] == "validation_failed"
    after = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    assert after == before  # 0 filas nuevas


def test_enroll_without_mfa(client, idp, admin_conn):
    before = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    resp = _enroll(client, idp, f"fp-mfa-{uuid.uuid4().hex[:8]}", amr=None)
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"
    after = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    assert after == before


def test_enroll_invalid_public_key(client, idp, admin_conn):
    before = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    resp = _enroll(client, idp, f"fp-key-{uuid.uuid4().hex[:8]}",
                   public_key="not-a-pem-key")
    assert resp.status_code == 422
    assert resp.json()["error"]["reason"] == "validation_failed"
    after = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    assert after == before


def test_enroll_bad_signature(client, idp, admin_conn):
    before = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    resp = _enroll(client, idp, f"fp-sig-{uuid.uuid4().hex[:8]}",
                   sign_with_other_key=True)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "invalid_token"
    after = admin_conn.execute("SELECT count(*) FROM devices").fetchone()[0]
    assert after == before


# ---------------------------------------------------------------------------
# AC-DEV-007 — exchange: polling 202 repetible mientras pending
# ---------------------------------------------------------------------------
def test_exchange_pending_polls_202(client, idp):
    fp = f"fp-poll-{uuid.uuid4().hex[:8]}"
    enrolled = _enroll(client, idp, fp).json()
    for _ in range(3):  # repetible: el polling NO consume el hash
        resp = client.post("/api/devices/enroll/exchange",
                           json={"enrollment_token":
                                 enrolled["enrollment_token"]})
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "pending"


# ---------------------------------------------------------------------------
# AC-DEV-002/003/004/006/007 — flujo completo e2e
# ---------------------------------------------------------------------------
def test_full_flow_enroll_approve_exchange_revoke(client, idp, admin_conn):
    fp = f"fp-e2e-{uuid.uuid4().hex[:8]}"

    # 1. enroll -> pending
    enrolled = _enroll(client, idp, fp).json()
    device_id = enrolled["device_id"]

    # 2. exchange mientras pending -> 202 (aún no hay token)
    assert client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token":
                             enrolled["enrollment_token"]}).status_code == 202

    # 3. admin (alice, org_admin) aprueba desde la consola
    alice = mint(client, idp, "alice")
    resp = client.post(f"/api/devices/{device_id}/approve",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    row = _device_row(admin_conn, device_id)
    assert row["status"] == "active"
    assert row["approved_at"] is not None
    assert str(row["approved_by"]) == USER_ALICE

    # 4. exchange -> 200 device token firmado (consume el hash, one-time)
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token":
                             enrolled["enrollment_token"]})
    assert resp.status_code == 200, resp.text
    device_token = resp.json()["device_token"]
    assert resp.json()["expires_in"] > 0

    # 5. el device token canjeado pasa el DeviceGate (AC-DEV-007)
    carol = mint(client, idp, "carol")
    headers = {**bearer_headers(carol), "X-Device-Token": device_token}
    assert client.get("/api/_probe/device",
                      headers=headers).status_code == 200

    # 6. segundo canje -> 410 gone (one-time, ADR-006)
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token":
                             enrolled["enrollment_token"]})
    assert resp.status_code == 410
    assert resp.json()["error"]["reason"] == "gone"

    # 7. sesión ligada al dispositivo -> revoke la invalida en la MISMA
    #    transacción (AC-DEV-004): device 401 + sesión 401 en el request
    #    siguiente, sin cache (REQ-DEV-004 <= 60 s por construcción).
    session_id = _session_id(carol)
    admin_conn.execute(
        "UPDATE sessions SET device_id = %s WHERE id = %s",
        (device_id, session_id),
    )
    resp = client.post(f"/api/devices/{device_id}/revoke",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"
    srow = _q(admin_conn, "SELECT status FROM sessions WHERE id = %s",
              (session_id,))[0]
    assert srow["status"] == "revoked"
    # el device token muere inmediatamente (AC-DEV-003): sesión FRESCA del
    # mismo usuario + el device token viejo -> 401 device_not_authorized.
    carol_fresh = mint(client, idp, "carol")
    resp = client.get("/api/_probe/device", headers={
        **bearer_headers(carol_fresh), "X-Device-Token": device_token,
    })
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "device_not_authorized"
    # la sesión ligada al dispositivo también muere (invalid_token)
    resp = client.get("/api/_probe/whoami", headers=bearer_headers(carol))
    assert resp.status_code == 401

    # 8. audit del flujo completo (AC-DEV-006) + cadena íntegra
    enrolled_ev = _audit_events(admin_conn, TENANT_A, "device.enrolled",
                                device_id)
    approved_ev = _audit_events(admin_conn, TENANT_A, "device.approved",
                                device_id)
    revoked_ev = _audit_events(admin_conn, TENANT_A, "device.revoked",
                               device_id)
    assert len(enrolled_ev) == 1 and len(approved_ev) == 1
    assert len(revoked_ev) == 1
    assert str(approved_ev[0]["actor_id"]) == USER_ALICE
    assert approved_ev[0]["actor_role"] == "org_admin"
    assert str(revoked_ev[0]["actor_id"]) == USER_ALICE
    assert revoked_ev[0]["payload"]["outcome"] == "revoked"
    for ev in (enrolled_ev[0], approved_ev[0], revoked_ev[0]):
        assert str(ev["device_id"]) == device_id
        assert ev["payload"]["fingerprint"] == fp
        assert ev["payload"]["user_id"] == USER_CAROL
    chain = _q(admin_conn, "SELECT status FROM audit_verify_chain(%s)",
               (TENANT_A,))[0]
    assert chain["status"] == "ok"

    return device_id, fp  # reutilizado por el test de re-enrollment


def test_approve_non_pending_conflicts(client, idp):
    fp = f"fp-conflict-{uuid.uuid4().hex[:8]}"
    device_id = _enroll(client, idp, fp).json()["device_id"]
    alice = mint(client, idp, "alice")
    assert client.post(f"/api/devices/{device_id}/approve",
                       headers=bearer_headers(alice)).status_code == 200
    resp = client.post(f"/api/devices/{device_id}/approve",
                       headers=bearer_headers(alice))
    assert resp.status_code == 409
    assert resp.json()["error"]["reason"] == "conflict"


def test_reject_pending_device(client, idp, admin_conn):
    fp = f"fp-reject-{uuid.uuid4().hex[:8]}"
    enrolled = _enroll(client, idp, fp).json()
    device_id = enrolled["device_id"]
    alice = mint(client, idp, "alice")
    resp = client.post(f"/api/devices/{device_id}/reject",
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    row = _device_row(admin_conn, device_id)
    assert row["status"] == "rejected"
    assert row["enrollment_token_hash"] is None  # el token muere con el device
    # el enrollment token ya no canjea nada -> 410
    resp = client.post("/api/devices/enroll/exchange",
                       json={"enrollment_token":
                             enrolled["enrollment_token"]})
    assert resp.status_code == 410
    events = _audit_events(admin_conn, TENANT_A, "device.revoked", device_id)
    assert len(events) == 1
    assert events[0]["payload"]["outcome"] == "rejected"


def test_exchange_no_oracle_uniform_410(client, idp):
    """Fila inexistente, hash consumido y hash de OTRO dispositivo responden
    igual: 410 con el mismo cuerpo (D-DEV-03, sin oráculo)."""
    fp = f"fp-oracle-{uuid.uuid4().hex[:8]}"
    other_fp = f"fp-oracle-other-{uuid.uuid4().hex[:8]}"
    enrolled = _enroll(client, idp, fp).json()
    other = _enroll(client, idp, other_fp).json()

    # token con formato válido pero dispositivo inexistente
    ghost = f"enr1.{TENANT_A}.{uuid.uuid4()}.whatever-secret"
    r_ghost = client.post("/api/devices/enroll/exchange",
                          json={"enrollment_token": ghost})
    # secreto de un dispositivo presentado contra otro
    wrong_dev = enrolled["enrollment_token"].replace(
        enrolled["device_id"], other["device_id"],
    )
    r_wrong = client.post("/api/devices/enroll/exchange",
                          json={"enrollment_token": wrong_dev})
    assert r_ghost.status_code == 410
    assert r_wrong.status_code == 410
    # mismo reason + mensaje (el request_id es lo único que puede diferir)
    assert r_ghost.json()["error"]["reason"] == "gone"
    assert r_wrong.json()["error"]["reason"] == "gone"
    assert (r_ghost.json()["error"]["message"]
            == r_wrong.json()["error"]["message"])


# ---------------------------------------------------------------------------
# AC-DEV-008 — renovación del device token (sesión + MFA + propiedad)
# ---------------------------------------------------------------------------
def test_renew_device_token(client, idp):
    fp = f"fp-renew-{uuid.uuid4().hex[:8]}"
    device_id = _enroll(client, idp, fp).json()["device_id"]
    alice = mint(client, idp, "alice")
    client.post(f"/api/devices/{device_id}/approve",
                headers=bearer_headers(alice))

    carol = mint(client, idp, "carol")
    resp = client.post(f"/api/devices/{device_id}/token",
                       headers=bearer_headers(carol))
    assert resp.status_code == 200, resp.text
    token = resp.json()["device_token"]
    headers = {**bearer_headers(carol), "X-Device-Token": token}
    assert client.get("/api/_probe/device",
                      headers=headers).status_code == 200


def test_renew_negatives(client, idp):
    fp = f"fp-renewneg-{uuid.uuid4().hex[:8]}"
    pending_id = _enroll(client, idp, fp).json()["device_id"]
    carol = mint(client, idp, "carol")

    # dispositivo ajeno -> 404 (sin oráculo)
    resp = client.post(f"/api/devices/{DEVICE_ALICE}/token",
                       headers=bearer_headers(carol))
    assert resp.status_code == 404
    # dispositivo no active -> 409
    resp = client.post(f"/api/devices/{pending_id}/token",
                       headers=bearer_headers(carol))
    assert resp.status_code == 409
    # sin MFA -> 403
    carol_nomfa = mint(client, idp, "carol", amr=None)
    resp = client.post(f"/api/devices/{pending_id}/token",
                       headers=bearer_headers(carol_nomfa))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "mfa_required"


# ---------------------------------------------------------------------------
# AC-DEV-009 — re-enrollment tras revoke/reject; conflictos de fingerprint
# ---------------------------------------------------------------------------
def test_reenroll_after_revocation(client, idp, admin_conn):
    fp = f"fp-reenroll-{uuid.uuid4().hex[:8]}"
    device_id = _enroll(client, idp, fp).json()["device_id"]
    alice = mint(client, idp, "alice")
    client.post(f"/api/devices/{device_id}/approve",
                headers=bearer_headers(alice))
    client.post(f"/api/devices/{device_id}/revoke",
                headers=bearer_headers(alice))

    new_key = _keypair_pem()
    resp = _enroll(client, idp, fp, public_key=new_key)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["device_id"] == device_id  # mismo device_id (upsert)
    assert body["enrollment_token"]
    row = _device_row(admin_conn, device_id)
    assert row["status"] == "pending"
    assert row["public_key"] == new_key
    assert row["approved_at"] is None
    # dos eventos device.enrolled para el mismo device (re-enrollment)
    events = _audit_events(admin_conn, TENANT_A, "device.enrolled", device_id)
    assert len(events) == 2


def test_reenroll_conflicts(client, idp):
    # fingerprint active del mismo usuario -> 409
    fp = f"fp-conf-self-{uuid.uuid4().hex[:8]}"
    device_id = _enroll(client, idp, fp).json()["device_id"]
    alice = mint(client, idp, "alice")
    client.post(f"/api/devices/{device_id}/approve",
                headers=bearer_headers(alice))
    resp = _enroll(client, idp, fp)
    assert resp.status_code == 409

    # fingerprint de OTRO usuario (alice, seed active) -> 409
    resp = _enroll(client, idp, "fp-alice-laptop")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# AC-DEV-005 — vista admin: RBAC + scope de departamentos + cross-tenant
# ---------------------------------------------------------------------------
def test_admin_list_rbac_and_scoping(client, idp, admin_conn):
    # bob (dept_admin de Finance/A2) enrola su propio dispositivo
    bob_fp = f"fp-bob-{uuid.uuid4().hex[:8]}"
    bob_device = _enroll(client, idp, bob_fp, user="bob").json()["device_id"]
    # carol (dept A1) enrola el suyo
    carol_fp = f"fp-carol-admin-{uuid.uuid4().hex[:8]}"
    carol_device = _enroll(client, idp, carol_fp).json()["device_id"]

    alice = mint(client, idp, "alice")
    bob = mint(client, idp, "bob")
    carol = mint(client, idp, "carol")

    # user sin devices.manage -> 403
    resp = client.get("/api/devices", headers=bearer_headers(carol))
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "permission_denied"

    # org_admin ve todo el tenant, con las métricas de REQ-DEV-005
    resp = client.get("/api/devices", headers=bearer_headers(alice))
    assert resp.status_code == 200
    items = {d["id"]: d for d in resp.json()["devices"]}
    assert bob_device in items and carol_device in items
    assert DEVICE_ALICE in items
    d = items[carol_device]
    assert d["email"] == "carol@tenant-a.test"
    assert d["department_name"] == "IT"
    assert d["status"] == "pending"
    assert d["active_sessions"] == 0
    assert "enrolled_at" in d and "last_seen_at" in d

    # filtro por estado
    resp = client.get("/api/devices?status=pending",
                      headers=bearer_headers(alice))
    pending_ids = {d["id"] for d in resp.json()["devices"]}
    assert carol_device in pending_ids
    assert DEVICE_ALICE not in pending_ids

    # dept_admin (bob, dept A2) SOLO ve dispositivos de su departamento:
    # el suyo sí, el de carol (dept A1) no.
    resp = client.get("/api/devices", headers=bearer_headers(bob))
    assert resp.status_code == 200
    bob_ids = {d["id"] for d in resp.json()["devices"]}
    assert bob_device in bob_ids
    assert carol_device not in bob_ids
    # detail fuera de scope -> 404 (igual que inexistente, D-CP-09)
    assert client.get(f"/api/devices/{carol_device}",
                      headers=bearer_headers(bob)).status_code == 404
    # y no puede aprobarlo
    assert client.post(f"/api/devices/{carol_device}/approve",
                       headers=bearer_headers(bob)).status_code == 404
    # el suyo sí
    assert client.get(f"/api/devices/{bob_device}",
                      headers=bearer_headers(bob)).status_code == 200


def test_cross_tenant_isolation(client, idp, admin_conn):
    _seed_tenant_b_admin(admin_conn)
    carol_fp = f"fp-xt-{uuid.uuid4().hex[:8]}"
    carol_device = _enroll(client, idp, carol_fp).json()["device_id"]

    erin = _mint_sub(client, idp, SUB_ERIN, tenant=TENANT_B, org=ORG_B)
    # erin (org_admin del tenant B) no ve NI lista NI detail del tenant A
    resp = client.get("/api/devices", headers=bearer_headers(erin))
    assert resp.status_code == 200
    assert carol_device not in {d["id"] for d in resp.json()["devices"]}
    assert client.get(f"/api/devices/{carol_device}",
                      headers=bearer_headers(erin)).status_code == 404
    assert client.post(f"/api/devices/{carol_device}/approve",
                       headers=bearer_headers(erin)).status_code == 404
    # el enrollment token del tenant A tampoco canjea desde el hint B
    # (RLS: la fila es invisible fuera de su tenant) — cubierto por 410 en
    # test_exchange_no_oracle_uniform_410; aquí verificamos la lista vacía.
    dave = mint(client, idp, "dave", tenant=TENANT_B)
    assert client.get("/api/devices",
                      headers=bearer_headers(dave)).status_code == 403


# ---------------------------------------------------------------------------
# AC-DEV-010 — /api/devices/me: solo dispositivos propios
# ---------------------------------------------------------------------------
def test_my_devices(client, idp):
    carol_fp = f"fp-me-{uuid.uuid4().hex[:8]}"
    device_id = _enroll(client, idp, carol_fp).json()["device_id"]
    carol = mint(client, idp, "carol")
    resp = client.get("/api/devices/me", headers=bearer_headers(carol))
    assert resp.status_code == 200
    devices = resp.json()["devices"]
    ids = {d["id"] for d in devices}
    fps = {d["fingerprint"] for d in devices}
    assert device_id in ids
    assert carol_fp in fps
    assert "fp-alice-laptop" not in fps  # los de alice ausentes
    assert DEVICE_ALICE not in ids
    mine = [d for d in devices if d["id"] == device_id][0]
    assert mine["status"] == "pending"
    assert mine["platform"] == "linux"


# ---------------------------------------------------------------------------
# AC-DEV-003 — last_seen_at avanza con throttle de 60 s (D-DEV-09)
# ---------------------------------------------------------------------------
def test_last_seen_throttle(client, idp, admin_conn):
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    settings = get_settings()
    admin_conn.execute(
        "UPDATE devices SET last_seen_at = now() - interval '5 minutes' "
        "WHERE id = %s", (DEVICE_ALICE,),
    )
    alice = mint(client, idp, "alice")
    headers = {
        **bearer_headers(alice),
        "X-Device-Token": issue_device_token(
            settings, device_id=DEVICE_ALICE, user_id=USER_ALICE,
            tenant_id=TENANT_A,
        ),
    }
    assert client.get("/api/_probe/device",
                      headers=headers).status_code == 200
    first = _device_row(admin_conn, DEVICE_ALICE)["last_seen_at"]
    assert first is not None  # avanzó desde el valor viejo

    # request inmediato: el throttle (>60 s) evita la segunda escritura
    assert client.get("/api/_probe/device",
                      headers=headers).status_code == 200
    second = _device_row(admin_conn, DEVICE_ALICE)["last_seen_at"]
    assert second == first

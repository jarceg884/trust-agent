"""Spec 025 — Super-user del Provider Console (mandato Miller).

Evidencia real de AC-PS-001…012 contra PostgreSQL efímero (conftest):
login usuario+contraseña, throttle persistido, required action del primer
login, MFA TOTP RFC 6238 (vectores del RFC), sesión que sobrevive a un
nuevo proceso del API (nada en memoria), audit append-only con cadena y
fail-closed sin operador seedeado.

El operador lo seedea el bootstrap del `client` fixture (create_app) con el
password inicial PINNEADO del env fixture (camino CI/test de D-PS-03).
"""
import base64
import hashlib
import re
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.provider import totp as totp_mod
from app.provider.operator import SESSION_PREFIX, ensure_provider_operator
from app.main import create_app
from tests.conftest import ADMIN_DSN, TEST_DB

USERNAME = "provider-admin"
INITIAL_PW = "ProviderInitial!2026x"
ROTATED_PW = "RotatedOperator!2026"
BREAK_GLASS = {"Authorization": "Bearer test-provider-token"}

# Vectores del RFC 6238 Apéndice B (SHA-1, semilla ASCII "12345678901234567890").
# El RFC los publica a 8 dígitos; nuestra implementación usa 6 (estándar de
# facto de los authenticators) → el código esperado son los últimos 6 dígitos.
RFC_KEY = b"12345678901234567890"
RFC_SECRET_B32 = base64.b32encode(RFC_KEY).decode().rstrip("=")
RFC_VECTORS = [
    (59, "287082"),          # 94287082
    (1111111109, "081804"),  # 07081804
    (1111111111, "050471"),  # 14050471
    (1234567890, "005924"),  # 89005924
    (2000000000, "279037"),  # 69279037
    (20000000000, "353130"),  # 65353130
]


def _sql(query, params=()):
    """Una fila (dict) de la BD de test como superusuario."""
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d.name for d in cur.description]
            row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def _sql_all(query, params=()):
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, password, totp_code=None, username=USERNAME):
    body = {"username": username, "password": password}
    if totp_code is not None:
        body["totp_code"] = totp_code
    return client.post("/api/provider/auth/login", json=body)


def _code_for(secret_b32: str) -> str:
    """Código TOTP REAL del secret, al tiempo actual (ventana ±1 del verify)."""
    key = totp_mod._decode_secret(secret_b32)
    return totp_mod.totp_at(key, int(time.time()))


def _clear_provider_throttle():
    """Limpia el contador del operador (estado de test, no dato de negocio):
    el lockout de 30 s del AC-PS-004 no debe envenenar tests posteriores."""
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ops.login_throttle WHERE key LIKE 'acct:provider:%'")


# ---------------------------------------------------------------------------
# TOTP RFC 6238 — vectores oficiales (D-PS-05)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ts,expected", RFC_VECTORS)
def test_rfc6238_vectors(ts, expected):
    assert totp_mod.totp_at(RFC_KEY, ts) == expected


def test_totp_verify_accepts_rfc_vector_and_window():
    assert totp_mod.verify(RFC_SECRET_B32, "287082", at=59) is True
    # ventana ±1 paso (±30 s) de deriva de reloj
    assert totp_mod.verify(RFC_SECRET_B32, "287082", at=59 + 30) is True
    assert totp_mod.verify(RFC_SECRET_B32, "287082", at=59 - 30) is True
    assert totp_mod.verify(RFC_SECRET_B32, "287082", at=59 + 90) is False


def test_totp_verify_fail_closed_on_malformed():
    for bad in ("", "abcdef", "12345", "1234567", "12 456", "00000x"):
        assert totp_mod.verify(RFC_SECRET_B32, bad) is False
    assert totp_mod.verify("not-base32!!", "287082") is False


def test_totp_secret_is_160_bits_base32():
    secret = totp_mod.generate_secret()
    assert len(totp_mod._decode_secret(secret)) == 20
    assert "=" not in secret
    assert secret.startswith("otpauth://totp/") is False  # es solo el secret
    url = totp_mod.otpauth_url(secret, USERNAME)
    assert url.startswith("otpauth://totp/")
    assert "SHA1" in url and "digits=6" in url and "period=30" in url


# ---------------------------------------------------------------------------
# AC-PS-001/002 — esquema en BD + seed idempotente con OTA
# ---------------------------------------------------------------------------

def test_operator_table_schema(client):
    """AC-PS-001: columnas completas, hash PHC argon2id, sin tenant_id y
    SIN RLS (plano proveedor, D-PS-01)."""
    cols = {
        r["column_name"]
        for r in _sql_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'ops' AND table_name = 'provider_operators'"
        )
    }
    assert {"username", "password_hash", "status", "must_change_password",
            "totp_secret_enc", "totp_dek_enc", "totp_nonce", "totp_key_id",
            "totp_active", "created_at", "updated_at"} <= cols
    assert "tenant_id" not in cols
    rls = _sql(
        "SELECT relrowsecurity AS rls FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'ops' AND c.relname = 'provider_operators'"
    )
    assert rls["rls"] is False
    op = _sql("SELECT password_hash, status, must_change_password "
              "FROM ops.provider_operators WHERE username = %s", (USERNAME,))
    assert op is not None
    assert re.match(r"^\$argon2id\$", op["password_hash"])
    assert op["status"] == "active"
    assert op["must_change_password"] is True


def test_bootstrap_seed_is_idempotent(client):
    """AC-PS-002: el operador ya seedeado → ensure_provider_operator NO
    genera ni devuelve ningún password nuevo (la OTA se loguea solo en la
    rama que realmente insertó)."""
    before = _sql("SELECT count(*) AS n FROM ops.provider_operators")["n"]
    assert before == 1
    from app.db import get_engine

    assert ensure_provider_operator(get_engine()) is None
    assert _sql("SELECT count(*) AS n FROM ops.provider_operators")["n"] == 1


# ---------------------------------------------------------------------------
# AC-PS-003 — login ok, sesión en PostgreSQL, audit
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def first_login(client):
    """Primer login real con el password one-time del bootstrap."""
    resp = _login(client, INITIAL_PW)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_login_ok_mints_pg_session(client, first_login):
    body = first_login
    assert body["operator"]["username"] == USERNAME
    assert body["operator"]["must_change_password"] is True
    assert body["operator"]["mfa_enrolled"] is False
    token = body["session_token"]
    assert token.startswith(SESSION_PREFIX)
    _, session_id, secret = token.split(".")
    # Solo sha256(secret) vive en BD (jti) — el secret jamás (D-PS-04).
    row = _sql("SELECT jti, status, mfa_verified FROM ops.provider_sessions "
               "WHERE id = %s", (session_id,))
    assert row is not None
    assert row["jti"] == hashlib.sha256(secret.encode()).hexdigest()
    assert row["status"] == "active"
    # Audit del plano proveedor con cadena global válida.
    events = _sql_all("SELECT action FROM ops.provider_audit "
                      "WHERE username = %s ORDER BY id", (USERNAME,))
    assert events[-1]["action"] == "provider.login"
    chain = _sql("SELECT status FROM ops.provider_audit_verify_chain()")
    assert chain["status"] == "ok"


def test_session_token_works_on_business_routes(client, first_login):
    resp = client.get("/api/admin/tenants", headers=_bearer(first_login["session_token"]))
    # must_change_password=true → 403 required action (se verifica abajo);
    # con el flag limpio esta misma llamada responde 200 (AC-PS-006).
    assert resp.status_code == 403
    assert resp.json()["error"]["reason"] == "password_change_required"


# ---------------------------------------------------------------------------
# AC-PS-006 — required action del primer login
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rotated(client, first_login):
    """Completa la required action: password nueva conocida + flag limpio."""
    token = first_login["session_token"]
    resp = client.post("/api/provider/auth/password",
                       headers=_bearer(token),
                       json={"current_password": INITIAL_PW,
                             "new_password": ROTATED_PW})
    assert resp.status_code == 200, resp.text
    return {"password": ROTATED_PW, "token": token}


def test_me_endpoint_works_during_required_action(client, first_login):
    """/auth/me es la única ruta que funciona con el flag puesto (el gate
    de la UI se apoya en ella para redirigir a /change-password)."""
    resp = client.get("/api/provider/auth/me", headers=_bearer(first_login["session_token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == USERNAME
    assert body["must_change_password"] is True


def test_password_change_flow(client, first_login, rotated):
    # Password corta → 422 (política ≥ 12 chars).
    resp = client.post("/api/provider/auth/password",
                       headers=_bearer(rotated["token"]),
                       json={"current_password": ROTATED_PW,
                             "new_password": "short"})
    assert resp.status_code == 422
    # Flag limpio en BD + business routes desbloqueadas.
    op = _sql("SELECT must_change_password FROM ops.provider_operators "
              "WHERE username = %s", (USERNAME,))
    assert op["must_change_password"] is False
    resp = client.get("/api/admin/tenants", headers=_bearer(rotated["token"]))
    assert resp.status_code == 200
    # La password VIEJA ya no entra (AC-PS-006).
    _clear_provider_throttle()
    resp = _login(client, INITIAL_PW)
    assert resp.status_code == 401
    # Audit del cambio.
    events = _sql_all("SELECT action, payload FROM ops.provider_audit "
                      "WHERE username = %s ORDER BY id", (USERNAME,))
    assert any(e["action"] == "provider.password_changed"
               and e["payload"]["was_first_login"] is True for e in events)


def test_password_change_revokes_other_sessions(client, rotated):
    """D-PS-08: cambiar la password revoca las demás sesiones del operador."""
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 200
    other = resp.json()["session_token"]
    resp = client.post("/api/provider/auth/password",
                       headers=_bearer(other),
                       json={"current_password": ROTATED_PW,
                             "new_password": ROTATED_PW + "b"})
    assert resp.status_code == 200
    # La sesión del fixture `rotated` quedó revocada.
    resp = client.get("/api/provider/auth/me", headers=_bearer(rotated["token"]))
    assert resp.status_code == 401
    # La sesión que hizo el cambio sigue viva y el password canónico del
    # módulo queda restaurado para los tests siguientes.
    resp = client.post("/api/provider/auth/password",
                       headers=_bearer(other),
                       json={"current_password": ROTATED_PW + "b",
                             "new_password": ROTATED_PW})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC-PS-004 — password malo → 401 + throttle persistido + 429
# ---------------------------------------------------------------------------

def test_bad_password_401_throttle_and_audit(client):
    _clear_provider_throttle()
    resp = _login(client, "wrong-password")
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["reason"] == "invalid_token"
    # Sin oracle: el mensaje no revela cuál factor falló.
    assert "password" in err["message"] and "username" in err["message"]
    acct = f"acct:provider:{hashlib.sha256(USERNAME.encode()).hexdigest()}"
    row = _sql("SELECT fail_count FROM ops.login_throttle WHERE key = %s", (acct,))
    assert row is not None and row["fail_count"] >= 1
    denied = _sql_all("SELECT action, payload FROM ops.provider_audit "
                      "WHERE username = %s ORDER BY id DESC LIMIT 1", (USERNAME,))
    assert denied[0]["action"] == "provider.login_denied"
    assert denied[0]["payload"]["reason"] == "bad_credentials"


def test_lockout_429_and_survives_new_app_process(client):
    """AC-PS-004: N fallos consecutivos → 429; el contador vive en
    PostgreSQL, así que un proceso NUEVO del API sigue bloqueado."""
    _clear_provider_throttle()
    for _ in range(5):
        resp = _login(client, "wrong-password")
        assert resp.status_code == 401
    resp = _login(client, "wrong-password")
    assert resp.status_code == 429
    assert resp.json()["error"]["reason"] == "rate_limited"
    assert "Retry-After" in resp.headers or "retry_after" in resp.json()["error"]
    # Fail-closed incluso con la password CORRECTA: el lock es de la BD.
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 429
    # Proceso nuevo del API (cero estado en memoria): sigue bloqueado.
    fresh = TestClient(create_app())
    resp = _login(fresh, ROTATED_PW)
    assert resp.status_code == 429
    _clear_provider_throttle()


# ---------------------------------------------------------------------------
# AC-PS-005 — la sesión sobrevive un proceso nuevo del API
# ---------------------------------------------------------------------------

def test_session_survives_new_app_process(client):
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 200
    token = resp.json()["session_token"]
    # Un proceso FastAPI recién creado (sin NADA en memoria) acepta el token:
    # la sesión vive exclusivamente en ops.provider_sessions.
    fresh = TestClient(create_app())
    resp = fresh.get("/api/admin/tenants", headers=_bearer(token))
    assert resp.status_code == 200
    # Logout revoca.
    resp = fresh.post("/api/provider/auth/logout", headers=_bearer(token))
    assert resp.status_code == 200
    resp = client.get("/api/admin/tenants", headers=_bearer(token))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AC-PS-007/008/009 — MFA TOTP opcional, verificada antes de activar
# ---------------------------------------------------------------------------

def _totp_code_from_db() -> str | None:
    """Código TOTP REAL del secret cifrado en BD, o None si MFA no está
    activa (los tests de enrolamiento dejan al operador con MFA puesta)."""
    op = _sql("SELECT totp_active, totp_secret_enc, totp_dek_enc, totp_nonce "
              "FROM ops.provider_operators WHERE username = %s", (USERNAME,))
    if not op or not op["totp_active"]:
        return None
    from app.config import get_settings
    from app.vault.crypto import decrypt_secret, load_kek

    kek = load_kek(get_settings().vault_kek_b64)
    secret = decrypt_secret(op["totp_secret_enc"], op["totp_dek_enc"],
                            op["totp_nonce"], kek).decode()
    return _code_for(secret)


def _operator_session(client):
    resp = _login(client, ROTATED_PW, totp_code=_totp_code_from_db())
    assert resp.status_code == 200, resp.text
    return resp.json()["session_token"]


def test_mfa_enroll_flow_requires_real_code(client):
    token = _operator_session(client)
    # 1) enroll-start: secret one-time + otpauth.
    resp = client.post("/api/provider/mfa/enroll-start", headers=_bearer(token))
    assert resp.status_code == 200
    start = resp.json()
    assert start["secret"] and start["otpauth_url"].startswith("otpauth://totp/")
    assert start["secret"] in start["otpauth_url"]
    # 2) código inventado NO activa nada (verificar antes de activar).
    resp = client.post("/api/provider/mfa/enroll-confirm", headers=_bearer(token),
                       json={"code": "000000"})
    assert resp.status_code in (401, 422)
    op = _sql("SELECT totp_active FROM ops.provider_operators WHERE username = %s",
              (USERNAME,))
    assert op["totp_active"] is False
    # 3) código REAL del secret → activa.
    code = _code_for(start["secret"])
    if code == "000000":  # evita el flanco del código prohibido del paso 2
        time.sleep(0.1)
        code = _code_for(start["secret"])
    resp = client.post("/api/provider/mfa/enroll-confirm", headers=_bearer(token),
                       json={"code": code})
    assert resp.status_code == 200, resp.text
    op = _sql("SELECT totp_active, totp_secret_enc IS NOT NULL AS has_secret "
              "FROM ops.provider_operators WHERE username = %s", (USERNAME,))
    assert op["totp_active"] is True and op["has_secret"] is True
    # Audit del enrolamiento.
    last = _sql_all("SELECT action FROM ops.provider_audit ORDER BY id DESC LIMIT 1")
    assert last[0]["action"] == "provider.mfa_enrolled"
    # El secret cifrado en reposo no es el plaintext.
    enc = _sql("SELECT totp_secret_enc FROM ops.provider_operators "
               "WHERE username = %s", (USERNAME,))
    assert start["secret"].encode() not in (enc["totp_secret_enc"] or b"")


def test_login_requires_totp_when_enrolled(client):
    _clear_provider_throttle()
    # Sin código → 401 mfa_code_required (prompt de UX, NO cuenta como fallo).
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 401
    assert resp.json()["error"]["reason"] == "mfa_code_required"
    acct = f"acct:provider:{hashlib.sha256(USERNAME.encode()).hexdigest()}"
    row = _sql("SELECT fail_count FROM ops.login_throttle WHERE key = %s", (acct,))
    assert row is None or row["fail_count"] == 0
    # Código malo → 401 + throttle + audit bad_totp.
    resp = _login(client, ROTATED_PW, totp_code="000000")
    assert resp.status_code == 401
    denied = _sql_all("SELECT action, payload FROM ops.provider_audit "
                      "ORDER BY id DESC LIMIT 1")
    assert denied[0]["action"] == "provider.login_denied"
    assert denied[0]["payload"]["reason"] == "bad_totp"
    # Código real → 200 con mfa_verified=true en la sesión.
    secret_enc = _sql("SELECT totp_secret_enc, totp_dek_enc, totp_nonce "
                      "FROM ops.provider_operators WHERE username = %s", (USERNAME,))
    from app.config import get_settings
    from app.vault.crypto import decrypt_secret, load_kek

    kek = load_kek(get_settings().vault_kek_b64)
    secret = decrypt_secret(secret_enc["totp_secret_enc"],
                            secret_enc["totp_dek_enc"],
                            secret_enc["totp_nonce"], kek).decode()
    resp = _login(client, ROTATED_PW, totp_code=_code_for(secret))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operator"]["mfa_enrolled"] is True
    _, session_id, _ = body["session_token"].split(".")
    row = _sql("SELECT mfa_verified FROM ops.provider_sessions WHERE id = %s",
               (session_id,))
    assert row["mfa_verified"] is True
    return secret


def test_mfa_disable_requires_password_and_code(client):
    token = _operator_session(client)
    # Sin password correcta → 401.
    resp = client.post("/api/provider/mfa/disable", headers=_bearer(token),
                       json={"password": "not-the-password", "code": "000000"})
    assert resp.status_code in (401, 422)
    # Password correcta + código real → desactiva.
    secret_enc = _sql("SELECT totp_secret_enc, totp_dek_enc, totp_nonce "
                      "FROM ops.provider_operators WHERE username = %s", (USERNAME,))
    from app.config import get_settings
    from app.vault.crypto import decrypt_secret, load_kek

    kek = load_kek(get_settings().vault_kek_b64)
    secret = decrypt_secret(secret_enc["totp_secret_enc"],
                            secret_enc["totp_dek_enc"],
                            secret_enc["totp_nonce"], kek).decode()
    resp = client.post("/api/provider/mfa/disable", headers=_bearer(token),
                       json={"password": ROTATED_PW,
                             "code": _code_for(secret)})
    assert resp.status_code == 200, resp.text
    op = _sql("SELECT totp_active, totp_secret_enc FROM ops.provider_operators "
              "WHERE username = %s", (USERNAME,))
    assert op["totp_active"] is False and op["totp_secret_enc"] is None
    last = _sql_all("SELECT action FROM ops.provider_audit ORDER BY id DESC LIMIT 1")
    assert last[0]["action"] == "provider.mfa_disabled"
    # Sin MFA el login vuelve a NO pedir código.
    _clear_provider_throttle()
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 200
    assert resp.json()["operator"]["mfa_enrolled"] is False


# ---------------------------------------------------------------------------
# AC-PS-010 — matriz de autenticación provider + fail-closed doble
# ---------------------------------------------------------------------------

def test_auth_matrix_session_breakglass_and_fail_closed(client):
    # Sesión válida → 200.
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 200
    token = resp.json()["session_token"]
    assert client.get("/api/admin/tenants", headers=_bearer(token)).status_code == 200
    # Sesión revocada → 401.
    client.post("/api/provider/auth/logout", headers=_bearer(token))
    assert client.get("/api/admin/tenants", headers=_bearer(token)).status_code == 401
    # Break-glass configurado → 200 (deprecado, D-PS-06).
    assert client.get("/api/admin/tenants", headers=BREAK_GLASS).status_code == 200
    # Token basura → 401.
    assert client.get("/api/admin/tenants",
                      headers=_bearer("garbage")).status_code == 401

    # Sin operador seedeado → 503 fail-closed para AMBOS caminos.
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ops.provider_sessions")
            cur.execute("DELETE FROM ops.provider_operators")
    assert client.get("/api/admin/tenants", headers=BREAK_GLASS).status_code == 503
    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 503
    assert client.get("/api/admin/tenants",
                      headers=_bearer(token)).status_code == 503

    # Restaurar el seed para el resto de la suite: el bootstrap re-seedea
    # (password inicial pineado del env de test) y completa la required
    # action para volver al estado canónico (ROTATED_PW, flag limpio).
    from app.db import get_engine

    one_time = ensure_provider_operator(get_engine())
    assert one_time == INITIAL_PW  # camino pineado (D-PS-03, CI/test)
    assert client.get("/api/admin/tenants", headers=BREAK_GLASS).status_code == 200
    resp = _login(client, INITIAL_PW)
    assert resp.status_code == 200
    t = resp.json()["session_token"]
    resp = client.post("/api/provider/auth/password", headers=_bearer(t),
                       json={"current_password": INITIAL_PW,
                             "new_password": ROTATED_PW})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC-PS-011 — audit append-only: UPDATE/DELETE rechazados, cadena válida
# ---------------------------------------------------------------------------

def test_provider_audit_is_append_only(client):
    with psycopg.connect(ADMIN_DSN.format(db=TEST_DB)) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute("UPDATE ops.provider_audit SET username = 'x'")
            conn.rollback()
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute("DELETE FROM ops.provider_audit")
            conn.rollback()
    chain = _sql("SELECT status FROM ops.provider_audit_verify_chain()")
    assert chain["status"] == "ok"


# ---------------------------------------------------------------------------
# AC-PS-012 — cero secretos en cuerpos de respuesta
# ---------------------------------------------------------------------------

def test_no_secrets_in_responses(client):
    _clear_provider_throttle()
    resp = _login(client, INITIAL_PW)  # password vieja: 401 tras la rotación
    assert resp.status_code == 401
    assert INITIAL_PW not in resp.text
    assert "argon2" not in resp.text

    resp = _login(client, ROTATED_PW)
    assert resp.status_code == 200
    body = resp.json()
    token = body["session_token"]
    assert ROTATED_PW not in resp.text
    assert "password_hash" not in body and "totp_secret" not in body

    # /auth/me expone estado, jamás secretos.
    resp = client.get("/api/provider/auth/me", headers=_bearer(token))
    assert resp.status_code == 200
    me = resp.json()
    assert set(me) == {"username", "must_change_password", "mfa_enrolled",
                       "mfa_verified"}

    # El secret TOTP solo aparece en el one-time de enroll-start.
    resp = client.post("/api/provider/mfa/enroll-start", headers=_bearer(token))
    assert resp.status_code == 200
    secret = resp.json()["secret"]
    resp = client.get("/api/provider/auth/me", headers=_bearer(token))
    assert secret not in resp.text
    # Limpieza: dejar el enrolamiento pendiente sin activar no afecta a la
    # suite (totp_active sigue false).

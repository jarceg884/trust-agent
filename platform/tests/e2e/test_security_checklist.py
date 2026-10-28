"""QA e2e — checklist de seguridad: NINGÚN hallazgo de la auditoría AIDLC
(C1-C6 y los altos) se repite en el control plane.

Cada test ataca un hallazgo concreto de auditoria/AUDITORIA-AIDLC.md con
evidencia ejecutada (grep real sobre app/, probes reales contra la DB,
respuestas reales del API).
"""
import os
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    PLATFORM_DIR, TENANT_A, TENANT_B, TEST_DB, USER_ALICE, bearer_headers,
    mint,
)

APP_DIR = os.path.join(PLATFORM_DIR, "app")


def _source_lines(pattern: str, exclude: str | None = None) -> list[str]:
    """grep real sobre el código de la app (excluye caches y tests)."""
    hits = []
    for root, _, files in os.walk(APP_DIR):
        if "__pycache__" in root:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            if exclude and os.path.abspath(path).startswith(os.path.abspath(exclude)):
                continue
            with open(path) as fh:
                for i, line in enumerate(fh, 1):
                    if re.search(pattern, line):
                        hits.append(f"{path}:{i}")
    return hits


# ---------------------------------------------------------------------------
# C6 + altos — verify=False / TLS desactivado
# ---------------------------------------------------------------------------
def test_c6_no_verify_false_anywhere():
    assert _source_lines(r"verify\s*=\s*False") == []


# ---------------------------------------------------------------------------
# ROPC solo en el bootstrap documentado del proveedor (constitution IV)
# ---------------------------------------------------------------------------
def test_password_grant_only_documented_bootstrap():
    hits = _source_lines(r"grant_type=password|grant_type.*password")
    # Excepciones documentadas (ADR-019, spec 015 v1.3.0):
    #   1. admin-cli del proveedor (ADR-015, keycloak_admin.py) — bootstrap.
    #   2. Direct Access Grant del auth panel (app/auth/panel.py) — decisión
    #      de producto Miller 2026-10-28: login del panel propio, Keycloak
    #      nunca visible. Nada de ROPC en ningún otro módulo.
    sanctioned = (
        os.path.join(APP_DIR, "tenants", "keycloak_admin.py"),
        os.path.join(APP_DIR, "auth", "panel.py"),
    )
    assert hits and all(h.startswith(sanctioned) for h in hits), hits
    # Ningún OTRO módulo puede transportar passwords: el conjunto de
    # ARCHIVOS con hits debe ser exactamente el par sancionado.
    assert {h.split(":", 1)[0] for h in hits} == set(sanctioned), hits


# ---------------------------------------------------------------------------
# Altos — superadmin hardcodeado / fallbacks de credenciales
# ---------------------------------------------------------------------------
def test_no_hardcoded_superadmin_or_fallbacks():
    assert _source_lines(r"jose-arce|PANEL_SUPERADMIN|superadmin") == []
    # Sin secretos literales sospechosos en el código de la app
    assert _source_lines(r"(password|secret|token)\s*=\s*['\"][A-Za-z0-9]{8,}") == []


# ---------------------------------------------------------------------------
# C5 — enrolamiento MFA por scraping de HTML (regex/QR) prohibido
# ---------------------------------------------------------------------------
def test_c5_no_html_scraping_or_qr_decoding():
    # Excepción documentada (ADR-019, D-AUTH-14): KC 26 NO expone REST para
    # generar el secret TOTP ni crear la credencial OTP — el ÚNICO mecanismo
    # real es el required action CONFIGURE_TOTP, que el auth panel conduce
    # headless (app/auth/panel.py). El scraping ahí es deliberado, cubierto
    # por tests de regresión + e2e real, y limitado a ese módulo.
    sanctioned = os.path.join(APP_DIR, "auth", "panel.py")
    hits = _source_lines(
        r"BeautifulSoup|cv2|pyzbar|decode_qr|action=\"[^\"]*login-actions")
    assert all(h.startswith(sanctioned) for h in hits), hits
    # QR decoding (imagen → secret) sigue PROHIBIDO en todas partes: el
    # panel muestra el PNG que genera el IdP, jamás lo decodifica.
    assert _source_lines(r"cv2|pyzbar|decode_qr") == []
    # El cliente KC del proveedor usa Admin REST + JSON, no formularios
    assert _source_lines(r"re\.search\(.*action", exclude=sanctioned) == []


# ---------------------------------------------------------------------------
# C2 — sesiones en PostgreSQL, nunca en memoria de proceso
# ---------------------------------------------------------------------------
def test_c2_sessions_in_db_survive_api_restart(client, idp, admin_conn):
    alice = mint(client, idp, "alice")
    token = alice["session_token"]

    # La sesión vive en la DB (hash del secreto, no el secreto)
    session_id = token.split(".")[2]
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT jti, status FROM sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
    assert row is not None, "la sesión DEBE estar en PostgreSQL"
    assert row[1] == "active"
    secret = token.split(".")[3]
    assert secret not in str(row[0]), "en DB va el hash, no el secreto"

    # Reinicio simulado del API: engine desechado + app nueva
    from app.db import reset_engine
    from app.main import create_app

    reset_engine()
    with TestClient(create_app()) as fresh:
        resp = fresh.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, "la sesión sobrevive al reinicio (DB)"


def test_c2_session_token_not_derived_from_identity(client, idp):
    alice = mint(client, idp, "alice")
    token = alice["session_token"]
    # C2 del AIDLC: el token viejo era base64(username:hex). Aquí el token
    # no contiene ninguna pista del usuario.
    for leak in ("alice", "sub-alice-orgadmin", "tenant-a"):
        assert leak not in token
    assert token.startswith("sts1.")
    assert len(token.split(".")) == 4


# ---------------------------------------------------------------------------
# JWT sin verificar firma (alto del AIDLC) — JWKS obligatorio
# ---------------------------------------------------------------------------
def test_jwt_signature_enforced(client, idp):
    # Firmado con OTRA clave (no está en el JWKS) → 401
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"),
                         sign_with_other_key=True)
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401

    # HS256 (simétrico, sin JWKS) → 401
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), algorithm="HS256")
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401

    # Sin claim tenant_id → 401 (el tenant sale de credenciales verificadas)
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), omit_tenant_id=True)
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401

    # aud incorrecta → 401
    token = idp.make_jwt(sub="sub-alice-orgadmin", tenant_id=TENANT_A,
                         issuer=idp.issuer("tenant-a"), audience="other-app")
    resp = client.post("/api/auth/session",
                       json={"id_token": token, "kind": "desktop"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# C1 + C4 — secretos en respuestas persistentes / en claro en DB
# ---------------------------------------------------------------------------
def test_c1_c4_secrets_one_time_and_never_in_db(client, idp, admin_conn):
    import secrets as pysecrets

    alice = mint(client, idp, "alice")
    canary = "sec-canary-" + pysecrets.token_hex(12)

    # Vault: el valor en claro aparece EXACTAMENTE en el 201 de registro
    resp = client.post("/api/vault/credentials", json={
        "name": "qa-checklist-cred", "credential_type": "api_key",
        "secret_value": canary,
    }, headers=bearer_headers(alice))
    assert resp.status_code == 201
    assert resp.json()["secret_value"] == canary
    credential_id = resp.json()["credential_id"]

    # GET posterior: metadatos solamente
    resp = client.get(f"/api/vault/credentials/{credential_id}",
                      headers=bearer_headers(alice))
    assert "secret_value" not in resp.text

    # Key virtual: plaintext EXACTAMENTE en el 201 de emisión
    resp = client.post("/api/models/keys",
                       json={"user_id": USER_ALICE},
                       headers=bearer_headers(alice))
    assert resp.status_code == 201
    key = resp.json()["key"]
    assert key

    # C4: NINGUNO de los dos plaintext vive en la DB (cifrado / hash only)
    dsn = ("host=127.0.0.1 port=55432 user=postgres "
           "password=trustlocal "
           # t_e000bf40: conftest.TEST_DB (import-time) — os.environ
           # ['TRUST_TEST_DB'] ya fue purgado por el fixture env (H6)
           f"dbname={TEST_DB}")
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for table, col in [
            ("vault_credentials", "ciphertext_secret::text"),
            ("vault_credentials", "name"),
            ("audit_log", "payload::text"),
            ("api_keys", "litellm_key_hash"),
            ("api_keys", "alias"),
            ("sessions", "jti"),
            ("users", "email || ' ' || display_name"),
        ]:
            for needle in (canary, key):
                cur.execute(
                    f"SELECT count(*) FROM {table} "
                    f"WHERE {col} LIKE %s", (f"%{needle}%",))
                n = cur.fetchone()[0]
                if needle == key and table == "api_keys" and \
                        col == "litellm_key_hash":
                    continue  # el hash sha256 NO es el plaintext
                assert n == 0, f"plaintext de {table}.{col} en la DB"

    # El hash de la key SÍ está (sha256) — control positivo del cifrado
    import hashlib
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM api_keys WHERE litellm_key_hash = %s",
            (hashlib.sha256(key.encode()).hexdigest(),))
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# C3 — single-tenant por diseño → cubierto por test_isolation_e2e.py
# (API 404 + RLS fail-closed + WITH CHECK). Aquí: el tenant SIEMPRE sale de
# credenciales verificadas, jamás de un header forjable.
# ---------------------------------------------------------------------------
def test_c3_tenant_never_from_headers(client, idp):
    alice = mint(client, idp, "alice")
    # Un header X-Tenant-Id forjado NO traslada la sesión de Alice
    resp = client.get(
        "/api/me",
        headers={**bearer_headers(alice), "X-Tenant-Id": TENANT_B})
    body = resp.json()
    assert resp.status_code == 200
    assert body["user_id"] == USER_ALICE, "el tenant NO sale de headers"

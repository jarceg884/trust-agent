"""Spec 034 — Hermes CLI gobernado: instalador CLI por tenant, device_type
en el enrolamiento y exposición en las APIs de dispositivos
(REQ-034-001..004).

Cubre contra PostgreSQL real:
- GET /download/{slug}/cli: script bash con org_code horneado, audit
  ``download.issued`` (artifact=cli), fail-closed (disabled/suspendido),
  anti-enumeración y rate limit;
- página pública con las DOS opciones (desktop + comando curl copiable);
- bundle con el miembro ``skin.yaml`` (fuente única del skin, D-034-02);
- POST /api/devices/enroll con ``device_type`` ('cli'|'desktop', 422 en
  valor inválido) y exposición en /api/devices, /api/devices/me y
  /api/devices/presence.
"""
import hashlib
import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from conftest import (
    TENANT_A,
    TENANT_B,
    TEST_DB,
    _admin,
    bearer_headers,
    mint,
)
from tests.test_devices_flow import _enroll, _keypair_pem

# t_e000bf40: TEST_DB viene de conftest (import-time, honra TRUST_TEST_DB);
# antes estaba HARDCODEADO a "trust_agent_platform_test" e ignoraba el
# override, conectándose a la DB equivocada (o inexistente).


def _audit_rows(action, tenant=TENANT_A):
    with _admin(TEST_DB) as c:
        rows = c.execute(
            "SELECT payload FROM audit_log "
            "WHERE tenant_id = %s AND action = %s ORDER BY seq DESC",
            (tenant, action),
        ).fetchall()
    out = []
    for r in rows:
        p = r[0]
        out.append(p if isinstance(p, dict) else json.loads(p))
    return out


def _set_tenant_state(tenant, **kwargs):
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    with _admin(TEST_DB) as c:
        c.execute(
            f"UPDATE tenants SET {sets} WHERE id = %s",
            (*kwargs.values(), tenant),
        )


def _ip_client(client, ip):
    return TestClient(client.app, client=(ip, 54321))


@pytest.fixture(autouse=True)
def _pristine():
    def reset():
        with _admin(TEST_DB) as c:
            c.execute(
                "UPDATE tenants SET status = 'active', download_enabled = true "
                "WHERE id IN (%s, %s)",
                (TENANT_A, TENANT_B),
            )
            c.execute("DELETE FROM ops.login_throttle WHERE key LIKE 'dl:%'")
    reset()
    yield
    reset()


# --- REQ-034-001: instalador CLI por tenant ------------------------------------

def test_cli_installer_served_with_org_code(client):
    c = _ip_client(client, "198.51.100.201")
    r = c.get("/download/tenant-a/cli")
    assert r.status_code == 200, r.text
    assert "text/x-shellscript" in r.headers["content-type"]
    assert r.headers["X-Installer-SHA256"] == hashlib.sha256(
        r.content
    ).hexdigest()

    script = r.text
    # org_code del tenant horneado (público, D-023-02/D-034-01).
    assert 'SLUG="tenant-a"' in script
    assert 'ORG_CODE="tenant-a"' in script
    # Binding + device_type cli (D-034-04) + API base del origen.
    assert "org-code.txt" in script
    assert 'set_env TRUST_DEVICE_TYPE "cli"' in script
    assert "set_env TRUST_ORG_CODE" in script
    assert "/download/tenant-a/cli" in script  # comentario de uso (curl)
    # Fuente única del plugin: descarga el bundle canónico (D-034-02).
    assert "/download/${SLUG}/bundle" in script
    # Config idéntica al desktop (gateway standalone, plugin, skin, modelo).
    assert "gateway.standalone true" in script
    assert "trust-agent-governance" in script
    assert "display.skin" in script
    # Sin secretos (D-034-08): org_code público, nada de claves.
    assert "sk-" not in script


def test_cli_installer_audit_per_served(client):
    c = _ip_client(client, "198.51.100.202")
    before = len(_audit_rows("download.issued"))
    assert c.get("/download/tenant-a/cli").status_code == 200
    rows = _audit_rows("download.issued")
    assert len(rows) == before + 1  # REQ-034-002: audit por servido
    assert rows[0].get("artifact") == "cli"
    assert rows[0].get("slug") == "tenant-a"


def test_cli_installer_is_per_tenant(client):
    ca = _ip_client(client, "198.51.100.203")
    cb = _ip_client(client, "198.51.100.204")
    assert 'SLUG="tenant-a"' in ca.get("/download/tenant-a/cli").text
    assert 'SLUG="tenant-b"' in cb.get("/download/tenant-b/cli").text


# --- REQ-034-002: seguridad del endpoint ----------------------------------------

def test_cli_installer_fail_closed(client):
    _set_tenant_state(TENANT_B, download_enabled=False)
    r = client.get("/download/tenant-b/cli")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "link_disabled"

    _set_tenant_state(TENANT_B, download_enabled=True, status="suspended")
    r = client.get("/download/tenant-b/cli")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "tenant_suspended"


def test_cli_installer_unknown_slug_and_rate_limit(client):
    c = _ip_client(client, "198.51.100.205")
    for _ in range(5):
        assert c.get("/download/no-such-org/cli").status_code == 404
    # El sondeo quema el contador de IP → 429 anti-enumeración.
    assert c.get("/download/no-such-org/cli").status_code == 429

    c = _ip_client(client, "198.51.100.206")
    for _ in range(5):  # download_bundle_rate_max = 5 (mismo budget)
        assert c.get("/download/tenant-a/cli").status_code == 200
    assert c.get("/download/tenant-a/cli").status_code == 429


# --- REQ-034-003: página pública con las dos opciones ---------------------------

def test_page_offers_desktop_and_cli(client):
    r = client.get("/download/tenant-a")
    assert r.status_code == 200
    body = r.text
    assert "/download/tenant-a/bundle" in body       # opción desktop
    assert "Download for desktop" in body
    assert "/download/tenant-a/cli" in body          # opción CLI
    assert "curl -fsSL" in body                      # comando copiable
    assert "WSL2" in body                            # targets oficiales
    assert 'id="cli"' in body                        # sección ancla
    # Con el link deshabilitado no se ofrece el comando CLI (honesto).
    _set_tenant_state(TENANT_A, download_enabled=False)
    body2 = client.get("/download/tenant-a").text
    assert "curl -fsSL" not in body2
    assert "disabled" in body2


# --- D-034-02: skin.yaml como miembro del bundle --------------------------------

def test_bundle_carries_skin_member(client):
    c = _ip_client(client, "198.51.100.207")
    r = c.get("/download/tenant-a/bundle")
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        skin = tar.extractfile("trust-agent-tenant-a/skin.yaml").read().decode()
    assert "name: trust-tenant-a" in skin
    assert "agent_name:" in skin  # skin white-label completo


# --- REQ-034-004: device_type en el enrolamiento --------------------------------

def test_enroll_device_type_cli_persisted_and_exposed(client, idp, admin_conn):
    fp = "cli-" + hashlib.sha256(b"spec034-cli").hexdigest()
    r = _enroll(client, idp, fp, device_type="cli")
    assert r.status_code == 202, r.text
    device_id = r.json()["device_id"]

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT device_type, status FROM devices WHERE id = %s",
            (device_id,),
        )
        device_type, status = cur.fetchone()
    assert (device_type, status) == ("cli", "pending")

    # Exposición: lista admin, detalle, presence y /me.
    alice = mint(client, idp, "alice")
    h = bearer_headers(alice)
    listed = client.get("/api/devices", headers=h).json()["devices"]
    row = next(d for d in listed if d["id"] == device_id)
    assert row["device_type"] == "cli"
    detail = client.get(f"/api/devices/{device_id}", headers=h).json()
    assert detail["device_type"] == "cli"
    presence = client.get("/api/devices/presence", headers=h).json()
    prow = next(d for d in presence["devices"] if d["id"] == device_id)
    assert prow["device_type"] == "cli"

    carol = mint(client, idp, "carol")
    mine = client.get("/api/devices/me", headers=bearer_headers(carol)).json()
    crow = next(d for d in mine["devices"] if d["id"] == device_id)
    assert crow["device_type"] == "cli"


def test_enroll_device_type_default_desktop(client, idp, admin_conn):
    fp = "cli-" + hashlib.sha256(b"spec034-default").hexdigest()
    r = _enroll(client, idp, fp)  # sin device_type → default retrocompatible
    assert r.status_code == 202, r.text
    device_id = r.json()["device_id"]
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT device_type FROM devices WHERE id = %s", (device_id,)
        )
        assert cur.fetchone()[0] == "desktop"


def test_enroll_device_type_invalid_422(client, idp):
    fp = "cli-" + hashlib.sha256(b"spec034-invalid").hexdigest()
    r = _enroll(client, idp, fp, device_type="phone")
    assert r.status_code == 422, r.text
    assert "device_type" in r.text

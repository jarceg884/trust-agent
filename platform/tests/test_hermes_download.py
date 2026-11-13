"""Spec 023 — Hermes download por tenant: página/bundle públicos, gestión del
link (GET/PUT /api/download-link), rate limit, anti-enumeración, audit
``download.issued`` y aislamiento multi-tenant (AC-HDL-010, REQ-HDL-001..007).
"""
import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from conftest import (
    TENANT_A,
    TENANT_B,
    TEST_DB,  # t_e000bf40: antes hardcoded — ignoraba TRUST_TEST_DB
    USER_ALICE,
    USER_BOB,
    USER_CAROL,
    _admin,
    bearer_headers,
    mint,
)


def _audit_count(action, tenant=TENANT_A):
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            "WHERE tenant_id = %s AND action = %s",
            (tenant, action),
        ).fetchone()
    return row[0]


def _set_tenant_state(tenant, **kwargs):
    """Direct column update for test preconditions (status, branding...)."""
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    with _admin(TEST_DB) as c:
        c.execute(
            f"UPDATE tenants SET {sets} WHERE id = %s",
            (*kwargs.values(), tenant),
        )


def _ip_client(client, ip):
    """TestClient con IP de origen controlada (los contadores de throttle son
    por IP; TestClient por defecto reporta host 'testclient' → ip None)."""
    return TestClient(client.app, client=(ip, 54321))


@pytest.fixture(autouse=True)
def _pristine_tenant_a():
    """Cada test arranca con el tenant A habilitado y sin branding propio."""
    def reset():
        with _admin(TEST_DB) as c:
            c.execute(
                "DELETE FROM branding_versions WHERE tenant_id = %s", (TENANT_A,)
            )
            c.execute(
                "DELETE FROM branding_assets WHERE tenant_id = %s", (TENANT_A,)
            )
            c.execute(
                "UPDATE tenants SET branding = '{}'::jsonb, "
                "branding_draft = '{}'::jsonb, branding_version = 0, "
                "download_enabled = true WHERE id = %s",
                (TENANT_A,),
            )
            c.execute(
                "UPDATE tenants SET status = 'active' WHERE id IN (%s, %s)",
                (TENANT_A, TENANT_B),
            )
            # Contadores de throttle de los IPs que usan estos tests.
            c.execute("DELETE FROM ops.login_throttle WHERE key LIKE 'dl:%'")
    reset()
    yield
    reset()


# --- REQ-HDL-001: página pública con branding ---------------------------------

def test_page_public_with_tenant_branding(client, idp):
    alice = mint(client, idp, "alice", extra_claims={"email": "alice@tenant-a.test"})
    r = client.put(
        "/api/branding",
        json={
            "product_name": "Acme Agent",
            "tagline": "Acme governed AI",
            "accent_color": "#c90104",
        },
        headers=bearer_headers(alice),
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/branding/publish", headers=bearer_headers(alice))
    assert r.status_code == 200, r.text

    r = client.get("/download/tenant-a")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Acme Agent" in body            # branding publicado del tenant
    assert "#c90104" in body               # accent color aplicado
    assert "/download/tenant-a/bundle" in body  # botón de descarga
    # Aislamiento (REQ-HDL-010): nada del tenant B en la página de A.
    assert "Tenant B" not in body and "tenant-b" not in body


def test_page_default_branding_when_none_published(client):
    r = client.get("/download/tenant-a")
    assert r.status_code == 200
    assert "Trust Agent" in r.text  # default del proveedor (spec 028)


# --- REQ-HDL-006: anti-enumeración --------------------------------------------

def test_unknown_slug_404_then_rate_limited(client):
    c = _ip_client(client, "198.51.100.101")
    for _ in range(5):
        assert c.get("/download/no-such-org").status_code == 404
    # El 6º sondeo quema el contador de IP (login_max_failures=5 en tests)
    # → 429 anti-enumeración (mismo comportamiento que /api/auth/org-config).
    assert c.get("/download/no-such-org").status_code == 429


# --- REQ-HDL-002/004: bundle real + audit -------------------------------------

def _get_bundle(c, slug="tenant-a"):
    r = c.get(f"/download/{slug}/bundle")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/gzip"
    return r


def test_bundle_contents_and_audit(client):
    c = _ip_client(client, "198.51.100.102")
    before = _audit_count("download.issued")
    r = _get_bundle(c)
    after = _audit_count("download.issued")
    assert after == before + 1  # REQ-HDL-004: audit por descarga

    # El header X-Bundle-SHA256 es el sha256 real del cuerpo.
    import hashlib
    assert r.headers["X-Bundle-SHA256"] == hashlib.sha256(r.content).hexdigest()

    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        names = tar.getnames()
        root = "trust-agent-tenant-a"
        for member in (
            f"{root}/install.sh",
            f"{root}/trust-login",
            f"{root}/README.md",
            f"{root}/manifest.json",
            f"{root}/plugin/trust-agent-governance/plugin.yaml",
            f"{root}/plugin/trust-agent-governance/enroll.py",
            f"{root}/plugin/trust-agent-governance/enforcement.py",
        ):
            assert member in names, member
        assert not any("__pycache__" in n for n in names)

        manifest = json.loads(
            tar.extractfile(f"{root}/manifest.json").read()
        )
        assert manifest["slug"] == "tenant-a"
        assert manifest["org_code"] == "tenant-a"       # binding horneado
        assert manifest["plugin"] == "trust-agent-governance"
        assert manifest["members_sha256"][f"{root}/install.sh"]

        install = tar.extractfile(f"{root}/install.sh").read().decode()
        assert "org-code.txt" in install                 # binding en el instalador
        assert 'printf \'%s\\n\' "${ORG_CODE}"' in install
        assert "gateway.standalone true" in install      # D-023-06
        assert "trust-agent-governance" in install

        # Sin secretos en el tarball (org_code es público, D-023-02).
        assert b"sk-master-test" not in r.content


def test_bundle_is_per_tenant(client):
    """El bundle de A lleva el binding de A; el de B el de B (aislamiento)."""
    ca = _ip_client(client, "198.51.100.103")
    cb = _ip_client(client, "198.51.100.104")
    ra, rb = _get_bundle(ca, "tenant-a"), _get_bundle(cb, "tenant-b")

    def roots(resp):
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            return {n.split("/")[0] for n in tar.getnames()}

    assert roots(ra) == {"trust-agent-tenant-a"}
    assert roots(rb) == {"trust-agent-tenant-b"}


# --- REQ-HDL-003: rate limit del bundle ---------------------------------------

def test_bundle_rate_limited(client):
    c = _ip_client(client, "198.51.100.105")
    for _ in range(5):  # download_bundle_rate_max = 5
        assert c.get("/download/tenant-a/bundle").status_code == 200
    r = c.get("/download/tenant-a/bundle")
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


# --- REQ-HDL-007: tenant suspendido → fail-closed ------------------------------

def test_suspended_tenant_refused(client):
    _set_tenant_state(TENANT_B, status="suspended")
    r = client.get("/download/tenant-b")
    assert r.status_code == 403
    assert "unavailable" in r.text
    r = client.get("/download/tenant-b/bundle")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "tenant_suspended"


# --- REQ-HDL-005: gestión del link (contrato exacto con la UI) -----------------

def test_download_link_contract_and_toggle(client, idp):
    alice = mint(client, idp, "alice")
    h = bearer_headers(alice)

    r = client.get("/api/download-link", headers=h)
    assert r.status_code == 200, r.text
    state = r.json()
    assert set(state) == {"url", "enabled", "updated_at"}   # contrato UI
    assert state["enabled"] is True                          # default (D-023-08)
    assert state["url"].endswith("/download/tenant-a")

    # Deshabilitar → audit + efecto en el endpoint público.
    before = _audit_count("download.link.disabled")
    r = client.put("/api/download-link", json={"enabled": False}, headers=h)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert _audit_count("download.link.disabled") == before + 1

    c = _ip_client(client, "198.51.100.106")
    r = c.get("/download/tenant-a/bundle")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "link_disabled"
    r = c.get("/download/tenant-a")
    assert r.status_code == 200
    assert "disabled" in r.text  # página honesta, sin botón funcional

    # Re-habilitar → audit + descarga restaurada.
    before = _audit_count("download.link.enabled")
    r = client.put("/api/download-link", json={"enabled": True}, headers=h)
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert _audit_count("download.link.enabled") == before + 1
    assert c.get("/download/tenant-a/bundle").status_code == 200


def test_download_link_permission_gates(client, idp):
    """Solo org_admin (download_link.manage): user y dept_admin → 403."""
    carol = mint(client, idp, "carol")   # user
    r = client.get("/api/download-link", headers=bearer_headers(carol))
    assert r.status_code == 403
    bob = mint(client, idp, "bob")       # dept_admin (NO tiene el permiso)
    r = client.get("/api/download-link", headers=bearer_headers(bob))
    assert r.status_code == 403
    r = client.put(
        "/api/download-link", json={"enabled": False},
        headers=bearer_headers(bob),
    )
    assert r.status_code == 403
    # Y sin sesión no hay gestión (el router exige MFA).
    assert client.get("/api/download-link").status_code == 401


# --- Public edge (console :3100, 2026-11-12) ----------------------------------
# The control plane is published through the console's public port, which forwards
# /download/**, the device API and /llm/** to the API and LiteLLM on loopback. A request
# that crossed that edge reaches the API as http://127.0.0.1:8000: whatever the API tells an
# employee (api_base, the CLI installer URL, the admin's link, the model gateway) must be
# the public origin, never the edge's loopback upstream.
EDGE = "http://203.0.113.10:3100"


@pytest.fixture
def public_edge(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TRUST_PUBLIC_API_BASE_URL", EDGE + "/")
    monkeypatch.setenv("TRUST_LITELLM_PUBLIC_BASE_URL", EDGE + "/llm")
    get_settings.cache_clear()
    yield EDGE
    monkeypatch.delenv("TRUST_PUBLIC_API_BASE_URL")
    monkeypatch.delenv("TRUST_LITELLM_PUBLIC_BASE_URL")
    get_settings.cache_clear()


def test_desktop_config_keeps_the_request_origin_by_default(client):
    c = _ip_client(client, "198.51.100.120")
    r = c.get("/download/tenant-a/desktop-config")
    assert r.status_code == 200, r.text
    assert r.json()["api_base"] == str(c.base_url).rstrip("/")


def test_public_edge_is_what_employees_are_told(client, idp, public_edge):
    c = _ip_client(client, "198.51.100.121")

    config = c.get("/download/tenant-a/desktop-config")
    assert config.status_code == 200, config.text
    assert config.json()["api_base"] == public_edge          # trailing "/" normalized
    assert config.json()["litellm_base"] == public_edge + "/llm"

    page = c.get("/download/tenant-a")
    assert page.status_code == 200
    assert f"{public_edge}/download/tenant-a/cli" in page.text
    assert str(c.base_url) not in page.text.replace(public_edge, "")

    alice = mint(client, idp, "alice")
    link = client.get("/api/download-link", headers=bearer_headers(alice))
    assert link.status_code == 200, link.text
    assert link.json()["url"] == f"{public_edge}/download/tenant-a"



def test_desktop_config_points_at_the_public_logo_route_only_for_published_logos(client):
    c = _ip_client(client, "198.51.100.122")
    config = c.get("/download/tenant-a/desktop-config").json()
    assert "branding_version" in config
    for variant in ("light", "dark"):
        url, sha = config[f"logo_{variant}_url"], config[f"logo_{variant}_sha256"]
        assert (url is None) == (sha is None)
        logo = c.get(f"/download/tenant-a/branding/logo/{variant}")
        if url is None:
            assert logo.status_code == 404
        else:
            assert url.endswith(f"/download/tenant-a/branding/logo/{variant}")
            assert logo.status_code == 200 and logo.headers["etag"] == f'"{sha}"'
    assert c.get("/download/tenant-a/branding/logo/purple").status_code == 404

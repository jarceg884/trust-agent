"""Spec 040 — Instaladores desktop por plataforma: página con 3 botones,
endpoints /download/{slug}/installer/{platform}, audit por plataforma,
estados honestos (REQ-PI-001..004, REQ-PI-008, REQ-PI-009).

Los artefactos reales los construye el HOST (scripts/build_desktop_installers.py);
aquí se verifica el SERVING: el directorio de artefactos se apunta a un tmp
con un manifest + binario de prueba (el contrato que el script escribe).
"""
import hashlib
import json

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
from test_hermes_download import (
    _audit_count,
    _ip_client,
    _set_tenant_state,
)


@pytest.fixture(autouse=True)
def _pristine_tenant_a():
    """Cada test arranca con el tenant A habilitado (mismo reset que 023)."""
    def reset():
        with _admin(TEST_DB) as c:
            c.execute(
                "UPDATE tenants SET download_enabled = true WHERE id = %s",
                (TENANT_A,),
            )
            c.execute(
                "UPDATE tenants SET status = 'active' WHERE id IN (%s, %s)",
                (TENANT_A, TENANT_B),
            )
            c.execute("DELETE FROM ops.login_throttle WHERE key LIKE 'dl:%'")
    reset()
    yield
    reset()


@pytest.fixture()
def artifacts_dir(tmp_path, monkeypatch):
    """Apunta settings.installer_artifacts_dir a un tmp (layout del script)."""
    root = tmp_path / "installer-artifacts"
    root.mkdir()
    monkeypatch.setenv("TRUST_INSTALLER_ARTIFACTS_DIR", str(root))
    from app.config import get_settings

    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _bake_build(root, slug: str, version: str, platforms: dict[str, bytes]):
    """Escribe el layout que produce scripts/build_desktop_installers.py."""
    d = root / slug / version
    d.mkdir(parents=True, exist_ok=True)
    manifest = {"slug": slug, "plugin_version": version, "platforms": {}}
    for platform, payload in platforms.items():
        name = f"trust-agent-setup-{slug}-v{version}-{platform}-x64.bin"
        (d / name).write_bytes(payload)
        manifest["platforms"][platform] = {
            "filename": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    (d / "installer-manifest.json").write_text(json.dumps(manifest))
    return manifest


def _plugin_version() -> str:
    import os

    from app.config import get_settings

    plugin = os.path.join(
        get_settings().hermes_plugin_dir, "plugin.yaml"
    )
    import re

    match = re.search(
        r"^version:\s*(\S+)", open(plugin).read(), re.MULTILINE
    )
    assert match is not None
    return match.group(1)


# --- REQ-PI-001: página con 3 botones por plataforma ---------------------------

def test_page_has_three_platform_buttons(client):
    r = client.get("/download/tenant-a")
    assert r.status_code == 200
    body = r.text
    # Las 3 tarjetas por plataforma con detección de OS (D-040-06).
    for platform in ("windows", "macos", "linux"):
        assert f'data-os="{platform}"' in body, platform
    assert "Download for your platform" in body
    assert "navigator.userAgent" in body  # OS detect client-side
    # La sección CLI sigue (spec 034, REQ-034-003).
    assert "/download/tenant-a/cli" in body
    assert "curl -fsSL" in body


def test_page_buttons_honest_without_builds(client):
    """Sin build servido: ningún botón enlaza a un artefacto inexistente
    (REQ-PI-002/009) y macOS ofrece la vía CLI real de hoy."""
    r = client.get("/download/tenant-a")
    body = r.text
    assert "/download/tenant-a/installer/" not in body
    assert "Use the CLI on macOS" in body  # honesto (D-040-04)
    assert "No installer build is currently available" in body


def test_page_buttons_enabled_with_build(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"windows": b"fake-exe", "linux": b"fake-appimage"})
    r = client.get("/download/tenant-a")
    body = r.text
    assert "/download/tenant-a/installer/windows" in body
    assert "/download/tenant-a/installer/linux" in body
    assert "Detected for your system" in body  # badge en cards con build
    # macOS sin build sigue honesto.
    assert "/download/tenant-a/installer/macos" not in body
    assert "Use the CLI on macOS" in body


# --- REQ-PI-003: serving real + audit por plataforma ---------------------------

def test_installer_served_and_audited(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"linux": b"fake-appimage-payload"})
    c = _ip_client(client, "198.51.100.110")
    before = _audit_count("download.issued")
    r = c.get("/download/tenant-a/installer/linux")
    assert r.status_code == 200, r.text
    assert r.content == b"fake-appimage-payload"
    assert r.headers["X-Installer-SHA256"] == hashlib.sha256(
        b"fake-appimage-payload").hexdigest()
    assert "attachment" in r.headers["content-disposition"]

    # Audit download.issued artifact=installer-linux (D-040-08).
    with _admin(TEST_DB) as conn:
        row = conn.execute(
            "SELECT payload->>'artifact', payload->>'platform', "
            "payload->>'plugin_version' FROM audit_log "
            "WHERE tenant_id = %s AND action = 'download.issued' "
            "ORDER BY seq DESC LIMIT 1",
            (TENANT_A,),
        ).fetchone()
    assert row[0] == "installer-linux"
    assert row[1] == "linux"
    assert row[2] == _plugin_version()  # versionado por release (REQ-PI-008)
    assert _audit_count("download.issued") == before + 1


def test_windows_installer_served_and_audited(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"windows": b"fake-nsis-exe"})
    c = _ip_client(client, "198.51.100.111")
    r = c.get("/download/tenant-a/installer/windows")
    assert r.status_code == 200
    assert r.content == b"fake-nsis-exe"
    with _admin(TEST_DB) as conn:
        row = conn.execute(
            "SELECT payload->>'artifact' FROM audit_log "
            "WHERE tenant_id = %s AND action = 'download.issued' "
            "ORDER BY seq DESC LIMIT 1",
            (TENANT_A,),
        ).fetchone()
    assert row[0] == "installer-windows"


def test_macos_without_build_404_honest(client, artifacts_dir):
    """REQ-PI-009: jamás se finge un .dmg — 404 installer_not_built."""
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"linux": b"x"})
    c = _ip_client(client, "198.51.100.112")
    r = c.get("/download/tenant-a/installer/macos")
    assert r.status_code == 404
    assert r.json()["error"]["reason"] == "installer_not_built"


def test_unknown_platform_404(client):
    r = client.get("/download/tenant-a/installer/beos")
    assert r.status_code == 404


def test_old_plugin_version_not_served(client, artifacts_dir):
    """D-040-07: solo se sirve el build de la versión del plugin ACTUAL."""
    _bake_build(artifacts_dir, "tenant-a", "0.0.1-ancient",
                {"linux": b"stale-build"})
    c = _ip_client(client, "198.51.100.113")
    r = c.get("/download/tenant-a/installer/linux")
    assert r.status_code == 404
    assert r.json()["error"]["reason"] == "installer_not_built"
    # Y la página no habilita el botón con un build viejo.
    assert "/download/tenant-a/installer/linux" not in client.get(
        "/download/tenant-a").text


def test_missing_artifact_file_not_served(client, artifacts_dir):
    """El manifest solo no basta: el binario debe existir (honestidad)."""
    d = artifacts_dir / "tenant-a" / _plugin_version()
    d.mkdir(parents=True)
    (d / "installer-manifest.json").write_text(json.dumps({
        "slug": "tenant-a",
        "plugin_version": _plugin_version(),
        "platforms": {"linux": {"filename": "ghost.bin",
                                "sha256": "0" * 64, "size": 1}},
    }))
    c = _ip_client(client, "198.51.100.114")
    r = c.get("/download/tenant-a/installer/linux")
    assert r.status_code == 404
    assert r.json()["error"]["reason"] == "installer_not_built"


# --- REQ-PI-004: seguridad pública heredada del bundle -------------------------

def test_installer_rate_limited(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"linux": b"x"})
    c = _ip_client(client, "198.51.100.115")
    for _ in range(5):  # download_bundle_rate_max = 5
        assert c.get("/download/tenant-a/installer/linux").status_code == 200
    r = c.get("/download/tenant-a/installer/linux")
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_installer_unknown_slug_anti_enumeration(client):
    c = _ip_client(client, "198.51.100.116")
    for _ in range(5):
        assert c.get("/download/no-such-org/installer/linux").status_code == 404
    assert c.get("/download/no-such-org/installer/linux").status_code == 429


def test_installer_fail_closed(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-b", _plugin_version(),
                {"linux": b"x"})
    c = _ip_client(client, "198.51.100.117")
    # Link deshabilitado → 403 link_disabled.
    _set_tenant_state(TENANT_B, download_enabled=False)
    r = c.get("/download/tenant-b/installer/linux")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "link_disabled"
    # Tenant suspendido → 403 tenant_suspended (fail-closed).
    _set_tenant_state(TENANT_B, download_enabled=True, status="suspended")
    r = c.get("/download/tenant-b/installer/linux")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "tenant_suspended"


def test_installer_isolation_per_tenant(client, artifacts_dir):
    """El manifest de B no habilita nada en A (aislamiento)."""
    _bake_build(artifacts_dir, "tenant-b", _plugin_version(),
                {"linux": b"tenant-b-build"})
    c = _ip_client(client, "198.51.100.118")
    assert c.get("/download/tenant-a/installer/linux").status_code == 404
    assert c.get("/download/tenant-b/installer/linux").status_code == 200
